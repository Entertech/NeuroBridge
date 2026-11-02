"""Allowlisted version and environment metadata for a running Python process."""

from datetime import datetime, timezone
from pathlib import Path
import platform
import struct
import sys

from .versioning import APPLICATION_VERSION


def _source_commit() -> str:
    root = Path(__file__).resolve().parent.parent
    commit = "unknown"
    try:
        for line in (root / "build-info.txt").read_text(encoding="utf-8").splitlines():
            if line.startswith("source_commit="):
                value = line.partition("=")[2]
                if len(value) == 40 and all(char in "0123456789abcdef" for char in value):
                    commit = value
    except (OSError, UnicodeError):
        pass
    return commit


# Read alongside module loading so an export from an old running process does
# not pick up a newly replaced build-info file and report the new commit.
SOURCE_COMMIT = _source_commit()


def runtime_context() -> dict:
    context = {
        "schemaVersion": 1,
        "diagnosticScope": "runtime",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "applicationVersion": APPLICATION_VERSION,
        "sourceCommit": SOURCE_COMMIT,
        "versionBasis": "loaded application module; environment of exporting process",
        "osName": platform.system(),
        "osVersion": platform.version(),
        "kernelVersion": platform.release(),
        "osArchitecture": platform.machine(),
        "pythonVersion": platform.python_version(),
        "pythonByteOrder": sys.byteorder,
        "pythonArchitecture": f"{struct.calcsize('P') * 8}-bit",
        "environmentQueryError": "",
    }
    try:
        if sys.platform == "win32":
            import os

            native = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE")
            context["osArchitecture"] = native or context["osArchitecture"]
            version = sys.getwindowsversion()
            context["osBuild"] = str(version.build)
            context["osVersion"] = f"{version.major}.{version.minor}.{version.build}"
            context["osName"] = "Windows " + platform.win32_ver()[0]
        elif sys.platform.startswith("linux"):
            release = platform.freedesktop_os_release()
            context.update(osId=release.get("ID", "unknown"), osName=release.get("PRETTY_NAME", "unknown"),
                           osVersion=release.get("VERSION", release.get("VERSION_ID", "unknown")),
                           osBuild=release.get("KYLIN_RELEASE_ID", release.get("BUILD_ID", "unknown")),
                           libcVersion=" ".join(platform.libc_ver()) or "unknown")
    except OSError as error:
        context["environmentQueryError"] = str(error)
    context["osBits"] = {"x86_64": 64, "AMD64": 64, "aarch64": 64, "ARM64": 64,
                         "loongarch64": 64, "loong64": 64, "mips64": 64, "mips64el": 64, "sw64": 64, "sw_64": 64,
                         "armv7l": 32, "armv8l": 32, "armhf": 32, "i386": 32, "i486": 32, "i586": 32, "i686": 32, "x86": 32}.get(context["osArchitecture"], "unknown")
    return context


"""Read-only Linux network snapshots; never probe the network or log payloads."""


import asyncio
import json
import logging
import sys
import time
from typing import Any

from .config import GatewayConfig

LOG = logging.getLogger(__name__)


async def _ip_json(*arguments: str) -> list[dict[str, Any]]:
    process = await asyncio.create_subprocess_exec(
        "ip", "-j", *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(2):
            output, _ = await process.communicate()
        if process.returncode != 0 or len(output) > 1024 * 1024:
            raise RuntimeError("ip diagnostic command failed or output exceeded limit")
        return json.loads(output)
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


def network_snapshot(addresses: list[dict], routes: list[dict], neighbours: list[dict], host: str, interface: str | None) -> dict:
    """Whitelist operational fields; omit hardware addresses and unrelated data."""
    links = []
    for item in addresses:
        name = item.get("ifname")
        if interface not in (None, "auto") and name != interface:
            continue
        links.append({
            "interface": name,
            "operstate": item.get("operstate"),
            "carrier": "LOWER_UP" in item.get("flags", []),
            "addresses": sorted(f"{addr['local']}/{addr['prefixlen']}" for addr in item.get("addr_info", []) if addr.get("family") == "inet"),
        })
    names = {item["interface"] for item in links}
    return {
        "hostAssigned": any(addr.get("local") == host for item in addresses for addr in item.get("addr_info", [])),
        "links": sorted(links, key=lambda item: item["interface"] or ""),
        "routes": [{key: item[key] for key in ("dst", "gateway", "dev", "metric", "protocol") if key in item} for item in routes if item.get("dev") in names],
        "neighbours": [{key: item[key] for key in ("dst", "dev", "state") if key in item} for item in neighbours if item.get("dev") in names],
    }


class NetworkDiagnostics:
    def __init__(self, config: GatewayConfig, boot_id: str) -> None:
        self.config, self.boot_id = config, boot_id
        self._requested = asyncio.Event()
        self._last_request = float("-inf")

    def request_snapshot(self, _reason: str) -> None:
        # Coalesce a burst of failed clients; never block a capture callback.
        if time.monotonic() - self._last_request >= 5:
            self._last_request = time.monotonic()
            self._requested.set()

    async def run(self) -> None:
        interval = self.config.logging.diagnostics_interval_seconds
        if not interval or sys.platform != "linux":
            return
        previous: dict | None = None
        reason = "startup"
        was_unavailable = False
        while True:
            try:
                addresses = await _ip_json("address", "show")
                routes = await _ip_json("route", "show")
                neighbours = await _ip_json("neigh", "show")
                snapshot = network_snapshot(addresses, routes, neighbours, self.config.server.host, self.config.network.interface)
                if snapshot != previous or reason != "poll":
                    LOG.info("Network diagnostic: bootId=%s reason=%s host=%s port=%s snapshot=%s", self.boot_id, reason, self.config.server.host, self.config.server.port, json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
                previous = snapshot
                was_unavailable = False
            except Exception as exc:
                if not was_unavailable:
                    LOG.warning("Network diagnostic unavailable: bootId=%s errorType=%s", self.boot_id, type(exc).__name__)
                was_unavailable = True
            try:
                await asyncio.wait_for(self._requested.wait(), timeout=interval)
                self._requested.clear()
                reason = "delivery_failure"
            except TimeoutError:
                reason = "poll"
