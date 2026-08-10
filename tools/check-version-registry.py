#!/usr/bin/env python3
"""CI gate for the single source of truth of versions and public documents."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.parse import quote

from external_protocol_registry import (
    ROOT,
    external_protocol_catalog,
    load_registry,
    select_external_protocol,
    select_prerelease_protocol,
)


def fail(message: str) -> None:
    raise SystemExit(f"version-registry check failed: {message}")


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    registry = load_registry()
    policy = registry["change_policy"]
    lifecycle = registry["protocol_lifecycle"]
    external_catalog = external_protocol_catalog(registry)
    external = select_external_protocol(registry)
    prerelease = select_prerelease_protocol(registry)
    integration = registry["documents"]["integration_plan"]
    capture_package = registry["documents"].get("external_capture_package")
    ssh_operations = registry["documents"].get("external_ssh_operations")
    wire_version = registry["northbound_wire_protocol"]["version"]
    application_version = registry["application"]["version"]

    if policy["external_documents_require_explicit_user_request"] is not True:
        fail("external document updates must require an explicit user request")
    if policy["default_external_document_action"] != "record_only":
        fail("the default external document action must be record_only")
    if external_catalog["audience"] != "b_side":
        fail("external_northbound must be a B-side document catalog")
    if external_catalog["current_version"] != lifecycle["released_version"]:
        fail("internal and external protocol lifecycle must share the released version")
    if prerelease["version"] != lifecycle["prerelease_version"]:
        fail("internal and external protocol lifecycle must share the prerelease version")
    if prerelease["based_on_released_version"] != lifecycle["released_version"]:
        fail("prerelease must be based on the current released version")
    known_versions = [protocol["version"] for protocol in external_catalog["versions"]]
    if len(known_versions) != len(set(known_versions)):
        fail("every external protocol version must be stored exactly once")
    if external_catalog["current_version"] not in known_versions:
        fail("current external protocol version must exist in the catalog")
    artifact_names = [protocol["pdf_artifact_name"] for protocol in external_catalog["versions"]]
    if len(artifact_names) != len(set(artifact_names)):
        fail("every external protocol version must have a unique PDF artifact name")
    for versioned_protocol in external_catalog["versions"]:
        if versioned_protocol["status"] != "published":
            fail("stored external protocol versions must be published and locked")
        publication_records = [
            record for record in registry["change_records"] if record["id"] == versioned_protocol["publication_record"]
        ]
        if len(publication_records) != 1:
            fail("each external protocol version must reference exactly one persisted publication record")
        publication_record = publication_records[0]
        if publication_record["external_document_action"] != "explicit_user_authorized":
            fail("published external documents require explicit_user_authorized record")
        if publication_record["external_document_version"] != versioned_protocol["version"]:
            fail("publication record version does not match the stored external document")
        if publication_record["date"] != versioned_protocol["published_date"]:
            fail("publication record date does not match the stored external document")
        if publication_record["change_state"] != "locked":
            fail("published external documents must have a locked publication record")
        markdown = ROOT / versioned_protocol["markdown_path"]
        if not markdown.is_file():
            fail("every published external Markdown must exist")
        if digest(markdown) != versioned_protocol["markdown_sha256"]:
            fail("external document changed without a persisted version-registry update")
        if Path(versioned_protocol["pdf_artifact_name"]).name != versioned_protocol["pdf_artifact_name"]:
            fail("external PDFs must be uploaded as flat CI artifact filenames")

    locks = {lock["id"]: lock for lock in registry["release_locks"]}
    publication_record = next(record for record in registry["change_records"] if record["id"] == external["publication_record"])
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
            if record["target_external_release"] != prerelease["version"]:
                fail("unlocked changes must target the unified prerelease version")
        else:
            fail("change records must be locked or unlocked")

    if capture_package:
        if capture_package["audience"] != "data_recipient":
            fail("external capture package document must target data recipients")
        capture_markdown = ROOT / capture_package["markdown_path"]
        if not capture_markdown.is_file():
            fail("external capture package Markdown must exist")
        if digest(capture_markdown) != capture_package["markdown_sha256"]:
            fail("external capture package changed without a persisted version-registry update")
        capture_text = capture_markdown.read_text(encoding="utf-8")
        if capture_text.splitlines()[0] != f'# {capture_package["title"]} v{capture_package["version"]}':
            fail("external capture package title does not match the version registry")
        if f'版本：v{capture_package["version"]}' not in capture_text or f'日期：{capture_package["published_date"]}' not in capture_text:
            fail("external capture package version or date does not match the version registry")
        capture_records = [record for record in registry["change_records"] if record["id"] == capture_package["publication_record"]]
        if len(capture_records) != 1:
            fail("external capture package must reference one publication record")
        capture_record = capture_records[0]
        if capture_record["external_document_action"] != "explicit_user_authorized" or capture_record["change_state"] != "locked":
            fail("external capture package requires an explicitly authorized locked publication record")
        capture_lock = locks.get(capture_record.get("release_lock"))
        if capture_lock is None or capture_lock["scope"] != "external_capture_package_document" or capture_lock["to_version"] != capture_package["version"]:
            fail("external capture package publication lock is invalid")

    if ssh_operations and ssh_operations["status"] == "published":
        ssh_markdown = ROOT / ssh_operations["markdown_path"]
        required_ssh_fields = ("published_date", "markdown_sha256", "publication_record")
        if any(field not in ssh_operations for field in required_ssh_fields):
            fail("published SSH operations document requires publication metadata")
        if not ssh_markdown.is_file() or digest(ssh_markdown) != ssh_operations["markdown_sha256"]:
            fail("published SSH operations Markdown hash is invalid")
        ssh_records = [record for record in registry["change_records"] if record["id"] == ssh_operations["publication_record"]]
        if len(ssh_records) != 1 or ssh_records[0]["external_document_action"] != "explicit_user_authorized" or ssh_records[0]["change_state"] != "locked":
            fail("published SSH operations document requires an explicitly authorized locked record")
        ssh_lock = locks.get(ssh_records[0].get("release_lock"))
        if ssh_lock is None or ssh_lock["scope"] != "external_ssh_operations_document" or ssh_lock["to_version"] != ssh_operations["version"]:
            fail("published SSH operations publication lock is invalid")

    tracked_pdfs = subprocess.run(
        ["git", "ls-files", "--", "doc/tech/*.pdf"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    if tracked_pdfs:
        fail("protocol PDFs must be CI artifacts, not repository files")

    prerelease_markdown = ROOT / prerelease["markdown_path"]
    if not prerelease_markdown.is_file():
        fail("the active prerelease Markdown must exist")
    prerelease_text = prerelease_markdown.read_text(encoding="utf-8")
    expected_prerelease_title = f'# {external_catalog["title"]} 预发布 v{prerelease["version"]}'
    if prerelease_text.splitlines()[0] != expected_prerelease_title:
        fail("prerelease Markdown title does not match the unified protocol lifecycle")
    if f'版本：v{prerelease["version"]}（预发布）' not in prerelease_text:
        fail("prerelease Markdown version does not match the unified protocol lifecycle")
    if "仅供内部评审，尚未对 B 端发布" not in prerelease_text:
        fail("prerelease Markdown must remain internal until promotion")
    legacy_internal_protocol = ROOT / "doc/tech/头环蓝牙网关北向网络协议_v0.1.md"
    if legacy_internal_protocol.exists():
        fail("only the latest internal prerelease protocol document may be retained")

    markdown = ROOT / external["markdown_path"]
    markdown_text = markdown.read_text(encoding="utf-8")
    expected_title = f'# {external_catalog["title"]} v{external["version"]}'
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
    if f'{external_catalog["title"]} v{external["version"]}' not in readme or not any(path in readme for path in markdown_link_paths):
        fail("README must link B-side users to the registry-selected external protocol")
    if f'v{integration["version"]}' not in (ROOT / integration["markdown_path"]).read_text(encoding="utf-8"):
        fail("integration plan version does not match the registry")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if pyproject["project"]["version"] != application_version:
        fail("run tools/sync-version-registry.py after changing the application version")

    generated_client_version = (
        "// Generated from neurobridge/version_registry.toml by tools/sync-version-registry.py.\n"
        f'window.NEUROBRIDGE_VERSION = Object.freeze({{ protocolVersion: "{wire_version}" }});\n'
    )
    if (ROOT / "web" / "b-client-test" / "version.js").read_text(encoding="utf-8") != generated_client_version:
        fail("run tools/sync-version-registry.py after changing the version registry")
    change_log = (ROOT / policy["change_log_path"]).read_text(encoding="utf-8")
    if f'v{publication_lock["from_version"]} → v{publication_lock["to_version"]}' not in change_log:
        fail("version change log must include the locked release interval")
    if "## 未锁定变更" not in change_log:
        fail("version change log must include the unlocked change section")


if __name__ == "__main__":
    main()
