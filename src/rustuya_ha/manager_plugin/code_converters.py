"""Load user Python *code converters* from the `custom_converters/` directory.

A code converter is a `*.py` file dropped next to the JSON converters. It
defines `setup(api)` and uses `api` — a thin, friendly facade over the manager's
plugin runtime (api_version >= 2) — to react to decoded DPs and publish derived
DPs: the per-device logic that JSON can't express. The result *topic* is rendered
by the manager (`derived_dp`); a converter only supplies `(dp, value)`.

This module is the Tuya/HA-specific wiring onto the generic ``plugin_engine``
(dynamic import + isolated ``setup(api)`` loading, see
``rustuya_ha.plugin_engine``): it resolves the `custom_converters/` directory
convention and adds the one accessor the generic ``PluginApi`` doesn't have —
`api.converter(product_id)`, the merged JSON override for a product.

Loaded once at `register()` time, per-file isolated (a broken file is logged and
skipped, never taking down the manager). Editing a file takes effect on the next
manager restart (re-exec), like any in-process Python.

Example `custom_converters/00_curtain.py`::

    def setup(api):
        PRODUCT = "h2wipnagcunsar5r"
        by_device = {}

        @api.on_product(PRODUCT)               # every device of this model
        async def _(device_id, dps, origin):
            st = by_device.setdefault(device_id, dict(api.current_dps(device_id)))
            st.update(dps)
            state = ...                        # any per-device logic
            if state is not None:
                await api.derive(device_id, "99", state)   # topic = manager's job
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..core import converter as _conv
from ..plugin_engine.api import PluginApi
from ..plugin_engine.loader import load_plugins

logger = logging.getLogger("code_converter")


class ConverterApi(PluginApi):
    """`PluginApi` plus the one Tuya-specific accessor: a device's merged
    `custom_converters` JSON override, keyed by product_id."""

    def __init__(self, ctx: Any, mapping: dict) -> None:
        super().__init__(ctx)
        self.converters = mapping  # the merged custom_converters mapping
        self.log = logging.getLogger("code_converter")

    def converter(self, product_id: Optional[str]):
        """The merged converter override for a product_id (or None)."""
        if not product_id:
            return None
        return self.converters.get(product_id)


def load_code_converters(ctx: Any, *, path: Optional[str] = None) -> int:
    """Discover and run every `*.py` code converter in the converters directory.

    Returns the number successfully set up. No-op (returns 0) if the host runtime
    is too old (api_version < 2) or there are no `*.py` files."""
    if getattr(ctx, "api_version", 1) < 2:
        logger.info("code converters need manager api_version >= 2; skipping")
        return 0
    files = _conv.py_files(path)
    if not files:
        return 0
    api = ConverterApi(ctx, _conv.load_converters(path))
    return load_plugins(files, api)
