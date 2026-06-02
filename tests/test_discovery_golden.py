"""Golden snapshot tests for tuya_discovery_generator.

Two snapshot suites:

1. Real devices (tuyadevices.json + tests/golden/snapshots.json) — gitignored
   private data, ~90% verified working in HA. This is the strict regression
   barrier: anything here breaking means a real regression.

2. Synthetic devices (tests/fixtures/synthetic_devices.json + tests/golden/
   synthetic_snapshots.json) — tracked. Covers handler entry points
   (climate/fan/light) that no real device exercises. Their payloads are NOT
   verified correct; the snapshot only locks "whatever generator does now" so
   refactors can't silently divert these branches.

Regenerate baseline when behavior intentionally changes:

    python3 tests/generate_snapshots.py
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha import initialize_generator  # noqa: E402

REAL_DEVICES_PATH = ROOT / "tuyadevices.json"
SYNTH_DEVICES_PATH = Path(__file__).parent / "fixtures" / "synthetic_devices.json"
REAL_SNAPSHOT_PATH = Path(__file__).parent / "golden" / "snapshots.json"
SYNTH_SNAPSHOT_PATH = Path(__file__).parent / "golden" / "synthetic_snapshots.json"


@pytest.fixture(scope="session")
def generator():
    return initialize_generator()


def _load_json(path: Path):
    with open(path) as f:
        return json.load(f)


def _load_device_ids(path: Path):
    if not path.exists():
        return []
    return [d["id"] for d in _load_json(path) if d.get("id")]


def _assert_matches_snapshot(generator, devices_path, snapshot_path, device_id):
    if not snapshot_path.exists():
        pytest.fail(
            f"Snapshot missing: {snapshot_path}\n"
            "Run: python3 tests/generate_snapshots.py"
        )
    devices = _load_json(devices_path)
    snapshot = _load_json(snapshot_path)

    device = next(d for d in devices if d.get("id") == device_id)
    assert device_id in snapshot, (
        f"Device {device_id} has no snapshot entry. "
        f"Regenerate: python3 tests/generate_snapshots.py"
    )
    expected = snapshot[device_id]

    payloads, source = generator.generate(device)

    assert source == expected["source"], (
        f"source label changed for {device_id} ({device.get('name')}): "
        f"{expected['source']!r} -> {source!r}"
    )

    actual_topics = set(payloads)
    expected_topics = set(expected["payloads"])

    missing = expected_topics - actual_topics
    extra = actual_topics - expected_topics
    assert not missing and not extra, (
        f"topic set drift for {device_id} ({device.get('name')})\n"
        f"  missing: {sorted(missing)}\n"
        f"  extra:   {sorted(extra)}"
    )

    for topic in sorted(expected_topics):
        assert payloads[topic] == expected["payloads"][topic], (
            f"payload drift for {device_id} ({device.get('name')}) on {topic}\n"
            f"  expected: {json.dumps(expected['payloads'][topic], sort_keys=True)}\n"
            f"  actual:   {json.dumps(payloads[topic], sort_keys=True)}"
        )


REAL_DEVICE_IDS = _load_device_ids(REAL_DEVICES_PATH)
SYNTH_DEVICE_IDS = _load_device_ids(SYNTH_DEVICES_PATH)


@pytest.mark.skipif(not REAL_DEVICES_PATH.exists(), reason="tuyadevices.json not present")
@pytest.mark.parametrize("device_id", REAL_DEVICE_IDS)
def test_real_device_matches_snapshot(device_id, generator):
    _assert_matches_snapshot(generator, REAL_DEVICES_PATH, REAL_SNAPSHOT_PATH, device_id)


@pytest.mark.parametrize("device_id", SYNTH_DEVICE_IDS)
def test_synthetic_device_matches_snapshot(device_id, generator):
    _assert_matches_snapshot(generator, SYNTH_DEVICES_PATH, SYNTH_SNAPSHOT_PATH, device_id)


@pytest.mark.skipif(not REAL_DEVICES_PATH.exists(), reason="tuyadevices.json not present")
def test_real_snapshot_covers_every_device():
    """No new real device should silently fall out of snapshot coverage."""
    device_ids = {d["id"] for d in _load_json(REAL_DEVICES_PATH) if d.get("id")}
    snapshot_ids = set(_load_json(REAL_SNAPSHOT_PATH))
    assert device_ids == snapshot_ids, (
        f"snapshot/device id mismatch\n"
        f"  not in snapshot: {sorted(device_ids - snapshot_ids)}\n"
        f"  not in devices:  {sorted(snapshot_ids - device_ids)}"
    )


def test_synthetic_snapshot_covers_every_device():
    device_ids = {d["id"] for d in _load_json(SYNTH_DEVICES_PATH) if d.get("id")}
    snapshot_ids = set(_load_json(SYNTH_SNAPSHOT_PATH))
    assert device_ids == snapshot_ids
