from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from neurobridge.config import AlgorithmConfig, BleConfig, GatewayConfig, NetworkConfig, RecordingConfig, ServerConfig, load
from neurobridge.wired_address import ensure_address, main


class FakeNetwork:
    def __init__(self, addresses=(), flags=("UP", "LOWER_UP")):
        self.links = [{"ifname": "eth0", "flags": list(flags), "addr_info": [self.address(value) for value in addresses]}]
        self.routes = [{"dst": "default", "gateway": "10.20.30.1", "dev": "eth0"}]
        self.commands = []

    @staticmethod
    def address(value):
        item = ipaddress.ip_interface(value)
        return {"family": "inet" if item.version == 4 else "inet6", "local": str(item.ip), "prefixlen": item.network.prefixlen}

    def run(self, arguments, **kwargs):
        self.commands.append(arguments)
        if arguments == ["ip", "-j", "address", "show"]:
            return subprocess.CompletedProcess(arguments, 0, json.dumps(self.links))
        if arguments[:5] == ["ip", "-4", "-j", "route", "show"]:
            return subprocess.CompletedProcess(arguments, 0, json.dumps(self.routes))
        if arguments[:4] == ["ip", "-4", "address", "add"]:
            self.links[0]["addr_info"].append(self.address(arguments[4]))
        elif arguments == ["ip", "link", "set", "dev", "eth0", "up"]:
            self.links[0]["flags"].append("UP")
        else:
            raise AssertionError(f"Unexpected mutation: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, "")


class WiredAddressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sysfs = self.root / "interfaces"
        self.sysfs.mkdir()
        self.ethernet("eth0")
        self.config = GatewayConfig(ServerConfig("192.168.88.10", 8765, "/neurobridge/v1/ws"), BleConfig(False, None, "profile", 5, 3), RecordingConfig(self.root / "recordings", None, None, 1), AlgorithmConfig(False, ()), network=NetworkConfig(interface="auto", subnet_cidr="192.168.88.0/24"))

    def tearDown(self):
        self.temporary.cleanup()

    def ethernet(self, name):
        entry = self.sysfs / name
        entry.mkdir()
        (entry / "device").mkdir()
        (entry / "type").write_text("1")

    def test_offline_unplugged_interface_gets_fixed_ip_and_is_brought_up(self):
        network = FakeNetwork(flags=())
        result = ensure_address(self.config, network.run, self.sysfs)
        self.assertEqual(result["action"], "added")
        self.assertFalse(result["carrier"])
        self.assertIn("UP", network.links[0]["flags"])
        self.assertEqual(network.links[0]["addr_info"], [FakeNetwork.address("192.168.88.10/24")])

    def test_existing_lan_ipv6_and_default_route_are_preserved(self):
        network = FakeNetwork(["10.20.30.40/24", "2001:db8::25/64"])
        routes = json.dumps(network.routes)
        result = ensure_address(self.config, network.run, self.sysfs)
        self.assertEqual(result["action"], "added")
        self.assertEqual(network.links[0]["addr_info"], [FakeNetwork.address(value) for value in ("10.20.30.40/24", "2001:db8::25/64", "192.168.88.10/24")])
        self.assertEqual(json.dumps(network.routes), routes)

    def test_second_check_is_idempotent(self):
        network = FakeNetwork(["10.20.30.40/24"])
        ensure_address(self.config, network.run, self.sysfs)
        self.assertEqual(ensure_address(self.config, network.run, self.sysfs)["action"], "already_present")
        self.assertEqual(sum(command[1:4] == ["-4", "address", "add"] for command in network.commands), 1)

    def test_existing_fixed_address_on_down_port_is_brought_up(self):
        network = FakeNetwork(["192.168.88.10/24"], flags=())
        self.assertEqual(ensure_address(self.config, network.run, self.sysfs)["action"], "link_enabled")
        self.assertEqual(len(network.links[0]["addr_info"]), 1)

    def test_multiple_ports_are_not_guessed_and_wifi_is_not_eligible(self):
        self.ethernet("eth1")
        network = FakeNetwork()
        with self.assertRaisesRegex(ValueError, "multiple physical"):
            ensure_address(self.config, network.run, self.sysfs)
        (self.sysfs / "eth1" / "wireless").mkdir()
        config = replace(self.config, network=replace(self.config.network, interface="eth1"))
        with self.assertRaisesRegex(ValueError, "physical Ethernet"):
            ensure_address(config, network.run, self.sysfs)
        self.assertFalse(network.commands)

    def test_address_on_another_port_and_existing_wrong_prefix_are_rejected(self):
        for conflict in ("other_port", "wrong_prefix"):
            with self.subTest(conflict=conflict):
                network = FakeNetwork(["192.168.88.10/16"] if conflict == "wrong_prefix" else [])
                if conflict == "other_port":
                    network.links.append({"ifname": "wlan0", "addr_info": [FakeNetwork.address("192.168.88.10/24")]})
                with self.assertRaises(ValueError):
                    ensure_address(self.config, network.run, self.sysfs)
                self.assertFalse(any(command[1:4] == ["-4", "address", "add"] for command in network.commands))

    def test_other_interface_subnet_route_is_preserved_without_adding_conflict(self):
        network = FakeNetwork(["10.20.30.40/24"])
        network.routes.append({"dst": "192.168.88.0/24", "dev": "wlan0"})
        with self.assertRaisesRegex(ValueError, "overlaps"):
            ensure_address(self.config, network.run, self.sysfs)
        self.assertEqual(len(network.links[0]["addr_info"]), 1)

    def test_same_port_routed_subnet_is_not_replaced_by_a_connected_route(self):
        network = FakeNetwork(["10.20.30.40/24"])
        network.routes.append({"dst": "192.168.88.0/24", "dev": "eth0", "gateway": "10.20.30.1"})
        with self.assertRaisesRegex(ValueError, "overlaps"):
            ensure_address(self.config, network.run, self.sysfs)
        self.assertEqual(len(network.links[0]["addr_info"]), 1)

    def test_disabled_and_loopback_modes_do_not_inspect_or_modify_network(self):
        network = FakeNetwork()
        disabled = replace(self.config, network=replace(self.config.network, ensure_static_address=False))
        self.assertEqual(ensure_address(disabled, network.run, self.sysfs)["action"], "disabled")
        loopback = replace(self.config, server=replace(self.config.server, host="127.0.0.1"))
        self.assertEqual(ensure_address(loopback, network.run, self.sysfs)["action"], "loopback_skipped")
        self.assertFalse(network.commands)

    def test_watch_repairs_removed_alias_and_pins_the_selected_port(self):
        network = FakeNetwork(["10.20.30.40/24"])
        sleeps = 0
        def sleep(_):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 1:
                network.links[0]["addr_info"] = [FakeNetwork.address("10.20.30.40/24")]
                self.ethernet("eth1")
            else:
                raise KeyboardInterrupt
        with patch("neurobridge.wired_address.load", return_value=self.config), patch("neurobridge.wired_address.sys.platform", "linux"), patch("neurobridge.wired_address.os.geteuid", return_value=0), patch("neurobridge.wired_address.address_lock", nullcontext), patch("neurobridge.wired_address.time.sleep", sleep), patch("neurobridge.wired_address.ensure_address", side_effect=lambda config: ensure_address(config, network.run, self.sysfs)):
            with self.assertRaises(KeyboardInterrupt):
                main(["--watch"])
        self.assertEqual(sum(command[1:4] == ["-4", "address", "add"] for command in network.commands), 2)
        self.assertIn(FakeNetwork.address("10.20.30.40/24"), network.links[0]["addr_info"])

    def test_non_root_launch_does_not_attempt_network_changes(self):
        with patch("neurobridge.wired_address.sys.platform", "linux"), patch("neurobridge.wired_address.os.geteuid", return_value=1000):
            with self.assertRaisesRegex(SystemExit, "root systemd helper"):
                main([])

    def test_new_configuration_defaults_and_validation(self):
        path = self.root / "gateway.toml"
        path.write_text("")
        self.assertTrue(load(path).network.ensure_static_address)
        self.assertEqual(load(path).network.address_check_interval_seconds, 5)
        for value in ("0", "nan", "inf", "3601"):
            path.write_text(f"[network]\naddress_check_interval_seconds = {value}\n")
            with self.assertRaises(ValueError):
                load(path)


if __name__ == "__main__":
    unittest.main()
