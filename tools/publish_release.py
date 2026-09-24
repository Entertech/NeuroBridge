#!/usr/bin/env python3
"""Publish one already verified aggregate ZIP without moving existing tags."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256 as hashlib_sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from tools.release_pipeline import ROOT, log, save_json, sha256, version_tuple


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
    raw = command("gh", "release", "view", tag, "--json", "url,isDraft,assets", allow_missing=True)
    return json.loads(raw) if raw else None


def ensure_release(tag: str, notes: Path) -> dict:
    info = release_info(tag)
    if info is None:
        command("gh", "release", "create", tag, "--draft", "--title", tag, "--notes-file", str(notes), "--verify-tag")
        info = release_info(tag)
        if info is None or not info.get("isDraft"):
            raise ValueError("draft Release creation could not be verified")
        log("draft_created", tag=tag, url=info.get("url"))
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


def publish(directory: Path) -> dict:
    manifest_path = directory / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = manifest["applicationVersion"]
    version_tuple(version)
    tag = f"v{version}"
    commit = command("git", "rev-parse", "HEAD")
    if manifest["git"]["commit"] != commit or manifest["releaseStatus"] != "candidate" or manifest["trigger"] != "push_master":
        raise ValueError("manifest commit, release state or trigger does not match this checkout")
    if manifest["git"]["dirty"] or command("git", "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("release checkout is dirty")
    if len(manifest["coverage"]["windows"]["targetResults"]) != 12 or len(manifest["coverage"]["kylin"]["targetResults"]) != 20:
        raise ValueError("manifest lacks results for all 32 targets")
    if any(manifest["coverage"][platform]["builtPackageCount"] < 1 for platform in ("windows", "kylin")):
        raise ValueError("minimum per-platform package gate failed")
    archive = directory / manifest["aggregateArchive"]["fileName"]
    if not archive.is_file() or sha256(archive) != manifest["aggregateArchive"]["sha256"]:
        raise ValueError("aggregate ZIP missing or SHA-256 mismatch")
    verify_nested_archives(archive, manifest)
    checksums = directory / f"{archive.name}.sha256"
    if checksums.read_text(encoding="utf-8") != f"{sha256(archive)}  {archive.name}\n":
        raise ValueError("checksum sidecar differs from aggregate ZIP")
    notes = directory / "release-notes.md"
    gaps = [item for platform in ("windows", "kylin") for item in manifest["coverage"][platform]["targetResults"] if item["status"] != "candidate"]
    notes.write_text(f"NeuroBridge {tag}\n\nSource commit: `{commit}`. Unsigned packages; physical verification is pending.\n\n" + "Incomplete targets:\n" + ("\n".join(f"- `{item['targetId']}`: {item['status']} — {item['reason']}" for item in gaps) or "- None") + "\n", encoding="utf-8")
    release_log = directory / "release-logs.jsonl"
    if not release_log.is_file() or len(release_log.read_text(encoding="utf-8").splitlines()) != 32:
        raise ValueError("release logs missing or incomplete")
    logged = {entry["target"]["id"]: entry["status"] for entry in (json.loads(line) for line in release_log.read_text(encoding="utf-8").splitlines())}
    expected_logged = {item["targetId"]: item["status"] for platform in ("windows", "kylin") for item in manifest["coverage"][platform]["targetResults"]}
    if logged != expected_logged:
        raise ValueError("release logs do not match target coverage")
    assets = [archive, manifest_path, checksums, release_log]
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
    parser.add_argument("--dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        publish(args.dir)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        log("publication_failed", reason=str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
