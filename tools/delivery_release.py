"""Assemble one delivery ZIP from the successful software Releases of a train."""

from hashlib import file_digest
import io
import json
from pathlib import Path
from shutil import copyfileobj
import zipfile

from tools.document_release import checkpoint, download, verified_software_receipt
from tools.publish_release import command, ensure_release, ensure_tag, release_info, sha256_bytes, verify_asset
from tools.release_pipeline import sha256

CHECKPOINT = "<!-- neurobridge-verified-delivery\n"
MANIFEST = "metadata/delivery-manifest.json"
GUIDE = "bundle-directory-guide.pdf"


def delivery_entry(name: str) -> zipfile.ZipInfo:
    """Fixed ZIP metadata keeps the delivery archive byte-identical on rebuild."""
    return zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))


def directory_guide(guides: dict[str, bytes]) -> bytes:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError

    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_metadata({"/Title": "NeuroBridge 交付包目录说明"})
    try:
        for member, guide in guides.items():
            reader = PdfReader(io.BytesIO(guide))
            if not reader.pages:
                raise ValueError(f"empty directory guide: {member}")
            writer.append(reader, outline_item=member, import_outline=False)
        writer.write(output)
    except PdfReadError as error:
        raise ValueError(f"cannot assemble directory guide: {error}") from error
    return output.getvalue()


def bundled_documents(bundle: zipfile.ZipFile, receipt: dict) -> dict[str, bytes]:
    manifest = json.loads(bundle.read("metadata/bundle-manifest.json"))
    aliases = json.loads(bundle.read("metadata/document-filenames.json"))
    catalog = {doc["key"]: doc for doc in json.loads(bundle.read("docs/external/release-manifest.json"))["documents"]}
    if manifest["softwareSystem"] != receipt["system"] or len(manifest["systemArchives"]) != 1:
        raise ValueError(f"bundle differs from software system: {receipt['tag']}")
    system = manifest["systemArchives"][0]
    expected_keys = {
        doc["key"] for doc in catalog.values()
        if doc["delivery"] == "always" or (doc["delivery"] == "platform_bound" and system["platform"] in doc["platforms"])
    }
    keys = [doc["key"] for doc in receipt["bundledDocuments"]]
    if sorted(keys) != sorted(expected_keys):
        raise ValueError(f"bundle document list differs from software receipt: {receipt['tag']}")
    documents = {}
    bound = {}
    for doc in receipt["bundledDocuments"]:
        entry = catalog[doc["key"]]
        if any(entry.get(key) != doc.get(key) for key in ("version", "pdfSha256", "documentTag")):
            raise ValueError(f"bundled document metadata differs from software receipt: {doc['key']}")
        alias = aliases[entry["pdf_artifact_name"]]
        if Path(alias).name != alias or not alias.endswith(".pdf") or alias in documents or alias in bound:
            raise ValueError(f"invalid or duplicate bundled PDF filename: {alias}")
        if entry["delivery"] == "always":
            documents[alias] = bundle.read(f"docs/external/{alias}")
        elif entry["delivery"] == "platform_bound" and system["platform"] in entry["platforms"]:
            bound[alias] = doc["pdfSha256"]
        else:
            raise ValueError(f"bundled document does not apply to software system: {doc['key']}")
        if alias in documents and sha256_bytes(documents[alias]) != doc["pdfSha256"]:
            raise ValueError(f"document SHA-256 mismatch: {alias}")
        doc["deliveryFileName"] = f"docs/{receipt['system']}/{alias}"
    if bound:
        with zipfile.ZipFile(io.BytesIO(bundle.read(system["fileName"]))) as platform:
            variant = system["variants"][0]
            with zipfile.ZipFile(io.BytesIO(platform.read(variant["fileName"]))) as inner:
                for alias, expected in bound.items():
                    pdf = inner.read(f"docs/{alias}")
                    if sha256_bytes(pdf) != expected:
                        raise ValueError(f"document SHA-256 mismatch: {alias}")
                    documents[alias] = pdf
    return documents


