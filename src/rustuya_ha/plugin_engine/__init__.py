"""plugin_engine: generic drop-in ``.py`` plugin loader for a rustuya-manager
plugin host.

Two pieces, both free of any Tuya/HA-discovery knowledge:

- ``PluginApi`` — a friendly facade over the manager's plugin-runtime ``ctx``
  (api_version >= 2): decorators to react to device/DP events, helpers to
  publish derived DPs or command a device, and read-only accessors for the
  device snapshot / bridge config. Duck-typed against the host contract, no
  import from rustuya_manager.
- ``load_plugins`` — imports a list of ``*.py`` files and calls each one's
  ``setup(api)``, isolated (a broken file is logged and skipped, never taking
  down the host).

Any rustuya-manager plugin wanting the "drop-in .py files reacting to DP
changes" pattern can reuse this directly. rustuya-ha's own wiring — the
``custom_converters/`` directory convention and the product_id-keyed JSON
override lookup — lives in ``manager_plugin.code_converters.ConverterApi``,
a thin ``PluginApi`` subclass, not here.
"""
from .api import PluginApi
from .loader import load_plugins

__all__ = ["PluginApi", "load_plugins"]
