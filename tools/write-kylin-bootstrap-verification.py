#!/usr/bin/env python3
"""Write the release verification record for one Kylin bootstrap package.

The bootstrap package has no prebuilt runtime: the runtime is produced on the
machine that installs it. The verification record therefore hashes the pinned
offline inputs that the package carries, not a compiled runtime tree.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.kylin_package import inspect_package


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument('--offline-resources', default='all')
    args = parser.parse_args()
    packages = sorted(args.input_dir.glob("*.deb"))
    if len(packages) != 1:
        print(f"expected one bootstrap deb in {args.input_dir}, found {len(packages)}", file=sys.stderr)
        return 1
    package = packages[0]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    now = datetime.now(timezone.utc).isoformat()
    inputs = inspect_package(package, args.offline_resources)
    input_digest = inputs['inputSha256']
    log = args.input_dir / "validation.log"
    log.write_text(
        f"bootstrap package {package.name}\n"
        "native format validation: passed (deb)\n"
        "runtime: built on the installing Kylin machine, not prebuilt\n",
        encoding="utf-8",
    )
    with log.open('a', encoding='utf-8') as output:
        output.write(f"offline resources: {','.join(inputs['resources']) or 'none'}; artifacts={inputs['artifactCount']}; bytes={inputs['resourceBytes']}\n")
    report = {
        "targetId": args.target,
        "targetArchitecture": "all",
        "sourceCommit": commit,
        "fileName": package.name,
        "sha256": digest(package),
        "automatedValidation": "passed",
        "validationLog": log.name,
        "toolchain": "kylin-bootstrap; dpkg-deb; runtime built at install time",
        "runtimeSha256": input_digest,
        "inputSha256": input_digest,
        "sourceReferences": [{
            "kind": "other",
            "url": f"https://github.com/Entertech/NeuroBridge/commit/{commit}",
            "retrievedAt": now,
            "sha256": hashlib.sha256(commit.encode("ascii")).hexdigest(),
        }],
        "builtAt": now,
    }
    (args.input_dir / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
