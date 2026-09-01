from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from neurobridge.config import DEFAULT_ALGORITHM_COMMAND, load
from neurobridge.northbound.strategy import access_strategy


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
                "[access]\nmode = \"wired_b_side\"\n"
                "[local_ui]\nenabled = false\n"
                "[server]\nhost = \"192.168.88.10\"\n"
                "[network]\nmode = \"static\"\ninterface = \"auto\"\nsubnet_cidr = \"192.168.88.0/24\"\n",
                encoding="utf-8",
            )
            config = load(path)
            self.assertEqual(config.network.interface, "auto")
            path.write_text(
                "[access]\nmode = \"wired_b_side\"\n"
                "[local_ui]\nenabled = false\n"
                "[server]\nhost = \"192.168.88.10\"\n"
                "[network]\nmode = \"dhcp\"\ninterface = \"auto\"\nsubnet_cidr = \"192.168.88.0/24\"\n"
                "dhcp_range_start = \"192.168.88.20\"\ndhcp_range_end = \"192.168.88.30\"\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must explicitly name"):
                load(path)


class AccessStrategyConfigurationTests(unittest.TestCase):
    def test_local_browser_is_the_loopback_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text("", encoding="utf-8")
            config = load(path)
            self.assertEqual(config.access.mode, "local_browser")
            self.assertEqual(config.server.host, "127.0.0.1")
            self.assertTrue(config.local_ui.enabled)
            self.assertEqual(config.local_ui.host, "127.0.0.1")
            self.assertIsNone(config.network.interface)

    def test_local_browser_rejects_external_bindings_and_dedicated_network_fields(self) -> None:
        cases = (
            ('[access]\nmode = "local_browser"\n[server]\nhost = "192.168.88.10"\n', "server.host"),
            ('[network]\nmode = "static"\ninterface = "enp1s0"\n', "network or DHCP fields"),
            ('[network]\nmode = "static"\ndhcp_range_start = "192.168.88.20"\n', "network or DHCP fields"),
            ('[local_ui]\nenabled = false\n', "requires local_ui.enabled"),
            ('[local_ui]\nport = 8765\n', "server.port"),
            ('[local_ui]\nport = 8766\n[download]\nenabled = true\n', "download.port"),
        )
        for contents, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "gateway.toml"
                path.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    load(path)

    def test_wired_strategy_preserves_private_network_client_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text(
                '[access]\nmode = "wired_b_side"\n'
                "[local_ui]\nenabled = false\n"
                '[server]\nhost = "192.168.88.10"\n'
                '[download]\nenabled = true\nhost = "192.168.88.10"\n',
                encoding="utf-8",
            )
            config = load(path)
            self.assertEqual(config.access.mode, "wired_b_side")
            self.assertFalse(config.local_ui.enabled)
            self.assertIsNone(access_strategy(config.access.mode).websocket_origins(config))

    def test_legacy_private_address_configuration_infers_wired_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text('[server]\nhost = "192.168.88.10"\n', encoding="utf-8")
            config = load(path)
            self.assertEqual(config.access.mode, "wired_b_side")
            self.assertFalse(config.local_ui.enabled)

    def test_unknown_access_strategy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text('[access]\nmode = "future_mode"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "access.mode"):
                load(path)
