"""Backup / restore persistence for the retained HA discovery state.

The full discovery state lives entirely in the retained ``homeassistant/.../config``
MQTT topics, and publish/clear already collect it before mutating — so a backup is
just a serialization of that map, and a restore re-publishes it (retained) while
clearing any topics added since. This gives an "undo" for publish/clear.

Pure-ish: only the local filesystem is touched (no MQTT, no third-party deps), so
both the CLI and the manager plugin can share one snapshot format and rotation
policy. The pure plan builder lives in ``core.restore`` and is re-exported here so
callers get a single ``backup.*`` surface over both persistence and planning.

Backup files hold device names/ids/models (same sensitivity as tuyadevices.json),
so the backup dir should be gitignored.
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from .restore import restore_plan  # noqa: F401  (re-export: backup.restore_plan)

DEFAULT_DIR = ".rustuya-ha-backups"
KEEP = 20  # rotate: keep the newest N auto-backups


def safe_path(directory: str, name: str) -> str:
    """Resolve a backup `name` strictly inside `directory`, blocking traversal.

    A restore target is user-supplied, so confine it: strip any directory parts
    (`basename`) then verify, via realpath + commonpath, that the result stays in
    `directory`. Raises ValueError if it would escape — the barrier that keeps an
    arbitrary-path read from reaching `load()`."""
    base = os.path.realpath(directory)
    target = os.path.realpath(os.path.join(base, os.path.basename(name)))
    if os.path.commonpath((base, target)) != base:
        raise ValueError(f"backup path escapes its directory: {name!r}")
    return target


def save(backup_dir: str, mqtt_data: Dict[str, dict], prefix: str = "auto") -> str:
    """Serialize the collected retained discovery (topic -> payload) to a
    timestamped file. Returns the path. Rotates old auto-backups."""
    d = Path(backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    topics = {t: m["payload"] for t, m in mqtt_data.items()}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = d / f"{prefix}-{stamp}.json"
    # Avoid clobbering within the same second.
    n = 1
    while path.exists():
        path = d / f"{prefix}-{stamp}-{n}.json"
        n += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"created": stamp, "count": len(topics), "topics": topics},
                  f, indent=2, ensure_ascii=False)
    _rotate(d, prefix)
    return str(path)


def _rotate(d: Path, prefix: str):
    files = sorted(d.glob(f"{prefix}-*.json"), key=lambda p: p.stat().st_mtime)
    for stale in files[:-KEEP]:
        try:
            stale.unlink()
        except OSError:
            pass


def snapshot_file(backup_dir: str, src: str, prefix: str = "converters") -> Optional[str]:
    """Copy an existing file (e.g. the converters JSON) into the backup dir under
    a timestamped name, so a write that overwrites it is undoable. Returns the
    backup path, or None if the source doesn't exist yet (nothing to back up)."""
    s = Path(src)
    if not s.exists():
        return None
    d = Path(backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = d / f"{prefix}-{stamp}.json"
    n = 1
    while dst.exists():
        dst = d / f"{prefix}-{stamp}-{n}.json"
        n += 1
    dst.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")
    _rotate(d, prefix)  # bound growth like save() does (else converters backups pile up)
    return str(dst)


def load(path: str) -> Dict[str, dict]:
    """Return the {topic: payload} map from a backup file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["topics"] if isinstance(data, dict) and "topics" in data else data


def listing(backup_dir: str, limit: Optional[int] = None) -> List[Path]:
    """All backup files, newest first. ``limit`` caps the result (the full set
    can grow with manually-dropped files even though auto/converters rotate)."""
    d = Path(backup_dir)
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit] if limit is not None else files


def latest(backup_dir: str) -> Optional[str]:
    files = listing(backup_dir)
    return str(files[0]) if files else None
