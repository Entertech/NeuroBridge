from __future__ import annotations

import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock

from tools import release_pipeline
from tools.release_pipeline import CONFIG, assemble, matrix, run, save_json, sha256, target_result, version_tuple
from tools.publish_release import verify_nested_archives
from neurobridge.versioning import platform_version


class ReleasePipelineTests(unittest.TestCase):
    def test_matrix_has_one_kylin_bootstrap_and_two_windows_targets(self) -> None:
        targets = matrix()
        self.assertEqual(len(targets), 3)
        self.assertEqual(len({target["id"] for target in targets}), 3)
        self.assertEqual(sum(target["platform"] == "windows" for target in targets), 2)
        kylin = [target for target in targets if target["platform"] == "kylin"]
        self.assertEqual([target["id"] for target in kylin], ["kylin-v10-all-deb"])
        self.assertEqual(kylin[0]["format"], "deb")
        self.assertFalse(CONFIG["require_all_matrix_targets"])

    def test_missing_input_is_recorded_without_a_fake_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = target_result("windows-10-11-x86_64-exe", root / "inputs", root / "results")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "verified_offline_input_missing")
            self.assertEqual(list((root / "results/windows-10-11-x86_64-exe").iterdir()), [root / "results/windows-10-11-x86_64-exe/result.json"])

    def test_source_zip_cannot_masquerade_as_msi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target_id = "windows-10-11-x86_64-msi"
            source = root / "inputs" / target_id
            source.mkdir(parents=True)
            version = platform_version('windows')
            package = source / f"neurobridge-windows-{version}-x86_64.msi"
            package.write_bytes(b"PK\x03\x04source archive")
            (source / "validation.log").write_text("claimed validation\n")
            save_json(source / "verification.json", {
                "platformVersion": version, "targetId": target_id, "targetArchitecture": "x86_64", "sourceCommit": run("git", "rev-parse", "HEAD"),
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
                save_json(root / "results" / target["id"] / "result.json", {"platformVersion": platform_version(target['platform']), "target": target, "sourceCommit": run("git", "rev-parse", "HEAD"), "status": "blocked", "reason": "no verified input"})
            with self.assertRaisesRegex(ValueError, "minimum package gate failed"):
                assemble(root / "results", root / "release", "push_master")
            self.assertFalse((root / "release").exists())
            diagnostic = assemble(root / "results", root / "release", "pull_request")
            self.assertEqual(diagnostic["aggregateArchive"]["packageCount"], 0)
            self.assertEqual(diagnostic["trigger"], "pull_request")
            self.assertTrue(next(root.glob("release/neurobridge-*.zip")).is_file())

    def test_aggregate_records_gaps_and_checks_candidate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = {"windows-10-11-x86_64-msi", "windows-10-11-x86_64-exe", "kylin-v10-all-deb"}
            for target in matrix():
                folder = root / "results" / target["id"]
                version = platform_version(target['platform'])
                result = {"platformVersion": version, "target": target, "sourceCommit": run("git", "rev-parse", "HEAD"), "status": "blocked", "reason": "fixture lacks target runtime"}
                if target["id"] in selected:
                    # The bootstrap deb names the platform "kylin", not the
                    # matrix id "kylin-v10"; Windows keeps platform-version-arch.
                    if target["platform"] == "kylin":
                        name = f"neurobridge-bootstrap-{version}-20261009T000000Z-kylin-v10-{target['architecture']}.{target['format']}"
                    else:
                        name = f"neurobridge-{target['platform']}-{version}-{target['architecture']}.{target['format']}"
                    package = folder / name
                    package.parent.mkdir(parents=True)
                    package.write_bytes(target["id"].encode())
                    (folder / "validation.log").write_text("fixture verification evidence\n")
                    result.update(status="candidate", reason="fixture", fileName=package.name, sha256=sha256(package), validationLog="validation.log", sourceReferences=[{"kind": "other", "url": "https://example.com/fixture", "retrievedAt": "2026-01-01T00:00:00Z", "sha256": "0" * 64}])
                save_json(folder / "result.json", result)
            release = root / "release"
            manifest = assemble(root / "results", release)
            self.assertEqual(manifest["aggregateArchive"]["packageCount"], 3)
            self.assertEqual(manifest["coverage"]["windows"]["builtPackageCount"], 2)
            self.assertEqual(manifest["coverage"]["kylin"]["builtPackageCount"], 1)
            self.assertEqual(len(manifest["coverage"]["windows"]["targetResults"]), 2)
            self.assertEqual(len(manifest["coverage"]["kylin"]["targetResults"]), 1)
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
            package = root / f"results/windows-10-11-x86_64-msi/neurobridge-windows-{platform_version('windows')}-x86_64.msi"
            package.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "changed"):
                assemble(root / "results", release)

    def test_version_parser_rejects_ambiguous_forms(self) -> None:
        self.assertEqual(version_tuple("1.2.3"), (1, 2, 3))
        for value in ("v1.2.3", "1.2", "01.2.3", "1.2.3-rc1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_tuple(value)


class ReleaseVersionGateTests(unittest.TestCase):
    """Pin how the gate decides that a push has to produce a package.

    The decision compares [application].version only.  The changed-file list is
    used to *require* a bump, never to trigger a release, so editing
    PRODUCT_PATHS can silently turn a delivered-document edit into a no-op.
    Each branch of the decision is exercised against a real throwaway repo.
    """

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        repo_root = release_pipeline.ROOT
        for folder in ("neurobridge", "release", "doc/tech/对外", "doc/tech/内部"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / "neurobridge/version_registry.toml", self.root / "neurobridge/version_registry.toml")
        shutil.copy2(repo_root / "release/release_matrix.toml", self.root / "release/release_matrix.toml")
        for patch in (
            mock.patch.object(release_pipeline, "ROOT", self.root),
            mock.patch.object(release_pipeline, "REGISTRY", self.root / "neurobridge/version_registry.toml"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.git("init", "-q", ".")
        self.git("config", "user.email", "gate@example.invalid")
        self.git("config", "user.name", "gate")
        self.shipped_file().write_text("base\n", encoding="utf-8")
        self.shipped_document().write_text("base\n", encoding="utf-8")
        self.internal_document().write_text("base\n", encoding="utf-8")
        self.commit("base")

    def git(self, *arguments: str) -> str:
        return subprocess.check_output(("git", *arguments), cwd=self.root, text=True, encoding="utf-8").strip()

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def shipped_file(self) -> Path:
        return self.root / "neurobridge/gateway.py"

    def shipped_document(self) -> Path:
        return self.root / "doc/tech/对外/数据网关 Windows 部署与使用指南_v1.0.md"

    def internal_document(self) -> Path:
        return self.root / "doc/tech/内部/发布工作流技术方案.md"

    def test_a_shipped_file_change_requires_a_version_bump(self) -> None:
        self.shipped_file().write_text("changed\n", encoding="utf-8")
        self.commit("shipped file change")
        with self.assertRaisesRegex(ValueError, "without platform version bump"):
            release_pipeline.gate("HEAD~1")

    def test_a_delivered_document_change_requires_a_version_bump(self) -> None:
        self.shipped_document().write_text("updated\n", encoding="utf-8")
        self.commit("delivered document change")
        with self.assertRaisesRegex(ValueError, "without platform version bump"):
            release_pipeline.gate("HEAD~1")

    def test_an_internal_document_change_needs_no_release(self) -> None:
        self.internal_document().write_text("updated\n", encoding="utf-8")
        self.commit("internal document change")
        result = release_pipeline.gate("HEAD~1")
        self.assertFalse(result["productChanged"])
        self.assertFalse(result["shouldRelease"])

    def advance_version(self) -> str:
        registry = self.root / "neurobridge/version_registry.toml"
        text = registry.read_text(encoding="utf-8")
        current = tomllib.loads(text)["application"]["version"]
        major, minor, patch = (int(part) for part in current.split("."))
        bumped = f"{major}.{minor}.{patch + 1}"
        entry = (f'[[application_release_changes]]\nfrom_version = "{current}"\nto_version = "{bumped}"\n'
                 'impact = "patch"\ncompatibility = "gate fixture"\nevidence = "gate fixture"\n\n')
        text = re.sub(r'(\[application\]\n(?:#[^\n]*\n)*version = )"[^"]+"', rf'\g<1>"{bumped}"', text, count=1)
        text = text.replace("[[application_release_changes]]", entry + "[[application_release_changes]]", 1)
        for platform in ("windows", "kylin"):
            old = tomllib.loads(text)["platform_releases"][platform]["version"]
            parts = version_tuple(old)
            new = f"{parts[0]}.{parts[1]}.{parts[2] + 1}"
            text = text.replace(f'[platform_releases.{platform}]\nversion = "{old}"', f'[platform_releases.{platform}]\nversion = "{new}"', 1)
            text += f'\n[[platform_release_changes]]\nplatform = "{platform}"\nfrom_version = "{old}"\nto_version = "{new}"\nimpact = "patch"\ncompatibility = "fixture"\nevidence = "fixture"\n'
        registry.write_text(text, encoding="utf-8")
        self.commit("bump the application version")
        return bumped

    def test_advancing_the_application_version_releases(self) -> None:
        bumped = self.advance_version()
        result = release_pipeline.gate("HEAD~1")
        self.assertEqual(result["applicationVersion"], bumped)
        self.assertTrue(result["shouldRelease"])

    def advance_platform(self, platform: str, *, commit: bool = True) -> str:
        path = self.root / 'neurobridge/version_registry.toml'
        text = path.read_text()
        old = tomllib.loads(text)['platform_releases'][platform]['version']
        major, minor, patch = version_tuple(old)
        new = f'{major}.{minor}.{patch + 1}'
        text = text.replace(f'[platform_releases.{platform}]\nversion = "{old}"', f'[platform_releases.{platform}]\nversion = "{new}"')
        text += f'\n[[platform_release_changes]]\nplatform = "{platform}"\nfrom_version = "{old}"\nto_version = "{new}"\nimpact = "patch"\ncompatibility = "fixture"\nevidence = "fixture"\n'
        path.write_text(text)
        if commit:
            self.commit('independent platform bump')
        return new

    def test_each_platform_can_advance_without_the_other_or_delivery_version(self) -> None:
        original = tomllib.loads(release_pipeline.REGISTRY.read_text())
        for platform, directory in (('windows', 'windows'), ('kylin', 'linux')):
            base = self.git('rev-parse', 'HEAD')
            (self.root / directory).mkdir(exist_ok=True)
            (self.root / directory / 'gateway.sh').write_text('platform change')
            new = self.advance_platform(platform)
            result = release_pipeline.gate(base)
            other = 'kylin' if platform == 'windows' else 'windows'
            self.assertTrue(result['platforms'][platform]['shouldRelease'])
            self.assertFalse(result['platforms'][other]['shouldRelease'])
            self.assertEqual(result['platformVersions'][platform], new)
            self.assertEqual(result['applicationVersion'], original['application']['version'])

    def test_shared_change_requires_both_platform_bumps(self) -> None:
        base = self.git('rev-parse', 'HEAD')
        self.shipped_file().write_text('shared change')
        self.advance_platform('windows')
        with self.assertRaisesRegex(ValueError, 'kylin.*without platform version bump'):
            release_pipeline.gate(base)
        self.advance_platform('kylin')
        self.assertTrue(release_pipeline.gate(base)['shouldRelease'])

    def test_delivery_bump_alone_does_not_rebuild_platform_installers(self) -> None:
        registry = self.root / 'neurobridge/version_registry.toml'
        text = registry.read_text()
        old = tomllib.loads(text)['application']['version']
        a, b, c = version_tuple(old)
        new = f'{a}.{b}.{c + 1}'
        text = re.sub(r'(\[application\]\n(?:#[^\n]*\n)*version = )"[^"]+"', rf'\g<1>"{new}"', text, count=1)
        text += f'\n[[application_release_changes]]\nfrom_version = "{old}"\nto_version = "{new}"\nimpact = "patch"\ncompatibility = "fixture"\nevidence = "fixture"\n'
        registry.write_text(text)
        self.commit('delivery only')
        plan = release_pipeline.gate('HEAD~1')
        self.assertTrue(plan['shouldRelease'])
        self.assertFalse(any(d['shouldRelease'] for d in plan['platforms'].values()))

    def test_legacy_ignored_platform_fields_use_the_actual_installer_baseline(self) -> None:
        old = {'application': {'version': '0.3.0'}, 'platform_releases': {'windows': {'version': '0.0.1'}, 'kylin': {'version': '0.0.1'}}}
        self.assertEqual(release_pipeline.platform_versions(old), {'windows': '0.3.0', 'kylin': '0.3.0'})

    def test_regression_and_invalid_release_evidence_fail_closed(self) -> None:
        path = self.root / 'neurobridge/version_registry.toml'
        old = path.read_text()
        current = tomllib.loads(old)['platform_releases']['windows']['version']
        for new in ('0.0.1', '7.0.0', '01.2.3'):
            path.write_text(old.replace(f'[platform_releases.windows]\nversion = "{current}"', f'[platform_releases.windows]\nversion = "{new}"'))
            with self.subTest(new=new), self.assertRaises(ValueError):
                release_pipeline.gate('HEAD')
        path.write_text(old)

    def public_releases(self, pages: list):
        real_run = release_pipeline.run

        def run_with_releases(*args: str) -> str:
            if args[0] == "gh":
                return json.dumps(pages)
            return real_run(*args)

        return mock.patch.object(release_pipeline, "run", side_effect=run_with_releases)

    def test_first_release_ignores_failed_tag_and_walks_past_later_commits(self) -> None:
        base = self.git("rev-parse", "HEAD")
        self.advance_version()
        self.git("tag", "-a", "v" + tomllib.loads(release_pipeline.REGISTRY.read_text())["application"]["version"], "-m", "failed attempt")
        self.shipped_file().write_text("packaging correction\n")
        self.commit("follow-up after bump")
        with self.public_releases([[]]):
            resolved = release_pipeline.release_base("example/repo")
        self.assertEqual(resolved, base)
        self.assertTrue(release_pipeline.gate(resolved)["shouldRelease"])

    def test_only_public_releases_with_uploaded_zip_count_as_published(self) -> None:
        version = tomllib.loads(release_pipeline.REGISTRY.read_text())["application"]["version"]
        base = self.git("rev-parse", "HEAD")
        self.git("tag", "-a", "v" + version, "-m", "published")
        self.advance_version()
        current = tomllib.loads(release_pipeline.REGISTRY.read_text())["application"]["version"]
        self.git("tag", "-a", "v" + current, "-m", "failed draft")
        old = {"tag_name": "v" + version, "draft": False, "prerelease": False, "published_at": "2026-10-01T00:00:00Z", "assets": [{"name": f"neurobridge-v{version}.zip", "state": "uploaded"}]}
        latest = {**old, "tag_name": "v" + current, "assets": [{"name": f"neurobridge-v{current}.zip", "state": "uploaded"}]}
        for invalid in ({**latest, "draft": True}, {**latest, "prerelease": True}, {**latest, "assets": []}, {**latest, "published_at": None}):
            with self.subTest(release=invalid), self.public_releases([[invalid], [old]]):
                resolved = release_pipeline.release_base("example/repo")
                self.assertEqual(resolved, base)
                self.assertTrue(release_pipeline.gate(resolved)["shouldRelease"])
        with self.public_releases([[latest], [old]]):
            resolved = release_pipeline.release_base("example/repo")
            self.assertFalse(release_pipeline.gate(resolved)["shouldRelease"])

    def test_repository_plan_reuses_windows_when_only_kylin_advances(self) -> None:
        registry = tomllib.loads(release_pipeline.REGISTRY.read_text())
        version = registry['application']['version']
        baseline = self.git('rev-parse', 'HEAD')
        releases = []
        for platform in ('windows', 'kylin'):
            tag = f"{platform}-v{registry['platform_releases'][platform]['version']}"
            self.git('tag', '-a', tag, '-m', 'platform')
            releases.append({'tag_name': tag, 'draft': False, 'prerelease': False, 'published_at': '2026-10-10T00:00:00Z', 'assets': [{'name': tag + '.zip', 'state': 'uploaded'}]})
        delivery_tag = f'delivery-v{version}-{baseline[:12]}'
        self.git('tag', '-a', delivery_tag, '-m', 'delivery')
        releases.append({'tag_name': delivery_tag, 'draft': False, 'prerelease': False, 'published_at': '2026-10-10T00:00:00Z', 'assets': [{'name': release_pipeline.delivery_filename(version, baseline), 'state': 'uploaded'}]})
        with self.public_releases([releases]):
            self.assertFalse(release_pipeline.repository_plan('example/repo')['shouldRelease'])
        (self.root / 'linux').mkdir()
        (self.root / 'linux/setup.sh').write_text('kylin fix')
        new = self.advance_platform('kylin')
        with self.public_releases([releases]):
            plan = release_pipeline.repository_plan('example/repo')
        self.assertFalse(plan['platforms']['windows']['shouldRelease'])
        self.assertTrue(plan['platforms']['kylin']['shouldRelease'])
        self.assertEqual(plan['platformVersions']['kylin'], new)
        self.assertTrue(plan['shouldRelease'])
        tag = f'kylin-v{new}'
        self.git('tag', '-a', tag, '-m', 'independent success')
        release = {'tag_name': tag, 'draft': False, 'prerelease': False, 'published_at': '2026-10-10T01:00:00Z', 'assets': [{'name': tag + '.zip', 'state': 'uploaded'}]}
        with self.public_releases([releases + [release]]):
            retry = release_pipeline.repository_plan('example/repo')
        self.assertTrue(retry['shouldRelease'])
        self.assertFalse(any(d['shouldRelease'] for d in retry['platforms'].values()))

    def test_published_platform_does_not_hide_same_version_product_changes(self) -> None:
        version = tomllib.loads(release_pipeline.REGISTRY.read_text())['platform_releases']['windows']['version']
        tag = f'windows-v{version}'
        self.git('tag', '-a', tag, '-m', 'published')
        release = {'tag_name': tag, 'draft': False, 'prerelease': False, 'published_at': '2026-10-10T00:00:00Z', 'assets': [{'name': tag + '.zip', 'state': 'uploaded'}]}
        (self.root / 'windows').mkdir()
        (self.root / 'windows/service.py').write_text('changed without version')
        self.commit('invalid product change')
        with self.public_releases([[release]]), self.assertRaisesRegex(ValueError, 'without platform version bump'):
            release_pipeline.repository_plan('example/repo')

    def test_release_lookup_errors_do_not_fall_back_to_tags(self) -> None:
        with mock.patch.object(release_pipeline, "run", side_effect=subprocess.CalledProcessError(1, "gh")):
            with self.assertRaises(subprocess.CalledProcessError):
                release_pipeline.release_base("example/repo")


if __name__ == "__main__":
    unittest.main()
