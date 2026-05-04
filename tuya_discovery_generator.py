import json
import re
import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
from tuya_mapping import (
    UNIT_MAP, UNIT_OVERRIDES, UNIT_NORM_MAP, DEFAULT_UNITS_BY_CLASS,
    DP_CODE_MAP, GENERIC_MAP, PROPERTY_MAP, CATEGORY_MAP, COMPLEX_SIGNATURES
)
from external_converter import ExternalConverter

# --- Constants & Configuration ---
@dataclass
class Config:
    HA_DISCOVERY_TOPIC = "homeassistant/{}/{}_{}/config"
    HA_STATE_TOPIC = "rustuya/event/{}/{}"
    HA_COMMAND_TOPIC = "rustuya/command/set/{}/{}"
    HA_ERROR_TOPIC = "rustuya/error/{}"
    DEFAULT_MANUFACTURER = "rustuya"

def normalize_unit(unit: str, device_class: str = None) -> str:
    """Normalize unit strings for HA compatibility."""
    if device_class in UNIT_OVERRIDES: return UNIT_OVERRIDES[device_class]
    if not unit: return unit
    cleaned = ''.join(filter(str.isalpha, unit)).lower()
    return UNIT_MAP.get(cleaned, UNIT_MAP.get(unit, unit))

logger = logging.getLogger("tuya_discovery")

# --- Discovery Generator ---

