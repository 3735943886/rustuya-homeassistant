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
        },
        "discovery_overrides": {
          "<component>": {
            "position_dp": "2",
            "invert_position": true, "invert_set_position": true,
            "payload_open": "open", "payload_close": "close", "payload_stop": "stop"
          }
        }
      }
    }

All fields under each dp_meta entry are optional. `code`, `type`, and
`ent_info` are consumed directly by the generator; remaining keys are merged
into the DP's metadata bag (scale/min/max/options/val_map/etc.).

`discovery_overrides` patches the *generated discovery payload* for whole
components that dp_meta can't reach (e.g. a cover's position inversion, command
payload map). It is keyed by component type and applies to every entity of that
component on the device. Keys are either structured or verbatim:

- `<role>_dp` (command_dp/state_dp/position_dp/set_position_dp) names a DP,
  resolved to a topic through the active scheme (stays correct per-device). Read
  roles (state/position) also get their value_template rebuilt through the codec
  — so it adapts to the bridge payload shape rather than hardcoding
  `value_json.value`.
- `<role>_stream` (state_stream/position_stream): "active" | "passive" (default
  "passive"). Passive reads the retained `state` snapshot; active keeps only the
  delta.
- `invert_position: true` inverts the read direction (position_template emits
  `100 - value`); `invert_set_position: true` independently inverts the write
  direction (a `set_position_template` of `100 - value`).
- `<role>_dp: null` drops that whole role — its topic, and for a read role its
  template (e.g. a state-less, optimistic cover that reports only position).
  `"remove": ["field", ...]` drops arbitrary payload keys.
- every other key (payload_*, state_*, device_class, icon, a literal *_template,
  ...) is device- and payload-independent and merges verbatim.

See generator.DiscoveryGenerator._apply_overrides.

`"active": true` marks an incremental/delta DP (read the bridge's `active`
stream, ignore the retained `state` snapshot) — a per-product override for the
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
