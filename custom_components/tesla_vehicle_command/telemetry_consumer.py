"""Telemetry consumer for Tesla Fleet Telemetry stream."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime
from typing import Any

import zmq
import zmq.asyncio

from homeassistant.core import HomeAssistant

from .const import DOMAIN, TELEMETRY_ZMQ_PORT
from .coordinator import TeslaVehicleCommandCoordinator

_LOGGER = logging.getLogger(__name__)

# Hardcoded addon hostname for the telemetry receiver
TELEMETRY_ADDON_HOSTNAME = "local-tesla-vehicle-command-telemetry"
_BRICK_VOLTAGE_MAX_SIGNAL = "BrickVoltageMax"
_BRICK_VOLTAGE_MIN_SIGNAL = "BrickVoltageMin"
_BRICK_VOLTAGE_MAX_INDEX_SIGNAL = "NumBrickVoltageMax"
_BRICK_VOLTAGE_MIN_INDEX_SIGNAL = "NumBrickVoltageMin"


class TelemetryConsumer:
    """Consumes Fleet Telemetry stream from ZMQ and updates coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TeslaVehicleCommandCoordinator,
    ) -> None:
        """Initialize the telemetry consumer."""
        self.hass = hass
        self.coordinator = coordinator
        self._zmq_endpoint = f"tcp://{TELEMETRY_ADDON_HOSTNAME}:{TELEMETRY_ZMQ_PORT}"
        self._context: zmq.asyncio.Context | None = None
        self._socket: zmq.asyncio.Socket | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_signals_by_vin: dict[str, dict[str, Any]] = {}
        self._last_valid_pack_voltage: float | None = None

    async def async_start(self) -> None:
        """Start consuming telemetry data."""
        if self._running:
            return

        self._context = zmq.asyncio.Context()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.SUBSCRIBE, b"")
        _LOGGER.info("Connecting to Fleet Telemetry ZMQ at %s", self._zmq_endpoint)
        try:
            self._socket.connect(self._zmq_endpoint)
            _LOGGER.info("Successfully connected to Fleet Telemetry ZMQ at %s", self._zmq_endpoint)
        except Exception as err:
            _LOGGER.error("Failed to connect to Fleet Telemetry ZMQ at %s: %s", self._zmq_endpoint, err)
            raise

        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        _LOGGER.info("Started Fleet Telemetry consumer on %s", self._zmq_endpoint)

    async def async_stop(self) -> None:
        """Stop consuming telemetry data."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._socket:
            self._socket.close()
        if self._context:
            self._context.term()

        _LOGGER.info("Stopped Fleet Telemetry consumer")

    async def _consume_loop(self) -> None:
        """Main loop to consume telemetry messages."""
        _LOGGER.info("Telemetry consume loop started on %s", self._zmq_endpoint)
        while self._running:
            try:
                # Receive multipart message: [topic, payload]
                parts = await self._socket.recv_multipart()
                _LOGGER.info("Received telemetry message: topic=%s, parts=%d", parts[0].decode("utf-8", errors="ignore") if parts else "none", len(parts))
                if len(parts) >= 2:
                    topic = parts[0].decode("utf-8", errors="ignore")
                    payload = parts[1]
                    await self._process_message(topic, payload)
            except asyncio.CancelledError:
                _LOGGER.info("Telemetry consume loop cancelled")
                break
            except Exception as err:
                _LOGGER.error("Error in telemetry consume loop: %s", err, exc_info=True)
                await asyncio.sleep(1)

    async def _process_message(self, topic: str, payload: bytes) -> None:
        """Process a single telemetry message."""
        _LOGGER.info("Processing telemetry message: topic=%s, payload_size=%d", topic, len(payload))
        try:
            # The payload is JSON from the fleet-telemetry receiver
            data = json.loads(payload.decode("utf-8"))
            
            # Extract VIN from the message
            vin = data.get("vin")
            if not vin:
                self.coordinator.record_telemetry_receiver_error("missing_vin")
                _LOGGER.warning("Telemetry message missing VIN: %s", topic)
                return

            # Check if this vehicle is managed by us
            vehicle_config = self.coordinator.get_vehicle_config(vin)
            if not vehicle_config:
                self.coordinator.record_telemetry_receiver_error("unmanaged_vin")
                _LOGGER.warning("Telemetry for unmanaged vehicle: %s", vin)
                return

            _LOGGER.info("Processing telemetry for vehicle %s, topic: %s", vin, topic)
            # Process based on topic/type
            await self._update_coordinator_data(vin, topic, data)

        except json.JSONDecodeError as err:
            self.coordinator.record_telemetry_receiver_error("malformed_json")
            _LOGGER.warning("Failed to decode telemetry payload: %s", err)
        except Exception as err:
            self.coordinator.record_telemetry_receiver_error("processing_error")
            _LOGGER.error("Error processing telemetry message: %s", err, exc_info=True)

    async def _update_coordinator_data(
        self, vin: str, topic: str, data: dict[str, Any]
    ) -> None:
        """Update coordinator data with telemetry."""
        # Get current vehicle data
        current_data = self.coordinator.data.get(vin, {})
        response = current_data.get("response", {})

        # Handle different topic formats:
        # - tesla_telemetry_V (repo addon format)
        # - V (local addon format)
        # - connectivity, alerts, errors
        actual_topic = topic
        if topic.startswith("tesla_telemetry_"):
            actual_topic = topic[len("tesla_telemetry_"):]
            _LOGGER.debug("Normalized topic from %s to %s", topic, actual_topic)

        received_fields: set[str] | None = None
        processed_fields: set[str] | None = None

        # Update based on topic
        if actual_topic == "V":
            # Vehicle telemetry data - contains the actual signal values
            _LOGGER.info("Received vehicle telemetry data for %s", vin)
            received_fields, processed_fields = await self._process_vehicle_signals(
                vin, response, data
            )
        elif actual_topic == "connectivity":
            # Connectivity events - vehicle online/offline
            _LOGGER.info("Received connectivity event for %s: %s", vin, data.get("status", "unknown"))
            await self._process_connectivity(vin, response, data)
        elif actual_topic == "alerts":
            # Alerts
            _LOGGER.warning("Telemetry alert for %s: %s", vin, data)
        elif actual_topic == "errors":
            # Errors
            _LOGGER.warning("Telemetry error for %s: %s", vin, data)
        else:
            _LOGGER.debug("Unknown telemetry topic: %s (original: %s)", actual_topic, topic)

        self.coordinator.set_telemetry_data(
            vin,
            response,
            received_fields,
            processed_fields,
            self._last_signals_by_vin.get(vin) if actual_topic == "V" else None,
            self._parse_record_timestamp(data) if actual_topic == "V" else None,
        )

    async def _process_vehicle_signals(
        self, vin: str, response: dict[str, Any], data: dict[str, Any]
    ) -> tuple[set[str], set[str]]:
        """Process vehicle signal data from telemetry."""
        signals = self._decode_signals(data)
        self.coordinator.record_telemetry_signal_values(vin, signals)
        _LOGGER.info(
            "Processing %d telemetry signals for vehicle %s", len(signals), vin
        )
        last_signals = self._last_signals_by_vin.setdefault(
            vin, self.coordinator.get_telemetry_raw_signals(vin)
        )
        last_signals.update(
            {signal_name: value for signal_name, value in signals.items() if value is not None}
        )
        
        # Ensure all state containers exist
        response.setdefault("charge_state", {})
        response.setdefault("climate_state", {})
        response.setdefault("vehicle_state", {})
        response.setdefault("drive_state", {})
        response.setdefault("powertrain", {})
        response.setdefault("vehicle_config", {})
        response.setdefault("gui_settings", {})

        # Map requested Fleet Telemetry signals to the Fleet API state structure.
        signal_mapping = {
            # Battery/Charging
            "Soc": (
                ("charge_state", "usable_battery_level", self._to_soc),
                ("charge_state", "battery_level", self._to_soc),
            ),
            "RatedRange": (("charge_state", "battery_range", self._to_float),),
            "EstBatteryRange": (("charge_state", "est_battery_range", self._to_float),),
            "IdealBatteryRange": (("charge_state", "ideal_battery_range", self._to_float),),
            "DetailedChargeState": (("charge_state", "charging_state", self._charge_state),),
            "ChargeLimitSoc": (("charge_state", "charge_limit_soc", self._to_int),),
            "TimeToFullCharge": (("charge_state", "time_to_full_charge", self._to_float),),
            "ChargerVoltage": (("charge_state", "charger_voltage", self._charger_voltage),),
            "ChargeAmps": (
                ("charge_state", "charger_actual_current", self._to_int),
            ),
            "ChargePortDoorOpen": (("charge_state", "charge_port_door_open", self._is_truthy),),
            "ACChargingPower": (("charge_state", "charger_power", self._to_float),),
            "DCChargingPower": (("charge_state", "charger_power", self._to_float),),
            "ACChargingEnergyIn": (("charge_state", "charge_energy_added", self._to_float),),
            "DCChargingEnergyIn": (("charge_state", "charge_energy_added", self._to_float),),
            "ChargeCurrentRequest": (("charge_state", "charge_current_request", self._to_int),),
            "ChargeCurrentRequestMax": (("charge_state", "charge_current_request_max", self._to_int),),
            "ChargeEnableRequest": (("charge_state", "charge_enable_request", self._is_truthy),),
            "ChargePortLatch": (("charge_state", "charge_port_latch", self._charge_port_latch),),
            "ChargeRateMilePerHour": (("charge_state", "charge_rate", self._to_float),),
            "ChargerPhases": (("charge_state", "charger_phases", self._to_int),),
            "ChargingCableType": (("charge_state", "conn_charge_cable", self._charging_cable_type),),
            "FastChargerPresent": (("charge_state", "fast_charger_present", self._is_truthy),),
            "FastChargerType": (("charge_state", "fast_charger_type", self._fast_charger_type),),
            "NotEnoughPowerToHeat": (("charge_state", "not_enough_power_to_heat", self._is_truthy),),
            "ScheduledChargingMode": (("charge_state", "scheduled_charging_mode", self._scheduled_charging_mode),),
            "ScheduledChargingPending": (("charge_state", "scheduled_charging_pending", self._is_truthy),),
            "ScheduledChargingStartTime": (("charge_state", "scheduled_charging_start_time", None),),
            "SuperchargerSessionTripPlanner": (("charge_state", "supercharger_session_trip_planner", self._is_truthy),),
            "ChargeState": (("charge_state", "charging_state", self._charge_state),),
            "BMSState": (("charge_state", "bms_state", None),),
            "BatteryHeaterOn": (("climate_state", "battery_heater_on", self._is_truthy),),
            "EnergyRemaining": (("charge_state", "energy_remaining", self._to_float),),
            "EstimatedHoursToChargeTermination": (("charge_state", "estimated_hours_to_charge_termination", self._to_float),),
            "LifetimeEnergyUsed": (("charge_state", "lifetime_energy_used", self._to_float),),
            "PackCurrent": (("charge_state", "pack_current", self._to_float),),
            "PackVoltage": (("charge_state", "pack_voltage", self._pack_voltage),),
            "IsolationResistance": (("charge_state", "isolation_resistance", self._to_float),),
            "ModuleTempMax": (("charge_state", "module_temp_max", self._to_float),),
            "ModuleTempMin": (("charge_state", "module_temp_min", self._to_float),),
            "NumModuleTempMax": (("charge_state", "num_module_temp_max", self._to_int),),
            "NumModuleTempMin": (("charge_state", "num_module_temp_min", self._to_int),),
            "DCDCEnable": (("charge_state", "dcdc_enable", self._is_truthy),),
            "PowershareHoursLeft": (("charge_state", "powershare_hours_left", self._to_int),),
            "PowershareInstantaneousPowerKW": (("charge_state", "powershare_instantaneous_power_kw", self._to_float),),
            "PowershareStatus": (("charge_state", "powershare_status", None),),
            "PowershareStopReason": (("charge_state", "powershare_stop_reason", None),),
            "PowershareType": (("charge_state", "powershare_type", None),),
            "PreconditioningEnabled": (
                ("charge_state", "preconditioning_enabled", self._is_truthy),
                ("climate_state", "is_preconditioning", self._is_truthy),
            ),

            # Climate
            "InsideTemp": (("climate_state", "inside_temp", None),),
            "OutsideTemp": (("climate_state", "outside_temp", None),),
            "HvacLeftTemperatureRequest": (("climate_state", "driver_temp_setting", None),),
            "HvacRightTemperatureRequest": (("climate_state", "passenger_temp_setting", None),),
            "HvacFanStatus": (("climate_state", "fan_status", None),),
            "HvacPower": (("climate_state", "is_climate_on", self._is_truthy),),
            "ClimateKeeperMode": (("climate_state", "climate_keeper_mode", self._climate_keeper_mode),),
            "DefrostMode": (("climate_state", "defrost_mode", self._defrost_mode),),
            "SeatHeaterLeft": (("climate_state", "seat_heater_left", self._to_int),),
            "SeatHeaterRight": (("climate_state", "seat_heater_right", self._to_int),),
            "SeatHeaterRearLeft": (("climate_state", "seat_heater_rear_left", self._to_int),),
            "SeatHeaterRearRight": (("climate_state", "seat_heater_rear_right", self._to_int),),
            "SeatHeaterRearCenter": (("climate_state", "seat_heater_rear_center", self._to_int),),
            "HvacSteeringWheelHeatLevel": (
                ("climate_state", "steering_wheel_heat_level", self._to_int),
                ("climate_state", "steering_wheel_heater", self._is_heat_active),
            ),
            "ClimateSeatCoolingFrontLeft": (
                ("climate_state", "seat_fan_front_left", self._to_int),
                ("climate_state", "seat_cooler_left", self._to_int),
            ),
            "ClimateSeatCoolingFrontRight": (
                ("climate_state", "seat_fan_front_right", self._to_int),
                ("climate_state", "seat_cooler_right", self._to_int),
            ),
            "HvacACEnabled": (("climate_state", "hvac_ac_enabled", self._is_truthy),),
            "HvacAutoMode": (("climate_state", "hvac_auto_request", None),),
            "HvacFanSpeed": (("climate_state", "hvac_fan_speed", self._to_int),),
            "HvacSteeringWheelHeatAuto": (("climate_state", "auto_steering_wheel_heat", self._is_truthy),),
            "RearDefrostEnabled": (("climate_state", "is_rear_defroster_on", self._is_truthy),),
            "RearDisplayHvacEnabled": (("climate_state", "rear_display_hvac_enabled", self._is_truthy),),
            "WiperHeatEnabled": (("climate_state", "wiper_blade_heater", self._is_truthy),),
            "AutoSeatClimateLeft": (("climate_state", "auto_seat_climate_left", self._is_truthy),),
            "AutoSeatClimateRight": (("climate_state", "auto_seat_climate_right", self._is_truthy),),
            "CabinOverheatProtectionMode": (("climate_state", "cabin_overheat_protection", self._cabin_overheat_protection),),
            "CabinOverheatProtectionTemperatureLimit": (("climate_state", "cop_activation_temperature", self._cop_activation_temp),),
            "DefrostForPreconditioning": (("climate_state", "defrost_for_preconditioning", self._is_truthy),),
            
            # Vehicle State
            "Locked": (("vehicle_state", "locked", self._is_truthy),),
            "SentryMode": (("vehicle_state", "sentry_mode", self._is_sentry_active),),
            "DriverSeatOccupied": (("vehicle_state", "is_user_present", self._is_truthy),),
            "FdWindow": (("vehicle_state", "fd_window", self._window_position),),
            "FpWindow": (("vehicle_state", "fp_window", self._window_position),),
            "RdWindow": (("vehicle_state", "rd_window", self._window_position),),
            "RpWindow": (("vehicle_state", "rp_window", self._window_position),),
            "TpmsPressureFl": (("vehicle_state", "tpms_pressure_fl", None),),
            "TpmsPressureFr": (("vehicle_state", "tpms_pressure_fr", None),),
            "TpmsPressureRl": (("vehicle_state", "tpms_pressure_rl", None),),
            "TpmsPressureRr": (("vehicle_state", "tpms_pressure_rr", None),),
            "DoorState": (("vehicle_state", "door_state", None),),
            "DriverSeatBelt": (("vehicle_state", "driver_seat_belt", self._is_truthy),),
            "PassengerSeatBelt": (("vehicle_state", "passenger_seat_belt", None),),
            "GuestModeEnabled": (("vehicle_state", "guest_mode_enabled", self._is_truthy),),
            "GuestModeMobileAccessState": (("vehicle_state", "guest_mode_mobile_access_state", None),),
            "HomelinkDeviceCount": (("vehicle_state", "homelink_device_count", self._to_int),),
            "HomelinkNearby": (("vehicle_state", "homelink_nearby", self._is_truthy),),
            "PinToDriveEnabled": (("vehicle_state", "pin_to_drive_enabled", self._is_truthy),),
            "RemoteStartEnabled": (("vehicle_state", "remote_start_enabled", self._is_truthy),),
            "ServiceMode": (("vehicle_state", "service_mode", self._is_truthy),),
            "SpeedLimitMode": (("vehicle_state", "speed_limit_mode", self._is_truthy),),
            "ValetModeEnabled": (("vehicle_state", "valet_mode", self._is_truthy),),
            "VehicleName": (("vehicle_state", "vehicle_name", None),),
            "CenterDisplay": (("vehicle_state", "center_display_state", None),),
            "LightsHazardsActive": (("vehicle_state", "lights_hazards_active", self._is_truthy),),
            "LightsHighBeams": (("vehicle_state", "lights_high_beams", self._is_truthy),),
            "LightsTurnSignal": (("vehicle_state", "lights_turn_signal", None),),
            "TonneauOpenPercent": (("vehicle_state", "tonneau_open_percent", None),),
            "TonneauPosition": (("vehicle_state", "tonneau_position", None),),
            "TonneauTentMode": (("vehicle_state", "tonneau_tent_mode", None),),
            "CarType": (("vehicle_config", "car_type", self._car_type),),
            "EfficiencyPackage": (("vehicle_config", "efficiency_package", None),),
            "EuropeVehicle": (("vehicle_config", "eu_vehicle", self._is_truthy),),
            "ExteriorColor": (("vehicle_config", "exterior_color", None),),
            "OffroadLightbarPresent": (("vehicle_config", "offroad_lightbar_present", self._is_truthy),),
            "RearSeatHeaters": (("vehicle_config", "rear_seat_heaters", None),),
            "RightHandDrive": (("vehicle_config", "rhd", self._is_truthy),),
            "RoofColor": (("vehicle_config", "roof_color", None),),
            "SunroofInstalled": (("vehicle_config", "sun_roof_installed", self._presence),),
            "Trim": (("vehicle_config", "trim_badging", None),),
            "WheelType": (("vehicle_config", "wheel_type", None),),
            "ChargePort": (("vehicle_config", "charge_port_type", None),),
            
            # Drive State
            "Gear": (("drive_state", "shift_state", self._shift_state),),
            "VehicleSpeed": (("drive_state", "speed", self._to_int),),
            "GpsHeading": (("drive_state", "heading", self._to_int),),
            "GpsState": (("drive_state", "gps_state", self._is_truthy),),
            "Odometer": (("vehicle_state", "odometer", None),),
            "BrakePedal": (("drive_state", "brake_pedal", self._is_truthy),),
            "BrakePedalPos": (("drive_state", "brake_pedal_pos", None),),
            "PedalPosition": (("drive_state", "pedal_position", None),),
            "CruiseSetSpeed": (("drive_state", "cruise_set_speed", None),),
            "CruiseFollowDistance": (("drive_state", "cruise_follow_distance", None),),
            "DriveRail": (("drive_state", "drive_rail", self._is_truthy),),
            "LongitudinalAcceleration": (("drive_state", "longitudinal_acceleration", None),),
            "LateralAcceleration": (("drive_state", "lateral_acceleration", None),),
            "MilesSinceReset": (("drive_state", "miles_since_reset", None),),
            "SelfDrivingMilesSinceReset": (("drive_state", "self_driving_miles_since_reset", None),),
            "SpeedLimitWarning": (("drive_state", "speed_limit_warning", None),),
            "ForwardCollisionWarning": (("drive_state", "forward_collision_warning", None),),
            "LaneDepartureAvoidance": (("drive_state", "lane_departure_avoidance", None),),
            "EmergencyLaneDepartureAvoidance": (("drive_state", "emergency_lane_departure_avoidance", None),),
            "AutomaticBlindSpotCamera": (("drive_state", "automatic_blind_spot_camera", self._is_truthy),),
            "BlindSpotCollisionWarningChime": (("drive_state", "blind_spot_collision_warning_chime", self._is_truthy),),
            "AutomaticEmergencyBrakingOff": (("drive_state", "automatic_emergency_braking_off", self._is_truthy),),
            
            # Location/Navigation
            "Location": (("drive_state", "latitude", None), ("drive_state", "longitude", None)),
            "DestinationLocation": (("drive_state", "active_route_latitude", None), ("drive_state", "active_route_longitude", None)),
            "DestinationName": (("drive_state", "active_route_destination", None),),
            "OriginLocation": (("drive_state", "origin_latitude", None), ("drive_state", "origin_longitude", None)),
            "RouteLine": (("drive_state", "route_line", None),),
            "RouteTrafficMinutesDelay": (("drive_state", "active_route_traffic_minutes_delay", None),),
            "RouteLastUpdated": (("drive_state", "route_last_updated", None),),
            "MilesToArrival": (("drive_state", "active_route_miles_to_arrival", None),),
            "MinutesToArrival": (("drive_state", "active_route_minutes_to_arrival", None),),
            "ExpectedEnergyPercentAtTripArrival": (("drive_state", "active_route_energy_at_arrival", self._to_int),),
            "LocatedAtFavorite": (("drive_state", "located_at_favorite", self._is_truthy),),
            "LocatedAtHome": (("drive_state", "located_at_home", self._is_truthy),),
            "LocatedAtWork": (("drive_state", "located_at_work", self._is_truthy),),
            
            # Powertrain
            "DiStateF": (("powertrain", "di_state_f", None),),
            "DiStateR": (("powertrain", "di_state_r", None),),
            "DiStateREL": (("powertrain", "di_state_rel", None),),
            "DiStateRER": (("powertrain", "di_state_rer", None),),
            "DiAxleSpeedF": (("powertrain", "di_axle_speed_f", self._axle_speed_to_kmh),),
            "DiAxleSpeedR": (("powertrain", "di_axle_speed_r", self._axle_speed_to_kmh),),
            "DiAxleSpeedREL": (("powertrain", "di_axle_speed_rel", self._axle_speed_to_kmh),),
            "DiAxleSpeedRER": (("powertrain", "di_axle_speed_rer", self._axle_speed_to_kmh),),
            "DiMotorCurrentF": (("powertrain", "di_motor_current_f", None),),
            "DiMotorCurrentR": (("powertrain", "di_motor_current_r", None),),
            "DiMotorCurrentREL": (("powertrain", "di_motor_current_rel", None),),
            "DiMotorCurrentRER": (("powertrain", "di_motor_current_rer", None),),
            "DiTorqueActualF": (("powertrain", "di_torque_actual_f", None),),
            "DiTorqueActualR": (("powertrain", "di_torque_actual_r", None),),
            "DiTorqueActualREL": (("powertrain", "di_torque_actual_rel", None),),
            "DiTorqueActualRER": (("powertrain", "di_torque_actual_rer", None),),
            "DiTorquemotor": (("powertrain", "di_torquemotor", None),),
            "DiSlaveTorqueCmd": (("powertrain", "di_slave_torque_cmd", None),),
            "DiInverterTF": (("powertrain", "di_inverter_tf", None),),
            "DiInverterTR": (("powertrain", "di_inverter_tr", None),),
            "DiInverterTREL": (("powertrain", "di_inverter_trel", None),),
            "DiInverterTRER": (("powertrain", "di_inverter_trer", None),),
            "DiHeatsinkTF": (("powertrain", "di_heatsink_tf", None),),
            "DiHeatsinkTR": (("powertrain", "di_heatsink_tr", None),),
            "DiHeatsinkTREL": (("powertrain", "di_heatsink_trel", None),),
            "DiHeatsinkTRER": (("powertrain", "di_heatsink_trer", None),),
            "DiStatorTempF": (("powertrain", "di_stator_temp_f", None),),
            "DiStatorTempR": (("powertrain", "di_stator_temp_r", None),),
            "DiStatorTempREL": (("powertrain", "di_stator_temp_rel", None),),
            "DiStatorTempRER": (("powertrain", "di_stator_temp_rer", None),),
            "DiVBatF": (("powertrain", "di_vbat_f", None),),
            "DiVBatR": (("powertrain", "di_vbat_r", None),),
            "DiVBatREL": (("powertrain", "di_vbat_rel", None),),
            "DiVBatRER": (("powertrain", "di_vbat_rer", None),),
            "Hvil": (("powertrain", "hvil_status", self._hvil_status),),
            
            # Safety/Service
            "TpmsHardWarnings": (("vehicle_state", "tpms_hard_warning_fl", None), ("vehicle_state", "tpms_hard_warning_fr", None), ("vehicle_state", "tpms_hard_warning_rl", None), ("vehicle_state", "tpms_hard_warning_rr", None)),
            "TpmsSoftWarnings": (("vehicle_state", "tpms_soft_warning_fl", None), ("vehicle_state", "tpms_soft_warning_fr", None), ("vehicle_state", "tpms_soft_warning_rl", None), ("vehicle_state", "tpms_soft_warning_rr", None)),
            "TpmsLastSeenPressureTimeFl": (("vehicle_state", "tpms_last_seen_pressure_time_fl", None),),
            "TpmsLastSeenPressureTimeFr": (("vehicle_state", "tpms_last_seen_pressure_time_fr", None),),
            "TpmsLastSeenPressureTimeRl": (("vehicle_state", "tpms_last_seen_pressure_time_rl", None),),
            "TpmsLastSeenPressureTimeRr": (("vehicle_state", "tpms_last_seen_pressure_time_rr", None),),
            
            # User Preferences
            "Setting24HourTime": (("gui_settings", "gui_24_hour_time", self._is_truthy),),
            "SettingChargeUnit": (("gui_settings", "gui_charge_rate_units", None),),
            "SettingDistanceUnit": (("gui_settings", "gui_distance_units", None),),
            "SettingTemperatureUnit": (("gui_settings", "gui_temperature_units", None),),
            "SettingTirePressureUnit": (("gui_settings", "gui_tirepressure_units", None),),
            
            # Version
            "Version": (("vehicle_state", "car_version", None),),
            "SoftwareUpdateDownloadPercentComplete": (("vehicle_state", "software_update.download_perc", self._to_int),),
            "SoftwareUpdateExpectedDurationMinutes": (("vehicle_state", "software_update.expected_duration_sec", self._to_int),),
            "SoftwareUpdateInstallationPercentComplete": (("vehicle_state", "software_update.install_perc", self._to_int),),
            "SoftwareUpdateScheduledStartTime": (("vehicle_state", "software_update.scheduled_time_ms", None),),
            "SoftwareUpdateVersion": (("vehicle_state", "software_update.version", None),),
        }

        processed_fields: set[str] = set()
        for signal_name, targets in signal_mapping.items():
            if signal_name in signals:
                value = signals[signal_name]
                if value is None:
                    continue
                updated_state = False
                for state_category, state_key, transform in targets:
                    converted_value = transform(value) if transform else value
                    if converted_value is not None:
                        response[state_category][state_key] = converted_value
                        updated_state = True
                if updated_state:
                    processed_fields.add(signal_name)

        received_fields = set(signals)
        record_time = self._parse_record_timestamp(data)
        # Window boundaries only need an occasional, precisely-paired sample
        # (see coordinator.record_battery_charge_state), so live samples
        # require Soc and EnergyRemaining in the exact same frame rather than
        # tolerating a cross-frame pairing that could offset the two values
        # in time relative to each other.
        if "Soc" in processed_fields and "EnergyRemaining" in processed_fields:
            charge_state = response["charge_state"]
            soc_percent = self._to_float(signals.get("Soc"))
            energy_remaining_kwh = self._to_float(signals.get("EnergyRemaining"))
            if soc_percent is not None and energy_remaining_kwh is not None:
                module_temps = [
                    temp
                    for temp in (
                        self._to_float(signals.get("ModuleTempMax")),
                        self._to_float(signals.get("ModuleTempMin")),
                    )
                    if temp is not None
                ]
                temperature_c = (
                    sum(module_temps) / len(module_temps) if module_temps else None
                )
                charge_state.update(
                    self.coordinator.record_battery_capacity_sample(
                        vin,
                        soc_percent,
                        energy_remaining_kwh,
                        observed_at=record_time,
                        source="live",
                        temperature_c=temperature_c,
                    )
                )
        charge_state = response["charge_state"]
        charge_state.update(
            self.coordinator.record_battery_charge_state(
                vin, charge_state.get("charging_state"), observed_at=record_time
            )
        )
        self._apply_charging_composites(
            vin,
            response,
            last_signals,
            signals,
            received_fields,
            processed_fields,
        )
        self._apply_door_state(response, signals, processed_fields)

        location = signals.get("Location")
        if isinstance(location, dict):
            drive_state = response["drive_state"]
            drive_state["latitude"] = location.get("latitude")
            drive_state["longitude"] = location.get("longitude")
            processed_fields.add("Location")

        _LOGGER.debug("Updated telemetry data for %s: %d signals", vin, len(signals))
        return received_fields, processed_fields

    @staticmethod
    def _decode_signals(data: dict[str, Any]) -> dict[str, Any]:
        """Decode the official Fleet Telemetry typed ``data`` record format."""
        signals: dict[str, Any] = {}
        for item in data.get("data", []):
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if isinstance(key, str):
                signals[key] = TelemetryConsumer._unwrap_value(item.get("value"))
        return signals

    @staticmethod
    def _parse_record_timestamp(data: dict[str, Any]) -> datetime | None:
        """Return the record's capture time, if it is timezone-aware."""
        raw = data.get("createdAt")
        if not isinstance(raw, str) or not raw.strip():
            return None
        text = raw.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    @staticmethod
    def _unwrap_value(value: Any) -> Any:
        """Return a value from Tesla Fleet Telemetry's typed protobuf JSON."""
        if not isinstance(value, dict):
            return value
        if "invalid" in value:
            return None
        if "locationValue" in value:
            return value["locationValue"]
        for key in (
            "doubleValue",
            "floatValue",
            "intValue",
            "longValue",
            "stringValue",
            "booleanValue",
            "boolValue",
        ):
            if key in value:
                return value[key]
        for key, typed_value in value.items():
            if key.endswith("Value"):
                return typed_value
        return value

    async def _process_connectivity(
        self, vin: str, response: dict[str, Any], data: dict[str, Any]
    ) -> None:
        """Process connectivity events."""
        # Connectivity events indicate vehicle online/offline/sleeping
        status = self._connectivity_status(data.get("status"))
        response.setdefault("vehicle_state", {})
        response["vehicle_state"]["connectivity_status"] = status
        self.coordinator.set_connectivity_status(vin, status)

        if status == "CONNECTED":
            _LOGGER.info("Vehicle %s connected via telemetry", vin)

    @classmethod
    def _connectivity_status(cls, value: Any) -> str:
        """Normalize Fleet Telemetry connectivity enum representations."""
        value = cls._unwrap_value(value)
        if isinstance(value, bool):
            return "UNKNOWN"
        if isinstance(value, (int, float)) and float(value).is_integer():
            return {0: "UNKNOWN", 1: "CONNECTED", 2: "DISCONNECTED"}.get(
                int(value), "UNKNOWN"
            )

        normalized = str(value or "").strip().upper().replace("-", "_")
        aliases = {
            "CONNECTED": "CONNECTED",
            "ONLINE": "CONNECTED",
            "CONNECTIVITY_EVENT_CONNECTED": "CONNECTED",
            "CONNECTIVITYEVENTCONNECTED": "CONNECTED",
            "CONNECTIVITY_STATE_CONNECTED": "CONNECTED",
            "CONNECTIVITYSTATECONNECTED": "CONNECTED",
            "VEHICLE_CONNECTIVITY_CONNECTED": "CONNECTED",
            "VEHICLE_CONNECTIVITY_STATE_CONNECTED": "CONNECTED",
            "VEHICLECONNECTIVITYSTATECONNECTED": "CONNECTED",
            "DISCONNECTED": "DISCONNECTED",
            "OFFLINE": "DISCONNECTED",
            "CONNECTIVITY_EVENT_DISCONNECTED": "DISCONNECTED",
            "CONNECTIVITYEVENTDISCONNECTED": "DISCONNECTED",
            "CONNECTIVITY_STATE_DISCONNECTED": "DISCONNECTED",
            "CONNECTIVITYSTATEDISCONNECTED": "DISCONNECTED",
            "VEHICLE_CONNECTIVITY_DISCONNECTED": "DISCONNECTED",
            "VEHICLE_CONNECTIVITY_STATE_DISCONNECTED": "DISCONNECTED",
            "VEHICLECONNECTIVITYSTATEDISCONNECTED": "DISCONNECTED",
        }
        return aliases.get(
            normalized, aliases.get(normalized.replace("_", ""), "UNKNOWN")
        )

    def _apply_charging_composites(
        self,
        vin: str,
        response: dict[str, Any],
        last_signals: dict[str, Any],
        signals: dict[str, Any],
        received_fields: set[str],
        processed_fields: set[str],
    ) -> None:
        """Derive charge values delivered as AC/DC-specific telemetry fields."""
        charge_state = response["charge_state"]
        powers = [
            value
            for key in ("ACChargingPower", "DCChargingPower")
            if isinstance(value := last_signals.get(key), (int, float))
        ]
        if powers:
            charge_state["charger_power"] = max(powers)
        processed_fields.update(
            {"ACChargingPower", "DCChargingPower"} & received_fields
        )

        is_dc_charging = (
            isinstance(last_signals.get("DCChargingPower"), (int, float))
            and last_signals["DCChargingPower"] > 0
        )
        energy_key = "DCChargingEnergyIn" if is_dc_charging else "ACChargingEnergyIn"
        energy = last_signals.get(energy_key)
        if isinstance(energy, (int, float)):
            charge_state["charge_energy_added"] = energy
        processed_fields.update(
            {"ACChargingEnergyIn", "DCChargingEnergyIn"} & received_fields
        )

        # A decoded record has no per-signal correlation ID. Update extrema and
        # imbalance only when both extrema occur in that same record.
        brick_max: float | None = None
        brick_min: float | None = None
        if _BRICK_VOLTAGE_MAX_SIGNAL in received_fields:
            brick_max = self._to_millivolts(signals.get(_BRICK_VOLTAGE_MAX_SIGNAL))
            if not isinstance(brick_max, (int, float)):
                last_signals.pop(_BRICK_VOLTAGE_MAX_SIGNAL, None)
        if _BRICK_VOLTAGE_MIN_SIGNAL in received_fields:
            brick_min = self._to_millivolts(signals.get(_BRICK_VOLTAGE_MIN_SIGNAL))
            if not isinstance(brick_min, (int, float)):
                last_signals.pop(_BRICK_VOLTAGE_MIN_SIGNAL, None)

        has_brick_snapshot = {
            _BRICK_VOLTAGE_MAX_SIGNAL,
            _BRICK_VOLTAGE_MIN_SIGNAL,
            _BRICK_VOLTAGE_MAX_INDEX_SIGNAL,
            _BRICK_VOLTAGE_MIN_INDEX_SIGNAL,
        }.issubset(received_fields)
        brick_max_index = self._to_int(signals.get(_BRICK_VOLTAGE_MAX_INDEX_SIGNAL))
        brick_min_index = self._to_int(signals.get(_BRICK_VOLTAGE_MIN_INDEX_SIGNAL))
        pair_imbalance: float | None = None
        if (
            has_brick_snapshot
            and isinstance(brick_max, (int, float))
            and isinstance(brick_min, (int, float))
            and brick_max >= brick_min
            and brick_max_index is not None
            and brick_min_index is not None
        ):
            candidate_imbalance = brick_max - brick_min
            if math.isfinite(candidate_imbalance):
                pair_imbalance = candidate_imbalance

        if isinstance(pair_imbalance, (int, float)):
            charge_state["brick_voltage_max"] = brick_max
            charge_state["brick_voltage_min"] = brick_min
            charge_state["brick_voltage_imbalance"] = pair_imbalance
            charge_state["num_brick_voltage_max"] = brick_max_index
            charge_state["num_brick_voltage_min"] = brick_min_index

    @classmethod
    def _apply_door_state(
        cls,
        response: dict[str, Any],
        signals: dict[str, Any],
        processed_fields: set[str],
    ) -> None:
        """Expand the composite DoorState signal into Fleet API door fields."""
        doors = signals.get("DoorState")
        if not isinstance(doors, dict):
            return
        door_mapping = {
            "DriverFront": "df",
            "DriverRear": "dr",
            "PassengerFront": "pf",
            "PassengerRear": "pr",
            "TrunkFront": "ft",
            "TrunkRear": "rt",
        }
        vehicle_state = response["vehicle_state"]
        for telemetry_key, state_key in door_mapping.items():
            if telemetry_key in doors:
                # Convert 0/1 to "Closed"/"Open" for ENUM sensors
                vehicle_state[state_key] = "Open" if cls._is_truthy(doors[telemetry_key]) else "Closed"
                processed_fields.add(f"DoorState.{telemetry_key}")
        processed_fields.add("DoorState")

    @staticmethod
    def _to_int(value: Any) -> int | None:
        """Convert telemetry numeric values to Fleet API integer fields."""
        if isinstance(value, bool):
            return None
        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(numeric_value):
            return None
        return int(round(numeric_value))

    @classmethod
    def _to_soc(cls, value: Any) -> int | None:
        """Convert a telemetry state of charge only when it is in range."""
        if isinstance(value, bool):
            return None
        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(numeric_value) or not 0 <= numeric_value <= 100:
            return None
        return int(round(numeric_value))

    @staticmethod
    def _is_finite_number(value: Any) -> bool:
        """Return whether a value is a finite non-boolean number."""
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        """Convert telemetry numeric values to float fields."""
        if isinstance(value, bool):
            return None
        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return numeric_value if math.isfinite(numeric_value) else None

    @staticmethod
    def _axle_speed_to_kmh(value: Any) -> float | None:
        """Convert axle speed from RPM to km/h.
        
        Telemetry provides axle speed in RPM. Convert to km/h using typical
        tire circumference. Formula: km/h = RPM * circumference_m * 60 / 1000
        Using ~2.1m tire circumference for typical Tesla tires.
        """
        try:
            rpm = float(value)
            # Typical Tesla tire circumference ~2.1m
            return round(rpm * 2.1 * 60 / 1000, 1)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_millivolts(value: Any) -> float | None:
        """Convert telemetry voltage values from volts to millivolts."""
        if isinstance(value, bool):
            return None
        try:
            volts = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        millivolts = volts * 1000
        return millivolts if math.isfinite(millivolts) else None

    @staticmethod
    def _enum_tail(value: Any) -> str:
        """Strip the protobuf enum field prefix from a telemetry value."""
        text = str(value)
        marker = text.rfind("State")
        return text[marker + len("State") :] if marker >= 0 else text

    @classmethod
    def _shift_state(cls, value: Any) -> str | None:
        """Normalize a shift-state enum to Tesla's single-letter API form."""
        token = cls._enum_tail(value).strip().upper()
        if not token:
            return None

        # Accept only explicit gear tokens to avoid mapping values like
        # "NA"/"NotAvailable" to Neutral by first-letter matching.
        direct_map = {
            "P": "P",
            "PARK": "P",
            "PARKING": "P",
            "D": "D",
            "DRIVE": "D",
            "DRIVING": "D",
            "R": "R",
            "REVERSE": "R",
            "N": "N",
            "NEUTRAL": "N",
        }
        return direct_map.get(token)

    @classmethod
    def _charge_state(cls, value: Any) -> str:
        """Normalize a detailed charging-state enum."""
        return cls._enum_tail(value).strip() or "Disconnected"

    def _climate_keeper_mode(self, value: Any) -> str:
        """Normalize a climate-keeper enum."""
        text = str(value)
        # Handle "ClimateKeeperModeStateOff" -> "off", "ClimateKeeperModeStateDog" -> "dog", etc.
        if text.startswith("ClimateKeeperMode"):
            text = text[len("ClimateKeeperMode"):]
        # Also handle "State" suffix if present
        marker = text.rfind("State")
        if marker >= 0:
            text = text[marker + len("State"):]
        return text.strip().lower() or "off"

    def _scheduled_charging_mode(self, value: Any) -> str:
        """Normalize a scheduled charging mode enum."""
        text = str(value)
        # Handle "ScheduledChargingModeOff" -> "off", "ScheduledChargingModeStartAt" -> "start_at", etc.
        if text.startswith("ScheduledChargingMode"):
            text = text[len("ScheduledChargingMode"):]
        # Also handle "State" suffix if present
        marker = text.rfind("State")
        if marker >= 0:
            text = text[marker + len("State"):]
        return text.strip().lower() or "off"

    def _defrost_mode(self, value: Any) -> str:
        """Normalize a defrost-mode enum."""
        text = str(value)
        # Handle "DefrostModeStateOff" -> "Off", "DefrostModeStateNormal" -> "Normal", etc.
        if text.startswith("DefrostMode"):
            text = text[len("DefrostMode"):]
        # Also handle "State" suffix if present
        marker = text.rfind("State")
        if marker >= 0:
            text = text[marker + len("State"):]
        return text.strip() or "Unknown"

    def _cabin_overheat_protection(self, value: Any) -> str:
        """Normalize a cabin overheat protection mode enum."""
        text = str(value)
        # Handle "CabinOverheatProtectionModeStateOff" -> "Off", "CabinOverheatProtectionModeStateOn" -> "On", etc.
        if text.startswith("CabinOverheatProtectionMode"):
            text = text[len("CabinOverheatProtectionMode"):]
        # Also handle "State" suffix if present
        marker = text.rfind("State")
        if marker >= 0:
            text = text[marker + len("State"):]
        return text.strip() or "Off"

    def _cop_activation_temp(self, value: Any) -> str:
        """Normalize a COP activation temperature enum."""
        text = str(value)
        # Handle "ClimateOverheatProtectionTempLimitLow" -> "Low", etc.
        if text.startswith("ClimateOverheatProtectionTempLimit"):
            text = text[len("ClimateOverheatProtectionTempLimit"):]
        return text.strip() or "Low"

    def _charging_cable_type(self, value: Any) -> str:
        """Normalize a charging cable type enum."""
        if value is None:
            return "None"

        normalized = str(value).strip().replace("-", "_").upper()
        for prefix in (
            "CHARGING_CABLE_TYPE_STATE_",
            "CHARGING_CABLE_TYPE_",
            "CHARGINGCABLETYPESTATE",
            "CHARGINGCABLETYPE",
            "CABLE_TYPE_STATE_",
            "CABLE_TYPE_",
            "CABLETYPESTATE",
            "CABLETYPE",
        ):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break

        aliases = {
            "GBAC": "GB_AC",
            "GBDC": "GB_DC",
            "IEC": "IEC",
            "SAE": "SAE",
            "SNA": "SNA",
            "NONE": "None",
            "<INVALID>": "None",
            "INVALID": "None",
            "UNKNOWN": "Unknown",
        }
        return aliases.get(normalized, normalized or "Unknown")

    @staticmethod
    def _charge_port_latch(value: Any) -> str:
        """Normalize a charge-port latch enum."""
        text = str(value).strip()
        for prefix in ("ChargePortLatchState", "ChargePortLatch"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return text if text in {"Engaged", "Disengaged"} else "Unknown"

    @staticmethod
    def _car_type(value: Any) -> str | None:
        """Remove the redundant protobuf prefix from a vehicle model."""
        if value is None:
            return None
        text = str(value).strip()
        if text.startswith("CarType"):
            text = text[len("CarType"):]
        return text or None

    @staticmethod
    def _hvil_status(value: Any) -> str:
        """Normalize the high-voltage interlock status enum."""
        text = str(value).strip()
        for prefix in ("HvilStatus", "HvilState", "Hvil"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return text or "Unknown"

    @classmethod
    def _presence(cls, value: Any) -> str:
        """Normalize a hardware-presence signal."""
        if value is None:
            return "Unknown"
        return "Present" if cls._is_truthy(value) else "Not Present"

    def _fast_charger_type(self, value: Any) -> str:
        """Normalize a fast charger type enum."""
        text = str(value)
        # Handle "FastChargerTypeSupercharger" -> "Supercharger", etc.
        if text.startswith("FastChargerType"):
            text = text[len("FastChargerType"):]
        return text.strip() or "Unknown"

    def _pack_voltage(self, value: Any) -> float | None:
        """Filter out transient low-voltage readings during vehicle wake-up.

        During wake-up, Fleet Telemetry emits transient PackVoltage values
        (4–13 V) while BMSState is Standby. After BMS transitions to active,
        PackVoltage updates to the expected HV battery voltage (~390 V).
        This method ignores values <= 200 V and returns the last valid reading.
        """
        try:
            voltage = float(value)
        except (TypeError, ValueError):
            return None

        if voltage > 200:
            self._last_valid_pack_voltage = voltage
            return voltage

        # Return last valid voltage if available, otherwise None to ignore transient
        return self._last_valid_pack_voltage

    @staticmethod
    def _charger_voltage(value: Any) -> int | None:
        """Ignore transient low-voltage noise while the vehicle is not charging."""
        voltage = TelemetryConsumer._to_int(value)
        return voltage if voltage is not None and voltage >= 20 else None

    @classmethod
    def _window_position(cls, value: Any) -> int:
        """Return the Fleet API binary window representation (1=open, 0=closed)."""
        if value is None:
            return 0

        if isinstance(value, bool):
            return 1 if value else 0

        if isinstance(value, (int, float)):
            # Some telemetry streams expose window position as 0..100; >0 means open/vented.
            return 1 if float(value) > 0 else 0

        text = cls._enum_tail(value).strip().lower()
        if not text:
            return 0

        if text in {"open", "opened", "vent", "vented", "partiallyopen", "ajar", "true", "on", "1"}:
            return 1
        if text in {"closed", "close", "shut", "false", "off", "0"}:
            return 0

        # Fallback: if the token contains an open-like keyword, treat it as open.
        return 1 if ("open" in text or "vent" in text) else 0

    @classmethod
    def _is_sentry_active(cls, value: Any) -> bool:
        """Normalize sentry telemetry states to an enabled boolean."""
        if isinstance(value, bool):
            return value
        return cls._enum_tail(value).strip().lower() not in {"", "off", "unknown"}

    @classmethod
    def _is_heat_active(cls, value: Any) -> bool:
        """Normalize steering-wheel heat levels to a boolean."""
        numeric_value = cls._to_int(value)
        return numeric_value > 0 if numeric_value is not None else cls._is_truthy(value)

    @classmethod
    def _is_truthy(cls, value: Any) -> bool:
        """Normalize telemetry booleans and state enums."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return cls._enum_tail(value).strip().lower() in {
            "true",
            "1",
            "on",
            "open",
            "enabled",
            "armed",
        }


async def async_setup_telemetry_consumer(
    hass: HomeAssistant,
    coordinator: TeslaVehicleCommandCoordinator,
) -> TelemetryConsumer | None:
    """Set up and start the telemetry consumer."""
    _LOGGER.info("Setting up telemetry consumer")
    consumer = TelemetryConsumer(hass, coordinator)
    await consumer.async_start()
    coordinator.set_telemetry_receiver_available(True)
    _LOGGER.info("Telemetry consumer started successfully")
    return consumer
