from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from neurobridge.network_config import (
    MANAGED_MARKER,
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
            ensure_output_is_safe(output, "enp1s0")
