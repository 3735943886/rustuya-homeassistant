# --- Unit Normalization ---
UNIT_MAP = {
    'w': 'W', 'kwh': 'kWh', 'kw': 'kW', 'v': 'V', 'ma': 'mA', 'a': 'A',
    'c': '°C', 'f': '°F', '℃': '°C', '℉': '°F', 'pct': '%', 'percent': '%', 'lux': 'lx'
}

UNIT_OVERRIDES = {
    'temperature': '°C',
    'humidity': '%',
    'battery': '%',
    'illuminance': 'lx',
    'power': 'W',
    'voltage': 'V',
    'current': 'mA',
    'energy': 'kWh'
}

# --- Unit Normalization Data (Advanced) ---
UNIT_NORM_MAP = {
    "°C": {"℃", "°c", "c", "celsius"},
    "°F": {"℉", "°f", "f", "fahrenheit"},
    "W": {"w", "watt"},
    "kW": {"kw"},
    "kWh": {"kwh", "kw.h", "kw·h", "kilowatt-hour"},
    "V": {"v", "volt"},
    "mA": {"ma", "milliampere"},
    "A": {"a", "ampere"},
    "lx": {"lux"},
    "Hz": {"hz"},
    "ppm": {"parts per million"},
    "%": {"pct", "percent", "% rh"},
    "s": {"sec", "second", "seconds"},
    "min": {"min", "minute", "minutes"},
    "h": {"h", "hour", "hours"},
    "m": {"m", "meter", "meters"},
}

DEFAULT_UNITS_BY_CLASS = {
    "temperature": "°C",
    "humidity": "%",
    "battery": "%",
    "power": "W",
    "voltage": "V",
    "current": "A",
    "energy": "kWh",
    "illuminance": "lx",
    "distance": "m",
}

# --- DP Code Mapping ---
DP_CODE_MAP = {
    # Component, Device Class, Unit, Icon
    "switch": ("switch", "switch", None, "mdi:power"),
    "switch_1": ("switch", "switch", None, "mdi:power"),
    "switch_2": ("switch", "switch", None, "mdi:power"),
    "switch_3": ("switch", "switch", None, "mdi:power"),
    "switch_4": ("switch", "switch", None, "mdi:power"),
    "switch_led": ("light", None, None, None),
    "switch_usb1": ("switch", "outlet", None, "mdi:usb"),
    "temp_current": ("sensor", "temperature", "°C", None),
    "temp_set": ("number", "temperature", "°C", None),
    "humidity_value": ("sensor", "humidity", "%", None),
    "humidity_current": ("sensor", "humidity", "%", None),
    "co2_value": ("sensor", "co2", "ppm", None),
    "co_value": ("sensor", "co", "ppm", None),
    "pm25_value": ("sensor", "pm25", "µg/m³", None),
    "illuminance_value": ("sensor", "illuminance", "lx", None),
    "bright_value": ("sensor", "illuminance", "lx", None),
    "battery_percentage": ("sensor", "battery", "%", None),
    "battery_state": ("binary_sensor", "battery", None, None),
    "battery": ("sensor", "battery", "%", None),
    "cur_current": ("sensor", "current", "mA", None),
    "cur_voltage": ("sensor", "voltage", "V", None),
    "cur_power": ("sensor", "power", "W", None),
    "add_ele": ("sensor", "energy", "kWh", None),
    "electricity_left": ("sensor", "battery", "%", None),
    "contact_state": ("binary_sensor", "door", None, None),
    "doorcontact_state": ("binary_sensor", "door", None, None),
    "smoke_sensor_status": ("binary_sensor", "smoke", None, None),
    "gas_sensor_status": ("binary_sensor", "gas", None, None),
    "watersensor_state": ("binary_sensor", "moisture", None, None),
    "tamper": ("binary_sensor", "problem", None, None),
    "action": ("sensor", None, None, None),
    "work_mode": ("sensor", None, None, None),
    "fault": ("binary_sensor", "problem", None, None),
    # Scene/Button Modes
    "switch_mode": ("event", "button", None, None),
    "switch_mode1": ("event", "button", None, None),
    "switch_mode2": ("event", "button", None, None),
    "switch_mode3": ("event", "button", None, None),
    "switch_mode4": ("event", "button", None, None),
    # Common Settings & States
    "child_lock": ("switch", None, None, "mdi:lock"),
    "relay_status": ("select", None, None, "mdi:restore"),
    "light_mode": ("select", None, None, "mdi:led-on"),
    "switch_type": ("select", None, None, "mdi:toggle-switch-outline"),
    "work_state": ("sensor", None, None, "mdi:run"),
    "alarm_active": ("binary_sensor", "safety", None, "mdi:alert"),
    "countdown_1": ("number", None, "s", "mdi:timer-sand"),
    "countdown_2": ("number", None, "s", "mdi:timer-sand"),
    "countdown_3": ("number", None, "s", "mdi:timer-sand"),
    "countdown_4": ("number", None, "s", "mdi:timer-sand"),
    "presence_time": ("number", None, "s", "mdi:clock-outline"),
    "near_detection": ("number", None, "m", "mdi:magnify-plus"),
    "far_detection": ("number", None, "m", "mdi:magnify-minus"),
}

