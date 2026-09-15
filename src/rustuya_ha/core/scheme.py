"""rustuya-bridge adapter for the generic tuya2ha discovery scheme.

The vendor-neutral ``TopicScheme``/``PayloadCodec`` Protocols and their
``Default*`` implementation live in ``rustuya_ha.tuya2ha.scheme`` (re-exported
here for convenience/back-compat). This module adds the one transport-specific
piece: deriving a ``TopicScheme``/``PayloadCodec`` pair from a *rustuya-bridge*
``BridgeConfig`` (its resolved topic/payload templates), fed via
``bridge.py``'s pure template-rendering helpers.

- ``DefaultTopicScheme``/``DefaultPayloadCodec`` (from tuya2ha) reproduce the
  previous hardcoded behaviour byte-for-byte (golden baseline).
- ``BridgeTopicScheme``/``BridgePayloadCodec`` (this module) derive the layout
  from a ``BridgeConfig``. Fed the legacy profile (``bridge.LEGACY``) they
  produce output identical to the Default ones — that parity is the proof the
  derive logic is correct (see tests).
"""
import json
from typing import Any, Dict, Optional

from ..tuya2ha.scheme import (
    TopicScheme, PayloadCodec, DefaultTopicScheme, DefaultPayloadCodec,
    DEFAULT_MANUFACTURER, LEGACY_VALUE_EXPR, LEGACY_TYPE_EXPR,
    build_availability_template, build_value_template, build_command_value_expr,
)
from .bridge import (
    BridgeConfig, render_topic, jinja_accessor,
    HA_DISCOVERY_TOPIC, COMMAND_ACTION, ERROR_LEVEL,
)

__all__ = [
    "TopicScheme", "PayloadCodec", "DefaultTopicScheme", "DefaultPayloadCodec",
    "DEFAULT_MANUFACTURER", "LEGACY_VALUE_EXPR", "LEGACY_TYPE_EXPR",
    "build_availability_template", "build_value_template", "build_command_value_expr",
    "BridgeTopicScheme", "BridgePayloadCodec", "scheme_for",
]


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
