"""Climate entities for Toshiba Air Conditioner."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    HVACMode,
    SWING_OFF,
    SWING_ON,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ToshibaRoom, _same_protocol_value
from .const import DOMAIN
from .coordinator import ToshibaDataUpdateCoordinator

_HA_MODE_MAP: dict[str, HVACMode] = {
    "auto": HVACMode.AUTO,
    "fan_only": HVACMode.FAN_ONLY,
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "dry": HVACMode.DRY,
}

_HA_FAN_MAP = {
    "auto": FAN_AUTO,
    "low": FAN_LOW,
    "medium": FAN_MEDIUM,
    "high": FAN_HIGH,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Toshiba climate entities."""
    coordinator: ToshibaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ToshibaClimateEntity(coordinator, room) for room in coordinator.rooms
    )


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_online(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "off", "offline"}


class ToshibaClimateEntity(CoordinatorEntity[ToshibaDataUpdateCoordinator], ClimateEntity):
    """A Toshiba indoor-unit room."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:air-conditioner"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self, coordinator: ToshibaDataUpdateCoordinator, room: ToshibaRoom
    ) -> None:
        super().__init__(coordinator)
        self.room = room
        self._attr_unique_id = room.unique_id
        self._attr_name = room.name
        self._mode_codes = {
            _HA_MODE_MAP[neutral]: code
            for neutral, code in room.mode_codes.items()
            if neutral in _HA_MODE_MAP
        }
        self._fan_codes = {
            _HA_FAN_MAP[mode]: code
            for mode, code in room.compact_fan_codes.items()
            if mode in _HA_FAN_MAP
        }
        self._attr_hvac_modes = [HVACMode.OFF, *self._mode_codes]
        self._attr_min_temp, self._attr_max_temp, self._attr_target_temperature_step = (
            room.temperature_limits
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.unique_id)},
            name=room.name,
            manufacturer="Toshiba Carrier",
            model=room.model or room.device_type or "Air Conditioner",
        )
        features = self._attr_supported_features
        if self._fan_codes:
            features |= ClimateEntityFeature.FAN_MODE
        if room.supports_vertical_swing:
            features |= ClimateEntityFeature.SWING_MODE
        if room.supports_horizontal_swing:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        self._attr_supported_features = features

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
    def current_temperature(self) -> float | None:
        """Return current room temperature."""
        return _as_float(self._state.get("T_room"))

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        return _as_float(self._state.get("T_set"))

    @property
    def fan_modes(self) -> list[str] | None:
        """Return fan modes allowed in the current HVAC mode."""
        if not self._fan_codes:
            return None
        modes = list(self._fan_codes)
        if self.hvac_mode == HVACMode.DRY:
            return modes[1:2]
        if (
            self.hvac_mode == HVACMode.FAN_ONLY
            and self.room.outer_factory == "Hangzhou"
        ):
            return modes[1:]
        return modes

    @property
    def fan_mode(self) -> str | None:
        """Return the currently reported fan mode."""
        raw_fan = self._state.get("I_fan")
        for fan_mode, code in self._fan_codes.items():
            if _same_protocol_value(raw_fan, code):
                return fan_mode
        return None

    @property
    def swing_modes(self) -> list[str] | None:
        """Return vertical swing modes when the model has a vertical louver."""
        return [SWING_OFF, SWING_ON] if self.room.supports_vertical_swing else None

    @property
    def swing_mode(self) -> str | None:
        """Return vertical swing state."""
        if not self.room.supports_vertical_swing:
            return None
        return SWING_ON if _same_protocol_value(self._state.get("ver"), 1) else SWING_OFF

    @property
    def swing_horizontal_modes(self) -> list[str] | None:
        """Return horizontal swing modes when the model has a horizontal louver."""
        return [SWING_OFF, SWING_ON] if self.room.supports_horizontal_swing else None

    @property
    def swing_horizontal_mode(self) -> str | None:
        """Return horizontal swing state."""
        if not self.room.supports_horizontal_swing:
            return None
        return SWING_ON if _same_protocol_value(self._state.get("hor"), 1) else SWING_OFF

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        on_code, off_code = self.room.power_codes
        power = self._state.get("I_onoff")
        if power is None or _same_protocol_value(power, off_code):
            return HVACMode.OFF
        if not _same_protocol_value(power, on_code) and power in (False, 0, "0"):
            return HVACMode.OFF
        raw_mode = self._state.get("I_mode")
        for mode, code in self._mode_codes.items():
            if _same_protocol_value(raw_mode, code):
                return mode
        return HVACMode.AUTO if HVACMode.AUTO in self._mode_codes else next(
            iter(self._mode_codes), HVACMode.OFF
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose useful identifiers and reported fault codes."""
        return {
            "house": self.room.house_name,
            "room_id": self.room.room_id,
            "gateway_sn": self.room.gateway_sn,
            "indoor_units": list(self.room.idu_names),
            "model": self.room.model,
            "vertical_swing_supported": self.room.supports_vertical_swing,
            "horizontal_swing_supported": self.room.supports_horizontal_swing,
            "error_code": self._state.get("error_code"),
            "protection_code": self._state.get("pro_code"),
        }

    async def async_turn_on(self) -> None:
        """Turn the room on."""
        on_code, _ = self.room.power_codes
        await self.coordinator.async_control(self.room, onOff=on_code)

    async def async_turn_off(self) -> None:
        """Turn the room off."""
        _, off_code = self.room.power_codes
        await self.coordinator.async_control(self.room, onOff=off_code)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode, turning the unit on when needed."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        if hvac_mode not in self._mode_codes:
            raise ValueError(f"Unsupported Toshiba HVAC mode: {hvac_mode}")
        on_code, _ = self.room.power_codes
        values: dict[str, Any] = {
            "onOff": on_code,
            "mode": self._mode_codes[hvac_mode],
        }
        if (
            hvac_mode in (HVACMode.DRY, HVACMode.FAN_ONLY)
            and len(self._fan_codes) > 1
        ):
            values["fan"] = next(
                code for label, code in self._fan_codes.items() if label != FAN_AUTO
            )
        await self.coordinator.async_control(self.room, **values)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set a model-supported fan speed."""
        if fan_mode not in (self.fan_modes or []):
            raise ValueError(f"Unsupported Toshiba fan mode: {fan_mode}")
        await self.coordinator.async_control(self.room, fan=self._fan_codes[fan_mode])

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set vertical swing using the Android app's model-aware field."""
        if swing_mode not in (self.swing_modes or []):
            raise ValueError(f"Unsupported Toshiba vertical swing mode: {swing_mode}")
        await self.coordinator.async_control(
            self.room, swing_lon=1 if swing_mode == SWING_ON else 3
        )

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set horizontal swing using the Android app's model-aware field."""
        if swing_horizontal_mode not in (self.swing_horizontal_modes or []):
            raise ValueError(
                f"Unsupported Toshiba horizontal swing mode: {swing_horizontal_mode}"
            )
        await self.coordinator.async_control(
            self.room, swing_lat=1 if swing_horizontal_mode == SWING_ON else 3
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature and optional HVAC mode."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        values: dict[str, Any] = {"temperature": float(temperature)}
        hvac_mode = kwargs.get("hvac_mode")
        if hvac_mode == HVACMode.OFF:
            _, off_code = self.room.power_codes
            values = {"onOff": off_code}
        elif hvac_mode in self._mode_codes:
            on_code, _ = self.room.power_codes
            values.update(onOff=on_code, mode=self._mode_codes[hvac_mode])
        await self.coordinator.async_control(self.room, **values)
