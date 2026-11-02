"""Maintain the configured wired IPv4 alias without replacing existing networks."""

from __future__ import annotations

import argparse
from dataclasses import replace
from contextlib import contextmanager
import ipaddress
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

from .config import GatewayConfig, load
from .network_config import DEFAULT_SUBNET_CIDR, physical_ethernet_interfaces, resolve_interface, validate_address
from .versioning import APPLICATION_VERSION

LOG = logging.getLogger(__name__)


def ensure_address(config: GatewayConfig, run: Callable = subprocess.run, sys_class_net: Path = Path("/sys/class/net")) -> dict:
    if not config.network.ensure_static_address:
        return {"action": "disabled"}
    if ipaddress.ip_address(config.server.host).is_loopback:
        return {"action": "loopback_skipped"}
    address, subnet = validate_address(config.server.host, config.network.subnet_cidr or DEFAULT_SUBNET_CIDR)
    interface = resolve_interface(config.network.interface, sys_class_net)
    if interface not in physical_ethernet_interfaces(sys_class_net):
        raise ValueError("fixed address requires a physical Ethernet interface; Wi-Fi and virtual interfaces are not eligible")

    def ip(*arguments: str) -> str:
        try:
            result = run(["ip", *arguments], check=True, capture_output=True, text=True, timeout=5)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"ip {' '.join(arguments)} failed: exitCode={exc.returncode} reason={(exc.stderr or '').strip()[:256]}") from exc
        return result.stdout

    links = json.loads(ip("-j", "address", "show"))
    link = next((item for item in links if item.get("ifname") == interface), None)
    if link is None:
        raise ValueError("selected Ethernet interface disappeared during address inspection")
    for item in links:
        if item.get("ifname") != interface and any(addr.get("local") == str(address) for addr in item.get("addr_info", [])):
            raise ValueError(f"configured address already belongs to another interface: {item.get('ifname')}")
    fixed = f"{address}/{subnet.prefixlen}"
    existing = sorted(f"{addr['local']}/{addr['prefixlen']}" for addr in link.get("addr_info", []) if addr.get("family") in {"inet", "inet6"})
    snapshot = {"interface": interface, "fixedAddress": fixed, "carrier": "LOWER_UP" in link.get("flags", []), "existingAddresses": existing}
    if fixed in existing:
        if "UP" not in link.get("flags", []):
            ip("link", "set", "dev", interface, "up")
            return {"action": "link_enabled", **snapshot}
        return {"action": "already_present", **snapshot}
    if any(addr.get("local") == str(address) for addr in link.get("addr_info", [])):
        raise ValueError("configured address exists with a different prefix; refusing to replace it")
    routes = json.loads(ip("-4", "-j", "route", "show", "table", "all"))
    for route in routes:
        destination = route.get("dst", "default")
        if destination == "default" or route.get("dev") is None:
            continue
        try:
            route_subnet = ipaddress.ip_network(destination, strict=False)
            if route_subnet.prefixlen == 0:
                continue
            overlaps = route_subnet.overlaps(subnet)
        except ValueError:
            continue
        if overlaps and (route.get("dev") != interface or route.get("gateway")):
            raise ValueError(f"fixed subnet overlaps an existing route: {route.get('dev')} {destination}")
    LOG.info("Wired address preparation: version=%s decision=append_only snapshot=%s defaultRoutes=%s", APPLICATION_VERSION, json.dumps(snapshot, separators=(",", ":")), json.dumps([route for route in routes if route.get("dst", "default") == "default"], separators=(",", ":")))
    if "UP" not in link.get("flags", []):
        ip("link", "set", "dev", interface, "up")
    # Only add the configured address. Never flush addresses, change DHCP,
    # restart a connection, or replace a default route or DNS setting.
    ip("-4", "address", "add", fixed, "dev", interface)
    after = json.loads(ip("-j", "address", "show"))
    if not any(item.get("ifname") == interface and any(addr.get("local") == str(address) and addr.get("prefixlen") == subnet.prefixlen for addr in item.get("addr_info", [])) for item in after):
        raise RuntimeError("fixed address was not present after add; inspect network-manager changes")
    return {"action": "added", **snapshot}


@contextmanager
def address_lock():
    # Runtime-directory ownership prevents an unprivileged service from
    # substituting this lock file. Serializes startup with the repair watcher.
    import fcntl
    directory = Path("/run/neurobridge-wired-address")
    directory.mkdir(mode=0o750, exist_ok=True)
    with (directory / "address.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/neurobridge/gateway.toml")
    parser.add_argument("--watch", action="store_true", help="repair the alias after link/network-manager changes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Wired address setup requires the root systemd helper on Linux.")
    config = load(args.config)
    previous: dict | None = None
    previous_error: str | None = None
    while True:
        started = time.monotonic()
        try:
            with address_lock():
                state = ensure_address(config)
            if state != previous or previous_error is not None:
                LOG.info("Wired address ready: version=%s elapsedMs=%s state=%s", APPLICATION_VERSION, round((time.monotonic() - started) * 1000), json.dumps(state, separators=(",", ":")))
            previous, previous_error = state, None
            if state.get("interface") and config.network.interface in (None, "auto"):
                # Keep watching the chosen physical port even if hotplug later
                # changes which other Ethernet interfaces are present.
                config = replace(config, network=replace(config.network, interface=state["interface"]))
            if not args.watch or state["action"] in {"disabled", "loopback_skipped"}:
                return
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            if reason != previous_error:
                LOG.error("Wired address setup failed: version=%s host=%s interface=%s reason=%s", APPLICATION_VERSION, config.server.host, config.network.interface, reason)
            previous_error = reason
            if not args.watch:
                raise SystemExit(1) from exc
        time.sleep(config.network.address_check_interval_seconds)


if __name__ == "__main__":
    main()
