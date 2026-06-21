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
                         passive_only: bool = False,
                         transform: Optional[str] = None) -> str:
    """Jinja value_template reading the device's MQTT state payload.

    The type distinction relies on the payload carrying ``{type}``
    (``type_expr`` set). With it: ``event`` entities keep only `active`, and
    stateful entities with ``skip_active`` keep only `state` (render '' for
    everything else so HA skips the update) to read the retained full-state
    snapshot, ignoring the ephemeral no-retain `active`/`passive` deltas.
    Without a payload ``{type}`` (``type_expr`` is None), no such filter is
    emitted — the event topic itself is expected to separate the types, or it
    can't be told.

    The "no value" fallback renders ``''`` (empty string), not ``none``: HA
    *ignores* an empty payload (keeps the previous state) but resets the entity
    to ``unknown`` on a ``None``. This matters when several DPs share one
    state_topic (no ``{dp}`` in the event topic) and the bridge sends partial
    deltas — e.g. ``{"1": true}`` must not blank out the entity reading DP 2."""
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
        inner = ("'true' if %s and %s == true else 'false' if %s and %s == false else ''"
                 % (guard, value_expr, guard, value_expr))
    else:
        base = value_expr
        if scale > 0:
            expr = "((%s | float) / %g) | round(%d)" % (base, 10 ** scale, scale)
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
        inner = "%s if %s else ''" % (final_expr, guard)

    # In cache mode the bridge emits no-retain `active`/`passive` deltas plus a
    # retained `state` snapshot. Pick which {type} this entity reads. Parens
    # needed — `inner` is itself a conditional. (No {type} to tell them apart =>
    # no filter, fall through.)
    keep = ("active" if active_only else "passive" if passive_only
            else "state" if skip_active else None)
    #   active_only: incremental/delta DP (e.g. add_ele) reads the `active` delta,
    #     never the snapshot, which would re-add the same increment (double-count).
    #   passive_only: the `passive` companion of such a delta DP — same no-retain
    #     delta semantics, but for devices that report add_ele via `passive`
    #     (readback) instead of `active`. Also no replay on reconnect (no-retain).
    #   skip_active: absolute-state DP reads the retained `state` snapshot, never
    #     the ephemeral (possibly partial) deltas.
    if keep and type_expr:
        inner = "(%s) if %s | default('') == '%s' else ''" % (inner, type_expr, keep)
    return "{{ %s }}" % inner


# --- command (write) value encoding ---

def _jinja_sq(s: str) -> str:
    """Escape a string for use inside a single-quoted Jinja literal."""
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


def build_command_value_expr(kind: str = "raw", scale: int = 0,
                             val_map: Optional[Dict[str, Any]] = None,
                             transform: Optional[str] = None) -> str:
    """Jinja snippet (WITH its own JSON quoting) for the DP value HA must send.

    This is the inverse of ``build_value_template``: scale multiplies (read
    divides), and ``val_map`` is inverted (read maps device-raw -> friendly
    label; write maps the chosen label back to the device-raw). ``value`` is
    HA's command-template variable. The returned snippet is dropped verbatim
    after ``"<dp>": `` inside the command payload, so each kind emits the right
    JSON type/quoting itself.

    kinds:
      bool   -> ``{{ value }}`` (value is payload_on/off 'true'/'false' -> JSON literal)
      number -> integer, scale-multiplied; ``transform`` can wrap it (e.g. invert)
      enum   -> inverted val_map lookup, emitted as a quoted JSON string
      string -> ``{{ value | tojson }}`` (self-quoting JSON string)
      raw    -> the value as a quoted JSON string verbatim
    """
    if kind == "bool":
        return "{{ value }}"
    if kind == "number":
        if scale and scale > 0:
            expr = "(value | float * %d) | round(0) | int" % (10 ** scale)
        else:
            expr = "(value | float) | int"
        if transform:
            expr = transform % ("(%s)" % expr)
        return "{{ %s }}" % expr
    if kind == "enum" and val_map:
        # Invert device-raw -> label into label -> device-raw (first raw wins for
        # duplicate labels). Single-quoted so it nests inside the JSON template.
        inv: Dict[str, str] = {}
        for raw, label in val_map.items():
            inv.setdefault(str(label), str(raw))
        items = ", ".join("'%s': '%s'" % (_jinja_sq(k), _jinja_sq(v)) for k, v in inv.items())
        return "\"{{ {%s}[value] | default(value) }}\"" % items
    if kind == "string":
        return "{{ value | tojson }}"
    return "\"{{ value }}\""


# --- TopicScheme ---

