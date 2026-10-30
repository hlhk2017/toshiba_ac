"""The Toshiba Air Conditioner integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ToshibaAcApi, ToshibaApiError, ToshibaAuthError, ToshibaConnectionError
from .const import CONF_PHONE, DOMAIN, PLATFORMS
from .coordinator import ToshibaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Toshiba Air Conditioner from a config entry."""
    api = ToshibaAcApi(
        async_get_clientsession(hass),
        entry.data[CONF_PHONE],
        entry.data[CONF_PASSWORD],
    )
    try:
        rooms = await api.async_discover()
    except ToshibaAuthError as err:
        raise ConfigEntryAuthFailed from err
    except ToshibaConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    except ToshibaApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    for room in rooms:
        _LOGGER.info(
            "Toshiba AC discovered: room=%s, model=%s, device_type=%s, "
            "factory=%s, fan_modes=%s, vertical_swing=%s, horizontal_swing=%s",
            room.name,
            room.model or "unknown",
            room.device_type or "unknown",
            room.outer_factory or "unknown",
            list(room.fan_codes),
            room.supports_vertical_swing,
            room.supports_horizontal_swing,
        )

    coordinator = ToshibaDataUpdateCoordinator(hass, entry, api, rooms)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Toshiba Air Conditioner config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
