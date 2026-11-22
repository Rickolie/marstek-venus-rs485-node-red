"""The history reads the real recorder."""

from homeassistant.core import HomeAssistant

from tests.integration.test_controller import setup


async def test_history_reads_the_recorder(hass: HomeAssistant) -> None:
    entry = await setup(hass, {"sensor.sparrow_p60_heat_pump_outside_temperature": "5", "sensor.sparrow_p60_heat_pump": "800"})
    await hass.async_block_till_done(wait_background_tasks=True)  # the history started at start-up
    await entry.runtime_data.async_recalculate_history()
    await hass.async_block_till_done()
    # less than one full day of data: no usable days, but the recorder could be read
    assert entry.runtime_data.history_error == "no days found on which the heat pump has been used"
