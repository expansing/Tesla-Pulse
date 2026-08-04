"""Climate entity for Tesla Vehicle Command."""

from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAX_TEMP, MIN_TEMP, TEMP_STEP
from .coordinator import TeslaVehicleCommandCoordinator
from .entity import TeslaVehicleControlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entity."""
    coordinator: TeslaVehicleCommandCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for vehicle in coordinator.vehicles:
        vin = vehicle["vin"]
        entities.append(TeslaClimateEntity(coordinator, vin, vehicle["name"]))

    async_add_entities(entities)


class TeslaClimateEntity(TeslaVehicleControlEntity, ClimateEntity):
    """Climate entity for Tesla vehicle."""

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_climate"
        self._attr_name = "Climate"

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current HVAC mode."""
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        climate = response.get("climate_state", {})

        if climate.get("is_climate_on"):
            return HVACMode.HEAT_COOL
        return HVACMode.OFF

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        climate = response.get("climate_state", {})
        return climate.get("inside_temp")

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        climate = response.get("climate_state", {})
        return climate.get("driver_temp_setting")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_send_command(self.vin, "climate_off")
        elif hvac_mode == HVACMode.HEAT_COOL:
            await self.coordinator.async_send_command(self.vin, "climate_on")

        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs) -> None:
        """Set target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # Tesla uses same temp for driver and passenger
        await self.coordinator.async_send_command(
            self.vin,
            "set_temps",
            {"driver_temp": temperature, "passenger_temp": temperature},
        )

        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn on climate."""
        await self.coordinator.async_send_command(self.vin, "climate_on")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn off climate."""
        await self.coordinator.async_send_command(self.vin, "climate_off")
        await self.coordinator.async_request_refresh()