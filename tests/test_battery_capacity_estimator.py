"""Regression tests for the usable-capacity estimator state machine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
        "battery_soh_confidence": 100.0,
        "estimated_battery_soh": 88.4,
    }


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

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 70.0


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
    first_capacity = coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"]

    # The 49 kWh reading has already been consumed; a later Soc-only frame
    # must not reuse it to fabricate a new capacity sample.
    asyncio.run(
        process_frame(
            [{"key": "Soc", "value": {"doubleValue": 71}}],
            "2026-01-01T00:00:45Z",
        )
    )

    assert first_capacity == 70.0
    assert (
        coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 70.0
    )

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

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == round(
        49.5 / 72 * 100, 2
    )


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
        assert metrics["estimated_usable_capacity"] == 70.0
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


def test_live_energy_and_soc_immediately_update_soh(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A fresh BMS energy/SOC pair overrides stale historical window evidence."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 60.0, "delta_soc": 40.0}]
    }

    coordinator.record_battery_capacity_sample(VIN, 70, 49, observed_at=START)

    assert coordinator._battery_capacity_metrics(VIN) == {
        "estimated_usable_capacity": 70.0,
        "battery_soh_confidence": 70.0,
        "estimated_battery_soh": 95.2,
    }
    assert coordinator.get_battery_soh_diagnostics(VIN) == {
        "usable_capacity_kwh": 70.0,
        "original_usable_capacity_kwh": 73.5,
        "estimation_method": "live_energy_soc",
        "live_capacity_observed_at": START.isoformat(),
    }


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
        "battery_soh_confidence": 80.0,
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
    """The estimate uses SOC span as the window weight rather than an average."""
    model = coordinator._battery_capacity_models.setdefault(VIN, {})
    model["accepted_windows"] = [
        {"capacity_kwh": 60.0, "delta_soc": 20.0},
        {"capacity_kwh": 65.0, "delta_soc": 60.0},
        {"capacity_kwh": 70.0, "delta_soc": 20.0},
    ]

    assert coordinator._battery_capacity_metrics(VIN)["estimated_usable_capacity"] == 65.0


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


def test_live_reset_discards_the_transient_live_capacity_observation(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A live reset prevents the latest BMS sample from surviving the reset."""
    coordinator.record_battery_capacity_sample(VIN, 70, 49, observed_at=START)

    coordinator.reset_battery_capacity_history(VIN, "live")

    assert coordinator._battery_capacity_metrics(VIN) == {}
    assert "latest_live_capacity_observation" not in coordinator._battery_capacity_models[VIN]


def test_invalid_live_capacity_falls_back_to_valid_window_evidence(
    coordinator: TeslaVehicleCommandCoordinator,
) -> None:
    """A live capacity above the configured original capacity cannot override SOH."""
    coordinator._battery_capacity_models[VIN] = {
        "accepted_windows": [{"capacity_kwh": 60.0, "delta_soc": 40.0}],
        "latest_live_capacity_observation": {
            "soc": 70.0,
            "energy": 56.0,
            "capacity_kwh": 80.0,
            "timestamp": START.isoformat(),
        },
    }

    assert coordinator._battery_capacity_metrics(VIN) == {
        "estimated_usable_capacity": 60.0,
        "battery_soh_confidence": 40.0,
        "estimated_battery_soh": 81.6,
    }
    assert coordinator.get_battery_capacity_diagnostics(VIN)["estimation_method"] == "window_slope"


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