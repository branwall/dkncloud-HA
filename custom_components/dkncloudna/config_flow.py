"""Config flow for DKN Cloud NA integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant

from .api import DknAuthError, DknCloudApi, DknConnectionError
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, str]:
    """Validate the user input allows us to connect."""
    api = DknCloudApi(
        email=data[CONF_EMAIL],
        password=data[CONF_PASSWORD],
    )
    await api.test_connection()
    return {"title": f"DKN Cloud NA ({data[CONF_EMAIL]})"}


class DknCloudNaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DKN Cloud NA."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except DknAuthError:
                errors["base"] = "invalid_auth"
            except DknConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=info["title"], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )
