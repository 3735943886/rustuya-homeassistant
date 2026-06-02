"""Proof that the bridge-config derive logic is correct: fed the legacy profile
(the actual bridge config that matched the pre-refactor hardcoded output), the
Bridge scheme/codec must reproduce the Default ones AND the golden snapshots
byte-for-byte.

Legacy bridge config (tests/fixtures/legacy_bridge_config.json):
    mqtt_event_topic   = {root}/event/{id}/{dp}
    mqtt_command_topic = {root}/command/{action}/{id}/{dp}
    mqtt_message_topic = {root}/{level}/{id}
    mqtt_payload_template = {"type":"{type}","value":{value},"name":"{name}"}
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha import UserConverter, scheme_for, BridgeConfig  # noqa: E402
from rustuya_ha.core.generator import DiscoveryGenerator  # noqa: E402
from rustuya_ha.core.scheme import (  # noqa: E402
    DefaultTopicScheme, DefaultPayloadCodec, BridgeTopicScheme, BridgePayloadCodec,
)
from rustuya_ha.core.bridge import LEGACY  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_bridge_config.json"
REAL_DEVICES_PATH = ROOT / "tuyadevices.json"
SYNTH_DEVICES_PATH = Path(__file__).parent / "fixtures" / "synthetic_devices.json"
REAL_SNAPSHOT_PATH = Path(__file__).parent / "golden" / "snapshots.json"
SYNTH_SNAPSHOT_PATH = Path(__file__).parent / "golden" / "synthetic_snapshots.json"


def _load(path):
    with open(path) as f:
        return json.load(f)


def test_fixture_matches_embedded_legacy():
    """The stored fixture and the in-code LEGACY constant must agree."""
    assert BridgeConfig.from_dict(_load(FIXTURE)) == LEGACY


# --- scheme/codec string parity with the Default (hardcoded) implementations ---

def test_bridge_topic_scheme_matches_default():
    bs, ds = BridgeTopicScheme(LEGACY), DefaultTopicScheme()
    for dev, dp in [("dev1", "5"), ("abc", "105")]:
        assert bs.state(dev, dp) == ds.state(dev, dp)
        assert bs.command(dev, dp) == ds.command(dev, dp)
        assert bs.availability(dev) == ds.availability(dev)
    assert bs.discovery("sensor", "dev1", "temp") == ds.discovery("sensor", "dev1", "temp")


def test_bridge_payload_codec_matches_default():
    bc, dc = BridgePayloadCodec(LEGACY), DefaultPayloadCodec()
    assert bc.availability_template() == dc.availability_template()
    cases = [
        ("sensor", 0, None), ("sensor", 1, None), ("switch", 0, None),
        ("binary_sensor", 0, None), ("event", 0, None),
        ("select", 0, {"on": "1", "off": "0"}), ("number", 2, None),
    ]
    for comp, scale, vmap in cases:
        assert bc.value_template(comp, scale, vmap, dp_id="9") == dc.value_template(comp, scale, vmap), \
            f"value_template drift for {comp}/{scale}/{vmap}"


# --- end-to-end: generator under legacy bridge config == golden snapshots ---

def _legacy_generator():
    scheme, codec = scheme_for(LEGACY)
    return DiscoveryGenerator(UserConverter(), scheme=scheme, codec=codec)


def _assert_snapshot(devices_path, snapshot_path, device_id):
    devices = _load(devices_path)
    snapshot = _load(snapshot_path)
    device = next(d for d in devices if d.get("id") == device_id)
    expected = snapshot[device_id]
    payloads, source = _legacy_generator().generate(device)
    assert source == expected["source"]
    assert set(payloads) == set(expected["payloads"])
    for topic in expected["payloads"]:
        assert payloads[topic] == expected["payloads"][topic], f"payload drift on {topic}"


def _ids(path):
    return [d["id"] for d in _load(path) if d.get("id")] if path.exists() else []


@pytest.mark.skipif(not REAL_DEVICES_PATH.exists(), reason="tuyadevices.json not present")
@pytest.mark.parametrize("device_id", _ids(REAL_DEVICES_PATH))
def test_legacy_real_matches_golden(device_id):
    _assert_snapshot(REAL_DEVICES_PATH, REAL_SNAPSHOT_PATH, device_id)


@pytest.mark.parametrize("device_id", _ids(SYNTH_DEVICES_PATH))
def test_legacy_synthetic_matches_golden(device_id):
    _assert_snapshot(SYNTH_DEVICES_PATH, SYNTH_SNAPSHOT_PATH, device_id)
