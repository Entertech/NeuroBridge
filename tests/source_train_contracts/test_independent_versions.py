import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import promote_document, release_pipeline, release_scope, release_version_policy

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
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

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
        windows = self.root / "windows/fixture.py"
        windows.parent.mkdir()
        windows.write_text("example = True", encoding="utf-8")
        head = self.commit()
        release_version_policy.check_versions(self.base, head)
        baselines = dict.fromkeys(("windows-10", "windows-11", "kylin-v10"), self.base)
        scope = release_scope.scope_for(head, baselines, ",".join(baselines), config=release_pipeline.CONFIG)
        self.assertEqual(scope["systems"], ["windows-11"])
        self.assertEqual(release_pipeline.software_version_at(head, "windows-11"), "0.4.0")

    def test_document_promotion_uses_fixed_path_and_allows_no_draft(self):
        draft = self.root / "doc/tech/头环数据网关北向网络协议_预发布.md"
        old = release_version_policy.formal_document(release_scope.registry_at(self.base), "northbound")
        path = promote_document.promote("northbound", "0.3", "2026-11-17")
        self.assertEqual(path, release_version_policy.fixed_path(old))
        self.assertFalse(draft.exists())
        self.assertFalse((self.root / old["markdown_path"]).exists())
        self.assertIn("状态：已发布", (self.root / path).read_text(encoding="utf-8"))
        head = self.commit()
        release_version_policy.check_versions(self.base, head)
        for tool in ("sync-version-registry.py", "check-version-registry.py"):
            subprocess.run([sys.executable, f"tools/{tool}"], cwd=self.root, check=True)
        self.assertIsNone(release_version_policy.draft_document(release_scope.registry_at(head), "northbound"))
        self.assertIn("接口基线", self.git("show", f"{self.base}:{old['markdown_path']}"))
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
        self.assertEqual(release_version_policy.formal_document(release_scope.registry_at(head), "northbound")["version"], "0.2")

    def test_pr_rejects_formal_markdown_edit_without_version(self):
        old = release_version_policy.formal_document(release_scope.registry_at(self.base), "northbound")
        path = self.root / old["markdown_path"]
        path.write_bytes(path.read_bytes() + b"\n")
        head = self.commit()
        with self.assertRaisesRegex(ValueError, "without a new document version"):
            release_version_policy.check_versions(self.base, head)

    def test_pr_rejects_document_version_bump_if_only_delivery_metadata_changed(self):
        old = release_version_policy.formal_document(release_scope.registry_at(self.base), "northbound")
        path = promote_document.promote("northbound", "0.3", "2026-11-17")
        original = self.git("show", f"{self.base}:{old['markdown_path']}")
        (self.root / path).write_text(original.replace("v0.2", "v0.3", 2) + "\n", encoding="utf-8")
        head = self.commit()
        with self.assertRaisesRegex(ValueError, "without a body change"):
            release_version_policy.check_versions(self.base, head)
