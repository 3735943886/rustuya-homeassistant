import json
import re
import logging
from typing import Dict, Any, Optional, Set, Tuple
from .mapping import (
    UNIT_NORM_MAP, DEFAULT_UNITS_BY_CLASS,
    DP_CODE_MAP, GENERIC_MAP, PROPERTY_MAP, CATEGORY_MAP, COMPLEX_SIGNATURES,
    ACTIVE_ONLY_CODES,
)
from .converter import UserConverter
from .scheme import (
    TopicScheme, PayloadCodec, DefaultTopicScheme, DefaultPayloadCodec,
    DEFAULT_MANUFACTURER,
)

logger = logging.getLogger("tuya_discovery")


# --- Discovery Generator ---

class DiscoveryGenerator:
    def __init__(self, converter: Optional[UserConverter] = None,
                 scheme: Optional[TopicScheme] = None,
                 codec: Optional[PayloadCodec] = None):
        self.converter = converter or UserConverter()
        # Topic layout + payload shape seams (#2 injection point). Defaults
        # reproduce the historical hardcoded behaviour byte-for-byte.
        self.scheme: TopicScheme = scheme or DefaultTopicScheme()
        self.codec: PayloadCodec = codec or DefaultPayloadCodec()

    # --- Public ---

    def generate(self, device: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        device_id, product_id = device['id'], device.get('product_id')
        category, name = device.get('category', 'unknown'), device.get('name', device_id)
        product_name = device.get('product_name', category)

        # 1. Normalize DPs
        normalized_dps = self._normalize_dps(device)

        # 2. Apply user-defined overrides (if product_id matches)
        info = {"vendor": "Tuya", "model": product_name}
        source_label = "generic"
        user_info = self.converter.find(product_id)
        if user_info:
            source_label = "custom"
            info["model"] = user_info.get('model') or product_name
            for dp_id, meta in user_info.get('dp_meta', {}).items():
                self._merge_user_dp(normalized_dps, str(dp_id), meta)

        # 3. Build Entities
        parent_id = device.get('parent')
        entities = self._build_entities(device_id, name, category, normalized_dps, info, device.get('function', {}), parent_id=parent_id)

        # 4. Apply user-defined discovery payload overrides (component-level)
        if user_info:
            overrides = user_info.get('discovery_overrides')
            if overrides:
                self._apply_overrides(device_id, entities, overrides)

        return entities, source_label

    # --- Discovery payload overrides ---

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

    @staticmethod
    def _topic_dp(topic: str) -> str:
        """The DP a builder-emitted topic addresses — every scheme puts {dp} last."""
        return topic.rsplit("/", 1)[-1]

    def _apply_overrides(self, dev_id: str, entities: Dict[str, Any], overrides: Dict[str, Any]):
        """Merge user ``discovery_overrides`` into generated component payloads.

        Keyed by component type ("cover", "climate", ...); applies to every entity
        of that component on the device (single-entity components like cover/
        climate/fan/light are the intended target; for per-DP tweaks on sensors/
        switches use ``dp_meta`` instead).

        Two kinds of keys:
        - structured — read roles (state, position) take `<role>_dp` (DP, resolved
          to a topic via the scheme) and `<role>_stream` ("active"|"passive",
          default "passive": passive reads the retained snapshot, active keeps only
          the delta). Either rebuilds the role's value_template through the codec
          (payload-shape adaptation kept, not hardcoded). `invert_position: true`
          inverts the read direction (position_template emits `100 - value`);
          `invert_set_position: true` independently inverts the write direction
          (`set_position_template` of `100 - value`) — separate because a device
          can report and accept position on different conventions. Read-role knobs
          work without `<role>_dp`: the builder's own DP is reused. Command roles
          (command, set_position) take only `<role>_dp`.
        - removal — `<role>_dp: null` drops that whole role (its topic, and for a
          read role its template), e.g. a state-less optimistic cover that reports
          only position. `"remove": [field, ...]` drops arbitrary payload keys.
        - verbatim — every other key (payload_open, state_*, device_class, icon,
          a literal *_template, ...) is device- and payload-independent and is
          copied as-is.
        """
        for topic, payload in entities.items():
            comp = topic.split("/")[-3]
            block = overrides.get(comp)
            if not block:
                continue
            invert_pos = bool(block.get("invert_position"))

            for dp_key, stream_key, topic_field, tmpl_field, invertable in self._READ_ROLES:
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
                    dp = self._topic_dp(payload[topic_field])
                else:
                    continue  # role not present on this entity
                active = stream == "active"
                # Re-emit the topic only when the dp or the stream's active flag
                # was actually chosen (a pure invert leaves the builder's topic).
                if has_dp or stream is not None:
                    payload[topic_field] = self.scheme.state(dev_id, dp, active=active)
                payload[tmpl_field] = self.codec.value_template(
                    comp, dp_id=dp, active_only=active,
                    transform="100 - ((%s) | int)" if invert else None)

            for dp_key, topic_field in self._CMD_ROLES:
                if dp_key not in block:
                    continue
                if block[dp_key] is None:  # explicit null -> drop the command topic
                    payload.pop(topic_field, None)
                else:
                    payload[topic_field] = self.scheme.command(dev_id, str(block[dp_key]))
            if block.get("invert_set_position"):
                # Command direction (HA position -> device): no codec seam exists
                # for command encoding, so the legacy raw-int form is used.
                payload["set_position_template"] = "{{ 100 - value | int }}"

            for key, val in block.items():
                if key not in self._OVERRIDE_RESERVED:
                    payload[key] = val

            # Drop arbitrary keys last, so removal always wins over the merges above.
            for field in block.get("remove", ()):
                payload.pop(field, None)

    # --- DP normalization & merging ---

    def _normalize_dps(self, device: Dict[str, Any]) -> Dict[str, Any]:
        raw_dps = device.get('local_strategy') or device.get('mapping') or device.get('function') or {}
        normalized = {}
        for dp_id, val in raw_dps.items():
            dp_id = str(dp_id)
            if isinstance(val, dict):
                code = val.get('status_code') or val.get('code') or dp_id
                vtype = val.get('config_item', {}).get('valueType') or val.get('type', "String")
                meta = val.get('config_item', {}).get('valueDesc', {}) or val.get('values', {})
                if isinstance(meta, str):
                    try: meta = json.loads(meta)
                    except: meta = {}

                # Extract val_map from enumMappingMap
                if 'val_map' not in meta:
                    enum_map = val.get('config_item', {}).get('enumMappingMap')
                    if enum_map and isinstance(enum_map, dict):
                        meta['val_map'] = { k: v.get('value') for k, v in enum_map.items() if isinstance(v, dict) and v.get('value') }
            else: code, vtype, meta = dp_id, "String", {}
            normalized[dp_id] = {"code": code, "type": vtype, "meta": meta, "ent_info": None}
        return normalized

    def _merge_user_dp(self, dps: Dict[str, Any], dp_id: str, meta: Dict[str, Any]):
        """Inject a missing DP or override an existing one with user-supplied metadata."""
        if dp_id not in dps:
            dps[dp_id] = {
                "code": meta.get('code', dp_id),
                "type": meta.get('type', "Integer"),
                "meta": meta,
                "ent_info": meta.get('ent_info'),
            }
        else:
            obj = dps[dp_id]
            if 'code' in meta: obj['code'] = meta['code']
            if 'type' in meta: obj['type'] = meta['type']
            if 'ent_info' in meta: obj['ent_info'] = meta['ent_info']
            # Reserved keys aren't merged into the DP's metadata bag
            reserved = {'code', 'type', 'ent_info', 'comp', 'class', 'unit', 'icon'}
            obj['meta'].update({k: v for k, v in meta.items() if k not in reserved})

        if not dps[dp_id].get("ent_info"):
            dps[dp_id]["ent_info"] = PROPERTY_MAP.get(dps[dp_id]["code"])

    # --- Helpers (shared across entity builders) ---

    @staticmethod
    def _find_dp(dps: Dict[str, Any], pattern: str) -> Optional[str]:
        for d_id, obj in dps.items():
            if re.match(pattern, obj["code"]): return d_id
        return None

    @classmethod
    def _check_signature(cls, dps: Dict[str, Any], comp_type: str):
        sig = COMPLEX_SIGNATURES.get(comp_type)
        if not sig: return False
        found_mandatory = [cls._find_dp(dps, p) for p in sig['mandatory']]
        if None in found_mandatory: return False
        found_optional = [cls._find_dp(dps, p) for p in sig['optional'] if cls._find_dp(dps, p)]
        return found_mandatory + found_optional

    def _normalize_unit(self, unit: Optional[str], device_class: Optional[str] = None) -> Optional[str]:
        if not unit: return None
        u_lower = unit.lower().strip()
        for standard, aliases in UNIT_NORM_MAP.items():
            if u_lower == standard.lower() or u_lower in aliases: return standard
        return None

    # --- Entity builders ---

    def _build_entities(self, dev_id: str, name: str, category: str, dps: Dict[str, Any], info: Dict[str, Any], functions: Dict[str, Any], parent_id: Optional[str] = None) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        consumed: Set[str] = set()
        model = info.get('model', category)
        common_device = {"identifiers": [dev_id], "name": name, "manufacturer": DEFAULT_MANUFACTURER, "model": model}
        avail_id = parent_id or dev_id
        avail = {
            "availability_topic": self.scheme.availability(avail_id),
            "availability_template": self.codec.availability_template(),
            "payload_available": "online", "payload_not_available": "offline"
        }

        self._build_cover(dev_id, category, dps, common_device, avail, consumed, results)
        self._build_climate(dev_id, category, dps, common_device, avail, consumed, results)
        self._build_fan(dev_id, category, dps, common_device, avail, consumed, results)
        self._build_light(dev_id, category, dps, common_device, avail, consumed, results)
        self._build_individual(dev_id, category, dps, functions, common_device, avail, consumed, results)

        return results

    def _build_cover(self, dev_id, category, dps, common_device, avail, consumed, results):
        if CATEGORY_MAP.get(category) == 'cover' or self._check_signature(dps, 'cover'):
            c_dp, p_dp, s_dp = self._find_dp(dps, r'^(control|control_1|state|mach_operate)$'), self._find_dp(dps, r'^(percent_control|position|percent_state)$'), self._find_dp(dps, r'^(percent_state|position)$')
            if c_dp or p_dp:
                payload = {"name": None, "unique_id": f"{dev_id}_cover", "device": common_device, **avail}
                if category == 'cl': payload["device_class"] = "curtain"
                elif category == 'mc': payload["device_class"] = "window"
                if c_dp:
                    payload["command_topic"] = self.scheme.command(dev_id, c_dp)
                    if dps[c_dp]["code"] in ["state", "control", "mach_operate"]:
                        payload.update({"state_topic": self.scheme.state(dev_id, c_dp), "value_template": self.codec.value_template("cover", dp_id=c_dp)})
                    consumed.add(c_dp)
                if p_dp:
                    payload.update({"set_position_topic": self.scheme.command(dev_id, p_dp), "position_topic": self.scheme.state(dev_id, s_dp or p_dp), "position_template": self.codec.value_template("cover", dp_id=s_dp or p_dp)})
                    consumed.update([p_dp, s_dp] if s_dp else [p_dp])
                results[self.scheme.discovery("cover", dev_id, "motor")] = payload

    def _build_climate(self, dev_id, category, dps, common_device, avail, consumed, results):
        if CATEGORY_MAP.get(category) == 'climate' or self._check_signature(dps, 'climate'):
            t_set, t_cur, mode, sw = self._find_dp(dps, r'^(temp_set|occupied_heating_setpoint)$'), self._find_dp(dps, r'^(temp_current|local_temperature)$'), self._find_dp(dps, r'^(mode|system_mode)$'), self._find_dp(dps, r'^switch$')
            if t_set and t_cur:
                payload = {"name": None, "unique_id": f"{dev_id}_climate", "device": common_device, **avail}
                payload.update({"temperature_command_topic": self.scheme.command(dev_id, t_set), "temperature_state_topic": self.scheme.state(dev_id, t_set), "temperature_state_template": self.codec.value_template("climate", dps[t_set]['meta'].get('scale', 0), dp_id=t_set)})
                payload.update({"current_temperature_topic": self.scheme.state(dev_id, t_cur), "current_temperature_template": self.codec.value_template("climate", dps[t_cur]['meta'].get('scale', 0), dp_id=t_cur)})
                consumed.update([t_set, t_cur])
                if mode:
                    payload.update({"mode_command_topic": self.scheme.command(dev_id, mode), "mode_state_topic": self.scheme.state(dev_id, mode), "mode_state_template": self.codec.value_template("climate", dp_id=mode)})
                    consumed.add(mode)
                if sw:
                    payload["power_command_topic"] = self.scheme.command(dev_id, sw)
                    consumed.add(sw)
                results[self.scheme.discovery("climate", dev_id, "thermostat")] = payload

    def _build_fan(self, dev_id, category, dps, common_device, avail, consumed, results):
        if CATEGORY_MAP.get(category) == 'fan' or self._check_signature(dps, 'fan'):
            sp_dp, sw_dp, osc_dp = self._find_dp(dps, r'^(fan_speed|fan_mode)$'), self._find_dp(dps, r'^switch$'), self._find_dp(dps, r'^fan_horizontal$')
            if sp_dp:
                payload = {"name": None, "unique_id": f"{dev_id}_fan", "device": common_device, **avail}
                payload.update({"percentage_command_topic": self.scheme.command(dev_id, sp_dp), "percentage_state_topic": self.scheme.state(dev_id, sp_dp), "percentage_value_template": self.codec.value_template("fan", dp_id=sp_dp)})
                consumed.add(sp_dp)
                if sw_dp:
                    payload.update({"command_topic": self.scheme.command(dev_id, sw_dp), "state_topic": self.scheme.state(dev_id, sw_dp), "state_value_template": self.codec.value_template("fan", dp_id=sw_dp), "payload_on": "true", "payload_off": "false"})
                    consumed.add(sw_dp)
                if osc_dp:
                    payload.update({"oscillation_command_topic": self.scheme.command(dev_id, osc_dp), "oscillation_state_topic": self.scheme.state(dev_id, osc_dp), "oscillation_value_template": self.codec.value_template("fan", dp_id=osc_dp), "payload_on": "true", "payload_off": "false"})
                    consumed.add(osc_dp)
                results[self.scheme.discovery("fan", dev_id, "fan")] = payload

    def _build_light(self, dev_id, category, dps, common_device, avail, consumed, results):
        if CATEGORY_MAP.get(category) == 'light' or self._check_signature(dps, 'light'):
            sw_dp, br_dp, tm_dp = self._find_dp(dps, r'^(switch_led|switch)$'), self._find_dp(dps, r'^(bright_value|brightness)$'), self._find_dp(dps, r'^(temp_value|color_temp)$')
            if br_dp:
                payload = {"name": None, "unique_id": f"{dev_id}_light", "device": common_device, **avail}
                payload.update({"brightness_command_topic": self.scheme.command(dev_id, br_dp), "brightness_state_topic": self.scheme.state(dev_id, br_dp), "brightness_value_template": self.codec.value_template("light", dp_id=br_dp)})
                consumed.add(br_dp)
                if sw_dp:
                    payload.update({"command_topic": self.scheme.command(dev_id, sw_dp), "state_topic": self.scheme.state(dev_id, sw_dp), "payload_on": "true", "payload_off": "false", "state_value_template": self.codec.value_template("light", dp_id=sw_dp)})
                    consumed.add(sw_dp)
                if tm_dp:
                    payload.update({"color_temp_command_topic": self.scheme.command(dev_id, tm_dp), "color_temp_state_topic": self.scheme.state(dev_id, tm_dp), "color_temp_value_template": self.codec.value_template("light", dp_id=tm_dp)})
                    consumed.add(tm_dp)
                results[self.scheme.discovery("light", dev_id, "led")] = payload

    def _build_individual(self, dev_id, category, dps, functions, common_device, avail, consumed, results):
        cat_map = GENERIC_MAP.get(category, {})
        for d_id, obj in dps.items():
            if d_id in consumed: continue
            code, vtype, meta, ent_info = obj["code"], obj["type"], obj["meta"], obj["ent_info"]
            if not ent_info: ent_info = cat_map.get(code)
            if not ent_info and str(code).startswith("switch_mode"): ent_info = ("event", "button", None, None)
            if not ent_info: ent_info = DP_CODE_MAP.get(code)
            if not ent_info:
                controllable = code in functions or d_id in functions
                if vtype == 'Boolean': ent_info = cat_map.get("default_bool") or ("switch", None, None, None)
                elif controllable: ent_info = ("number" if vtype == 'Integer' else "select" if vtype == 'Enum' else "text", None, None, None)
                else: ent_info = ("sensor", None, None, None)

            comp, dev_cls, unit, icon = ent_info
            # `event` entities and incremental/delta DPs (e.g. add_ele) read the
            # `active` stream; absolute-state DPs read the retained `passive` one.
            # See mapping.ACTIVE_ONLY_CODES; per-device override via dp_meta "active".
            delta = (code in ACTIVE_ONLY_CODES) or bool(meta.get("active"))
            is_active = (comp == "event") or delta
            payload = {"name": str(code).replace("_", " ").capitalize(), "unique_id": f"{dev_id}_{code}", "state_topic": self.scheme.state(dev_id, d_id, active=is_active), "device": common_device, **avail}
            if dev_cls: payload["device_class"] = dev_cls
            final_unit = self._normalize_unit(meta.get('unit'), dev_cls) or self._normalize_unit(unit, dev_cls) or DEFAULT_UNITS_BY_CLASS.get(dev_cls)
            if final_unit: payload["unit_of_measurement"] = final_unit
            if icon: payload["icon"] = icon

            # JSON Payload & Scaling & Mapping
            payload["value_template"] = self.codec.value_template(comp, meta.get('scale', 0), meta.get('val_map'), dp_id=d_id, active_only=delta)

            # Delta/incremental DP (e.g. add_ele): the same increment value recurs,
            # and HA suppresses state-changed events for unchanged values — so a
            # utility_meter / integration would miss repeats. force_update makes
            # every report fire. (Only delta sensors need it; absolute state doesn't.)
            if delta:
                payload["force_update"] = True

            if comp == "number":
                scale = meta.get('scale', 0)
                for k in ['min', 'max', 'step']:
                    if k in meta:
                        val = meta[k]
                        if scale > 0:
                            val = round(val / (10 ** scale), scale)
                        payload[k] = val
            elif comp == "select":
                options = meta.get('options') or meta.get('range')
                if options:
                    payload["options"] = options
            elif comp == "event":
                event_types = meta.get('options') or meta.get('range')
                if event_types:
                    payload["event_types"] = event_types + ["single_click", "double_click", "long_press"]

            if comp in ["binary_sensor", "switch"]: payload.update({"payload_on": "true", "payload_off": "false"})
            if comp not in ['sensor', 'binary_sensor', 'event']: payload["command_topic"] = self.scheme.command(dev_id, d_id)
            results[self.scheme.discovery(comp, dev_id, code)] = payload


def initialize_generator(custom_path: Optional[str] = None) -> DiscoveryGenerator:
    return DiscoveryGenerator(UserConverter(custom_path))
