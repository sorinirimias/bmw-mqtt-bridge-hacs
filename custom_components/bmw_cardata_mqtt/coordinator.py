"""Runtime coordinator: BMW CarData MQTT streaming + token lifecycle."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import paho.mqtt.client as mqtt

from homeassistant.components import mqtt as ha_mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .bmw_auth import (
    BmwAuthError,
    BmwAuthExpired,
    async_refresh_tokens,
    jwt_exp,
)
from .const import (
    BMW_STREAM_HOST,
    BMW_STREAM_PORT,
    CONF_CLIENT_ID,
    CONF_GCID,
    CONF_MQTT_PREFIX,
    CONF_REFRESH_TOKEN,
    CONF_REPUBLISH,
    DEFAULT_MQTT_PREFIX,
    SIGNAL_NEW_SIGNAL,
    TOKEN_REFRESH_MARGIN,
    TOKEN_REFRESH_MIN_DELAY,
    signal_update,
)

_LOGGER = logging.getLogger(__name__)


def _build_client(client_id: str) -> mqtt.Client:
    """Create a paho MQTT v5 client, supporting paho 1.x and 2.x APIs."""
    try:
        return mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv5,
        )
    except (AttributeError, TypeError):
        # paho-mqtt < 2.0
        return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv5)


class BmwCarDataRuntime:
    """Manages the BMW streaming connection and exposes the latest signals."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the runtime from a config entry."""
        self.hass = hass
        self.entry = entry
        self.client_id: str = entry.data[CONF_CLIENT_ID]
        self.gcid: str = entry.data[CONF_GCID]
        self.refresh_token: str = entry.data[CONF_REFRESH_TOKEN]

        # Optional republishing of BMW signals to the Home Assistant MQTT broker.
        self.republish: bool = entry.options.get(CONF_REPUBLISH, False)
        prefix = entry.options.get(CONF_MQTT_PREFIX, DEFAULT_MQTT_PREFIX)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        self.mqtt_prefix: str = prefix or DEFAULT_MQTT_PREFIX
        self._mqtt_warned = False

        self.id_token: str | None = None
        self.id_exp: int = 0

        # vin -> {prop: value} and vin -> {prop: extra-attributes}
        self.data: dict[str, dict[str, Any]] = {}
        self.attributes: dict[str, dict[str, dict[str, Any]]] = {}
        self.known: set[tuple[str, str]] = set()

        self._client: mqtt.Client | None = None
        self._unsub_refresh = None
        self._stopping = False

    # ------------------------------------------------------------------ setup
    async def async_start(self) -> None:
        """Refresh tokens, connect to BMW, and schedule future refreshes."""
        await self._async_refresh_tokens()
        await self.hass.async_add_executor_job(self._connect)
        self._schedule_refresh()

    async def async_stop(self) -> None:
        """Tear down the connection and timers."""
        self._stopping = True
        if self._unsub_refresh is not None:
            self._unsub_refresh()
            self._unsub_refresh = None
        if self._client is not None:
            await self.hass.async_add_executor_job(self._disconnect)
            self._client = None

    # -------------------------------------------------------------- tokens
    async def _async_refresh_tokens(self) -> None:
        """Refresh id/refresh tokens and persist the new refresh token."""
        session = async_get_clientsession(self.hass)
        tokens = await async_refresh_tokens(session, self.client_id, self.refresh_token)
        self.id_token = tokens["id_token"]
        self.refresh_token = tokens["refresh_token"]
        self.id_exp = jwt_exp(self.id_token)

        # Persist the rotated refresh token so restarts keep working.
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_REFRESH_TOKEN: self.refresh_token},
        )
        _LOGGER.debug(
            "BMW token refreshed, id_token valid for %ss",
            max(0, self.id_exp - int(time.time())),
        )

    def _schedule_refresh(self) -> None:
        """Schedule the next token refresh before expiry."""
        delay = self.id_exp - time.time() - TOKEN_REFRESH_MARGIN
        delay = max(delay, TOKEN_REFRESH_MIN_DELAY)
        self._unsub_refresh = async_call_later(self.hass, delay, self._handle_refresh)

    async def _handle_refresh(self, _now) -> None:
        """Refresh the token then reconnect the MQTT client with it."""
        self._unsub_refresh = None
        if self._stopping:
            return
        try:
            await self._async_refresh_tokens()
        except BmwAuthExpired:
            _LOGGER.error(
                "BMW refresh token is no longer valid - re-authentication required"
            )
            self.entry.async_start_reauth(self.hass)
            return
        except BmwAuthError as err:
            _LOGGER.warning("BMW token refresh failed, retrying soon: %s", err)
            self._unsub_refresh = async_call_later(self.hass, 60, self._handle_refresh)
            return

        if self._client is not None:
            await self.hass.async_add_executor_job(self._reconnect_with_token)
        self._schedule_refresh()

    # ------------------------------------------------------------- mqtt (exec)
    def _connect(self) -> None:
        """Create and start the paho client (runs in an executor thread)."""
        client = _build_client(self.client_id)
        client.tls_set()  # default system / certifi CA bundle
        client.username_pw_set(self.gcid, self.id_token)
        # Gentle exponential backoff: BMW returns MQTT v5 reason 151 ("Quota
        # exceeded") if we reconnect too aggressively, so start at 15s and
        # back off up to 5 minutes rather than hammering the broker.
        client.reconnect_delay_set(min_delay=15, max_delay=300)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        client.connect_async(BMW_STREAM_HOST, BMW_STREAM_PORT, keepalive=30)
        client.loop_start()
        self._client = client

    def _disconnect(self) -> None:
        assert self._client is not None
        try:
            self._client.disconnect()
            self._client.loop_stop()
        except Exception:  # noqa: BLE001 - best-effort teardown
            _LOGGER.debug("Error during BMW MQTT disconnect", exc_info=True)

    def _reconnect_with_token(self) -> None:
        """Apply the refreshed id_token and reconnect (runs in executor)."""
        assert self._client is not None
        self._client.username_pw_set(self.gcid, self.id_token)
        try:
            self._client.reconnect()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("BMW reconnect after refresh failed", exc_info=True)

    # paho callbacks (VERSION2 signatures; tolerate VERSION1 via *args)
    def _on_connect(self, client, _userdata, _flags, reason_code, *args) -> None:
        # paho 2.x passes a ReasonCode object; paho 1.x passes an int.
        is_failure = getattr(reason_code, "is_failure", None)
        if is_failure is None:
            is_failure = bool(reason_code)
        rc_display = getattr(reason_code, "value", reason_code)
        if not is_failure:
            client.subscribe(f"{self.gcid}/+", qos=1)
            _LOGGER.info("Connected to BMW CarData stream")
        else:
            _LOGGER.warning("BMW CarData connect failed: reason_code=%s", rc_display)

    def _on_disconnect(self, _client, _userdata, *args) -> None:
        _LOGGER.info("Disconnected from BMW CarData stream")

    def _on_message(self, _client, _userdata, message) -> None:
        payload = message.payload.decode("utf-8", "replace")
        # Marshal into the event loop; paho runs this in its own thread.
        self.hass.loop.call_soon_threadsafe(
            self._handle_message, message.topic, payload
        )

    # --------------------------------------------------------- loop callbacks
    @callback
    def _handle_message(self, topic: str, payload: str) -> None:
        """Parse a BMW payload and update entity state (event-loop thread)."""
        try:
            msg = json.loads(payload)
        except ValueError:
            _LOGGER.debug("Ignoring non-JSON BMW payload on %s", topic)
            return

        vin = msg.get("vin")
        if not vin:
            parts = topic.split("/")
            vin = parts[1] if len(parts) > 1 else None
        data = msg.get("data")
        if not vin or not isinstance(data, dict):
            return

        veh = self.data.setdefault(vin, {})
        veh_attrs = self.attributes.setdefault(vin, {})

        if self.republish:
            self._republish(topic, payload, vin, data)

        for prop, obj in data.items():
            if isinstance(obj, dict) and "value" in obj:
                value = obj["value"]
                extra = {k: v for k, v in obj.items() if k != "value"}
            else:
                value = obj
                extra = {}

            veh[prop] = value
            veh_attrs[prop] = extra

            key = (vin, prop)
            if key not in self.known:
                self.known.add(key)
                async_dispatcher_send(self.hass, SIGNAL_NEW_SIGNAL, vin, prop)
            else:
                async_dispatcher_send(self.hass, signal_update(vin, prop))

    @callback
    def _republish(
        self, topic: str, payload: str, vin: str, data: dict[str, Any]
    ) -> None:
        """Republish a BMW message to the HA MQTT broker (event-loop thread).

        Mirrors the upstream bmw-mqtt-bridge: the full payload is published to
        a legacy topic and a raw topic, and each signal value is also published
        to a per-signal topic for easy consumption.
        """
        prefix = self.mqtt_prefix
        rest = topic.split("/", 1)[1] if "/" in topic else topic
        legacy_topic = f"{prefix}{rest}"
        raw_topic = f"{prefix}raw/{rest}"

        try:
            ha_mqtt.async_publish(self.hass, legacy_topic, payload, 0, False)
            ha_mqtt.async_publish(self.hass, raw_topic, payload, 0, False)
            for prop, obj in data.items():
                value = obj.get("value") if isinstance(obj, dict) else obj
                signal_topic = f"{prefix}vehicles/{vin}/{prop}"
                out = value if isinstance(value, str) else json.dumps(value)
                ha_mqtt.async_publish(self.hass, signal_topic, out, 0, False)
        except Exception:  # noqa: BLE001 - MQTT integration may not be set up
            if not self._mqtt_warned:
                self._mqtt_warned = True
                _LOGGER.warning(
                    "Cannot republish to MQTT: the Home Assistant MQTT "
                    "integration is not configured. Add the MQTT integration "
                    "(e.g. the Mosquitto broker) or disable republishing in the "
                    "BMW CarData MQTT Bridge options."
                )
