from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from tools import release_preflight


class ReleasePreflightTests(unittest.TestCase):
    def test_plan_reports_wire_change_without_promoting_the_document(self):
        source = "1" * 40
        baseline = "2" * 40
        registry = '''
[application]
version = "0.3.2"
[northbound_wire_protocol]
version = "1.0"
[protocol_lifecycle]
released_version = "0.2"
prerelease_version = "0.3"
[documents.external_northbound]
current_version = "0.2"
title = "Northbound"
delivery = "always"
[[documents.external_northbound.versions]]
version = "0.2"
status = "published"
markdown_path = "protocol.md"
pdf_artifact_name = "protocol.pdf"
[documents.external_windows_operations]
version = "1.2"
status = "unpublished"
title = "Windows guide"
delivery = "platform_bound"
platforms = ["windows"]
markdown_path = "windows.md"
pdf_artifact_name = "windows.pdf"
'''
        matrix = (Path(release_preflight.__file__).resolve().parents[1] / "release/release_matrix.toml").read_text()

        def git(*args):
            if args[:2] == ("git", "rev-parse"):
                return args[-1].removesuffix("^{commit}")
            if args[:2] == ("git", "cat-file"):
                self.assertTrue(args[-1].startswith(source + ":"))
                return "blob"
            if args[-1] == f"{source}:release/release_matrix.toml":
                return matrix
            if args[-1] == f"{source}:{release_preflight.REGISTRY_PATH}":
                return registry.replace('version = "1.0"', 'version = "1.1"')
            self.assertEqual(args[-1], f"{baseline}:{release_preflight.REGISTRY_PATH}")
            return registry.replace('version = "1.2"', 'version = "1.1"')

        with patch.object(release_preflight, "run", side_effect=git), \
             patch("tools.release_pipeline.run", side_effect=git):
            plan = release_preflight.release_plan(source, baseline)

        self.assertEqual(plan["sourceCommit"], source)
        self.assertEqual(len(plan["packages"]), 5)
        self.assertEqual(plan["northbound"]["releasedDocumentVersion"], "0.2")
        self.assertEqual(plan["northbound"]["wireVersion"], "1.1")
        self.assertEqual(plan["documents"][1]["baseVersion"], "1.1")
        self.assertEqual(plan["documentBlockers"], ["Windows guide v1.2 is still unpublished."])
        self.assertIn("published northbound document version stayed the same", plan["warnings"][0])

    def test_formal_preflight_saves_plan_before_rejecting_unpublished_documents(self):
        sha = "1" * 40
        plan = {
            "sourceCommit": sha, "baseCommit": sha, "applicationVersion": "0.4.0",
            "documentBlockers": ["Windows guide is still unpublished."],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gate = root / "gate.json"
            gate.write_text(json.dumps({
                "baseCommit": sha, "applicationVersion": "0.4.0", "shouldRelease": True, "reason": "version_advanced",
            }))
            for mode, expected_status in (("publish", 1), ("candidate", 0)):
                with self.subTest(mode=mode):
                    output = root / f"{mode}.json"
                    summary = root / f"{mode}.md"
                    argv = ["release_preflight", "--head", sha, "--gate", str(gate),
                            "--mode", mode, "--output", str(output), "--summary", str(summary)]
                    with patch("sys.argv", argv), patch.object(release_preflight, "run", return_value=sha), \
                            patch.object(release_preflight, "release_plan", return_value=plan), \
                            patch.object(release_preflight, "render_summary", return_value="Release preflight\n"):
                        self.assertEqual(release_preflight.main(), expected_status)
                    self.assertEqual(json.loads(output.read_text()), plan)
                    self.assertTrue(summary.is_file())
