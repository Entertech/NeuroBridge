from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pypdf import PdfReader, PdfWriter

from tools import delivery_release as delivery
from tools.publish_release import sha256_bytes
from tools.release_pipeline import sha256
from tools.source_train import Train

from tests.source_train_contracts.test_parallel_release import SHA, TAGS, attempt


PROTOCOL = b"%PDF- v0.2"


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return stream.getvalue()


def system_release(folder, system, protocol=PROTOCOL):
    name = f"neurobridge-{system}-v1.2.3.zip"
    path = folder / name
    platform = system.split("-", 1)[0]
    guide = f"{platform}-install.pdf"
    guide_pdf = f"%PDF- {platform} install".encode()
    directory_pdf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=210, height=297)
    writer.write(directory_pdf)
    documents = [
        {"key": "northbound", "version": "0.2", "pdfSha256": sha256_bytes(PROTOCOL), "documentTag": "docs/northbound/v0.2"},
        {"key": f"{platform}_install", "version": "1.0", "pdfSha256": sha256_bytes(guide_pdf)},
    ]
    catalog = [
        documents[0] | {"delivery": "always", "pdf_artifact_name": "协议.pdf"},
        documents[1] | {"delivery": "platform_bound", "platforms": [platform], "pdf_artifact_name": "安装.pdf"},
        {"key": "review", "delivery": "review_only"},
    ]
    path.write_bytes(zip_bytes({
        "metadata/bundle-manifest.json": json.dumps({
            "softwareSystem": system, "systemArchives": [{
                "platform": platform, "fileName": f"{platform}.zip", "variants": [{"fileName": "variant.zip"}],
            }],
        }),
        "metadata/document-filenames.json": json.dumps({"协议.pdf": "northbound-v0.2.pdf", "安装.pdf": guide}),
        "docs/external/release-manifest.json": json.dumps({"documents": catalog}),
        "docs/external/northbound-v0.2.pdf": protocol,
        delivery.GUIDE: directory_pdf.getvalue(),
        f"{platform}.zip": zip_bytes({"variant.zip": zip_bytes({f"docs/{guide}": guide_pdf})}),
    }))
    return path, {
        "tag": TAGS[system], "system": system, "sourceCommit": SHA,
        "archive": {"fileName": name, "sha256": sha256(path)}, "bundledDocuments": documents,
        "documentObligations": [{"document": "northbound", "requiredVersion": "0.3", "pullRequest": 12}],
    }


class DeliveryReleaseTests(unittest.TestCase):
    def test_assembly_includes_every_successful_system_and_its_documents(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            record = attempt() | {"softwareResult": "success"}
            assets = {TAGS[system]: system_release(path, system) for system in ("windows-11", "kylin-v10")}
            record["systemResults"] = {
                system: {"tag": tag, "status": "published", "sha256": assets[tag][1]["archive"]["sha256"]}
                if tag in assets else {"tag": tag, "status": "failed"}
                for system, tag in TAGS.items()
            }
            with patch.object(delivery, "verified_software_receipt", side_effect=lambda tag, _: deepcopy(assets[tag][1])) as verify, \
                 patch.object(delivery, "download", side_effect=lambda tag, *_: assets[tag][0]):
                archive = delivery.assemble("1.2.3", record, path)
                digest = sha256(archive)
                self.assertEqual({call.args[0] for call in verify.call_args_list}, set(assets))
                with zipfile.ZipFile(archive) as bundle:
                    manifest = json.loads(bundle.read(delivery.MANIFEST))
                    self.assertEqual(set(manifest["systems"]), {"windows-11", "kylin-v10"})
                    self.assertEqual(manifest["failedSystems"], ["windows-10"])
                    guide = PdfReader(io.BytesIO(bundle.read(delivery.GUIDE)))
                    self.assertEqual(len(guide.pages), 2)
                    self.assertEqual(
                        [bookmark.title for bookmark in guide.outline],
                        [receipt["archive"]["deliveryFileName"] for receipt in manifest["systems"].values()],
                    )
                    for system, receipt in manifest["systems"].items():
                        self.assertEqual(bundle.read(receipt["archive"]["deliveryFileName"]), assets[TAGS[system]][0].read_bytes())
                        self.assertEqual(bundle.read(f"docs/{system}/northbound-v0.2.pdf"), PROTOCOL)
                        self.assertIn(f"docs/{system}/{system.split('-')[0]}-install.pdf", bundle.namelist())
                        self.assertEqual(receipt["documentObligations"][0]["requiredVersion"], "0.3")
                    self.assertFalse(any("windows-10" in name or "v0.3" in name for name in bundle.namelist()))
                self.assertEqual(sha256(delivery.assemble("1.2.3", record, path)), digest)

    def test_invalid_component_or_pdf_stops_before_tag_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, receipt = system_release(Path(temporary), "windows-11", protocol=b"%PDF- changed")
            result = {"status": "published", "tag": TAGS["windows-11"], "sha256": receipt["archive"]["sha256"]}
            record = attempt() | {
                "softwareResult": "success",
                "systemResults": {key: result if key == "windows-11" else {"status": "failed"} for key in TAGS},
            }
            train = Train("1.2.3", True)
            with patch.object(train, "mutable"), patch.object(train, "required_record", return_value=record), \
                 patch.object(delivery, "verified_software_receipt", return_value=receipt), \
                 patch.object(delivery, "download", return_value=path), patch.object(train, "ref") as tag:
                with self.assertRaisesRegex(ValueError, "document SHA-256 mismatch"):
                    train.publish_delivery()
                path.write_bytes(b"changed component")
                with self.assertRaisesRegex(ValueError, "software ZIP SHA-256 mismatch"):
                    train.publish_delivery()
                record["softwareResult"] = "failure"
                with self.assertRaisesRegex(ValueError, "at least one success"):
                    train.publish_delivery()
            tag.assert_not_called()

    def test_publication_retry_checks_existing_asset_without_upload_or_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "neurobridge-v1.2.3.zip"
            archive.write_bytes(b"zip")
            tag = "delivery/v1.2.3"
            public = {
                "body": delivery.CHECKPOINT + json.dumps({
                    "sourceCommit": SHA, "fileName": archive.name, "sha256": sha256(archive),
                }) + "\n-->\n",
                "assets": [{"name": archive.name}], "isDraft": False, "url": "https://example.com/release",
            }
            with patch.object(delivery, "ensure_tag") as verify_tag, \
                 patch.object(delivery, "ensure_release", return_value=public), \
                 patch.object(delivery, "release_info", return_value=public), \
                 patch.object(delivery, "verify_asset") as verify, patch.object(delivery, "command") as command:
                self.assertEqual(delivery.publish(tag, SHA, archive)["tag"], tag)
                verify_tag.assert_called_once_with(tag, SHA)
                verify.assert_called_once_with(tag, archive, True)
                archive.write_bytes(b"different retry")
                with self.assertRaisesRegex(ValueError, "immutable checkpoint"):
                    delivery.publish(tag, SHA, archive)
                command.assert_not_called()
