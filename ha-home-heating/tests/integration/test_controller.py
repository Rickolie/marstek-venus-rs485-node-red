"""The integration in a real Home Assistant core: outputs, watchdog, MQTT, settings."""

import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.home_heating import settings
from custom_components.home_heating.const import DOMAIN, EVENT_ALERT

ELEMENT, INSTANT, VALVE = "switch.sonoff_10023c6bdb", "switch.sonoff_1002736ed7", "climate.heatexchanger_valve_thermostat"


SERVICE_LOG: list[str] = []


def register_device_services(hass: HomeAssistant) -> None:
    """Stand-in device services: record the calls in order instead of switching real devices.

    Registered again after setup: loading the integration's own switch entities sets up the switch
    component, which registers the real switch services.
    """

    async def record(call):
        data = {k: v for k, v in call.data.items() if k != "entity_id"}
        entity = call.data["entity_id"]
        entity = entity[0] if isinstance(entity, list) else entity
        SERVICE_LOG.append(f"{call.domain}.{call.service} {entity} {json.dumps(data)}")

    for domain, service in [("switch", "turn_on"), ("switch", "turn_off"), ("climate", "set_temperature"),
                            ("climate", "set_hvac_mode"), ("number", "set_value"), ("fan", "set_percentage")]:
        hass.services.async_register(domain, service, record)


@pytest.fixture
def calls(hass: HomeAssistant):
    SERVICE_LOG.clear()
    register_device_services(hass)
    return SERVICE_LOG


