"""Sensor entities for Tesla Vehicle Command."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeslaVehicleCommandCoordinator
from .entity import TeslaVehicleCommandEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class TeslaSensorEntityDescription(SensorEntityDescription):
    """Describes Tesla sensor entity."""

    value_path: str | None = None
    unit_path: str | None = None
    conversion: str | None = None
    value_map: dict[Any, str] | None = None
    default_value: Any = None


SENSOR_DESCRIPTIONS = [
    # Battery / Charging
    TeslaSensorEntityDescription(
        key="battery_level",
        name="Battery Level",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_path="charge_state.battery_level",
        icon="mdi:battery",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="battery_range",
        name="Battery Range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_path="charge_state.battery_range",
        conversion="mi_to_km",
        icon="mdi:road",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="ideal_battery_range",
        name="Ideal Battery Range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_path="charge_state.ideal_battery_range",
        conversion="mi_to_km",
        icon="mdi:road-variant",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charging_state",
        name="Charging State",
        device_class=SensorDeviceClass.ENUM,
        options=["Charging", "Complete", "Disconnected", "Stopped", "NoPower", "Starting"],
        value_path="charge_state.charging_state",
        icon="mdi:ev-station",
        default_value="Disconnected",
    ),
    TeslaSensorEntityDescription(
        key="charge_limit",
        name="Charge Limit",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_path="charge_state.charge_limit_soc",
        icon="mdi:battery-charging-50",
        entity_category=EntityCategory.DIAGNOSTIC,
        default_value=90,
    ),
    TeslaSensorEntityDescription(
        key="charge_current",
        name="Charge Current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="charge_state.charge_current_request",
        icon="mdi:current-ac",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charge_power",
        name="Charge Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        value_path="charge_state.charger_power",
        icon="mdi:flash",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charge_energy_added",
        name="Charge Energy Added",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_path="charge_state.charge_energy_added",
        icon="mdi:battery-plus",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="time_to_full_charge",
        name="Time to Full Charge",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        value_path="charge_state.time_to_full_charge",
        icon="mdi:timer",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charger_voltage",
        name="Charger Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_path="charge_state.charger_voltage",
        icon="mdi:flash",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charger_actual_current",
        name="Charger Actual Current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="charge_state.charger_actual_current",
        icon="mdi:current-ac",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="est_battery_range",
        name="Estimated Battery Range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_path="charge_state.est_battery_range",
        conversion="mi_to_km",
        icon="mdi:road",
    ),
    TeslaSensorEntityDescription(
        key="usable_battery_level",
        name="Usable Battery Level",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_path="charge_state.usable_battery_level",
        icon="mdi:battery",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charge_port_door_open",
        name="Charge Port Door Open",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="charge_state.charge_port_door_open",
        icon="mdi:ev-plug-type2",
        default_value="Closed",
    ),
    TeslaSensorEntityDescription(
        key="charge_current_request",
        name="Charge Current Request",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="charge_state.charge_current_request",
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charge_current_request_max",
        name="Charge Current Request Max",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="charge_state.charge_current_request_max",
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charge_enable_request",
        name="Charge Enable Request",
        device_class=SensorDeviceClass.ENUM,
        options=["Enabled", "Disabled"],
        value_path="charge_state.charge_enable_request",
        icon="mdi:battery-charging",
        entity_category=EntityCategory.DIAGNOSTIC,
        default_value="Disabled",
    ),
    TeslaSensorEntityDescription(
        key="charge_port_latch",
        name="Charge Port Latch",
        device_class=SensorDeviceClass.ENUM,
        options=["Engaged", "Disengaged", "Unknown", "ChargePortLatchEngaged"],
        value_path="charge_state.charge_port_latch",
        icon="mdi:ev-plug-type2",
        default_value="Unknown",
    ),
    TeslaSensorEntityDescription(
        key="charge_rate",
        name="Charge Rate",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value_path="charge_state.charge_rate",
        conversion="mi_to_km",
        icon="mdi:speedometer",
        default_value=0,
    ),
    TeslaSensorEntityDescription(
        key="charger_phases",
        name="Charger Phases",
        device_class=SensorDeviceClass.ENUM,
        options=["1", "2", "3"],
        value_path="charge_state.charger_phases",
        icon="mdi:flash",
        default_value="1",
    ),
    TeslaSensorEntityDescription(
        key="charging_cable_type",
        name="Charging Cable Type",
        device_class=SensorDeviceClass.ENUM,
        options=["Unknown", "IEC", "SAE", "GB_AC", "GB_DC", "SNA"],
        value_path="charge_state.conn_charge_cable",
        icon="mdi:ev-plug-type2",
        default_value="Unknown",
    ),
    TeslaSensorEntityDescription(
        key="fast_charger_present",
        name="Fast Charger Present",
        device_class=SensorDeviceClass.ENUM,
        options=["Present", "Not Present"],
        value_path="charge_state.fast_charger_present",
        icon="mdi:ev-station",
        default_value="Not Present",
    ),
    TeslaSensorEntityDescription(
        key="fast_charger_type",
        name="Fast Charger Type",
        device_class=SensorDeviceClass.ENUM,
        options=["Unknown", "Supercharger", "CHAdeMO", "GB", "ACSingleWireCAN", "Combo", "MCSingleWireCAN", "Other", "SNA"],
        value_path="charge_state.fast_charger_type",
        icon="mdi:ev-station",
        default_value="Unknown",
    ),
    TeslaSensorEntityDescription(
        key="not_enough_power_to_heat",
        name="Not Enough Power to Heat",
        device_class=SensorDeviceClass.ENUM,
        options=["Yes", "No"],
        value_path="charge_state.not_enough_power_to_heat",
        icon="mdi:battery-alert",
        default_value="No",
    ),
    TeslaSensorEntityDescription(
        key="scheduled_charging_mode",
        name="Scheduled Charging Mode",
        device_class=SensorDeviceClass.ENUM,
        options=["off", "start_at", "depart_at"],
        value_path="charge_state.scheduled_charging_mode",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        default_value="off",
    ),
    TeslaSensorEntityDescription(
        key="scheduled_charging_pending",
        name="Scheduled Charging Pending",
        device_class=SensorDeviceClass.ENUM,
        options=["Pending", "Not Pending"],
        value_path="charge_state.scheduled_charging_pending",
        icon="mdi:calendar-clock",
        default_value="Not Pending",
    ),
    TeslaSensorEntityDescription(
        key="scheduled_charging_start_time",
        name="Scheduled Charging Start Time",
        value_path="charge_state.scheduled_charging_start_time",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        default_value="00:00",
    ),
    TeslaSensorEntityDescription(
        key="supercharger_session_trip_planner",
        name="Supercharger Session Trip Planner",
        device_class=SensorDeviceClass.ENUM,
        options=["Yes", "No"],
        value_path="charge_state.supercharger_session_trip_planner",
        icon="mdi:ev-station",
        default_value="No",
    ),
    TeslaSensorEntityDescription(
        key="battery_heater_on",
        name="Battery Heater On",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.battery_heater_on",
        icon="mdi:battery-charging",
    ),
    TeslaSensorEntityDescription(
        key="energy_remaining",
        name="Energy Remaining",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_path="charge_state.energy_remaining",
        icon="mdi:battery",
    ),
    TeslaSensorEntityDescription(
        key="estimated_hours_to_charge_termination",
        name="Estimated Hours to Charge Termination",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="h",
        value_path="charge_state.estimated_hours_to_charge_termination",
        icon="mdi:timer",
    ),
    TeslaSensorEntityDescription(
        key="lifetime_energy_used",
        name="Lifetime Energy Used",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_path="charge_state.lifetime_energy_used",
        icon="mdi:battery",
    ),
    TeslaSensorEntityDescription(
        key="pack_current",
        name="Pack Current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="charge_state.pack_current",
        icon="mdi:current-dc",
    ),
    TeslaSensorEntityDescription(
        key="pack_voltage",
        name="Pack Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_path="charge_state.pack_voltage",
        icon="mdi:flash",
    ),
    TeslaSensorEntityDescription(
        key="module_temp_max",
        name="Module Temp Max",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="charge_state.module_temp_max",
        icon="mdi:thermometer",
    ),
    TeslaSensorEntityDescription(
        key="module_temp_min",
        name="Module Temp Min",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="charge_state.module_temp_min",
        icon="mdi:thermometer",
    ),
    TeslaSensorEntityDescription(
        key="brick_voltage_max",
        name="Brick Voltage Max",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        value_path="charge_state.brick_voltage_max",
        icon="mdi:flash",
    ),
    TeslaSensorEntityDescription(
        key="brick_voltage_min",
        name="Brick Voltage Min",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        value_path="charge_state.brick_voltage_min",
        icon="mdi:flash",
    ),
    TeslaSensorEntityDescription(
        key="brick_voltage_imbalance",
        name="Brick Voltage Imbalance",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        value_path="charge_state.brick_voltage_imbalance",
        icon="mdi:flash-alert",
    ),
    TeslaSensorEntityDescription(
        key="battery_balance_score",
        name="Battery Balance Score",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_path="charge_state.battery_balance_score",
        icon="mdi:battery-heart-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="num_brick_voltage_max",
        name="Num Brick Voltage Max",
        value_path="charge_state.num_brick_voltage_max",
        icon="mdi:counter",
    ),
    TeslaSensorEntityDescription(
        key="num_brick_voltage_min",
        name="Num Brick Voltage Min",
        value_path="charge_state.num_brick_voltage_min",
        icon="mdi:counter",
    ),
    TeslaSensorEntityDescription(
        key="num_module_temp_max",
        name="Num Module Temp Max",
        value_path="charge_state.num_module_temp_max",
        icon="mdi:counter",
    ),
    TeslaSensorEntityDescription(
        key="num_module_temp_min",
        name="Num Module Temp Min",
        value_path="charge_state.num_module_temp_min",
        icon="mdi:counter",
    ),
    TeslaSensorEntityDescription(
        key="dcdc_enable",
        name="DCDC Enable",
        device_class=SensorDeviceClass.ENUM,
        options=["Enabled", "Disabled"],
        value_path="charge_state.dcdc_enable",
        icon="mdi:current-dc",
    ),
    TeslaSensorEntityDescription(
        key="powershare_hours_left",
        name="Powershare Hours Left",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="h",
        value_path="charge_state.powershare_hours_left",
        icon="mdi:timer",
    ),
    TeslaSensorEntityDescription(
        key="powershare_instantaneous_power_kw",
        name="Powershare Instantaneous Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        value_path="charge_state.powershare_instantaneous_power_kw",
        icon="mdi:flash",
    ),
    TeslaSensorEntityDescription(
        key="powershare_status",
        name="Powershare Status",
        device_class=SensorDeviceClass.ENUM,
        options=["Inactive", "Active", "Error"],
        value_path="charge_state.powershare_status",
        icon="mdi:ev-station",
    ),
    TeslaSensorEntityDescription(
        key="powershare_stop_reason",
        name="Powershare Stop Reason",
        device_class=SensorDeviceClass.ENUM,
        options=["None", "User Stop", "Error", "Complete"],
        value_path="charge_state.powershare_stop_reason",
        icon="mdi:stop-circle",
    ),
    TeslaSensorEntityDescription(
        key="powershare_type",
        name="Powershare Type",
        device_class=SensorDeviceClass.ENUM,
        options=["V2L", "V2H", "V2G"],
        value_path="charge_state.powershare_type",
        icon="mdi:ev-station",
    ),
    TeslaSensorEntityDescription(
        key="preconditioning_max",
        name="Preconditioning Max",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.preconditioning_max",
        icon="mdi:fan",
    ),

    # Climate
    TeslaSensorEntityDescription(
        key="inside_temp",
        name="Inside Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="climate_state.inside_temp",
        icon="mdi:thermometer",
    ),
    TeslaSensorEntityDescription(
        key="outside_temp",
        name="Outside Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="climate_state.outside_temp",
        icon="mdi:thermometer-lines",
    ),
    TeslaSensorEntityDescription(
        key="driver_temp_setting",
        name="Driver Temperature Setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="climate_state.driver_temp_setting",
        icon="mdi:thermostat",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="passenger_temp_setting",
        name="Passenger Temperature Setting",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="climate_state.passenger_temp_setting",
        icon="mdi:thermostat",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="is_climate_on",
        name="Climate On",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.is_climate_on",
        icon="mdi:fan",
    ),
    TeslaSensorEntityDescription(
        key="fan_status",
        name="Fan Status",
        value_path="climate_state.fan_status",
        icon="mdi:fan",
    ),
    TeslaSensorEntityDescription(
        key="is_preconditioning",
        name="Preconditioning",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.is_preconditioning",
        icon="mdi:fan",
    ),
    TeslaSensorEntityDescription(
        key="climate_keeper_mode",
        name="Climate Keeper Mode",
        device_class=SensorDeviceClass.ENUM,
        options=["off", "dog", "camp", "on"],
        value_path="climate_state.climate_keeper_mode",
        icon="mdi:fan",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="defrost_mode",
        name="Defrost Mode",
        device_class=SensorDeviceClass.ENUM,
        options=["Unknown", "Off", "Normal", "Max", "AutoDefog"],
        value_path="climate_state.defrost_mode",
        icon="mdi:car-defrost-front",
    ),
    TeslaSensorEntityDescription(
        key="seat_heater_left",
        name="Seat Heater Left",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "Low", "Medium", "High"],
        value_path="climate_state.seat_heater_left",
        icon="mdi:seat-heater",
    ),
    TeslaSensorEntityDescription(
        key="seat_heater_right",
        name="Seat Heater Right",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "Low", "Medium", "High"],
        value_path="climate_state.seat_heater_right",
        icon="mdi:seat-heater",
    ),
    TeslaSensorEntityDescription(
        key="seat_heater_rear_left",
        name="Rear Seat Heater Left",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "Low", "Medium", "High"],
        value_path="climate_state.seat_heater_rear_left",
        icon="mdi:seat-heater",
    ),
    TeslaSensorEntityDescription(
        key="seat_heater_rear_right",
        name="Rear Seat Heater Right",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "Low", "Medium", "High"],
        value_path="climate_state.seat_heater_rear_right",
        icon="mdi:seat-heater",
    ),
    TeslaSensorEntityDescription(
        key="seat_heater_rear_center",
        name="Rear Seat Heater Center",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "Low", "Medium", "High"],
        value_path="climate_state.seat_heater_rear_center",
        icon="mdi:seat-heater",
    ),
    TeslaSensorEntityDescription(
        key="steering_wheel_heater",
        name="Steering Wheel Heater",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.steering_wheel_heater",
        icon="mdi:steering",
    ),
    TeslaSensorEntityDescription(
        key="steering_wheel_heat_level",
        name="Steering Wheel Heat Level",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "Low", "Medium", "High"],
        value_path="climate_state.steering_wheel_heat_level",
        icon="mdi:steering",
    ),
    TeslaSensorEntityDescription(
        key="auto_steering_wheel_heat",
        name="Auto Steering Wheel Heat",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.auto_steering_wheel_heat",
        icon="mdi:steering",
    ),
    TeslaSensorEntityDescription(
        key="seat_cooler_left",
        name="Seat Cooler Left",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "Low", "Medium", "High"],
        value_path="climate_state.seat_fan_front_left",
        icon="mdi:seat-cooler",
    ),
    TeslaSensorEntityDescription(
        key="seat_cooler_right",
        name="Seat Cooler Right",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "Low", "Medium", "High"],
        value_path="climate_state.seat_fan_front_right",
        icon="mdi:seat-cooler",
    ),
    TeslaSensorEntityDescription(
        key="wiper_blade_heater",
        name="Wiper Blade Heater",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.wiper_blade_heater",
        icon="mdi:wiper",
    ),
    TeslaSensorEntityDescription(
        key="is_rear_defroster_on",
        name="Rear Defroster",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.is_rear_defroster_on",
        icon="mdi:car-defrost-rear",
    ),
    TeslaSensorEntityDescription(
        key="auto_seat_climate_left",
        name="Auto Seat Climate Left",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.auto_seat_climate_left",
        icon="mdi:seat-heater",
    ),
    TeslaSensorEntityDescription(
        key="auto_seat_climate_right",
        name="Auto Seat Climate Right",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.auto_seat_climate_right",
        icon="mdi:seat-heater",
    ),
    TeslaSensorEntityDescription(
        key="cabin_overheat_protection",
        name="Cabin Overheat Protection",
        device_class=SensorDeviceClass.ENUM,
        options=["Off", "On", "Fan Only"],
        value_path="climate_state.cabin_overheat_protection",
        icon="mdi:thermometer-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="cop_activation_temp",
        name="COP Activation Temperature",
        device_class=SensorDeviceClass.ENUM,
        options=["Low", "Medium", "High"],
        value_path="climate_state.cop_activation_temperature",
        icon="mdi:thermometer-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="defrost_for_preconditioning",
        name="Defrost for Preconditioning",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="climate_state.defrost_for_preconditioning",
        icon="mdi:wiper",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),

    # Drive / Location
    TeslaSensorEntityDescription(
        key="odometer",
        name="Odometer",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_path="vehicle_state.odometer",
        conversion="mi_to_km",
        icon="mdi:counter",
    ),
    TeslaSensorEntityDescription(
        key="speed",
        name="Speed",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value_path="drive_state.speed",
        conversion="mph_to_kph",
        icon="mdi:speedometer",
    ),
    TeslaSensorEntityDescription(
        key="latitude",
        name="Latitude",
        value_path="drive_state.latitude",
        icon="mdi:map-marker",
    ),
    TeslaSensorEntityDescription(
        key="longitude",
        name="Longitude",
        value_path="drive_state.longitude",
        icon="mdi:map-marker",
    ),
    TeslaSensorEntityDescription(
        key="heading",
        name="Heading",
        native_unit_of_measurement="°",
        value_path="drive_state.heading",
        icon="mdi:compass",
    ),
    TeslaSensorEntityDescription(
        key="shift_state",
        name="Shift State",
        device_class=SensorDeviceClass.ENUM,
        options=["Driving", "Neutral", "Reverse", "Parking"],
        value_path="drive_state.shift_state",
        icon="mdi:car-shift-pattern",
    ),

    # Vehicle State
    TeslaSensorEntityDescription(
        key="locked",
        name="Locked",
        device_class=SensorDeviceClass.ENUM,
        options=["Locked", "Unlocked"],
        value_path="vehicle_state.locked",
        icon="mdi:lock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="valet_mode",
        name="Valet Mode",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="vehicle_state.valet_mode",
        icon="mdi:account-key",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="sentry_mode",
        name="Sentry Mode",
        device_class=SensorDeviceClass.ENUM,
        options=["On", "Off"],
        value_path="vehicle_state.sentry_mode",
        icon="mdi:shield-car",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="fd_window",
        name="Front Driver Window",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.fd_window",
        icon="mdi:car-door",
    ),
    TeslaSensorEntityDescription(
        key="fp_window",
        name="Front Passenger Window",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.fp_window",
        icon="mdi:car-door",
    ),
    TeslaSensorEntityDescription(
        key="rd_window",
        name="Rear Driver Window",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.rd_window",
        icon="mdi:car-door",
    ),
    TeslaSensorEntityDescription(
        key="rp_window",
        name="Rear Passenger Window",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.rp_window",
        icon="mdi:car-door",
    ),
    TeslaSensorEntityDescription(
        key="door_driver_front",
        name="Driver Front Door",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.df",
        icon="mdi:car-door",
    ),
    TeslaSensorEntityDescription(
        key="door_driver_rear",
        name="Driver Rear Door",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.dr",
        icon="mdi:car-door",
    ),
    TeslaSensorEntityDescription(
        key="door_passenger_front",
        name="Passenger Front Door",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.pf",
        icon="mdi:car-door",
    ),
    TeslaSensorEntityDescription(
        key="door_passenger_rear",
        name="Passenger Rear Door",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.pr",
        icon="mdi:car-door",
    ),
    TeslaSensorEntityDescription(
        key="door_trunk_front",
        name="Front Trunk",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.ft",
        icon="mdi:car-front",
    ),
    TeslaSensorEntityDescription(
        key="door_trunk_rear",
        name="Rear Trunk",
        device_class=SensorDeviceClass.ENUM,
        options=["Open", "Closed"],
        value_path="vehicle_state.rt",
        icon="mdi:car-back",
    ),
    TeslaSensorEntityDescription(
        key="is_user_present",
        name="Driver Present",
        device_class=SensorDeviceClass.ENUM,
        options=["Present", "Not Present"],
        value_path="vehicle_state.is_user_present",
        icon="mdi:account",
    ),
    TeslaSensorEntityDescription(
        key="car_version",
        name="Software Version",
        value_path="vehicle_state.car_version",
        icon="mdi:package-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="tpms_fl",
        name="Front Left Tire Pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        value_path="vehicle_state.tpms_pressure_fl",
        icon="mdi:car-tire-alert",
    ),
    TeslaSensorEntityDescription(
        key="tpms_fr",
        name="Front Right Tire Pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        value_path="vehicle_state.tpms_pressure_fr",
        icon="mdi:car-tire-alert",
    ),
    TeslaSensorEntityDescription(
        key="tpms_rl",
        name="Rear Left Tire Pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        value_path="vehicle_state.tpms_pressure_rl",
        icon="mdi:car-tire-alert",
    ),
    TeslaSensorEntityDescription(
        key="tpms_rr",
        name="Rear Right Tire Pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        value_path="vehicle_state.tpms_pressure_rr",
        icon="mdi:car-tire-alert",
    ),

    # Powertrain (Diagnostic)
    TeslaSensorEntityDescription(
        key="di_state_f",
        name="Drive Inverter State Front",
        value_path="powertrain.di_state_f",
        icon="mdi:car-electric",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_state_r",
        name="Drive Inverter State Rear",
        value_path="powertrain.di_state_r",
        icon="mdi:car-electric",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_state_rel",
        name="Drive Inverter State Rear Left",
        value_path="powertrain.di_state_rel",
        icon="mdi:car-electric",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_state_rer",
        name="Drive Inverter State Rear Right",
        value_path="powertrain.di_state_rer",
        icon="mdi:car-electric",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_axle_speed_f",
        name="Axle Speed Front",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value_path="powertrain.di_axle_speed_f",
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_axle_speed_r",
        name="Axle Speed Rear",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value_path="powertrain.di_axle_speed_r",
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_axle_speed_rel",
        name="Axle Speed Rear Left",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value_path="powertrain.di_axle_speed_rel",
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_axle_speed_rer",
        name="Axle Speed Rear Right",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        value_path="powertrain.di_axle_speed_rer",
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_motor_current_f",
        name="Motor Current Front",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="powertrain.di_motor_current_f",
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_motor_current_r",
        name="Motor Current Rear",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="powertrain.di_motor_current_r",
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_motor_current_rel",
        name="Motor Current Rear Left",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="powertrain.di_motor_current_rel",
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_motor_current_rer",
        name="Motor Current Rear Right",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="powertrain.di_motor_current_rer",
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_torque_actual_f",
        name="Torque Actual Front",
        value_path="powertrain.di_torque_actual_f",
        icon="mdi:rotate-3d",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_torque_actual_r",
        name="Torque Actual Rear",
        value_path="powertrain.di_torque_actual_r",
        icon="mdi:rotate-3d",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_torque_actual_rel",
        name="Torque Actual Rear Left",
        value_path="powertrain.di_torque_actual_rel",
        icon="mdi:rotate-3d",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_torque_actual_rer",
        name="Torque Actual Rear Right",
        value_path="powertrain.di_torque_actual_rer",
        icon="mdi:rotate-3d",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_torquemotor",
        name="Torque Motor",
        value_path="powertrain.di_torquemotor",
        icon="mdi:rotate-3d",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_slave_torque_cmd",
        name="Slave Torque Command",
        value_path="powertrain.di_slave_torque_cmd",
        icon="mdi:rotate-3d",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_inverter_tf",
        name="Inverter Temp Front",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_inverter_tf",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_inverter_tr",
        name="Inverter Temp Rear",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_inverter_tr",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_inverter_trel",
        name="Inverter Temp Rear Left",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_inverter_trel",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_inverter_trer",
        name="Inverter Temp Rear Right",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_inverter_trer",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_heatsink_tf",
        name="Heatsink Temp Front",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_heatsink_tf",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_heatsink_tr",
        name="Heatsink Temp Rear",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_heatsink_tr",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_heatsink_trel",
        name="Heatsink Temp Rear Left",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_heatsink_trel",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_heatsink_trer",
        name="Heatsink Temp Rear Right",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_heatsink_trer",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_stator_temp_f",
        name="Stator Temp Front",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_stator_temp_f",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_stator_temp_r",
        name="Stator Temp Rear",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_stator_temp_r",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_stator_temp_rel",
        name="Stator Temp Rear Left",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_stator_temp_rel",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_stator_temp_rer",
        name="Stator Temp Rear Right",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_path="powertrain.di_stator_temp_rer",
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_vbat_f",
        name="Battery Voltage Front",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_path="powertrain.di_vbat_f",
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_vbat_r",
        name="Battery Voltage Rear",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_path="powertrain.di_vbat_r",
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_vbat_rel",
        name="Battery Voltage Rear Left",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_path="powertrain.di_vbat_rel",
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="di_vbat_rer",
        name="Battery Voltage Rear Right",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_path="powertrain.di_vbat_rer",
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="hvil_status",
        name="HVIL Status",
        value_path="powertrain.hvil_status",
        icon="mdi:shield-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),

    # Vehicle Config (Diagnostic)
    TeslaSensorEntityDescription(
        key="car_type",
        name="Car Type",
        value_path="vehicle_config.car_type",
        icon="mdi:car",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="efficiency_package",
        name="Efficiency Package",
        value_path="vehicle_config.efficiency_package",
        icon="mdi:battery-heart",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="eu_vehicle",
        name="Europe Vehicle",
        device_class=SensorDeviceClass.ENUM,
        options=["True", "False"],
        value_path="vehicle_config.eu_vehicle",
        icon="mdi:earth",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="exterior_color",
        name="Exterior Color",
        value_path="vehicle_config.exterior_color",
        icon="mdi:palette",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="offroad_lightbar_present",
        name="Offroad Lightbar Present",
        device_class=SensorDeviceClass.ENUM,
        options=["True", "False"],
        value_path="vehicle_config.offroad_lightbar_present",
        icon="mdi:lightbar",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="rear_seat_heaters",
        name="Rear Seat Heaters",
        value_path="vehicle_config.rear_seat_heaters",
        icon="mdi:seat-heater",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="rhd",
        name="Right Hand Drive",
        device_class=SensorDeviceClass.ENUM,
        options=["True", "False"],
        value_path="vehicle_config.rhd",
        icon="mdi:steering",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="roof_color",
        name="Roof Color",
        value_path="vehicle_config.roof_color",
        icon="mdi:palette",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="sun_roof_installed",
        name="Sunroof Installed",
        device_class=SensorDeviceClass.ENUM,
        options=["True", "False"],
        value_path="vehicle_config.sun_roof_installed",
        icon="mdi:sun-roof",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="trim_badging",
        name="Trim Badging",
        value_path="vehicle_config.trim_badging",
        icon="mdi:car",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="wheel_type",
        name="Wheel Type",
        value_path="vehicle_config.wheel_type",
        icon="mdi:car-tire",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TeslaSensorEntityDescription(
        key="charge_port_type",
        name="Charge Port Type",
        value_path="vehicle_config.charge_port_type",
        icon="mdi:ev-plug-type2",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: TeslaVehicleCommandCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Clean up orphaned entities from previous versions
    entity_registry = er.async_get(hass)
    current_keys = {desc.key for desc in SENSOR_DESCRIPTIONS}
    current_keys.add("telemetry_status")  # TeslaTelemetryStatusSensor

    for vehicle in coordinator.vehicles:
        vin = vehicle["vin"]
        # Remove entities that are no longer defined
        for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
            if entity_entry.unique_id.startswith(f"{vin}_"):
                sensor_key = entity_entry.unique_id[len(vin) + 1:]
                if sensor_key not in current_keys:
                    _LOGGER.info("Removing orphaned sensor entity: %s", entity_entry.entity_id)
                    entity_registry.async_remove(entity_entry.entity_id)

    entities = []
    for vehicle in coordinator.vehicles:
        vin = vehicle["vin"]
        entities.append(TeslaTelemetryStatusSensor(coordinator, vin, vehicle["name"]))
        for description in SENSOR_DESCRIPTIONS:
            entities.append(TeslaSensorEntity(coordinator, vin, vehicle["name"], description))

    async_add_entities(entities)


class TeslaTelemetryStatusSensor(TeslaVehicleCommandEntity, SensorEntity):
    """Diagnostic sensor reporting Fleet Telemetry data flow for a vehicle."""

    _attr_icon = "mdi:transmission-tower"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_translation_key = "telemetry_status"

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
    ) -> None:
        """Initialize the telemetry status sensor."""
        super().__init__(coordinator, vin, vehicle_name)
        self._attr_unique_id = f"{vin}_telemetry_status"

    @property
    def native_value(self) -> str:
        """Return whether telemetry records are arriving for this vehicle."""
        return self.coordinator.get_telemetry_status(self.vin)["state"]

    @property
    def available(self) -> bool:
        """Expose receiver state even if the fallback Fleet API poll fails."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the most recent telemetry record diagnostics."""
        metadata = self.coordinator.get_telemetry_status(self.vin)
        last_received = metadata.get("last_received")
        return {
            "last_received": last_received.isoformat() if last_received else None,
            "received_fields": metadata.get("received_fields", []),
            "processed_fields": metadata.get("processed_fields", []),
            "unprocessed_fields": sorted(
                set(metadata.get("received_fields", []))
                - set(metadata.get("processed_fields", []))
            ),
        }


