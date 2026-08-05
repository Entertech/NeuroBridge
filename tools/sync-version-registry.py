#!/usr/bin/env python3
"""Generate non-Python version consumers from the single version registry."""

from __future__ import annotations

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "neurobridge" / "version_registry.toml"
CLIENT_VERSION_PATH = ROOT / "tools" / "b-client-test" / "version.js"
PYPROJECT_PATH = ROOT / "pyproject.toml"


def main() -> None:
    registry = tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    protocol_version = registry["northbound_wire_protocol"]["version"]
    application_version = registry["application"]["version"]
    CLIENT_VERSION_PATH.write_text(
        "// Generated from neurobridge/version_registry.toml by tools/sync-version-registry.py.\n"
        f'window.NEUROBRIDGE_VERSION = Object.freeze({{ protocolVersion: "{protocol_version}" }});\n',
        encoding="utf-8",
    )
    pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")
    updated_pyproject, replacements = re.subn(
        r'(?m)^(version\s*=\s*)"[^"]+"$',
        rf'\g<1>"{application_version}"',
        pyproject_text,
        count=1,
    )
    if replacements != 1:
        raise SystemExit("Unable to update project version in pyproject.toml")
    PYPROJECT_PATH.write_text(updated_pyproject, encoding="utf-8")


if __name__ == "__main__":
    main()
