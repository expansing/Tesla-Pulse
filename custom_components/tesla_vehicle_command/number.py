"""Number entities for Tesla Vehicle Command."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHARGE_LIMIT_MAX,
    CHARGE_LIMIT_MIN,
    CHARGE_LIMIT_STEP,
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
    TEMP_STEP,
)
from .coordinator import TeslaVehicleCommandCoordinator
from .entity import TeslaVehicleControlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator: TeslaVehicleCommandCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for vehicle in coordinator.vehicles:
        vin = vehicle["vin"]
        entities.extend([
            TeslaChargeLimitNumber(coordinator, vin, vehicle["name"]),
            TeslaTargetTemperatureNumber(coordinator, vin, vehicle["name"]),
        ])

    async_add_entities(entities)


class TeslaChargeLimitNumber(TeslaVehicleControlEntity, NumberEntity):
    """Number entity for charge limit."""

    _attr_native_min_value = CHARGE_LIMIT_MIN
    _attr_native_max_value = CHARGE_LIMIT_MAX
    _attr_native_step = CHARGE_LIMIT_STEP
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_charge_limit"
        self._attr_name = "Charge Limit"
        self._attr_icon = "mdi:battery-charging-50"

    @property
    def native_value(self) -> float | None:
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        charge_state = response.get("charge_state", {})
        return charge_state.get("charge_limit_soc")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_send_command(
            self.vin, "set_charge_limit", {"percent": int(value)}
        )
        await self.coordinator.async_request_refresh()


class TeslaTargetTemperatureNumber(TeslaVehicleControlEntity, NumberEntity):
    """Number entity for target temperature."""

    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_step = TEMP_STEP
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_target_temperature"
        self._attr_name = "Target Temperature"
        self._attr_icon = "mdi:thermostat"

    @property
    def native_value(self) -> float | None:
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        climate = response.get("climate_state", {})
        return climate.get("driver_temp_setting")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_send_command(
            self.vin, "set_temps", {"driver_temp": value, "passenger_temp": value}
        )
        await self.coordinator.async_request_refresh()