"""Sensor platform for DKN Cloud NA (exterior temperature)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import DknCloudApi
from .const import DOMAIN, PROP_EXT_TEMP

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DKN Cloud NA sensor entities."""
    api: DknCloudApi = hass.data[DOMAIN][entry.entry_id]

    entities = [
        DknExteriorTemperatureSensor(api, mac, device_info)
        for mac, device_info in api.devices.items()
    ]

    async_add_entities(entities)


class DknExteriorTemperatureSensor(SensorEntity):
    """Exterior temperature sensor for a DKN device."""

    _attr_has_entity_name = True
    _attr_name = "Exterior temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        api: DknCloudApi,
        mac: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor entity."""
        self._api = api
        self._mac = mac
        self._device_info = device_info
        self._attr_unique_id = f"dkncloudna_{mac}_ext_temp"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, mac)},
            "name": device_info.get("name", f"DKN {mac}"),
            "manufacturer": "Daikin",
            "model": "DKN Cloud NA",
        }
        self._unregister_callback: Any = None

    @property
    def _data(self) -> dict[str, Any]:
        """Get current device data."""
        return self._device_info.get("data", {})

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._mac in self._api.devices

    @property
    def native_value(self) -> float | None:
        """Return the exterior temperature."""
        temp = self._data.get(PROP_EXT_TEMP)
        if temp is None:
            return None
        # Units come from installation level, stored on device_info
        units = self._device_info.get("units", 0)
        if units == 1:  # Fahrenheit
            return round(((temp - 32) * 5 / 9), 1)
        return temp

    async def async_added_to_hass(self) -> None:
        """Register for device updates when added to hass."""

        @callback
        def _handle_update(mac: str | None) -> None:
            if mac is None or mac == self._mac:
                self.async_write_ha_state()

        self._unregister_callback = self._api.register_device_callback(
            _handle_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unregister callbacks when removed."""
        if self._unregister_callback:
            self._unregister_callback()
