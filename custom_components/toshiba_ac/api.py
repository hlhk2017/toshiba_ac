"""Cloud API used by the Toshiba Air Conditioner Android application."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import logging
import math
import os
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    APP_VERSION,
    BRAND_HEADER,
    CONTROL_URL,
    DEVICE_URL,
    HOUSE_URL,
    LOGIN_URL,
    MODEL_MODE_KEYS,
    PASSWORD_PASSPHRASE,
    STATE_URL,
    TOKEN_ERROR_CODES,
)

_LOGGER = logging.getLogger(__name__)
_REQUEST_TIMEOUT = ClientTimeout(total=15)


class ToshibaApiError(Exception):
    """Base exception for Toshiba cloud errors."""


class ToshibaAuthError(ToshibaApiError):
    """Raised when Toshiba cloud credentials are rejected."""


class ToshibaConnectionError(ToshibaApiError):
    """Raised when the Toshiba cloud cannot be reached."""


@dataclass(frozen=True, slots=True)
class ToshibaHouse:
    """A house returned by the Toshiba cloud."""

    house_id: str
    name: str


@dataclass(slots=True)
class ToshibaRoom:
    """An indoor-unit room returned by the Toshiba cloud."""

    house_id: str
    house_name: str
    room_id: str
    name: str
    gateway_sn: str
    gateway_id: str | None
    idu_names: tuple[str, ...]
    model_info: dict[str, Any] = field(default_factory=dict)
    extra_info: dict[str, Any] = field(default_factory=dict)
    device_type: str | None = None
    outer_factory: str | None = None
    model: str | None = None

    @property
    def unique_id(self) -> str:
        """Return a stable unique ID for Home Assistant."""
        return f"{self.gateway_sn}_{self.room_id}"

    @property
    def device_sn(self) -> str:
        """Return the indoor unit target used by controlShadow."""
        return ",".join(self.idu_names)

    @property
    def mode_codes(self) -> dict[str, Any]:
        """Return supported neutral HVAC modes and their protocol values."""
        setting_model = self.model_info.get("settingModel")
        if not isinstance(setting_model, dict):
            return {}
        result: dict[str, int | float] = {}
        for key, mode in MODEL_MODE_KEYS.items():
            value = _protocol_number(setting_model.get(key))
            if value is not None:
                result[mode] = value
        return result

    @property
    def power_codes(self) -> tuple[Any, Any]:
        """Return the model-specific on and off protocol values."""
        model = self.model_info.get("onOffModel")
        if not isinstance(model, dict):
            return 1, 0
        on_code = _protocol_number(model.get("on"))
        off_code = _protocol_number(model.get("off"))
        return (
            on_code if on_code is not None else 1,
            off_code if off_code is not None else 0,
        )

    @property
    def fan_codes(self) -> dict[str, int]:
        """Return fan labels and direct protocol values for this model."""
        model = self.model_info.get("fanSpeedModel")
        if not isinstance(model, dict):
            return {}
        minimum = int(_first_number(model, "min_value", "minValue", default=1))
        maximum = int(_first_number(model, "max_value", "maxValue", default=0))
        step = int(_first_number(model, "step", default=1))
        if step <= 0 or minimum < 0 or maximum < minimum or maximum - minimum > 10:
            return {}
        result = {"auto": 0}
        result.update(
            {
                f"{value}速": value
                for value in range(minimum, maximum + 1, step)
            }
        )
        return result

    @property
    def compact_fan_codes(self) -> dict[str, int]:
        """Return the four fan modes shown on the climate entity."""
        full_codes = self.fan_codes
        return {
            mode: full_codes[label]
            for mode, label in (
                ("auto", "auto"),
                ("low", "1速"),
                ("medium", "3速"),
                ("high", "5速"),
            )
            if label in full_codes
        }

    @property
    def supports_vertical_swing(self) -> bool:
        """Return whether the model advertises a vertical louver."""
        return _capability_enabled(self.extra_info.get("louver_V"))

    @property
    def supports_horizontal_swing(self) -> bool:
        """Return whether the model advertises a horizontal louver."""
        return _capability_enabled(self.extra_info.get("louver_H"))

    @property
    def temperature_limits(self) -> tuple[float, float, float]:
        """Return min, max and step from available model metadata."""
        model = self.model_info.get("temperatureModel")
        if not isinstance(model, dict):
            return 16.0, 30.0, 1.0
        minimum = _first_number(model, "min_value", "minValue", "min", default=16)
        maximum = _first_number(model, "max_value", "maxValue", "max", default=30)
        step = _first_number(model, "step", "step_value", "stepValue", default=1)
        if minimum >= maximum:
            return 16.0, 30.0, 1.0
        return minimum, maximum, step if step in (0.5, 1.0) else 1.0


def _first_number(data: dict[str, Any], *keys: str, default: float) -> float:
    for key in keys:
        value = _as_float(data.get(key))
        if value is not None:
            return value
    return float(default)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _protocol_number(value: Any) -> int | float | None:
    """Normalize numeric strings returned in model metadata."""
    number = _as_float(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


def _capability_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    number = _as_float(value)
    if number is not None:
        return number > 0
    return str(value).strip().lower() in {"true", "on", "yes"}


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _evp_bytes_to_key(password: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive CryptoJS/OpenSSL AES-256-CBC key and IV."""
    derived = b""
    block = b""
    while len(derived) < 48:
        block = hashlib.md5(
            block + password + salt, usedforsecurity=False
        ).digest()
        derived += block
    return derived[:32], derived[32:48]


