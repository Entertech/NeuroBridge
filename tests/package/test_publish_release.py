from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

from tools import publish_release, release_pipeline
from neurobridge.versioning import APPLICATION_VERSION


class PublishReleaseTests(unittest.TestCase):
    def candidate(self, root: Path) -> tuple[Path, str]:
        commit = release_pipeline.run("git", "rev-parse", "HEAD")
        selected = {"windows-10-x86_64-msi", "kylin-server-x86_64-deb"}
        for target in release_pipeline.matrix():
            folder = root / "results" / target["id"]
            result = {"target": target, "sourceCommit": commit, "status": "blocked", "reason": "fixture"}
            if target["id"] in selected:
                folder.mkdir(parents=True)
                package = folder / f"neurobridge-{target['id']}.{target['format']}"
                package.write_bytes(target["id"].encode())
                (folder / "validation.log").write_text("fixture\n")
                result.update(status="candidate", fileName=package.name, sha256=release_pipeline.sha256(package), validationLog="validation.log", sourceReferences=[])
            release_pipeline.save_json(folder / "result.json", result)
        directory = root / "release"
        manifest = release_pipeline.assemble(root / "results", directory, "push_master")
        # This fixture's provenance represents the clean CI checkout.
        manifest["git"]["dirty"] = False
        release_pipeline.save_json(directory / "release-manifest.json", manifest)
        return directory, manifest["aggregateArchive"]["sha256"]

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


if __name__ == "__main__":
    unittest.main()
