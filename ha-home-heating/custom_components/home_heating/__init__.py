"""Home Heating: heat pump feedforward, boiler, ventilation and watchdogs.

All settings are on the Home Heating page in the sidebar (frontend/home-heating-panel.js).
The algorithms are in logic/, the controller connects them to Home Assistant.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import DOMAIN, NAME, PANEL_COMPONENT, PANEL_URL, STATIC_URL
from .controller import Controller

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
VERSION = "0.1.0"

type HomeHeatingConfigEntry = ConfigEntry[Controller]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services and the websocket API, registered once."""
    websocket.async_register(hass)

    def controller() -> Controller | None:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        return entries[0].runtime_data if entries else None

    async def handle(call: ServiceCall) -> None:
        if (c := controller()) is None:
            return
        await {
            "recalculate_history": c.async_recalculate_history,
            "refresh_forecast": c.async_refresh_forecast,
            "start_legionella": c.async_start_legionella,
            "plan_prices": c.async_plan_prices,
        }[call.service]()

    for service in ("recalculate_history", "refresh_forecast", "start_legionella", "plan_prices"):
        hass.services.async_register(DOMAIN, service, handle)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HomeHeatingConfigEntry) -> bool:
    controller = Controller(hass, entry)
    entry.runtime_data = controller
    await controller.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async def _started(_hass: HomeAssistant) -> None:
        await controller.async_started()

    entry.async_on_unload(async_at_started(hass, _started))
    await _async_register_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeHeatingConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
        frontend.async_remove_panel(hass, PANEL_URL)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: HomeHeatingConfigEntry) -> None:
    await entry.runtime_data.async_apply_settings()


async def _async_register_panel(hass: HomeAssistant) -> None:
    if not hass.data.get(f"{DOMAIN}_static"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), cache_headers=False)]
        )
        hass.data[f"{DOMAIN}_static"] = True
    if PANEL_URL in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=NAME,
        sidebar_icon="mdi:heat-pump",
        module_url=f"{STATIC_URL}/home-heating-panel.js?v={VERSION}",
        require_admin=True,
        config={},
    )