class TeslaSensorEntity(TeslaVehicleCommandEntity, SensorEntity):
    """Sensor entity for Tesla vehicle data."""

    entity_description: TeslaSensorEntityDescription

    def __init__(
        self,
        coordinator: TeslaVehicleCommandCoordinator,
        vin: str,
        vehicle_name: str,
        description: TeslaSensorEntityDescription,
    ) -> None:
        """Initialize the sensor entity."""
        super().__init__(coordinator, vin, vehicle_name)
        self.entity_description = description
        self._attr_unique_id = f"{vin}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        vehicle_data = self.coordinator.data.get(self.vin, {})
        response = vehicle_data.get("response", {})

        # Navigate the value path
        value = response
        path = self.entity_description.value_path
        if path:
            for key in path.split("."):
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    return None

        if value is None:
            if self.entity_description.default_value is not None:
                return self.entity_description.default_value
            if self.entity_description.key == "shift_state":
                return "Parking"
            return None

        if self.entity_description.value_map and value in self.entity_description.value_map:
            return self.entity_description.value_map[value]

        if self.entity_description.key in {
            "fd_window",
            "fp_window",
            "rd_window",
            "rp_window",
            "ft",
            "trunk",
        }:
            return "Closed" if value == 0 else "Open"

        # Apply conversions
        conversion = self.entity_description.conversion
        if conversion == "c_to_f" and isinstance(value, (int, float)):
            return round(value * 9 / 5 + 32, 1)
        elif conversion == "mi_to_km" and isinstance(value, (int, float)):
            return round(value * 1.609344, 1)
        elif conversion == "mph_to_kph" and isinstance(value, (int, float)):
            return round(value * 1.609344, 1)
        elif conversion == "psi_to_bar" and isinstance(value, (int, float)):
            return round(value * 0.0689476, 2)

        # Handle boolean / level enums using declared options
        if self.entity_description.device_class == SensorDeviceClass.ENUM:
            options = self.entity_description.options or []
            if isinstance(value, bool):
                if len(options) >= 2:
                    return options[0] if value else options[1]
                return "On" if value else "Off"
            if isinstance(value, (int, float)) and options == ["Off", "Low", "Medium", "High"]:
                level = int(value)
                if 0 <= level < len(options):
                    return options[level]
            shift_states = {"D": "Driving", "N": "Neutral", "R": "Reverse", "P": "Parking"}
            mapped = shift_states.get(str(value))
            if mapped:
                return mapped
            text = str(value)
            if text in options:
                return text
            # Handle raw Fleet API enum values (e.g., "ScheduledChargingModeOff", "ClimateKeeperModeStateOff", "DefrostModeStateOff")
            # ScheduledChargingMode: "ScheduledChargingModeOff" -> "off", "ScheduledChargingModeStartAt" -> "start_at"
            if text.startswith("ScheduledChargingMode"):
                text = text[len("ScheduledChargingMode"):]
                marker = text.rfind("State")
                if marker >= 0:
                    text = text[marker + len("State"):]
                text = text.strip().lower()
                if text in options:
                    return text
            # ClimateKeeperMode: "ClimateKeeperModeStateOff" -> "off", "ClimateKeeperModeStateDog" -> "dog"
            if text.startswith("ClimateKeeperMode"):
                text = text[len("ClimateKeeperMode"):]
                marker = text.rfind("State")
                if marker >= 0:
                    text = text[marker + len("State"):]
                text = text.strip().lower()
                if text in options:
                    return text
            # DefrostMode: "DefrostModeStateOff" -> "Off", "DefrostModeStateNormal" -> "Normal"
            if text.startswith("DefrostMode"):
                text = text[len("DefrostMode"):]
                marker = text.rfind("State")
                if marker >= 0:
                    text = text[marker + len("State"):]
                text = text.strip()
                if text in options:
                    return text
            # CabinOverheatProtectionMode: "CabinOverheatProtectionModeStateOff" -> "Off", "CabinOverheatProtectionModeStateOn" -> "On", "CabinOverheatProtectionModeStateFanOnly" -> "Fan Only"
            if text.startswith("CabinOverheatProtectionMode"):
                text = text[len("CabinOverheatProtectionMode"):]
                marker = text.rfind("State")
                if marker >= 0:
                    text = text[marker + len("State"):]
                text = text.strip()
                if text in options:
                    return text
            # ClimateOverheatProtectionTempLimit: "ClimateOverheatProtectionTempLimitLow" -> "Low", etc.
            if text.startswith("ClimateOverheatProtectionTempLimit"):
                text = text[len("ClimateOverheatProtectionTempLimit"):]
                text = text.strip()
                if text in options:
                    return text
            capitalized = text.capitalize()
            if capitalized in options:
                return capitalized
            return text

        return value