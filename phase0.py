"""
Phase 0 — single interactive run for iPhone/Termius.

Walks you through:
  1. Dyson cloud login (email + password + OTP)
  2. Lists devices found on your account
  3. Connects locally over MQTT to the TP09
  4. Reads state + sensors
  5. Toggles power off → on, speed, auto
  6. Disconnects

No env vars. No paste-back. Just answer the prompts.
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import sys
import time

from libdyson import get_device
from libdyson.cloud import DysonAccount
from libdyson.const import DEVICE_TYPE_NAMES


def banner(text: str) -> None:
    print(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def step(text: str) -> None:
    print(f"\n--- {text} ---")


def prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or (default or "")


def resolve_host(hostname: str) -> str | None:
    try:
        return socket.gethostbyname(hostname)
    except OSError:
        return None


def login() -> DysonAccount:
    banner("STEP 1 — Dyson cloud login (one time only)")
    email = prompt("Dyson account email")
    if not email:
        sys.exit("ERROR: email is required")
    password = getpass.getpass("Dyson account password (hidden): ")
    if not password:
        sys.exit("ERROR: password is required")
    region = prompt("Region (2-letter)", "GB")

    print(f"\nRequesting OTP for {email}...")
    account = DysonAccount()
    verify = account.login_email_otp(email, region)

    print(f"Dyson just emailed a 6-digit code to {email}.")
    otp = prompt("OTP code from email").strip()
    if not otp:
        sys.exit("ERROR: OTP is required")

    print("Verifying...")
    auth_info = verify(otp, password)
    return DysonAccount(auth_info)


def pick_device(account: DysonAccount):
    banner("STEP 2 — Devices on your account")
    devices = account.devices()
    if not devices:
        sys.exit("ERROR: no Dyson devices on this account")

    for i, d in enumerate(devices):
        type_name = DEVICE_TYPE_NAMES.get(d.product_type, "?")
        print(f"  [{i}] {d.name}  serial={d.serial}  type={d.product_type} ({type_name})")

    if len(devices) == 1:
        print("\nOnly one device — using it.")
        return devices[0]

    idx = prompt("Pick device number", "0")
    try:
        return devices[int(idx)]
    except (ValueError, IndexError):
        sys.exit("ERROR: invalid choice")


def smoke_test(d) -> None:
    banner("STEP 3 — Local MQTT smoke test")

    hostname = f"{d.serial.lower()}.local"
    print(f"Trying mDNS hostname: {hostname}")
    ip = resolve_host(hostname)
    if ip:
        print(f"  → {ip}")
    else:
        print("  mDNS lookup failed — fall back to manual entry")
        ip = prompt("TP09 LAN IP (e.g. 192.168.1.x)")
        if not ip:
            sys.exit("ERROR: need an IP")

    type_name = DEVICE_TYPE_NAMES.get(d.product_type, "?")
    print(f"\nlibdyson device class for {d.product_type} = {type_name}")
    device = get_device(d.serial, d.credential, d.product_type)
    if device is None:
        sys.exit(f"ERROR: libdyson does not recognise product_type={d.product_type}")

    msgs: list = []
    device.add_message_listener(lambda m: msgs.append(m))

    print(f"Connecting to {ip}...")
    try:
        device.connect(ip)
    except Exception as exc:
        sys.exit(f"ERROR: connect failed: {exc}")
    print("Connected. Waiting 3s for initial state...")
    time.sleep(3)

    step("STATE")
    for attr in ("is_on", "speed", "auto_mode", "night_mode", "oscillation",
                 "fan_mode", "fan_state", "humidity", "temperature",
                 "particulate_matter_2_5", "particulate_matter_10",
                 "volatile_organic_compounds", "nitrogen_dioxide",
                 "formaldehyde", "hepa_filter_life", "carbon_filter_life"):
        try:
            val = getattr(device, attr, None)
            print(f"  {attr}: {val}")
        except Exception as exc:
            print(f"  {attr}: <error: {exc}>")

    if prompt("\nRun command tests (power/speed/auto)? [y/N]", "n").lower() != "y":
        device.disconnect()
        print("Skipped command tests. Disconnected.")
        return

    step("TURN OFF")
    try:
        device.turn_off(); time.sleep(2); print(f"  is_on → {device.is_on}")
    except Exception as exc:
        print(f"  failed: {exc}")

    step("TURN ON")
    try:
        device.turn_on(); time.sleep(2); print(f"  is_on → {device.is_on}")
    except Exception as exc:
        print(f"  failed: {exc}")

    step("SET SPEED 3")
    try:
        device.set_fan_speed(3); time.sleep(2); print(f"  speed → {device.speed}")
    except Exception as exc:
        print(f"  failed: {exc}")

    step("ENABLE AUTO")
    try:
        device.enable_auto_mode(); time.sleep(2); print(f"  auto_mode → {device.auto_mode}")
    except Exception as exc:
        print(f"  failed: {exc}")

    device.disconnect()
    print(f"\nDisconnected. {len(msgs)} MQTT messages observed during run.")


def save_creds(devices_info: list[dict]) -> None:
    banner("STEP 4 — Save local creds for Phase 1")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creds.json")
    if os.path.exists(path):
        if prompt(f"{path} exists — overwrite? [y/N]", "n").lower() != "y":
            print("Skipped saving.")
            return
    with open(path, "w") as fh:
        json.dump(devices_info, fh, indent=2)
    os.chmod(path, 0o600)
    print(f"Saved → {path} (chmod 600)")
    print("This file holds your local MQTT password. Do NOT commit it.")


def main() -> int:
    try:
        account = login()
        device_info = pick_device(account)

        save_creds([{
            "serial": device_info.serial,
            "name": device_info.name,
            "product_type": device_info.product_type,
            "credential": device_info.credential,
        }])

        smoke_test(device_info)

        banner("PHASE 0 PASSED ✓")
        print("Next step: install the dyson-uc integration tarball on your UCR3.")
        return 0
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
