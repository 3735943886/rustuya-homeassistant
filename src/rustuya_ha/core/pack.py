"""Default-converter pack — mirror this project's curated converters into a live
converters directory, fetched from GitHub and decoupled from the plugin release.

The pack is published as `custom_converters/manifest.json` on `master` (built by
`scripts/build_converter_manifest.py`). `sync()` fetches it, downloads and
SHA-256-verifies each listed file, and writes them into the target directory:
adding new defaults, updating changed ones, and removing defaults the manifest
dropped. Editing a converter on master and pushing is enough for users to pull
it — no plugin version bump, exactly like the manager's plugin catalog.

Ownership is explicit and conservative. `sync()` only ever writes or deletes
files it *manages*, recorded by name+hash in a `.rustuya_pack.json` ledger beside
them. A user's own files — `99_*` overrides, hand-authored converters, anything
not in the pack — are never written, never deleted, never inspected. Like the
catalog, the manifest is trust-on-fetch over TLS while every file is
content-pinned (checksum mismatch → the file is skipped, not written).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("converter_pack")

MANIFEST_NAME = "manifest.json"
# The published pack lives in the repo's custom_converters/ on master, so a
# converter fix is live on push (no plugin release). Mirrors catalog.CATALOG_URL.
DEFAULT_BASE_URL = (
    "https://raw.githubusercontent.com/3735943886/rustuya-homeassistant"
    "/master/custom_converters/"
)
DEFAULT_MANIFEST_URL = DEFAULT_BASE_URL + MANIFEST_NAME

# Dot-prefixed so the converter loader skips it (like dotfiles); records the
# {name: sha256} of every file this pack manages, so an update knows what's ours.
LEDGER_NAME = ".rustuya_pack.json"

MAX_FILE_BYTES = 1 * 1024 * 1024  # converters are small text files
TIMEOUT_S = 15
_ALLOWED_SCHEMES = ("http", "https", "file")  # file:// for tests / offline mirrors
_ALLOWED_SUFFIXES = (".json", ".py")


class PackError(Exception):
    """A pack fetch/sync failure whose message is safe to surface to the UI."""


def _download(url: str, cap: int = MAX_FILE_BYTES) -> bytes:
    """Fetch `url` into memory, scheme-restricted and size-bounded (mirrors the
    manager catalog's `_download`)."""
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in _ALLOWED_SCHEMES:
        raise PackError(f"unsupported URL scheme: {scheme or '(none)'}")
    req = urllib.request.Request(url, headers={"User-Agent": "rustuya-ha"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - scheme allow-listed
            data = resp.read(cap + 1)
    except (OSError, ValueError) as exc:
        raise PackError(f"could not fetch {url}: {exc}") from exc
    if len(data) > cap:
        raise PackError(f"pack file exceeds the size limit: {url}")
    return data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(name: str) -> str:
    """Reject traversal / unexpected types; return the bare filename."""
    n = Path(name).name
    if n != name or not n or n.startswith("."):
        raise PackError(f"unsafe pack file name: {name!r}")
    if Path(n).suffix not in _ALLOWED_SUFFIXES:
        raise PackError(f"pack file must be .json or .py: {name!r}")
    return n


def fetch_manifest(url: str = DEFAULT_MANIFEST_URL) -> Dict[str, Any]:
    """Download + parse the pack manifest. Raises `PackError` on any
    network/JSON/shape problem."""
    raw = _download(url)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PackError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        raise PackError("manifest has no usable 'files' list")
    return doc


def _ledger_path(dest: Path) -> Path:
    return dest / LEDGER_NAME


def read_ledger(dest: Path) -> Dict[str, str]:
    """The `{name: sha256}` of files this pack manages in `dest`, or `{}`."""
    try:
        doc = json.loads(_ledger_path(dest).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = doc.get("files") if isinstance(doc, dict) else None
    if not isinstance(files, dict):
        return {}
    return {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)}


def _write_ledger(dest: Path, files: Dict[str, str]) -> None:
    target = _ledger_path(dest)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "files": files}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def has_synced(dest: str | Path) -> bool:
    """True once the pack has been synced into `dest` at least once."""
    return _ledger_path(Path(dest)).is_file()


def _validate(name: str, data: bytes) -> None:
    """Reject a file that can't load — malformed JSON or a Python syntax error —
    before it ever lands on disk."""
    if name.endswith(".json"):
        try:
            obj = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PackError(f"{name}: invalid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise PackError(f"{name}: a JSON converter must be an object")
    elif name.endswith(".py"):
        try:
            compile(data.decode("utf-8"), name, "exec")  # syntax-check only; never executed
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise PackError(f"{name}: Python syntax error: {exc}") from exc


def _atomic_write(target: Path, data: bytes) -> None:
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def sync(
    dest_dir: str | Path,
    *,
    manifest_url: str = DEFAULT_MANIFEST_URL,
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Mirror the pack into `dest_dir`. Returns a summary:
    `{added, updated, unchanged, removed, failed, managed, changed}`.

    Only files this pack manages (the manifest's set + the prior ledger) are ever
    written or removed; everything else in `dest_dir` is left untouched. A file
    that fails to download/verify is skipped and its previously-synced copy (if
    any) is kept — a transient network error never deletes a working converter.
    `manifest` may be passed directly (tests / a pre-fetched doc) to skip the
    network fetch."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    doc = manifest if manifest is not None else fetch_manifest(manifest_url)
    base = doc.get("base_url")
    if not isinstance(base, str) or not base:
        base = DEFAULT_BASE_URL

    prev = read_ledger(dest)
    managed: Dict[str, str] = {}
    added: List[str] = []
    updated: List[str] = []
    unchanged: List[str] = []
    removed: List[str] = []
    failed: List[Dict[str, Any]] = []

    for entry in doc["files"]:
        if not isinstance(entry, dict):
            continue
        raw_name = entry.get("name")
        try:
            name = _safe_name(raw_name or "")
            sha = entry.get("sha256")
            if not isinstance(sha, str) or not sha:
                raise PackError(f"{name}: manifest entry has no sha256")
            sha = sha.lower()
            target = dest / name
            existed = target.exists()
            if existed and _sha(target.read_bytes()) == sha:
                unchanged.append(name)
                managed[name] = sha
                continue
            data = _download(base + name)
            if _sha(data) != sha:
                raise PackError(f"{name}: checksum does not match the manifest")
            _validate(name, data)
            _atomic_write(target, data)
            (updated if (existed or name in prev) else added).append(name)
            managed[name] = sha
        except PackError as exc:
            failed.append({"name": raw_name, "error": str(exc)})
            logger.warning("converter pack: %s", exc)
            # A failure must not orphan a working prior copy: keep managing it.
            if isinstance(raw_name, str) and raw_name in prev:
                managed[raw_name] = prev[raw_name]

    # Remove defaults we previously managed that the manifest no longer lists —
    # never a file we didn't put there.
    for name in prev:
        if name in managed:
            continue
        target = dest / name
        if target.exists():
            try:
                target.unlink()
                removed.append(name)
            except OSError as exc:
                logger.warning("converter pack: could not remove %s: %s", name, exc)
                managed[name] = prev[name]  # still ours — keep tracking it

    _write_ledger(dest, managed)
    return {
        "added": sorted(added),
        "updated": sorted(updated),
        "unchanged": sorted(unchanged),
        "removed": sorted(removed),
        "failed": failed,
        "managed": len(managed),
        "changed": len(added) + len(updated) + len(removed),
    }
