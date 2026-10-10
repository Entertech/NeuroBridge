#!/usr/bin/env python3
"""Prepare verified dependencies only; never build an installer or runtime."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile
import urllib.request
from kylin_inputs import CACHE, catalog, digest, verified_input, selected_artifacts, selection_key


def event(message):
    print(f'EVENT utc={datetime.now(timezone.utc).isoformat()} phase=prepare_inputs {message}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='fetch missing locked inputs over HTTPS')
    parser.add_argument('--cache-dir', type=Path, default=CACHE)
    parser.add_argument('--offline-resources', default='all', help='all (default), none, or comma-separated resource names')
    parser.add_argument('--selection-key', action='store_true', help='print canonical cache selection key without preparing files')
    args = parser.parse_args()
    if args.selection_key:
        print(selection_key(args.offline_resources))
        return
    data = catalog()
    event('selection=' + selection_key(args.offline_resources))
    for key, item in selected_artifacts(data, args.offline_resources).items():
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
