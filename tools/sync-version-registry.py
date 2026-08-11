#!/usr/bin/env python3
"""Generate non-Python version consumers from the single version registry."""

from __future__ import annotations

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "neurobridge" / "version_registry.toml"
CLIENT_VERSION_PATH = ROOT / "web" / "b-client-test" / "version.js"
PYPROJECT_PATH = ROOT / "pyproject.toml"


def render_change_log(registry: dict) -> str:
    external = registry["documents"]["external_northbound"]
    locks = registry["release_locks"]
    records = registry["change_records"]
    lines = [
        "# 版本变更记录",
        "",
        "本文件由 `neurobridge/version_registry.toml` 生成；版本台账是唯一来源。本文仅展示 B 端可见的已发布版本和待发布变更。",
        "",
        "## 已发布版本",
        "",
    ]
    for lock in locks:
        if lock["scope"] != "external_northbound_document":
            continue
        lines.extend(
            [
                f'### v{lock["from_version"]} → v{lock["to_version"]}',
                "",
                f'- 状态：`{lock["status"]}`；锁定时间：{lock["locked_at"]}。',
                f'- 锁定依据：已发布的 B 端 {external["title"]} v{lock["to_version"]} PDF。',
            ]
        )
        summary = lock.get("change_summary", [])
        if summary:
            lines.extend(
                [
                    "- 变更摘要：",
                    *[f"  - {item}" for item in summary],
                ]
            )
        lines.extend(
            [
                "- 约束：该版本 Markdown、PDF 及其语义已锁定，后续变更不会回写到此版本。",
                "",
            ]
        )
    lines.extend(["## 待发布变更", ""])
    unlocked = [record for record in records if record["change_state"] == "unlocked"]
    if not unlocked:
        lines.extend(["当前没有面向 B 端的待发布变更。", ""])
    for record in unlocked:
        lines.extend(
            [
                f'### {record["date"]}：{record["id"]}',
                "",
                f'- 变更：{record["summary"]}',
                f'- 目标版本：v{record["target_external_release"]}（以已发布的 v{record["base_locked_release"]} 为基线）。',
                f'- 影响说明：{record["reason"]}',
                "",
            ]
        )
    return "\n".join(lines)


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
    change_log_path = ROOT / registry["change_policy"]["change_log_path"]
    change_log_path.parent.mkdir(parents=True, exist_ok=True)
    change_log_path.write_text(render_change_log(registry), encoding="utf-8")


if __name__ == "__main__":
    main()
