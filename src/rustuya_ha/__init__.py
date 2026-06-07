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
    BridgeTopicScheme, BridgePayloadCodec, scheme_for,
    DEFAULT_MANUFACTURER,
)
from .core.bridge import BridgeConfig, LEGACY as LEGACY_BRIDGE_CONFIG
from .core.verifier import (
    DiscoveryVerifier,
    filter_matches_by_categories,
    filter_status_results,
    CATEGORY_ALIASES,
)
from .core.restore import restore_plan
from .core.plan import publish_plan, clear_plan, owned_topics

__all__ = [
    "DiscoveryGenerator",
    "initialize_generator",
    "UserConverter",
    "TopicScheme",
    "PayloadCodec",
    "DefaultTopicScheme",
    "DefaultPayloadCodec",
    "BridgeTopicScheme",
    "BridgePayloadCodec",
    "scheme_for",
    "BridgeConfig",
    "LEGACY_BRIDGE_CONFIG",
    "DEFAULT_MANUFACTURER",
    "DiscoveryVerifier",
    "filter_matches_by_categories",
    "filter_status_results",
    "CATEGORY_ALIASES",
    "restore_plan",
    "publish_plan",
    "clear_plan",
    "owned_topics",
]
