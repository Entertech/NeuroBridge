#!/usr/bin/env python3
"""Fail-closed release planning, target evidence and archive assembly.

This script never turns a source candidate into an installer. Target runners
provide native packages plus a verification report from their own toolchain.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / "release/release_matrix.toml").read_text(encoding="utf-8"))
REGISTRY = ROOT / "neurobridge/version_registry.toml"
PRODUCT_PATHS = ("neurobridge/", "windows/", "linux/", "packaging/", "release/", "tools/build-product-candidate.py", "tools/release_pipeline.py", "tools/publish_release.py", "requirements.lock", "pyproject.toml", ".github/workflows/")
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
FORMATS = {"exe", "msi", "deb", "rpm"}
MAGIC = {"exe": b"MZ", "msi": bytes.fromhex("d0cf11e0a1b11ae1"), "deb": b"!<arch>\n", "rpm": bytes.fromhex("edabeedb")}


def log(event: str, **data: object) -> None:
    print(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "event": event, **data}, ensure_ascii=False, sort_keys=True), flush=True)


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.PIPE).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_digest(value: object, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError(f"{label} must be a SHA-256 digest")


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def matrix() -> list[dict[str, str]]:
    targets = []
    for version in CONFIG["windows"]["versions"]:
        for arch in CONFIG["windows"]["architectures"]:
            for fmt in CONFIG["windows"]["formats"]:
                targets.append({"id": f"windows-{version}-{arch}-{fmt}", "platform": "windows", "osVersion": version, "edition": "none", "architecture": arch, "format": fmt, "runner": CONFIG["build"]["windows_coordinator"]})
    for edition in CONFIG["kylin"]["editions"]:
        for arch in CONFIG["kylin"]["architectures"]:
            for fmt in CONFIG["kylin"]["formats"]:
                targets.append({"id": f"kylin-{edition}-{arch}-{fmt}", "platform": "kylin", "osVersion": "V10", "edition": edition, "architecture": arch, "format": fmt, "runner": CONFIG["build"]["linux_coordinator"]})
    if len(targets) != CONFIG["aggregate"]["expected_package_count"] or len({item["id"] for item in targets}) != len(targets):
        raise ValueError("release matrix count or IDs differ from the declared contract")
    return targets


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid application version: {value!r}")
    return tuple(map(int, match.groups()))


def gate(base: str) -> dict[str, object]:
    current_registry = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    try:
        previous_registry = tomllib.loads(run("git", "show", f"{base}:neurobridge/version_registry.toml"))
        changed = [name for name in subprocess.check_output(("git", "diff", "--name-only", "-z", f"{base}...HEAD"), cwd=ROOT).decode("utf-8").split("\0") if name]
    except subprocess.CalledProcessError as error:
        raise ValueError(f"cannot resolve release baseline {base}: {error.stderr.strip()}") from error
    old = previous_registry["application"]["version"]
    new = current_registry["application"]["version"]
    old_parts, new_parts = version_tuple(old), version_tuple(new)
    product_changed = any(path.startswith(PRODUCT_PATHS) for path in changed)
    if new_parts < old_parts:
        raise ValueError(f"application version regressed: {old} -> {new}")
    if product_changed and new_parts == old_parts:
        raise ValueError(f"product/deployment/package files changed without application version bump: {old}")
    if new_parts > old_parts:
        records = [item for item in current_registry.get("application_release_changes", []) if item.get("from_version") == old and item.get("to_version") == new]
        if len(records) != 1:
            raise ValueError(f"exactly one application_release_changes entry is required for {old} -> {new}")
        record = records[0]
        impact = record.get("impact")
        expected = {"patch": (old_parts[0], old_parts[1], old_parts[2] + 1), "minor": (old_parts[0], old_parts[1] + 1, 0), "major": (old_parts[0] + 1, 0, 0)}.get(impact)
        if new_parts != expected or not record.get("compatibility") or not record.get("evidence"):
            raise ValueError(f"invalid {impact!r} version step or missing release evidence: {old} -> {new}")
    result: dict[str, object] = {"baseVersion": old, "applicationVersion": new, "productChanged": product_changed, "shouldRelease": new_parts > old_parts, "changedFiles": changed, "reason": "version_advanced" if new_parts > old_parts else "same_version_no_product_change"}
    log("version_gate", **result)
    return result


def target_result(target_id: str, input_root: Path, output_root: Path) -> dict[str, object]:
    target = next((item for item in matrix() if item["id"] == target_id), None)
    if target is None:
        raise ValueError(f"unknown target: {target_id}")
    output = output_root / target_id
    output.mkdir(parents=True, exist_ok=True)
    source = input_root / target_id
    report_path = source / "verification.json"
    result: dict[str, object] = {"target": target, "status": "blocked", "reason": "verified_offline_input_missing", "stage": "input", "checkedAt": datetime.now(timezone.utc).isoformat(), "sourceCommit": run("git", "rev-parse", "HEAD"), "runner": os.environ.get("RUNNER_IMAGE", os.environ.get("RUNNER_OS", sys.platform))}
    fetch_log = output / "input-fetch.log"
    if fetch_log.is_file() and not report_path.is_file():
        summary = fetch_log.read_text(encoding="utf-8", errors="replace").splitlines()
        if summary and summary[-1].startswith("blocked:"):
            result["reason"] = summary[-1]
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            package = source / report["fileName"]
            if package.parent != source or package.suffix.lower() != "." + target["format"]:
                raise ValueError("package filename, path or format mismatch")
            if package.name == "result.json" or report["validationLog"] == "result.json":
                raise ValueError("input cannot overwrite target result")
            if not all(fragment in package.stem.lower() for fragment in (target["platform"], target["architecture"], tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["application"]["version"])):
                raise ValueError("package filename must identify platform, architecture and application version")
            if report["targetId"] != target_id or report["sourceCommit"] != result["sourceCommit"] or report.get("targetArchitecture") != target["architecture"]:
                raise ValueError("target ID or source commit mismatch")
            if report.get("automatedValidation") != "passed" or not report.get("validationLog"):
                raise ValueError("automated validation evidence missing")
            if not package.is_file() or package.stat().st_size == 0 or sha256(package) != report["sha256"]:
                raise ValueError("package missing or SHA-256 mismatch")
            with package.open("rb") as source_file:
                if source_file.read(len(MAGIC[target["format"]])) != MAGIC[target["format"]]:
                    raise ValueError("package is not the declared native installer format")
            if not report.get("toolchain") or not report.get("runtimeSha256") or not report.get("inputSha256") or not report.get("sourceReferences") or not report.get("builtAt"):
                raise ValueError("toolchain or offline input provenance missing")
            if not isinstance(report["builtAt"], str) or datetime.fromisoformat(report["builtAt"].replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("builtAt must be a timezone-aware timestamp")
            require_digest(report["runtimeSha256"], "runtimeSha256")
            require_digest(report["inputSha256"], "inputSha256")
            if not isinstance(report["sourceReferences"], list):
                raise ValueError("sourceReferences must be a nonempty list")
            for reference in report["sourceReferences"]:
                if not isinstance(reference, dict) or set(reference) != {"kind", "url", "retrievedAt", "sha256"} or reference["kind"] not in {"os-matrix", "dependency", "install-source", "other"} or not str(reference["url"]).startswith("https://") or not reference["retrievedAt"]:
                    raise ValueError("invalid offline source reference")
                parsed_url = urlsplit(reference["url"])
                if not parsed_url.hostname or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
                    raise ValueError("source reference URL must not contain credentials or query data")
                if not isinstance(reference["retrievedAt"], str) or datetime.fromisoformat(reference["retrievedAt"].replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("source reference retrievedAt must be timezone-aware")
                require_digest(reference["sha256"], "source reference sha256")
            verification_log = source / report["validationLog"]
            if verification_log.parent != source or not verification_log.is_file() or verification_log.stat().st_size == 0:
                raise ValueError("validation log missing")
            shutil.copy2(package, output / package.name)
            shutil.copy2(verification_log, output / verification_log.name)
            result.update(status="candidate", reason="automated_validation_passed", stage="package", fileName=package.name, sha256=report["sha256"], validationLog=verification_log.name, size=package.stat().st_size, toolchain=report["toolchain"], runtimeSha256=report["runtimeSha256"], inputSha256=report["inputSha256"], reportSha256=sha256(report_path), sourceReferences=report["sourceReferences"], builtAt=report["builtAt"])
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
            result.update(status="failed", reason=str(error), stage="verify")
    save_json(output / "result.json", result)
    log("target_result", targetId=target_id, status=result["status"], reason=result["reason"])
    return result


def add_zip_entry(bundle: zipfile.ZipFile, path: Path, name: str, timestamp: tuple[int, int, int, int, int, int]) -> None:
    if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
        raise ValueError(f"unsafe ZIP entry: {name}")
    info = zipfile.ZipInfo(name, timestamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    with path.open("rb") as source, bundle.open(info, "w") as destination:
        shutil.copyfileobj(source, destination, 1024 * 1024)


def assemble(results_root: Path, output: Path, trigger: str = "push_master") -> dict[str, object]:
    if trigger not in {"pull_request", "push_master"}:
        raise ValueError(f"invalid release trigger: {trigger}")
    targets = matrix()
    commit = run("git", "rev-parse", "HEAD")
    commit_at = datetime.fromtimestamp(int(run("git", "show", "-s", "--format=%ct", "HEAD")), timezone.utc)
    results = []
    for target in targets:
        path = results_root / target["id"] / "result.json"
        if not path.is_file():
            raise ValueError(f"missing result for {target['id']}")
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("target") != target or result.get("sourceCommit") != commit or result.get("status") not in {"candidate", "failed", "blocked", "source-only"}:
            raise ValueError(f"invalid result identity/status for {target['id']}")
        results.append(result)
    winners = {platform: [item for item in results if item["target"]["platform"] == platform and item["status"] == "candidate"] for platform in ("windows", "kylin")}
    counts = {platform: len(items) for platform, items in winners.items()}
    log("coverage_gate", totalTargets=len(results), candidatePackages=counts)
    if any(count < CONFIG["minimum_packages_per_platform"] for count in counts.values()):
        raise ValueError(f"minimum package gate failed: {counts}; each platform requires {CONFIG['minimum_packages_per_platform']}")
    version = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["application"]["version"]
    built_times = sorted(datetime.fromisoformat(item.get("builtAt", commit_at.isoformat()).replace("Z", "+00:00")).astimezone(timezone.utc) for items in winners.values() for item in items)
    date = built_times[-1].strftime("%Y%m%d")
    zip_stamp = built_times[-1].timetuple()[:6]
    output.mkdir(parents=True, exist_ok=True)
    platform_archives = []
    for platform in ("windows", "kylin"):
        archive = output / f"{platform}-v{version}-{date}.zip"
        package_entries = []
        seen_packages: set[str] = set()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in winners[platform]:
                target = item["target"]
                source = results_root / target["id"]
                package = source / item["fileName"]
                validation_log = source / item["validationLog"]
                if package.name in seen_packages or package.suffix.lower() != "." + target["format"]:
                    raise ValueError(f"duplicate or misnamed candidate: {target['id']}")
                seen_packages.add(package.name)
                if not package.is_file() or sha256(package) != item["sha256"] or not validation_log.is_file():
                    raise ValueError(f"candidate bytes/log changed: {target['id']}")
                add_zip_entry(bundle, package, f"packages/{package.name}", zip_stamp)
                add_zip_entry(bundle, validation_log, f"validation/{target['id']}/{validation_log.name}", zip_stamp)
                package_entries.append({"target": platform, "osVersion": target["osVersion"], "edition": target["edition"], "architecture": target["architecture"], "format": target["format"], "status": "candidate", "fileName": package.name, "sha256": item["sha256"], "validation": "pending", "validationLog": f"validation/{target['id']}/{validation_log.name}"})
        platform_archives.append({"platform": platform, "fileName": archive.name, "sha256": sha256(archive), "status": "candidate", "packageCount": len(package_entries), "packages": package_entries})
    coverage = {"requireAllMatrixTargets": False}
    for platform in ("windows", "kylin"):
        config = CONFIG[platform]
        coverage[platform] = {"expectedTargets": [item["id"] for item in targets if item["platform"] == platform], "expectedFormats": config["formats"], "expectedPackageCount": config["expected_package_count"], "builtPackageCount": counts[platform], "targetResults": [{"targetId": item["target"]["id"], "status": item["status"], "reason": item["reason"], **({"validationLog": item["validationLog"]} if item.get("validationLog") else {})} for item in results if item["target"]["platform"] == platform]}
    internal_manifest = {"applicationVersion": version, "sourceCommit": commit, "coverage": coverage, "platformArchives": [{key: item[key] for key in ("platform", "fileName", "sha256", "packageCount")} for item in platform_archives]}
    internal_path = output / "build-manifest.json"
    save_json(internal_path, internal_manifest)
    release_log = output / "release-logs.jsonl"
    stable_fields = ("target", "status", "reason", "sourceCommit", "fileName", "sha256", "validationLog", "toolchain", "runtimeSha256", "inputSha256", "sourceReferences", "builtAt")
    release_log.write_text("".join(json.dumps({key: item[key] for key in stable_fields if key in item}, ensure_ascii=False, sort_keys=True) + "\n" for item in results), encoding="utf-8")
    archive = output / f"neurobridge-v{version}-{date}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in platform_archives:
            add_zip_entry(bundle, output / item["fileName"], item["fileName"], zip_stamp)
        add_zip_entry(bundle, internal_path, internal_path.name, zip_stamp)
        add_zip_entry(bundle, release_log, release_log.name, zip_stamp)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or set(bundle.namelist()) != {"build-manifest.json", "release-logs.jsonl", *(item["fileName"] for item in platform_archives)}:
            raise ValueError("aggregate ZIP verification failed")
    references = list({json.dumps(ref, sort_keys=True): ref for item in results if item["status"] == "candidate" for ref in item["sourceReferences"]}.values())
    manifest = {"schemaVersion": "1.0", "manifestType": "neurobridge.release", "applicationVersion": version, "wireProtocolVersion": tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["northbound_wire_protocol"]["version"], "releaseStatus": "candidate", "trigger": trigger, "git": {"commit": commit, "ref": os.environ.get("GITHUB_REF", "refs/heads/master"), "dirty": bool(run("git", "status", "--porcelain", "--untracked-files=no"))}, "build": {"startedAt": built_times[0].isoformat(), "finishedAt": built_times[-1].isoformat(), "offline": True, "workflowRunId": os.environ.get("GITHUB_RUN_ID", "local"), "runner": os.environ.get("RUNNER_IMAGE", sys.platform)}, "coverage": coverage, "platformArchives": platform_archives, "aggregateArchive": {"fileName": archive.name, "sha256": sha256(archive), "status": "candidate", "packageCount": sum(counts.values())}, "release": {"tagStatus": "pending", "githubReleaseStatus": "pending"}, "validation": {"status": "pending", "automated": "passed", "physicalVerification": "pending", "logFiles": [release_log.name] + [entry["validationLog"] for item in platform_archives for entry in item["packages"]]}, "sourceReferences": references}
    save_json(output / "release-manifest.json", manifest)
    (output / f"{archive.name}.sha256").write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
    log("aggregate_verified", archive=archive.name, sha256=sha256(archive), packages=sum(counts.values()))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--output", type=Path)
    check = commands.add_parser("gate")
    check.add_argument("--base", required=True)
    check.add_argument("--output", type=Path)
    target = commands.add_parser("target")
    target.add_argument("--target", required=True)
    target.add_argument("--input-root", type=Path, required=True)
    target.add_argument("--output-root", type=Path, required=True)
    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--results-root", type=Path, required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    aggregate.add_argument("--trigger", choices=("pull_request", "push_master"), default="push_master")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            value = {"include": matrix()}
            if args.output:
                save_json(args.output, value)
            print(json.dumps(value, separators=(",", ":")))
        elif args.command == "gate":
            value = gate(args.base)
            if args.output:
                save_json(args.output, value)
        elif args.command == "target":
            target_result(args.target, args.input_root, args.output_root)
        else:
            assemble(args.results_root, args.output, args.trigger)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        log("release_error", command=args.command, reason=str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
