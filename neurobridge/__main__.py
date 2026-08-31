from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import sys

from .config import load
from .ble.flowtime import FlowtimeAdapter
from .business.gateway import Gateway
from .download import serve_downloads
from .logging_setup import configure_logging
from .northbound.websocket import serve

LOG = logging.getLogger(__name__)


async def run(config_path: str) -> None:
    config = load(config_path)
    configure_logging(config.logging)
    LOG.info(
        "Process starting: pid=%s python=%s platform=%s arch=%s config=%s",
        os.getpid(),
        sys.version.split()[0],
        platform.platform(),
        platform.machine(),
        config_path,
    )
    gateway = Gateway(config)
    # Keep the production runtime on the same adapter-error path as the macOS
    # POC.  The Linux deployment has no local control page, so the gateway
    # records a concise, durable operational error instead of silently losing
    # the reason after the adapter has scheduled its reconnect attempt.
    adapter = FlowtimeAdapter(
        gateway.config.ble,
        gateway.receive_packet,
        gateway.update_status,
        gateway.on_device_ready,
        gateway.update_connection_error,
    )
    await gateway.start()
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(serve(gateway))
            group.create_task(adapter.run())
            if gateway.config.download.enabled:
                group.create_task(serve_downloads(gateway))
    except Exception:
        LOG.exception("Gateway runtime task failed")
        raise
    finally:
        await adapter.stop()
        await gateway.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="NeuroBridge gateway")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.config))
    except Exception:
        # Logging is configured inside run after config validation.  Keep the
        # traceback visible to systemd when a malformed config fails earlier.
        logging.getLogger(__name__).exception("NeuroBridge process exited with an unhandled error")
        raise


if __name__ == "__main__":
    main()
