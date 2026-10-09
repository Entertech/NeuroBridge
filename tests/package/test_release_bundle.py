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


WINDOWS_GUIDE = "数据网关 Windows 部署与使用指南_v1.0.pdf"
KYLIN_GUIDE = "数据网关银河麒麟部署与使用指南_v1.0.pdf"
SSH_GUIDE = "头环数据网关 SSH 运维操作指南_v1.0.pdf"
WIRED_GUIDE = "头环数据网关有线网络配置指南_v1.0.pdf"

PACKAGE_SPECS = {
    "windows": [
        ("7", "none", "x86", "exe"),
        ("7", "none", "x86_64", "msi"),
        ("10", "none", "x86", "exe"),
        ("11", "none", "x86_64", "exe"),
    ],
    "kylin": [
        ("V10", "v10", "x86_64", "deb"),
    ],
}


def write_document_package(documents: Path) -> None:
    """Write the review artifact with one document per delivery policy."""
    documents.mkdir(parents=True)
    manifest = {
        "documents": [
            {"pdf_artifact_name": "protocol.pdf", "delivery": "always", "platforms": []},
            {"pdf_artifact_name": WINDOWS_GUIDE, "delivery": "platform_bound", "platforms": ["windows"]},
            {"pdf_artifact_name": KYLIN_GUIDE, "delivery": "platform_bound", "platforms": ["kylin"]},
            {"pdf_artifact_name": SSH_GUIDE, "delivery": "review_only", "platforms": []},
            {"pdf_artifact_name": WIRED_GUIDE, "delivery": "review_only", "platforms": []},
        ]
    }
    with zipfile.ZipFile(documents / "neurobridge-external-documents.zip", "w") as archive:
        archive.writestr("protocol.pdf", b"pdf")
        archive.writestr(WINDOWS_GUIDE, b"windows guide")
        archive.writestr(KYLIN_GUIDE, b"kylin guide")
        archive.writestr(SSH_GUIDE, b"excluded")
        archive.writestr(WIRED_GUIDE, b"excluded")
        archive.writestr("b-client-test/index.html", b"<html></html>")
        archive.writestr("release-manifest.json", json.dumps(manifest, ensure_ascii=False))


def write_release_fixture(release: Path, built_platforms: tuple[str, ...]) -> None:
    """Write verified native packages for the requested platforms only."""
    release.mkdir()
    targets = matrix()
    coverage = {
        platform: {
            "builtPackageCount": 1,
            "expectedFormats": ["exe", "msi"] if platform == "windows" else ["deb"],
            "expectedPackageCount": 12 if platform == "windows" else 1,
            "expectedTargets": [item["id"] for item in targets if item["platform"] == platform],
            "targetResults": [{"targetId": item["id"], "status": "blocked", "reason": "fixture"} for item in targets if item["platform"] == platform],
        }
        for platform in ("windows", "kylin")
    }

    platform_archives = []
    for platform, specs in PACKAGE_SPECS.items():
        if platform not in built_platforms:
            continue
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
    (release / "release-logs.jsonl").write_text(
        "\n".join(json.dumps({"target": {"id": item["id"]}, "status": "blocked"}) for item in targets) + "\n",
        encoding="utf-8",
    )


