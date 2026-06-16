"""Sensor platform for the BMW CarData integration.

Sensors are created dynamically as signals arrive on the BMW stream, one entity
per (VIN, signal) pair, grouped under a device per vehicle (VIN).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW_SIGNAL, signal_update
from .coordinator import BmwCarDataRuntime
from .signal_map import classify


def _is_number(value: Any) -> bool:
    """Return True for real numeric values (excluding booleans)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BMW CarData sensors and listen for newly discovered signals."""
    runtime: BmwCarDataRuntime = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add(vin: str, prop: str) -> None:
        async_add_entities([BmwSignalSensor(runtime, vin, prop)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_SIGNAL, _add)
    )

    # Add any signals already received before this platform was set up.
    for vin, prop in list(runtime.known):
        _add(vin, prop)


class BmwSignalSensor(SensorEntity):
    """A single BMW CarData signal as a sensor entity."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, runtime: BmwCarDataRuntime, vin: str, prop: str) -> None:
        """Initialize the sensor for one (VIN, signal) pair."""
        self._runtime = runtime
        self._vin = vin
        self._prop = prop
        self._cls = classify(prop)
        self._attr_unique_id = f"{vin}_{prop}"
        self._attr_name = prop
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vin)},
            manufacturer="BMW",
            model="CarData",
            name=f"BMW {vin}",
        )

    @property
    def native_value(self) -> Any:
        """Return the latest value for this signal."""
        return self._runtime.data.get(self._vin, {}).get(self._prop)

    @property
    def device_class(self):
        """Return the device class, only when the current value is numeric."""
        if self._cls.device_class is not None and _is_number(self.native_value):
            return self._cls.device_class
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit, only when the current value is numeric."""
        if self._cls.unit is not None and _is_number(self.native_value):
            return self._cls.unit
        return None

    @property
    def state_class(self):
        """Return the state class, only when the current value is numeric."""
        if self._cls.state_class is not None and _is_number(self.native_value):
            return self._cls.state_class
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return any extra fields BMW sent alongside the value."""
        return self._runtime.attributes.get(self._vin, {}).get(self._prop) or None

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates for this signal."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_update(self._vin, self._prop),
                self.async_write_ha_state,
            )
        )
