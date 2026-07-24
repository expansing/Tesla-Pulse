"""Telemetry consumer for Tesla Fleet Telemetry stream."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import zmq
import zmq.asyncio

from homeassistant.core import HomeAssistant

from .const import DOMAIN, TELEMETRY_ZMQ_PORT
from .coordinator import TeslaVehicleCommandCoordinator

_LOGGER = logging.getLogger(__name__)

# Hardcoded addon hostname for the telemetry receiver
TELEMETRY_ADDON_HOSTNAME = "local-tesla-vehicle-command-telemetry"


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
                _LOGGER.warning("Telemetry message missing VIN: %s", topic)
                return

            # Check if this vehicle is managed by us
            vehicle_config = self.coordinator.get_vehicle_config(vin)
            if not vehicle_config:
                _LOGGER.warning("Telemetry for unmanaged vehicle: %s", vin)
                return

            _LOGGER.info("Processing telemetry for vehicle %s, topic: %s", vin, topic)
            # Process based on topic/type
            await self._update_coordinator_data(vin, topic, data)

        except json.JSONDecodeError as err:
            _LOGGER.warning("Failed to decode telemetry payload: %s", err)
        except Exception as err:
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
        )

    async def _process_vehicle_signals(
        self, vin: str, response: dict[str, Any], data: dict[str, Any]
    ) -> tuple[set[str], set[str]]:
        """Process vehicle signal data from telemetry."""
        signals = self._decode_signals(data)
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

        # Map requested Fleet Telemetry signals to the Fleet API state structure.
        signal_mapping = {
            # Battery/Charging
            "Soc": (
                ("charge_state", "battery_level", self._to_int),
                ("charge_state", "usable_battery_level", self._to_int),
            ),
            "BatteryLevel": (("charge_state", "battery_level", self._to_int),),
            "RatedRange": (("charge_state", "battery_range", None),),
            "EstBatteryRange": (("charge_state", "est_battery_range", None),),
            "IdealBatteryRange": (("charge_state", "ideal_battery_range", None),),
            "DetailedChargeState": (("charge_state", "charging_state", self._charge_state),),
            "ChargeLimitSoc": (("charge_state", "charge_limit_soc", self._to_int),),
            "TimeToFullCharge": (("charge_state", "time_to_full_charge", None),),
            "ChargerVoltage": (("charge_state", "charger_voltage", self._to_int),),
            "ChargeAmps": (
                ("charge_state", "charger_actual_current", self._to_int),
                ("charge_state", "charge_current_request", self._to_int),
            ),
            "ChargePortDoorOpen": (("charge_state", "charge_port_door_open", self._is_truthy),),
            
            # Climate
            "InsideTemp": (("climate_state", "inside_temp", None),),
            "OutsideTemp": (("climate_state", "outside_temp", None),),
            "HvacLeftTemperatureRequest": (("climate_state", "driver_temp_setting", None),),
            "HvacRightTemperatureRequest": (("climate_state", "passenger_temp_setting", None),),
            "HvacFanStatus": (("climate_state", "fan_status", None),),
            "HvacPower": (("climate_state", "is_climate_on", self._is_truthy),),
            "PreconditioningEnabled": (("climate_state", "is_preconditioning", self._is_truthy),),
            "ClimateKeeperMode": (("climate_state", "climate_keeper_mode", self._climate_keeper_mode),),
            "DefrostMode": (("climate_state", "defrost_mode", self._is_defrost_active),),
            "SeatHeaterLeft": (("climate_state", "seat_heater_left", self._to_int),),
            "SeatHeaterRight": (("climate_state", "seat_heater_right", self._to_int),),
            "SeatHeaterRearLeft": (("climate_state", "seat_heater_rear_left", self._to_int),),
            "SeatHeaterRearRight": (("climate_state", "seat_heater_rear_right", self._to_int),),
            "SeatHeaterRearCenter": (("climate_state", "seat_heater_rear_center", self._to_int),),
            "HvacSteeringWheelHeatLevel": (
                ("climate_state", "steering_wheel_heater", self._is_heat_active),
            ),
            
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
            
            # Drive State
            "Gear": (("drive_state", "shift_state", self._shift_state),),
            "VehicleSpeed": (("drive_state", "speed", self._to_int),),
            "GpsHeading": (("drive_state", "heading", self._to_int),),
            "Odometer": (("vehicle_state", "odometer", None),),
            
            # Version
            "Version": (("vehicle_state", "car_version", None),),
        }

        processed_fields: set[str] = set()
        for signal_name, targets in signal_mapping.items():
            if signal_name in signals:
                for state_category, state_key, transform in targets:
                    value = signals[signal_name]
                    response[state_category][state_key] = (
                        transform(value) if transform else value
                    )
                processed_fields.add(signal_name)

        self._apply_charging_composites(
            response, last_signals, set(signals), processed_fields
        )
        self._apply_door_state(response, signals, processed_fields)

        location = signals.get("Location")
        if isinstance(location, dict):
            drive_state = response["drive_state"]
            drive_state["latitude"] = location.get("latitude")
            drive_state["longitude"] = location.get("longitude")
            processed_fields.add("Location")

        _LOGGER.debug("Updated telemetry data for %s: %d signals", vin, len(signals))
        return set(signals), processed_fields

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
        status = data.get("status", "unknown")
        response.setdefault("vehicle_state", {})
        response["vehicle_state"]["connectivity_status"] = status
        
        # If vehicle just came online, we might want to trigger a full refresh
        if status == "online":
            _LOGGER.info("Vehicle %s came online via telemetry", vin)

    @classmethod
    def _apply_charging_composites(
        cls,
        response: dict[str, Any],
        last_signals: dict[str, Any],
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
            charge_state["charger_power"] = cls._to_int(max(powers))
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
                vehicle_state[state_key] = cls._window_position(doors[telemetry_key])
        processed_fields.add("DoorState")

    @staticmethod
    def _to_int(value: Any) -> int | None:
        """Convert telemetry numeric values to Fleet API integer fields."""
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _enum_tail(value: Any) -> str:
        """Strip the protobuf enum field prefix from a telemetry value."""
        text = str(value)
        marker = text.rfind("State")
        return text[marker + len("State") :] if marker >= 0 else text

    @classmethod
    def _shift_state(cls, value: Any) -> str | None:
        """Normalize a shift-state enum to Tesla's single-letter API form."""
        state = cls._enum_tail(value).strip().upper()[:1]
        return state if state in {"P", "D", "R", "N"} else None

    @classmethod
    def _charge_state(cls, value: Any) -> str:
        """Normalize a detailed charging-state enum."""
        return cls._enum_tail(value).strip() or "Disconnected"

    @classmethod
    def _climate_keeper_mode(cls, value: Any) -> str:
        """Normalize a climate-keeper enum."""
        return (cls._enum_tail(value).strip() or "off").lower()

    @classmethod
    def _window_position(cls, value: Any) -> int:
        """Return the Fleet API's binary door/window representation."""
        return 1 if cls._is_truthy(value) else 0

    @classmethod
    def _is_sentry_active(cls, value: Any) -> bool:
        """Normalize sentry telemetry states to an enabled boolean."""
        if isinstance(value, bool):
            return value
        return cls._enum_tail(value).strip().lower() not in {"", "off", "unknown"}

    @classmethod
    def _is_defrost_active(cls, value: Any) -> bool:
        """Normalize a defrost-mode enum to an enabled boolean."""
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