"""Base entity for Tesla Vehicle Command."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TeslaVehicleCommandCoordinator


class TeslaVehicleCommandEntity(CoordinatorEntity[TeslaVehicleCommandCoordinator]):
    """Base entity for Tesla Vehicle Command."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.vin = vin
        self._vehicle_name = vehicle_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={("tesla_vehicle_command", self.vin)},
            name=self._vehicle_name,
            manufacturer="Tesla",
            model="Vehicle",
            sw_version="1.0",
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False

        vehicle_data = self.coordinator.data.get(self.vin, {})
        return "error" not in vehicle_data