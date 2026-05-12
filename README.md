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

## What you'll need before starting

- Your **Dyson account email + password** (the same one you use in the Dyson Link app — needed once to fetch your fan's local credentials)
- The UCR3 **on the same network** as the fan — OR, if the fan is on a different VLAN, see the [Troubleshooting → multiple Dysons](#troubleshooting) section for the Static LAN IP override
- Access to the **UCR3 web configurator** — get its URL from the remote: Settings → Network → tap the IP / hostname shown

That's it. No CLI, no Home Assistant, no extra services.

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

Two ways. **Use the Cloud path on your first install.** The Manual path is for power users who want to skip OTP on re-installs.

**Cloud path (recommended — easiest first-timer setup):**

- Type your **Dyson account email** in the top field (same one you use in the Dyson Link app)
- Type your **Dyson account password** in the password field
- Region defaults to `GB` — change to your two-letter country code (`US`, `DE`, etc.) if your Dyson account is registered elsewhere
- (Optional) **Static LAN IP** — leave blank unless mDNS doesn't work on your network. See [Troubleshooting](#troubleshooting) if you're not sure.
- Leave the manual fields at the bottom **empty**
- Click **Next**
- Dyson will email you a **6-digit code** to the address you entered. Check your inbox.
- **Type the code quickly** — it expires in a couple of minutes. If you take too long, the setup fails and you restart from the email step.
- Enter the code on the next screen
- Click **Next** again
- Setup completes. Within ~5-10 seconds the integration connects to your fan and entities appear on the Smart Home tab.

**Manual path (skip the cloud — best for re-installs):**

If you've previously installed dyson-uc, your fan's local credentials were logged to the integration's "Logs" screen on lines starting with `PASTE-ME-NEXT-TIME`. Copy those into the four "manual" fields at the bottom of the setup screen and leave email/password empty. Tap **Next**. Done — no OTP, no waiting.

**Save your credentials somewhere safe** (Notes, 1Password, etc.) after the first cloud setup so you can use the Manual path next time without having to OTP again.

### Step 6 — You're done

Open the **Smart Home** tab on the remote. The Dyson widgets are grouped under the device name (e.g. "Living room"). Tap anything. The fan should respond within a second or two.

**Important first-time-install note:** if you've put Dyson tiles on a **custom Home Screen page** and they show "Unknown" instead of real values, that's a UCR3 firmware caching quirk — see the [Troubleshooting](#troubleshooting) section below. One-time fix, then you'll never see it again.

## Updating

Always use the **Update** button in the configurator, not the **Install** button — Update preserves your saved credentials so you don't have to re-do the OTP flow every release. (If your configurator only offers "Install", that's fine too; the manual-paste path covers you.)

## Troubleshooting

**My Home Screen tiles still show "Unknown" or stale values after a successful install.**

This is a **UCR3 firmware caching quirk**, not an integration bug. When you install or reinstall a custom integration, the touchscreen widgets on a Home Screen / Activity page cache the last-known state of each tile locally. If those widgets were on screen as `UNAVAILABLE` while the integration was reconnecting, the cached state stays stuck until something forces a refresh — even though the integration is correctly sending current values to the UC core in the background.

The integration does a brief "state flash" on first connect to try to nudge the touchscreen renderer, which helps **IF you're viewing the Home Screen page at install time**. Otherwise you have two equally good fixes — pick whichever:

1. **Reboot the remote** (Settings → System → Restart) — wipes every widget cache in one go. Easiest.
2. **Remove and re-add the Dyson tiles on your Home Screen page** — drops each widget and recreates it, forcing a fresh state read. No reboot needed.

You only need to do this **once after install/reinstall** — once the cache is fresh, tiles stay current going forward.

**Where do I find my fan's serial number?**

You may need this for the manual setup path OR for the Static LAN IP per-device mapping (Troubleshooting section below). Four ways to find it:

1. **Physical sticker on the device** — usually on the base, back, or under the bin. Format: five short groups separated by hyphens, e.g. `AAA-XX-ZZZ0000A`.
2. **Dyson Link app** on your phone → tap the device → Settings (gear icon) → Device details.
3. **After a successful cloud-OTP setup**, the integration writes the serial into the UCR3 integration log on lines starting with `PASTE-ME-NEXT-TIME serial=...`. Open the UCR3 web configurator → Integrations → Dyson (local) → Logs to read them. Useful if you want the manual-setup creds for future re-installs.
4. **mDNS scan from a Mac/Linux PC on the same network as the fan**:
   ```
   dns-sd -B _dyson_mqtt._tcp
   ```
   Shows `<product>-<country>-<serial>` for each Dyson advertising on that network segment.

**Nothing happens when I tap a button.**
Open the integration in the configurator → **Logs**. Look for `climate cmd`, `switch cmd`, or `select cmd` lines. If you see `ignored — client not ready`, the integration is still connecting to the fan — wait 10-20 seconds and retry. If you see a Python traceback, open an issue with the trace pasted in.

**"Not responding" or sensors stop updating.**
The integration auto-reconnects every 10 seconds if the MQTT connection drops. If you see a sustained outage, restart the integration from the configurator (Integrations → Dyson (local) → Disable, then Enable).

**Setup says "Authorisation error".**
Either the Dyson account email/password is wrong, the OTP code is stale (Dyson codes expire fast — start the flow over), or your region code doesn't match the country your Dyson account is registered in.

**My device shows everything UNAVAILABLE and the logs say "mDNS resolve failed".**
Your network isn't propagating the fan's `.local` hostname to the UCR3 (common on some mesh routers, VLAN setups and firewalls — multicast traffic doesn't cross VLAN/subnet boundaries by default). Fix per the case below.

**Case 1 — single Dyson, mDNS doesn't reach the UCR3:**

1. Reserve a static DHCP lease for the fan in your router so its IP doesn't change.
2. Re-run integration setup. Use either the **cloud-OTP path** or the **manual path** as normal, AND paste the fan's reserved IP into the **Static LAN IP** field (the dedicated section between the cloud and manual sections). e.g. `192.168.1.42`. The integration skips mDNS and connects directly.

**Case 2 — multiple Dysons, some on a VLAN/subnet where mDNS doesn't work:**

If you have several fans and only some are unreachable via mDNS, you need a **per-device mapping** — set the IP only for the unreachable ones, leave the others to mDNS as before. Format:

```
SERIAL_OF_PROBLEM_FAN=ITS_IP
```

For two devices both on cross-VLAN networks:

```
AAA-XX-ZZZ0000A=192.168.1.42, BBB-YY-WWW1111B=192.168.5.10
```

- Separator: comma or newline (both work)
- Serials are case-insensitive
- **Each device needs its own correct IP** — paste the IP that fan currently has, then reserve that IP in your router's DHCP so it doesn't change
- Devices NOT listed continue to use mDNS as normal — leave reachable fans out of the mapping
- A plain IP without `SERIAL=` prefix still works for single-device setups; it's treated as "apply to every device"

**Important caveat for VLAN setups:** even with the static IP override, your router must still permit TCP traffic from the UCR3's subnet to the fan's subnet. Most consumer routers allow this by default, but some "guest network" or "IoT isolation" modes block inter-subnet TCP entirely — in which case no software fix works and you'd need to disable that isolation rule for the fan's IP.

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
