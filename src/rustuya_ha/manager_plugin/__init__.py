"""rustuya-manager plugin: live HA MQTT-discovery status (read-only MVP).

This is the *reverse-dependency* glue described in docs/webui-roadmap.md (M2):
rustuya-ha plugs **into** rustuya-manager, never the other way round. The manager
ships a universal, HA-agnostic plugin host; this module advertises a
``register(ctx)`` callable under the ``rustuya_manager.plugins`` entry-point so
that *if both packages are installed* the manager grows an "HA Discovery" tab.

Crucially this module imports **nothing** from ``rustuya_manager`` — ``ctx`` is
used purely by duck-typing against the documented host contract (api_version,
devices(), bridge_config(), add_mqtt_subscription, add_api_router,
state_namespace, add_page, bridge_client). That keeps the dependency arrow
one-way (rustuya-ha → manager, via the ``manager`` extra) and lets the adapter
logic be unit-tested with a fake ctx and no manager/fastapi installed.

Read-only: we only *observe*. We subscribe to retained ``homeassistant/.../config``
topics, compare them against what ``DiscoveryGenerator`` would emit for the
manager's cloud devices (using the bridge's own topic/payload scheme), and push
a per-device status grid to our state namespace. No publish/clear here — that is
M3.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from ..core import backup, converter, pack, plan
from ..core.bridge import BridgeConfig, advise
from ..core.detail import device_detail, has_detail
from ..core.generator import initialize_generator
from ..core.restore import restore_plan
from ..core.scheme import scheme_for
from ..core.verifier import DiscoveryVerifier

logger = logging.getLogger("rustuya_ha.manager_plugin")

# Our private slice of the manager's State (rides its WS broadcast).
NAMESPACE = "discovery"
# HA discovery convention: every entity config lives at a topic ending /config.
HA_PREFIX = "homeassistant"
# Coalesce the cold-start burst of retained config messages into one recompute.
DEBOUNCE_SEC = 0.3
# Where pre-write snapshots are saved for undo (same format/rotation as the CLI).
BACKUP_DIR = ".rustuya-ha-backups"


class DiscoveryPlugin:
    """Adapter between the manager host (``ctx``) and rustuya-ha's core engine.

    Holds the accumulated retained HA-discovery state and recomputes the
    verifier categorization on demand. Pure of any manager import; everything
    it needs from the host comes through ``ctx``.
    """

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.generator = initialize_generator()
        self.verifier = DiscoveryVerifier(self.generator)
        # topic -> {"payload": <parsed dict>, "retain": bool}
        self.retained: Dict[str, Dict[str, Any]] = {}
        self.config_source = "default"
        # The resolved live BridgeConfig (None until a bridge config is seen);
        # kept so summarize() can advise on suboptimal settings.
        self.bridge_cfg: Optional[BridgeConfig] = None
        self.namespace = None  # set by register() to ctx.state_namespace(...)
        self.backup_dir = BACKUP_DIR
        self._dirty = False
        self._push_task: Optional[asyncio.Task] = None

    # ── bridge scheme ────────────────────────────────────────────────────
    def _scheme(self):
        """Resolve (scheme, codec) from the host's live bridge config, or None
        when none is available yet (caller keeps the generator's legacy default).
        Also records `config_source` for the UI badge."""
        cfg_dict = self.ctx.bridge_config()
        if cfg_dict:
            self.config_source = "bridge"
            self.bridge_cfg = BridgeConfig.from_dict(cfg_dict)
            return scheme_for(self.bridge_cfg)
        self.config_source = "default"
        self.bridge_cfg = None
        return None

    def _apply_bridge_config(self) -> None:
        """Point the generator at the bridge's live topic/payload scheme.

        The same resolution the CLI does, minus the file/legacy fallbacks (the
        host already owns the live config). With no bridge config the generator
        keeps its built-in default scheme (the legacy hardcoded layout)."""
        s = self._scheme()
        if s:
            self.generator.scheme, self.generator.codec = s

    def _reload_generator(self) -> None:
        """Rebuild the generator so it re-reads the converters file from disk
        (after an edit). The verifier holds the same generator reference."""
        self.generator = initialize_generator()
        self.verifier.generator = self.generator

    # ── retained-state maintenance ───────────────────────────────────────
    def update_retained(self, topic: str, payload: str) -> bool:
        """Fold one ``homeassistant/#`` message into the retained map.

        Returns True if the map changed (so the caller can decide to recompute).
        Only ``.../config`` topics are tracked; an empty payload is a retain-clear
        and drops the topic. Unparseable JSON is ignored (logged at debug)."""
        if not topic.endswith("/config"):
            return False
        if payload == "" or payload is None:
            return self.retained.pop(topic, None) is not None
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            logger.debug("ignoring non-JSON retained payload on %s", topic)
            return False
        self.retained[topic] = {"payload": parsed, "retain": True}
        return True

    # ── compute / summarize ──────────────────────────────────────────────
    def compute(self) -> Dict[str, Any]:
        """Run the verifier against current cloud devices + retained HA state."""
        self._apply_bridge_config()
        devices: List[Dict[str, Any]] = list(self.ctx.devices().values())
        return self.verifier.verify(devices, self.retained)

    def summarize(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten verifier output into a compact, JSON-safe status grid.

        Shape consumed by the frontend:
          {counts: {cat: n}, devices: [{id, name, product_id, category}],
           config_source, retained_topics, errors: [...]}
        """
        counts = {cat: len(v) for cat, v in results.items() if isinstance(v, list)}
        # product_id isn't carried in the verifier rows (it builds fresh dicts),
        # so map it back from the in-memory cloud snapshot by device id. Surfaced
        # per device in the grid — the authoring key for product-scoped converters.
        pid_by_id = {did: raw.get("product_id") for did, raw in self.ctx.devices().items()}
        devices: List[Dict[str, Any]] = []
        for cat, lst in results.items():
            if cat == "errors" or not isinstance(lst, list):
                continue
            for d in lst:
                if cat == "orphans":
                    devices.append(
                        {"id": d.get("id", "unknown"), "name": "(orphan)",
                         "category": "orphans", "topics": d.get("topics", [])}
                    )
                elif "id" in d:
                    row = {
                        "id": d["id"], "name": d.get("name", d["id"]),
                        "product_id": pid_by_id.get(d["id"]) or "",
                        "category": cat,
                        "matched": len(d.get("matched", [])),
                        "mismatched": len(d.get("mismatched", [])),
                        "missing": len(d.get("missing", [])),
                        "unexpected": len(d.get("unexpected", [])),
                        "expected": d.get("expected_count", 0),
                    }
                    # Attach compact drill-in detail only for rows that have a
                    # diff/missing/unexpected — keeps the WS namespace payload lean
                    # (perfect rows carry just their counts).
                    if has_detail(d):
                        row["detail"] = device_detail(d)
                    devices.append(row)
        devices.sort(key=lambda x: (x["category"], str(x["id"])))
        return {
            "counts": counts,
            "devices": devices,
            "config_source": self.config_source,
            "retained_topics": len(self.retained),
            "errors": results.get("errors", []),
            # Read-only hints on suboptimal bridge settings; only when we can
            # actually see the live bridge config (never for the default guess).
            "advice": advise(self.bridge_cfg) if self.bridge_cfg is not None else [],
        }

    def status(self) -> Dict[str, Any]:
        """Full on-demand status: the summary grid plus the raw category detail
        (for the API endpoint / future field-diff view)."""
        results = self.compute()
        summary = self.summarize(results)
        summary["detail"] = results
        return summary

    # ── live push (debounced) ────────────────────────────────────────────
    async def on_mqtt(self, topic: str, payload: str, retain: bool) -> None:
        """MQTT tap handler for ``homeassistant/#``. Updates retained state and
        schedules a coalesced recompute+push to the state namespace."""
        if self.update_retained(topic, payload):
            self._schedule_push()

    def _schedule_push(self) -> None:
        self._dirty = True
        if self._push_task is not None and not self._push_task.done():
            return
        try:
            self._push_task = asyncio.ensure_future(self._push_loop())
        except RuntimeError:
            # No running loop (e.g. unit test calling update_retained directly).
            self._push_task = None

    async def _push_loop(self) -> None:
        """Coalesce a burst of retained messages: recompute once the dust has
        settled, repeating only if new messages landed mid-compute."""
        try:
            while self._dirty:
                self._dirty = False
                await asyncio.sleep(DEBOUNCE_SEC)
                await self.push()
        finally:
            self._push_task = None

    async def push(self) -> None:
        if self.namespace is None:
            return
        results = self.compute()
        await self.namespace.set(self.summarize(results))

    # ── write actions (M3) ───────────────────────────────────────────────
    # All mutate retained `homeassistant/.../config` topics via the host's
    # generic `publish_raw`. The broker echoes each retained write back through
    # our `homeassistant/#` tap, which updates `self.retained` and re-pushes the
    # namespace — so the UI refreshes itself after a write with no extra plumbing.
    def _devices_by_id(self) -> Dict[str, Dict[str, Any]]:
        return {did: raw for did, raw in self.ctx.devices().items()}

    async def _execute(self, msgs: List[Dict[str, Any]]) -> str:
        """Snapshot current retained state (undo point), then publish each
        message retained. Returns the backup file path."""
        path = backup.save(self.backup_dir, self.retained, prefix="auto")
        client = self.ctx.bridge_client
        for m in msgs:
            await client.publish_raw(m["topic"], m["payload"], retain=m.get("retain", True))
        return path

    async def publish(self, ids: List[str], dry_run: bool = False) -> Dict[str, Any]:
        """(Re)publish discovery for the given device ids. Clears each device's
        own stale topics first, then publishes its expected payloads."""
        self._apply_bridge_config()
        by_id = self._devices_by_id()
        devices = [by_id[i] for i in ids if i in by_id]
        unknown = [i for i in ids if i not in by_id]
        msgs, per_device, errors = plan.publish_plan(devices, self.generator, self.retained)
        result = {
            "action": "publish", "dry_run": dry_run, "per_device": per_device,
            "errors": errors, "unknown_ids": unknown, "msg_count": len(msgs),
        }
        if dry_run or not msgs:
            result["executed"] = False
            return result
        result["backup"] = await self._execute(msgs)
        result["executed"] = True
        return result

    async def clear(
        self,
        ids: Optional[List[str]] = None,
        dry_run: bool = False,
        topics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Clear retained discovery topics.

        By device id (``ids``) — every retained topic each device owns — or by
        explicit ``topics`` (used for orphans, whose owner id the verifier can't
        attribute, so an id-based clear can't reach them). Only topics actually
        retained are emitted as retain-clears.
        """
        if topics is not None:
            present = [t for t in topics if t in self.retained]
            msgs = [{"topic": t, "payload": "", "retain": True} for t in sorted(present)]
            per_device = [{"id": "(topics)", "clear": len(present)}] if present else []
        else:
            msgs, per_device = plan.clear_plan(self.retained, ids or [])
        result = {
            "action": "clear", "dry_run": dry_run,
            "per_device": per_device, "msg_count": len(msgs),
        }
        if dry_run or not msgs:
            result["executed"] = False
            return result
        result["backup"] = await self._execute(msgs)
        result["executed"] = True
        return result

    async def restore(self, file: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
        """Revert retained discovery to a saved snapshot (full-scope undo):
        re-publish the snapshot's topics and clear any topics added since."""
        if file:
            # A restore target is user-supplied — confine it to the backup dir
            # (no arbitrary-path read) and require it to exist.
            try:
                path = backup.safe_path(self.backup_dir, file)
            except ValueError:
                return {"action": "restore", "error": "invalid backup name", "executed": False}
            if not os.path.isfile(path):
                return {"action": "restore", "error": "no such backup", "executed": False}
        else:
            path = backup.latest(self.backup_dir)
            if not path:
                return {"action": "restore", "error": "no backup found", "executed": False}
        snapshot = backup.load(path)
        set_msgs, clear_msgs = restore_plan(snapshot, list(self.retained.keys()))
        msgs = clear_msgs + set_msgs
        result = {
            "action": "restore", "from": str(path), "set": len(set_msgs),
            "clear": len(clear_msgs), "dry_run": dry_run, "msg_count": len(msgs),
        }
        if dry_run or not msgs:
            result["executed"] = False
            return result
        result["backup"] = await self._execute(msgs)  # snapshot pre-restore too
        result["executed"] = True
        return result

    def list_backups(self, limit: int = 50) -> Dict[str, Any]:
        """Newest `limit` backups + the full count, so the UI stays bounded even
        if the dir grows (manual drops; auto/converters self-rotate)."""
        all_files = backup.listing(self.backup_dir)
        shown = all_files[:limit]
        return {
            "backups": [{"name": p.name, "path": str(p)} for p in shown],
            "total": len(all_files),
        }

    # ── custom converters: file-level editing ────────────────────────────
    # Converters live as a drop-in directory (custom_converters/) of *.json
    # (merged by product_id) + *.py code converters. The editor browses and edits
    # those files directly; the generator/CLI resolve the same directory.
    def converters_files(self) -> Dict[str, Any]:
        """The converter files on disk + the fleet's product_ids (authoring
        context: which products exist, their devices, and whether a JSON
        converter already mentions them)."""
        mapping = converter.load_converters()
        products: Dict[str, Dict[str, Any]] = {}
        for did, raw in self.ctx.devices().items():
            pid = raw.get("product_id")
            if not pid:
                continue
            e = products.setdefault(
                pid,
                {"product_id": pid, "device_ids": [], "device_names": [],
                 "has_override": pid in mapping},
            )
            e["device_ids"].append(did)
            e["device_names"].append(raw.get("name", did))
        pack_dir = converter.savable_dir(None)
        ledger = pack.read_ledger(pack_dir)
        # Flag pack-managed files so the editor can show them read-only: editing one
        # is futile (the next pack sync overwrites it), so users customize by
        # creating their own file. The ledger is the authoritative owner record.
        files = converter.list_files()
        for f in files:
            f["managed"] = f["name"] in ledger
        return {
            "dir": str(pack_dir),
            "files": files,
            "products": sorted(products.values(), key=lambda x: x["product_id"]),
            # Default-converter pack state, so the UI can offer "get the defaults"
            # on a fresh install vs "update" once they're present.
            "pack": {
                "synced": pack.has_synced(pack_dir),
                "managed": len(ledger),
            },
        }

    def read_converter_file(self, name: str) -> Dict[str, Any]:
        """Raw text of one converter file (404 via ValueError if absent)."""
        try:
            content = converter.read_file(name)
        except (FileNotFoundError, OSError) as e:
            raise ValueError(f"no such converter file: {name}") from e
        return {"name": name, "kind": name.rsplit(".", 1)[-1], "content": content}

    def _generator_with(self, mapping: Dict[str, Any]):
        """A fresh generator using `mapping` as its converter overrides, with the
        live bridge scheme applied — for validating an unsaved edit."""
        gen = initialize_generator()
        gen.converter.mapping = mapping
        s = self._scheme()
        if s:
            gen.scheme, gen.codec = s
        return gen

    def _validate_converter_file(self, name: str, content: str) -> None:
        """Reject a file that can't be loaded: malformed JSON / Python syntax, or
        a JSON converter that breaks generation for any of its products."""
        if name.endswith(".json"):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                raise ValueError(f"invalid JSON: {e}") from e
            if not isinstance(parsed, dict):
                raise ValueError("a JSON converter must be an object of product_id -> override")
            for pid, override in parsed.items():
                if not isinstance(override, dict):
                    raise ValueError(f"override for {pid} must be a JSON object")
            gen = self._generator_with(parsed)
            try:
                for d in self.ctx.devices().values():
                    if d.get("product_id") in parsed:
                        gen.generate(d)
            except Exception as e:  # any generation failure → 400, not a 500
                raise ValueError(f"converter breaks generation: {e}") from e
        elif name.endswith(".py"):
            try:
                compile(content, name, "exec")  # syntax-check only; never executed here
            except SyntaxError as e:
                raise ValueError(f"Python syntax error: {e}") from e

    def _assert_editable(self, name: str) -> None:
        """Reject writing/deleting a pack-managed (default) converter — the next
        `update_default_converters` sync overwrites it anyway, so editing it in
        place is a trap. Users customize by creating their own file (which
        deep-merges on top). The pack ledger is the authoritative owner record."""
        if name in pack.read_ledger(converter.savable_dir(None)):
            # Terse, locale-agnostic: the UI surfaces this verbatim in a toast and
            # shows the localized "copy to a new file" guidance separately.
            raise ValueError(f"{name}: default-pack converter, read-only")

    def save_converter_file(self, name: str, content: str) -> Dict[str, Any]:
        """Create/overwrite one converter file. Validates first (JSON shape +
        generation, or Python syntax), backs up any prior version, writes, and —
        for a JSON file — reloads the generator. A `.py` change needs a manager
        restart to take effect (restart_required). Pack-managed defaults are
        read-only (ValueError → 400)."""
        target = converter.file_path(name)  # validates the name (ValueError → 400)
        self._assert_editable(target.name)
        self._validate_converter_file(name, content)
        backup_path = backup.snapshot_file(self.backup_dir, str(target), prefix="converters")
        path = converter.write_file(name, content)
        is_json = name.endswith(".json")
        if is_json:
            self._reload_generator()
        return {"name": target.name, "kind": "json" if is_json else "py",
                "path": path, "backup": backup_path, "restart_required": not is_json}

    async def update_default_converters(self) -> Dict[str, Any]:
        """Fetch the curated default-converter pack from GitHub and mirror it into
        the converters directory — adding/updating/removing only the files the
        pack manages, never the user's own (`99_*`, hand-authored). Reloads the
        generator for the JSON changes; a `.py` add/update/remove needs a restart
        to take effect. Network/verify failures raise `pack.PackError`."""
        dest = converter.savable_dir(None)
        summary = await asyncio.to_thread(pack.sync, str(dest))
        self._reload_generator()  # JSON converters hot-reload
        touched = summary["added"] + summary["updated"] + summary["removed"]
        summary["restart_required"] = any(n.endswith(".py") for n in touched)
        return summary

    def delete_converter_file(self, name: str) -> Dict[str, Any]:
        """Delete one converter file (backing it up first). Reloads the generator
        for a JSON file; a `.py` removal needs a restart to fully unload it."""
        target = converter.file_path(name)  # validates the name
        self._assert_editable(target.name)
        backup_path = backup.snapshot_file(self.backup_dir, str(target), prefix="converters")
        converter.delete_file(name)
        is_json = name.endswith(".json")
        if is_json:
            self._reload_generator()
        return {"name": target.name, "deleted": True, "backup": backup_path,
                "restart_required": not is_json}


def _pin_converters_dir(ctx: Any) -> Optional[str]:
    """Point the converters directory at the host's CWD-independent data dir
    (``ctx.data_dir``) when one is offered, so the dir the plugin reads, the
    editor writes, and the pack syncs into is the same regardless of the
    manager's working directory — and survives plugin reinstalls.

    Feature-detected and deferential: an older host without ``data_dir``, or an
    operator-set ``RUSTUYA_CONVERTERS``, is left untouched. Returns the directory
    it pinned (for logging/tests), or ``None`` when it left resolution alone."""
    data_dir = getattr(ctx, "data_dir", None)
    if not callable(data_dir) or os.environ.get(converter.ENV_VAR):
        return None
    try:
        path = str(data_dir("custom_converters"))
    except Exception:
        logger.debug("rustuya-ha: ctx.data_dir unusable; using default converters dir", exc_info=True)
        return None
    os.environ[converter.ENV_VAR] = path
    logger.info("rustuya-ha: converters dir → %s", path)
    return path


def register(ctx: Any) -> None:
    """Entry-point hook the manager's plugin host calls once at startup.

    Wires up: the ``homeassistant/#`` MQTT tap, a read-only status API, the
    state namespace, and the UI page. Guards on ``api_version`` so a future
    breaking host bump cleanly refuses to load this plugin instead of crashing.
    """
    if getattr(ctx, "api_version", 0) < 1:
        logger.warning(
            "host plugin api_version=%s unsupported by rustuya-ha discovery plugin; skipping",
            getattr(ctx, "api_version", None),
        )
        return

    # Must run before the generator is built (it reads converters).
    _pin_converters_dir(ctx)

    plugin = DiscoveryPlugin(ctx)
    plugin.namespace = ctx.state_namespace(NAMESPACE)
    ctx.add_mqtt_subscription(f"{HA_PREFIX}/#", plugin.on_mqtt)

    # Load user Python code converters (custom_converters/*.py): per-device
    # derivation logic that reacts to decoded DPs and publishes derived DPs via
    # the manager runtime. Self-guards to a no-op on a host with api_version < 2.
    from . import code_converters

    try:
        loaded = code_converters.load_code_converters(ctx)
        if loaded:
            logger.info("rustuya-ha: loaded %d code converter(s)", loaded)
    except Exception:  # never let a converter problem block plugin startup
        logger.exception("rustuya-ha: code converter loading failed")

    # On a fresh install (the pack has never been synced here), seed the curated
    # default converters from GitHub once — as a one-shot background service so it
    # never blocks startup and a network hiccup is a logged no-op, not a failure.
    # Needs the service runtime (api_version >= 2); updates after this are the
    # user's explicit "update defaults" action. A seeded `.py` activates on the
    # next restart (code converters load at register time, above).
    if getattr(ctx, "api_version", 0) >= 2 and not pack.has_synced(converter.savable_dir(None)):
        async def _seed_default_converters() -> None:
            try:
                summary = await plugin.update_default_converters()
                await plugin.push()
                logger.info("rustuya-ha: seeded default converters (%s)", summary)
            except pack.PackError as e:
                logger.warning("rustuya-ha: default converter seed skipped: %s", e)

        try:
            ctx.add_service(lambda: _seed_default_converters())
        except Exception:  # add_service missing/again → just skip the auto-seed
            logger.debug("rustuya-ha: auto-seed service not registered", exc_info=True)

    # Imported lazily: the manager (and thus fastapi) is only present when this
    # plugin actually runs inside the host, so the module stays importable for
    # unit tests without fastapi installed.
    from fastapi import APIRouter, Body, HTTPException

    router = APIRouter()

    @router.get("/api/discovery/status")
    async def discovery_status() -> Dict[str, Any]:
        return plugin.status()

    @router.get("/api/discovery/backups")
    async def discovery_backups() -> Dict[str, Any]:
        return plugin.list_backups()

    # publish/clear take {ids: [...], dry_run: bool}; restore takes {file?, dry_run}.
    # A RuntimeError from publish_raw means the broker is down → surface as 503 so
    # the UI can toast "try again" rather than failing opaquely.
    @router.post("/api/discovery/publish")
    async def discovery_publish(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return await plugin.publish(body.get("ids", []), bool(body.get("dry_run", False)))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @router.post("/api/discovery/clear")
    async def discovery_clear(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return await plugin.clear(
                body.get("ids", []),
                bool(body.get("dry_run", False)),
                topics=body.get("topics"),
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @router.post("/api/discovery/restore")
    async def discovery_restore(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return await plugin.restore(body.get("file"), bool(body.get("dry_run", False)))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    # custom_converters editing: the converters live as a drop-in directory of
    # files (*.json merged + *.py code converters); the editor is file-level.
    @router.get("/api/discovery/converters")
    async def discovery_converters() -> Dict[str, Any]:
        return plugin.converters_files()

    @router.get("/api/discovery/converters/file")
    async def discovery_converter_read(name: str) -> Dict[str, Any]:
        try:
            return plugin.read_converter_file(name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @router.post("/api/discovery/converters/file")
    async def discovery_converter_write(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        if not body.get("name"):
            raise HTTPException(status_code=400, detail="name required")
        try:
            res = plugin.save_converter_file(body["name"], body.get("content", ""))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        await plugin.push()  # a JSON converter change may shift categories → refresh grid
        return res

    @router.post("/api/discovery/converters/file/delete")
    async def discovery_converter_delete(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        if not body.get("name"):
            raise HTTPException(status_code=400, detail="name required")
        try:
            res = plugin.delete_converter_file(body["name"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        await plugin.push()  # a JSON converter removal may shift categories → refresh grid
        return res

    # Pull the curated default-converter pack from GitHub (decoupled from this
    # plugin's release — a converter fix on master is enough). Mirrors only the
    # pack's own files; the user's converters are untouched.
    @router.post("/api/discovery/converters/update")
    async def discovery_converters_update() -> Dict[str, Any]:
        try:
            res = await plugin.update_default_converters()
        except pack.PackError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        await plugin.push()  # new/changed converters may shift categories → refresh grid
        return res

    ctx.add_api_router(router)

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    ctx.add_page("discovery", "HA Discovery", static_dir=static_dir)

    logger.info("rustuya-ha discovery plugin registered")
    return plugin  # returned for tests; the host ignores the return value
