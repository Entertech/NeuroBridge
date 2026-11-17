"""Report the package and document inputs from an exact source snapshot."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import subprocess
import tomllib

from .external_document_package import ExternalDocument, collect_external_documents
from .release_pipeline import matrix, release_config_at, run, save_json, select_targets, system_version


REGISTRY_PATH = "neurobridge/version_registry.toml"


def document_version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))+", value):
        raise ValueError(f"Invalid external document version: {value!r}")
    return tuple(map(int, value.split(".")))


def document_impacts(registry: dict, documents: list[ExternalDocument], platforms: set[str]) -> list[dict]:
    by_key = {doc.key: doc for doc in documents}
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
        satisfied = document_version(doc.version) >= document_version(minimum) and doc.status == "published"
        pull_request = requirement.get("pull_request")
        if pull_request is not None and (type(pull_request) is not int or pull_request < 1):
            raise ValueError(f"{change_id}: invalid required_document pull_request")
        impacts.append({
            "changeId": change_id, "document": doc.key, "title": doc.title, "requiredVersion": minimum,
            "plannedVersion": doc.version, "status": doc.status, "platforms": scoped,
            "pullRequest": pull_request, "satisfied": satisfied,
        })
    return impacts


def formal_document_blockers(plan: dict) -> list[str]:
    blockers = list(plan["documentBlockers"])
    selected = {target["platform"] for target in plan.get("packages", [])}
    for impact in plan.get("documentImpacts", []):
        if impact["satisfied"] or selected.isdisjoint(impact["platforms"]):
            continue
        reference = f" (document PR #{impact['pullRequest']})" if impact["pullRequest"] else ""
        blockers.append(
            f"{impact['changeId']}: {impact['title']} requires published v{impact['requiredVersion']} or later; "
            f"candidate selects v{impact['plannedVersion']} ({impact['status']}){reference}."
        )
    return blockers


def release_plan(head: str, base: str) -> dict:
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
    packages = matrix(config)
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
    if wire != previous_wire and released == previous_versions["northbound"]:
        warnings.append("Wire protocol changed while the published northbound document version stayed the same.")
    return {
        "sourceCommit": source,
        "baseCommit": baseline,
        "baseApplicationVersion": old_registry["application"]["version"],
        "applicationVersion": registry["application"]["version"],
        "packages": packages,
        "generatedDocuments": ["bundle-directory-guide.pdf"],
        "documents": [{**asdict(doc), "baseVersion": previous_versions.get(doc.key)} for doc in documents],
        "documentImpacts": document_impacts(registry, documents, {target["platform"] for target in packages}),
        "northbound": {
            "wireVersion": wire,
            "baseWireVersion": previous_wire,
            "releasedDocumentVersion": released,
            "prereleaseDocumentVersion": registry["protocol_lifecycle"]["prerelease_version"],
        },
        "documentBlockers": [
            f"{doc.title} v{doc.version} is still unpublished."
            for doc in documents if doc.status != "published"
        ],
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
            status = "satisfied" if impact["satisfied"] else "update required"
            if selected_platforms.isdisjoint(impact["platforms"]):
                status = "outside selected scope"
            reference = f" · PR #{impact['pullRequest']}" if impact["pullRequest"] else ""
            lines.append(f"- `{impact['changeId']}` · {impact['title']} · required v{impact['requiredVersion']}"
                         f" · planned v{impact['plannedVersion']} · {status}{reference}")
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
    plan = release_plan(args.head, gate["baseCommit"])
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
