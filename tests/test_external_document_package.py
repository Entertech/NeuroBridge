from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from external_document_package import (  # noqa: E402
    MANIFEST_FILENAME,
    build_release_manifest,
    collect_external_documents,
    collect_web_client_files,
    package_documents,
)
from external_protocol_registry import load_registry  # noqa: E402


class ExternalDocumentPackageTests(unittest.TestCase):
    def test_all_current_external_documents_keep_their_registered_versions(self) -> None:
        documents = collect_external_documents(load_registry())

        self.assertEqual(
            [document.key for document in documents],
            ["northbound", "capture_package", "ssh_operations", "wired_network_operations"],
        )
        self.assertEqual([document.version for document in documents], ["0.2", "0.1", "1.0", "1.0"])
        self.assertEqual(
            [document.pdf_artifact_name for document in documents],
            [
                "头环数据网关北向网络协议_v0.2.pdf",
                "头环数据采集包格式说明_v0.1.pdf",
                "头环数据网关 SSH 运维操作指南_v1.0.pdf",
                "头环数据网关有线网络配置指南_v1.0.pdf",
            ],
        )

        manifest = build_release_manifest(documents, "candidate")
        self.assertEqual(manifest["versionDecision"], "retain_existing_versions")
        self.assertEqual(
            manifest["unpublishedSourceDocumentCount"],
            sum(document.status == "unpublished" for document in documents),
        )
        self.assertEqual([item["artifactStatus"] for item in manifest["documents"]], ["candidate"] * 4)
        self.assertEqual(
            [item["versionAction"] for item in manifest["documents"]],
            [
                "retain_published_version" if document.status == "published" else "publish_current_unpublished_version"
                for document in documents
            ],
        )
        self.assertEqual(manifest["sourceMetadataState"], "unchanged")

    def test_formal_package_rejects_unpublished_source_documents(self) -> None:
        documents = collect_external_documents(load_registry())
        unpublished_documents = [
            documents[0].__class__(**{**documents[0].__dict__, "status": "unpublished"}),
            *documents[1:],
        ]

        with self.assertRaisesRegex(ValueError, "requires every packaged source document"):
            build_release_manifest(unpublished_documents, "publish")

    def test_web_client_is_a_complete_static_page_for_the_release_zip(self) -> None:
        files = collect_web_client_files()

        self.assertEqual(
            [path.name for path in files],
            ["README.md", "app.js", "index.html", "runtime-config.js", "styles.css", "version.js"],
        )

    def test_zip_contains_all_pdfs_and_a_directly_openable_b_side_page(self) -> None:
        documents = collect_external_documents(load_registry())
        manifest = build_release_manifest(documents, "candidate")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rendered_pdfs = []
            for document in documents:
                pdf = root / document.pdf_artifact_name
                pdf.write_bytes(b"%PDF-1.4\\nplaceholder\\n")
                rendered_pdfs.append(pdf)

            package = package_documents(rendered_pdfs, collect_web_client_files(), manifest, root / "artifact")

            with zipfile.ZipFile(package) as archive:
                entries = set(archive.namelist())
                self.assertTrue({document.pdf_artifact_name for document in documents}.issubset(entries))
                self.assertIn("b-client-test/index.html", entries)
                packaged_manifest = json.loads(archive.read(MANIFEST_FILENAME))
            self.assertEqual(packaged_manifest["webClient"]["entryPoint"], "b-client-test/index.html")
            self.assertTrue(packaged_manifest["webClient"]["openDirectly"])

    def test_all_published_documents_still_retain_their_versions(self) -> None:
        documents = collect_external_documents(load_registry())
        published_documents = [document.__class__(**{**document.__dict__, "status": "published"}) for document in documents]

        manifest = build_release_manifest(published_documents, "publish")

        self.assertEqual(manifest["versionDecision"], "retain_existing_versions")
        self.assertEqual(manifest["unpublishedSourceDocumentCount"], 0)
        self.assertTrue(all(item["versionAction"] == "retain_published_version" for item in manifest["documents"]))
