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

from ..core import backup, converter, plan
from ..core.bridge import BridgeConfig
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
            return scheme_for(BridgeConfig.from_dict(cfg_dict))
        self.config_source = "default"
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
          {counts: {cat: n}, devices: [{id, name, category}], config_source,
           retained_topics, errors: [...]}
        """
        counts = {cat: len(v) for cat, v in results.items() if isinstance(v, list)}
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
        path = file or backup.latest(self.backup_dir)
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

    # ── custom converters editing (M5) ───────────────────────────────────
    # Edits the same per-product_id override file the generator/CLI resolve
    # (RUSTUYA_CONVERTERS / ./custom_converters.json), so UI and CLI share one
    # source. Writes never touch the packaged example (redirected to CWD).
    def converters_info(self) -> Dict[str, Any]:
        """Current converters mapping + the product_ids present in the fleet
        (with their device ids/names and whether an override exists)."""
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
        return {
            "path": str(converter.resolve_path(None)),
            "save_path": str(converter.savable_path(None)),
            "converters": mapping,
            "products": sorted(products.values(), key=lambda x: x["product_id"]),
        }

    def _generator_with(self, mapping: Dict[str, Any]):
        """A fresh generator using `mapping` as its converter overrides, with the
        live bridge scheme applied — for previewing an unsaved edit."""
        gen = initialize_generator()
        gen.converter.mapping = mapping
        s = self._scheme()
        if s:
            gen.scheme, gen.codec = s
        return gen

    @staticmethod
    def _validate_override(override: Any) -> None:
        if override is not None and not isinstance(override, dict):
            raise ValueError("override must be a JSON object (or null to delete)")

    def preview_converter(self, product_id: str, override: Any) -> Dict[str, Any]:
        """Regenerate discovery for every fleet device of `product_id` using the
        proposed (unsaved) override. `override=None` previews removal."""
        self._validate_override(override)
        merged = dict(converter.load_converters())
        if override is None:
            merged.pop(product_id, None)
        else:
            merged[product_id] = override
        gen = self._generator_with(merged)
        devices = [r for r in self.ctx.devices().values() if r.get("product_id") == product_id]
        out: List[Dict[str, Any]] = []
        for d in devices:
            try:
                payloads, source = gen.generate(d)
                out.append({"id": d["id"], "name": d.get("name", d["id"]),
                            "source": source, "topics": payloads})
            except Exception as e:  # surface a bad override instead of 500ing
                out.append({"id": d["id"], "name": d.get("name", d["id"]), "error": str(e)})
        return {"product_id": product_id, "devices": out}

    def save_all_converters(self, mapping: Any) -> Dict[str, Any]:
        """Replace the *entire* converters file with `mapping` (the UI's "All"
        editor). Validates it's product_id -> object and that nothing breaks
        generation across the fleet, backs up the old file, then writes + reloads."""
        if not isinstance(mapping, dict):
            raise ValueError("converters must be a JSON object of product_id -> override")
        for pid, override in mapping.items():
            if not isinstance(override, dict):
                raise ValueError(f"override for {pid} must be a JSON object")
        gen = self._generator_with(mapping)
        try:
            for d in self.ctx.devices().values():
                gen.generate(d)
        except Exception as e:  # any generation failure → 400, not a 500
            raise ValueError(f"converters break generation: {e}") from e
        backup_path = backup.snapshot_file(
            self.backup_dir, str(converter.savable_path(None)), prefix="converters"
        )
        path = converter.save_converters(mapping)
        self._reload_generator()
        return {"saved": True, "count": len(mapping), "path": path, "backup": backup_path}

    def save_converter(self, product_id: str, override: Any) -> Dict[str, Any]:
        """Persist (or delete, when `override=None`) one product_id's override.
        Backs up the prior file, refuses an override that crashes generation,
        then reloads the generator so subsequent status/publish use it."""
        self._validate_override(override)
        mapping = dict(converter.load_converters())
        if override is None:
            mapping.pop(product_id, None)
        else:
            mapping[product_id] = override
        # Refuse to persist an override that breaks generation for its devices.
        gen = self._generator_with(mapping)
        try:
            for d in self.ctx.devices().values():
                if d.get("product_id") == product_id:
                    gen.generate(d)
        except Exception as e:  # any generation failure → 400, not a 500
            raise ValueError(f"override breaks generation: {e}") from e
        backup_path = backup.snapshot_file(
            self.backup_dir, str(converter.savable_path(None)), prefix="converters"
        )
        path = converter.save_converters(mapping)
        self._reload_generator()
        return {"product_id": product_id, "saved": True, "deleted": override is None,
                "path": path, "backup": backup_path}


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

    plugin = DiscoveryPlugin(ctx)
    plugin.namespace = ctx.state_namespace(NAMESPACE)
    ctx.add_mqtt_subscription(f"{HA_PREFIX}/#", plugin.on_mqtt)

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

    # custom_converters editing (M5). preview/save take {product_id, override}
    # where override is a JSON object (or null to delete the product's override).
    @router.get("/api/discovery/converters")
    async def discovery_converters() -> Dict[str, Any]:
        return plugin.converters_info()

    @router.post("/api/discovery/converters/preview")
    async def discovery_converters_preview(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return plugin.preview_converter(body["product_id"], body.get("override"))
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.post("/api/discovery/converters/save")
    async def discovery_converters_save(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            res = plugin.save_converter(body["product_id"], body.get("override"))
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        await plugin.push()  # converter change may shift categories → refresh grid
        return res

    @router.post("/api/discovery/converters/save_all")
    async def discovery_converters_save_all(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            res = plugin.save_all_converters(body.get("converters"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        await plugin.push()  # converter changes may shift categories → refresh grid
        return res

    ctx.add_api_router(router)

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    ctx.add_page("discovery", "HA Discovery", static_dir=static_dir)

    logger.info("rustuya-ha discovery plugin registered")
    return plugin  # returned for tests; the host ignores the return value
