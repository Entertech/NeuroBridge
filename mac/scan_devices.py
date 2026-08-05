#!/usr/bin/env python3
"""List visible BLE advertisement names and service UUIDs for local POC matching."""

from __future__ import annotations

import argparse
import asyncio


async def scan(seconds: float) -> None:
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=seconds)
    print(f"visible_devices: {len(devices)}")
    for index, device in enumerate(devices, start=1):
        metadata = getattr(device, "metadata", {}) or {}
        uuids = sorted(str(value).lower() for value in metadata.get("uuids", []))
        print(f"device_{index}: name={getattr(device, 'name', None)!r} rssi={getattr(device, 'rssi', None)!r}")
        print(f"  service_uuids={','.join(uuids) or '(not advertised)'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Perform a read-only local BLE scan without printing device addresses.")
    parser.add_argument("--seconds", type=float, default=8)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30:
        parser.error("seconds must be between 1 and 30")
    asyncio.run(scan(args.seconds))


if __name__ == "__main__":
    main()
