"""Climate platform for DKN Cloud NA."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import DknCloudApi
from .const import (
    DOMAIN,
    FAN_SPEED_MAP,
    FAN_SPEED_REVERSE_MAP,
    MODE_COOL,
    MODE_DRY,
    MODE_FAN,
    MODE_HEAT,
    MODE_HEAT_COOL,
    PROP_MODE,
    PROP_POWER,
    PROP_REAL_MODE,
    PROP_SETPOINT_AUTO,
    PROP_SETPOINT_COOL,
    PROP_SETPOINT_HEAT,
    PROP_SPEED_STATE,
    PROP_WORK_TEMP,
    SPEED_AUTO,
)

_LOGGER = logging.getLogger(__name__)

# Map device mode values to HA HVAC modes
HVAC_MODE_MAP = {
    MODE_HEAT_COOL: HVACMode.HEAT_COOL,
    MODE_COOL: HVACMode.COOL,
    MODE_HEAT: HVACMode.HEAT,
    MODE_FAN: HVACMode.FAN_ONLY,
    MODE_DRY: HVACMode.DRY,
}

HVAC_MODE_REVERSE_MAP = {v: k for k, v in HVAC_MODE_MAP.items()}

SETPOINT_PROP_MAP = {
    MODE_HEAT_COOL: PROP_SETPOINT_AUTO,
    MODE_COOL: PROP_SETPOINT_COOL,
    MODE_HEAT: PROP_SETPOINT_HEAT,
}

HVAC_ACTION_MAP = {
    MODE_COOL: HVACAction.COOLING,
    MODE_HEAT: HVACAction.HEATING,
    MODE_FAN: HVACAction.FAN,
    MODE_DRY: HVACAction.DRYING,
    MODE_HEAT_COOL: HVACAction.IDLE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DKN Cloud NA climate entities."""
    api: DknCloudApi = hass.data[DOMAIN][entry.entry_id]

    entities = [
        DknClimateEntity(api, mac, device_info)
        for mac, device_info in api.devices.items()
    ]

    async_add_entities(entities)


class DknClimateEntity(ClimateEntity):
    """Representation of a DKN Cloud NA HVAC unit."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = 0.5
    _attr_target_temperature_step = 1.0
    _attr_min_temp = 16
    _attr_max_temp = 30
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT_COOL,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.FAN_ONLY,
        HVACMode.DRY,
    ]
    _attr_fan_modes = list(FAN_SPEED_MAP.values())
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        api: DknCloudApi,
        mac: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the climate entity."""
        self._api = api
        self._mac = mac
        self._device_info = device_info
        self._attr_unique_id = f"dkncloudna_{mac}"
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
        return self._mac in self._api.devices and self._api.is_socket_healthy

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        if not self._data.get(PROP_POWER):
            return HVACMode.OFF
        mode = self._data.get(PROP_MODE)
        return HVAC_MODE_MAP.get(mode, HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running HVAC action."""
        if not self._data.get(PROP_POWER):
            return HVACAction.OFF
        real_mode = self._data.get(PROP_REAL_MODE)
        return HVAC_ACTION_MAP.get(real_mode, HVACAction.IDLE)

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        temp = self._data.get(PROP_WORK_TEMP)
        if temp is not None:
            return self._to_celsius(temp)
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        mode = self._data.get(PROP_MODE)
        prop = SETPOINT_PROP_MAP.get(mode, PROP_SETPOINT_AUTO)
        temp = self._data.get(prop)
        if temp is not None:
            return self._to_celsius(temp)
        return None

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        speed_state = self._data.get(PROP_SPEED_STATE)
        return FAN_SPEED_MAP.get(speed_state, "auto")

    @property
    def _units(self) -> int:
        """Return the temperature unit setting (0=Celsius, 1=Fahrenheit)."""
        return self._device_info.get("units", 0)

    def _to_celsius(self, value: float) -> float:
        """Convert temperature to Celsius if device reports Fahrenheit."""
        if self._units == 1:  # Fahrenheit
            return round(((value - 32) * 5 / 9), 1)
        return value

    def _to_device_temp(self, celsius: float) -> float:
        """Convert Celsius to device units if needed."""
        if self._units == 1:  # Fahrenheit
            return round((celsius * 9 / 5) + 32)
        return celsius

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._api.send_command(self._mac, PROP_POWER, False)
            return

        device_mode = HVAC_MODE_REVERSE_MAP.get(hvac_mode)
        if device_mode is None:
            return

        if not self._data.get(PROP_POWER):
            await self._api.send_command(self._mac, PROP_POWER, True)

        await self._api.send_command(self._mac, PROP_MODE, device_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        mode = self._data.get(PROP_MODE)
        prop = SETPOINT_PROP_MAP.get(mode, PROP_SETPOINT_AUTO)
        device_temp = self._to_device_temp(temperature)
        await self._api.send_command(self._mac, prop, device_temp)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new fan mode."""
        speed_state = FAN_SPEED_REVERSE_MAP.get(fan_mode, SPEED_AUTO)
        await self._api.send_command(self._mac, PROP_SPEED_STATE, speed_state)

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        await self._api.send_command(self._mac, PROP_POWER, True)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self._api.send_command(self._mac, PROP_POWER, False)

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
