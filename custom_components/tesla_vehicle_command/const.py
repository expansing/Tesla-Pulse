"""Constants for Tesla Vehicle Command integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "tesla_vehicle_command"

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

# Configuration keys
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_FLEET_API_BASE_URL = "fleet_api_base_url"
CONF_VEHICLES = "vehicles"
CONF_VIN = "vin"
CONF_NAME = "name"
CONF_PRIVATE_KEY_PATH = "private_key_path"
CONF_TELEMETRY_HOSTNAME = "telemetry_hostname"
CONF_TELEMETRY_INACTIVITY_MINUTES = "telemetry_inactivity_minutes"
CONF_TELEMETRY_PORT = "telemetry_port"
CONF_WAKE_BEFORE_COMMAND = "wake_before_command"
CONF_WAKE_ON_STARTUP = "wake_on_startup"
OPT_OPTIONAL_POWERTRAIN_SENSORS_MIGRATED = "optional_powertrain_sensors_migrated"

DEFAULT_TELEMETRY_INACTIVITY_MINUTES = 15
MAX_TELEMETRY_INACTIVITY_MINUTES = 120
MIN_TELEMETRY_INACTIVITY_MINUTES = 1
WAKE_TELEMETRY_TIMEOUT_SECONDS = 90

# OAuth2
OAUTH2_AUTHORIZE = "https://auth.tesla.com/oauth2/v3/authorize"
OAUTH2_TOKEN = "https://auth.tesla.com/oauth2/v3/token"
OAUTH2_SCOPES = [
    "openid",
    "offline_access",
    "vehicle_device_data",
    "vehicle_cmds",
    "vehicle_charging_cmds",
    "vehicle_location",
]

# Fleet API regions
FLEET_API_BASE_URL_NA = "https://fleet-api.prd.na.vn.cloud.tesla.com"
FLEET_API_BASE_URL_EU = "https://fleet-api.prd.eu.vn.cloud.tesla.com"

# Proxy settings
PROXY_PORT = 4443
PROXY_HOST = "local-tesla-vehicle-command-proxy"
PROXY_TIMEOUT = 30

# Telemetry settings
TELEMETRY_ADDON_SLUG = "tesla_vehicle_command_telemetry"
TELEMETRY_ZMQ_PORT = 5284

# API endpoints (proxied through tesla-http-proxy)
API_VEHICLES = "/api/1/vehicles"
API_WAKE_UP = "/api/1/vehicles/{vin}/wake_up"
API_COMMAND = "/api/1/vehicles/{vin}/command/{command}"
API_FLEET_TELEMETRY_CONFIG = "/api/1/vehicles/fleet_telemetry_config"

# Vehicle commands
COMMANDS = {
    "lock": "door_lock",
    "unlock": "door_unlock",
    "door_lock": "door_lock",
    "door_unlock": "door_unlock",
    "honk": "honk_horn",
    "flash": "flash_lights",
    "climate_on": "auto_conditioning_start",
    "climate_off": "auto_conditioning_stop",
    "charge_start": "charge_start",
    "charge_stop": "charge_stop",
    "charge_port_open": "charge_port_door_open",
    "charge_port_close": "charge_port_door_close",
    "trunk_rear": "actuate_trunk",
    "trunk_front": "actuate_trunk",
    "sentry_on": "set_sentry_mode",
    "sentry_off": "set_sentry_mode",
    "seat_heater": "remote_seat_heater_request",
    "steering_heater": "remote_steering_wheel_heater_request",
    "window_vent": "window_control",
    "window_close": "window_control",
    "sunroof": "sun_roof_control",
    "set_temps": "set_temps",
    "set_charge_limit": "set_charge_limit",
    "wake_up": "wake_up",
    "valet_mode_on": "set_valet_mode",
    "valet_mode_off": "set_valet_mode",
    "speed_limit": "speed_limit_set_limit",
    "send_navigation": "navigation_request",
    "media_toggle_playback": "media_toggle_playback",
    "media_next_track": "media_next_track",
    "media_prev_track": "media_prev_track",
    "media_next_fav": "media_next_fav",
    "media_prev_fav": "media_prev_fav",
    "media_volume_up": "media_volume_up",
    "media_volume_down": "media_volume_down",
    "remote_start": "remote_start_drive",
    "remote_start_drive": "remote_start_drive",
    "set_charge_current": "set_charging_amps",
    "open_tonneau": "open_tonneau",
    "close_tonneau": "close_tonneau",
    "homelink": "trigger_homelink",
    "charge_schedule": "set_scheduled_charging",
    "preconditioning_max": "set_preconditioning_max",
    "preconditioning_max_off": "set_preconditioning_max",
    "seat_cooler": "remote_seat_cooler_request",
    "charge_max_range": "charge_max_range",
    "charge_standard": "charge_standard",
    # Missing commands from Tesla Fleet API
    "add_charge_schedule": "add_charge_schedule",
    "add_precondition_schedule": "add_precondition_schedule",
    "adjust_volume": "adjust_volume",
    "cancel_software_update": "cancel_software_update",
    "clear_pin_to_drive_admin": "clear_pin_to_drive_admin",
    "erase_user_data": "erase_user_data",
    "guest_mode": "guest_mode",
    "navigation_gps_request": "navigation_gps_request",
    "navigation_sc_request": "navigation_sc_request",
    "navigation_waypoints_request": "navigation_waypoints_request",
    "parental_controls_activate": "parental_controls_activate",
    "parental_controls_clear_pin_admin": "parental_controls_clear_pin_admin",
    "parental_controls_deactivate": "parental_controls_deactivate",
    "parental_controls_enable_setting": "parental_controls_enable_setting",
    "parental_controls_set_speed_limit": "parental_controls_set_speed_limit",
    "remote_auto_seat_climate_request": "remote_auto_seat_climate_request",
    "remote_auto_steering_wheel_heat_climate_request": "remote_auto_steering_wheel_heat_climate_request",
    "remote_steering_wheel_heat_level_request": "remote_steering_wheel_heat_level_request",
    "remove_charge_schedule": "remove_charge_schedule",
    "remove_precondition_schedule": "remove_precondition_schedule",
    "reset_pin_to_drive_pin": "reset_pin_to_drive_pin",
    "reset_valet_pin": "reset_valet_pin",
    "schedule_software_update": "schedule_software_update",
    "set_bioweapon_mode": "set_bioweapon_mode",
    "set_cabin_overheat_protection": "set_cabin_overheat_protection",
    "set_climate_keeper_mode": "set_climate_keeper_mode",
    "set_cop_temp": "set_cop_temp",
    "set_pin_to_drive": "set_pin_to_drive",
    "set_scheduled_departure": "set_scheduled_departure",
    "set_vehicle_name": "set_vehicle_name",
    "speed_limit_activate": "speed_limit_activate",
    "speed_limit_clear_pin": "speed_limit_clear_pin",
    "speed_limit_clear_pin_admin": "speed_limit_clear_pin_admin",
    "speed_limit_deactivate": "speed_limit_deactivate",
    "upcoming_calendar_entries": "upcoming_calendar_entries",
}

# Command bodies
COMMAND_BODIES = {
    "trunk_rear": {"which_trunk": "rear"},
    "trunk_front": {"which_trunk": "front"},
    "sentry_on": {"on": True},
    "sentry_off": {"on": False},
    "window_vent": {"command": "vent", "lat": 0, "lon": 0},
    "window_close": {"command": "close", "lat": 0, "lon": 0},
    "valet_mode_on": {"on": True},
    "valet_mode_off": {"on": False},
    "homelink": {"lat": 0, "lon": 0},
    "preconditioning_max": {"on": True, "manual_override": True},
    "preconditioning_max_off": {"on": False, "manual_override": False},
    "open_tonneau": {},
    "close_tonneau": {},
    # Missing command bodies (match Tesla Vehicle Command Proxy params)
    "add_charge_schedule": {"start_time": "00:00", "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
    "add_precondition_schedule": {"start_time": "07:00", "days": ["monday", "tuesday", "wednesday", "thursday", "friday"], "target_temp": 21},
    "adjust_volume": {"volume": 5},
    "cancel_software_update": {},
    "clear_pin_to_drive_admin": {},
    "erase_user_data": {},
    "guest_mode": {"enable": True},
    "navigation_gps_request": {"lat": 0, "lon": 0, "order": 1},
    "navigation_sc_request": {"id": 0},
    "navigation_waypoints_request": {"waypoints": []},
    "parental_controls_activate": {"pin": "0000"},
    "parental_controls_clear_pin_admin": {},
    "parental_controls_deactivate": {"pin": "0000"},
    "parental_controls_enable_setting": {"setting": "SpeedLimit", "enable": True},
    "parental_controls_set_speed_limit": {"speed_limit": 100},
    "remote_auto_seat_climate_request": {"auto_seat_position": 1, "auto_climate_on": True},
    "remote_auto_steering_wheel_heat_climate_request": {"on": True},
    "remote_steering_wheel_heat_level_request": {"level": 1},
    "remove_charge_schedule": {"id": 0},
    "remove_precondition_schedule": {"id": 0},
    "reset_pin_to_drive_pin": {},
    "reset_valet_pin": {},
    "schedule_software_update": {"offset_sec": 0},
    "set_bioweapon_mode": {"on": True, "manual_override": True},
    "set_cabin_overheat_protection": {"on": True, "fan_only": False},
    "set_climate_keeper_mode": {"climate_keeper_mode": 1},
    "set_cop_temp": {"cop_temp": 1},
    "set_pin_to_drive": {"on": True, "password": "0000"},
    "set_scheduled_departure": {"enable": True, "departure_time": 420, "end_off_peak_time": 480},
    "set_vehicle_name": {"vehicle_name": "My Tesla"},
    "speed_limit_activate": {"pin": "0000"},
    "speed_limit_clear_pin": {"pin": "0000"},
    "speed_limit_clear_pin_admin": {},
    "speed_limit_deactivate": {"pin": "0000"},
    "upcoming_calendar_entries": {},
    "charge_max_range": {},
    "charge_standard": {},
}

# Temperature limits (Celsius)
MIN_TEMP = 15.0
MAX_TEMP = 28.0
TEMP_STEP = 0.5

# Charge limit
CHARGE_LIMIT_MIN = 50
CHARGE_LIMIT_MAX = 100
CHARGE_LIMIT_STEP = 5

# Seat heater levels
SEAT_HEATER_OFF = 0
SEAT_HEATER_LOW = 1
SEAT_HEATER_MEDIUM = 2
SEAT_HEATER_HIGH = 3

# Fleet Telemetry settings
DEFAULT_TELEMETRY_PORT = 4443
MIN_TELEMETRY_PORT = 1
MAX_TELEMETRY_PORT = 65535

# Proxy binary names by platform
PROXY_BINARIES = {
    "linux_x86_64": "tesla-http-proxy-linux-amd64",
    "linux_aarch64": "tesla-http-proxy-linux-arm64",
    "darwin_x86_64": "tesla-http-proxy-darwin-amd64",
    "darwin_arm64": "tesla-http-proxy-darwin-arm64",
    "win32": "tesla-http-proxy-windows-amd64.exe",
}

# GitHub releases
GITHUB_REPO = "teslamotors/vehicle-command"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"