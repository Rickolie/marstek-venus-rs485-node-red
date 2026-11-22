"""All settings, in one place.

The sidebar page is built from SECTIONS, so a new setting only needs to be added here.
Defaults are the values of the Node-RED flows this integration replaces.
No Home Assistant imports: the algorithm tests use the defaults directly.
"""

from __future__ import annotations

from copy import deepcopy

TEMP = ["sensor", "input_number", "number"]
NUMBER_OUT = ["number", "input_number", "climate", "water_heater"]

# Field types: bool, number, text, time, select, weekday, entity (one), entities (master + backups),
# table (rows of outside / water)
SECTIONS = [
    {
        "id": "general",
        "title": "General",
        "help": "Nothing is switched or written until 'Write to devices' is on: compare the decisions with "
        "your current setup first.",
        "fields": [
            {"key": "outputs_enabled", "type": "bool", "default": False, "label": "Write to devices",
             "help": "Off: only calculate and show what would be done (dry run)."},
            {"key": "update_interval", "type": "number", "default": 60, "min": 10, "max": 600, "step": 10,
             "unit": "s", "label": "Update every"},
        ],
    },
    {
        "id": "inputs",
        "title": "Inputs",
        "help": "Per input one or more entities. The first is the master; when it is unavailable the next one "
        "takes over. The watchdog reports unavailable, stale and deviating sensors.",
        "fields": [
            {"key": "in_outside_temp", "type": "entities", "domains": TEMP, "label": "Outside temperature",
             "default": ["sensor.sparrow_p60_heat_pump_outside_temperature"]},
            {"key": "in_inside_temp", "type": "entities", "domains": TEMP, "label": "Inside temperature",
             "default": []},
            {"key": "in_water_target_temp", "type": "entities", "domains": TEMP, "label": "Heat pump water target temperature",
             "default": ["sensor.sparrow_p60_heat_pump_water_target_temperature"]},
            {"key": "in_heat_pump_power", "type": "entities", "domains": ["sensor"], "label": "Heat pump power (W)",
             "default": ["sensor.sparrow_p60_heat_pump"]},
            {"key": "in_cop", "type": "entities", "domains": ["sensor"], "label": "Heat pump COP",
             "default": ["sensor.heatpump_cop_not_below_0"]},
            {"key": "in_boiler_temp", "type": "entities", "domains": TEMP, "label": "Boiler temperature",
             "default": ["sensor.thermostat_heatexchanger_w_offset"]},
            {"key": "in_cv_temp", "type": "entities", "domains": TEMP, "label": "CV water temperature",
             "default": ["sensor.diyless_thermostat_1_ch_return_temperature"]},
            {"key": "in_solar_next_hour", "type": "entities", "domains": ["sensor"], "label": "Expected solar power next hour (W)",
             "default": ["sensor.power_production_next_hour"]},
            {"key": "in_solar_remaining_today", "type": "entities", "domains": ["sensor"],
             "label": "Expected solar energy rest of today (kWh)", "default": [],
             "help": "For heating the boiler on the cheapest prices. Empty: always plan the cheapest hours."},
            {"key": "in_holiday", "type": "entities", "domains": ["input_boolean", "binary_sensor", "switch"],
             "label": "On holiday", "default": ["input_boolean.onholiday"]},
            {"key": "weather_entity", "type": "entity", "domains": ["weather"], "label": "Weather forecast",
             "default": "weather.forecast_home"},
            {"key": "input_max_age_minutes", "type": "number", "default": 0, "min": 0, "max": 1440, "step": 5,
             "unit": "min", "label": "Sensor stale after",
             "help": "A sensor that did not report for this long counts as unavailable. 0: no check "
             "(sensors that only report on change look stale)."},
            {"key": "input_max_deviation", "type": "number", "default": 2.0, "min": 0, "max": 20, "step": 0.5,
             "unit": "°C", "label": "Max deviation from the master",
             "help": "Warn when a backup temperature differs more from the master. 0: no check."},
        ],
    },
    {
        "id": "heat_pump",
        "title": "Heat pump feedforward",
        "fields": [
            {"key": "hp_enabled", "type": "bool", "default": False, "label": "Control the heat pump"},
            {"key": "outside_source", "type": "select", "options": ["Current", "Forecast"], "default": "Current",
             "label": "Outside temperature"},
            {"key": "forecast_lead_time", "type": "number", "default": 3, "min": 0, "max": 24, "step": 0.5, "unit": "h",
             "label": "Forecast lead time"},
            {"key": "forecast_window", "type": "number", "default": 2, "min": 0, "max": 12, "step": 0.5, "unit": "h",
             "label": "Forecast average window"},
            {"key": "forecast_weight", "type": "number", "default": 100, "min": 0, "max": 100, "step": 5, "unit": "%",
             "label": "Forecast weight", "help": "100 %: forecast only, 0 %: current temperature only."},
            {"key": "forecast_bias_correction", "type": "bool", "default": False,
             "label": "Correct the forecast with the measured temperature"},
            {"key": "forecast_max_age_hours", "type": "number", "default": 3, "min": 1, "max": 24, "step": 1, "unit": "h",
             "label": "Use the current temperature when the forecast is older than"},
            {"key": "curve_source", "type": "select", "options": ["Manual", "History"], "default": "Manual",
             "label": "Heating curve"},
            {"key": "heating_table", "type": "table", "label": "Manual heating curve (lookup table)",
             "default": [{"outside": -20, "water": 50}, {"outside": -10, "water": 44}, {"outside": 0, "water": 38},
                         {"outside": 10, "water": 27}, {"outside": 20, "water": 18}],
             "help": "Water temperature per outside temperature, linear in between, the end value beyond the table."},
            {"key": "min_water_temp", "type": "number", "default": 15, "min": 10, "max": 45, "step": 0.5, "unit": "°C",
             "label": "Min water temperature"},
            {"key": "max_water_temp", "type": "number", "default": 55, "min": 25, "max": 65, "step": 0.5, "unit": "°C",
             "label": "Max water temperature"},
            {"key": "simple_enabled", "type": "bool", "default": True, "label": "Simple day schedule in mild weather"},
            {"key": "simple_outside_temp", "type": "number", "default": 13.5, "min": -10, "max": 25, "step": 0.5,
             "unit": "°C", "label": "Simple schedule from average outside temperature",
             "help": "Your lookup table reaches 24 °C water at 13.3 °C outside."},
            {"key": "simple_forecast_hours", "type": "number", "default": 24, "min": 1, "max": 48, "step": 1, "unit": "h",
             "label": "Average over the next (with forecast)"},
            {"key": "simple_start_time", "type": "time", "default": "00:00", "label": "Start heating at"},
            {"key": "simple_stop_inside_temp", "type": "number", "default": 21, "min": 15, "max": 25, "step": 0.1,
             "unit": "°C", "label": "Stop for the day at inside temperature"},
            {"key": "simple_water_temp", "type": "number", "default": 24, "min": 18, "max": 35, "step": 0.5, "unit": "°C",
             "label": "Simple schedule water temperature"},
            {"key": "out_water_temp_entity", "type": "entity", "domains": NUMBER_OUT, "default": "",
             "label": "Write water temperature to", "help": "Empty: only calculate."},
            {"key": "out_min_change", "type": "number", "default": 0.5, "min": 0.1, "max": 5, "step": 0.1, "unit": "°C",
             "label": "Only write when it changes at least"},
            {"key": "out_heating_switch", "type": "entity", "domains": ["switch", "input_boolean"], "default": "",
             "label": "Heat pump on/off switch", "help": "On while heating or cooling, off when the simple schedule stops."},
            {"key": "out_mode_entity", "type": "entity", "domains": ["climate", "select", "input_select"], "default": "",
             "label": "Heat pump mode (heat / cool)", "help": "climate: hvac mode, select: the options below."},
            {"key": "mode_heat", "type": "text", "default": "heat", "label": "Mode option for heating"},
            {"key": "mode_cool", "type": "text", "default": "cool", "label": "Mode option for cooling"},
            {"key": "mode_off", "type": "text", "default": "off", "label": "Mode option for off"},
        ],
    },
    {
        "id": "cooling",
        "title": "Cooling",
        "fields": [
            {"key": "cooling_enabled", "type": "bool", "default": False, "label": "Cooling mode"},
            {"key": "cooling_from_outside", "type": "number", "default": 24, "min": 18, "max": 35, "step": 0.5,
             "unit": "°C", "label": "Cool when the outside temperature will be above"},
            {"key": "cooling_hysteresis", "type": "number", "default": 2, "min": 0, "max": 10, "step": 0.5, "unit": "°C",
             "label": "Stop cooling below that minus"},
            {"key": "cooling_forecast_hours", "type": "number", "default": 12, "min": 1, "max": 48, "step": 1, "unit": "h",
             "label": "Look ahead (with forecast)"},
            {"key": "cooling_table", "type": "table", "label": "Cooling curve",
             "default": [{"outside": 22, "water": 19}, {"outside": 40, "water": 22}]},
            {"key": "cooling_min_water", "type": "number", "default": 19, "min": 15, "max": 25, "step": 0.5, "unit": "°C",
             "label": "Min cooling water temperature"},
        ],
    },
    {
        "id": "ventilation",
        "title": "Ventilation",
        "fields": [
            {"key": "vent_enabled", "type": "bool", "default": False, "label": "Ventilation boost"},
            {"key": "vent_entity", "type": "entity", "domains": ["fan", "select", "input_select", "switch", "input_boolean"],
             "default": "", "label": "Ventilation"},
            {"key": "vent_boost_value", "type": "text", "default": "100", "label": "Boost value",
             "help": "fan: percentage, select: option, switch: ignored (on)."},
            {"key": "vent_normal_value", "type": "text", "default": "", "label": "Normal value",
             "help": "After the boost. Empty for a fan: back to the percentage before the boost."},
            {"key": "vent_min_outside", "type": "number", "default": 10, "min": 0, "max": 25, "step": 0.5, "unit": "°C",
             "label": "Outside above"},
            {"key": "vent_min_inside", "type": "number", "default": 22, "min": 15, "max": 30, "step": 0.5, "unit": "°C",
             "label": "Inside above"},
            {"key": "vent_hysteresis", "type": "number", "default": 0.5, "min": 0, "max": 3, "step": 0.1, "unit": "°C",
             "label": "Hysteresis"},
        ],
    },
    {
        "id": "boiler",
        "title": "Boiler",
        "fields": [
            {"key": "boiler_enabled", "type": "bool", "default": False, "label": "Control the boiler"},
            {"key": "use_instant", "type": "bool", "default": False, "label": "Use doorstroomboiler"},
            {"key": "element_switch", "type": "entity", "domains": ["switch"], "default": "switch.sonoff_10023c6bdb",
             "label": "Boiler element"},
            {"key": "instant_switch", "type": "entity", "domains": ["switch"], "default": "switch.sonoff_1002736ed7",
             "label": "Doorstroomboiler"},
            {"key": "valve_entity", "type": "entity", "domains": ["climate"], "default": "climate.heatexchanger_valve_thermostat",
             "label": "Heat exchanger valve"},
            {"key": "solar_threshold", "type": "number", "default": 3000, "min": 0, "max": 20000, "step": 100, "unit": "W",
             "label": "Heat on solar from"},
            {"key": "max_temp", "type": "number", "default": 60, "min": 40, "max": 80, "step": 1, "unit": "°C",
             "label": "Max boiler temperature"},
            {"key": "restart_hysteresis", "type": "number", "default": 5, "min": 0, "max": 20, "step": 0.5, "unit": "°C",
             "label": "Restart below the target minus"},
            {"key": "instant_below", "type": "number", "default": 38, "min": 20, "max": 60, "step": 1, "unit": "°C",
             "label": "Doorstroomboiler on below"},
            {"key": "valve_open", "type": "number", "default": 35, "min": 5, "max": 80, "step": 1, "unit": "°C",
             "label": "Valve set point open"},
            {"key": "valve_closed", "type": "number", "default": 7, "min": 5, "max": 80, "step": 1, "unit": "°C",
             "label": "Valve set point closed"},
            {"key": "legionella_enabled", "type": "bool", "default": True, "label": "Weekly legionella run"},
            {"key": "legionella_weekday", "type": "weekday", "default": "sun", "label": "Legionella day"},
            {"key": "legionella_time", "type": "time", "default": "11:00", "label": "Legionella time"},
            {"key": "legionella_temp", "type": "number", "default": 60, "min": 55, "max": 80, "step": 1, "unit": "°C",
             "label": "Legionella temperature"},
            {"key": "price_enabled", "type": "bool", "default": False, "label": "Heat on the cheapest prices without enough solar"},
            {"key": "price_entity", "type": "entity", "domains": ["sensor"], "label": "Price sensor",
             "default": "sensor.zonneplan_current_quarter_hourly_electricity_tariff"},
            {"key": "price_attribute", "type": "text", "default": "forecast", "label": "Attribute with the price list"},
            {"key": "price_time_key", "type": "text", "default": "start_date", "label": "Start time key"},
            {"key": "price_value_key", "type": "text", "default": "price_tax_included.amount", "label": "Price key",
             "help": "Dots for nested values."},
            {"key": "price_factor", "type": "number", "default": 10000000, "min": 0.0001, "max": 1e12, "step": "any",
             "label": "Divide the price by", "help": "To get € per kWh. Zonneplan: 10000000."},
            {"key": "price_hours", "type": "number", "default": 2, "min": 0.25, "max": 12, "step": 0.25, "unit": "h",
             "label": "Cheapest hours per day"},
            {"key": "price_plan_time", "type": "time", "default": "00:05", "label": "Plan the day at"},
            {"key": "price_deadline", "type": "time", "default": "17:00", "label": "Warm before"},
            {"key": "price_target_temp", "type": "number", "default": 55, "min": 40, "max": 80, "step": 1, "unit": "°C",
             "label": "Heat up to"},
            {"key": "price_min_solar_kwh", "type": "number", "default": 5, "min": 0, "max": 100, "step": 0.5, "unit": "kWh",
             "label": "Enough solar from"},
        ],
    },
    {
        "id": "history",
        "title": "History",
        "fields": [
            {"key": "history_time", "type": "time", "default": "00:05", "label": "Recalculate daily at"},
            {"key": "min_run_power", "type": "number", "default": 50, "min": 0, "max": 2000, "step": 10, "unit": "W",
             "label": "Heat pump running above"},
            {"key": "min_day_energy", "type": "number", "default": 0.5, "min": 0, "max": 50, "step": 0.1, "unit": "kWh",
             "label": "Day used from"},
            {"key": "min_day_coverage", "type": "number", "default": 0.9, "min": 0.5, "max": 1, "step": 0.05,
             "label": "Data needed per day (fraction)"},
        ],
    },
    {
        "id": "watchdog",
        "title": "Watchdog & MQTT",
        "help": "Problems are sent to MQTT (and as a Home Assistant event) so Node-RED can forward them to "
        "Telegram or Signal.",
        "fields": [
            {"key": "mqtt_enabled", "type": "bool", "default": True, "label": "Publish to MQTT"},
            {"key": "mqtt_base_topic", "type": "text", "default": "home_heating", "label": "MQTT base topic"},
            {"key": "alert_repeat_hours", "type": "number", "default": 24, "min": 0, "max": 168, "step": 1, "unit": "h",
             "label": "Repeat an alert every", "help": "While the problem stays. 0: only when it changes."},
            {"key": "output_verify_minutes", "type": "number", "default": 2, "min": 1, "max": 30, "step": 1, "unit": "min",
             "label": "A device must follow a command within"},
            {"key": "boiler_heating_check_minutes", "type": "number", "default": 120, "min": 0, "max": 600, "step": 10,
             "unit": "min", "label": "Element on without temperature rise for",
             "help": "0: no check."},
            {"key": "boiler_min_rise", "type": "number", "default": 1, "min": 0.1, "max": 10, "step": 0.1, "unit": "°C",
             "label": "Minimum rise in that time"},
        ],
    },
]

