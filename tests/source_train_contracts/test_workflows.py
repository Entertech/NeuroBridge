"""Reject realistic workflow regressions using the actual YAML configuration."""
import copy
import unittest

import yaml

from tools import check_source_train_workflows as policy
from tools.source_train import CONFIG_PATH
import tomllib


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.config = tomllib.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        self.workflows = policy.load_workflows()

    def test_repository_workflows_satisfy_contract(self):
        policy.validate(self.config, self.workflows)

    def test_version_gate_passes_event_context_for_push_baseline_recovery(self):
        steps = self.workflows['ci.yml']['jobs']['version-gate']['steps']
        gate = next(step for step in steps if step.get('id') == 'gate')
        self.assertEqual(gate['env']['SOURCE_EVENT'], '${{ github.event_name }}')
        self.assertEqual(gate['env']['BASE_SHA'], '${{ github.event.pull_request.base.sha || github.event.before }}')
        self.assertIn('--event "$SOURCE_EVENT"', gate['run'])

    def test_yaml_on_key_and_real_booleans_are_preserved(self):
        value = yaml.load('on: [push]\nflag: false\n', Loader=policy.WorkflowLoader)
        self.assertEqual(value['on'], ['push'])
        self.assertIs(value['flag'], False)
        self.assertIs(yaml.safe_load('on: true')[True], True)  # global loader unchanged

    def test_permissions_regression_in_caller_or_callee_is_rejected(self):
        for remove_grant in (True, False):
            with self.subTest(remove_grant=remove_grant):
                workflows = copy.deepcopy(self.workflows)
                if remove_grant:
                    workflows['source-train.yml']['jobs']['packages']['permissions']['actions'] = 'read'
                else:
                    workflows['release.yml']['jobs']['release-cleanup']['permissions']['deployments'] = 'write'
                with self.assertRaisesRegex(ValueError, 'cannot grant'):
                    policy.validate(self.config, workflows)

    def test_missing_renamed_or_skipped_required_check_is_rejected(self):
        for mutation in ('missing', 'renamed', 'skipped', 'false', 'version-gate-skipped'):
            with self.subTest(mutation=mutation):
                workflows = copy.deepcopy(self.workflows)
                jobs = workflows['ci.yml']['jobs']
                if mutation == 'missing': del jobs['unit-and-websocket']
                elif mutation == 'renamed': jobs['unit-and-websocket']['name'] = 'unrecognized check'
                elif mutation == 'false': jobs['unit-and-websocket']['if'] = False
                elif mutation == 'version-gate-skipped': jobs['version-gate']['if'] = False
                else: jobs['unit-and-websocket']['if'] = "github.ref == 'refs/heads/master'"
                with self.assertRaisesRegex(ValueError, 'required code check'):
                    policy.validate(self.config, workflows)

    def test_every_stage_must_receive_push_and_pr_checks_without_path_filters(self):
        for event in ('push', 'pull_request'):
            for mutation in ('default-only', 'excluded-finalization', 'paths'):
                with self.subTest(event=event, mutation=mutation):
                    workflows = copy.deepcopy(self.workflows)
                    trigger = workflows['ci.yml']['on'][event]
                    if mutation == 'default-only': trigger['branches'] = ['master']
                    elif mutation == 'excluded-finalization': trigger['branches'].append('!finalize_*')
                    else: trigger['paths'] = ['neurobridge/**']
                    with self.assertRaises(ValueError): policy.validate(self.config, workflows)

    def test_ordinary_events_cannot_enter_either_packager(self):
        for workflow in ('release.yml', 'kylin-bootstrap.yml'):
            for event in ('push', 'pull_request', 'workflow_run'):
                with self.subTest(workflow=workflow, event=event):
                    workflows = copy.deepcopy(self.workflows)
                    workflows[workflow]['on'][event] = None
                    with self.assertRaisesRegex(ValueError, 'ordinary push/PR/Test'):
                        policy.validate(self.config, workflows)
