"""Tests for custom_converters `discovery_overrides` (component-level payload patch).

dp_meta can only retune per-DP metadata; some devices need the *generated
discovery payload* itself patched — e.g. a curtain whose position is inverted
(HA 0=closed/100=open vs the motor's reading) and whose open/close/stop command
enum is lowercase. These tests lock that the override hook merges such fields,
resolves `<role>_dp` keys through the scheme, and stays a no-op when absent.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core.converter import UserConverter  # noqa: E402
from rustuya_ha.core.generator import DiscoveryGenerator  # noqa: E402

PRODUCT_ID = "f6jujmx0is5td50x"

# Synthetic curtain motor — same DP layout as a real cl/curtain device
# (1=control enum, 2=percent_control, 3=percent_state) but with public ids so
# the test runs without the gitignored tuyadevices.json.
CURTAIN_DEVICE = {
    "id": "dev_curtain_demo",
    "name": "demo_curtain",
    "category": "cl",
    "product_id": PRODUCT_ID,
    "product_name": "curtain motor",
    "function": {
        "control": {"code": "control", "type": "Enum",
                    "values": {"range": ["open", "stop", "close", "continue"]}},
        "percent_control": {"code": "percent_control", "type": "Integer",
                            "values": {"unit": "%", "min": 0, "max": 100, "scale": 0, "step": 1}},
    },
    "local_strategy": {
        "1": {"status_code": "control", "config_item": {"valueType": "Enum",
              "valueDesc": {"range": ["open", "stop", "close", "continue"]}}},
        "2": {"status_code": "percent_control", "config_item": {"valueType": "Integer",
              "valueDesc": {"unit": "%", "min": 0, "max": 100, "scale": 0, "step": 1}}},
        "3": {"status_code": "percent_state", "config_item": {"valueType": "Integer",
              "valueDesc": {"unit": "%", "min": 0, "max": 100, "scale": 0, "step": 1}}},
    },
}

COVER_TOPIC = "homeassistant/cover/dev_curtain_demo_motor/config"


def _generator(converters: dict, tmp_path, name="custom_converters.json") -> DiscoveryGenerator:
    path = tmp_path / name
    path.write_text(json.dumps(converters))
    return DiscoveryGenerator(UserConverter(str(path)))


def test_no_overrides_is_noninferior(tmp_path):
    """A converter entry without discovery_overrides leaves the payload untouched."""
    plain = _generator({PRODUCT_ID: {"model": "Curtain Motor"}}, tmp_path)
    # Baseline must be override-free: an empty converter file, NOT DiscoveryGenerator()
    # which would pick up the repo-root custom_converters.json (and its real curtain
    # override) via CWD resolution.
    baseline = _generator({}, tmp_path, name="empty.json")

    over_payloads, _ = plain.generate(CURTAIN_DEVICE)
    base_payloads, _ = baseline.generate(CURTAIN_DEVICE)

    # Only the model label differs (that's dp_meta-level, not an override).
    assert set(over_payloads) == set(base_payloads)
    cover = over_payloads[COVER_TOPIC]
    base_cover = base_payloads[COVER_TOPIC]
    assert {k: v for k, v in cover.items() if k != "device"} == \
           {k: v for k, v in base_cover.items() if k != "device"}


def test_cover_override_merges_and_resolves_dp(tmp_path):
    gen = _generator({
        PRODUCT_ID: {
            "model": "Curtain Motor",
            "discovery_overrides": {
                "cover": {
                    "position_dp": "2",  # read position back off percent_control, not percent_state
                    "invert_position": True,
                    "invert_set_position": True,
                    "payload_open": "open", "payload_close": "close", "payload_stop": "stop",
                    "state_opening": "open", "state_closing": "close", "state_stopped": "stop",
                },
            },
        },
    }, tmp_path)

    payloads, source = gen.generate(CURTAIN_DEVICE)
    cover = payloads[COVER_TOPIC]

    assert source == "custom"
    # literal fields merged verbatim
    assert cover["payload_open"] == "open"
    assert cover["payload_close"] == "close"
    assert cover["payload_stop"] == "stop"
    assert cover["state_opening"] == "open"
    # command-side inversion (no codec seam for command encoding -> legacy raw int)
    assert cover["set_position_template"] == "{{ 100 - value | int }}"
    # position_dp resolved to a topic via the scheme (DP 2, not the default DP 3)
    assert cover["position_topic"] == "rustuya/event/dev_curtain_demo/2"
    # set_position command topic untouched by the override (still DP 2 from the builder)
    assert cover["set_position_topic"] == "rustuya/command/set/dev_curtain_demo/2"
    # read-side position template went through the codec: inverted AND payload-shape
    # aware (carries value_json.value via the codec, NOT a hardcoded literal), and
    # keeps the active/passive filter the codec emits for stateful entities.
    assert "100 - ((value_json.value) | int)" in cover["position_template"]
    assert cover["position_template"].startswith("{{")
    # structured keys must not leak into the payload verbatim
    assert "position_dp" not in cover
    assert "invert_position" not in cover


def test_invert_position_and_set_position_are_independent(tmp_path):
    """Read- and write-direction inversion are separate flags."""
    # read inverted, write NOT inverted -> no set_position_template
    read_only = _generator({PRODUCT_ID: {"discovery_overrides": {"cover": {
        "position_dp": "2", "invert_position": True}}}}, tmp_path, name="a.json")
    cover = read_only.generate(CURTAIN_DEVICE)[0][COVER_TOPIC]
    assert "100 - ((value_json.value) | int)" in cover["position_template"]
    assert "set_position_template" not in cover

    # write inverted, read NOT inverted -> set_position_template present, position plain
    write_only = _generator({PRODUCT_ID: {"discovery_overrides": {"cover": {
        "position_dp": "2", "invert_set_position": True}}}}, tmp_path, name="b.json")
    cover = write_only.generate(CURTAIN_DEVICE)[0][COVER_TOPIC]
    assert cover["set_position_template"] == "{{ 100 - value | int }}"
    assert "100 -" not in cover["position_template"]


def test_position_stream_selects_active_vs_passive(tmp_path):
    """`position_stream` flips the read filter between passive (drop active deltas)
    and active (keep only deltas); default is passive."""
    def pos_template(stream):
        block = {"position_dp": "2"}
        if stream:
            block["position_stream"] = stream
        gen = _generator({PRODUCT_ID: {"discovery_overrides": {"cover": block}}},
                         tmp_path, name=f"{stream or 'def'}.json")
        return gen.generate(CURTAIN_DEVICE)[0][COVER_TOPIC]["position_template"]

    passive_default = pos_template(None)
    passive = pos_template("passive")
    active = pos_template("active")

    assert passive_default == passive  # default is passive
    # passive: render '' when the message is an active delta, else the value
    assert passive.startswith("{{ '' if value_json.type | default('') == 'active' else")
    # active: keep only the active delta, render '' otherwise
    assert active.rstrip().endswith("== 'active' else '' }}")
    assert passive != active


def test_invert_position_without_position_dp_reuses_builder_dp(tmp_path):
    """`invert_position` must work on its own — reusing the builder's position DP
    (percent_state /3 here) without an explicit position_dp override."""
    gen = _generator({PRODUCT_ID: {"discovery_overrides": {"cover": {
        "invert_position": True,
        "payload_open": "close", "payload_close": "open",  # reversed-direction motor
    }}}}, tmp_path)
    cover = gen.generate(CURTAIN_DEVICE)[0][COVER_TOPIC]
    # builder default position DP is percent_state /3, left in place...
    assert cover["position_topic"] == "rustuya/event/dev_curtain_demo/3"
    # ...but its template is now inverted through the codec
    assert "100 - ((value_json.value) | int)" in cover["position_template"]
    assert cover["payload_open"] == "close" and cover["payload_close"] == "open"


def test_state_stream_without_state_dp_reuses_builder_dp(tmp_path):
    """A read-role stream change applies even when its dp isn't overridden."""
    gen = _generator({PRODUCT_ID: {"discovery_overrides": {"cover": {
        "state_stream": "active",
    }}}}, tmp_path)
    cover = gen.generate(CURTAIN_DEVICE)[0][COVER_TOPIC]
    # control DP /1 kept, but value_template now keeps only the active delta
    assert cover["state_topic"].endswith("/1")
    assert cover["value_template"].rstrip().endswith("== 'active' else '' }}")


