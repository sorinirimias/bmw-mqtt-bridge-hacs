"""Constants for the BMW CarData integration."""

from __future__ import annotations

DOMAIN = "bmw_cardata_mqtt"

# Config entry keys
CONF_CLIENT_ID = "client_id"
CONF_GCID = "gcid"
CONF_REFRESH_TOKEN = "refresh_token"

# BMW OAuth2 device-authorization-grant endpoints
OAUTH_DEVICE_CODE_URL = "https://customer.bmwgroup.com/gcdm/oauth/device/code"
OAUTH_TOKEN_URL = "https://customer.bmwgroup.com/gcdm/oauth/token"
OAUTH_SCOPES = "authenticate_user openid cardata:streaming:read"

# BMW CarData streaming MQTT broker
BMW_STREAM_HOST = "customer.streaming-cardata.bmwgroup.com"
BMW_STREAM_PORT = 9000

# Token refresh timing (seconds)
TOKEN_REFRESH_MARGIN = 600  # refresh 10 minutes before the id_token expires
TOKEN_REFRESH_MIN_DELAY = 60

# Dispatcher signals
SIGNAL_NEW_SIGNAL = f"{DOMAIN}_new_signal"


def signal_update(vin: str, prop: str) -> str:
    """Return the per-signal dispatcher topic used to push state updates."""
    return f"{DOMAIN}_update_{vin}_{prop}"
