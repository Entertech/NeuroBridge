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
