from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from neurobridge.config import (
    AccessConfig,
    AlgorithmConfig,
    BleConfig,
    GatewayConfig,
    LocalUiConfig,
    NetworkConfig,
    RecordingConfig,
    ServerConfig,
    load,
)
from neurobridge.dhcp import render_dnsmasq_config


def gateway_config(mode: str = "dhcp") -> GatewayConfig:
    return GatewayConfig(
        ServerConfig("192.168.88.10", 8765, "/neurobridge/v1/ws"),
        BleConfig(False, None, "0000ff10-1212-abcd-1523-785feabcd123", 5, 3),
        RecordingConfig(Path("/var/lib/neurobridge/recordings"), None, None, 1),
        AlgorithmConfig(False, ()),
        network=NetworkConfig(mode, "enp1s0", "192.168.88.0/24", "192.168.88.20", "192.168.88.200", "12h"),
        access=AccessConfig("wired_b_side"),
        local_ui=LocalUiConfig(False),
    )


class DhcpConfigurationTests(unittest.TestCase):
    def test_dhcp_mode_renders_an_interface_bound_router_advertisement(self) -> None:
        rendered = render_dnsmasq_config(gateway_config())
        self.assertIn("interface=enp1s0", rendered)
        self.assertIn("port=0", rendered)
        self.assertIn("user=neurobridge", rendered)
        self.assertIn("log-facility=/var/log/neurobridge/dhcp.log", rendered)
        self.assertIn("dhcp-range=192.168.88.20,192.168.88.200,255.255.255.0,12h", rendered)
        self.assertIn("dhcp-option=option:router,192.168.88.10", rendered)

    def test_static_mode_has_no_dhcp_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "network.mode is static"):
            render_dnsmasq_config(gateway_config("static"))

    def test_dhcp_mode_requires_a_range_inside_gateway_subnet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "gateway.toml"
            config_path.write_text(
                "\n".join((
                    "[data_source]", 'type = "bluetooth"', "",
                    "[access]", 'mode = "wired_b_side"', "",
                    "[local_ui]", "enabled = false", "",
                    "[server]", 'host = "192.168.88.10"', "",
                    "[network]", 'mode = "dhcp"', 'interface = "enp1s0"',
                    'subnet_cidr = "192.168.88.0/24"',
                    'dhcp_range_start = "192.168.89.20"', 'dhcp_range_end = "192.168.89.200"', "",
                )),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "DHCP range"):
                load(config_path)
