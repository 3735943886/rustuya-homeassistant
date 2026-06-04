"""Topic and payload seams for discovery generation.

These abstractions are the injection point for issue #2: the generator routes
every topic build and every Jinja ``value_template`` through a ``TopicScheme`` /
``PayloadCodec``.

- ``DefaultTopicScheme`` / ``DefaultPayloadCodec`` reproduce the previous
  hardcoded behaviour byte-for-byte (golden baseline).
- ``BridgeTopicScheme`` / ``BridgePayloadCodec`` derive the layout from a
  ``BridgeConfig`` (rustuya-bridge's resolved config). Fed the legacy profile
  (``bridge.LEGACY``) they produce output identical to the Default ones — that
  parity is the proof the derive logic is correct (see tests).
"""
import json
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from .mapping import UNAVAILABLE_ERROR_CODES
from .bridge import (
    BridgeConfig, render_topic, jinja_accessor,
    HA_DISCOVERY_TOPIC, COMMAND_ACTION, ERROR_LEVEL,
)

DEFAULT_MANUFACTURER = "rustuya"

# Legacy Jinja accessors — the payload shape the hardcoded templates assumed.
LEGACY_VALUE_EXPR = "value_json.value"
LEGACY_TYPE_EXPR = "value_json.type"


# --- shared template builders (parameterized by JSON accessor expressions) ---

def build_availability_template() -> str:
    # The error/message topic payload carries errorCode regardless of the user's
    # event payload_template, so this is fixed.
    return (
        "{{ 'offline' if value_json is defined and value_json != None "
        "and value_json.errorCode in " + json.dumps(sorted(UNAVAILABLE_ERROR_CODES)) +
        " else 'online' }}"
    )


def build_value_template(comp: str, scale: int = 0,
                         val_map: Optional[Dict[str, Any]] = None,
                         value_expr: str = LEGACY_VALUE_EXPR,
                         type_expr: Optional[str] = LEGACY_TYPE_EXPR,
                         skip_active: bool = False,
                         active_only: bool = False,
                         transform: Optional[str] = None) -> str:
    """Jinja value_template reading the device's MQTT state payload.

    The active/passive distinction relies on the payload carrying ``{type}``
    (``type_expr`` set). With it: ``event`` entities keep only `active`, and
    stateful entities with ``skip_active`` drop `active` (render '' so HA skips
    the update) to read only the retained `passive` snapshot. Without a payload
    ``{type}`` (``type_expr`` is None), no such filter is emitted — the event
    topic itself is expected to separate active/passive, or it can't be told."""
    # When value_expr is the payload root itself (bare `{value}` payloads), the
    # "<expr> != None" clause duplicates "value_json != None" — drop the tail.
    if value_expr == "value_json":
        guard = "value_json is defined and value_json != None"
    else:
        guard = "value_json is defined and value_json != None and %s != None" % value_expr
    if comp == "event":
        cond = guard
        if type_expr:
            cond = "%s and %s | default('') == 'active'" % (guard, type_expr)
        return "{{ { \"event_type\": %s } | to_json if %s else '' }}" % (value_expr, cond)

    if comp in ["binary_sensor", "switch"] and not val_map:
        inner = ("'true' if %s and %s == true else 'false' if %s and %s == false else 'unknown'"
                 % (guard, value_expr, guard, value_expr))
    else:
        base = value_expr
        if scale > 0:
            expr = "((%s | float) / %g) | round(1)" % (base, 10 ** scale)
        else:
            expr = base
        if val_map:
            map_str = json.dumps(val_map)
            mapped_expr = "(%s | string | lower)" % expr
            final_expr = "%s[%s] | default(%s)" % (map_str, mapped_expr, mapped_expr)
        else:
            final_expr = expr
        # `transform` wraps the resolved value expression (e.g. "100 - (%s | int)"
        # to invert a cover position). It composes with whatever `value_expr` the
        # codec derived, so the payload-shape adaptation is preserved.
        if transform:
            final_expr = transform % final_expr
        inner = "%s if %s else 'unknown'" % (final_expr, guard)

    if active_only and type_expr:
        # Incremental/delta DP (e.g. add_ele): keep only `active`, drop the stale
        # retained `passive` snapshot. Parens needed — `inner` is itself a
        # conditional. (When there's no {type} to tell them apart, fall through.)
        inner = "(%s) if %s | default('') == 'active' else ''" % (inner, type_expr)
    elif skip_active and type_expr:
        # Absolute-state DP: drop `active` duplicate, read the `passive` snapshot.
        # `X if a else Y if b else Z` binds right, so prefixing is enough.
        inner = "'' if %s | default('') == 'active' else %s" % (type_expr, inner)
    return "{{ %s }}" % inner


# --- TopicScheme ---

