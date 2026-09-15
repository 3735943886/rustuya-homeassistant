"""Classify a Tuya device's DPs into HA entity descriptors (the IL).

Pure decision logic: given a device's raw DPs (+ any product-specific
overrides), decide what HA entities they represent and how — no topics, no
Jinja, no transport. See ``il.py`` for the output shape and
``render_mqtt.py`` for the (only, for now) consumer.
"""
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .dp_mapping import (
    UNIT_NORM_MAP, DEFAULT_UNITS_BY_CLASS,
    DP_CODE_MAP, GENERIC_MAP, PROPERTY_MAP, CATEGORY_MAP, COMPLEX_SIGNATURES,
    ACTIVE_ONLY_CODES,
)
from .converter import Converter
from .il import DpRole, EntityIL

# Write-side value encoding per HA component (used when building a role's
# write side). Anything not listed falls back to "raw" (sent as a quoted string).
_CMD_KIND = {"switch": "bool", "binary_sensor": "bool", "number": "number",
             "select": "enum", "text": "string"}


def classify_device(
    device: Dict[str, Any], converter: Converter
) -> Tuple[List[EntityIL], str, str, Optional[Dict[str, Any]]]:
    """Returns ``(entities, source_label, model, user_info)``.

    ``user_info`` is the raw per-product_id override block (or None) — the
    caller needs it again to apply ``discovery_overrides`` after rendering
    (see ``render_mqtt.apply_overrides``); it isn't folded into the IL because
    those overrides patch the *rendered* MQTT payload's own field names, not
    DP semantics (see that module's docstring)."""
    device_id, product_id = device['id'], device.get('product_id')
    category = device.get('category', 'unknown')
    product_name = device.get('product_name', category)

    normalized_dps = _normalize_dps(device)

    model = product_name
    source_label = "generic"
    user_info = converter.find(product_id)
    if user_info:
        source_label = "custom"
        model = user_info.get('model') or product_name
        for dp_id, meta in user_info.get('dp_meta', {}).items():
            _merge_user_dp(normalized_dps, str(dp_id), meta)

    functions = device.get('function', {})
    consumed: Set[str] = set()
    entities: List[EntityIL] = []
    _classify_cover(category, normalized_dps, consumed, entities)
    _classify_climate(category, normalized_dps, consumed, entities)
    _classify_fan(category, normalized_dps, consumed, entities)
    _classify_light(category, normalized_dps, consumed, entities)
    _classify_individual(category, normalized_dps, functions, consumed, entities)

    return entities, source_label, model, user_info


# --- DP normalization & merging ---

def _normalize_dps(device: Dict[str, Any]) -> Dict[str, Any]:
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


def _merge_user_dp(dps: Dict[str, Any], dp_id: str, meta: Dict[str, Any]):
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


# --- helpers ---

def _find_dp(dps: Dict[str, Any], pattern: str) -> Optional[str]:
    for d_id, obj in dps.items():
        if re.match(pattern, obj["code"]): return d_id
    return None


def _check_signature(dps: Dict[str, Any], comp_type: str):
    sig = COMPLEX_SIGNATURES.get(comp_type)
    if not sig: return False
    found_mandatory = [_find_dp(dps, p) for p in sig['mandatory']]
    if None in found_mandatory: return False
    found_optional = [_find_dp(dps, p) for p in sig['optional'] if _find_dp(dps, p)]
    return found_mandatory + found_optional


def _normalize_unit(unit: Optional[str], device_class: Optional[str] = None) -> Optional[str]:
    if not unit: return None
    u_lower = unit.lower().strip()
    for standard, aliases in UNIT_NORM_MAP.items():
        if u_lower == standard.lower() or u_lower in aliases: return standard
    return None


# --- classifiers ---

def _classify_cover(category, dps, consumed, entities):
    if CATEGORY_MAP.get(category) == 'cover' or _check_signature(dps, 'cover'):
        c_dp = _find_dp(dps, r'^(control|control_1|state|mach_operate)$')
        p_dp = _find_dp(dps, r'^(percent_control|position|percent_state)$')
        s_dp = _find_dp(dps, r'^(percent_state|position)$')
        if c_dp or p_dp:
            device_class = "curtain" if category == 'cl' else "window" if category == 'mc' else None
            roles: Dict[str, DpRole] = {}
            if c_dp:
                roles["command"] = DpRole(dp_id=c_dp, readable=False, writable=True, kind="raw")
                if dps[c_dp]["code"] in ["state", "control", "mach_operate"]:
                    roles["state"] = DpRole(dp_id=c_dp, readable=True, writable=False)
                consumed.add(c_dp)
            if p_dp:
                scale = dps[p_dp]['meta'].get('scale', 0)
                roles["set_position"] = DpRole(dp_id=p_dp, readable=False, writable=True, kind="number", scale=scale)
                roles["position"] = DpRole(dp_id=(s_dp or p_dp), readable=True, writable=False)
                consumed.update([p_dp, s_dp] if s_dp else [p_dp])
            entities.append(EntityIL(component="cover", key="motor", grouped=True,
                                     name=None, device_class=device_class, roles=roles))


