"""Exercise per-target failure reporting with real subprocesses and ZIP gates."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from tools import release_pipeline as pipeline
from tools.publish_release import verify_nested_archives


class WindowsReleaseBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs, self.results = self.root / "inputs", self.root / "results"
        self.commit = pipeline.run("git", "rev-parse", "HEAD")
        self.version = pipeline.tomllib.loads(pipeline.REGISTRY.read_text(encoding="utf-8"))["application"]["version"]
        self.target = "windows-10-x86_64-msi"

    def steps(self, target_id, source, fail=None):
        steps = []
        for phase in ("runtime", "toolchain", "package"):
            script = "print('phase child output: " + phase + "')\n"
            if phase == "package":
                script += f"from pathlib import Path\nPath({str(source / 'validation.log')!r}).write_text('WIX error: detailed compiler failure\\n', encoding='utf-8')\n"
            if phase == fail:
                script += "import sys\nprint('stderr failure detail', file=sys.stderr)\nsys.exit(17)\n"
            elif phase == "package":
                report, name, content = self.fixture(target_id)
                script += ("import json\n"
                           f"Path({str(source / name)!r}).write_bytes({content!r})\n"
                           f"Path({str(source / 'verification.json')!r}).write_text({json.dumps(report)!r}, encoding='utf-8')\n")
            steps.append((phase, [sys.executable, "-c", script]))
        return steps

    def fixture(self, target_id):
        target = next(item for item in pipeline.matrix() if item["id"] == target_id)
        name = f"neurobridge-{self.version}-{target_id}.{target['format']}"
        content = pipeline.MAGIC[target["format"]] + b"native test fixture"
        report = dict(targetId=target_id, targetArchitecture=target["architecture"], sourceCommit=self.commit,
                      fileName=name, sha256=pipeline.hashlib.sha256(content).hexdigest(),
                      automatedValidation="passed", validationLog="validation.log", toolchain="fixture",
                      runtimeSha256="a" * 64, inputSha256="b" * 64,
                      sourceReferences=[dict(kind="other", url="https://example.com/source", retrievedAt="2026-10-29T00:00:00Z", sha256="c" * 64)],
                      builtAt="2026-10-29T00:00:00Z")
        return report, name, content

    def build(self, target_id, fail=None):
        with mock.patch.object(pipeline, "windows_build_steps", side_effect=lambda target, source: self.steps(target, source, fail)):
            pipeline.build_windows_input(target_id, self.inputs, self.results)
        return pipeline.target_result(target_id, self.inputs, self.results)

    def test_failures_at_each_stage_record_exit_code_and_skip_later_stages(self):
        for phase in ("runtime", "toolchain", "package"):
            with self.subTest(phase=phase):
                result = self.build(self.target, fail=phase)
                self.assertEqual(result["status"], "failed")
                self.assertIn(phase, result["reason"])
                self.assertIn("17", result["reason"])
                directory = self.results / self.target
                outcome = json.loads((directory / "build-outcome.json").read_text(encoding="utf-8"))
                self.assertEqual(outcome["exitCode"], 17)
                log = (directory / "windows-build.log").read_text(encoding="utf-8")
                self.assertIn("stderr failure detail", log)
                if phase != "package":
                    self.assertNotIn("phase=package", log)
                else:
                    self.assertIn("WIX error: detailed compiler failure", (directory / "native-validation.log").read_text(encoding="utf-8"))

    def test_missing_tool_is_reported_and_a_later_attempt_replaces_old_failure(self):
        with mock.patch.object(pipeline, "windows_build_steps", return_value=[("toolchain", [str(self.root / "missing-tool")])]):
            pipeline.build_windows_input(self.target, self.inputs, self.results)
        self.assertEqual(pipeline.target_result(self.target, self.inputs, self.results)["status"], "failed")
        self.assertEqual(self.build(self.target)["status"], "candidate")

    def test_failed_attempt_cannot_reuse_an_earlier_successful_verification(self):
        self.assertEqual(self.build(self.target)["status"], "candidate")
        self.assertEqual(self.build(self.target, fail="runtime")["status"], "failed")
        self.assertFalse((self.inputs / self.target / "verification.json").exists())

    def test_partial_windows_failure_still_aggregates_but_all_failed_is_rejected(self):
        for target in pipeline.matrix():
            if target["platform"] == "windows":
                self.build(target["id"], fail=None if target["id"] == self.target else "package")
            else:
                report, name, content = self.fixture(target["id"])
                source = self.inputs / target["id"]
                source.mkdir(parents=True)
                (source / name).write_bytes(content)
                (source / "validation.log").write_text("bootstrap fixture\n")
                pipeline.save_json(source / "verification.json", report)
                pipeline.target_result(target["id"], self.inputs, self.results)
        release = self.root / "release"
        manifest = pipeline.assemble(self.results, release, "push_master")
        self.assertEqual(manifest["aggregateArchive"]["packageCount"], 2)
        self.assertEqual(manifest["coverage"]["windows"]["builtPackageCount"], 1)
        self.assertEqual(sum(item["status"] == "failed" for item in manifest["coverage"]["windows"]["targetResults"]), 3)
        verify_nested_archives(release / manifest["aggregateArchive"]["fileName"], manifest)
        self.build(self.target, fail="package")
        with self.assertRaisesRegex(ValueError, "minimum package gate failed"):
            pipeline.assemble(self.results, self.root / "all-failed", "push_master")

    def test_recorded_build_failure_does_not_fail_the_target_job(self):
        # The CLI must exit successfully after recording an expected build
        # failure; otherwise the workflow skips aggregate despite other winners.
        args = ["release_pipeline", "build-windows", "--target", self.target,
                "--input-root", str(self.inputs), "--output-root", str(self.results)]
        with mock.patch.object(sys, "argv", args), mock.patch.object(pipeline, "windows_build_steps", side_effect=lambda target, source: self.steps(target, source, "package")):
            self.assertEqual(pipeline.main(), 0)
        self.assertEqual(pipeline.target_result(self.target, self.inputs, self.results)["status"], "failed")

    def test_cannot_persist_outcome_remains_a_job_failure(self):
        args = ["release_pipeline", "build-windows", "--target", self.target,
                "--input-root", str(self.inputs), "--output-root", str(self.results)]
        with mock.patch.object(sys, "argv", args), mock.patch.object(pipeline, "save_json", side_effect=OSError("disk full")):
            self.assertEqual(pipeline.main(), 1)

    def test_successful_process_without_verification_is_failed_not_missing_input(self):
        with mock.patch.object(pipeline, "windows_build_steps", return_value=[("package", [sys.executable, "-c", "print('no report')"])]):
            pipeline.build_windows_input(self.target, self.inputs, self.results)
        result = pipeline.target_result(self.target, self.inputs, self.results)
        self.assertEqual(result["status"], "failed")
        self.assertIn("without verification.json", result["reason"])

    def test_windows_toolchain_and_native_builder_use_locked_inputs(self):
        steps = dict(pipeline.windows_build_steps(self.target, self.inputs / self.target))
        self.assertIn("tools/prepare-windows-candidate-runtime.ps1", " ".join(steps["runtime"]).replace("\\", "/"))
        self.assertIn("tools/build_environment.py", " ".join(steps["toolchain"]).replace("\\", "/"))
        self.assertIn("windows-tools", steps["toolchain"])
        from tools.build_environment import load_lock
        self.assertEqual(load_lock()["downloads"]["wix"]["version"], "7.0.0")
        self.assertEqual(load_lock()["downloads"]["wixtoolset.bootstrapperapplications.wixext"]["version"], "7.0.0")
        self.assertIn("tools/build-native-package.py", " ".join(steps["package"]).replace("\\", "/"))
