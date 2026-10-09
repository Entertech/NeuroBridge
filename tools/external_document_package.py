"""Build the GitHub Artifact package for the current external document set."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import zipfile

from external_protocol_registry import ROOT, load_registry, select_external_protocol


PACKAGE_FILENAME = "neurobridge-external-documents.zip"
MANIFEST_FILENAME = "release-manifest.json"
PACKAGE_FORMAT_VERSION = 1
WEB_CLIENT_DIRECTORY = ROOT / "web" / "b-client-test"
WEB_CLIENT_ARCHIVE_DIRECTORY = "b-client-test"


DELIVERY_MODES = ("always", "platform_bound", "review_only")


@dataclass(frozen=True)
class ExternalDocument:
    key: str
    title: str
    version: str
    status: str
    delivery: str
    platforms: tuple[str, ...]
    markdown_path: str
    pdf_artifact_name: str


def _delivery_policy(section: dict, key: str) -> tuple[str, tuple[str, ...]]:
    """Read the declarative delivery condition that decides where a document ships."""
    delivery = section.get("delivery")
    if delivery not in DELIVERY_MODES:
        raise ValueError(
            f"External document {key} has unsupported delivery {delivery!r}; "
            f"expected one of {', '.join(DELIVERY_MODES)}."
        )
    platforms = tuple(section.get("platforms", ()))
    if delivery == "platform_bound":
        if not platforms:
            raise ValueError(f"Platform-bound external document {key} must declare at least one platform.")
    elif platforms:
        raise ValueError(f"External document {key} declares platforms without delivery=platform_bound.")
    return delivery, platforms


def _make_document(key: str, policy_source: dict, content_source: dict) -> ExternalDocument:
    delivery, platforms = _delivery_policy(policy_source, key)
    return ExternalDocument(
        key=key,
        title=policy_source["title"],
        version=content_source["version"],
        status=content_source["status"],
        delivery=delivery,
        platforms=platforms,
        markdown_path=content_source["markdown_path"],
        pdf_artifact_name=content_source["pdf_artifact_name"],
    )


def collect_external_documents(registry: dict) -> list[ExternalDocument]:
    """Return every registered external document in delivery order.

    Documents are discovered from the registry, so adding one only needs a
    ``[documents.external_<key>]`` entry carrying a ``delivery`` policy; the section
    order inside the registry is the delivery order.
    """
    sections = registry["documents"]
    documents = [_make_document("northbound", sections["external_northbound"], select_external_protocol(registry))]
    for name, section in sections.items():
        if name == "external_northbound" or not name.startswith("external_"):
            continue
        if not isinstance(section, dict) or "markdown_path" not in section:
            continue
        documents.append(_make_document(name.removeprefix("external_"), section, section))
    if len({document.pdf_artifact_name for document in documents}) != len(documents):
        raise ValueError("Every external document must have a unique PDF artifact filename.")
    for document in documents:
        if document.status not in {"published", "unpublished"}:
            raise ValueError(f"External document {document.key} has unsupported status {document.status!r}.")
        if not (ROOT / document.markdown_path).is_file():
            raise FileNotFoundError(f"External document Markdown is unavailable: {document.markdown_path}")
    return documents


def build_release_manifest(documents: list[ExternalDocument], mode: str) -> dict:
    """Describe a candidate or a fully published, locked document snapshot."""
    if mode not in {"candidate", "publish"}:
        raise ValueError(f"Unsupported package mode: {mode}")
    unpublished = [document for document in documents if document.status == "unpublished"]
    if mode == "publish" and unpublished:
        names = ", ".join(f"{document.title} v{document.version}" for document in unpublished)
        raise ValueError(
            "A formal external document package requires every packaged source document "
            f"to be published and locked first; still unpublished: {names}."
        )
    return {
        "formatVersion": PACKAGE_FORMAT_VERSION,
        "packageMode": mode,
        "versionDecision": "retain_existing_versions",
        "sourceDocumentCount": len(documents),
        "unpublishedSourceDocumentCount": len(unpublished),
        "documents": [
            {
                **asdict(document),
                "versionAction": "retain_published_version"
                if document.status == "published"
                else "publish_current_unpublished_version",
                "artifactStatus": "published" if mode == "publish" else "candidate",
            }
            for document in documents
        ],
        "sourceMetadataState": "published" if mode == "publish" else "unchanged",
        "note": "Candidate packages do not modify source metadata. Formal packages are allowed only after the release PR has published and locked every packaged source document.",
    }


def collect_web_client_files() -> list[Path]:
    """Return the self-contained B-side test page files that can be opened from disk."""
    required = ("index.html", "app.js", "runtime-config.js", "styles.css", "version.js", "README.md")
    missing = [name for name in required if not (WEB_CLIENT_DIRECTORY / name).is_file()]
    if missing:
        raise FileNotFoundError(f"B-side test page is incomplete: {', '.join(missing)}")
    return sorted(path for path in WEB_CLIENT_DIRECTORY.rglob("*") if path.is_file())


def render_documents(documents: list[ExternalDocument], pdf_directory: Path) -> list[Path]:
    pdf_directory.mkdir(parents=True, exist_ok=True)
    renderer = ROOT / "tools" / "render-protocol-pdf.sh"
    rendered = []
    for document in documents:
        output = pdf_directory / document.pdf_artifact_name
        subprocess.run([str(renderer), str(ROOT / document.markdown_path), str(output)], check=True)
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"PDF renderer produced no file for {document.key}")
        rendered.append(output)
    return rendered


def package_documents(rendered_pdfs: list[Path], web_client_files: list[Path], manifest: dict, output_directory: Path) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    for item, pdf in zip(manifest["documents"], rendered_pdfs, strict=True):
        item["pdfSha256"] = sha256(pdf.read_bytes()).hexdigest()
    manifest["webClient"] = {
        "archiveDirectory": WEB_CLIENT_ARCHIVE_DIRECTORY,
        "entryPoint": f"{WEB_CLIENT_ARCHIVE_DIRECTORY}/index.html",
        "openDirectly": True,
        "files": [
            {
                "path": str(path.relative_to(WEB_CLIENT_DIRECTORY)),
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for path in web_client_files
        ],
    }
    manifest_path = output_directory / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package_path = output_directory / PACKAGE_FILENAME
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for pdf in rendered_pdfs:
            archive.write(pdf, arcname=pdf.name)
        for file in web_client_files:
            archive.write(file, arcname=f"{WEB_CLIENT_ARCHIVE_DIRECTORY}/{file.relative_to(WEB_CLIENT_DIRECTORY)}")
        archive.write(manifest_path, arcname=MANIFEST_FILENAME)
    with zipfile.ZipFile(package_path) as archive:
        expected = (
            {pdf.name for pdf in rendered_pdfs}
            | {f"{WEB_CLIENT_ARCHIVE_DIRECTORY}/{file.relative_to(WEB_CLIENT_DIRECTORY)}" for file in web_client_files}
            | {MANIFEST_FILENAME}
        )
        if set(archive.namelist()) != expected or archive.testzip() is not None:
            raise RuntimeError("External document ZIP validation failed")
    return package_path


def build_package(output_directory: Path, pdf_directory: Path, mode: str) -> Path:
    documents = collect_external_documents(load_registry())
    manifest = build_release_manifest(documents, mode)
    if mode == "publish":
        subprocess.run(["python3", "tools/check-version-registry.py"], cwd=ROOT, check=True)
    return package_documents(
        render_documents(documents, pdf_directory),
        collect_web_client_files(),
        manifest,
        output_directory,
    )
