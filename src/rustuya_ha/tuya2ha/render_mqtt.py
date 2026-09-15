"""Render tuya2ha's IL entities into Home Assistant MQTT-discovery payloads.

The only consumer of ``TopicScheme``/``PayloadCodec`` in this package — takes
the transport-agnostic ``EntityIL`` list from ``classify.py`` and produces
``{discovery_topic: payload_dict}``, exactly HA's MQTT discovery shape. A
native HA integration component would write a different renderer over the
same IL and never touch this module.

``apply_overrides`` (the ``discovery_overrides`` JSON key, see
``rustuya_ha.core.converter``) lives here rather than in classification: it
patches the *generated MQTT payload's own field names* (state_topic,
value_template, payload_open, ...) — an MQTT-shape concept with no IL
equivalent, not a DP-semantics one.
"""
from typing import Any, Dict, List, Optional

from .il import DpRole, EntityIL
from .scheme import DEFAULT_MANUFACTURER, PayloadCodec, TopicScheme


def render_mqtt(dev_id: str, name: str, model: str, entities: List[EntityIL],
                scheme: TopicScheme, codec: PayloadCodec,
                parent_id: Optional[str] = None) -> Dict[str, Any]:
    """Render every entity to its HA MQTT discovery payload, keyed by
    discovery topic. Also emits the `_passive` companion sensor for delta DPs
    (see the individual-entity docs in classify.py)."""
    results: Dict[str, Any] = {}
    common_device = {"identifiers": [dev_id], "name": name,
                      "manufacturer": DEFAULT_MANUFACTURER, "model": model}
    avail_id = parent_id or dev_id
    avail = {
        "availability_topic": scheme.availability(avail_id),
        "availability_template": codec.availability_template(),
        "payload_available": "online", "payload_not_available": "offline",
    }

    for entity in entities:
        payload = _render_entity(dev_id, entity, scheme, codec, common_device, avail)
        results[scheme.discovery(entity.component, dev_id, entity.key)] = payload

        # Delta DP `passive` companion: some devices report the increment on
        # the `passive` stream (readback) rather than `active`. Emit a second
        # sensor reading `passive` so those devices still get a value. Same
        # no-retain delta semantics (no reconnect replay), distinct
        # unique_id/entity_id via the `_passive` suffix. (Not for events.)
        if entity.force_update and not entity.grouped and entity.component != "event":
            role = entity.roles["main"]
            companion = dict(payload)
            companion["unique_id"] = f"{dev_id}_{entity.key}_passive"
            companion["name"] = f"{payload['name']} passive"
            companion["state_topic"] = scheme.state(dev_id, role.dp_id, passive=True)
            companion["value_template"] = codec.value_template(
                entity.component, role.scale, role.val_map, dp_id=role.dp_id, passive_only=True)
            results[scheme.discovery(entity.component, dev_id, f"{entity.key}_passive")] = companion

    return results


def _cmd(scheme: TopicScheme, codec: PayloadCodec, dev_id: str, role: DpRole,
         topic_field: str) -> Dict[str, Any]:
    """Build a command topic and, when the bridge needs it, the matching
    ``*_command_template`` — returned as a dict to splat into a payload."""
    out = {topic_field: scheme.command(dev_id, role.dp_id)}
    tmpl = codec.command_template(dev_id, role.dp_id, kind=role.kind,
                                  scale=role.scale, val_map=role.val_map)
    if tmpl is not None:
        out[topic_field.replace("_topic", "_template")] = tmpl
    return out


def _render_entity(dev_id, entity: EntityIL, scheme, codec, common_device, avail) -> Dict[str, Any]:
    if entity.grouped:
        renderer = {"cover": _render_cover, "climate": _render_climate,
                   "fan": _render_fan, "light": _render_light}[entity.component]
        return renderer(dev_id, entity, scheme, codec, common_device, avail)
    return _render_individual(dev_id, entity, scheme, codec, common_device, avail)