# --- Active/passive value semantics ---
# rustuya-bridge publishes two flavors of each event (see bridge README §Events):
#   active  = the delta that just changed (no-retain; the "moment of change")
#   passive = full retained snapshot (the current state, recoverable on reconnect)
#
# Almost every DP is an ABSOLUTE STATE (temperature, switch, mode...) → read the
# retained `passive` snapshot. But a few Tuya DPs are CUMULATIVE-INCREMENT / delta
# values that only make sense on the active push — reading them from the retained
# passive snapshot re-delivers a stale increment (double-count / phantom on
# reconnect). Those few are listed here and treated like `event` entities:
# subscribe the `active` stream, ignore passive.
#
# This is a Tuya smell (a "sensor" that is really an event). Keep the set MINIMAL
# and explicit. NOTE: cumulative TOTALS (e.g. "energy" = total kWh) are absolute
# state and must NOT be here — only the per-report increments belong.
# Per-device exceptions can also be set via custom_converters dp_meta "active": true.
ACTIVE_ONLY_CODES = {
    "add_ele",  # incremental energy added since last report (NOT a running total)
}

# --- Tuya Category Reference ---
# Official Tuya category codes -> description (per developer.tuya.com).
# This dict is reference-only. Actual HA domain routing is done by CATEGORY_MAP below.
# When encountering a new category device, look it up here for meaning; if it's worth
# building a handler, add it deliberately to CATEGORY_MAP.
TUYA_CATEGORIES = {
    'cjkg':   'Scene switch',
    'ckqdkg': 'Hotel key card switch',
    'cl':     'Window covers',
    'clkg':   'Curtain switch',
    'co2bj':  'CO2 sensor',
    'cobj':   'CO sensor',
    'cz':     'Socket',
    'dc':     'String lights',
    'dd':     'Strip lights',
    'dj':     'Light source',
    'dlq':    'Circuit breaker',
    'dr':     'Electric blanket',
    'fwd':    'Ambiance light',
    'gyd':    'Motion sensor light',
    'hps':    'Human presence sensor',
    'kg':     'Switch',
    'mcs':    'Contact sensor',
    'pir':    'PIR sensor',
    'pm2.5':  'PM2.5 sensor',
    'rqbj':   'Gas detector',
    'sgbj':   'Siren alarm',
    'sj':     'Water leak detector',
    'sos':    'Emergency button',
    'tgkg':   'Dimmer switch',
    'tgq':    'Dimmer',
    'wsdcg':  'Temperature and humidity sensor',
    'xdd':    'Ceiling light',
    'ywbj':   'Smoke detector',
    'zd':     'Vibration sensor',
}

# --- Category Mapping (Tuya Category -> HA Domain) ---
# The actual HA domain routing dict. Only categories registered here receive the
# cover/climate/fan/light-specific handlers in _build_entities or per-category
# overrides from GENERIC_MAP. Unregistered categories fall back to generic
# individual DP handling.
# Categories present in TUYA_CATEGORIES but missing here (e.g. cjkg, ckqdkg, dlq,
# pir) are intentionally unregistered — handled via generic fallback until a
# clearer mapping is known.
CATEGORY_MAP = {
    # Cover / Curtain
    'cl': 'cover', 'clkg': 'cover', 'mc': 'cover', 'rs': 'cover', 'jdcljqr': 'cover',
    # Climate / Heater
    'wk': 'climate', 'qn': 'climate', 'kt': 'climate', 'wkcz': 'climate',
    # Fan / Air Purifier
    'fs': 'fan', 'kj': 'fan', 'fskj': 'fan',
    # Humidifier / Dehumidifier
    'jsq': 'humidifier', 'cs': 'humidifier',
    # Light
    'dj': 'light', 'dd': 'light', 'fwl': 'light', 'dc': 'light',
    # Socket / Switch
    'cz': 'switch', 'kg': 'switch', 'pc': 'switch', 'tdq': 'switch',
    # Sensor / Security
    'wsdcg': 'sensor', 'ywscg': 'sensor', 'rqcg': 'sensor', 'cg': 'sensor',
    'mcs': 'binary_sensor', 'hps': 'binary_sensor', 'sos': 'binary_sensor',
}

# --- Complex Entity Signature DPs ---
# If these DPs are found, we try to group them even if category is generic
COMPLEX_SIGNATURES = {
    'cover': {
        'mandatory': [r'^(control|control_1|state|mach_operate)$'],
        'optional': [r'^(percent_control|position|percent_state)$']
    },
    'climate': {
        'mandatory': [r'^(temp_set|occupied_heating_setpoint)$', r'^(temp_current|local_temperature)$'],
        'optional': [r'^(mode|system_mode|switch)$']
    },
    'light': {
        'mandatory': [r'^(bright_value|brightness)$'],
        'optional': [r'^(temp_value|color_temp|color_data)$']
    },
    'fan': {
        'mandatory': [r'^(fan_speed|fan_mode)$'],
        'optional': [r'^(switch|fan_horizontal|fan_direction)$']
    }
}

