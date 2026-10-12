"""Read release and protocol versions from the repository version registry."""

from __future__ import annotations

from importlib.resources import files
import tomllib
import sys


def _load_registry() -> dict:
    return tomllib.loads(files("neurobridge").joinpath("version_registry.toml").read_text(encoding="utf-8"))


REGISTRY = _load_registry()
DELIVERY_VERSION = REGISTRY["application"]["version"]


def platform_version(platform: str, registry: dict | None = None) -> str:
    if platform not in {"windows", "kylin"}:
        raise ValueError(f"unknown delivery platform: {platform}")
    source = REGISTRY if registry is None else registry
    if "application" in source and not source.get("release_version_policy", {}).get("independent_platforms", False):
        return source["application"]["version"]
    return source["platform_releases"][platform]["version"]


def runtime_version(system: str = sys.platform) -> str:
    if system == "win32":
        return platform_version("windows")
    if system.startswith("linux"):
        return platform_version("kylin")
    return DELIVERY_VERSION


APPLICATION_VERSION = runtime_version()
NORTHBOUND_PROTOCOL_VERSION = REGISTRY["northbound_wire_protocol"]["version"]
