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
    "presence_state": ("binary_sensor", "motion", None, None),
    "pir": ("binary_sensor", "motion", None, None),
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
    "indicator": ("select", None, None, "mdi:led-on"),
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

# --- Category Mapping (Tuya Category -> HA Domain) ---
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
