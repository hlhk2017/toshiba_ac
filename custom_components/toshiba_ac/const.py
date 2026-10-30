"""Constants for the Toshiba Air Conditioner integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "toshiba_ac"
PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SELECT]

CONF_PHONE = "phone"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 300
UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

APP_VERSION = "4.8.1"
BRAND_HEADER = "toshiba"
PASSWORD_PASSPHRASE = b"Vca:n!/=1laFSim/"

LOGIN_URL = "https://user.rlc.digital.carrier.cn/user-login"
HOUSE_URL = "https://property.rlc.digital.carrier.cn/graphql/graphql"
DEVICE_URL = "https://device.rlc.digital.carrier.cn/device-lite"
STATE_URL = "https://state.rlc.digital.carrier.cn/graphql/graphql"
CONTROL_URL = "https://telemetry.rlc.digital.carrier.cn/graphql/graphql"

TOKEN_ERROR_CODES = {"TOKEN_EXPIRED", "TOKEN_INVALID"}

# Keys used by modelInfoStr. Values are neutral names so api.py remains
# independent of Home Assistant's climate platform.
MODEL_MODE_KEYS: dict[str, str] = {
    "auto": "auto",
    "fan": "fan_only",
    "cool": "cool",
    "heat": "heat",
    "dehumidify": "dry",
}