@runtime_checkable
class TopicScheme(Protocol):
    """Maps (device, dp, component) tuples to MQTT topic strings."""

    def discovery(self, component: str, dev_id: str, code: str) -> str: ...
    def state(self, dev_id: str, dp_id: str, active: bool = False,
              passive: bool = False, derived: bool = False) -> str: ...
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

    def state(self, dev_id: str, dp_id: str, active: bool = False,
              passive: bool = False, derived: bool = False) -> str:
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

    def state(self, dev_id: str, dp_id: str, active: bool = False,
              passive: bool = False, derived: bool = False) -> str:
        # `derived` = a manager-rendered derived DP ({type}=derived), e.g. a cover
        # state folded from raw DPs by a code converter. The other types are
        # rustuya-bridge §Events: `active` = device push (Tuya cmd 8, wrapped
        # `data.dps`); `passive` = query response / periodic report (cmd 16, root
        # `dps`); `state` = retained full snapshot, emitted ONLY in cache mode
        # (mqtt_retain=true), recoverable on reconnect.
        #
        #   - `event` entities + delta DPs pass active=/passive= explicitly and
        #     keep that single stream (a delta read from both would double-count).
        #   - stateful (absolute) entities: in cache mode read the retained
        #     `state` snapshot; in pass-through there's NO snapshot and the bridge
        #     sends each delta to active OR passive depending on the device, so we
        #     subscribe with a wildcard on the {type} level to catch both. When
        #     the event topic has no {type} (the types collide on one topic, or
        #     {type} rides in the payload) `type="+"` renders to the plain topic —
        #     and the pass-through value_template emits no type filter, so the
        #     value is read regardless. (Absolute values are idempotent, so seeing
        #     both an active and a passive copy is harmless: last write wins.)
        if derived:
            mtype = "derived"
        elif active:
            mtype = "active"
        elif passive:
            mtype = "passive"
        elif self.config.retain:
            mtype = "state"
        else:
            mtype = "+"  # pass-through: wildcard the {type} level (catch active|passive)
        return render_topic(self.config.event_topic,
                            root=self.config.root, id=dev_id, dp=str(dp_id),
                            type=mtype)

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
                       passive_only: bool = False,
                       transform: Optional[str] = None) -> str: ...
    def availability_template(self) -> str: ...
    def command_template(self, dev_id: str, dp_id: str, kind: str = "raw",
                         scale: int = 0, val_map: Optional[Dict[str, Any]] = None,
                         transform: Optional[str] = None) -> Optional[str]: ...


class DefaultPayloadCodec:
    """Templates for the historical payload shape. Legacy ran with retain=true and
    a {type}-carrying payload, so stateful entities read the `state` snapshot only."""

    def availability_template(self) -> str:
        return build_availability_template()

    def value_template(self, comp: str, scale: int = 0,
                       val_map: Optional[Dict[str, Any]] = None,
                       dp_id: Optional[str] = None,
                       active_only: bool = False,
                       passive_only: bool = False,
                       transform: Optional[str] = None) -> str:
        return build_value_template(comp, scale, val_map,
                                    skip_active=not (active_only or passive_only),
                                    active_only=active_only, passive_only=passive_only,
                                    transform=transform)

    def command_template(self, dev_id: str, dp_id: str, kind: str = "raw",
                         scale: int = 0, val_map: Optional[Dict[str, Any]] = None,
                         transform: Optional[str] = None) -> Optional[str]:
        # Legacy/default layout puts the DP in the command topic, so HA writes a
        # bare value and no template is needed (preserves the golden output).
        return None


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
                       passive_only: bool = False,
                       transform: Optional[str] = None) -> str:
        index = None if self._per_dp else (str(dp_id) if dp_id is not None else None)
        value_expr = jinja_accessor("value_json", self._value_path, index=index)
        return build_value_template(comp, scale, val_map, value_expr, self._type_expr,
                                    skip_active=(self._skip_active and not (active_only or passive_only)),
                                    active_only=active_only, passive_only=passive_only,
                                    transform=transform)

    def command_template(self, dev_id: str, dp_id: str, kind: str = "raw",
                         scale: int = 0, val_map: Optional[Dict[str, Any]] = None,
                         transform: Optional[str] = None) -> Optional[str]:
        """Build a Home Assistant ``command_template`` that emits the bridge's
        ``set`` request JSON, carrying whatever the command topic does NOT.

        Returns None when the command topic already encodes the DP
        (``command_per_dp``): a bare value works there, so HA needs no template
        (and the historical output is preserved). Otherwise the device id and DP
        live only in the payload, so we emit e.g.::

            {"action": "set", "id": "<dev>", "dps": {"7": {{ (value|float)|int }}}}

        ``id``/``action`` are included only when the topic doesn't already carry
        them (``{id}`` / ``{action}``)."""
        if self.config.command_per_dp:
            return None
        parts = []
        if not self.config.command_has_action:
            parts.append('"action": "set"')
        if not self.config.command_has_id:
            parts.append('"id": %s' % json.dumps(str(dev_id)))
        value_expr = build_command_value_expr(kind, scale, val_map, transform)
        parts.append('"dps": {%s: %s}' % (json.dumps(str(dp_id)), value_expr))
        return "{%s}" % ", ".join(parts)


def scheme_for(config: BridgeConfig):
    """Convenience: (TopicScheme, PayloadCodec) pair for a bridge config."""
    return BridgeTopicScheme(config), BridgePayloadCodec(config)
