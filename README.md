# dyson-uc

Local control for Wi-Fi Dyson air purifiers, fans, heaters and humidifiers on the Unfolded Circle Remote 3.

After a one-time sign-in to your Dyson account (so it can fetch your fan's local password), everything runs entirely on your home Wi-Fi. No cloud calls during use. Push-driven state, instant response.

## Supported devices

Anything Dyson with built-in Wi-Fi:

- **Pure Cool**: TP04 / TP07 / TP09 / TP11, DP04
- **Pure Hot+Cool**: HP04 / HP07 / HP09
- **Pure Humidify+Cool**: PH01 / PH02 / PH03 / PH04
- **Big+Quiet**: BP02 / BP03 / BP04

Older Pure Cool Link models (TP01-03) also work.

Bluetooth-only Dyson hair tools, headphones and vacuums are **not** supported — those don't expose a local Wi-Fi protocol.

## What you'll see on the remote

After install, every Dyson device on your account appears as one group of widgets on the Smart Home tab:

- **Power** — on/off toggle
- **Fan speed** — Auto / 1 to 10 (picking a number disables Auto automatically)
- **Direction (centre angle)** — 12 angle steps every 30° to point the fan
- **Direction dial** — drag a colour wheel to set the angle continuously (cosmetic note: the dial reuses the Remote's Light widget so it will show an unused brightness slider — harmless, ignore it)
- **Oscillation** — Off / 45° / 90° / 180° / 350° sweep
- **Night mode** — quiet, dimmed display
- **Continuous air monitoring** — keeps sensors active even when fan is off
- **Diffuse mode** — air comes out the back of the fan (rear airflow, doesn't blow on you)
- **Temperature**, **Humidity** — sensor readings
- **PM2.5**, **PM10**, **VOC**, **NO₂**, **Formaldehyde** — air-quality sensors (depending on model)
- **HEPA filter life**, **Carbon filter life** — percentage remaining

Hot+Cool models (HP04/07/09) also get a proper thermostat widget instead of the plain power switch, because target temperature is meaningful when there's a heater.

## Install

### Step 1 — Get the tarball

Grab the latest `dyson-sbr-vX.Y.Z.tar.gz` from the [Releases page](https://github.com/sbr-labs/dyson-uc/releases).

### Step 2 — Open the Remote 3 web configurator

In a browser on the same network as your remote, go to your remote's web configurator (usually `http://remote-<id>.local` or the IP shown under Settings → Network on the remote).

### Step 3 — Upload the integration

1. Click **Integrations** in the sidebar
2. Click **Upload custom integration** (or **Update** if you've installed dyson-uc before — that keeps your credentials)
3. Select the `.tar.gz` file you downloaded
4. Wait for "Integration installed" confirmation

### Step 4 — Add the integration

1. Still on the Integrations page, click **+ Add integration**
2. Pick **Dyson (local)** from the list
3. You'll see the setup screen

### Step 5 — Sign in (one-time)

Two ways. Pick whichever fits.

**Cloud path (easiest first-timer setup):**

- Type your **Dyson account email** in the top field
- Type your **Dyson account password** in the password field
- Region defaults to `GB` — change to your two-letter country code (`US`, `DE`, etc.) if needed
- Leave the manual fields at the bottom **empty**
- Click **Next**
- Dyson will email you a 6-digit code within a few seconds
- Enter the code on the next screen
- Click **Next** again

**Manual path (skip the cloud — best for re-installs):**

If you've previously installed dyson-uc and saved your device's serial + local credential, paste them into the four "manual" fields at the bottom of the setup screen and leave email/password empty. Tap **Next**. Done — no OTP, no waiting.

(Your credentials get logged to the integration's "Logs" screen on first successful cloud setup, on lines that start with `PASTE-ME-NEXT-TIME` — copy them somewhere safe like Notes or 1Password.)

### Step 6 — You're done

Open the **Smart Home** tab on the remote. The Dyson widgets are grouped under the device name (e.g. "Living room"). Tap anything. The fan should respond within a second or two.

## Updating

Always use the **Update** button in the configurator, not the **Install** button — Update preserves your saved credentials so you don't have to re-do the OTP flow every release. (If your configurator only offers "Install", that's fine too; the manual-paste path covers you.)

## Troubleshooting

**Nothing happens when I tap a button.**
Open the integration in the configurator → **Logs**. Look for `climate cmd`, `switch cmd`, or `select cmd` lines. If you see `ignored — client not ready`, the integration is still connecting to the fan — wait 10-20 seconds and retry. If you see a Python traceback, open an issue with the trace pasted in.

**"Not responding" or sensors stop updating.**
The integration auto-reconnects every 10 seconds if the MQTT connection drops. If you see a sustained outage, restart the integration from the configurator (Integrations → Dyson (local) → Disable, then Enable).

**Setup says "Authorisation error".**
Either the Dyson account email/password is wrong, the OTP code is stale (Dyson codes expire fast — start the flow over), or your region code doesn't match the country your Dyson account is registered in.

**My device shows everything UNAVAILABLE and the logs say "mDNS resolve failed".**
Your network isn't propagating the fan's `.local` hostname to the UCR3 (common on some mesh routers, VLAN setups and firewalls — multicast traffic doesn't cross VLANs by default). Fix:

1. Reserve a static DHCP lease for the fan in your router so its IP doesn't change.
2. Re-run integration setup. Use either the **cloud-OTP path** or the **manual path** as normal, AND fill in the **Static LAN IP** field (the dedicated section between the cloud and manual sections) with the fan's reserved address. The integration will skip mDNS and connect directly to that address. Works on either path.

**My TP09's commands aren't working but sensors are fine.**
You probably hit a `set_speed` while the fan is in AUTO mode. The integration handles this — it disables AUTO before changing speed. If commands still don't land, file an issue with the integration logs attached.

## Privacy & security

- Your Dyson account email and password are used **only once** during setup to fetch the per-device local MQTT credential. They are not saved.
- Only the local MQTT credential is persisted, in the integration's config directory on the remote.
- The integration speaks directly to your fan over your LAN. No telemetry, no analytics, no third-party callouts.

## Credits

Built on top of [libdyson-neon](https://github.com/libdyson-wg/libdyson-neon) (the same library that powers ha-dyson). Thanks to the libdyson-wg team for reverse-engineering Dyson's MQTT protocol and keeping it maintained.

## License

MIT — see [LICENSE](LICENSE).
