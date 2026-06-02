"""Derive HA discovery topics & value-templates from a rustuya-bridge config.

The bridge's source of truth is the retained MQTT topic ``{root}/bridge/config``
(its resolved config, published at startup). Given that config's topic and
payload templates, this module computes the concrete topics and Jinja
``value_template`` expressions the HA entities must use — replacing the
previously hardcoded layout.

The derive logic is **pure Python** with no third-party dependency. The forward
direction needed here — render a topic, and locate where ``{value}`` sits in the
payload template — is simple, so the semantics of the bridge's Rust helpers
(``src/template.rs`` render + ``src/payload.rs`` sentinel walk) are mirrored
directly. (The hard *reverse* direction — extracting a DP's value from a live
payload — is the bridge's job at runtime, not ours.)
"""
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# rustuya-bridge src/template.rs:19 + src/payload.rs:53
TOPIC_VARS = ["id", "name", "dp", "action", "cid", "type", "level"]
PAYLOAD_VARS = ["value", "dps", "timestamp", "root"]
RECOGNIZED_VARS = TOPIC_VARS + PAYLOAD_VARS

# rustuya-bridge src/config.rs:11-18 defaults
DEFAULTS = {
    "root": "rustuya",
    "command_topic": "{root}/command",
    "event_topic": "{root}/event/{type}/{id}",
    "message_topic": "{root}/{level}/{id}",
    "payload_template": "{value}",
}

# HA discovery convention — fixed, independent of the bridge.
HA_DISCOVERY_TOPIC = "homeassistant/{}/{}_{}/config"
# HA writes a value => a "set" action; device errors arrive at level "error".
COMMAND_ACTION = "set"
ERROR_LEVEL = "error"

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def render_topic(template: str, **vars: str) -> str:
    """Substitute ``{key}`` placeholders; unknown keys are left literal.

    Mirrors rustuya-bridge ``render_template`` (src/template.rs:28-51)."""
    def sub(m):
        key = m.group(1)
        val = vars.get(key)
        return val if val is not None else m.group(0)
    return _PLACEHOLDER_RE.sub(sub, template)


def _sentinel(var: str) -> str:
    return f"@@RB::{var}@@"


def placeholder_path(payload_template: str, var: str) -> Optional[List[Any]]:
    """Return the JSON path (list of keys/indices) where ``{var}`` sits inside a
    payload template, or ``None`` if the template isn't JSON-shaped / the var is
    absent. Bare ``{value}`` as the whole template -> ``[]`` (the payload root).

    Mirrors the bridge's sentinel reverse-parse (src/payload.rs:14-31)."""
    s = payload_template
    for v in RECOGNIZED_VARS:
        # Quoted form keeps its quotes; bare form gets quoted so the result is JSON.
        s = s.replace(f'"{{{v}}}"', f'"{_sentinel(v)}"')
        s = s.replace(f"{{{v}}}", f'"{_sentinel(v)}"')
    try:
        tree = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
    target = _sentinel(var)

    def walk(node, path):
        if node == target:
            return path
        if isinstance(node, dict):
            for k, v in node.items():
                r = walk(v, path + [k])
                if r is not None:
                    return r
        elif isinstance(node, list):
            for i, v in enumerate(node):
                r = walk(v, path + [i])
                if r is not None:
                    return r
        return None

    return walk(tree, [])


def jinja_accessor(base: str, path: List[Any], index: Optional[str] = None) -> str:
    """Build a Jinja accessor from a JSON path. Identifier keys use dot access,
    others use bracket access. ``index`` (a dp id) appends a final ``['index']``
    for multi-DP payloads where the value lives in a dps dict.

    e.g. base='value_json', path=['value'] -> 'value_json.value'
         path=['data','v'] -> 'value_json.data.v'
         path=[], index='2' -> "value_json['2']" """
    expr = base
    for key in path:
        if isinstance(key, int):
            expr += f"[{key}]"
        elif isinstance(key, str) and key.isidentifier():
            expr += f".{key}"
        else:
            expr += f"['{key}']"
    if index is not None:
        expr += f"['{index}']"
    return expr


@dataclass
class BridgeConfig:
    root: str = DEFAULTS["root"]
    command_topic: str = DEFAULTS["command_topic"]
    event_topic: str = DEFAULTS["event_topic"]
    message_topic: str = DEFAULTS["message_topic"]
    payload_template: str = DEFAULTS["payload_template"]

    @classmethod
    def from_dict(cls, cfg: Dict[str, Any]) -> "BridgeConfig":
        """Build from a bridge config dict (the retained bridge/config payload or
        a config file). Missing keys fall back to bridge defaults."""
        def g(key, default):
            v = cfg.get(key)
            return v if v else default
        return cls(
            root=g("mqtt_root_topic", DEFAULTS["root"]),
            command_topic=g("mqtt_command_topic", DEFAULTS["command_topic"]),
            event_topic=g("mqtt_event_topic", DEFAULTS["event_topic"]),
            message_topic=g("mqtt_message_topic", DEFAULTS["message_topic"]),
            payload_template=g("mqtt_payload_template", DEFAULTS["payload_template"]),
        )

    @classmethod
    def from_json_file(cls, path: str) -> "BridgeConfig":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_bridge_config_topic(cls, payload: str) -> "BridgeConfig":
        """Parse the retained ``{root}/bridge/config`` JSON payload."""
        return cls.from_dict(json.loads(payload))

    # --- derived helpers ---

    @property
    def per_dp(self) -> bool:
        """True when each DP gets its own topic (event topic embeds ``{dp}``)."""
        return "{dp}" in self.event_topic

    def value_path(self) -> Optional[List[Any]]:
        """Path to the per-DP value (``{value}``) in the payload template; for
        multi-DP, the path to the dps dict (``{dps}``)."""
        var = "value" if self.per_dp else "dps"
        return placeholder_path(self.payload_template, var)

    def type_path(self) -> Optional[List[Any]]:
        return placeholder_path(self.payload_template, "type")


LEGACY_DICT = {
    "mqtt_root_topic": "rustuya",
    "mqtt_command_topic": "{root}/command/{action}/{id}/{dp}",
    "mqtt_event_topic": "{root}/event/{id}/{dp}",
    "mqtt_message_topic": "{root}/{level}/{id}",
    "mqtt_payload_template": '{"type": "{type}", "value": {value}, "name": "{name}"}',
}
# The config that reproduces the pre-refactor hardcoded output (see tests).
LEGACY = BridgeConfig.from_dict(LEGACY_DICT)


def validate_payload_template(template: str) -> Optional[str]:
    """Return a warning string if the payload template looks un-parseable, else
    None. Must be JSON-shaped after sentinel substitution and contain at least
    one recognized value placeholder."""
    if placeholder_path(template, "value") is None and placeholder_path(template, "dps") is None:
        return "payload template has no {value}/{dps} placeholder or is not JSON-shaped"
    return None