@runtime_checkable
class TopicScheme(Protocol):
    """Maps (device, dp, component) tuples to MQTT topic strings."""

    def discovery(self, component: str, dev_id: str, code: str) -> str: ...
    def state(self, dev_id: str, dp_id: str, active: bool = False) -> str: ...
    def command(self, dev_id: str, dp_id: str) -> str: ...
    def availability(self, dev_id: str) -> str: ...


class DefaultTopicScheme:
    """The historical hardcoded topic layout (unchanged output)."""

    DISCOVERY = "homeassistant/{}/{}_{}/config"
    STATE = "rustuya/event/{}/{}"
    COMMAND = "rustuya/command/set/{}/{}"
    ERROR = "rustuya/error/{}"

    def discovery(self, component: str, dev_id: str, code: str) -> str:
        return self.DISCOVERY.format(component, dev_id, code)

    def state(self, dev_id: str, dp_id: str, active: bool = False) -> str:
        return self.STATE.format(dev_id, dp_id)  # legacy topic has no {type}

    def command(self, dev_id: str, dp_id: str) -> str:
        return self.COMMAND.format(dev_id, dp_id)

    def availability(self, dev_id: str) -> str:
        return self.ERROR.format(dev_id)


class BridgeTopicScheme:
    """Topic layout derived from a rustuya-bridge config."""

    def __init__(self, config: BridgeConfig):
        self.config = config

    def discovery(self, component: str, dev_id: str, code: str) -> str:
        return HA_DISCOVERY_TOPIC.format(component, dev_id, code)

    def state(self, dev_id: str, dp_id: str, active: bool = False) -> str:
        # rustuya-bridge README §Events: `active` = no-retain delta (event
        # semantics, fires once), `passive` = retained full snapshot (current
        # state, recoverable on reconnect). HA `event` entities need `active`;
        # all stateful entities read `passive`. (No-op when {type} not in topic.)
        return render_topic(self.config.event_topic,
                            root=self.config.root, id=dev_id, dp=str(dp_id),
                            type="active" if active else "passive")

    def command(self, dev_id: str, dp_id: str) -> str:
        return render_topic(self.config.command_topic,
                            root=self.config.root, action=COMMAND_ACTION,
                            id=dev_id, dp=str(dp_id))

    def availability(self, dev_id: str) -> str:
        return render_topic(self.config.message_topic,
                            root=self.config.root, level=ERROR_LEVEL, id=dev_id)


# --- PayloadCodec ---

@runtime_checkable
class PayloadCodec(Protocol):
    """Produces Jinja templates that read a device's MQTT state payload."""

    def value_template(self, comp: str, scale: int = 0,
                       val_map: Optional[Dict[str, Any]] = None,
                       dp_id: Optional[str] = None,
                       active_only: bool = False,
                       transform: Optional[str] = None) -> str: ...
    def availability_template(self) -> str: ...


class DefaultPayloadCodec:
    """Templates for the historical payload shape. Legacy ran with retain=true and
    a {type}-carrying payload, so stateful entities read passive only."""

    def availability_template(self) -> str:
        return build_availability_template()

    def value_template(self, comp: str, scale: int = 0,
                       val_map: Optional[Dict[str, Any]] = None,
                       dp_id: Optional[str] = None,
                       active_only: bool = False,
                       transform: Optional[str] = None) -> str:
        return build_value_template(comp, scale, val_map,
                                    skip_active=not active_only, active_only=active_only,
                                    transform=transform)


class BridgePayloadCodec:
    """value_template / availability derived from a bridge payload_template.

    per-DP: the payload carries one DP's value (``{value}``) -> value_expr is the
    accessor to that value. multi-DP: the payload carries the full dps dict
    (``{dps}``) -> value_expr indexes it by dp id."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self._per_dp = config.per_dp
        vpath = config.value_path()
        self._value_path = vpath if vpath is not None else []  # [] = payload root
        tpath = config.type_path()
        # None when the payload carries no {type}: no active/passive filter is
        # emitted (the topic is expected to separate them, or it can't be told).
        self._type_expr = jinja_accessor("value_json", tpath) if tpath is not None else None
        self._skip_active = config.skip_active

    def availability_template(self) -> str:
        return build_availability_template()

    def value_template(self, comp: str, scale: int = 0,
                       val_map: Optional[Dict[str, Any]] = None,
                       dp_id: Optional[str] = None,
                       active_only: bool = False,
                       transform: Optional[str] = None) -> str:
        index = None if self._per_dp else (str(dp_id) if dp_id is not None else None)
        value_expr = jinja_accessor("value_json", self._value_path, index=index)
        return build_value_template(comp, scale, val_map, value_expr, self._type_expr,
                                    skip_active=(self._skip_active and not active_only),
                                    active_only=active_only, transform=transform)


def scheme_for(config: BridgeConfig):
    """Convenience: (TopicScheme, PayloadCodec) pair for a bridge config."""
    return BridgeTopicScheme(config), BridgePayloadCodec(config)
