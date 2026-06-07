"""Tests for --category filter helpers in tuya_discovery_admin."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core.verifier import (  # noqa: E402
    CATEGORY_ALIASES,
    filter_matches_by_categories,
    filter_status_results,
)


def _dev(dev_id, name=None):
    return {"id": dev_id, "name": name or dev_id}


@pytest.fixture
def sample_results():
    """Synthetic verify() output spanning every category."""
    return {
        "perfect":             [_dev("p1")],
        "mismatched_payload":  [_dev("m1"), _dev("m2")],
        "partially_missing":   [_dev("pm1")],
        "pure_missing":        [_dev("pu1"), _dev("pu2")],
        "unexpected_topics":   [_dev("u1")],
        "no_dp_config":        [_dev("n1")],
        "orphans":             [],
        "errors":              ["gen failure for x"],
    }


@pytest.fixture
def sample_matches():
    return [_dev(i) for i in ("p1", "m1", "m2", "pm1", "pu1", "pu2", "u1", "n1")]


# --- filter_matches_by_categories ---


def test_no_categories_returns_matches_unchanged(sample_matches, sample_results):
    assert filter_matches_by_categories(sample_matches, sample_results, None) is sample_matches
    assert filter_matches_by_categories(sample_matches, sample_results, []) is sample_matches


def test_single_category_mismatched(sample_matches, sample_results):
    out = filter_matches_by_categories(sample_matches, sample_results, ["mismatched"])
    assert sorted(d["id"] for d in out) == ["m1", "m2"]


def test_each_alias_maps_to_expected_category(sample_matches, sample_results):
    cases = {
        "mismatched": ["m1", "m2"],
        "partial":    ["pm1"],
        "pure":       ["pu1", "pu2"],
        "unexpected": ["u1"],
        "no-dp":      ["n1"],
        "perfect":    ["p1"],
    }
    for alias, expected_ids in cases.items():
        out = filter_matches_by_categories(sample_matches, sample_results, [alias])
        assert sorted(d["id"] for d in out) == expected_ids, f"alias {alias!r}"


def test_multiple_categories_union(sample_matches, sample_results):
    out = filter_matches_by_categories(sample_matches, sample_results,
                                       ["mismatched", "pure"])
    assert sorted(d["id"] for d in out) == ["m1", "m2", "pu1", "pu2"]


def test_pattern_and_category_combine_as_and(sample_results):
    """Caller pre-filters `matches` by pattern; category narrows further. Verify intersection."""
    pattern_matches = [_dev("m1"), _dev("pu1"), _dev("p1")]  # caller already pattern-filtered
    out = filter_matches_by_categories(pattern_matches, sample_results, ["mismatched"])
    assert [d["id"] for d in out] == ["m1"]


def test_match_not_in_any_requested_category_dropped(sample_results):
    pattern_matches = [_dev("p1")]  # only "perfect"
    out = filter_matches_by_categories(pattern_matches, sample_results, ["mismatched"])
    assert out == []


def test_unknown_alias_raises(sample_matches, sample_results):
    with pytest.raises(KeyError):
        filter_matches_by_categories(sample_matches, sample_results, ["nonsense"])


def test_aliases_cover_every_list_category(sample_results):
    """Catch silent drift: every list-typed category (except orphans/errors) should
    be reachable via at least one alias."""
    list_cats = {k for k, v in sample_results.items()
                 if isinstance(v, list) and k not in ("orphans", "errors")}
    reachable = set(CATEGORY_ALIASES.values())
    assert list_cats == reachable, (
        f"unreachable: {list_cats - reachable}, extra: {reachable - list_cats}"
    )


# --- filter_status_results ---


def test_status_filter_none_passthrough(sample_results):
    assert filter_status_results(sample_results, None) is sample_results
    assert filter_status_results(sample_results, []) is sample_results


def test_status_filter_blanks_unselected_lists(sample_results):
    out = filter_status_results(sample_results, ["mismatched"])
    assert out["mismatched_payload"] == sample_results["mismatched_payload"]
    for k in ("perfect", "partially_missing", "pure_missing",
              "unexpected_topics", "no_dp_config", "orphans"):
        assert out[k] == [], f"{k} should be blanked"


def test_status_filter_preserves_errors(sample_results):
    out = filter_status_results(sample_results, ["mismatched"])
    assert out["errors"] == sample_results["errors"]


def test_status_filter_multiple_categories(sample_results):
    out = filter_status_results(sample_results, ["mismatched", "pure"])
    assert out["mismatched_payload"] == sample_results["mismatched_payload"]
    assert out["pure_missing"] == sample_results["pure_missing"]
    assert out["partially_missing"] == []
    assert out["perfect"] == []