def assemble(version: str, record: dict, directory: Path) -> Path:
    results = record["systemResults"]
    succeeded = sorted(system for system, result in results.items() if result["status"] == "published")
    if record["softwareResult"] != "success" or not succeeded or results.keys() != record["systemTags"].keys():
        raise ValueError("delivery requires a complete system summary with at least one success")
    archive = directory / f"neurobridge-v{version}.zip"
    manifest = {
        "schemaVersion": 1, "applicationVersion": version, "sourceCommit": record["candidate"],
        "systems": {}, "failedSystems": sorted(results.keys() - set(succeeded)),
    }
    digests = {}
    guides = {}
    with zipfile.ZipFile(archive, "w") as delivery:
        for system in succeeded:
            result = results[system]
            tag = record["systemTags"][system]
            receipt = verified_software_receipt(tag, directory / system)
            component = receipt["archive"]
            if result["tag"] != tag or receipt["sourceCommit"] != record["candidate"] or component["sha256"] != result["sha256"]:
                raise ValueError(f"public software receipt differs from the recorded result: {tag}")
            path = download(tag, component["fileName"], directory / system)
            if sha256(path) != component["sha256"]:
                raise ValueError(f"software ZIP SHA-256 mismatch: {tag}")
            member = f"systems/{system}/{path.name}"
            with path.open("rb") as source, delivery.open(zipfile.ZipInfo(member), "w", force_zip64=True) as target:
                copyfileobj(source, target, 1024 * 1024)
            component["deliveryFileName"] = member
            digests[member] = component["sha256"]
            manifest["systems"][system] = receipt
            with zipfile.ZipFile(path) as bundle:
                for name, pdf in sorted(bundled_documents(bundle, receipt).items()):
                    delivery.writestr(delivery_entry(f"docs/{system}/{name}"), pdf)
                guide = bundle.read(GUIDE)
                guides[member] = guide
                guide_member = f"docs/{system}/{GUIDE}"
                delivery.writestr(delivery_entry(guide_member), guide)
                digests[guide_member] = sha256_bytes(guide)
            for doc in receipt["bundledDocuments"]:
                digests[doc["deliveryFileName"]] = doc["pdfSha256"]
        guide = directory_guide(guides)
        delivery.writestr(delivery_entry(GUIDE), guide)
        digests[GUIDE] = sha256_bytes(guide)
        manifest["directoryGuide"] = {"fileName": GUIDE, "sha256": digests[GUIDE]}
        payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
        delivery.writestr(delivery_entry(MANIFEST), payload)
        digests[MANIFEST] = sha256_bytes(payload)
    with zipfile.ZipFile(archive) as delivery:
        if sorted(delivery.namelist()) != sorted(digests):
            raise ValueError("delivery ZIP entries differ from the recorded software and document list")
        for member, digest in digests.items():
            with delivery.open(member) as stream:
                if file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError(f"delivery ZIP entry SHA-256 mismatch: {member}")
    return archive


def publish(tag: str, commit: str, archive: Path) -> dict:
    expected = {"sourceCommit": commit, "fileName": archive.name, "sha256": sha256(archive)}
    notes = archive.with_name("delivery-notes.md")
    notes.write_text(
        f"NeuroBridge delivery archive\n\nSource commit: `{commit}`\n\n"
        + CHECKPOINT + json.dumps(expected, sort_keys=True) + "\n-->\n",
        encoding="utf-8",
    )
    ensure_tag(tag, commit)
    info = ensure_release(tag, notes)
    assets = {asset["name"] for asset in info["assets"]}
    if info.get("isPrerelease") or assets - {archive.name}:
        raise ValueError("delivery Release has unexpected status or assets")
    if not info["isDraft"] and assets != {archive.name}:
        raise ValueError("public delivery Release is missing its immutable ZIP")
    if not info["isDraft"] or assets:
        if checkpoint(info, CHECKPOINT) != expected:
            raise ValueError("delivery Release differs from its immutable checkpoint")
    verify_asset(tag, archive, archive.name in assets)
    if info["isDraft"]:
        command("gh", "release", "edit", tag, "--draft=false")
    final = release_info(tag)
    if (not final or final["isDraft"] or final.get("isPrerelease")
            or {asset["name"] for asset in final["assets"]} != {archive.name}
            or checkpoint(final, CHECKPOINT) != expected):
        raise ValueError("public delivery Release could not be verified")
    return expected | {"tag": tag, "url": final["url"]}
