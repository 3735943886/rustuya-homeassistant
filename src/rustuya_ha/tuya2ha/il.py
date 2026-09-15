"""The intermediate representation (IL) between classification and rendering.

``classify.classify_device`` turns a Tuya device dict into a list of
``EntityIL`` — "this is an HA sensor entity, °C, reading DP 5, scaled by 10" —
with zero knowledge of topics, Jinja, or MQTT. A renderer (``render_mqtt.py``
for this package's MQTT-discovery output; a native HA integration component
would write its own) turns that into whatever the target actually needs.

Role names are HA's own entity-attribute vocabulary (target/current
temperature, hvac mode, position, percentage, brightness) rather than MQTT
field names, because that vocabulary is shared by native HA entities too —
the same IL can feed a completely different renderer.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DpRole:
    """One DP binding within an entity: which raw DP, and how to interpret its
    value. ``kind`` only matters for a writable role (how to encode the write);
    ``scale``/``val_map`` matter for both directions."""

    dp_id: str
    readable: bool = True
    writable: bool = False
    kind: str = "raw"  # "bool" | "number" | "enum" | "string" | "raw" -- write encoding
    scale: int = 0
    val_map: Optional[Dict[str, Any]] = None
    # "state": absolute value, read from a retained/full snapshot.
    # "active": an incremental/delta value (e.g. add_ele) or an event -- only
    # ever meaningful on the live push, never a snapshot replay.
    stream: str = "state"


@dataclass
class EntityIL:
    """One HA entity: its display/classification metadata plus the DP role(s)
    it reads/writes. ``grouped`` distinguishes a multi-role entity built by a
    dedicated classifier (cover/climate/fan/light — roles keyed by HA
    attribute name) from a single-DP ``individual`` entity (one "main" role) —
    needed because a single DP's fallback mapping can itself resolve to the
    "light"/"cover" component (e.g. a switch-only `switch_led` DP falls back to
    an individual on/off light, not a full brightness+switch+color_temp one)."""

    component: str
    key: str  # discovery/identity key: a fixed slug (motor/thermostat/fan/led) or a DP code
    grouped: bool = False
    name: Optional[str] = None
    device_class: Optional[str] = None
    unit: Optional[str] = None
    icon: Optional[str] = None
    options: Optional[List[str]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    force_update: bool = False  # a delta/incremental DP (see dp_mapping.ACTIVE_ONLY_CODES)
    roles: Dict[str, DpRole] = field(default_factory=dict)
