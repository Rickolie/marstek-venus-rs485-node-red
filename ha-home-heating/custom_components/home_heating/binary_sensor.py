"""Binary sensors: what the heat pump, ventilation and boiler should do, and the watchdog problem."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import HomeHeatingEntity

BINARY_SENSORS = [
    # key, name, value, device class, icon
    ("heating", "Heat pump heating", lambda d: (d.get("feedforward") or {}).get("demand") == "heat", None, "mdi:fire"),
    ("cooling", "Heat pump cooling", lambda d: (d.get("feedforward") or {}).get("demand") == "cool", None, "mdi:snowflake"),
    ("ventilation_boost", "Ventilation boost", lambda d: (d.get("ventilation") or {}).get("active"), None, "mdi:fan-plus"),
    ("boiler_element", "Boiler element should heat", lambda d: (d.get("boiler") or {}).get("element"), None, "mdi:water-boiler"),
    ("instant_boiler", "Doorstroomboiler should heat", lambda d: (d.get("boiler") or {}).get("instant"), None, "mdi:water-boiler-auto"),
    ("legionella", "Legionella run", lambda d: (d.get("boiler") or {}).get("legionella"), None, "mdi:bacteria"),
    ("problem", "Problem", lambda d: (d.get("watchdog") or {}).get("state", "ok") != "ok", BinarySensorDeviceClass.PROBLEM, None),
]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    controller = entry.runtime_data
    async_add_entities(HomeHeatingBinarySensor(controller, *b) for b in BINARY_SENSORS)


class HomeHeatingBinarySensor(HomeHeatingEntity, BinarySensorEntity):
    def __init__(self, controller, key, name, value, device_class, icon) -> None:
        super().__init__(controller, key, name)
        self._value = value
        self._attr_device_class = device_class
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        return bool(self._value(self.data)) if self.data else None

    @property
    def extra_state_attributes(self) -> dict:
        if self._attr_device_class == BinarySensorDeviceClass.PROBLEM and self.data:
            checks = (self.data.get("watchdog") or {}).get("checks") or {}
            return {"problems": [c["message"] for c in checks.values() if c["state"] != "ok"]}
        return {}
