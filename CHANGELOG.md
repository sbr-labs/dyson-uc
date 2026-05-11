# Changelog

## 0.13.0 — 2026-05-11

- Wire up the climate widget for Hot+Cool models (HP04/07/09):
  - `TARGET_TEMPERATURE` command now calls `set_heat_target` and enables heat mode.
  - `HVAC_MODE: HEAT` enables heat mode; `AUTO`/`FAN` disable it.
  - `heat_target` and `heat_mode_is_on` are surfaced on the climate attributes so the thermostat slider tracks reality.
  - Climate options now declare 1-37°C target range, 1°C step, Celsius.
- Cool-only models (TP04-11, DP04, PH01-04, BP02-04) unchanged — they keep the simpler power-switch + temperature-sensor layout.

## 0.12.0 — 2026-05-11

Initial public release.

- Local-MQTT control of Wi-Fi Dyson air purifiers, fans, heaters and humidifiers via [libdyson-neon](https://github.com/libdyson-wg/libdyson-neon).
- One-time Dyson account sign-in fetches per-device local MQTT credential; all runtime traffic stays on the LAN.
- Manual credential-paste alternative path skips the cloud step entirely on re-installs.
- Per-device entity model:
  - Power switch (cool-only models) or full Climate widget (Hot+Cool models).
  - Fan speed select with Auto + 1-10.
  - Oscillation sweep-width select (Off / 45° / 90° / 180° / 350°).
  - Direction centre-angle select (12 angles every 30°).
  - Direction dial — colour-wheel hack via Light entity for continuous angle picking.
  - Switches for night mode, continuous air monitoring, diffuse (rear) airflow.
  - Sensors for temperature, humidity, PM2.5, PM10, VOC, NO₂, formaldehyde, HEPA filter life, carbon filter life.
- Model-aware entity exposure — formaldehyde sensor only on HCHO-capable variants, climate widget only on heat-capable variants.
- Auto-reconnect with 10-second backoff if the local MQTT connection drops.
- mDNS auto-discovery of the device's LAN address on the `_dyson_mqtt._tcp` service.
