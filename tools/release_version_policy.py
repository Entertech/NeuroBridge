"""Compare independently versioned release objects against a PR baseline."""

from pathlib import PurePosixPath
import re

from tools.external_protocol_registry import select_external_protocol
from tools.release_pipeline import matrix, release_config_at, run, system_version
from tools.release_scope import advanced_systems, changed_systems, registry_at


DOCUMENT_VERSION_FIELDS = (
    "version", "status", "markdown_path", "markdown_sha256", "pdf_artifact_name",
    "created_date", "published_date", "publication_record",
)


def formal_document(registry: dict, key: str) -> dict:
    section = registry["documents"][f"external_{key}"]
    return section | select_external_protocol(registry) if key == "northbound" else section


def document_history(registry: dict, key: str) -> dict[str, dict]:
    section = registry["documents"][f"external_{key}"]
    history = {entry["version"]: entry for entry in section.get("versions", [])}
    if section.get("status") == "published":
        history.setdefault(section["version"], section)
    return history


def draft_document(registry: dict, key: str) -> dict | None:
    if key == "northbound":
        return registry["documents"].get("internal_prerelease")
    return registry.get("document_prereleases", {}).get(key)


def versioned_path(doc: dict, version: str) -> str:
    return str(PurePosixPath(doc["markdown_path"]).with_name(f"{doc['title']}_v{version}.md"))


def document_body(text: str) -> str:
    # Delivery metadata does not justify a new version. Strip only the header
    # block so status, dates and versions discussed in the body still count.
    lines = text.splitlines()
    first_section = next((index for index, line in enumerate(lines) if line.startswith("## ")), len(lines))
    lines = [
        line for index, line in enumerate(lines)
        if not (index < first_section and re.match(
            r"^(?:# |(?:版本|日期|创建日期|发布日期|状态|适用对象)[：:])", line.strip()
        ))
    ]
    visible = re.sub(r"<!--.*?-->", "", "\n".join(lines), flags=re.S)
    return "\n".join(line.rstrip() for line in visible.splitlines() if line.strip())


def check_versions(base: str, head: str) -> None:
    current, previous = registry_at(head), registry_at(base)
    if "software_versions" in previous and "software_versions" not in current:
        raise ValueError("independent software versions cannot be removed")
    if "software_versions" in current:
        config = release_config_at(head)
        baselines = {system_version(target): base for target in matrix(config)}
        advanced_systems(current, baselines, changed_systems(head, baselines, config.get("selection", {})))
    check_document_versions(base, head, previous, current)


def check_document_versions(base: str, head: str, previous: dict, current: dict) -> None:
    from tools.release_preflight import document_version

    paths = set(run("git", "ls-tree", "-r", "--name-only", "-z", head).split("\0"))
    for section in previous.get("documents", {}):
        if not section.startswith("external_"):
            continue
        key = section.removeprefix("external_")
        old = formal_document(previous, key)
        new = formal_document(current, key)
        history = document_history(current, key)
        for version, historical in document_history(previous, key).items():
            if historical["status"] != "published":
                continue
            saved = history.get(version)
            path = historical["markdown_path"]
            if (saved is None or path not in paths
                    or any(historical.get(field) != saved.get(field) for field in DOCUMENT_VERSION_FIELDS)
                    or run("git", "rev-parse", f"{base}:{path}") != run("git", "rev-parse", f"{head}:{path}")):
                raise ValueError(f"{key} published Markdown or metadata changed for v{version}")
        if old["status"] != "published":
            continue
        if new["status"] != "published":
            raise ValueError(f"{key} published document status cannot be reverted")
        old_ref = f"{base}:{old['markdown_path']}"
        new_ref = f"{head}:{new['markdown_path']}"
        if document_version(new["version"]) < document_version(old["version"]):
            raise ValueError(f"{key} document version regressed")
        if old["version"] == new["version"]:
            if any(old.get(field) != new.get(field) for field in DOCUMENT_VERSION_FIELDS):
                raise ValueError(f"{key} published Markdown changed without a new document version")
            continue
        before = run("git", "show", old_ref)
        after = run("git", "show", new_ref)
        if document_body(before) == document_body(after):
            raise ValueError(f"{key} document version advanced without a body change")
        expected = versioned_path(old, new["version"])
        if new["markdown_path"] != expected:
            raise ValueError(f"{key} formal Markdown must use {expected}")
        draft = draft_document(previous, key)
        current_draft = draft_document(current, key)
        if not draft or draft["version"] != new["version"]:
            raise ValueError(f"{key} release must promote its registered prerelease")
        if current_draft or draft["markdown_path"] in paths:
            raise ValueError(f"{key} release must remove the promoted prerelease")
