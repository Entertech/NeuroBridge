from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.release_pipeline import CONFIG, assemble, matrix, run, save_json, sha256, target_result, version_tuple
from tools.publish_release import verify_nested_archives
from neurobridge.versioning import APPLICATION_VERSION


class ReleasePipelineTests(unittest.TestCase):
    def test_matrix_has_all_32_distinct_package_targets(self) -> None:
        targets = matrix()
        self.assertEqual(len(targets), 32)
        self.assertEqual(len({target["id"] for target in targets}), 32)
        self.assertEqual(sum(target["platform"] == "windows" for target in targets), 12)
        self.assertEqual(sum(target["platform"] == "kylin" for target in targets), 20)
        self.assertFalse(CONFIG["require_all_matrix_targets"])

    def test_missing_input_is_recorded_without_a_fake_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = target_result("windows-7-x86-exe", root / "inputs", root / "results")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "verified_offline_input_missing")
            self.assertEqual(list((root / "results/windows-7-x86-exe").iterdir()), [root / "results/windows-7-x86-exe/result.json"])

    def test_source_zip_cannot_masquerade_as_msi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target_id = "windows-10-x86_64-msi"
            source = root / "inputs" / target_id
            source.mkdir(parents=True)
            package = source / f"neurobridge-windows-{APPLICATION_VERSION}-x86_64.msi"
            package.write_bytes(b"PK\x03\x04source archive")
            (source / "validation.log").write_text("claimed validation\n")
            save_json(source / "verification.json", {
                "targetId": target_id, "targetArchitecture": "x86_64", "sourceCommit": run("git", "rev-parse", "HEAD"),
                "fileName": package.name, "sha256": sha256(package), "automatedValidation": "passed",
                "validationLog": "validation.log", "toolchain": "fixture", "runtimeSha256": "0" * 64,
                "inputSha256": "0" * 64, "builtAt": "2026-01-01T00:00:00Z", "sourceReferences": [{"kind": "other", "url": "https://example.com", "retrievedAt": "2026-01-01T00:00:00Z", "sha256": "0" * 64}],
            })
            result = target_result(target_id, root / "inputs", root / "results")
            self.assertEqual(result["status"], "failed")
            self.assertIn("native installer format", result["reason"])

    def test_aggregate_requires_every_result_and_one_package_per_platform(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "missing result"):
                assemble(root / "results", root / "release")
            for target in matrix():
                save_json(root / "results" / target["id"] / "result.json", {"target": target, "sourceCommit": run("git", "rev-parse", "HEAD"), "status": "blocked", "reason": "no verified input"})
            with self.assertRaisesRegex(ValueError, "minimum package gate failed"):
                assemble(root / "results", root / "release")
            self.assertFalse((root / "release").exists())

    def test_aggregate_records_gaps_and_checks_candidate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = {"windows-10-x86_64-msi", "kylin-server-x86_64-deb"}
            for target in matrix():
                folder = root / "results" / target["id"]
                result = {"target": target, "sourceCommit": run("git", "rev-parse", "HEAD"), "status": "blocked", "reason": "fixture lacks target runtime"}
                if target["id"] in selected:
                    package = folder / f"neurobridge-{target['platform']}-{APPLICATION_VERSION}-{target['architecture']}.{target['format']}"
                    package.parent.mkdir(parents=True)
                    package.write_bytes(target["id"].encode())
                    (folder / "validation.log").write_text("fixture verification evidence\n")
                    result.update(status="candidate", reason="fixture", fileName=package.name, sha256=sha256(package), validationLog="validation.log", sourceReferences=[{"kind": "other", "url": "https://example.com/fixture", "retrievedAt": "2026-01-01T00:00:00Z", "sha256": "0" * 64}])
                save_json(folder / "result.json", result)
            release = root / "release"
            manifest = assemble(root / "results", release)
            self.assertEqual(manifest["aggregateArchive"]["packageCount"], 2)
            self.assertEqual(manifest["coverage"]["windows"]["builtPackageCount"], 1)
            self.assertEqual(manifest["coverage"]["kylin"]["builtPackageCount"], 1)
            self.assertEqual(len(manifest["coverage"]["windows"]["targetResults"]), 12)
            self.assertEqual(len(manifest["coverage"]["kylin"]["targetResults"]), 20)
            archive = next(release.glob("neurobridge-*.zip"))
            first_digest = sha256(archive)
            verify_nested_archives(archive, manifest)
            self.assertEqual(json.loads((release / "release-manifest.json").read_text())["aggregateArchive"]["sha256"], first_digest)
            self.assertEqual(assemble(root / "results", release)["aggregateArchive"]["sha256"], first_digest)
            self.assertEqual(sha256(archive), first_digest)
            tampered = json.loads(json.dumps(manifest))
            tampered["platformArchives"][0]["packages"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "package hash mismatch"):
                verify_nested_archives(archive, tampered)
            package = root / f"results/windows-10-x86_64-msi/neurobridge-windows-{APPLICATION_VERSION}-x86_64.msi"
            package.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "changed"):
                assemble(root / "results", release)

    def test_version_parser_rejects_ambiguous_forms(self) -> None:
        self.assertEqual(version_tuple("1.2.3"), (1, 2, 3))
        for value in ("v1.2.3", "1.2", "01.2.3", "1.2.3-rc1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_tuple(value)


if __name__ == "__main__":
    unittest.main()
