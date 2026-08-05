#!/usr/bin/env python3
"""CI gate for the single source of truth of versions and public documents."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import tomllib
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "neurobridge" / "version_registry.toml"


def fail(message: str) -> None:
    raise SystemExit(f"version-registry check failed: {message}")


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    registry = tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    policy = registry["change_policy"]
    external = registry["documents"]["external_northbound"]
    internal = registry["documents"]["internal_northbound"]
    integration = registry["documents"]["integration_plan"]
    wire_version = registry["northbound_wire_protocol"]["version"]
    application_version = registry["application"]["version"]

    if policy["external_documents_require_explicit_user_request"] is not True:
        fail("external document updates must require an explicit user request")
    if policy["default_external_document_action"] != "record_only":
        fail("the default external document action must be record_only")
    if external["audience"] != "b_side" or external["status"] != "published":
        fail("external_northbound must be the published B-side document")
    publication_records = [record for record in registry["change_records"] if record["id"] == external["publication_record"]]
    if len(publication_records) != 1:
        fail("external_northbound must reference exactly one persisted publication record")
    publication_record = publication_records[0]
    if publication_record["external_document_action"] != "explicit_user_authorized":
        fail("published external documents require explicit_user_authorized record")
    if publication_record["external_document_version"] != external["version"]:
        fail("publication record version does not match the current external document")
    if publication_record["date"] != external["published_date"]:
        fail("publication record date does not match the current external document")
    if publication_record["change_state"] != "locked":
        fail("published external documents must have a locked publication record")

    locks = {lock["id"]: lock for lock in registry["release_locks"]}
    publication_lock = locks.get(publication_record["release_lock"])
    if publication_lock is None or publication_lock["status"] != "locked":
        fail("published external documents must reference a locked release interval")
    if publication_lock["to_version"] != external["version"]:
        fail("locked release interval must end at the published external version")
    for record in registry["change_records"]:
        if record["change_state"] == "locked":
            if record.get("release_lock") not in locks:
                fail("locked change records must reference a release lock")
        elif record["change_state"] == "unlocked":
            if record["external_document_action"] != "record_only":
                fail("unlocked changes must not modify external documents")
            if record["base_locked_release"] != external["version"]:
                fail("unlocked changes must be anchored to the current locked release")
            if record["target_external_release"] != "unassigned":
                fail("unlocked changes must not claim an unpublished external version")
        else:
            fail("change records must be locked or unlocked")

    markdown = ROOT / external["markdown_path"]
    pdf = ROOT / external["pdf_path"]
    if not markdown.is_file() or not pdf.is_file():
        fail("published external Markdown and PDF must both exist")
    if digest(markdown) != external["markdown_sha256"] or digest(pdf) != external["pdf_sha256"]:
        fail("external document changed without a persisted version-registry update")

    markdown_text = markdown.read_text(encoding="utf-8")
    expected_title = f'# {external["title"]} v{external["version"]}'
    if markdown_text.splitlines()[0] != expected_title:
        fail("external Markdown title does not match the version registry")
    if f'版本：v{external["version"]}' not in markdown_text:
        fail("external Markdown version does not match the version registry")
    if f'日期：{external["published_date"]}' not in markdown_text:
        fail("external Markdown date does not match the version registry")
    forbidden_patterns = (
        r"蓝牙",
        r"\bBLE\b",
        r"\bFlowtime\b",
        r"\bEnter-Biomodule\b",
        r"0000ff[0-9a-f-]*",
        r"\bFF[0-9A-F]{2}\b",
        r"设备扫描",
        r"\bRSSI\b",
        r"连接策略",
        r"\bJSONL\b",
    )
    if any(re.search(pattern, markdown_text, flags=re.IGNORECASE) for pattern in forbidden_patterns):
        fail("external Markdown contains gateway implementation details")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    markdown_link_paths = (external["markdown_path"], quote(external["markdown_path"]))
    if f'{external["title"]} v{external["version"]}' not in readme or not any(path in readme for path in markdown_link_paths):
        fail("README must link B-side users to the registry-selected external protocol")
    if f'版本：v{internal["version"]}' not in (ROOT / internal["markdown_path"]).read_text(encoding="utf-8"):
        fail("internal northbound document version does not match the registry")
    if f'v{integration["version"]}' not in (ROOT / integration["markdown_path"]).read_text(encoding="utf-8"):
        fail("integration plan version does not match the registry")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if pyproject["project"]["version"] != application_version:
        fail("run tools/sync-version-registry.py after changing the application version")

    generated_client_version = (
        "// Generated from neurobridge/version_registry.toml by tools/sync-version-registry.py.\n"
        f'window.NEUROBRIDGE_VERSION = Object.freeze({{ protocolVersion: "{wire_version}" }});\n'
    )
    if (ROOT / "tools" / "b-client-test" / "version.js").read_text(encoding="utf-8") != generated_client_version:
        fail("run tools/sync-version-registry.py after changing the version registry")
    change_log = (ROOT / policy["change_log_path"]).read_text(encoding="utf-8")
    if f'v{publication_lock["from_version"]} → v{publication_lock["to_version"]}' not in change_log:
        fail("version change log must include the locked release interval")
    if "## 未锁定变更" not in change_log:
        fail("version change log must include the unlocked change section")


if __name__ == "__main__":
    main()
