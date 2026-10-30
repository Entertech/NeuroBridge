"""Failure injection with real local Git remotes and a disposable runner clone.

Only hosting metadata and GraphQL transport are replaced. Ref transactions use
Git's real compare-and-swap semantics; no GitHub writes or real releases occur.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import source_train as train

REPOSITORY = train.ROOT


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory/'source'
        self.root.mkdir()
        self.remote = self.directory/'origin.git'
        original = train.run
        patch = mock.patch.object(train, 'run', side_effect=lambda *args, **kwargs:
                                  original(*args, **({'cwd': self.root} | kwargs)))
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(train, 'ROOT', self.root)
        patch.start(); self.addCleanup(patch.stop)
        self.git('init', '-q', '--initial-branch=master')
        self.identity()
        (self.root/'neurobridge').mkdir()
        (self.root/'.release').mkdir()
        (self.root/'release').mkdir()
        shutil.copyfile(train.CONFIG_PATH, self.root/'.release/code-release-train.toml')
        shutil.copyfile(REPOSITORY/'release/release_matrix.toml', self.root/'release/release_matrix.toml')
        self.registry('0.3.2')
        (self.root/'shared.txt').write_text('baseline\n')
        self.commit('baseline')
        self.baseline = self.git('rev-parse', 'HEAD')
        self.git('init', '--bare', '-q', str(self.remote))
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-q', 'origin', 'master')
        self.registry('0.3.3')
        (self.root/'.release/train.json').write_text(json.dumps({
            'version': '0.3.3', 'defaultBaseline': self.baseline, 'requirements': ['feature/example']}))
        self.commit('initialize selected version')
        self.publication = self.git('rev-parse', 'HEAD')
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/publish_0.3.3', 'HEAD:refs/heads/dev_0.3.3')
        (self.root/'candidate.txt').write_text('exact gated source\n')
        self.commit('post-rebase candidate')
        self.candidate = self.git('rev-parse', 'HEAD')
        self.obj = train.Train('0.3.3', True)
        self.record = {'schemaVersion': 1, 'repository': self.obj.repository, 'version': '0.3.3',
                       'policyFingerprint': self.obj.fingerprint(), 'publication': self.publication,
                       'defaultBaseline': self.baseline, 'candidate': self.candidate,
                       'candidateBranch': 'release-candidate/0.3.3/recovery', 'phase': 'candidate'}
        self.obj.save(self.record)
        self.cleanup_mutations = 0
        self.before_cleanup = None

    def git(self, *args, data=None):
        return subprocess.check_output(['git', *args], cwd=self.root,
                                       input=data.encode('utf-8') if data is not None else None,
                                       stderr=subprocess.STDOUT).decode('utf-8').strip()

    def identity(self):
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')

    def registry(self, version):
        (self.root/'neurobridge/version_registry.toml').write_text('[application]\nversion="'+version+'"\n')

    def commit(self, message):
        self.git('add', '-A'); self.git('commit', '-qm', message)

    def api(self, path, payload=None):
        if path == 'graphql':
            self.cleanup_mutations += 1
            if self.before_cleanup:
                self.before_cleanup()
            updates = payload['variables']['input']['refUpdates']
            self.assertTrue(all(not update['force'] and update['afterOid'] == '0'*40 for update in updates))
            commands = ['start'] + ['delete '+update['name']+' '+update['beforeOid'] for update in updates]
            commands += ['prepare', 'commit']
            try:
                self.git('--git-dir='+str(self.remote), 'update-ref', '--stdin', data='\n'.join(commands)+'\n')
            except subprocess.CalledProcessError:
                return {'errors': [{'message': 'expected OID changed; transaction rejected'}]}
            return {'data': {'updateRefs': {'clientMutationId': None}}}
        self.assertEqual(path, 'repos/'+self.obj.repository)
        return {'default_branch': 'master', 'allow_merge_commit': True, 'node_id': 'fixture-repository'}

    def test_new_runner_restores_candidate_objects_from_checkpoint_without_rebase(self):
        # Simulate the runner disappearing after saving state but before pushing
        # the hidden branch. A single-branch clone cannot initially see candidate.
        publication_before = self.obj.ref('refs/heads/publish_0.3.3')
        fresh = self.directory/'fresh-runner'
        self.git('clone', '-q', '--no-local', '--single-branch', '--branch', 'master', '--no-tags', str(self.remote), str(fresh))
        self.root = fresh
        train.ROOT = fresh
        self.identity()
        with self.assertRaises(subprocess.CalledProcessError): self.git('cat-file', '-e', self.candidate)
        self.obj = train.Train('0.3.3', True)
        promotion = {'state': 'MERGED', 'headRefName': 'dev_0.3.3', 'baseRefName': 'publish_0.3.3',
                     'mergeCommit': {'oid': self.publication},
                     'reviews': [{'author': {'login': 'reviewer'}, 'state': 'APPROVED'}]}
        state_before = self.obj.ref('refs/heads/release-state/0.3.3')
        with mock.patch.object(self.obj, 'mutable'), mock.patch.object(self.obj, 'protection'), \
             mock.patch.object(self.obj, 'pr', return_value=promotion), mock.patch.object(train, 'api', side_effect=self.api):
            first = self.obj.prepare(23)
            second = self.obj.prepare(23)
        self.assertEqual(first['candidate'], self.candidate)
        self.assertEqual(second['candidate'], self.candidate)
        self.assertEqual(self.obj.ref('refs/heads/'+self.record['candidateBranch']), self.candidate)
        self.assertEqual(self.obj.ref('refs/heads/publish_0.3.3'), publication_before)
        self.assertEqual(self.obj.ref('refs/heads/release-state/0.3.3'), state_before)
        self.assertIsNone(self.obj.tag())

    def finalized(self):
        self.git('push', '-q', 'origin', self.candidate+':refs/heads/'+self.record['candidateBranch'],
                 self.candidate+':refs/heads/finalize_0.3.3')
        self.git('tag', '-a', 'v0.3.3', self.candidate, '-m', 'published source')
        self.git('push', '-q', 'origin', 'refs/tags/v0.3.3')
        self.git('checkout', '-q', '-B', 'master', self.baseline)
        (self.root/'parallel.txt').write_text('reviewed default update\n')
        self.commit('default advances')
        self.git('merge', '--no-ff', '-qm', 'protected finalization', self.candidate)
        self.merged = self.git('rev-parse', 'HEAD')
        self.git('push', '-q', 'origin', 'master')
        self.record.update(finalizationPR=24, taggedCandidate=self.candidate, phase='tagged-pending-merge')
        self.obj.save(self.record)
        return {'state': 'MERGED', 'headRefOid': self.candidate, 'mergeCommit': {'oid': self.merged}}

    def verify(self, pr):
        with mock.patch.object(self.obj, 'mutable'), mock.patch.object(self.obj, 'pr', return_value=pr), \
             mock.patch.object(train, 'api', side_effect=self.api):
            return self.obj.verify()

    def test_verified_merge_cleans_only_expected_branches_and_retains_source_evidence(self):
        result = self.verify(self.finalized())
        self.assertEqual(result['phase'], 'complete')
        self.assertEqual(result['verifiedDefault'], self.merged)
        for branch in ('dev_0.3.3', 'finalize_0.3.3', self.record['candidateBranch']):
            self.assertIsNone(self.obj.ref('refs/heads/'+branch))
        self.assertEqual(self.obj.ref('refs/heads/publish_0.3.3'), self.publication)
        self.assertEqual(self.obj.load()['candidate'], self.candidate)
        self.assertEqual(self.obj.tag(), self.candidate)
        self.assertEqual(self.cleanup_mutations, 1)

    def test_concurrent_ref_advance_rejects_entire_cleanup_transaction(self):
        pr = self.finalized()
        (self.root/'next.txt').write_text('new work after publication\n')
        self.commit('unreleased work')
        advanced = self.git('rev-parse', 'HEAD')
        self.git('push', '-q', 'origin', advanced+':refs/heads/unreleased-fixture')
        hidden = self.record['candidateBranch']
        self.before_cleanup = lambda: self.git('--git-dir='+str(self.remote), 'update-ref', 'refs/heads/'+hidden, advanced)
        with self.assertRaisesRegex(ValueError, 'atomic branch cleanup rejected'): self.verify(pr)
        self.assertEqual(self.obj.ref('refs/heads/dev_0.3.3'), self.publication)
        self.assertEqual(self.obj.ref('refs/heads/finalize_0.3.3'), self.candidate)
        self.assertEqual(self.obj.ref('refs/heads/'+hidden), advanced)
        self.assertEqual(self.obj.tag(), self.candidate)

    def test_merged_pr_with_equal_tree_but_squashed_history_keeps_all_branches(self):
        pr = self.finalized()
        tree = self.git('rev-parse', self.merged+'^{tree}')
        squashed = self.git('commit-tree', tree, '-p', self.baseline, '-m', 'squashed source')
        self.git('push', '-q', 'origin', squashed+':refs/heads/squash-fixture')
        self.git('--git-dir='+str(self.remote), 'update-ref', 'refs/heads/master', squashed)
        pr['mergeCommit']['oid'] = squashed
        with self.assertRaises(RuntimeError): self.verify(pr)
        for branch in ('dev_0.3.3', 'finalize_0.3.3', self.record['candidateBranch']):
            self.assertIsNotNone(self.obj.ref('refs/heads/'+branch))
        self.assertEqual(self.obj.tag(), self.candidate)
        self.assertEqual(self.cleanup_mutations, 0)

    def test_failure_after_tag_keeps_checkpoint_and_every_branch(self):
        self.finalized()
        state = self.obj.ref('refs/heads/release-state/0.3.3')
        with mock.patch.object(self.obj, 'mutable'), mock.patch.object(train, 'api', side_effect=self.api):
            result = self.obj.fail()
        self.assertEqual(result['phase'], 'tagged-pending-merge')
        self.assertEqual(self.obj.ref('refs/heads/release-state/0.3.3'), state)
        for branch in ('dev_0.3.3', 'finalize_0.3.3', self.record['candidateBranch']):
            self.assertIsNotNone(self.obj.ref('refs/heads/'+branch))
        self.assertEqual(self.obj.tag(), self.candidate)
        self.assertEqual(self.cleanup_mutations, 0)

    def test_untagged_failure_removes_only_hidden_branch_and_retains_recovery_objects(self):
        self.git('push', '-q', 'origin', self.candidate+':refs/heads/'+self.record['candidateBranch'])
        with mock.patch.object(self.obj, 'mutable'), mock.patch.object(train, 'api', side_effect=self.api):
            self.obj.fail()
        self.assertIsNone(self.obj.ref('refs/heads/'+self.record['candidateBranch']))
        for branch in ('dev_0.3.3', 'publish_0.3.3'):
            self.assertEqual(self.obj.ref('refs/heads/'+branch), self.publication)
        restored = train.Train('0.3.3').load()
        self.assertEqual(restored['phase'], 'failed')
        self.assertEqual(restored['candidate'], self.candidate)
        self.git('cat-file', '-e', restored['candidate'])
        self.assertIsNone(self.obj.tag())

    def test_default_merge_conflict_blocks_finalization_without_changing_source(self):
        (self.root/'shared.txt').write_text('candidate change\n'); self.commit('candidate conflict')
        candidate = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', '-B', 'master', self.baseline)
        (self.root/'shared.txt').write_text('default change\n'); self.commit('default conflict')
        default = self.git('rev-parse', 'HEAD')
        with self.assertRaises(RuntimeError): train.preserving_updates(candidate, candidate, default)
        self.assertEqual(self.git('rev-parse', 'HEAD'), default)
        self.assertEqual(self.obj.ref('refs/heads/publish_0.3.3'), self.publication)
