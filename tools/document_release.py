"""Publish one approved external PDF and verify its software obligations."""

from __future__ import annotations

import argparse
from hashlib import sha256 as hash_bytes
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .external_protocol_registry import ROOT, load_registry, markdown_digest, select_external_protocol
from .publish_release import ARCHIVE_CHECKPOINT, command, ensure_release, ensure_tag, release_info, verify_asset
from .release_pipeline import require_digest, save_json, sha256, software_tag
from .release_preflight import document_version
from .source_train import require_sha


CHECKPOINT = "<!-- neurobridge-verified-document\n"
RECEIPT = "document-receipt.json"


def document_tag(key: str, version: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", key):
        raise ValueError(f"Invalid document key: {key!r}")
    document_version(version)
    return f"docs/{key.replace('_', '-')}/v{version}"


def registered_document(key: str, version: str) -> dict:
    registry = load_registry()
    if key == "northbound":
        doc = select_external_protocol(registry, version) | {"title": registry["documents"]["external_northbound"]["title"]}
    else:
        current = registry["documents"][f"external_{key}"]
        doc = current if current["version"] == version else next(
            (entry for entry in current.get("versions", []) if entry["version"] == version), None,
        )
    if doc is None or doc["status"] != "published":
        raise ValueError("document must be approved and registered as published on master")
    path = ROOT / doc["markdown_path"]
    require_digest(doc["markdown_sha256"], "approved Markdown SHA-256")
    if markdown_digest(path) != doc["markdown_sha256"]:
        raise ValueError("approved Markdown differs from its locked SHA-256")
    if Path(doc["pdf_artifact_name"]).name != doc["pdf_artifact_name"] or not doc["pdf_artifact_name"].endswith(".pdf"):
        raise ValueError("registered PDF must be a flat .pdf filename")
    return doc


def prior_public_versions(key: str, known: dict) -> dict:
    from .release_scope import registry_at
    from .release_version_policy import formal_document

    prefix = document_tag(key, "0.0").removesuffix("0.0")
    versions = {}
    for tag in command("git", "tag", "--list", f"{prefix}*").splitlines():
        version = tag.removeprefix(prefix)
        if version in known or document_version(version) >= max(map(document_version, known)):
            continue
        if release_info(tag) is None:
            continue
        commit = command("git", "rev-parse", f"refs/tags/{tag}^{{commit}}")
        doc = formal_document(registry_at(commit), key)
        if doc["version"] != version:
            raise ValueError(f"document tag disagrees with its source registry: {tag}")
        versions[version] = doc | {"taggedCommit": commit}
    return versions


def public_release(tag: str) -> dict:
    info = release_info(tag)
    if not info or info["isDraft"] or info.get("isPrerelease"):
        raise ValueError(f"public release required: {tag}")
    return info


def download(tag: str, name: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    command("gh", "release", "download", tag, "--pattern", name, "--dir", str(directory))
    path = directory / name
    if not path.is_file():
        raise ValueError(f"missing release asset {tag}: {name}")
    return path


def verified_software_receipt(tag: str, directory: Path) -> dict:
    system, _, source_version = tag.partition("/v")
    if software_tag(system, source_version) != tag:
        raise ValueError(f"invalid software tag: {tag}")
    info = public_release(tag)
    archive = f"neurobridge-{system}-v{source_version}.zip"
    if {archive, "software-receipt.json"} - {asset["name"] for asset in info["assets"]}:
        raise ValueError(f"software release is missing its verified assets: {tag}")
    receipt = json.loads(download(tag, "software-receipt.json", directory).read_bytes())
    if (receipt.get("schemaVersion") != 1 or receipt["tag"] != tag or receipt["system"] != system
            or receipt["archive"]["fileName"] != archive):
        raise ValueError(f"software receipt differs from release: {tag}")
    require_digest(receipt["archive"]["sha256"], "verified software ZIP SHA-256")
    saved = checkpoint(info, ARCHIVE_CHECKPOINT)
    if (saved["fileName"], saved["sha256"], saved["sourceCommit"]) != (
        archive, receipt["archive"]["sha256"], receipt["sourceCommit"],
    ):
        raise ValueError(f"software receipt differs from verified archive checkpoint: {tag}")
    ensure_tag(tag, require_sha(receipt["sourceCommit"]))
    return receipt


def software_obligations(tags: list[str], key: str, version: str, directory: Path) -> dict[str, list[str]]:
    if not tags or len(set(tags)) != len(tags):
        raise ValueError("provide distinct public software tags before releasing the document")
    closed = {}
    for index, tag in enumerate(sorted(tags)):
        receipt = verified_software_receipt(tag, directory / str(index))
        applicable = [item for item in receipt["documentObligations"] if item["document"] == key]
        if any(document_version(version) < document_version(item["requiredVersion"]) for item in applicable):
            raise ValueError(f"{tag} requires a newer version of {key}")
        closed[tag] = sorted({item["changeId"] for item in applicable})
    return closed


def checkpoint(info: dict, marker: str = CHECKPOINT) -> dict:
    body = info.get("body") or ""
    if body.count(marker) != 1:
        raise ValueError("release has no unique asset checkpoint")
    stored = body.split(marker, 1)[1]
    if "\n-->" not in stored:
        raise ValueError("release has an incomplete asset checkpoint")
    return json.loads(stored.split("\n-->", 1)[0])


def verify(key: str, version: str, directory: Path) -> dict:
    tag = document_tag(key, version)
    info = public_release(tag)
    receipt = json.loads(download(tag, RECEIPT, directory).read_bytes())
    if (receipt.get("schemaVersion") != 1 or receipt != checkpoint(info)
            or (receipt["tag"], receipt["document"], receipt["version"]) != (tag, key, version)):
        raise ValueError("public document receipt differs from the tag or PDF checkpoint")
    ensure_tag(tag, require_sha(receipt["sourceCommit"]))
    filename = receipt["pdf"]["fileName"]
    if Path(filename).name != filename or not filename.endswith(".pdf"):
        raise ValueError("invalid PDF asset name")
    if {asset["name"] for asset in info["assets"]} != {filename, RECEIPT}:
        raise ValueError("public document release assets differ from its receipt")
    require_digest(receipt["pdf"]["sha256"], "published PDF SHA-256")
    if sha256(download(tag, filename, directory)) != receipt["pdf"]["sha256"]:
        raise ValueError("public PDF SHA-256 differs from receipt")
    if software_obligations(list(receipt["software"]), key, version, directory / "software") != receipt["software"]:
        raise ValueError("public document receipt does not match the software obligations")
    return receipt


def publish(key: str, version: str, tags: list[str], directory: Path) -> dict:
    from tools.release_version_policy import fixed_path

    tag = document_tag(key, version)
    source = require_sha(command("git", "rev-parse", "HEAD"))
    command("git", "fetch", "--no-tags", "--refmap=", "origin", "refs/heads/master")
    master = require_sha(command("git", "rev-parse", "FETCH_HEAD^{commit}"))
    command("git", "merge-base", "--is-ancestor", source, master)
    subprocess.run(["bash", "tools/check-external-protocol.sh"], cwd=ROOT, check=True)
    doc = registered_document(key, version)
    closed = software_obligations(tags, key, version, directory / "software")
    tagged = command("git", "ls-remote", "--refs", "origin", f"refs/tags/{tag}")
    if not tagged and doc["markdown_path"] != fixed_path(doc):
        raise ValueError("new document tags require a promoted version at the fixed Markdown path")
    if tagged:
        command("git", "fetch", "--no-tags", "--refmap=", "origin", f"refs/tags/{tag}")
        source = require_sha(command("git", "rev-parse", "FETCH_HEAD^{commit}"))
        command("git", "merge-base", "--is-ancestor", source, master)
        markdown = subprocess.check_output(["git", "--no-replace-objects", "show", f"{source}:{doc['markdown_path']}"], cwd=ROOT)
        if hash_bytes(markdown).hexdigest() != doc["markdown_sha256"]:
            raise ValueError("tagged Markdown differs from the approved document version")
    identity = {"schemaVersion": 1, "tag": tag, "document": key, "version": version,
                "sourceCommit": source, "markdownSha256": doc["markdown_sha256"], "software": closed}
    filename = doc["pdf_artifact_name"]
    info = release_info(tag)
    existing = {asset["name"] for asset in info["assets"]} if info else set()
    if existing - {filename, RECEIPT}:
        raise ValueError("document release has unexpected assets")
    if info and not info["isDraft"]:
        receipt = verify(key, version, directory / "public")
        if any(receipt[field] != value for field, value in identity.items()):
            raise ValueError("document version was already published with different evidence")
        return receipt
    if existing:
        receipt = checkpoint(info)
        if any(receipt[field] != value for field, value in identity.items()) or filename not in existing:
            raise ValueError("draft release does not match the requested document and software tags")
        pdf = download(tag, filename, directory)
        if receipt["pdf"] != {"fileName": filename, "sha256": sha256(pdf)}:
            raise ValueError("draft PDF differs from its verified checkpoint")
    else:
        pdf = directory / filename
        subprocess.run([str(ROOT / "tools/render-protocol-pdf.sh"), str(ROOT / doc["markdown_path"]), str(pdf)],
                       cwd=ROOT, check=True)
        receipt = identity | {"pdf": {"fileName": filename, "sha256": sha256(pdf)}}
    subprocess.run(["pdfinfo", str(pdf)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["pdftoppm", "-png", "-r", "144", str(pdf), str(directory / "page")], check=True)
    if not tagged:
        command("git", "tag", "-a", tag, source, "-m", tag)
        command("git", "push", "origin", f"refs/tags/{tag}")
    ensure_tag(tag, source)
    notes = directory / "release-notes.md"
    notes.write_text(f"Document {tag}\n\nSoftware: {', '.join(closed)}\n\n"
                     + CHECKPOINT + json.dumps(receipt, sort_keys=True) + "\n-->\n", encoding="utf-8")
    ensure_release(tag, notes)
    save_json(directory / RECEIPT, receipt)
    verify_asset(tag, pdf, filename in existing)
    verify_asset(tag, directory / RECEIPT, RECEIPT in existing)
    command("gh", "release", "edit", tag, "--draft=false", "--latest=false")
    return verify(key, version, directory / "public")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("publish", "verify"))
    parser.add_argument("--document", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--software-tags", default="")
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="neurobridge-doc-release-") as temporary:
            directory = Path(temporary)
            if args.operation == "publish":
                if not args.execute or (os.getenv("GITHUB_ACTIONS") == "true" and os.getenv("TRAIN_RELEASE_IDENTITY") != "workflow"):
                    raise ValueError("document publication requires explicit execution and a release identity in Actions")
                receipt = publish(args.document, args.version, args.software_tags.split(",") if args.software_tags else [], directory)
                if args.preview_dir:
                    from shutil import copyfile
                    args.preview_dir.mkdir(parents=True, exist_ok=True)
                    for page in directory.glob("page-*.png"):
                        copyfile(page, args.preview_dir / page.name)
                    save_json(args.preview_dir / RECEIPT, receipt)
            else:
                receipt = verify(args.document, args.version, directory)
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Document release blocked: {error}\n")


if __name__ == "__main__":
    main()
