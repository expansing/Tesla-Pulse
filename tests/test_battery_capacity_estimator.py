"""Regression tests for the usable-capacity estimator state machine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.tesla_vehicle_command.const import (
    CONF_BATTERY_REFERENCE_CAPACITIES,
)
from custom_components.tesla_vehicle_command.coordinator import (
    TeslaVehicleCommandCoordinator,
)
from custom_components.tesla_vehicle_command.telemetry_consumer import TelemetryConsumer

VIN = "test-vin"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def coordinator() -> TeslaVehicleCommandCoordinator:
    """Build only the state required by pure estimator methods."""
    instance = TeslaVehicleCommandCoordinator.__new__(TeslaVehicleCommandCoordinator)
    instance._battery_capacity_models = {}
    instance._telemetry_metadata = {}
    instance._telemetry_receiver_available = False
    instance._vehicles = [{"vin": VIN}]
    instance.proxy_manager = SimpleNamespace(telemetry_ca_path=None)
    instance._telemetry_store = SimpleNamespace(async_delay_save=lambda *args: None)
    instance._telemetry_raw_signals = {}
    instance._telemetry_receiver_diagnostics = {}
    instance._signal_capabilities = {}
    instance._capability_valid_signals = {}
    instance._capability_frame_counts = {}
    instance._capability_listener = None
    instance.data = {}
    instance.entry = SimpleNamespace(
        options={CONF_BATTERY_REFERENCE_CAPACITIES: {VIN: 73.5}}
    )
    return instance


def _record_window(
    coordinator: TeslaVehicleCommandCoordinator,
    start_soc: float,
    start_energy: float,
    end_soc: float,
    end_energy: float,
    start_time: datetime,
) -> None:
    coordinator.record_battery_capacity_sample(
        VIN, start_soc, start_energy, observed_at=start_time
    )
    midpoint_soc = (start_soc + end_soc) / 2
    midpoint_energy = (start_energy + end_energy) / 2
    coordinator.record_battery_capacity_sample(
        VIN,
        midpoint_soc,
        midpoint_energy,
        observed_at=start_time + timedelta(minutes=30),
    )
    coordinator.record_battery_capacity_sample(
        VIN,
        end_soc,
        end_energy,
        observed_at=start_time + timedelta(hours=1),
    )
    coordinator.flush_active_battery_window(VIN)


def test_records_a_valid_window_and_calculates_soh(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A 20-point same-direction run produces capacity, SOH, and confidence."""
    _record_window(coordinator, 80, 52, 100, 65, START)

    assert coordinator._battery_capacity_metrics(VIN) == {
        "estimated_usable_capacity": 65.0,
        "battery_soh_confidence": 20.0,
        "estimated_battery_soh": 88.4,
    }


def test_starting_a_new_active_window_does_not_change_the_published_estimate(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A sample that only opens a new window must not alter the published SOH."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [
            {"capacity_kwh": 65.0, "delta_soc": 40.0, "source": "live"}
        ]
    }

    coordinator.record_battery_capacity_sample(VIN, 55, 40, observed_at=START)

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0


def test_small_reversal_jitter_does_not_finalize_the_active_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A single opposite-direction step at or below the 1% tolerance is noise."""
    coordinator.record_battery_capacity_sample(VIN, 50, 32.5, observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 60, 39.0, observed_at=START + timedelta(minutes=10)
    )
    # A momentary regen-sized dip (0.5% <= 1.0% tolerance) must not close the window.
    coordinator.record_battery_capacity_sample(
        VIN, 59.5, 38.7, observed_at=START + timedelta(minutes=11)
    )

    active = coordinator._battery_capacity_models[VIN]["active_window"]
    assert active is not None
    assert active["direction"] == "charging"
    assert active["reversal_streak"] == 1
    assert active["last"]["soc"] == 60.0
    assert active["last"]["energy"] == 39.0


def test_repeated_small_reversals_finalize_the_active_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A second consecutive small reversal exhausts the noise tolerance streak."""
    coordinator.record_battery_capacity_sample(VIN, 50, 32.5, observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 65, 42.25, observed_at=START + timedelta(minutes=10)
    )
    coordinator.record_battery_capacity_sample(
        VIN, 64.5, 41.925, observed_at=START + timedelta(minutes=11)
    )
    coordinator.record_battery_capacity_sample(
        VIN, 64.0, 41.6, observed_at=START + timedelta(minutes=12)
    )

    model = coordinator._battery_capacity_models[VIN]
    new_active = model["active_window"]
    assert new_active["start"]["soc"] == 64.0
    assert new_active["direction"] is None
    assert new_active["reversal_streak"] == 0
    # The closed run's 15-point span is below the minimum and is dropped silently.
    assert model.get("accepted_windows", []) == []


