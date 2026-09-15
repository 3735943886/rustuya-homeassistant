"""Topic and payload seam for Tuya-DP-to-HA discovery generation.

This is the vendor-neutral half of the conversion: ``TopicScheme`` maps
(device, dp, component) tuples to MQTT topic strings, ``PayloadCodec`` builds
the Jinja ``value_template``/``command_template`` expressions that read/write a
device's state payload. ``DiscoveryGenerator`` (see ``generator.py``) routes
every topic build and every template through these two seams instead of
hardcoding a topic layout or payload shape, so it can be reused against any
MQTT transport.

``DefaultTopicScheme``/``DefaultPayloadCodec`` are a concrete, self-contained
implementation (a `rustuya/`-prefixed topic layout, `{"type":..., "value":...}`
payloads) usable out of the box or as a reference for writing your own -- swap
in a different pair to target a different transport/bridge without touching
``DiscoveryGenerator``.
"""
import json
from typing import Any, Dict, Optional, Protocol, runtime_checkable

DEFAULT_MANUFACTURER = "rustuya"

# Legacy Jinja accessors -- the payload shape DefaultPayloadCodec assumes.
LEGACY_VALUE_EXPR = "value_json.value"
LEGACY_TYPE_EXPR = "value_json.type"

# Transport error codes that should mark an HA entity as unavailable. This is
# DefaultPayloadCodec's own default (rustuya-bridge's errorCode taxonomy) --
# a PayloadCodec for a different transport can implement availability_template
# however fits that transport's error signaling.
UNAVAILABLE_ERROR_CODES = [901, 905, 914]


# --- shared template builders (parameterized by JSON accessor expressions) ---

def build_availability_template(unavailable_codes=UNAVAILABLE_ERROR_CODES) -> str:
    # The error/message topic payload carries errorCode regardless of the user's
    # event payload_template, so this is fixed.
    return (
        "{{ 'offline' if value_json is defined and value_json != None "
        "and value_json.errorCode in " + json.dumps(sorted(unavailable_codes)) +
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
    emitted -- the event topic itself is expected to separate the types, or it
    can't be told.

    The "no value" fallback renders ``''`` (empty string), not ``none``: HA
    *ignores* an empty payload (keeps the previous state) but resets the entity
    to ``unknown`` on a ``None``. This matters when several DPs share one
    state_topic (no ``{dp}`` in the event topic) and the transport sends partial
    deltas -- e.g. ``{"1": true}`` must not blank out the entity reading DP 2."""
    # When value_expr is the payload root itself (bare `{value}` payloads), the
    # "<expr> != None" clause duplicates "value_json != None" -- drop the tail.
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

    # In cache mode the transport emits no-retain `active`/`passive` deltas plus a
    # retained `state` snapshot. Pick which {type} this entity reads. Parens
    # needed -- `inner` is itself a conditional. (No {type} to tell them apart =>
    # no filter, fall through.)
    keep = ("active" if active_only else "passive" if passive_only
            else "state" if skip_active else None)
    #   active_only: incremental/delta DP (e.g. add_ele) reads the `active` delta,
    #     never the snapshot, which would re-add the same increment (double-count).
    #   passive_only: the `passive` companion of such a delta DP -- same no-retain
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
    """A concrete, self-contained topic layout: ``rustuya/`` state/command
    topics, standard HA discovery topics. Usable out of the box, or as a
    reference for writing your own ``TopicScheme``."""

    DISCOVERY = "homeassistant/{}/{}_{}/config"
    STATE = "rustuya/event/{}/{}"
    COMMAND = "rustuya/command/set/{}/{}"
    ERROR = "rustuya/error/{}"

    def discovery(self, component: str, dev_id: str, code: str) -> str:
        return self.DISCOVERY.format(component, dev_id, code)

    def state(self, dev_id: str, dp_id: str, active: bool = False,
              passive: bool = False, derived: bool = False) -> str:
        return self.STATE.format(dev_id, dp_id)  # this layout has no {type}

    def command(self, dev_id: str, dp_id: str) -> str:
        return self.COMMAND.format(dev_id, dp_id)

    def availability(self, dev_id: str) -> str:
        return self.ERROR.format(dev_id)


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
    """Templates for ``{"type": ..., "value": ...}`` payloads, published with
    retain=true (stateful entities read the `state` snapshot only). Usable out
    of the box, or as a reference for writing your own ``PayloadCodec``."""

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
        # This layout puts the DP in the command topic, so HA writes a bare
        # value and no template is needed.
        return None
