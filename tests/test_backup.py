"""Backup/restore: save/load roundtrip + the pure restore_plan revert logic."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.cli import backup  # noqa: E402
from rustuya_ha.core import backup as core_backup  # noqa: E402  (snapshot_file not re-exported by cli)


def _mqtt(topics):
    return {t: {"payload": p, "retain": True} for t, p in topics.items()}


def test_save_load_roundtrip(tmp_path):
    data = _mqtt({
        "homeassistant/sensor/d1_temp/config": {"name": None, "unique_id": "d1_temp"},
        "homeassistant/switch/d1_sw/config": {"name": "S", "unique_id": "d1_sw"},
    })
    path = backup.save(str(tmp_path), data, prefix="auto")
    assert Path(path).exists()
    loaded = backup.load(path)
    assert loaded == {t: m["payload"] for t, m in data.items()}


def test_latest_and_listing(tmp_path):
    assert backup.latest(str(tmp_path)) is None
    p1 = backup.save(str(tmp_path), _mqtt({"a/config": {"x": 1}}), prefix="auto")
    p2 = backup.save(str(tmp_path), _mqtt({"b/config": {"x": 2}}), prefix="snap")
    files = backup.listing(str(tmp_path))
    assert {f.name for f in files} == {Path(p1).name, Path(p2).name}
    # newest by mtime is returned by latest()
    assert backup.latest(str(tmp_path)) in (p1, p2)


def test_rotate_keeps_recent(tmp_path):
    for i in range(backup.KEEP + 5):
        backup.save(str(tmp_path), _mqtt({f"t{i}/config": {"i": i}}), prefix="auto")
    autos = list(Path(tmp_path).glob("auto-*.json"))
    assert len(autos) == backup.KEEP


def test_listing_limit(tmp_path):
    for i in range(5):
        f = Path(tmp_path) / f"manual-{i}.json"
        f.write_text('{"topics": {}}', encoding="utf-8")
        os.utime(f, (1000 + i, 1000 + i))  # distinct mtimes -> deterministic order
    assert len(backup.listing(str(tmp_path))) == 5  # no limit -> all
    capped = backup.listing(str(tmp_path), limit=2)
    assert [p.name for p in capped] == ["manual-4.json", "manual-3.json"]  # newest first


def test_snapshot_file_rotates(tmp_path):
    """converters snapshots must rotate like save() does, else they grow forever."""
    src = Path(tmp_path) / "custom_converters.json"
    src.write_text("{}", encoding="utf-8")
    for _ in range(core_backup.KEEP + 3):
        core_backup.snapshot_file(str(tmp_path), str(src), prefix="converters")
    snaps = list(Path(tmp_path).glob("converters-*.json"))
    assert len(snaps) == core_backup.KEEP


def test_restore_plan_sets_snapshot_and_clears_additions():
    snapshot = {
        "homeassistant/sensor/d1_temp/config": {"unique_id": "d1_temp"},
        "homeassistant/switch/d1_sw/config": {"unique_id": "d1_sw"},
    }
    # live state: one snapshot topic modified-away is still set; one NEW topic added.
    current = {
        "homeassistant/sensor/d1_temp/config",
        "homeassistant/switch/d1_sw/config",
        "homeassistant/light/d1_new/config",  # added after snapshot -> must be cleared
    }
    set_msgs, clear_msgs = backup.restore_plan(snapshot, current)

    set_topics = {m["topic"] for m in set_msgs}
    assert set_topics == set(snapshot)
    assert all(m["retain"] and m["payload"] != "" for m in set_msgs)
    # payloads are JSON-serialized snapshot values
    by_topic = {m["topic"]: json.loads(m["payload"]) for m in set_msgs}
    assert by_topic["homeassistant/sensor/d1_temp/config"] == snapshot["homeassistant/sensor/d1_temp/config"]

    assert [m["topic"] for m in clear_msgs] == ["homeassistant/light/d1_new/config"]
    assert all(m["payload"] == "" and m["retain"] for m in clear_msgs)


def test_restore_plan_noop_when_identical():
    snap = {"a/config": {"x": 1}}
    set_msgs, clear_msgs = backup.restore_plan(snap, {"a/config"})
    assert len(set_msgs) == 1 and clear_msgs == []
