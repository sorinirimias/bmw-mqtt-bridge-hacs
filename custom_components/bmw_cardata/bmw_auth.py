"""BMW CarData OAuth2 device-flow and token helpers.

Implements the BMW GCDM OAuth 2.0 Device Authorization Grant (with PKCE) plus
the refresh-token grant, mirroring the upstream bmw-mqtt-bridge scripts but in
async Python using Home Assistant's shared aiohttp session.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass

import aiohttp

from .const import (
    OAUTH_DEVICE_CODE_URL,
    OAUTH_SCOPES,
    OAUTH_TOKEN_URL,
)


class BmwAuthError(Exception):
    """Raised when authentication or token refresh fails."""


class BmwAuthExpired(BmwAuthError):
    """Raised when the device code expired or the refresh token is invalid."""


@dataclass
class DeviceCode:
    """A pending device-authorization request."""

    device_code: str
    verification_uri: str
    interval: int
    expires_in: int


def generate_pkce() -> tuple[str, str]:
    """Return a (code_verifier, code_challenge) PKCE pair (S256)."""
    verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(64))
        .rstrip(b"=")
        .decode("ascii")[:96]
    )
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def jwt_exp(token: str) -> int:
    """Return the 'exp' (unix seconds) claim of a JWT, or 0 if unavailable."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return int(payload.get("exp", 0))
    except (IndexError, ValueError, TypeError):
        return 0


async def async_request_device_code(
    session: aiohttp.ClientSession, client_id: str, code_challenge: str
) -> DeviceCode:
    """Request a device code; returns the verification URL the user must open."""
    data = {
        "client_id": client_id,
        "scope": OAUTH_SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    try:
        async with session.post(OAUTH_DEVICE_CODE_URL, data=data) as resp:
            body = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise BmwAuthError(f"device code request failed: {err}") from err

    device_code = body.get("device_code")
    verification_uri = body.get("verification_uri_complete")
    if not verification_uri and body.get("verification_uri"):
        verification_uri = (
            f"{body['verification_uri']}?user_code={body.get('user_code', '')}"
        )
    if not device_code or not verification_uri:
        raise BmwAuthError(f"invalid device code response: {body}")

    return DeviceCode(
        device_code=device_code,
        verification_uri=verification_uri,
        interval=int(body.get("interval", 5)),
        expires_in=int(body.get("expires_in", 300)),
    )


async def async_poll_for_tokens(
    session: aiohttp.ClientSession,
    client_id: str,
    code_verifier: str,
    device: DeviceCode,
) -> dict:
    """Poll the token endpoint until the user completes the browser login."""
    interval = device.interval
    deadline = time.monotonic() + device.expires_in

    while time.monotonic() < deadline:
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device.device_code,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        try:
            async with session.post(OAUTH_TOKEN_URL, data=data) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise BmwAuthError(f"token poll failed: {err}") from err

        error = body.get("error")
        if not error:
            return body

        if error == "authorization_pending":
            pass
        elif error == "slow_down":
            interval += 5
        elif error in ("expired_token", "expired_device_code", "invalid_grant"):
            raise BmwAuthExpired(error)
        else:
            raise BmwAuthError(f"{error}: {body.get('error_description', '')}")

        await asyncio.sleep(interval)

    raise BmwAuthExpired("device code timed out")


async def async_refresh_tokens(
    session: aiohttp.ClientSession, client_id: str, refresh_token: str
) -> dict:
    """Exchange a refresh token for fresh id/access/refresh tokens."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    try:
        async with session.post(OAUTH_TOKEN_URL, data=data) as resp:
            status = resp.status
            body = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise BmwAuthError(f"refresh request failed: {err}") from err

    if status != 200 or body.get("error"):
        if body.get("error") in ("invalid_grant", "invalid_token"):
            raise BmwAuthExpired(body.get("error"))
        raise BmwAuthError(f"refresh failed (HTTP {status}): {body}")

    if not body.get("id_token") or not body.get("refresh_token"):
        raise BmwAuthError("refresh response missing tokens")

    return body
