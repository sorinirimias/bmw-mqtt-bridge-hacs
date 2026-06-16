"""Config flow for the BMW CarData integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bmw_auth import (
    BmwAuthError,
    DeviceCode,
    async_poll_for_tokens,
    async_request_device_code,
    generate_pkce,
)
from .const import CONF_CLIENT_ID, CONF_GCID, CONF_REFRESH_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_GCID): str,
    }
)


class BmwCarDataConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the BMW CarData device-flow setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._client_id: str | None = None
        self._gcid: str | None = None
        self._code_verifier: str | None = None
        self._code_challenge: str | None = None
        self._device: DeviceCode | None = None
        self._refresh_token: str | None = None
        self._auth_task = None
        self._reauth_entry = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the BMW CLIENT_ID and GCID."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._client_id = user_input[CONF_CLIENT_ID].strip()
            self._gcid = user_input[CONF_GCID].strip()
            self._code_verifier, self._code_challenge = generate_pkce()
            return await self.async_step_auth()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run the device flow: show the BMW login URL and poll for tokens."""
        session = async_get_clientsession(self.hass)

        # Request the device code once (quick), so we have the URL to display.
        if self._device is None:
            try:
                self._device = await async_request_device_code(
                    session, self._client_id, self._code_challenge
                )
            except BmwAuthError:
                _LOGGER.exception("BMW device code request failed")
                return self.async_abort(reason="cannot_connect")

        # Kick off the (potentially long) polling task once.
        if self._auth_task is None:
            self._auth_task = self.hass.async_create_task(
                async_poll_for_tokens(
                    session, self._client_id, self._code_verifier, self._device
                )
            )

        if not self._auth_task.done():
            return self.async_show_progress(
                step_id="auth",
                progress_action="wait_for_login",
                description_placeholders={"url": self._device.verification_uri},
                progress_task=self._auth_task,
            )

        # Task finished - collect the result.
        try:
            tokens = self._auth_task.result()
            self._refresh_token = tokens["refresh_token"]
        except Exception:  # noqa: BLE001 - surfaced as a failed step
            _LOGGER.exception("BMW device-flow login failed")
            return self.async_show_progress_done(next_step_id="auth_failed")

        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create (or update) the config entry."""
        await self.async_set_unique_id(self._gcid)

        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data={
                    **self._reauth_entry.data,
                    CONF_REFRESH_TOKEN: self._refresh_token,
                },
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"BMW CarData MQTT ({self._gcid[:8]})",
            data={
                CONF_CLIENT_ID: self._client_id,
                CONF_GCID: self._gcid,
                CONF_REFRESH_TOKEN: self._refresh_token,
            },
        )

    async def async_step_auth_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Abort after a failed login."""
        return self.async_abort(reason="auth_failed")

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the refresh token is rejected."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._client_id = entry_data[CONF_CLIENT_ID]
        self._gcid = entry_data[CONF_GCID]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication, then re-run the device flow."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        self._code_verifier, self._code_challenge = generate_pkce()
        return await self.async_step_auth()
