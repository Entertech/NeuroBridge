"""Build and verify the Windows x64 algorithm using checksum-locked portable tools."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from neurobridge.algorithm_setup import smoke_test_bridge
from windows.gateway_helper import config_path, validate_config, instance_lock


def digest(path: Path) -> str:
    with path.open('rb') as source:
        value = sha256()
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def source_fingerprint(root: Path) -> str:
    value = sha256()
    for name in ('sdk.lock', 'windows/algorithm_build.py', 'mac/algorithm_bridge',
                 'third_party/AffectiveCloud-Algorithm-SDK', 'third_party/NumCpp'):
        path = root / name
        files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        for file in files:
            if '__pycache__' in file.parts or file.suffix == '.pyc':
                continue
            value.update(file.relative_to(root).as_posix().encode())
            value.update(bytes.fromhex(digest(file)))
    return value.hexdigest()


def obtain_archive(package: dict, cache: Path, offline: bool) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / package['filename']
    if path.exists():
        if digest(path) != package['sha256']:
            raise ValueError(f'SHA-256 mismatch: {path}; replace this archive before retrying')
        return path
    if offline:
        raise ValueError(f'Offline build input missing: {path}; no download attempted')
    print(f'Downloading {package["filename"]} ...', flush=True)
    partial = path.with_suffix('.download')
    try:
        for attempt in range(3):
            try:
                request = urllib.request.Request(package['url'], headers={'User-Agent': 'NeuroBridge-build'})
                with urllib.request.urlopen(request, timeout=60) as response, partial.open('wb') as output:
                    shutil.copyfileobj(response, output, 1024 * 1024)
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
        if digest(partial) != package['sha256']:
            raise ValueError(f'Download SHA-256 mismatch: {package["filename"]}')
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def extract_archive(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            name = PureWindowsPath(item.filename)
            if (name.is_absolute() or name.root or name.drive or '..' in name.parts or
                    ':' in item.filename or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError(f'Unsafe ZIP member: {item.filename}')
        bundle.extractall(destination)


def validate_pe(path: Path) -> None:
    with path.open('rb') as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise ValueError('Algorithm output is not a Windows executable')
        source.seek(struct.unpack_from('<I', header, 60)[0])
        signature = source.read(6)
        if signature != b'PE\0\0\x64\x86':
            raise ValueError('Algorithm output must be Windows AMD64 PE')


def validate_imports(output: str) -> list[str]:
    imports = sorted(set(re.findall(r'^\s*Name: (\S+\.dll)\s*$', output, re.I | re.M)))
    if not imports:
        raise ValueError('No DLL import information found in Windows executable')
    allowed = {'kernel32.dll', 'ntdll.dll', 'user32.dll', 'advapi32.dll', 'shell32.dll',
               'ole32.dll', 'ws2_32.dll', 'bcrypt.dll', 'ucrtbase.dll', 'msvcrt.dll'}
    unexpected = [name for name in imports if name.lower() not in allowed and
                  not name.lower().startswith(('api-ms-win-', 'ext-ms-win-'))]
    if unexpected:
        raise ValueError(f'Non-system runtime DLL dependencies: {unexpected}')
    return imports


def smoke(path: Path) -> dict:
    validate_pe(path)
    # os.environ is case-insensitive on Windows, but a plain dict is not.
    # Normalize once so SYSTEMROOT/SystemRoot and PATH/Path behave identically.
    env = {key.upper(): value for key, value in os.environ.items()}
    system_root = env.get('SYSTEMROOT')
    if not system_root:
        raise ValueError('Windows SYSTEMROOT environment variable is missing; cannot isolate the algorithm DLL search path')
    system = Path(system_root)
    # A successful test must not depend on the compiler's DLL search path.
    env['PATH'] = os.pathsep.join(map(str, (system / 'System32', system)))
    return smoke_test_bridge(path, timeout_seconds=15, environment=env)


def install_verified(candidate: Path, manifest: dict, destination: Path) -> None:
    """Do not replace the working program until the candidate has passed all gates."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = destination.with_suffix('.manifest.json')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = destination.parent / 'backups' / stamp
    existing = [path for path in (destination, manifest_path) if path.exists()]
    if existing:
        backup.mkdir(parents=True)
        for path in existing:
            shutil.copy2(path, backup / path.name)
    staged = destination.with_suffix('.new.exe')
    staged_manifest = manifest_path.with_suffix('.new.json')
    try:
        shutil.copy2(candidate, staged)
        staged_manifest.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        os.replace(staged, destination)
        try:
            os.replace(staged_manifest, manifest_path)
        except OSError:
            if destination in existing:
                os.replace(backup / destination.name, destination)
            else:
                destination.unlink(missing_ok=True)
            raise
    finally:
        staged.unlink(missing_ok=True)
        staged_manifest.unlink(missing_ok=True)


