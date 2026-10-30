"""Config flow for Toshiba Air Conditioner."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import selector

from .api import ToshibaAcApi, ToshibaApiError, ToshibaAuthError, ToshibaConnectionError
from .const import CONF_PHONE, DOMAIN


class ToshibaAcConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Toshiba Air Conditioner configuration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = str(user_input[CONF_PHONE]).strip()
            password = str(user_input[CONF_PASSWORD])
            api = ToshibaAcApi(async_get_clientsession(self.hass), phone, password)
            try:
                rooms = await api.async_discover()
            except ToshibaAuthError:
                errors["base"] = "invalid_auth"
            except ToshibaConnectionError:
                errors["base"] = "cannot_connect"
            except ToshibaApiError:
                errors["base"] = "unknown"
            else:
                if not rooms:
                    errors["base"] = "no_devices"
                else:
                    await self.async_set_unique_id(phone.lower())
                    self._abort_if_unique_id_configured()
                    title = f"东芝空调 ({phone})"
                    return self.async_create_entry(
                        title=title,
                        data={CONF_PHONE: phone, CONF_PASSWORD: password},
                    )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PHONE,
                    default=(user_input or {}).get(CONF_PHONE, ""),
                ): selector({"text": {"type": "text"}}),
                vol.Required(CONF_PASSWORD): selector({"text": {"type": "password"}}),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start credential renewal."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate and store renewed credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = str(user_input[CONF_PASSWORD])
            api = ToshibaAcApi(
                async_get_clientsession(self.hass), entry.data[CONF_PHONE], password
            )
            try:
                await api.async_discover()
            except ToshibaAuthError:
                errors["base"] = "invalid_auth"
            except ToshibaConnectionError:
                errors["base"] = "cannot_connect"
            except ToshibaApiError:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): selector(
                        {"text": {"type": "password"}}
                    )
                }
            ),
            errors=errors,
        )
