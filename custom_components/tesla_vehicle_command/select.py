"""Select entities for Tesla Vehicle Command."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeslaVehicleCommandCoordinator
from .entity import TeslaVehicleControlEntity


SEAT_HEATER_OPTIONS = ["Off", "Low", "Medium", "High"]
SEAT_POSITIONS = [
    (0, "Front Left"),
    (1, "Front Right"),
    (2, "Rear Left"),
    (3, "Rear Right"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities."""
    coordinator: TeslaVehicleCommandCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for vehicle in coordinator.vehicles:
        vin = vehicle["vin"]
        # Seat heaters for each position
        for seat_index, seat_name in SEAT_POSITIONS:
            entities.append(TeslaSeatHeaterSelect(coordinator, vin, vehicle["name"], seat_index, seat_name))

        # Steering wheel heater
        entities.append(TeslaSteeringHeaterSelect(coordinator, vin, vehicle["name"]))

    async_add_entities(entities)


class TeslaSeatHeaterSelect(TeslaVehicleControlEntity, SelectEntity):
    """Select entity for seat heater level."""

    _attr_options = SEAT_HEATER_OPTIONS

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
        seat_index: int,
        seat_name: str,
    ) -> None:
        super().__init__(coordinator, vin, vehicle_name)
        self._seat_index = seat_index
        self._attr_unique_id = f"{vin}_seat_heater_{seat_index}"
        self._attr_name = f"{seat_name} Seat Heater"
        self._attr_icon = "mdi:seat-heater"

    @property
    def current_option(self) -> str | None:
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        climate = response.get("climate_state", {})

        seat_keys = [
            "seat_heater_left",
            "seat_heater_right",
            "seat_heater_rear_left",
            "seat_heater_rear_right",
        ]

        if self._seat_index < len(seat_keys):
            level = climate.get(seat_keys[self._seat_index])
            if isinstance(level, int) and 0 <= level < len(SEAT_HEATER_OPTIONS):
                return SEAT_HEATER_OPTIONS[level]
        return None

    async def async_select_option(self, option: str) -> None:
        level = SEAT_HEATER_OPTIONS.index(option)
        await self.coordinator.async_send_command(
            self.vin,
            "seat_heater",
            {"seat_position": self._seat_index, "level": level},
        )
        await self.coordinator.async_request_refresh()


class TeslaSteeringHeaterSelect(TeslaVehicleControlEntity, SelectEntity):
    """Select entity for steering wheel heater."""

    _attr_options = ["Off", "On"]

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_steering_heater"
        self._attr_name = "Steering Wheel Heater"
        self._attr_icon = "mdi:steering"

    @property
    def current_option(self) -> str | None:
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        climate = response.get("climate_state", {})
        heater_enabled = climate.get("steering_wheel_heater")
        if heater_enabled is None:
            return None
        return "On" if heater_enabled else "Off"

    async def async_select_option(self, option: str) -> None:
        on = option == "On"
        await self.coordinator.async_send_command(
            self.vin, "steering_heater", {"on": on}
        )
        await self.coordinator.async_request_refresh()