from __future__ import annotations

import argparse
import asyncio
import logging

from .config import load
from .flowtime import FlowtimeAdapter
from .gateway import Gateway
from .server import serve


async def run(config_path: str) -> None:
    gateway = Gateway(load(config_path))
    adapter = FlowtimeAdapter(gateway.config.ble, gateway.receive_packet, gateway.update_status)
    await gateway.start()
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(serve(gateway))
            group.create_task(adapter.run())
    finally:
        await adapter.stop()
        await gateway.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="NeuroBridge gateway")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
