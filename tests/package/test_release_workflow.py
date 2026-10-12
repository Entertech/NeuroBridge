"""Check cross-job release guarantees without a runtime YAML dependency."""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[2] / '.github/workflows/release.yml').read_text()


def job(name: str) -> str:
    match = re.search(r'^  ' + re.escape(name) + r':\n([\s\S]*?)(?=^  [a-z][a-z-]*:|\Z)', SOURCE, re.M)
    assert match is not None, name
    return match.group(1)


class ReleaseWorkflowTests(unittest.TestCase):
    def test_platform_release_is_independent_of_documents_and_other_platform_success(self):
        block = job('platform-releases')
        self.assertIn('fail-fast: false', block)
        self.assertIn('needs: [release-plan, release-targets]', block)
        self.assertNotIn("needs.release-targets.result == 'success'", block)
        self.assertNotIn('external-documents', block)
        self.assertIn('platform-release-${{ github.repository }}-${{ matrix.platform }}-${{ matrix.version }}', block)

    def test_platform_publication_checks_downloaded_artifact_before_writes(self):
        block = job('platform-releases')
        digest = block.index('Record the digest before Artifact upload')
        upload = block.index('id: upload')
        download = block.index('artifact-ids:')
        publish = block.index('tools.platform_release publish')
        self.assertLess(digest, upload); self.assertLess(upload, download); self.assertLess(download, publish)
        self.assertIn('--expected-sha256 ${{ steps.digest.outputs.sha256 }}', block)
        self.assertIn("matrix.build && github.event_name == 'workflow_run'", block)
        self.assertIn('tools.platform_release recover', block)

    def test_complete_delivery_requires_platforms_and_uses_the_planned_filename(self):
        block = job('release-aggregate')
        self.assertIn("needs.platform-releases.result == 'success'", block)
        self.assertIn('pattern: delivery-input-*', block)
        self.assertIn('${{ needs.release-plan.outputs.archive_name }}', block)
        self.assertNotIn('neurobridge-v${{ needs.release-plan.outputs.version }}.zip', SOURCE)
        self.assertIn('gate[\'platforms\'][target[\'platform\']][\'shouldRelease\']', job('release-plan'))
        self.assertIn('tools.platform_release fetch', job('platform-releases'))

    def test_native_input_discovery_remains_pinned_to_commit_and_branch(self):
        block = job('release-targets')
        self.assertIn('--commit "$EXPECTED_SOURCE_SHA" --status success', block)
        self.assertIn('r["headBranch"]==os.environ["EXPECTED_BRANCH"]', block)
        self.assertIn('d.get("headSha")==os.environ["EXPECTED_SOURCE_SHA"]', block)


if __name__ == '__main__':
    unittest.main()
