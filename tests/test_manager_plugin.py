"""Tests for the rustuya-manager plugin adapter (manager_plugin).

These exercise the adapter against a *fake* host ctx — no rustuya_manager and no
fastapi import is required for the core logic (register() imports fastapi lazily).
The fake ctx mirrors the documented host contract: api_version, devices(),
bridge_config(), add_mqtt_subscription, state_namespace, add_api_router, add_page.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core import backup  # noqa: E402
from rustuya_ha.core.generator import initialize_generator  # noqa: E402
from rustuya_ha.manager_plugin import DiscoveryPlugin, register  # noqa: E402

DEVICES = json.load(open(ROOT / "tests" / "fixtures" / "synthetic_devices.json"))
LEGACY_CFG = json.load(open(ROOT / "tests" / "fixtures" / "legacy_bridge_config.json"))


class FakeNamespace:
    def __init__(self):
        self.data = None

    async def set(self, data):
        self.data = data


class FakeBridgeClient:
    """Captures publish_raw calls; optionally simulates a disconnected broker."""

    def __init__(self, connected=True):
        self.connected = connected
        self.published = []  # list of (topic, payload, retain)

    async def publish_raw(self, topic, payload, *, retain=False, qos=1):
        if not self.connected:
            raise RuntimeError("MQTT broker not connected")
        self.published.append((topic, payload, retain))


class FakeCtx:
    """Minimal stand-in for the manager's PluginContext."""

    def __init__(self, devices=None, bridge_cfg=None, api_version=1, connected=True):
        self.api_version = api_version
        self._devices = {d["id"]: d for d in (devices or [])}
        self._bridge_cfg = bridge_cfg
        self.subscriptions = []
        self.routers = []
        self.pages = []
        self.ns = FakeNamespace()
        self.bridge_client = FakeBridgeClient(connected=connected)

    def devices(self):
        return dict(self._devices)

    def bridge_config(self):
        return dict(self._bridge_cfg) if self._bridge_cfg is not None else None

    def add_mqtt_subscription(self, topic_filter, handler):
        self.subscriptions.append((topic_filter, handler))

    def state_namespace(self, name):
        return self.ns

    def add_api_router(self, router):
        self.routers.append(router)

    def add_page(self, id, label, *, static_dir, entry="index.js"):
        self.pages.append({"id": id, "label": label, "static_dir": static_dir})


def _retained_for(devices, bridge_cfg=None):
    """Generate the exact discovery topics/payloads the generator would emit,
    formatted as the retained map the plugin maintains ({topic: {payload}})."""
    gen = initialize_generator()
    if bridge_cfg is not None:
        from rustuya_ha.core.bridge import BridgeConfig
        from rustuya_ha.core.scheme import scheme_for

        gen.scheme, gen.codec = scheme_for(BridgeConfig.from_dict(bridge_cfg))
    retained = {}
    for d in devices:
        payloads, _ = gen.generate(d)
        for topic, payload in payloads.items():
            retained[topic] = {"payload": payload, "retain": True}
    return retained


# ── update_retained ──────────────────────────────────────────────────────
def test_update_retained_tracks_only_config_topics():
    p = DiscoveryPlugin(FakeCtx())
    assert p.update_retained("homeassistant/sensor/x/state", '{"a":1}') is False
    assert p.retained == {}
    assert p.update_retained("homeassistant/sensor/x/config", '{"a":1}') is True
    assert p.retained["homeassistant/sensor/x/config"]["payload"] == {"a": 1}


def test_update_retained_empty_payload_clears():
    p = DiscoveryPlugin(FakeCtx())
    p.update_retained("homeassistant/sensor/x/config", '{"a":1}')
    assert p.update_retained("homeassistant/sensor/x/config", "") is True
    assert p.retained == {}
    # clearing an absent topic is a no-op (no change)
    assert p.update_retained("homeassistant/sensor/x/config", "") is False


def test_update_retained_ignores_bad_json():
    p = DiscoveryPlugin(FakeCtx())
    assert p.update_retained("homeassistant/sensor/x/config", "{not json") is False
    assert p.retained == {}


