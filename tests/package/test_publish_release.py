from __future__ import annotations

import json
import importlib.util
import shutil
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

from tools import publish_release, release_pipeline
from neurobridge.versioning import APPLICATION_VERSION


class PublishReleaseTests(unittest.TestCase):
    def candidate(self, root: Path, package_suffix: bytes = b"") -> tuple[Path, str]:
        commit = release_pipeline.run("git", "rev-parse", "HEAD")
        selected = {"windows-10-x86_64-msi", "kylin-v10-x86_64-deb"}
        for target in release_pipeline.matrix():
            folder = root / "results" / target["id"]
            result = {"target": target, "sourceCommit": commit, "status": "blocked", "reason": "fixture"}
            if target["id"] in selected:
                folder.mkdir(parents=True)
                package = folder / f"neurobridge-{target['id']}.{target['format']}"
                package.write_bytes(target["id"].encode() + package_suffix)
                (folder / "validation.log").write_text("fixture\n")
                result.update(status="candidate", fileName=package.name, sha256=release_pipeline.sha256(package), validationLog="validation.log", sourceReferences=[])
            release_pipeline.save_json(folder / "result.json", result)
        directory = root / "release"
        manifest = release_pipeline.assemble(root / "results", directory, "push_master")
        # This fixture's provenance represents the clean CI checkout.
        manifest["git"]["dirty"] = False
        release_pipeline.save_json(directory / "release-manifest.json", manifest)
        return directory, manifest["aggregateArchive"]["sha256"]

    def bundle(self, root: Path, document: bytes, package_suffix: bytes = b"") -> Path:
        spec = importlib.util.spec_from_file_location("bundle_builder", release_pipeline.ROOT / "tools/build-release-bundle.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        directory, _ = self.candidate(root, package_suffix)
        documents = root / "documents"
        documents.mkdir()
        with zipfile.ZipFile(documents / "neurobridge-external-documents.zip", "w") as fixture:
            fixture.writestr("release-manifest.json", json.dumps({"documents": [{"pdf_artifact_name": "protocol.pdf", "delivery": "always", "platforms": []}]}))
            fixture.writestr("protocol.pdf", document)
        archive = root / "downloaded" / f"neurobridge-v{APPLICATION_VERSION}.zip"
        builder.build_bundle(directory, documents, archive)
        return archive

    def test_retry_reuses_original_bytes_and_can_complete_the_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = self.bundle(root / "first", b"renderer output at first generation time")
            rebuilt = self.bundle(root / "retry", b"renderer output at later generation time")
            self.assertNotEqual(original.read_bytes(), rebuilt.read_bytes())
            commit = release_pipeline.run("git", "rev-parse", "HEAD")
            checkpoint = publish_release.archive_checkpoint(original, APPLICATION_VERSION, commit)
            info = {"isDraft": True, "url": "https://example.invalid/release", "assets": [{"name": original.name}], "body": publish_release.ARCHIVE_CHECKPOINT + json.dumps(checkpoint) + "\n-->\n"}
            events = []

            def command(*args):
                if args[:2] == ("git", "rev-parse"):
                    return commit
                if args[:2] == ("git", "status"):
                    return ""
                if args[:3] == ("gh", "release", "download"):
                    shutil.copy2(original, Path(args[-1]) / original.name)
                    events.append("download")
                    return ""
                if args[:3] == ("gh", "release", "edit") and args[-1] == "--draft=false":
                    events.append("public")
                    return ""
                self.fail(f"unexpected write or command: {args}")

            with mock.patch.object(publish_release, "command", side_effect=command), mock.patch.object(publish_release, "release_info", return_value=info), mock.patch.object(publish_release, "ensure_tag") as tag:
                self.assertTrue(publish_release.reuse_release_bundle(rebuilt))
                tag.assert_not_called()
            self.assertEqual(rebuilt.read_bytes(), original.read_bytes())
            digest = release_pipeline.sha256(rebuilt)
            with mock.patch.object(publish_release, "command", side_effect=command), mock.patch.object(publish_release, "ensure_tag"), mock.patch.object(publish_release, "ensure_release", return_value=info), mock.patch.object(publish_release, "release_info", return_value={**info, "isDraft": False}):
                receipt = publish_release.publish(rebuilt.parent, digest)
            self.assertEqual(receipt["aggregateArchiveSha256"], checkpoint["sha256"])
            self.assertEqual(events, ["download", "download", "public"])

    def test_retry_rejects_wrong_provenance_corruption_and_changed_packages(self) -> None:
        for failure in ("missing-checkpoint", "null-body", "incomplete-checkpoint", "other-commit", "wrong-digest", "changed-package"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original = self.bundle(root / "first", b"first document bytes")
                rebuilt = self.bundle(root / "retry", b"later document bytes", b"changed" if failure == "changed-package" else b"")
                before = rebuilt.read_bytes()
                commit = release_pipeline.run("git", "rev-parse", "HEAD")
                checkpoint = publish_release.archive_checkpoint(original, APPLICATION_VERSION, commit)
                if failure == "other-commit":
                    checkpoint["sourceCommit"] = "0" * 40
                if failure == "wrong-digest":
                    checkpoint["sha256"] = "0" * 64
                body = publish_release.ARCHIVE_CHECKPOINT + json.dumps(checkpoint) + "\n-->\n"
                info = {"isDraft": True, "assets": [{"name": original.name}], "body": "old release without checkpoint" if failure == "missing-checkpoint" else body}
                if failure == "null-body":
                    info["body"] = None
                if failure == "incomplete-checkpoint":
                    info["body"] = body.removesuffix("\n-->\n")

                def command(*args):
                    if args[:2] == ("git", "rev-parse"):
                        return commit
                    if args[:3] == ("gh", "release", "download"):
                        shutil.copy2(original, Path(args[-1]) / original.name)
                        return ""
                    self.fail(f"unexpected write: {args}")

                with mock.patch.object(publish_release, "command", side_effect=command), mock.patch.object(publish_release, "release_info", return_value=info):
                    with self.assertRaises(ValueError):
                        publish_release.reuse_release_bundle(rebuilt)
                self.assertEqual(before, rebuilt.read_bytes())

    def test_no_uploaded_asset_leaves_new_bundle_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.bundle(Path(temporary), b"document fixture")
            before = archive.read_bytes()
            commit = release_pipeline.run("git", "rev-parse", "HEAD")
            for info in (None, {"isDraft": True, "assets": []}):
                with self.subTest(info=info), mock.patch.object(publish_release, "command", return_value=commit), mock.patch.object(publish_release, "release_info", return_value=info):
                    self.assertFalse(publish_release.reuse_release_bundle(archive))
                    self.assertEqual(before, archive.read_bytes())

    def test_empty_draft_records_new_verified_digest_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notes = Path(temporary) / "notes.md"
            notes.write_text("verified archive checkpoint fixture")
            info = {"isDraft": True, "assets": []}
            with mock.patch.object(publish_release, "release_info", return_value=info), mock.patch.object(publish_release, "command") as command:
                self.assertEqual(publish_release.ensure_release("v" + APPLICATION_VERSION, notes), info)
                command.assert_called_once_with("gh", "release", "edit", "v" + APPLICATION_VERSION, "--notes-file", str(notes))

    def test_downloaded_artifact_must_match_before_any_tag_or_release_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory, digest = self.candidate(Path(temporary))
            commit = release_pipeline.run("git", "rev-parse", "HEAD")
            with mock.patch.object(publish_release, "command", side_effect=[commit, ""]), mock.patch.object(publish_release, "ensure_tag") as tag, mock.patch.object(publish_release, "ensure_release") as release:
                with self.assertRaisesRegex(ValueError, "downloaded Actions Artifact differs"):
                    publish_release.publish(directory, "0" * 64)
                tag.assert_not_called()
                release.assert_not_called()
            self.assertNotEqual(digest, "0" * 64)

    def test_single_user_bundle_checks_the_pre_upload_digest(self) -> None:
        spec = importlib.util.spec_from_file_location("bundle_builder", release_pipeline.ROOT / "tools/build-release-bundle.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory, _ = self.candidate(root)
            documents = root / "documents"
            documents.mkdir()
            with zipfile.ZipFile(documents / "neurobridge-external-documents.zip", "w") as fixture:
                fixture.writestr("release-manifest.json", json.dumps({"documents": []}))
                fixture.writestr("b-client-test/index.html", "fixture")
            downloaded = root / "downloaded"
            downloaded.mkdir()
            archive = downloaded / f"neurobridge-v{APPLICATION_VERSION}.zip"
            builder.build_bundle(directory, documents, archive)
            digest = release_pipeline.sha256(archive)
            commit = release_pipeline.run("git", "rev-parse", "HEAD")
            final = {"url": "https://example.invalid/release", "isDraft": False, "assets": [{"name": archive.name}]}
            with mock.patch.object(publish_release, "command", side_effect=[commit, ""]), mock.patch.object(publish_release, "ensure_tag") as tag:
                with self.assertRaisesRegex(ValueError, "downloaded Actions Artifact differs"):
                    publish_release.publish(downloaded, "0" * 64)
                tag.assert_not_called()
            with mock.patch.object(publish_release, "command", side_effect=[commit, ""]), mock.patch.object(publish_release, "ensure_tag") as tag, mock.patch.object(publish_release, "ensure_release", return_value=final), mock.patch.object(publish_release, "verify_asset") as assets, mock.patch.object(publish_release, "release_info", return_value=final):
                self.assertEqual(publish_release.publish(downloaded, digest)["status"], "published")
                tag.assert_called_once_with("v" + APPLICATION_VERSION, commit)
                assets.assert_called_once_with("v" + APPLICATION_VERSION, archive, True)

    def test_tag_follows_download_and_content_checks_and_assets_precede_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory, digest = self.candidate(Path(temporary))
            commit = release_pipeline.run("git", "rev-parse", "HEAD")
            events = []
            verified = publish_release.verify_nested_archives

            def verify(*args):
                verified(*args)
                events.append("artifact_verified")

            def command(*args):
                if args[:2] == ("git", "rev-parse"):
                    return commit
                if args[:2] == ("git", "status"):
                    return ""
                if args[:3] == ("gh", "release", "edit"):
                    events.append("public")
                    return ""
                self.fail(f"unexpected command: {args}")

            asset_names = {p.name for p in directory.iterdir() if p.name in ("release-manifest.json", "release-logs.jsonl") or p.name.startswith(f"neurobridge-v{APPLICATION_VERSION}-") and (p.suffix == ".zip" or p.suffix == ".sha256")}
            final = {"url": "https://example.invalid/release", "isDraft": False, "assets": [{"name": name} for name in asset_names]}
            with mock.patch.object(publish_release, "command", side_effect=command), mock.patch.object(publish_release, "verify_nested_archives", side_effect=verify), mock.patch.object(publish_release, "ensure_tag", side_effect=lambda *args: events.append("tag")), mock.patch.object(publish_release, "ensure_release", return_value={"isDraft": True, "assets": []}), mock.patch.object(publish_release, "verify_asset", side_effect=lambda *args: events.append("asset_verified")), mock.patch.object(publish_release, "release_info", return_value=final):
                receipt = publish_release.publish(directory, digest)
            self.assertEqual(events[:2], ["artifact_verified", "tag"])
            self.assertEqual(events[-1], "public")
            self.assertEqual(events.count("asset_verified"), 4)
            self.assertEqual(receipt["status"], "published")
            self.assertEqual(receipt["aggregateArchiveSha256"], digest)
            self.assertEqual(json.loads((directory / "release-receipt.json").read_text())["status"], "published")
            notes = (directory / "release-notes.md").read_text()
            checkpoint = json.loads(notes.split(publish_release.ARCHIVE_CHECKPOINT, 1)[1].split("\n-->", 1)[0])
            self.assertEqual(checkpoint["sourceCommit"], commit)
            self.assertEqual(checkpoint["sha256"], digest)


if __name__ == "__main__":
    unittest.main()
