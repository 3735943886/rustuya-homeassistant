"""User-defined Tuya device DP overrides + code converters.

Converters live as a **drop-in directory** (`custom_converters/`): any number of
`*.json` files keyed by product_id (merged together) plus `*.py` *code
converters* (loaded at runtime — see manager_plugin.code_converters). When a
device's product_id is found in the merged mapping, the generator uses the
entries to override or inject DP metadata. A single explicit `*.json` file is
still accepted (CLI `--converters`, `$RUSTUYA_CONVERTERS`, tests) for
convenience.

JSON file format (each file is one of these, merged by product_id):

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

When the same product_id appears in more than one JSON file, the files are
merged in sorted-filename order with a recursive (deep) merge — last writer wins
on a leaf, nested dicts combine — so one product may be split across files.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("user_converter")

# The drop-in directory name, and the env var (which may point at the directory
# OR a single `*.json` file — both are honored).
DIRNAME = "custom_converters"
ENV_VAR = "RUSTUYA_CONVERTERS"
# Legacy single-file name, still used as the canonical whole-mapping write target
# (save_converters); the file-level editor (write_file) supersedes it.
FILENAME = "custom_converters.json"

# The only file types the editor/loader will surface or touch.
_ALLOWED_SUFFIXES = (".json", ".py")


def resolve_path(path: Optional[str] = None) -> Path:
    """Resolve the converters source: explicit arg > $RUSTUYA_CONVERTERS >
    ./custom_converters/. Normally a directory (its `*.json` are merged), but an
    explicit `*.json` file is honored too. The default dir need not exist — a
    missing source just loads as an empty mapping (no bundled fallback, so a
    fresh install applies no overrides until the user adds some)."""
    if path:
        return Path(path)
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    return Path(DIRNAME)


def savable_dir(path: Optional[str] = None) -> Path:
    """The writable converters directory — where new files are created and edits
    land. If the source resolves to a single `.json` file (existing or not), that
    file's parent directory is used."""
    target = resolve_path(path)
    if target.suffix == ".json":  # explicit single file — detect by suffix so a
        return target.parent      # not-yet-created path isn't mistaken for a dir
    return target


def savable_path(path: Optional[str] = None) -> Path:
    """Canonical whole-mapping write target. An explicit single `.json` file is
    written directly; otherwise a `*.json` inside the writable dir. Kept for
    `save_converters`; the per-file editor (write_file) is preferred."""
    target = resolve_path(path)
    if target.suffix == ".json":
        return target
    return savable_dir(path) / FILENAME


def _json_files(target: Path) -> List[Path]:
    """The `*.json` converter files under `target` (a dir → sorted glob; a single
    `.json` → itself), in deterministic order so merges are reproducible."""
    if target.is_dir():
        return sorted(p for p in target.glob("*.json") if not p.name.startswith("."))
    if target.is_file() and target.suffix == ".json":
        return [target]
    return []


def py_files(path: Optional[str] = None) -> List[Path]:
    """The `*.py` code-converter files under the resolved directory, sorted.
    Names starting with `_`/`.` are skipped (helpers, dotfiles)."""
    target = resolve_path(path)
    if not target.is_dir():
        return []
    return sorted(p for p in target.glob("*.py") if not p.name.startswith(("_", ".")))


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge `src` into `dst` (last writer wins on leaves); nested
    dicts combine rather than replace — so one product_id may span files."""
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def load_converters(path: Optional[str] = None) -> Dict[str, Any]:
    """Load + merge the resolved converters mapping (empty dict if none)."""
    return UserConverter(path).mapping


def save_converters(mapping: Dict[str, Any], path: Optional[str] = None) -> str:
    """Atomically write the whole `mapping` as pretty JSON to `savable_path`.
    Returns the written path. (Whole-mapping write; the per-file editor uses
    write_file instead.)"""
    target = savable_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, target)
    return str(target)


# ── file-level CRUD for the editor (operate on the resolved directory) ───────
def _safe_name(name: str) -> str:
    """Reject path traversal / unexpected types; return the bare filename."""
    n = Path(name).name
    if n != name or not n or n.startswith("."):
        raise ValueError(f"invalid converter filename: {name!r}")
    if Path(n).suffix not in _ALLOWED_SUFFIXES:
        raise ValueError(f"converter files must be .json or .py: {name!r}")
    return n


def list_files(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """List converter files in the resolved source: [{name, kind, size}]."""
    target = resolve_path(path)
    if target.is_dir():
        files = sorted(
            p
            for p in target.iterdir()
            if p.is_file() and p.suffix in _ALLOWED_SUFFIXES and not p.name.startswith(".")
        )
    elif target.is_file():
        files = [target]
    else:
        files = []
    return [{"name": p.name, "kind": p.suffix.lstrip("."), "size": p.stat().st_size} for p in files]


def file_path(name: str, path: Optional[str] = None) -> Path:
    """The absolute path a given converter filename writes to (name validated)."""
    return savable_dir(path) / _safe_name(name)


def read_file(name: str, path: Optional[str] = None) -> str:
    """Raw text of one converter file in the resolved source directory."""
    target = resolve_path(path)
    base = target if target.is_dir() else target.parent
    return (base / _safe_name(name)).read_text(encoding="utf-8")


def write_file(name: str, content: str, path: Optional[str] = None) -> str:
    """Atomically write raw text to one converter file in the writable directory.
    `.json` is validated as JSON first; `.py` is written verbatim. Returns the
    written path."""
    name = _safe_name(name)
    if name.endswith(".json"):
        json.loads(content)  # raise on malformed JSON before touching disk
    d = savable_dir(path)
    d.mkdir(parents=True, exist_ok=True)
    target = d / name
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, target)
    return str(target)


def delete_file(name: str, path: Optional[str] = None) -> None:
    """Delete one converter file from the writable directory (idempotent)."""
    (savable_dir(path) / _safe_name(name)).unlink(missing_ok=True)


class UserConverter:
    def __init__(self, path: Optional[str] = None):
        self.path = resolve_path(path)
        self.mapping: Dict[str, Any] = {}
        self._load()

    def _load(self):
        merged: Dict[str, Any] = {}
        for f in _json_files(self.path):
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    _deep_merge(merged, data)
                else:
                    logger.error(f"Ignoring non-object converter file {f}")
            except Exception as e:
                logger.error(f"Failed to load {f}: {e}")
        self.mapping = merged

    def find(self, product_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not product_id:
            return None
        return self.mapping.get(product_id)
