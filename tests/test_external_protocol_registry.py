from __future__ import annotations

from pathlib import Path
import sys
import unittest


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from external_protocol_registry import load_registry, select_external_protocol  # noqa: E402


class ExternalProtocolRegistryTests(unittest.TestCase):
    def test_current_and_explicit_versions_resolve_to_the_stored_document(self) -> None:
        registry = load_registry()
        current = select_external_protocol(registry)
        numeric = select_external_protocol(registry, "0.2")
        prefixed = select_external_protocol(registry, "v0.2")

        self.assertEqual(current, numeric)
        self.assertEqual(numeric, prefixed)
        self.assertEqual(numeric["markdown_path"], "doc/tech/头环数据网关北向网络协议_v0.2.md")

    def test_unknown_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown external protocol version"):
            select_external_protocol(load_registry(), "9.9")