def _render_cover(dev_id, entity, scheme, codec, common_device, avail):
    payload = {"name": None, "unique_id": f"{dev_id}_cover", "device": common_device, **avail}
    if entity.device_class:
        payload["device_class"] = entity.device_class
    if "command" in entity.roles:
        r = entity.roles["command"]
        payload.update(_cmd(scheme, codec, dev_id, r, "command_topic"))
    if "state" in entity.roles:
        r = entity.roles["state"]
        payload["state_topic"] = scheme.state(dev_id, r.dp_id)
        payload["value_template"] = codec.value_template("cover", dp_id=r.dp_id)
    if "set_position" in entity.roles:
        r = entity.roles["set_position"]
        payload.update(_cmd(scheme, codec, dev_id, r, "set_position_topic"))
    if "position" in entity.roles:
        r = entity.roles["position"]
        payload["position_topic"] = scheme.state(dev_id, r.dp_id)
        payload["position_template"] = codec.value_template("cover", dp_id=r.dp_id)
    return payload


def _render_climate(dev_id, entity, scheme, codec, common_device, avail):
    payload = {"name": None, "unique_id": f"{dev_id}_climate", "device": common_device, **avail}

    r = entity.roles["temperature"]
    payload.update(_cmd(scheme, codec, dev_id, r, "temperature_command_topic"))
    payload["temperature_state_topic"] = scheme.state(dev_id, r.dp_id)
    payload["temperature_state_template"] = codec.value_template("climate", r.scale, dp_id=r.dp_id)

    r = entity.roles["current_temperature"]
    payload["current_temperature_topic"] = scheme.state(dev_id, r.dp_id)
    payload["current_temperature_template"] = codec.value_template("climate", r.scale, dp_id=r.dp_id)

    if "mode" in entity.roles:
        r = entity.roles["mode"]
        payload.update(_cmd(scheme, codec, dev_id, r, "mode_command_topic"))
        payload["mode_state_topic"] = scheme.state(dev_id, r.dp_id)
        payload["mode_state_template"] = codec.value_template("climate", dp_id=r.dp_id)

    if "power" in entity.roles:
        r = entity.roles["power"]
        # HA climate has no `power_command_template`, so on a flat command
        # topic (id/dp only encodable in the payload) power can't be routed —
        # emit power_command_topic only when the topic carries the DP itself
        # (per-dp layout). Otherwise skip it.
        if codec.command_template(dev_id, r.dp_id, kind="bool") is None:
            payload["power_command_topic"] = scheme.command(dev_id, r.dp_id)

    return payload


def _render_fan(dev_id, entity, scheme, codec, common_device, avail):
    payload = {"name": None, "unique_id": f"{dev_id}_fan", "device": common_device, **avail}

    r = entity.roles["percentage"]
    payload.update(_cmd(scheme, codec, dev_id, r, "percentage_command_topic"))
    payload["percentage_state_topic"] = scheme.state(dev_id, r.dp_id)
    payload["percentage_value_template"] = codec.value_template("fan", dp_id=r.dp_id)

    if "switch" in entity.roles:
        r = entity.roles["switch"]
        payload.update(_cmd(scheme, codec, dev_id, r, "command_topic"))
        payload["state_topic"] = scheme.state(dev_id, r.dp_id)
        payload["state_value_template"] = codec.value_template("fan", dp_id=r.dp_id)
        payload["payload_on"] = "true"
        payload["payload_off"] = "false"

    if "oscillation" in entity.roles:
        r = entity.roles["oscillation"]
        payload.update(_cmd(scheme, codec, dev_id, r, "oscillation_command_topic"))
        payload["oscillation_state_topic"] = scheme.state(dev_id, r.dp_id)
        payload["oscillation_value_template"] = codec.value_template("fan", dp_id=r.dp_id)
        payload["payload_on"] = "true"
        payload["payload_off"] = "false"

    return payload


