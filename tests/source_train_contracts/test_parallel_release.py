import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import source_train as train


SHA = "1" * 40
DIGEST = "2" * 64
TAGS = {"windows-10": "windows-10/v1.2.3", "windows-11": "windows-11/v1.2.3", "kylin-v10": "kylin-v10/v1.2.3"}


def attempt() -> dict:
    return {
        "candidate": SHA, "review": {"sha": SHA}, "parallelSystems": True, "systemTags": TAGS,
        "packageScope": {"systems": list(TAGS)}, "packageRunId": "18", "recoveryRunIds": ["17"],
    }


class ParallelReleaseTests(unittest.TestCase):
    def test_single_public_system_succeeds_when_other_systems_fail(self):
        obj = train.Train("1.2.3", True)
        record = attempt()
        record["finalizationPR"] = 31

        def lookup(tag, directory):
            if tag == TAGS["windows-11"]:
                return {"sourceCommit": SHA, "archive": {"sha256": DIGEST}}
            raise ValueError("no public release")

        with patch.object(obj, "mutable"), patch.object(obj, "required_record", return_value=record), \
             patch.object(obj, "save") as save, patch("tools.document_release.verified_software_receipt", side_effect=lookup) as check:
            result = obj.finish_systems()
        self.assertEqual(check.call_count, 3)
        self.assertEqual(result["softwareResult"], "success")
        self.assertEqual(result["systemResults"]["windows-11"]["status"], "published")
        self.assertEqual(result["systemResults"]["windows-10"]["status"], "failed")
        self.assertEqual(result["systemResults"]["kylin-v10"]["status"], "failed")
        save.assert_called_once_with(result)
        with patch.object(obj, "ref", return_value=SHA) as ref, patch.object(obj, "fetch", return_value=SHA):
            self.assertEqual(obj.delivery_tag(result, complete=True), SHA)
            ref.assert_called_once_with("refs/tags/windows-11/v1.2.3")
        with patch.object(obj, "mutable"), patch.object(obj, "required_record", return_value=record), \
             patch("tools.document_release.verified_software_receipt") as check:
            self.assertEqual(obj.finish_systems()["systemResults"], result["systemResults"])
            check.assert_not_called()
            with self.assertRaisesRegex(ValueError, "missed the recorded delivery"):
                obj.publish_system("windows-10", Path(), Path(), "18")

    def test_all_systems_failed_marks_workflow_failed_and_saves_results(self):
        obj = train.Train("1.2.3", True)
        record = attempt()
        with patch.object(obj, "mutable"), patch.object(obj, "required_record", return_value=record), \
             patch.object(obj, "save") as save, patch.object(obj, "open_pr") as finalization, \
             patch("tools.document_release.verified_software_receipt", side_effect=ValueError("not published")):
            with self.assertRaisesRegex(ValueError, "all selected systems failed"):
                obj.finish_systems()
        self.assertEqual(record["softwareResult"], "failure")
        self.assertEqual(set(record["systemResults"]), set(TAGS))
        save.assert_called_once_with(record)
        finalization.assert_not_called()

    def test_failure_to_open_finalization_pr_does_not_revoke_published_software(self):
        obj = train.Train("1.2.3", True)
        record = attempt()
        evidence = {"sourceCommit": SHA, "archive": {"sha256": DIGEST}}
        with patch.object(obj, "mutable"), patch.object(obj, "required_record", return_value=record), \
             patch.object(obj, "save"), patch.object(obj, "default", side_effect=RuntimeError("temporary unavailable")), \
             patch("tools.document_release.verified_software_receipt", return_value=evidence):
            result = obj.finish_systems()
        self.assertEqual(result["softwareResult"], "success")
        self.assertIn("temporary unavailable", result["finalizationError"])

    def test_worker_only_uses_its_own_tag_and_recovered_receipt(self):
        obj = train.Train("1.2.3", True)
        system = "windows-11"
        verified = {"systems": {system: {"sha256": DIGEST, "tag": TAGS[system]}}}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            proof = folder / "package-receipt.json"
            proof.write_text(json.dumps(verified), encoding="utf-8")
            with patch.object(obj, "mutable"), patch.object(obj, "required_record", return_value=attempt()), \
                 patch.object(obj, "ref", return_value=None), patch.object(obj, "fetch", return_value=SHA), \
                 patch.object(train, "run", side_effect=lambda *args: SHA if args[:2] == ("git", "rev-parse") else ""), \
                 patch.object(train, "delivery_receipt", return_value=verified) as verify, \
                 patch("tools.release_pipeline.release_config_at", return_value=None), \
                 patch("tools.publish_release.publish") as publish:
                obj.publish_system(system, folder, proof, "17")
                self.assertTrue(all(target["osVersion"] == "11" for target in verify.call_args.args[3]))
                publish.assert_called_once_with(folder / "systems" / system, DIGEST, system=system)
                with self.assertRaisesRegex(ValueError, "recorded packaging run"):
                    obj.publish_system(system, folder, proof, "99")
