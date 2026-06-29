"""Tests for the rustuya-manager plugin adapter (manager_plugin).

These exercise the adapter against a *fake* host ctx — no rustuya_manager and no
fastapi import is required for the core logic (register() imports fastapi lazily).
The fake ctx mirrors the documented host contract: api_version, devices(),
bridge_config(), add_mqtt_subscription, state_namespace, add_api_router, add_page.
"""
import asyncio
import json
import os
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
        self.services = []
        self.topic_requirements = []   # (source, template, must_have, must_not_have)
        self.retain_required_by = []

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

    def add_service(self, factory):
        self.services.append(factory)

    def require_topic(self, source, template, *, must_have=(), must_not_have=()):
        self.topic_requirements.append((source, template, tuple(must_have), tuple(must_not_have)))

    def require_retain(self, source):
        self.retain_required_by.append(source)


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


# ── converters dir pinning (ctx.data_dir adoption) ─────────────────────────
def test_pin_converters_dir_uses_data_dir(tmp_path, monkeypatch):
    from rustuya_ha.core import converter
    from rustuya_ha.manager_plugin import _pin_converters_dir

    monkeypatch.delenv(converter.ENV_VAR, raising=False)

    class CtxWithDataDir(FakeCtx):
        def data_dir(self, name):
            d = tmp_path / name
            d.mkdir(parents=True, exist_ok=True)
            return d

    pinned = _pin_converters_dir(CtxWithDataDir())
    assert pinned == str(tmp_path / "custom_converters")
    assert os.environ[converter.ENV_VAR] == str(tmp_path / "custom_converters")


def test_pin_converters_dir_respects_explicit_env(tmp_path, monkeypatch):
    from rustuya_ha.core import converter
    from rustuya_ha.manager_plugin import _pin_converters_dir

    monkeypatch.setenv(converter.ENV_VAR, "/operator/choice")

    class CtxWithDataDir(FakeCtx):
        def data_dir(self, name):  # would resolve elsewhere, but env wins
            return tmp_path / name

    assert _pin_converters_dir(CtxWithDataDir()) is None
    assert os.environ[converter.ENV_VAR] == "/operator/choice"


def test_pin_converters_dir_noop_on_old_host(monkeypatch):
    from rustuya_ha.core import converter
    from rustuya_ha.manager_plugin import _pin_converters_dir

    monkeypatch.delenv(converter.ENV_VAR, raising=False)
    # FakeCtx has no data_dir attribute → feature-detect leaves env unset.
    assert _pin_converters_dir(FakeCtx()) is None
    assert converter.ENV_VAR not in os.environ


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
    # Every grid row carries id/name/category/product_id and is JSON-serializable.
    json.dumps(summary)
    pid_by_id = {d["id"]: d.get("product_id", "") for d in DEVICES}
    for row in summary["devices"]:
        assert {"id", "name", "category", "product_id"} <= set(row)
        assert row["product_id"] == pid_by_id.get(row["id"], "")


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


def test_clear_by_explicit_topics(tmp_path):
    """Orphan clear path: clearing by topic list (not device id) retain-clears
    exactly the listed topics that are actually retained, ignoring unknown ones."""
    async def go():
        ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
        p = DiscoveryPlugin(ctx)
        p.backup_dir = str(tmp_path)
        _seed_retained(p, DEVICES, LEGACY_CFG)
        topics = sorted(p.retained.keys())
        target = topics[:1]  # clear just the first retained topic
        res = await p.clear(topics=target + ["homeassistant/sensor/ghost/config"])
        assert res["executed"] is True
        assert res["msg_count"] == 1  # the bogus topic isn't retained -> skipped
        published = [t for t, payload, retain in ctx.bridge_client.published]
        assert published == target
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
    assert p.list_backups() == {"backups": [], "total": 0}
    backup.save(str(tmp_path), {"homeassistant/x/config": {"payload": {"a": 1}}}, prefix="auto")
    res = p.list_backups()
    assert res["total"] == 1
    names = [b["name"] for b in res["backups"]]
    assert len(names) == 1 and names[0].startswith("auto-")


