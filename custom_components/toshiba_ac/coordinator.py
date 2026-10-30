"""Data coordinator for Toshiba Air Conditioner."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ToshibaAcApi,
    ToshibaApiError,
    ToshibaAuthError,
    ToshibaRoom,
    _same_protocol_value,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)
_CONTROL_STATE_KEYS = {
    "onOff": "I_onoff",
    "mode": "I_mode",
    "temperature": "T_set",
    "fan": "I_fan",
    "swing_lon": "ver",
    "swing_lat": "hor",
}
_PENDING_CONTROL_TTL = 15.0


class ToshibaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll all gateways belonging to a Toshiba account."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ToshibaAcApi,
        rooms: list[ToshibaRoom],
    ) -> None:
        from datetime import timedelta

        scan_interval = int(
            entry.options.get(
                CONF_SCAN_INTERVAL,
                entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self.rooms = rooms
        self._pending_controls: dict[
            str, dict[str, tuple[Any, float]]
        ] = {}

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch and map gateway states to rooms."""
        states_by_gateway: dict[str, list[dict[str, Any]]] = {}
        try:
            for gateway_sn in dict.fromkeys(room.gateway_sn for room in self.rooms):
                states_by_gateway[gateway_sn] = await self.api.async_get_gateway_states(
                    gateway_sn
                )
        except ToshibaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ToshibaApiError as err:
            raise UpdateFailed(str(err)) from err

        result: dict[str, dict[str, Any]] = {}
        for room in self.rooms:
            gateway_states = states_by_gateway.get(room.gateway_sn, [])
            matches = [
                state
                for state in gateway_states
                if str(state.get("iduName") or state.get("iduSn")) in room.idu_names
            ]
            if not matches and len(gateway_states) == 1:
                matches = gateway_states
            if matches:
                # The app also represents a room with the first indoor-unit state.
                state = dict(matches[0])
                self._apply_pending_controls(room.unique_id, state)
                result[room.unique_id] = state
        return result

    def _apply_pending_controls(
        self, room_id: str, state: dict[str, Any]
    ) -> None:
        """Keep optimistic values until the cloud confirms or they expire."""
        pending = self._pending_controls.get(room_id)
        if not pending:
            return
        now = monotonic()
        remaining: dict[str, tuple[Any, float]] = {}
        for state_key, (expected, expires_at) in pending.items():
            if _same_protocol_value(state.get(state_key), expected):
                continue
            if now < expires_at:
                state[state_key] = expected
                remaining[state_key] = (expected, expires_at)
        if remaining:
            self._pending_controls[room_id] = remaining
        else:
            self._pending_controls.pop(room_id, None)

    def _set_optimistic_state(
        self, room: ToshibaRoom, values: dict[str, Any]
    ) -> None:
        """Publish successful controls to entities without cloud polling delay."""
        state_updates = {
            state_key: values[control_key]
            for control_key, state_key in _CONTROL_STATE_KEYS.items()
            if control_key in values and values[control_key] is not None
        }
        if not state_updates:
            return

        data = {
            room_id: dict(state)
            for room_id, state in (self.data or {}).items()
        }
        room_state = data.setdefault(room.unique_id, {})
        room_state.update(state_updates)

        expires_at = monotonic() + _PENDING_CONTROL_TTL
        pending = self._pending_controls.setdefault(room.unique_id, {})
        pending.update(
            {
                state_key: (value, expires_at)
                for state_key, value in state_updates.items()
            }
        )
        self.async_set_updated_data(data)

    async def async_control(self, room: ToshibaRoom, **values: Any) -> None:
        """Send a command, publish it locally, then refresh in the background."""
        try:
            await self.api.async_control(room, **values)
        except ToshibaAuthError as err:
            raise ConfigEntryAuthFailed from err
        self._set_optimistic_state(room, values)
        self.hass.async_create_task(self.async_request_refresh())
