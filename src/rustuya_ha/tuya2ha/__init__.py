"""tuya2ha: generic, sans-I/O Tuya-DP -> Home Assistant conversion.

Two stages, cleanly separated:

1. **Classify** (``classify.classify_device``) — a Tuya Cloud device dict
   (``local_strategy``/``mapping``/``function``) + DP overrides -> a list of
   transport-agnostic entity descriptors (``EntityIL``/``DpRole``, see
   ``il.py``): "this DP is a number sensor, °C, scale 1" / "this DP pair is a
   cover's position." No topics, no templates, no MQTT, no network, no disk.

2. **Render** — that IL -> whatever the consumer needs. This package ships
   the MQTT-discovery renderer (``render_mqtt.render_mqtt``, HA's MQTT
   discovery ``{topic: payload_dict}`` shape); a native HA integration
   component would write a different renderer over the same IL and never
   import ``render_mqtt`` at all.

``DiscoveryGenerator`` wires classify + the MQTT renderer together as the
stable, simple entry point::

    from rustuya_ha.tuya2ha import DiscoveryGenerator

    gen = DiscoveryGenerator()  # DefaultTopicScheme + DefaultPayloadCodec
    entities, source = gen.generate(tuya_device_dict)

Bring your own topic layout / payload shape by implementing
``scheme.TopicScheme`` / ``scheme.PayloadCodec`` (e.g. ``rustuya_ha.core.scheme``'s
``BridgeTopicScheme``/``BridgePayloadCodec``, derived from a rustuya-bridge
config); bring your own DP overrides by implementing ``converter.Converter``
(``find(product_id) -> dict | None``) or using ``converter.DictConverter``
over an already-loaded mapping.
"""
from .classify import classify_device
from .converter import Converter, DictConverter, NoConverter, deep_merge, merge_all
from .generator import DiscoveryGenerator
from .il import DpRole, EntityIL
from .render_mqtt import render_mqtt
from .scheme import (
    TopicScheme, PayloadCodec, DefaultTopicScheme, DefaultPayloadCodec,
    DEFAULT_MANUFACTURER,
)

__all__ = [
    "DiscoveryGenerator",
    "classify_device",
    "render_mqtt",
    "EntityIL",
    "DpRole",
    "Converter",
    "DictConverter",
    "NoConverter",
    "deep_merge",
    "merge_all",
    "TopicScheme",
    "PayloadCodec",
    "DefaultTopicScheme",
    "DefaultPayloadCodec",
    "DEFAULT_MANUFACTURER",
]
