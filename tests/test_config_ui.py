from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from neurobridge.config_ui import ConfigUiController, config_to_api, document_from_api
from neurobridge.config import load


class ConfigUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.directory.name) / "gateway.toml"
        self.config_path.write_text(
            "[server]\nhost = \"192.168.88.10\"\nport = 8765\npath = \"/neurobridge/v1/ws\"\n"
            "[network]\nmode = \"static\"\ninterface = \"enp1s0\"\nsubnet_cidr = \"192.168.88.0/24\"\n"
            "[ble]\nenabled = false\ndevice_name = \"Flowtime Headband\"\nmodel_nbr_uuid = \"0000ff10-1212-abcd-1523-785feabcd123\"\nscan_timeout_seconds = 5\nreconnect_delay_seconds = 3\n"
            "[recording]\ndirectory = \"/var/lib/neurobridge/recordings\"\nsubject_id = \"\"\nreplay_recording_id = \"\"\nreplay_speed = 1\n"
            "[download]\nenabled = true\nhost = \"192.168.88.10\"\nport = 8766\npath = \"/downloads\"\n"
            "[logging]\ndirectory = \"/var/log/neurobridge\"\nfilename = \"neurobridge.log\"\nlevel = \"INFO\"\n"
            "[algorithm]\nenabled = true\ncommand = [\"/opt/controlled-bridge\", \"--safe\"]\n",
            encoding="utf-8",
        )
        self.commands: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            self.commands.append(command)
            if command[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(command, 0, "active\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        self.controller = ConfigUiController(self.config_path, command_runner=run, network_command="network-tool")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def request(self) -> dict[str, object]:
        return {"config": config_to_api(load(self.config_path)), "applyNetwork": False}

    def test_document_preserves_controlled_algorithm_command(self) -> None:
        document = document_from_api(self.request()["config"], self.controller._raw())
        self.assertIn('command = ["/opt/controlled-bridge", "--safe"]', document)

    def test_network_change_requires_explicit_apply(self) -> None:
        request = self.request()
        config = request["config"]
        assert isinstance(config, dict)
        config["server"]["host"] = "192.168.89.10"
        config["network"]["subnet_cidr"] = "192.168.89.0/24"
        with self.assertRaisesRegex(ValueError, "apply dedicated-link"):
            self.controller.save(request)
        self.assertEqual(load(self.config_path).server.host, "192.168.88.10")

    def test_save_validates_then_restarts_gateway_units(self) -> None:
        request = self.request()
        config = request["config"]
        assert isinstance(config, dict)
        config["recording"]["subject_id"] = "SUBJECT-001"
        result = self.controller.save(request)
        self.assertIn("Configuration saved", result["message"])
        self.assertEqual(load(self.config_path).recording.subject_id, "SUBJECT-001")
        self.assertIn(["systemctl", "restart", "neurobridge-dhcp.service"], self.commands)
        self.assertIn(["systemctl", "restart", "neurobridge.service"], self.commands)
        self.assertIn('command = ["/opt/controlled-bridge", "--safe"]', self.config_path.read_text(encoding="utf-8"))

    def test_failed_network_apply_restores_the_previous_configuration(self) -> None:
        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "network-tool":
                return subprocess.CompletedProcess(command, 1, "", "existing route blocks change")
            return subprocess.CompletedProcess(command, 0, "", "")

        controller = ConfigUiController(self.config_path, command_runner=run, network_command="network-tool")
        request = self.request()
        config = request["config"]
        assert isinstance(config, dict)
        config["server"]["host"] = "192.168.89.10"
        config["network"]["subnet_cidr"] = "192.168.89.0/24"
        request["applyNetwork"] = True
        with self.assertRaisesRegex(RuntimeError, "existing route blocks change"):
            controller.save(request)
        self.assertEqual(load(self.config_path).server.host, "192.168.88.10")

    def test_invalid_values_never_replace_current_configuration(self) -> None:
        request = self.request()
        config = request["config"]
        assert isinstance(config, dict)
        config["server"]["port"] = 70000
        with self.assertRaisesRegex(ValueError, "port"):
            self.controller.save(request)
        self.assertEqual(load(self.config_path).server.port, 8765)
