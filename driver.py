"""Unfolded Circle integration for Wi-Fi Dyson devices (SBR build).

Architecture mirrors heos-uc and sky-q-uc:
- Persistent local MQTT connection per configured device (libdyson handles TLS)
- Push-event driven state cache — no polling
- One-time cloud auth at setup time only; runtime is 100% LAN
- Each device → one climate entity (power/auto/fan-speed/temp) plus sensors
  (air quality, filter life) and switches (night, oscillation, monitoring)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import ucapi
from ucapi import climate, light, select, sensor, switch
from ucapi.api_definitions import (
    AbortDriverSetup,
    DriverSetupRequest,
    IntegrationSetupError,
    RequestUserInput,
    SetupAction,
    SetupComplete,
    SetupDriver,
    SetupError,
    UserDataResponse,
)

from src.cloud_setup import DysonCloudSetup, DysonSetupError
from src.const import (
    DIRECTION_PRESETS,
    DIRECTION_TO_CENTRE,
    OSC_OFF,
    OSC_PRESETS,
    OSC_PRESET_RANGES,
    caps_for,
    compose_oscillation,
    direction_from_centre,
    osc_preset_from_angles,
)
from src.dyson_client import DysonClient

logging.basicConfig(
    level=os.environ.get("DYSON_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_LOG = logging.getLogger("dyson")

CONFIG_FILENAME = "dyson_config.json"

_HVAC_OFF = "OFF"
_HVAC_FAN = "FAN"
_HVAC_AUTO = "AUTO"
_HVAC_HEAT = "HEAT"
_FAN_MODES = [str(i) for i in range(1, 11)]
_SPEED_AUTO = "Auto"
_SPEED_OPTIONS = [_SPEED_AUTO] + _FAN_MODES

_setup_state = DysonCloudSetup()
_clients: dict[str, DysonClient] = {}
# Module-level handle to the IntegrationAPI so command handlers can push
# optimistic state updates without waiting for the fan's MQTT confirmation.
_api_ref: ucapi.IntegrationAPI | None = None


def _config_path(api: ucapi.IntegrationAPI) -> Path:
    return Path(api.config_dir_path) / CONFIG_FILENAME


def _load_config(api: ucapi.IntegrationAPI) -> dict[str, Any]:
    p = _config_path(api)
    if not p.exists():
        return {"devices": []}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        _LOG.warning("config corrupt, starting fresh")
        return {"devices": []}


def _save_config(api: ucapi.IntegrationAPI, cfg: dict[str, Any]) -> None:
    p = _config_path(api)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _safe_id(serial: str) -> str:
    """UC entity IDs need to be filesystem-safe. Strip the hyphens."""
    return serial.replace("-", "_").lower()


def _climate_state(device) -> str:
    if device is None or not getattr(device, "is_on", False):
        return climate.States.OFF.value
    if getattr(device, "heat_mode_is_on", False):
        return climate.States.HEAT.value
    if getattr(device, "auto_mode", False):
        return climate.States.AUTO.value
    return climate.States.FAN.value


def _climate_attrs(device) -> dict[str, Any]:
    if device is None:
        return {climate.Attributes.STATE.value: climate.States.UNAVAILABLE.value}
    attrs: dict[str, Any] = {
        climate.Attributes.STATE.value: _climate_state(device),
    }
    temp = getattr(device, "temperature", None)
    if temp is not None:
        # libdyson returns temp in K * 10 in some versions, deg C in others.
        # Reading it as kelvin if > 200, else as C.
        try:
            t = float(temp)
            if t > 200:  # kelvin or kelvin*10
                t = t / 10.0 if t > 1000 else t
                t = t - 273.15
            attrs[climate.Attributes.CURRENT_TEMPERATURE.value] = round(t, 1)
        except (TypeError, ValueError):
            pass
    speed = getattr(device, "speed", None)
    if isinstance(speed, int) and 1 <= speed <= 10:
        attrs[climate.Attributes.FAN_MODE.value] = str(speed)
    # HotCool-only: surface the heat target so the thermostat slider works.
    target = getattr(device, "heat_target", None)
    if target is not None:
        try:
            attrs[climate.Attributes.TARGET_TEMPERATURE.value] = round(float(target), 1)
        except (TypeError, ValueError):
            pass
    return attrs


async def _climate_cmd(entity, cmd_id: str, params: dict | None) -> ucapi.StatusCodes:
    serial = entity.id.split("__")[0]
    _LOG.info("climate cmd %s id=%s params=%s", cmd_id, entity.id, params)
    client = _clients.get(serial)
    if client is None or client.device is None:
        _LOG.warning("climate cmd ignored — client not ready for %s", serial)
        return ucapi.StatusCodes.SERVICE_UNAVAILABLE
    d = client.device
    loop = asyncio.get_running_loop()

    def run(fn, *args):
        return loop.run_in_executor(None, lambda: fn(*args))

    try:
        if cmd_id == climate.Commands.ON.value:
            await run(d.turn_on)
        elif cmd_id == climate.Commands.OFF.value:
            await run(d.turn_off)
        elif cmd_id == climate.Commands.HVAC_MODE.value:
            mode = (params or {}).get("hvac_mode")
            if mode == _HVAC_OFF:
                await run(d.turn_off)
            elif mode == _HVAC_AUTO:
                await run(d.turn_on)
                await run(d.enable_auto_mode)
                if hasattr(d, "disable_heat_mode"):
                    await run(d.disable_heat_mode)
            elif mode == _HVAC_FAN:
                await run(d.turn_on)
                await run(d.disable_auto_mode)
                if hasattr(d, "disable_heat_mode"):
                    await run(d.disable_heat_mode)
            elif mode == _HVAC_HEAT:
                if not hasattr(d, "enable_heat_mode"):
                    return ucapi.StatusCodes.NOT_IMPLEMENTED
                await run(d.turn_on)
                await run(d.enable_heat_mode)
            else:
                return ucapi.StatusCodes.BAD_REQUEST
        elif cmd_id == climate.Commands.TARGET_TEMPERATURE.value:
            if not hasattr(d, "set_heat_target"):
                return ucapi.StatusCodes.NOT_IMPLEMENTED
            target = (params or {}).get("temperature")
            try:
                target_c = float(target)
            except (TypeError, ValueError):
                return ucapi.StatusCodes.BAD_REQUEST
            # Dyson firmware clamps internally; we mirror the documented
            # 1-37°C range so the UCR3 slider can't ask for impossible values.
            target_c = max(1.0, min(37.0, target_c))
            # Setting a target implies heating intent — enable heat mode + on.
            if not getattr(d, "is_on", False):
                await run(d.turn_on)
            await run(d.enable_heat_mode)
            await run(d.set_heat_target, target_c)
        elif cmd_id == climate.Commands.FAN_MODE.value:
            fm = (params or {}).get("fan_mode")
            try:
                # libdyson disables auto_mode automatically when set_speed is
                # called on most firmwares, but doing it ourselves guarantees
                # the command lands on TP09/TP07 where AUTO silently overrides.
                if getattr(d, "auto_mode", False):
                    await run(d.disable_auto_mode)
                await run(d.set_speed, int(fm))
            except (TypeError, ValueError):
                return ucapi.StatusCodes.BAD_REQUEST
        else:
            return ucapi.StatusCodes.NOT_IMPLEMENTED
        return ucapi.StatusCodes.OK
    except Exception as exc:
        _LOG.warning("climate cmd %s failed: %s", cmd_id, exc)
        return ucapi.StatusCodes.SERVER_ERROR


async def _switch_cmd(entity, cmd_id: str, _params: dict | None) -> ucapi.StatusCodes:
    serial, _, feature = entity.id.partition("__")
    _LOG.info("switch cmd %s id=%s feature=%s", cmd_id, entity.id, feature)
    client = _clients.get(serial)
    if client is None or client.device is None:
        _LOG.warning("switch cmd ignored — client not ready for %s", serial)
        return ucapi.StatusCodes.SERVICE_UNAVAILABLE
    d = client.device
    loop = asyncio.get_running_loop()

    def run(fn):
        return loop.run_in_executor(None, fn)

    setters = {
        "power": (d.turn_on, d.turn_off),
        "night": (d.enable_night_mode, d.disable_night_mode),
        "monitoring": (
            getattr(d, "enable_continuous_monitoring", None),
            getattr(d, "disable_continuous_monitoring", None),
        ),
        # "Diffuse ON" means rear airflow → disable_front_airflow.
        "diffuse": (
            getattr(d, "disable_front_airflow", None),
            getattr(d, "enable_front_airflow", None),
        ),
    }
    on_fn, off_fn = setters.get(feature, (None, None))
    if on_fn is None or off_fn is None:
        return ucapi.StatusCodes.NOT_IMPLEMENTED
    try:
        target: bool | None = None
        if cmd_id == switch.Commands.ON.value:
            await run(on_fn)
            target = True
        elif cmd_id == switch.Commands.OFF.value:
            await run(off_fn)
            target = False
        elif cmd_id == switch.Commands.TOGGLE.value:
            current = {
                "power": getattr(d, "is_on", False),
                "night": getattr(d, "night_mode", False),
                "monitoring": getattr(d, "continuous_monitoring", False),
                # diffuse is ON when front_airflow is OFF
                "diffuse": not getattr(d, "front_airflow", True),
            }.get(feature, False)
            await run(off_fn if current else on_fn)
            target = not current
        if target is not None:
            _optimistic_update(entity.id, {
                switch.Attributes.STATE.value:
                    switch.States.ON.value if target else switch.States.OFF.value,
            })
        return ucapi.StatusCodes.OK
    except Exception as exc:
        _LOG.warning("switch cmd %s failed: %s", cmd_id, exc)
        return ucapi.StatusCodes.SERVER_ERROR


async def _light_cmd(entity, cmd_id: str, params: dict | None) -> ucapi.StatusCodes:
    """Direction-dial hack: reuse the Light entity's hue colour wheel as a
    360° direction picker. Hue value (0-359) drives the oscillation centre,
    Light on/off mirrors oscillation enable/disable."""
    serial, _, feature = entity.id.partition("__")
    _LOG.info("light cmd %s id=%s params=%s", cmd_id, entity.id, params)
    if feature != "dial":
        return ucapi.StatusCodes.NOT_IMPLEMENTED
    client = _clients.get(serial)
    if client is None or client.device is None:
        return ucapi.StatusCodes.SERVICE_UNAVAILABLE
    d = client.device
    loop = asyncio.get_running_loop()

    def run(fn, *args):
        return loop.run_in_executor(None, lambda: fn(*args))

    try:
        if cmd_id == light.Commands.OFF.value:
            await run(d.disable_oscillation)
            return ucapi.StatusCodes.OK
        if cmd_id == light.Commands.TOGGLE.value:
            if getattr(d, "oscillation", False):
                await run(d.disable_oscillation)
            else:
                # Resume with current angles or defaults centred on 180°.
                low = getattr(d, "oscillation_angle_low", 135) or 135
                high = getattr(d, "oscillation_angle_high", 225) or 225
                await run(d.enable_oscillation, low, high)
            return ucapi.StatusCodes.OK
        if cmd_id == light.Commands.ON.value:
            hue_param = (params or {}).get("hue")
            if hue_param is None:
                # No hue change — just resume oscillation at current angles.
                low = getattr(d, "oscillation_angle_low", 135) or 135
                high = getattr(d, "oscillation_angle_high", 225) or 225
                await run(d.enable_oscillation, low, high)
                return ucapi.StatusCodes.OK
            try:
                hue = int(hue_param) % 360
            except (TypeError, ValueError):
                return ucapi.StatusCodes.BAD_REQUEST
            # Dyson's dial rotates the opposite way from UC's hue wheel, so
            # mirror the hue before mapping to a fan angle. Clamp at the
            # firmware's 0-350 range.
            centre = min(350, (360 - hue) % 360)
            cur_low = getattr(d, "oscillation_angle_low", None)
            cur_high = getattr(d, "oscillation_angle_high", None)
            cur_span = (cur_high - cur_low) if (cur_low is not None and cur_high is not None) else 45
            low, high = compose_oscillation(centre, cur_span)
            await run(d.enable_oscillation, low, high)
            return ucapi.StatusCodes.OK
        return ucapi.StatusCodes.NOT_IMPLEMENTED
    except Exception as exc:
        _LOG.warning("light dial cmd failed: %s", exc)
        return ucapi.StatusCodes.SERVER_ERROR


async def _select_cmd(entity, cmd_id: str, params: dict | None) -> ucapi.StatusCodes:
    """Routes select-entity commands to the right libdyson call.
    Handles two select entities: oscillation angle and fan speed."""
    serial, _, feature = entity.id.partition("__")
    _LOG.info("select cmd %s id=%s feature=%s params=%s", cmd_id, entity.id, feature, params)
    client = _clients.get(serial)
    if client is None or client.device is None:
        _LOG.warning("select cmd ignored — client not ready for %s", serial)
        return ucapi.StatusCodes.SERVICE_UNAVAILABLE
    d = client.device
    loop = asyncio.get_running_loop()

    def run(fn, *args):
        return loop.run_in_executor(None, lambda: fn(*args))

    if feature == "osc_angle":
        options_list = list(OSC_PRESETS)
    elif feature == "speed":
        options_list = list(_SPEED_OPTIONS)
    elif feature == "direction":
        options_list = list(DIRECTION_PRESETS)
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED

    option: str | None = None
    if cmd_id == select.Commands.SELECT_OPTION.value:
        option = (params or {}).get("option")
    elif cmd_id == select.Commands.SELECT_FIRST.value:
        option = options_list[0]
    elif cmd_id == select.Commands.SELECT_LAST.value:
        option = options_list[-1]
    elif cmd_id in (select.Commands.SELECT_NEXT.value, select.Commands.SELECT_PREVIOUS.value):
        if feature == "osc_angle":
            current = osc_preset_from_angles(
                getattr(d, "oscillation_angle_low", None),
                getattr(d, "oscillation_angle_high", None),
                getattr(d, "oscillation", False),
            )
        elif feature == "direction":
            low = getattr(d, "oscillation_angle_low", 90) or 90
            high = getattr(d, "oscillation_angle_high", 270) or 270
            current = direction_from_centre((low + high) // 2)
        else:
            current = _SPEED_AUTO if getattr(d, "auto_mode", False) else str(getattr(d, "speed", 1) or 1)
        try:
            idx = options_list.index(current)
        except ValueError:
            idx = 0
        step = 1 if cmd_id == select.Commands.SELECT_NEXT.value else -1
        option = options_list[(idx + step) % len(options_list)]
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED

    try:
        if feature == "osc_angle":
            if option == OSC_OFF:
                await run(d.disable_oscillation)
            elif option in OSC_PRESET_RANGES:
                low, high = OSC_PRESET_RANGES[option]
                await run(d.enable_oscillation, low, high)
            else:
                return ucapi.StatusCodes.BAD_REQUEST
            _optimistic_update(entity.id, {
                select.Attributes.STATE.value: select.States.ON.value,
                select.Attributes.CURRENT_OPTION.value: option,
                select.Attributes.OPTIONS.value: list(OSC_PRESETS),
            })
        elif feature == "direction":
            new_centre = DIRECTION_TO_CENTRE.get(option)
            if new_centre is None:
                return ucapi.StatusCodes.BAD_REQUEST
            # Preserve the current sweep span if oscillation is on; default
            # to a narrow 45° lock if it's currently off.
            cur_low = getattr(d, "oscillation_angle_low", None)
            cur_high = getattr(d, "oscillation_angle_high", None)
            cur_span = (cur_high - cur_low) if (cur_low is not None and cur_high is not None) else 45
            low, high = compose_oscillation(new_centre, cur_span)
            await run(d.enable_oscillation, low, high)
            _optimistic_update(entity.id, {
                select.Attributes.STATE.value: select.States.ON.value,
                select.Attributes.CURRENT_OPTION.value: option,
                select.Attributes.OPTIONS.value: list(DIRECTION_PRESETS),
            })
        else:  # speed
            if option == _SPEED_AUTO:
                if not getattr(d, "is_on", True):
                    await run(d.turn_on)
                await run(d.enable_auto_mode)
            else:
                try:
                    target = int(option)
                except (TypeError, ValueError):
                    return ucapi.StatusCodes.BAD_REQUEST
                if not getattr(d, "is_on", True):
                    await run(d.turn_on)
                if getattr(d, "auto_mode", False):
                    await run(d.disable_auto_mode)
                await run(d.set_speed, target)
            _optimistic_update(entity.id, {
                select.Attributes.STATE.value: select.States.ON.value,
                select.Attributes.CURRENT_OPTION.value: option,
                select.Attributes.OPTIONS.value: list(_SPEED_OPTIONS),
            })
        return ucapi.StatusCodes.OK
    except Exception as exc:
        _LOG.warning("select %s failed: %s", feature, exc)
        return ucapi.StatusCodes.SERVER_ERROR


def _build_entities(api: ucapi.IntegrationAPI, dev_cfg: dict[str, Any]) -> list:
    """Builds every UC entity for one Dyson device. `area` ties everything
    to the device name so the Smart Home tab groups them under one card."""
    serial = dev_cfg["serial"]
    name = dev_cfg.get("name") or serial
    pt = dev_cfg["product_type"]
    caps = caps_for(pt)
    out = []

    # Heat-capable models (HP09 etc.) get a proper Climate widget — target
    # temperature is meaningful there. Cool-only models (TP09 etc.) get a
    # plain Power switch + a Temperature sensor instead, so the user never
    # sees a useless target-temperature setter.
    if caps.has_heat:
        out.append(climate.Climate(
            identifier=f"{serial}__climate",
            name=name,
            features=[
                climate.Features.ON_OFF,
                climate.Features.HEAT,
                climate.Features.FAN,
                climate.Features.CURRENT_TEMPERATURE,
                climate.Features.TARGET_TEMPERATURE,
            ],
            attributes={climate.Attributes.STATE.value: climate.States.OFF.value},
            options={
                climate.Options.FAN_MODES.value: _FAN_MODES,
                climate.Options.MIN_TEMPERATURE.value: 1,
                climate.Options.MAX_TEMPERATURE.value: 37,
                climate.Options.TARGET_TEMPERATURE_STEP.value: 1,
                climate.Options.TEMPERATURE_UNIT.value: "CELSIUS",
            },
            icon="uc:fan",
            area=name,
            cmd_handler=_climate_cmd,
        ))
    else:
        out.append(switch.Switch(
            identifier=f"{serial}__power",
            name="Power",
            features=[switch.Features.ON_OFF, switch.Features.TOGGLE],
            attributes={switch.Attributes.STATE.value: switch.States.OFF.value},
            device_class=switch.DeviceClasses.SWITCH,
            icon="uc:plug",
            area=name,
            cmd_handler=_switch_cmd,
        ))
        out.append(sensor.Sensor(
            identifier=f"{serial}__temperature",
            name="Temperature",
            features=[],
            attributes={
                sensor.Attributes.STATE.value: sensor.States.UNAVAILABLE.value,
                sensor.Attributes.VALUE.value: None,
                sensor.Attributes.UNIT.value: "°C",
            },
            device_class=sensor.DeviceClasses.TEMPERATURE,
            icon="uc:thermometer",
            area=name,
        ))

    # Standalone Fan-speed picker — accessible regardless of climate mode.
    # "Auto" is the first option so picking it re-enables auto without
    # forcing the user to go through the climate widget.
    out.append(select.Select(
        identifier=f"{serial}__speed",
        name="Fan speed",
        attributes={
            select.Attributes.STATE.value: select.States.ON.value,
            select.Attributes.CURRENT_OPTION.value: _SPEED_AUTO,
            select.Attributes.OPTIONS.value: list(_SPEED_OPTIONS),
        },
        icon="uc:gauge",
        area=name,
        cmd_handler=_select_cmd,
    ))

    out.append(switch.Switch(
        identifier=f"{serial}__night",
        name="Night mode",
        features=[switch.Features.ON_OFF, switch.Features.TOGGLE],
        attributes={switch.Attributes.STATE.value: switch.States.OFF.value},
        icon="uc:moon",
        area=name,
        cmd_handler=_switch_cmd,
    ))
    out.append(switch.Switch(
        identifier=f"{serial}__monitoring",
        name="Continuous air monitoring",
        features=[switch.Features.ON_OFF, switch.Features.TOGGLE],
        attributes={switch.Attributes.STATE.value: switch.States.OFF.value},
        icon="uc:eye",
        area=name,
        cmd_handler=_switch_cmd,
    ))

    # Diffuse mode — when ON, air exits the back of the fan (purifies the
    # room without blowing on you). When OFF, normal front airflow.
    out.append(switch.Switch(
        identifier=f"{serial}__diffuse",
        name="Diffuse mode (rear airflow)",
        features=[switch.Features.ON_OFF, switch.Features.TOGGLE],
        attributes={switch.Attributes.STATE.value: switch.States.OFF.value},
        icon="uc:wind",
        area=name,
        cmd_handler=_switch_cmd,
    ))

    if caps.has_oscillation:
        out.append(select.Select(
            identifier=f"{serial}__osc_angle",
            name="Oscillation",
            attributes={
                select.Attributes.STATE.value: select.States.UNAVAILABLE.value,
                select.Attributes.CURRENT_OPTION.value: OSC_OFF,
                select.Attributes.OPTIONS.value: list(OSC_PRESETS),
            },
            icon="uc:arrows-rotate",
            area=name,
            cmd_handler=_select_cmd,
        ))
        out.append(select.Select(
            identifier=f"{serial}__direction",
            name="Direction (centre angle)",
            attributes={
                select.Attributes.STATE.value: select.States.ON.value,
                select.Attributes.CURRENT_OPTION.value: "180°",
                select.Attributes.OPTIONS.value: list(DIRECTION_PRESETS),
            },
            icon="uc:compass",
            area=name,
            cmd_handler=_select_cmd,
        ))
        # Circular-dial direction picker — reuses the Light entity's hue
        # colour wheel so the user gets a continuous 360° dial matching the
        # Dyson app's UX. The "light" doesn't represent a real lamp; the
        # colour you drag to corresponds directly to the fan's facing.
        out.append(light.Light(
            identifier=f"{serial}__dial",
            name="Direction dial",
            features=[light.Features.ON_OFF, light.Features.COLOR],
            attributes={
                light.Attributes.STATE.value: light.States.OFF.value,
                light.Attributes.HUE.value: 180,
                light.Attributes.SATURATION.value: 100,
            },
            icon="uc:compass",
            area=name,
            cmd_handler=_light_cmd,
        ))

    sensor_specs = [
        ("humidity", "Humidity", sensor.DeviceClasses.HUMIDITY, "%", "uc:droplet"),
        ("pm25", "PM2.5", sensor.DeviceClasses.CUSTOM, "µg/m³", "uc:smog"),
        ("pm10", "PM10", sensor.DeviceClasses.CUSTOM, "µg/m³", "uc:smog"),
        ("voc", "VOC index", sensor.DeviceClasses.CUSTOM, "", "uc:flask"),
        ("no2", "NO₂ index", sensor.DeviceClasses.CUSTOM, "", "uc:flask"),
        ("hepa", "HEPA filter life", sensor.DeviceClasses.CUSTOM, "%", "uc:filter"),
        ("carbon", "Carbon filter life", sensor.DeviceClasses.CUSTOM, "%", "uc:filter"),
    ]
    if caps.has_hcho:
        sensor_specs.append(
            ("hcho", "Formaldehyde", sensor.DeviceClasses.CUSTOM, "mg/m³", "uc:flask")
        )

    for key, label, dclass, unit, icon in sensor_specs:
        out.append(sensor.Sensor(
            identifier=f"{serial}__{key}",
            name=label,
            features=[],
            attributes={
                sensor.Attributes.STATE.value: sensor.States.UNAVAILABLE.value,
                sensor.Attributes.VALUE.value: None,
                sensor.Attributes.UNIT.value: unit,
            },
            device_class=dclass,
            icon=icon,
            area=name,
        ))

    return out


def _refresh_attrs(api: ucapi.IntegrationAPI, serial: str) -> None:
    """Refresh entity attributes from the device's current libdyson state.
    Every section is wrapped — one bad attribute should never crash the
    integration's main loop and trigger 'not responding' in the UC."""
    client = _clients.get(serial)
    if client is None or client.device is None:
        return
    d = client.device

    if api.configured_entities.contains(f"{serial}__climate"):
        try:
            api.configured_entities.update_attributes(
                f"{serial}__climate", _climate_attrs(d)
            )
        except Exception as exc:
            _LOG.warning("climate refresh failed: %s", exc)

    def sw_state(v: Any) -> str:
        return switch.States.ON.value if v else switch.States.OFF.value

    # Power switch (cool-only models) and Temperature sensor mirror the
    # climate state but as independent, simpler entities.
    power_id = f"{serial}__power"
    if api.configured_entities.contains(power_id):
        try:
            api.configured_entities.update_attributes(
                power_id,
                {switch.Attributes.STATE.value: sw_state(getattr(d, "is_on", False))},
            )
        except Exception as exc:
            _LOG.warning("power refresh failed: %s", exc)

    temp_id = f"{serial}__temperature"
    if api.configured_entities.contains(temp_id):
        try:
            t_raw = getattr(d, "temperature", None)
            t_c = None
            if t_raw is not None:
                t = float(t_raw)
                if t > 200:
                    t = t / 10.0 if t > 1000 else t
                    t = t - 273.15
                t_c = round(t, 1)
            if t_c is None:
                api.configured_entities.update_attributes(temp_id, {
                    sensor.Attributes.STATE.value: sensor.States.UNAVAILABLE.value,
                })
            else:
                api.configured_entities.update_attributes(temp_id, {
                    sensor.Attributes.STATE.value: sensor.States.ON.value,
                    sensor.Attributes.VALUE.value: t_c,
                })
        except Exception as exc:
            _LOG.warning("temperature refresh failed: %s", exc)

    for key, attr in (("night", "night_mode"), ("monitoring", "continuous_monitoring")):
        try:
            api.configured_entities.update_attributes(
                f"{serial}__{key}",
                {switch.Attributes.STATE.value: sw_state(getattr(d, attr, False))},
            )
        except Exception as exc:
            _LOG.warning("switch %s refresh failed: %s", key, exc)

    diffuse_id = f"{serial}__diffuse"
    if api.configured_entities.contains(diffuse_id):
        try:
            # Diffuse ON when front_airflow OFF.
            diffuse_on = not getattr(d, "front_airflow", True)
            api.configured_entities.update_attributes(
                diffuse_id,
                {switch.Attributes.STATE.value: sw_state(diffuse_on)},
            )
        except Exception as exc:
            _LOG.warning("diffuse refresh failed: %s", exc)

    osc_id = f"{serial}__osc_angle"
    if api.configured_entities.contains(osc_id):
        try:
            current = osc_preset_from_angles(
                getattr(d, "oscillation_angle_low", None),
                getattr(d, "oscillation_angle_high", None),
                getattr(d, "oscillation", False),
            )
            # OPTIONS must be re-sent on every update — UCR3 replaces, not merges.
            api.configured_entities.update_attributes(osc_id, {
                select.Attributes.STATE.value: select.States.ON.value,
                select.Attributes.CURRENT_OPTION.value: current,
                select.Attributes.OPTIONS.value: list(OSC_PRESETS),
            })
        except Exception as exc:
            _LOG.warning("osc_angle refresh failed: %s", exc)

    dir_id = f"{serial}__direction"
    if api.configured_entities.contains(dir_id):
        try:
            low = getattr(d, "oscillation_angle_low", None)
            high = getattr(d, "oscillation_angle_high", None)
            if low is not None and high is not None:
                centre = (low + high) // 2
                current_dir = direction_from_centre(centre)
            else:
                current_dir = "180°"
            api.configured_entities.update_attributes(dir_id, {
                select.Attributes.STATE.value: select.States.ON.value,
                select.Attributes.CURRENT_OPTION.value: current_dir,
                select.Attributes.OPTIONS.value: list(DIRECTION_PRESETS),
            })
        except Exception as exc:
            _LOG.warning("direction refresh failed: %s", exc)

    dial_id = f"{serial}__dial"
    if api.configured_entities.contains(dial_id):
        try:
            low = getattr(d, "oscillation_angle_low", None)
            high = getattr(d, "oscillation_angle_high", None)
            centre = (low + high) // 2 if (low is not None and high is not None) else 180
            # Inverse of the hue->angle mirror in _light_cmd so the dial
            # face shows the same orientation the fan is actually pointing.
            hue = (360 - centre) % 360
            osc_on = getattr(d, "oscillation", False)
            api.configured_entities.update_attributes(dial_id, {
                light.Attributes.STATE.value: light.States.ON.value if osc_on else light.States.OFF.value,
                light.Attributes.HUE.value: hue,
                light.Attributes.SATURATION.value: 100,
            })
        except Exception as exc:
            _LOG.warning("dial refresh failed: %s", exc)

    speed_id = f"{serial}__speed"
    if api.configured_entities.contains(speed_id):
        try:
            if getattr(d, "auto_mode", False):
                current_speed = _SPEED_AUTO
            else:
                current_speed = str(getattr(d, "speed", 1) or 1)
            # Re-send OPTIONS every refresh so the picker never goes blank.
            api.configured_entities.update_attributes(speed_id, {
                select.Attributes.STATE.value: select.States.ON.value,
                select.Attributes.CURRENT_OPTION.value: current_speed,
                select.Attributes.OPTIONS.value: list(_SPEED_OPTIONS),
            })
        except Exception as exc:
            _LOG.warning("speed refresh failed: %s", exc)

    sensor_map = [
        ("humidity", getattr(d, "humidity", None)),
        ("pm25", getattr(d, "particulate_matter_2_5", None)),
        ("pm10", getattr(d, "particulate_matter_10", None)),
        ("voc", getattr(d, "volatile_organic_compounds", None)),
        ("no2", getattr(d, "nitrogen_dioxide", None)),
        ("hepa", getattr(d, "hepa_filter_life", None)),
        ("carbon", getattr(d, "carbon_filter_life", None)),
        ("hcho", getattr(d, "formaldehyde", None)),
    ]
    for key, val in sensor_map:
        entity_id = f"{serial}__{key}"
        if not api.configured_entities.contains(entity_id):
            continue
        # If libdyson hasn't seen the sensor yet (None), leave the entity
        # UNAVAILABLE rather than pushing a malformed ON+None pair.
        try:
            if val is None:
                api.configured_entities.update_attributes(entity_id, {
                    sensor.Attributes.STATE.value: sensor.States.UNAVAILABLE.value,
                })
            else:
                api.configured_entities.update_attributes(entity_id, {
                    sensor.Attributes.STATE.value: sensor.States.ON.value,
                    sensor.Attributes.VALUE.value: val,
                })
        except Exception as exc:
            _LOG.warning("sensor %s refresh failed: %s", key, exc)


