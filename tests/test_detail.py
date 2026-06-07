"""Tests for the pure field-diff / drill-in detail helpers (core.detail)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core import detail  # noqa: E402


def test_topic_field_diffs_only_differing_keys():
    a = {"x": 1, "y": 2, "z": 3}
    e = {"x": 1, "y": 9, "w": 4}
    diffs = detail.topic_field_diffs(a, e)
    assert [d["key"] for d in diffs] == ["w", "y", "z"]  # sorted; x omitted (equal)
    by = {d["key"]: d for d in diffs}
    assert by["y"] == {"key": "y", "actual": 2, "expected": 9}
    assert by["z"] == {"key": "z", "actual": 3, "expected": None}  # only in actual
    assert by["w"] == {"key": "w", "actual": None, "expected": 4}  # only in expected


def test_device_detail_compacts_mismatched_and_omits_matched():
    dr = {
        "mismatched": [{"topic": "t1", "actual": {"a": 1}, "expected": {"a": 2}}],
        "missing": ["m1"], "unexpected": ["u1"], "matched": ["k1"],
    }
    det = detail.device_detail(dr)
    assert det["mismatched"] == [
        {"topic": "t1", "fields": [{"key": "a", "actual": 1, "expected": 2}]}
    ]
    assert det["missing"] == ["m1"] and det["unexpected"] == ["u1"]
    assert "matched" not in det
    assert detail.device_detail(dr, include_matched=True)["matched"] == ["k1"]


def test_has_detail():
    assert detail.has_detail({"mismatched": [{"topic": "t"}]})
    assert detail.has_detail({"missing": ["m"]})
    assert detail.has_detail({"unexpected": ["u"]})
    assert not detail.has_detail(
        {"matched": ["k"], "mismatched": [], "missing": [], "unexpected": []}
    )
