# Changelog

## 0.20.0 — 2026-05-12

- **README rewritten with plain-English setup instructions**, "what you'll need before starting" preamble, an explicit "where do I find my fan's serial" FAQ, and a dedicated troubleshooting entry for the UCR3 widget-cache quirk (with the reboot-or-re-add-tiles workaround).
- **Tile-cache blink on first connect.** UCR3 caches each tile's last-rendered state on Home Screen / Activity pages. Reinstalling the integration (or restarting the daemon) while tiles were on screen as UNAVAILABLE meant the cached state stuck until a full UCR3 reboot, even though entity_change events were flowing normally. The integration now performs a one-shot "state blink" on the first MQTT connect per device: every entity flashes to UNAVAILABLE for ~300ms then receives its real values, which forces the touchscreen renderer to redraw. Only fires once per device per daemon lifetime, so it's invisible on normal reconnects.
- **Per-device Static LAN IP** for multi-device setups. The Static LAN IP field now accepts either a bare IP (legacy single-device behaviour — applied to every device the cloud returns) OR `SERIAL=IP` pairs separated by commas/newlines for any number of devices. Only the listed serials skip mDNS; devices not in the mapping fall back to mDNS as normal.
  - Example: `AAA-XX-ZZZ0000A=192.168.1.42, BBB-YY-WWW1111B=192.168.5.10`
  - Fixes v0.19.0 limitation where a single static IP was applied to every cloud-fetched device, which broke setups with two fans on different subnets when only one was unreachable via mDNS.
- Backward compatible: existing setups with a plain IP keep working unchanged.

## 0.19.0 — 2026-05-12

- **Static LAN IP override now applies to both cloud-OTP and manual setup paths.** Previously the IP field was buried in the manual section and only worked when you also pasted serial/credential/product_type. Users with a Dyson on a separate VLAN (or any mDNS-blocking network topology) can now do the normal Dyson account sign-in AND paste a static IP in the same screen — the integration applies the IP override to whatever devices the Dyson cloud returns. No more two-step "do cloud first, then re-do as manual" workaround.
- New `static_ip` field at the top level of the setup form (after region, before the manual section). The old `manual_ip` field name is still accepted for backward compatibility with anyone who'd already filled it.

## 0.18.1 — 2026-05-12

- Restored entity registration and client start in the setup handler — last v0.18 attempt removed them and broke setup on fresh installs where the UC core didn't reconnect immediately after `SetupComplete`. Now entities are added in both the setup handler AND on_connect — duplicate `add()` calls are harmless ("already exists" debug log).

## 0.17.0 — 2026-05-12

- **Static LAN IP override** in setup — a new optional field at the bottom of the manual setup section lets you paste your fan's static IP directly. Skips mDNS entirely. Fixes setups where the integration logs `mDNS resolve failed for <serial>.local: [Errno -3] Temporary failure in name resolution` because the network (mesh routers, segregated VLANs, certain firewalls) doesn't propagate the device's `.local` hostname to the UCR3.
- The mDNS-failure log line now explicitly suggests the Static LAN IP path as the fix.

## 0.16.0 — 2026-05-12

- **Multi-device responsiveness** — fixes intermittent UC-to-daemon WebSocket disconnects (visible as `BrokenPipeError` in the integration log followed by a reconnect) reported on setups with two Dyson devices.
- **Dedup attribute updates** — every `entity_change` event is now compared against the last state sent for that entity. Identical refreshes are skipped, dropping WS traffic by ~80% in steady state. The UCR3's WS receive buffer no longer saturates when both fans push state simultaneously.
- **Keep fan clients alive across UC WS reconnects** — the UC core's WebSocket flaps every time you navigate around the remote UI; the daemon no longer tears down its MQTT connections to the fans during these brief flaps. Result: instant state on UI reconnect, no re-connect penalty from libdyson.
- **Send full state on every UC WS reconnect** — the dedup cache clears when a fresh UC core attaches, so the new WS client immediately receives complete state for all entities (previously could see stale UNAVAILABLE values).
- **Louder MQTT disconnect logging** — `WARNING`-level log line with a hint that a competing client (Dyson Link app, ha-dyson on Home Assistant, etc.) may be holding the fan's MQTT session. Helps diagnose the "constant disconnects" symptom Dyson firmware exhibits when two MQTT clients fight for the single session slot.

## 0.15.0 — 2026-05-11

- **Optimistic UI updates** — tiles now reflect the new state the instant you tap, rather than waiting 1-2 seconds for the fan to round-trip the MQTT state push. Applies to night mode, continuous monitoring, diffuse mode, power, fan speed, oscillation sweep, and direction picker. The real state confirmation from the fan still arrives shortly after and corrects the display if the command somehow didn't land.

## 0.14.0 — 2026-05-11

- Real-time responsiveness improvements:
  - Cache the device's resolved LAN IP across reconnect attempts so we skip mDNS lookup (500ms-2s) on every retry. Cache invalidates on a failed connect so we re-resolve if the device moves on the LAN.
  - Eagerly call `request_current_status` + `request_environmental_data` right after MQTT connect so the entity tiles paint with real values immediately instead of waiting for the fan's next periodic push (saves 2-5s on first paint).
  - Reconnect delay 10s → 3s. Disconnect-poll cadence 2s → 1s.
  - New `EXIT_STANDBY` handler — when the remote wakes from sleep, the integration immediately requests a fresh state pull so the user doesn't see stale values for a few seconds after waking the remote.

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
