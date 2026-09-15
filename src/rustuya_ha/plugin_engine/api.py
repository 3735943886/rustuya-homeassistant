"""Generic facade over a rustuya-manager plugin host (``ctx``), plus the
sync/async handler-wrapping helpers ``loader.py`` needs.

Everything here is duck-typed against the manager's documented plugin-runtime
contract (api_version >= 2: watch_device/watch_dp/watch_dps, derived_dp,
set_device_dp, add_service, devices, current_dps, bridge_config) — no import
from rustuya_manager, no Tuya/HA-specific knowledge. Any manager plugin that
wants a friendlier facade for "drop-in .py files reacting to DP changes" can
use ``PluginApi`` directly or subclass it to add domain-specific accessors
(rustuya-ha's ``ConverterApi``, in ``manager_plugin/code_converters.py``, adds
a product_id -> converter-override lookup on top).
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Optional


def as_async(handler):
    """Wrap a user handler `(device_id, dps, origin)` so both sync and async
    forms satisfy the runtime's async DpWatcher contract."""

    async def _w(device_id, dps, origin):
        r = handler(device_id, dps, origin)
        if inspect.isawaitable(r):
            await r

    return _w


def as_async_dp(handler, dp):
    """Like `as_async` but for the `on_dp` convenience: `(device_id, value)`."""

    async def _w(device_id, dps, origin):
        r = handler(device_id, dps.get(dp))
        if inspect.isawaitable(r):
            await r

    return _w


class PluginApi:
    """The object handed to each dropped-in `.py` file's `setup(api)` — a
    friendly facade over the manager's plugin-runtime ctx (api_version >= 2).
    Registration helpers are decorators; outputs delegate the topic to the
    manager."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self.log = logging.getLogger("plugin_engine")

    # -- react to decoded DPs ----------------------------------------------
    def on_device(self, device_id: str):
        """Decorator: `handler(device_id, dps, origin)` for one device's events."""

        def deco(handler):
            self._ctx.watch_device(device_id, as_async(handler))
            return handler

        return deco

    def on_dp(self, device_id: str, dp) -> Any:
        """Decorator: `handler(device_id, value)` when `device_id` emits `dp`."""
        dp = str(dp)

        def deco(handler):
            self._ctx.watch_dp(device_id, dp, as_async_dp(handler, dp))
            return handler

        return deco

    def on_product(self, product_id: str):
        """Decorator: `handler(device_id, dps, origin)` for *every* device whose
        product_id matches — one handler serving a whole device model. It is
        called with each matching device's id, so keep per-device state keyed
        by `device_id` (a closure dict) rather than a single bag."""

        def deco(handler):
            h = as_async(handler)

            async def _w(device_id, dps, origin):
                if self.product_id(device_id) == product_id:
                    await h(device_id, dps, origin)

            self._ctx.watch_dps(_w)
            return handler

        return deco

    def on_any(self, handler):
        """Register `handler(device_id, dps, origin)` for every device's events."""
        self._ctx.watch_dps(as_async(handler))
        return handler

    # -- output (the manager renders the topic; we send only the value) ----
    async def derive(self, device_id: str, dp, value, *, retain: Optional[bool] = None) -> None:
        """Publish a derived DP value for `device_id`; the manager renders the
        topic + byte-faithful payload from the bridge templates."""
        await self._ctx.derived_dp(device_id, str(dp), retain=retain).set(value)

    async def clear(self, device_id: str, dp) -> None:
        """Clear a previously-published derived DP (empty retained)."""
        await self._ctx.derived_dp(device_id, str(dp)).clear()

    async def set_dp(self, device_id: str, dp, value) -> None:
        """Command a real device's DP (external -> Tuya)."""
        await self._ctx.set_device_dp(device_id, str(dp), value)

    def service(self, coro_factory) -> None:
        """Register a long-lived, manager-supervised async daemon."""
        self._ctx.add_service(coro_factory)

    # -- data the logic can read --------------------------------------------
    def devices(self) -> dict:
        """The cloud devices snapshot `{id: raw_data}`."""
        return self._ctx.devices()

    def current_dps(self, device_id: Optional[str] = None) -> dict:
        """Snapshot of the manager's current decoded DP values (manager rc58+).

        `device_id=None` -> `{device_id: {dp: value}}` for every device;
        a `device_id` -> that device's `{dp: value}` (`{}` if unknown). Call it
        at setup to seed a combinator from the values already on hand — e.g.
        the position a curtain reported before this plugin loaded — instead of
        waiting for each source DP's next change. Returns `{}` on hosts too old
        to expose it, so a plugin degrades gracefully."""
        fn = getattr(self._ctx, "current_dps", None)
        if fn is None:
            return {}
        return fn(device_id)

    def product_id(self, device_id: str):
        """The `product_id` of a device."""
        return (self._ctx.devices().get(device_id) or {}).get("product_id")

    def bridge_config(self):
        """The bridge's (credential-redacted) raw config dict, or None."""
        return self._ctx.bridge_config()