def _classify_climate(category, dps, consumed, entities):
    if CATEGORY_MAP.get(category) == 'climate' or _check_signature(dps, 'climate'):
        t_set = _find_dp(dps, r'^(temp_set|occupied_heating_setpoint)$')
        t_cur = _find_dp(dps, r'^(temp_current|local_temperature)$')
        mode = _find_dp(dps, r'^(mode|system_mode)$')
        sw = _find_dp(dps, r'^switch$')
        if t_set and t_cur:
            roles: Dict[str, DpRole] = {
                "temperature": DpRole(dp_id=t_set, readable=True, writable=True, kind="number",
                                      scale=dps[t_set]['meta'].get('scale', 0)),
                "current_temperature": DpRole(dp_id=t_cur, readable=True, writable=False,
                                              scale=dps[t_cur]['meta'].get('scale', 0)),
            }
            consumed.update([t_set, t_cur])
            if mode:
                roles["mode"] = DpRole(dp_id=mode, readable=True, writable=True, kind="enum",
                                       val_map=dps[mode]['meta'].get('val_map'))
                consumed.add(mode)
            if sw:
                roles["power"] = DpRole(dp_id=sw, readable=False, writable=True, kind="bool")
                consumed.add(sw)
            entities.append(EntityIL(component="climate", key="thermostat", grouped=True,
                                     name=None, roles=roles))


def _classify_fan(category, dps, consumed, entities):
    if CATEGORY_MAP.get(category) == 'fan' or _check_signature(dps, 'fan'):
        sp_dp = _find_dp(dps, r'^(fan_speed|fan_mode)$')
        sw_dp = _find_dp(dps, r'^switch$')
        osc_dp = _find_dp(dps, r'^fan_horizontal$')
        if sp_dp:
            roles: Dict[str, DpRole] = {
                "percentage": DpRole(dp_id=sp_dp, readable=True, writable=True, kind="number",
                                     scale=dps[sp_dp]['meta'].get('scale', 0)),
            }
            consumed.add(sp_dp)
            if sw_dp:
                roles["switch"] = DpRole(dp_id=sw_dp, readable=True, writable=True, kind="bool")
                consumed.add(sw_dp)
            if osc_dp:
                roles["oscillation"] = DpRole(dp_id=osc_dp, readable=True, writable=True, kind="bool")
                consumed.add(osc_dp)
            entities.append(EntityIL(component="fan", key="fan", grouped=True, name=None, roles=roles))


def _classify_light(category, dps, consumed, entities):
    if CATEGORY_MAP.get(category) == 'light' or _check_signature(dps, 'light'):
        sw_dp = _find_dp(dps, r'^(switch_led|switch)$')
        br_dp = _find_dp(dps, r'^(bright_value|brightness)$')
        tm_dp = _find_dp(dps, r'^(temp_value|color_temp)$')
        if br_dp:
            roles: Dict[str, DpRole] = {
                "brightness": DpRole(dp_id=br_dp, readable=True, writable=True, kind="number",
                                     scale=dps[br_dp]['meta'].get('scale', 0)),
            }
            consumed.add(br_dp)
            if sw_dp:
                roles["switch"] = DpRole(dp_id=sw_dp, readable=True, writable=True, kind="bool")
                consumed.add(sw_dp)
            if tm_dp:
                roles["color_temp"] = DpRole(dp_id=tm_dp, readable=True, writable=True, kind="number",
                                             scale=dps[tm_dp]['meta'].get('scale', 0))
                consumed.add(tm_dp)
            entities.append(EntityIL(component="light", key="led", grouped=True, name=None, roles=roles))


def _classify_individual(category, dps, functions, consumed, entities):
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
        # `active` stream; absolute-state DPs read the retained `state` snapshot.
        # See dp_mapping.ACTIVE_ONLY_CODES; per-device override via dp_meta "active".
        delta = (code in ACTIVE_ONLY_CODES) or bool(meta.get("active"))
        is_active = (comp == "event") or delta
        final_unit = _normalize_unit(meta.get('unit'), dev_cls) or _normalize_unit(unit, dev_cls) or DEFAULT_UNITS_BY_CLASS.get(dev_cls)

        role = DpRole(dp_id=d_id, readable=True,
                     writable=(comp not in ('sensor', 'binary_sensor', 'event')),
                     kind=_CMD_KIND.get(comp, "raw"),
                     scale=meta.get('scale', 0), val_map=meta.get('val_map'),
                     stream="active" if is_active else "state")
        entity = EntityIL(component=comp, key=code, grouped=False,
                          name=str(code).replace("_", " ").capitalize(),
                          device_class=dev_cls, unit=final_unit, icon=icon,
                          force_update=delta, roles={"main": role})

        if comp == "number":
            scale = meta.get('scale', 0)
            for k in ('min', 'max', 'step'):
                if k in meta:
                    val = meta[k]
                    if scale > 0:
                        val = round(val / (10 ** scale), scale)
                    setattr(entity, k, val)
        elif comp == "select":
            options = meta.get('options') or meta.get('range')
            if options:
                entity.options = options
        elif comp == "event":
            event_types = meta.get('options') or meta.get('range')
            if event_types:
                entity.options = event_types + ["single_click", "double_click", "long_press"]

        entities.append(entity)
