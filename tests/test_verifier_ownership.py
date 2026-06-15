"""Ownership scoping in DiscoveryVerifier.verify().

Only discovery we publish (carrying our ``device.manufacturer`` marker) is ever
classified as unexpected/orphan; configs from other MQTT-discovery integrations
sharing the broker are out of scope entirely (never flagged, never clearable).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core.scheme import DEFAULT_MANUFACTURER  # noqa: E402
from rustuya_ha.core.verifier import DiscoveryVerifier  # noqa: E402


class _FakeGen:
    """Generator stub: each device produces the topics handed to it (default none)."""

    def __init__(self, expected=None):
        self._expected = expected or {}

    def generate(self, device):
        return dict(self._expected.get(device["id"], {})), None


def _config(dev_id, manufacturer=DEFAULT_MANUFACTURER):
    return {
        "name": dev_id,
        "device": {"identifiers": [dev_id], "manufacturer": manufacturer},
    }


def _msgs(mapping):
    return {topic: {"payload": payload, "retain": True} for topic, payload in mapping.items()}


def test_foreign_config_is_not_an_orphan():
    """A retained config from another integration (foreign manufacturer, owner not
    in the fleet) is ignored — not an orphan — while one bearing our marker is."""
    v = DiscoveryVerifier(_FakeGen())
    msgs = _msgs({
        "homeassistant/sensor/zigbee2mqtt_x/config": _config("z1", manufacturer="zigbee2mqtt"),
        "homeassistant/sensor/ours_stale/config": _config("gone"),  # our marker, device removed
    })
    out = v.verify(devices=[], mqtt_messages=msgs)

    orphan_topics = {t for o in out["orphans"] for t in o["topics"]}
    assert orphan_topics == {"homeassistant/sensor/ours_stale/config"}


def test_foreign_extra_on_known_device_is_not_unexpected():
    """An unexpected topic is only flagged when it carries our marker. A foreign
    config that happens to reference a known device id stays out of scope."""
    fleet = [{"id": "d1", "name": "d1"}]
    v = DiscoveryVerifier(_FakeGen())  # generator emits nothing -> nothing expected
    msgs = _msgs({
        "homeassistant/sensor/d1_extra/config": _config("d1"),                       # ours -> unexpected
        "homeassistant/sensor/d1_foreign/config": _config("d1", manufacturer="esphome"),  # foreign -> ignored
    })
    out = v.verify(devices=fleet, mqtt_messages=msgs)

    unexpected = [d for d in out["unexpected_topics"] if d["id"] == "d1"]
    assert unexpected and unexpected[0]["unexpected"] == ["homeassistant/sensor/d1_extra/config"]
    assert out["orphans"] == []


def test_missing_device_block_is_out_of_scope():
    """A malformed/foreign config without a device block isn't ours -> ignored."""
    v = DiscoveryVerifier(_FakeGen())
    msgs = _msgs({"homeassistant/sensor/weird/config": {"name": "weird"}})
    out = v.verify(devices=[], mqtt_messages=msgs)
    assert out["orphans"] == []
