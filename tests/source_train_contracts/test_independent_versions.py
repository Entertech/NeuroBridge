import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import check_source_train_workflows, document_release, promote_document, release_pipeline, release_scope, release_version_policy, source_train
from tools.external_protocol_registry import markdown_digest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class IndependentVersionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        for name in ("config", "neurobridge", "doc", "release", "web", "tools"):
            shutil.copytree(ROOT / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("README.md", "pyproject.toml"):
            shutil.copy2(ROOT / name, self.root)
        for patcher in (
            patch.object(release_pipeline, "ROOT", self.root),
            patch.object(promote_document, "ROOT", self.root),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.git("init", "-q", "-b", "fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")
        self.registry = self.root / "neurobridge/version_registry.toml"
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True, encoding="utf-8").strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_system_version_requires_matching_pr_changes_and_filters_explicit_selection(self):
        text = self.registry.read_text(encoding="utf-8")
        self.registry.write_text(text.replace('windows-11 = "0.3.2"', 'windows-11 = "0.4.0"'), encoding="utf-8")
        wrong = self.root / "config/kylin-fixture.toml"
        wrong.write_text("# Kylin change", encoding="utf-8")
        bad = self.commit()
        with self.assertRaisesRegex(ValueError, "windows-11 version advanced without corresponding"):
            release_version_policy.check_versions(self.base, bad)
        windows = self.root / "tools/build-native-package.py"
        with windows.open("a", encoding="utf-8") as output:
            output.write("\n# Installer-only change\n")
        head = self.commit()
        release_version_policy.check_versions(self.base, head)
        baselines = dict.fromkeys(("windows-10", "windows-11", "kylin-v10"), self.base)
        scope = release_scope.scope_for(head, baselines, ",".join(baselines), config=release_pipeline.CONFIG)
        self.assertEqual(scope["systems"], ["windows-11"])
        self.assertIn("tools/build-native-package.py", scope["changedFiles"]["windows-11"])
        self.assertNotIn("tools/build-native-package.py", scope["changedFiles"]["kylin-v10"])
        self.assertEqual(release_pipeline.software_version_at(head, "windows-11"), "0.4.0")
        checkpoint = {"candidate": head, "packageScope": scope}
        source_train.require_package_selection(checkpoint, release_pipeline.matrix(), "windows-10,windows-11")

    def test_document_promotion_preserves_published_versions_and_allows_no_draft(self):
        draft = self.root / "doc/tech/头环数据网关北向网络协议_预发布.md"
        old = release_version_policy.formal_document(release_scope.registry_at(self.base), "northbound")
        path = promote_document.promote("northbound", "0.3", "2026-11-17")
        self.assertEqual(path, release_version_policy.versioned_path(old, "0.3"))
        self.assertFalse(draft.exists())
        self.assertTrue((self.root / old["markdown_path"]).is_file())
        self.assertIn("状态：已发布", (self.root / path).read_text(encoding="utf-8"))
        head = self.commit()
        release_version_policy.check_versions(self.base, head)
        for tool in ("sync-version-registry.py", "check-version-registry.py"):
            subprocess.run([sys.executable, f"tools/{tool}"], cwd=self.root, check=True)
        registry = release_scope.registry_at(head)
        self.assertIsNone(release_version_policy.draft_document(registry, "northbound"))
        self.assertEqual(list(release_version_policy.document_history(registry, "northbound")), ["0.2", "0.3"])
        with patch.object(document_release, "ROOT", self.root), patch.object(document_release, "load_registry", return_value=registry):
            self.assertEqual(document_release.registered_document("northbound", "0.2")["markdown_path"], old["markdown_path"])
        self.assertEqual(self.git("rev-parse", f"{self.base}:{old['markdown_path']}"),
                         self.git("rev-parse", f"{head}:{old['markdown_path']}"))
        next_draft = promote_document.create_draft("northbound", "0.4")
        self.assertEqual(next_draft, draft.relative_to(self.root).as_posix())
        with self.assertRaisesRegex(ValueError, "without a body change"):
            promote_document.promote("northbound", "0.4", "2026-11-18")

    def test_another_document_promotes_independently(self):
        key = "windows_operations"
        draft = self.root / promote_document.create_draft(key, "1.2")
        with draft.open("a", encoding="utf-8") as output:
            output.write("\n## 新的验收步骤\n记录服务状态。\n")
        draft_head = self.commit()
        path = promote_document.promote(key, "1.2", "2026-11-17")
        head = self.commit()
        release_version_policy.check_versions(draft_head, head)
        self.assertFalse(draft.exists())
        self.assertIn("记录服务状态", (self.root / path).read_text(encoding="utf-8"))
        self.assertEqual(list(release_version_policy.document_history(release_scope.registry_at(head), key)),
                         ["1.0", "1.1", "1.2"])
        self.assertEqual(release_version_policy.formal_document(release_scope.registry_at(head), "northbound")["version"], "0.2")

    def test_pr_rejects_formal_markdown_edit_even_during_promotion(self):
        old = release_version_policy.formal_document(release_scope.registry_at(self.base), "northbound")
        path = self.root / old["markdown_path"]
        for promote in (False, True):
            with self.subTest(promote=promote):
                if promote:
                    promote_document.promote("northbound", "0.3", "2026-11-17")
                path.write_bytes(path.read_bytes() + b"\n")
                with self.assertRaisesRegex(ValueError, "published Markdown or metadata changed for v0.2"):
                    release_version_policy.check_versions(self.base, self.commit())

    def test_required_ci_rejects_same_version_edit_with_matching_digest(self):
        jobs = check_source_train_workflows.load_workflows()["ci.yml"]["jobs"]
        step = next(step for step in jobs["unit-and-websocket"]["steps"] if step.get("id") == "protocol-pdf")
        command = ["bash", "-e", "-c", step["run"]]
        output = self.root / "github-output"
        env = os.environ | {"PROTOCOL_BASE": self.base, "PROTOCOL_VERSION": "", "GITHUB_OUTPUT": str(output)}
        subprocess.run(command, cwd=self.root, env=env, check=True, capture_output=True)
        self.assertEqual(output.read_bytes(), b"")
        output.unlink()

        old = release_version_policy.formal_document(release_scope.registry_at(self.base), "northbound")
        path = self.root / old["markdown_path"]
        path.write_bytes(path.read_bytes() + b"\nPublished document edit.\n")
        text = self.registry.read_text(encoding="utf-8")
        self.registry.write_text(text.replace(old["markdown_sha256"], markdown_digest(path)), encoding="utf-8")
        self.commit()
        subprocess.run([sys.executable, "tools/check-version-registry.py"], cwd=self.root, check=True)
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"published Markdown or metadata changed for v0.2", result.stderr)

    def test_crlf_checkout_matches_the_published_markdown_digest(self):
        markdown_files = sorted((self.root / "doc").rglob("*.md"))
        self.assertTrue(markdown_files)
        for markdown in markdown_files:
            markdown.write_bytes(markdown.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        subprocess.run([sys.executable, "tools/check-version-registry.py"], cwd=self.root, check=True)

    def test_pr_rejects_document_version_bump_if_only_delivery_metadata_changed(self):
        old = release_version_policy.formal_document(release_scope.registry_at(self.base), "northbound")
        path = promote_document.promote("northbound", "0.3", "2026-11-17")
        original = self.git("show", f"{self.base}:{old['markdown_path']}")
        (self.root / path).write_text(original.replace("v0.2", "v0.3", 2) + "\n", encoding="utf-8")
        head = self.commit()
        with self.assertRaisesRegex(ValueError, "without a body change"):
            release_version_policy.check_versions(self.base, head)
