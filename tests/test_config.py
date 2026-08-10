from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from neurobridge.config import DEFAULT_ALGORITHM_COMMAND, load


class AlgorithmConfigurationTests(unittest.TestCase):
    def test_algorithm_is_enabled_and_uses_installed_bridge_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text("", encoding="utf-8")
            algorithm = load(path).algorithm
            self.assertTrue(algorithm.enabled)
            self.assertEqual(algorithm.command, DEFAULT_ALGORITHM_COMMAND)

    def test_explicit_algorithm_command_overrides_installed_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text('[algorithm]\nenabled = true\ncommand = ["/opt/bridge"]\n', encoding="utf-8")
            self.assertEqual(load(path).algorithm.command, ("/opt/bridge",))

    def test_static_network_allows_auto_interface_but_dhcp_requires_a_named_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text(
                "[server]\nhost = \"192.168.88.10\"\n"
                "[network]\nmode = \"static\"\ninterface = \"auto\"\nsubnet_cidr = \"192.168.88.0/24\"\n",
                encoding="utf-8",
            )
            config = load(path)
            self.assertEqual(config.network.interface, "auto")
            path.write_text(
                "[server]\nhost = \"192.168.88.10\"\n"
                "[network]\nmode = \"dhcp\"\ninterface = \"auto\"\nsubnet_cidr = \"192.168.88.0/24\"\n"
                "dhcp_range_start = \"192.168.88.20\"\ndhcp_range_end = \"192.168.88.30\"\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must explicitly name"):
                load(path)
