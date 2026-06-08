"""Tests for the command (write) side: command_template generation.

When the bridge command topic carries no `{dp}` (e.g. a flat `{root}/command`),
HA can't put the target id/dp in the topic, so the generator must emit a
`command_template` that builds the bridge's `set` request JSON:
    {"action": "set", "id": "<dev>", "dps": {"<dp>": <value>}}
When the topic IS per-dp (legacy), no template is emitted (bare value) — the
golden output is unchanged (guarded by test_discovery_golden).
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core.bridge import BridgeConfig, LEGACY  # noqa: E402
from rustuya_ha.core.generator import initialize_generator  # noqa: E402
from rustuya_ha.core.scheme import (  # noqa: E402
    BridgePayloadCodec, build_command_value_expr, scheme_for,
)

DEVICES = json.load(open(ROOT / "tests" / "fixtures" / "synthetic_devices.json"))
FLAT = BridgeConfig.from_json_file(str(ROOT / "tests" / "fixtures" / "flat_command_bridge_config.json"))


def _render_skeleton(template: str) -> dict:
    """Replace every {{ jinja }} with a literal 0 and parse — proves the
    non-Jinja scaffolding is valid JSON of the expected shape."""
    return json.loads(re.sub(r"\{\{.*?\}\}", "0", template))


# ── value expression encoding ─────────────────────────────────────────────
def test_value_expr_bool_is_bare():
    assert build_command_value_expr("bool") == "{{ value }}"


def test_value_expr_number_scale_multiplies():
    assert build_command_value_expr("number", scale=0) == "{{ (value | float) | int }}"
    assert "* 10" in build_command_value_expr("number", scale=1)
    assert "* 100" in build_command_value_expr("number", scale=2)


def test_value_expr_number_transform_wraps():
    expr = build_command_value_expr("number", transform="100 - %s")
    assert expr.startswith("{{ 100 - (")


def test_value_expr_enum_inverts_val_map_first_wins():
    vm = {"0": "power_off", "1": "power_on", "2": "last", "memory": "last"}
    expr = build_command_value_expr("enum", val_map=vm)
    assert "'power_off': '0'" in expr
    assert "'power_on': '1'" in expr
    assert "'last': '2'" in expr        # first raw mapping to "last" wins
    assert "'memory'" not in expr       # duplicate label dropped
    assert expr.startswith('"') and expr.endswith('"')  # emitted as a JSON string


def test_value_expr_string_uses_tojson():
    assert build_command_value_expr("string") == "{{ value | tojson }}"


# ── BridgeConfig command-topic flags ──────────────────────────────────────
def test_command_flags_flat_vs_legacy():
    assert FLAT.command_per_dp is False
    assert FLAT.command_has_id is False
    assert FLAT.command_has_action is False
    # Legacy: "{root}/command/{action}/{id}/{dp}"
    assert LEGACY.command_per_dp is True
    assert LEGACY.command_has_id is True
    assert LEGACY.command_has_action is True


# ── codec.command_template ────────────────────────────────────────────────
def test_codec_template_none_when_per_dp():
    codec = BridgePayloadCodec(LEGACY)
    assert codec.command_template("dev1", "7", kind="number") is None


def test_codec_template_flat_builds_set_json():
    codec = BridgePayloadCodec(FLAT)
    tmpl = codec.command_template("eb7ba8427911a8ccbda92w", "7", kind="number")
    skel = _render_skeleton(tmpl)
    assert skel["action"] == "set"
    assert skel["id"] == "eb7ba8427911a8ccbda92w"
    assert skel["dps"] == {"7": 0}


def test_codec_template_omits_id_action_when_topic_has_them():
    # id in topic, action+dp not: only dps (+action) ride in the payload.
    cfg = BridgeConfig.from_dict({
        "mqtt_root_topic": "rustuya",
        "mqtt_command_topic": "{root}/command/{id}",
    })
    codec = BridgePayloadCodec(cfg)
    tmpl = codec.command_template("dev1", "3", kind="bool")
    skel = _render_skeleton(tmpl)
    assert "id" not in skel          # id comes from the topic
    assert skel["action"] == "set"   # action still needed in payload
    assert skel["dps"] == {"3": 0}


# ── generator integration ─────────────────────────────────────────────────
def _generate(cfg):
    gen = initialize_generator()
    gen.scheme, gen.codec = scheme_for(cfg)
    out = {}
    for d in DEVICES:
        payloads, _ = gen.generate(d)
        out.update(payloads)
    return out


_CMD_TOPIC_KEYS = (
    "command_topic", "set_position_topic", "temperature_command_topic",
    "mode_command_topic", "percentage_command_topic", "oscillation_command_topic",
    "brightness_command_topic", "color_temp_command_topic",
)


def test_flat_config_every_command_topic_has_template():
    payloads = _generate(FLAT)
    seen_any = False
    for topic, p in payloads.items():
        for key in _CMD_TOPIC_KEYS:
            if key in p:
                seen_any = True
                tmpl_key = key.replace("_topic", "_template")
                assert tmpl_key in p, f"{topic}: {key} has no {tmpl_key}"
                skel = _render_skeleton(p[tmpl_key])
                assert skel["action"] == "set"
                assert "id" in skel and isinstance(skel["dps"], dict)
    assert seen_any, "no command-capable entities generated from synthetic devices"


def test_legacy_config_emits_no_command_template():
    payloads = _generate(LEGACY)
    for topic, p in payloads.items():
        assert not any(k.endswith("_command_template") or k == "command_template" for k in p), topic


def test_flat_number_template_scales_and_targets_right_dp():
    payloads = _generate(FLAT)
    # synth_climate_01 has temp_set (dp 2) as a climate temperature command.
    climate = next(p for t, p in payloads.items() if "climate" in t)
    tmpl = climate["temperature_command_template"]
    skel = _render_skeleton(tmpl)
    assert skel["action"] == "set" and skel["id"] == "synth_climate_01"
    assert list(skel["dps"].keys()) == ["2"]  # temp_set DP
    assert "value | float" in tmpl  # numeric encoding
