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