def test_a_reversal_beyond_tolerance_finalizes_immediately(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A reversal step larger than the tolerance closes the window with no grace."""
    coordinator.record_battery_capacity_sample(VIN, 50, 32.5, observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 70, 45.5, observed_at=START + timedelta(minutes=10)
    )
    coordinator.record_battery_capacity_sample(
        VIN, 68, 44.2, observed_at=START + timedelta(minutes=11)
    )

    model = coordinator._battery_capacity_models[VIN]
    assert model["accepted_windows"][0]["capacity_kwh"] == 65.0
    assert model["active_window"]["start"]["soc"] == 68.0


def test_soc_and_energy_pair_across_separate_telemetry_frames(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Fields sent in separate frames still pair when the companion is fresh."""
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)
    response = coordinator._empty_response()

    async def process_frame(data: list[dict[str, object]], timestamp: str) -> None:
        await consumer._process_vehicle_signals(
            VIN, response, {"data": data, "createdAt": timestamp}
        )

    asyncio.run(
        process_frame(
            [{"key": "Soc", "value": {"doubleValue": 70}}],
            "2026-01-01T00:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [{"key": "EnergyRemaining", "value": {"doubleValue": 49}}],
            "2026-01-01T00:00:30Z",
        )
    )

    active = coordinator._battery_capacity_models[VIN]["active_window"]
    assert active["last"]["soc"] == 70.0
    assert active["last"]["energy"] == 49.0


def test_stale_companion_signal_does_not_pair_across_frames(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A companion field seen too long ago must not be paired as co-temporal."""
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)
    response = coordinator._empty_response()

    async def process_frame(data: list[dict[str, object]], timestamp: str) -> None:
        await consumer._process_vehicle_signals(
            VIN, response, {"data": data, "createdAt": timestamp}
        )

    asyncio.run(
        process_frame(
            [{"key": "Soc", "value": {"doubleValue": 70}}],
            "2026-01-01T00:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [{"key": "EnergyRemaining", "value": {"doubleValue": 49}}],
            "2026-01-01T00:05:00Z",
        )
    )

    assert coordinator._battery_capacity_metrics(VIN) == {}


def test_a_stale_companion_reading_cannot_pair_with_multiple_later_frames(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """One companion reading is consumed on pairing, not reused for later frames."""
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)
    response = coordinator._empty_response()

    async def process_frame(data: list[dict[str, object]], timestamp: str) -> None:
        await consumer._process_vehicle_signals(
            VIN, response, {"data": data, "createdAt": timestamp}
        )

    asyncio.run(
        process_frame(
            [{"key": "EnergyRemaining", "value": {"doubleValue": 49}}],
            "2026-01-01T00:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [{"key": "Soc", "value": {"doubleValue": 70}}],
            "2026-01-01T00:00:30Z",
        )
    )
    active = coordinator._battery_capacity_models[VIN]["active_window"]
    assert active["last"]["soc"] == 70.0
    assert active["last"]["energy"] == 49.0

    # The 49 kWh reading has already been consumed; a later Soc-only frame
    # must not reuse it to fabricate a new sample.
    asyncio.run(
        process_frame(
            [{"key": "Soc", "value": {"doubleValue": 71}}],
            "2026-01-01T00:00:45Z",
        )
    )

    active = coordinator._battery_capacity_models[VIN]["active_window"]
    assert active["last"]["soc"] == 70.0
    assert active["last"]["energy"] == 49.0

    asyncio.run(
        process_frame(
            [{"key": "EnergyRemaining", "value": {"doubleValue": 49.5}}],
            "2026-01-01T00:01:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [{"key": "Soc", "value": {"doubleValue": 72}}],
            "2026-01-01T00:01:05Z",
        )
    )

    active = coordinator._battery_capacity_models[VIN]["active_window"]
    assert active["last"]["soc"] == 72.0
    assert active["last"]["energy"] == 49.5


@pytest.mark.parametrize(
    ("gap_seconds", "should_pair"),
    [(120, True), (121, False)],
)
def test_cross_frame_pairing_respects_the_max_age_boundary(
    coordinator: TeslaVehicleCommandCoordinator,
    gap_seconds: int,
    should_pair: bool,
) -> None:
    """The freshness window is inclusive at its configured maximum age."""
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)
    response = coordinator._empty_response()

    async def process_frame(data: list[dict[str, object]], timestamp: str) -> None:
        await consumer._process_vehicle_signals(
            VIN, response, {"data": data, "createdAt": timestamp}
        )

    asyncio.run(
        process_frame(
            [{"key": "Soc", "value": {"doubleValue": 70}}],
            "2026-01-01T00:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [{"key": "EnergyRemaining", "value": {"doubleValue": 49}}],
            f"2026-01-01T00:0{gap_seconds // 60}:{gap_seconds % 60:02d}Z",
        )
    )

    metrics = coordinator._battery_capacity_metrics(VIN)
    if should_pair:
        active = coordinator._battery_capacity_models[VIN]["active_window"]
        assert active["last"]["soc"] == 70.0
        assert active["last"]["energy"] == 49.0
    else:
        assert metrics == {}


def test_out_of_order_frame_does_not_pair_with_a_later_companion_timestamp(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A frame timestamped before its companion must not be treated as fresh."""
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)
    response = coordinator._empty_response()

    async def process_frame(data: list[dict[str, object]], timestamp: str) -> None:
        await consumer._process_vehicle_signals(
            VIN, response, {"data": data, "createdAt": timestamp}
        )

    asyncio.run(
        process_frame(
            [{"key": "EnergyRemaining", "value": {"doubleValue": 49}}],
            "2026-01-01T00:00:30Z",
        )
    )
    asyncio.run(
        process_frame(
            [{"key": "Soc", "value": {"doubleValue": 70}}],
            "2026-01-01T00:00:00Z",
        )
    )

    assert coordinator._battery_capacity_metrics(VIN) == {}


