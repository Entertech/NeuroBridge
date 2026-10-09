#!/usr/bin/env python3
"""Assemble the user-facing release bundle from verified native packages."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RELEASE_MATRIX = tomllib.loads((ROOT / "release/release_matrix.toml").read_text(encoding="utf-8"))
MANIFEST_FILENAME = "release-manifest.json"
# Platforms whose per-version archives must also carry the shipped documents.
DOCUMENT_BEARING_PLATFORMS = ("windows",)


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


def find_one(root: Path, pattern: str) -> Path | None:
    matches = sorted(path for path in root.rglob(pattern) if path.is_file())
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"multiple inputs match {pattern}: {', '.join(map(str, matches))}")
    return matches[0]


def collect_native_packages(release_directory: Path, release_manifest: dict) -> tuple[dict[tuple[str, str, str], list[dict]], list[tuple[str, bytes]]]:
    grouped: dict[tuple[str, str, str], list[dict]] = {}
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
                platform = platform_archive["platform"]
                family = f"windows-{package['osVersion']}" if platform == "windows" else f"kylin-{package['edition']}"
                member = f"packages/{package['fileName']}"
                if member not in members:
                    raise ValueError(f"missing package member {member} in {archive_path.name}")
                value = archive.read(member)
                if digest_bytes(value) != package["sha256"]:
                    raise ValueError(f"package hash mismatch: {package['fileName']}")
                grouped.setdefault((platform, family, package["architecture"]), []).append(
                    {"fileName": package["fileName"], "format": package["format"], "sha256": package["sha256"], "bytes": value}
                )
                validation_member = package.get("validationLog")
                if validation_member and validation_member in members:
                    validation.append((validation_member, archive.read(validation_member)))
    return grouped, validation


def build_architecture_archive(packages: list[dict], timestamp: tuple[int, int, int, int, int, int]) -> tuple[bytes, dict]:
    seen: set[str] = set()
    checksums: list[str] = []
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for package in sorted(packages, key=lambda item: item["fileName"]):
            filename = package["fileName"]
            safe_name(filename)
            if PurePosixPath(filename).name != filename or filename in seen:
                raise ValueError(f"duplicate or unsafe package name: {filename}")
            seen.add(filename)
            add_bytes(archive, filename, package["bytes"], timestamp)
            checksums.append(f"{package['sha256']}  {filename}")
        add_bytes(archive, "checksums.sha256", ("\n".join(checksums) + "\n").encode(), timestamp)
    data = stream.getvalue()
    return data, {
        "packageCount": len(packages),
        "packages": [{key: item[key] for key in ("fileName", "format", "sha256")} for item in sorted(packages, key=lambda item: item["fileName"])],
        "sha256": digest_bytes(data),
    }


def build_system_archives(
    grouped: dict[tuple[str, str, str], list[dict]],
    timestamp: tuple[int, int, int, int, int, int],
    platform_documents: dict[str, dict[str, bytes]],
) -> tuple[dict[str, bytes], list[dict]]:
    files: dict[str, bytes] = {}
    manifest: list[dict] = []
    for platform in ("windows", "kylin"):
        if platform == "windows":
            families = [f"windows-{version}" for version in RELEASE_MATRIX["windows"]["versions"]]
            expected_architectures = RELEASE_MATRIX["windows"]["architectures"]
        else:
            families = [f"kylin-{edition}" for edition in RELEASE_MATRIX["kylin"]["editions"]]
            expected_architectures = RELEASE_MATRIX["kylin"]["architectures"]
        family_archives: list[tuple[str, bytes, dict]] = []
        for family in families:
            architectures = [(architecture, grouped.get((platform, family, architecture), [])) for architecture in expected_architectures]
            arch_entries: list[dict] = []
            family_documents = {f"docs/{name}": payload for name, payload in sorted(platform_documents.get(platform, {}).items())}
            family_stream = io.BytesIO()
            with zipfile.ZipFile(family_stream, "w", zipfile.ZIP_DEFLATED) as family_zip:
                for architecture, packages in architectures:
                    data, entry = build_architecture_archive(packages, timestamp)
                    filename = f"{family}-{architecture}.zip"
                    add_bytes(family_zip, filename, data, timestamp)
                    arch_entries.append({"architecture": architecture, "fileName": filename, **entry})
                for document_name, payload in family_documents.items():
                    add_bytes(family_zip, document_name, payload, timestamp)
            family_data = family_stream.getvalue()
            family_filename = f"{family}.zip"
            family_archives.append((
                family_filename,
                family_data,
                {
                    "name": family,
                    "fileName": family_filename,
                    "sha256": digest_bytes(family_data),
                    "architectureArchives": arch_entries,
                    "documents": sorted(family_documents),
                },
            ))
        system_filename = f"{platform}.zip"
        system_stream = io.BytesIO()
        with zipfile.ZipFile(system_stream, "w", zipfile.ZIP_DEFLATED) as system_zip:
            for family_filename, family_data, _ in family_archives:
                add_bytes(system_zip, family_filename, family_data, timestamp)
        system_data = system_stream.getvalue()
        system_path = f"{platform}/{system_filename}"
        files[system_path] = system_data
        manifest.append({"platform": platform, "fileName": system_path, "sha256": digest_bytes(system_data), "variants": [entry for _, _, entry in family_archives]})
    return files, manifest


def collect_bundle_documents(
    documents_root: Path,
    available_platforms: set[str],
) -> tuple[dict[str, bytes], dict[str, dict[str, bytes]]]:
    """Route each packaged document by its registered delivery condition.

    Returns the top-level bundle files plus, per platform, the documents that belong
    inside that platform's per-version archives. ``review_only`` documents stay in the
    review artifact; ``always`` documents ship under ``docs/external/``; a
    ``platform_bound`` document ships with its platform only when that platform actually
    produced packages. Platforms listed in ``DOCUMENT_BEARING_PLATFORMS`` receive the
    document inside every per-version archive instead of a copy at the platform root.
    """
    package = find_one(documents_root, "neurobridge-external-documents.zip")
    if package is None:
        raise FileNotFoundError("external document package is missing")
    files: dict[str, bytes] = {}
    platform_documents: dict[str, dict[str, bytes]] = {}
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise ValueError("external document package is corrupt")
        manifest = json.loads(archive.read(MANIFEST_FILENAME).decode("utf-8"))
        policies = {item["pdf_artifact_name"]: item for item in manifest.get("documents", [])}
        for name in archive.namelist():
            safe_name(name)
            if name.endswith("/"):
                continue
            payload = archive.read(name)
            policy = policies.get(PurePosixPath(name).name)
            if policy is None:
                if name.lower().endswith(".pdf"):
                    raise ValueError(f"packaged document {name} is not registered in {MANIFEST_FILENAME}")
                # Non-document attachments: the B-side test page and the manifest itself.
                files[f"docs/external/{name}"] = payload
                continue
            delivery = policy["delivery"]
            if delivery == "review_only":
                continue
            if delivery == "platform_bound":
                for platform in policy.get("platforms", ()):
                    if platform not in available_platforms:
                        continue
                    if platform in DOCUMENT_BEARING_PLATFORMS:
                        platform_documents.setdefault(platform, {})[PurePosixPath(name).name] = payload
                    else:
                        files[f"{platform}/{name}"] = payload
                continue
            files[f"docs/external/{name}"] = payload
    return files, platform_documents


def documents_for_platform_archives(
    files: dict[str, bytes],
    platform_documents: dict[str, dict[str, bytes]],
    available_platforms: set[str],
) -> dict[str, dict[str, bytes]]:
    """Return the documents that must travel inside each platform's per-version archives.

    Every archive gets the shared external documents plus whatever documents are bound
    to that platform, so an operator who unpacks a single version still gets them.
    """
    shared = {
        PurePosixPath(path).name: payload
        for path, payload in files.items()
        if path.startswith("docs/external/") and path.endswith(".pdf")
    }
    result: dict[str, dict[str, bytes]] = {}
    for platform in DOCUMENT_BEARING_PLATFORMS:
        if platform not in available_platforms:
            continue
        selected = {**shared, **platform_documents.get(platform, {})}
        if selected:
            result[platform] = selected
    return result


def build_bundle(release_directory: Path, documents_root: Path, output: Path) -> dict:
    release_manifest = json.loads((release_directory / "release-manifest.json").read_text(encoding="utf-8"))
    timestamp = zip_timestamp(release_manifest)
    grouped, validation = collect_native_packages(release_directory, release_manifest)
    available_platforms = {platform for platform, _, _ in grouped}
    files, bound_documents = collect_bundle_documents(documents_root, available_platforms)
    platform_documents = documents_for_platform_archives(files, bound_documents, available_platforms)
    system_archives, system_manifest = build_system_archives(grouped, timestamp, platform_documents)
    files.update(system_archives)
    for name, value in validation:
        files[f"metadata/validation/{name.removeprefix('validation/')}"] = value
    bundle_manifest = {
        "schemaVersion": 2,
        "applicationVersion": release_manifest["applicationVersion"],
        "sourceCommit": release_manifest["git"]["commit"],
        "releaseStatus": release_manifest["releaseStatus"],
        "trigger": release_manifest["trigger"],
        "coverage": release_manifest["coverage"],
        "systemArchives": system_manifest,
        "documents": sorted(name for name in files if name.startswith("docs/")),
        "systemDocuments": sorted(name for name in files if name.startswith(("windows/", "kylin/")) and name.endswith(".pdf")),
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
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = build_bundle(args.release_dir, args.documents_dir, args.output)
    except (OSError, KeyError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