def _render_light(dev_id, entity, scheme, codec, common_device, avail):
    payload = {"name": None, "unique_id": f"{dev_id}_light", "device": common_device, **avail}

    r = entity.roles["brightness"]
    payload.update(_cmd(scheme, codec, dev_id, r, "brightness_command_topic"))
    payload["brightness_state_topic"] = scheme.state(dev_id, r.dp_id)
    payload["brightness_value_template"] = codec.value_template("light", dp_id=r.dp_id)

    if "switch" in entity.roles:
        r = entity.roles["switch"]
        payload.update(_cmd(scheme, codec, dev_id, r, "command_topic"))
        payload["state_topic"] = scheme.state(dev_id, r.dp_id)
        payload["payload_on"] = "true"
        payload["payload_off"] = "false"
        payload["state_value_template"] = codec.value_template("light", dp_id=r.dp_id)

    if "color_temp" in entity.roles:
        r = entity.roles["color_temp"]
        payload.update(_cmd(scheme, codec, dev_id, r, "color_temp_command_topic"))
        payload["color_temp_state_topic"] = scheme.state(dev_id, r.dp_id)
        payload["color_temp_value_template"] = codec.value_template("light", dp_id=r.dp_id)

    return payload


def _render_individual(dev_id, entity: EntityIL, scheme, codec, common_device, avail):
    r = entity.roles["main"]
    comp = entity.component
    is_active = r.stream == "active"
    payload = {
        "name": entity.name, "unique_id": f"{dev_id}_{entity.key}",
        "state_topic": scheme.state(dev_id, r.dp_id, active=is_active),
        "device": common_device, **avail,
    }
    if entity.device_class: payload["device_class"] = entity.device_class
    if entity.unit: payload["unit_of_measurement"] = entity.unit
    if entity.icon: payload["icon"] = entity.icon

    # JSON Payload & Scaling & Mapping
    payload["value_template"] = codec.value_template(
        comp, r.scale, r.val_map, dp_id=r.dp_id, active_only=entity.force_update)

    if entity.force_update:
        payload["force_update"] = True

    if comp == "number":
        for k in ("min", "max", "step"):
            v = getattr(entity, k)
            if v is not None:
                payload[k] = v
    elif comp == "select":
        if entity.options:
            payload["options"] = entity.options
    elif comp == "event":
        if entity.options:
            payload["event_types"] = entity.options

    if comp in ("binary_sensor", "switch"):
        payload["payload_on"] = "true"
        payload["payload_off"] = "false"
    if comp not in ("sensor", "binary_sensor", "event"):
        payload.update(_cmd(scheme, codec, dev_id, r, "command_topic"))

    return payload


# --- discovery payload overrides (post-render) ---

# Topic-role override keys: "<role>_dp" picks a DP and resolves it through the
# active scheme, so the emitted topic stays correct per-device (a literal
# topic string would otherwise hardcode one device's id).
#
# Read roles also get their value_template rebuilt through the codec (adapts
# to the bridge payload shape, not hardcoded `value_json.value`) and a
# "<role>_stream" key choosing the active/passive stream. Touching ANY of a
# read role's knobs (dp / stream / — for position — invert) rebuilds that
# role even if its dp isn't overridden, reusing the builder's dp.
#   (dp_key, stream_key, topic_field, template_field, invertable)
_READ_ROLES = [
    ("state_dp", "state_stream", "state_topic", "value_template", False),
    ("position_dp", "position_stream", "position_topic", "position_template", True),
]
# Command roles are write-only: a DP -> topic, no template, no stream.
_CMD_ROLES = [
    ("command_dp", "command_topic"),
    ("set_position_dp", "set_position_topic"),
]
# Reserved keys consumed by the override logic — never merged verbatim.
_OVERRIDE_RESERVED = (
    {r[0] for r in _READ_ROLES} | {r[1] for r in _READ_ROLES}
    | {c[0] for c in _CMD_ROLES}
    | {"invert_position", "invert_set_position", "remove", "component"}
)