def test_missing_record_timestamp_prevents_cross_frame_pairing(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Without a parsable createdAt, cross-frame pairing must stay disabled."""
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)
    response = coordinator._empty_response()

    async def process_frame(data: list[dict[str, object]]) -> None:
        await consumer._process_vehicle_signals(VIN, response, {"data": data})

    asyncio.run(process_frame([{"key": "Soc", "value": {"doubleValue": 70}}]))
    asyncio.run(
        process_frame([{"key": "EnergyRemaining", "value": {"doubleValue": 49}}])
    )

    assert coordinator._battery_capacity_metrics(VIN) == {}


def test_charge_stop_finalizes_a_qualifying_charging_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A live charging stop scores a qualifying run at any configured limit."""
    coordinator.record_battery_capacity_sample(VIN, 50, 32.5, observed_at=START)
    coordinator.record_battery_charge_state(VIN, "Charging")
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2)
    )
    coordinator.record_battery_charge_state(VIN, "Stopped")

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0
    assert coordinator._battery_capacity_models[VIN]["active_window"] is None


def test_charge_stop_updates_an_estimate_with_more_weighted_new_evidence(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A larger live run outweighs a smaller older estimate at any charge limit."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 60.0, "delta_soc": 20.0}]
    }

    coordinator.record_battery_capacity_sample(VIN, 50, 32.5, observed_at=START)
    coordinator.record_battery_charge_state(VIN, "Charging")
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2)
    )
    coordinator.record_battery_charge_state(VIN, "Complete")

    assert coordinator._battery_capacity_metrics(VIN) == {
        "estimated_usable_capacity": 65.0,
        "battery_soh_confidence": 30.0,
        "estimated_battery_soh": 88.4,
    }


def test_charge_stop_closes_after_a_sub_epsilon_final_soc_step(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A rounded final SOC change does not defer a completed charge session."""
    coordinator.record_battery_capacity_sample(VIN, 50, 32.5, observed_at=START)
    coordinator.record_battery_charge_state(VIN, "Charging")
    coordinator.record_battery_capacity_sample(
        VIN, 79.99, 51.9935, observed_at=START + timedelta(hours=2)
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2, minutes=1)
    )
    coordinator.record_battery_charge_state(VIN, "Stopped")

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0


def test_repeated_charge_stop_frames_do_not_duplicate_capacity_windows(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Repeated stopped states do not re-score the completed charging run."""
    coordinator.record_battery_capacity_sample(VIN, 50, 32.5, observed_at=START)
    coordinator.record_battery_charge_state(VIN, "Charging")
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2)
    )
    coordinator.record_battery_charge_state(VIN, "Stopped")
    coordinator.record_battery_charge_state(VIN, "Stopped")

    assert len(coordinator._battery_capacity_models[VIN]["accepted_windows"]) == 1


def test_terminal_charge_state_closes_a_restored_live_charging_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A terminal telemetry delta closes a migrated live charging window."""
    coordinator._battery_capacity_models[VIN] = coordinator._migrate_battery_capacity_model(
        {
            "active_window": {
                "start": {
                    "soc": 50,
                    "energy": 32.5,
                    "timestamp": START.isoformat(),
                    "temperature": None,
                },
                "last": {
                    "soc": 80,
                    "energy": 52,
                    "timestamp": (START + timedelta(hours=2)).isoformat(),
                    "temperature": None,
                },
                "direction": "charging",
                "reversal_streak": 0,
                "source": "live",
                "temp_sum": 0.0,
                "temp_count": 0,
            }
        }
    )
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)

    asyncio.run(
        consumer._process_vehicle_signals(
            VIN,
            coordinator._empty_response(),
            {
                "data": [
                    {
                        "key": "DetailedChargeState",
                        "value": {"stringValue": "DetailedChargeStateStopped"},
                    }
                ],
                "createdAt": "2026-01-01T02:01:00Z",
            },
        )
    )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0
    assert coordinator._battery_capacity_models[VIN]["active_window"] is None


@pytest.mark.parametrize("terminal_state", ["Stopped", "Complete", "Disconnected"])
def test_terminal_telemetry_delta_finalizes_a_live_charging_window(
    coordinator: TeslaVehicleCommandCoordinator,
    terminal_state: str,
) -> None:
    """Typed terminal telemetry closes a live run without a matching SOC frame."""
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)
    response = coordinator._empty_response()

    async def process_frame(data: list[dict[str, object]], timestamp: str) -> None:
        await consumer._process_vehicle_signals(
            VIN, response, {"data": data, "createdAt": timestamp}
        )

    asyncio.run(
        process_frame(
            [
                {
                    "key": "DetailedChargeState",
                    "value": {"stringValue": "DetailedChargeStateCharging"},
                },
                {"key": "Soc", "value": {"doubleValue": 50}},
                {"key": "EnergyRemaining", "value": {"doubleValue": 32.5}},
            ],
            "2026-01-01T00:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [
                {"key": "Soc", "value": {"doubleValue": 80}},
                {"key": "EnergyRemaining", "value": {"doubleValue": 52}},
            ],
            "2026-01-01T02:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [
                {
                    "key": "DetailedChargeState",
                    "value": {
                        "stringValue": f"DetailedChargeState{terminal_state}"
                    },
                }
            ],
            "2026-01-01T02:01:00Z",
        )
    )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0
    assert coordinator._battery_capacity_models[VIN]["active_window"] is None


def test_weighted_median_prefers_larger_soc_coverage(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The rolling median (used for outlier rejection) weighs windows by SOC span."""
    windows = [
        {"capacity_kwh": 60.0, "delta_soc": 20.0},
        {"capacity_kwh": 65.0, "delta_soc": 60.0},
        {"capacity_kwh": 70.0, "delta_soc": 20.0},
    ]

    assert coordinator._weighted_median_capacity(windows) == 65.0