FIELDS = {f["key"]: f for section in SECTIONS for f in section["fields"]}
DEFAULTS = {key: f["default"] for key, f in FIELDS.items()}


def defaults() -> dict:
    return deepcopy(DEFAULTS)


def merged(options: dict | None) -> dict:
    """Stored options on top of the defaults (new settings get their default)."""
    result = defaults()
    for key, value in (options or {}).items():
        if key in FIELDS:
            result[key] = value
    return result


def _time(value) -> str:
    hour, minute = (int(p) for p in str(value).split(":")[:2])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"invalid time {value}")
    return f"{hour:02d}:{minute:02d}"


def validate(raw: dict) -> dict:
    """Check and convert settings from the sidebar page. Raises ValueError with the setting's label."""
    result = {}
    for key, value in raw.items():
        f = FIELDS.get(key)
        if f is None:
            continue  # unknown settings are dropped
        try:
            t = f["type"]
            if t == "bool":
                value = bool(value)
            elif t == "number":
                value = float(value)
                if "min" in f and value < f["min"] or "max" in f and value > f["max"]:
                    raise ValueError(f"must be between {f['min']:g} and {f['max']:g}")
                value = int(value) if value.is_integer() and isinstance(f["default"], int) else value
            elif t in ("text", "entity"):
                value = str(value or "").strip()
            elif t == "time":
                value = _time(value)
            elif t == "select":
                if value not in f["options"]:
                    raise ValueError(f"must be one of {', '.join(f['options'])}")
            elif t == "weekday":
                if value not in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
                    raise ValueError("invalid weekday")
            elif t == "entities":
                value = [str(v).strip() for v in (value or []) if str(v).strip()]
            elif t == "table":
                rows = [{"outside": float(r["outside"]), "water": float(r["water"])} for r in value or []]
                if t == "table" and len(rows) < 2:
                    raise ValueError("needs at least 2 points")
                value = sorted(rows, key=lambda r: r["outside"])
        except (TypeError, ValueError, KeyError) as err:
            raise ValueError(f"{f['label']}: {err}") from err
        result[key] = value
    return result
