from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from neurobridge.config import load
from neurobridge.configuration.migration import migrate_file


class ConfigurationMigrationTests(unittest.TestCase):
    def test_layered_configuration_merges_defaults_system_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            defaults = root / "defaults.toml"
            system = root / "system.toml"
            override = root / "override.toml"
            defaults.write_text('[data_source]\ntype="serial"\n[server]\nhost="127.0.0.1"\nport=8765\n', encoding="utf-8")
            system.write_text('[server]\nport=9000\n[recording]\ndirectory="./system-recordings"\n', encoding="utf-8")
            override.write_text('[server]\nport=9100\n', encoding="utf-8")
            config = load(override, defaults_path=defaults, system_path=system)
            self.assertEqual(config.server.port, 9100)
            self.assertEqual(config.data_source.type, "serial")
            self.assertEqual(config.recording.directory, Path("./system-recordings"))

    def test_file_migration_is_atomic_backed_up_audited_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "gateway.toml"
            original = b'[data_source]\ntype = "serial"\n'
            path.write_bytes(original)
            self.assertTrue(migrate_file(path))
            self.assertIn("config_schema_version = 1", path.read_text(encoding="utf-8"))
            backups = list((root / "backups").glob("*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            self.assertEqual(len((root / "migration-history.jsonl").read_text(encoding="utf-8").splitlines()), 1)
            self.assertFalse(migrate_file(path))
            self.assertEqual(len(list((root / "backups").glob("*.bak"))), 1)
