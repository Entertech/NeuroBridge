#!/usr/bin/env python3
"""Publish one already verified aggregate ZIP without moving existing tags."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256 as hashlib_sha256
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from tools.release_pipeline import CONFIG, ROOT, log, require_digest, save_json, sha256, version_tuple

ARCHIVE_CHECKPOINT = "<!-- neurobridge-verified-archive\n"


def command(*args: str, allow_missing: bool = False) -> str | None:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        if allow_missing:
            return None
        raise ValueError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def ensure_tag(tag: str, commit: str) -> None:
    remote = command("git", "ls-remote", "--tags", "--refs", "origin", f"refs/tags/{tag}")
    if remote:
        command("git", "fetch", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
        actual = command("git", "rev-list", "-n", "1", tag)
        kind = command("git", "cat-file", "-t", tag)
        if actual != commit or kind != "tag":
            raise ValueError(f"existing tag {tag} is not an annotated tag for {commit}")
        log("tag_reused", tag=tag, commit=commit)
        return
    command("git", "tag", "-a", tag, "-m", f"NeuroBridge {tag}", commit)
    command("git", "push", "origin", f"refs/tags/{tag}")
    log("tag_created", tag=tag, commit=commit)


def release_info(tag: str) -> dict | None:
    raw = command("gh", "release", "view", tag, "--json", "url,isDraft,assets,body", allow_missing=True)
    return json.loads(raw) if raw else None


def ensure_release(tag: str, notes: Path) -> dict:
    info = release_info(tag)
    if info is None:
        command("gh", "release", "create", tag, "--draft", "--title", tag, "--notes-file", str(notes), "--verify-tag")
        info = release_info(tag)
        if info is None or not info.get("isDraft"):
            raise ValueError("draft Release creation could not be verified")
        log("draft_created", tag=tag, url=info.get("url"))
    elif info.get("isDraft") and not info.get("assets"):
        # No uploaded bytes exist yet: a failed first upload can safely register
        # the newly verified artifact as its checkpoint before trying again.
        command("gh", "release", "edit", tag, "--notes-file", str(notes))
    return info


def verify_asset(tag: str, asset: Path, existing: bool) -> None:
    if not existing:
        command("gh", "release", "upload", tag, str(asset))
        log("asset_uploaded", name=asset.name, sha256=sha256(asset))
    with tempfile.TemporaryDirectory(prefix="neurobridge-release-asset-") as temporary:
        command("gh", "release", "download", tag, "--pattern", asset.name, "--dir", temporary)
        downloaded = Path(temporary) / asset.name
        if not downloaded.is_file() or downloaded.stat().st_size != asset.stat().st_size or sha256(downloaded) != sha256(asset):
            raise ValueError(f"published asset differs from local verified bytes: {asset.name}")
    log("asset_verified", name=asset.name, sha256=sha256(asset))


def verify_nested_archives(archive: Path, manifest: dict) -> None:
    with zipfile.ZipFile(archive) as outer:
        if "metadata/bundle-manifest.json" in outer.namelist():
            verify_release_bundle(archive, json.loads(outer.read("metadata/bundle-manifest.json")))
            return
    archives = manifest["platformArchives"]
    if {item["platform"] for item in archives} != {"windows", "kylin"}:
        raise ValueError("release must contain exactly one ZIP per platform")
    with zipfile.ZipFile(archive) as outer, tempfile.TemporaryDirectory(prefix="neurobridge-release-verify-") as temporary:
        expected_outer = {"build-manifest.json", "release-logs.jsonl", *(item["fileName"] for item in archives)}
        if set(outer.namelist()) != expected_outer or outer.testzip() is not None:
            raise ValueError("aggregate ZIP contents or CRC do not match the release contract")
        internal = json.loads(outer.read("build-manifest.json"))
        if internal["sourceCommit"] != manifest["git"]["commit"] or internal["coverage"] != manifest["coverage"]:
            raise ValueError("internal and external manifests differ")
        for platform in archives:
            target = Path(temporary) / platform["fileName"]
            with outer.open(platform["fileName"]) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
            if sha256(target) != platform["sha256"] or len(platform["packages"]) != platform["packageCount"]:
                raise ValueError(f"platform ZIP hash/count mismatch: {platform['platform']}")
            with zipfile.ZipFile(target) as inner:
                expected_inner = {name for item in platform["packages"] for name in (f"packages/{item['fileName']}", item["validationLog"])}
                if set(inner.namelist()) != expected_inner or inner.testzip() is not None:
                    raise ValueError(f"platform ZIP contents/CRC mismatch: {platform['platform']}")
                for item in platform["packages"]:
                    digest = hashlib_sha256()
                    with inner.open(f"packages/{item['fileName']}") as source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(block)
                    if digest.hexdigest() != item["sha256"]:
                        raise ValueError(f"package hash mismatch: {item['fileName']}")


def verify_release_bundle(archive: Path, bundle_manifest: dict) -> None:
    """Verify the single user-facing ZIP and its system/version/architecture ZIPs."""
    with zipfile.ZipFile(archive) as outer:
        if outer.testzip() is not None:
            raise ValueError("release bundle CRC verification failed")
        names = set(outer.namelist())
        required = {"metadata/bundle-manifest.json", "metadata/build-manifest.json", "metadata/release-logs.jsonl"}
        required.update(bundle_manifest.get("documents", []))
        required.update(bundle_manifest.get("systemDocuments", []))
        required.update(bundle_manifest.get("validationFiles", []))
        required.update(bundle_manifest.get("metadataFiles", []))
        for system in bundle_manifest.get("systemArchives", []):
            path = system["fileName"]
            required.add(path)
            if path not in names:
                raise ValueError(f"system archive missing: {path}")
            data = outer.read(path)
            if sha256_bytes(data) != system["sha256"]:
                raise ValueError(f"system archive hash mismatch: {path}")
            with zipfile.ZipFile(io.BytesIO(data)) as system_zip:
                if system_zip.testzip() is not None:
                    raise ValueError(f"system archive is corrupt: {path}")
                expected_variants = {variant["fileName"] for variant in system.get("variants", [])}
                if set(system_zip.namelist()) != expected_variants:
                    raise ValueError(f"system archive contents mismatch: {path}")
                for variant in system.get("variants", []):
                    variant_data = system_zip.read(variant["fileName"])
                    if sha256_bytes(variant_data) != variant["sha256"]:
                        raise ValueError(f"variant archive hash mismatch: {variant['fileName']}")
                    with zipfile.ZipFile(io.BytesIO(variant_data)) as variant_zip:
                        if variant_zip.testzip() is not None:
                            raise ValueError(f"variant archive is corrupt: {variant['fileName']}")
                        expected_architectures = {item["fileName"] for item in variant.get("architectureArchives", [])}
                        expected_documents = set(variant.get("documents", []))
                        expected_support = set(variant.get("supportFiles", []))
                        if set(variant_zip.namelist()) != expected_architectures | expected_documents | expected_support:
                            raise ValueError(f"variant archive contents mismatch: {variant['fileName']}")
                        for item in variant.get("architectureArchives", []):
                            architecture_data = variant_zip.read(item["fileName"])
                            if sha256_bytes(architecture_data) != item["sha256"]:
                                raise ValueError(f"architecture archive hash mismatch: {item['fileName']}")
                            with zipfile.ZipFile(io.BytesIO(architecture_data)) as architecture_zip:
                                expected_packages = {package["fileName"] for package in item.get("packages", [])} | {"checksums.sha256"}
                                if set(architecture_zip.namelist()) != expected_packages or architecture_zip.testzip() is not None:
                                    raise ValueError(f"architecture archive contents/CRC mismatch: {item['fileName']}")
                                checksums = "\n".join(f"{package['sha256']}  {package['fileName']}" for package in item.get("packages", [])) + "\n"
                                if architecture_zip.read("checksums.sha256").decode("utf-8") != checksums:
                                    raise ValueError(f"architecture checksum list mismatch: {item['fileName']}")
                                for package in item.get("packages", []):
                                    if sha256_bytes(architecture_zip.read(package["fileName"])) != package["sha256"]:
                                        raise ValueError(f"package hash mismatch: {package['fileName']}")
        if not required.issubset(names) or names != required:
            raise ValueError("release bundle is missing documented or metadata entries")


def sha256_bytes(value: bytes) -> str:
    return hashlib_sha256(value).hexdigest()


def load_bundle_manifest(directory: Path) -> tuple[Path, dict] | None:
    archives = sorted(path for path in directory.glob("*.zip") if path.is_file())
    if len(archives) != 1:
        return None
    archive = archives[0]
    with zipfile.ZipFile(archive) as bundle:
        if "metadata/bundle-manifest.json" not in bundle.namelist():
            return None
        raw = json.loads(bundle.read("metadata/bundle-manifest.json"))
    system_archives = raw.get("systemArchives", [])
    package_count = sum(
        item["packageCount"]
        for system in system_archives
        for variant in system.get("variants", [])
        for item in variant.get("architectureArchives", [])
    )
    if not system_archives:
        package_count = sum(item.get("packageCount", 0) for item in raw.get("architectureArchives", []))
    manifest = {
        "applicationVersion": raw["applicationVersion"],
        "releaseStatus": raw["releaseStatus"],
        "trigger": raw["trigger"],
        "git": {"commit": raw["sourceCommit"], "ref": "refs/heads/master", "dirty": False},
        "coverage": raw["coverage"],
        "aggregateArchive": {"fileName": archive.name, "sha256": sha256(archive), "status": "candidate", "packageCount": package_count},
        "bundleManifest": raw,
    }
    return archive, manifest


def archive_checkpoint(archive: Path, version: str, commit: str) -> dict:
    return {"schemaVersion": "1.0", "applicationVersion": version, "sourceCommit": commit, "fileName": archive.name, "sha256": sha256(archive)}


def bundle_packages(manifest: dict) -> list[tuple]:
    return sorted(
        (system["platform"], variant["name"], architecture["architecture"], package["fileName"], package["format"], package["sha256"])
        for system in manifest["systemArchives"]
        for variant in system["variants"]
        for architecture in variant["architectureArchives"]
        for package in architecture["packages"]
    )


def reuse_release_bundle(archive: Path) -> bool:
    """Recover immutable uploaded bytes when PDFs were regenerated on a retry.

    This is read-only on GitHub. The recovered ZIP is uploaded to Actions again
    and checked by publish() before any tag or Release write occurs.
    """
    with zipfile.ZipFile(archive) as bundle:
        current = json.loads(bundle.read("metadata/bundle-manifest.json"))
    version = current["applicationVersion"]
    version_tuple(version)
    commit = command("git", "rev-parse", "HEAD")
    if current["sourceCommit"] != commit or current["trigger"] != "push_master":
        raise ValueError("release retry bundle does not match this source commit and publication trigger")
    verify_release_bundle(archive, current)
    tag = f"v{version}"
    info = release_info(tag)
    if info is None or not info.get("assets"):
        log("release_bundle_reuse_skipped", tag=tag, reason="no_uploaded_release_asset")
        return False
    if {item["name"] for item in info["assets"]} != {archive.name}:
        raise ValueError("existing Release assets do not match the single release bundle")
    body = info.get("body") or ""
    if body.count(ARCHIVE_CHECKPOINT) != 1:
        raise ValueError("existing Release asset lacks an unambiguous verified archive checkpoint; refusing replacement")
    saved_checkpoint = body.split(ARCHIVE_CHECKPOINT, 1)[1]
    if "\n-->" not in saved_checkpoint:
        raise ValueError("existing Release archive checkpoint is incomplete")
    checkpoint = json.loads(saved_checkpoint.split("\n-->", 1)[0])
    if checkpoint.get("schemaVersion") != "1.0" or checkpoint.get("applicationVersion") != version or checkpoint.get("sourceCommit") != commit or checkpoint.get("fileName") != archive.name:
        raise ValueError("existing Release checkpoint belongs to another version, commit or filename")
    require_digest(checkpoint.get("sha256"), "original release bundle digest")
    with tempfile.TemporaryDirectory(prefix="neurobridge-release-reuse-") as temporary:
        command("gh", "release", "download", tag, "--pattern", archive.name, "--dir", temporary)
        downloaded = Path(temporary) / archive.name
        if not downloaded.is_file() or sha256(downloaded) != checkpoint["sha256"]:
            raise ValueError("original release bundle differs from its verified archive checkpoint")
        with zipfile.ZipFile(downloaded) as bundle:
            previous = json.loads(bundle.read("metadata/bundle-manifest.json"))
        if any(previous.get(key) != current.get(key) for key in ("sourceCommit", "applicationVersion", "releaseStatus", "trigger", "coverage")) or bundle_packages(previous) != bundle_packages(current):
            raise ValueError("release retry changed source, package bytes or target results; a new version is required")
        verify_release_bundle(downloaded, previous)
        replacement = archive.with_suffix(archive.suffix + ".reuse")
        shutil.copyfile(downloaded, replacement)
        replacement.replace(archive)
    log("release_bundle_reused", tag=tag, sha256=sha256(archive))
    return True


def publish(directory: Path, expected_sha256: str) -> dict:
    require_digest(expected_sha256, "uploaded aggregate ZIP digest")
    manifest_path = directory / "release-manifest.json"
    bundle_mode = not manifest_path.is_file()
    if bundle_mode:
        loaded = load_bundle_manifest(directory)
        if loaded is None:
            raise ValueError("release bundle or release-manifest.json is missing")
        archive, manifest = loaded
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        archive = directory / manifest["aggregateArchive"]["fileName"]
    version = manifest["applicationVersion"]
    version_tuple(version)
    tag = f"v{version}"
    commit = command("git", "rev-parse", "HEAD")
    if manifest["git"]["commit"] != commit or manifest["releaseStatus"] != "candidate" or manifest["trigger"] != "push_master":
        raise ValueError("manifest commit, release state or trigger does not match this checkout")
    if manifest["git"]["dirty"] or command("git", "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("release checkout is dirty")
    expected_counts = {"windows": CONFIG["windows"]["expected_package_count"], "kylin": CONFIG["kylin"]["expected_package_count"]}
    if any(len(manifest["coverage"][platform]["targetResults"]) != count for platform, count in expected_counts.items()):
        raise ValueError("manifest lacks results for every release target")
    if any(manifest["coverage"][platform]["builtPackageCount"] < 1 for platform in ("windows", "kylin")):
        raise ValueError("minimum per-platform package gate failed")
    if not archive.is_file() or sha256(archive) != manifest["aggregateArchive"]["sha256"]:
        raise ValueError("aggregate ZIP missing or SHA-256 mismatch")
    if sha256(archive) != expected_sha256.lower():
        raise ValueError("downloaded Actions Artifact differs from the ZIP verified before upload")
    verify_nested_archives(archive, manifest)
    log("uploaded_artifact_verified", name=archive.name, sha256=expected_sha256.lower())
    if not bundle_mode:
        checksums = directory / f"{archive.name}.sha256"
        if checksums.read_text(encoding="utf-8") != f"{sha256(archive)}  {archive.name}\n":
            raise ValueError("checksum sidecar differs from aggregate ZIP")
    notes = directory / "release-notes.md"
    gaps = [item for platform in ("windows", "kylin") for item in manifest["coverage"][platform]["targetResults"] if item["status"] != "candidate"]
    notes.write_text(f"NeuroBridge {tag}\n\nSource commit: `{commit}`. Unsigned packages; physical verification is pending.\n\n" + "Incomplete targets:\n" + ("\n".join(f"- `{item['targetId']}`: {item['status']} — {item['reason']}" for item in gaps) or "- None") + "\n", encoding="utf-8")
    with notes.open("a", encoding="utf-8") as output:
        output.write("\n" + ARCHIVE_CHECKPOINT + json.dumps(archive_checkpoint(archive, version, commit), sort_keys=True) + "\n-->\n")
    if bundle_mode:
        with zipfile.ZipFile(archive) as bundle:
            release_log_text = bundle.read("metadata/release-logs.jsonl").decode("utf-8")
    else:
        release_log = directory / "release-logs.jsonl"
        release_log_text = release_log.read_text(encoding="utf-8") if release_log.is_file() else ""
    if len(release_log_text.splitlines()) != sum(expected_counts.values()):
        raise ValueError("release logs missing or incomplete")
    logged = {entry["target"]["id"]: entry["status"] for entry in (json.loads(line) for line in release_log_text.splitlines())}
    expected_logged = {item["targetId"]: item["status"] for platform in ("windows", "kylin") for item in manifest["coverage"][platform]["targetResults"]}
    if logged != expected_logged:
        raise ValueError("release logs do not match target coverage")
    assets = [archive] if bundle_mode else [archive, manifest_path, directory / f"{archive.name}.sha256", directory / "release-logs.jsonl"]
    expected_names = {item.name for item in assets}
    ensure_tag(tag, commit)
    info = ensure_release(tag, notes)
    remote_assets = {item["name"]: item for item in info.get("assets", [])}
    if set(remote_assets) - expected_names:
        raise ValueError("Release has unexpected assets; refusing to replace or delete them")
    for asset in assets:
        verify_asset(tag, asset, asset.name in remote_assets)
    if info["isDraft"]:
        command("gh", "release", "edit", tag, "--draft=false")
    final = release_info(tag)
    if final is None or final["isDraft"] or {item["name"] for item in final.get("assets", [])} != expected_names:
        raise ValueError("public Release or asset list could not be verified")
    receipt = {"schemaVersion": "1.0", "applicationVersion": version, "sourceCommit": commit, "tag": tag, "githubReleaseUrl": final["url"], "aggregateArchiveFileName": archive.name, "aggregateArchiveSha256": sha256(archive), "publishedAt": datetime.now(timezone.utc).isoformat(), "status": "published"}
    save_json(directory / "release-receipt.json", receipt)
    log("release_published", tag=tag, url=final["url"], archiveSha256=receipt["aggregateArchiveSha256"])
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dir", type=Path)
    action.add_argument("--reuse-bundle", type=Path, help="recover an existing verified Release ZIP without GitHub writes")
    parser.add_argument("--expected-sha256", help="ZIP digest computed before Actions Artifact upload")
    args = parser.parse_args()
    if args.dir is not None and args.expected_sha256 is None:
        parser.error("--expected-sha256 is required for publication")
    try:
        if args.reuse_bundle is not None:
            reuse_release_bundle(args.reuse_bundle)
        else:
            publish(args.dir, args.expected_sha256)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        log("publication_failed", reason=str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
