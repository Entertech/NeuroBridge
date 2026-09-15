from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import logging
import os
from pathlib import Path
import platform
import sys
import signal
import time

from .configuration.runtime import load_runtime_config
from .bootstrap import build_container
from .download import serve_downloads
from .logging_setup import configure_logging
from .northbound.local_ui import serve_local_ui
from .northbound.strategy import access_strategy
from .northbound.websocket import serve
from .versioning import APPLICATION_VERSION

LOG = logging.getLogger(__name__)


def _file_sha256(path: str) -> str:
    """Identify the deployed configuration without logging its contents."""
    try:
        digest = sha256()
        with Path(path).open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        return f"unavailable:{type(error).__name__}"


async def run(config_path: str, *, override_path: str | None = None, development: bool = False) -> None:
    started_at = time.monotonic()
    config = load_runtime_config(config_path, override_path=override_path, development=development)
    container = build_container(config)
    strategy = access_strategy(config.access.mode)
    configure_logging(config.logging)
    LOG.info(
        "Process starting: appVersion=%s pid=%s python=%s platform=%s arch=%s kernel=%s config=%s configSha256=%s",
        APPLICATION_VERSION,
        os.getpid(),
        sys.version.split()[0],
        platform.platform(),
        platform.machine(),
        platform.release(),
        config_path,
        _file_sha256(config_path),
    )
    LOG.info(
        "Runtime configuration: profile=%s transport=%s deviceProtocol=%s accessMode=%s accessSummary=%s bleEnabled=%s scanTimeoutSeconds=%s reconnectDelaySeconds=%s "
        "server=%s:%s%s networkMode=%s networkInterface=%s subnet=%s downloadEnabled=%s "
        "downloadEndpoint=%s:%s%s recordingDirectory=%s replaySpeed=%s algorithmEnabled=%s "
        "algorithmCommand=%s loggingLevel=%s logFile=%s",
        container.profile.profile_id,
        config.data_source.type,
        container.profile.device_protocol,
        config.access.mode,
        strategy.summary(config),
        config.ble.enabled,
        config.ble.scan_timeout_seconds,
        config.ble.reconnect_delay_seconds,
        config.server.host,
        config.server.port,
        config.server.path,
        config.network.mode,
        config.network.interface,
        config.network.subnet_cidr,
        config.download.enabled,
        config.download.host,
        config.download.port,
        config.download.path,
        config.recording.directory,
        config.recording.replay_speed,
        config.algorithm.enabled,
        config.algorithm.command[0] if config.algorithm.command else None,
        config.logging.level,
        config.logging.directory / config.logging.filename,
    )
    if config.data_source.type == "serial":
        LOG.info(
            "Serial runtime configuration: device=%s candidateTypes=%s baudRate=%s streamObservationTimeoutMs=%s "
            "commandWriteTimeoutMs=%s dataTimeoutSeconds=%s reconnectDelaySeconds=%s "
            "statsIntervalSeconds=%s maxBufferBytes=%s dtr=%s rts=%s startupPolicy=direct_e1 validation=valid_28_byte_frame",
            config.serial.device,
            ",".join(config.serial.candidate_types),
            config.serial.baud_rate,
            config.serial.handshake_timeout_ms,
            config.serial.command_response_timeout_ms,
            config.serial.data_timeout_seconds,
            config.serial.reconnect_delay_seconds,
            config.serial.stats_interval_seconds,
            config.serial.max_buffer_bytes,
            config.serial.dtr,
            config.serial.rts,
        )
    gateway = container.gateway
    adapter = container.device_adapter
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    handles_sigterm = False
    if sys.platform != "win32" and task is not None:
        try:
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            handles_sigterm = True
        except (NotImplementedError, RuntimeError):
            pass  # Non-main-thread embedding keeps its own signal lifecycle.
    try:
        await gateway.start()
        async with asyncio.TaskGroup() as group:
            group.create_task(serve(gateway, container.northbound_controller))
            group.create_task(adapter.run())
            if strategy.serves_local_ui(config):
                group.create_task(serve_local_ui(config))
            if gateway.config.download.enabled:
                group.create_task(serve_downloads(gateway))
    except Exception:
        LOG.exception("Gateway runtime task failed")
        raise
    finally:
        LOG.info("Process shutdown started: uptimeSeconds=%.3f", time.monotonic() - started_at)
        try:
            await adapter.stop()
        finally:
            try:
                await gateway.stop()
            finally:
                if handles_sigterm:
                    loop.remove_signal_handler(signal.SIGTERM)
        LOG.info("Process shutdown completed: uptimeSeconds=%.3f", time.monotonic() - started_at)


def main() -> None:
    parser = argparse.ArgumentParser(description="NeuroBridge gateway")
    parser.add_argument("--config", required=True)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--override-config", help="Temporary override file; requires --development")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.config, override_path=args.override_config, development=args.development))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception:
        # Logging is configured inside run after config validation.  Keep the
        # traceback visible to systemd when a malformed config fails earlier.
        logging.getLogger(__name__).exception("NeuroBridge process exited with an unhandled error")
        raise


if __name__ == "__main__":
    main()
