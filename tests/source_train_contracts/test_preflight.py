from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from tools import release_preflight
from tools.external_document_package import ExternalDocument


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
                        self.assertEqual(json.loads(output.read_text()), plan | {
                            "formalDocumentBlockers": plan["documentBlockers"], "documentMode": mode,
                        })
                    self.assertTrue(summary.is_file())

    def test_pr12_document_requirement_is_independent_of_wire_version(self):
        doc = ExternalDocument("northbound", "Northbound", "0.2", "published", "always", (), "v0.2.md", "v0.2.pdf")
        registry = {"change_records": [{
            "id": "replay-loop",
            "required_document": {"key": "northbound", "minimum_version": "0.3", "pull_request": 12},
        }]}
        plan = {
            "sourceCommit": "1" * 40, "baseCommit": "2" * 40,
            "applicationVersion": "0.3.3", "baseApplicationVersion": "0.3.2",
            "packages": [{"id": "windows-11-x86_64-exe", "runner": "windows-2022", "platform": "windows"}],
            "documents": [], "documentBlockers": [], "warnings": [], "generatedDocuments": [],
            "northbound": {"wireVersion": "1.0", "baseWireVersion": "1.0", "prereleaseDocumentVersion": "0.3"},
        }
        platforms = {"windows", "kylin"}
        for version, status, blocked in (("0.2", "published", True), ("0.3", "unpublished", True),
                                         ("0.3", "published", False), ("0.10", "published", False)):
            with self.subTest(version=version, status=status):
                plan["documentImpacts"] = release_preflight.document_impacts(
                    registry, [replace(doc, version=version, status=status)], platforms,
                )
                summary = release_preflight.render_summary(plan, "publish", "version_advanced")
                self.assertEqual(bool(release_preflight.formal_document_blockers(plan)), blocked)
                self.assertEqual("document PR #12" in summary, blocked)
                if blocked:
                    self.assertIn(f"candidate selects v{version}", summary)
        registry["change_records"][0]["required_document"]["platforms"] = ["kylin"]
        plan["documentImpacts"] = release_preflight.document_impacts(registry, [doc], platforms)
        self.assertEqual(release_preflight.formal_document_blockers(plan), [])
