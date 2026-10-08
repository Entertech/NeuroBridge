from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import importlib.util
import sys

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
_spec = importlib.util.spec_from_file_location("build_release_bundle", TOOLS_DIR / "build-release-bundle.py")
_build_release_bundle = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_build_release_bundle)
build_bundle = _build_release_bundle.build_bundle
from publish_release import verify_release_bundle
from release_pipeline import matrix, run, save_json


class ReleaseBundleTests(unittest.TestCase):
    def test_user_bundle_has_docs_system_archives_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            documents = root / "documents"
            candidates = root / "candidates/windows"
            release.mkdir()
            (documents / "document-preview").mkdir(parents=True)
            (documents / "system-prds").mkdir(parents=True)
            candidates.mkdir(parents=True)
            with zipfile.ZipFile(documents / "neurobridge-external-documents.zip", "w") as archive:
                archive.writestr("protocol.pdf", b"pdf")
                archive.writestr("release-manifest.json", b"{}")
            (documents / "system-prds/NeuroBridge项目结构与多系统接入_PRD.pdf").write_bytes(b"windows prd")
            (documents / "system-prds/银河麒麟V10耳机USB串口接入_PRD.pdf").write_bytes(b"kylin prd")
            with zipfile.ZipFile(candidates / "neurobridge-0.2.0-windows-x86_64.zip", "w") as archive:
                archive.writestr("install.ps1", "Write-Host install")

            targets = matrix()
            coverage = {
                platform: {
                    "builtPackageCount": 0,
                    "expectedFormats": ["exe", "msi"] if platform == "windows" else ["deb", "rpm"],
                    "expectedPackageCount": 12 if platform == "windows" else 20,
                    "expectedTargets": [item["id"] for item in targets if item["platform"] == platform],
                    "targetResults": [{"targetId": item["id"], "status": "blocked", "reason": "fixture"} for item in targets if item["platform"] == platform],
                }
                for platform in ("windows", "kylin")
            }
            save_json(release / "release-manifest.json", {
                "applicationVersion": "0.2.0",
                "releaseStatus": "candidate",
                "trigger": "pull_request",
                "git": {"commit": run("git", "rev-parse", "HEAD")},
                "coverage": coverage,
                "build": {"finishedAt": "2026-01-01T00:00:00Z"},
                "platformArchives": [],
            })
            (release / "build-manifest.json").write_text("{}\n", encoding="utf-8")
            (release / "release-logs.jsonl").write_text("\n".join(json.dumps({"target": {"id": item["id"]}, "status": "blocked"}) for item in targets) + "\n", encoding="utf-8")

            output = root / "neurobridge-v0.2.0.zip"
            manifest = build_bundle(release, documents, root / "candidates", output)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("docs/external/protocol.pdf", names)
                self.assertIn("windows/NeuroBridge项目结构与多系统接入_PRD.pdf", names)
                self.assertIn("kylin/银河麒麟V10耳机USB串口接入_PRD.pdf", names)
                self.assertIn("windows/windows-x86_64.zip", names)
                self.assertIn("metadata/bundle-manifest.json", names)
                nested = zipfile.ZipFile(archive.open("windows/windows-x86_64.zip"))
                self.assertEqual(nested.namelist(), ["packages/neurobridge-0.2.0-windows-x86_64.zip"])
                verify_release_bundle(output, json.loads(archive.read("metadata/bundle-manifest.json")))
            self.assertEqual(manifest["architectureArchives"][0]["packageCount"], 1)


if __name__ == "__main__":
    unittest.main()
