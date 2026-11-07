"""Resolve the Supervisor DNS hostnames of the companion add-ons.

Supervisor derives an add-on hostname from its slug, and the slug starts with
the repository it was installed from: ``local_`` for a local add-on, or an
8-character hash of the repository URL when it comes from the Add-on Store
(``174da054_tesla_vehicle_command_proxy`` for this repository, something else
for a fork). Hardcoding ``local-...`` therefore only works for local installs.

The hostnames are resolved once from the Supervisor API when the proxy manager
starts. Without Supervisor, or if the add-on is not found, the historical
``local-...`` names are kept, so existing local installs behave as before.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable, Mapping
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import PROXY_HOST

_LOGGER = logging.getLogger(__name__)

PROXY_ADDON = "tesla_vehicle_command_proxy"
TELEMETRY_ADDON = "tesla_vehicle_command_telemetry"

DEFAULT_HOSTS: dict[str, str] = {
    PROXY_ADDON: PROXY_HOST,
    TELEMETRY_ADDON: "local-tesla-vehicle-command-telemetry",
}

_resolved: dict[str, str] = dict(DEFAULT_HOSTS)


def proxy_host() -> str:
    """Return the hostname of the vehicle command proxy add-on."""
    return _resolved[PROXY_ADDON]


def telemetry_host() -> str:
    """Return the hostname of the Fleet Telemetry receiver add-on."""
    return _resolved[TELEMETRY_ADDON]


def hostname_from_slug(slug: str) -> str:
    """Return the Supervisor DNS hostname of an add-on slug."""
    return slug.replace("_", "-")


def pick_addon_slug(addons: Iterable[Mapping[str, Any]], name: str) -> str | None:
    """Return the installed slug for add-on ``name``, whatever its repository.

    A started add-on wins over a stopped one, and a local add-on wins a tie.
    """
    candidates = [
        addon
        for addon in addons
        if isinstance(addon.get("slug"), str)
        and addon["slug"].endswith(f"_{name}")
        and len(addon["slug"]) > len(name) + 1
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda addon: (
            addon.get("state") != "started",
            addon["slug"] != f"local_{name}",
            addon["slug"],
        )
    )
    return candidates[0]["slug"]


async def _async_list_addons(hass: HomeAssistant) -> list[Mapping[str, Any]] | None:
    """Return the installed add-ons, or None when Supervisor is unavailable."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    host = os.environ.get("SUPERVISOR") or "supervisor"
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"http://{host}/addons",
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        _LOGGER.warning("Could not list add-ons from Supervisor: %s", err)
        return None
    addons = (payload.get("data") or {}).get("addons")
    return addons if isinstance(addons, list) else None


async def async_resolve_addon_hosts(hass: HomeAssistant) -> None:
    """Resolve the add-on hostnames from Supervisor, keeping defaults on failure."""
    addons = await _async_list_addons(hass)
    if addons is None:
        return
    for name, default in DEFAULT_HOSTS.items():
        slug = pick_addon_slug(addons, name)
        hostname = hostname_from_slug(slug) if slug else default
        if slug is None:
            _LOGGER.debug("Add-on %s not found in Supervisor, using %s", name, default)
        elif hostname != _resolved[name]:
            _LOGGER.info("Using add-on hostname %s for %s", hostname, name)
        _resolved[name] = hostname
