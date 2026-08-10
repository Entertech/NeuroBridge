#!/usr/bin/env python3
"""Promote current unpublished external documents in a release-branch checkout."""

from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
from pathlib import Path
import subprocess

from external_protocol_registry import ROOT, load_registry


def replace_once(text: str, old: str, new: str, description: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one {description} marker.")
    return text.replace(old, new, 1)


def publish_ssh_operations(release_date: str) -> bool:
    """Promote the only current unpublished external document without changing its version."""
    registry_path = ROOT / "neurobridge" / "version_registry.toml"
    registry = load_registry()
    document = registry["documents"]["external_ssh_operations"]
    if document["status"] == "published":
        return False
    if document["status"] != "unpublished":
        raise SystemExit(f"Unsupported SSH operations document status: {document['status']}")

    markdown_path = ROOT / document["markdown_path"]
    markdown = markdown_path.read_text(encoding="utf-8")
    markdown = replace_once(markdown, "状态：未发布（评审稿）", "状态：已发布", "SSH document status")
    markdown = replace_once(
        markdown,
        "未发布版本仅供评审和现场准备使用，不能作为验收或正式交付依据。",
        "本版本已作为正式交付文档发布；现场验收仍应以双方确认的部署配置和验收记录为准。",
        "SSH document unpublished notice",
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    digest = sha256(markdown_path.read_bytes()).hexdigest()

    publication_record = f"{release_date}-public-ssh-operations-v{document['version']}"
    lock_id = f"ssh-operations-v0.0-to-v{document['version']}"
    registry_text = registry_path.read_text(encoding="utf-8")
    registry_text = replace_once(registry_text, 'status = "unpublished"\naudience = "operator"', 'status = "published"\naudience = "operator"', "SSH registry status")
    registry_text = replace_once(
        registry_text,
        f'created_date = "{document["created_date"]}"\nreason = "{document["reason"]}"',
        "\n".join(
            [
                f'created_date = "{document["created_date"]}"',
                f'published_date = "{release_date}"',
                f'markdown_sha256 = "{digest}"',
                f'publication_record = "{publication_record}"',
                'reason = "经手工发布工作流生成完整 PDF ZIP Artifact 后标记为已发布；版本保持不变。"',
            ]
        ),
        "SSH registry publication metadata",
    )
    insert_before = "[change_policy]"
    release_lock = "\n".join(
        [
            "[[release_locks]]",
            f'id = "{lock_id}"',
            'scope = "external_ssh_operations_document"',
            'from_version = "0.0"',
            f'to_version = "{document["version"]}"',
            'status = "locked"',
            f'locked_by = "{publication_record}"',
            f'locked_at = "{release_date}"',
            "",
            "",
        ]
    )
    registry_text = replace_once(registry_text, insert_before, release_lock + insert_before, "change policy heading")
    record = "\n".join(
        [
            "[[change_records]]",
            f'id = "{publication_record}"',
            f'date = "{release_date}"',
            'scope = "external_ssh_operations_document"',
            'summary = "发布头环数据网关 SSH 运维操作指南。"',
            'external_document_action = "explicit_user_authorized"',
            f'external_document_version = "{document["version"]}"',
            'change_state = "locked"',
            f'release_lock = "{lock_id}"',
            'reason = "手工发布工作流已生成并上传包含三份对外文档 PDF 与 B 端联调网页的 ZIP Artifact。"',
            "",
            "",
        ]
    )
    registry_text = replace_once(registry_text, "[[change_records]]", record + "[[change_records]]", "first change record")
    registry_path.write_text(registry_text, encoding="utf-8")
    subprocess.run(["python3", "tools/sync-version-registry.py"], cwd=ROOT, check=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat(), help="Publication date in YYYY-MM-DD format")
    parser.add_argument("--check", action="store_true", help="Print whether an external document still needs publication")
    args = parser.parse_args()
    if args.check:
        registry = load_registry()
        print("true" if registry["documents"]["external_ssh_operations"]["status"] == "unpublished" else "false")
        return
    try:
        date.fromisoformat(args.date)
    except ValueError as error:
        raise SystemExit("--date must use YYYY-MM-DD") from error
    print("published" if publish_ssh_operations(args.date) else "no-unpublished-documents")


if __name__ == "__main__":
    main()
