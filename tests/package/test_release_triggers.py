"""Source-train package boundary contracts; ordinary CI never distributes."""
from pathlib import Path
import subprocess
import re
import unittest
from tests.package.test_native_inputs_workflow import job_block

WORKFLOWS = Path(__file__).resolve().parents[2] / '.github/workflows'


class ReleaseTriggerTests(unittest.TestCase):
    def test_code_validation_cannot_start_packaging(self):
        text = (WORKFLOWS/'ci.yml').read_text()
        self.assertNotIn('uses: ./.github/workflows/release.yml', text)
        self.assertIn('python -m tools.source_train check', text)
        for pattern in ('feature/**', 'bugfix/**', 'dev_*', 'publish_*', 'release-candidate/**', 'finalize_*'):
            self.assertEqual(text.count(pattern), 2)

    def test_only_explicit_release_enters_packaging(self):
        block = job_block('packages', WORKFLOWS/'source-train.yml')
        self.assertIn("needs.source.outputs.operation == 'release'", block)
        self.assertIn("needs.source.outputs.should_package == 'true'", block)
        self.assertIn('source_sha: ${{ needs.source.outputs.candidate }}', block)
        self.assertIn('source_train: true', block)
        source = (WORKFLOWS/'source-train.yml').read_text()
        self.assertIn('actions/create-github-app-token@v2', source)
        self.assertIn('secrets.RELEASE_BOT_APP_ID', source)
        self.assertNotIn('token: ${{ github.token }}', source)
        self.assertIn('group: source-train-${{ github.repository }}\n', source)

    def test_packaging_never_creates_tags_or_releases(self):
        text = (WORKFLOWS/'release.yml').read_text()
        self.assertNotIn('release-publish:', text)
        self.assertNotIn('tools.publish_release', text)
        self.assertIn('tools.source_train verify-package', text)
        self.assertLess(text.index('train-package-receipt-'), text.index('id: upload'))
        self.assertIn('retention-days: 90', text)
        self.assertIn('inputs.source_sha || github.sha', text)
        bootstrap = (WORKFLOWS/'kylin-bootstrap.yml').read_text()
        self.assertNotIn('workflow_run', bootstrap)
        self.assertIn('workflow_dispatch:', bootstrap)

    def test_failure_path_retains_tagged_source(self):
        text = job_block('retain-failed-attempt', WORKFLOWS/'source-train.yml')
        self.assertIn("needs.packages.result == 'failure'", text)
        self.assertIn("needs.finalize.result == 'failure'", text)
        self.assertIn('tools.source_train fail', text)
        self.assertNotIn('git push', text)

    def test_cleanup_preserves_both_test_diagnostics_and_train_receipts(self):
        block = job_block('release-cleanup', WORKFLOWS/'release.yml')
        script = block.split('        run: |\n', 1)[1]
        script = '\n'.join(line[10:] for line in script.splitlines())
        script = re.sub(r'\$\{\{[^}]+\}\}', 'fixture', script)
        fake_gh = '''gh() {
          if [[ "$*" == *'--method DELETE'* ]]; then printf '%s\\n' "${@: -1}";
          else printf '%s\\n' '1\trelease-plan-source' '2\trelease-target-kylin' '3\tverified-release-source' '4\twindows-algorithm-log' '5\ttrain-package-receipt-source'; fi
        }
'''
        result = subprocess.run(['bash', '-e', '-c', fake_gh+script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ['repos/fixture/actions/artifacts/1', 'repos/fixture/actions/artifacts/2'])
