"""Tests for the Charging Current number (set_charging_amps)."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import pytest


def _stub(name: str, **attributes) -> None:
    module = sys.modules.get(name) or ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    sys.modules[name] = module


class _CoordinatorEntity:
    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls

    @property
    def available(self) -> bool:
        return True


class _NumberMode:
    SLIDER = "slider"
    BOX = "box"


class _Unit:
    CELSIUS = "°C"
    AMPERE = "A"


_stub("homeassistant.components")
_stub("homeassistant.components.number", NumberEntity=object, NumberMode=_NumberMode)
_stub("homeassistant.const", PERCENTAGE="%", UnitOfTemperature=_Unit, UnitOfElectricCurrent=_Unit)
_stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_stub("homeassistant.helpers.device_registry", DeviceInfo=dict)
_stub("homeassistant.helpers.update_coordinator", CoordinatorEntity=_CoordinatorEntity)

from custom_components.tesla_vehicle_command.coordinator import (  # noqa: E402
    TeslaVehicleCommandCoordinator,
)
from custom_components.tesla_vehicle_command.number import (  # noqa: E402
    CHARGING_CURRENT_FALLBACK_MAX,
    CHARGING_CURRENT_MIN,
    TeslaChargingCurrentNumber,
)
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

VIN = "5YJ3TEST"


class _Coordinator:
    def __init__(self, request=None, request_max=None) -> None:
        charge = {}
        if request is not None:
            charge["charge_current_request"] = request
        if request_max is not None:
            charge["charge_current_request_max"] = request_max
        self.data = {VIN: {"response": {"charge_state": charge}}}
        self.sent: list[tuple[str, dict | None]] = []
        self.refreshed = 0
        self.fail = None

    async def async_send_command(self, vin: str, command: str, body=None):
        assert vin == VIN
        self.sent.append((command, body))
        if self.fail:
            raise RuntimeError(self.fail)
        return {}

    async def async_request_refresh(self) -> None:
        self.refreshed += 1


def _number(request=None, request_max=None):
    coordinator = _Coordinator(request, request_max)
    return TeslaChargingCurrentNumber(coordinator, VIN, "Car"), coordinator


def test_value_is_the_amps_the_car_is_set_to():
    assert _number(12, 16)[0].native_value == 12


def test_value_unknown_without_data():
    assert _number()[0].native_value is None


def test_range_is_5_to_the_car_maximum():
    number, _ = _number(12, 16)
    assert number._attr_native_min_value == CHARGING_CURRENT_MIN == 5
    assert number.native_max_value == 16


def test_range_falls_back_when_the_maximum_is_unknown():
    number, _ = _number(12)
    assert number.native_max_value == CHARGING_CURRENT_FALLBACK_MAX


def test_range_never_below_the_minimum():
    number, _ = _number(3, 3)
    assert number.native_max_value >= number._attr_native_min_value


def test_set_sends_whole_amps_and_refreshes():
    number, coordinator = _number(16, 16)
    asyncio.run(number.async_set_native_value(11.0))
    assert coordinator.sent == [("set_charge_current", {"charging_amps": 11})]
    assert coordinator.refreshed == 1


def test_set_is_clamped_to_the_range():
    number, coordinator = _number(16, 16)
    asyncio.run(number.async_set_native_value(40))
    asyncio.run(number.async_set_native_value(1))
    assert [body["charging_amps"] for _, body in coordinator.sent] == [16, 5]


def test_failure_is_a_home_assistant_error():
    number, coordinator = _number(16, 16)
    coordinator.fail = "Command failed: 408 - vehicle unavailable"
    with pytest.raises(HomeAssistantError):
        asyncio.run(number.async_set_native_value(10))


def test_accepted_command_updates_the_shown_value():
    updates = TeslaVehicleCommandCoordinator._command_state_updates(
        object(), "set_charge_current", {"charging_amps": 9}
    )
    assert updates == {"charge_state": {"charge_current_request": 9}}


def test_command_maps_to_set_charging_amps():
    from custom_components.tesla_vehicle_command.const import COMMANDS

    assert COMMANDS["set_charge_current"] == "set_charging_amps"
