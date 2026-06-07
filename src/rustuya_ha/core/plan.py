"""Pure plan builders for publish / clear of retained HA discovery topics.

Shared single source of truth for the CLI (``rustuya_ha.cli.manager``) and the
manager plugin (``rustuya_ha.manager_plugin``) so both emit byte-identical MQTT
message lists. Each builder is **pure**: it takes already-collected inputs (device
dicts, a generator, the current retained map) and returns the
``{topic, payload, retain}`` messages plus a per-device summary — no MQTT I/O, no
file I/O. The callers own collection (CLI via paho; plugin via the bridge tap) and
execution (CLI via paho publish; plugin via ``bridge_client.publish_raw``).

The retained map shape is ``{topic: {"payload": <parsed dict>, ...}}`` — exactly
what both callers already hold (CLI ``mqtt_data``; plugin ``self.retained``).
"""
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


def device_id_of(payload: Dict[str, Any]) -> Optional[str]:
    """Owner device id of a discovery payload (its ``device.identifiers[0]``)."""
    ids = payload.get("device", {}).get("identifiers", [])
    return ids[0] if ids else None


def owned_topics(retained: Dict[str, dict], device_ids) -> Dict[str, set]:
    """Map each requested device id -> set of retained topics it owns."""
    ids = set(device_ids)
    by_dev: Dict[str, set] = defaultdict(set)
    for topic, msg in retained.items():
        did = device_id_of(msg["payload"])
        if did in ids:
            by_dev[did].add(topic)
    return by_dev


def publish_plan(
    devices: List[Dict[str, Any]], generator, retained: Dict[str, dict]
) -> Tuple[List[dict], List[dict], List[dict]]:
    """Build the messages to (re)publish discovery for ``devices``.

    For each device: generate its expected payloads, clear any of *its own*
    currently-retained topics that are no longer expected (stale), then publish
    the expected ones (retained). Per device the clears precede the publishes,
    both topic-sorted, devices in input order — matching the historical CLI order.

    Returns ``(msgs, per_device, errors)``:
      - ``msgs``:       ``[{topic, payload, retain}]`` (payload is JSON / "" for clear)
      - ``per_device``: ``[{id, name, publish, clear}]``
      - ``errors``:     ``[{id, error}]`` for devices the generator raised on
    """
    new_topics: Dict[str, Dict[str, dict]] = {}
    errors: List[dict] = []
    names: Dict[str, str] = {}
    for d in devices:
        names[d["id"]] = d.get("name", d["id"])
        try:
            payloads, _ = generator.generate(d)
            new_topics[d["id"]] = payloads
        except Exception as e:  # generator failure is reported, not fatal
            errors.append({"id": d["id"], "error": str(e)})

    existing = owned_topics(retained, new_topics.keys())
    msgs: List[dict] = []
    per_device: List[dict] = []
    for dev_id, new in new_topics.items():
        old = existing.get(dev_id, set())
        stale = old - set(new)
        for t in sorted(stale):
            msgs.append({"topic": t, "payload": "", "retain": True})
        for t in sorted(new):
            msgs.append({"topic": t, "payload": json.dumps(new[t]), "retain": True})
        per_device.append(
            {"id": dev_id, "name": names[dev_id], "publish": len(new), "clear": len(stale)}
        )
    return msgs, per_device, errors


def clear_plan(
    retained: Dict[str, dict], device_ids, topics_by_dev: Optional[Dict[str, list]] = None
) -> Tuple[List[dict], List[dict]]:
    """Build the messages to clear retained discovery for ``device_ids``.

    By default clears *all* retained topics each device owns. Pass
    ``topics_by_dev`` (e.g. the verifier's per-device ``unexpected`` lists) to
    clear only a specific subset — used for "clear stale only". Topics are
    deduped and sorted; an empty retained payload is the retain-clear.

    Returns ``(msgs, per_device)`` where ``per_device`` is ``[{id, clear}]``.
    """
    if topics_by_dev is None:
        by_dev = owned_topics(retained, device_ids)
    else:
        ids = set(device_ids)
        by_dev = {k: set(v) for k, v in topics_by_dev.items() if k in ids}
    msgs = [
        {"topic": t, "payload": "", "retain": True}
        for topics in by_dev.values()
        for t in sorted(topics)
    ]
    per_device = [{"id": k, "clear": len(v)} for k, v in by_dev.items()]
    return msgs, per_device
