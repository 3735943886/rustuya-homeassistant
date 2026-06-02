"""Unit tests for the pure config->seam derive helpers (no MQTT/pyrustuyabridge)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core.bridge import (  # noqa: E402
    BridgeConfig, render_topic, placeholder_path, jinja_accessor, LEGACY,
)
from rustuya_ha.core.scheme import BridgeTopicScheme, BridgePayloadCodec  # noqa: E402


# --- render_topic ---

def test_render_topic_substitutes_known_leaves_unknown():
    assert render_topic("{root}/event/{id}/{dp}", root="rustuya", id="dev", dp="5") == "rustuya/event/dev/5"
    # unknown placeholder stays literal
    assert render_topic("{root}/x/{name}", root="r", id="dev") == "r/x/{name}"


# --- placeholder_path ---

def test_placeholder_path_bare_value_is_root():
    assert placeholder_path("{value}", "value") == []


def test_placeholder_path_object_key():
    tpl = '{"type": "{type}", "value": {value}, "name": "{name}"}'
    assert placeholder_path(tpl, "value") == ["value"]
    assert placeholder_path(tpl, "type") == ["type"]


def test_placeholder_path_nested():
    assert placeholder_path('{"data": {"v": {value}}}', "value") == ["data", "v"]


def test_placeholder_path_missing_or_non_json():
    assert placeholder_path('{"value": {value}}', "dps") is None
    assert placeholder_path("v={value};ts={timestamp}", "value") is None  # text-style, not JSON


# --- jinja_accessor ---

def test_jinja_accessor_dot_bracket_index():
    assert jinja_accessor("value_json", []) == "value_json"
    assert jinja_accessor("value_json", ["value"]) == "value_json.value"
    assert jinja_accessor("value_json", ["data", "v"]) == "value_json.data.v"
    assert jinja_accessor("value_json", [], index="2") == "value_json['2']"
    assert jinja_accessor("value_json", ["dps"], index="2") == "value_json.dps['2']"


# --- topology detection ---

def test_per_dp_detection():
    assert LEGACY.per_dp is True
    multi = BridgeConfig.from_dict({
        "mqtt_event_topic": "{root}/event/{type}/{id}",
        "mqtt_payload_template": '{"dps": {dps}}',
    })
    assert multi.per_dp is False
    assert multi.value_path() == ["dps"]


# --- multi-DP value_template indexes by dp ---

def test_multi_dp_value_template_indexes():
    multi = BridgeConfig.from_dict({
        "mqtt_event_topic": "{root}/event/{type}/{id}",
        "mqtt_payload_template": '{"dps": {dps}}',
    })
    codec = BridgePayloadCodec(multi)
    tpl = codec.value_template("sensor", dp_id="2")
    assert "value_json.dps['2']" in tpl
    # per-DP (legacy) does NOT index
    legacy_codec = BridgePayloadCodec(LEGACY)
    assert "value_json.value" in legacy_codec.value_template("sensor", dp_id="2")
    assert "['2']" not in legacy_codec.value_template("sensor", dp_id="2")


def test_bridge_topic_scheme_multi_dp_same_device_topic():
    multi = BridgeConfig.from_dict({"mqtt_event_topic": "{root}/event/{type}/{id}"})
    s = BridgeTopicScheme(multi)
    # no {dp} in template -> all dps share one device-level topic; {type}->passive
    assert s.state("dev", "1") == s.state("dev", "2") == "rustuya/event/passive/dev"


def test_skip_active_gating():
    """Stateful entities go passive-only ONLY when retain=true AND payload carries
    {type}; otherwise they must accept whatever arrives (any config supported)."""
    def vt(cfg):
        return BridgePayloadCodec(BridgeConfig.from_dict(cfg)).value_template("sensor", dp_id="2")

    base = {"mqtt_event_topic": "{root}/event/{id}/{dp}"}
    with_type = {**base, "mqtt_payload_template": '{"type":"{type}","value":{value}}'}
    no_type = {**base, "mqtt_payload_template": '{"value":{value}}'}

    assert "== 'active' else" in vt({**with_type, "mqtt_retain": True})    # skip
    assert "== 'active' else" not in vt({**with_type, "mqtt_retain": False})  # no passive -> no skip
    assert "== 'active' else" not in vt({**no_type, "mqtt_retain": True})     # can't tell -> no skip


def test_event_active_filter_gated_on_payload_type():
    """The event template filters to `active` only when the payload carries
    {type}. Without it (e.g. topic separates type), it must NOT reference a
    missing value_json.type — else the event never fires."""
    def ev(cfg):
        return BridgePayloadCodec(BridgeConfig.from_dict(cfg)).value_template("event", dp_id="1")

    sep_topic_no_payload_type = {"mqtt_event_topic": "{root}/event/{type}/{id}/{dp}",
                                 "mqtt_payload_template": "{value}", "mqtt_retain": True}
    # no active filter -> fires (note: "event_type" literal is always present)
    assert "== 'active'" not in ev(sep_topic_no_payload_type)
    assert "value_json.type" not in ev(sep_topic_no_payload_type)

    with_payload_type = {"mqtt_event_topic": "{root}/event/{type}/{id}/{dp}",
                         "mqtt_payload_template": '{"type":"{type}","value":{value}}'}
    assert "== 'active' else ''" in ev(with_payload_type)  # keeps active filter


def test_state_active_vs_passive_topic():
    # bridge README: event entities read active topic, stateful entities passive.
    s = BridgeTopicScheme(BridgeConfig.from_dict({"mqtt_event_topic": "{root}/event/{type}/{id}/{dp}"}))
    assert s.state("dev", "5") == "rustuya/event/passive/dev/5"
    assert s.state("dev", "5", active=True) == "rustuya/event/active/dev/5"
    # legacy (no {type}) is unaffected by the flag
    assert BridgeTopicScheme(LEGACY).state("dev", "5", active=True) == "rustuya/event/dev/5"