def _start_client(api: ucapi.IntegrationAPI, dev_cfg: dict[str, Any]) -> None:
    serial = dev_cfg["serial"]
    if serial in _clients:
        return

    loop = asyncio.get_event_loop()

    async def on_state_change():
        _refresh_attrs(api, serial)

    client = DysonClient(
        serial=serial,
        credential=dev_cfg["credential"],
        product_type=dev_cfg["product_type"],
        on_state_change=on_state_change,
        loop=loop,
    )
    _clients[serial] = client
    client.start()


def _optimistic_update(entity_id: str, attrs: dict) -> None:
    """Push attribute changes to UCR3 immediately after we issue a command,
    without waiting for the fan's MQTT echo. Makes the tile feel snappy on
    touch. If the command actually fails, the next real state push will
    correct the display within 1-2 seconds."""
    if _api_ref is None:
        return
    try:
        _api_ref.configured_entities.update_attributes(entity_id, attrs)
    except Exception as exc:
        _LOG.debug("optimistic update failed (non-fatal): %s", exc)


async def setup_handler(api: ucapi.IntegrationAPI, msg: SetupDriver) -> SetupAction:
    if isinstance(msg, DriverSetupRequest):
        data = msg.setup_data or {}

        # Manual path takes priority: if serial + credential + product_type
        # were pasted, skip the cloud OTP dance entirely.
        m_serial = (data.get("manual_serial") or "").strip()
        m_cred = data.get("manual_credential") or ""
        m_pt = (data.get("manual_product_type") or "").strip()
        m_name = (data.get("manual_name") or "").strip()

        if m_serial and m_cred and m_pt:
            dev = {
                "serial": m_serial,
                "credential": m_cred,
                "product_type": m_pt,
                "name": m_name or m_serial,
            }
            _save_config(api, {"devices": [dev]})
            for e in _build_entities(api, dev):
                api.available_entities.add(e)
                api.configured_entities.add(e)
            _start_client(api, dev)
            _LOG.info("manual setup complete for %s (%s)", m_serial, m_pt)
            return SetupComplete()

        # Cloud path: needs email + password to start OTP flow.
        email = (data.get("email") or "").strip()
        password = data.get("password") or ""
        region = (data.get("region") or "GB").strip().upper()

        if not email or not password:
            return SetupError(error_type=IntegrationSetupError.OTHER)

        try:
            _setup_state.request_otp(email, password, region)
        except DysonSetupError as exc:
            _LOG.warning("setup OTP request failed: %s", exc)
            return SetupError(error_type=IntegrationSetupError.AUTHORIZATION_ERROR)

        return RequestUserInput(
            title={"en": "Enter the 6-digit code Dyson just emailed"},
            settings=[{
                "id": "otp",
                "label": {"en": "OTP code"},
                "field": {"text": {"value": ""}},
            }],
        )

    if isinstance(msg, UserDataResponse):
        otp = (msg.input_values or {}).get("otp", "").strip()
        try:
            devices = _setup_state.verify_otp(otp)
        except DysonSetupError as exc:
            _LOG.warning("OTP verify failed: %s", exc)
            return SetupError(error_type=IntegrationSetupError.AUTHORIZATION_ERROR)

        if not devices:
            return SetupError(error_type=IntegrationSetupError.OTHER)

        cfg = {"devices": devices}
        _save_config(api, cfg)

        # Surface creds to logs so the user can copy them for future
        # manual-path re-installs without re-OTPing. They were fetched on
        # this remote and never leave it — the log is local to UCR3.
        for dev in devices:
            _LOG.info(
                "PASTE-ME-NEXT-TIME serial=%s product_type=%s name=%r",
                dev["serial"], dev["product_type"], dev["name"],
            )
            _LOG.info(
                "PASTE-ME-NEXT-TIME credential=%s",
                dev["credential"],
            )

        for dev in devices:
            for e in _build_entities(api, dev):
                api.available_entities.add(e)
                api.configured_entities.add(e)
            _start_client(api, dev)
        return SetupComplete()

    if isinstance(msg, AbortDriverSetup):
        _LOG.info("setup aborted: %s", msg.error)
        return SetupComplete()

    return SetupError(error_type=IntegrationSetupError.OTHER)