def test_list_backups_caps_to_limit(tmp_path):
    """The UI listing is bounded: with more files than the limit, only the
    newest `limit` are returned but `total` reports the full count."""
    p = DiscoveryPlugin(FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG))
    p.backup_dir = str(tmp_path)
    d = Path(tmp_path)
    for i in range(8):
        # Distinct mtimes so newest-first ordering is well-defined.
        f = d / f"manual-{i:02d}.json"
        f.write_text('{"topics": {}}', encoding="utf-8")
        os.utime(f, (1000 + i, 1000 + i))
    res = p.list_backups(limit=3)
    assert res["total"] == 8
    assert [b["name"] for b in res["backups"]] == ["manual-07.json", "manual-06.json", "manual-05.json"]


# ── custom converters editing (M5) ───────────────────────────────────────
def _isolated_converters(monkeypatch, tmp_path):
    """Point converter resolution at an empty tmp *directory* (the drop-in model),
    so tests don't read/write the repo's or packaged converters."""
    d = tmp_path / "custom_converters"
    d.mkdir()
    monkeypatch.setenv("RUSTUYA_CONVERTERS", str(d))
    return d


def test_converters_files_lists_fleet_products(monkeypatch, tmp_path):
    _isolated_converters(monkeypatch, tmp_path)
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    info = p.converters_files()
    pids = {pr["product_id"] for pr in info["products"]}
    assert pids == {d["product_id"] for d in DEVICES if d.get("product_id")}
    assert all(pr["has_override"] is False for pr in info["products"])  # empty dir
    assert info["files"] == []


def test_save_converter_file_persists_backs_up_and_reloads(monkeypatch, tmp_path):
    d = _isolated_converters(monkeypatch, tmp_path)
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    p.backup_dir = str(tmp_path / "bk")
    pid = DEVICES[0]["product_id"]

    res = p.save_converter_file("a.json", json.dumps({pid: {"model": "SAVED-Y"}}))
    assert res["kind"] == "json" and res["restart_required"] is False
    assert res["backup"] is None  # no prior file
    assert json.load(open(d / "a.json"))[pid]["model"] == "SAVED-Y"
    # generator reloaded from disk → it now applies the saved override
    payloads, _ = p.generator.generate(DEVICES[0])
    assert "SAVED-Y" in json.dumps(payloads)

    # overwriting the same file backs up the prior version
    res2 = p.save_converter_file("a.json", json.dumps({pid: {"model": "SAVED-Z"}}))
    assert res2["backup"] is not None and Path(res2["backup"]).is_file()

    # listing + reading the file back
    info = p.converters_files()
    assert {f["name"] for f in info["files"]} == {"a.json"}
    assert "SAVED-Z" in p.read_converter_file("a.json")["content"]

    # delete removes the file (and its overrides)
    res3 = p.delete_converter_file("a.json")
    assert res3["deleted"] is True and not (d / "a.json").exists()
    assert p.converters_files()["files"] == []


def test_save_converter_file_py_needs_restart_and_compiles(monkeypatch, tmp_path):
    d = _isolated_converters(monkeypatch, tmp_path)
    p = DiscoveryPlugin(FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG))
    p.backup_dir = str(tmp_path / "bk")
    res = p.save_converter_file("hook.py", "def setup(api):\n    pass\n")
    assert res["kind"] == "py" and res["restart_required"] is True
    assert (d / "hook.py").read_text().startswith("def setup")
    # a .py listed alongside .json, both surfaced by converters_files
    p.save_converter_file("x.json", "{}")
    assert {f["name"] for f in p.converters_files()["files"]} == {"hook.py", "x.json"}


def test_save_converter_file_rejects_bad_content(monkeypatch, tmp_path):
    d = _isolated_converters(monkeypatch, tmp_path)
    p = DiscoveryPlugin(FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG))
    p.backup_dir = str(tmp_path / "bk")
    with pytest.raises(ValueError):  # override that breaks generation
        p.save_converter_file(
            "bad.json", json.dumps({DEVICES[0]["product_id"]: {"dp_meta": "not-a-dict"}})
        )
    with pytest.raises(ValueError, match="syntax"):  # Python that won't compile
        p.save_converter_file("bad.py", "def setup(api:\n")
    with pytest.raises(ValueError):  # path traversal
        p.save_converter_file("../escape.json", "{}")
    assert list(d.iterdir()) == []  # nothing persisted on any refusal


