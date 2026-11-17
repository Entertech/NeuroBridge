import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import document_release as release


SOFTWARE = "windows-11/v0.3.3"
DOCUMENT = "docs/northbound/v0.3"
SOURCE = "1" * 40
DIGEST = "2" * 64
ARCHIVE = "neurobridge-windows-11-v0.3.3.zip"
CHANGE = "2026-08-06-replay-connection-lifecycle-and-loop"


def body(marker, item):
    return marker + json.dumps(item) + "\n-->\n"


class DocumentReleaseTests(unittest.TestCase):
    def test_only_verified_public_software_can_supply_pr12_obligation(self):
        software = {
            "schemaVersion": 1, "tag": SOFTWARE, "system": "windows-11", "sourceCommit": SOURCE,
            "archive": {"fileName": ARCHIVE, "sha256": DIGEST},
            "documentObligations": [{"changeId": CHANGE, "document": "northbound", "requiredVersion": "0.3"}],
        }
        info = {
            "isDraft": False, "isPrerelease": False,
            "assets": [{"name": name} for name in (ARCHIVE, "software-receipt.json")],
            "body": body(release.ARCHIVE_CHECKPOINT, software["archive"] | {"sourceCommit": SOURCE}),
        }
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            receipt = folder / "software-receipt.json"
            receipt.write_text(json.dumps(software))
            with patch.object(release, "release_info", return_value=None), patch.object(release, "download") as fetch:
                with self.assertRaisesRegex(ValueError, "public release required"):
                    release.software_obligations([SOFTWARE], "northbound", "0.3", folder)
                fetch.assert_not_called()
            with patch.object(release, "release_info", return_value=info), \
                 patch.object(release, "download", return_value=receipt), patch.object(release, "ensure_tag") as tag:
                with self.assertRaisesRegex(ValueError, "requires a newer version"):
                    release.software_obligations([SOFTWARE], "northbound", "0.2", folder)
                self.assertEqual(release.software_obligations([SOFTWARE], "northbound", "0.3", folder), {SOFTWARE: [CHANGE]})
                tag.assert_called_with(SOFTWARE, SOURCE)

    def test_verification_closes_only_for_matching_formal_tag_and_pdf_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            pdf = folder / "northbound-v0.3.pdf"
            pdf.write_bytes(b"%PDF-1.4 fixture")
            pdf_digest = release.sha256(pdf)
            receipt = {"schemaVersion": 1, "tag": DOCUMENT, "document": "northbound", "version": "0.3", "sourceCommit": SOURCE,
                       "software": {SOFTWARE: [CHANGE]}, "pdf": {"fileName": pdf.name, "sha256": pdf_digest}}
            (folder / release.RECEIPT).write_text(json.dumps(receipt))
            info = {"isDraft": False, "assets": [{"name": name} for name in (release.RECEIPT, pdf.name)],
                    "body": body(release.CHECKPOINT, receipt)}
            with patch.object(release, "release_info", return_value=info), patch.object(release, "ensure_tag") as tag, \
                 patch.object(release, "download", side_effect=lambda ref, name, directory: folder / name), \
                 patch.object(release, "software_obligations", return_value=receipt["software"]) as obligations:
                self.assertEqual(release.verify("northbound", "0.3", folder), receipt)
                tag.assert_called_once_with(DOCUMENT, SOURCE)
                obligations.assert_called_once_with([SOFTWARE], "northbound", "0.3", folder / "software")
                pdf.write_bytes(b"%PDF-1.4 different")
                with self.assertRaisesRegex(ValueError, "PDF SHA-256"):
                    release.verify("northbound", "0.3", folder)

    def test_upload_retry_reuses_pdf_bound_to_existing_document_tag(self):
        import hashlib
        approved = b"approved document source"
        markdown_sha = hashlib.sha256(approved).hexdigest()
        doc = {"markdown_path": "northbound.md", "markdown_sha256": markdown_sha, "pdf_artifact_name": "northbound.pdf"}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            pdf = folder / doc["pdf_artifact_name"]
            pdf.write_bytes(b"%PDF-1.4 original")
            receipt = {
                "schemaVersion": 1, "tag": DOCUMENT, "document": "northbound", "version": "0.3",
                "sourceCommit": SOURCE, "markdownSha256": markdown_sha, "software": {SOFTWARE: [CHANGE]},
                "pdf": {"fileName": pdf.name, "sha256": release.sha256(pdf)},
            }
            info = {"isDraft": True, "assets": [{"name": pdf.name}], "body": body(release.CHECKPOINT, receipt)}
            with patch.object(release, "command", return_value=SOURCE) as command, \
                 patch.object(release, "registered_document", return_value=doc), \
                 patch.object(release, "release_info", return_value=info), \
                 patch.object(release, "software_obligations", return_value=receipt["software"]), \
                 patch.object(release, "download", return_value=pdf), \
                 patch.object(release, "ensure_tag"), patch.object(release, "ensure_release"), \
                 patch.object(release, "verify_asset") as asset, patch.object(release, "verify", return_value=receipt), \
                 patch.object(release.subprocess, "check_output", return_value=approved), \
                 patch.object(release.subprocess, "run") as subprocess_run:
                self.assertEqual(release.publish("northbound", "0.3", [SOFTWARE], folder), receipt)
                self.assertFalse(any("render-protocol-pdf" in str(call) for call in subprocess_run.call_args_list))
                self.assertFalse(any(call.args[:2] == ("git", "tag") for call in command.call_args_list))
                asset.assert_any_call(DOCUMENT, pdf, True)
