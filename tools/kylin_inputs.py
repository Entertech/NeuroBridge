"""Validate locked Kylin inputs independently of the build host architecture."""
from pathlib import Path
import hashlib
import re
import tomllib
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'config/kylin-bootstrap-inputs.toml'
CACHE = ROOT / '.runtime/kylin-bootstrap-inputs'
OFFLINE = ROOT / 'packaging/kylin/offline'
RESOURCE_INPUTS = {
    'python': ('python_x86_64', 'python_source'),
    'cmake': ('cmake_x86_64', 'cmake_source'),
    'eigen': ('eigen',), 'pyserial': ('pyserial',), 'websockets': ('websockets',),
}


def resource_selection(value='all'):
    value = value.strip()
    if value == 'all':
        return tuple(RESOURCE_INPUTS)
    if value == 'none':
        return ()
    names = [part.strip() for part in value.split(',')]
    if not names or any(name not in RESOURCE_INPUTS for name in names) or len(set(names)) != len(names):
        raise ValueError('offline resources must be all, none, or unique comma-separated names: ' + ','.join(RESOURCE_INPUTS))
    return tuple(name for name in RESOURCE_INPUTS if name in names)


def selection_key(value='all'):
    names = resource_selection(value)
    if not names:
        return 'none'
    return 'all' if len(names) == len(RESOURCE_INPUTS) else '-'.join(names)


def selected_artifacts(data, value='all'):
    keys = {key for name in resource_selection(value) for key in RESOURCE_INPUTS[name]}
    return {key: item for key, item in data['artifacts'].items() if key in keys}


def input_relative_path(key, filename):
    if key == 'python_x86_64':
        directory = 'python-runtime'
    elif key in ('pyserial', 'websockets'):
        directory = 'wheelhouse'
    else:
        directory = 'packaging/kylin/offline'
    return f'source/{directory}/{filename}'


def offline_manifest(data, value='all'):
    return {'schemaVersion': 1, 'resources': list(resource_selection(value)),
            'artifacts': [{'key': key, 'path': input_relative_path(key, item['filename']),
                           'sha256': item['sha256']} for key, item in selected_artifacts(data, value).items()]}


def catalog(path=LOCK):
    data = tomllib.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported Kylin input catalog schema')
    for key, item in data['artifacts'].items():
        name = item['filename']
        if Path(name).name != name or not re.fullmatch(r'[A-Za-z0-9+_.-]+', name):
            raise ValueError(f'Unsafe input filename: {key}')
        url = urlparse(item['url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError(f'Input URL must use HTTPS without credentials: {key}')
        if not re.fullmatch('[0-9a-f]{64}', item['sha256']):
            raise ValueError(f'Invalid input digest: {key}')
    for key, profile in data['profiles'].items():
        if profile['bits'] not in ('32', '64'):
            raise ValueError(f'Invalid profile bitness: {key}')
        for field in ('python', 'cmake'):
            if profile[field] not in data['artifacts']:
                raise ValueError(f'Missing profile dependency: {key}.{field}')
    return data


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def verified_input(item, cache=CACHE, offline=OFFLINE):
    candidates = (cache / item['filename'], offline / item['filename'], offline / 'wheelhouse' / item['filename'])
    for path in candidates:
        if path.exists():
            if path.is_symlink() or not path.is_file() or digest(path) != item['sha256']:
                raise ValueError(f'Kylin input checksum/ownership check failed: {path}')
            return path
    raise ValueError(f'Missing locked input {item["filename"]}; run tools/prepare-kylin-bootstrap-inputs.py --download')
