"""Config flow for Tesla Pulse integration."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode
from pathlib import Path

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    AUTH_CALLBACK_PATH,
    _encode_jwt,
)

from .const import (
    BATTERY_CAPACITY_PRESET_CUSTOM,
    BATTERY_CAPACITY_PRESETS,
    CONF_BATTERY_REFERENCE_CAPACITIES,
    CONF_BATTERY_REFERENCE_CAPACITY_KWH,
    CONF_BATTERY_REFERENCE_CAPACITY_PRESET,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FLEET_API_BASE_URL,
    CONF_TELEMETRY_HOSTNAME,
    CONF_TELEMETRY_INACTIVITY_MINUTES,
    CONF_TELEMETRY_PORT,
    CONF_TELEMETRY_REGISTRATION_REQUIRED_VINS,
    CONF_WAKE_ON_STARTUP,
    CONF_VEHICLES,
    CONF_VIN,
    CONF_NAME,
    CONF_PRIVATE_KEY_PATH,
    DOMAIN,
    FLEET_API_BASE_URL_EU,
    FLEET_API_BASE_URL_NA,
    DEFAULT_TELEMETRY_PORT,
    DEFAULT_TELEMETRY_INACTIVITY_MINUTES,
    MAX_TELEMETRY_PORT,
    MAX_TELEMETRY_INACTIVITY_MINUTES,
    MIN_TELEMETRY_PORT,
    MIN_TELEMETRY_INACTIVITY_MINUTES,
    OAUTH2_AUTHORIZE,
    OAUTH2_SCOPES,
    OAUTH2_TOKEN,
)

_LOGGER = logging.getLogger(__name__)


class PartnerAccountNotRegisteredError(Exception):
    """Raised when Tesla requires Fleet API partner-account registration."""


# Step 1: User provides OAuth credentials
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
    }
)

# Step 3: Vehicle selection
STEP_VEHICLE_SCHEMA = vol.Schema(
    {
        vol.Required("vehicles"): vol.All(
            list,
            vol.Length(min=1),
        ),
    }
)

# Step 4: Private key - generate or import
STEP_KEY_SCHEMA = vol.Schema(
    {
        vol.Required("key_action"): vol.In(["generate", "import"]),
        vol.Optional("private_key"): str,
    }
)


class TeslaVehicleCommandConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tesla Pulse."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TeslaVehicleCommandOptionsFlow:
        """Return the options flow for this integration."""
        return TeslaVehicleCommandOptionsFlow()

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._redirect_uri: str | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._fleet_api_base_url = FLEET_API_BASE_URL_EU
        self._vehicles: list[dict[str, Any]] = []
        self._selected_vehicles: list[str] = []
        self._auth_url: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - OAuth credentials."""
        errors = {}

        if user_input is not None:
            self._client_id = user_input[CONF_CLIENT_ID]
            self._client_secret = user_input[CONF_CLIENT_SECRET]

            # Validate credentials by trying to get auth URL
            try:
                self._auth_url = await self._generate_auth_url()
                return await self.async_step_auth()
            except Exception as err:
                _LOGGER.error("Failed to generate auth URL: %s", err)
                errors["base"] = "auth_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "docs_url": "https://developer.tesla.com/docs/fleet-api/authentication/third-party-tokens"
            },
        )

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Tesla's OAuth callback."""
        if user_input is not None:
            if "error" in user_input:
                return self.async_abort(reason="authorize_rejected")
            try:
                await self._exchange_code_for_tokens(user_input["code"])
            except Exception as err:
                _LOGGER.error("Token exchange failed: %s", err)
                return self.async_abort(reason="token_exchange_failed")
            return self.async_external_step_done(next_step_id="vehicles")

        return self.async_external_step(
            step_id="auth",
            url=self._auth_url or "",
        )

    async def _generate_auth_url(self) -> str:
        """Generate Tesla OAuth authorization URL."""
        external_url = self.hass.config.external_url
        if not external_url:
            raise RuntimeError("Home Assistant external URL is not configured")
        self._redirect_uri = f"{external_url.rstrip('/')}{AUTH_CALLBACK_PATH}"
        _LOGGER.info("Tesla OAuth redirect URI: %s", self._redirect_uri)
        state = _encode_jwt(
            self.hass,
            {"flow_id": self.flow_id, "redirect_uri": self._redirect_uri},
        )

        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(OAUTH2_SCOPES),
            "state": state,
        }

        return f"{OAUTH2_AUTHORIZE}?{urlencode(params)}"

    async def async_step_vehicles(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select vehicles to add."""
        errors = {}

        if user_input and "vehicles" in user_input:
            self._selected_vehicles = user_input["vehicles"]
            return await self.async_step_key()

        # Fetch vehicle list if not already done
        if not self._vehicles:
            try:
                self._vehicles = await self._fetch_vehicles()
            except PartnerAccountNotRegisteredError:
                return self.async_abort(reason="partner_account_not_registered")
            except Exception as err:
                _LOGGER.error("Failed to fetch vehicles: %s", err)
                errors["base"] = "fetch_vehicles_failed"
                return self.async_show_form(
                    step_id="vehicles",
                    data_schema=vol.Schema({}),
                    errors=errors,
                )

        if not self._vehicles:
            errors["base"] = "no_vehicles"
            return self.async_show_form(
                step_id="vehicles",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        # Build vehicle selection schema
        vehicle_options = [
            selector.SelectOptionDict(
                value=vehicle["vin"],
                label=f"{vehicle.get('display_name', vehicle['vin'])} ({vehicle['vin']})",
            )
            for vehicle in self._vehicles
        ]

        schema = vol.Schema(
            {
                vol.Required("vehicles"): vol.All(
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=vehicle_options,
                            multiple=True,
                        )
                    ),
                    vol.Length(min=1),
                )
            }
        )

        return self.async_show_form(
            step_id="vehicles",
            data_schema=schema,
            errors=errors,
        )

    async def _fetch_vehicles(self) -> list[dict[str, Any]]:
        """Fetch vehicle list from Tesla Fleet API."""
        if not self._access_token:
            raise RuntimeError("No access token")

        session = async_get_clientsession(self.hass)
        headers = {"Authorization": f"Bearer {self._access_token}"}

        async with session.get(
            f"{self._fleet_api_base_url}/api/1/vehicles",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                if resp.status == 421:
                    region_match = re.search(
                        r"use base URL: (https://fleet-api\.prd\.(?:na|eu)\.vn\.cloud\.tesla\.com)",
                        text,
                    )
                    if region_match:
                        regional_base_url = region_match.group(1)
                        if (
                            regional_base_url in (
                                FLEET_API_BASE_URL_NA,
                                FLEET_API_BASE_URL_EU,
                            )
                            and regional_base_url != self._fleet_api_base_url
                        ):
                            self._fleet_api_base_url = regional_base_url
                            return await self._fetch_vehicles()
                if resp.status == 412 and "must be registered" in text:
                    raise PartnerAccountNotRegisteredError from None
                raise RuntimeError(f"Failed to fetch vehicles: {resp.status} - {text}")

            data = await resp.json()
            return data.get("response", [])

    async def async_step_key(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle private key generation or import."""
        errors = {}

        if user_input is not None:
            action = user_input["key_action"]

            if action == "generate":
                # Generate new key pair
                private_key_path = await self._generate_key_pair()
            elif action == "import":
                private_key = user_input.get("private_key", "").strip()
                if not private_key:
                    errors["private_key"] = "required"
                    return self.async_show_form(
                        step_id="key",
                        data_schema=STEP_KEY_SCHEMA,
                        errors=errors,
                    )
                private_key_path = await self._import_private_key(private_key)
            else:
                errors["base"] = "invalid_action"
                return self.async_show_form(
                    step_id="key",
                    data_schema=STEP_KEY_SCHEMA,
                    errors=errors,
                )

            # Create config entry
            return await self._create_entry(private_key_path)

        return self.async_show_form(
            step_id="key",
            data_schema=STEP_KEY_SCHEMA,
            errors=errors,
            description_placeholders={
                "enroll_url": "https://www.tesla.com/teslaaccount/keys"
            },
        )

    async def _generate_key_pair(self) -> str:
        """Generate a new ECDH key pair for vehicle command authentication."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        # Generate private key
        private_key = ec.generate_private_key(ec.SECP256R1())

        # Serialize to PEM
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        key_dir = self.hass.config.path(DOMAIN, "keys")
        vin = self._selected_vehicles[0]
        key_path = Path(key_dir) / f"{vin}.pem"

        await self.hass.async_add_executor_job(
            self._write_private_key, key_path, pem
        )

        _LOGGER.info("Generated private key at %s", key_path)
        return str(key_path)

    async def _import_private_key(self, private_key_pem: str) -> str:
        """Import an existing private key."""
        # Validate it's a valid PEM
        from cryptography.hazmat.primitives import serialization

        try:
            serialization.load_pem_private_key(
                private_key_pem.encode(),
                password=None,
            )
        except Exception as err:
            raise ValueError(f"Invalid private key: {err}")

        key_dir = self.hass.config.path(DOMAIN, "keys")
        vin = self._selected_vehicles[0]
        key_path = Path(key_dir) / f"{vin}.pem"

        await self.hass.async_add_executor_job(
            self._write_private_key, key_path, private_key_pem.encode()
        )

        _LOGGER.info("Imported private key to %s", key_path)
        return str(key_path)

    @staticmethod
    def _write_private_key(key_path: Path, pem: bytes) -> None:
        """Write a private key without blocking Home Assistant's event loop."""
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(pem)
        key_path.chmod(0o600)

    async def _create_entry(self, private_key_path: str) -> FlowResult:
        """Create the config entry."""
        vehicles_data = []
        for vin in self._selected_vehicles:
            vehicle = next((v for v in self._vehicles if v["vin"] == vin), None)
            if vehicle:
                vehicles_data.append(
                    {
                        CONF_VIN: vin,
                        CONF_NAME: vehicle.get("display_name", vin),
                        CONF_PRIVATE_KEY_PATH: private_key_path,
                    }
                )

        data = {
            CONF_CLIENT_ID: self._client_id,
            CONF_CLIENT_SECRET: self._client_secret,
            CONF_FLEET_API_BASE_URL: self._fleet_api_base_url,
            CONF_VEHICLES: vehicles_data,
            "tokens": {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": 0,  # Will be updated on first refresh
            },
        }

        return self.async_create_entry(
            title="Tesla Pulse",
            data=data,
        )

    async def _exchange_code_for_tokens(self, code: str) -> None:
        """Exchange authorization code for access/refresh tokens."""
        if not self._redirect_uri:
            raise RuntimeError("OAuth redirect URI is not initialized")

        session = async_get_clientsession(self.hass)
        token_data = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "redirect_uri": self._redirect_uri,
        }

        async with session.post(
            OAUTH2_TOKEN,
            data=token_data,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Token exchange failed: {resp.status} - {text}")

            tokens = await resp.json()

        self._access_token = tokens["access_token"]
        self._refresh_token = tokens["refresh_token"]

        _LOGGER.debug("Obtained access and refresh tokens")


class TeslaVehicleCommandOptionsFlow(config_entries.OptionsFlow):
    """Handle Tesla Pulse integration options."""

    def __init__(self) -> None:
        """Initialize options collected across vehicle-specific steps."""
        super().__init__()
        self._pending_options: dict[str, Any] = {}
        self._battery_references: dict[str, float] = {}
        self._battery_vehicle_index = 0

    @staticmethod
    def _battery_preset_selector() -> selector.SelectSelector:
        """Return a non-authoritative usable-capacity preset dropdown."""
        options = [
            BATTERY_CAPACITY_PRESET_CUSTOM,
            *BATTERY_CAPACITY_PRESETS,
        ]
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=options,
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="battery_reference_capacity_preset",
            )
        )

    @staticmethod
    def _resolve_battery_reference(
        reference: Any, preset: Any
    ) -> float | None:
        """Resolve an entered value, else a preset, else no reference."""
        if reference is not None:
            return float(reference)
        if isinstance(preset, str) and preset in BATTERY_CAPACITY_PRESETS:
            return float(BATTERY_CAPACITY_PRESETS[preset])
        return None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage Fleet Telemetry receiver settings."""
        if user_input is not None:
            submitted_options = dict(user_input)
            submitted_reference = submitted_options.pop(
                CONF_BATTERY_REFERENCE_CAPACITY_KWH, None
            )
            submitted_preset = submitted_options.pop(
                CONF_BATTERY_REFERENCE_CAPACITY_PRESET, None
            )
            self._pending_options = {
                **self.config_entry.options,
                **submitted_options,
            }
            telemetry_changed = any(
                submitted_options.get(key) != self.config_entry.options.get(key)
                for key in (CONF_TELEMETRY_HOSTNAME, CONF_TELEMETRY_PORT)
            )
            if telemetry_changed:
                self._pending_options[CONF_TELEMETRY_REGISTRATION_REQUIRED_VINS] = [
                    vehicle[CONF_VIN]
                    for vehicle in self.config_entry.data.get(CONF_VEHICLES, [])
                    if isinstance(vehicle, dict) and isinstance(vehicle.get(CONF_VIN), str)
                ]
            references = self.config_entry.options.get(
                CONF_BATTERY_REFERENCE_CAPACITIES, {}
            )
            self._battery_references = (
                dict(references) if isinstance(references, dict) else {}
            )
            self._battery_vehicle_index = 0
            vehicles = self.config_entry.data.get(CONF_VEHICLES, [])
            if len(vehicles) == 1:
                vin = vehicles[0][CONF_VIN]
                resolved = self._resolve_battery_reference(
                    submitted_reference, submitted_preset
                )
                if resolved is None:
                    self._battery_references.pop(vin, None)
                else:
                    self._battery_references[vin] = resolved
                self._pending_options[CONF_BATTERY_REFERENCE_CAPACITIES] = (
                    self._battery_references
                )
                return self.async_create_entry(
                    title="", data=self._pending_options
                )
            return await self.async_step_battery()

        telemetry_hostname = self.config_entry.options.get(
            CONF_TELEMETRY_HOSTNAME, ""
        )
        telemetry_inactivity_minutes = self.config_entry.options.get(
            CONF_TELEMETRY_INACTIVITY_MINUTES,
            DEFAULT_TELEMETRY_INACTIVITY_MINUTES,
        )
        telemetry_port = self.config_entry.options.get(
            CONF_TELEMETRY_PORT, DEFAULT_TELEMETRY_PORT
        )
        wake_on_startup = self.config_entry.options.get(CONF_WAKE_ON_STARTUP, False)
        schema_fields: dict[Any, Any] = {
            vol.Optional(
                CONF_TELEMETRY_HOSTNAME, default=telemetry_hostname
            ): str,
            vol.Required(
                CONF_TELEMETRY_INACTIVITY_MINUTES,
                default=telemetry_inactivity_minutes,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_TELEMETRY_INACTIVITY_MINUTES,
                    max=MAX_TELEMETRY_INACTIVITY_MINUTES,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_TELEMETRY_PORT, default=telemetry_port
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_TELEMETRY_PORT,
                    max=MAX_TELEMETRY_PORT,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_WAKE_ON_STARTUP, default=wake_on_startup
            ): selector.BooleanSelector(),
        }
        vehicles = self.config_entry.data.get(CONF_VEHICLES, [])
        if len(vehicles) == 1:
            references = self.config_entry.options.get(
                CONF_BATTERY_REFERENCE_CAPACITIES, {}
            )
            current_capacity = (
                references.get(vehicles[0][CONF_VIN])
                if isinstance(references, dict)
                else None
            )
            marker_options = (
                {"description": {"suggested_value": current_capacity}}
                if current_capacity is not None
                else {}
            )
            schema_fields[
                vol.Optional(
                    CONF_BATTERY_REFERENCE_CAPACITY_KWH,
                    **marker_options,
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10,
                    max=200,
                    step=0.1,
                    unit_of_measurement="kWh",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            schema_fields[
                vol.Optional(
                    CONF_BATTERY_REFERENCE_CAPACITY_PRESET,
                    default=BATTERY_CAPACITY_PRESET_CUSTOM,
                )
            ] = self._battery_preset_selector()

        schema = vol.Schema(schema_fields)

        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect the new usable battery capacity for each configured vehicle."""
        vehicles = self.config_entry.data.get(CONF_VEHICLES, [])
        if self._battery_vehicle_index >= len(vehicles):
            self._pending_options[CONF_BATTERY_REFERENCE_CAPACITIES] = (
                self._battery_references
            )
            return self.async_create_entry(title="", data=self._pending_options)

        vehicle = vehicles[self._battery_vehicle_index]
        vin = vehicle[CONF_VIN]
        if user_input is not None:
            reference_capacity = user_input.get(
                CONF_BATTERY_REFERENCE_CAPACITY_KWH
            )
            preset = user_input.get(CONF_BATTERY_REFERENCE_CAPACITY_PRESET)
            resolved = self._resolve_battery_reference(reference_capacity, preset)
            if resolved is None:
                self._battery_references.pop(vin, None)
            else:
                self._battery_references[vin] = resolved
            self._battery_vehicle_index += 1
            return await self.async_step_battery()

        current_capacity = self._battery_references.get(vin)
        marker_options = (
            {"description": {"suggested_value": current_capacity}}
            if current_capacity is not None
            else {}
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_BATTERY_REFERENCE_CAPACITY_KWH,
                    **marker_options,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=200,
                        step=0.1,
                        unit_of_measurement="kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_BATTERY_REFERENCE_CAPACITY_PRESET,
                    default=BATTERY_CAPACITY_PRESET_CUSTOM,
                ): self._battery_preset_selector(),
            }
        )
        return self.async_show_form(
            step_id="battery",
            data_schema=schema,
            description_placeholders={
                "vehicle_name": vehicle.get(CONF_NAME, vin),
                "vin": vin,
            },
        )