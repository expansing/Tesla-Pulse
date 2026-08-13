"""Minimal Home Assistant import stubs for coordinator unit tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class _DataUpdateCoordinator(Generic[T]):
    """Stand-in for the generic base class used only during import."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.hass = args[0] if args else None
        self.data: T | None = None

    def async_update_listeners(self) -> None:
        """Provide the coordinator method used by tested helpers."""

    def async_set_updated_data(self, data: T) -> None:
        """Provide the coordinator method used by cache loading."""
        self.data = data


class _Store(Generic[T]):
    """Stand-in for Home Assistant's generic persistent store."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


class HomeAssistantError(Exception):
    """Stand-in for Home Assistant's user-facing error base class."""


class _Platform:
    """Names used by the integration's platform constants."""

    SENSOR = "sensor"
    CLIMATE = "climate"
    LOCK = "lock"
    COVER = "cover"
    SWITCH = "switch"
    NUMBER = "number"
    BUTTON = "button"
    SELECT = "select"


def _module(name: str, **attributes: Any) -> ModuleType:
    module = ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    sys.modules[name] = module
    return module


def _install_home_assistant_stubs() -> None:
    homeassistant = _module("homeassistant")
    _module("homeassistant.config_entries", ConfigEntry=object)
    _module("homeassistant.const", Platform=_Platform)
    _module("homeassistant.core", HomeAssistant=object)
    _module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
    helpers = _module("homeassistant.helpers")
    _module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
    _module("homeassistant.helpers.storage", Store=_Store)
    _module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_DataUpdateCoordinator,
        UpdateFailed=RuntimeError,
    )
    homeassistant.helpers = helpers

    _module("aiohttp", ClientError=Exception, ClientTimeout=object)

    package_name = "custom_components.tesla_vehicle_command"
    package = _module(package_name)
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "tesla_vehicle_command")
    ]
    _module(f"{package_name}.proxy_manager", ProxyManager=object)


_install_home_assistant_stubs()