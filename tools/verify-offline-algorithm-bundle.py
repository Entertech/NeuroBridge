#!/usr/bin/env python3
"""Verify and unpack the SDK sources carried by an offline Ubuntu bundle."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tarfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if destination not in target.parents and target != destination:
                fail(f"Archive member escapes destination: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                fail(f"Archive contains an unsupported link or device: {member.name}")
        bundle.extractall(destination, filter="data")


def main() -> None:
    if len(sys.argv) != 3:
        fail("Usage: verify-offline-algorithm-bundle.py BUNDLE_DIRECTORY EMPTY_DESTINATION")
    bundle_dir, destination = (Path(value).resolve() for value in sys.argv[1:])
    manifest_path = bundle_dir / "manifest.toml"
    if not manifest_path.is_file():
        fail(f"Offline bundle manifest is missing: {manifest_path}")
    if destination.exists() and any(destination.iterdir()):
        fail(f"Destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    bundle = manifest.get("bundle", {})
    if bundle.get("format_version") != 1 or bundle.get("ubuntu_version") != "24.04" or bundle.get("architecture") != "amd64":
        fail("Offline bundle is not for Ubuntu 24.04 amd64")
    lock = tomllib.loads((ROOT / "sdk.lock").read_text(encoding="utf-8"))
    expected = {
        "affective": ("AffectiveCloud-Algorithm-SDK", lock["affective_algorithm_sdk"]["commit"]),
        "numcpp": ("NumCpp", lock["affective_algorithm_sdk"]["build"]["numcpp_commit"]),
    }
    archives = manifest.get("archives", {})
    for name, (directory, commit) in expected.items():
        entry = archives.get(name)
        if not isinstance(entry, dict) or entry.get("commit") != commit:
            fail(f"Offline bundle {name} source does not match sdk.lock")
        archive = bundle_dir / str(entry.get("path", ""))
        if not archive.is_file() or sha256(archive) != entry.get("sha256"):
            fail(f"Offline bundle {name} archive checksum does not match manifest")
        extract(archive, destination)
        if not (destination / directory).is_dir():
            fail(f"Offline bundle {name} archive has the wrong top-level directory")
    print("Offline algorithm SDK bundle verified.")


if __name__ == "__main__":
    main()