# ── compute / categorization end-to-end ──────────────────────────────────
def test_compute_all_missing_when_no_retained():
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    results = p.compute()
    # No retained discovery at all → every device is pure_missing.
    assert {d["id"] for d in results["pure_missing"]} == {d["id"] for d in DEVICES}
    assert results["perfect"] == []
    assert p.config_source == "bridge"


def test_compute_perfect_when_retained_matches_generator():
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    # Seed retained with exactly what the generator (legacy scheme) emits.
    for topic, entry in _retained_for(DEVICES, LEGACY_CFG).items():
        p.retained[topic] = entry
    results = p.compute()
    perfect_ids = {d["id"] for d in results["perfect"]}
    assert perfect_ids == {d["id"] for d in DEVICES}, results
    assert results["mismatched_payload"] == []
    assert results["pure_missing"] == []


def test_compute_default_scheme_without_bridge_config():
    # No bridge config → default (legacy-equivalent) scheme; still categorizes.
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=None)
    p = DiscoveryPlugin(ctx)
    results = p.compute()
    assert p.config_source == "default"
    assert {d["id"] for d in results["pure_missing"]} == {d["id"] for d in DEVICES}


# ── summarize ────────────────────────────────────────────────────────────
def test_summarize_shape():
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    summary = p.summarize(p.compute())
    assert set(summary) >= {"counts", "devices", "config_source", "retained_topics", "errors"}
    assert summary["counts"]["pure_missing"] == len(DEVICES)
    assert len(summary["devices"]) == len(DEVICES)
    # Every grid row carries id/name/category and is JSON-serializable.
    json.dumps(summary)
    for row in summary["devices"]:
        assert {"id", "name", "category"} <= set(row)


# ── drill-in detail in the grid (M4) ─────────────────────────────────────
def test_summarize_attaches_detail_only_for_problem_rows():
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    retained = _retained_for(DEVICES, LEGACY_CFG)
    # Corrupt one topic so its owning device becomes mismatched_payload (the
    # device.identifiers stay intact, so it still resolves to that device).
    topic = next(iter(retained))
    retained[topic] = {
        "payload": {**retained[topic]["payload"], "_corrupt": 1}, "retain": True,
    }
    for t, e in retained.items():
        p.retained[t] = e
    summary = p.summarize(p.compute())

    mism = [r for r in summary["devices"] if r["category"] == "mismatched_payload"]
    assert mism, summary["counts"]
    assert "detail" in mism[0]
    fields = mism[0]["detail"]["mismatched"][0]["fields"]
    assert any(f["key"] == "_corrupt" for f in fields)
    # Perfect rows stay lean — no detail attached.
    perfect = [r for r in summary["devices"] if r["category"] == "perfect"]
    assert perfect and all("detail" not in r for r in perfect)


# ── on_mqtt debounced push ───────────────────────────────────────────────
# Driven via asyncio.run() rather than pytest-asyncio (not a repo test dep).
def test_on_mqtt_pushes_summary_to_namespace():
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
        p = DiscoveryPlugin(ctx)
        p.namespace = ctx.ns
        retained = _retained_for(DEVICES, LEGACY_CFG)
        for topic, entry in retained.items():
            await p.on_mqtt(topic, json.dumps(entry["payload"]), True)
        # Let the debounced push task run.
        await asyncio.sleep(0.05)
        if p._push_task:
            await p._push_task
        assert ctx.ns.data is not None
        assert ctx.ns.data["counts"]["perfect"] == len(DEVICES)

    asyncio.run(go())


def test_on_mqtt_non_config_topic_no_push():
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
        p = DiscoveryPlugin(ctx)
        p.namespace = ctx.ns
        await p.on_mqtt("homeassistant/sensor/x/state", "true", True)
        await asyncio.sleep(0.05)
        assert ctx.ns.data is None  # no /config change → no recompute/push

    asyncio.run(go())


# ── status() ─────────────────────────────────────────────────────────────
def test_status_includes_detail():
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    out = p.status()
    assert "detail" in out and "perfect" in out["detail"]
    json.dumps(out)


# ── write actions: publish / clear / restore (M3) ────────────────────────
def _seed_retained(plugin, devices, bridge_cfg):
    for topic, entry in _retained_for(devices, bridge_cfg).items():
        plugin.retained[topic] = entry


