from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from neurobridge.config import load
from neurobridge.serial_setup import apply_serial_config


class SerialSetupTests(unittest.TestCase):
    def test_one_command_configuration_preserves_unrelated_sections_and_is_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gateway.toml"
            path.write_text(
                '[server]\nhost = "127.0.0.1"\nport = 9001\npath = "/ws"\n'
                '[data_source]\ntype = "bluetooth"\n'
                '[serial]\ndevice = "/dev/old"\nhandshake_timeout_ms = 500\n',
                encoding="utf-8",
            )
            for _ in range(2):
                apply_serial_config(
                    path,
                    device="auto",
                    handshake_timeout_ms=1000,
                    command_response_timeout_ms=1200,
                    data_timeout_seconds=6,
                    reconnect_delay_seconds=4,
                    stats_interval_seconds=12,
                )
            config = load(path)
            self.assertEqual(config.server.port, 9001)
            self.assertEqual(config.server.path, "/ws")
            self.assertEqual(config.data_source.type, "serial")
            self.assertEqual(config.serial.device, "auto")
            self.assertEqual(config.serial.command_response_timeout_ms, 1200)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("[data_source]"), 1)
            self.assertEqual(text.count("[serial]"), 1)
