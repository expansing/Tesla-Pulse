"""Switch entities for Tesla Vehicle Command."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeslaVehicleCommandCoordinator
from .entity import TeslaVehicleControlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    coordinator: TeslaVehicleCommandCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for vehicle in coordinator.vehicles:
        vin = vehicle["vin"]
        entities.extend([
            TeslaSentryModeSwitch(coordinator, vin, vehicle["name"]),
            TeslaChargePortSwitch(coordinator, vin, vehicle["name"]),
            TeslaDefrostSwitch(coordinator, vin, vehicle["name"]),
            TeslaChargingSwitch(coordinator, vin, vehicle["name"]),
        ])

    async_add_entities(entities)


class TeslaSentryModeSwitch(TeslaVehicleControlEntity, SwitchEntity):
    """Switch for Sentry Mode."""

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_sentry_mode"
        self._attr_name = "Sentry Mode"
        self._attr_icon = "mdi:shield-car"

    @property
    def is_on(self) -> bool | None:
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        vehicle_state = response.get("vehicle_state", {})
        return vehicle_state.get("sentry_mode", False)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self.vin, "sentry_on")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self.vin, "sentry_off")
        await self.coordinator.async_request_refresh()


class TeslaChargePortSwitch(TeslaVehicleControlEntity, SwitchEntity):
    """Switch for Charge Port."""

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_charge_port"
        self._attr_name = "Charge Port"
        self._attr_icon = "mdi:ev-plug-ccs2"

    @property
    def is_on(self) -> bool | None:
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        charge_state = response.get("charge_state", {})
        return charge_state.get("charge_port_door_open", False)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self.vin, "charge_port_open")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self.vin, "charge_port_close")
        await self.coordinator.async_request_refresh()


class TeslaDefrostSwitch(TeslaVehicleControlEntity, SwitchEntity):
    """Switch for Defrost (max AC + heat + rear defrost)."""

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_defrost"
        self._attr_name = "Defrost"
        self._attr_icon = "mdi:car-defrost-rear"

    @property
    def is_on(self) -> bool | None:
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        climate = response.get("climate_state", {})
        mode = climate.get("defrost_mode")
        if mode is None:
            return None
        if isinstance(mode, bool):
            return mode
        if isinstance(mode, (int, float)):
            # 0=Unknown, 1=Off, 2=Normal, 3=Max, 4=AutoDefog
            return int(mode) not in (0, 1)
        normalized = str(mode).strip().lower()
        return normalized not in ("", "off", "unknown", "none", "0")

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self.vin, "preconditioning_max")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self.vin, "preconditioning_max_off")
        await self.coordinator.async_request_refresh()


# charging states in which the car is taking (or about to take) energy
CHARGING_ON_STATES = ("Charging", "Starting")
CHARGING_STATES = ("Charging", "Complete", "Disconnected", "Stopped", "NoPower", "Starting")


class TeslaChargingSwitch(TeslaVehicleControlEntity, SwitchEntity):
    """Switch to start or stop charging (charge_start / charge_stop).

    Lets automations pause the car without cutting power to the wall charger.
    """

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_charging"
        self._attr_name = "Charging"
        self._attr_icon = "mdi:ev-station"

    @property
    def is_on(self) -> bool | None:
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        state = response.get("charge_state", {}).get("charging_state")
        if state not in CHARGING_STATES:
            return None
        return state in CHARGING_ON_STATES

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self.vin, "charge_start")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self.vin, "charge_stop")
        await self.coordinator.async_request_refresh()
