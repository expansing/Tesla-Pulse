"""Regression tests for the usable-capacity estimator state machine."""

from __future__ import annotations

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