"""Pure restore-plan logic for retained HA discovery state.

The full discovery state lives entirely in the retained
``homeassistant/.../config`` MQTT topics, so an "undo" for publish/clear is just:
re-publish a saved snapshot (retained) and clear any topics added since. This
module holds only the pure plan builder — the file persistence of backups
(serialize/rotate/list) lives in the CLI shell at ``rustuya_ha.cli.backup``.
"""
import json
from typing import Dict, List, Tuple


def restore_plan(snapshot_topics: Dict[str, dict],
                 current_topics) -> Tuple[List[dict], List[dict]]:
    """Pure: messages to fully revert retained discovery to ``snapshot_topics``.

    - set: re-publish every saved topic (retained) — overwrites modified ones too.
    - clear: empty-publish topics present now but absent from the snapshot (the
      additions). Together these make the live state match the snapshot exactly."""
    set_msgs = [{"topic": t, "payload": json.dumps(p), "retain": True}
                for t, p in snapshot_topics.items()]
    clear = sorted(set(current_topics) - set(snapshot_topics))
    clear_msgs = [{"topic": t, "payload": "", "retain": True} for t in clear]
    return set_msgs, clear_msgs
