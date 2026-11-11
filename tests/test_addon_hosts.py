"""Add-on hostnames follow the installed slug, whatever repository it came from."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.tesla_vehicle_command import addon_hosts


@pytest.fixture(autouse=True)
def _reset_hosts(monkeypatch):
    monkeypatch.setattr(addon_hosts, "_resolved", dict(addon_hosts.DEFAULT_HOSTS))


def _addon(slug: str, state: str = "started") -> dict:
    return {"slug": slug, "state": state}


def _resolve(monkeypatch, addons) -> None:
    async def fake_list(hass):
        return addons

    monkeypatch.setattr(addon_hosts, "_async_list_addons", fake_list)
    asyncio.run(addon_hosts.async_resolve_addon_hosts(None))


# ---- slug selection


def test_local_install_keeps_the_local_slug():
    addons = [_addon("local_tesla_vehicle_command_proxy")]
    assert (
        addon_hosts.pick_addon_slug(addons, addon_hosts.PROXY_ADDON)
        == "local_tesla_vehicle_command_proxy"
    )


def test_add_on_store_install_uses_the_repository_slug():
    addons = [_addon("core_mosquitto"), _addon("174da054_tesla_vehicle_command_proxy")]
    assert (
        addon_hosts.pick_addon_slug(addons, addon_hosts.PROXY_ADDON)
        == "174da054_tesla_vehicle_command_proxy"
    )


def test_started_add_on_wins_over_a_stopped_one():
    addons = [
        _addon("local_tesla_vehicle_command_proxy", state="stopped"),
        _addon("174da054_tesla_vehicle_command_proxy"),
    ]
    assert (
        addon_hosts.pick_addon_slug(addons, addon_hosts.PROXY_ADDON)
        == "174da054_tesla_vehicle_command_proxy"
    )


def test_local_add_on_wins_a_tie():
    addons = [
        _addon("174da054_tesla_vehicle_command_proxy"),
        _addon("local_tesla_vehicle_command_proxy"),
    ]
    assert (
        addon_hosts.pick_addon_slug(addons, addon_hosts.PROXY_ADDON)
        == "local_tesla_vehicle_command_proxy"
    )


def test_unrelated_or_bare_slugs_do_not_match():
    addons = [
        _addon("tesla_vehicle_command_proxy"),
        _addon("174da054_tesla_vehicle_command_telemetry"),
        {"state": "started"},
    ]
    assert addon_hosts.pick_addon_slug(addons, addon_hosts.PROXY_ADDON) is None


def test_hostname_replaces_underscores():
    assert (
        addon_hosts.hostname_from_slug("174da054_tesla_vehicle_command_proxy")
        == "174da054-tesla-vehicle-command-proxy"
    )


# ---- resolution


def test_defaults_keep_the_historical_local_names():
    assert addon_hosts.proxy_host() == "local-tesla-vehicle-command-proxy"
    assert addon_hosts.telemetry_host() == "local-tesla-vehicle-command-telemetry"


def test_resolution_follows_the_installed_add_ons(monkeypatch):
    _resolve(
        monkeypatch,
        [
            _addon("174da054_tesla_vehicle_command_proxy"),
            _addon("174da054_tesla_vehicle_command_telemetry"),
        ],
    )
    assert addon_hosts.proxy_host() == "174da054-tesla-vehicle-command-proxy"
    assert addon_hosts.telemetry_host() == "174da054-tesla-vehicle-command-telemetry"


def test_missing_add_on_falls_back_to_the_local_name(monkeypatch):
    _resolve(monkeypatch, [_addon("174da054_tesla_vehicle_command_proxy")])
    assert addon_hosts.proxy_host() == "174da054-tesla-vehicle-command-proxy"
    assert addon_hosts.telemetry_host() == "local-tesla-vehicle-command-telemetry"


def test_unavailable_supervisor_keeps_the_current_names(monkeypatch):
    _resolve(monkeypatch, None)
    assert addon_hosts.proxy_host() == "local-tesla-vehicle-command-proxy"


# ---- Supervisor API


class _Response:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        if self._error:
            raise self._error

    async def json(self):
        return self._payload


def _session(monkeypatch, response, calls):
    def get(url, **kwargs):
        calls.append((url, kwargs["headers"]))
        return response

    monkeypatch.setattr(
        addon_hosts, "async_get_clientsession", lambda hass: SimpleNamespace(get=get)
    )
    monkeypatch.setattr(
        addon_hosts.aiohttp, "ClientTimeout", lambda **kwargs: kwargs, raising=False
    )


def test_list_addons_reads_the_supervisor_api(monkeypatch):
    calls = []
    addons = [_addon("174da054_tesla_vehicle_command_proxy")]
    _session(monkeypatch, _Response({"data": {"addons": addons}}), calls)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")
    monkeypatch.delenv("SUPERVISOR", raising=False)

    assert asyncio.run(addon_hosts._async_list_addons(None)) == addons
    assert calls == [("http://supervisor/addons", {"Authorization": "Bearer token"})]


def test_list_addons_without_supervisor_returns_none(monkeypatch):
    calls = []
    _session(monkeypatch, _Response({}), calls)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    assert asyncio.run(addon_hosts._async_list_addons(None)) is None
    assert calls == []


def test_list_addons_api_error_returns_none(monkeypatch):
    calls = []
    _session(monkeypatch, _Response(error=addon_hosts.aiohttp.ClientError("403")), calls)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")

    assert asyncio.run(addon_hosts._async_list_addons(None)) is None
