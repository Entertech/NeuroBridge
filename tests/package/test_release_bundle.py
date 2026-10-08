from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

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
    def test_user_bundle_has_system_variant_architecture_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            documents = root / "documents"
            release.mkdir()
            (documents / "system-prds").mkdir(parents=True)
            (documents / "document-preview").mkdir(parents=True)
            with zipfile.ZipFile(documents / "neurobridge-external-documents.zip", "w") as archive:
                archive.writestr("protocol.pdf", b"pdf")
                archive.writestr("头环数据网关 SSH 运维操作指南_v1.0.pdf", b"excluded")
                archive.writestr("头环数据网关有线网络配置指南_v1.0.pdf", b"excluded")
                archive.writestr("release-manifest.json", b"{}")
            (documents / "system-prds/NeuroBridge项目结构与多系统接入_PRD.pdf").write_bytes(b"windows prd")
            (documents / "system-prds/银河麒麟V10耳机USB串口接入_PRD.pdf").write_bytes(b"kylin prd")

            targets = matrix()
            coverage = {
                platform: {
                    "builtPackageCount": 1,
                    "expectedFormats": ["exe", "msi"] if platform == "windows" else ["deb", "rpm"],
                    "expectedPackageCount": 12 if platform == "windows" else 20,
                    "expectedTargets": [item["id"] for item in targets if item["platform"] == platform],
                    "targetResults": [{"targetId": item["id"], "status": "blocked", "reason": "fixture"} for item in targets if item["platform"] == platform],
                }
                for platform in ("windows", "kylin")
            }

            package_specs = {
                "windows": [
                    ("7", "none", "x86", "exe"),
                    ("7", "none", "x86_64", "msi"),
                    ("10", "none", "x86", "exe"),
                    ("11", "none", "x86_64", "exe"),
                ],
                "kylin": [
                    ("V10", "server", "x86_64", "deb"),
                    ("V10", "server", "arm64", "rpm"),
                    ("V10", "desktop", "x86_64", "deb"),
                ],
            }
            platform_archives = []
            for platform, specs in package_specs.items():
                archive_name = f"{platform}-native.zip"
                package_entries = []
                with zipfile.ZipFile(release / archive_name, "w") as archive:
                    for index, (os_version, edition, architecture, package_format) in enumerate(specs):
                        family = f"windows-{os_version}" if platform == "windows" else f"kylin-{edition}"
                        filename = f"neurobridge-0.2.0-{family}-{architecture}.{package_format}"
                        payload = f"{filename}-{index}".encode()
                        validation_name = f"validation/{platform}-{index}.json"
                        archive.writestr(f"packages/{filename}", payload)
                        archive.writestr(validation_name, b"passed\n")
                        package_entries.append({
                            "target": platform,
                            "osVersion": os_version,
                            "edition": edition,
                            "architecture": architecture,
                            "format": package_format,
                            "status": "candidate",
                            "fileName": filename,
                            "sha256": _build_release_bundle.digest_bytes(payload),
                            "validationLog": validation_name,
                        })
                platform_archives.append({"platform": platform, "fileName": archive_name, "packages": package_entries})

            save_json(release / "release-manifest.json", {
                "applicationVersion": "0.2.0",
                "releaseStatus": "candidate",
                "trigger": "pull_request",
                "git": {"commit": run("git", "rev-parse", "HEAD")},
                "coverage": coverage,
                "build": {"finishedAt": "2026-01-01T00:00:00Z"},
                "platformArchives": platform_archives,
            })
            (release / "build-manifest.json").write_text("{}\n", encoding="utf-8")
            (release / "release-logs.jsonl").write_text("\n".join(json.dumps({"target": {"id": item["id"]}, "status": "blocked"}) for item in targets) + "\n", encoding="utf-8")

            output = root / "neurobridge-v0.2.0.zip"
            manifest = build_bundle(release, documents, output)
            with zipfile.ZipFile(output) as outer:
                names = set(outer.namelist())
                self.assertIn("windows/windows.zip", names)
                self.assertIn("kylin/kylin.zip", names)
                self.assertIn("windows/NeuroBridge项目结构与多系统接入_PRD.pdf", names)
                self.assertIn("kylin/银河麒麟V10耳机USB串口接入_PRD.pdf", names)
                self.assertNotIn("docs/external/头环数据网关 SSH 运维操作指南_v1.0.pdf", names)
                self.assertNotIn("docs/external/头环数据网关有线网络配置指南_v1.0.pdf", names)
                with zipfile.ZipFile(outer.open("windows/windows.zip")) as windows:
                    self.assertEqual(set(windows.namelist()), {"windows-7.zip", "windows-10.zip", "windows-11.zip"})
                    with zipfile.ZipFile(windows.open("windows-7.zip")) as windows_7:
                        self.assertEqual(set(windows_7.namelist()), {"windows-7-x86.zip", "windows-7-x86_64.zip"})
                        with zipfile.ZipFile(windows_7.open("windows-7-x86.zip")) as x86:
                            self.assertEqual(set(x86.namelist()), {"neurobridge-0.2.0-windows-7-x86.exe", "checksums.sha256"})
                with zipfile.ZipFile(outer.open("kylin/kylin.zip")) as kylin:
                    self.assertEqual(set(kylin.namelist()), {"kylin-server.zip", "kylin-desktop.zip"})
                verify_release_bundle(output, json.loads(outer.read("metadata/bundle-manifest.json")))
            self.assertEqual(len(manifest["systemArchives"]), 2)


if __name__ == "__main__":
    unittest.main()