def _topic_dp(topic: str) -> str:
    """The DP a builder-emitted topic addresses — every scheme puts {dp} last."""
    return topic.rsplit("/", 1)[-1]


def _override_cmd(scheme, codec, dev_id: str, dp_id, topic_field: str, kind: str = "raw") -> Dict[str, Any]:
    dp_id = str(dp_id)
    out = {topic_field: scheme.command(dev_id, dp_id)}
    tmpl = codec.command_template(dev_id, dp_id, kind=kind)
    if tmpl is not None:
        out[topic_field.replace("_topic", "_template")] = tmpl
    return out


def apply_overrides(dev_id: str, entities: Dict[str, Any], overrides: Dict[str, Any],
                    scheme: TopicScheme, codec: PayloadCodec) -> None:
    """Merge user ``discovery_overrides`` into generated component payloads
    (mutates ``entities`` in place). See ``rustuya_ha.core.converter``'s
    module docstring for the full key reference.

    Keyed by component type ("cover", "climate", ...); applies to every entity
    of that component on the device (single-entity components like cover/
    climate/fan/light are the intended target; for per-DP tweaks on sensors/
    switches use ``dp_meta`` instead)."""
    for topic, payload in entities.items():
        comp = topic.split("/")[-3]
        block = overrides.get(comp)
        if not block:
            continue
        invert_pos = bool(block.get("invert_position"))

        for dp_key, stream_key, topic_field, tmpl_field, invertable in _READ_ROLES:
            if dp_key in block and block[dp_key] is None:
                # explicit null -> drop the whole read role (topic + template),
                # e.g. an optimistic, state-less cover reporting only position.
                payload.pop(topic_field, None)
                payload.pop(tmpl_field, None)
                continue
            has_dp, stream = dp_key in block, block.get(stream_key)
            invert = invertable and invert_pos
            if not (has_dp or stream is not None or invert):
                continue  # role untouched
            # Resolve the DP: explicit override, else reuse the builder's.
            if has_dp:
                dp = str(block[dp_key])
            elif payload.get(topic_field):
                dp = _topic_dp(payload[topic_field])
            else:
                continue  # role not present on this entity
            active = stream == "active"
            derived = stream == "derived"
            # Re-emit the topic only when the dp or the stream was actually
            # chosen (a pure invert leaves the builder's topic). `derived`
            # points the read at a code converter's {type}=derived DP.
            if has_dp or stream is not None:
                payload[topic_field] = scheme.state(dev_id, dp, active=active, derived=derived)
            payload[tmpl_field] = codec.value_template(
                comp, dp_id=dp, active_only=active,
                transform="100 - ((%s) | int)" if invert else None)

        for dp_key, topic_field in _CMD_ROLES:
            if dp_key not in block:
                continue
            tmpl_field = topic_field.replace("_topic", "_template")
            if block[dp_key] is None:  # explicit null -> drop the command topic
                payload.pop(topic_field, None)
                payload.pop(tmpl_field, None)
            else:
                kind = "number" if topic_field == "set_position_topic" else "raw"
                payload.update(_override_cmd(scheme, codec, dev_id, str(block[dp_key]), topic_field, kind=kind))
        if block.get("invert_set_position"):
            # Invert the write direction (HA position -> device). Route through
            # the command codec so the flat-topic JSON wrapper (action/id/dps)
            # is preserved; falls back to the bare raw-int form on a per-dp
            # topic (where no template is emitted).
            pdp = (_topic_dp(payload["set_position_topic"])
                  if payload.get("set_position_topic") else None)
            tmpl = (codec.command_template(dev_id, pdp, kind="number", transform="100 - %s")
                   if pdp is not None else None)
            payload["set_position_template"] = tmpl or "{{ 100 - value | int }}"

        for key, val in block.items():
            if key not in _OVERRIDE_RESERVED:
                payload[key] = val

        # Drop arbitrary keys last, so removal always wins over the merges above.
        for field in block.get("remove", ()):
            payload.pop(field, None)
