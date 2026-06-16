"""The BMW CarData integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .bmw_auth import BmwAuthError, BmwAuthExpired
from .const import DOMAIN
from .coordinator import BmwCarDataRuntime

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type BmwConfigEntry = ConfigEntry[BmwCarDataRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: BmwConfigEntry) -> bool:
    """Set up BMW CarData from a config entry."""
    runtime = BmwCarDataRuntime(hass, entry)
    try:
        await runtime.async_start()
    except BmwAuthExpired as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except BmwAuthError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BmwConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime: BmwCarDataRuntime = hass.data[DOMAIN].pop(entry.entry_id)
        await runtime.async_stop()
    return unload_ok
