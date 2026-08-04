"""API client for Tesla Vehicle Command integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_COMMAND,
    API_VEHICLES,
    API_WAKE_UP,
    COMMAND_BODIES,
    COMMANDS,
    PROXY_HOST,
    PROXY_PORT,
)
from .proxy_manager import ProxyManager

_LOGGER = logging.getLogger(__name__)


class TeslaVehicleCommandAPI:
    """API client for communicating with tesla-http-proxy."""

    def __init__(
        self,
        hass: HomeAssistant,
        proxy_manager: ProxyManager,
        access_token: str,
    ) -> None:
        """Initialize the API client."""
        self.hass = hass
        self.proxy_manager = proxy_manager
        self.access_token = access_token
        self._session = async_get_clientsession(hass)

    @property
    def base_url(self) -> str:
        """Return the proxy base URL."""
        return f"https://{PROXY_HOST}:{PROXY_PORT}"

    @property
    def ssl_context(self):
        """Return SSL context for proxy communication."""
        return self.proxy_manager.ssl_context

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Make a request to the proxy."""
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                json=json_data,
                ssl=self.ssl_context,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 401:
                    # Token might be expired
                    raise aiohttp.ClientResponseError(
                        request_info=resp.request_info,
                        history=resp.history,
                        status=resp.status,
                        message="Unauthorized - token may be expired",
                    )

                if resp.status >= 400:
                    text = await resp.text()
                    raise aiohttp.ClientResponseError(
                        request_info=resp.request_info,
                        history=resp.history,
                        status=resp.status,
                        message=f"API error: {text}",
                    )

                return await resp.json()

        except asyncio.TimeoutError as err:
            raise TimeoutError(f"Request timed out: {url}") from err
        except aiohttp.ClientError as err:
            raise RuntimeError(f"Request failed: {err}") from err

    async def get_vehicles(self) -> list[dict[str, Any]]:
        """Get list of vehicles."""
        result = await self._request("GET", API_VEHICLES)
        return result.get("response", [])

    async def wake_up(self, vin: str) -> dict[str, Any]:
        """Wake up vehicle."""
        path = API_WAKE_UP.format(vin=vin)
        result = await self._request("POST", path, timeout=60)
        return result

    async def send_command(
        self,
        vin: str,
        command: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a command to the vehicle."""
        # Map command to API command
        api_command = COMMANDS.get(command, command)
        command_body = body if body is not None else COMMAND_BODIES.get(command, {})

        path = API_COMMAND.format(vin=vin, command=api_command)
        result = await self._request("POST", path, json_data=command_body)
        return result

    # The proxy returns the Tesla API response format
    async def set_temperature(self, vin: str, driver_temp: float, passenger_temp: float) -> dict[str, Any]:
        """Set climate temperature."""
        return await self.send_command(
            vin,
            "set_temps",
            {"driver_temp": driver_temp, "passenger_temp": passenger_temp},
        )

    async def set_charge_limit(self, vin: str, percent: int) -> dict[str, Any]:
        """Set charge limit."""
        return await self.send_command(
            vin,
            "set_charge_limit",
            {"percent": percent},
        )

    async def set_seat_heater(self, vin: str, seat: int, level: int) -> dict[str, Any]:
        """Set seat heater level (0-3)."""
        return await self.send_command(
            vin,
            "seat_heater",
            {"seat_position": seat, "level": level},
        )

    async def set_steering_heater(self, vin: str, on: bool) -> dict[str, Any]:
        """Set steering wheel heater."""
        return await self.send_command(
            vin,
            "steering_heater",
            {"on": on},
        )

    async def control_windows(self, vin: str, command: str) -> dict[str, Any]:
        """Control windows (vent/close)."""
        return await self.send_command(
            vin,
            f"window_{command}",
        )

    async def control_sunroof(self, vin: str, state: str, percent: int = 0) -> dict[str, Any]:
        """Control sunroof (open/close/vent)."""
        body = {"state": state}
        if percent:
            body["percent"] = percent
        return await self.send_command(vin, "sunroof", body)

    async def actuate_trunk(self, vin: str, which: str) -> dict[str, Any]:
        """Actuate trunk (front/rear)."""
        return await self.send_command(
            vin,
            f"trunk_{which}",
        )

    async def set_sentry_mode(self, vin: str, on: bool) -> dict[str, Any]:
        """Set sentry mode."""
        return await self.send_command(
            vin,
            "sentry_on" if on else "sentry_off",
        )