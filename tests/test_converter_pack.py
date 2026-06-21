"""Tests for the default-converter pack (core.pack) + its manifest.

`pack.sync` mirrors a curated converter set into a live directory, fetching each
file by URL and SHA-256-verifying it. These tests drive it over `file://` URLs
(a local "remote"), and lock the contract that matters most: it only ever
touches files it manages, never the user's own.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rustuya_ha.core import pack  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_for(src: Path, names) -> dict:
    """A manifest whose base_url points at a local `src` dir over file://."""
    return {
        "version": 1,
        "base_url": f"file://{src}/",
        "files": [{"name": n, "sha256": _sha((src / n).read_bytes())} for n in names],
    }


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    (d / "00_default.json").write_text('{"p1": {"model": "A"}}')
    (d / "00_curtain.py").write_text("def setup(api):\n    pass\n")
    return d


@pytest.fixture
def dest(tmp_path):
    return tmp_path / "live"


def test_fresh_sync_adds_all_and_writes_ledger(src, dest):
    res = pack.sync(dest, manifest=_manifest_for(src, ["00_default.json", "00_curtain.py"]))
    assert res["added"] == ["00_curtain.py", "00_default.json"]
    assert res["updated"] == [] and res["removed"] == [] and res["failed"] == []
    assert (dest / "00_default.json").read_text() == '{"p1": {"model": "A"}}'
    assert (dest / "00_curtain.py").exists()
    assert pack.has_synced(dest)
    assert set(pack.read_ledger(dest)) == {"00_default.json", "00_curtain.py"}


def test_user_files_are_never_touched(src, dest):
    dest.mkdir(parents=True)
    (dest / "99_custom.json").write_text('{"mine": 1}')
    (dest / "handwritten.py").write_text("# mine\n")

    pack.sync(dest, manifest=_manifest_for(src, ["00_default.json"]))
    # later updates + removals, too
    (src / "00_default.json").write_text('{"p1": {"model": "B"}}')
    pack.sync(dest, manifest=_manifest_for(src, []))  # drop everything from the pack

    assert (dest / "99_custom.json").read_text() == '{"mine": 1}'
    assert (dest / "handwritten.py").read_text() == "# mine\n"
    assert "99_custom.json" not in pack.read_ledger(dest)


def test_unchanged_then_update(src, dest):
    pack.sync(dest, manifest=_manifest_for(src, ["00_default.json", "00_curtain.py"]))
    # second sync, nothing changed remotely → all unchanged, no re-download needed
    res = pack.sync(dest, manifest=_manifest_for(src, ["00_default.json", "00_curtain.py"]))
    assert set(res["unchanged"]) == {"00_default.json", "00_curtain.py"}
    assert res["added"] == [] and res["updated"] == []

    # change one file remotely → just that one updates
    (src / "00_default.json").write_text('{"p1": {"model": "B"}}')
    res = pack.sync(dest, manifest=_manifest_for(src, ["00_default.json", "00_curtain.py"]))
    assert res["updated"] == ["00_default.json"]
    assert res["unchanged"] == ["00_curtain.py"]
    assert (dest / "00_default.json").read_text() == '{"p1": {"model": "B"}}'


def test_dropped_default_is_removed(src, dest):
    pack.sync(dest, manifest=_manifest_for(src, ["00_default.json", "00_curtain.py"]))
    res = pack.sync(dest, manifest=_manifest_for(src, ["00_default.json"]))
    assert res["removed"] == ["00_curtain.py"]
    assert not (dest / "00_curtain.py").exists()
    assert (dest / "00_default.json").exists()
    assert set(pack.read_ledger(dest)) == {"00_default.json"}


def test_checksum_mismatch_is_skipped_not_written(src, dest):
    m = _manifest_for(src, ["00_default.json"])
    m["files"][0]["sha256"] = "0" * 64  # wrong hash
    res = pack.sync(dest, manifest=m)
    assert res["added"] == [] and len(res["failed"]) == 1
    assert not (dest / "00_default.json").exists()


def test_failed_refetch_keeps_prior_copy(src, dest):
    # First good sync.
    pack.sync(dest, manifest=_manifest_for(src, ["00_default.json"]))
    assert (dest / "00_default.json").exists()
    # Now the remote advertises a new hash but serves bytes that don't match
    # (simulated by editing the file after computing the manifest off the old one,
    # i.e. a corrupt/incomplete fetch): the prior copy must survive.
    bad = _manifest_for(src, ["00_default.json"])
    bad["files"][0]["sha256"] = "f" * 64
    res = pack.sync(dest, manifest=bad)
    assert len(res["failed"]) == 1
    assert (dest / "00_default.json").read_text() == '{"p1": {"model": "A"}}'
    assert "00_default.json" in pack.read_ledger(dest)  # still managed


def test_invalid_json_is_rejected(src, dest):
    (src / "00_default.json").write_text("{ not json")
    res = pack.sync(dest, manifest=_manifest_for(src, ["00_default.json"]))
    assert len(res["failed"]) == 1 and res["added"] == []
    assert not (dest / "00_default.json").exists()


def test_traversal_name_rejected(src, dest):
    m = {"version": 1, "base_url": f"file://{src}/",
         "files": [{"name": "../evil.json", "sha256": "0" * 64}]}
    res = pack.sync(dest, manifest=m)
    assert len(res["failed"]) == 1
    assert not (dest.parent / "evil.json").exists()


# ── the committed manifest must match the pack on disk (drift guard) ─────────
def test_committed_manifest_in_sync():
    spec = importlib.util.spec_from_file_location(
        "_build_manifest", ROOT / "scripts" / "build_converter_manifest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fresh = mod.build_manifest()
    committed = json.loads((ROOT / "custom_converters" / "manifest.json").read_text())
    assert committed == fresh, (
        "custom_converters/manifest.json is stale — run "
        "python scripts/build_converter_manifest.py"
    )
