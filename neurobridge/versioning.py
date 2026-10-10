"""Read release and protocol versions from the repository version registry."""

from __future__ import annotations

from importlib.resources import files
import sys
import tomllib


def _load_registry() -> dict:
    return tomllib.loads(files("neurobridge").joinpath("version_registry.toml").read_text(encoding="utf-8"))


REGISTRY = _load_registry()


def application_version(platform: str, registry: dict | None = None) -> str:
    """Resolve a product version independently from the aggregate release version."""
    application = (REGISTRY if registry is None else registry)["application"]
    return application.get("platform_versions", {}).get(platform, application["version"])


RELEASE_VERSION = REGISTRY["application"]["version"]
APPLICATION_VERSION = application_version("windows" if sys.platform == "win32" else "kylin")
NORTHBOUND_PROTOCOL_VERSION = REGISTRY["northbound_wire_protocol"]["version"]
