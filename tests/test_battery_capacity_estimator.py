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
    """Build an accepted window via the recorder-only direction-based path.

    This is a test convenience for seeding aggregation-focused tests (median,
    average, recency, outlier rejection) that don't care how a window was
    formed. It intentionally uses source="recorder" because live windows are
    bounded only by charge-state transitions (see the dedicated live-window
    tests below), not by same-direction SOC tracking.
    """
    coordinator.record_battery_capacity_sample(
        VIN, start_soc, start_energy, observed_at=start_time, source="recorder"
    )
    midpoint_soc = (start_soc + end_soc) / 2
    midpoint_energy = (start_energy + end_energy) / 2
    coordinator.record_battery_capacity_sample(
        VIN,
        midpoint_soc,
        midpoint_energy,
        observed_at=start_time + timedelta(minutes=30),
        source="recorder",
    )
    coordinator.record_battery_capacity_sample(
        VIN,
        end_soc,
        end_energy,
        observed_at=start_time + timedelta(hours=1),
        source="recorder",
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


def test_recorder_small_reversal_jitter_does_not_finalize_the_active_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A single opposite-direction step at or below the 1% tolerance is noise.

    This direction-based tolerance only applies to recorder-imported history,
    which has no charge-state signal to bound windows by transitions.
    """
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 60, 39.0, observed_at=START + timedelta(minutes=10), source="recorder"
    )
    # A momentary regen-sized dip (0.5% <= 1.0% tolerance) must not close the window.
    coordinator.record_battery_capacity_sample(
        VIN, 59.5, 38.7, observed_at=START + timedelta(minutes=11), source="recorder"
    )

    active = coordinator._battery_capacity_models[VIN]["active_window"]
    assert active is not None
    assert active["direction"] == "charging"
    assert active["reversal_streak"] == 1
    assert active["last"]["soc"] == 60.0
    assert active["last"]["energy"] == 39.0


def test_recorder_repeated_small_reversals_finalize_the_active_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A second consecutive small reversal exhausts the noise tolerance streak."""
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 65, 42.25, observed_at=START + timedelta(minutes=10), source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 64.5, 41.925, observed_at=START + timedelta(minutes=11), source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 64.0, 41.6, observed_at=START + timedelta(minutes=12), source="recorder"
    )

    model = coordinator._battery_capacity_models[VIN]
    new_active = model["active_window"]
    assert new_active["start"]["soc"] == 64.0
    assert new_active["direction"] is None
    assert new_active["reversal_streak"] == 0
    # The closed run's 15-point span is below the minimum and is dropped silently.
    assert model.get("accepted_windows", []) == []


def test_recorder_reversal_beyond_tolerance_finalizes_immediately(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A reversal step larger than the tolerance closes the window with no grace."""
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 70, 45.5, observed_at=START + timedelta(minutes=10), source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 68, 44.2, observed_at=START + timedelta(minutes=11), source="recorder"
    )

    model = coordinator._battery_capacity_models[VIN]
    assert model["accepted_windows"][0]["capacity_kwh"] == 65.0
    assert model["active_window"]["start"]["soc"] == 68.0


def test_same_frame_soc_and_energy_are_recorded_as_the_latest_live_sample(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Soc and EnergyRemaining in the same frame are tracked as one live sample."""
    consumer = TelemetryConsumer(SimpleNamespace(), coordinator)
    response = coordinator._empty_response()

    asyncio.run(
        consumer._process_vehicle_signals(
            VIN,
            response,
            {
                "data": [
                    {"key": "Soc", "value": {"doubleValue": 70}},
                    {"key": "EnergyRemaining", "value": {"doubleValue": 49}},
                ],
                "createdAt": "2026-01-01T00:00:00Z",
            },
        )
    )

    sample = coordinator._battery_capacity_models[VIN]["latest_live_sample"]
    assert sample["soc"] == 70.0
    assert sample["energy"] == 49.0


def test_separate_frames_never_pair_even_with_no_gap_between_them(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Soc and EnergyRemaining in different frames are never combined.

    Live window boundaries only need an occasional precisely-paired sample
    (see coordinator.record_battery_charge_state), so pairing across frames
    is no longer tolerated even when the frames are immediately adjacent;
    that would risk anchoring a session boundary on values offset in time.
    """
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
            "2026-01-01T00:00:00Z",
        )
    )

    assert "latest_live_sample" not in coordinator._battery_capacity_models.get(VIN, {})


def test_stale_companion_signal_does_not_pair_across_frames(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A field reported minutes apart from its companion is never paired.

    Fields sent in separate frames must never combine into one sample,
    regardless of how close together they arrive.
    """
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
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2)
    )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0
    # Charging ending immediately opens the resting window, anchored where charging left off.
    model = coordinator._battery_capacity_models[VIN]
    assert len(model["accepted_windows"]) == 1
    assert model["live_window"]["start"]["soc"] == 80.0


