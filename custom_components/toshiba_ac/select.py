"""Select entities for Toshiba Air Conditioner."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ToshibaRoom, _same_protocol_value
from .const import DOMAIN
from .coordinator import ToshibaDataUpdateCoordinator

_OPTION_LABELS = {"auto": "自动"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up full fan-speed select entities."""
    coordinator: ToshibaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ToshibaFanSpeedSelect(coordinator, room)
        for room in coordinator.rooms
        if room.fan_codes
    )


def _is_online(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "off", "offline"}


class ToshibaFanSpeedSelect(
    CoordinatorEntity[ToshibaDataUpdateCoordinator], SelectEntity
):
    """Expose every fan speed advertised by an indoor-unit model."""

    _attr_has_entity_name = True
    _attr_name = "风速档位"
    _attr_icon = "mdi:fan-chevron-up"

    def __init__(
        self, coordinator: ToshibaDataUpdateCoordinator, room: ToshibaRoom
    ) -> None:
        super().__init__(coordinator)
        self.room = room
        self._attr_unique_id = f"{room.unique_id}_fan_speed"
        self._option_codes = {
            _OPTION_LABELS.get(label, label): code
            for label, code in room.fan_codes.items()
        }
        self._attr_options = list(self._option_codes)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.unique_id)},
            name=room.name,
            manufacturer="Toshiba Carrier",
            model=room.model or room.device_type or "Air Conditioner",
        )

    @property
    def _state(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.room.unique_id, {})

    @property
    def available(self) -> bool:
        """Return whether this indoor unit is available."""
        return (
            super().available
            and bool(self._state)
            and _is_online(self._state.get("is_online"))
        )

    @property
    def current_option(self) -> str | None:
        """Return the full fan speed currently reported by the cloud."""
        raw_fan = self._state.get("I_fan")
        for option, code in self._option_codes.items():
            if _same_protocol_value(raw_fan, code):
                return option
        return None

    async def async_select_option(self, option: str) -> None:
        """Set a full model-supported fan speed."""
        if option not in self._option_codes:
            raise ValueError(f"Unsupported Toshiba fan speed: {option}")
        await self.coordinator.async_control(
            self.room, fan=self._option_codes[option]
        )
