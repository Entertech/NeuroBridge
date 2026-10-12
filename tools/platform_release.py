#!/usr/bin/env python3
"""Build, verify, publish or reuse immutable releases of a single platform."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from tools import publish_release as publication
from tools.release_pipeline import (MAGIC, ROOT, add_zip_entry, log, matrix,
                                    platform_versions, require_digest, run,
                                    save_json, sha256, version_tuple)

CHECKPOINT = "<!-- neurobridge-platform-archive\n"
MANIFEST = "platform-release-manifest.json"


def validate_archive(archive: Path, platform: str, version: str) -> dict:
    """Validate all identities, file paths and bytes before publishing/extracting."""
    version_tuple(version)
    targets = {t['id']: t for t in matrix() if t['platform'] == platform}
    if not targets:
        raise ValueError('unknown platform')
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or len(bundle.namelist()) != len(set(bundle.namelist())):
            raise ValueError('platform ZIP CRC or duplicate members')
        manifest = json.loads(bundle.read(MANIFEST))
        if (manifest.get('schemaVersion') != 1 or manifest.get('platform') != platform
                or manifest.get('version') != version
                or not re.fullmatch(r'[0-9a-f]{40,64}', manifest.get('sourceCommit', ''))):
            raise ValueError('platform archive identity mismatch')
        results = manifest['results']
        if len(results) != len(targets) or {r['target']['id'] for r in results} != set(targets):
            raise ValueError('platform release requires every installer format')
        expected = {MANIFEST}
        for result in results:
            target = targets[result['target']['id']]
            if (result['target'] != target or result.get('platformVersion') != version
                    or result.get('sourceCommit') != manifest['sourceCommit']
                    or result.get('status') != 'candidate'):
                raise ValueError('platform result provenance mismatch')
            for field in ('fileName', 'validationLog'):
                name = result[field]
                if Path(name).name != name or '\\' in name or name in {'result.json', MANIFEST}:
                    raise ValueError('unsafe platform archive path')
                expected.add(f"{target['id']}/{name}")
            expected.add(f"{target['id']}/result.json")
            if json.loads(bundle.read(f"{target['id']}/result.json")) != result:
                raise ValueError('archived result differs from manifest')
            data = bundle.read(f"{target['id']}/{result['fileName']}")
            if not data.startswith(MAGIC[target['format']]) or publication.sha256_bytes(data) != result['sha256']:
                raise ValueError('platform package format or digest mismatch')
            if not all(fragment in result['fileName'] for fragment in (platform, version, target['architecture'])):
                raise ValueError('platform package filename identity mismatch')
            if not bundle.read(f"{target['id']}/{result['validationLog']}"):
                raise ValueError('platform validation log missing')
            for field in ('runtimeSha256', 'inputSha256', 'reportSha256'):
                require_digest(result.get(field), field)
            if not result.get('toolchain') or not result.get('sourceReferences'):
                raise ValueError('platform input provenance missing')
            if datetime.fromisoformat(result['builtAt'].replace('Z', '+00:00')).tzinfo is None:
                raise ValueError('platform build timestamp requires timezone')
        if set(bundle.namelist()) != expected:
            raise ValueError('unexpected platform archive members')
    return manifest


def build_archive(results_root: Path, output: Path, platform: str) -> Path:
    version = platform_versions()[platform]
    commit = run('git', 'rev-parse', 'HEAD')
    results = [json.loads((results_root / t['id'] / 'result.json').read_text()) for t in matrix() if t['platform'] == platform]
    if any(r.get('status') != 'candidate' or r.get('sourceCommit') != commit or r.get('platformVersion') != version for r in results):
        raise ValueError(f'{platform} release requires all verified installers from this commit')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'{platform}-v{version}.zip'
    manifest = {'schemaVersion': 1, 'platform': platform, 'version': version, 'sourceCommit': commit, 'physicalVerification': 'pending', 'results': results}
    manifest_path = output / MANIFEST
    save_json(manifest_path, manifest)
    stamp = max(datetime.fromisoformat(r['builtAt'].replace('Z', '+00:00')) for r in results).astimezone(timezone.utc).timetuple()[:6]
    temporary = archive.with_suffix('.zip.new')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as bundle:
        add_zip_entry(bundle, manifest_path, MANIFEST, stamp)
        for result in results:
            target_id = result['target']['id']
            for name in ('result.json', result['fileName'], result['validationLog']):
                add_zip_entry(bundle, results_root / target_id / name, f'{target_id}/{name}', stamp)
    validate_archive(temporary, platform, version)
    temporary.replace(archive)
    manifest_path.unlink()
    return archive


def fetch_archive(platform: str, output: Path, results_root: Path, *, retry: bool = False) -> Path:
    version = platform_versions()[platform]
    tag = f'{platform}-v{version}'
    info = publication.release_info(tag)
    if info is None or (info['isDraft'] and not retry):
        raise ValueError(f'no published platform release available: {tag}')
    name = tag + '.zip'
    if {a['name'] for a in info['assets']} != {name}:
        raise ValueError('platform release asset list mismatch')
    body = info.get('body') or ''
    if body.count(CHECKPOINT) != 1 or '\n-->' not in body.split(CHECKPOINT)[1]:
        raise ValueError('platform release lacks a verified checkpoint')
    checkpoint = json.loads(body.split(CHECKPOINT)[1].split('\n-->')[0])
    if checkpoint.get('platform') != platform or checkpoint.get('version') != version or checkpoint.get('fileName') != name:
        raise ValueError('platform release checkpoint identity mismatch')
    require_digest(checkpoint.get('sha256'), 'published platform archive digest')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='neurobridge-platform-fetch-') as temporary:
        publication.command('gh', 'release', 'download', tag, '--pattern', name, '--dir', temporary)
        archive = Path(temporary) / name
        if sha256(archive) != checkpoint['sha256']:
            raise ValueError('downloaded platform archive digest mismatch')
        manifest = validate_archive(archive, platform, version)
        publication.command('git', 'fetch', 'origin', f'refs/tags/{tag}:refs/tags/{tag}')
        if publication.command('git', 'rev-list', '-n', '1', tag) != manifest['sourceCommit'] or checkpoint.get('sourceCommit') != manifest['sourceCommit']:
            raise ValueError('platform release tag/commit mismatch')
        if retry and manifest['sourceCommit'] != run('git', 'rev-parse', 'HEAD'):
            raise ValueError('existing platform release belongs to another commit; advance the platform version')
        with zipfile.ZipFile(archive) as bundle:
            for result in manifest['results']:
                target_id = result['target']['id']
                folder = results_root / target_id
                folder.mkdir(parents=True, exist_ok=True)
                for name_in_result in (result['fileName'], result['validationLog']):
                    (folder / name_in_result).write_bytes(bundle.read(f'{target_id}/{name_in_result}'))
                restored = result if retry else {**result, 'reusedRelease': {'tag': tag, 'archiveSha256': checkpoint['sha256'], 'sourceCommit': manifest['sourceCommit']}}
                save_json(folder / 'result.json', restored)
        shutil.copy2(archive, output / name)
    return output / name


def publish_platform(archive: Path, platform: str, expected_sha256: str) -> dict:
    require_digest(expected_sha256, 'pre-upload platform archive digest')
    if sha256(archive) != expected_sha256:
        raise ValueError('downloaded Actions Artifact differs from platform ZIP verified before upload')
    version = platform_versions()[platform]
    manifest = validate_archive(archive, platform, version)
    commit = publication.command('git', 'rev-parse', 'HEAD')
    if manifest['sourceCommit'] != commit or publication.command('git', 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('platform publication requires its clean source checkout')
    tag = f'{platform}-v{version}'
    if archive.name != tag + '.zip':
        raise ValueError('platform archive filename mismatch')
    checkpoint = {'platform': platform, 'version': version, 'sourceCommit': commit, 'fileName': archive.name, 'sha256': expected_sha256}
    notes = archive.parent / 'platform-release-notes.md'
    notes.write_text(f'NeuroBridge {tag}\n\nSource commit: `{commit}`. Unsigned; physical verification pending.\n\n' + CHECKPOINT + json.dumps(checkpoint, sort_keys=True) + '\n-->\n', encoding='utf-8')
    publication.ensure_tag(tag, commit)
    info = publication.ensure_release(tag, notes)
    names = {a['name'] for a in info.get('assets', [])}
    if names - {archive.name}:
        raise ValueError('platform Release has unexpected assets')
    publication.verify_asset(tag, archive, archive.name in names)
    if info['isDraft']:
        publication.command('gh', 'release', 'edit', tag, '--draft=false', '--latest=false')
    final = publication.release_info(tag)
    if final is None or final['isDraft'] or {a['name'] for a in final['assets']} != {archive.name}:
        raise ValueError('platform publication could not be verified')
    receipt = {**checkpoint, 'status': 'published', 'githubReleaseUrl': final['url']}
    save_json(archive.parent / 'platform-release-receipt.json', receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('build', 'fetch', 'recover', 'publish'))
    parser.add_argument('--platform', choices=('windows', 'kylin'), required=True)
    parser.add_argument('--results-root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    try:
        if args.action == 'build':
            archive = build_archive(args.results_root, args.output, args.platform)
            print(json.dumps({'fileName': archive.name, 'sha256': sha256(archive)}))
        elif args.action == 'recover':
            tag = f'{args.platform}-v{platform_versions()[args.platform]}'
            info = publication.release_info(tag)
            if info is not None and info.get('assets'):
                fetch_archive(args.platform, args.output, args.results_root, retry=True)
        elif args.action == 'fetch':
            fetch_archive(args.platform, args.output, args.results_root)
        else:
            publish_platform(args.archive, args.platform, args.expected_sha256)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        log('platform_release_failed', platform=args.platform, reason=str(error))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