def test_weighted_average_capacity_handles_empty_and_unweighted_input(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The published estimator's helper handles its own edge cases directly."""
    assert coordinator._weighted_average_capacity([]) is None

    unweighted = [
        {"capacity_kwh": 60.0, "delta_soc": 0.0},
        {"capacity_kwh": 70.0, "delta_soc": 0.0},
    ]
    assert coordinator._weighted_average_capacity(unweighted) == 65.0


def test_published_estimate_reflects_recent_sessions_not_a_mass_of_old_evidence(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Enough recent sessions must move the published SOH past older evidence."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    old_sessions = [
        {
            "capacity_kwh": 61.83,
            "delta_soc": 25.0,
            "end_time": f"2026-01-0{day}T00:00:00+00:00",
        }
        for day in range(1, 9)
    ]
    recent_sessions = [
        {
            "capacity_kwh": 65.6,
            "delta_soc": 30.0,
            "end_time": f"2026-02-0{day}T00:00:00+00:00",
        }
        for day in range(1, 6)
    ]
    model["accepted_windows"] = old_sessions + recent_sessions

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.6


def test_missing_end_time_on_every_window_falls_back_to_the_rolling_median(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Without any reliable timestamp, the estimate falls back to a robust median."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    model["accepted_windows"] = [
        {"capacity_kwh": 60.0, "delta_soc": 20.0},
        {"capacity_kwh": 65.0, "delta_soc": 60.0},
        {"capacity_kwh": 70.0, "delta_soc": 20.0},
    ]

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0


def test_tied_end_time_is_handled_deterministically_by_the_weighted_average(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Windows sharing the same end_time still combine deterministically."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    model["accepted_windows"] = [
        {
            "capacity_kwh": 61.83,
            "delta_soc": 25.0,
            "end_time": "2026-01-01T00:00:00+00:00",
        },
        {
            "capacity_kwh": 65.6,
            "delta_soc": 30.0,
            "end_time": "2026-01-01T00:00:00+00:00",
        },
    ]

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 63.89


def test_recorder_window_with_later_end_time_outranks_an_older_live_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Source is metadata only; the newest window wins regardless of source."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    model["accepted_windows"] = [
        {
            "capacity_kwh": 61.83,
            "delta_soc": 25.0,
            "end_time": "2026-01-01T00:00:00+00:00",
            "source": "live",
        },
        {
            "capacity_kwh": 65.6,
            "delta_soc": 30.0,
            "end_time": "2026-01-08T00:00:00+00:00",
            "source": "recorder",
        },
    ]

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 63.89


def test_live_window_with_later_end_time_outranks_an_older_recorder_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Source is metadata only; the newest window wins regardless of source."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    model["accepted_windows"] = [
        {
            "capacity_kwh": 61.83,
            "delta_soc": 25.0,
            "end_time": "2026-01-01T00:00:00+00:00",
            "source": "recorder",
        },
        {
            "capacity_kwh": 65.6,
            "delta_soc": 30.0,
            "end_time": "2026-01-08T00:00:00+00:00",
            "source": "live",
        },
    ]

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 63.89


def test_confidence_diagnostics_report_recent_span_and_historical_total(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Confidence diagnostics expose the recent span and the lifetime total."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    old_sessions = [
        {
            "capacity_kwh": 61.83,
            "delta_soc": 25.0,
            "end_time": f"2026-01-0{day}T00:00:00+00:00",
        }
        for day in range(1, 3)
    ]
    recent_sessions = [
        {
            "capacity_kwh": 65.6,
            "delta_soc": 30.0,
            "end_time": f"2026-02-0{day}T00:00:00+00:00",
        }
        for day in range(1, 6)
    ]
    model["accepted_windows"] = old_sessions + recent_sessions

    assert coordinator.get_battery_confidence_diagnostics(VIN) == {
        "total_soc_span": 100.0,
        "window_count": 5,
        "historical_total_soc_span": 200.0,
    }


