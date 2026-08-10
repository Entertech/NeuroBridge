#!/usr/bin/env python3
"""Promote all pending external documents on the PR branch before it merges."""

from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
from pathlib import Path
import subprocess

from external_protocol_registry import ROOT, load_registry


PUBLISHABLE_DOCUMENTS = (
    {
        "key": "external_ssh_operations",
        "slug": "ssh-operations",
        "scope": "external_ssh_operations_document",
        "summary": "发布头环数据网关 SSH 运维操作指南。",
    },
    {
        "key": "external_wired_network_operations",
        "slug": "wired-network-operations",
        "scope": "external_wired_network_operations_document",
        "summary": "发布头环数据网关有线网络配置指南。",
    },
)
UNPUBLISHED_STATUS = "状态：未发布（评审稿）"
PUBLISHED_STATUS = "状态：已发布"
UNPUBLISHED_NOTICE = "未发布版本仅供评审和现场准备使用，不能作为验收或正式交付依据。"
PUBLISHED_NOTICE = "本版本已作为正式交付文档发布；现场验收仍应以双方确认的部署配置和验收记录为准。"


def replace_once(text: str, old: str, new: str, description: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one {description} marker.")
    return text.replace(old, new, 1)


def insert_before_first(text: str, marker: str, content: str, description: str) -> str:
    position = text.find(marker)
    if position < 0:
        raise SystemExit(f"Missing {description} marker.")
    return text[:position] + content + text[position:]


def replace_in_section(text: str, key: str, old: str, new: str, description: str) -> str:
    heading = f"[documents.{key}]"
    start = text.find(heading)
    if start < 0:
        raise SystemExit(f"Missing registry section {heading}.")
    next_section = text.find("\n[", start + len(heading))
    end = len(text) if next_section < 0 else next_section
    section = replace_once(text[start:end], old, new, description)
    return text[:start] + section + text[end:]


def pending_document_specs(registry: dict) -> list[dict]:
    pending = []
    for spec in PUBLISHABLE_DOCUMENTS:
        document = registry["documents"][spec["key"]]
        if document["status"] == "unpublished":
            if "pdf_artifact_name" not in document:
                raise SystemExit(f"Unpublished external document lacks a PDF artifact name: {spec['key']}")
            pending.append({**spec, "document": document})
        elif document["status"] != "published":
            raise SystemExit(f"Unsupported {spec['key']} document status: {document['status']}")
    return pending


def ensure_pr_branch() -> None:
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    if not branch or branch == "master":
        raise SystemExit("Promote external documents on the pending PR branch, never directly on master.")


def promote_external_documents(release_date: str) -> bool:
    """Promote all pending package documents without changing their versions."""
    ensure_pr_branch()
    registry_path = ROOT / "neurobridge" / "version_registry.toml"
    registry = load_registry()
    pending = pending_document_specs(registry)
    if not pending:
        return False

    registry_text = registry_path.read_text(encoding="utf-8")
    release_locks = []
    change_records = []
    markdown_updates: list[tuple[Path, str]] = []
    for item in pending:
        spec = item
        document = item["document"]
        markdown_path = ROOT / document["markdown_path"]
        markdown = markdown_path.read_text(encoding="utf-8")
        markdown = replace_once(markdown, UNPUBLISHED_STATUS, PUBLISHED_STATUS, f"{spec['key']} document status")
        markdown = replace_once(markdown, UNPUBLISHED_NOTICE, PUBLISHED_NOTICE, f"{spec['key']} unpublished notice")
        digest = sha256(markdown.encode("utf-8")).hexdigest()
        markdown_updates.append((markdown_path, markdown))

        publication_record = f"{release_date}-public-{spec['slug']}-v{document['version']}"
        lock_id = f"{spec['slug']}-v0.0-to-v{document['version']}"
        registry_text = replace_in_section(
            registry_text,
            spec["key"],
            'status = "unpublished"\naudience = "operator"',
            'status = "published"\naudience = "operator"',
            f"{spec['key']} registry status",
        )
        registry_text = replace_in_section(
            registry_text,
            spec["key"],
            f'created_date = "{document["created_date"]}"\nreason = "{document["reason"]}"',
            "\n".join(
                [
                    f'created_date = "{document["created_date"]}"',
                    f'published_date = "{release_date}"',
                    f'markdown_sha256 = "{digest}"',
                    f'publication_record = "{publication_record}"',
                    'reason = "经发布 PR 审核后标记为已发布；版本保持不变，master CI 将生成正式 PDF ZIP Artifact。"',
                ]
            ),
            f"{spec['key']} publication metadata",
        )
        release_locks.append(
            "\n".join(
                [
                    "[[release_locks]]",
                    f'id = "{lock_id}"',
                    f'scope = "{spec["scope"]}"',
                    'from_version = "0.0"',
                    f'to_version = "{document["version"]}"',
                    'status = "locked"',
                    f'locked_by = "{publication_record}"',
                    f'locked_at = "{release_date}"',
                    "",
                ]
            )
        )
        change_records.append(
            "\n".join(
                [
                    "[[change_records]]",
                    f'id = "{publication_record}"',
                    f'date = "{release_date}"',
                    f'scope = "{spec["scope"]}"',
                    f'summary = "{spec["summary"]}"',
                    'external_document_action = "explicit_user_authorized"',
                    f'external_document_version = "{document["version"]}"',
                    'change_state = "locked"',
                    f'release_lock = "{lock_id}"',
                    'reason = "用户明确授权在发布 PR 分支完成文档状态、发布记录和锁定区间；PR 合入 master 后由 CI 生成正式 Artifact。"',
                    "",
                ]
            )
        )

    registry_text = replace_once(
        registry_text,
        "[change_policy]",
        "\n".join(release_locks) + "\n[change_policy]",
        "change policy heading",
    )
    registry_text = insert_before_first(
        registry_text,
        "[[change_records]]",
        "\n".join(change_records),
        "first change record",
    )
    registry_path.write_text(registry_text, encoding="utf-8")
    for markdown_path, markdown in markdown_updates:
        markdown_path.write_text(markdown, encoding="utf-8")
    subprocess.run(["python3", "tools/sync-version-registry.py"], cwd=ROOT, check=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat(), help="Publication date in YYYY-MM-DD format")
    parser.add_argument("--check", action="store_true", help="Print whether a packaged external document needs promotion")
    args = parser.parse_args()
    registry = load_registry()
    if args.check:
        print("true" if pending_document_specs(registry) else "false")
        return
    try:
        date.fromisoformat(args.date)
    except ValueError as error:
        raise SystemExit("--date must use YYYY-MM-DD") from error
    print("published" if promote_external_documents(args.date) else "no-unpublished-documents")


if __name__ == "__main__":
    main()
