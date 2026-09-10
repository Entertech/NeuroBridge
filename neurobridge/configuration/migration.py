"""Deterministic, backup-first configuration schema migration."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import tomllib
import uuid


CURRENT_SCHEMA_VERSION = 1


def migrate(values: Mapping[str, object]) -> dict[str, object]:
    """Return a migrated copy; missing version is the pre-registry schema 0."""

    result = _copy_mapping(values)
    version = result.get("config_schema_version", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("config_schema_version must be an integer")
    if version < 0 or version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"No configuration migration is available from schema {version}")
    if version == 0:
        result["config_schema_version"] = 1
        version = 1
    if version != CURRENT_SCHEMA_VERSION:
        raise ValueError(f"No configuration migration is available from schema {version}")
    return result


def migrate_file(
    path: str | Path,
    *,
    backup_directory: str | Path | None = None,
    history_path: str | Path | None = None,
) -> bool:
    """Atomically migrate a file, retaining the exact old bytes and audit row."""

    source = Path(path)
    original = source.read_bytes()
    original_stat = source.stat()
    raw = tomllib.loads(original.decode("utf-8"))
    migrated = migrate(raw)
    if migrated == raw:
        return False
    backup_root = Path(backup_directory) if backup_directory else source.parent / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / f"{source.name}.{timestamp}.{uuid.uuid4().hex[:8]}.bak"
    shutil.copy2(source, backup)
    temporary = source.with_name(f".{source.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(_toml_document(migrated), encoding="utf-8")
        temporary.chmod(stat.S_IMODE(original_stat.st_mode))
        if hasattr(os, "chown"):
            os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        # Parse and migrate the staged value before replacing the live file.
        migrate(tomllib.loads(temporary.read_text(encoding="utf-8")))
        os.replace(temporary, source)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    audit = Path(history_path) if history_path else source.parent / "migration-history.jsonl"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": str(source),
        "backup": str(backup),
        "fromSchema": raw.get("config_schema_version", 0),
        "toSchema": CURRENT_SCHEMA_VERSION,
    }
    with audit.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return True


def _copy_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _copy_mapping(value) if isinstance(value, Mapping) else list(value) if isinstance(value, list) else value
        for key, value in values.items()
    }


def _toml_document(values: Mapping[str, object]) -> str:
    lines: list[str] = []
    scalars = {key: value for key, value in values.items() if not isinstance(value, Mapping)}
    tables = {key: value for key, value in values.items() if isinstance(value, Mapping)}
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for key, table in tables.items():
        if lines:
            lines.append("")
        lines.append(f"[{key}]")
        for field, value in table.items():
            if isinstance(value, Mapping):
                raise ValueError("Nested TOML tables deeper than one level are not supported")
            lines.append(f"{field} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ValueError(f"Unsupported TOML value type: {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Atomically migrate a NeuroBridge configuration file")
    parser.add_argument("path", type=Path)
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--history-path", type=Path)
    args = parser.parse_args()
    changed = migrate_file(
        args.path,
        backup_directory=args.backup_directory,
        history_path=args.history_path,
    )
    print("migrated" if changed else "already-current")


if __name__ == "__main__":
    main()
