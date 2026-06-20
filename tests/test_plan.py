"""Tests for the pure publish/clear plan builders (core.plan)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core import plan  # noqa: E402
from rustuya_ha.core.generator import initialize_generator  # noqa: E402

DEVICES = json.load(open(ROOT / "tests" / "fixtures" / "synthetic_devices.json"))


def _gen():
    return initialize_generator()


def _retained_from(generator, devices):
    """{topic: {"payload": dict}} exactly as the generator would emit."""
    out = {}
    for d in devices:
        payloads, _ = generator.generate(d)
        for topic, payload in payloads.items():
            out[topic] = {"payload": payload, "retain": True}
    return out


def test_device_id_of():
    assert plan.device_id_of({"device": {"identifiers": ["abc"]}}) == "abc"
    assert plan.device_id_of({"device": {}}) is None
    assert plan.device_id_of({}) is None


def test_publish_plan_into_empty_is_all_publishes():
    gen = _gen()
    msgs, per_device, errors = plan.publish_plan(DEVICES, gen, {})
    assert errors == []
    # No retained state → no clears, every msg is a non-empty retained publish.
    assert all(m["retain"] and m["payload"] != "" for m in msgs)
    assert sum(p["publish"] for p in per_device) == len(msgs)
    assert all(p["clear"] == 0 for p in per_device)
    # Topic count equals what the generator produced.
    expected = _retained_from(gen, DEVICES)
    assert {m["topic"] for m in msgs} == set(expected)


def test_publish_plan_idempotent_topics_no_stale():
    gen = _gen()
    retained = _retained_from(gen, DEVICES)
    msgs, per_device, errors = plan.publish_plan(DEVICES, gen, retained)
    # Same scheme → same topics → republish (overwrite), zero clears.
    assert all(p["clear"] == 0 for p in per_device)
    assert all(m["payload"] != "" for m in msgs)


def test_publish_plan_clears_stale_owned_topic():
    gen = _gen()
    d = DEVICES[0]
    retained = _retained_from(gen, [d])
    # Inject an extra retained topic OWNED by this device but not in its plan.
    stale_topic = "homeassistant/sensor/%s_ghost/config" % d["id"]
    retained[stale_topic] = {"payload": {"device": {"identifiers": [d["id"]]}}, "retain": True}
    msgs, per_device, _ = plan.publish_plan([d], gen, retained)
    clears = [m for m in msgs if m["payload"] == ""]
    assert [m["topic"] for m in clears] == [stale_topic]
    assert per_device[0]["clear"] == 1


def test_publish_plan_reports_generator_errors():
    class BoomGen:
        def generate(self, d):
            raise ValueError("boom")
    msgs, per_device, errors = plan.publish_plan([{"id": "x", "name": "X"}], BoomGen(), {})
    assert msgs == [] and per_device == []
    # The raw exception text is logged, not exposed — a generic message is returned.
    assert errors == [{"id": "x", "error": "Failed to generate discovery payloads"}]


def test_clear_plan_clears_all_owned():
    gen = _gen()
    retained = _retained_from(gen, DEVICES)
    ids = [d["id"] for d in DEVICES]
    msgs, per_device = plan.clear_plan(retained, ids)
    assert all(m["payload"] == "" and m["retain"] for m in msgs)
    assert {m["topic"] for m in msgs} == set(retained)
    assert sum(p["clear"] for p in per_device) == len(retained)


def test_clear_plan_only_requested_ids():
    gen = _gen()
    retained = _retained_from(gen, DEVICES)
    target = DEVICES[0]["id"]
    msgs, per_device = plan.clear_plan(retained, [target])
    # Every cleared topic belongs to the requested device only.
    for m in msgs:
        assert plan.device_id_of(retained[m["topic"]]["payload"]) == target


def test_clear_plan_subset_via_topics_by_dev():
    gen = _gen()
    retained = _retained_from(gen, DEVICES)
    d = DEVICES[0]["id"]
    owned = sorted(plan.owned_topics(retained, [d])[d])
    subset = owned[:1]
    msgs, per_device = plan.clear_plan(retained, [d], topics_by_dev={d: subset})
    assert [m["topic"] for m in msgs] == subset
    assert per_device == [{"id": d, "clear": 1}]
