"""
Phase 0 diagnostic — figure out WHY commands didn't visibly work.

Loads saved creds, connects, then for each command:
  1. Prints state BEFORE
  2. Sends command
  3. Waits 5s
  4. Prints state AFTER
  5. Reports PASS/FAIL based on whether the relevant attribute changed

Most likely failure mode: set_speed is silently ignored while the
fan is in AUTO mode. We explicitly disable_auto_mode first.

Pauses between steps so you can listen / look at the fan.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time

from libdyson import get_device
from libdyson.const import DEVICE_TYPE_NAMES


def get_state(d) -> dict:
    return {
        "is_on": getattr(d, "is_on", None),
        "speed": getattr(d, "speed", None),
        "auto_mode": getattr(d, "auto_mode", None),
        "night_mode": getattr(d, "night_mode", None),
        "oscillation": getattr(d, "oscillation", None),
        "fan_mode": str(getattr(d, "fan_mode", None)),
        "fan_state": str(getattr(d, "fan_state", None)),
    }


def show(label: str, s: dict) -> None:
    parts = [f"{k}={v}" for k, v in s.items()]
    print(f"  {label}: {' | '.join(parts)}")


def step(num: int, what: str, fn, attr: str, expected_change: str, d, wait: float = 5.0) -> bool:
    print(f"\n[{num}] {what}")
    before = get_state(d)
    show("before", before)
    try:
        fn()
    except Exception as exc:
        print(f"  CALL FAILED: {exc}")
        return False
    print(f"  waiting {wait}s for state to settle...")
    time.sleep(wait)
    after = get_state(d)
    show("after ", after)
    changed = before.get(attr) != after.get(attr)
    verdict = "PASS" if changed else "FAIL"
    print(f"  → {verdict}: {attr} {before.get(attr)} → {after.get(attr)}  (expected: {expected_change})")
    return changed


def main() -> int:
    creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creds.json")
    if not os.path.exists(creds_path):
        sys.exit("ERROR: creds.json missing — run phase0.sh first")
    devs = json.load(open(creds_path))
    if not devs:
        sys.exit("ERROR: no devices in creds.json")
    dev = devs[0]

    serial = dev["serial"]
    cred = dev["credential"]
    pt = dev["product_type"]
    print(f"Device: {dev['name']}  serial={serial}  type={pt} ({DEVICE_TYPE_NAMES.get(pt, '?')})")

    hostname = f"{serial.lower()}.local"
    try:
        ip = socket.gethostbyname(hostname)
    except OSError:
        ip = input(f"mDNS lookup failed. Enter TP09 LAN IP: ").strip()
    print(f"Host: {ip}")

    device = get_device(serial, cred, pt)
    if device is None:
        sys.exit(f"ERROR: libdyson does not recognise {pt}")

    msg_count = [0]
    device.add_message_listener(lambda _m: msg_count.__setitem__(0, msg_count[0] + 1))

    print("\nConnecting...")
    device.connect(ip)
    print("Connected. Waiting 4s for initial state push...")
    time.sleep(4)
    print(f"\nMessages received during connect: {msg_count[0]}")

    print("\n=== INITIAL STATE ===")
    show("state", get_state(device))

    results = []

    # 1. Power ON if currently off (so we can hear later commands)
    if not device.is_on:
        results.append(("turn_on", step(
            1, "turn_on", device.turn_on, "is_on", "True", device, wait=4
        )))
    else:
        print("\n[1] Skipping turn_on (already on)")

    # 2. Disable auto mode (otherwise speed changes are silently overridden)
    results.append(("disable_auto_mode", step(
        2, "disable_auto_mode", device.disable_auto_mode, "auto_mode", "False", device, wait=4
    )))

    # 3. Set fan speed 1 — should be quiet/slow
    print("\n[3] set_speed(1) — fan should drop to slowest")
    results.append(("speed→1", step(
        3, "set_speed(1)", lambda: device.set_speed(1), "speed", "1", device, wait=6
    )))

    # 4. Set fan speed 10 — should be loud, audibly distinct from speed 1
    print("\n[4] set_speed(10) — fan should ramp to MAX (loud)")
    results.append(("speed→10", step(
        4, "set_speed(10)", lambda: device.set_speed(10), "speed", "10", device, wait=8
    )))

    # 5. Toggle oscillation (visually obvious)
    starting_osc = device.oscillation
    target = not starting_osc
    osc_fn = device.enable_oscillation if target else device.disable_oscillation
    print(f"\n[5] toggle oscillation ({starting_osc} → {target}) — head should start/stop swinging")
    results.append(("oscillation toggle", step(
        5, f"oscillation→{target}", osc_fn, "oscillation", str(target), device, wait=5
    )))

    # 6. Back to auto, calm things down
    print("\n[6] enable_auto_mode — fan returns to auto")
    results.append(("enable_auto_mode", step(
        6, "enable_auto_mode", device.enable_auto_mode, "auto_mode", "True", device, wait=4
    )))

    device.disconnect()
    print(f"\nTotal MQTT messages: {msg_count[0]}")

    print("\n=== RESULTS ===")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    all_passed = all(ok for _, ok in results)
    print(f"\nOverall: {'ALL PASSED ✓' if all_passed else 'SOME FAILED ✗'}")

    if not all_passed:
        print("\nDiagnosis hints:")
        for name, ok in results:
            if ok:
                continue
            if "speed" in name:
                print(f"  - {name}: device may need disable_auto_mode first, OR product_type {pt} is wrong")
            elif "auto_mode" in name:
                print(f"  - {name}: command sent but state didn't update — may indicate MQTT publish silently dropped")
            elif "oscillation" in name:
                print(f"  - {name}: this model may use angle-based oscillation (newer firmware)")
            else:
                print(f"  - {name}: state attribute didn't update; check libdyson supports this for {pt}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
