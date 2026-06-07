"""Backup / restore of the retained HA discovery state (CLI surface).

Persistence (save/load/listing/latest/rotate) and the pure ``restore_plan`` now
live in ``rustuya_ha.core.backup`` so the engine is shared with the manager
plugin (which depends only on ``rustuya_ha.core``). This module re-exports them
unchanged so existing CLI call sites (``backup.save``, ``backup.restore_plan``,
``backup.DEFAULT_DIR`` …) keep working byte-for-byte.
"""
from ..core.backup import (  # noqa: F401  (re-export)
    DEFAULT_DIR,
    KEEP,
    latest,
    listing,
    load,
    restore_plan,
    save,
)
