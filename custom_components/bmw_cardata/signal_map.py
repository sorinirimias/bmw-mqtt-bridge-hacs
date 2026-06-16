"""Heuristic classification of BMW CarData signals to HA sensor metadata.

BMW's signal catalog varies by vehicle and is not fully documented, so instead of
hard-coding exact keys we match on substrings of the signal name to assign a
device class, unit and state class for the common numeric signals. Anything that
does not match stays a plain (generic) sensor.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)


@dataclass(frozen=True)
class SignalClass:
    """HA sensor metadata for a BMW signal."""

    device_class: SensorDeviceClass | None = None
    unit: str | None = None
    state_class: SensorStateClass | None = None


_NONE = SignalClass()

# Ordered (keywords, classification). The first rule whose any keyword appears in
# the lowercased signal name wins, so put more specific rules first.
_RULES: tuple[tuple[tuple[str, ...], SignalClass], ...] = (
    (
        ("stateofcharge", "soc", "chargelevel", "batterylevel", "chargingstate"),
        SignalClass(SensorDeviceClass.BATTERY, PERCENTAGE, SensorStateClass.MEASUREMENT),
    ),
    (
        ("odometer", "mileage", "totaldistance"),
        SignalClass(
            SensorDeviceClass.DISTANCE,
            UnitOfLength.KILOMETERS,
            SensorStateClass.TOTAL_INCREASING,
        ),
    ),
    (
        ("range",),
        SignalClass(
            SensorDeviceClass.DISTANCE,
            UnitOfLength.KILOMETERS,
            SensorStateClass.MEASUREMENT,
        ),
    ),
    (
        ("tirepressure", "tyrepressure", "pressure"),
        SignalClass(
            SensorDeviceClass.PRESSURE,
            UnitOfPressure.KPA,
            SensorStateClass.MEASUREMENT,
        ),
    ),
    (
        ("temperature", "temp"),
        SignalClass(
            SensorDeviceClass.TEMPERATURE,
            UnitOfTemperature.CELSIUS,
            SensorStateClass.MEASUREMENT,
        ),
    ),
    (
        ("humidity",),
        SignalClass(SensorDeviceClass.HUMIDITY, PERCENTAGE, SensorStateClass.MEASUREMENT),
    ),
    (
        ("speed",),
        SignalClass(
            SensorDeviceClass.SPEED,
            UnitOfSpeed.KILOMETERS_PER_HOUR,
            SensorStateClass.MEASUREMENT,
        ),
    ),
    (
        ("voltage",),
        SignalClass(
            SensorDeviceClass.VOLTAGE,
            UnitOfElectricPotential.VOLT,
            SensorStateClass.MEASUREMENT,
        ),
    ),
    (
        ("fuellevel", "tanklevel"),
        SignalClass(None, PERCENTAGE, SensorStateClass.MEASUREMENT),
    ),
)


def classify(prop: str) -> SignalClass:
    """Return sensor metadata for a BMW signal name (best-effort)."""
    name = "".join(c for c in prop.lower() if c.isalnum())
    for keywords, signal_class in _RULES:
        if any(keyword in name for keyword in keywords):
            return signal_class
    return _NONE
