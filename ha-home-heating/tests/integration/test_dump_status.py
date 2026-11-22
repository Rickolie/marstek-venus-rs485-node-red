"""Write the settings schema and a realistic status to a file, for testing the sidebar page in a browser.

Only runs with HOME_HEATING_DUMP=<directory>.
"""

import json
import os

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_heating import settings
from custom_components.home_heating.const import DOMAIN

DUMP = os.environ.get("HOME_HEATING_DUMP")


@pytest.mark.skipif(not DUMP, reason="only for the browser test")
async def test_dump(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "http", {})
    for entity_id, state in {
        "sensor.sparrow_p60_heat_pump_outside_temperature": "6.5", "sensor.outside_backup": "6.9",
        "sensor.living_room": "20.4", "sensor.thermostat_heatexchanger_w_offset": "47",
        "sensor.diyless_thermostat_1_ch_return_temperature": "unavailable", "sensor.cv_backup": "31",
        "sensor.power_production_next_hour": "800", "input_boolean.onholiday": "off",
        "switch.sonoff_10023c6bdb": "on", "switch.sonoff_1002736ed7": "off",
    }.items():
        hass.states.async_set(entity_id, state)
    hass.states.async_set("climate.heatexchanger_valve_thermostat", "heat", {"temperature": 7})
    options = {**settings.defaults(), "in_outside_temp": ["sensor.sparrow_p60_heat_pump_outside_temperature", "sensor.outside_backup"],
               "in_inside_temp": ["sensor.living_room"],
               "in_cv_temp": ["sensor.diyless_thermostat_1_ch_return_temperature", "sensor.cv_backup"],
               "hp_enabled": True, "boiler_enabled": True, "use_instant": True}
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_tick()
    with open(os.path.join(DUMP, "panel-data.json"), "w") as f:
        json.dump({"sections": settings.SECTIONS, "settings": settings.merged(entry.options),
                   "status": entry.runtime_data.status(),
                   "states": {s.entity_id: {"state": s.state, "attributes": dict(s.attributes)} for s in hass.states.async_all()}},
                  f, default=str)
