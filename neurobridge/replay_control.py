"""Persist the gateway operator's replay policy and apply it with systemd."""

from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import tomllib

from .config import load

CONFIG_PATH = Path('/etc/neurobridge/gateway.toml')
SYSTEMCTL = '/usr/bin/systemctl'
SERVICE = 'neurobridge.service'


class ReplayControlError(RuntimeError):
    """An operator-facing message containing no configuration contents."""


def updated_config(contents: str, enabled: bool) -> str:
    """Preserve deployment comments; reject layouts we cannot edit exactly."""
    original = tomllib.loads(contents)
    expected = deepcopy(original)
    expected.setdefault('recording', {})['replay_enabled'] = enabled
    value = 'true' if enabled else 'false'
    newline = '\r\n' if '\r\n' in contents else '\n'
    lines = contents.splitlines(keepends=True)
    header = re.compile(r'''\s*\[\s*(?:recording|"recording"|'recording')\s*\]\s*(?:#.*)?''')
    sections = [i for i, line in enumerate(lines) if header.fullmatch(line.strip())]
    if not sections and 'recording' not in original:
        result = contents + ('' if not contents or contents.endswith('\n') else newline) + f'[recording]{newline}replay_enabled = {value}{newline}'
    else:
        if len(sections) != 1:
            raise ValueError('Use one explicit [recording] table before changing replay policy.')
        start = sections[0] + 1
        end = next((i for i in range(start, len(lines)) if lines[i].lstrip().startswith('[')), len(lines))
        key = re.compile(r'''(?P<prefix>\s*(?:replay_enabled|"replay_enabled"|'replay_enabled')\s*=\s*)(?:true|false)(?P<suffix>\s*(?:#.*)?)''')
        matches = [(i, key.fullmatch(lines[i].rstrip('\r\n'))) for i in range(start, end)]
        matches = [(i, match) for i, match in matches if match]
        if 'replay_enabled' in original.get('recording', {}):
            if len(matches) != 1:
                raise ValueError('Cannot safely locate recording.replay_enabled; use a boolean in [recording].')
            i, match = matches[0]
            ending = lines[i][len(lines[i].rstrip('\r\n')):]
            lines[i] = match['prefix'] + value + match['suffix'] + ending
        else:
            if lines[start - 1] and not lines[start - 1].endswith('\n'):
                lines[start - 1] += newline
            lines.insert(start, f'replay_enabled = {value}{newline}')
        result = ''.join(lines)
    if tomllib.loads(result) != expected:
        raise ValueError('Replay edit would change other deployment settings; no configuration was written.')
    return result


def atomic_write(path: Path, contents: bytes, metadata: os.stat_result) -> None:
    descriptor, name = tempfile.mkstemp(prefix='.replay-config-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as file:
            if os.geteuid() == 0:
                os.fchown(file.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(file.fileno(), stat.S_IMODE(metadata.st_mode))
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        # Validate the entire deployment with the same loader used at startup.
        load(temporary)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def service_active() -> bool:
    result = subprocess.run([SYSTEMCTL, 'is-active', '--quiet', SERVICE], check=False)
    if result.returncode not in (0, 3):
        raise ReplayControlError('Cannot query neurobridge.service. Inspect systemd.')
    return result.returncode == 0


def apply(action: str, path: Path = CONFIG_PATH) -> dict:
    if action not in {'enable', 'disable', 'status'}:
        raise ValueError('Replay action must be enable, disable or status.')
    if path.is_symlink() or not path.is_file():
        raise ValueError('Gateway configuration must be an existing regular file, not a symlink.')
    if action == 'status':
        return {'replayEnabledConfigured': load(path).recording.replay_enabled, 'serviceActive': service_active(), 'applied': None}
    lock_descriptor = os.open(path.parent / '.replay-control.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_descriptor, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as file:
            metadata = os.fstat(file.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError('Gateway configuration must be a regular file.')
            original = file.read()
        load(path)
        was_active = service_active()
        contents = updated_config(original.decode('utf-8'), action == 'enable').encode('utf-8')
        if path.read_bytes() != original or path.stat().st_ino != metadata.st_ino:
            raise RuntimeError('Gateway configuration changed concurrently; retry the command.')
        atomic_write(path, contents, metadata)
        if was_active:
            print('Replay policy saved. Restarting gateway; B-side clients must reconnect and resubscribe.', flush=True)
            result = subprocess.run([SYSTEMCTL, 'restart', SERVICE], check=False)
            try:
                activated = result.returncode == 0 and service_active()
            except ReplayControlError:
                activated = False
            if not activated:
                raise ReplayControlError('Replay policy was saved, but gateway restart/activation failed. Inspect neurobridge-ops logs; the requested policy remains saved.')
        return {'replayEnabledConfigured': action == 'enable', 'serviceActive': was_active, 'applied': was_active}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['enable', 'disable', 'status'])
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, 'Run through neurobridge-ops replay or sudo on the gateway.\n')
    try:
        result = apply(args.action)
    except ReplayControlError as error:
        parser.exit(1, str(error) + '\n')
    except (OSError, ValueError, RuntimeError):
        # Loader/decoder exceptions may embed configuration text; never echo it.
        parser.exit(1, 'Replay command failed. Configuration may have been saved without activation; inspect service state and the deployment configuration.\n')
    print(json.dumps(result, separators=(',', ':')))
    if args.action == 'status':
        print('Status describes saved configuration; enable/disable reapplies it to a running gateway.')
    elif not result['applied']:
        print('Gateway is stopped. Policy is saved and will apply on its next start.')


if __name__ == '__main__':
    main()
