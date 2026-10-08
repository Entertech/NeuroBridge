#!/usr/bin/env python3
"""Assemble the user-facing release bundle from verified package inputs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name:
        raise ValueError(f"unsafe ZIP member: {name}")


def zip_timestamp(manifest: dict) -> tuple[int, int, int, int, int, int]:
    value = manifest.get("build", {}).get("finishedAt")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timetuple()[:6]
        except ValueError:
            pass
    return datetime.now(timezone.utc).timetuple()[:6]


def add_bytes(bundle: zipfile.ZipFile, name: str, value: bytes, timestamp: tuple[int, int, int, int, int, int]) -> None:
    safe_name(name)
    info = zipfile.ZipInfo(name, timestamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    bundle.writestr(info, value)


def add_file(bundle: zipfile.ZipFile, path: Path, name: str, timestamp: tuple[int, int, int, int, int, int]) -> None:
    add_bytes(bundle, name, path.read_bytes(), timestamp)


def find_one(root: Path, pattern: str) -> Path | None:
    matches = sorted(path for path in root.rglob(pattern) if path.is_file())
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"multiple inputs match {pattern}: {', '.join(map(str, matches))}")
    return matches[0]


def candidate_package(root: Path, platform: str) -> tuple[Path, str] | None:
    matches = sorted(
        path for path in root.rglob(f"neurobridge-*-{platform}-*")
        if path.is_file() and path.suffix in {".zip", ".gz"}
    )
    if len(matches) > 1:
        raise ValueError(f"multiple candidate packages for {platform}: {', '.join(map(str, matches))}")
    package = matches[0] if matches else None
    if package is None:
        return None
    return package, "x86_64"


def collect_native_packages(release_directory: Path, release_manifest: dict) -> tuple[dict[str, list[tuple[str, bytes]]], list[tuple[str, bytes]]]:
    grouped: dict[str, list[tuple[str, bytes]]] = {}
    validation: list[tuple[str, bytes]] = []
    for platform_archive in release_manifest.get("platformArchives", []):
        archive_path = release_directory / platform_archive["fileName"]
        if not archive_path.is_file():
            continue
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise ValueError(f"corrupt platform archive: {archive_path.name}")
            members = set(archive.namelist())
            for package in platform_archive.get("packages", []):
                architecture = package["architecture"]
                member = f"packages/{package['fileName']}"
                if member not in members:
                    raise ValueError(f"missing package member {member} in {archive_path.name}")
                grouped.setdefault(f"{platform_archive['platform']}/{architecture}", []).append(
                    (package["fileName"], archive.read(member))
                )
                validation_member = package.get("validationLog")
                if validation_member and validation_member in members:
                    validation.append((validation_member, archive.read(validation_member)))
    return grouped, validation


def build_architecture_archives(grouped: dict[str, list[tuple[str, bytes]]], timestamp: tuple[int, int, int, int, int, int]) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    archives: dict[str, bytes] = {}
    entries: list[dict[str, object]] = []
    for key in sorted(grouped):
        platform, architecture = key.split("/", 1)
        seen: set[str] = set()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for filename, value in sorted(grouped[key]):
                if filename in seen:
                    raise ValueError(f"duplicate package in {key}: {filename}")
                seen.add(filename)
                add_bytes(archive, f"packages/{filename}", value, timestamp)
        filename = f"{platform}-{architecture}.zip"
        data = stream.getvalue()
        archives[f"{platform}/{filename}"] = data
        entries.append({"platform": platform, "architecture": architecture, "fileName": filename, "packageCount": len(seen), "sha256": digest_bytes(data)})
    return archives, entries


def external_document_entries(documents_root: Path) -> dict[str, bytes]:
    package = find_one(documents_root, "neurobridge-external-documents.zip")
    if package is None:
        raise FileNotFoundError("external document package is missing")
    result: dict[str, bytes] = {}
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise ValueError("external document package is corrupt")
        for name in archive.namelist():
            safe_name(name)
            if name.endswith("/"):
                continue
            result[f"docs/external/{name}"] = archive.read(name)
    return result


def build_bundle(release_directory: Path, documents_root: Path, candidates_root: Path, output: Path) -> dict:
    release_manifest_path = release_directory / "release-manifest.json"
    release_manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))
    timestamp = zip_timestamp(release_manifest)
    grouped, validation = collect_native_packages(release_directory, release_manifest)
    for platform in ("windows", "kylin"):
        candidate = candidate_package(candidates_root / platform, platform)
        if candidate is not None:
            package, architecture = candidate
            grouped.setdefault(f"{platform}/{architecture}", []).append((package.name, package.read_bytes()))
    architecture_archives, architecture_manifest = build_architecture_archives(grouped, timestamp)
    files: dict[str, bytes] = external_document_entries(documents_root)
    windows_prd = find_one(documents_root, "system-prds/NeuroBridge项目结构与多系统接入_PRD.pdf")
    kylin_prd = find_one(documents_root, "system-prds/银河麒麟V10耳机USB串口接入_PRD.pdf")
    if windows_prd is None or kylin_prd is None:
        raise FileNotFoundError("system PRD PDFs are missing")
    files["windows/NeuroBridge项目结构与多系统接入_PRD.pdf"] = windows_prd.read_bytes()
    files["kylin/银河麒麟V10耳机USB串口接入_PRD.pdf"] = kylin_prd.read_bytes()
    for name, value in architecture_archives.items():
        files[name] = value
    for name, value in validation:
        files[f"metadata/validation/{name.removeprefix('validation/')}"] = value
    bundle_manifest = {
        "schemaVersion": 1,
        "applicationVersion": release_manifest["applicationVersion"],
        "sourceCommit": release_manifest["git"]["commit"],
        "releaseStatus": release_manifest["releaseStatus"],
        "trigger": release_manifest["trigger"],
        "coverage": release_manifest["coverage"],
        "architectureArchives": architecture_manifest,
        "documents": sorted(name for name in files if name.startswith("docs/")),
        "systemDocuments": sorted(
            name for name in files if name.startswith(("windows/", "kylin/")) and name.endswith(".pdf")
        ),
        "validationFiles": sorted(name for name in files if name.startswith("metadata/validation/")),
    }
    files["metadata/bundle-manifest.json"] = json.dumps(bundle_manifest, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    files["metadata/build-manifest.json"] = (release_directory / "build-manifest.json").read_bytes()
    files["metadata/release-logs.jsonl"] = (release_directory / "release-logs.jsonl").read_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".new")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(files):
            add_bytes(bundle, name, files[name], timestamp)
    with zipfile.ZipFile(temporary) as bundle:
        if bundle.testzip() is not None or set(bundle.namelist()) != set(files):
            raise ValueError("release bundle validation failed")
    temporary.replace(output)
    return {**bundle_manifest, "fileName": output.name, "sha256": digest_file(output), "size": output.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", required=True, type=Path)
    parser.add_argument("--documents-dir", required=True, type=Path)
    parser.add_argument("--candidates-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = build_bundle(args.release_dir, args.documents_dir, args.candidates_dir, args.output)
    except (OSError, KeyError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