class DiscoveryGenerator:
    def __init__(self, converter: Optional[ExternalConverter] = None):
        # External converter is optional; if not provided, only internal mapping is used.
        self.converter = converter or ExternalConverter()

    def _normalize_unit(self, unit: Optional[str], device_class: Optional[str] = None) -> Optional[str]:
        if not unit: return DEFAULT_UNITS_BY_CLASS.get(device_class)
        u_lower = unit.lower().strip()
        for standard, aliases in UNIT_NORM_MAP.items():
            if u_lower == standard.lower() or u_lower in aliases: return standard
        if device_class in DEFAULT_UNITS_BY_CLASS and len(unit) > 5: return DEFAULT_UNITS_BY_CLASS[device_class]
        return unit

    def generate(self, device: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        device_id, product_id = device['id'], device.get('product_id')
        category, name = device.get('category', 'unknown'), device.get('name', device_id)
        product_name = device.get('product_name', category)
        
        # 1. Normalize DPs
        normalized_dps = self._normalize_dps(device)
        
        # 2. Match Source (External Converter)
        matched_source, ext_info = self.converter.find_match(product_id)
        
        # 3. Enrich DPs
        info = {"vendor": "Tuya", "model": product_name}
        if matched_source == "custom":
            info["model"] = ext_info.get('model') or product_name
            if "mappings" in ext_info:
                mappings = ext_info.get("mappings", {})
                for dp_id, val in normalized_dps.items():
                    if mapping := mappings.get(dp_id):
                        if isinstance(mapping, (list, tuple)): val["ent_info"] = mapping
                        elif isinstance(mapping, str): val.update({"code": mapping, "ent_info": PROPERTY_MAP.get(mapping)})
        elif matched_source == "yaml":
            info["model"] = ext_info.get('name') or product_name
            dp_meta = ext_info.get('dp_meta', {})
            for dp_id, obj in normalized_dps.items():
                if meta := dp_meta.get(dp_id):
                    # Override entity info
                    obj["ent_info"] = (meta.get('comp'), meta.get('class'), meta.get('unit'), meta.get('icon'))
                    # Enrich metadata (scale, min, max, etc.)
                    for k in ['scale', 'min', 'max', 'step', 'options', 'val_map']:
                        if k in meta: obj['meta'][k] = meta[k]
        elif matched_source == "ts":
            info["model"] = ext_info.get('model') or product_name
            dp_map = ext_info.get("dp_map", {})
            for dp_id, obj in normalized_dps.items():
                if prop := dp_map.get(dp_id):
                    obj.update({"code": prop, "ent_info": PROPERTY_MAP.get(prop)})
        
        # Final Source Label
        source_label = matched_source or "generic"
        
        # 4. Build Entities
        entities = self._build_entities(device_id, name, category, normalized_dps, info, device.get('function', {}), source_label)
        
        return entities, source_label

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

    def _build_entities(self, dev_id: str, name: str, category: str, dps: Dict[str, Any], info: Dict[str, Any], functions: Dict[str, Any], source: str) -> Dict[str, Any]:
        results, consumed = {}, set()
        model = info.get('model', category)
        common_device = {"identifiers": [dev_id], "name": name, "manufacturer": Config.DEFAULT_MANUFACTURER, "model": model}
        avail = {
            "availability_topic": Config.HA_ERROR_TOPIC.format(dev_id),
            "availability_template": "{{ 'online' if value_json.errorCode == 0 else 'offline' }}",
            "payload_available": "online", "payload_not_available": "offline"
        }

        def find_dp(pattern):
            for d_id, obj in dps.items():
                if re.match(pattern, obj["code"]): return d_id
            return None

        def get_tpl(comp, scale=0, val_map=None):
            if comp == "event": return "{{ { \"event_type\": value_json.value } | to_json if value_json.type == 'active' else '' }}"
            
            base = "value_json.value"
            
            if comp in ["binary_sensor", "switch"] and not val_map:
                return "{{ 'true' if value_json.value else 'false' }}"

            # 1. Scaling
            if scale > 0:
                expr = "((%s | float) / %g) | round(1)" % (base, 10 ** scale)
            else:
                expr = base
                
            # 2. Value Mapping (Enum)
            if val_map:
                map_str = json.dumps(val_map)
                expr = "(%s | string | lower)" % expr
                return "{{ %s[%s] | default(%s) }}" % (map_str, expr, expr)
                
            return "{{ %s }}" % expr

        def check_signature(comp_type):
            sig = COMPLEX_SIGNATURES.get(comp_type)
            if not sig: return False
            found_mandatory = [find_dp(p) for p in sig['mandatory']]
            if None in found_mandatory: return False
            found_optional = [find_dp(p) for p in sig['optional'] if find_dp(p)]
            return found_mandatory + found_optional

        # --- A. Cover ---
        if CATEGORY_MAP.get(category) == 'cover' or check_signature('cover'):
            c_dp, p_dp, s_dp = find_dp(r'^(control|control_1|state|mach_operate)$'), find_dp(r'^(percent_control|position|percent_state)$'), find_dp(r'^(percent_state|position)$')
            if c_dp or p_dp:
                payload = {"name": name, "unique_id": f"{dev_id}_cover", "device": common_device, **avail}
                if category == 'cl': payload["device_class"] = "curtain"
                elif category == 'mc': payload["device_class"] = "window"
                if c_dp:
                    payload["command_topic"] = Config.HA_COMMAND_TOPIC.format(dev_id, c_dp)
                    if dps[c_dp]["code"] in ["state", "control", "mach_operate"]:
                        payload.update({"state_topic": Config.HA_STATE_TOPIC.format(dev_id, c_dp), "value_template": get_tpl("cover")})
                    consumed.add(c_dp)
                if p_dp:
                    payload.update({"set_position_topic": Config.HA_COMMAND_TOPIC.format(dev_id, p_dp), "position_topic": Config.HA_STATE_TOPIC.format(dev_id, s_dp or p_dp), "position_template": get_tpl("cover")})
                    consumed.update([p_dp, s_dp] if s_dp else [p_dp])
                results[Config.HA_DISCOVERY_TOPIC.format("cover", dev_id, "motor")] = payload

        # --- B. Climate ---
        if CATEGORY_MAP.get(category) == 'climate' or check_signature('climate'):
            t_set, t_cur, mode, sw = find_dp(r'^(temp_set|occupied_heating_setpoint)$'), find_dp(r'^(temp_current|local_temperature)$'), find_dp(r'^(mode|system_mode)$'), find_dp(r'^switch$')
            if t_set and t_cur:
                payload = {"name": name, "unique_id": f"{dev_id}_climate", "device": common_device, **avail}
                payload.update({"temperature_command_topic": Config.HA_COMMAND_TOPIC.format(dev_id, t_set), "temperature_state_topic": Config.HA_STATE_TOPIC.format(dev_id, t_set), "temperature_state_template": get_tpl("climate", dps[t_set]['meta'].get('scale', 0))})
                payload.update({"current_temperature_topic": Config.HA_STATE_TOPIC.format(dev_id, t_cur), "current_temperature_template": get_tpl("climate", dps[t_cur]['meta'].get('scale', 0))})
                consumed.update([t_set, t_cur])
                if mode:
                    payload.update({"mode_command_topic": Config.HA_COMMAND_TOPIC.format(dev_id, mode), "mode_state_topic": Config.HA_STATE_TOPIC.format(dev_id, mode), "mode_state_template": get_tpl("climate")})
                    consumed.add(mode)
                if sw:
                    payload["power_command_topic"] = Config.HA_COMMAND_TOPIC.format(dev_id, sw)
                    consumed.add(sw)
                results[Config.HA_DISCOVERY_TOPIC.format("climate", dev_id, "thermostat")] = payload

        # --- C. Fan ---
        if CATEGORY_MAP.get(category) == 'fan' or check_signature('fan'):
            sp_dp, sw_dp, osc_dp = find_dp(r'^(fan_speed|fan_mode)$'), find_dp(r'^switch$'), find_dp(r'^fan_horizontal$')
            if sp_dp:
                payload = {"name": name, "unique_id": f"{dev_id}_fan", "device": common_device, **avail}
                payload.update({"percentage_command_topic": Config.HA_COMMAND_TOPIC.format(dev_id, sp_dp), "percentage_state_topic": Config.HA_STATE_TOPIC.format(dev_id, sp_dp), "percentage_value_template": get_tpl("fan")})
                consumed.add(sp_dp)
                if sw_dp:
                    payload.update({"command_topic": Config.HA_COMMAND_TOPIC.format(dev_id, sw_dp), "state_topic": Config.HA_STATE_TOPIC.format(dev_id, sw_dp), "state_value_template": get_tpl("fan"), "payload_on": "true", "payload_off": "false"})
                    consumed.add(sw_dp)
                if osc_dp:
                    payload.update({"oscillation_command_topic": Config.HA_COMMAND_TOPIC.format(dev_id, osc_dp), "oscillation_state_topic": Config.HA_STATE_TOPIC.format(dev_id, osc_dp), "oscillation_value_template": get_tpl("fan"), "payload_on": "true", "payload_off": "false"})
                    consumed.add(osc_dp)
                results[Config.HA_DISCOVERY_TOPIC.format("fan", dev_id, "fan")] = payload

        # --- D. Light ---
        if CATEGORY_MAP.get(category) == 'light' or check_signature('light'):
            sw_dp, br_dp, tm_dp = find_dp(r'^(switch_led|switch)$'), find_dp(r'^(bright_value|brightness)$'), find_dp(r'^(temp_value|color_temp)$')
            if br_dp:
                payload = {"name": name, "unique_id": f"{dev_id}_light", "device": common_device, **avail}
                payload.update({"brightness_command_topic": Config.HA_COMMAND_TOPIC.format(dev_id, br_dp), "brightness_state_topic": Config.HA_STATE_TOPIC.format(dev_id, br_dp), "brightness_value_template": get_tpl("light")})
                consumed.add(br_dp)
                if sw_dp:
                    payload.update({"command_topic": Config.HA_COMMAND_TOPIC.format(dev_id, sw_dp), "state_topic": Config.HA_STATE_TOPIC.format(dev_id, sw_dp), "payload_on": "true", "payload_off": "false", "state_value_template": get_tpl("light")})
                    consumed.add(sw_dp)
                if tm_dp:
                    payload.update({"color_temp_command_topic": Config.HA_COMMAND_TOPIC.format(dev_id, tm_dp), "color_temp_state_topic": Config.HA_STATE_TOPIC.format(dev_id, tm_dp), "color_temp_value_template": get_tpl("light")})
                    consumed.add(tm_dp)
                results[Config.HA_DISCOVERY_TOPIC.format("light", dev_id, "led")] = payload

        # --- Individual Remaining DPs ---
        cat_map = GENERIC_MAP.get(category, {})
        for d_id, obj in dps.items():
            if d_id in consumed: continue
            code, vtype, meta, ent_info = obj["code"], obj["type"], obj["meta"], obj["ent_info"]
            if not ent_info: ent_info = cat_map.get(code)
            if not ent_info and str(code).startswith("switch_mode"): ent_info = ("event", "button", None, None)
            if not ent_info and source == "ts": ent_info = PROPERTY_MAP.get(code)
            if not ent_info: ent_info = DP_CODE_MAP.get(code)
            if not ent_info:
                controllable = code in functions or d_id in functions
                if vtype == 'Boolean': ent_info = cat_map.get("default_bool") or ("switch", None, None, None)
                elif controllable: ent_info = ("number" if vtype == 'Integer' else "select" if vtype == 'Enum' else "text", None, None, None)
                else: ent_info = ("sensor", None, None, None)
            
            comp, dev_cls, unit, icon = ent_info
            payload = {"name": str(code).replace("_", " ").capitalize(), "unique_id": f"{dev_id}_{code}", "state_topic": Config.HA_STATE_TOPIC.format(dev_id, d_id), "device": common_device, **avail}
            if dev_cls: payload["device_class"] = dev_cls
            final_unit = self._normalize_unit(unit or meta.get('unit'), dev_cls)
            if final_unit: payload["unit_of_measurement"] = final_unit
            if icon: payload["icon"] = icon
            
            # JSON Payload & Scaling & Mapping
            payload["value_template"] = get_tpl(comp, meta.get('scale', 0), meta.get('val_map'))
            
            if comp == "number":
                for k in ['min', 'max', 'step']:
                    if k in meta: payload[k] = meta[k]
            elif comp == "select":
                options = meta.get('options') or meta.get('range')
                if options:
                    payload["options"] = options
            elif comp in ["binary_sensor", "switch"]: payload.update({"payload_on": "true", "payload_off": "false"})
            if comp not in ['sensor', 'binary_sensor', 'event']: payload["command_topic"] = Config.HA_COMMAND_TOPIC.format(dev_id, d_id)
            results[Config.HA_DISCOVERY_TOPIC.format(comp, dev_id, code)] = payload
        return results

def initialize_generator(yaml_path=None, ts_path=None, custom_path=None, force_update=False):
    converter = ExternalConverter(yaml_path, ts_path, custom_path, force_update)
    return DiscoveryGenerator(converter)

def main():
    gen = initialize_generator()
    try:
        with open('tuyadevices.json', 'r') as f:
            devices = json.load(f)
        for d in devices:
            if d.get('name') == 'kids_curtain_outside':
                payloads, source = gen.generate(d)
                print(f"Matched via: {source}\n{json.dumps(payloads, indent=2)}")
    except Exception as e:
        logger.error(f"Main failed: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
