"""rustuya-homeassistant: generate Home Assistant MQTT Discovery payloads
for Tuya devices bridged by rustuya-bridge.

Public library API — import these to drive generation from a web UI or other
front-end without going through the CLI::

    from rustuya_ha import DiscoveryGenerator, initialize_generator

The CLI lives in ``rustuya_ha.cli`` and is a thin wrapper over this core.
"""
from .core.generator import DiscoveryGenerator, initialize_generator
from .core.converter import UserConverter
from .core.scheme import (
    TopicScheme, PayloadCodec, DefaultTopicScheme, DefaultPayloadCodec,
    DEFAULT_MANUFACTURER,
)

__all__ = [
    "DiscoveryGenerator",
    "initialize_generator",
    "UserConverter",
    "TopicScheme",
    "PayloadCodec",
    "DefaultTopicScheme",
    "DefaultPayloadCodec",
    "DEFAULT_MANUFACTURER",
]
