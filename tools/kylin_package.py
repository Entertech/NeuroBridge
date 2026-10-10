"""Verify the actual bootstrap DEB payload against its selected locked inputs."""
import hashlib
import json
from pathlib import PurePosixPath
import subprocess
import tarfile
import tempfile

from tools.kylin_inputs import LOCK, catalog, offline_manifest

PAYLOAD = 'usr/lib/neurobridge-bootstrap/'
REQUIRED = {'platform.sh', 'resources.sh', 'install-bootstrap.sh', 'run-logged.sh',
            'bootstrap-build.sh', 'bootstrap-install.sh', 'kylin-bootstrap-inputs.toml',
            'offline-resources.json', 'source/linux/setup-kylin-python.sh',
            'source/linux/setup-kylin-algorithm.sh', 'source/packaging/kylin/python-launcher.sh',
            'source/mac/algorithm_bridge/CMakeLists.txt',
            'source/mac/algorithm_bridge/affective_bridge.cpp',
            'source/third_party/NumCpp/CMakeLists.txt',
            'source/third_party/AffectiveCloud-Algorithm-SDK/cpp/package/CMakeLists.txt'}
RESOURCE_DIRS = ('source/python-runtime/', 'source/wheelhouse/', 'source/packaging/kylin/offline/')


def inspect_payload(archive, offline_resources, data, lock_bytes):
    manifest = offline_manifest(data, offline_resources)
    expected = {item['path']: item['sha256'] for item in manifest['artifacts']}
    present = set()
    hashes = {}
    total_bytes = 0
    for member in archive:
        name = PurePosixPath(member.name).as_posix()
        if '..' in PurePosixPath(name).parts:
            raise ValueError('Unsafe package member path')
        if not name.startswith(PAYLOAD) or member.isdir():
            continue
        relative = name[len(PAYLOAD):]
        if relative in present:
            raise ValueError(f'Duplicate payload member: {relative}')
        present.add(relative)
        if relative in REQUIRED and not member.isfile():
            raise ValueError(f'Required payload must be a regular file: {relative}')
        if relative.startswith(RESOURCE_DIRS) and relative not in expected:
            raise ValueError(f'Unselected or unexpected offline resource in package: {relative}')
        if relative not in expected and relative not in ('offline-resources.json', 'kylin-bootstrap-inputs.toml'):
            continue
        if not member.isfile():
            raise ValueError(f'Input must be a regular file: {relative}')
        stream = archive.extractfile(member)
        if relative == 'offline-resources.json':
            if member.size > 65536 or json.load(stream) != manifest:
                raise ValueError('Offline resource manifest does not match build selection')
        elif relative == 'kylin-bootstrap-inputs.toml':
            if member.size != len(lock_bytes) or stream.read() != lock_bytes:
                raise ValueError('Package input lock differs from source lock')
        else:
            value = hashlib.sha256()
            while block := stream.read(1024 * 1024):
                value.update(block)
            hashes[relative] = value.hexdigest()
            if hashes[relative] != expected[relative]:
                raise ValueError(f'Package input checksum mismatch: {relative}')
            total_bytes += member.size
    missing = (REQUIRED | set(expected)) - present
    if missing:
        raise ValueError('Package payload missing: ' + ', '.join(sorted(missing)))
    # Include the selection and full lock even when no resource bytes ship.
    value = hashlib.sha256(lock_bytes)
    value.update(json.dumps(manifest, sort_keys=True).encode())
    for path, digest in sorted(hashes.items()):
        value.update(path.encode())
        value.update(bytes.fromhex(digest))
    return {'resources': manifest['resources'], 'artifactCount': len(expected),
            'resourceBytes': total_bytes, 'inputSha256': value.hexdigest()}


def inspect_package(package, offline_resources='all'):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(package)],
                                   stdout=subprocess.PIPE, stderr=errors)
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                result = inspect_payload(archive, offline_resources, catalog(), LOCK.read_bytes())
            process.stdout.close()
            if process.wait() != 0:
                raise ValueError('dpkg-deb could not read package payload')
            return result
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()
