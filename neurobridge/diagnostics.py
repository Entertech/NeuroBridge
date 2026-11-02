"""Read-only Linux network snapshots; never probe the network or log payloads."""

from __future__ import annotations

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
