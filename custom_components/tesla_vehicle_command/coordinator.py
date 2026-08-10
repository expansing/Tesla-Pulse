"""Data coordinator for Tesla Vehicle Command."""

from __future__ import annotations

import asyncio
import logging
import math
import ssl
from datetime import datetime, timedelta, timezone
from functools import partial
from statistics import median
from typing import Any, Callable

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_COMMAND,
    API_FLEET_TELEMETRY_CONFIG,
    API_WAKE_UP,
    COMMAND_BODIES,
    COMMANDS,
    CONF_BATTERY_REFERENCE_CAPACITIES,
    CONF_TELEMETRY_HOSTNAME,
    CONF_TELEMETRY_INACTIVITY_MINUTES,
    CONF_TELEMETRY_PORT,
    CONF_TELEMETRY_REGISTRATION_REQUIRED_VINS,
    DEFAULT_TELEMETRY_INACTIVITY_MINUTES,
    DOMAIN,
    PROXY_HOST,
    PROXY_PORT,
    WAKE_TELEMETRY_TIMEOUT_SECONDS,
)
from .proxy_manager import ProxyManager

_LOGGER = logging.getLogger(__name__)

_FLEET_TELEMETRY_FIELDS = {
    # Charging
    "Soc": {"interval_seconds": 60},
    "BatteryLevel": {"interval_seconds": 60},
    "RatedRange": {"interval_seconds": 60},
    "EstBatteryRange": {"interval_seconds": 60},
    "IdealBatteryRange": {"interval_seconds": 60},
    "DetailedChargeState": {"interval_seconds": 60},
    "ChargeLimitSoc": {"interval_seconds": 300},
    "TimeToFullCharge": {"interval_seconds": 60},
    "ChargerVoltage": {"interval_seconds": 60, "minimum_delta": 1},
    "ChargeAmps": {"interval_seconds": 60},
    "ACChargingPower": {"interval_seconds": 60},
    "DCChargingPower": {"interval_seconds": 60},
    "ACChargingEnergyIn": {"interval_seconds": 60},
    "DCChargingEnergyIn": {"interval_seconds": 60},
    "ChargePortDoorOpen": {"interval_seconds": 60},
    "ChargeCurrentRequest": {"interval_seconds": 60},
    "ChargeCurrentRequestMax": {"interval_seconds": 60},
    "ChargeEnableRequest": {"interval_seconds": 60},
    "ChargePortLatch": {"interval_seconds": 60},
    "ChargeRateMilePerHour": {"interval_seconds": 60},
    "ChargerPhases": {"interval_seconds": 60},
    "ChargingCableType": {"interval_seconds": 60},
    "FastChargerPresent": {"interval_seconds": 60},
    "FastChargerType": {"interval_seconds": 60},
    "ScheduledChargingMode": {"interval_seconds": 300},
    "ScheduledChargingPending": {"interval_seconds": 300},
    "ScheduledChargingStartTime": {"interval_seconds": 300},
    "SuperchargerSessionTripPlanner": {"interval_seconds": 60},
    "NotEnoughPowerToHeat": {"interval_seconds": 60},
    "BatteryHeaterOn": {"interval_seconds": 60},
    "BMSState": {"interval_seconds": 60},
    "BmsFullchargecomplete": {"interval_seconds": 60},
    "DCDCEnable": {"interval_seconds": 60},
    "PackCurrent": {"interval_seconds": 60},
    "PackVoltage": {"interval_seconds": 60},
    "EnergyRemaining": {"interval_seconds": 60},
    "EstimatedHoursToChargeTermination": {"interval_seconds": 60},
    "LifetimeEnergyUsed": {"interval_seconds": 3600},
    "ModuleTempMax": {"interval_seconds": 60},
    "ModuleTempMin": {"interval_seconds": 60},
    "NumModuleTempMax": {"interval_seconds": 60},
    "NumModuleTempMin": {"interval_seconds": 60},
    "BrickVoltageMax": {"interval_seconds": 60},
    "BrickVoltageMin": {"interval_seconds": 60},
    "NumBrickVoltageMax": {"interval_seconds": 60},
    "NumBrickVoltageMin": {"interval_seconds": 60},
    "IsolationResistance": {"interval_seconds": 3600},
    "PowershareHoursLeft": {"interval_seconds": 60},
    "PowershareInstantaneousPowerKW": {"interval_seconds": 60},
    "PowershareStatus": {"interval_seconds": 60},
    "PowershareStopReason": {"interval_seconds": 60},
    "PowershareType": {"interval_seconds": 60},
    
    # Climate
    "InsideTemp": {"interval_seconds": 300},
    "OutsideTemp": {"interval_seconds": 300},
    "HvacLeftTemperatureRequest": {"interval_seconds": 300},
    "HvacRightTemperatureRequest": {"interval_seconds": 300},
    "HvacFanStatus": {"interval_seconds": 60},
    "HvacPower": {"interval_seconds": 60},
    "PreconditioningEnabled": {"interval_seconds": 60},
    "ClimateKeeperMode": {"interval_seconds": 300},
    "DefrostMode": {"interval_seconds": 60},
    "SeatHeaterLeft": {"interval_seconds": 60},
    "SeatHeaterRight": {"interval_seconds": 60},
    "SeatHeaterRearLeft": {"interval_seconds": 60},
    "SeatHeaterRearRight": {"interval_seconds": 60},
    "SeatHeaterRearCenter": {"interval_seconds": 60},
    "HvacSteeringWheelHeatLevel": {"interval_seconds": 60},
    "HvacACEnabled": {"interval_seconds": 60},
    "HvacAutoMode": {"interval_seconds": 60},
    "HvacFanSpeed": {"interval_seconds": 60},
    "HvacSteeringWheelHeatAuto": {"interval_seconds": 60},
    "RearDefrostEnabled": {"interval_seconds": 60},
    "RearDisplayHvacEnabled": {"interval_seconds": 60},
    "DefrostForPreconditioning": {"interval_seconds": 60},
    "CabinOverheatProtectionMode": {"interval_seconds": 300},
    "CabinOverheatProtectionTemperatureLimit": {"interval_seconds": 300},
    "WiperHeatEnabled": {"interval_seconds": 60},
    "ClimateSeatCoolingFrontLeft": {"interval_seconds": 60},
    "ClimateSeatCoolingFrontRight": {"interval_seconds": 60},
    "AutoSeatClimateLeft": {"interval_seconds": 60},
    "AutoSeatClimateRight": {"interval_seconds": 60},
    
    # Vehicle State
    "Locked": {"interval_seconds": 60},
    "SentryMode": {"interval_seconds": 300},
    "DoorState": {"interval_seconds": 60},
    "DriverSeatOccupied": {"interval_seconds": 60},
    "FdWindow": {"interval_seconds": 60},
    "FpWindow": {"interval_seconds": 60},
    "RdWindow": {"interval_seconds": 60},
    "RpWindow": {"interval_seconds": 60},
    "TpmsPressureFl": {"interval_seconds": 300},
    "TpmsPressureFr": {"interval_seconds": 300},
    "TpmsPressureRl": {"interval_seconds": 300},
    "TpmsPressureRr": {"interval_seconds": 300},
    "TpmsHardWarnings": {"interval_seconds": 300},
    "TpmsSoftWarnings": {"interval_seconds": 300},
    "TpmsLastSeenPressureTimeFl": {"interval_seconds": 300},
    "TpmsLastSeenPressureTimeFr": {"interval_seconds": 300},
    "TpmsLastSeenPressureTimeRl": {"interval_seconds": 300},
    "TpmsLastSeenPressureTimeRr": {"interval_seconds": 300},
    "ValetModeEnabled": {"interval_seconds": 60},
    "SpeedLimitMode": {"interval_seconds": 60},
    "CurrentLimitMph": {"interval_seconds": 60},
    "PinToDriveEnabled": {"interval_seconds": 60},
    "GuestModeEnabled": {"interval_seconds": 60},
    "GuestModeMobileAccessState": {"interval_seconds": 60},
    "ServiceMode": {"interval_seconds": 3600},
    "RemoteStartEnabled": {"interval_seconds": 60},
    "HomelinkDeviceCount": {"interval_seconds": 60},
    "HomelinkNearby": {"interval_seconds": 60},
    "PairedPhoneKeyAndKeyFobQty": {"interval_seconds": 3600},
    "DriverSeatBelt": {"interval_seconds": 60},
    "PassengerSeatBelt": {"interval_seconds": 60},
    "LightsHazardsActive": {"interval_seconds": 60},
    "LightsHighBeams": {"interval_seconds": 60},
    "LightsTurnSignal": {"interval_seconds": 60},
    "CenterDisplay": {"interval_seconds": 60},
    "TonneauOpenPercent": {"interval_seconds": 60},
    "TonneauPosition": {"interval_seconds": 60},
    "TonneauTentMode": {"interval_seconds": 60},
    "OffroadLightbarPresent": {"interval_seconds": 3600},
    "SunroofInstalled": {"interval_seconds": 3600},
    "RightHandDrive": {"interval_seconds": 3600},
    "EuropeVehicle": {"interval_seconds": 3600},
    "CarType": {"interval_seconds": 3600},
    "Trim": {"interval_seconds": 3600},
    "ExteriorColor": {"interval_seconds": 3600},
    "RoofColor": {"interval_seconds": 3600},
    "WheelType": {"interval_seconds": 3600},
    "EfficiencyPackage": {"interval_seconds": 3600},
    "RearSeatHeaters": {"interval_seconds": 3600},
    "ChargePort": {"interval_seconds": 3600},
    "SoftwareUpdateDownloadPercentComplete": {"interval_seconds": 60},
    "SoftwareUpdateExpectedDurationMinutes": {"interval_seconds": 60},
    "SoftwareUpdateInstallationPercentComplete": {"interval_seconds": 60},
    "SoftwareUpdateScheduledStartTime": {"interval_seconds": 60},
    "SoftwareUpdateVersion": {"interval_seconds": 60},
    "Version": {"interval_seconds": 3600},
    "VehicleName": {"interval_seconds": 3600},
    
    # Driving
    "Gear": {"interval_seconds": 2},
    "VehicleSpeed": {"interval_seconds": 2},
    "GpsHeading": {"interval_seconds": 2},
    "GpsState": {"interval_seconds": 2},
    "Location": {"interval_seconds": 2},
    "Odometer": {"interval_seconds": 300},
    "BrakePedal": {"interval_seconds": 2},
    "BrakePedalPos": {"interval_seconds": 2},
    "PedalPosition": {"interval_seconds": 2},
    "CruiseSetSpeed": {"interval_seconds": 2},
    "CruiseFollowDistance": {"interval_seconds": 2},
    "DriveRail": {"interval_seconds": 2},
    "LongitudinalAcceleration": {"interval_seconds": 2},
    "LateralAcceleration": {"interval_seconds": 2},
    "MilesSinceReset": {"interval_seconds": 3600},
    "SelfDrivingMilesSinceReset": {"interval_seconds": 3600, "minimum_delta": 1},
    "SpeedLimitWarning": {"interval_seconds": 60},
    "ForwardCollisionWarning": {"interval_seconds": 60},
    "LaneDepartureAvoidance": {"interval_seconds": 60},
    "EmergencyLaneDepartureAvoidance": {"interval_seconds": 60},
    "AutomaticBlindSpotCamera": {"interval_seconds": 60},
    "BlindSpotCollisionWarningChime": {"interval_seconds": 60},
    "AutomaticEmergencyBrakingOff": {"interval_seconds": 60},
    
    # Location/Navigation
    "DestinationLocation": {"interval_seconds": 60},
    "DestinationName": {"interval_seconds": 60},
    "OriginLocation": {"interval_seconds": 60},
    "RouteLine": {"interval_seconds": 60},
    "RouteTrafficMinutesDelay": {"interval_seconds": 60},
    "RouteLastUpdated": {"interval_seconds": 60},
    "MilesToArrival": {"interval_seconds": 60},
    "MinutesToArrival": {"interval_seconds": 60},
    "ExpectedEnergyPercentAtTripArrival": {"interval_seconds": 60},
    "LocatedAtFavorite": {"interval_seconds": 60},
    "LocatedAtHome": {"interval_seconds": 60},
    "LocatedAtWork": {"interval_seconds": 60},
    
    # Powertrain
    "DiStateF": {"interval_seconds": 60},
    "DiStateR": {"interval_seconds": 60},
    "DiStateREL": {"interval_seconds": 60},
    "DiStateRER": {"interval_seconds": 60},
    "DiAxleSpeedF": {"interval_seconds": 60},
    "DiAxleSpeedR": {"interval_seconds": 60},
    "DiAxleSpeedREL": {"interval_seconds": 60},
    "DiAxleSpeedRER": {"interval_seconds": 60},
    "DiMotorCurrentF": {"interval_seconds": 60},
    "DiMotorCurrentR": {"interval_seconds": 60},
    "DiMotorCurrentREL": {"interval_seconds": 60},
    "DiMotorCurrentRER": {"interval_seconds": 60},
    "DiTorqueActualF": {"interval_seconds": 60},
    "DiTorqueActualR": {"interval_seconds": 60},
    "DiTorqueActualREL": {"interval_seconds": 60},
    "DiTorqueActualRER": {"interval_seconds": 60},
    "DiTorquemotor": {"interval_seconds": 60},
    "DiSlaveTorqueCmd": {"interval_seconds": 60},
    "DiInverterTF": {"interval_seconds": 60},
    "DiInverterTR": {"interval_seconds": 60},
    "DiInverterTREL": {"interval_seconds": 60},
    "DiInverterTRER": {"interval_seconds": 60},
    "DiHeatsinkTF": {"interval_seconds": 60},
    "DiHeatsinkTR": {"interval_seconds": 60},
    "DiHeatsinkTREL": {"interval_seconds": 60},
    "DiHeatsinkTRER": {"interval_seconds": 60},
    "DiStatorTempF": {"interval_seconds": 60},
    "DiStatorTempR": {"interval_seconds": 60},
    "DiStatorTempREL": {"interval_seconds": 60},
    "DiStatorTempRER": {"interval_seconds": 60},
    "DiVBatF": {"interval_seconds": 60},
    "DiVBatR": {"interval_seconds": 60},
    "DiVBatREL": {"interval_seconds": 60},
    "DiVBatRER": {"interval_seconds": 60},
    "Hvil": {"interval_seconds": 60},
    
    # Safety/Service
    "TpmsPressureFl": {"interval_seconds": 300},
    "TpmsPressureFr": {"interval_seconds": 300},
    "TpmsPressureRl": {"interval_seconds": 300},
    "TpmsPressureRr": {"interval_seconds": 300},

    
    # User Preferences
    "Setting24HourTime": {"interval_seconds": 3600},
    "SettingChargeUnit": {"interval_seconds": 3600},
    "SettingDistanceUnit": {"interval_seconds": 3600},
    "SettingTemperatureUnit": {"interval_seconds": 3600},
    "SettingTirePressureUnit": {"interval_seconds": 3600},
}