def test_resting_window_spans_a_long_gap_between_drive_and_park(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A resting session covers driving plus parked standby as one window.

    Telemetry while parked can be sparse, so the resting window must not be
    split by a long gap the way recorder-imported history is; it stays open
    until the vehicle starts charging again, however long that takes.
    """
    coordinator.record_battery_charge_state(VIN, "Stopped", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START, source="live"
    )
    # A multi-day gap while parked, well beyond the 6-hour recorder gap limit.
    coordinator.record_battery_capacity_sample(
        VIN, 24, 15.6, observed_at=START + timedelta(days=3), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Charging", observed_at=START + timedelta(days=3)
    )

    model = coordinator._battery_capacity_models[VIN]
    assert len(model["accepted_windows"]) == 1
    assert model["accepted_windows"][0]["capacity_kwh"] == 65.0
    assert model["accepted_windows"][0]["direction"] == "discharging"


def test_charge_stop_updates_an_estimate_with_more_weighted_new_evidence(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A larger live run outweighs a smaller older estimate at any charge limit."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 60.0, "delta_soc": 20.0}]
    }

    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Complete", observed_at=START + timedelta(hours=2)
    )

    assert coordinator._battery_capacity_metrics(VIN) == {
        "estimated_usable_capacity": 65.0,
        "battery_soh_confidence": 30.0,
        "estimated_battery_soh": 88.4,
    }


def test_charge_stop_uses_the_latest_sample_regardless_of_its_final_step_size(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The freshest live sample anchors the close, however small its last step was."""
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 79.99, 51.9935, observed_at=START + timedelta(hours=2), source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2, minutes=1), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2, minutes=1)
    )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0


def test_repeated_charge_stop_frames_do_not_duplicate_capacity_windows(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Repeated stopped states do not re-score the completed charging run."""
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2)
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2, minutes=1)
    )

    assert len(coordinator._battery_capacity_models[VIN]["accepted_windows"]) == 1


def test_stale_anchor_at_transition_discards_the_window_and_recovers_on_next_sample(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A transition without a fresh anchor is not scored with an offset value.

    If the freshest known live sample is older than the anchor staleness
    guard (15 minutes) relative to the transition, the ending window is
    discarded rather than closed with a value that may no longer reflect
    reality; the next fresh sample re-anchors a new window instead.
    """
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="live"
    )
    # No further live sample arrives until well after charging ends.
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2)
    )

    model = coordinator._battery_capacity_models[VIN]
    assert model.get("accepted_windows", []) == []
    assert model["live_window"] is None

    # The next fresh sample re-anchors a new window from that point onward.
    coordinator.record_battery_capacity_sample(
        VIN, 79, 51.35, observed_at=START + timedelta(hours=2, minutes=5), source="live"
    )
    assert model["live_window"]["start"]["soc"] == 79.0


