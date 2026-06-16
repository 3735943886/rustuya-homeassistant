#!/usr/bin/env python3
"""Build the drop-in plugin zip consumed by rustuya-manager's plugin catalog.

rustuya-manager installs plugins by downloading a zip, checking its SHA-256
against the curated catalog (`rustuya_manager/data/plugins.json`), and unpacking
it into the managed `/data/plugins` dir. Its `catalog._unpack_zip` records each
*top-level* entry name in the install ledger, and `plugins._discover_dir_plugins`
imports each such name and calls its `register(ctx)`. So the artifact must be a
zip whose single top-level entry is the importable `rustuya_ha/` package — i.e.
the same tree the wheel ships, minus the `.dist-info`.

Everything the wheel ships already lives under `src/rustuya_ha/` (including the
force-included `data/custom_converters.json`), so we zip that tree directly
rather than building and unpacking a wheel — no build backend needed.

Reproducibility is the whole point: the manager refuses any artifact whose hash
doesn't match the catalog, so the bytes this produces must be identical on every
machine and every CI rerun. We therefore pin entry order, timestamps,
permissions, and use ZIP_STORED (no deflate — its output can vary across zlib
versions). Same source tree in -> byte-identical zip out -> stable SHA-256.

Usage:
    python scripts/build_dropin.py [--outdir dist]

Prints the artifact path and its SHA-256 (the value to paste into the manager's
plugins.json catalog entry).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "src" / "rustuya_ha"
ARCNAME_ROOT = "rustuya_ha"  # top-level dir inside the zip (the import name)

# A fixed DOS timestamp (1980-01-01 00:00:00, the zip epoch) so the archive
# carries no build-time mtimes — the bytes depend only on file contents.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _excluded(rel: Path) -> bool:
    """Skip caches and compiled artifacts — never part of the shipped package."""
    parts = rel.parts
    return "__pycache__" in parts or rel.suffix in {".pyc", ".pyo"}


def _source_files() -> list[Path]:
    """All shippable files under src/rustuya_ha, sorted for deterministic order."""
    files = [
        p
        for p in PKG_DIR.rglob("*")
        if p.is_file() and not _excluded(p.relative_to(PKG_DIR))
    ]
    return sorted(files, key=lambda p: p.relative_to(PKG_DIR).as_posix())


def _project_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def build(outdir: Path) -> Path:
    version = _project_version()
    outdir.mkdir(parents=True, exist_ok=True)
    artifact = outdir / f"rustuya_ha-{version}-dropin.zip"

    files = _source_files()
    if not (PKG_DIR / "__init__.py") in files:
        sys.exit(f"error: {PKG_DIR}/__init__.py missing — not a package tree?")

    # Write members one at a time with pinned metadata so the byte stream is
    # reproducible (ZipFile.write would stamp the on-disk mtime/mode).
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in files:
            rel = path.relative_to(PKG_DIR).as_posix()
            info = zipfile.ZipInfo(f"{ARCNAME_ROOT}/{rel}", date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16  # regular file, rw-r--r--
            zf.writestr(info, path.read_bytes())

    return artifact


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--outdir", default="dist", type=Path, help="output directory (default: dist)"
    )
    args = ap.parse_args()

    artifact = build(args.outdir if args.outdir.is_absolute() else REPO_ROOT / args.outdir)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    print(f"artifact: {artifact}")
    print(f"sha256:   {digest}")


if __name__ == "__main__":
    main()