_TELEMETRY_STORE_VERSION = 2
_COMMAND_STATE_CONFIRMATION_TIMEOUT = timedelta(seconds=30)
_BATTERY_CAPACITY_MIN_SOC_SPAN = 20.0
_BATTERY_CAPACITY_MAX_SESSION_GAP = timedelta(hours=6)
_BATTERY_CAPACITY_MIN_KWH = 10.0
_BATTERY_CAPACITY_MAX_KWH = 200.0
_BATTERY_CAPACITY_MAX_ESTIMATES = 12
_BATTERY_CAPACITY_SOC_EPSILON = 0.05
_BATTERY_CAPACITY_REVERSAL_TOLERANCE_PCT = 1.0
_BATTERY_CAPACITY_REVERSAL_STREAK_LIMIT = 2
_BATTERY_CAPACITY_OUTLIER_DEVIATION_PCT = 35.0
_BATTERY_CAPACITY_MAX_REJECTED = 12
_BATTERY_CAPACITY_MODEL_VERSION = 3
_BATTERY_HIGH_SOC_MIN_PERCENT = 95.0
_BATTERY_HIGH_SOC_MAX_OBSERVATIONS = 12
_BATTERY_HIGH_SOC_WARNING_DEVIATION_PCT = 10.0
_BATTERY_SNAPSHOT_MAX_DAYS = 400
_TELEMETRY_DIAGNOSTIC_COUNTER_MAX = 10_000
_BATTERY_HISTORY_LOOKBACK = timedelta(days=30)
_BATTERY_HISTORY_PAIR_TOLERANCE = timedelta(seconds=2)
_BATTERY_HISTORY_RETRY_INTERVAL = timedelta(days=1)
_COMMAND_STATE_SIGNALS = {
    ("charge_state", "charge_limit_soc"): "ChargeLimitSoc",
    ("charge_state", "charge_port_door_open"): "ChargePortDoorOpen",
    ("climate_state", "defrost_mode"): "DefrostMode",
    ("climate_state", "driver_temp_setting"): "HvacLeftTemperatureRequest",
    ("climate_state", "is_climate_on"): "HvacPower",
    ("climate_state", "passenger_temp_setting"): "HvacRightTemperatureRequest",
    ("climate_state", "seat_heater_left"): "SeatHeaterLeft",
    ("climate_state", "seat_heater_rear_left"): "SeatHeaterRearLeft",
    ("climate_state", "seat_heater_rear_right"): "SeatHeaterRearRight",
    ("climate_state", "seat_heater_right"): "SeatHeaterRight",
    ("climate_state", "steering_wheel_heater"): "HvacSteeringWheelHeatLevel",
    ("vehicle_state", "fd_window"): "FdWindow",
    ("vehicle_state", "fp_window"): "FpWindow",
    ("vehicle_state", "ft"): "DoorState.TrunkFront",
    ("vehicle_state", "locked"): "Locked",
    ("vehicle_state", "rd_window"): "RdWindow",
    ("vehicle_state", "rp_window"): "RpWindow",
    ("vehicle_state", "sentry_mode"): "SentryMode",
    ("vehicle_state", "valet_mode"): "ValetModeEnabled",
}

_OPTIONAL_CAPABILITY_GROUPS = {
    "powertrain_rel_rer": {
        "DiAxleSpeedREL", "DiAxleSpeedRER", "DiHeatsinkTREL", "DiHeatsinkTRER",
        "DiInverterTREL", "DiInverterTRER", "DiMotorCurrentREL", "DiMotorCurrentRER",
        "DiStateREL", "DiStateRER", "DiStatorTempREL", "DiStatorTempRER",
        "DiTorqueActualREL", "DiTorqueActualRER", "DiVBatREL", "DiVBatRER",
    },
    "powershare": {
        "PowershareHoursLeft", "PowershareInstantaneousPowerKW", "PowershareStatus",
        "PowershareStopReason", "PowershareType",
    },
    "rear_display_hvac": {"RearDisplayHvacEnabled"},
    "sunroof": {"SunroofInstalled"},
    "tonneau": {"TonneauOpenPercent", "TonneauPosition", "TonneauTentMode"},
}
_OPTIONAL_CAPABILITY_ABSENCE_SESSIONS = 2
_OPTIONAL_CAPABILITY_MINIMUM_FRAMES = 3


class TeslaVehicleCommandCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Tesla vehicle data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        proxy_manager: ProxyManager,
    ) -> None:
        """Initialize."""
        self.entry = entry
        self.proxy_manager = proxy_manager
        self._vehicles = entry.data.get("vehicles", [])
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._telemetry_store = Store[dict[str, Any]](
            hass,
            _TELEMETRY_STORE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.telemetry",
        )
        self._telemetry_receiver_available = False
        self._telemetry_receiver_diagnostics: dict[str, int] = {}
        self._telemetry_metadata: dict[str, dict[str, Any]] = {}
        self._telemetry_raw_signals: dict[str, dict[str, Any]] = {}
        self._capability_valid_signals: dict[str, set[str]] = {}
        self._capability_frame_counts: dict[str, int] = {}
        self._signal_capabilities: dict[str, dict[str, dict[str, Any]]] = {}
        self._capability_listener: (
            Callable[[str, set[str], set[str]], None] | None
        ) = None
        self._telemetry_events = {
            vehicle["vin"]: asyncio.Event() for vehicle in self._vehicles
        }
        self._telemetry_generations = {
            vehicle["vin"]: 0 for vehicle in self._vehicles
        }
        self._command_locks = {
            vehicle["vin"]: asyncio.Lock() for vehicle in self._vehicles
        }
        self._commands_in_progress: set[str] = set()
        self._vehicles_waking: set[str] = set()
        self._pending_command_states: dict[
            tuple[str, str, str], tuple[Any, datetime]
        ] = {}
        self._battery_capacity_models: dict[str, dict[str, Any]] = {}
        self._sleep_timers: dict[str, asyncio.TimerHandle] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )

    @property
    def vehicles(self) -> list[dict[str, Any]]:
        """Return configured vehicles."""
        return self._vehicles

    @property
    def access_token(self) -> str | None:
        """Return current access token."""
        return self._access_token

    def set_telemetry_receiver_available(self, available: bool) -> None:
        """Set whether the local Fleet Telemetry receiver is reachable."""
        self._telemetry_receiver_available = available

    def record_telemetry_receiver_error(self, category: str) -> None:
        """Count a bounded receiver error without storing message contents."""
        if category not in {
            "malformed_json",
            "missing_vin",
            "unmanaged_vin",
            "processing_error",
        }:
            raise ValueError("Unknown telemetry receiver error category")
        self._telemetry_receiver_diagnostics[category] = min(
            self._telemetry_receiver_diagnostics.get(category, 0) + 1,
            _TELEMETRY_DIAGNOSTIC_COUNTER_MAX,
        )
        self._telemetry_store.async_delay_save(self._telemetry_store_payload, 30)
        self.async_update_listeners()

    def get_telemetry_receiver_diagnostics(self) -> dict[str, int]:
        """Return receiver-wide bounded error counters."""
        return {
            category: self._telemetry_receiver_diagnostics.get(category, 0)
            for category in (
                "malformed_json",
                "missing_vin",
                "unmanaged_vin",
                "processing_error",
            )
        }

    def set_capability_listener(
        self, listener: Callable[[str, set[str], set[str]], None]
    ) -> None:
        """Set the callback used to apply optional entity capability decisions."""
        self._capability_listener = listener

    def get_capability_diagnostics(self, vin: str) -> dict[str, list[str]]:
        """Return explainable optional-feature capability states for one vehicle."""
        capabilities = self._signal_capabilities.get(vin, {})
        observed = self._capability_valid_signals.get(vin, set())
        supported: list[str] = []
        unsupported: list[str] = []
        not_yet_observed: list[str] = []
        for group_name, signals in _OPTIONAL_CAPABILITY_GROUPS.items():
            if capabilities.get(group_name, {}).get("auto_disabled"):
                unsupported.append(group_name)
            elif observed.intersection(signals):
                supported.append(group_name)
            else:
                not_yet_observed.append(group_name)
        return {
            "supported": sorted(supported),
            "unsupported": sorted(unsupported),
            "not_yet_observed": sorted(not_yet_observed),
        }

    def rescan_capabilities(self, vin: str) -> None:
        """Clear all persisted capability state and begin a new observation run."""
        capabilities = self._signal_capabilities.setdefault(vin, {})
        reenabled = {
            group_name
            for group_name, capability in capabilities.items()
            if capability.get("auto_disabled")
        }
        self._signal_capabilities[vin] = {}
        self._capability_valid_signals.pop(vin, None)
        self._capability_frame_counts.pop(vin, None)
        if reenabled and self._capability_listener:
            self._capability_listener(vin, set(), reenabled)
        self._telemetry_store.async_delay_save(self._telemetry_store_payload, 30)
        self.async_update_listeners()

    def record_telemetry_signal_values(
        self, vin: str, signals: dict[str, Any]
    ) -> None:
        """Track optional raw signals received during the current awake session."""
        valid = self._capability_valid_signals.setdefault(vin, set())
        self._capability_frame_counts[vin] = self._capability_frame_counts.get(vin, 0) + 1
        optional_signals = set().union(*_OPTIONAL_CAPABILITY_GROUPS.values())
        for signal_name, value in signals.items():
            if signal_name not in optional_signals:
                continue
            if value is not None:
                valid.add(signal_name)

        self._apply_reappeared_capabilities(vin, valid)

    def _apply_reappeared_capabilities(
        self, vin: str, valid_signals: set[str]
    ) -> None:
        """Re-enable entities whose previously absent signals become valid."""
        capabilities = self._signal_capabilities.setdefault(vin, {})
        reenabled: set[str] = set()
        for group_name, signals in _OPTIONAL_CAPABILITY_GROUPS.items():
            if not valid_signals.intersection(signals):
                continue
            capability = capabilities.setdefault(group_name, {})
            if capability.get("auto_disabled"):
                capability["auto_disabled"] = False
                reenabled.add(group_name)
            capability["absent_sessions"] = 0

        if reenabled and self._capability_listener:
            self._capability_listener(vin, set(), reenabled)

    def record_telemetry_update(
        self,
        vin: str,
        received_fields: set[str],
        processed_fields: set[str],
        vehicle_capture_at: datetime | None = None,
    ) -> None:
        """Record a successfully processed telemetry message for a vehicle."""
        received_at = datetime.now(timezone.utc)
        metadata = self._telemetry_metadata.get(vin, {})
        updated_metadata = {
            **metadata,
            "last_received": received_at,
            "connectivity_status": "ONLINE",
            "received_fields": sorted(received_fields),
            "processed_fields": sorted(processed_fields),
        }
        if received_fields - processed_fields:
            updated_metadata["partial_frame_count"] = min(
                int(metadata.get("partial_frame_count", 0)) + 1,
                _TELEMETRY_DIAGNOSTIC_COUNTER_MAX,
            )
        updated_metadata["frame_count"] = min(
            int(metadata.get("frame_count", 0)) + 1,
            _TELEMETRY_DIAGNOSTIC_COUNTER_MAX,
        )
        if vehicle_capture_at is not None and vehicle_capture_at.tzinfo is not None:
            capture_at = vehicle_capture_at.astimezone(timezone.utc)
            updated_metadata["last_vehicle_capture"] = capture_at
            updated_metadata["transport_delay_seconds"] = round(
                max(0.0, (received_at - capture_at).total_seconds()), 3
            )
        self._telemetry_metadata[vin] = updated_metadata
        self._telemetry_generations[vin] = (
            self._telemetry_generations.get(vin, 0) + 1
        )
        self._telemetry_events.setdefault(vin, asyncio.Event()).set()
        self._schedule_sleep_transition(vin)

    def set_connectivity_status(self, vin: str, status: str) -> None:
        """Record an explicit vehicle connectivity state from Fleet Telemetry."""
        normalized_status = status.strip().upper()
        metadata = self._telemetry_metadata.setdefault(vin, {})
        metadata["connectivity_status"] = normalized_status

        if normalized_status == "DISCONNECTED":
            self._schedule_sleep_transition(vin)
        elif normalized_status in {"CONNECTED", "ONLINE"}:
            metadata["last_received"] = datetime.now().astimezone()
            self._telemetry_generations[vin] = (
                self._telemetry_generations.get(vin, 0) + 1
            )
            self._telemetry_events.setdefault(vin, asyncio.Event()).set()
            self._schedule_sleep_transition(vin)

        self.async_update_listeners()

    @property
    def telemetry_inactivity_timeout(self) -> timedelta:
        """Return the configured interval after which a vehicle is asleep."""
        minutes = self.entry.options.get(
            CONF_TELEMETRY_INACTIVITY_MINUTES,
            DEFAULT_TELEMETRY_INACTIVITY_MINUTES,
        )
        return timedelta(minutes=float(minutes))

    def is_vehicle_awake(self, vin: str) -> bool:
        """Return whether the vehicle has sent telemetry recently."""
        metadata = self._telemetry_metadata.get(vin, {})
        connectivity_status = str(metadata.get("connectivity_status", "")).strip().upper()
        if connectivity_status == "DISCONNECTED":
            return False

        last_received = metadata.get("last_received")
        return bool(
            isinstance(last_received, datetime)
            and datetime.now().astimezone() - last_received
            <= self.telemetry_inactivity_timeout
        )

    def is_command_in_progress(self, vin: str) -> bool:
        """Return whether a vehicle is waking or executing a command."""
        return vin in self._commands_in_progress

    def is_vehicle_waking(self, vin: str) -> bool:
        """Return whether a vehicle is being woken for a command."""
        return vin in self._vehicles_waking

    def _set_vehicle_waking(self, vin: str, waking: bool) -> None:
        """Publish a vehicle's command-readiness phase."""
        if waking:
            self._vehicles_waking.add(vin)
        else:
            self._vehicles_waking.discard(vin)
        self.async_update_listeners()

    def _require_telemetry_receiver(self) -> None:
        """Require telemetry before using it as command readiness evidence."""
        if not self._telemetry_receiver_available:
            raise RuntimeError(
                "Fleet Telemetry must be running before commands can verify "
                "that the vehicle is awake"
            )

    def _acquire_command_slot(self, vin: str) -> None:
        """Reserve a vehicle for one wake-and-command operation."""
        if vin in self._commands_in_progress:
            raise RuntimeError(f"A command is already in progress for vehicle {vin}")
        self._commands_in_progress.add(vin)
        self.async_update_listeners()

    def _release_command_slot(self, vin: str) -> None:
        """Release a vehicle after its command operation completes."""
        self._commands_in_progress.discard(vin)
        self.async_update_listeners()

    def _schedule_sleep_transition(self, vin: str) -> None:
        """Notify entities when the telemetry inactivity window expires."""
        if timer := self._sleep_timers.pop(vin, None):
            timer.cancel()
        last_received = self._telemetry_metadata.get(vin, {}).get("last_received")
        if not isinstance(last_received, datetime):
            return
        elapsed = datetime.now().astimezone() - last_received
        delay = (self.telemetry_inactivity_timeout - elapsed).total_seconds()
        if delay <= 0:
            self._handle_sleep_timeout(vin)
            return
        self._sleep_timers[vin] = self.hass.loop.call_later(
            delay,
            self._handle_sleep_timeout,
            vin,
        )

    def _handle_sleep_timeout(self, vin: str) -> None:
        """Publish the transition to asleep after telemetry inactivity."""
        self._sleep_timers.pop(vin, None)
        self._complete_capability_session(vin)
        self.async_update_listeners()

    def _complete_capability_session(self, vin: str) -> None:
        """Evaluate optional signals after one complete awake telemetry session."""
        valid = self._capability_valid_signals.pop(vin, set())
        frame_count = self._capability_frame_counts.pop(vin, 0)
        if frame_count < _OPTIONAL_CAPABILITY_MINIMUM_FRAMES:
            return

        capabilities = self._signal_capabilities.setdefault(vin, {})
        disabled: set[str] = set()
        for group_name, signals in _OPTIONAL_CAPABILITY_GROUPS.items():
            capability = capabilities.setdefault(group_name, {})
            if valid.intersection(signals):
                capability["absent_sessions"] = 0
                continue

            capability["absent_sessions"] = (
                int(capability.get("absent_sessions", 0)) + 1
            )
            if (
                capability["absent_sessions"]
                >= _OPTIONAL_CAPABILITY_ABSENCE_SESSIONS
                and not capability.get("auto_disabled")
            ):
                capability["auto_disabled"] = True
                disabled.add(group_name)

        if disabled and self._capability_listener:
            self._capability_listener(vin, disabled, set())
        self._telemetry_store.async_delay_save(self._telemetry_store_payload, 30)

    def _prepare_telemetry_wait(self, vin: str) -> tuple[int, asyncio.Event]:
        """Capture telemetry progress and await the next vehicle frame."""
        generation = self._telemetry_generations.get(vin, 0)
        event = asyncio.Event()
        self._telemetry_events[vin] = event
        return generation, event

    async def _async_wait_for_telemetry_after(
        self,
        vin: str,
        generation: int,
        event: asyncio.Event,
    ) -> None:
        """Wait until a vehicle frame arrives after a captured generation."""
        if self._telemetry_generations.get(vin, 0) > generation:
            return
        await asyncio.wait_for(event.wait(), timeout=WAKE_TELEMETRY_TIMEOUT_SECONDS)

    @staticmethod
    def _validate_command_response(response: Any) -> dict[str, Any]:
        """Reject command responses that Tesla reports as unsuccessful."""
        if not isinstance(response, dict):
            raise RuntimeError("Command returned an invalid response")
        payload = response.get("response", response)
        if not isinstance(payload, dict) or payload.get("result") is not True:
            if isinstance(payload, dict) and payload.get("result") is False:
                reason = payload.get("reason") or "Tesla rejected the command"
                raise RuntimeError(f"Command failed: {reason}")
            raise RuntimeError("Command returned an invalid success response")
        return response

    @staticmethod
    def _wake_response_is_online(response: Any) -> bool:
        """Return whether a wake response explicitly reports an online vehicle."""
        if not isinstance(response, dict):
            return False
        payload = response.get("response", response)
        return (
            isinstance(payload, dict)
            and str(payload.get("state", "")).strip().lower() == "online"
        )

    def shutdown(self) -> None:
        """Cancel coordinator timers."""
        for timer in self._sleep_timers.values():
            timer.cancel()
        self._sleep_timers.clear()

    def get_telemetry_status(self, vin: str) -> dict[str, Any]:
        """Return telemetry health and diagnostic metadata for a vehicle."""
        metadata = self._telemetry_metadata.get(vin, {})
        last_received = metadata.get("last_received")
        processed_fields = set(metadata.get("processed_fields", []))
        has_partial_battery_data = bool(
            {"Soc", "EnergyRemaining"}.intersection(processed_fields)
            and not {"Soc", "EnergyRemaining"}.issubset(processed_fields)
        )
        if not self._telemetry_receiver_available:
            state = "receiver_unavailable"
        elif metadata.get("registration_status") == "failed":
            state = "registration_failed"
        elif last_received and datetime.now(timezone.utc) - last_received <= timedelta(
            minutes=15
        ) and has_partial_battery_data:
            state = "partial_battery_data"
        elif last_received and datetime.now(timezone.utc) - last_received <= timedelta(
            minutes=15
        ):
            state = "receiving"
        elif last_received:
            state = "stale"
        elif self._telemetry_receiver_available:
            state = "waiting"

        return {"state": state, **metadata}

    def get_telemetry_setup_validation(self, vin: str) -> dict[str, Any]:
        """Return sanitized Fleet Telemetry setup checks for one vehicle."""
        if self.get_vehicle_config(vin) is None:
            raise ValueError("Vehicle is not configured")

        hostname = self.entry.options.get(CONF_TELEMETRY_HOSTNAME, "").strip()
        port = self.entry.options.get(CONF_TELEMETRY_PORT)
        try:
            port_is_valid = 1 <= int(port) <= 65535
        except (TypeError, ValueError):
            port_is_valid = False

        certificate_path = self.proxy_manager.telemetry_ca_path
        metadata = self._telemetry_metadata.get(vin, {})
        last_received = metadata.get("last_received")
        registration_status = metadata.get("registration_status", "not_attempted")
        pending_vins = self.entry.options.get(
            CONF_TELEMETRY_REGISTRATION_REQUIRED_VINS, []
        )
        if isinstance(pending_vins, list) and vin in pending_vins:
            registration_status = "registration_required"

        return {
            "receiver": (
                "available" if self._telemetry_receiver_available else "unavailable"
            ),
            "hostname": "configured" if hostname else "missing",
            "port": "configured" if port_is_valid else "missing_or_invalid",
            "certificate": (
                "available"
                if certificate_path is not None and certificate_path.is_file()
                else "missing"
            ),
            "registration": registration_status,
            "vehicle_frame": (
                "received" if isinstance(last_received, datetime) else "not_received"
            ),
        }

    def validate_telemetry_setup(self, vin: str) -> dict[str, Any]:
        """Publish a timestamped result for an explicit validation action."""
        result = self.get_telemetry_setup_validation(vin)
        self._telemetry_metadata.setdefault(vin, {})["last_validation"] = (
            datetime.now(timezone.utc)
        )
        self._telemetry_store.async_delay_save(self._telemetry_store_payload, 30)
        self.async_update_listeners()
        return result

    @staticmethod
    def _empty_response() -> dict[str, dict[str, Any]]:
        """Return the Fleet API-shaped state used before telemetry arrives."""
        return {
            "charge_state": {},
            "climate_state": {},
            "drive_state": {},
            "vehicle_state": {},
        }

    def _empty_telemetry_data(self) -> dict[str, dict[str, Any]]:
        """Return empty telemetry-backed data for configured vehicles."""
        return {
            vehicle["vin"]: {"response": self._empty_response()}
            for vehicle in self._vehicles
        }

    async def async_load_telemetry_cache(self) -> None:
        """Restore the last telemetry state without reading vehicle data from Tesla."""
        stored_cache = await self._telemetry_store.async_load()
        data = self._empty_telemetry_data()
        stored_data = (
            stored_cache.get("vehicles", {})
            if isinstance(stored_cache, dict) and "vehicles" in stored_cache
            else stored_cache
        )
        if isinstance(stored_data, dict):
            for vin in data:
                cached_vehicle = stored_data.get(vin)
                cached_response = (
                    cached_vehicle.get("response")
                    if isinstance(cached_vehicle, dict)
                    else None
                )
                if isinstance(cached_response, dict):
                    data[vin] = {"response": cached_response}

        if not isinstance(stored_cache, dict):
            self.async_set_updated_data(data)
            return

        for vin, metadata in stored_cache.get("metadata", {}).items():
            if vin not in data or not isinstance(metadata, dict):
                continue
            restored_metadata = dict(metadata)
            last_received = metadata.get("last_received")
            if isinstance(last_received, str):
                try:
                    last_received = datetime.fromisoformat(last_received)
                except ValueError:
                    last_received = None
            if isinstance(last_received, datetime):
                restored_metadata["last_received"] = last_received
                self._telemetry_metadata[vin] = restored_metadata
                if self.is_vehicle_awake(vin):
                    self._schedule_sleep_transition(vin)
            else:
                restored_metadata.pop("last_received", None)
            for timestamp_key in (
                "last_registration",
                "last_validation",
                "last_vehicle_capture",
            ):
                timestamp = restored_metadata.get(timestamp_key)
                if isinstance(timestamp, str):
                    try:
                        restored_metadata[timestamp_key] = datetime.fromisoformat(
                            timestamp
                        )
                    except ValueError:
                        restored_metadata.pop(timestamp_key, None)
            self._telemetry_metadata[vin] = restored_metadata

        raw_signals = stored_cache.get("raw_signals", {})
        if isinstance(raw_signals, dict):
            self._telemetry_raw_signals = {
                vin: signals
                for vin, signals in raw_signals.items()
                if vin in data and isinstance(signals, dict)
            }
        receiver_diagnostics = stored_cache.get("receiver_diagnostics")
        if isinstance(receiver_diagnostics, dict):
            self._telemetry_receiver_diagnostics = {
                category: min(
                    int(receiver_diagnostics.get(category, 0)),
                    _TELEMETRY_DIAGNOSTIC_COUNTER_MAX,
                )
                for category in (
                    "malformed_json",
                    "missing_vin",
                    "unmanaged_vin",
                    "processing_error",
                )
                if isinstance(receiver_diagnostics.get(category, 0), int)
            }
        capabilities = stored_cache.get("signal_capabilities", {})
        if isinstance(capabilities, dict):
            self._signal_capabilities = {
                vin: groups
                for vin, groups in capabilities.items()
                if vin in data and isinstance(groups, dict)
            }
        battery_capacity_models = stored_cache.get("battery_capacity_models", {})
        if isinstance(battery_capacity_models, dict):
            self._battery_capacity_models = {
                vin: self._migrate_battery_capacity_model(model)
                for vin, model in battery_capacity_models.items()
                if vin in data and isinstance(model, dict)
            }
        for vin, vehicle_data in data.items():
            response = vehicle_data.get("response", {})
            charge_state = response.get("charge_state")
            if not isinstance(charge_state, dict):
                charge_state = {}
                response["charge_state"] = charge_state
            for key in (
                "estimated_usable_capacity",
                "estimated_battery_soh",
                "battery_soh_confidence",
            ):
                charge_state.pop(key, None)
            charge_state.update(self._battery_capacity_metrics(vin))
        self.async_set_updated_data(data)

    def get_telemetry_raw_signals(self, vin: str) -> dict[str, Any]:
        """Return persisted raw signals used to reassemble delta records."""
        return dict(self._telemetry_raw_signals.get(vin, {}))

    def _telemetry_store_payload(self) -> dict[str, Any]:
        """Serialize telemetry state and diagnostics for local storage."""
        metadata: dict[str, dict[str, Any]] = {}
        for vin, details in self._telemetry_metadata.items():
            serialized = dict(details)
            for timestamp_key in (
                "last_received",
                "last_registration",
                "last_validation",
                "last_vehicle_capture",
            ):
                timestamp = serialized.get(timestamp_key)
                if isinstance(timestamp, datetime):
                    serialized[timestamp_key] = timestamp.isoformat()
            audit = serialized.get("command_audit")
            if isinstance(audit, dict):
                serialized_audit = dict(audit)
                for timestamp_key in ("dispatch_time", "confirmation_time"):
                    timestamp = serialized_audit.get(timestamp_key)
                    if isinstance(timestamp, datetime):
                        serialized_audit[timestamp_key] = timestamp.isoformat()
                serialized["command_audit"] = serialized_audit
            metadata[vin] = serialized
        return {
            "vehicles": self.data if isinstance(self.data, dict) else {},
            "metadata": metadata,
            "raw_signals": self._telemetry_raw_signals,
            "receiver_diagnostics": self.get_telemetry_receiver_diagnostics(),
            "signal_capabilities": self._signal_capabilities,
            "battery_capacity_models": {
                vin: {
                    key: value
                    for key, value in model.items()
                    if key != "latest_live_capacity_observation"
                }
                for vin, model in self._battery_capacity_models.items()
                if isinstance(model, dict)
            },
        }

    def record_battery_capacity_sample(
        self,
        vin: str,
        soc_percent: Any,
        energy_remaining_kwh: Any,
        observed_at: datetime | None = None,
        source: str = "live",
        temperature_c: Any = None,
    ) -> dict[str, Any]:
        """Feed one same-record SOC/energy pair into the window state machine."""
        if (
            not self._is_finite_number(soc_percent)
            or not self._is_finite_number(energy_remaining_kwh)
            or not 0 <= soc_percent <= 100
            or energy_remaining_kwh < 0
        ):
            return self._battery_capacity_metrics(vin)

        now = observed_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            return self._battery_capacity_metrics(vin)
        now = now.astimezone(timezone.utc)

        temperature = (
            float(temperature_c) if self._is_finite_number(temperature_c) else None
        )
        sample = {
            "soc": float(soc_percent),
            "energy": float(energy_remaining_kwh),
            "timestamp": now.isoformat(),
            "temperature": temperature,
        }
        normalized_source = "recorder" if source == "recorder" else "live"

        model = self._battery_capacity_models.setdefault(vin, {})
        if normalized_source == "live" and sample["soc"] > 0:
            self._record_live_capacity_observation(model, sample)
        if sample["soc"] >= _BATTERY_HIGH_SOC_MIN_PERCENT:
            self._record_high_soc_observation(model, sample, normalized_source)
        active = model.get("active_window")
        if not isinstance(active, dict):
            model["active_window"] = self._new_active_window(sample, normalized_source)
            return self._battery_capacity_metrics(vin)

        try:
            last = active["last"]
            last_timestamp = datetime.fromisoformat(last["timestamp"])
            if last_timestamp.tzinfo is None:
                raise ValueError("naive battery timestamp")
            last_soc = float(last["soc"])
        except (KeyError, TypeError, ValueError):
            model["active_window"] = self._new_active_window(sample, normalized_source)
            return self._battery_capacity_metrics(vin)

        # A long silence ends the current same-direction run.
        if now - last_timestamp > _BATTERY_CAPACITY_MAX_SESSION_GAP:
            self._finalize_or_discard_window(vin, active)
            model["active_window"] = self._new_active_window(sample, normalized_source)
            return self._battery_capacity_metrics(vin)

        soc_step = sample["soc"] - last_soc
        if abs(soc_step) < _BATTERY_CAPACITY_SOC_EPSILON:
            # No meaningful SOC change; keep the window open and advance time.
            self._extend_active_window(active, sample)
            return self._battery_capacity_metrics(vin)

        step_direction = "charging" if soc_step > 0 else "discharging"
        direction = active.get("direction")
        if direction not in ("charging", "discharging"):
            active["direction"] = step_direction
            active["reversal_streak"] = 0
            self._extend_active_window(active, sample)
            return self._battery_capacity_metrics(vin)

        if step_direction != direction:
            if abs(soc_step) <= _BATTERY_CAPACITY_REVERSAL_TOLERANCE_PCT:
                streak = int(active.get("reversal_streak", 0)) + 1
                active["reversal_streak"] = streak
                if streak < _BATTERY_CAPACITY_REVERSAL_STREAK_LIMIT:
                    # Small opposite step (regen jitter); treat as noise, but advance time.
                    active["last"]["timestamp"] = sample["timestamp"]
                    temperature = sample.get("temperature")
                    if temperature is not None:
                        active["temp_sum"] = float(active.get("temp_sum", 0.0)) + float(temperature)
                        active["temp_count"] = int(active.get("temp_count", 0)) + 1
                    return self._battery_capacity_metrics(vin)
            self._finalize_or_discard_window(vin, active)
            model["active_window"] = self._new_active_window(sample, normalized_source)
            return self._battery_capacity_metrics(vin)

        active["reversal_streak"] = 0
        self._extend_active_window(active, sample)
        return self._battery_capacity_metrics(vin)

    @staticmethod
    def _new_active_window(sample: dict[str, Any], source: str) -> dict[str, Any]:
        """Return a fresh active window anchored on a sample."""
        temperature = sample.get("temperature")
        return {
            "start": dict(sample),
            "last": dict(sample),
            "direction": None,
            "reversal_streak": 0,
            "source": source,
            "temp_sum": float(temperature) if temperature is not None else 0.0,
            "temp_count": 1 if temperature is not None else 0,
        }

    @staticmethod
    def _extend_active_window(
        active: dict[str, Any], sample: dict[str, Any]
    ) -> None:
        """Advance the active window's end with a new sample."""
        active["last"] = dict(sample)
        temperature = sample.get("temperature")
        if temperature is not None:
            active["temp_sum"] = float(active.get("temp_sum", 0.0)) + float(temperature)
            active["temp_count"] = int(active.get("temp_count", 0)) + 1

    def record_battery_charge_state(
        self, vin: str, charging_state: Any
    ) -> dict[str, Any]:
        """Close a live charging run when Fleet Telemetry reports charging stopped."""
        if not isinstance(charging_state, str):
            return self._battery_capacity_metrics(vin)

        model = self._battery_capacity_models.get(vin)
        if not isinstance(model, dict):
            return self._battery_capacity_metrics(vin)

        is_charging = charging_state.strip().casefold() == "charging"
        model["is_charging"] = is_charging
        active = model.get("active_window")
        if (
            not is_charging
            and isinstance(active, dict)
            and active.get("source") == "live"
            and active.get("direction") == "charging"
        ):
            self._finalize_or_discard_window(vin, active)
            model["active_window"] = None
        return self._battery_capacity_metrics(vin)

    def flush_active_battery_window(self, vin: str) -> None:
        """Close any open window so a completed run can be scored."""
        model = self._battery_capacity_models.get(vin)
        if not isinstance(model, dict):
            return
        active = model.get("active_window")
        if isinstance(active, dict):
            self._finalize_or_discard_window(vin, active)
        model["active_window"] = None

    def reset_battery_capacity_history(self, vin: str, scope: str = "all") -> None:
        """Discard selected persisted estimator evidence for a vehicle."""
        if scope not in {"all", "live", "recorder"}:
            raise ValueError("Battery history reset scope must be all, live, or recorder")

        model = self._battery_capacity_models.setdefault(
            vin, {"model_version": _BATTERY_CAPACITY_MODEL_VERSION}
        )
        for key in ("accepted_windows", "rejected_windows"):
            windows = model.get(key)
            if not isinstance(windows, list):
                model[key] = []
                continue
            if scope == "all":
                model[key] = []
            else:
                model[key] = [
                    window
                    for window in windows
                    if isinstance(window, dict) and window.get("source") != scope
                ]

        active = model.get("active_window")
        if scope == "all" or (
            isinstance(active, dict) and active.get("source") == scope
        ):
            model["active_window"] = None
        if scope in {"all", "live"}:
            model.pop("latest_live_capacity_observation", None)
        model["last_reset_scope"] = scope
        model["last_reset"] = datetime.now(timezone.utc).isoformat()
        self._telemetry_store.async_delay_save(self._telemetry_store_payload, 30)
        self.async_update_listeners()

    def _finalize_or_discard_window(
        self, vin: str, active: dict[str, Any]
    ) -> None:
        """Close an active window, storing it as accepted or rejected."""
        try:
            start = active["start"]
            last = active["last"]
            start_soc = float(start["soc"])
            end_soc = float(last["soc"])
            start_energy = float(start["energy"])
            end_energy = float(last["energy"])
        except (KeyError, TypeError, ValueError):
            return

        delta_soc = abs(end_soc - start_soc)
        if delta_soc < _BATTERY_CAPACITY_MIN_SOC_SPAN:
            return  # Not enough span; drop silently.

        delta_energy = abs(end_energy - start_energy)
        capacity_kwh = delta_energy / delta_soc * 100
        direction = active.get("direction")
        if direction not in ("charging", "discharging"):
            direction = "charging" if end_soc >= start_soc else "discharging"
        source = active.get("source")
        source = source if source in ("live", "recorder") else "unknown"
        temp_count = int(active.get("temp_count", 0))
        temperature_c = (
            round(float(active.get("temp_sum", 0.0)) / temp_count, 2)
            if temp_count > 0
            else None
        )
        window = {
            "start_soc": round(start_soc, 3),
            "end_soc": round(end_soc, 3),
            "start_energy": round(start_energy, 3),
            "end_energy": round(end_energy, 3),
            "delta_soc": round(delta_soc, 3),
            "delta_energy": round(delta_energy, 3),
            "capacity_kwh": round(capacity_kwh, 3),
            "start_time": start.get("timestamp"),
            "end_time": last.get("timestamp"),
            "direction": direction,
            "source": source,
            "temperature_c": temperature_c,
            "rejected": False,
            "rejection_reason": None,
        }

        model = self._battery_capacity_models.setdefault(vin, {})
        if not _BATTERY_CAPACITY_MIN_KWH <= capacity_kwh <= _BATTERY_CAPACITY_MAX_KWH:
            self._store_rejected_window(
                model, window, f"implausible capacity {capacity_kwh:.1f} kWh"
            )
            return

        current_median = self._weighted_median_capacity(
            self._valid_accepted_windows(model)
        )
        if current_median is not None and current_median > 0:
            deviation = abs(capacity_kwh - current_median) / current_median * 100
            if deviation > _BATTERY_CAPACITY_OUTLIER_DEVIATION_PCT:
                self._store_rejected_window(
                    model, window, f"deviates {deviation:.1f}% from rolling median"
                )
                return

        accepted = model.get("accepted_windows")
        accepted = list(accepted) if isinstance(accepted, list) else []
        accepted.append(window)
        accepted.sort(key=lambda item: item.get("end_time") or "", reverse=True)
        model["accepted_windows"] = accepted[:_BATTERY_CAPACITY_MAX_ESTIMATES]

    @staticmethod
    def _store_rejected_window(
        model: dict[str, Any], window: dict[str, Any], reason: str
    ) -> None:
        """Retain an implausible window for diagnostics, excluded from metrics."""
        rejected_window = dict(window)
        rejected_window["rejected"] = True
        rejected_window["rejection_reason"] = reason
        rejected = model.get("rejected_windows")
        rejected = list(rejected) if isinstance(rejected, list) else []
        rejected.append(rejected_window)
        rejected.sort(key=lambda item: item.get("end_time") or "", reverse=True)
        model["rejected_windows"] = rejected[:_BATTERY_CAPACITY_MAX_REJECTED]

    @staticmethod
    def _record_high_soc_observation(
        model: dict[str, Any], sample: dict[str, Any], source: str
    ) -> None:
        """Retain a bounded high-SOC comparison observation for diagnostics."""
        capacity_kwh = sample["energy"] / sample["soc"] * 100
        observations = model.get("high_soc_observations")
        observations = list(observations) if isinstance(observations, list) else []
        observations.append(
            {
                "soc": round(sample["soc"], 3),
                "energy": round(sample["energy"], 3),
                "capacity_kwh": round(capacity_kwh, 3),
                "timestamp": sample["timestamp"],
                "source": source,
            }
        )
        observations.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        model["high_soc_observations"] = observations[:_BATTERY_HIGH_SOC_MAX_OBSERVATIONS]

    @staticmethod
    def _record_live_capacity_observation(
        model: dict[str, Any], sample: dict[str, Any]
    ) -> None:
        """Store the current BMS-derived usable capacity from one live frame."""
        model["latest_live_capacity_observation"] = {
            "soc": round(sample["soc"], 3),
            "energy": round(sample["energy"], 3),
            "capacity_kwh": round(sample["energy"] / sample["soc"] * 100, 3),
            "timestamp": sample["timestamp"],
        }

    def _latest_live_capacity_observation(
        self, vin: str, model: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return a valid current BMS-derived capacity observation, if present."""
        observation = model.get("latest_live_capacity_observation")
        if not isinstance(observation, dict):
            return None
        capacity = observation.get("capacity_kwh")
        soc = observation.get("soc")
        if (
            self._is_finite_number(capacity)
            and capacity > 0
            and self._is_finite_number(soc)
            and 0 < soc <= 100
            and self._battery_reference_capacity(vin) > 0
            and capacity <= self._battery_reference_capacity(vin)
        ):
            return observation
        return None

    @classmethod
    def _valid_accepted_windows(
        cls, model: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return non-rejected accepted windows that pass sanity bounds."""
        windows = model.get("accepted_windows")
        if not isinstance(windows, list):
            return []
        valid: list[dict[str, Any]] = []
        for window in windows:
            if not isinstance(window, dict) or window.get("rejected"):
                continue
            capacity = window.get("capacity_kwh")
            delta_soc = window.get("delta_soc")
            if (
                cls._is_finite_number(capacity)
                and _BATTERY_CAPACITY_MIN_KWH <= capacity <= _BATTERY_CAPACITY_MAX_KWH
                and cls._is_finite_number(delta_soc)
                and _BATTERY_CAPACITY_MIN_SOC_SPAN <= delta_soc <= 100
            ):
                valid.append(window)
        return valid

    @staticmethod
    def _weighted_median_capacity(
        windows: list[dict[str, Any]],
    ) -> float | None:
        """Return the ΔSOC-weighted median capacity across windows."""
        if not windows:
            return None
        ordered = sorted(windows, key=lambda item: float(item["capacity_kwh"]))
        total_weight = sum(float(item["delta_soc"]) for item in ordered)
        if total_weight <= 0:
            return float(median([float(item["capacity_kwh"]) for item in ordered]))
        half_weight = total_weight / 2
        cumulative = 0.0
        for window in ordered:
            cumulative += float(window["delta_soc"])
            if cumulative >= half_weight:
                return float(window["capacity_kwh"])
        return float(ordered[-1]["capacity_kwh"])

    @staticmethod
    def _median_absolute_deviation(
        values: list[float], center: float
    ) -> float:
        """Return the median absolute deviation of values around a center."""
        if not values:
            return 0.0
        return float(median([abs(value - center) for value in values]))

    def _battery_reference_capacity(self, vin: str) -> float:
        """Return the configured usable-when-new capacity, or 0 if unset."""
        references = self.entry.options.get(CONF_BATTERY_REFERENCE_CAPACITIES, {})
        reference = references.get(vin) if isinstance(references, dict) else None
        if self._is_finite_number(reference) and reference > 0:
            return float(reference)
        return 0.0

    def _battery_capacity_metrics(self, vin: str) -> dict[str, Any]:
        """Return capacity, SOH, and confidence from live telemetry or windows."""
        model = self._battery_capacity_models.get(vin, {})
        windows = self._valid_accepted_windows(model)
        live_observation = self._latest_live_capacity_observation(vin, model)
        estimate = (
            float(live_observation["capacity_kwh"])
            if live_observation is not None
            else self._weighted_median_capacity(windows)
        )
        if estimate is None:
            return {}
        estimated_capacity = round(float(estimate), 2)
        total_soc_span = sum(float(window["delta_soc"]) for window in windows)
        confidence = (
            round(float(live_observation["soc"]), 1)
            if live_observation is not None
            else round(min(100.0, total_soc_span), 1)
        )
        metrics: dict[str, Any] = {
            "estimated_usable_capacity": estimated_capacity,
            "battery_soh_confidence": confidence,
        }
        reference_capacity_kwh = self._battery_reference_capacity(vin)
        if reference_capacity_kwh > 0:
            metrics["estimated_battery_soh"] = round(
                estimated_capacity / reference_capacity_kwh * 100, 1
            )
        return metrics

    def _record_daily_battery_snapshot(self, vin: str) -> None:
        """Persist one bounded daily snapshot when a valid estimate exists."""
        metrics = self._battery_capacity_metrics(vin)
        capacity = metrics.get("estimated_usable_capacity")
        if capacity is None:
            return
        model = self._battery_capacity_models.setdefault(vin, {})
        day = datetime.now(timezone.utc).date().isoformat()
        snapshots = model.get("daily_snapshots")
        snapshots = list(snapshots) if isinstance(snapshots, list) else []
        snapshot = {
            "date": day,
            "usable_capacity_kwh": capacity,
            "soh_percent": metrics.get("estimated_battery_soh"),
            "confidence": metrics.get("battery_soh_confidence"),
        }
        snapshots = [item for item in snapshots if item.get("date") != day]
        snapshots.append(snapshot)
        snapshots.sort(key=lambda item: item.get("date") or "", reverse=True)
        model["daily_snapshots"] = snapshots[:_BATTERY_SNAPSHOT_MAX_DAYS]

    def get_battery_capacity_diagnostics(self, vin: str) -> dict[str, Any]:
        """Return accepted capacity windows and data-quality indicators."""
        model = self._battery_capacity_models.get(vin, {})
        windows = self._valid_accepted_windows(model)
        live_observation = self._latest_live_capacity_observation(vin, model)
        estimation_method = (
            "live_energy_soc" if live_observation is not None else "window_slope"
        )
        rejected = model.get("rejected_windows")
        rejected_windows = [
            window for window in rejected if isinstance(window, dict)
        ] if isinstance(rejected, list) else []
        rejected_count = len(rejected_windows)
        accepted_source_counts = {
            source: sum(window.get("source") == source for window in windows)
            for source in ("live", "recorder")
        }
        rejected_source_counts = {
            source: sum(window.get("source") == source for window in rejected_windows)
            for source in ("live", "recorder")
        }
        if not windows:
            return {
                "accepted_window_count": 0,
                "accepted_windows": [],
                "accepted_window_sources": accepted_source_counts,
                "estimation_method": estimation_method,
                "live_capacity_kwh": (
                    live_observation["capacity_kwh"]
                    if live_observation is not None
                    else None
                ),
                "live_capacity_observed_at": (
                    live_observation["timestamp"]
                    if live_observation is not None
                    else None
                ),
                "rejected_window_count": rejected_count,
                "rejected_window_sources": rejected_source_counts,
                "rejected_windows": [
                    {
                        "capacity_kwh": window.get("capacity_kwh"),
                        "delta_soc": window.get("delta_soc"),
                        "end_time": window.get("end_time"),
                        "source": window.get("source", "unknown"),
                        "rejection_reason": window.get("rejection_reason"),
                    }
                    for window in rejected_windows
                ],
            }

        capacities = [float(window["capacity_kwh"]) for window in windows]
        estimate = (
            float(live_observation["capacity_kwh"])
            if live_observation is not None
            else self._weighted_median_capacity(windows)
        )
        observations = model.get("high_soc_observations")
        high_soc_capacities = [
            float(observation["capacity_kwh"])
            for observation in observations
            if isinstance(observation, dict)
            and self._is_finite_number(observation.get("capacity_kwh"))
        ] if isinstance(observations, list) else []
        high_soc_median = median(high_soc_capacities) if high_soc_capacities else None
        high_soc_deviation = (
            abs(high_soc_median - estimate) / estimate * 100
            if high_soc_median is not None and estimate not in (None, 0)
            else None
        )
        snapshots = model.get("daily_snapshots")
        recent_snapshots = [
            snapshot for snapshot in snapshots if isinstance(snapshot, dict)
        ][:30] if isinstance(snapshots, list) else []
        trend_change_kwh = None
        trend_stable = False
        snapshot_capacities = [
            float(snapshot["usable_capacity_kwh"])
            for snapshot in recent_snapshots
            if self._is_finite_number(snapshot.get("usable_capacity_kwh"))
        ]
        if len(recent_snapshots) >= 2:
            newest_capacity = recent_snapshots[0].get("usable_capacity_kwh")
            oldest_capacity = recent_snapshots[-1].get("usable_capacity_kwh")
            if self._is_finite_number(newest_capacity) and self._is_finite_number(
                oldest_capacity
            ):
                trend_change_kwh = round(
                    float(newest_capacity) - float(oldest_capacity), 3
                )
        if len(snapshot_capacities) >= 7:
            snapshot_center = float(median(snapshot_capacities))
            trend_stable = self._median_absolute_deviation(
                snapshot_capacities, snapshot_center
            ) <= 1.0
        newest_end = max(
            (window.get("end_time") for window in windows if window.get("end_time")),
            default=None,
        )
        newest_age_hours: float | None = None
        if isinstance(newest_end, str):
            try:
                end_dt = datetime.fromisoformat(newest_end)
            except ValueError:
                end_dt = None
            if end_dt is not None and end_dt.tzinfo is not None:
                newest_age_hours = round(
                    (datetime.now(timezone.utc) - end_dt).total_seconds() / 3600, 2
                )
        detail = [
            {
                "start_soc": window.get("start_soc"),
                "end_soc": window.get("end_soc"),
                "start_energy": window.get("start_energy"),
                "end_energy": window.get("end_energy"),
                "delta_soc": window.get("delta_soc"),
                "delta_energy": window.get("delta_energy"),
                "capacity_kwh": window.get("capacity_kwh"),
                "start_time": window.get("start_time"),
                "end_time": window.get("end_time"),
                "direction": window.get("direction"),
                "source": window.get("source", "unknown"),
                "temperature_c": window.get("temperature_c"),
            }
            for window in windows
        ]
        return {
            "accepted_window_count": len(windows),
            "accepted_windows": detail,
            "accepted_window_sources": accepted_source_counts,
            "estimation_method": estimation_method,
            "live_capacity_kwh": (
                live_observation["capacity_kwh"]
                if live_observation is not None
                else None
            ),
            "live_capacity_observed_at": (
                live_observation["timestamp"]
                if live_observation is not None
                else None
            ),
            "accepted_capacity_min_kwh": round(min(capacities), 3),
            "accepted_capacity_max_kwh": round(max(capacities), 3),
            "high_soc_observation_count": len(high_soc_capacities),
            "high_soc_comparison_capacity_kwh": (
                round(high_soc_median, 3) if high_soc_median is not None else None
            ),
            "high_soc_deviation_percent": (
                round(high_soc_deviation, 2) if high_soc_deviation is not None else None
            ),
            "high_soc_discrepancy_warning": bool(
                high_soc_deviation is not None
                and high_soc_deviation > _BATTERY_HIGH_SOC_WARNING_DEVIATION_PCT
            ),
            "daily_snapshots_30d": recent_snapshots,
            "daily_snapshot_change_30d_kwh": trend_change_kwh,
            "daily_snapshot_trend_stable": trend_stable,
            "newest_window_age_hours": newest_age_hours,
            "median_absolute_deviation_kwh": (
                round(self._median_absolute_deviation(capacities, float(estimate)), 3)
                if estimate is not None
                else None
            ),
            "rejected_window_count": rejected_count,
            "rejected_window_sources": rejected_source_counts,
            "rejected_windows": [
                {
                    "capacity_kwh": window.get("capacity_kwh"),
                    "delta_soc": window.get("delta_soc"),
                    "end_time": window.get("end_time"),
                    "source": window.get("source", "unknown"),
                    "rejection_reason": window.get("rejection_reason"),
                }
                for window in rejected_windows
            ],
        }

    def get_battery_soh_diagnostics(self, vin: str) -> dict[str, Any]:
        """Return the inputs used to compute Battery SOH."""
        model = self._battery_capacity_models.get(vin, {})
        metrics = self._battery_capacity_metrics(vin)
        live_observation = self._latest_live_capacity_observation(vin, model)
        reference = self._battery_reference_capacity(vin)
        return {
            "usable_capacity_kwh": metrics.get("estimated_usable_capacity"),
            "original_usable_capacity_kwh": reference if reference > 0 else None,
            "estimation_method": (
                "live_energy_soc" if live_observation is not None else "window_slope"
            ),
            "live_capacity_observed_at": (
                live_observation["timestamp"]
                if live_observation is not None
                else None
            ),
        }

    def get_battery_confidence_diagnostics(self, vin: str) -> dict[str, Any]:
        """Return the accepted SOC coverage backing SOH Confidence."""
        model = self._battery_capacity_models.get(vin, {})
        windows = self._valid_accepted_windows(model)
        total_span = round(sum(float(window["delta_soc"]) for window in windows), 1)
        live_observation = self._latest_live_capacity_observation(vin, model)
        return {
            "total_soc_span": (
                round(float(live_observation["soc"]), 1)
                if live_observation is not None
                else total_span
            ),
            "window_count": len(windows),
            "estimation_method": (
                "live_energy_soc" if live_observation is not None else "window_slope"
            ),
        }

    def _migrate_battery_capacity_model(
        self, model: dict[str, Any]
    ) -> dict[str, Any]:
        """Migrate persisted battery-estimator state to the current schema."""
        if not isinstance(model, dict):
            return {}
        accepted: list[dict[str, Any]] = []
        existing_accepted = model.get("accepted_windows")
        if isinstance(existing_accepted, list):
            accepted = [window for window in existing_accepted if isinstance(window, dict)]
        elif isinstance(model.get("estimates"), list):
            legacy = model["estimates"]
            for estimate in legacy:
                if not isinstance(estimate, dict):
                    continue
                capacity = estimate.get("capacity_kwh")
                soc_span = estimate.get("soc_span")
                if not (
                    self._is_finite_number(capacity)
                    and self._is_finite_number(soc_span)
                ):
                    continue
                legacy_source = estimate.get("source")
                accepted.append(
                    {
                        "start_soc": estimate.get("start_soc_percent"),
                        "end_soc": estimate.get("end_soc_percent"),
                        "start_energy": estimate.get("start_energy_kwh"),
                        "end_energy": estimate.get("end_energy_kwh"),
                        "delta_soc": round(float(soc_span), 3),
                        "delta_energy": estimate.get("energy_delta_kwh"),
                        "capacity_kwh": round(float(capacity), 3),
                        "start_time": estimate.get("start_timestamp"),
                        "end_time": estimate.get("end_timestamp"),
                        "direction": None,
                        "source": (
                            "recorder" if legacy_source == "recorder" else "live"
                        ),
                        "temperature_c": None,
                        "rejected": False,
                        "rejection_reason": None,
                    }
                )
        migrated = {
            key: value
            for key, value in model.items()
            if key not in (
                "estimates",
                "anchor",
                "last_sample",
                "direction",
                "latest_live_capacity_observation",
            )
        }
        migrated["accepted_windows"] = accepted[-_BATTERY_CAPACITY_MAX_ESTIMATES:]
        rejected = migrated.get("rejected_windows")
        migrated["rejected_windows"] = (
            [window for window in rejected if isinstance(window, dict)][
                -_BATTERY_CAPACITY_MAX_REJECTED:
            ]
            if isinstance(rejected, list)
            else []
        )
        observations = migrated.get("high_soc_observations")
        migrated["high_soc_observations"] = (
            [item for item in observations if isinstance(item, dict)][
                -_BATTERY_HIGH_SOC_MAX_OBSERVATIONS:
            ]
            if isinstance(observations, list)
            else []
        )
        snapshots = migrated.get("daily_snapshots")
        migrated["daily_snapshots"] = (
            [item for item in snapshots if isinstance(item, dict)][
                -_BATTERY_SNAPSHOT_MAX_DAYS:
            ]
            if isinstance(snapshots, list)
            else []
        )
        migrated["model_version"] = _BATTERY_CAPACITY_MODEL_VERSION
        return migrated

    async def async_import_battery_history(self) -> None:
        """Seed capacity estimates from tightly paired Recorder history states."""
        if "recorder" not in self.hass.config.components:
            return

        from homeassistant.components.recorder import get_instance, history
        from homeassistant.helpers import entity_registry as er

        entity_registry = er.async_get(self.hass)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - _BATTERY_HISTORY_LOOKBACK
        imported = False
        attempted = False
        for vehicle in self._vehicles:
            vin = vehicle["vin"]
            model = self._battery_capacity_models.get(vin, {})
            last_attempt = model.get("history_import_attempted_at")
            if isinstance(last_attempt, str):
                try:
                    last_attempt_at = datetime.fromisoformat(last_attempt)
                except ValueError:
                    pass
                else:
                    if (
                        last_attempt_at.tzinfo is not None
                        and end_time - last_attempt_at
                        < _BATTERY_HISTORY_RETRY_INTERVAL
                    ):
                        continue

            soc_entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, f"{vin}_battery_level"
            )
            energy_entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, f"{vin}_energy_remaining"
            )
            if not soc_entity_id or not energy_entity_id:
                continue

            history_start_time = start_time
            last_import = model.get("history_import_completed_at")
            if isinstance(last_import, str):
                try:
                    last_import_at = datetime.fromisoformat(last_import)
                except ValueError:
                    last_import_at = None
                if last_import_at is not None and last_import_at.tzinfo is not None:
                    history_start_time = max(
                        start_time,
                        last_import_at.astimezone(timezone.utc),
                    )

            try:
                states = await get_instance(self.hass).async_add_executor_job(
                    partial(
                        history.get_significant_states,
                        hass=self.hass,
                        start_time=history_start_time,
                        end_time=end_time,
                        entity_ids=[soc_entity_id, energy_entity_id],
                        filters=None,
                        include_start_time_state=False,
                        significant_changes_only=False,
                        minimal_response=False,
                        no_attributes=True,
                    )
                )
                pairs = self._pair_battery_history_states(
                    states.get(soc_entity_id, []),
                    states.get(energy_entity_id, []),
                )
            except Exception:
                _LOGGER.warning(
                    "Unable to import battery history for %s",
                    vin,
                    exc_info=True,
                )
                failed_model = self._battery_capacity_models.setdefault(vin, {})
                failed_model["history_import_attempted_at"] = (
                    end_time.isoformat()
                )
                attempted = True
                continue

            attempted = True
            current_model = self._battery_capacity_models.setdefault(vin, {})
            if not pairs:
                current_model["history_import_attempted_at"] = (
                    end_time.isoformat()
                )
                self._battery_capacity_models[vin] = current_model
                continue

            active_window = current_model.get("active_window")
            current_model["active_window"] = None
            for observed_at, soc_percent, energy_remaining_kwh in pairs:
                self.record_battery_capacity_sample(
                    vin,
                    soc_percent,
                    energy_remaining_kwh,
                    observed_at,
                    source="recorder",
                )
            self.flush_active_battery_window(vin)
            if isinstance(active_window, dict):
                current_model["active_window"] = active_window
            metrics = self._battery_capacity_metrics(vin)
            imported_model = self._battery_capacity_models.setdefault(vin, {})
            imported_model["history_import_attempted_at"] = end_time.isoformat()
            imported_model["history_import_completed_at"] = end_time.isoformat()
            if not metrics:
                continue

            vehicle_data = self.data.get(vin, {})
            response = vehicle_data.get("response", {})
            charge_state = response.get("charge_state")
            if isinstance(charge_state, dict):
                charge_state.update(metrics)
                imported = True

        if imported:
            self.async_set_updated_data(dict(self.data))
        if attempted:
            self._telemetry_store.async_delay_save(
                self._telemetry_store_payload, 30
            )

    @staticmethod
    def _pair_battery_history_states(
        soc_states: list[Any], energy_states: list[Any]
    ) -> list[tuple[datetime, float, float]]:
        """Pair source states only when Recorder timestamps identify one update."""
        energy_index = 0
        pairs: list[tuple[datetime, float, float]] = []
        for soc_state in soc_states:
            if energy_index >= len(energy_states):
                break
            while energy_index + 1 < len(energy_states) and abs(
                energy_states[energy_index + 1].last_updated
                - soc_state.last_updated
            ) <= abs(
                energy_states[energy_index].last_updated
                - soc_state.last_updated
            ):
                energy_index += 1
            energy_state = energy_states[energy_index]
            if (
                abs(energy_state.last_updated - soc_state.last_updated)
                > _BATTERY_HISTORY_PAIR_TOLERANCE
            ):
                continue
            try:
                soc_percent = float(soc_state.state)
                energy_remaining_kwh = float(energy_state.state)
            except (TypeError, ValueError):
                continue
            if not (
                math.isfinite(soc_percent)
                and math.isfinite(energy_remaining_kwh)
            ):
                continue
            pairs.append(
                (
                    max(soc_state.last_updated, energy_state.last_updated),
                    soc_percent,
                    energy_remaining_kwh,
                )
            )
            energy_index += 1
        return pairs

    def set_telemetry_data(
        self,
        vin: str,
        response: dict[str, Any],
        received_fields: set[str] | None = None,
        processed_fields: set[str] | None = None,
        raw_signals: dict[str, Any] | None = None,
        vehicle_capture_at: datetime | None = None,
    ) -> None:
        """Publish and persist state sourced exclusively from telemetry."""
        if received_fields is not None and processed_fields is not None:
            self.record_telemetry_update(
                vin, received_fields, processed_fields, vehicle_capture_at
            )
        if raw_signals is not None:
            self._telemetry_raw_signals[vin] = dict(raw_signals)

        # Apply the score and door composites without deriving telemetry-owned
        # brick imbalance from independently received extrema.
        processed_response = self._process_vehicle_response(response)
        self._reconcile_command_states(vin, processed_response, processed_fields)

        updated_data = dict(self.data or self._empty_telemetry_data())
        updated_data[vin] = {"response": processed_response}
        self.async_set_updated_data(updated_data)
        self._record_daily_battery_snapshot(vin)
        self._telemetry_store.async_delay_save(
            self._telemetry_store_payload, 30
        )

    def _publish_command_state(
        self, vin: str, updates: dict[str, dict[str, Any]]
    ) -> None:
        """Publish deterministic state after a successful command."""
        updated_data = dict(self.data or self._empty_telemetry_data())
        vehicle_data = dict(updated_data.get(vin, {}))
        response = dict(vehicle_data.get("response", {}))
        for section_name, section_updates in updates.items():
            section = dict(response.get(section_name, {}))
            section.update(section_updates)
            response[section_name] = section
            for key, value in section_updates.items():
                if (section_name, key) in _COMMAND_STATE_SIGNALS:
                    self._pending_command_states[(vin, section_name, key)] = (
                        value,
                        datetime.now().astimezone()
                        + _COMMAND_STATE_CONFIRMATION_TIMEOUT,
                    )
        vehicle_data["response"] = response
        updated_data[vin] = vehicle_data
        self.async_set_updated_data(updated_data)

    def _reconcile_command_states(
        self,
        vin: str,
        response: dict[str, Any],
        processed_fields: set[str] | None,
    ) -> None:
        """Keep accepted command state until matching telemetry or timeout."""
        now = datetime.now().astimezone()
        for pending_key, (expected, expires_at) in list(
            self._pending_command_states.items()
        ):
            pending_vin, section_name, key = pending_key
            if now >= expires_at:
                self._pending_command_states.pop(pending_key, None)
                continue
            if pending_vin != vin or not processed_fields:
                continue
            signal_name = _COMMAND_STATE_SIGNALS[(section_name, key)]
            if signal_name not in processed_fields:
                continue
            section = response.get(section_name)
            if not isinstance(section, dict):
                continue
            if section.get(key) == expected:
                self._pending_command_states.pop(pending_key, None)
                audit = self._telemetry_metadata.setdefault(vin, {}).get("command_audit")
                if isinstance(audit, dict):
                    confirmed_at = datetime.now(timezone.utc)
                    audit["confirmation_time"] = confirmed_at
                    dispatched_at = audit.get("dispatch_time")
                    if isinstance(dispatched_at, datetime):
                        audit["confirmation_latency_seconds"] = round(
                            (confirmed_at - dispatched_at).total_seconds(), 3
                        )
            else:
                section[key] = expected

    def _complete_command_response(
        self,
        vin: str,
        command: str,
        body: dict[str, Any],
        response: Any,
    ) -> dict[str, Any]:
        """Validate a command response and publish its deterministic outcome."""
        validated_response = self._validate_command_response(response)
        audit = self._telemetry_metadata.setdefault(vin, {}).get("command_audit")
        if isinstance(audit, dict):
            audit["response_status"] = "accepted"
        updates = self._command_state_updates(command, body)
        if updates:
            self._publish_command_state(vin, updates)
        return validated_response

    def _command_state_updates(
        self, command: str, body: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Return deterministic state changes for an accepted command."""
        fixed_updates: dict[str, dict[str, dict[str, Any]]] = {
            "lock": {"vehicle_state": {"locked": True}},
            "unlock": {"vehicle_state": {"locked": False}},
            "sentry_on": {"vehicle_state": {"sentry_mode": True}},
            "sentry_off": {"vehicle_state": {"sentry_mode": False}},
            "valet_mode_on": {"vehicle_state": {"valet_mode": True}},
            "valet_mode_off": {"vehicle_state": {"valet_mode": False}},
            "charge_port_open": {
                "charge_state": {"charge_port_door_open": True}
            },
            "charge_port_close": {
                "charge_state": {"charge_port_door_open": False}
            },
            "climate_on": {"climate_state": {"is_climate_on": True}},
            "climate_off": {"climate_state": {"is_climate_on": False}},
            "preconditioning_max": {
                "climate_state": {"defrost_mode": "Max"}
            },
            "preconditioning_max_off": {
                "climate_state": {"defrost_mode": "Off"}
            },
        }
        if command in fixed_updates:
            return fixed_updates[command]

        if command == "steering_heater" and isinstance(body.get("on"), bool):
            return {
                "climate_state": {"steering_wheel_heater": body["on"]}
            }
        if command == "set_temps" and isinstance(
            temperature := body.get("driver_temp"), (int, float)
        ) and not isinstance(temperature, bool):
            passenger_temperature = body.get("passenger_temp", temperature)
            if isinstance(passenger_temperature, bool) or not isinstance(
                passenger_temperature, (int, float)
            ):
                return {}
            return {
                "climate_state": {
                    "driver_temp_setting": temperature,
                    "passenger_temp_setting": passenger_temperature,
                }
            }
        if command == "set_charge_limit" and isinstance(
            percent := body.get("percent"), (int, float)
        ) and not isinstance(percent, bool):
            return {"charge_state": {"charge_limit_soc": int(percent)}}
        if command == "seat_heater":
            seat_keys = (
                "seat_heater_left",
                "seat_heater_right",
                "seat_heater_rear_left",
                "seat_heater_rear_right",
            )
            seat_position = body.get("seat_position")
            level = body.get("level")
            if (
                isinstance(seat_position, int)
                and not isinstance(seat_position, bool)
                and 0 <= seat_position < len(seat_keys)
                and isinstance(level, int)
                and not isinstance(level, bool)
            ):
                return {"climate_state": {seat_keys[seat_position]: level}}
        if command in {"window_vent", "window_close"}:
            position = 1 if command == "window_vent" else 0
            return {
                "vehicle_state": {
                    key: position
                    for key in ("fd_window", "fp_window", "rd_window", "rp_window")
                }
            }
        if command == "trunk_front":
            return {"vehicle_state": {"ft": "Open"}}
        return {}

    def _process_vehicle_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Process vehicle response to compute derived fields."""
        # Create a copy to avoid modifying the original
        processed = dict(response)
        
        self._apply_charging_composites(processed, {}, set(), set())

        # Apply door state expansion for Fleet API responses
        # Fleet API may return composite DoorState that needs expansion
        self._apply_door_state(processed, {}, set())
        
        return processed

    def _apply_charging_composites(
        self,
        response: dict[str, Any],
        last_signals: dict[str, Any],
        received_fields: set[str],
        processed_fields: set[str],
    ) -> None:
        """Update score from an existing valid brick imbalance and current SOC."""
        del last_signals, received_fields, processed_fields
        charge_state = response.setdefault("charge_state", {})
        imbalance = charge_state.get("brick_voltage_imbalance")
        soc = charge_state.get("battery_level")
        if not self._is_valid_soc(soc):
            soc = charge_state.get("usable_battery_level")
        if (
            not self._is_finite_number(imbalance)
            or imbalance < 0
            or not self._is_valid_soc(soc)
        ):
            return

        if soc >= 90:
            if imbalance <= 10:
                score = 100
            elif imbalance <= 20:
                score = 85
            elif imbalance <= 30:
                score = 70
            else:
                score = 55
        elif soc >= 50:
            if imbalance <= 20:
                score = 100
            elif imbalance <= 30:
                score = 85
            elif imbalance <= 50:
                score = 70
            else:
                score = 55
        else:
            if imbalance <= 40:
                score = 100
            elif imbalance <= 80:
                score = 85
            elif imbalance <= 120:
                score = 70
            else:
                score = 55
        charge_state["battery_balance_score"] = score

    @staticmethod
    def _is_finite_number(value: Any) -> bool:
        """Return whether a value is a finite non-boolean number."""
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    @classmethod
    def _is_valid_soc(cls, value: Any) -> bool:
        """Return whether a value is a valid state of charge percentage."""
        return cls._is_finite_number(value) and 0 <= value <= 100

    def _apply_door_state(
        self,
        response: dict[str, Any],
        signals: dict[str, Any],
        processed_fields: set[str],
    ) -> None:
        """Expand the composite DoorState signal into Fleet API door fields."""
        # Handle telemetry format: signals = {"DoorState": {"DriverFront": 1, ...}}
        doors = signals.get("DoorState")
        if isinstance(doors, dict):
            door_mapping = {
                "DriverFront": "df",
                "DriverRear": "dr",
                "PassengerFront": "pf",
                "PassengerRear": "pr",
                "TrunkFront": "ft",
                "TrunkRear": "rt",
            }
            vehicle_state = response.setdefault("vehicle_state", {})
            for telemetry_key, state_key in door_mapping.items():
                if telemetry_key in doors:
                    # Convert 0/1 to "Closed"/"Open" for ENUM sensors
                    vehicle_state[state_key] = "Open" if self._is_truthy(doors[telemetry_key]) else "Closed"
            processed_fields.add("DoorState")
            return
        
        # Handle Fleet API format: response already has individual door states (df, dr, pf, pr, ft, rt)
        # Just ensure they're converted to "Open"/"Closed" strings for ENUM sensors
        vehicle_state = response.get("vehicle_state", {})
        door_keys = ["df", "dr", "pf", "pr", "ft", "rt"]
        for key in door_keys:
            if key in vehicle_state:
                vehicle_state[key] = "Open" if self._is_truthy(vehicle_state[key]) else "Closed"

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        """Normalize telemetry booleans and state enums."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value)
        marker = text.rfind("State")
        tail = text[marker + len("State"):] if marker >= 0 else text
        return tail.strip().lower() in {
            "true",
            "1",
            "on",
            "open",
            "enabled",
            "armed",
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Return cached telemetry state without polling Tesla vehicle data."""
        return self.data or self._empty_telemetry_data()

    async def _ensure_valid_token(self) -> None:
        """Ensure we have a valid access token."""
        import time

        if self._access_token and time.time() < self._token_expires_at - 60:
            return

        # Token expired or not set, refresh from config entry
        tokens = self.entry.data.get("tokens", {})
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise UpdateFailed("No refresh token available")

        # Refresh token
        await self._refresh_access_token(refresh_token)

    async def _refresh_access_token(self, refresh_token: str) -> None:
        """Refresh the access token."""
        import time

        client_id = self.entry.data.get("client_id")
        client_secret = self.entry.data.get("client_secret")

        if not client_id or not client_secret:
            raise UpdateFailed("Missing OAuth credentials")

        session = async_get_clientsession(self.hass)
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }

        async with session.post(
            "https://auth.tesla.com/oauth2/v3/token",
            data=data,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise UpdateFailed(f"Token refresh failed: {resp.status} - {text}")

            token_data = await resp.json()

        self._access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 28800)
        self._token_expires_at = time.time() + expires_in

        # Update stored tokens
        tokens = self.entry.data.get("tokens", {})
        new_tokens = {
            **tokens,
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", refresh_token),
            "expires_at": int(self._token_expires_at),
        }

        # Update config entry
        new_data = {**self.entry.data, "tokens": new_tokens}
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

        _LOGGER.debug("Refreshed access token")

    async def _get_ssl_context(self) -> aiohttp.ClientSSLContext:
        """Get SSL context for proxy communication."""
        ca_path = self.proxy_manager.cert_path
        if not ca_path or not ca_path.exists():
            # Fallback: disable verification (not recommended for production)
            return False

        def _create_ssl_context() -> ssl.SSLContext:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.load_verify_locations(str(ca_path))
            return ssl_context

        return await self.hass.async_add_executor_job(_create_ssl_context)

    async def async_send_command(
        self,
        vin: str,
        command: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a command to the vehicle."""
        self._telemetry_metadata.setdefault(vin, {})["command_audit"] = {
            "command": command,
            "dispatch_time": datetime.now(timezone.utc),
            "response_status": "in_progress",
        }
        self._acquire_command_slot(vin)
        try:
            lock = self._command_locks.setdefault(vin, asyncio.Lock())
            async with lock:
                return await self._async_send_command(vin, command, body)
        except Exception as err:
            audit = self._telemetry_metadata.setdefault(vin, {}).get("command_audit")
            if isinstance(audit, dict):
                audit["response_status"] = "failed"
                audit["failure_category"] = type(err).__name__
            raise
        finally:
            self._release_command_slot(vin)

    async def _async_send_command(
        self,
        vin: str,
        command: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Execute one command for a vehicle that telemetry reports as awake."""
        if not self.proxy_manager.is_running:
            raise RuntimeError("Proxy not running")

        if command == "wake_up":
            raise RuntimeError("Use async_wake_up to wake a vehicle")

        if not self.is_vehicle_awake(vin):
            raise RuntimeError(
                f"Vehicle {vin} is asleep. Wake it and wait for it to become available "
                "before sending commands."
            )

        await self._ensure_valid_token()

        session = async_get_clientsession(self.hass)
        ssl_context = await self._get_ssl_context()

        cmd = COMMANDS.get(command, command)
        url = f"https://{PROXY_HOST}:{PROXY_PORT}{API_COMMAND.format(vin=vin, command=cmd)}"
        headers = {"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"}

        # Use predefined body or provided body
        if body is None:
            body = dict(COMMAND_BODIES.get(command, {}))

        if command in {"window_vent", "window_close"}:
            drive_state = self.data.get(vin, {}).get("response", {}).get(
                "drive_state", {}
            )
            latitude = drive_state.get("latitude")
            longitude = drive_state.get("longitude")
            if not isinstance(latitude, (int, float)) or not isinstance(
                longitude, (int, float)
            ):
                raise RuntimeError(
                    "Window control requires a current vehicle location from telemetry"
                )
            body = {**body, "lat": latitude, "lon": longitude}

        async with session.post(url, headers=headers, json=body, ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 401:
                self._token_expires_at = 0
                await self._ensure_valid_token()
                headers["Authorization"] = f"Bearer {self._access_token}"
                async with session.post(url, headers=headers, json=body, ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=30)) as retry_resp:
                    if retry_resp.status != 200:
                        text = await retry_resp.text()
                        raise RuntimeError(f"Command failed: {retry_resp.status} - {text}")
                    return self._complete_command_response(
                        vin, command, body, await retry_resp.json()
                    )

            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Command failed: {resp.status} - {text}")

            return self._complete_command_response(
                vin, command, body, await resp.json()
            )

    async def async_wake_up(self, vin: str) -> dict[str, Any]:
        """Wake a vehicle and wait until API or telemetry confirms readiness."""
        if self.is_vehicle_awake(vin):
            _LOGGER.debug("Skipping wake-up for already awake vehicle %s", vin)
            return {"response": {"state": "online"}}

        self._acquire_command_slot(vin)
        try:
            telemetry_generation, telemetry_event = self._prepare_telemetry_wait(vin)
            try:
                self._set_vehicle_waking(vin, True)
                self._require_telemetry_receiver()
                response = await self._async_wake_up(vin)
                if self._wake_response_is_online(response):
                    self.set_connectivity_status(vin, "CONNECTED")
                    return response
                try:
                    await self._async_wait_for_telemetry_after(
                        vin, telemetry_generation, telemetry_event
                    )
                except TimeoutError as err:
                    raise RuntimeError(
                        f"Vehicle {vin} did not become ready within "
                        f"{WAKE_TELEMETRY_TIMEOUT_SECONDS} seconds after wake-up"
                    ) from err
                return response
            finally:
                self._set_vehicle_waking(vin, False)
        finally:
            self._release_command_slot(vin)

    async def _async_wake_up(self, vin: str) -> dict[str, Any]:
        """Request that the vehicle wake through the Fleet API."""
        if not self.proxy_manager.is_running:
            raise RuntimeError("Proxy not running")

        await self._ensure_valid_token()

        session = async_get_clientsession(self.hass)
        ssl_context = await self._get_ssl_context()

        url = f"https://{PROXY_HOST}:{PROXY_PORT}{API_WAKE_UP.format(vin=vin)}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        async with session.post(
            url,
            headers=headers,
            json={},
            ssl=ssl_context,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Wake up failed: {resp.status} - {text}")

            return await resp.json()

    async def async_configure_fleet_telemetry(self, vin: str) -> dict[str, Any]:
        """Register a Fleet Telemetry destination through the command proxy."""
        if not self.proxy_manager.is_running:
            self._record_telemetry_registration_result(vin, "failed")
            raise RuntimeError("Proxy not running")

        hostname = self.entry.options.get(CONF_TELEMETRY_HOSTNAME, "").strip()
        port = self.entry.options.get(CONF_TELEMETRY_PORT)
        ca_path = self.proxy_manager.telemetry_ca_path
        if not hostname or not port or not ca_path or not ca_path.is_file():
            self._record_telemetry_registration_result(vin, "failed")
            raise RuntimeError(
                "Configure a telemetry hostname and restart the integration first"
            )

        try:
            await self._ensure_valid_token()
            telemetry_ca = await self.hass.async_add_executor_job(ca_path.read_text)
            ssl_context = await self._get_ssl_context()
        except Exception:
            self._record_telemetry_registration_result(vin, "failed")
            raise

        session = async_get_clientsession(self.hass)
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        body = {
            "vins": [vin],
            "config": {
                "hostname": hostname,
                "port": int(port),
                "ca": telemetry_ca,
                "fields": _FLEET_TELEMETRY_FIELDS,
            },
        }
        url = f"https://{PROXY_HOST}:{PROXY_PORT}{API_FLEET_TELEMETRY_CONFIG}"
        try:
            async with session.post(
                url,
                headers=headers,
                json=body,
                ssl=ssl_context,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise RuntimeError(
                        "Fleet Telemetry configuration failed: "
                        f"{response.status} - {text}"
                    )
                result = await response.json()
        except Exception:
            self._record_telemetry_registration_result(vin, "failed")
            raise

        self._record_telemetry_registration_result(vin, "registered")
        pending_vins = self.entry.options.get(
            CONF_TELEMETRY_REGISTRATION_REQUIRED_VINS, []
        )
        if isinstance(pending_vins, list) and vin in pending_vins:
            options = dict(self.entry.options)
            options[CONF_TELEMETRY_REGISTRATION_REQUIRED_VINS] = [
                pending_vin for pending_vin in pending_vins if pending_vin != vin
            ]
            self.hass.config_entries.async_update_entry(self.entry, options=options)
        return result

    def _record_telemetry_registration_result(self, vin: str, status: str) -> None:
        """Persist a non-sensitive Fleet Telemetry registration outcome."""
        metadata = self._telemetry_metadata.setdefault(vin, {})
        metadata["registration_status"] = status
        metadata["last_registration"] = datetime.now(timezone.utc)
        self._telemetry_store.async_delay_save(self._telemetry_store_payload, 30)
        self.async_update_listeners()

    def get_vehicle_config(self, vin: str) -> dict[str, Any] | None:
        """Get vehicle configuration."""
        for vehicle in self._vehicles:
            if vehicle["vin"] == vin:
                return vehicle
        return None

    async def async_fetch_initial_vehicle_data(self, vin: str) -> dict[str, Any] | None:
        """Fetch initial vehicle data from Fleet API via proxy."""
        if not self.proxy_manager.is_running:
            _LOGGER.warning("Proxy not running, cannot fetch initial vehicle data for %s", vin)
            return None

        try:
            await self._ensure_valid_token()
        except Exception as err:
            _LOGGER.error("Failed to ensure valid token for %s: %s", vin, err)
            return None

        session = async_get_clientsession(self.hass)
        ssl_context = await self._get_ssl_context()

        url = f"https://{PROXY_HOST}:{PROXY_PORT}/api/1/vehicles/{vin}/vehicle_data"
        headers = {"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"}

        try:
            async with session.get(url, headers=headers, ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 401:
                    self._token_expires_at = 0
                    await self._ensure_valid_token()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    async with session.get(url, headers=headers, ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=30)) as retry_resp:
                        if retry_resp.status != 200:
                            text = await retry_resp.text()
                            _LOGGER.error("Failed to fetch vehicle data for %s: %s - %s", vin, retry_resp.status, text)
                            return None
                        return await retry_resp.json()

                if resp.status == 408:
                    _LOGGER.debug(
                        "Vehicle %s is offline or asleep; skipping Fleet API poll", vin
                    )
                    return None

                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Failed to fetch vehicle data for %s: %s - %s", vin, resp.status, text)
                    return None

                return await resp.json()
        except Exception as err:
            _LOGGER.error("Error fetching initial vehicle data for %s: %s", vin, err)
            return None
