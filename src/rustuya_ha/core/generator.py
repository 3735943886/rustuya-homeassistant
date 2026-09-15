"""Factory wiring the generic tuya2ha generator to this project's file-based
converter loader — the CLI/manager-plugin entry point.

``DiscoveryGenerator`` itself (the DP -> HA discovery conversion, sans I/O)
lives in ``rustuya_ha.tuya2ha.generator``; re-exported here for back-compat.
"""
from typing import Optional

from ..tuya2ha.generator import DiscoveryGenerator, Converter
from .converter import UserConverter

__all__ = ["DiscoveryGenerator", "Converter", "initialize_generator"]


def initialize_generator(custom_path: Optional[str] = None) -> DiscoveryGenerator:
    return DiscoveryGenerator(UserConverter(custom_path))
