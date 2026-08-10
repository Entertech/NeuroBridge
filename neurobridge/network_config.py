"""Safely configure the dedicated B-side Ethernet link without DHCP."""

from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable

from neurobridge.config import load


DEFAULT_SUBNET_CIDR = "192.168.88.0/24"
MANAGED_MARKER = "# Managed by NeuroBridge; do not edit."
INTERFACE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}\Z")


def physical_ethernet_interfaces(sys_class_net: Path = Path("/sys/class/net")) -> list[str]:
    """Return physical Ethernet interfaces, including an unplugged USB NIC."""
    candidates: list[str] = []
    if not sys_class_net.is_dir():
        return candidates
    for entry in sys_class_net.iterdir():
        if entry.name == "lo" or (entry / "wireless").exists() or not (entry / "device").exists():
            continue
        try:
            interface_type = (entry / "type").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if interface_type == "1":
            candidates.append(entry.name)
    return sorted(candidates)


def resolve_interface(configured_interface: str | None, sys_class_net: Path = Path("/sys/class/net")) -> str:
    """Resolve ``auto`` only when it has one unambiguous physical Ethernet NIC."""
    if configured_interface and configured_interface != "auto":
        if INTERFACE_PATTERN.fullmatch(configured_interface) is None:
            raise ValueError("network.interface is invalid")
        if not (sys_class_net / configured_interface).is_dir():
            raise ValueError(f"configured network interface does not exist: {configured_interface}")
        if configured_interface == "lo":
            raise ValueError("network.interface must be a dedicated Ethernet interface, not lo")
        return configured_interface
    candidates = physical_ethernet_interfaces(sys_class_net)
    if not candidates:
        raise ValueError("could not find a physical Ethernet interface; set network.interface explicitly")
    if len(candidates) != 1:
        raise ValueError(
            "found multiple physical Ethernet interfaces ("
            + ", ".join(candidates)
            + "); set network.interface explicitly to protect the management network"
        )
    return candidates[0]


def validate_address(host: str, subnet_cidr: str) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Network]:
    try:
        address = ipaddress.ip_address(host)
        subnet = ipaddress.ip_network(subnet_cidr, strict=True)
    except ValueError as exc:
        raise ValueError("server.host or network.subnet_cidr is invalid") from exc
    if not isinstance(address, ipaddress.IPv4Address) or not isinstance(subnet, ipaddress.IPv4Network):
        raise ValueError("the dedicated B-side link requires IPv4")
    if subnet.prefixlen > 30:
        raise ValueError("network.subnet_cidr must leave usable addresses for both the gateway and B-side host")
    if not address.is_private or address not in subnet or address in {subnet.network_address, subnet.broadcast_address}:
        raise ValueError("network.subnet_cidr must contain server.host as a usable private IPv4 address")
    return address, subnet


def render_netplan(interface: str, address: ipaddress.IPv4Address, subnet: ipaddress.IPv4Network) -> str:
    """Render a standalone, route-free Netplan fragment for the dedicated link."""
    return (
        f"{MANAGED_MARKER}\n"
        "network:\n"
        "  version: 2\n"
        "  ethernets:\n"
        f"    {interface}:\n"
        "      dhcp4: false\n"
        "      dhcp6: false\n"
        "      link-local: []\n"
        "      optional: true\n"
        "      addresses:\n"
        f"        - {address}/{subnet.prefixlen}\n"
    )