def test_publish_dry_run_does_not_write(tmp_path):
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
        p = DiscoveryPlugin(ctx)
        p.backup_dir = str(tmp_path)
        res = await p.publish([d["id"] for d in DEVICES], dry_run=True)
        assert res["executed"] is False and res["msg_count"] > 0
        assert ctx.bridge_client.published == []
        assert list(tmp_path.iterdir()) == []  # no backup written on dry-run

    asyncio.run(go())


def test_publish_writes_and_backs_up(tmp_path):
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
        p = DiscoveryPlugin(ctx)
        p.backup_dir = str(tmp_path)
        res = await p.publish([d["id"] for d in DEVICES])
        assert res["executed"] is True
        # Every device's expected topics were published (retained, non-empty).
        assert len(ctx.bridge_client.published) == res["msg_count"] > 0
        assert all(retain and payload != "" for _, payload, retain in ctx.bridge_client.published)
        # A backup snapshot was written before the write.
        assert Path(res["backup"]).is_file()

    asyncio.run(go())


def test_clear_writes_empty_retained(tmp_path):
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
        p = DiscoveryPlugin(ctx)
        p.backup_dir = str(tmp_path)
        _seed_retained(p, DEVICES, LEGACY_CFG)
        n_topics = len(p.retained)
        res = await p.clear([d["id"] for d in DEVICES])
        assert res["executed"] is True
        assert len(ctx.bridge_client.published) == n_topics
        assert all(payload == "" and retain for _, payload, retain in ctx.bridge_client.published)

    asyncio.run(go())


def test_restore_reverts_to_snapshot(tmp_path):
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
        p = DiscoveryPlugin(ctx)
        p.backup_dir = str(tmp_path)
        # Snapshot a known-good retained state to a backup file.
        _seed_retained(p, DEVICES, LEGACY_CFG)
        snap_path = backup.save(str(tmp_path), p.retained, prefix="manual")
        snapshot_topics = set(p.retained)
        # Now simulate drift: drop one topic, add an extra one.
        dropped = next(iter(snapshot_topics))
        del p.retained[dropped]
        p.retained["homeassistant/sensor/extra/config"] = {"payload": {}, "retain": True}
        res = await p.restore(snap_path)
        assert res["executed"] is True
        published = {t: pl for t, pl, _ in ctx.bridge_client.published}
        # The dropped topic is re-set; the added topic is cleared (empty payload).
        assert dropped in published and published[dropped] != ""
        assert published.get("homeassistant/sensor/extra/config") == ""

    asyncio.run(go())


def test_restore_no_backup_returns_error(tmp_path):
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
        p = DiscoveryPlugin(ctx)
        p.backup_dir = str(tmp_path)  # empty dir → no backups
        res = await p.restore()
        assert res["executed"] is False and "error" in res
        assert ctx.bridge_client.published == []

    asyncio.run(go())


def test_publish_raises_when_broker_down(tmp_path):
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG, connected=False)
        p = DiscoveryPlugin(ctx)
        p.backup_dir = str(tmp_path)
        with pytest.raises(RuntimeError, match="not connected"):
            await p.publish([d["id"] for d in DEVICES])

    asyncio.run(go())


def test_list_backups(tmp_path):
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    p.backup_dir = str(tmp_path)
    assert p.list_backups() == []
    backup.save(str(tmp_path), {"homeassistant/x/config": {"payload": {"a": 1}}}, prefix="auto")
    names = [b["name"] for b in p.list_backups()]
    assert len(names) == 1 and names[0].startswith("auto-")


# ── register() wiring (needs fastapi) ────────────────────────────────────
def test_register_wires_host_surfaces():
    pytest.importorskip("fastapi")
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    plugin = register(ctx)
    assert plugin is not None
    assert ctx.subscriptions and ctx.subscriptions[0][0] == "homeassistant/#"
    assert len(ctx.routers) == 1
    assert ctx.pages and ctx.pages[0]["id"] == "discovery"
    # static dir exists and ships index.js
    static_dir = Path(ctx.pages[0]["static_dir"])
    assert (static_dir / "index.js").is_file()


def test_register_refuses_incompatible_api_version():
    ctx = FakeCtx(api_version=0)
    assert register(ctx) is None
    assert ctx.subscriptions == []
