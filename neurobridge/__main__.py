from __future__ import annotations

import argparse
import asyncio

from .config import load
from .ble.flowtime import FlowtimeAdapter
from .business.gateway import Gateway
from .download import serve_downloads
from .logging_setup import configure_logging
from .northbound.websocket import serve


async def run(config_path: str) -> None:
    config = load(config_path)
    configure_logging(config.logging)
    gateway = Gateway(config)
    adapter = FlowtimeAdapter(gateway.config.ble, gateway.receive_packet, gateway.update_status, gateway.on_device_ready)
    await gateway.start()
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(serve(gateway))
            group.create_task(adapter.run())
            if gateway.config.download.enabled:
                group.create_task(serve_downloads(gateway))
    finally:
        await adapter.stop()
        await gateway.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="NeuroBridge gateway")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
