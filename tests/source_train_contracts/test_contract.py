from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

from tools import source_train as train

CONFIG = tomllib.loads(train.CONFIG_PATH.read_text())
GOOD = '1'*40
OTHER = '2'*40


class ContractTests(unittest.TestCase):
    def test_git_plumbing_stdin_uses_exact_utf8_bytes_without_newline_translation(self):
        entry = '100644 blob '+GOOD+'\tattempt.json\n'
        result = subprocess.CompletedProcess([], 0, stdout=b'tree-oid\n', stderr=b'')
        with mock.patch.object(train.subprocess, 'run', return_value=result) as process:
            self.assertEqual(train.run('git', 'mktree', data=entry), 'tree-oid')
        self.assertEqual(process.call_args.kwargs['input'], entry.encode('utf-8'))
        self.assertNotIn('text', process.call_args.kwargs)
        self.assertNotIn('encoding', process.call_args.kwargs)

    def test_every_direct_entry_rejects_noncanonical_versions(self):
        for bad in ('latest', 'dev', 'release', '1', '1.4', '01.2.3', '1.02.3', '1.2.03', '1.2.3-rc.1', '1.2.3+build', '1.2.3.4', '1.2.3\n', '-1.2.3'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): train.numeric(bad)
                with self.assertRaises(ValueError): train.Train(bad)
                with self.assertRaises(ValueError): train.names(CONFIG, bad)
        self.assertEqual(train.numeric('0.1.0'), (0, 1, 0))

    def test_exact_templates_never_add_a_second_prefix(self):
        names = train.names(CONFIG, '5.1.3')
        self.assertEqual(names['integration'], 'dev_5.1.3')
        self.assertEqual(names['publish'], 'publish_5.1.3')
        self.assertEqual(names['finalization'], 'finalize_5.1.3')
        self.assertEqual(names['release_candidate'], 'release-candidate/5.1.3/{attempt}')

    def test_missing_skipped_and_failed_code_checks_never_pass(self):
        good = {'name': 'test', 'status': 'completed', 'conclusion': 'success'}
        train.check_results([good], ['test'])
        for checks in ([], [good | {'status': 'in_progress'}], [good | {'conclusion': 'skipped'}],
                       [good | {'conclusion': 'failure'}], [good | {'conclusion': 'cancelled'}],
                       [good, good | {'conclusion': 'neutral'}]):
            with self.subTest(checks=checks):
                with self.assertRaises(ValueError): train.check_results(checks, ['test'])

    def test_review_required_and_latest_review_revokes_approval(self):
        pr = {'state': 'OPEN', 'isDraft': False, 'headRefName': 'feature/a', 'baseRefName': 'dev_1.2.3',
              'reviewDecision': 'APPROVED', 'reviews': [{'author': {'login': 'alice'}, 'state': 'APPROVED'}]}
        train.check_pr(pr, 'feature/a', 'dev_1.2.3', 1)
        for change in ({'isDraft': True}, {'state': 'CLOSED'}, {'baseRefName': 'master'}, {'reviewDecision': 'REVIEW_REQUIRED'},
                       {'reviews': pr['reviews'] + [{'author': {'login': 'alice'}, 'state': 'CHANGES_REQUESTED'}]}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError): train.check_pr(pr | change, 'feature/a', 'dev_1.2.3', 1)

    def test_actions_source_writes_require_the_app_and_explicit_execution(self):
        obj = train.Train('1.2.3')
        with self.assertRaisesRegex(ValueError, '--execute'): obj.mutable()
        obj.execute = True
        with mock.patch.object(train, 'run', return_value=''), mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'TRAIN_APP_TOKEN_USED': 'false'}):
            with self.assertRaisesRegex(ValueError, 'GitHub App'): obj.mutable()
        with mock.patch.object(train, 'run', return_value=' M user-file'):
            with self.assertRaisesRegex(ValueError, 'clean worktree'): obj.mutable()

    def test_default_only_rewriting_policy_blocks_before_packaging(self):
        obj = train.Train('1.2.3')
        with mock.patch.object(train, 'run', return_value='false'), mock.patch.object(train, 'api', return_value={'default_branch': 'master', 'allow_merge_commit': False}):
            with self.assertRaisesRegex(ValueError, 'merge commit'): obj.default()

    def test_automatic_branch_deletion_blocks_source_publication(self):
        obj = train.Train('1.2.3')
        with mock.patch.object(train, 'run', return_value='false'), \
             mock.patch.object(train, 'api', return_value={'default_branch': 'master', 'allow_merge_commit': True, 'delete_branch_on_merge': True}):
            with self.assertRaisesRegex(ValueError, 'deferred train cleanup'):
                obj.default()

    def test_existing_tag_without_record_never_starts_a_candidate(self):
        obj = train.Train('1.2.3', True)
        pr = {'state': 'MERGED', 'headRefName': 'dev_1.2.3', 'baseRefName': 'publish_1.2.3', 'mergeCommit': {'oid': GOOD}, 'reviews': [{'author': {'login': 'alice'}, 'state': 'APPROVED'}]}
        with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'default', return_value=('master', OTHER)), \
             mock.patch.object(obj, 'protection'), mock.patch.object(obj, 'pr', return_value=pr), \
             mock.patch.object(obj, 'fetch', return_value=GOOD), mock.patch.object(obj, 'metadata', return_value={}), \
             mock.patch.object(obj, 'load', return_value=None), mock.patch.object(obj, 'tag', return_value=GOOD), \
             mock.patch.object(train, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'no trustworthy'): obj.prepare(12)
            run.assert_not_called()

    def test_unchanged_retry_returns_exact_candidate_without_rebase(self):
        obj = train.Train('1.2.3', True)
        pr = {'state': 'MERGED', 'headRefName': 'dev_1.2.3', 'baseRefName': 'publish_1.2.3', 'mergeCommit': {'oid': GOOD}, 'reviews': [{'author': {'login': 'alice'}, 'state': 'APPROVED'}]}
        record = {'publication': GOOD, 'candidate': OTHER, 'candidateBranch': 'release-candidate/1.2.3/fixture', 'policyFingerprint': obj.fingerprint()}
        with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'default', return_value=('master', '3'*40)), \
             mock.patch.object(obj, 'protection'), mock.patch.object(obj, 'pr', return_value=pr), \
             mock.patch.object(obj, 'fetch', return_value=GOOD), mock.patch.object(obj, 'metadata', return_value={}), \
             mock.patch.object(obj, 'load', return_value=record), mock.patch.object(obj, 'tag', return_value=None), \
             mock.patch.object(obj, 'ref', return_value=OTHER), mock.patch.object(train, 'run') as run:
            self.assertIs(obj.prepare(12), record)
            run.assert_not_called()

    def test_retry_restores_recorded_candidate_after_interrupted_push(self):
        obj = train.Train('1.2.3', True)
        pr = {'state': 'MERGED', 'headRefName': 'dev_1.2.3', 'baseRefName': 'publish_1.2.3',
              'mergeCommit': {'oid': GOOD}, 'reviews': [{'author': {'login': 'alice'}, 'state': 'APPROVED'}]}
        record = {'publication': GOOD, 'candidate': OTHER, 'candidateBranch': 'release-candidate/1.2.3/fixture',
                  'policyFingerprint': obj.fingerprint()}
        for remote in (None, GOOD):
            with self.subTest(remote=remote), mock.patch.object(obj, 'mutable'), \
                 mock.patch.object(obj, 'default', return_value=('master', '3'*40)), \
                 mock.patch.object(obj, 'protection'), mock.patch.object(obj, 'pr', return_value=pr), \
                 mock.patch.object(obj, 'fetch', return_value=GOOD), mock.patch.object(obj, 'metadata', return_value={}), \
                 mock.patch.object(obj, 'load', return_value=record), mock.patch.object(obj, 'tag', return_value=None), \
                 mock.patch.object(obj, 'ref', return_value=remote), mock.patch.object(train, 'run') as run:
                if remote is None:
                    self.assertIs(obj.prepare(12), record)
                    run.assert_called_once_with('git', 'push', 'origin', OTHER+':refs/heads/'+record['candidateBranch'])
                else:
                    with self.assertRaisesRegex(ValueError, 'candidate branch changed'): obj.prepare(12)
                    run.assert_not_called()

    def test_discovery_survives_tools_module_loaded_first(self):
        root = Path(__file__).resolve().parents[2]
        script = """
import sys, unittest
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root/'tools'))
import source_train
loader = unittest.TestLoader()
suite = loader.discover(str(root/'tests'), pattern='test_contract.py')
assert not loader.errors, loader.errors
assert suite.countTestCases() > 0
"""
        subprocess.run([sys.executable, '-c', script], cwd=root, check=True, capture_output=True, text=True)

    def test_cleanup_uses_atomic_expected_tips_without_forcing(self):
        obj = train.Train('1.2.3', True)
        expected = {'dev_1.2.3': GOOD, 'finalize_1.2.3': OTHER}
        with mock.patch.object(train, 'api', side_effect=[{'node_id': 'repository-id'}, {'data': {'updateRefs': {}}}]) as api:
            obj.delete_refs(expected)
        path, payload = api.call_args.args
        self.assertEqual(path, 'graphql')
        updates = payload['variables']['input']['refUpdates']
        self.assertEqual(updates, [{'name': 'refs/heads/'+branch, 'beforeOid': sha,
                                   'afterOid': '0'*40, 'force': False} for branch, sha in expected.items()])

    def test_failed_attempt_never_deletes_a_changed_candidate_branch(self):
        obj = train.Train('1.2.3', True)
        record = {'candidate': GOOD, 'candidateBranch': 'release-candidate/1.2.3/fixture'}
        with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'required_record', return_value=record), \
             mock.patch.object(obj, 'tag', return_value=None), mock.patch.object(obj, 'ref', return_value=OTHER), \
             mock.patch.object(obj, 'save') as save, mock.patch.object(obj, 'delete_refs') as delete:
            with self.assertRaisesRegex(ValueError, 'candidate branch changed'): obj.fail()
            save.assert_not_called(); delete.assert_not_called()

    def test_orphan_publication_branch_is_preserved_before_initialization(self):
        obj = train.Train('1.2.3', True)
        with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'fetch', return_value=GOOD), \
             mock.patch.object(obj, 'default', return_value=('master', GOOD)), \
             mock.patch.object(obj, 'ref', side_effect=[None, OTHER]), mock.patch.object(train, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'publication branch already exists'):
                obj.initialize(['feature/fixture'])
            run.assert_not_called()

    def test_missing_recorded_immutable_tag_is_not_recreated(self):
        obj = train.Train('1.2.3')
        with mock.patch.object(obj, 'load', return_value={'candidate': GOOD, 'taggedCandidate': GOOD}), mock.patch.object(obj, 'tag', return_value=None):
            with self.assertRaisesRegex(ValueError, 'do not recreate'): obj.required_record()

    def test_state_ref_contains_candidate_and_previous_checkpoint_as_parents(self):
        obj = train.Train('1.2.3'); obj.state_sha = OTHER
        calls = []
        def git(*args, **kwargs):
            calls.append((args, kwargs)); return GOOD
        with mock.patch.object(train, 'run', side_effect=git):
            obj.save({'candidate': GOOD, 'version': '1.2.3'})
        commit = next(args for args, _ in calls if args[:2] == ('git', 'commit-tree'))
        self.assertEqual(commit[3:], ('-p', GOOD, '-p', OTHER))
        push = calls[-1][0]
        self.assertEqual(push[:3], ('git', 'push', 'origin'))
        self.assertNotIn('--force', push)
        self.assertTrue(push[-1].endswith(':refs/heads/release-state/1.2.3'))

    def test_existing_verified_artifact_skips_packaging_and_resending(self):
        obj = train.Train('1.2.3', True)
        record = {'candidate': GOOD, 'review': {'sha': GOOD}, 'artifact': {'id': 7, 'status': 'verified'}}
        with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'default', return_value=('master', OTHER)), \
             mock.patch.object(obj, 'protection'), mock.patch.object(obj, 'required_record', return_value=record), \
             mock.patch.object(obj, 'checks'), mock.patch.object(obj, 'save') as save:
            self.assertIs(obj.begin_release(), record)
            save.assert_not_called()

    def test_uncertain_or_lost_package_evidence_never_starts_another_build(self):
        cases = [
            ({'artifacts': []}, {'status': 'in_progress'}, 'active or uncertain'),
            ({'artifacts': []}, {'status': 'completed', 'conclusion': 'success'}, 'active or uncertain'),
            ({'artifacts': [{'name': 'verified-release-'+GOOD, 'expired': True}]}, {}, 'expired'),
            ({'artifacts': [{'name': 'train-package-receipt-'+GOOD}]}, {}, 'cannot be recovered'),
        ]
        for artifacts, status, reason in cases:
            with self.subTest(reason=reason):
                obj = train.Train('1.2.3', True)
                record = {'candidate': GOOD, 'review': {'sha': GOOD}, 'artifact': None, 'packageRunId': '17'}
                with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'default', return_value=('master', OTHER)), \
                     mock.patch.object(obj, 'protection'), mock.patch.object(obj, 'required_record', return_value=record), \
                     mock.patch.object(obj, 'checks'), mock.patch.object(obj, 'save') as save, \
                     mock.patch.object(train, 'api', side_effect=lambda path: artifacts if path.endswith('/artifacts?per_page=100') else status):
                    with self.assertRaisesRegex(ValueError, reason): obj.begin_release()
                    save.assert_not_called()

    def test_failed_candidate_code_check_prevents_packaging_checkpoint(self):
        obj = train.Train('1.2.3', True)
        record = {'candidate': GOOD, 'review': {'sha': GOOD}}
        with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'default', return_value=('master', OTHER)), \
             mock.patch.object(obj, 'protection'), mock.patch.object(obj, 'required_record', return_value=record), \
             mock.patch.object(obj, 'checks', side_effect=ValueError('required code check failed')), \
             mock.patch.object(obj, 'save') as save, mock.patch.object(train, 'api') as api:
            with self.assertRaisesRegex(ValueError, 'required code check failed'): obj.begin_release()
            save.assert_not_called(); api.assert_not_called()

    def test_merge_uses_preserving_method_and_expected_head_without_bypass(self):
        obj = train.Train('1.2.3', True)
        pr = {'number': 10, 'state': 'OPEN', 'isDraft': False, 'headRefName': 'finalize_1.2.3',
              'baseRefName': 'master', 'headRefOid': GOOD, 'reviewDecision': 'APPROVED',
              'reviews': [{'author': {'login': 'alice'}, 'state': 'APPROVED'}]}
        with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'default', return_value=('master', OTHER)), \
             mock.patch.object(obj, 'protection'), mock.patch.object(obj, 'required_record', return_value={'candidate': GOOD, 'finalizationPR': 10}), \
             mock.patch.object(obj, 'tag', return_value=GOOD), mock.patch.object(obj, 'checks'), \
             mock.patch.object(obj, 'pr', return_value=pr), mock.patch.object(obj, 'fetch', return_value=GOOD), \
             mock.patch.object(obj, 'verify', return_value={'phase': 'complete'}), mock.patch.object(train, 'version_at', return_value='1.2.2'), mock.patch.object(train, 'preserving_updates'), mock.patch.object(train, 'run') as run:
            obj.merge()
        merge = run.call_args_list[-1].args
        self.assertIn('--merge', merge); self.assertIn('--match-head-commit', merge)
        self.assertNotIn('--admin', merge); self.assertNotIn('--squash', merge)

    def test_wrong_tag_or_nonancestor_keeps_all_branches(self):
        for wrong_tag in (True, False):
            obj = train.Train('1.2.3', True)
            record = {'candidate': GOOD, 'finalizationPR': 10}
            with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'required_record', return_value=record), \
                 mock.patch.object(obj, 'default', return_value=('master', OTHER)), mock.patch.object(obj, 'pr', return_value={'state': 'MERGED'}), \
                 mock.patch.object(obj, 'tag', return_value=OTHER if wrong_tag else GOOD), mock.patch.object(obj, 'save') as save, \
                 mock.patch.object(train, 'run', side_effect=RuntimeError('not ancestor')) as run:
                with self.assertRaises((ValueError, RuntimeError)): obj.verify()
                save.assert_not_called()
                self.assertFalse(any('push' in call.args for call in run.call_args_list))


class GitSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.patch = mock.patch.object(train, 'ROOT', self.root); self.patch.start(); self.addCleanup(self.patch.stop)
        # run's default cwd is bound at definition time; give the fixture its cwd.
        original = train.run
        self.p = mock.patch.object(train, 'run', side_effect=lambda *a, **k: original(*a, **({'cwd': self.root} | k)))
        self.p.start(); self.addCleanup(self.p.stop)
        self.git('init', '-q'); self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root/'neurobridge').mkdir(); (self.root/'.release').mkdir()
        self.registry('0.3.2'); self.commit('baseline'); self.baseline = self.git('rev-parse', 'HEAD')

    def git(self, *a):
        return subprocess.check_output(['git', *a], cwd=self.root, text=True).strip()

    def registry(self, version):
        (self.root/'neurobridge/version_registry.toml').write_text('[application]\nversion="'+version+'"\n')

    def commit(self, message):
        self.git('add', '-A'); self.git('commit', '-qm', message)

    def test_requirement_changes_code_without_initializing_version(self):
        (self.root/'neurobridge/app.py').write_text('new feature')
        self.commit('requirement')
        result = train.source_check('feature/new', self.baseline)
        self.assertFalse(result['shouldRelease'])
        self.registry('0.3.3'); self.commit('illegal feature bump')
        with self.assertRaisesRegex(ValueError, 'must not initialize'):
            train.source_check('feature/new', self.baseline)

    def test_first_requirement_push_resolves_zero_before_from_remote_default(self):
        with mock.patch.object(train, 'api', return_value={'default_branch': 'master'}), \
             mock.patch.object(train.Train, 'fetch', return_value=self.baseline) as fetch:
            result = train.source_check('feature/new', '0'*40)
            self.assertFalse(result['shouldRelease'])
            fetch.assert_called_once_with('refs/heads/master')

    def test_push_with_missing_before_uses_verified_default(self):
        (self.root/'docs.txt').write_text('rebased documentation')
        self.commit('rebased requirement')
        for before in ('f'*40, '0'*40):
            with self.subTest(before=before), \
                 mock.patch.object(train, 'api', return_value={'default_branch': 'master'}), \
                 mock.patch.object(train.Train, 'fetch', return_value=self.baseline) as fetch:
                result = train.source_check('codex/rebased', before, event='push')
                self.assertEqual(result['reason'], 'requirement_validation')
                fetch.assert_called_once_with('refs/heads/master')

    def test_force_push_with_available_nonancestor_still_uses_default(self):
        self.registry('0.3.3'); self.commit('abandoned version bump')
        before = self.git('rev-parse', 'HEAD')
        self.git('reset', '--hard', self.baseline)
        (self.root/'docs.txt').write_text('rewritten requirement')
        self.commit('replacement')
        with mock.patch.object(train, 'api', return_value={'default_branch': 'master'}), \
             mock.patch.object(train.Train, 'fetch', return_value=self.baseline) as fetch:
            result = train.source_check('feature/rewritten', before, event='push')
            self.assertFalse(result['shouldRelease'])
            fetch.assert_called_once_with('refs/heads/master')

    def test_fast_forward_push_keeps_before_without_remote_lookup(self):
        (self.root/'docs.txt').write_text('ordinary push'); self.commit('docs')
        with mock.patch.object(train, 'api') as api, mock.patch.object(train.Train, 'fetch') as fetch:
            self.assertFalse(train.source_check('codex/ordinary', self.baseline, event='push')['shouldRelease'])
            api.assert_not_called(); fetch.assert_not_called()

    def test_rebased_requirement_cannot_hide_illegal_version_initialization(self):
        self.registry('0.3.3'); self.commit('illegal requirement bump')
        with mock.patch.object(train, 'api', return_value={'default_branch': 'master'}), \
             mock.patch.object(train.Train, 'fetch', return_value=self.baseline):
            with self.assertRaisesRegex(ValueError, 'must not initialize'):
                train.source_check('codex/rebased', 'f'*40, event='push')

    def test_pr_and_explicit_cli_baselines_never_fall_back(self):
        for event in ('pull_request', None):
            with self.subTest(event=event), mock.patch.object(train, 'api') as api:
                with self.assertRaises(RuntimeError):
                    train.source_check('feature/pr', 'f'*40, event=event)
                api.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'requires its target baseline'):
            train.source_check('feature/pr', '0'*40, event='pull_request')
        with mock.patch.object(train, 'api') as api:
            self.assertFalse(train.source_check('feature/pr', self.baseline, event='pull_request')['shouldRelease'])
            api.assert_not_called()

    def test_default_push_cannot_fall_back_to_its_own_tip(self):
        with mock.patch.object(train, 'api', return_value={'default_branch': 'master'}), \
             mock.patch.object(train.Train, 'fetch') as fetch:
            with self.assertRaisesRegex(ValueError, 'available ancestor baseline'):
                train.source_check('master', 'f'*40, event='push')
            fetch.assert_not_called()

    def test_fallback_rejects_default_mismatch_and_fetch_failure(self):
        with mock.patch.object(train, 'api', return_value={'default_branch': 'unexpected'}):
            with self.assertRaisesRegex(ValueError, 'configuration mismatch'):
                train.source_check('codex/rebased', 'f'*40, event='push')
        with mock.patch.object(train, 'api', return_value={'default_branch': 'master'}), \
             mock.patch.object(train.Train, 'fetch', side_effect=RuntimeError('fetch unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'fetch unavailable'):
                train.source_check('codex/rebased', 'f'*40, event='push')

    def test_rebase_changes_candidate_but_does_not_rewrite_publication(self):
        self.git('branch', 'default')
        self.registry('0.3.3'); self.commit('initialize train')
        self.git('branch', 'publish_0.3.3'); publication = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', 'default'); (self.root/'docs.txt').write_text('parallel default change'); self.commit('default advances')
        default = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', '--detach', publication)
        self.git('rebase', '--rebase-merges', '--onto', default, self.baseline)
        candidate = self.git('rev-parse', 'HEAD')
        self.assertNotEqual(candidate, publication)
        self.assertEqual(self.git('rev-parse', 'publish_0.3.3'), publication)
        self.git('merge-base', '--is-ancestor', default, candidate)
        self.assertEqual(train.version_at(candidate), '0.3.3')

    def test_forged_merge_with_unbuilt_edits_is_rejected(self):
        self.registry('0.3.3'); self.commit('candidate'); candidate = self.git('rev-parse', 'HEAD')
        (self.root/'unbuilt.txt').write_text('unverified source')
        self.git('add', '-A'); tree = self.git('write-tree')
        forged = self.git('commit-tree', tree, '-p', candidate, '-p', self.baseline, '-m', 'forged merge')
        with self.assertRaisesRegex(ValueError, 'unpackaged tree'):
            train.preserving_updates(candidate, forged, self.baseline)

    def test_clean_default_merge_preserves_packaged_candidate(self):
        self.git('branch', 'parallel-default')
        self.registry('0.3.3'); self.commit('candidate'); candidate = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', 'parallel-default')
        (self.root/'default.txt').write_text('reviewed parallel default change'); self.commit('default advance')
        default = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', '--detach', candidate)
        self.git('merge', '--no-ff', '-qm', 'preserving update', default)
        train.preserving_updates(candidate, self.git('rev-parse', 'HEAD'), default)



class ArtifactTests(unittest.TestCase):
    def bundle(self, root: Path, missing: str | None = None, magic: bool = True):
        import importlib.util
        import zipfile
        from neurobridge.versioning import APPLICATION_VERSION
        from tools import release_pipeline as pipeline
        commit = pipeline.run('git', 'rev-parse', 'HEAD')
        for target in pipeline.matrix():
            folder = root/'targets'/target['id']; folder.mkdir(parents=True)
            result = {'target': target, 'sourceCommit': commit, 'status': 'blocked', 'reason': 'fixture'}
            if target['id'] != missing:
                package = folder/f"fixture-{target['id']}.{target['format']}"
                package.write_bytes((pipeline.MAGIC[target['format']] if magic else b'source zip')+b'fixture bytes')
                (folder/'validation.log').write_text('fixture')
                result.update(status='candidate', fileName=package.name, sha256=pipeline.sha256(package), validationLog='validation.log', sourceReferences=[])
            pipeline.save_json(folder/'result.json', result)
        directory = root/'release'; manifest = pipeline.assemble(root/'targets', directory, 'push_master')
        manifest['git']['dirty'] = False
        pipeline.save_json(directory/'release-manifest.json', manifest)
        documents = root/'documents'; documents.mkdir()
        with zipfile.ZipFile(documents/'neurobridge-external-documents.zip', 'w') as f:
            f.writestr('release-manifest.json', json.dumps({'documents': []}))
        spec = importlib.util.spec_from_file_location('fixture_bundle', pipeline.ROOT/'tools/build-release-bundle.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        archive = root/'downloaded'/'bundle.zip'
        with mock.patch.object(module, 'render_directory_pdf', return_value=b'%PDF-1.4 fixture'):
            module.build_bundle(directory, documents, archive)
        return archive, APPLICATION_VERSION, commit, pipeline.matrix()

    def test_every_defined_installer_and_exact_sha_required(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.bundle(Path(directory))
            receipt = train.artifact_receipt(*args)
            self.assertEqual(len(receipt['targets']), 5)
            self.assertEqual(receipt['sourceCommit'], args[2])
            with self.assertRaisesRegex(ValueError, 'source commit'):
                train.artifact_receipt(args[0], args[1], '0'*40, args[3])

    def test_green_minimum_platform_bundle_cannot_release_a_train_with_missing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.bundle(Path(directory), missing='windows-10-x86_64-exe')
            with self.assertRaisesRegex(ValueError, 'every defined package'):
                train.artifact_receipt(*args)

    def test_source_bytes_with_installer_suffix_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.bundle(Path(directory), magic=False)
            with self.assertRaisesRegex(ValueError, 'installer format'):
                train.artifact_receipt(*args)

    def test_destination_never_creates_a_missing_source_tag(self):
        from tools import publish_release
        with mock.patch.object(publish_release, 'command', return_value='') as command:
            with self.assertRaisesRegex(ValueError, 'must exist'):
                publish_release.ensure_tag('v1.2.3', GOOD)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[:2], ('git', 'ls-remote'))

    def test_uncertain_remote_lookup_does_not_become_a_missing_release(self):
        from tools import publish_release
        result = subprocess.CompletedProcess([], 1, stdout='', stderr='network timeout')
        with mock.patch.object(publish_release.subprocess, 'run', return_value=result):
            with self.assertRaises(ValueError):
                publish_release.command('gh', 'release', 'view', 'v1.2.3', allow_missing=True)


    def test_downloaded_artifact_must_match_the_pre_upload_receipt(self):
        from tools.release_pipeline import matrix
        with tempfile.TemporaryDirectory() as directory:
            archive, version, candidate, targets = self.bundle(Path(directory))
            original = train.artifact_receipt(archive, version, candidate, targets)
            obj = train.Train(version, True)
            record = {'candidate': candidate, 'packageRunId': '17', 'phase': 'packaging'}
            def api(path):
                if path.endswith('/artifacts/7'):
                    return {'expired': False, 'name': 'verified-release-'+candidate, 'workflow_run': {'id': 17}}
                return {'artifacts': [{'id': 6, 'name': 'train-package-receipt-'+candidate, 'expired': False}]}
            for tamper in (False, True):
                proof = original | ({'sha256': '0'*64} if tamper else {})
                def download(*args, **kwargs):
                    (Path(args[-1])/'package-receipt.json').write_text(json.dumps(proof))
                    return ''
                with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'required_record', return_value=copy.deepcopy(record)), \
                     mock.patch.object(train, 'api', side_effect=api), mock.patch.object(train, 'run', side_effect=download), \
                     mock.patch.object(obj, 'save') as save:
                    if tamper:
                        with self.assertRaisesRegex(ValueError, 'pre-upload'):
                            obj.record_artifact(archive.parent, 7)
                        save.assert_not_called()
                    else:
                        result = obj.record_artifact(archive.parent, 7)
                        self.assertEqual(result['artifact']['sha256'], original['sha256'])
                        self.assertEqual(result['phase'], 'published')
                        save.assert_called_once()


class RemoteAncestryTests(GitSourceTests):
    # Exercise real local Git remotes; no hosting writes or real packages.
    def test_fresh_remote_tag_wins_over_cached_local_tag_and_replace_refs(self):
        self.registry('0.3.3'); self.commit('candidate'); candidate = self.git('rev-parse', 'HEAD')
        self.git('branch', 'candidate')
        self.git('checkout', '-q', '-B', 'master', self.baseline)
        (self.root/'default.txt').write_text('parallel default')
        self.commit('default advance')
        self.git('merge', '--no-ff', '-qm', 'preserving finalization', 'candidate')
        merged = self.git('rev-parse', 'HEAD')
        remote = self.root.parent/(self.root.name+'-remote.git')
        self.addCleanup(lambda: __import__('shutil').rmtree(remote, ignore_errors=True))
        subprocess.check_call(['git', 'init', '--bare', '-q', str(remote)])
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', '-q', 'origin', 'master')
        self.git('tag', '-a', 'v0.3.3', candidate, '-m', 'source')
        self.git('push', '-q', 'origin', 'v0.3.3')
        self.git('tag', '-d', 'v0.3.3')
        self.git('tag', 'v0.3.3', self.baseline)  # stale local tag must not count
        self.git('replace', candidate, self.baseline)
        obj = train.Train('0.3.3', True)
        record = {'candidate': candidate, 'publication': candidate, 'finalizationPR': 10,
                  'candidateBranch': 'release-candidate/0.3.3/fixture'}
        expected = {'dev_0.3.3': candidate, record['candidateBranch']: candidate, 'finalize_0.3.3': candidate}
        for branch, sha in (expected | {'publish_0.3.3': candidate}).items():
            self.git('push', '-q', 'origin', sha+':refs/heads/'+branch)
        with mock.patch.object(obj, 'mutable'), mock.patch.object(obj, 'required_record', return_value=record), \
             mock.patch.object(obj, 'save') as save, mock.patch.object(obj, 'delete_refs') as delete, \
             mock.patch.object(obj, 'pr', return_value={'state': 'MERGED', 'headRefOid': candidate, 'mergeCommit': {'oid': merged}}), \
             mock.patch.object(train, 'api', return_value={'default_branch': 'master', 'allow_merge_commit': True}):
            result = obj.verify()
        self.assertEqual(result['phase'], 'complete')
        self.assertEqual(result['verifiedDefault'], merged)
        self.assertEqual(save.call_count, 1)
        delete.assert_called_once_with(expected)
        # Fetch with --refmap= updates FETCH_HEAD, not the stale local tag.
        self.assertEqual(self.git('rev-parse', 'v0.3.3'), self.baseline)

    def test_extra_plain_commit_is_not_a_preserving_finalization_update(self):
        self.registry('0.3.3'); self.commit('candidate'); candidate = self.git('rev-parse', 'HEAD')
        (self.root/'unpackaged.txt').write_text('unverified change'); self.commit('extra source edit')
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'merge updates only'):
            train.preserving_updates(candidate, head, self.baseline)

    def test_cleanup_preserves_advanced_train_branches(self):
        self.registry('0.3.3'); self.commit('candidate'); candidate = self.git('rev-parse', 'HEAD')
        publication = candidate
        self.git('branch', 'candidate')
        self.git('merge', '--no-ff', '-qm', 'nothing to merge', 'candidate')
        merged = self.git('commit-tree', self.git('rev-parse', 'HEAD^{tree}'), '-p', self.baseline,
                          '-p', candidate, '-m', 'finalization')
        (self.root/'next.txt').write_text('unreleased work'); self.commit('next train work')
        advanced = self.git('rev-parse', 'HEAD')
        obj = train.Train('0.3.3', True)
        record = {'candidate': candidate, 'publication': publication, 'finalizationPR': 10,
                  'candidateBranch': 'release-candidate/0.3.3/fixture'}
        for changed in ('dev_0.3.3', record['candidateBranch'], 'finalize_0.3.3'):
            refs = {'dev_0.3.3': publication, 'publish_0.3.3': publication,
                    record['candidateBranch']: candidate, 'finalize_0.3.3': candidate}
            refs[changed] = advanced
            with self.subTest(changed=changed), mock.patch.object(obj, 'mutable'), \
                 mock.patch.object(obj, 'required_record', return_value=copy.deepcopy(record)), \
                 mock.patch.object(obj, 'default', return_value=('master', merged)), \
                 mock.patch.object(obj, 'tag', return_value=candidate), \
                 mock.patch.object(obj, 'pr', return_value={'state': 'MERGED', 'headRefOid': candidate, 'mergeCommit': {'oid': merged}}), \
                 mock.patch.object(obj, 'ref', side_effect=lambda ref: refs[ref.removeprefix('refs/heads/')]), \
                 mock.patch.object(obj, 'fetch', side_effect=lambda ref: refs[ref.removeprefix('refs/heads/')]), \
                 mock.patch.object(obj, 'delete_refs') as delete, mock.patch.object(obj, 'save') as save:
                with self.assertRaises((ValueError, RuntimeError)): obj.verify()
                save.assert_not_called(); delete.assert_not_called()
