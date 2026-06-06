"""Delta DPs (add_ele) get a `passive` companion sensor for devices that report
the increment on the passive stream instead of active."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha import initialize_generator  # noqa: E402

DEVICE = {
    "id": "devX",
    "name": "energy",
    "category": "cz",
    "local_strategy": {
        "18": {"status_code": "add_ele",
               "config_item": {"valueType": "Integer",
                               "valueDesc": {"unit": "kW·h", "scale": 3}}},
    },
}


def test_add_ele_emits_active_and_passive_companion():
    payloads, _ = initialize_generator().generate(DEVICE)
    active_topic = "homeassistant/sensor/devX_add_ele/config"
    passive_topic = "homeassistant/sensor/devX_add_ele_passive/config"
    assert active_topic in payloads
    assert passive_topic in payloads

    active, passive = payloads[active_topic], payloads[passive_topic]
    # distinct identity
    assert active["unique_id"] == "devX_add_ele"
    assert passive["unique_id"] == "devX_add_ele_passive"
    assert passive["name"] == "Add ele passive"
    # both are delta sensors -> force_update, same device_class/unit
    assert active["force_update"] is True and passive["force_update"] is True
    assert active["device_class"] == passive["device_class"]
    assert active["unit_of_measurement"] == passive["unit_of_measurement"]
    # the companion filters the `passive` stream; the original keeps `active`
    assert active["value_template"].rstrip().endswith("== 'active' else '' }}")
    assert passive["value_template"].rstrip().endswith("== 'passive' else '' }}")


def test_absolute_state_dp_has_no_passive_companion():
    """A normal (non-delta) sensor must NOT spawn a companion."""
    device = {
        "id": "devY", "name": "meter", "category": "cz",
        "local_strategy": {"19": {"status_code": "energy",
                                  "config_item": {"valueType": "Integer",
                                                  "valueDesc": {"unit": "kW·h", "scale": 3}}}},
    }
    payloads, _ = initialize_generator().generate(device)
    assert not any(t.endswith("_passive/config") for t in payloads)
