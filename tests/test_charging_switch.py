"""Tests for the Charging switch (charge_start / charge_stop)."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType


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


_stub("homeassistant.components")
_stub("homeassistant.components.switch", SwitchEntity=object)
_stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_stub("homeassistant.helpers.device_registry", DeviceInfo=dict)
_stub("homeassistant.helpers.update_coordinator", CoordinatorEntity=_CoordinatorEntity)

from custom_components.tesla_vehicle_command.switch import TeslaChargingSwitch  # noqa: E402

VIN = "5YJ3TEST"


class _Coordinator:
    def __init__(self, charging_state=None) -> None:
        charge = {} if charging_state is None else {"charging_state": charging_state}
        self.data = {VIN: {"response": {"charge_state": charge}}}
        self.sent: list[str] = []
        self.refreshed = 0
        self.fail = None

    async def async_send_command(self, vin: str, command: str, body=None):
        assert vin == VIN
        self.sent.append(command)
        if self.fail:
            raise RuntimeError(f"Command failed: car could not execute command: {self.fail}")
        return {}

    async def async_request_refresh(self) -> None:
        self.refreshed += 1


def _switch(state=None) -> tuple[TeslaChargingSwitch, _Coordinator]:
    coordinator = _Coordinator(state)
    return TeslaChargingSwitch(coordinator, VIN, "Car"), coordinator


def test_is_on_while_charging_or_starting():
    assert _switch("Charging")[0].is_on is True
    assert _switch("Starting")[0].is_on is True


def test_is_off_when_not_charging():
    for state in ("Complete", "Disconnected", "Stopped", "NoPower"):
        assert _switch(state)[0].is_on is False


def test_unknown_state_is_none():
    assert _switch()[0].is_on is None
    assert _switch("Weird")[0].is_on is None


def test_turn_off_sends_charge_stop():
    switch, coordinator = _switch("Charging")
    asyncio.run(switch.async_turn_off())
    assert coordinator.sent == ["charge_stop"]
    assert coordinator.refreshed == 1


def test_turn_on_sends_charge_start():
    switch, coordinator = _switch("Stopped")
    asyncio.run(switch.async_turn_on())
    assert coordinator.sent == ["charge_start"]


def test_unique_id_and_name():
    switch, _ = _switch("Charging")
    assert switch._attr_unique_id == f"{VIN}_charging"
    assert switch._attr_name == "Charging"


def test_charge_commands_do_not_fake_the_charging_state():
    """charging_state must change only with real telemetry: automations read it to confirm the car really stopped."""
    from custom_components.tesla_vehicle_command.coordinator import TeslaVehicleCommandCoordinator

    for command in ("charge_start", "charge_stop"):
        assert TeslaVehicleCommandCoordinator._command_state_updates(None, command, {}) == {}


def test_stop_when_the_car_is_not_charging_counts_as_done():
    switch, coordinator = _switch("Stopped")
    coordinator.fail = "not_charging"
    asyncio.run(switch.async_turn_off())
    assert coordinator.refreshed == 1


def test_start_when_already_charging_or_full_counts_as_done():
    for reason in ("is_charging", "complete"):
        switch, coordinator = _switch("Charging")
        coordinator.fail = reason
        asyncio.run(switch.async_turn_on())
        assert coordinator.refreshed == 1


def test_other_failures_are_home_assistant_errors():
    """Automations use continue_on_error, which only covers HomeAssistantError."""
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    switch, coordinator = _switch("Disconnected")
    coordinator.fail = "disconnected"
    with pytest.raises(HomeAssistantError, match="disconnected"):
        asyncio.run(switch.async_turn_on())
    assert coordinator.refreshed == 0