def declared_interfaces(netplan_directory: Path, ignored_file: Path) -> set[str]:
    """Find explicit interface keys in other Netplan files.

    This deliberately errs on the safe side: an explicit duplicate is an
    operator-owned configuration and must not be overridden by this tool.
    """
    result: set[str] = set()
    for path in sorted((*netplan_directory.glob("*.yaml"), *netplan_directory.glob("*.yml"))):
        if path == ignored_file:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"cannot read existing Netplan file: {path}") from exc
        ethernets_indent: int | None = None
        for line in lines:
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            if re.fullmatch(r"ethernets:\s*(?:#.*)?", stripped):
                ethernets_indent = indent
                continue
            if ethernets_indent is None:
                continue
            if stripped and not stripped.startswith("#") and indent <= ethernets_indent:
                ethernets_indent = None
                continue
            if indent == ethernets_indent + 2:
                match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]{0,14}):\s*(?:#.*)?", stripped)
                if match:
                    result.add(match.group(1))
    return result


def ensure_output_is_safe(output: Path, interface: str) -> None:
    if output.exists():
        try:
            first_line = output.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError) as exc:
            raise ValueError(f"cannot verify existing Netplan file ownership: {output}") from exc
        if first_line != MANAGED_MARKER:
            raise ValueError(f"refusing to overwrite unmanaged Netplan file: {output}")
    elif interface in declared_interfaces(output.parent, output):
        raise ValueError(
            f"{interface} is already configured by another Netplan file; "
            "choose the dedicated interface explicitly or remove the conflicting deployment configuration"
        )


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def active_network_state(
    interface: str,
    expected_address: ipaddress.IPv4Interface,
    run: CommandRunner = subprocess.run,
) -> tuple[set[ipaddress.IPv4Interface], bool]:
    """Return global IPv4 addresses and whether the link has a default route.

    A carrier is deliberately not inspected: a direct B-side cable also has a
    carrier. Existing layer-3 state is the reliable indication that the host
    has already put this interface into service for another network.
    """
    try:
        addresses_result = run(
            ["ip", "-o", "-4", "addr", "show", "dev", interface, "scope", "global"],
            capture_output=True,
            check=False,
            text=True,
        )
        routes_result = run(
            ["ip", "-4", "route", "show", "default", "dev", interface],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        raise ValueError("cannot inspect the current network state; refusing to reconfigure the interface") from exc
    if addresses_result.returncode != 0 or routes_result.returncode != 0:
        raise ValueError("cannot inspect the current network state; refusing to reconfigure the interface")
    addresses: set[ipaddress.IPv4Interface] = set()
    for match in re.finditer(r"\binet\s+([^\s]+)", addresses_result.stdout):
        try:
            addresses.add(ipaddress.IPv4Interface(match.group(1)))
        except ipaddress.AddressValueError:
            continue
    has_default_route = bool(routes_result.stdout.strip())
    return addresses, has_default_route


def ensure_interface_is_available(
    interface: str,
    address: ipaddress.IPv4Address,
    subnet: ipaddress.IPv4Network,
    force: bool,
    run: CommandRunner = subprocess.run,
) -> None:
    """Refuse to disturb a live management or deployment network by default."""
    expected = ipaddress.IPv4Interface(f"{address}/{subnet.prefixlen}")
    addresses, has_default_route = active_network_state(interface, expected, run)
    other_addresses = addresses - {expected}
    if not has_default_route and not other_addresses:
        return
    if force:
        return
    details: list[str] = []
    if other_addresses:
        details.append("existing IPv4 address " + ", ".join(str(item) for item in sorted(other_addresses, key=str)))
    if has_default_route:
        details.append("an existing default route")
    raise ValueError(
        f"refusing to reconfigure active interface {interface} ({'; '.join(details)}). "
        "Disconnect it from the current network or rerun manually with --replace-active after confirming this is the dedicated B-side link"
    )


def write_then_apply(output: Path, content: str) -> None:
    """Generate first and restore the prior file if Netplan rejects or cannot apply it."""
    previous = output.read_bytes() if output.exists() else None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o600)
    temporary_path.replace(output)
    try:
        subprocess.run(["netplan", "generate"], check=True)
        subprocess.run(["netplan", "apply"], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        if previous is None:
            output.unlink(missing_ok=True)
        else:
            output.write_bytes(previous)
            output.chmod(0o600)
        try:
            subprocess.run(["netplan", "apply"], check=True)
        except (OSError, subprocess.CalledProcessError):
            pass
        raise RuntimeError("Netplan rejected or could not apply the new dedicated-link configuration; previous file was restored") from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Configure NeuroBridge's dedicated B-side Ethernet link")
    parser.add_argument("--config", default="/etc/neurobridge/gateway.toml")
    parser.add_argument("--output", default="/etc/netplan/99-neurobridge-b-side.yaml")
    parser.add_argument("--apply", action="store_true", help="write the managed Netplan file and apply it")
    parser.add_argument(
        "--replace-active",
        action="store_true",
        help="allow replacement of an interface that already has IPv4 state; requires --apply",
    )
    arguments = parser.parse_args(argv)
    if arguments.apply and os.geteuid() != 0:
        raise SystemExit("Run with sudo when using --apply.")
    if arguments.replace_active and not arguments.apply:
        raise SystemExit("--replace-active requires --apply.")

    config = load(arguments.config)
    # Preserve upgraded deployments that predate automatic link management:
    # they intentionally have neither field and may own their Netplan file.
    if config.network.interface is None and config.network.subnet_cidr is None:
        print(
            "Automatic dedicated-link configuration skipped because this existing gateway.toml has no network.interface or network.subnet_cidr. "
            "Add both fields to opt in.",
            file=sys.stderr,
        )
        return
    interface = resolve_interface(config.network.interface)
    subnet_cidr = config.network.subnet_cidr or DEFAULT_SUBNET_CIDR
    address, subnet = validate_address(config.server.host, subnet_cidr)
    ensure_interface_is_available(interface, address, subnet, arguments.replace_active)
    output = Path(arguments.output)
    ensure_output_is_safe(output, interface)
    content = render_netplan(interface, address, subnet)
    if not arguments.apply:
        print(content, end="")
        print("Configuration is valid. Re-run with --apply to write and activate it.", file=sys.stderr)
        return
    write_then_apply(output, content)
    print(f"Configured dedicated Ethernet interface {interface} with {address}/{subnet.prefixlen}.")


if __name__ == "__main__":  # pragma: no cover - exercised through the console entry point
    main()
