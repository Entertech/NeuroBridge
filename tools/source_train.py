#!/usr/bin/env python3
"""Source release orchestration. Packaging is delegated to release.yml.

No command writes without --execute. Actions runs write with the triggering
workflow token; the workflow dispatches code checks explicitly because that
token's push and pull-request events do not start new workflow runs. Checkpoints
use fast-forward-only state refs whose parents retain the candidate Git objects
after a failed untagged candidate branch is removed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
CONFIG_PATH = ROOT / ".release/code-release-train.toml"


def numeric(version: str) -> tuple[int, int, int]:
    if not VERSION.fullmatch(version):
        raise ValueError("version must be canonical major.minor.patch")
    return tuple(map(int, version.split('.')))


def names(config: dict, version: str) -> dict[str, str]:
    numeric(version)
    return {key.removesuffix('_template'): value.format(version=version, attempt='{attempt}')
            for key, value in config['branches'].items() if key.endswith('_template')}


def run(*args: str, cwd: Path = ROOT, data: str | None = None) -> str:
    env = os.environ.copy()
    env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
    # Git plumbing consumes a byte protocol. Text-mode stdin translates LF to
    # CRLF on Windows, making mktree store a literal CR in "attempt.json\r".
    result = subprocess.run(args, cwd=cwd, input=data.encode('utf-8') if data is not None else None, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {result.stderr.decode('utf-8', errors='replace').strip()}")
    return result.stdout.decode('utf-8').strip()


def api(path: str, payload: dict | None = None, method: str | None = None):
    args = ['gh', 'api', path]
    if method:
        args += ['--method', method]
    if payload is not None:
        args += ['--input', '-']
    raw = run(*args, data=json.dumps(payload) if payload is not None else None)
    return json.loads(raw) if raw else None


def version_at(ref: str) -> str:
    return tomllib.loads(run('git', 'show', f'{ref}:neurobridge/version_registry.toml'))['application']['version']


def require_sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise ValueError("expected a full immutable commit SHA")
    return value


def check_results(checks: list[dict], required: list[str]) -> None:
    # Missing, cancelled, skipped, neutral and still-running checks do not pass.
    for name in required:
        matches = [item for item in checks if item['name'] == name]
        if not matches or any(item.get('conclusion') != 'success' or item.get('status') != 'completed' for item in matches):
            raise ValueError(f"required code check is not successful: {name}")


def checks_pending(checks: list[dict], required: list[str]) -> bool:
    """True while listed checks are still running and none has failed."""
    pending = False
    for name in required:
        matches = [item for item in checks if item['name'] == name]
        if not matches:
            continue
        for item in matches:
            if item.get('status') != 'completed':
                pending = True
            elif item.get('conclusion') != 'success':
                return False
    return pending


WORKFLOW_PR_AUTHOR = 'github-actions[bot]'
CHECK_POLL_SECONDS = 30


def expected_pr_author() -> str:
    """Return the account that must author train-created pull requests."""
    override = os.environ.get('TRAIN_PR_AUTHOR')
    if override:
        return override
    if os.environ.get('TRAIN_RELEASE_IDENTITY') == 'workflow':
        return WORKFLOW_PR_AUTHOR
    return run('gh', 'api', 'user', '--jq', '.login')


def require_approvals(pr: dict, approvals: int):
    latest = {item['author']['login']: item['state'] for item in pr.get('reviews', [])
              if item['state'] in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED')}
    if sum(state == 'APPROVED' for state in latest.values()) < approvals:
        raise ValueError('insufficient independent approvals')


def check_pr(pr: dict, head: str, base: str, approvals: int) -> None:
    if pr['state'] != 'OPEN' or pr['isDraft'] or pr['headRefName'] != head or pr['baseRefName'] != base:
        raise ValueError("PR must be open, non-draft and target the selected train")
    if pr.get('reviewDecision') != 'APPROVED':
        raise ValueError("PR approval is required")
    require_approvals(pr, approvals)


def preserving_updates(candidate: str, head: str, default_tip: str):
    run('git', 'merge-base', '--is-ancestor', candidate, head)
    for extra in run('git', 'rev-list', head, '^'+candidate, '^'+default_tip).splitlines():
        if len(run('git', 'rev-list', '--parents', '-n', '1', extra).split()) < 3:
            raise ValueError('finalization permits preserving merge updates only')
    # Parent counts alone allow a hand-crafted merge carrying unbuilt edits.
    # Compare the resulting merge so an unchanged candidate PR can also merge
    # into a default branch that advanced through unrelated reviewed changes.
    expected = run('git', 'merge-tree', '--write-tree', candidate, default_tip)
    if run('git', 'merge-tree', '--write-tree', head, default_tip) != expected:
        raise ValueError('finalization contains an unpackaged tree change')


def package_systems(record: dict) -> str:
    scope = record.get('packageScope')
    if scope is None:
        return ''  # Previous attempts used the full matrix.
    systems = scope['systems']
    if not isinstance(systems, list) or not systems or any(not isinstance(system, str) for system in systems):
        raise ValueError('recorded package scope must contain selected systems')
    return ','.join(systems)


def artifact_receipt(archive: Path, version: str, candidate: str, matrix: list[dict]) -> dict:
    numeric(version)
    require_sha(candidate)
    from tools.publish_release import load_bundle_manifest, verify_nested_archives, bundle_packages
    from tools.release_pipeline import sha256, MAGIC, system_version, validate_target_coverage
    loaded = load_bundle_manifest(archive.parent)
    if loaded is None or loaded[0].resolve() != archive.resolve():
        raise ValueError("one canonical delivery ZIP is required")
    _, manifest = loaded
    if manifest['applicationVersion'] != version or manifest['git']['commit'] != candidate or manifest['git']['dirty']:
        raise ValueError("package version or exact source commit does not match candidate")
    if manifest['releaseStatus'] != 'candidate' or manifest['trigger'] != 'push_master':
        raise ValueError('train package must carry candidate provenance')
    expected = {item['id']: item for item in matrix}
    actual = validate_target_coverage(manifest['coverage'], matrix)
    if any(item.get('status') != 'candidate' for item in actual.values()):
        raise ValueError("every defined package must exist; failed, blocked or skipped targets forbid publication")
    verify_nested_archives(archive, manifest)
    # Verify real installer headers inside the delivery ZIP, not a green job or source ZIP.
    with zipfile.ZipFile(archive) as outer:
        bundle = json.loads(outer.read('metadata/bundle-manifest.json'))
        build = json.loads(outer.read('metadata/build-manifest.json'))
        logs = [json.loads(line) for line in outer.read('metadata/release-logs.jsonl').decode().splitlines()]
        if (build['sourceCommit'] != candidate or build['applicationVersion'] != version
                or build['coverage'] != manifest['coverage']):
            raise ValueError('build provenance does not match candidate')
        if len(logs) != len(expected) or {item['target']['id'] for item in logs} != set(expected):
            raise ValueError('complete per-package source evidence required')
        for item in logs:
            if (item['sourceCommit'] != candidate or item['status'] != 'candidate'
                    or item['target'] != expected[item['target']['id']]):
                raise ValueError('installer source evidence differs from candidate')
        package_records = [{'targetId': item['target']['id'], 'fileName': item['fileName'], 'sha256': item['sha256']} for item in logs]
        if sorted((item['target']['platform'], system_version(item['target']), item['target']['architecture'],
                   item['fileName'], item['target']['format'], item['sha256']) for item in logs) != bundle_packages(bundle):
            raise ValueError('installer receipts differ from bundled package identities')
        def installers(blob: bytes) -> list[bytes]:
            import io
            values = []
            with zipfile.ZipFile(io.BytesIO(blob)) as nested:
                for name in nested.namelist():
                    value = nested.read(name)
                    if name.endswith('.zip'):
                        values.extend(installers(value))
                    elif Path(name).suffix.removeprefix('.') in MAGIC:
                        fmt = Path(name).suffix.removeprefix('.')
                        if not value.startswith(MAGIC[fmt]):
                            raise ValueError(f"invalid installer format: {name}")
                        values.append(value)
            return values
        packages = []
        for system in bundle['systemArchives']:
            packages.extend(installers(outer.read(system['fileName'])))
    if len(packages) != len(expected):
        raise ValueError("delivery ZIP does not contain every defined installer")
    return {'sourceCommit': candidate, 'version': version, 'fileName': archive.name,
            'sha256': sha256(archive), 'targets': sorted(actual),
            'packages': sorted(package_records, key=lambda item: item['targetId'])}


def delivery_receipt(archive: Path, version: str, candidate: str, targets: list[dict], *, per_system=False, software_versions=None) -> dict:
    from tools.release_pipeline import software_tag, system_version

    receipt = artifact_receipt(archive, version, candidate, targets)
    if not per_system:
        return receipt
    receipt["systems"] = {}
    systems = {system_version(target) for target in targets}
    root = archive.parent / "systems"
    if not root.is_dir() or {path.name for path in root.iterdir()} != systems:
        raise ValueError("downloaded system bundles differ from the selected systems")
    for system in sorted(systems):
        selected = [target for target in targets if system_version(target) == system]
        software_version = (software_versions or {}).get(system, version)
        system_archive = root / system / f"neurobridge-{system}-v{software_version}.zip"
        scoped = artifact_receipt(system_archive, version, candidate, selected)
        canonical = [item for item in receipt["packages"] if item["targetId"] in scoped["targets"]]
        if scoped["packages"] != canonical:
            raise ValueError(f"{system} bundle differs from the verified installer bytes")
        with zipfile.ZipFile(system_archive) as bundle:
            manifest = json.loads(bundle.read("metadata/bundle-manifest.json"))
            if manifest.get("softwareSystem") != system or not isinstance(manifest.get("documentObligations"), list):
                raise ValueError(f"{system} bundle lacks system or document obligation evidence")
            if manifest.get("softwareVersion", version) != software_version:
                raise ValueError(f"{system} bundle version differs from the registry")
        receipt["systems"][system] = scoped | {"tag": software_tag(system, software_version)}
    return receipt


class Train:
    def __init__(self, version: str, execute: bool = False):
        numeric(version)
        self.version = version
        self.config = tomllib.loads(CONFIG_PATH.read_text())
        self.repository = self.config['repository']['name']
        self.names = names(self.config, version)
        self.execute = execute
        self.state_sha = None

    def fingerprint(self) -> str:
        return hashlib.sha256(CONFIG_PATH.read_bytes() + (ROOT/'release/release_matrix.toml').read_bytes()).hexdigest()

    def ref(self, branch: str) -> str | None:
        # Nonzero lookup is uncertainty, never evidence that a tag/ref is absent.
        lines = run('git', 'ls-remote', '--refs', 'origin', branch).splitlines()
        if not lines:
            return None
        if len(lines) != 1:
            raise ValueError("ambiguous remote ref")
        return require_sha(lines[0].split()[0])

    def fetch(self, ref: str) -> str:
        env = os.environ.copy()
        env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull)
        subprocess.run(['git', 'fetch', '--no-tags', '--refmap=', 'origin', ref], cwd=ROOT, env=env, check=True, capture_output=True)
        return require_sha(run('git', 'rev-parse', '--verify', 'FETCH_HEAD^{commit}'))

    def default_repository(self) -> tuple[str, dict]:
        meta = api(f'repos/{self.repository}')
        branch = meta['default_branch']
        if branch != self.config['repository']['default_branch']:
            raise ValueError("configured default branch differs from repository metadata")
        return branch, meta

    @staticmethod
    def require_merge_policy(meta: dict) -> None:
        if meta.get('allow_merge_commit') is not True:
            raise ValueError("default branch must permit a merge commit preserving the candidate")
        if meta.get('delete_branch_on_merge'):
            raise ValueError('automatic PR branch deletion would violate deferred train cleanup')

    def default(self) -> tuple[str, str]:
        if run('git', 'rev-parse', '--is-shallow-repository') != 'false':
            raise ValueError('full history is required for source finalization')
        branch, meta = self.default_repository()
        self.require_merge_policy(meta)
        return branch, self.fetch(f'refs/heads/{branch}')

    def mutable(self):
        if not self.execute:
            raise ValueError("mutation requires an explicit --execute")
        print(json.dumps({'sourceOperation': getattr(self, 'operation', 'explicit-cli'),
              'repository': self.repository, 'defaultBranch': self.config['repository']['default_branch'],
              'version': self.version, 'branches': self.names,
              'requirements': getattr(self, 'requirements', []),
              'remoteWrites': 'only the selected source operation; no force-push or Ruleset changes'}, ensure_ascii=False), file=sys.stderr)
        if run('git', 'status', '--porcelain'):
            raise ValueError("source orchestration requires a clean worktree")
        if os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('TRAIN_RELEASE_IDENTITY') != 'workflow':
            raise ValueError("automated source writes use the triggering workflow identity")
        origin = run('git', 'remote', 'get-url', 'origin')
        origin = origin.replace('git@github.com:', 'https://github.com/', 1)
        parsed = urlsplit(origin)
        if parsed.hostname != 'github.com' or parsed.path.strip('/').removesuffix('.git').casefold() != self.repository.casefold():
            raise ValueError('origin differs from the configured GitHub repository')

    def protection(self, branch: str) -> dict:
        # Classic protection and Rulesets may coexist. Query effective rules too;
        # callers must never silently install or weaken protection.
        try:
            rules = api(f'repos/{self.repository}/rules/branches/{branch}')
        except RuntimeError as error:
            if self.unreadable(error):
                return self.protection_unverified(branch)
            raise
        pull = [r['parameters'] for r in rules if r['type'] == 'pull_request']
        if any(p.get('allowed_merge_methods') and 'merge' not in p['allowed_merge_methods'] for p in pull):
            raise ValueError('branch protection permits only rewriting merge methods')
        if pull and all(p.get('required_approving_review_count', 0) >= self.config['code_gates']['required_reviewers'] for p in pull):
            if any(r['type'] == 'required_linear_history' for r in rules):
                raise ValueError("linear-only protection cannot preserve a tagged candidate by merge commit")
            return {'branch': branch, 'status': 'passed'}
        try:
            policy = api(f'repos/{self.repository}/branches/{branch}/protection')
        except RuntimeError as error:
            if self.unreadable(error):
                return self.protection_unverified(branch)
            raise ValueError(f"reviewed PR protection must be configured for {branch}") from error
        reviews = policy.get('required_pull_request_reviews') or {}
        if reviews.get('required_approving_review_count', 0) < self.config['code_gates']['required_reviewers']:
            raise ValueError(f"{branch} requires at least one approving review before enabling the train")
        if not policy.get('enforce_admins', {}).get('enabled'):
            raise ValueError('branch review protection must also apply to administrators')
        if policy.get('required_linear_history', {}).get('enabled'):
            raise ValueError("linear-only protection cannot preserve candidate ancestry")
        return {'branch': branch, 'status': 'passed'}

    @staticmethod
    def unreadable(error: Exception) -> bool:
        message = str(error)
        return any(marker in message for marker in ('HTTP 403', 'Resource not accessible', 'not accessible by integration'))

    def protection_unverified(self, branch: str) -> dict:
        # Actions installation tokens cannot hold the Administration read that
        # Rulesets and classic branch protection require. GitHub still enforces
        # the protection at merge time; record the limitation instead of failing
        # every train operation on an API call the token may never satisfy.
        reason = 'workflow token cannot read Rulesets or branch protection (Administration read unavailable)'
        print(json.dumps({'event': 'branch_protection_unverified', 'branch': branch, 'reason': reason}, ensure_ascii=False), file=sys.stderr)
        return {'branch': branch, 'status': 'unverified', 'reason': reason}

    def plan_actions_identity(self) -> dict:
        identity = self.config['repository'].get('release_identity')
        if identity != 'workflow':
            return {'status': 'blocked', 'reason': 'release writes must use the triggering workflow token'}
        return {'status': 'workflow', 'continuation': 'explicit-workflow-dispatch'}

    def package_scope(self, candidate: str, requested: str = "") -> dict:
        from tools.release_pipeline import matrix, release_base, release_config_at, system_release_bases, system_version
        from tools.release_scope import scope_for

        base = release_base(self.repository, report=False)
        config = release_config_at(candidate)
        baselines = {system_version(target): base for target in matrix(config)}
        state = names(self.config, version_at(base))['state']
        ref = f'refs/heads/{state}'
        if self.ref(ref):
            commit = self.fetch(ref)
            record = json.loads(run('git', 'show', f'{commit}:attempt.json'))
            if record['candidate'] != base or record['repository'] != self.repository:
                raise ValueError('prior release scope does not match the published source')
            previous = record.get('packageScope')
            if previous is not None:
                for system in baselines.keys() - set(previous['systems']):
                    baselines[system] = require_sha(previous['baseCommits'].get(system, base))
        baselines.update(system_release_bases(self.repository, set(baselines)))
        return scope_for(candidate, baselines, requested, config=config)

    def plan(self, head: str, systems: str = "") -> dict:
        from tools.release_preflight import formal_document_blockers, release_plan
        from tools.release_pipeline import select_targets, software_tag

        default, meta = self.default_repository()
        baseline = self.fetch(f'refs/heads/{default}')
        result = release_plan(head, baseline, software=True)
        record = self.load()
        same_source = record and record['candidate'] == result['sourceCommit']
        scope = record.get('packageScope') if same_source else None
        if scope is None:
            scope = self.package_scope(result['sourceCommit'], systems)
        elif systems and select_targets(result['packages'], systems) != select_targets(result['packages'], package_systems(record)):
            raise ValueError('requested systems differ from the recorded package scope')
        result['packageScope'] = scope
        result['systemTags'] = {system: software_tag(system, result.get("softwareVersions", {}).get(system, self.version)) for system in scope['systems']}
        result['packages'] = select_targets(result['packages'], ','.join(scope['systems'])) if scope['systems'] else []
        result['formalDocumentBlockers'] = formal_document_blockers(result)
        try:
            if 'allow_merge_commit' not in meta or 'delete_branch_on_merge' not in meta:
                merge_policy = {'status': 'unverified', 'reason': 'Repository merge settings are not visible to this token.'}
            else:
                self.require_merge_policy(meta)
                merge_policy = {'status': 'passed'}
        except ValueError as error:
            merge_policy = {'status': 'blocked', 'reason': str(error)}
        protections = {}
        for branch in (default, self.names['integration'], self.names['publish']):
            try:
                protections[branch] = self.protection(branch)
            except ValueError as error:
                protections[branch] = {'status': 'blocked', 'reason': str(error)}
            except RuntimeError as error:
                protections[branch] = {'status': 'unverified', 'reason': str(error)}
        candidate_gates = {'status': 'pending', 'reason': 'No release candidate recorded.'}
        if record is not None:
            candidate = require_sha(record['candidate'])
            candidate_gates = {'sourceCommit': candidate}
            try:
                self.checks(candidate)
            except (RuntimeError, ValueError) as error:
                candidate_gates['codeChecks'] = {'status': 'blocked', 'reason': str(error)}
            else:
                candidate_gates['codeChecks'] = {'status': 'passed'}
            candidate_gates['humanReview'] = 'recorded' if (record.get('review') or {}).get('sha') == candidate else 'pending'

        return {
            **result, 'repository': self.repository, 'version': self.version, 'branches': self.names,
            'default': default, 'publishPolicy': self.config['source_release'], 'writes': False,
            'gates': {
                'sourceVersion': 'matches_target' if result['applicationVersion'] == self.version else 'differs_from_target',
                'mergePolicy': merge_policy,
                'branchProtection': protections,
                'releaseIdentity': self.plan_actions_identity(),
                'requiredCodeChecks': self.config['code_gates']['checks'],
                'candidate': candidate_gates,
                'packages': 'verified_receipt_required',
            },
        }

    def load(self) -> dict | None:
        ref = f"refs/heads/{self.names['state']}"
        self.state_sha = self.ref(ref)
        if self.state_sha is None:
            return None
        sha = self.fetch(ref)
        record = json.loads(run('git', 'show', f'{sha}:attempt.json'))
        if record.get('version') != self.version or record.get('repository') != self.repository:
            raise ValueError("durable attempt identity mismatch")
        require_sha(record['candidate'])
        return record

    def save(self, record: dict):
        record['updatedAt'] = datetime.now(timezone.utc).isoformat()
        blob = run('git', 'hash-object', '-w', '--stdin', data=json.dumps(record, sort_keys=True, indent=2)+'\n')
        tree = run('git', 'mktree', data=f'100644 blob {blob}\tattempt.json\n')
        parents = ['-p', record['candidate']]
        if self.state_sha:
            parents += ['-p', self.state_sha]
        commit = run('git', 'commit-tree', tree, *parents, data='Checkpoint source release '+self.version+'\n')
        # The previous state is a parent, so a concurrent write is rejected by a
        # normal fast-forward push. Candidate objects remain reachable here.
        run('git', 'push', 'origin', f"{commit}:refs/heads/{self.names['state']}")
        self.state_sha = commit

    def tag(self) -> str | None:
        ref = f"refs/tags/v{self.version}"
        if self.ref(ref) is None:
            return None
        return self.fetch(ref)

    def delivery_tag(self, record: dict | None, *, complete=False) -> str | None:
        if not record or not record.get("systemTags"):
            return self.tag()
        from tools.release_pipeline import software_tag, software_version_at

        expected = {system: software_tag(system, software_version_at(record["candidate"], system)
                                         if "softwareVersions" in record else self.version)
                    for system in package_systems(record).split(",")}
        if record["systemTags"] != expected:
            raise ValueError("recorded software tags differ from the locked release scope")
        if record.get("parallelSystems"):
            expected = {system: tag for system, tag in expected.items()
                        if record.get("systemResults", {}).get(system, {}).get("status") == "published"}
            if complete and not expected:
                raise ValueError("no successful system Release has been recorded")
        found = []
        for tag in expected.values():
            ref = f"refs/tags/{tag}"
            if self.ref(ref) is None:
                if complete:
                    raise ValueError(f"missing immutable software tag: {tag}")
                continue
            commit = self.fetch(ref)
            if commit != record["candidate"]:
                raise ValueError(f"software tag {tag} differs from packaged candidate")
            found.append(commit)
        return found[0] if found else None

    def ensure_candidate(self, record: dict):
        ref = 'refs/heads/'+record['candidateBranch']
        existing = self.ref(ref)
        if existing is None:
            run('git', 'push', 'origin', record['candidate']+':'+ref)
        elif existing != record['candidate']:
            raise ValueError('candidate branch changed; preserve it for reconciliation')

    def delete_refs(self, expected: dict[str, str]):
        if not expected:
            return
        # GitHub updateRefs compares every beforeOid and deletes atomically.
        # A concurrent update preserves all refs instead of deleting new work.
        # https://docs.github.com/en/graphql/reference/git#updaterefs
        repository_id = api(f'repos/{self.repository}')['node_id']
        payload = {'query': 'mutation($input: UpdateRefsInput!) { updateRefs(input: $input) { clientMutationId } }',
                   'variables': {'input': {'repositoryId': repository_id, 'refUpdates': [
                       {'name': 'refs/heads/'+branch, 'beforeOid': require_sha(sha),
                        'afterOid': '0'*40, 'force': False} for branch, sha in expected.items()]}}}
        result = api('graphql', payload)
        if result.get('errors'):
            raise ValueError('atomic branch cleanup rejected; preserve refs for reconciliation')

    def fail(self):
        self.mutable()
        record = self.required_record()
        if self.delivery_tag(record) is None:
            branch = record['candidateBranch']
            existing = self.ref('refs/heads/'+branch)
            if existing is not None and existing != record['candidate']:
                raise ValueError('candidate branch changed; preserve it for reconciliation')
            record['phase'] = 'failed'; self.save(record)
            self.delete_refs({branch: existing} if existing else {})
        return record

    def checks(self, sha: str):
        required = self.config['code_gates']['checks']
        timeout = float(os.environ.get('TRAIN_CHECK_TIMEOUT_SECONDS') or 0)
        deadline = time.monotonic() + timeout
        while True:
            pages = json.loads(run('gh', 'api', '--paginate', '--slurp', f'repos/{self.repository}/commits/{sha}/check-runs?per_page=100'))
            checks = [check for page in pages for check in page['check_runs']]
            # Wait only for runs that already exist; an undispatched check fails now.
            if not checks_pending(checks, required) or time.monotonic() >= deadline:
                check_results(checks, required)
                return
            time.sleep(CHECK_POLL_SECONDS)

    def pr(self, number: int) -> dict:
        return json.loads(run('gh', 'pr', 'view', str(number), '--repo', self.repository, '--json',
                              'number,state,isDraft,headRefName,baseRefName,headRefOid,mergeCommit,reviewDecision,reviews'))

    def open_pr(self, head: str, base: str, title: str) -> int:
        existing = json.loads(run('gh', 'pr', 'list', '--repo', self.repository, '--head', head, '--base', base,
                                  '--state', 'open', '--json', 'number'))
        if len(existing) > 1:
            raise ValueError("multiple promotion/finalization PRs for one train")
        if existing:
            return existing[0]['number']
        url = run('gh', 'pr', 'create', '--repo', self.repository, '--head', head, '--base', base,
                  '--title', title, '--body', f'Source train {self.version}; gates and approval required. Merge commit only for finalization.')
        number = int(url.rsplit('/', 1)[1])
        actual = json.loads(run('gh', 'pr', 'view', str(number), '--repo', self.repository,
                                '--json', 'author,baseRefName,headRefName,state,isDraft'))
        expected = expected_pr_author()
        if actual['author']['login'] != expected or actual['baseRefName'] != base or actual['headRefName'] != head or actual['state'] != 'OPEN' or actual['isDraft']:
            raise ValueError('created PR actor or source/target verification failed; stop further writes')
        return number

    def metadata(self, ref: str) -> dict:
        meta = json.loads(run('git', 'show', f"{ref}:{self.config['source']['metadata']}"))
        if meta['version'] != self.version or version_at(ref) != self.version:
            raise ValueError("branch name, train metadata and version registry disagree")
        require_sha(meta['defaultBaseline'])
        run('git', 'merge-base', '--is-ancestor', meta['defaultBaseline'], ref)
        if numeric(version_at(meta['defaultBaseline'])) >= numeric(self.version):
            raise ValueError('train must advance its recorded baseline version')
        return meta

    def initialize(self, requirements: list[str]):
        self.mutable()
        if not requirements or len(set(requirements)) != len(requirements):
            raise ValueError('select a nonempty, unique requirement scope before initialization')
        for branch in requirements:
            if not any(branch.startswith(prefix) for prefix in self.config['branches']['requirement_prefixes']):
                raise ValueError('unsupported requirement branch')
            self.fetch('refs/heads/'+branch)
        default, baseline = self.default()
        existing = self.ref(f"refs/heads/{self.names['integration']}")
        if existing:
            self.metadata(self.fetch(f"refs/heads/{self.names['integration']}"))
            return self.select(requirements)
        if self.ref(f"refs/heads/{self.names['publish']}"):
            raise ValueError('publication branch already exists without integration metadata; preserve it')
        if numeric(self.version) <= numeric(version_at(baseline)):
            raise ValueError('integration version must advance from default baseline')
        for branch in requirements:
            if version_at(self.fetch('refs/heads/'+branch)) != version_at(baseline):
                raise ValueError('requirement version differs from initialization baseline')
        # Require rules for these patterns BEFORE creating train branches.
        for branch in (default, self.names['integration'], self.names['publish']):
            self.protection(branch)
        old = numeric(version_at(baseline)); new = numeric(self.version)
        impact = { (old[0], old[1], old[2]+1): 'patch', (old[0], old[1]+1, 0): 'minor', (old[0]+1, 0, 0): 'major' }.get(new)
        if impact is None:
            raise ValueError("train initialization requires one documented patch/minor/major step")
        # Never leave the calling checkout on a half-initialized branch.
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)/'source'
            run('git', 'worktree', 'add', '--detach', str(work), baseline)
            try:
                path = work/'neurobridge/version_registry.toml'
                text = path.read_text()
                text = text.replace(f'version = "{version_at(baseline)}"', f'version = "{self.version}"', 1)
                text += (f'\n[[application_release_changes]]\nfrom_version = "{version_at(baseline)}"\n'
                         f'to_version = "{self.version}"\nimpact = "{impact}"\n'
                         'compatibility = "Selected source train; requirement PRs document behavior and compatibility."\n'
                         'evidence = "Explicit version-scope initialization from the recorded default baseline."\n')
                path.write_text(text)
                metadata = work/self.config['source']['metadata']; metadata.parent.mkdir(exist_ok=True)
                metadata.write_text(json.dumps({'version': self.version, 'defaultBranch': default,
                    'defaultBaseline': baseline, 'requirements': requirements}, indent=2)+'\n')
                run(*self.config['source']['synchronize_command'], cwd=work)
                run(*self.config['source']['validate_command'], cwd=work)
                run('git', 'add', '--', 'neurobridge/version_registry.toml', 'pyproject.toml',
                    'config/kylin-runtime-manifest.toml', self.config['source']['metadata'], cwd=work)
                run('git', 'commit', '-m', f'Initialize source train {self.version}', cwd=work)
                candidate = run('git', 'rev-parse', 'HEAD', cwd=work)
                run(sys.executable, '-m', 'tools.release_pipeline', 'gate', '--base', baseline, cwd=work)
                run('git', 'push', '--atomic', 'origin',
                    f"{candidate}:refs/heads/{self.names['integration']}",
                    f"{baseline}:refs/heads/{self.names['publish']}", cwd=work)
            finally:
                run('git', 'worktree', 'remove', '--force', str(work))
        return self.select(requirements)

    def select(self, requirements: list[str]):
        self.mutable()
        integration = self.fetch(f"refs/heads/{self.names['integration']}")
        metadata = self.metadata(integration)
        if set(requirements) != set(metadata['requirements']):
            raise ValueError('selected requirements differ from recorded train scope')
        if not requirements:
            raise ValueError("select explicit requirement branches; an empty scope is forbidden")
        prs = []
        for branch in requirements:
            if not any(branch.startswith(prefix) for prefix in self.config['branches']['requirement_prefixes']):
                raise ValueError("unsupported requirement branch convention")
            if branch.startswith(('codex/source-version', 'codex/release-')):
                raise ValueError("orchestration branch is not a product requirement")
            sha = self.fetch(f'refs/heads/{branch}')
            if version_at(sha) != version_at(self.metadata(integration)['defaultBaseline']):
                raise ValueError("requirements must preserve their development baseline version")
            prs.append(self.open_pr(branch, self.names['integration'], f'Integrate {branch} into {self.version}'))
        return {'integration': self.names['integration'], 'requirementPRs': prs}

    def promote(self):
        self.mutable()
        sha = self.fetch(f"refs/heads/{self.names['integration']}")
        meta = self.metadata(sha)
        self.checks(sha)
        prs = json.loads(run('gh', 'pr', 'list', '--repo', self.repository, '--base', self.names['integration'],
                              '--state', 'merged', '--limit', '1000', '--json', 'number,headRefName'))
        if not meta['requirements'] or len(prs) != len(meta['requirements']) or {p['headRefName'] for p in prs} != set(meta['requirements']):
            raise ValueError('merged requirements differ from the explicitly selected scope')
        for pr in prs:
            require_approvals(self.pr(pr['number']), self.config['code_gates']['required_reviewers'])
        self.protection(self.names['publish'])
        if self.ref(f"refs/heads/{self.names['publish']}") is None:
            run('git', 'push', 'origin', f"{meta['defaultBaseline']}:refs/heads/{self.names['publish']}")
        return {'promotionPR': self.open_pr(self.names['integration'], self.names['publish'], f'Publish source {self.version}')}

    def prepare(self, promotion: int):
        self.mutable()
        default, baseline = self.default()
        self.protection(default)
        self.protection(self.names['publish'])
        pr = self.pr(promotion)
        if pr['state'] != 'MERGED' or pr['headRefName'] != self.names['integration'] or pr['baseRefName'] != self.names['publish']:
            raise ValueError("candidate requires the merged dev-to-publish PR")
        require_approvals(pr, self.config['code_gates']['required_reviewers'])
        publication = self.fetch(f"refs/heads/{self.names['publish']}")
        if pr['mergeCommit']['oid'] != publication:
            raise ValueError("publication branch changed after promotion")
        meta = self.metadata(publication)
        record = self.load()
        tag = self.delivery_tag(record, complete=bool(record and record.get("taggedCandidate")))
        if record:
            if record.get('policyFingerprint') != self.fingerprint():
                raise ValueError('release policy changed; preserve existing attempt for reconciliation')
            if record['publication'] != publication:
                raise ValueError("changed publication inputs require an explicit new release decision")
            if tag and tag != record['candidate']:
                raise ValueError("existing tag differs from durable candidate")
            self.fetch(f"refs/heads/{self.names['state']}")
            if record.get('phase') != 'complete':
                self.ensure_candidate(record)
            return record
        if tag:
            raise ValueError("existing tag has no trustworthy train record; reconcile before proceeding")
        if numeric(self.version) <= numeric(version_at(baseline)):
            raise ValueError('new candidate must advance the current default version')
        # Rebase only a disposable detached checkout, never publish_B itself.
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)/'candidate'
            run('git', 'worktree', 'add', '--detach', str(work), publication)
            try:
                run('git', 'rebase', '--rebase-merges', '--onto', baseline, meta['defaultBaseline'], cwd=work)
                candidate = run('git', 'rev-parse', 'HEAD', cwd=work)
                if version_at(candidate) != self.version:
                    raise ValueError("candidate rebase did not preserve the initialized version")
                for policy_path in ('.release/code-release-train.toml', 'release/release_matrix.toml'):
                    if run('git', 'show', candidate+':'+policy_path) != (ROOT/policy_path).read_text().strip():
                        raise ValueError('candidate changes the release contract; review and bootstrap policy migration first')
                run(*self.config['source']['validate_command'], cwd=work)
                attempt = hashlib.sha256((publication+baseline).encode()).hexdigest()[:16]
                branch = self.names['release_candidate'].format(attempt=attempt)
                record = {'schemaVersion': 1, 'repository': self.repository, 'version': self.version,
                          'policyFingerprint': self.fingerprint(), 'adjustment': 'none',
                          'publication': publication, 'defaultBaseline': baseline, 'candidate': candidate,
                          'candidateBranch': branch, 'promotionPR': promotion, 'phase': 'candidate',
                          'review': None, 'artifact': None, 'download': None}
                self.save(record)
                run('git', 'push', 'origin', f'{candidate}:refs/heads/{branch}')
            finally:
                run('git', 'worktree', 'remove', '--force', str(work))
        return record

    def required_record(self) -> dict:
        record = self.load()
        if record is None:
            raise ValueError("no durable candidate record; prepare the promotion first")
        tag = self.delivery_tag(record, complete=bool(record.get("taggedCandidate")))
        if record.get('taggedCandidate') and tag is None:
            raise ValueError('previously recorded immutable tag disappeared; do not recreate it')
        if tag and tag != record['candidate']:
            raise ValueError("remote tag mismatch; preserve every involved branch")
        if record.get('policyFingerprint') != self.fingerprint():
            raise ValueError('recorded release policy changed; do not rebuild or republish')
        if version_at(record['candidate']) != self.version:
            raise ValueError("recorded candidate version mismatch")
        return record

    def review(self, candidate: str):
        self.mutable()
        record = self.required_record()
        if require_sha(candidate) != record['candidate']:
            raise ValueError("review must name the exact post-rebase candidate SHA")
        actor = os.environ.get('GITHUB_ACTOR') or run('gh', 'api', 'user', '--jq', '.login')
        if actor.endswith('[bot]'):
            raise ValueError("candidate source review must be explicitly approved by a human")
        permission = api(f'repos/{self.repository}/collaborators/{actor}/permission')['permission']
        if permission not in ('admin', 'maintain', 'write'):
            raise ValueError("candidate reviewer requires repository write permission")
        self.checks(candidate)
        record['review'] = {'sha': candidate, 'reviewer': actor, 'at': datetime.now(timezone.utc).isoformat()}
        self.save(record)
        return record

    def begin_release(self, systems: str = "") -> dict:
        self.mutable()
        default, _ = self.default(); self.protection(default)
        record = self.required_record()
        from tools.release_pipeline import matrix, release_config_at, select_targets, software_tag
        recorded_scope = record.get('packageScope')
        recorded_systems = package_systems(record)
        if systems and (recorded_scope or record.get('packageRunId') or record.get('artifact')):
            targets = matrix(release_config_at(record['candidate']))
            if select_targets(targets, systems) != select_targets(targets, recorded_systems):
                raise ValueError('requested systems differ from the recorded package scope')
        if record.get('finalizationPR') and self.delivery_tag(record, complete=True) and self.pr(record['finalizationPR'])['state'] == 'MERGED':
            return self.verify()
        if not record.get('review') or record['review']['sha'] != record['candidate']:
            raise ValueError("exact post-rebase candidate requires human source review")
        self.checks(record['candidate'])
        if record.get('artifact'):
            return record
        # Reconcile an interrupted Artifact upload instead of rebuilding blindly.
        previous_run = record.get('packageRunId')
        if previous_run and record.get("parallelSystems"):
            if any(item["status"] == "published" for item in record.get("systemResults", {}).values()):
                return record
            attempt = record.get('packageRunAttempt', '1')
            status = api(f'repos/{self.repository}/actions/runs/{previous_run}/attempts/{attempt}')
            if status['status'] != 'completed' or status.get('conclusion') not in ('failure', 'cancelled'):
                raise ValueError('previous parallel software release still active or uncertain')
            record["recoveryRunIds"] = list(dict.fromkeys([previous_run, *record.get("recoveryRunIds", [])]))
        elif previous_run:
            artifacts = api(f'repos/{self.repository}/actions/runs/{previous_run}/artifacts?per_page=100')['artifacts']
            matching = [a for a in artifacts if a['name'] == 'verified-release-'+record['candidate']]
            if any(a['expired'] for a in matching):
                raise ValueError('prior published package expired; do not rebuild or resend')
            found = matching
            if len(found) > 1:
                raise ValueError("multiple package receipts require reconciliation")
            if found:
                record['artifact'] = {'id': found[0]['id'], 'runId': previous_run, 'pendingVerification': True}
                self.save(record)
                return record
            if any(a['name'] == 'train-package-receipt-'+record['candidate'] for a in artifacts):
                raise ValueError('package checkpoint exists but canonical bytes cannot be recovered; reconcile before retry')
            attempt = record.get('packageRunAttempt', '1')
            status = api(f'repos/{self.repository}/actions/runs/{previous_run}/attempts/{attempt}')
            if status['status'] != 'completed' or status.get('conclusion') not in ('failure', 'cancelled'):
                raise ValueError('previous package run still active or uncertain')
        if self.delivery_tag(record):
            raise ValueError("tagged candidate is missing its publication receipt; do not rebuild")
        default_tip = self.fetch('refs/heads/'+default)
        from tools.release_preflight import formal_document_blockers, release_plan
        plan = release_plan(record['candidate'], default_tip, software=True)
        scope = recorded_scope
        if scope is None and not previous_run:
            scope = self.package_scope(record['candidate'], systems)
            if not scope['systems']:
                if scope.get('versionControlled'):
                    return record | {'skipRelease': True, 'skipReason': 'no selected software version has advanced'}
                raise ValueError('no affected systems since the last delivery; specify systems to package explicitly')
        if numeric(self.version) <= numeric(version_at(default_tip)):
            raise ValueError('new train version must advance the current default version')
        if plan['documentBlockers']:
            raise ValueError('formal document preflight failed: ' + ' '.join(plan['documentBlockers']))
        if scope:
            plan['packages'] = select_targets(plan['packages'], ','.join(scope['systems']))
        blockers = formal_document_blockers(plan)
        if previous_run and not record.get("parallelSystems") and blockers:
            raise ValueError('formal document preflight failed: ' + ' '.join(blockers))
        if recorded_scope is None and scope:
            record['packageScope'] = {key: scope[key] for key in ('mode', 'systems', 'baseCommits')}
        if not previous_run:
            if "softwareVersions" in plan:
                record["softwareVersions"] = plan["softwareVersions"]
            record["systemTags"] = {system: software_tag(system, plan.get("softwareVersions", {}).get(system, self.version))
                                    for system in record["packageScope"]["systems"]}
            record["parallelSystems"] = True
        record['packageRunId'] = os.environ['GITHUB_RUN_ID']
        record['packageRunAttempt'] = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
        record['phase'] = 'packaging'
        self.save(record)
        # Restore the SAME candidate and rerun gates on a retry; never rebase it.
        self.ensure_candidate(record)
        return record

    def record_artifact(self, directory: Path, artifact_id: int):
        self.mutable()
        record = self.required_record()
        from tools.release_pipeline import matrix, release_config_at, select_targets
        archives = list(directory.glob('*.zip'))
        if len(archives) != 1:
            raise ValueError("one downloaded verified release ZIP is required")
        systems = package_systems(record)
        candidate = record['candidate']
        targets = select_targets(matrix(release_config_at(candidate)), systems)
        versions = {system: tag.split("/v", 1)[1] for system, tag in record.get("systemTags", {}).items()}
        receipt = delivery_receipt(archives[0], self.version, candidate, targets, per_system=bool(versions), software_versions=versions)
        remote = api(f'repos/{self.repository}/actions/artifacts/{artifact_id}')
        if remote['expired'] or remote['name'] != 'verified-release-'+record['candidate']:
            raise ValueError("uploaded Artifact identity or retention is invalid")
        if str(remote['workflow_run']['id']) != str(record['packageRunId']):
            raise ValueError("Artifact was not produced by the recorded package run")
        # Compare against a durable receipt uploaded before the canonical ZIP.
        receipts = api(f"repos/{self.repository}/actions/runs/{record['packageRunId']}/artifacts?per_page=100")['artifacts']
        proofs = [item for item in receipts if item['name'] == 'train-package-receipt-'+record['candidate'] and not item['expired']]
        if len(proofs) != 1:
            raise ValueError('pre-upload package checkpoint missing or ambiguous')
        with tempfile.TemporaryDirectory() as temporary:
            run('gh', 'run', 'download', str(record['packageRunId']), '--repo', self.repository,
                '--name', proofs[0]['name'], '--dir', temporary)
            original = json.loads((Path(temporary)/'package-receipt.json').read_text())
        if original != receipt:
            raise ValueError('downloaded publication differs from pre-upload package checkpoint')
        old = record.get('artifact') or {}
        if old.get('sha256') and old['sha256'] != receipt['sha256']:
            raise ValueError("retry may not replace already published package bytes")
        record['artifact'] = receipt | {'id': artifact_id, 'runId': record['packageRunId'], 'status': 'verified'}
        record['phase'] = 'published'
        self.save(record)
        return record

    def publish_system(self, system: str, directory: Path, proof: Path, artifact_run: str) -> dict:
        self.mutable()
        from tools.publish_release import publish
        from tools.release_pipeline import matrix, release_config_at, select_targets

        record = self.required_record()
        candidate = record["candidate"]
        if (not record.get("parallelSystems") or system not in record["systemTags"]
                or (record.get("review") or {}).get("sha") != candidate):
            raise ValueError("system publication requires the reviewed candidate and locked release scope")
        if record.get("softwareResult") == "success":
            result = record["systemResults"][system]
            if result["status"] != "published":
                raise ValueError("system missed the recorded delivery; include it in a later train")
            return result
        if artifact_run not in (str(record["packageRunId"]), *record.get("recoveryRunIds", [])):
            raise ValueError("system packages did not come from a recorded packaging run")
        if run("git", "rev-parse", "HEAD") != candidate:
            raise ValueError("system publication checkout differs from candidate")
        targets = select_targets(matrix(release_config_at(candidate)), system)
        archive = directory / f"neurobridge-v{self.version}.zip"
        tag = record["systemTags"][system]
        verified = delivery_receipt(archive, self.version, candidate, targets, per_system=True,
                                    software_versions={system: tag.split("/v", 1)[1]})
        if verified != json.loads(proof.read_bytes()):
            raise ValueError("downloaded system package differs from its pre-upload receipt")
        receipt = verified["systems"][system]
        if receipt["tag"] != tag:
            raise ValueError("verified installer receipt differs from the locked system tag")
        ref = f"refs/tags/{tag}"
        if self.ref(ref) is None:
            if run("git", "tag", "--list", tag):
                if run("git", "rev-parse", f"{ref}^{{commit}}") != candidate:
                    raise ValueError(f"local tag {tag} differs from the reviewed candidate")
            else:
                run("git", "tag", "-a", tag, candidate, "-m", f"NeuroBridge {tag}")
            run("git", "push", "origin", ref)
        if self.fetch(ref) != candidate:
            raise ValueError(f"remote tag {tag} differs from the reviewed candidate")
        return publish(directory / "systems" / system, receipt["sha256"], system=system)

    def finish_systems(self) -> dict:
        self.mutable()
        from tools.document_release import verified_software_receipt

        record = self.required_record()
        if not record.get("parallelSystems"):
            raise ValueError("parallel software release is not active")
        results = record.get("systemResults", {})
        errors = (ValueError, RuntimeError, KeyError, TypeError, OSError, subprocess.CalledProcessError)
        if record.get("softwareResult") != "success":
            with tempfile.TemporaryDirectory(prefix="neurobridge-release-results-") as temporary:
                for system, tag in record["systemTags"].items():
                    try:
                        receipt = verified_software_receipt(tag, Path(temporary) / system)
                        if receipt["sourceCommit"] != record["candidate"]:
                            raise ValueError("public release differs from the reviewed source")
                    except errors as error:
                        results[system] = {"tag": tag, "status": "failed", "reason": str(error)}
                    else:
                        results[system] = {"tag": tag, "status": "published", "sha256": receipt["archive"]["sha256"]}
            record["systemResults"] = results
            record["softwareResult"] = "success" if any(item["status"] == "published" for item in results.values()) else "failure"
            record["phase"] = "tagged-pending-merge" if record["softwareResult"] == "success" else "failed"
            if record["softwareResult"] == "success":
                record["taggedCandidate"] = record["candidate"]
            self.save(record)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as output:
                output.write(f"## Software release: {record['softwareResult']}\n\n")
                for system, result in results.items():
                    reason = f" · {result['reason']}" if "reason" in result else ""
                    output.write(f"- `{system}` · `{result['tag']}` · {result['status']}{reason}\n")
        if record["softwareResult"] != "success":
            raise ValueError("all selected systems failed to publish")
        record["finalizationBranch"] = self.names["finalization"]
        if not record.get("finalizationPR"):
            try:
                default, _ = self.default()
                self.protection(default)
                final = self.ref("refs/heads/" + self.names["finalization"])
                if final is None:
                    run("git", "push", "origin", f"{record['candidate']}:refs/heads/{self.names['finalization']}")
                else:
                    final = self.fetch("refs/heads/" + self.names["finalization"])
                    default_tip = self.fetch("refs/heads/" + default)
                    preserving_updates(record["candidate"], final, default_tip)
                record["finalizationPR"] = self.open_pr(self.names["finalization"], default, f"Finalize verified {self.version}")
                self.save(record)
            except errors as error:
                # Software is already published, but the protected finalization PR
                # is the only route to master. Persist the reason and fail the job
                # so a green run never hides a release that cannot be merged.
                record["finalizationError"] = str(error)
                self.save(record)
                raise ValueError(f"software published but the finalization PR was not created: {error}") from error
        return record

    def publish_delivery(self) -> dict:
        from tools.delivery_release import assemble, publish

        self.mutable()
        record = self.required_record()
        candidate = record["candidate"]
        tag = f"delivery/v{self.version}"
        ref = f"refs/tags/{tag}"
        with tempfile.TemporaryDirectory(prefix="neurobridge-delivery-") as temporary:
            archive = assemble(self.version, record, Path(temporary))
            if self.ref(ref) is None:
                run("git", "push", "origin", f"{candidate}:{ref}")
            return publish(tag, candidate, archive)

    def finalize(self, directory: Path):
        self.mutable()
        default, _ = self.default(); self.protection(default)
        record = self.required_record()
        artifact = record.get('artifact') or {}
        if artifact.get('status') != 'verified':
            raise ValueError("tagging requires verified package/publication evidence")
        # Revalidate downloaded bytes on every retry before tag or Release writes.
        self.record_artifact(directory, int(artifact['id']))
        record = self.required_record()
        self.delivery_tag(record)
        tags = record.get("systemTags")
        for tag in tags.values() if tags else ('v'+self.version,):
            if self.ref(f'refs/tags/{tag}') is None:
                run('git', 'tag', '-a', tag, record['candidate'], '-m', 'NeuroBridge '+tag)
                run('git', 'push', 'origin', f'refs/tags/{tag}')
        if self.delivery_tag(record, complete=True) != record['candidate']:
            raise ValueError("remote tag identity check failed")
        record['phase'] = 'tagged-pending-merge'; record['taggedCandidate'] = record['candidate']; self.save(record)
        if not record.get('download'):
            # A failed/uncertain GitHub Release is reconciled by the existing
            # destination's same-commit/same-byte checks, not sent as new source.
            record['download'] = {'status': 'uncertain'}; self.save(record)
        if record['download']['status'] != 'success':
            with tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)/'source'
                run('git', 'worktree', 'add', '--detach', str(work), record['candidate'])
                try:
                    if tags:
                        for system in tags:
                            receipt = artifact['systems'][system]
                            run(sys.executable, '-m', 'tools.publish_release',
                                '--dir', str((directory / 'systems' / system).resolve()),
                                '--expected-sha256', receipt['sha256'], '--system', system, cwd=work)
                    else:
                        run(sys.executable, '-m', 'tools.publish_release', '--dir', str(directory.resolve()),
                            '--expected-sha256', artifact['sha256'], '--systems', package_systems(record), cwd=work)
                finally:
                    run('git', 'worktree', 'remove', '--force', str(work))
            record['download'] = {'status': 'success'}; self.save(record)
        final = self.ref('refs/heads/'+self.names['finalization'])
        if final is None:
            run('git', 'push', 'origin', f"{record['candidate']}:refs/heads/{self.names['finalization']}")
        else:
            final = self.fetch('refs/heads/'+self.names['finalization'])
            default_tip = self.fetch('refs/heads/'+default)
            preserving_updates(record['candidate'], final, default_tip)
        record['finalizationPR'] = self.open_pr(self.names['finalization'], default, f'Finalize verified {self.version}')
        record['finalizationBranch'] = self.names['finalization']
        self.save(record)
        return record

    def merge(self):
        self.mutable()
        record = self.required_record()
        default, default_tip = self.default(); self.protection(default)
        if self.delivery_tag(record, complete=True) != record['candidate']:
            raise ValueError('finalization cannot merge before the source tag is verified')
        if numeric(version_at(default_tip)) > numeric(self.version):
            raise ValueError('finalization may not regress a newer default version')
        pr = self.pr(record['finalizationPR'])
        if pr['state'] != 'MERGED':
            check_pr(pr, self.names['finalization'], default, self.config['code_gates']['required_reviewers'])
            self.checks(pr['headRefOid'])
            tip = self.fetch('refs/heads/'+self.names['finalization'])
            if tip != pr['headRefOid']: raise ValueError('finalization head changed during verification')
            preserving_updates(record['candidate'], tip, default_tip)
            run('gh', 'pr', 'merge', str(pr['number']), '--repo', self.repository,
                '--merge', '--match-head-commit', tip)
        return self.verify()

    def verify(self):
        self.mutable()
        record = self.required_record()
        default, tip = self.default()
        if not record.get('finalizationPR'):
            raise ValueError('finalization requires the protected PR to merge')
        pr = self.pr(record['finalizationPR'])
        if pr['state'] != 'MERGED':
            raise ValueError('finalization requires the protected PR to merge')
        tag = self.delivery_tag(record, complete=True)
        if tag != record['candidate']:
            raise ValueError("fetched tag does not equal packaged candidate")
        run('git', 'merge-base', '--is-ancestor', tag, tip)
        merge_commit = pr['mergeCommit']['oid']
        parents = run('git', 'rev-list', '--parents', '-n', '1', merge_commit).split()
        if len(parents) != 3:
            raise ValueError('finalization PR must use a preserving merge commit')
        preserving_updates(record['candidate'], pr['headRefOid'], parents[1])
        preserving_updates(record['candidate'], merge_commit, parents[1])
        # Inspect all branch tips before any deletion or completion checkpoint.
        # Integration may contain only commits already included in publication.
        expected = {}
        for branch in (self.names['integration'], record['candidateBranch'], self.names['finalization']):
            ref = 'refs/heads/'+branch
            if self.ref(ref) is None:
                continue
            current = self.fetch(ref)
            if branch == self.names['integration']:
                # A rebased candidate need not retain the original publication
                # merge object. Fetch its protected ref before checking dev.
                publication = self.fetch('refs/heads/'+self.names['publish'])
                run('git', 'merge-base', '--is-ancestor', record['publication'], publication)
                run('git', 'merge-base', '--is-ancestor', current, record['publication'])
            elif current != (record['candidate'] if branch == record['candidateBranch'] else pr['headRefOid']):
                raise ValueError('train branch changed after publication; preserve every branch')
            expected[branch] = current
        record['phase'] = 'complete'; record['verifiedDefault'] = tip; self.save(record)
        # Keep publish_B and the durable state ref. Compare the inspected tips
        # again on the server so concurrent work cannot be deleted.
        self.delete_refs(expected)
        return record


def source_check(branch: str, base: str, head: str = 'HEAD', event: str | None = None):
    """Code-only gates on every stage; requirement versions stay at fork baseline."""
    config = tomllib.loads(CONFIG_PATH.read_text())
    current = version_at('HEAD')
    numeric(current)
    # A force push can remove the before object from the fetched history, or
    # leave it on an unrelated line. Only push events may replace that baseline;
    # a missing PR target or an explicitly supplied CLI base remains an error.
    if event == 'push' and base not in (None, '', '0'*40):
        require_sha(base)
        try:
            run('git', 'merge-base', '--is-ancestor', base, head)
        except RuntimeError:
            print(f'push baseline {base} is missing or not an ancestor; resolving default branch', file=sys.stderr)
            base = None
    if event == 'pull_request' and base in (None, '', '0'*40):
        raise ValueError('pull request requires its target baseline')
    # New branches and rewritten requirement histories use the verified remote
    # default. Never compare a rewritten default branch with itself and silently
    # approve changes whose prior baseline cannot be recovered.
    if base in (None, '', '0'*40):
        default = api(f"repos/{config['repository']['name']}")['default_branch']
        if default != config['repository']['default_branch']:
            raise ValueError('default branch configuration mismatch')
        if event == 'push' and branch == default:
            raise ValueError('default branch push requires an available ancestor baseline')
        base = Train(current).fetch('refs/heads/'+default)
        print(f'comparison baseline resolved to {base} from {default}', file=sys.stderr)
    require_sha(base)
    from tools.release_pipeline import gate
    if event == 'pull_request':
        from tools.release_version_policy import check_versions
        check_versions(run('git', 'merge-base', base, head), head)
    if branch.startswith(tuple(config['branches']['requirement_prefixes'])):
        fork = run('git', 'merge-base', base, head)
        if version_at(head) != version_at(fork):
            raise ValueError('requirements must not initialize release versions')
        return {'shouldRelease': False, 'reason': 'requirement_validation'}
    match = re.fullmatch(r'(?:dev_|publish_|finalize_)(.+)', branch)
    selected = match.group(1) if match else None
    if branch.startswith('release-candidate/'):
        parts = branch.split('/')
        if len(parts) != 3: raise ValueError('invalid candidate branch')
        selected = parts[1]
    if selected:
        numeric(selected)
        if current != selected:
            if branch.startswith('publish_'):
                train = Train(selected)
                meta = train.metadata(train.fetch('refs/heads/'+train.names['integration']))
                if run('git', 'rev-parse', 'HEAD') == meta['defaultBaseline']:
                    return {'shouldRelease': False, 'reason': 'publication_awaiting_promotion'}
            raise ValueError('branch version differs from registry')
        meta = json.loads(run('git', 'show', 'HEAD:'+config['source']['metadata']))
        if meta['version'] != current: raise ValueError('metadata version mismatch')
        train = Train(current)
        if branch.startswith(('release-candidate/', 'finalize_')):
            record = train.required_record()
            run('git', 'merge-base', '--is-ancestor', record['candidate'], 'HEAD')
            if branch.startswith('finalize_'):
                if train.delivery_tag(record, complete=True) != record['candidate']: raise ValueError('finalization tag mismatch')
                preserving_updates(record['candidate'], 'HEAD', train.default()[1])
        # dev/publish receive requirements after one version initialization.
        return gate(meta['defaultBaseline'])
    if branch.startswith('release-state/'):
        raise ValueError('state refs are not product source')
    return gate(base)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['plan', 'initialize', 'select', 'promote', 'prepare', 'review', 'begin-release', 'record-artifact', 'finalize', 'verify', 'merge', 'check', 'verify-package', 'fail', 'publish-system', 'finish-systems', 'publish-delivery'])
    p.add_argument('--version'); p.add_argument('--execute', action='store_true')
    p.add_argument('--requirements', default=''); p.add_argument('--promotion', type=int)
    p.add_argument('--candidate'); p.add_argument('--branch'); p.add_argument('--base'); p.add_argument('--head', default='HEAD')
    p.add_argument('--event', choices=['push', 'pull_request'])
    p.add_argument('--directory', type=Path); p.add_argument('--artifact-id', type=int)
    p.add_argument('--summary', type=Path, help='plan: append a Markdown preflight summary')
    p.add_argument('--systems', default='', help='plan/begin-release/verify-package: comma-separated system versions')
    p.add_argument('--system-bundles', action='store_true', help='also verify the per-system delivery ZIPs')
    p.add_argument('--proof', type=Path)
    p.add_argument('--artifact-run')
    args = p.parse_args()
    try:
        if args.operation == 'check':
            result = source_check(args.branch, args.base, args.head, args.event)
        elif args.operation == 'verify-package':
            from tools.release_pipeline import matrix, release_config_at, select_targets, software_version_at, system_version
            archives = list(args.directory.glob('*.zip'))
            if len(archives) != 1: raise ValueError('one delivery ZIP required')
            candidate = require_sha(args.candidate)
            targets = select_targets(matrix(release_config_at(candidate)), args.systems)
            versions = {system: software_version_at(candidate, system)
                        for system in {system_version(target) for target in targets}} if args.system_bundles else {}
            result = delivery_receipt(archives[0], args.version, candidate, targets, per_system=args.system_bundles,
                                      software_versions=versions)
        else:
            train = Train(args.version, args.execute)
            train.operation = args.operation
            train.requirements = [x.strip() for x in args.requirements.split(',') if x.strip()]
            if args.operation == 'plan':
                result = train.plan(args.head, args.systems)
                if args.summary is not None:
                    from tools.release_preflight import render_summary
                    with args.summary.open('a', encoding='utf-8') as summary:
                        summary.write(render_summary(result, 'publish', result['gates']['sourceVersion']))
            elif args.operation in ('initialize', 'select'):
                requirements = [x.strip() for x in args.requirements.split(',') if x.strip()]
                result = getattr(train, args.operation)(requirements)
            elif args.operation == 'prepare': result = train.prepare(args.promotion)
            elif args.operation == 'review': result = train.review(args.candidate)
            elif args.operation == 'begin-release': result = train.begin_release(args.systems)
            elif args.operation == 'record-artifact': result = train.record_artifact(args.directory, args.artifact_id)
            elif args.operation == 'finalize': result = train.finalize(args.directory)
            elif args.operation == 'publish-system':
                result = train.publish_system(args.systems, args.directory, args.proof, args.artifact_run)
            elif args.operation == 'finish-systems': result = train.finish_systems()
            elif args.operation == 'fail': result = train.fail()
            else: result = getattr(train, args.operation.replace('-', '_'))()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        output = os.environ.get('GITHUB_OUTPUT')
        if output and isinstance(result, dict) and 'candidate' in result:
            with open(output, 'a') as target:
                target.write('candidate='+result['candidate']+'\n')
                target.write('candidate_branch='+result['candidateBranch']+'\n')
                target.write('should_package='+str(not bool(result.get('artifact'))).lower()+'\n')
                target.write('skip_release='+str(bool(result.get('skipRelease'))).lower()+'\n')
                target.write('artifact_id='+str((result.get('artifact') or {}).get('id', ''))+'\n')
                target.write('package_run_id='+str(result.get('packageRunId', ''))+'\n')
                target.write('systems='+package_systems(result)+'\n')
                target.write('system_releases='+str(bool(result.get("systemTags"))).lower()+'\n')
                target.write('parallel_systems='+str(bool(result.get("parallelSystems"))).lower()+'\n')
                target.write('system_matrix='+json.dumps(sorted(result.get("systemTags", {})))+'\n')
                target.write('recovery_runs='+','.join(result.get("recoveryRunIds", []))+'\n')
                target.write('software_result='+str(result.get("softwareResult", ""))+'\n')
                target.write('complete='+str(result.get('phase') == 'complete').lower()+'\n')
                target.write('finalization_branch='+str(result.get('finalizationBranch', ''))+'\n')
    except (ValueError, RuntimeError, KeyError, TypeError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f'source train blocked: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