def build(root: Path, *, offline: bool = False, force: bool = False) -> Path:
    config = validate_config(config_path(root))
    destination = root / '.runtime/algorithm/neurobridge_affective_bridge.exe'
    if config.algorithm.command != (str(destination),):
        # Preserve an explicitly configured external bridge. Never overwrite it.
        if len(config.algorithm.command) != 1:
            raise ValueError('Expected one executable in algorithm.command')
        external = Path(config.algorithm.command[0])
        smoke(external)
        print(f'Configured external Windows bridge verified; configuration preserved: {external}')
        return external
    inputs = source_fingerprint(root)
    manifest_path = destination.with_suffix('.manifest.json')
    if not force and destination.exists() and manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding='utf-8'))
            if previous.get('sourceFingerprint') == inputs and previous.get('sha256') == digest(destination):
                smoke(destination)
                print(f'Windows algorithm is up to date: {destination}', flush=True)
                return destination
        except (OSError, ValueError, RuntimeError):
            pass
    lock = tomllib.loads((root / 'sdk.lock').read_text(encoding='utf-8'))
    contract = lock['windows_algorithm_build']
    packages = contract['packages']
    if packages['eigen']['version'] != lock['affective_algorithm_sdk']['build']['eigen_versions']['windows_x86_64']:
        raise ValueError('Windows Eigen lock mismatch')
    cache = root / 'algorithm-packages/windows'
    archives = {name: obtain_archive(package, cache, offline) for name, package in packages.items()}
    output = root / '.runtime/algorithm'
    output.mkdir(parents=True, exist_ok=True)
    logs = root / '.runtime/logs'
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / 'windows-algorithm-build.log'
    if log_path.exists():
        os.replace(log_path, log_path.with_suffix('.previous.log'))
    print(f'Building Windows algorithm; log={log_path}', flush=True)
    with tempfile.TemporaryDirectory(prefix='win-build-', dir=root / '.runtime') as temporary, log_path.open('w', encoding='utf-8') as log:
        workspace = Path(temporary)
        locations = {}
        for name, archive in archives.items():
            directory = workspace / name
            extract_archive(archive, directory)
            locations[name] = directory / packages[name]['directory']
        compiler = locations['llvm'] / 'bin/x86_64-w64-mingw32-clang++.exe'
        cmake = locations['cmake'] / 'bin/cmake.exe'
        ninja = locations['ninja'] / 'ninja.exe'
        env = dict(os.environ)
        env['PATH'] = str(compiler.parent) + os.pathsep + env['PATH']

        def run(args: list, *, capture: bool = False) -> str:
            command = list(map(str, args))
            log.write(subprocess.list2cmdline(command) + '\n')
            log.flush()
            if capture:
                result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
                log.write(result.stdout + result.stderr)
            else:
                result = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
            if result.returncode:
                raise RuntimeError(f'Build command failed ({result.returncode}); see {log_path}')
            return result.stdout if capture else ''

        versions = {'compiler': run([compiler, '--version'], capture=True),
                    'cmake': run([cmake, '--version'], capture=True),
                    'ninja': run([ninja, '--version'], capture=True)}
        for key, expected in [('compiler', contract['compiler_version']), ('cmake', packages['cmake']['version']), ('ninja', packages['ninja']['version'])]:
            if expected not in versions[key]:
                raise ValueError(f'{key} version does not match sdk.lock')
        prefix = workspace / 'prefix'
        common = ['-G', 'Ninja', f'-DCMAKE_MAKE_PROGRAM={ninja.as_posix()}',
                  f'-DCMAKE_CXX_COMPILER={compiler.as_posix()}',
                  f'-DCMAKE_C_COMPILER={(compiler.parent / "x86_64-w64-mingw32-clang.exe").as_posix()}',
                  f'-DCMAKE_INSTALL_PREFIX={prefix.as_posix()}', '-DCMAKE_BUILD_TYPE=Release',
                  '-DCMAKE_CXX_FLAGS=-D_USE_MATH_DEFINES -DNOMINMAX',
                  '-DCMAKE_EXE_LINKER_FLAGS=-static -Wl,--strip-all']
        print('Preparing Eigen and NumCpp headers ...', flush=True)
        run([cmake, '-S', locations['eigen'], '-B', workspace / 'eigen-build', *common,
             '-DBUILD_TESTING=OFF', '-DEIGEN_BUILD_DOC=OFF', '-DEIGEN_BUILD_PKGCONFIG=OFF'])
        run([cmake, '--install', workspace / 'eigen-build'])
        run([cmake, '-S', root / 'third_party/NumCpp', '-B', workspace / 'numcpp-build', *common,
             '-DNUMCPP_NO_USE_BOOST=ON'])
        run([cmake, '--install', workspace / 'numcpp-build'])
        print('Compiling the SDK and algorithm executable ...', flush=True)
        run([cmake, '-S', root / 'mac/algorithm_bridge', '-B', workspace / 'bridge', *common,
             f'-DAFFECTIVE_SDK_SOURCE_DIR={(root / "third_party/AffectiveCloud-Algorithm-SDK").as_posix()}',
             f'-DCMAKE_PREFIX_PATH={prefix.as_posix()}', '-DNUMCPP_NO_USE_BOOST=ON'])
        run([cmake, '--build', workspace / 'bridge', '--parallel', '2'])
        candidate = workspace / 'bridge/bin/neurobridge_affective_bridge.exe'
        validate_pe(candidate)
        imports = validate_imports(run([locations['llvm'] / 'bin/llvm-readobj.exe', '--coff-imports', candidate], capture=True))
        verification = smoke(candidate)
        if source_fingerprint(root) != inputs:
            raise RuntimeError('Algorithm sources changed during compilation; retry from stable source')
        try:
            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, stderr=subprocess.DEVNULL, text=True, timeout=10).strip()
        except (OSError, subprocess.SubprocessError):
            revision = 'unavailable-source-export'
        manifest = {'schemaVersion': 1, 'sourceCommit': revision, 'sourceFingerprint': inputs,
                    'target': 'windows-x86_64', 'configuration': 'Release', 'signed': False,
                    'sha256': digest(candidate), 'toolchain': versions, 'dependencies': packages,
                    'dllImports': imports, 'smokeTest': verification, 'realDeviceAcceptancePassed': False}
        install_verified(candidate, manifest, destination)
    print(f'Windows algorithm built and verified: {destination}', flush=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('The native Windows build must run on Windows x64')
    try:
        (ROOT / '.runtime').mkdir(exist_ok=True)
        with instance_lock(ROOT, 'windows-algorithm-build.lock'):
            build(ROOT, offline=args.offline, force=args.force)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
