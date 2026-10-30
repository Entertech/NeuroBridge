"""Read-only checks spanning source-train config and reusable workflows.

actionlint handles workflow syntax. These checks cover repository contracts and
caller/callee permissions that linting an individual YAML file cannot establish.
"""
from __future__ import annotations

import copy
from fnmatch import fnmatchcase
from pathlib import Path
import re
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[1]


class WorkflowLoader(yaml.SafeLoader):
    pass


# GitHub uses the string key "on". PyYAML's YAML 1.1 booleans would turn it
# into True. Keep actual true/false values, without changing SafeLoader globally.
WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for initial, resolvers in WorkflowLoader.yaml_implicit_resolvers.items():
    WorkflowLoader.yaml_implicit_resolvers[initial] = [
        item for item in resolvers if item[0] != 'tag:yaml.org,2002:bool'
    ]
WorkflowLoader.add_implicit_resolver('tag:yaml.org,2002:bool', re.compile(r'^(?:true|false)$', re.I), list('tTfF'))


def load_workflows(root: Path = ROOT) -> dict:
    return {path.name: yaml.load(path.read_text(encoding='utf-8'), Loader=WorkflowLoader)
            for path in (root/'.github/workflows').glob('*.yml')}


def events(workflow: dict) -> dict:
    value = workflow['on']
    if isinstance(value, str):
        return {value: None}
    if isinstance(value, list):
        return dict.fromkeys(value)
    return value


def permissions(value, inherited: dict | None = None) -> dict:
    if value is None:
        return dict(inherited or {})
    if isinstance(value, str):
        return {'*': value.removesuffix('-all')}
    return value


def validate_permissions(workflows: dict):
    rank = {'none': 0, 'read': 1, 'write': 2}
    for name, caller in workflows.items():
        for job_name, job in caller['jobs'].items():
            reference = job.get('uses', '')
            if not reference.startswith('./.github/workflows/'):
                continue
            callee = workflows[Path(reference).name]
            granted = permissions(job.get('permissions'), permissions(caller.get('permissions')))
            requested = permissions(callee.get('permissions'), granted)
            for callee_job in callee['jobs'].values():
                effective = permissions(callee_job.get('permissions'), requested)
                for scope in effective.keys() | granted.keys():
                    need = rank[effective.get(scope, effective.get('*', 'none'))]
                    have = rank[granted.get(scope, granted.get('*', 'none'))]
                    if need > have:
                        raise ValueError(f'{name}:{job_name} cannot grant {scope} to {reference}')


def matches(branch: str, patterns: list[str]) -> bool:
    included = False
    for pattern in patterns:
        if pattern.startswith('!'):
            if fnmatchcase(branch, pattern[1:]):
                included = False
        elif fnmatchcase(branch, pattern):
            included = True
    return included


def validate(config: dict, workflows: dict):
    validate_permissions(workflows)
    ci = workflows['ci.yml']
    defined = {job.get('name', key): job for key, job in ci['jobs'].items()}
    for check in config['code_gates']['checks']:
        if check not in defined:
            raise ValueError(f'required code check has no CI job: {check}')
        if 'if' in defined[check]:
            # The version gate may skip manual Test runs, but must run on every
            # source push/PR. Other required jobs have no conditional skip.
            permitted = "github.event_name == 'pull_request' || github.event_name == 'push'"
            if check != 'Application version gate' or defined[check]['if'] != permitted:
                raise ValueError(f'required code check can be skipped: {check}')
    branches = config['branches']
    examples = [config['repository']['default_branch']]
    examples += [prefix+'contract-test' for prefix in branches['requirement_prefixes']]
    examples += [branches[key].format(version='1.2.3', attempt='contract-test') for key in (
        'integration_template', 'publish_template', 'release_candidate_template', 'finalization_template')]
    for event in ('pull_request', 'push'):
        trigger = events(ci).get(event)
        if trigger is None:
            raise ValueError(f'code validation requires explicit {event} branch coverage')
        if 'paths' in trigger or 'paths-ignore' in trigger:
            raise ValueError('path filtering can skip required source checks')
        for branch in examples:
            if not matches(branch, trigger.get('branches', ['**'])) or matches(branch, trigger.get('branches-ignore', [])):
                raise ValueError(f'{event} skips source stage: {branch}')
    for name, allowed in (('release.yml', {'workflow_call', 'workflow_dispatch'}),
                          ('kylin-bootstrap.yml', {'workflow_dispatch'})):
        if set(events(workflows[name])) != allowed:
            raise ValueError(f'{name} must not publish from ordinary push/PR/Test events')
    if any(job.get('uses', '').endswith('/release.yml') for job in ci['jobs'].values()):
        raise ValueError('ordinary code CI must not call packaging')


def main():
    config = tomllib.loads((ROOT/'.release/code-release-train.toml').read_text(encoding='utf-8'))
    validate(config, load_workflows())
    print('source-train workflow contracts passed')


if __name__ == '__main__':
    main()
