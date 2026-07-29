"""Tesla Vehicle Command integration for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import CONF_WAKE_ON_STARTUP, DOMAIN
from .coordinator import TeslaVehicleCommandCoordinator
from .proxy_manager import ProxyManager
from .telemetry_consumer import TelemetryConsumer, async_setup_telemetry_consumer

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.CLIMATE,
    Platform.LOCK,
    Platform.COVER,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SELECT,
]

_LOGGER = logging.getLogger(__name__)

# Service schemas
SERVICE_SET_VALET_MODE_SCHEMA = vol.Schema({
    vol.Required("vin"): cv.string,
    vol.Required("enabled"): cv.boolean,
    vol.Optional("pin"): cv.string,
})

SERVICE_SET_SPEED_LIMIT_SCHEMA = vol.Schema({
    vol.Required("vin"): cv.string,
    vol.Required("speed_limit"): vol.All(vol.Coerce(int), vol.Range(min=30, max=200)),
    vol.Required("pin"): cv.string,
})

SERVICE_SEND_NAVIGATION_SCHEMA = vol.Schema({
    vol.Required("vin"): cv.string,
    vol.Required("latitude"): vol.Coerce(float),
    vol.Required("longitude"): vol.Coerce(float),
    vol.Optional("name"): cv.string,
})

SERVICE_CONFIGURE_FLEET_TELEMETRY_SCHEMA = vol.Schema({
    vol.Required("vin"): cv.string,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tesla Vehicle Command from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Initialize proxy manager
    proxy_manager = ProxyManager(hass, entry)
    await proxy_manager.async_start()

    if not proxy_manager.is_running:
        await proxy_manager.async_stop()
        raise ConfigEntryNotReady("Failed to start Tesla HTTP proxy")

    # Initialize coordinator
    coordinator = TeslaVehicleCommandCoordinator(hass, entry, proxy_manager)
    await coordinator.async_load_telemetry_cache()
    await coordinator.async_config_entry_first_refresh()

    if entry.options.get(CONF_WAKE_ON_STARTUP, False):
        # Refresh cached state from the Fleet API only when the user opts in.
        for vehicle in coordinator.vehicles:
            vin = vehicle["vin"]
            try:
                await coordinator.async_wake_up(vin)
                _LOGGER.info("Woke up vehicle %s", vin)
            except Exception as err:
                _LOGGER.warning("Failed to wake up vehicle %s: %s", vin, err)

            initial_data = await coordinator.async_fetch_initial_vehicle_data(vin)
            if initial_data and "response" in initial_data:
                processed_response = coordinator._process_vehicle_response(
                    initial_data["response"]
                )
                coordinator.set_telemetry_data(vin, processed_response)
                _LOGGER.info("Fetched initial vehicle data for %s", vin)
    else:
        _LOGGER.info("Restored cached telemetry state without waking vehicles")

    # Initialize telemetry consumer (auto-discovers endpoint via Supervisor API)
    telemetry_consumer = await async_setup_telemetry_consumer(hass, coordinator)

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "proxy_manager": proxy_manager,
        "telemetry_consumer": telemetry_consumer,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    async def handle_set_valet_mode(call: ServiceCall) -> None:
        """Handle set valet mode service."""
        vin = call.data["vin"]
        enabled = call.data["enabled"]
        pin = call.data.get("pin", "")

        if enabled and not pin:
            raise ValueError("PIN required when enabling valet mode")

        if enabled:
            await coordinator.async_send_command(vin, "valet_mode_on", {"pin": pin})
        else:
            await coordinator.async_send_command(vin, "valet_mode_off")

        await coordinator.async_request_refresh()

    async def handle_set_speed_limit(call: ServiceCall) -> None:
        """Handle set speed limit service."""
        vin = call.data["vin"]
        speed_limit = call.data["speed_limit"]
        pin = call.data["pin"]

        await coordinator.async_send_command(vin, "speed_limit", {"speed_limit": speed_limit, "pin": pin})
        await coordinator.async_request_refresh()

    async def handle_send_navigation(call: ServiceCall) -> None:
        """Handle send navigation service."""
        vin = call.data["vin"]
        latitude = call.data["latitude"]
        longitude = call.data["longitude"]
        name = call.data.get("name", "Home Assistant Destination")

        await coordinator.async_send_command(vin, "send_navigation", {
            "latitude": latitude,
            "longitude": longitude,
            "name": name,
        })
        await coordinator.async_request_refresh()

    async def handle_configure_fleet_telemetry(call: ServiceCall) -> None:
        """Register the configured Fleet Telemetry destination for a vehicle."""
        await coordinator.async_configure_fleet_telemetry(call.data["vin"])

    hass.services.async_register(
        DOMAIN, "set_valet_mode", handle_set_valet_mode, schema=SERVICE_SET_VALET_MODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "set_speed_limit", handle_set_speed_limit, schema=SERVICE_SET_SPEED_LIMIT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "send_navigation", handle_send_navigation, schema=SERVICE_SEND_NAVIGATION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "configure_fleet_telemetry",
        handle_configure_fleet_telemetry,
        schema=SERVICE_CONFIGURE_FLEET_TELEMETRY_SCHEMA,
    )

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["proxy_manager"].async_stop()
        if data.get("telemetry_consumer"):
            await data["telemetry_consumer"].async_stop()

    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", entry.version)
    return True