def test_state_dp_null_drops_state_role(tmp_path):
    """`state_dp: null` removes the cover's state_topic + value_template, leaving an
    optimistic, position-only cover."""
    gen = _generator({PRODUCT_ID: {"discovery_overrides": {"cover": {
        "state_dp": None,
        "invert_position": True,
    }}}}, tmp_path)
    cover = gen.generate(CURTAIN_DEVICE)[0][COVER_TOPIC]
    assert "state_topic" not in cover
    assert "value_template" not in cover
    # other roles untouched
    assert "command_topic" in cover
    assert "position_topic" in cover
    assert "100 - ((value_json.value) | int)" in cover["position_template"]


def test_remove_list_drops_arbitrary_fields(tmp_path):
    gen = _generator({PRODUCT_ID: {"discovery_overrides": {"cover": {
        "remove": ["device_class", "payload_stop"],
    }}}}, tmp_path)
    cover = gen.generate(CURTAIN_DEVICE)[0][COVER_TOPIC]
    assert "device_class" not in cover
    assert "payload_stop" not in cover
    assert "command_topic" in cover  # untouched


def test_override_only_touches_named_component(tmp_path):
    """An override for an absent component must not leak onto other entities."""
    gen = _generator({
        PRODUCT_ID: {"discovery_overrides": {"climate": {"payload_open": "X"}}},
    }, tmp_path)
    payloads, _ = gen.generate(CURTAIN_DEVICE)
    assert "payload_open" not in payloads[COVER_TOPIC]
