"""Smoke test: the integration loads in Home Assistant and creates its entities."""

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_heating.const import DOMAIN


async def test_setup_creates_entities(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, title="Home Heating", data={}, options={})
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.sparrow_p60_heat_pump_outside_temperature", "5")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_tick()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.home_heating_feedforward_water_temperature")
    assert state is not None and float(state.state) == 32.5
    print(sorted(s.entity_id for s in hass.states.async_all() if s.entity_id.split(".")[1].startswith("home_heating")))
