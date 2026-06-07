"""Backup / restore of the retained HA discovery state.

The full discovery state lives entirely in the retained ``homeassistant/.../config``
MQTT topics, and publish/clear already collect it before mutating — so a backup is
just a serialization of that map, and a restore re-publishes it (retained) while
clearing any topics added since. This gives an "undo" for publish/clear.

Backup files hold device names/ids/models (same sensitivity as tuyadevices.json),
so the backup dir should be gitignored.
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

# Pure plan builder lives in core; re-exported here so the CLI keeps a single
# `backup.*` surface (backup.restore_plan) over both persistence and planning.
from ..core.restore import restore_plan  # noqa: F401  (re-export)

DEFAULT_DIR = ".rustuya-ha-backups"
KEEP = 20  # rotate: keep the newest N auto-backups


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


def load(path: str) -> Dict[str, dict]:
    """Return the {topic: payload} map from a backup file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["topics"] if isinstance(data, dict) and "topics" in data else data


def listing(backup_dir: str) -> List[Path]:
    d = Path(backup_dir)
    if not d.exists():
        return []
    return sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def latest(backup_dir: str) -> Optional[str]:
    files = listing(backup_dir)
    return str(files[0]) if files else None
