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
        self.assertIn('systems: ${{ needs.source.outputs.systems }}', block)
        source = (WORKFLOWS/'source-train.yml').read_text()
        self.assertIn('actions/create-github-app-token@v2', source)
        self.assertIn('secrets.RELEASE_BOT_APP_ID', source)
        self.assertNotIn('token: ${{ github.token }}', source)
        self.assertIn("group: source-train-${{ github.repository }}${{ inputs.operation == 'plan' && format('-plan-{0}', github.run_id) || '' }}", source)

    def test_read_only_plan_does_not_use_the_release_app(self):
        source_path = WORKFLOWS/'source-train.yml'
        plan = job_block('plan', source_path)
        self.assertIn("inputs.operation == 'plan'", plan)
        self.assertIn('tools.source_train plan', plan)
        self.assertNotIn('create-github-app-token', plan)
        self.assertNotIn('--execute', plan)
        self.assertIn("inputs.operation != 'plan'", job_block('source', source_path))

    def test_document_preflight_runs_before_any_release_build(self):
        release_path = WORKFLOWS/'release.yml'
        plan = job_block('release-plan', release_path)
        self.assertIn('tools.release_preflight', plan)
        self.assertIn('tools/check-external-protocol.sh', plan)
        self.assertIn('--systems "$RELEASE_SYSTEMS"', plan)
        for job in ('external-documents', 'product-candidates', 'release-targets'):
            self.assertRegex(job_block(job, release_path), r'needs: .*release-plan')

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

    def test_delivery_receipt_and_upload_use_the_zip_outside_the_build_directory(self):
        block = job_block('release-aggregate', WORKFLOWS/'release.yml')
        self.assertIn('DELIVERY_DIR: artifacts/delivery', block)
        self.assertIn('DELIVERY_ZIP: artifacts/delivery/neurobridge-v', block)
        self.assertIn('--release-dir artifacts/release', block)
        self.assertIn('--output "$DELIVERY_ZIP"', block)
        self.assertIn('--directory "$DELIVERY_DIR"', block)
        self.assertIn('path: ${{ env.DELIVERY_ZIP }}', block)

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
        self.assertEqual(result.stdout.splitlines(), ['repos/fixture/actions/artifacts/2'])
