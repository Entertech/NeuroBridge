from __future__ import annotations

import ipaddress
from pathlib import Path
import subprocess
import tempfile
import unittest

from neurobridge.network_config import (
    MANAGED_MARKER,
    ensure_interface_can_be_retired,
    ensure_interface_is_available,
    declared_interfaces,
    ensure_output_is_safe,
    render_netplan,
    resolve_interface,
    validate_address,
)


class DedicatedNetworkConfigurationTests(unittest.TestCase):
    def _ethernet(self, root: Path, name: str) -> None:
        interface = root / name
        interface.mkdir()
        (interface / "type").write_text("1\n", encoding="ascii")
        (interface / "device").mkdir()

    def test_auto_selects_the_only_physical_ethernet_interface_even_when_unplugged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._ethernet(root, "enp1s0")
            self.assertEqual(resolve_interface("auto", root), "enp1s0")

    def test_auto_refuses_to_guess_between_multiple_physical_ethernet_interfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._ethernet(root, "enp1s0")
            self._ethernet(root, "enp2s0")
            with self.assertRaisesRegex(ValueError, "multiple physical Ethernet interfaces"):
                resolve_interface("auto", root)

    def test_netplan_fragment_is_route_free_and_disables_dhcp(self) -> None:
        address, subnet = validate_address("192.168.88.10", "192.168.88.0/24")
        rendered = render_netplan("enp1s0", address, subnet)
        self.assertTrue(rendered.startswith(MANAGED_MARKER))
        self.assertIn("dhcp4: false", rendered)
        self.assertIn("dhcp6: false", rendered)
        self.assertIn("link-local: []", rendered)
        self.assertIn("- 192.168.88.10/24", rendered)
        self.assertNotIn("gateway", rendered)
        self.assertNotIn("routes", rendered)
        self.assertNotIn("nameservers", rendered)

    def test_rejects_a_subnet_without_an_address_for_the_b_side(self) -> None:
        with self.assertRaisesRegex(ValueError, "both the gateway and B-side host"):
            validate_address("192.168.88.10", "192.168.88.10/31")

    def test_rejects_non_rfc1918_dedicated_link_addresses(self) -> None:
        addresses = (("127.0.0.1", "127.0.0.0/8"), ("169.254.1.1", "169.254.0.0/16"))
        for host, subnet in addresses:
            with self.subTest(host=host, subnet=subnet), self.assertRaisesRegex(ValueError, "RFC1918"):
                validate_address(host, subnet)

    def test_does_not_override_an_explicit_interface_in_another_netplan_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            netplan = Path(directory)
            (netplan / "50-cloud-init.yaml").write_text(
                "network:\n  version: 2\n  ethernets:\n    enp1s0:\n      dhcp4: true\n",
                encoding="utf-8",
            )
            output = netplan / "99-neurobridge-b-side.yaml"
            self.assertEqual(declared_interfaces(netplan, output), {"enp1s0"})
            with self.assertRaisesRegex(ValueError, "already configured"):
                ensure_output_is_safe(output, "enp1s0")

    def test_allows_replacing_only_its_own_managed_netplan_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "99-neurobridge-b-side.yaml"
            output.write_text(f"{MANAGED_MARKER}\nnetwork:\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot determine"):
                ensure_output_is_safe(output, "enp1s0")

    def test_refuses_to_move_a_managed_netplan_file_to_another_interface_without_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "99-neurobridge-b-side.yaml"
            output.write_text(
                render_netplan(
                    "enp1s0",
                    ipaddress.IPv4Address("192.168.88.10"),
                    ipaddress.IPv4Network("192.168.88.0/24"),
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "refusing to move"):
                ensure_output_is_safe(output, "enp2s0")
            self.assertEqual(ensure_output_is_safe(output, "enp2s0", True), "enp1s0")

    def test_refuses_to_retire_a_managed_interface_that_still_has_an_address(self) -> None:
        def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output = "2: enp1s0    inet 192.168.88.10/24 scope global enp1s0\n" if arguments[1] == "-o" else ""
            return subprocess.CompletedProcess(arguments, 0, output)

        with self.assertRaisesRegex(ValueError, "still has network state"):
            ensure_interface_can_be_retired("enp1s0", False, runner)
        ensure_interface_can_be_retired("enp1s0", True, runner)

    def test_refuses_to_replace_an_interface_with_an_existing_ipv4_address(self) -> None:
        def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output = "2: enp1s0    inet 10.0.0.25/24 scope global enp1s0\n" if arguments[1] == "-o" else ""
            return subprocess.CompletedProcess(arguments, 0, output)

        with self.assertRaisesRegex(ValueError, "existing IPv4 address"):
            ensure_interface_is_available(
                "enp1s0",
                ipaddress.IPv4Address("192.168.88.10"),
                ipaddress.IPv4Network("192.168.88.0/24"),
                False,
                runner,
            )

    def test_allows_a_repeat_application_when_only_the_managed_address_exists(self) -> None:
        def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output = "2: enp1s0    inet 192.168.88.10/24 scope global enp1s0\n" if arguments[1] == "-o" else ""
            return subprocess.CompletedProcess(arguments, 0, output)

        ensure_interface_is_available(
            "enp1s0",
            ipaddress.IPv4Address("192.168.88.10"),
            ipaddress.IPv4Network("192.168.88.0/24"),
            False,
            runner,
        )

    def test_refuses_to_replace_an_interface_with_a_default_route(self) -> None:
        def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output = "default via 10.0.0.1 dev enp1s0\n" if arguments[1] == "-4" else ""
            return subprocess.CompletedProcess(arguments, 0, output)

        with self.assertRaisesRegex(ValueError, "default route"):
            ensure_interface_is_available(
                "enp1s0",
                ipaddress.IPv4Address("192.168.88.10"),
                ipaddress.IPv4Network("192.168.88.0/24"),
                False,
                runner,
            )

    def test_refuses_to_replace_an_interface_with_an_existing_ipv6_address(self) -> None:
        def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output = ""
            if arguments[2] == "-6":
                output = "2: enp1s0    inet6 2001:db8::25/64 scope global enp1s0\\n"
            return subprocess.CompletedProcess(arguments, 0, output)

        with self.assertRaisesRegex(ValueError, "existing IPv6 address"):
            ensure_interface_is_available(
                "enp1s0",
                ipaddress.IPv4Address("192.168.88.10"),
                ipaddress.IPv4Network("192.168.88.0/24"),
                False,
                runner,
            )

    def test_refuses_to_retire_an_interface_with_an_ipv6_default_route(self) -> None:
        def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output = ""
            if arguments[1:3] == ["-6", "route"]:
                output = "default via 2001:db8::1 dev enp1s0\\n"
            return subprocess.CompletedProcess(arguments, 0, output)

        with self.assertRaisesRegex(ValueError, "still has network state"):
            ensure_interface_can_be_retired("enp1s0", False, runner)
