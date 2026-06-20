"""Load user Python *code converters* from the `custom_converters/` directory.

A code converter is a `*.py` file dropped next to the JSON converters. It
defines `setup(api)` and uses `api` — a thin, friendly facade over the manager's
plugin runtime (api_version >= 2) — to react to decoded DPs and publish derived
DPs: the per-device logic that JSON can't express. The result *topic* is rendered
by the manager (`derived_dp`); a converter only supplies `(dp, value)`.

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

import importlib.util
import inspect
import logging
from pathlib import Path
from typing import Any, Optional

from ..core import converter as _conv

logger = logging.getLogger("code_converter")


def _as_async(handler):
    """Wrap a user handler `(device_id, dps, origin)` so both sync and async
    forms satisfy the runtime's async DpWatcher contract."""

    async def _w(device_id, dps, origin):
        r = handler(device_id, dps, origin)
        if inspect.isawaitable(r):
            await r

    return _w


def _as_async_dp(handler, dp):
    """Like `_as_async` but for the `on_dp` convenience: `(device_id, value)`."""

    async def _w(device_id, dps, origin):
        r = handler(device_id, dps.get(dp))
        if inspect.isawaitable(r):
            await r

    return _w


class ConverterApi:
    """The object handed to each code converter's `setup(api)` — a friendly
    facade over the manager's plugin-runtime ctx (api_version >= 2). Registration
    helpers are decorators; outputs delegate the topic to the manager."""

    def __init__(self, ctx: Any, mapping: dict) -> None:
        self._ctx = ctx
        self.converters = mapping  # the merged custom_converters mapping
        self.log = logging.getLogger("code_converter")

    # ── react to decoded DPs ─────────────────────────────────────────────
    def on_device(self, device_id: str):
        """Decorator: `handler(device_id, dps, origin)` for one device's events."""

        def deco(handler):
            self._ctx.watch_device(device_id, _as_async(handler))
            return handler

        return deco

    def on_dp(self, device_id: str, dp) -> Any:
        """Decorator: `handler(device_id, value)` when `device_id` emits `dp`."""
        dp = str(dp)

        def deco(handler):
            self._ctx.watch_dp(device_id, dp, _as_async_dp(handler, dp))
            return handler

        return deco

    def on_product(self, product_id: str):
        """Decorator: `handler(device_id, dps, origin)` for *every* device whose
        product_id matches — one converter serving a whole device model. The
        handler is called with each matching device's id, so keep per-device
        state keyed by `device_id` (a closure dict) rather than a single bag."""

        def deco(handler):
            h = _as_async(handler)

            async def _w(device_id, dps, origin):
                if self.product_id(device_id) == product_id:
                    await h(device_id, dps, origin)

            self._ctx.watch_dps(_w)
            return handler

        return deco

    def on_any(self, handler):
        """Register `handler(device_id, dps, origin)` for every device's events."""
        self._ctx.watch_dps(_as_async(handler))
        return handler

    # ── output (the manager renders the topic; we send only the value) ───
    async def derive(self, device_id: str, dp, value, *, retain: Optional[bool] = None) -> None:
        """Publish a derived DP value for `device_id`; the manager renders the
        topic + byte-faithful payload from the bridge templates."""
        await self._ctx.derived_dp(device_id, str(dp), retain=retain).set(value)

    async def clear(self, device_id: str, dp) -> None:
        """Clear a previously-published derived DP (empty retained)."""
        await self._ctx.derived_dp(device_id, str(dp)).clear()

    async def set_dp(self, device_id: str, dp, value) -> None:
        """Command a real device's DP (external → Tuya)."""
        await self._ctx.set_device_dp(device_id, str(dp), value)

    def service(self, coro_factory) -> None:
        """Register a long-lived, manager-supervised async daemon."""
        self._ctx.add_service(coro_factory)

    # ── data the logic can read ──────────────────────────────────────────
    def devices(self) -> dict:
        """The cloud devices snapshot `{id: raw_data}`."""
        return self._ctx.devices()

    def current_dps(self, device_id: Optional[str] = None) -> dict:
        """Snapshot of the manager's current decoded DP values (manager rc58+).

        `device_id=None` → `{device_id: {dp: value}}` for every device;
        a `device_id` → that device's `{dp: value}` (`{}` if unknown). Call it at
        setup to seed a combinator from the values already on hand — e.g. the
        position a curtain reported before this converter loaded — instead of
        waiting for each source DP's next change. Returns `{}` on hosts too old to
        expose it, so a converter degrades gracefully."""
        fn = getattr(self._ctx, "current_dps", None)
        if fn is None:
            return {}
        return fn(device_id)

    def product_id(self, device_id: str):
        """The `product_id` of a device (for looking up its converter)."""
        return (self._ctx.devices().get(device_id) or {}).get("product_id")

    def converter(self, product_id: Optional[str]):
        """The merged converter override for a product_id (or None)."""
        if not product_id:
            return None
        return self.converters.get(product_id)

    def bridge_config(self):
        """The bridge's (credential-redacted) raw config dict, or None."""
        return self._ctx.bridge_config()


def _import_file(path: Path):
    name = f"rustuya_cc_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_code_converters(ctx: Any, *, path: Optional[str] = None) -> int:
    """Discover and run every `*.py` code converter in the converters directory.

    Returns the number successfully set up. No-op (returns 0) if the host runtime
    is too old (api_version < 2) or there are no `*.py` files. Each file is
    imported and `setup(api)`-called in isolation — one bad file is logged and
    skipped, never breaking the others or the manager."""
    if getattr(ctx, "api_version", 1) < 2:
        logger.info("code converters need manager api_version >= 2; skipping")
        return 0
    files = _conv.py_files(path)
    if not files:
        return 0
    api = ConverterApi(ctx, _conv.load_converters(path))
    loaded = 0
    for f in files:
        try:
            mod = _import_file(f)
        except Exception:
            logger.exception("code converter %s failed to import", f.name)
            continue
        setup = getattr(mod, "setup", None)
        if not callable(setup):
            logger.warning("code converter %s has no setup(api); skipping", f.name)
            continue
        try:
            setup(api)
            loaded += 1
            logger.info("loaded code converter %s", f.name)
        except Exception:
            logger.exception("code converter %s setup(api) failed", f.name)
    return loaded
