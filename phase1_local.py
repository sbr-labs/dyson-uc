"""
Phase 1 local end-to-end test — exercises driver.py internals against the
real TP09 without needing a UCR3 in the loop.

Sequence:
  1. Reads saved creds.json from phase 0.
  2. Builds the entities driver.py would build at setup.
  3. Starts the DysonClient.
  4. Waits for the first state push from the fan.
  5. Prints what the entities WOULD report to the UCR3.
  6. Exercises a single fan-speed command end-to-end through the
     same _climate_cmd handler the UCR3 would invoke.
  7. Verifies the state actually changed.

If this passes, the daemon is ready for tarball packaging.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

# ucapi expects a config dir — point it at a temp location.
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".phase1_tmp")
os.makedirs(TMP, exist_ok=True)
os.environ.setdefault("UC_CONFIG_HOME", TMP)
os.environ.setdefault("DYSON_LOG_LEVEL", "INFO")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import driver  # noqa: E402  — must come after env tweaks
from ucapi import climate  # noqa: E402


class FakeEntities:
    """Stand-in for api.configured_entities. Records attribute updates so
    we can assert the driver pushed the right values."""

    def __init__(self) -> None:
        self.entities: dict[str, dict] = {}

    def add(self, entity) -> None:
        self.entities[entity.id] = {"entity": entity, "attrs": {}}

    def contains(self, eid: str) -> bool:
        return eid in self.entities

    def update_attributes(self, eid: str, attrs: dict) -> None:
        if eid not in self.entities:
            return
        self.entities[eid]["attrs"].update(attrs)

    def get_attrs(self, eid: str) -> dict:
        return self.entities.get(eid, {}).get("attrs", {})


class FakeApi:
    """Minimal stand-in for ucapi.IntegrationAPI. Just enough surface for
    driver.py's internals to function."""

    def __init__(self) -> None:
        self.config_dir_path = TMP
        self.available_entities = FakeEntities()
        self.configured_entities = FakeEntities()


async def run() -> int:
    creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "creds.json")
    if not os.path.exists(creds_path):
        print("ERROR: creds.json missing — run phase0.sh first", file=sys.stderr)
        return 1
    devs = json.load(open(creds_path))
    if not devs:
        print("ERROR: no devices in creds.json", file=sys.stderr)
        return 1
    dev = devs[0]
    print(f"Target: {dev['name']}  serial={dev['serial']}  type={dev['product_type']}")

    api = FakeApi()

    # Build entities (would normally happen at setup-complete time)
    entities = driver._build_entities(api, dev)
    for e in entities:
        api.available_entities.add(e)
        api.configured_entities.add(e)
    print(f"\nBuilt {len(entities)} entities:")
    for e in entities:
        print(f"  {e.id}  ({type(e).__name__})")

    # Start client → background MQTT connection
    driver._start_client(api, dev)
    print("\nClient started. Waiting 6s for connect + state push...")
    await asyncio.sleep(6)

    serial = dev["serial"]
    client = driver._clients.get(serial)
    if client is None or not client.connected:
        print("FAIL: client did not connect", file=sys.stderr)
        return 2
    print("Connected.")

    climate_id = f"{serial}__climate"
    print(f"\n--- ENTITY STATE (after first push) ---")
    print(f"climate: {api.configured_entities.get_attrs(climate_id)}")
    for key in ("night", "oscillation", "monitoring"):
        eid = f"{serial}__{key}"
        if api.configured_entities.contains(eid):
            print(f"{key}: {api.configured_entities.get_attrs(eid)}")
    for key in ("humidity", "pm25", "pm10", "voc", "no2", "hepa", "carbon", "hcho"):
        eid = f"{serial}__{key}"
        if api.configured_entities.contains(eid):
            print(f"{key}: {api.configured_entities.get_attrs(eid)}")

    # Now exercise the command handler the UCR3 would hit
    print("\n--- COMMAND TEST: FAN_MODE → 8 via _climate_cmd ---")
    fake_entity = SimpleNamespace(id=climate_id)
    code = await driver._climate_cmd(
        fake_entity,
        climate.Commands.FAN_MODE.value,
        {"fan_mode": "8"},
    )
    print(f"  status code: {code}")
    print("  waiting 6s for state to settle...")
    await asyncio.sleep(6)
    speed_after = client.device.speed if client.device else None
    print(f"  device.speed after: {speed_after}")
    speed_ok = speed_after == 8

    print("\n--- COMMAND TEST: HVAC_MODE → AUTO via _climate_cmd ---")
    code = await driver._climate_cmd(
        fake_entity,
        climate.Commands.HVAC_MODE.value,
        {"hvac_mode": "AUTO"},
    )
    print(f"  status code: {code}")
    await asyncio.sleep(4)
    auto_after = client.device.auto_mode if client.device else None
    print(f"  device.auto_mode after: {auto_after}")
    auto_ok = auto_after is True

    # Tear down
    print("\nStopping client...")
    await client.stop()
    driver._clients.pop(serial, None)

    print("\n=== RESULTS ===")
    print(f"  {'PASS' if speed_ok else 'FAIL'}  FAN_MODE → 8 lands on device")
    print(f"  {'PASS' if auto_ok else 'FAIL'}  HVAC_MODE → AUTO lands on device")
    ok = speed_ok and auto_ok
    print(f"\nOverall: {'ALL PASSED ✓' if ok else 'SOME FAILED ✗'}")
    return 0 if ok else 1


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