async def setup(hass: HomeAssistant, states: dict, stub_devices: bool = True, **options) -> MockConfigEntry:
    await async_setup_component(hass, "http", {})
    for entity_id, value in states.items():
        if isinstance(value, tuple):
            hass.states.async_set(entity_id, value[0], value[1])
        else:
            hass.states.async_set(entity_id, value)
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={**settings.defaults(), **options})
    entry.add_to_hass(hass)
    with patch("custom_components.home_heating.controller.Controller._pause", AsyncMock()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    if stub_devices:
        register_device_services(hass)
    return entry


async def tick(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    """One run. Returns the device service calls made, in order, and how often it waited."""
    SERVICE_LOG.clear()
    with patch("custom_components.home_heating.controller.Controller._pause", AsyncMock()) as sleep:
        await entry.runtime_data.async_tick()
        await hass.async_block_till_done()
    return [*SERVICE_LOG, f"slept {sleep.await_count}"]


BOILER_STATES = {
    "sensor.thermostat_heatexchanger_w_offset": "30", "sensor.diyless_thermostat_1_ch_return_temperature": "45",
    "sensor.power_production_next_hour": "4000", "input_boolean.onholiday": "off",
    ELEMENT: "off", INSTANT: "on", VALVE: ("heat", {"temperature": 7}),
    "sensor.sparrow_p60_heat_pump_outside_temperature": "5",
}


async def test_boiler_switches_off_first_then_on(hass: HomeAssistant, calls) -> None:
    entry = await setup(hass, BOILER_STATES, outputs_enabled=True, boiler_enabled=True, use_instant=True)
    order = await tick(hass, entry)
    # sun: element on, doorstroomboiler off; CV warmer than the boiler: valve open
    assert order == [
        f'switch.turn_off {INSTANT} {{}}',
        f'switch.turn_on {ELEMENT} {{}}',
        f'climate.set_temperature {VALVE} {{"temperature": 35}}',
        "slept 1",
    ]


async def test_dry_run_switches_nothing(hass: HomeAssistant, calls) -> None:
    entry = await setup(hass, BOILER_STATES, outputs_enabled=False, boiler_enabled=True, use_instant=True)
    assert await tick(hass, entry) == ["slept 0"]
    actions = [a["action"] for a in entry.runtime_data.status()["actions"]]
    assert "Doorstroomboiler off" in actions and "Boiler element on" in actions


async def test_only_changes_are_written(hass: HomeAssistant, calls) -> None:
    states = {**BOILER_STATES, ELEMENT: "on", INSTANT: "off", VALVE: ("heat", {"temperature": 35})}
    entry = await setup(hass, states, outputs_enabled=True, boiler_enabled=True, use_instant=True)
    assert await tick(hass, entry) == ["slept 0"]


async def test_boiler_temperature_unavailable_switches_nothing(hass: HomeAssistant, calls) -> None:
    states = {**BOILER_STATES, "sensor.thermostat_heatexchanger_w_offset": "unavailable"}
    entry = await setup(hass, states, outputs_enabled=True, boiler_enabled=True, use_instant=True)
    assert await tick(hass, entry) == ["slept 0"]
    checks = entry.runtime_data.status()["watchdog"]["checks"]
    assert checks["input_in_boiler_temp"]["state"] == "error"


async def test_backup_sensor_takes_over(hass: HomeAssistant, calls) -> None:
    states = {**BOILER_STATES, "sensor.thermostat_heatexchanger_w_offset": "unavailable", "sensor.boiler_backup": "30"}
    entry = await setup(hass, states, outputs_enabled=True, boiler_enabled=True, use_instant=True,
                        in_boiler_temp=["sensor.thermostat_heatexchanger_w_offset", "sensor.boiler_backup"])
    order = await tick(hass, entry)
    assert f"switch.turn_on {ELEMENT} {{}}" in order
    status = entry.runtime_data.status()
    assert status["inputs"]["in_boiler_temp"]["source"] == "sensor.boiler_backup"
    assert status["watchdog"]["checks"]["input_in_boiler_temp"]["state"] == "warning"
    assert hass.states.get("sensor.home_heating_input_boiler_temperature").attributes["status"] == "backup"


async def test_device_not_following_alerts_on_mqtt(hass: HomeAssistant, calls, mqtt_mock) -> None:
    events = async_capture_events(hass, EVENT_ALERT)
    entry = await setup(hass, BOILER_STATES, outputs_enabled=True, boiler_enabled=True, use_instant=True)
    await tick(hass, entry)  # commands sent; the mocked switches keep their state
    before = len(mqtt_mock.async_publish.call_args_list)
    later = dt_util.utcnow() + timedelta(minutes=3)
    with patch("homeassistant.util.dt.utcnow", return_value=later), \
         patch("homeassistant.util.dt.now", return_value=dt_util.as_local(later)):
        await tick(hass, entry)
    published = mqtt_mock.async_publish.call_args_list[before:]
    alerts = [json.loads(c.args[1]) for c in published if c.args[0] == "home_heating/alert"]
    assert {a["check"] for a in alerts} >= {f"command_{ELEMENT}", f"command_{INSTANT}"}
    assert all(a["state"] == "error" and "did not follow" in a["message"] for a in alerts)
    assert {e.data["check"] for e in events} >= {f"command_{ELEMENT}"}
    assert hass.states.get("binary_sensor.home_heating_problem").state == "on"
    topics = {c.args[0] for c in published}
    assert {"home_heating/status", "home_heating/watchdog", "home_heating/heartbeat"} <= topics


async def test_heat_pump_outputs(hass: HomeAssistant, calls) -> None:
    states = {"sensor.sparrow_p60_heat_pump_outside_temperature": "5",
              "number.hp_water": ("30", {"min": 20, "max": 55}), "switch.hp": "off",
              "climate.hp": ("cool", {"temperature": 20})}
    entry = await setup(hass, states, outputs_enabled=True, hp_enabled=True, out_water_temp_entity="number.hp_water",
                        out_heating_switch="switch.hp", out_mode_entity="climate.hp", simple_outside_temp=30)
    order = await tick(hass, entry)
    assert order[:3] == [
        "switch.turn_on switch.hp {}",
        'climate.set_hvac_mode climate.hp {"hvac_mode": "heat"}',
        'number.set_value number.hp_water {"value": 32.5}',  # lookup table at 5 °C
    ]
    assert hass.states.get("binary_sensor.home_heating_heat_pump_heating").state == "on"


async def test_ventilation_boost_and_back(hass: HomeAssistant, calls) -> None:
    states = {"sensor.sparrow_p60_heat_pump_outside_temperature": "15", "sensor.inside": "23.5",
              "fan.wtw": ("on", {"percentage": 40})}
    entry = await setup(hass, states, outputs_enabled=True, vent_enabled=True, vent_entity="fan.wtw",
                        in_inside_temp=["sensor.inside"])
    assert SERVICE_LOG == ['fan.set_percentage fan.wtw {"percentage": 100}']  # first run, at start-up
    hass.states.async_set("fan.wtw", "on", {"percentage": 100})
    assert await tick(hass, entry) == ["slept 0"]  # stays boosted, nothing to change
    hass.states.async_set("fan.wtw", "on", {"percentage": 100})
    hass.states.async_set("sensor.sparrow_p60_heat_pump_outside_temperature", "8")
    assert await tick(hass, entry) == ['fan.set_percentage fan.wtw {"percentage": 40}', "slept 0"]  # back to before


async def test_price_plan_from_zonneplan_attribute(hass: HomeAssistant, calls) -> None:
    now = dt_util.now()
    start = now.replace(minute=0, second=0, microsecond=0)
    forecast = [{"start_date": (start + timedelta(minutes=15 * i)).isoformat(),
                 "price_tax_included": {"amount": 1_000_000 if i in (4, 5, 6, 7) else 3_000_000}} for i in range(96)]
    states = {**BOILER_STATES, "sensor.power_production_next_hour": "0",
              "sensor.zonneplan_current_quarter_hourly_electricity_tariff": ("0.3", {"forecast": forecast})}
    entry = await setup(hass, states, outputs_enabled=False, boiler_enabled=True, price_enabled=True,
                        price_hours=1, price_plan_time="00:00", price_deadline="23:59")
    await tick(hass, entry)
    plan = entry.runtime_data.status()["boiler"]["price_plan"]
    assert len(plan["slots"]) == 4 and plan["slots"][0][2] == 0.1  # the cheap hour, € 0.10


async def test_settings_page_api(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await setup(hass, {"sensor.sparrow_p60_heat_pump_outside_temperature": "5"})
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "home_heating/config"})
    config = (await client.receive_json())["result"]
    assert config["settings"]["heating_table"][0] == {"outside": -20, "water": 50}
    assert any(s["id"] == "boiler" for s in config["sections"])

    await client.send_json({"id": 2, "type": "home_heating/save", "settings": {"max_water_temp": 99}})
    response = await client.receive_json()
    assert not response["success"] and "Max water temperature" in response["error"]["message"]

    await client.send_json({"id": 3, "type": "home_heating/save",
                            "settings": {"max_water_temp": 50, "in_inside_temp": ["sensor.a", " ", "sensor.b"]}})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert entry.runtime_data.s["max_water_temp"] == 50
    assert entry.runtime_data.s["in_inside_temp"] == ["sensor.a", "sensor.b"]

    await client.send_json({"id": 4, "type": "home_heating/status"})
    assert (await client.receive_json())["result"]["feedforward"]["water_temp"] == 32.5


async def test_switch_entity_changes_setting(hass: HomeAssistant) -> None:
    entry = await setup(hass, {"sensor.sparrow_p60_heat_pump_outside_temperature": "5"}, stub_devices=False)
    assert hass.states.get("switch.home_heating_write_to_devices").state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.home_heating_write_to_devices"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["outputs_enabled"] is True and entry.runtime_data.s["outputs_enabled"] is True
    assert hass.states.get("switch.home_heating_write_to_devices").state == "on"


async def test_unload(hass: HomeAssistant) -> None:
    entry = await setup(hass, {})
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
