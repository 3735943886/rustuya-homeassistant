#!/usr/bin/env python3
"""Build the default-converter pack manifest (`custom_converters/manifest.json`).

The pack is the curated set of default converters this project ships — the
`00_*` JSON overrides and `.py` code converters in `custom_converters/`. A
manager-side plugin mirrors that set into a user's live converters directory and
keeps it updated *independently of the plugin's own release*: editing a converter
on `master` and pushing is enough, exactly like the manager's plugin catalog
(`plugins.json`). The manifest is the integrity index — each entry pins a
SHA-256, so the fetcher verifies every file before writing it.

What's in the pack: every `*.json` / `*.py` directly under `custom_converters/`
*except* a user's personal `99_*` overrides (gitignored), this manifest itself,
and dotfiles. The list is content-addressed and sorted, so the same tree yields
a byte-identical manifest on every machine — `tests/test_converter_pack.py`
asserts the committed file is in sync, the same guard the golden snapshots use.

Usage:
    python scripts/build_converter_manifest.py [--check]

    (no args)  regenerate custom_converters/manifest.json
    --check    exit non-zero if the committed manifest is stale (for CI/pre-commit)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = REPO_ROOT / "custom_converters"
MANIFEST_NAME = "manifest.json"

# The plugin fetches each file from here at `<base_url><name>`; the manifest URL
# itself is `<base_url>manifest.json`. Pinned to `master` so a converter fix is
# live on push — no plugin version bump (mirrors the manager's catalog model).
BASE_URL = (
    "https://raw.githubusercontent.com/3735943886/rustuya-homeassistant"
    "/master/custom_converters/"
)

PACK_SUFFIXES = (".json", ".py")


def _is_pack_file(p: Path) -> bool:
    """A shippable default-converter file: a `*.json`/`*.py` directly in the pack
    dir that isn't a personal `99_*` override, this manifest, or a dotfile."""
    return (
        p.is_file()
        and p.suffix in PACK_SUFFIXES
        and p.name != MANIFEST_NAME
        and not p.name.startswith("99_")
        and not p.name.startswith(".")
    )


def build_manifest(pack_dir: Path = PACK_DIR, *, base_url: str = BASE_URL) -> dict:
    """The manifest document for `pack_dir` — deterministic (files sorted by
    name, each pinned by SHA-256), so identical trees produce identical bytes."""
    files = []
    for p in sorted((q for q in pack_dir.iterdir() if _is_pack_file(q)), key=lambda q: q.name):
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        files.append({"name": p.name, "sha256": digest})
    return {"version": 1, "base_url": base_url, "files": files}


def _serialize(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true",
        help="exit non-zero if the committed manifest is stale (don't write)",
    )
    args = ap.parse_args()

    doc = build_manifest()
    text = _serialize(doc)
    target = PACK_DIR / MANIFEST_NAME

    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != text:
            print(f"{target} is stale — run: python scripts/build_converter_manifest.py", file=sys.stderr)
            sys.exit(1)
        print(f"{target} is up to date ({len(doc['files'])} files)")
        return

    target.write_text(text, encoding="utf-8")
    print(f"wrote {target} ({len(doc['files'])} files)")
    for f in doc["files"]:
        print(f"  {f['name']}  {f['sha256'][:12]}…")


if __name__ == "__main__":
    main()
