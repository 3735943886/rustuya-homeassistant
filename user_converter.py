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
            "options": [...], "val_map": {...}
          }
        }
      }
    }

All fields under each dp_meta entry are optional. `code`, `type`, and
`ent_info` are consumed directly by the generator; remaining keys are merged
into the DP's metadata bag (scale/min/max/options/val_map/etc.).
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("user_converter")

DEFAULT_PATH = "custom_converters.json"


class UserConverter:
    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or DEFAULT_PATH)
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
