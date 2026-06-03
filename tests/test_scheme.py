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
    # stateful entities ignore `active` (passive-only) and read value on passive.
    assert c.value_template("switch") == (
        "{{ '' if value_json.type | default('') == 'active' else "
        "'true' if value_json is defined and value_json != None and value_json.value != None "
        "and value_json.value == true else 'false' if value_json is defined and value_json != None "
        "and value_json.value != None and value_json.value == false else 'unknown' }}"
    )
    assert "/ 10) | round(1)" in c.value_template("sensor", scale=1)
    # event template is unchanged — it still keeps active.
    assert c.value_template("event").endswith("== 'active' else '' }}")


def test_active_only_sensor_keeps_active_drops_passive():
    """Incremental/delta DPs (e.g. add_ele) must read `active` and drop the stale
    retained `passive` snapshot — the inverse of the default passive-only."""
    c = DefaultPayloadCodec()
    passive = c.value_template("sensor")                 # normal: drop active
    active = c.value_template("sensor", active_only=True)  # delta: drop passive
    # active-only renders '' for non-active and the value for active
    assert active.endswith("== 'active' else '' }}")
    assert active.startswith("{{ (value_json.value if")
    # and it is NOT the passive-only form
    assert passive.startswith("{{ '' if value_json.type")
    assert active != passive
