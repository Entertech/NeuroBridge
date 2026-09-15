from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest

from neurobridge.bootstrap import build_container
from neurobridge.config import load
from neurobridge.profiles.resolver import RuntimePlatform, resolve_profile


ROOT = Path(__file__).resolve().parents[2]


class ProfileContractTests(unittest.TestCase):
    def _config(self, text: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "gateway.toml"
        path.write_text(text, encoding="utf-8")
        return load(path)

    def test_kylin_profile_binds_serial_parser_and_disables_replay(self) -> None:
        config = self._config('config_schema_version = 1\nprofile = "kylin_headset_local"\n[data_source]\ntype = "serial"\n')
        profile = resolve_profile(config, RuntimePlatform("kylin", "x86_64"))
        self.assertFalse(profile.capabilities.supports_replay)
        self.assertEqual(profile.device_protocol, "headset_rev181")
        container = build_container(config, RuntimePlatform("kylin", "x86_64"))
        self.assertEqual(type(container.parser).__name__, "HeadsetRev181Parser")

    def test_profile_conflicts_fail_before_device_creation(self) -> None:
        config = self._config(
            'profile = "kylin_headset_local"\n[data_source]\ntype = "bluetooth"\n'
        )
        with self.assertRaisesRegex(ValueError, "transport expected=serial"):
            resolve_profile(config, RuntimePlatform("kylin", "x86_64"))


class DependencyDirectionTests(unittest.TestCase):
    def test_stable_kernel_has_no_concrete_io_imports(self) -> None:
        forbidden = {"bleak", "serial", "websockets", "neurobridge.adapters", "neurobridge.ble", "neurobridge.serial"}
        for package in ("domain", "ports", "application"):
            for path in (ROOT / "neurobridge" / package).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                imports = []
                current_parts = ["neurobridge", package, path.stem]
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        if node.level:
                            parent = current_parts[: -node.level]
                            imports.append(".".join([*parent, node.module]))
                        else:
                            imports.append(node.module)
                for name in imports:
                    self.assertFalse(any(name == item or name.startswith(item + ".") for item in forbidden), f"{path}: {name}")


    def test_application_does_not_assemble_wire_envelopes_or_branch_on_transport(self):
        path = ROOT / "neurobridge/application/gateway.py"
        source = path.read_text()
        tree = ast.parse(source)
        self.assertNotIn('"protocolVersion"', source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                self.assertNotIn("data_source.type", ast.unparse(node))
