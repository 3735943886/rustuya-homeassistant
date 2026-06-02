"""Topic and payload seams for discovery generation.

These abstractions are the injection point for issue #2: today the generator
hardcodes one topic layout and one MQTT payload shape. By routing every topic
build and every Jinja ``value_template`` through a ``TopicScheme`` / ``PayloadCodec``,
a future ``BridgeTopicScheme`` / ``BridgePayloadCodec`` can be derived from a
rustuya-bridge config (``mqtt_event_topic`` / ``mqtt_command_topic`` /
``mqtt_payload_template``) using the ``pyrustuyabridge`` template helpers, and
injected into ``DiscoveryGenerator`` without touching the entity builders.

``DefaultTopicScheme`` and ``DefaultPayloadCodec`` reproduce the previous
hardcoded behaviour byte-for-byte, so the golden snapshots stay non-inferior.
"""
import json
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from .mapping import UNAVAILABLE_ERROR_CODES

DEFAULT_MANUFACTURER = "rustuya"


@runtime_checkable
class TopicScheme(Protocol):
    """Maps (device, dp, component) tuples to MQTT topic strings."""

    def discovery(self, component: str, dev_id: str, code: str) -> str: ...
    def state(self, dev_id: str, dp_id: str) -> str: ...
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

    def state(self, dev_id: str, dp_id: str) -> str:
        return self.STATE.format(dev_id, dp_id)

    def command(self, dev_id: str, dp_id: str) -> str:
        return self.COMMAND.format(dev_id, dp_id)

    def availability(self, dev_id: str) -> str:
        return self.ERROR.format(dev_id)


@runtime_checkable
class PayloadCodec(Protocol):
    """Produces Jinja templates that read a device's MQTT state payload."""

    def value_template(self, comp: str, scale: int = 0,
                       val_map: Optional[Dict[str, Any]] = None) -> str: ...
    def availability_template(self) -> str: ...


class DefaultPayloadCodec:
    """Templates for the historical ``{"value": ..., "type": ..., "errorCode": ...}``
    payload shape (unchanged output)."""

    def availability_template(self) -> str:
        # entity unavailable로 만드는 errorCode 목록은 mapping.UNAVAILABLE_ERROR_CODES에서 관리.
        return (
            "{{ 'offline' if value_json is defined and value_json != None "
            "and value_json.errorCode in " + json.dumps(sorted(UNAVAILABLE_ERROR_CODES)) +
            " else 'online' }}"
        )

    def value_template(self, comp: str, scale: int = 0,
                       val_map: Optional[Dict[str, Any]] = None) -> str:
        guard = "value_json is defined and value_json != None and value_json.value != None"
        if comp == "event":
            return "{{ { \"event_type\": value_json.value } | to_json if %s and value_json.type | default('') == 'active' else '' }}" % guard

        if comp in ["binary_sensor", "switch"] and not val_map:
            return "{{ 'true' if %s and value_json.value == true else 'false' if %s and value_json.value == false else 'unknown' }}" % (guard, guard)

        base = "value_json.value"
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

        return "{{ %s if %s else 'unknown' }}" % (final_expr, guard)
