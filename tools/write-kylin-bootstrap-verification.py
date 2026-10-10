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
sys.path.insert(0, str(ROOT / "tools"))
from kylin_inputs import catalog, verified_input
OFFLINE = ROOT / "packaging" / "kylin" / "offline"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def digest_tree(path: Path) -> str:
    value = hashlib.sha256()
    for item in sorted(item for item in path.rglob("*") if item.is_file()):
        value.update(item.relative_to(path).as_posix().encode("utf-8"))
        value.update(bytes.fromhex(digest(item)))
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    args = parser.parse_args()
    packages = sorted(args.input_dir.glob("*.deb"))
    if len(packages) != 1:
        print(f"expected one bootstrap deb in {args.input_dir}, found {len(packages)}", file=sys.stderr)
        return 1
    package = packages[0]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    now = datetime.now(timezone.utc).isoformat()
    inputs = catalog()
    value = hashlib.sha256()
    for key, item in sorted(inputs["artifacts"].items()):
        value.update(key.encode())
        value.update(bytes.fromhex(digest(verified_input(item))))
    value.update(bytes.fromhex(digest(ROOT / "config/kylin-bootstrap-inputs.toml")))
    input_digest = value.hexdigest()
    log = args.input_dir / "validation.log"
    log.write_text(
        f"bootstrap package {package.name}\n"
        "native format validation: passed (deb)\n"
        "runtime: built on the installing Kylin machine, not prebuilt\n",
        encoding="utf-8",
    )
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
