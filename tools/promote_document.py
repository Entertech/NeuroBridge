"""Stage the promotion of an authorized document on its release PR branch."""

import argparse
from datetime import date
from hashlib import sha256
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

from tools.release_pipeline import ROOT, run
from tools.release_preflight import document_version
from tools.release_version_policy import document_body, draft_document, fixed_path, formal_document


def create_draft(key: str, version: str) -> str:
    import tomlkit

    registry_path = ROOT / "neurobridge/version_registry.toml"
    registry = tomlkit.parse(registry_path.read_text(encoding="utf-8"))
    current = formal_document(registry, key)
    if draft_document(registry, key) or document_version(version) <= document_version(current["version"]):
        raise ValueError("draft requires a newer version and no existing prerelease")
    path = Path("doc/tech") / f"{current['title']}_预发布.md"
    if (ROOT / path).exists():
        raise ValueError(f"draft already exists: {path}")
    draft = {
        "version": version, "status": "prerelease", "audience": "internal",
        "markdown_path": path.as_posix(), "pdf_artifact_name": f"{current['title']}_v{version}-预发布.pdf",
        "based_on_released_version": current["version"],
    }
    if key == "northbound":
        registry["documents"]["internal_prerelease"] = draft
        registry["protocol_lifecycle"]["prerelease_version"] = version
    else:
        registry.setdefault("document_prereleases", tomlkit.table())[key] = draft
    formal = (ROOT / current["markdown_path"]).read_text(encoding="utf-8")
    lines = formal.splitlines()
    lines[0] = f"# {current['title']} 预发布 v{version}"
    for index, line in enumerate(lines[1:], start=1):
        if line.startswith("## "):
            break
        if line.startswith(("版本：", "状态：")):
            lines[index] = ""
    lines[1:1] = ["", f"版本：v{version}（预发布）<br>", "状态：**仅供内部评审，尚未对 B 端发布**"]
    (ROOT / path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    registry_path.write_text(tomlkit.dumps(registry), encoding="utf-8")
    return path.as_posix()


def promote(key: str, version: str, published: str) -> str:
    import tomlkit

    if run("git", "branch", "--show-current") in ("", "master"):
        raise ValueError("promote the document on its release PR branch")
    date.fromisoformat(published)
    registry_path = ROOT / "neurobridge/version_registry.toml"
    registry = tomlkit.parse(registry_path.read_text(encoding="utf-8"))
    current = formal_document(registry, key)
    draft = draft_document(registry, key)
    if not draft or draft["version"] != version or document_version(version) <= document_version(current["version"]):
        raise ValueError("only a newer registered prerelease can be promoted")
    source = ROOT / draft["markdown_path"]
    destination = ROOT / fixed_path(current)
    old = ROOT / current["markdown_path"]
    if source == destination or source == old or (destination != old and destination.exists()):
        raise ValueError("prerelease and formal Markdown must use distinct paths")
    original = source.read_text(encoding="utf-8")
    lines = original.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("prerelease Markdown requires a title")
    first_section = next((index for index, line in enumerate(lines) if line.startswith("## ")), len(lines))
    header = [f"# {current['title']} v{version}", "", f"版本：v{version}<br>", f"日期：{published}<br>", "状态：已发布", ""]
    for line in lines[1:first_section]:
        if line.strip() and not re.match(r"^(?:(?:版本|日期|创建日期|发布日期|状态)[：:]|<!-- protocol-prerelease-change:)", line):
            header.append(line)
    body = "\n".join(lines[first_section:])
    body = re.sub(r"(?m)^<!-- protocol-prerelease-change:(?:start|end) -->\n?", "", body)
    body = re.sub(r"(?m)^## 预发布变更.*$", "## 本版变更", body)
    markdown = "\n".join(header + ["", body]).rstrip() + "\n"
    if document_body(markdown) == document_body(old.read_text(encoding="utf-8")):
        raise ValueError("document version cannot advance without a body change")

    section = registry["documents"][f"external_{key}"]
    scope = f"external_{key}_document"
    record_id = f"{published}-public-{key}-v{version}"
    lock_id = f"{key}-v{current['version']}-to-v{version}"
    entry = {
        "version": version, "status": "published", "markdown_path": destination.relative_to(ROOT).as_posix(),
        "pdf_artifact_name": f"{current['title']}_v{version}.pdf",
        "published_date": published, "markdown_sha256": sha256(markdown.encode("utf-8")).hexdigest(),
        "publication_record": record_id,
    }
    obsolete = {old}
    obsolete.update(ROOT / item["markdown_path"] for item in section.get("versions", []))
    if key == "northbound":
        section["current_version"] = version
        section["versions"] = tomlkit.aot()
        section["versions"].append(entry)
        lifecycle = registry["protocol_lifecycle"]
        lifecycle["released_version"] = version
        del lifecycle["prerelease_version"]
        del registry["documents"]["internal_prerelease"]
        for record in registry["change_records"]:
            if record["change_state"] == "unlocked":
                if record["target_external_release"] != version:
                    raise ValueError("unlocked protocol changes target a different version")
                record["change_state"] = "locked"
                record["release_lock"] = lock_id
    else:
        section.pop("versions", None)
        section.update(entry)
        del registry["document_prereleases"][key]
    registry["release_locks"].append({
        "id": lock_id, "scope": scope, "from_version": current["version"], "to_version": version,
        "status": "locked", "locked_by": record_id, "locked_at": published,
    })
    registry["change_records"].append({
        "id": record_id, "date": published, "scope": scope, "summary": f"发布{current['title']} v{version}。",
        "external_document_action": "explicit_user_authorized", "external_document_version": version,
        "change_state": "locked", "release_lock": lock_id, "reason": "经授权在发布 PR 晋升内部预发布稿。",
    })
    updated_registry = tomlkit.dumps(registry)
    readme = ROOT / "README.md"
    updated_readme = readme.read_text(encoding="utf-8")
    replacement = destination.relative_to(ROOT).as_posix()
    for path in obsolete:
        prior = path.relative_to(ROOT).as_posix()
        updated_readme = updated_readme.replace(prior, replacement).replace(quote(prior), quote(replacement))
    updated_readme = updated_readme.replace(f"{current['title']} v{current['version']}", f"{current['title']} v{version}")
    destination.write_text(markdown, encoding="utf-8")
    registry_path.write_text(updated_registry, encoding="utf-8")
    readme.write_text(updated_readme, encoding="utf-8")
    for path in (obsolete | {source}) - {destination}:
        path.unlink(missing_ok=True)
    return entry["markdown_path"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", default="northbound")
    parser.add_argument("--version", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    print(promote(args.document, args.version.removeprefix("v"), args.date))
    subprocess.run([sys.executable, "tools/sync-version-registry.py"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
