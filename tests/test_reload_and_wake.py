"""A token refresh must not reload the integration, and a wake-up ends as soon as telemetry arrives."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from custom_components.tesla_vehicle_command import coordinator as coordinator_module
from custom_components.tesla_vehicle_command.coordinator import (
    TeslaVehicleCommandCoordinator,
    options_changed,
)

VIN = "5YJ3TEST"


def _entry(tokens=None, options=None):
    return SimpleNamespace(
        entry_id="entry1",
        data={"vehicles": [{"vin": VIN}], "tokens": tokens or {}},
        options=options or {},
    )


def _coordinator(entry, hass=None) -> TeslaVehicleCommandCoordinator:
    return TeslaVehicleCommandCoordinator(hass, entry, SimpleNamespace(is_running=True))


# ---- reload only on option changes


def test_same_options_do_not_count_as_a_change():
    assert options_changed({"a": 1}, {"a": 1}) is False


def test_changed_options_count_as_a_change():
    assert options_changed({"a": 1}, {"a": 2}) is True
    assert options_changed({}, {"a": 1}) is True


def test_unknown_previous_options_count_as_a_change():
    assert options_changed(None, {}) is True


# ---- the stored token is reused after a reload


def test_valid_stored_token_is_reused_without_a_refresh():
    entry = _entry({"access_token": "stored", "refresh_token": "r", "expires_at": time.time() + 3600})
    coordinator = _coordinator(entry)
    refreshed = []

    async def refresh(token):
        refreshed.append(token)

    coordinator._refresh_access_token = refresh
    asyncio.run(coordinator._ensure_valid_token())
    assert refreshed == []
    assert coordinator.access_token == "stored"


def test_expired_stored_token_is_refreshed():
    entry = _entry({"access_token": "old", "refresh_token": "r", "expires_at": time.time() - 10})
    coordinator = _coordinator(entry)
    refreshed = []

    async def refresh(token):
        refreshed.append(token)

    coordinator._refresh_access_token = refresh
    asyncio.run(coordinator._ensure_valid_token())
    assert refreshed == ["r"]


def test_missing_or_bad_expiry_forces_a_refresh():
    for tokens in ({"access_token": "a", "refresh_token": "r"}, {"access_token": "a", "refresh_token": "r", "expires_at": "x"}):
        coordinator = _coordinator(_entry(tokens))
        refreshed = []

        async def refresh(token, refreshed=refreshed):
            refreshed.append(token)

        coordinator._refresh_access_token = refresh
        asyncio.run(coordinator._ensure_valid_token())
        assert refreshed == ["r"]


# ---- the wake-up wait ends with the first telemetry, not after the timeout


def _wake_coordinator(loop) -> TeslaVehicleCommandCoordinator:
    coordinator = _coordinator(_entry(), hass=SimpleNamespace(loop=loop))
    coordinator.set_telemetry_receiver_available(True)
    return coordinator


@pytest.mark.parametrize("arrives", ["during_request", "after_request"])
def test_wake_returns_as_soon_as_the_vehicle_connects(monkeypatch, arrives):
    monkeypatch.setattr(coordinator_module, "WAKE_TELEMETRY_TIMEOUT_SECONDS", 30)

    async def run():
        coordinator = _wake_coordinator(asyncio.get_running_loop())

        async def wake_request(vin):
            if arrives == "during_request":
                coordinator.set_connectivity_status(vin, "CONNECTED")
            else:
                asyncio.get_running_loop().call_later(0.05, coordinator.set_connectivity_status, vin, "CONNECTED")
            return {"response": {"state": "asleep"}}

        coordinator._async_wake_up = wake_request
        started = time.monotonic()
        await asyncio.wait_for(coordinator.async_wake_up(VIN), timeout=2)
        assert time.monotonic() - started < 1
        assert coordinator.is_vehicle_awake(VIN)
        assert not coordinator.is_vehicle_waking(VIN)
        for timer in coordinator._sleep_timers.values():
            timer.cancel()

    asyncio.run(run())


def test_wake_still_times_out_when_no_telemetry_comes(monkeypatch):
    monkeypatch.setattr(coordinator_module, "WAKE_TELEMETRY_TIMEOUT_SECONDS", 0.1)

    async def run():
        coordinator = _wake_coordinator(asyncio.get_running_loop())

        async def wake_request(vin):
            return {"response": {"state": "asleep"}}

        coordinator._async_wake_up = wake_request
        with pytest.raises(Exception, match="did not become ready"):
            await coordinator.async_wake_up(VIN)
        assert not coordinator.is_vehicle_waking(VIN)

    asyncio.run(run())
