"""User-defined Tuya device DP override loader.

Loads a single JSON file keyed by product_id. When a device's product_id is
found, the generator uses the entries to override or inject DP metadata.

File format (default path: custom_converters.json):

    {
      "<product_id>": {
        "model": "Display Name",
        "dp_meta": {
          "<dp_id>": {
            "code": "entity_name",
            "type": "Integer|Boolean|Enum|String",
            "ent_info": [comp, class, unit, icon],
            "scale": 1, "min": 0, "max": 100, "step": 1,
            "options": [...], "val_map": {...},
            "active": true
          }
        }
      }
    }

All fields under each dp_meta entry are optional. `code`, `type`, and
`ent_info` are consumed directly by the generator; remaining keys are merged
into the DP's metadata bag (scale/min/max/options/val_map/etc.).

`"active": true` marks an incremental/delta DP (read the bridge's `active`
stream, ignore the retained `passive` snapshot) — a per-product override for the
global set in mapping.ACTIVE_ONLY_CODES (e.g. add_ele).
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("user_converter")

FILENAME = "custom_converters.json"
ENV_VAR = "RUSTUYA_CONVERTERS"
# Bundled example used when no user file is found on disk.
PACKAGED_PATH = Path(__file__).resolve().parent.parent / "data" / FILENAME


def resolve_path(path: Optional[str] = None) -> Path:
    """Resolution order: explicit arg > $RUSTUYA_CONVERTERS > ./custom_converters.json
    > packaged example. The CWD file is kept first so existing workflows (and the
    golden snapshots generated from the repo-root file) stay unchanged."""
    if path:
        return Path(path)
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    cwd = Path(FILENAME)
    if cwd.exists():
        return cwd
    return PACKAGED_PATH


class UserConverter:
    def __init__(self, path: Optional[str] = None):
        self.path = resolve_path(path)
        self.mapping: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                self.mapping = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {self.path}: {e}")

    def find(self, product_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not product_id:
            return None
        return self.mapping.get(product_id)
