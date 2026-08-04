"""Lock entity for Tesla Vehicle Command."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
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
    """Set up lock entity."""
    coordinator: TeslaVehicleCommandCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for vehicle in coordinator.vehicles:
        vin = vehicle["vin"]
        entities.append(TeslaLockEntity(coordinator, vin, vehicle["name"]))

    async_add_entities(entities)


class TeslaLockEntity(TeslaVehicleControlEntity, LockEntity):
    """Lock entity for Tesla vehicle."""

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_lock"
        self._attr_name = "Door Lock"

    @property
    def is_locked(self) -> bool | None:
        """Return true if locked."""
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})
        vehicle_state = response.get("vehicle_state", {})
        return vehicle_state.get("locked")

    @property
    def is_locking(self) -> bool:
        """Return true if locking."""
        return False

    @property
    def is_unlocking(self) -> bool:
        """Return true if unlocking."""
        return False

    async def async_lock(self, **kwargs) -> None:
        """Lock the vehicle."""
        await self.coordinator.async_send_command(self.vin, "lock")
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the vehicle."""
        await self.coordinator.async_send_command(self.vin, "unlock")
        await self.coordinator.async_request_refresh()