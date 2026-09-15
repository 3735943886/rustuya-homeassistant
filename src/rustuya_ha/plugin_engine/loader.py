"""Discover and load drop-in ``*.py`` plugin files, each defining ``setup(api)``.

Fully generic: given a list of file paths and an already-built ``api`` object,
imports each in isolation (a broken file is logged and skipped, never taking
down the host) and calls its ``setup(api)``. No knowledge of what ``api`` is,
what directory the files came from, or what domain they operate in — that's
the caller's job (see ``rustuya_ha.manager_plugin.code_converters`` for the
Tuya/HA wiring: ``custom_converters/*.py`` + ``ConverterApi``).
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("plugin_engine")


def import_file(path: Path):
    name = f"plugin_engine_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_plugins(files: Iterable[Path], api: Any) -> int:
    """Import each file and call its ``setup(api)``. Returns the number
    successfully set up. Each file is isolated: an import error or an
    exception from ``setup()`` is logged and skipped, never raised."""
    loaded = 0
    for f in files:
        try:
            mod = import_file(f)
        except Exception:
            logger.exception("plugin %s failed to import", f.name)
            continue
        setup = getattr(mod, "setup", None)
        if not callable(setup):
            logger.warning("plugin %s has no setup(api); skipping", f.name)
            continue
        try:
            setup(api)
            loaded += 1
            logger.info("loaded plugin %s", f.name)
        except Exception:
            logger.exception("plugin %s setup(api) failed", f.name)
    return loaded