GENERIC_MAP = {
    # category -> { code_pattern -> (comp, dev_cls, unit, icon) }
    "kg": {"default_bool": ("switch", "switch", None, None)},
    "cz": {"default_bool": ("switch", "outlet", None, None)},
    "pc": {"default_bool": ("switch", "outlet", None, None)},
    # mcs (Contact sensor): some devices report contact state via the 'switch' code
    # (e.g. Door Sensor, product_id 7jIGJAymiH8OsFFb).
    # DP_CODE_MAP['switch'] maps to a controllable switch, but on mcs it should be
    # a read-only binary_sensor — override at the category level.
    "mcs": {"switch": ("binary_sensor", "door", None, None)},
}

# --- Herdsman Property Map (Property Name -> HA Entity Info) ---
PROPERTY_MAP = {
    "state": ("switch", "switch", None, None),
    "brightness": ("light", None, None, None),
    "color_temp": ("light", None, None, None),
    "temperature": ("sensor", "temperature", "°C", None),
    "va_temperature": ("sensor", "temperature", "°C", None),
    "humidity": ("sensor", "humidity", "%", None),
    "va_humidity": ("sensor", "humidity", "%", None),
    "battery": ("sensor", "battery", "%", None),
    "residual_electricity": ("sensor", "battery", "%", None),
    "power": ("sensor", "power", "W", None),
    "voltage": ("sensor", "voltage", "V", None),
    "current": ("sensor", "current", "mA", None),
    "energy": ("sensor", "energy", "kWh", None),
    "occupancy": ("binary_sensor", "motion", None, None),
    "contact": ("binary_sensor", "door", None, None),
    "illuminance": ("sensor", "illuminance", "lx", None),
    "distance": ("sensor", "distance", "m", None),
}

# --- rustuya errorCode Mapping ---
# Full dictionary of errorCodes that rustuya publishes to the
# rustuya/error/<device_id> topic.
# Source: rustuya/src/... define_error_codes! macro
#
# Columns: (name, human-readable message, emitter)
#   - "rustuya": rustuya emits this itself when it cannot reach the device or
#                 gets no response
#   - "device":  the device responded and rustuya forwarded it (i.e. the device
#                 is alive)
#   - "cloud":   Tuya cloud communication layer (unrelated to device health)
ERROR_CODES = {
    0:   ("ERR_SUCCESS",    "Connection Successful",             "device"),
    900: ("ERR_JSON",       "Invalid JSON Response from Device", "device"),
    901: ("ERR_CONNECT",    "Network Error: Unable to Connect",  "rustuya"),
    902: ("ERR_TIMEOUT",    "Timeout Waiting for Device",        "rustuya"),
    903: ("ERR_RANGE",      "Specified Value Out of Range",      "device"),
    904: ("ERR_PAYLOAD",    "Unexpected Payload from Device",    "device"),
    905: ("ERR_OFFLINE",    "Network Error: Device Unreachable", "rustuya"),
    906: ("ERR_STATE",      "Device in Unknown State",           "device"),
    907: ("ERR_FUNCTION",   "Function Not Supported by Device",  "device"),
    908: ("ERR_DEVTYPE",    "Device22 Detected: Retry Command",  "device"),
    909: ("ERR_CLOUDKEY",   "Missing Tuya Cloud Key and Secret", "cloud"),
    910: ("ERR_CLOUDRESP",  "Invalid JSON Response from Cloud",  "cloud"),
    911: ("ERR_CLOUDTOKEN", "Unable to Get Cloud Token",         "cloud"),
    912: ("ERR_PARAMS",     "Missing Function Parameters",       "cloud"),
    913: ("ERR_CLOUD",      "Error Response from Tuya Cloud",    "cloud"),
    914: ("ERR_KEY_OR_VER", "Check device key or version",       "device"),
}

# Subset of errorCodes from the dictionary above that should mark an HA entity
# as unavailable. `tuya_discovery_generator`'s availability_template references
# this list and is regenerated automatically, so when new codes appear just edit
# this list and regenerate the golden output.
#
# Editing guide:
#   - 901 ERR_CONNECT, 905 ERR_OFFLINE  -> emitted by rustuya on comm failure
#                                          (clearly unavailable)
#   - 914 ERR_KEY_OR_VER                -> device responds, but key/version
#                                          mismatch blocks all commands.
#                                          Requires re-registration. Functionally
#                                          unavailable.
#   - 902 ERR_TIMEOUT (excluded)        -> can be transient; excluded to avoid
#                                          false unavailable. Add it if real
#                                          offline cases are being missed in
#                                          practice.
#   - Other device-emitted (900/903/904/906/907/908)
#                                       -> the device replied = it's alive. Only
#                                          specific commands fail, so keep the
#                                          entity available (unlike 914, other
#                                          commands still work).
#   - 909~913 (cloud)                   -> unrelated to device health; no value
#                                          adding them.
UNAVAILABLE_ERROR_CODES = [901, 905, 914]
