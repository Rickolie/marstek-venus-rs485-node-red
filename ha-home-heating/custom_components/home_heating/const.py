"""Constants of Home Heating."""

DOMAIN = "home_heating"
NAME = "Home Heating"
SIGNAL_UPDATE = f"{DOMAIN}_update"
EVENT_ALERT = f"{DOMAIN}_alert"

PANEL_URL = "home-heating"
PANEL_COMPONENT = "home-heating-panel"
STATIC_URL = "/home_heating_static"

STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}.state"

FORECAST_INTERVAL_MIN = 30

# Inputs: settings key -> (label, unit, numeric, needed by)
# "needed by" decides when a missing input is an error for the watchdog.
INPUTS = {
    "in_outside_temp": ("Outside temperature", "°C", True, ("hp", "vent", "history")),
    "in_inside_temp": ("Inside temperature", "°C", True, ("vent",)),
    "in_water_target_temp": ("Heat pump water target temperature", "°C", True, ("history",)),
    "in_heat_pump_power": ("Heat pump power", "W", True, ("history",)),
    "in_cop": ("Heat pump COP", None, True, ("history",)),
    "in_boiler_temp": ("Boiler temperature", "°C", True, ("boiler",)),
    "in_cv_temp": ("CV water temperature", "°C", True, ("boiler",)),
    "in_solar_next_hour": ("Expected solar power next hour", "W", True, ("boiler",)),
    "in_solar_remaining_today": ("Expected solar energy rest of today", "kWh", True, ("price",)),
    "in_holiday": ("On holiday", None, False, ("boiler",)),
}

# The history uses the master entity of these inputs
HISTORY_INPUTS = {
    "outside": "in_outside_temp",
    "water": "in_water_target_temp",
    "power": "in_heat_pump_power",
    "cop": "in_cop",
}

# Switch entities for the main settings
SWITCHES = {
    "outputs_enabled": ("Write to devices", "mdi:power-plug"),
    "hp_enabled": ("Heat pump control", "mdi:heat-pump"),
    "cooling_enabled": ("Cooling mode", "mdi:snowflake"),
    "vent_enabled": ("Ventilation boost", "mdi:fan-plus"),
    "boiler_enabled": ("Boiler control", "mdi:water-boiler"),
    "use_instant": ("Use doorstroomboiler", "mdi:water-boiler-auto"),
    "legionella_enabled": ("Weekly legionella run", "mdi:bacteria"),
    "price_enabled": ("Boiler on cheapest prices", "mdi:currency-eur"),
}
