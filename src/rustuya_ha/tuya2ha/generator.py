"""Generic Tuya-DP -> Home Assistant MQTT-discovery converter.

Pure and I/O-free, in two stages:

1. ``classify.classify_device`` — Tuya device dict + DP overrides -> a list of
   transport-agnostic entity descriptors (``il.EntityIL``): no topics, no
   templates, no MQTT.
2. ``render_mqtt.render_mqtt`` — that IL -> ``{topic: payload_dict}``, HA's
   MQTT discovery shape, via the injected ``scheme.TopicScheme``/
   ``scheme.PayloadCodec`` (default: a self-contained ``rustuya/``-prefixed
   layout).

``DiscoveryGenerator`` just wires the two together — kept as the stable public
entry point (``generate(device) -> (entities_dict, source_label)``). A
different consumer (e.g. a native HA integration component) can call
``classify_device`` directly and write its own renderer over the same IL,
skipping MQTT entirely.
"""
from typing import Any, Dict, Optional, Tuple

from .classify import classify_device
from .converter import Converter, NoConverter
from .render_mqtt import apply_overrides, render_mqtt
from .scheme import (
    TopicScheme, PayloadCodec, DefaultTopicScheme, DefaultPayloadCodec,
)

__all__ = ["DiscoveryGenerator", "Converter"]


# --- Discovery Generator ---

class DiscoveryGenerator:
    def __init__(self, converter: Optional[Converter] = None,
                 scheme: Optional[TopicScheme] = None,
                 codec: Optional[PayloadCodec] = None):
        self.converter: Converter = converter or NoConverter()
        # Topic layout + payload shape seams: the renderer routes every topic
        # build and every value_template through these. Defaults are a
        # concrete, self-contained scheme (see scheme.py); swap in your own to
        # target a different transport.
        self.scheme: TopicScheme = scheme or DefaultTopicScheme()
        self.codec: PayloadCodec = codec or DefaultPayloadCodec()

    def generate(self, device: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        entities, source_label, model, user_info = classify_device(device, self.converter)
        dev_id = device['id']
        name = device.get('name', dev_id)
        parent_id = device.get('parent')

        payloads = render_mqtt(dev_id, name, model, entities, self.scheme, self.codec,
                               parent_id=parent_id)

        if user_info:
            overrides = user_info.get('discovery_overrides')
            if overrides:
                apply_overrides(dev_id, payloads, overrides, self.scheme, self.codec)

        return payloads, source_label
