"""Base entity: one Home Heating device, updated after every run of the controller."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME, SIGNAL_UPDATE
from .controller import Controller


class HomeHeatingEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: Controller, key: str, name: str) -> None:
        self.controller = controller
        self._attr_unique_id = f"{controller.entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, controller.entry.entry_id)},
            name=NAME,
            manufacturer="Home Heating",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def data(self) -> dict:
        return self.controller.data

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self.async_write_ha_state))
