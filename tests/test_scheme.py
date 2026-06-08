"""DefaultTopicScheme / DefaultPayloadCodec must reproduce the historical
hardcoded behaviour, so the #2 seam is provably non-inferior."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core.scheme import (  # noqa: E402
    DefaultTopicScheme, DefaultPayloadCodec, TopicScheme, PayloadCodec,
)


def test_default_topic_scheme_matches_legacy_formats():
    s = DefaultTopicScheme()
    assert s.discovery("sensor", "dev1", "temp") == "homeassistant/sensor/dev1_temp/config"
    assert s.state("dev1", "5") == "rustuya/event/dev1/5"
    assert s.command("dev1", "5") == "rustuya/command/set/dev1/5"
    assert s.availability("dev1") == "rustuya/error/dev1"


def test_default_scheme_satisfies_protocols():
    assert isinstance(DefaultTopicScheme(), TopicScheme)
    assert isinstance(DefaultPayloadCodec(), PayloadCodec)


def test_availability_template_offline_branch():
    tpl = DefaultPayloadCodec().availability_template()
    assert tpl.startswith("{{ 'offline' if")
    assert "value_json.errorCode in" in tpl


def test_value_template_event_and_switch_and_scale():
    c = DefaultPayloadCodec()
    assert "event_type" in c.value_template("event")
    # stateful entities keep only `state` (the retained snapshot) and drop the
    # no-retain active/passive deltas.
    assert c.value_template("switch") == (
        "{{ ('true' if value_json is defined and value_json != None and value_json.value != None "
        "and value_json.value == true else 'false' if value_json is defined and value_json != None "
        "and value_json.value != None and value_json.value == false else '') "
        "if value_json.type | default('') == 'state' else '' }}"
    )
    assert "/ 10) | round(1)" in c.value_template("sensor", scale=1)
    # event template is unchanged — it still keeps active.
    assert c.value_template("event").endswith("== 'active' else '' }}")


def test_active_only_sensor_keeps_active_drops_snapshot():
    """Incremental/delta DPs (e.g. add_ele) must read `active` and drop the
    retained `state` snapshot — the inverse of the default state-only."""
    c = DefaultPayloadCodec()
    state = c.value_template("sensor")                     # normal: read the snapshot
    active = c.value_template("sensor", active_only=True)  # delta: read the active push
    # active-only renders '' for non-active and the value for active
    assert active.endswith("== 'active' else '' }}")
    assert active.startswith("{{ (value_json.value if")
    # the state-only form keeps only `state`
    assert state.endswith("== 'state' else '' }}")
    assert state.startswith("{{ (value_json.value if")
    assert active != state


def test_passive_only_keeps_passive_delta():
    """The add_ele passive companion reads the raw `passive` delta — same shape
    as active_only but filtered on `passive`."""
    c = DefaultPayloadCodec()
    passive = c.value_template("sensor", active_only=False, passive_only=True)
    assert passive.endswith("== 'passive' else '' }}")
    assert passive.startswith("{{ (value_json.value if")
    # distinct from both the active delta and the state snapshot
    assert passive != c.value_template("sensor", active_only=True)
    assert passive != c.value_template("sensor")
