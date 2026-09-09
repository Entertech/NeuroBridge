"""Project-local Windows onboarding operations; device I/O stays in the adapter."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import socket
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neurobridge.algorithm_setup import smoke_test_bridge
from neurobridge.configuration.runtime import load_runtime_config
from neurobridge.profiles.resolver import resolve_profile, RuntimePlatform


def config_path(root: Path) -> Path:
    return root / '.runtime/config/windows-gateway.toml'


def validate_config(path: Path):
    config = load_runtime_config(path)
    if config.profile != 'windows_headset_local':
        raise ValueError('Expected profile=windows_headset_local; existing configuration was preserved')
    resolve_profile(config, RuntimePlatform('windows', 'x86_64'))
    if not config.algorithm.enabled:
        raise ValueError('Windows launcher requires algorithm.enabled=true')
    if not config.local_ui.enabled or config.logging.rotation_mode != 'size':
        raise ValueError('Windows launcher requires local UI and size-based log rotation')
    if any(host != '127.0.0.1' for host in
           (config.server.host, config.local_ui.host, config.download.host)):
        raise ValueError('All listeners must use 127.0.0.1')
    return config


def create_config(root: Path) -> Path:
    """Create once from the maintained template; never replace operator settings."""
    path = config_path(root)
    if path.exists():
        validate_config(path)
        return path
    for relative in ('config', 'logs', 'recordings', 'algorithm'):
        (root / '.runtime' / relative).mkdir(parents=True, exist_ok=True)
    text = (root / 'windows/gateway.toml.example').read_text(encoding='utf-8')
    replacements = {
        ('recording', 'directory'): json.dumps(str(root / '.runtime/recordings')),
        ('logging', 'directory'): json.dumps(str(root / '.runtime/logs')),
        ('algorithm', 'command'): json.dumps([str(root / '.runtime/algorithm/neurobridge_affective_bridge.exe')]),
    }
    section = ''
    lines = []
    for line in text.splitlines():
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
        key = line.split('=', 1)[0].strip()
        if (section, key) in replacements:
            line = f'{key} = {replacements.pop((section, key))}'
        lines.append(line)
    if replacements:
        raise ValueError('Windows template is missing required path fields')
    # Exclusive create protects existing user configuration, including concurrent setup.
    with path.open('x', encoding='utf-8', newline='\n') as output:
        output.write('\n'.join(lines) + '\n')
    validate_config(path)
    return path


def check_algorithm(config) -> dict:
    if len(config.algorithm.command) != 1:
        raise ValueError('Launcher expects a single Windows bridge executable in algorithm.command')
    bridge = Path(config.algorithm.command[0])
    if not bridge.is_absolute() or bridge.suffix.lower() != '.exe':
        raise ValueError('algorithm.command must be an absolute Windows .exe path')
    if not bridge.is_file():
        raise ValueError(f'Windows algorithm bridge missing: {bridge}. Supply the approved x64 .exe and its DLL dependencies; Linux/macOS binaries cannot be used.')
    return smoke_test_bridge(bridge)


def check_ports(config) -> None:
    ports = [config.server.port, config.local_ui.port]
    if config.download.enabled:
        ports.append(config.download.port)
    if len(ports) != len(set(ports)):
        raise ValueError('HTTP, WebSocket and download ports must be different')
    for port in ports:
        with socket.socket() as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError as error:
                raise ValueError(f'Port {port} is unavailable; check another gateway/service before starting') from error


def serial_summary(config) -> list[str]:
    from neurobridge.adapters.sources.serial_windows import discover_windows_com_candidates
    ports = discover_windows_com_candidates(config.serial)
    print('USB COM candidates: ' + (', '.join(ports) or 'none; check cable/driver in Device Manager'))
    return ports


def diagnostics(root: Path) -> Path:
    """Allowlisted metadata only: never archive config, logs or recordings wholesale."""
    from neurobridge.versioning import APPLICATION_VERSION
    report = {'applicationVersion': APPLICATION_VERSION, 'system': platform.platform(),
              'python': platform.python_version(), 'architecture': platform.machine(),
              'rawDataIncluded': False, 'logsIncluded': False}
    path = config_path(root)
    report['configExists'] = path.is_file()
    if path.is_file():
        report['configSha256'] = sha256(path.read_bytes()).hexdigest()
        try:
            config = validate_config(path)
            report['configValid'] = True
            report['comCandidates'] = serial_summary(config)
        except Exception as error:
            # Exception messages may include user configuration: keep only the type.
            report['configOrComErrorType'] = type(error).__name__
    directory = root / '.runtime/diagnostics'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    archive = directory / f'windows-diagnostics-{stamp}.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('summary.json', json.dumps(report, indent=2))
    archive.with_suffix('.zip.sha256').write_text(
        f'{sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n', encoding='ascii')
    return archive


@contextmanager
def instance_lock(root: Path, filename: str = 'windows-gateway.lock'):
    """Keep an OS-owned lock for the entire run; a crash releases it automatically."""
    import msvcrt
    path = root / '.runtime' / filename
    with path.open('a+b') as lock:
        if path.stat().st_size == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ValueError('This project gateway is already running') from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['config', 'check', 'algorithm', 'start', 'logs', 'diagnostics'])
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in {'amd64', 'x86_64'}:
        parser.error('This entry requires Windows x64; it does not replace Kylin acceptance')
    os.chdir(ROOT)
    try:
        if args.action == 'config':
            print(f'Configuration ready (existing settings preserved): {create_config(ROOT)}')
            return 0
        if args.action == 'diagnostics':
            print(diagnostics(ROOT))
            return 0
        config = validate_config(config_path(ROOT))
        if args.action == 'logs':
            path = config.logging.directory / config.logging.filename
            # Bounded tail; no second, unrotated console log.
            with path.open('rb') as source:
                source.seek(max(0, path.stat().st_size - 128 * 1024))
                print('\n'.join(source.read().decode('utf-8', errors='replace').splitlines()[-200:]))
        elif args.action == 'check':
            serial_summary(config)
        elif args.action == 'algorithm':
            print(json.dumps(check_algorithm(config)))
            print('Empty-input smoke test only; real-data algorithm acceptance is still required.')
        elif args.action == 'start':
            with instance_lock(ROOT):
                check_ports(config)
                check_algorithm(config)
                serial_summary(config)
                print(f'Open http://127.0.0.1:{config.local_ui.port}/capture/ after the gateway starts.', flush=True)
                print('Foreground gateway: keep this window open; Ctrl+C stops capture.', flush=True)
                from neurobridge.__main__ import run
                asyncio.run(run(str(config_path(ROOT))))
    except KeyboardInterrupt:
        print('Gateway stopped.')
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
