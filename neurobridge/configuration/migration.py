"""Deterministic configuration schema migration boundary."""

from __future__ import annotations

from typing import Mapping


CURRENT_SCHEMA_VERSION = 1


def migrate(values: Mapping[str, object]) -> dict[str, object]:
    result = dict(values)
    version = result.get("config_schema_version", CURRENT_SCHEMA_VERSION)
    if version != CURRENT_SCHEMA_VERSION:
        raise ValueError(f"No configuration migration is available from schema {version}")
    result["config_schema_version"] = CURRENT_SCHEMA_VERSION
    return result