async def on_connect(api: ucapi.IntegrationAPI) -> None:
    global _api_ref
    _api_ref = api
    cfg = _load_config(api)
    for dev in cfg.get("devices", []):
        for e in _build_entities(api, dev):
            api.available_entities.add(e)
            api.configured_entities.add(e)
        _start_client(api, dev)
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)


async def on_disconnect(_api: ucapi.IntegrationAPI) -> None:
    for c in list(_clients.values()):
        await c.stop()
    _clients.clear()


async def on_exit_standby(api: ucapi.IntegrationAPI) -> None:
    """When the remote wakes from standby, force a fresh state pull so the
    user sees current values instantly instead of waiting for the next
    periodic MQTT push."""
    for serial, client in _clients.items():
        if client.device is None:
            continue
        loop = asyncio.get_running_loop()
        try:
            if hasattr(client.device, "request_current_status"):
                await loop.run_in_executor(None, client.device.request_current_status)
            if hasattr(client.device, "request_environmental_data"):
                await loop.run_in_executor(None, client.device.request_environmental_data)
        except Exception as exc:
            _LOG.debug("exit-standby refresh failed for %s: %s", serial, exc)
        _refresh_attrs(api, serial)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    api = ucapi.IntegrationAPI(loop)

    driver_json = Path(__file__).parent / "driver.json"

    async def _setup(msg: SetupDriver) -> SetupAction:
        return await setup_handler(api, msg)

    api.add_listener(ucapi.Events.CONNECT, lambda: on_connect(api))
    api.add_listener(ucapi.Events.DISCONNECT, lambda: on_disconnect(api))
    api.add_listener(ucapi.Events.EXIT_STANDBY, lambda: on_exit_standby(api))

    loop.run_until_complete(api.init(str(driver_json), _setup))
    loop.run_forever()


if __name__ == "__main__":
    main()
