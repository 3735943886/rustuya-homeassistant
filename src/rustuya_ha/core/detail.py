"""Pure field-level drill-in detail for a verified device.

The verifier (``core.verifier``) classifies each device and, for mismatched
topics, attaches ``{topic, actual, expected}`` payload pairs. This module reduces
those to the minimal *differing* fields so both the CLI ``status --detail`` view
and the plugin's expand-row drill-in render the same compact diff (without
shipping whole payloads). Pure: no I/O.
"""
from typing import Any, Dict, List


def topic_field_diffs(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[dict]:
    """Return ``[{key, actual, expected}]`` for keys whose values differ.

    Keys present in only one side count as differing (the absent side is
    ``None``). Sorted by key for stable output."""
    keys = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
    return [{"key": k, "actual": actual.get(k), "expected": expected.get(k)} for k in keys]


def device_detail(device_result: Dict[str, Any], *, include_matched: bool = False) -> Dict[str, Any]:
    """Compact, JSON-safe drill-in detail for one verifier device entry.

    ``mismatched`` becomes ``[{topic, fields:[{key,actual,expected}]}]``;
    ``missing``/``unexpected`` stay as topic lists. ``matched`` is included only
    when asked (it's just a count in the collapsed grid, and can be long)."""
    mismatched = [
        {"topic": m["topic"], "fields": topic_field_diffs(m["actual"], m["expected"])}
        for m in device_result.get("mismatched", [])
    ]
    detail: Dict[str, Any] = {
        "mismatched": mismatched,
        "missing": list(device_result.get("missing", [])),
        "unexpected": list(device_result.get("unexpected", [])),
    }
    if include_matched:
        detail["matched"] = list(device_result.get("matched", []))
    return detail


def has_detail(device_result: Dict[str, Any]) -> bool:
    """True if the device has anything worth drilling into (a diff/missing/unexpected)."""
    return bool(
        device_result.get("mismatched")
        or device_result.get("missing")
        or device_result.get("unexpected")
    )
