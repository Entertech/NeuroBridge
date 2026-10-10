#!/usr/bin/env python3
"""Prepare verified dependencies only; never build an installer or runtime."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile
import urllib.request
from kylin_inputs import CACHE, catalog, digest, verified_input


def event(message):
    print(f'EVENT utc={datetime.now(timezone.utc).isoformat()} phase=prepare_inputs {message}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='fetch missing locked inputs over HTTPS')
    parser.add_argument('--cache-dir', type=Path, default=CACHE)
    args = parser.parse_args()
    data = catalog()
    for key, item in data['artifacts'].items():
        try:
            path = verified_input(item, args.cache_dir)
        except ValueError as error:
            # A corrupt cache must be corrected explicitly, never silently replaced.
            if not args.download or not str(error).startswith('Missing locked input'):
                raise
            args.cache_dir.mkdir(parents=True, exist_ok=True)
            event(f'download_start key={key} file={item["filename"]}')
            descriptor, temporary = tempfile.mkstemp(prefix='.input-', dir=args.cache_dir)
            path = Path(temporary)
            try:
                with os.fdopen(descriptor, 'wb') as output, urllib.request.urlopen(item['url'], timeout=60) as source:
                    if not source.geturl().startswith('https://'):
                        raise ValueError('Refusing a non-HTTPS redirect')
                    while block := source.read(1024 * 1024):
                        output.write(block)
                actual = digest(path)
                if actual != item['sha256']:
                    raise ValueError(f'Input digest mismatch key={key} expected={item["sha256"]} actual={actual}')
                path.replace(args.cache_dir / item['filename'])
                path = args.cache_dir / item['filename']
            finally:
                Path(temporary).unlink(missing_ok=True)
        event(f'verified key={key} file={path.name} sha256={item["sha256"]} bytes={path.stat().st_size}')
    event('complete profiles=' + ','.join(data['profiles']))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        event(f'failure reason={error}')
        sys.exit(1)
