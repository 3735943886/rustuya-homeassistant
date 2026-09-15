"""Generic, sans-I/O DP-override merging + lookup.

A "converter" in this package's sense is per-``product_id`` DP metadata
overrides + discovery-payload overrides — see ``Converter``
(``find(product_id) -> dict | None``), the protocol ``classify.classify_device``
(and ``DiscoveryGenerator``) consumes. This module is pure data transformation:
given already-loaded override dicts (however you got them — files, a database,
an API response), merge them and look one up by product_id. No file I/O, no
directory conventions, no MQTT — reusable by an MQTT-discovery generator, a
native HA integration component, or anything else that needs "per-Tuya-product
DP overrides."

``rustuya_ha.core.converter.UserConverter`` is the concrete, I/O-carrying
implementation this project actually ships: it resolves a `custom_converters/`
drop-in directory, reads its `*.json` files, and merges them with
``deep_merge`` from here.
"""
from typing import Any, Dict, Iterable, Optional, Protocol, runtime_checkable


@runtime_checkable
class Converter(Protocol):
    """Source of per-product_id DP overrides (dp_meta + discovery_overrides).

    Anything with this one method works — a file-backed loader, a dict lookup,
    a database query. ``DictConverter`` (below) and
    ``rustuya_ha.core.converter.UserConverter`` both satisfy it."""

    def find(self, product_id: Optional[str]) -> Optional[Dict[str, Any]]: ...


class NoConverter:
    """The no-overrides default: every device is classified from its raw DPs
    alone, no product_id lookup, no I/O."""

    def find(self, product_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return None


def deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``src`` into ``dst`` (last writer wins on leaves);
    nested dicts combine rather than replace — so one product_id's override may
    be split across several sources. Mutates and returns ``dst``."""
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def merge_all(mappings: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Deep-merge a sequence of product_id -> override dicts into one, in
    order (a later mapping wins on a conflicting leaf)."""
    merged: Dict[str, Any] = {}
    for m in mappings:
        deep_merge(merged, m)
    return merged


class DictConverter:
    """A ``generator.Converter`` over an already-assembled mapping — no I/O.

    Use ``merge_all``/``deep_merge`` first if you have several override
    sources to combine. ``.mapping`` is public and swappable in place (e.g. to
    validate an unsaved edit against a fresh generator)."""

    def __init__(self, mapping: Optional[Dict[str, Any]] = None):
        self.mapping: Dict[str, Any] = mapping if mapping is not None else {}

    def find(self, product_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not product_id:
            return None
        return self.mapping.get(product_id)
