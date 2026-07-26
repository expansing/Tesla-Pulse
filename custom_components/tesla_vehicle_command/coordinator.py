"""Data coordinator for Tesla Vehicle Command."""

from __future__ import annotations

import logging
import ssl
from datetime import datetime, timedelta
from typing import Any

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
    CONF_TELEMETRY_HOSTNAME,
    CONF_TELEMETRY_PORT,
    DOMAIN,
    PROXY_HOST,
    PROXY_PORT,
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
        self._telemetry_metadata: dict[str, dict[str, Any]] = {}
        self._telemetry_raw_signals: dict[str, dict[str, Any]] = {}

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

    def record_telemetry_update(
        self,
        vin: str,
        received_fields: set[str],
        processed_fields: set[str],
    ) -> None:
        """Record a successfully processed telemetry message for a vehicle."""
        self._telemetry_metadata[vin] = {
            "last_received": datetime.now().astimezone(),
            "received_fields": sorted(received_fields),
            "processed_fields": sorted(processed_fields),
        }

    def get_telemetry_status(self, vin: str) -> dict[str, Any]:
        """Return telemetry health and diagnostic metadata for a vehicle."""
        metadata = self._telemetry_metadata.get(vin, {})
        last_received = metadata.get("last_received")
        if last_received and datetime.now().astimezone() - last_received <= timedelta(
            minutes=15
        ):
            state = "receiving"
        elif last_received:
            state = "stale"
        elif self._telemetry_receiver_available:
            state = "waiting"
        else:
            state = "unavailable"

        return {"state": state, **metadata}

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
            last_received = metadata.get("last_received")
            if isinstance(last_received, str):
                try:
                    last_received = datetime.fromisoformat(last_received)
                except ValueError:
                    continue
            if isinstance(last_received, datetime):
                self._telemetry_metadata[vin] = {
                    "last_received": last_received,
                    "received_fields": metadata.get("received_fields", []),
                    "processed_fields": metadata.get("processed_fields", []),
                }

        raw_signals = stored_cache.get("raw_signals", {})
        if isinstance(raw_signals, dict):
            self._telemetry_raw_signals = {
                vin: signals
                for vin, signals in raw_signals.items()
                if vin in data and isinstance(signals, dict)
            }
        self.async_set_updated_data(data)

    def get_telemetry_raw_signals(self, vin: str) -> dict[str, Any]:
        """Return persisted raw signals used to reassemble delta records."""
        return dict(self._telemetry_raw_signals.get(vin, {}))

    def _telemetry_store_payload(self) -> dict[str, Any]:
        """Serialize telemetry state and diagnostics for local storage."""
        metadata = {
            vin: {
                **details,
                "last_received": details["last_received"].isoformat(),
            }
            for vin, details in self._telemetry_metadata.items()
            if isinstance(details.get("last_received"), datetime)
        }
        return {
            "vehicles": self.data if isinstance(self.data, dict) else {},
            "metadata": metadata,
            "raw_signals": self._telemetry_raw_signals,
        }

    def set_telemetry_data(
        self,
        vin: str,
        response: dict[str, Any],
        received_fields: set[str] | None = None,
        processed_fields: set[str] | None = None,
        raw_signals: dict[str, Any] | None = None,
    ) -> None:
        """Publish and persist state sourced exclusively from telemetry."""
        if received_fields is not None and processed_fields is not None:
            self.record_telemetry_update(vin, received_fields, processed_fields)
        if raw_signals is not None:
            self._telemetry_raw_signals[vin] = dict(raw_signals)

        # Process the response to compute derived fields (imbalance, balance score, door states)
        processed_response = self._process_vehicle_response(response)

        updated_data = dict(self.data or self._empty_telemetry_data())
        updated_data[vin] = {"response": processed_response}
        self.async_set_updated_data(updated_data)
        self._telemetry_store.async_delay_save(
            self._telemetry_store_payload, 30
        )

    def _process_vehicle_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Process vehicle response to compute derived fields."""
        # Create a copy to avoid modifying the original
        processed = dict(response)
        
        # Apply charging composites (imbalance, balance score)
        self._apply_charging_composites(
            processed,
            {},  # last_signals not available for Fleet API response
            set(),  # received_fields
            set(),  # processed_fields
        )
        
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
        """Derive charge values and compute battery health metrics."""
        charge_state = response.setdefault("charge_state", {})
        
        # Calculate brick voltage imbalance from max/min
        brick_max = charge_state.get("brick_voltage_max")
        brick_min = charge_state.get("brick_voltage_min")
        if isinstance(brick_max, (int, float)) and isinstance(brick_min, (int, float)):
            charge_state["brick_voltage_imbalance"] = brick_max - brick_min

        # Calculate battery balance score (0-100%) - SOC-aware
        # Based on imbalance thresholds that vary by SOC:
        # SOC >= 90%: <=10mV=Excellent(100%), <=20mV=Good(85%), <=30mV=Watch(70%), >30mV=Warning(55%)
        # SOC >= 50%: <=20mV=Excellent(100%), <=30mV=Good(85%), <=50mV=Watch(70%), >50mV=Warning(55%)
        # SOC < 50%:  <=40mV=Excellent(100%), <=80mV=Good(85%), <=120mV=Watch(70%), >120mV=Warning(55%)
        imbalance = charge_state.get("brick_voltage_imbalance")
        soc = charge_state.get("battery_level") or charge_state.get("usable_battery_level")
        if isinstance(imbalance, (int, float)) and isinstance(soc, (int, float)):
            if soc >= 90:
                # Near full charge - tightest thresholds
                if imbalance <= 10:
                    score = 100
                elif imbalance <= 20:
                    score = 85
                elif imbalance <= 30:
                    score = 70
                else:
                    score = 55
            elif soc >= 50:
                # Mid-range SOC
                if imbalance <= 20:
                    score = 100
                elif imbalance <= 30:
                    score = 85
                elif imbalance <= 50:
                    score = 70
                else:
                    score = 55
            else:
                # Low SOC - wider thresholds
                if imbalance <= 40:
                    score = 100
                elif imbalance <= 80:
                    score = 85
                elif imbalance <= 120:
                    score = 70
                else:
                    score = 55
            charge_state["battery_balance_score"] = score

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
        if not self.proxy_manager.is_running:
            raise RuntimeError("Proxy not running")

        await self._ensure_valid_token()

        session = async_get_clientsession(self.hass)
        ssl_context = await self._get_ssl_context()

        cmd = COMMANDS.get(command, command)
        url = f"https://{PROXY_HOST}:{PROXY_PORT}{API_COMMAND.format(vin=vin, command=cmd)}"
        headers = {"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"}

        # Use predefined body or provided body
        if body is None:
            body = COMMAND_BODIES.get(command, {})

        async with session.post(url, headers=headers, json=body, ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 401:
                self._token_expires_at = 0
                await self._ensure_valid_token()
                headers["Authorization"] = f"Bearer {self._access_token}"
                async with session.post(url, headers=headers, json=body, ssl=ssl_context, timeout=aiohttp.ClientTimeout(total=30)) as retry_resp:
                    if retry_resp.status != 200:
                        text = await retry_resp.text()
                        raise RuntimeError(f"Command failed: {retry_resp.status} - {text}")
                    return await retry_resp.json()

            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Command failed: {resp.status} - {text}")

            return await resp.json()

    async def async_wake_up(self, vin: str) -> dict[str, Any]:
        """Wake up the vehicle."""
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
            raise RuntimeError("Proxy not running")

        hostname = self.entry.options.get(CONF_TELEMETRY_HOSTNAME, "").strip()
        port = self.entry.options.get(CONF_TELEMETRY_PORT)
        ca_path = self.proxy_manager.telemetry_ca_path
        if not hostname or not port or not ca_path or not ca_path.is_file():
            raise RuntimeError(
                "Configure a telemetry hostname and restart the integration first"
            )

        await self._ensure_valid_token()
        telemetry_ca = await self.hass.async_add_executor_job(ca_path.read_text)
        session = async_get_clientsession(self.hass)
        ssl_context = await self._get_ssl_context()
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
                    f"Fleet Telemetry configuration failed: {response.status} - {text}"
                )
            return await response.json()

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
