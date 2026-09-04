from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from neurobridge.algorithm_setup import apply_algorithm_config, smoke_test_bridge
from neurobridge.config import load


class AlgorithmSetupTests(unittest.TestCase):
    @staticmethod
    def _bridge(root: Path, response: str = '{"algorithm":{}}') -> Path:
        bridge = root / "bridge"
        bridge.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "for _line in sys.stdin:\n"
            f"    print({response!r}, flush=True)\n",
            encoding="utf-8",
        )
        bridge.chmod(0o750)
        return bridge

    def test_smoke_test_reports_identity_without_payload_or_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._bridge(Path(directory))
            details = smoke_test_bridge(bridge)
            self.assertEqual(len(str(details["sha256"])), 64)
            self.assertEqual(details["responseFields"], ["algorithm"])
            self.assertEqual(details["stderrBytes"], 0)
            self.assertNotIn("eegRawBase64", str(details))

    def test_config_update_is_atomic_repeatable_and_preserves_other_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = self._bridge(root)
            config_path = root / "gateway.toml"
            config_path.write_text(
                '[server]\nhost = "127.0.0.1"\nport = 9001\npath = "/ws"\n\n'
                '[algorithm]\nenabled = false\n\n'
                '[data_source]\ntype = "serial"\n',
                encoding="utf-8",
            )
            backups = [apply_algorithm_config(config_path, bridge=bridge) for _ in range(2)]
            config = load(config_path)
            self.assertTrue(config.algorithm.enabled)
            self.assertEqual(config.algorithm.command, (str(bridge.resolve()),))
            self.assertEqual(config.server.port, 9001)
            self.assertEqual(config.data_source.type, "serial")
            self.assertEqual(config_path.read_text(encoding="utf-8").count("[algorithm]"), 1)
            self.assertTrue(all(backup.is_file() for backup in backups))
            self.assertEqual(len(set(backups)), 2)

    def test_smoke_test_rejects_bridge_reported_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = self._bridge(Path(directory), '{"algorithm":{},"bridgeError":"failed"}')
            with self.assertRaisesRegex(RuntimeError, "internal error"):
                smoke_test_bridge(bridge)