def test_pack_managed_files_are_read_only(monkeypatch, tmp_path):
    d = _isolated_converters(monkeypatch, tmp_path)
    p = DiscoveryPlugin(FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG))
    p.backup_dir = str(tmp_path / "bk")
    pid = DEVICES[0]["product_id"]
    # A pack-managed default (recorded in the ledger) sits next to a user file.
    (d / "00_default.json").write_text(json.dumps({pid: {"model": "PACK"}}))
    (d / ".rustuya_pack.json").write_text(json.dumps({"version": 1, "files": {"00_default.json": "x"}}))
    p.save_converter_file("99_mine.json", json.dumps({pid: {"model": "MINE"}}))

    # converters_files flags which files the pack owns
    by_name = {f["name"]: f for f in p.converters_files()["files"]}
    assert by_name["00_default.json"]["managed"] is True
    assert by_name["99_mine.json"]["managed"] is False

    # the managed default is read-only: save + delete both refused, file untouched
    with pytest.raises(ValueError, match="read-only"):
        p.save_converter_file("00_default.json", json.dumps({pid: {"model": "HACK"}}))
    with pytest.raises(ValueError, match="read-only"):
        p.delete_converter_file("00_default.json")
    assert json.load(open(d / "00_default.json"))[pid]["model"] == "PACK"

    # a user file is still editable + deletable
    p.save_converter_file("99_mine.json", json.dumps({pid: {"model": "MINE2"}}))
    p.delete_converter_file("99_mine.json")
    assert not (d / "99_mine.json").exists()


def test_save_converter_file_replaces_file(monkeypatch, tmp_path):
    d = _isolated_converters(monkeypatch, tmp_path)
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG)
    p = DiscoveryPlugin(ctx)
    p.backup_dir = str(tmp_path / "bk")
    pid = DEVICES[0]["product_id"]

    res = p.save_converter_file("c.json", json.dumps({pid: {"model": "ALL-Y"}}))
    assert res["restart_required"] is False
    assert json.load(open(d / "c.json")) == {pid: {"model": "ALL-Y"}}
    payloads, _ = p.generator.generate(DEVICES[0])
    assert "ALL-Y" in json.dumps(payloads)

    # rewriting the same file replaces its content (and backs it up)
    res2 = p.save_converter_file("c.json", "{}")
    assert res2["backup"] is not None
    assert json.load(open(d / "c.json")) == {}


def test_save_converter_file_rejects_bad_json_shape(monkeypatch, tmp_path):
    d = _isolated_converters(monkeypatch, tmp_path)
    p = DiscoveryPlugin(FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG))
    p.backup_dir = str(tmp_path / "bk")
    with pytest.raises(ValueError, match="object of product_id"):
        p.save_converter_file("x.json", json.dumps(["not", "a", "dict"]))
    with pytest.raises(ValueError, match="must be a JSON object"):
        p.save_converter_file("y.json", json.dumps({DEVICES[0]["product_id"]: "not-a-dict"}))
    with pytest.raises(ValueError, match="invalid JSON"):
        p.save_converter_file("z.json", "{not json")
    assert list(d.iterdir()) == []


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


def test_register_declares_topic_retain_requirements_on_v3_host():
    pytest.importorskip("fastapi")
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG, api_version=3)
    register(ctx)
    # retain=True and {type} in the event topic are what HA discovery needs for
    # full fidelity; {dp} is deliberately not required (generator adapts).
    assert ctx.retain_required_by == ["HA Discovery"]
    assert ("HA Discovery", "event", ("type",), ()) in ctx.topic_requirements
    assert all(t[1] != "command" for t in ctx.topic_requirements)


def test_register_skips_requirements_on_pre_v3_host():
    pytest.importorskip("fastapi")
    ctx = FakeCtx(devices=DEVICES, bridge_cfg=LEGACY_CFG, api_version=2)
    register(ctx)
    assert ctx.retain_required_by == []
    assert ctx.topic_requirements == []


