#!/usr/bin/env python3
"""Source release orchestration. Packaging is delegated to release.yml.

No command writes without --execute. Remote writes use the configured App in
Actions. Checkpoints use fast-forward-only state refs whose parents retain the
candidate Git objects after a failed untagged candidate branch is removed.
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
    result = subprocess.run(args, cwd=cwd, input=data, env=env, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


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


def artifact_receipt(archive: Path, version: str, candidate: str, matrix: list[dict]) -> dict:
    numeric(version)
    require_sha(candidate)
    from tools.publish_release import load_bundle_manifest, verify_nested_archives, bundle_packages
    from tools.release_pipeline import sha256, MAGIC
    loaded = load_bundle_manifest(archive.parent)
    if loaded is None or loaded[0].resolve() != archive.resolve():
        raise ValueError("one canonical delivery ZIP is required")
    _, manifest = loaded
    if manifest['applicationVersion'] != version or manifest['git']['commit'] != candidate or manifest['git']['dirty']:
        raise ValueError("package version or exact source commit does not match candidate")
    if manifest['releaseStatus'] != 'candidate' or manifest['trigger'] != 'push_master':
        raise ValueError('train package must carry candidate provenance')
    platforms = {item['platform'] for item in matrix}
    results = [item for platform in platforms for item in manifest['coverage'][platform]['targetResults']]
    expected = {item['id']: item for item in matrix}
    actual = {item['targetId']: item for item in results}
    if len(actual) != len(results) or set(actual) != set(expected):
        raise ValueError("package evidence must cover every defined target exactly once")
    if any(item.get('status') != 'candidate' for item in results):
        raise ValueError("every defined package must exist; failed, blocked or skipped targets forbid publication")
    verify_nested_archives(archive, manifest)
    # Verify real installer headers inside the delivery ZIP, not a green job or source ZIP.
    with zipfile.ZipFile(archive) as outer:
        bundle = json.loads(outer.read('metadata/bundle-manifest.json'))
        build = json.loads(outer.read('metadata/build-manifest.json'))
        logs = [json.loads(line) for line in outer.read('metadata/release-logs.jsonl').decode().splitlines()]
        if build['sourceCommit'] != candidate or build['applicationVersion'] != version:
            raise ValueError('build provenance does not match candidate')
        if len(logs) != len(expected) or {item['target']['id'] for item in logs} != set(expected):
            raise ValueError('complete per-package source evidence required')
        for item in logs:
            if item['sourceCommit'] != candidate or item['status'] != 'candidate':
                raise ValueError('installer source evidence differs from candidate')
        package_records = [{'targetId': item['target']['id'], 'fileName': item['fileName'], 'sha256': item['sha256']} for item in logs]
        if sorted((item['fileName'], item['sha256']) for item in package_records) != sorted((item[3], item[5]) for item in bundle_packages(bundle)):
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

    def default(self) -> tuple[str, str]:
        if run('git', 'rev-parse', '--is-shallow-repository') != 'false':
            raise ValueError('full history is required for source finalization')
        meta = api(f'repos/{self.repository}')
        branch = meta['default_branch']
        if branch != self.config['repository']['default_branch']:
            raise ValueError("configured default branch differs from repository metadata")
        if not meta['allow_merge_commit']:
            raise ValueError("default branch must permit a merge commit preserving the candidate")
        if meta.get('delete_branch_on_merge'):
            raise ValueError('automatic PR branch deletion would violate deferred train cleanup')
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
        if os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('TRAIN_APP_TOKEN_USED') != 'true':
            raise ValueError("automated source writes require the repository GitHub App")
        origin = run('git', 'remote', 'get-url', 'origin')
        origin = origin.replace('git@github.com:', 'https://github.com/', 1)
        parsed = urlsplit(origin)
        if parsed.hostname != 'github.com' or parsed.path.strip('/').removesuffix('.git').casefold() != self.repository.casefold():
            raise ValueError('origin differs from the configured GitHub repository')

    def protection(self, branch: str):
        # Classic protection and Rulesets may coexist. Query effective rules too;
        # callers must never silently install or weaken protection.
        rules = api(f'repos/{self.repository}/rules/branches/{branch}')
        pull = [r['parameters'] for r in rules if r['type'] == 'pull_request']
        if any(p.get('allowed_merge_methods') and 'merge' not in p['allowed_merge_methods'] for p in pull):
            raise ValueError('branch protection permits only rewriting merge methods')
        if pull and all(p.get('required_approving_review_count', 0) >= self.config['code_gates']['required_reviewers'] for p in pull):
            if any(r['type'] == 'required_linear_history' for r in rules):
                raise ValueError("linear-only protection cannot preserve a tagged candidate by merge commit")
            return
        try:
            policy = api(f'repos/{self.repository}/branches/{branch}/protection')
        except RuntimeError as error:
            raise ValueError(f"reviewed PR protection must be configured for {branch}") from error
        reviews = policy.get('required_pull_request_reviews') or {}
        if reviews.get('required_approving_review_count', 0) < self.config['code_gates']['required_reviewers']:
            raise ValueError(f"{branch} requires at least one approving review before enabling the train")
        if not policy.get('enforce_admins', {}).get('enabled'):
            raise ValueError('branch review protection must also apply to administrators')
        if policy.get('required_linear_history', {}).get('enabled'):
            raise ValueError("linear-only protection cannot preserve candidate ancestry")

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
        if self.tag() is None:
            branch = record['candidateBranch']
            existing = self.ref('refs/heads/'+branch)
            if existing is not None and existing != record['candidate']:
                raise ValueError('candidate branch changed; preserve it for reconciliation')
            record['phase'] = 'failed'; self.save(record)
            self.delete_refs({branch: existing} if existing else {})
        return record

    def checks(self, sha: str):
        pages = json.loads(run('gh', 'api', '--paginate', '--slurp', f'repos/{self.repository}/commits/{sha}/check-runs?per_page=100'))
        check_results([check for page in pages for check in page['check_runs']], self.config['code_gates']['checks'])

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
        expected = os.environ.get('TRAIN_APP_LOGIN') or run('gh', 'api', 'user', '--jq', '.login')
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
        tag = self.tag()
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
        tag = self.tag()
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

    def begin_release(self) -> dict:
        self.mutable()
        default, _ = self.default(); self.protection(default)
        record = self.required_record()
        if record.get('finalizationPR') and self.tag() and self.pr(record['finalizationPR'])['state'] == 'MERGED':
            return self.verify()
        if not record.get('review') or record['review']['sha'] != record['candidate']:
            raise ValueError("exact post-rebase candidate requires human source review")
        self.checks(record['candidate'])
        if record.get('artifact'):
            return record
        # Reconcile an interrupted Artifact upload instead of rebuilding blindly.
        previous_run = record.get('packageRunId')
        if previous_run:
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
        if self.tag():
            raise ValueError("tagged candidate is missing its publication receipt; do not rebuild")
        default_tip = self.fetch('refs/heads/'+default)
        if numeric(self.version) <= numeric(version_at(default_tip)):
            raise ValueError('new package version must advance the current default version')
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
        from tools.release_pipeline import matrix
        archives = list(directory.glob('*.zip'))
        if len(archives) != 1:
            raise ValueError("one downloaded verified release ZIP is required")
        receipt = artifact_receipt(archives[0], self.version, record['candidate'], matrix())
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
        tag = self.tag()
        if tag is None:
            # App token emits normal tag/PR events. No force push or retagging.
            run('git', 'tag', '-a', 'v'+self.version, record['candidate'], '-m', 'NeuroBridge '+self.version)
            run('git', 'push', 'origin', 'refs/tags/v'+self.version)
        if self.tag() != record['candidate']:
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
                    run(sys.executable, '-m', 'tools.publish_release', '--dir', str(directory.resolve()),
                        '--expected-sha256', artifact['sha256'], cwd=work)
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
        self.save(record)
        return record

    def merge(self):
        self.mutable()
        record = self.required_record()
        default, default_tip = self.default(); self.protection(default)
        if self.tag() != record['candidate']:
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
        tag = self.tag()
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


def source_check(branch: str, base: str, head: str = 'HEAD'):
    """Code-only gates on every stage; requirement versions stay at fork baseline."""
    config = tomllib.loads(CONFIG_PATH.read_text())
    current = version_at('HEAD')
    numeric(current)
    # New branch pushes have an all-zero before SHA. Resolve the real default
    # for fork comparison instead of failing the first dev/candidate checks.
    if base in (None, '', '0'*40):
        default = api(f"repos/{config['repository']['name']}")['default_branch']
        if default != config['repository']['default_branch']:
            raise ValueError('default branch configuration mismatch')
        base = Train(current).fetch('refs/heads/'+default)
    require_sha(base)
    from tools.release_pipeline import gate
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
                if train.tag() != record['candidate']: raise ValueError('finalization tag mismatch')
                preserving_updates(record['candidate'], 'HEAD', train.default()[1])
        # dev/publish receive requirements after one version initialization.
        return gate(meta['defaultBaseline'])
    if branch.startswith('release-state/'):
        raise ValueError('state refs are not product source')
    return gate(base)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['plan', 'initialize', 'select', 'promote', 'prepare', 'review', 'begin-release', 'record-artifact', 'finalize', 'verify', 'merge', 'check', 'verify-package', 'fail'])
    p.add_argument('--version'); p.add_argument('--execute', action='store_true')
    p.add_argument('--requirements', default=''); p.add_argument('--promotion', type=int)
    p.add_argument('--candidate'); p.add_argument('--branch'); p.add_argument('--base'); p.add_argument('--head', default='HEAD')
    p.add_argument('--directory', type=Path); p.add_argument('--artifact-id', type=int)
    args = p.parse_args()
    try:
        if args.operation == 'check':
            result = source_check(args.branch, args.base, args.head)
        elif args.operation == 'verify-package':
            from tools.release_pipeline import matrix
            archives = list(args.directory.glob('*.zip'))
            if len(archives) != 1: raise ValueError('one delivery ZIP required')
            result = artifact_receipt(archives[0], args.version, args.candidate, matrix())
        else:
            train = Train(args.version, args.execute)
            train.operation = args.operation
            train.requirements = [x.strip() for x in args.requirements.split(',') if x.strip()]
            if args.operation == 'plan':
                result = {'repository': train.repository, 'version': train.version, 'branches': train.names,
                          'default': train.default()[0],
                          'publishPolicy': train.config['source_release'], 'writes': False}
                from tools.release_pipeline import matrix
                result['packages'] = matrix()
            elif args.operation in ('initialize', 'select'):
                requirements = [x.strip() for x in args.requirements.split(',') if x.strip()]
                result = getattr(train, args.operation)(requirements)
            elif args.operation == 'prepare': result = train.prepare(args.promotion)
            elif args.operation == 'review': result = train.review(args.candidate)
            elif args.operation == 'begin-release': result = train.begin_release()
            elif args.operation == 'record-artifact': result = train.record_artifact(args.directory, args.artifact_id)
            elif args.operation == 'finalize': result = train.finalize(args.directory)
            elif args.operation == 'fail': result = train.fail()
            else: result = getattr(train, args.operation)()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        output = os.environ.get('GITHUB_OUTPUT')
        if output and isinstance(result, dict) and 'candidate' in result:
            with open(output, 'a') as target:
                target.write('candidate='+result['candidate']+'\n')
                target.write('candidate_branch='+result['candidateBranch']+'\n')
                target.write('should_package='+str(not bool(result.get('artifact'))).lower()+'\n')
                target.write('artifact_id='+str((result.get('artifact') or {}).get('id', ''))+'\n')
                target.write('package_run_id='+str(result.get('packageRunId', ''))+'\n')
                target.write('complete='+str(result.get('phase') == 'complete').lower()+'\n')
    except (ValueError, RuntimeError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(f'source train blocked: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
