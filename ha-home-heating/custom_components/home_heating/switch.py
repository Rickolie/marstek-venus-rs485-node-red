"""Switches for the main settings, so they can also be used in dashboards and automations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import settings as settings_mod
from .const import SWITCHES
from .entity import HomeHeatingEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data
    async_add_entities(SettingSwitch(controller, key, name, icon) for key, (name, icon) in SWITCHES.items())


class SettingSwitch(HomeHeatingEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller, key: str, name: str, icon: str) -> None:
        super().__init__(controller, f"setting_{key}", name)
        self.key = key
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        return bool(self.controller.s.get(self.key))

    async def _set(self, value: bool) -> None:
        entry = self.controller.entry
        options = {**settings_mod.merged(entry.options), self.key: value}
        self.hass.config_entries.async_update_entry(entry, options=options)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
