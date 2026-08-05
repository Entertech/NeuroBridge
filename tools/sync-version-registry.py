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
CHANGE_LOG_PATH = ROOT / "doc" / "tech" / "版本变更记录.md"


def render_change_log(registry: dict) -> str:
    external = registry["documents"]["external_northbound"]
    locks = registry["release_locks"]
    records = registry["change_records"]
    lines = [
        "# 版本变更记录",
        "",
        "本文件由 `neurobridge/version_registry.toml` 生成；版本台账是唯一来源。已锁定区间不可回写，未锁定变更不会修改已发布对外文档。",
        "",
        "## 已锁定的发布区间",
        "",
    ]
    for lock in locks:
        lines.extend(
            [
                f'### v{lock["from_version"]} → v{lock["to_version"]}',
                "",
                f'- 状态：`{lock["status"]}`；锁定时间：{lock["locked_at"]}。',
                f'- 锁定依据：已发布的 B 端 {external["title"]} v{lock["to_version"]} PDF。',
                "- 约束：此区间的 Markdown/PDF 及其语义不再回写；后续变更只能创建新的未锁定记录。",
                "",
            ]
        )
    lines.extend(["## 未锁定变更", ""])
    unlocked = [record for record in records if record["change_state"] == "unlocked"]
    if not unlocked:
        lines.extend(["当前没有未锁定变更。", ""])
    for record in unlocked:
        lines.extend(
            [
                f'### {record["date"]}：{record["id"]}',
                "",
                f'- 变更：{record["summary"]}',
                f'- 锚点：已锁定的 v{record["base_locked_release"]}；目标对外版本：`{record["target_external_release"]}`。',
                f'- 对外文档：`{record["external_document_action"]}`；{record["reason"]}',
                "- 发布条件：收到明确“更新对外文档”授权后，创建下一版本的 Markdown/PDF、发布锁定记录和文件摘要；不得修改已锁定区间。",
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
    CHANGE_LOG_PATH.write_text(render_change_log(registry), encoding="utf-8")


if __name__ == "__main__":
    main()
