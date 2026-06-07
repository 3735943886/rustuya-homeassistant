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

from ..core import backup, plan
from ..core.bridge import BridgeConfig
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
    def _apply_bridge_config(self) -> None:
        """Point the generator at the bridge's live topic/payload scheme.

        Reads the raw bridge config the host exposes (``ctx.bridge_config()``)
        and derives the scheme/codec — the same resolution the CLI does, minus
        the file/legacy fallbacks (the host already owns the live config). When
        no bridge config is available yet, the generator keeps its built-in
        default scheme, which reproduces the legacy hardcoded layout."""
        cfg_dict = self.ctx.bridge_config()
        if cfg_dict:
            cfg = BridgeConfig.from_dict(cfg_dict)
            self.generator.scheme, self.generator.codec = scheme_for(cfg)
            self.config_source = "bridge"
        else:
            self.config_source = "default"

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
                    devices.append(
                        {"id": d["id"], "name": d.get("name", d["id"]),
                         "category": cat,
                         "matched": len(d.get("matched", [])),
                         "mismatched": len(d.get("mismatched", [])),
                         "missing": len(d.get("missing", [])),
                         "unexpected": len(d.get("unexpected", [])),
                         "expected": d.get("expected_count", 0)}
                    )
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

    async def clear(self, ids: List[str], dry_run: bool = False) -> Dict[str, Any]:
        """Clear all retained discovery topics owned by the given device ids."""
        msgs, per_device = plan.clear_plan(self.retained, ids)
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

    def list_backups(self) -> List[Dict[str, str]]:
        return [{"name": p.name, "path": str(p)} for p in backup.listing(self.backup_dir)]


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
        return {"backups": plugin.list_backups()}

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
            return await plugin.clear(body.get("ids", []), bool(body.get("dry_run", False)))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @router.post("/api/discovery/restore")
    async def discovery_restore(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return await plugin.restore(body.get("file"), bool(body.get("dry_run", False)))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    ctx.add_api_router(router)

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    ctx.add_page("discovery", "HA Discovery", static_dir=static_dir)

    logger.info("rustuya-ha discovery plugin registered")
    return plugin  # returned for tests; the host ignores the return value
