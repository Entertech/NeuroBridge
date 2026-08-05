"""Lookup helpers for separately stored B-side protocol versions."""

from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "neurobridge" / "version_registry.toml"


def load_registry() -> dict:
    return tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def normalize_version(version: str) -> str:
    return version.removeprefix("v")


def external_protocol_catalog(registry: dict) -> dict:
    return registry["documents"]["external_northbound"]


def select_external_protocol(registry: dict, version: str | None = None) -> dict:
    catalog = external_protocol_catalog(registry)
    selected_version = normalize_version(version) if version else catalog["current_version"]
    for protocol in catalog["versions"]:
        if protocol["version"] == selected_version:
            return protocol
    available = ", ".join(f'v{item["version"]}' for item in catalog["versions"])
    raise ValueError(f"Unknown external protocol version v{selected_version}; available: {available}")