# ── code converters (api_version >= 2 reactive runtime) ──────────────────
class _RuntimeCtx:
    """Fake host implementing the api_version>=2 reactive runtime: enough to
    exercise the ConverterApi facade (on_product/on_device/derive). `emit`
    mirrors the manager's _dispatch_dp_watchers fan-out."""

    def __init__(self, devices, api_version=2, dps=None):
        self.api_version = api_version
        self._devices = {d["id"]: d for d in devices}
        self._dps = dps or {}   # {device_id: {dp: value}} live snapshot
        self.watchers = []   # (device_id|None, dp|None, handler)
        self.derived = []    # (device_id, dp, value)

    def devices(self):
        return dict(self._devices)

    def current_dps(self, device_id=None):
        if device_id is not None:
            return dict(self._dps.get(device_id, {}))
        return {d: dict(v) for d, v in self._dps.items()}

    def watch_dps(self, handler):
        self.watchers.append((None, None, handler))

    def watch_device(self, device_id, handler):
        self.watchers.append((device_id, None, handler))

    def watch_dp(self, device_id, dp, handler):
        self.watchers.append((device_id, dp, handler))

    def derived_dp(self, device_id, dp, *, retain=None):
        rec = self.derived

        class _D:
            async def set(self, value):
                rec.append((device_id, dp, value))

            async def clear(self):
                rec.append((device_id, dp, None))

        return _D()

    async def emit(self, device_id, dps, origin="device"):
        for did, dp, handler in self.watchers:
            if did is not None and did != device_id:
                continue
            if dp is not None and dp not in dps:
                continue
            await handler(device_id, dps, origin)


def test_on_product_filters_by_product_and_keeps_per_device_state():
    from rustuya_ha.manager_plugin.code_converters import ConverterApi

    ctx = _RuntimeCtx([
        {"id": "dev-a", "product_id": "P"},
        {"id": "dev-b", "product_id": "P"},
        {"id": "dev-c", "product_id": "Q"},
    ])
    api = ConverterApi(ctx, {})  # no JSON mapping needed
    seen = []

    @api.on_product("P")
    async def _(device_id, dps, origin):
        seen.append(device_id)
        await api.derive(device_id, "99", dps.get("1"))

    async def run():
        await ctx.emit("dev-c", {"1": "x"})  # product Q → skipped entirely
        await ctx.emit("dev-a", {"1": "a"})  # product P → handled
        await ctx.emit("dev-b", {"1": "b"})  # product P → handled, independent

    asyncio.run(run())
    assert seen == ["dev-a", "dev-b"]
    assert ctx.derived == [("dev-a", "99", "a"), ("dev-b", "99", "b")]


def test_load_code_converters_runs_setup_and_gates_on_api_version(monkeypatch, tmp_path):
    from rustuya_ha.manager_plugin import code_converters

    d = _isolated_converters(monkeypatch, tmp_path)
    (d / "cc.py").write_text(
        "def setup(api):\n"
        "    @api.on_product('P')\n"
        "    async def _(device_id, dps, origin):\n"
        "        await api.derive(device_id, '99', dps.get('1'))\n",
        encoding="utf-8",
    )
    devices = [{"id": "dev-a", "product_id": "P"}]

    old = _RuntimeCtx(devices, api_version=1)  # too old → no-op, nothing wired
    assert code_converters.load_code_converters(old) == 0
    assert old.watchers == []

    ctx = _RuntimeCtx(devices, api_version=2)  # loaded + wired
    assert code_converters.load_code_converters(ctx) == 1
    asyncio.run(ctx.emit("dev-a", {"1": "v"}))
    assert ctx.derived == [("dev-a", "99", "v")]


def test_current_dps_facade_reads_snapshot_and_tolerates_old_host():
    from rustuya_ha.manager_plugin.code_converters import ConverterApi

    ctx = _RuntimeCtx([{"id": "dev-a", "product_id": "P"}], dps={"dev-a": {"3": 100}})
    api = ConverterApi(ctx, {})
    assert api.current_dps("dev-a") == {"3": 100}
    assert api.current_dps("nope") == {}
    assert api.current_dps() == {"dev-a": {"3": 100}}

    # A host without current_dps (older runtime) degrades to {}, not an error.
    class _Old:
        api_version = 2

    assert ConverterApi(_Old(), {}).current_dps("dev-a") == {}
