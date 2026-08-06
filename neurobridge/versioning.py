"""Read release and protocol versions from the repository version registry."""

from __future__ import annotations

from importlib.resources import files
import tomllib


def _load_registry() -> dict:
    return tomllib.loads(files("neurobridge").joinpath("version_registry.toml").read_text(encoding="utf-8"))


REGISTRY = _load_registry()
APPLICATION_VERSION = REGISTRY["application"]["version"]
NORTHBOUND_PROTOCOL_VERSION = REGISTRY["northbound_wire_protocol"]["version"]