def test_confidence_diagnostics_fall_back_to_historical_total_without_a_timestamp(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Without any reliable end_time, confidence reports the historical total."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    model["accepted_windows"] = [
        {"capacity_kwh": 60.0, "delta_soc": 20.0},
        {"capacity_kwh": 65.0, "delta_soc": 30.0},
    ]

    assert coordinator.get_battery_confidence_diagnostics(VIN) == {
        "total_soc_span": 50.0,
        "window_count": 2,
        "historical_total_soc_span": 50.0,
    }


def test_one_small_session_cannot_override_several_large_recent_charges(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A single small later session must not dominate a weekend of large charges."""
    for day in range(1, 4):
        _record_window(
            coordinator,
            24,
            15.6,
            99,
            64.35,
            START + timedelta(days=day),
        )
    # A short drive right after the last big charge should only partly, not
    # substantially, pull the estimate away from the large reliable sessions.
    _record_window(
        coordinator,
        90,
        58.5,
        70,
        47.5,
        START + timedelta(days=3, hours=1),
    )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 64.18


def test_a_large_reliable_session_is_not_suppressed_by_several_smaller_ones(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A weighted median can hide a large session; the weighted average cannot."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    smaller_sessions = [
        {"capacity_kwh": 62.0, "delta_soc": 22.0, "end_time": f"2026-01-0{day}T00:00:00+00:00"}
        for day in range(1, 5)
    ]
    large_reliable_session = {
        "capacity_kwh": 65.18,
        "delta_soc": 76.0,
        "end_time": "2026-01-05T00:00:00+00:00",
    }
    windows = smaller_sessions + [large_reliable_session]
    model["accepted_windows"] = windows

    # The weighted median would still return 62.0 here: the four smaller
    # sessions' combined weight (88) crosses half of the total (82) before
    # the large 76-point session is ever reached in sorted order.
    assert coordinator._weighted_median_capacity(windows) == 62.0

    # The published estimate must not reproduce that suppression.
    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 63.47


def test_capacity_diagnostics_expose_how_many_windows_back_the_estimate(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Diagnostics distinguish total retained windows from those used now."""
    old_sessions = [
        {
            "capacity_kwh": 61.83,
            "delta_soc": 25.0,
            "end_time": f"2026-01-0{day}T00:00:00+00:00",
        }
        for day in range(1, 4)
    ]
    recent_sessions = [
        {
            "capacity_kwh": 65.6,
            "delta_soc": 30.0,
            "end_time": f"2026-02-0{day}T00:00:00+00:00",
        }
        for day in range(1, 6)
    ]
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": old_sessions + recent_sessions
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["accepted_window_count"] == 8
    assert diagnostics["estimate_window_count"] == 5


def test_exactly_five_sessions_are_all_used_in_the_estimate(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """With exactly the recency limit's worth of sessions, none are excluded."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    model["accepted_windows"] = [
        {
            "capacity_kwh": capacity,
            "delta_soc": 20.0,
            "end_time": f"2026-01-0{day}T00:00:00+00:00",
        }
        for day, capacity in zip(range(1, 6), [62.0, 64.0, 66.0, 68.0, 70.0])
    ]

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)
    assert diagnostics["accepted_window_count"] == 5
    assert diagnostics["estimate_window_count"] == 5
    # Equal weights: the weighted median of 5 equally-weighted values is the middle one.
    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 66.0


def test_a_sixth_older_session_is_excluded_from_the_estimate(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Adding one older session beyond the recency limit must not shift the estimate."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    model["accepted_windows"] = [
        {
            "capacity_kwh": capacity,
            "delta_soc": 20.0,
            "end_time": f"2026-01-0{day}T00:00:00+00:00",
        }
        for day, capacity in zip(range(1, 7), [40.0, 62.0, 64.0, 66.0, 68.0, 70.0])
    ]

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)
    assert diagnostics["accepted_window_count"] == 6
    assert diagnostics["estimate_window_count"] == 5
    # The oldest (40.0) is excluded; the result matches the 5-session case exactly.
    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 66.0


def test_outlier_gate_shares_the_same_recent_baseline_as_the_published_estimate(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A value close to the recent trend is accepted even against older evidence."""
    # Two old, heavily-weighted sessions establish a stale low baseline.
    _record_window(coordinator, 10, 5, 90, 45, START)
    _record_window(coordinator, 10, 5, 90, 45, START + timedelta(days=1))
    # Five smaller, more recent sessions establish the current real trend.
    for day in range(2, 7):
        _record_window(
            coordinator, 50, 32.5, 70, 45.5, START + timedelta(days=day)
        )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0

    # 68 kWh deviates 36% from the stale full-history baseline (50) but only
    # 4.6% from the current recent trend (65); it must be judged against the
    # latter and accepted, matching what the published estimate already shows.
    _record_window(coordinator, 50, 32.5, 70, 46.1, START + timedelta(days=7))

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)
    assert diagnostics["rejected_window_count"] == 0
    assert coordinator._battery_capacity_models[VIN]["accepted_windows"][0][
        "capacity_kwh"
    ] == 68.0


def test_rejects_capacity_far_from_rolling_median(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """An implausible but bounded second window remains diagnostic-only evidence."""
    _record_window(coordinator, 80, 52, 100, 65, START)
    _record_window(
        coordinator,
        60,
        30,
        80,
        50,
        START + timedelta(hours=2),
    )

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["accepted_window_count"] == 1
    assert diagnostics["rejected_window_count"] == 1
    rejected = coordinator._battery_capacity_models[VIN]["rejected_windows"][0]
    assert rejected["capacity_kwh"] == 100.0
    assert "rolling median" in rejected["rejection_reason"]


def test_long_gap_closes_the_previous_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A gap longer than six hours finalizes a completed same-direction run."""
    coordinator.record_battery_capacity_sample(VIN, 50, 32.5, observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 75, 48.75, observed_at=START + timedelta(hours=1)
    )
    coordinator.record_battery_capacity_sample(
        VIN, 76, 49.4, observed_at=START + timedelta(hours=8)
    )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0
    assert coordinator._battery_capacity_models[VIN]["active_window"]["start"]["soc"] == 76.0


def test_migrates_legacy_estimates_to_accepted_windows(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Legacy persisted estimates retain their capacity evidence after migration."""
    migrated = coordinator._migrate_battery_capacity_model(
        {
            "estimates": [
                {
                    "capacity_kwh": 64.5,
                    "soc_span": 25,
                    "energy_delta_kwh": 16.125,
                    "source": "recorder",
                }
            ],
            "anchor": {"soc_percent": 50},
        }
    )

    assert "estimates" not in migrated
    assert "anchor" not in migrated
    assert migrated["model_version"] == 3
    assert migrated["rejected_windows"] == []
    assert migrated["accepted_windows"] == [
        {
            "start_soc": None,
            "end_soc": None,
            "start_energy": None,
            "end_energy": None,
            "delta_soc": 25.0,
            "delta_energy": 16.125,
            "capacity_kwh": 64.5,
            "start_time": None,
            "end_time": None,
            "direction": None,
            "source": "recorder",
            "temperature_c": None,
            "rejected": False,
            "rejection_reason": None,
        }
    ]


def test_migrates_existing_windows_without_discarding_them(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Pre-versioned window models gain schema fields without losing evidence."""
    window = {"capacity_kwh": 65.0, "delta_soc": 20.0}

    migrated = coordinator._migrate_battery_capacity_model(
        {"accepted_windows": [window], "active_window": {"source": "live"}}
    )

    assert migrated["model_version"] == 3
    assert migrated["accepted_windows"] == [window]
    assert migrated["rejected_windows"] == []
    assert migrated["active_window"] == {"source": "live"}


def test_telemetry_setup_validation_is_sanitized_and_complete(
    coordinator: TeslaVehicleCommandCoordinator,
    tmp_path: Path,
) -> None:
    """Setup checks expose statuses, not hostnames, paths, or vehicle identifiers."""
    certificate_path = tmp_path / "telemetry-ca.pem"
    certificate_path.write_text("certificate")
    coordinator.entry.options.update(
        {"telemetry_hostname": "telemetry.example.test", "telemetry_port": 443}
    )
    coordinator.proxy_manager.telemetry_ca_path = certificate_path
    coordinator._telemetry_receiver_available = True
    coordinator._telemetry_metadata[VIN] = {
        "last_received": START,
        "registration_status": "registered",
    }

    assert coordinator.get_telemetry_setup_validation(VIN) == {
        "receiver": "available",
        "hostname": "configured",
        "port": "configured",
        "certificate": "available",
        "registration": "registered",
        "vehicle_frame": "received",
    }


def test_telemetry_setup_validation_reports_missing_configuration(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Unconfigured setup remains safe to inspect and does not reveal values."""
    result = coordinator.validate_telemetry_setup(VIN)

    assert result == {
        "receiver": "unavailable",
        "hostname": "missing",
        "port": "missing_or_invalid",
        "certificate": "missing",
        "registration": "not_attempted",
        "vehicle_frame": "not_received",
    }
    assert isinstance(coordinator._telemetry_metadata[VIN]["last_validation"], datetime)


def test_telemetry_metadata_persists_without_a_vehicle_frame(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Registration and validation outcomes survive before telemetry begins."""
    coordinator._telemetry_metadata[VIN] = {
        "registration_status": "registered",
        "last_registration": START,
        "last_validation": START + timedelta(minutes=1),
    }

    metadata = coordinator._telemetry_store_payload()["metadata"][VIN]

    assert metadata == {
        "registration_status": "registered",
        "last_registration": START.isoformat(),
        "last_validation": (START + timedelta(minutes=1)).isoformat(),
    }


def test_registration_failure_result_is_persisted(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Preflight registration failures replace stale success diagnostics."""
    coordinator._telemetry_metadata[VIN] = {"registration_status": "registered"}

    coordinator._record_telemetry_registration_result(VIN, "failed")

    metadata = coordinator._telemetry_store_payload()["metadata"][VIN]
    assert metadata["registration_status"] == "failed"
    assert "last_registration" in metadata


def test_reset_battery_history_removes_only_the_selected_source(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Recorder reset keeps live evidence and preserves non-estimator settings."""
    coordinator._battery_capacity_models[VIN] = {
        "model_version": 1,
        "accepted_windows": [
            {"source": "live", "capacity_kwh": 65.0, "delta_soc": 20.0},
            {"source": "recorder", "capacity_kwh": 64.0, "delta_soc": 20.0},
        ],
        "rejected_windows": [
            {"source": "live"},
            {"source": "recorder"},
        ],
        "active_window": {"source": "recorder"},
    }

    coordinator.reset_battery_capacity_history(VIN, "recorder")

    model = coordinator._battery_capacity_models[VIN]
    assert model["accepted_windows"] == [
        {"source": "live", "capacity_kwh": 65.0, "delta_soc": 20.0}
    ]
    assert model["rejected_windows"] == [{"source": "live"}]
    assert model["active_window"] is None
    assert model["last_reset_scope"] == "recorder"


def test_reset_battery_history_discards_invalid_window_entries(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Scoped reset keeps only dict window entries that remain in scope."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"source": "live"}, "bad-entry", 123],
        "rejected_windows": [{"source": "live"}, object()],
    }

    coordinator.reset_battery_capacity_history(VIN, "recorder")

    model = coordinator._battery_capacity_models[VIN]
    assert model["accepted_windows"] == [{"source": "live"}]
    assert model["rejected_windows"] == [{"source": "live"}]


def test_live_reset_discards_the_active_live_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A live reset closes an in-progress live window without inventing metrics."""
    coordinator.record_battery_capacity_sample(VIN, 70, 49, observed_at=START)

    coordinator.reset_battery_capacity_history(VIN, "live")

    assert coordinator._battery_capacity_metrics(VIN) == {}
    assert coordinator._battery_capacity_models[VIN]["active_window"] is None


def test_capacity_diagnostics_include_sources_ranges_and_rejections(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Diagnostics distinguish live and Recorder evidence without altering it."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [
            {"source": "live", "capacity_kwh": 65.0, "delta_soc": 20.0},
            {"source": "recorder", "capacity_kwh": 64.0, "delta_soc": 20.0},
        ],
        "rejected_windows": [
            {
                "source": "recorder",
                "capacity_kwh": 100.0,
                "delta_soc": 20.0,
                "end_time": "2026-01-01T00:00:00+00:00",
                "rejection_reason": "deviates from rolling median",
            }
        ],
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["accepted_window_sources"] == {"live": 1, "recorder": 1}
    assert diagnostics["accepted_capacity_min_kwh"] == 64.0
    assert diagnostics["accepted_capacity_max_kwh"] == 65.0
    assert diagnostics["rejected_window_sources"] == {"live": 0, "recorder": 1}
    assert diagnostics["rejected_windows"] == [
        {
            "capacity_kwh": 100.0,
            "delta_soc": 20.0,
            "end_time": "2026-01-01T00:00:00+00:00",
            "source": "recorder",
            "rejection_reason": "deviates from rolling median",
        }
    ]


def test_telemetry_status_reports_specific_health_reasons(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Receiver, registration, and incomplete battery states are distinguishable."""
    assert coordinator.get_telemetry_status(VIN)["state"] == "receiver_unavailable"

    coordinator._telemetry_receiver_available = True
    coordinator._telemetry_metadata[VIN] = {"registration_status": "failed"}
    assert coordinator.get_telemetry_status(VIN)["state"] == "registration_failed"

    coordinator._telemetry_metadata[VIN] = {
        "last_received": datetime.now(timezone.utc),
        "processed_fields": ["Soc"],
    }
    assert coordinator.get_telemetry_status(VIN)["state"] == "partial_battery_data"


def test_daily_snapshot_replaces_the_current_day(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Snapshots retain one latest valid estimate per day without affecting it."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 65.0, "delta_soc": 20.0}]
    }

    coordinator._record_daily_battery_snapshot(VIN)
    coordinator._battery_capacity_models[VIN]["accepted_windows"] = [
        {"capacity_kwh": 64.0, "delta_soc": 20.0}
    ]
    coordinator._record_daily_battery_snapshot(VIN)

    snapshots = coordinator._battery_capacity_models[VIN]["daily_snapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["usable_capacity_kwh"] == 64.0


def test_daily_snapshot_retention_is_bounded(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Snapshot storage retains the configured newest 400 records per vehicle."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 65.0, "delta_soc": 20.0}],
        "daily_snapshots": [
            {"date": f"2025-01-{day:03d}", "usable_capacity_kwh": 64.0}
            for day in range(1, 401)
        ],
    }

    coordinator._record_daily_battery_snapshot(VIN)

    assert len(coordinator._battery_capacity_models[VIN]["daily_snapshots"]) == 400


def test_capability_diagnostics_and_rescan_are_explainable(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Rescan clears only auto-disabled conclusions for a new observation run."""
    coordinator._signal_capabilities[VIN] = {
        "tonneau": {"auto_disabled": True},
        "sunroof": {"auto_disabled": False},
    }
    coordinator._capability_valid_signals[VIN] = {"SunroofInstalled"}

    diagnostics = coordinator.get_capability_diagnostics(VIN)
    assert "tonneau" in diagnostics["unsupported"]
    assert "sunroof" in diagnostics["supported"]

    coordinator.rescan_capabilities(VIN)

    assert coordinator._signal_capabilities[VIN] == {}
    assert coordinator.get_capability_diagnostics(VIN)["unsupported"] == []


def test_capacity_diagnostics_include_bounded_snapshot_trend(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The trend shows daily evidence without changing the live estimate."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 64.0, "delta_soc": 20.0}],
        "daily_snapshots": [
            {"date": "2026-01-02", "usable_capacity_kwh": 64.0},
            {"date": "2026-01-01", "usable_capacity_kwh": 65.0},
        ],
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["daily_snapshots_30d"] == [
        {"date": "2026-01-02", "usable_capacity_kwh": 64.0},
        {"date": "2026-01-01", "usable_capacity_kwh": 65.0},
    ]
    assert diagnostics["daily_snapshot_change_30d_kwh"] == -1.0
    assert diagnostics["daily_snapshot_trend_stable"] is False


def _snapshots_with_symmetric_noise(center: float, spread: float) -> list[dict[str, Any]]:
    """Return 7 daily snapshots whose median absolute deviation equals spread."""
    values = [
        center - spread,
        center - spread,
        center - spread,
        center,
        center + spread,
        center + spread,
        center + spread,
    ]
    return [
        {"date": f"2026-01-0{day}", "usable_capacity_kwh": value}
        for day, value in enumerate(values, start=1)
    ]


def test_trend_stability_treats_a_small_and_a_large_pack_the_same_relative_noise(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The same 1.4% noise level is judged identically for a 50 kWh and 100 kWh pack."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 50.0, "delta_soc": 20.0}],
        "daily_snapshots": _snapshots_with_symmetric_noise(50.0, 0.7),
    }
    small_pack_stable = coordinator.get_battery_capacity_diagnostics(VIN)[
        "daily_snapshot_trend_stable"
    ]

    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 100.0, "delta_soc": 20.0}],
        "daily_snapshots": _snapshots_with_symmetric_noise(100.0, 1.4),
    }
    large_pack_stable = coordinator.get_battery_capacity_diagnostics(VIN)[
        "daily_snapshot_trend_stable"
    ]

    assert small_pack_stable is True
    assert large_pack_stable is True


def test_trend_stability_is_not_marked_unstable_by_a_fixed_kwh_threshold(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A larger pack with small relative noise is not wrongly marked unstable.

    Under a fixed 1.0 kWh threshold, this 1.4 kWh deviation on a 100 kWh pack
    (1.4% relative noise) would have been marked unstable even though it is
    proportionally tighter than the 0.7 kWh/50 kWh case above.
    """
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 100.0, "delta_soc": 20.0}],
        "daily_snapshots": _snapshots_with_symmetric_noise(100.0, 1.4),
    }

    assert (
        coordinator.get_battery_capacity_diagnostics(VIN)["daily_snapshot_trend_stable"]
        is True
    )


def test_trend_stability_rejects_noise_above_the_relative_threshold(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Noise exceeding 1.5% of the pack's own capacity is still marked unstable."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 50.0, "delta_soc": 20.0}],
        "daily_snapshots": _snapshots_with_symmetric_noise(50.0, 0.8),
    }

    assert (
        coordinator.get_battery_capacity_diagnostics(VIN)["daily_snapshot_trend_stable"]
        is False
    )


def test_telemetry_update_tracks_bounded_partial_frame_counts(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Frame diagnostics count partial processing without retaining payload data."""
    coordinator._telemetry_generations = {}
    coordinator._telemetry_events = {}
    coordinator._sleep_timers = {}
    coordinator._schedule_sleep_transition = lambda vin: None
    coordinator.record_telemetry_update(VIN, {"Soc", "Unknown"}, {"Soc"})

    metadata = coordinator._telemetry_metadata[VIN]
    assert metadata["frame_count"] == 1
    assert metadata["partial_frame_count"] == 1


def test_receiver_diagnostics_are_bounded_and_payload_free(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Receiver failures are counted globally without retaining message data."""
    coordinator.record_telemetry_receiver_error("malformed_json")
    coordinator.record_telemetry_receiver_error("missing_vin")

    assert coordinator.get_telemetry_receiver_diagnostics() == {
        "malformed_json": 1,
        "missing_vin": 1,
        "unmanaged_vin": 0,
        "processing_error": 0,
    }


def test_setup_validation_warns_when_telemetry_registration_is_required(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Changing telemetry settings takes precedence over a prior registration."""
    coordinator.entry.options["telemetry_registration_required_vins"] = [VIN]
    coordinator._telemetry_metadata[VIN] = {"registration_status": "registered"}

    assert coordinator.get_telemetry_setup_validation(VIN)["registration"] == (
        "registration_required"
    )