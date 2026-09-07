from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from neurobridge.adapters.sources.serial_windows import discover_windows_com_candidates, open_windows_com
from neurobridge.bootstrap import build_container
from neurobridge.config import load
from neurobridge.profiles.resolver import RuntimePlatform


ROOT = Path(__file__).resolve().parents[2]


class Port:
    def __init__(self, device: str, *, vid=None, pid=None, hwid="") -> None:
        self.device = device
        self.vid = vid
        self.pid = pid
        self.hwid = hwid
        self.serial_number = None
        self.interface = None
        self.location = None


class PlatformProfileTests(unittest.TestCase):
    def test_all_platform_templates_bind_the_expected_source_and_parser(self) -> None:
        cases = (
            ("mac/gateway.capture.toml.example", RuntimePlatform("macos", "arm64"), "BluetoothBleakSource", "HeadbandBleParser"),
            ("config/gateway.ubuntu.toml.example", RuntimePlatform("ubuntu", "x86_64"), "BluetoothBleakSource", "HeadbandBleParser"),
            ("config/gateway.toml.example", RuntimePlatform("kylin", "x86_64"), "PosixSerialSource", "HeadsetRev181Parser"),
            ("windows/gateway.toml.example", RuntimePlatform("windows", "x86_64"), "WindowsSerialSource", "HeadsetRev181Parser"),
        )
        for relative, runtime, source_name, parser_name in cases:
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as directory:
                    config = load(ROOT / relative)
                    config = replace(config, recording=replace(config.recording, directory=Path(directory)))
                    container = build_container(config, runtime)
                    self.assertEqual(type(container.source).__name__, source_name)
                    self.assertEqual(type(container.parser).__name__, parser_name)

    def test_windows_discovery_keeps_only_usb_com_ports_in_numeric_order(self) -> None:
        ports = [
            Port("COM10", vid=1, pid=2),
            Port("COM2", hwid="USB VID:PID"),
            Port("COM1", hwid="ACPI"),
            Port("ttyUSB0", vid=1, pid=2),
        ]
        config = load(ROOT / "windows/gateway.toml.example").serial
        self.assertEqual(
            discover_windows_com_candidates(config, port_provider=lambda: ports),
            ["COM2", "COM10"],
        )

    def test_windows_com_applies_modem_lines_before_open(self) -> None:
        operations = []

        class Serial:
            def __init__(self, **options) -> None:
                self.options = options
                self._dtr = None
                self._rts = None
                self.port = None

            @property
            def dtr(self):
                return self._dtr

            @dtr.setter
            def dtr(self, value) -> None:
                self._dtr = value
                operations.append(("dtr", value))

            @property
            def rts(self):
                return self._rts

            @rts.setter
            def rts(self, value) -> None:
                self._rts = value
                operations.append(("rts", value))

            def open(self) -> None:
                operations.append(("open", self.port))

        serial_module = SimpleNamespace(
            Serial=Serial,
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
        )
        config = load(ROOT / "windows/gateway.toml.example").serial
        with patch.dict(sys.modules, {"serial": serial_module}):
            client = open_windows_com("COM10", config)
        self.assertEqual(client.options["port"], None)
        self.assertEqual(operations, [("dtr", False), ("rts", False), ("open", "COM10")])