class ReleaseBundleTests(unittest.TestCase):
    def test_user_bundle_has_system_variant_architecture_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            documents = root / "documents"
            write_document_package(documents)
            write_release_fixture(release, ("windows", "kylin"))

            output = root / "neurobridge-v0.2.0.zip"
            manifest = build_bundle(release, documents, output)
            with zipfile.ZipFile(output) as outer:
                names = set(outer.namelist())
                self.assertIn("windows/windows.zip", names)
                self.assertIn("kylin/kylin.zip", names)
                # internal system PRDs must not ship in the user-facing bundle
                self.assertFalse(any(name.endswith("_PRD.pdf") for name in names))
                # always: shipped for every release
                self.assertIn("docs/external/protocol.pdf", names)
                self.assertIn("docs/external/b-client-test/index.html", names)
                # platform_bound documents live inside the per-version archives, not at the root
                self.assertEqual({name for name in names if name.startswith("windows/")}, {"windows/windows.zip"})
                self.assertNotIn(f"windows/{WINDOWS_GUIDE}", names)
                self.assertNotIn(f"docs/external/{WINDOWS_GUIDE}", names)
                # review_only: never shipped to end users
                self.assertNotIn(f"docs/external/{SSH_GUIDE}", names)
                self.assertNotIn(f"docs/external/{WIRED_GUIDE}", names)
                with zipfile.ZipFile(outer.open("windows/windows.zip")) as windows:
                    self.assertEqual(set(windows.namelist()), {"windows-7.zip", "windows-10.zip", "windows-11.zip"})
                    # every Windows version archive carries the shipped documents
                    for version in ("7", "10", "11"):
                        with zipfile.ZipFile(windows.open(f"windows-{version}.zip")) as family:
                            self.assertIn(f"docs/{WINDOWS_GUIDE}", family.namelist())
                            self.assertIn("docs/protocol.pdf", family.namelist())
                    with zipfile.ZipFile(windows.open("windows-7.zip")) as windows_7:
                        self.assertEqual(
                            set(windows_7.namelist()),
                            {"windows-7-x86.zip", "windows-7-x86_64.zip", f"docs/{WINDOWS_GUIDE}", "docs/protocol.pdf"},
                        )
                        with zipfile.ZipFile(windows_7.open("windows-7-x86.zip")) as x86:
                            self.assertEqual(set(x86.namelist()), {"neurobridge-0.2.0-windows-7-x86.exe", "checksums.sha256"})
                self.assertEqual({name for name in names if name.startswith("kylin/")}, {"kylin/kylin.zip"})
                self.assertNotIn(f"kylin/{KYLIN_GUIDE}", names)
                self.assertNotIn(f"docs/external/{KYLIN_GUIDE}", names)
                with zipfile.ZipFile(outer.open("kylin/kylin.zip")) as kylin:
                    self.assertEqual(set(kylin.namelist()), {"kylin-v10.zip"})
                    with zipfile.ZipFile(kylin.open("kylin-v10.zip")) as family:
                        self.assertEqual(
                            set(family.namelist()),
                            {"kylin-v10-x86_64.zip", f"docs/{KYLIN_GUIDE}", "docs/protocol.pdf"},
                        )
                        self.assertNotIn(f"docs/{WINDOWS_GUIDE}", family.namelist())
                        with zipfile.ZipFile(family.open("kylin-v10-x86_64.zip")) as package:
                            self.assertEqual(
                                set(package.namelist()),
                                {"neurobridge-0.2.0-kylin-v10-x86_64.deb", "checksums.sha256"},
                            )
                verify_release_bundle(output, json.loads(outer.read("metadata/bundle-manifest.json")))
            self.assertEqual(len(manifest["systemArchives"]), 2)

    def test_platform_bound_document_is_omitted_when_platform_has_no_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            documents = root / "documents"
            write_document_package(documents)
            write_release_fixture(release, ("kylin",))

            output = root / "neurobridge-v0.2.0.zip"
            build_bundle(release, documents, output)
            with zipfile.ZipFile(output) as outer:
                names = set(outer.namelist())
                # The Windows guide must not appear anywhere once Windows produced no packages.
                self.assertNotIn(f"windows/{WINDOWS_GUIDE}", names)
                self.assertNotIn(f"docs/external/{WINDOWS_GUIDE}", names)
                with zipfile.ZipFile(outer.open("kylin/kylin.zip")) as kylin:
                    with zipfile.ZipFile(kylin.open("kylin-v10.zip")) as family:
                        self.assertIn(f"docs/{KYLIN_GUIDE}", family.namelist())
                        self.assertNotIn(f"docs/{WINDOWS_GUIDE}", family.namelist())
                # always documents are unaffected by platform coverage.
                self.assertIn("docs/external/protocol.pdf", names)
                verify_release_bundle(output, json.loads(outer.read("metadata/bundle-manifest.json")))

    def test_kylin_guide_is_omitted_when_kylin_has_no_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            documents = root / "documents"
            write_document_package(documents)
            write_release_fixture(release, ("windows",))

            output = root / "neurobridge-v0.2.0.zip"
            build_bundle(release, documents, output)
            with zipfile.ZipFile(output) as outer:
                names = set(outer.namelist())
                self.assertFalse(any(KYLIN_GUIDE in name for name in names))
                with zipfile.ZipFile(outer.open("windows/windows.zip")) as windows:
                    with zipfile.ZipFile(windows.open("windows-7.zip")) as family:
                        self.assertIn(f"docs/{WINDOWS_GUIDE}", family.namelist())
                        self.assertNotIn(f"docs/{KYLIN_GUIDE}", family.namelist())
                verify_release_bundle(output, json.loads(outer.read("metadata/bundle-manifest.json")))


if __name__ == "__main__":
    unittest.main()
