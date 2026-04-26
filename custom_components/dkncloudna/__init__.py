"""The DKN Cloud NA integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import DknAuthError, DknCloudApi, DknConnectionError
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DKN Cloud NA from a config entry."""
    api = DknCloudApi(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
    )

    try:
        await api.authenticate()
        await api.get_installations()
        await api.connect_socket()
    except DknAuthError as err:
        await api.disconnect()
        _LOGGER.error("DKN Cloud NA authentication failed: %s", err)
        raise ConfigEntryAuthFailed(str(err)) from err
    except DknConnectionError as err:
        await api.disconnect()
        _LOGGER.warning("DKN Cloud NA not reachable, will retry: %s", err)
        raise ConfigEntryNotReady(str(err)) from err

    api.start_supervisor()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = api

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        api: DknCloudApi = hass.data[DOMAIN].pop(entry.entry_id)
        await api.disconnect()

    return unload_ok
