"""Websocket API for the sidebar page: settings and live status."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import settings as settings_mod
from .const import DOMAIN


def _entry(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0] if entries else None


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_config)
    websocket_api.async_register_command(hass, ws_save)
    websocket_api.async_register_command(hass, ws_status)
    websocket_api.async_register_command(hass, ws_action)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@websocket_api.require_admin
@callback
def ws_config(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """The settings schema and the current settings."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Home Heating is not set up")
        return
    connection.send_result(msg["id"], {
        "sections": settings_mod.SECTIONS,
        "settings": settings_mod.merged(entry.options),
    })


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/save", vol.Required("settings"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Check and store the settings. The controller picks them up directly."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Home Heating is not set up")
        return
    try:
        changes = settings_mod.validate(msg["settings"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    options = {**settings_mod.merged(entry.options), **changes}
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"settings": options})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/status"})
@websocket_api.require_admin
@callback
def ws_status(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    connection.send_result(msg["id"], entry.runtime_data.status() if entry else {})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/action",
    vol.Required("action"): vol.In(["recalculate_history", "refresh_forecast", "start_legionella", "plan_prices"]),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_action(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Home Heating is not set up")
        return
    c = entry.runtime_data
    await {
        "recalculate_history": c.async_recalculate_history,
        "refresh_forecast": c.async_refresh_forecast,
        "start_legionella": c.async_start_legionella,
        "plan_prices": c.async_plan_prices,
    }[msg["action"]]()
    connection.send_result(msg["id"], c.status())
