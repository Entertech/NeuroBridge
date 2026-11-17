"""Report the package and document inputs from an exact source snapshot."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

from .external_document_package import ExternalDocument, collect_external_documents
from .release_pipeline import matrix, release_config_at, run, save_json, select_targets, system_version


REGISTRY_PATH = "neurobridge/version_registry.toml"


def document_version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))+", value):
        raise ValueError(f"Invalid external document version: {value!r}")
    return tuple(map(int, value.split(".")))


def document_impacts(registry: dict, documents: list[ExternalDocument], platforms: set[str],
                     *, bundled: list[ExternalDocument] | None = None) -> list[dict]:
    by_key = {doc.key: doc for doc in documents}
    planned = {doc.key: doc for doc in bundled} if bundled is not None else by_key
    impacts = []
    for change in registry.get("change_records", []):
        if "required_document" not in change:
            continue
        requirement = change["required_document"]
        change_id = change["id"]
        if not isinstance(requirement, dict) or set(requirement) - {"key", "minimum_version", "platforms", "pull_request"}:
            raise ValueError(f"{change_id}: invalid required_document")
        key = requirement.get("key")
        doc = by_key.get(key) if isinstance(key, str) else None
        if doc is None or doc.delivery == "review_only":
            raise ValueError(f"{change_id}: required_document must identify a deliverable external document")
        applicable = set(doc.platforms) if doc.delivery == "platform_bound" else platforms
        scoped = requirement.get("platforms", sorted(applicable))
        if (not isinstance(scoped, list) or not scoped or any(not isinstance(platform, str) for platform in scoped)
                or set(scoped) - (applicable & platforms)):
            raise ValueError(f"{change_id}: invalid required_document platforms")
        minimum = requirement.get("minimum_version")
        required_version = document_version(minimum)
        selected = planned.get(key)
        satisfied = bool(selected and selected.status == "published" and document_version(selected.version) >= required_version)
        pull_request = requirement.get("pull_request")
        if pull_request is not None and (type(pull_request) is not int or pull_request < 1):
            raise ValueError(f"{change_id}: invalid required_document pull_request")
        impacts.append({
            "changeId": change_id, "document": doc.key, "title": doc.title, "requiredVersion": minimum,
            "plannedVersion": selected.version if selected else None,
            "status": selected.status if selected else "not bundled", "platforms": scoped,
            "pullRequest": pull_request, "satisfied": satisfied,
        })
    return impacts


def formal_document_blockers(plan: dict) -> list[str]:
    selected = {target["platform"] for target in plan.get("packages", [])}
    return list(plan["documentBlockers"]) + [
        f"{entry['title']} v{entry['version']} is awaiting publication without a registered document obligation."
        for entry in plan.get("unregisteredDocumentUpdates", []) if not selected.isdisjoint(entry["platforms"])
    ]


def software_documents(registry: dict, documents: list[ExternalDocument], file_exists) -> tuple[list[ExternalDocument], dict]:
    from .document_release import document_tag, prior_public_versions, verify
    from .publish_release import release_info
    from .release_version_policy import fixed_path, formal_document

    legacy_tag = registry.get("document_delivery", {}).get("legacy_software_tag")
    if legacy_tag is None:
        return collect_external_documents(registry, file_exists=file_exists, published_only=True), {}
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", legacy_tag):
        raise ValueError("invalid legacy software tag for document delivery")
    legacy = tomllib.loads(run("git", "show", f"refs/tags/{legacy_tag}^{{commit}}:{REGISTRY_PATH}"))
    baseline = {doc.key: doc for doc in collect_external_documents(legacy, file_exists=lambda _: True)}
    selected, evidence = [], {}
    with tempfile.TemporaryDirectory(prefix="neurobridge-doc-preflight-") as temporary:
        for doc in documents:
            section = registry["documents"][f"external_{doc.key}"]
            history = {entry["version"]: entry for entry in section.get("versions", [])}
            if "version" in section:
                history[section["version"]] = section
            if doc.markdown_path == fixed_path(formal_document(registry, doc.key)):
                history.update(prior_public_versions(doc.key, known=history))
            previous = baseline.get(doc.key)
            for version in sorted(history, key=document_version, reverse=True):
                item = history[version]
                if item["status"] != "published" or (previous and document_version(version) < document_version(previous.version)):
                    continue
                tag = document_tag(doc.key, version)
                info = release_info(tag)
                if not info or info["isDraft"] or info.get("isPrerelease"):
                    continue
                receipt = verify(doc.key, version, Path(temporary) / doc.key)
                if receipt["markdownSha256"] != item["markdown_sha256"] or receipt["pdf"]["fileName"] != item["pdf_artifact_name"]:
                    raise ValueError(f"formal document {tag} differs from the approved registry")
                if "taggedCommit" in item and receipt["sourceCommit"] != item["taggedCommit"]:
                    raise ValueError(f"formal document {tag} differs from its Git snapshot")
                selected.append(ExternalDocument(
                    doc.key, doc.title, version, "published", doc.delivery, doc.platforms,
                    item["markdown_path"], item["pdf_artifact_name"],
                ))
                evidence[doc.key] = {"documentTag": tag, "pdfSha256": receipt["pdf"]["sha256"]}
                break
            else:
                if previous:
                    selected.append(replace(previous, title=doc.title, delivery=doc.delivery, platforms=doc.platforms))
                    legacy_section = legacy["documents"][f"external_{doc.key}"]
                    prior_record = legacy_section if legacy_section.get("version") == previous.version else next(
                        entry for entry in legacy_section["versions"] if entry["version"] == previous.version
                    )
                    evidence[doc.key] = {"markdownSha256": prior_record["markdown_sha256"],
                                         "markdownRef": f"refs/tags/{legacy_tag}^{{commit}}"}
    return selected, evidence


def release_plan(head: str, base: str, *, software: bool = False) -> dict:
    source = run("git", "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
    baseline = run("git", "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    registry = tomllib.loads(run("git", "show", f"{source}:{REGISTRY_PATH}"))
    old_registry = tomllib.loads(run("git", "show", f"{baseline}:{REGISTRY_PATH}"))
    config = release_config_at(source)

    def file_exists(path: str) -> bool:
        try:
            return run("git", "cat-file", "-t", f"{source}:{path}") == "blob"
        except subprocess.CalledProcessError:
            return False

    documents = collect_external_documents(registry, file_exists=file_exists)
    bundled, evidence = software_documents(registry, documents, file_exists) if software else (documents, {})
    packages = matrix(config)
    from tools.release_pipeline import software_versions, system_version
    versions = software_versions(registry, (system_version(target) for target in packages))
    previous_sections = old_registry["documents"]
    previous_versions = {
        name.removeprefix("external_"): section.get("version")
        for name, section in previous_sections.items() if name.startswith("external_")
    }
    previous_versions["northbound"] = previous_sections["external_northbound"]["current_version"]
    wire = registry["northbound_wire_protocol"]["version"]
    previous_wire = old_registry["northbound_wire_protocol"]["version"]
    released = registry["protocol_lifecycle"]["released_version"]
    warnings = []
    blockers = [f"{doc.title} v{doc.version} is still unpublished." for doc in bundled if doc.status != "published"]
    impacts = document_impacts(registry, documents, {target["platform"] for target in packages}, bundled=bundled)
    if software:
        for impact in impacts:
            document_tag = evidence.get(impact["document"], {}).get("documentTag")
            impact["documentTag"] = document_tag
            impact["satisfied"] = impact["satisfied"] and bool(document_tag)
    shipped = {doc.key: doc.version for doc in bundled}
    unregistered = []
    if software:
        for doc in documents:
            if doc.delivery == "review_only" or shipped.get(doc.key) == doc.version:
                continue
            applicable = set(doc.platforms) or {target["platform"] for target in packages}
            registered = {
                platform for impact in impacts
                if impact["document"] == doc.key and document_version(impact["requiredVersion"]) >= document_version(doc.version)
                for platform in impact["platforms"]
            }
            if applicable - registered:
                unregistered.append({"title": doc.title, "version": doc.version, "platforms": sorted(applicable - registered)})
    if wire != previous_wire and released == previous_versions["northbound"]:
        warnings.append("Wire protocol changed while the published northbound document version stayed the same.")
        prior = {item["id"]: item.get("required_document") for item in old_registry.get("change_records", [])}
        if software and not any(
            change.get("required_document", {}).get("key") == "northbound"
            and change["required_document"] != prior.get(change["id"])
            for change in registry.get("change_records", [])
        ):
            blockers.append("Wire protocol changed without a new or updated northbound document obligation.")
    return {
        "sourceCommit": source,
        "baseCommit": baseline,
        "baseApplicationVersion": old_registry["application"]["version"],
        "applicationVersion": registry["application"]["version"],
        "softwareVersions": versions,
        "packages": packages,
        "generatedDocuments": ["bundle-directory-guide.pdf"],
        "documents": [
            {**asdict(doc), "baseVersion": previous_versions.get(doc.key), **evidence.get(doc.key, {})}
            for doc in bundled
        ],
        "documentImpacts": impacts,
        "unregisteredDocumentUpdates": unregistered,
        "northbound": {
            "wireVersion": wire,
            "baseWireVersion": previous_wire,
            "releasedDocumentVersion": released,
            "prereleaseDocumentVersion": registry["protocol_lifecycle"].get("prerelease_version"),
        },
        "documentBlockers": blockers,
        "warnings": warnings,
    }


def render_summary(plan: dict, mode: str, version_gate: str) -> str:
    lines = [
        "## Release preflight", "",
        f"Source: `{plan['sourceCommit']}` · baseline: `{plan['baseCommit']}`",
        f"Application: `{plan['baseApplicationVersion']}` → `{plan['applicationVersion']}` · document mode: `{mode}`",
        f"Version gate: `{version_gate}`", "",
        f"### Installers ({len(plan['packages'])})", "",
        *(f"- `{target['id']}` · `{target['runner']}`" for target in plan["packages"]),
        "", "### Documents", "",
        "| Document | Baseline | Planned | Status | Delivery |",
        "|---|---|---|---|---|",
    ]
    selected_platforms = {target["platform"] for target in plan["packages"]}
    for doc in plan["documents"]:
        title = doc["title"].replace("|", r"\|")
        delivery = {"always": "Shared", "platform_bound": ", ".join(doc["platforms"]),
                    "review_only": "Review artifact only"}[doc["delivery"]]
        if doc["delivery"] == "platform_bound" and selected_platforms.isdisjoint(doc["platforms"]):
            delivery += " (not selected)"
        lines.append(f"| {title} | {doc['baseVersion'] or '—'} | {doc['version']} | {doc['status']} | {delivery} |")
    if plan.get("documentImpacts"):
        lines.extend(["", "### Required document updates", ""])
        for impact in plan["documentImpacts"]:
            status = "version available in bundle" if impact["satisfied"] else "pending document publication"
            if selected_platforms.isdisjoint(impact["platforms"]):
                status = "outside selected scope"
            elif impact.get("satisfied") and impact.get("documentTag"):
                status = f"formal PDF from {impact['documentTag']}"
            reference = f" · PR #{impact['pullRequest']}" if impact["pullRequest"] else ""
            bundled = f"v{impact['plannedVersion']}" if impact["plannedVersion"] else "—"
            lines.append(f"- `{impact['changeId']}` · {impact['title']} · required v{impact['requiredVersion']}"
                         f" · bundled {bundled} · {status}{reference}")
    protocol = plan["northbound"]
    lines.extend([
        "", f"Wire protocol: `{protocol['baseWireVersion']}` → `{protocol['wireVersion']}`.",
        f"Internal northbound prerelease: `{protocol['prereleaseDocumentVersion']}`.",
        "Generated in the delivery ZIP: " + ", ".join(plan["generatedDocuments"]) + ".", "",
        *(f"- Formal document gate: {item}" for item in formal_document_blockers(plan)),
        *(f"- Review: {item}" for item in plan["warnings"]),
    ])
    if "gates" in plan:
        gates = plan["gates"]
        secrets = gates["actionsSecrets"]
        candidate = gates["candidate"]
        merge_policy = gates["mergePolicy"]
        scope = plan["packageScope"]
        lines.extend([
            "", f"### Source train `{plan['version']}`", "",
            f"- System scope: {scope['mode']} · " + (', '.join(scope["systems"]) or "no affected system"),
            *(f"  - Planned software tag: `{tag}`." for tag in plan.get("systemTags", {}).values()),
            *(f"  - `{system}`: compared against `{base}`."
              for system, base in scope["baseCommits"].items()),
            f"- Actions Secrets: {secrets['status']}.",
            *(f"  - Could not confirm `{name}`." for name in secrets["notConfirmed"]),
            f"- Repository merge policy: {merge_policy['status']}. {merge_policy.get('reason', '')}".rstrip(),
            *(f"- Branch protection `{branch}`: {check['status']}. {check.get('reason', '')}".rstrip()
              for branch, check in gates["branchProtection"].items()),
            "- Required code checks: " + ", ".join(gates["requiredCodeChecks"]) + ".",
        ])
        if "sourceCommit" in candidate:
            lines.append(f"- Recorded candidate `{candidate['sourceCommit']}`: code checks {candidate['codeChecks']['status']}, human review {candidate['humanReview']}.")
            if "reason" in candidate["codeChecks"]:
                lines.append(f"  - {candidate['codeChecks']['reason']}")
        else:
            lines.append(f"- {candidate['reason']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True)
    parser.add_argument("--gate", required=True, type=Path)
    parser.add_argument("--mode", choices=("candidate", "publish"), required=True)
    parser.add_argument("--systems", default="")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()

    gate = json.loads(args.gate.read_text(encoding="utf-8"))
    plan = release_plan(args.head, gate["baseCommit"], software=args.mode == "publish")
    if args.systems:
        plan["packages"] = select_targets(plan["packages"], args.systems)
        plan["systems"] = list(dict.fromkeys(map(system_version, plan["packages"])))
    if plan["sourceCommit"] != run("git", "rev-parse", "HEAD") or plan["applicationVersion"] != gate["applicationVersion"]:
        parser.error("release plan and version gate must use the same checked-out source")
    blockers = formal_document_blockers(plan)
    plan["formalDocumentBlockers"] = blockers
    plan["documentMode"] = args.mode
    save_json(args.output, plan)
    with args.summary.open("a", encoding="utf-8") as summary:
        summary.write(render_summary(plan, args.mode, gate["reason"]))
    if gate["shouldRelease"] and args.mode == "publish" and blockers:
        for reason in blockers:
            print(reason)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
