#!/usr/bin/env python3
"""Check both included and omitted offline resources in the actual DEB."""
import argparse
import json
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.kylin_package import inspect_package

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--package', type=Path, required=True)
parser.add_argument('--offline-resources', default='all')
args = parser.parse_args()
try:
    print(json.dumps(inspect_package(args.package, args.offline_resources), sort_keys=True))
except (OSError, ValueError, tarfile.TarError) as error:
    print(f'bootstrap payload check failed: {error}', file=sys.stderr)
    sys.exit(1)