def test_stale_persisted_sample_does_not_seed_a_window_on_first_observed_state(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A stale sample that survived a restart cannot seed the very first window.

    battery_capacity_models are persisted, so latest_live_sample can be
    hours old after a restart even though charging_state_mode was reset.
    The first observed charge-state transition must apply the same
    15-minute freshness guard as every later transition, or a stale
    sample would seed a window whose start no longer reflects reality.
    """
    coordinator._battery_capacity_models[VIN] = {
        "latest_live_sample": {
            "soc": 50.0,
            "energy": 32.5,
            "timestamp": START.isoformat(),
            "temperature": None,
        },
    }

    coordinator.record_battery_charge_state(
        VIN, "Charging", observed_at=START + timedelta(hours=2)
    )

    assert coordinator._battery_capacity_models[VIN].get("live_window") is None


def test_fresh_persisted_sample_still_seeds_a_window_on_first_observed_state(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A still-fresh persisted sample can seed the very first window as before."""
    coordinator._battery_capacity_models[VIN] = {
        "latest_live_sample": {
            "soc": 50.0,
            "energy": 32.5,
            "timestamp": START.isoformat(),
            "temperature": None,
        },
    }

    coordinator.record_battery_charge_state(
        VIN, "Charging", observed_at=START + timedelta(minutes=5)
    )

    model = coordinator._battery_capacity_models[VIN]
    assert model["live_window"]["start"]["soc"] == 50.0


def test_rapid_charge_state_flapping_with_fresh_anchors_does_not_corrupt_state(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Frequent short transitions are dropped safely, and scoring recovers after."""
    t = START
    soc = 50.0
    for _ in range(5):
        coordinator.record_battery_charge_state(VIN, "Charging", observed_at=t)
        coordinator.record_battery_capacity_sample(
            VIN, soc, soc * 0.65, observed_at=t, source="live"
        )
        t += timedelta(seconds=3)
        soc += 0.2
        coordinator.record_battery_capacity_sample(
            VIN, soc, soc * 0.65, observed_at=t, source="live"
        )
        coordinator.record_battery_charge_state(VIN, "Stopped", observed_at=t)
        t += timedelta(seconds=3)

    # Each flapped session's tiny SOC span is below the minimum and dropped silently.
    model = coordinator._battery_capacity_models[VIN]
    assert model.get("accepted_windows", []) == []

    # A real, qualifying session afterward still scores correctly.
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=t)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=t, source="live"
    )
    t += timedelta(hours=2)
    coordinator.record_battery_capacity_sample(VIN, 80, 52, observed_at=t, source="live")
    coordinator.record_battery_charge_state(VIN, "Stopped", observed_at=t)

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0


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
                    "soc": 50,
                    "energy": 32.5,
                    "timestamp": START.isoformat(),
                    "temperature": None,
                },
                "direction": "charging",
                "source": "live",
                "temp_sum": 0.0,
                "temp_count": 0,
            }
        }
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2)
    )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0


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

    model = coordinator._battery_capacity_models[VIN]
    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0
    assert len(model["accepted_windows"]) == 1
    assert model["live_window"]["start"]["soc"] == 80.0


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


def test_recorder_long_gap_closes_the_previous_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A gap longer than six hours finalizes a completed same-direction run.

    This gap-based closing only applies to recorder-imported history, which
    has no charge-state signal; a live resting window intentionally spans
    arbitrarily long gaps instead (see test_resting_window_spans_a_long_gap).
    """
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 75, 48.75, observed_at=START + timedelta(hours=1), source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 76, 49.4, observed_at=START + timedelta(hours=8), source="recorder"
    )

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0
    assert coordinator._battery_capacity_models[VIN]["active_window"]["start"]["soc"] == 76.0


def test_recorder_import_finalizes_a_completed_window_after_silent_gap(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A finished charge is scored even when no later state change arrives.

    Recorder stops emitting significant battery states once charging ends.
    The importer must therefore use elapsed silence as the same real gap
    boundary it otherwise only sees when a later pair finally arrives.
    """
    coordinator.record_battery_capacity_sample(
        VIN, 54, 36.0, observed_at=START, source="recorder"
    )
    last_sample_time = START + timedelta(hours=2)
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52.34, observed_at=last_sample_time, source="recorder"
    )

    finalized = coordinator._finalize_stale_recorder_window(
        VIN, last_sample_time + timedelta(hours=6, seconds=1)
    )

    model = coordinator._battery_capacity_models[VIN]
    assert finalized is True
    assert model["active_window"] is None
    assert model["accepted_windows"][0]["capacity_kwh"] == pytest.approx(62.846)


def test_recorder_import_keeps_a_window_open_before_the_silent_gap(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A recent Recorder session remains open rather than being fragmented."""
    coordinator.record_battery_capacity_sample(
        VIN, 54, 36.0, observed_at=START, source="recorder"
    )
    last_sample_time = START + timedelta(hours=2)
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52.34, observed_at=last_sample_time, source="recorder"
    )

    finalized = coordinator._finalize_stale_recorder_window(
        VIN, last_sample_time + timedelta(hours=5, minutes=59)
    )

    assert finalized is False
    assert coordinator._battery_capacity_models[VIN]["active_window"] is not None


def test_recorder_import_stale_finalizer_does_not_touch_non_recorder_windows(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The Recorder finalizer cannot close a different source's active window."""
    coordinator._battery_capacity_models[VIN] = {
        "active_window": {
            "source": "live",
            "last": {"timestamp": START.isoformat()},
        }
    }

    finalized = coordinator._finalize_stale_recorder_window(
        VIN, START + timedelta(days=1)
    )

    assert finalized is False
    assert coordinator._battery_capacity_models[VIN]["active_window"]["source"] == "live"


def test_recorder_import_stale_finalizer_ignores_malformed_timestamp(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Corrupt persisted state cannot crash or spuriously close a window."""
    coordinator._battery_capacity_models[VIN] = {
        "active_window": {
            "source": "recorder",
            "last": {"timestamp": "not-a-timestamp"},
        }
    }

    finalized = coordinator._finalize_stale_recorder_window(
        VIN, START + timedelta(days=1)
    )

    assert finalized is False
    assert coordinator._battery_capacity_models[VIN]["active_window"] is not None


def test_recorder_import_batches_do_not_fragment_an_in_progress_session(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Samples fed in separate batches still combine into one session.

    Regression test for a real bug: async_import_battery_history used to
    reset and force-close the recorder active_window on every call, so a
    session split across two restarts by a batch boundary landing
    mid-session was fragmented into pieces each below the 20-point minimum
    and silently lost forever. Recorder samples spanning >=20 SOC points
    must still combine into one window even when recorded in two groups
    with nothing closing the window in between.
    """
    # First "import run" covers only part of the session.
    for step in range(12):
        coordinator.record_battery_capacity_sample(
            VIN,
            50 + step,
            32.5 + step * 0.65,
            observed_at=START + timedelta(minutes=step),
            source="recorder",
        )
    assert coordinator._battery_capacity_models[VIN].get("accepted_windows", []) == []

    # A later "import run" continues the same still-open session.
    for step in range(12, 21):
        coordinator.record_battery_capacity_sample(
            VIN,
            50 + step,
            32.5 + step * 0.65,
            observed_at=START + timedelta(minutes=step),
            source="recorder",
        )
    coordinator.flush_active_battery_window(VIN)

    model = coordinator._battery_capacity_models[VIN]
    assert len(model["accepted_windows"]) == 1
    assert model["accepted_windows"][0]["delta_soc"] == 20.0


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
        {"accepted_windows": [window], "active_window": {"source": "recorder"}}
    )

    assert migrated["model_version"] == 3
    assert migrated["accepted_windows"] == [window]
    assert migrated["rejected_windows"] == []
    assert migrated["active_window"] == {"source": "recorder"}


def test_migrates_a_legacy_live_active_window_into_the_new_live_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A pre-redesign live window becomes the starting point of the new one."""
    migrated = coordinator._migrate_battery_capacity_model(
        {"active_window": {"source": "live", "direction": "charging"}}
    )

    assert migrated["active_window"] is None
    assert migrated["live_window"] == {"source": "live", "direction": "charging"}
    assert migrated["charging_state_mode"] == "charging"


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
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 70, 49, observed_at=START, source="live"
    )

    coordinator.reset_battery_capacity_history(VIN, "live")

    assert coordinator._battery_capacity_metrics(VIN) == {}
    assert coordinator._battery_capacity_models[VIN]["live_window"] is None
    assert "charging_state_mode" not in coordinator._battery_capacity_models[VIN]


def test_diagnostics_expose_a_recorder_window_still_in_progress(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """An open Recorder session that hasn't reversed or gapped yet is visible.

    A Recorder window can legitimately stay open for a long time by design
    (it only closes on a real reversal or a 6-hour gap, to avoid
    fragmenting one continuous session across import batches). Without
    this, that accumulating evidence would be invisible until it closes.
    """
    coordinator.record_battery_capacity_sample(
        VIN, 54, 36.0, observed_at=START, source="recorder"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 70, 46.5, observed_at=START + timedelta(hours=1), source="recorder"
    )

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["recorder_window_in_progress"] == {
        "start_soc": 54.0,
        "current_soc": 70.0,
        "delta_soc_so_far": 16.0,
        "capacity_kwh_so_far": 65.625,
        "direction": "charging",
        "source": "recorder",
        "start_time": START.isoformat(),
        "last_update_time": (START + timedelta(hours=1)).isoformat(),
    }
    assert diagnostics["live_window_in_progress"] is None


def test_diagnostics_expose_a_live_window_still_in_progress(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """An open live charging session is visible before it closes."""
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 54, 36.0, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 70, 46.5, observed_at=START + timedelta(hours=1), source="live"
    )

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["live_window_in_progress"]["current_soc"] == 70.0
    assert diagnostics["live_window_in_progress"]["delta_soc_so_far"] == 16.0
    assert diagnostics["live_window_in_progress"]["direction"] == "charging"


def test_diagnostics_in_progress_windows_are_none_when_nothing_is_open(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """No open window in either mechanism reports None, not a crash."""
    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["recorder_window_in_progress"] is None
    assert diagnostics["live_window_in_progress"] is None


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
            "regression_r_squared": None,
            "charge_energy_added_kwh": None,
            "implied_charging_loss_pct": None,
            "charge_type": None,
        }
    ]


def test_capacity_diagnostics_expose_history_import_bookkeeping(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """History-import timestamps and pair count are surfaced for troubleshooting.

    Without these, there is no way to tell whether the Recorder catch-up
    import ran recently, found nothing, or hasn't run since a given restart.
    """
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"source": "recorder", "capacity_kwh": 65.0, "delta_soc": 20.0}],
        "history_import_attempted_at": "2026-08-13T04:00:00+00:00",
        "history_import_completed_at": "2026-08-13T04:00:00+00:00",
        "history_import_last_pair_count": 7,
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["history_import_attempted_at"] == "2026-08-13T04:00:00+00:00"
    assert diagnostics["history_import_completed_at"] == "2026-08-13T04:00:00+00:00"
    assert diagnostics["history_import_last_pair_count"] == 7


def test_capacity_diagnostics_history_import_fields_default_to_none(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A vehicle with no import attempt yet reports None rather than an error."""
    coordinator._battery_capacity_models[VIN] = {}

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["history_import_attempted_at"] is None
    assert diagnostics["history_import_completed_at"] is None
    assert diagnostics["history_import_last_pair_count"] is None


def test_history_import_throttle_never_blocks_after_a_successful_run(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A successful import (even with zero new pairs) is retried every restart.

    Regression test for a real bug: throttling on history_import_attempted_at
    silently blocked the incremental catch-up import for a full day after any
    restart, including successful ones, so new charge/discharge sessions were
    never re-scanned until the next restart at least 24h later.
    """
    now = START + timedelta(hours=1)
    model = {
        "history_import_attempted_at": START.isoformat(),
        "history_import_completed_at": START.isoformat(),
        "history_import_last_pair_count": 0,
    }

    assert coordinator._history_import_is_throttled(model, now) is False


def test_history_import_throttle_blocks_retries_after_a_recent_failure(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A genuine failure still throttles retries within the retry interval."""
    now = START + timedelta(hours=1)
    model = {
        "history_import_attempted_at": START.isoformat(),
        "history_import_last_failure_at": START.isoformat(),
    }

    assert coordinator._history_import_is_throttled(model, now) is True


def test_history_import_throttle_expires_after_the_retry_interval(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A failure older than the retry interval no longer blocks a retry."""
    now = START + timedelta(days=2)
    model = {"history_import_last_failure_at": START.isoformat()}

    assert coordinator._history_import_is_throttled(model, now) is False


def test_history_import_throttle_ignores_a_never_attempted_model(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A vehicle with no import history yet is never throttled."""
    assert coordinator._history_import_is_throttled({}, START) is False


def test_battery_history_import_skips_an_overlapping_run(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The setup and periodic import cannot race Recorder window state."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_import() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    coordinator._async_import_battery_history = slow_import

    async def run_imports() -> None:
        first_import = asyncio.create_task(coordinator.async_import_battery_history())
        await started.wait()
        await coordinator.async_import_battery_history()
        release.set()
        await first_import

    asyncio.run(run_imports())

    assert calls == 1
    assert coordinator._battery_history_import_in_progress is False


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


def test_implied_soc_zero_reserve_returns_the_median_gap_from_proportional(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A consistent gap between observed energy and a pure-proportional model is measured.

    If EnergyRemaining were exactly proportional to SOC, each observation's
    energy would equal slope/100 * soc exactly; a positive median gap is
    evidence of a reserve that persists even at indicated 0% SOC.
    """
    observations = [
        {"soc": 50.0, "energy": 32.0},
        {"soc": 90.0, "energy": 57.4},
    ]

    result = coordinator._implied_soc_zero_reserve_kwh(observations, 60.0)

    assert result == pytest.approx(2.7)


def test_implied_soc_zero_reserve_returns_none_without_observations(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """No high-SOC evidence yet means no implied-reserve estimate."""
    assert coordinator._implied_soc_zero_reserve_kwh([], 60.0) is None


def test_capacity_diagnostics_expose_implied_soc_zero_reserve(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The published estimate and high-SOC observations combine into a reserve estimate."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 60.0, "delta_soc": 20.0, "source": "live"}],
        "high_soc_observations": [
            {"soc": 50.0, "energy": 32.0, "capacity_kwh": 64.0},
            {"soc": 90.0, "energy": 57.4, "capacity_kwh": 63.8},
        ],
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["implied_soc_zero_reserve_kwh"] == pytest.approx(2.7)


def test_capacity_diagnostics_implied_soc_zero_reserve_is_none_without_evidence(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """No accepted windows means no published estimate to compare against."""
    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["implied_soc_zero_reserve_kwh"] is None


def _windows_with_intercepts(intercepts: list[float]) -> list[dict[str, Any]]:
    """Return minimal valid accepted windows, each carrying a regression intercept."""
    return [
        {
            "capacity_kwh": 62.0,
            "delta_soc": 20.0,
            "regression_intercept_kwh": intercept,
        }
        for intercept in intercepts
    ]


def test_regression_intercept_stability_requires_the_minimum_sample_count(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Fewer than 10 windows with a fitted intercept is not enough to call it stable.

    Guards against declaring the SOC=0% reserve hypothesis confirmed from a
    handful of sessions, per the recommendation to validate over 10-20
    completed windows before treating the intercept as a genuine offset.
    """
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": _windows_with_intercepts([3.0] * 9),
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["regression_intercept_sample_count"] == 9
    assert diagnostics["regression_intercept_median_kwh"] == 3.0
    assert diagnostics["regression_intercept_mad_kwh"] == 0.0
    assert diagnostics["regression_intercept_stable"] is False


def test_regression_intercept_stability_is_true_for_a_tight_cluster(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A tight cluster of >=10 fitted intercepts is reported as stable."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": _windows_with_intercepts(
            [2.6, 2.8, 2.9, 3.0, 3.0, 3.1, 3.2, 3.3, 3.3, 3.4]
        ),
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["regression_intercept_sample_count"] == 10
    assert diagnostics["regression_intercept_stable"] is True


def test_regression_intercept_stability_is_false_for_scattered_values(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A wide spread across >=10 fitted intercepts is not reported as stable."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": _windows_with_intercepts(
            [-4.0, -2.0, 0.0, 1.0, 3.0, 5.0, 6.0, 8.0, 9.0, 10.0]
        ),
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["regression_intercept_sample_count"] == 10
    assert diagnostics["regression_intercept_stable"] is False


def test_regression_intercept_diagnostics_default_when_no_windows_have_a_fit(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Windows without a computed regression fit contribute nothing to the check."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 62.0, "delta_soc": 20.0}],
    }

    diagnostics = coordinator.get_battery_capacity_diagnostics(VIN)

    assert diagnostics["regression_intercept_sample_count"] == 0
    assert diagnostics["regression_intercept_median_kwh"] is None
    assert diagnostics["regression_intercept_mad_kwh"] is None
    assert diagnostics["regression_intercept_stable"] is False


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


# --- Regression (slope/intercept/R²/RMSE) diagnostics -----------------------
#
# These are an additional diagnostic layer collected from the same-frame
# samples seen while a window is open. They never change capacity_kwh, which
# remains the simple endpoint-based delta_energy/delta_soc*100 calculation.


def test_linear_regression_returns_none_below_minimum_sample_count() -> None:
    """A two-point window has nothing meaningful to fit a line against."""
    assert (
        TeslaVehicleCommandCoordinator._linear_regression(
            [(50.0, 32.5), (80.0, 52.0)]
        )
        is None
    )


def test_linear_regression_returns_none_when_soc_has_no_spread() -> None:
    """A degenerate (constant SOC) sample set cannot be fit against SOC."""
    assert (
        TeslaVehicleCommandCoordinator._linear_regression(
            [(50.0, 10.0), (50.0, 12.0), (50.0, 11.0)]
        )
        is None
    )


def test_linear_regression_reports_a_perfect_fit_for_linear_data() -> None:
    """Points falling exactly on a line report R²=1.0 and near-zero RMSE."""
    points = [(50.0, 32.5), (60.0, 39.0), (70.0, 45.5), (80.0, 52.0)]

    regression = TeslaVehicleCommandCoordinator._linear_regression(points)

    assert regression is not None
    slope, intercept, r_squared, rmse = regression
    assert slope == pytest.approx(0.65)
    assert intercept == pytest.approx(0.0, abs=1e-9)
    assert r_squared == pytest.approx(1.0)
    assert rmse == pytest.approx(0.0, abs=1e-9)


def test_linear_regression_reports_a_poor_fit_for_non_linear_data() -> None:
    """Points that bounce between endpoint values report a low R²."""
    points = [(50.0, 32.5), (60.0, 52.0), (70.0, 32.5), (80.0, 52.0)]

    regression = TeslaVehicleCommandCoordinator._linear_regression(points)

    assert regression is not None
    assert regression[2] == pytest.approx(0.2, abs=0.01)


def test_live_charging_window_records_regression_diagnostics_from_intermediate_samples(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """Intermediate same-frame samples produce regression stats on the window.

    capacity_kwh itself is still computed only from the start/end endpoints.
    """
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 60, 39.0, observed_at=START + timedelta(minutes=30), source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 70, 45.5, observed_at=START + timedelta(hours=1), source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52.0, observed_at=START + timedelta(hours=1, minutes=30), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=1, minutes=30)
    )

    window = coordinator._battery_capacity_models[VIN]["accepted_windows"][0]
    assert window["capacity_kwh"] == 65.0
    assert window["regression_sample_count"] == 4
    assert window["regression_slope_kwh_per_soc"] == pytest.approx(0.65)
    assert window["regression_r_squared"] == pytest.approx(1.0)


def test_regression_fields_are_none_with_fewer_than_three_samples(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A two-point window has no fit; the R² gate does not apply to it."""
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START + timedelta(hours=2), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2)
    )

    window = coordinator._battery_capacity_models[VIN]["accepted_windows"][0]
    assert window["regression_r_squared"] is None
    assert window["regression_slope_kwh_per_soc"] is None
    assert window["regression_sample_count"] == 2


def test_poor_linear_fit_rejects_a_window_despite_plausible_endpoints(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A window whose intermediate samples don't track a line is rejected.

    The endpoints alone would look like an ordinary 65 kWh session, but the
    same-frame samples collected in between bounce between the two endpoint
    values instead of tracking a line - evidence the session isn't
    trustworthy even though its start/end values are individually plausible.
    """
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 60, 52.0, observed_at=START + timedelta(minutes=20), source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 70, 32.5, observed_at=START + timedelta(minutes=40), source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52.0, observed_at=START + timedelta(hours=1), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=1)
    )

    model = coordinator._battery_capacity_models[VIN]
    assert model.get("accepted_windows", []) == []
    assert len(model["rejected_windows"]) == 1
    rejected = model["rejected_windows"][0]
    assert rejected["capacity_kwh"] == 65.0
    assert rejected["regression_r_squared"] == pytest.approx(0.2, abs=0.01)
    assert "poor linear fit" in rejected["rejection_reason"]


# --- charge_energy_added (charger-side) cross-check --------------------------
#
# Compares the charger-metered energy added this session against the
# BMS-measured delta_energy for the same window, for charging-direction
# windows only. Also a diagnostic layer only; never feeds into capacity_kwh.


def test_charge_energy_added_cross_check_computes_implied_loss_for_ac_charging(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """AC charger-metered energy vs BMS delta_energy yields an implied loss %."""
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN,
        50,
        32.5,
        observed_at=START,
        source="live",
        charge_energy_added_kwh=0.0,
        charge_type="ac",
    )
    coordinator.record_battery_capacity_sample(
        VIN,
        80,
        52.0,
        observed_at=START + timedelta(hours=2),
        source="live",
        charge_energy_added_kwh=21.0,
        charge_type="ac",
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2)
    )

    window = coordinator._battery_capacity_models[VIN]["accepted_windows"][0]
    assert window["charge_type"] == "ac"
    assert window["charge_energy_added_kwh"] == 21.0
    # BMS measured 19.5 kWh added against 21.0 kWh metered by the charger.
    assert window["implied_charging_loss_pct"] == pytest.approx(7.14, abs=0.01)


def test_charge_energy_added_cross_check_distinguishes_dc_charging(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """DC (Supercharging) sessions are tagged separately from AC sessions."""
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN,
        50,
        32.5,
        observed_at=START,
        source="live",
        charge_energy_added_kwh=0.0,
        charge_type="dc",
    )
    coordinator.record_battery_capacity_sample(
        VIN,
        80,
        52.0,
        observed_at=START + timedelta(hours=1),
        source="live",
        charge_energy_added_kwh=20.0,
        charge_type="dc",
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=1)
    )

    window = coordinator._battery_capacity_models[VIN]["accepted_windows"][0]
    assert window["charge_type"] == "dc"
    assert window["charge_energy_added_kwh"] == 20.0
    assert window["implied_charging_loss_pct"] == pytest.approx(2.5, abs=0.01)


def test_charge_energy_added_fields_are_none_for_discharging_windows(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """The charger cross-check only applies to charging-direction windows."""
    coordinator.record_battery_charge_state(VIN, "Stopped", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN, 80, 52, observed_at=START, source="live"
    )
    coordinator.record_battery_capacity_sample(
        VIN, 50, 32.5, observed_at=START + timedelta(hours=2), source="live"
    )
    coordinator.record_battery_charge_state(
        VIN, "Charging", observed_at=START + timedelta(hours=2)
    )

    window = coordinator._battery_capacity_models[VIN]["accepted_windows"][0]
    assert window["direction"] == "discharging"
    assert window["charge_energy_added_kwh"] is None
    assert window["implied_charging_loss_pct"] is None
    assert window["charge_type"] is None


def test_charge_energy_added_cross_check_skips_when_added_delta_is_not_positive(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A non-increasing charger-metered value (e.g. reset mid-session) is skipped."""
    coordinator.record_battery_charge_state(VIN, "Charging", observed_at=START)
    coordinator.record_battery_capacity_sample(
        VIN,
        50,
        32.5,
        observed_at=START,
        source="live",
        charge_energy_added_kwh=5.0,
        charge_type="ac",
    )
    coordinator.record_battery_capacity_sample(
        VIN,
        80,
        52.0,
        observed_at=START + timedelta(hours=2),
        source="live",
        charge_energy_added_kwh=5.0,
        charge_type="ac",
    )
    coordinator.record_battery_charge_state(
        VIN, "Stopped", observed_at=START + timedelta(hours=2)
    )

    window = coordinator._battery_capacity_models[VIN]["accepted_windows"][0]
    assert window["charge_energy_added_kwh"] is None
    assert window["implied_charging_loss_pct"] is None
    assert window["charge_type"] == "ac"


def test_telemetry_frame_threads_dc_charge_energy_added_into_the_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """DCChargingPower/DCChargingEnergyIn signals flow through to the window."""
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
                {"key": "DCChargingPower", "value": {"doubleValue": 50}},
                {"key": "DCChargingEnergyIn", "value": {"doubleValue": 0.0}},
            ],
            "2026-01-01T00:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [
                {"key": "Soc", "value": {"doubleValue": 80}},
                {"key": "EnergyRemaining", "value": {"doubleValue": 52}},
                {"key": "DCChargingPower", "value": {"doubleValue": 50}},
                {"key": "DCChargingEnergyIn", "value": {"doubleValue": 20.0}},
            ],
            "2026-01-01T01:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [
                {
                    "key": "DetailedChargeState",
                    "value": {"stringValue": "DetailedChargeStateStopped"},
                }
            ],
            "2026-01-01T01:01:00Z",
        )
    )

    window = coordinator._battery_capacity_models[VIN]["accepted_windows"][0]
    assert window["charge_type"] == "dc"
    assert window["charge_energy_added_kwh"] == 20.0
    assert window["implied_charging_loss_pct"] == pytest.approx(2.5, abs=0.01)


def test_telemetry_frame_threads_ac_charge_energy_added_into_the_window(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """ACChargingEnergyIn is used when DCChargingPower is absent or zero."""
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
                {"key": "ACChargingEnergyIn", "value": {"doubleValue": 0.0}},
            ],
            "2026-01-01T00:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [
                {"key": "Soc", "value": {"doubleValue": 80}},
                {"key": "EnergyRemaining", "value": {"doubleValue": 52}},
                {"key": "ACChargingEnergyIn", "value": {"doubleValue": 21.0}},
            ],
            "2026-01-01T02:00:00Z",
        )
    )
    asyncio.run(
        process_frame(
            [
                {
                    "key": "DetailedChargeState",
                    "value": {"stringValue": "DetailedChargeStateStopped"},
                }
            ],
            "2026-01-01T02:01:00Z",
        )
    )

    window = coordinator._battery_capacity_models[VIN]["accepted_windows"][0]
    assert window["charge_type"] == "ac"
    assert window["charge_energy_added_kwh"] == 21.0
    assert window["implied_charging_loss_pct"] == pytest.approx(7.14, abs=0.01)


def test_r_squared_gate_accepts_a_window_exactly_at_the_threshold(
    coordinator: TeslaVehicleCommandCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression fit exactly at the minimum R² threshold is still accepted.

    The gate rejects only when R² is strictly below the threshold, so a fit
    landing exactly on it should pass like any other acceptable window.
    """
    monkeypatch.setattr(
        TeslaVehicleCommandCoordinator,
        "_linear_regression",
        staticmethod(lambda points: (0.65, 0.0, 0.95, 0.01)),
    )
    active = {
        "start": {"soc": 50.0, "energy": 32.5, "timestamp": START.isoformat()},
        "last": {
            "soc": 80.0,
            "energy": 52.0,
            "timestamp": (START + timedelta(hours=1)).isoformat(),
        },
        "direction": "charging",
        "source": "live",
        "samples": [(50.0, 32.5), (60.0, 39.0), (70.0, 45.5), (80.0, 52.0)],
    }

    coordinator._finalize_or_discard_window(VIN, active)

    model = coordinator._battery_capacity_models[VIN]
    assert len(model.get("accepted_windows", [])) == 1
    assert model["accepted_windows"][0]["regression_r_squared"] == 0.95


def test_r_squared_gate_rejects_a_window_just_below_the_threshold(
    coordinator: TeslaVehicleCommandCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression fit just below the minimum R² threshold is rejected."""
    monkeypatch.setattr(
        TeslaVehicleCommandCoordinator,
        "_linear_regression",
        staticmethod(lambda points: (0.65, 0.0, 0.9499, 0.01)),
    )
    active = {
        "start": {"soc": 50.0, "energy": 32.5, "timestamp": START.isoformat()},
        "last": {
            "soc": 80.0,
            "energy": 52.0,
            "timestamp": (START + timedelta(hours=1)).isoformat(),
        },
        "direction": "charging",
        "source": "live",
        "samples": [(50.0, 32.5), (60.0, 39.0), (70.0, 45.5), (80.0, 52.0)],
    }

    coordinator._finalize_or_discard_window(VIN, active)

    model = coordinator._battery_capacity_models[VIN]
    assert model.get("accepted_windows", []) == []
    assert len(model["rejected_windows"]) == 1
    reason = model["rejected_windows"][0]["rejection_reason"]
    # A value that would round to the 0.95 threshold at 3 decimals (0.9499
    # -> "0.950") must not be displayable as if it had passed; require
    # enough precision to show it is actually below the threshold, and
    # the threshold itself for an unambiguous message.
    assert "0.9499" in reason
    assert "0.95" in reason
    assert "0.950)" not in reason
    assert "poor linear fit" in model["rejected_windows"][0]["rejection_reason"]