def encrypt_password(password: str, *, salt: bytes | None = None) -> str:
    """Encrypt a password exactly like CryptoJS.AES.encrypt(passphrase)."""
    salt = os.urandom(8) if salt is None else salt
    if len(salt) != 8:
        raise ValueError("CryptoJS salt must be exactly 8 bytes")
    key, iv = _evp_bytes_to_key(PASSWORD_PASSPHRASE, salt)
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(password.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(b"Salted__" + salt + encrypted).decode("ascii")


def _gql_value(value: Any) -> str:
    """Serialize a flat Python value as a GraphQL literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite GraphQL number")
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_gql_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _same_protocol_value(left: Any, right: Any) -> bool:
    """Compare protocol values despite JSON number/string differences."""
    if left is None or right is None:
        return left is right
    return str(left).strip() == str(right).strip()


class ToshibaAcApi:
    """Async client for the cloud protocol embedded in app version 4.8.1."""

    def __init__(self, session: ClientSession, phone: str, password: str) -> None:
        self._session = session
        self.phone = phone.strip()
        self.password = password
        self.token: str | None = None

    async def async_login(self) -> str:
        """Authenticate and cache a bearer token."""
        query = (
            "mutation{appPwdLogin(reqLoginDto:{"
            f"needEncrypt:true,phonenumber:{_gql_value(self.phone)},"
            f"password:{_gql_value(encrypt_password(self.password))},"
            f"appVersion:{_gql_value(APP_VERSION)},os:\"android\","
            "osVersion:\"13\",code:\"\",registrationId:\"\""
            "}){token}}"
        )
        try:
            data = await self._async_graphql(
                LOGIN_URL, query, authenticated=False, retry_auth=False
            )
        except ToshibaConnectionError:
            raise
        except ToshibaApiError as err:
            raise ToshibaAuthError(str(err)) from err

        result = data.get("appPwdLogin")
        token = result.get("token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise ToshibaAuthError("The Toshiba cloud did not return a login token")
        self.token = token
        return token

    async def async_get_houses(self) -> list[ToshibaHouse]:
        """Return houses accessible to the authenticated account."""
        query = "query{appHouseIdList{houseInfo{houseId houseName}}}"
        data = await self._async_graphql(HOUSE_URL, query)
        account = data.get("appHouseIdList")
        raw_houses = account.get("houseInfo", []) if isinstance(account, dict) else []
        houses: list[ToshibaHouse] = []
        for raw in raw_houses or []:
            if not isinstance(raw, dict) or raw.get("houseId") is None:
                continue
            house_id = str(raw["houseId"])
            houses.append(
                ToshibaHouse(house_id, str(raw.get("houseName") or f"家庭 {house_id}"))
            )
        return houses

    async def async_get_rooms(self, house: ToshibaHouse) -> list[ToshibaRoom]:
        """Return controllable rooms in a house."""
        try:
            house_id = int(house.house_id)
        except ValueError as err:
            raise ToshibaApiError(f"Invalid house ID: {house.house_id}") from err
        query = f"""query{{appRoomIduList_v7(houseId:{house_id}){{
          iduNames gatewayId snCode roomName roomId orderNum iduState outerFactory
          modelInfoStr extraInfoStr isFloorHeat extendInfoStr deviceType isTianfuDishui
          iduList{{iduName deviceType}}
          deviceFeatureList{{iduId iduName deviceType sModule iModel iduState gatewayId
            snCode modelInfoStr extraInfoStr extendInfoStr}}
        }}}}"""
        data = await self._async_graphql(DEVICE_URL, query)
        raw_rooms = data.get("appRoomIduList_v7")
        if not isinstance(raw_rooms, list):
            return []
        rooms: list[ToshibaRoom] = []
        for raw in raw_rooms:
            room = self._parse_room(house, raw)
            if room is not None:
                rooms.append(room)
        return rooms

    async def async_get_gateway_states(self, gateway_sn: str) -> list[dict[str, Any]]:
        """Return all indoor-unit state records for a gateway."""
        query = f"""query{{iduStateQueryV2(gatewaySn:{_gql_value(gateway_sn)}){{
          gatewaySn iduSn AuxFun__eco AuxFun__elec AuxFun__force AuxFun__light
          AuxFun__silent AuxFun__sleep AuxFun__swingi AuxFun__swingo AuxFun__lock_M
          AuxFun__lock_R O_Ts F_def O_mute O_Away gatewayId I_fan I_mode I_onoff
          iduName T_room T_set ver hor I_flt_cur I_flt_all hrv_exchage hrv_air
          hrv_state fresh RH pro_code error_code oduSn is_online T4 O_mode O_elec
          T_wOut extraInfo
        }}}}"""
        data = await self._async_graphql(STATE_URL, query)
        states = data.get("iduStateQueryV2")
        if isinstance(states, dict):
            return [states]
        if not isinstance(states, list):
            return []
        return [state for state in states if isinstance(state, dict)]

    async def async_control(self, room: ToshibaRoom, **values: Any) -> Any:
        """Send a controlShadow mutation for a room."""
        if not room.device_sn:
            raise ToshibaApiError(f"Room {room.name} has no indoor unit name")
        params: dict[str, Any] = {
            "gatewaySn": room.gateway_sn,
            "deviceSn": room.device_sn,
            **values,
        }
        serialized = "\n".join(
            f"{key}:{_gql_value(value)}"
            for key, value in params.items()
            if value is not None
        )
        query = f"mutation{{controlShadow(param:{{{serialized}}})}}"
        data = await self._async_graphql(CONTROL_URL, query)
        result = data.get("controlShadow")
        if result is False:
            raise ToshibaApiError("The Toshiba cloud rejected the control command")
        return result

    async def async_discover(self) -> list[ToshibaRoom]:
        """Login and return every room visible to the account."""
        await self.async_login()
        houses = await self.async_get_houses()
        rooms: list[ToshibaRoom] = []
        for house in houses:
            rooms.extend(await self.async_get_rooms(house))
        return rooms

    async def _async_graphql(
        self,
        url: str,
        query: str,
        *,
        authenticated: bool = True,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        """Execute the request format used by the Android app."""
        if authenticated and not self.token:
            await self.async_login()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-brand": BRAND_HEADER,
        }
        if authenticated and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = {"operationName": None, "variables": {}, "query": query}

        try:
            async with self._session.post(
                url, json=body, headers=headers, timeout=_REQUEST_TIMEOUT
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except (TypeError, ValueError) as err:
                    text = (await response.text())[:200]
                    raise ToshibaApiError(
                        f"Toshiba cloud returned invalid JSON ({response.status}): {text}"
                    ) from err
                status = response.status
        except ToshibaApiError:
            raise
        except (ClientError, TimeoutError) as err:
            raise ToshibaConnectionError(f"Unable to reach Toshiba cloud: {err}") from err

        if not isinstance(payload, dict):
            raise ToshibaApiError("Toshiba cloud returned an invalid response")
        errors = payload.get("errors")
        extensions = payload.get("extensions")
        if isinstance(extensions, dict):
            errors = extensions.get("errors") or errors
        if errors:
            first = errors[0] if isinstance(errors, list) else errors
            code = first.get("code") if isinstance(first, dict) else None
            message = first.get("message") if isinstance(first, dict) else str(first)
            if authenticated and code in TOKEN_ERROR_CODES and retry_auth:
                _LOGGER.debug("Toshiba token expired; logging in again")
                self.token = None
                await self.async_login()
                return await self._async_graphql(
                    url, query, authenticated=True, retry_auth=False
                )
            if authenticated and code in TOKEN_ERROR_CODES:
                raise ToshibaAuthError(message or str(code))
            raise ToshibaApiError(message or f"Toshiba cloud error: {code or status}")
        if status >= 400:
            raise ToshibaApiError(f"Toshiba cloud returned HTTP {status}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ToshibaApiError("Toshiba cloud response has no data")
        return data

    @staticmethod
    def _parse_room(house: ToshibaHouse, raw: Any) -> ToshibaRoom | None:
        if not isinstance(raw, dict):
            return None
        gateway_sn = raw.get("snCode")
        room_id = raw.get("roomId")
        if gateway_sn is None or room_id is None:
            return None

        names = raw.get("iduNames")
        if isinstance(names, str):
            idu_names = tuple(name.strip() for name in names.split(",") if name.strip())
        elif isinstance(names, list):
            idu_names = tuple(str(name) for name in names if name is not None)
        else:
            idu_names = ()
        if not idu_names and isinstance(raw.get("iduList"), list):
            idu_names = tuple(
                str(item["iduName"])
                for item in raw["iduList"]
                if isinstance(item, dict) and item.get("iduName") is not None
            )

        model_info = _parse_json_object(raw.get("modelInfoStr"))
        extra_info = _parse_json_object(raw.get("extraInfoStr"))
        features = raw.get("deviceFeatureList")
        first_feature = features[0] if isinstance(features, list) and features else {}
        if not model_info and isinstance(first_feature, dict):
            model_info = _parse_json_object(first_feature.get("modelInfoStr"))
        model = None
        if isinstance(first_feature, dict):
            model = first_feature.get("sModule") or first_feature.get("iModel")
        model = model or extra_info.get("I_mdl")

        room_id_str = str(room_id)
        return ToshibaRoom(
            house_id=house.house_id,
            house_name=house.name,
            room_id=room_id_str,
            name=str(raw.get("roomName") or f"房间 {room_id_str}"),
            gateway_sn=str(gateway_sn),
            gateway_id=str(raw["gatewayId"]) if raw.get("gatewayId") is not None else None,
            idu_names=idu_names,
            model_info=model_info,
            extra_info=extra_info,
            device_type=str(raw["deviceType"]) if raw.get("deviceType") is not None else None,
            outer_factory=str(raw["outerFactory"]) if raw.get("outerFactory") else None,
            model=str(model).strip() if model else None,
        )


__all__ = [
    "ToshibaAcApi",
    "ToshibaApiError",
    "ToshibaAuthError",
    "ToshibaConnectionError",
    "ToshibaHouse",
    "ToshibaRoom",
    "_same_protocol_value",
    "encrypt_password",
]
