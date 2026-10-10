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
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RELEASE_MATRIX = tomllib.loads((ROOT / "release/release_matrix.toml").read_text(encoding="utf-8"))
MANIFEST_FILENAME = "release-manifest.json"
# Platforms whose per-version archives must also carry the shipped documents.
DOCUMENT_BEARING_PLATFORMS = ("windows", "kylin")


def delivery_pdf_name(name: str) -> str:
    # Some Kylin GUI extractors ignore ZIP's UTF-8 flag and decode names as GBK.
    # ASCII delivery aliases keep the registered PDF bytes/title/version intact.
    titles = {
        "头环数据网关北向网络协议": "northbound-protocol",
        "头环数据采集包格式说明": "capture-package-format",
        "数据网关银河麒麟部署与使用指南": "kylin-deployment-guide",
        "数据网关 Windows 部署与使用指南": "windows-deployment-guide",
    }
    for title, alias in titles.items():
        if name.startswith(title + "_v"):
            return alias + name[len(title):]
    if not name.isascii():
        return "document-" + sha256(name.encode("utf-8")).hexdigest()[:16] + ".pdf"
    return name


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
                policy = RELEASE_MATRIX[platform]
                versions = policy["versions"] if platform == "windows" else policy["editions"]
                version = package["osVersion"] if platform == "windows" else package["edition"]
                if version not in versions or package["architecture"] not in policy["architectures"] or package["format"] not in policy["formats"]:
                    raise ValueError(f"unsupported release target: {package['fileName']}")
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
    build_info: bytes,
) -> tuple[dict[str, bytes], list[dict]]:
    files: dict[str, bytes] = {}
    manifest: list[dict] = []
    for platform in ("windows", "kylin"):
        if platform == "windows":
            families = [f"windows-{version}" for version in RELEASE_MATRIX["windows"]["versions"]]
            expected_architectures = RELEASE_MATRIX["windows"]["architectures"]
        elif RELEASE_MATRIX["kylin"].get("package_kind") == "bootstrap":
            # One bootstrap package installs on the Kylin machine and builds
            # there, so it is not split by edition and architecture.
            families = ["kylin-v10"]
            expected_architectures = RELEASE_MATRIX["kylin"]["architectures"]
        else:
            families = [f"kylin-{edition}" for edition in RELEASE_MATRIX["kylin"]["editions"]]
            expected_architectures = RELEASE_MATRIX["kylin"]["architectures"]
        family_archives: list[tuple[str, bytes, dict]] = []
        for family in families:
            architectures = [(architecture, grouped.get((platform, family, architecture), [])) for architecture in expected_architectures]
            architectures = [(architecture, packages) for architecture, packages in architectures if packages]
            if not architectures:
                continue
            arch_entries: list[dict] = []
            family_documents = {f"docs/{delivery_pdf_name(name)}": payload for name, payload in sorted(platform_documents.get(platform, {}).items())}
            support_files = ["install-with-logs.ps1", "diagnostic-context.ps1", "build-info.txt"] if platform == "windows" else []
            family_stream = io.BytesIO()
            with zipfile.ZipFile(family_stream, "w", zipfile.ZIP_DEFLATED) as family_zip:
                for architecture, packages in architectures:
                    data, entry = build_architecture_archive(packages, timestamp)
                    filename = f"{family}-{architecture}.zip"
                    add_bytes(family_zip, filename, data, timestamp)
                    arch_entries.append({"architecture": architecture, "fileName": filename, **entry})
                for document_name, payload in family_documents.items():
                    add_bytes(family_zip, document_name, payload, timestamp)
                if platform == "windows":
                    add_bytes(family_zip, "install-with-logs.ps1", (ROOT / "packaging/windows/install-with-logs.ps1").read_bytes(), timestamp)
                    add_bytes(family_zip, "diagnostic-context.ps1", (ROOT / "windows/diagnostic-context.ps1").read_bytes(), timestamp)
                    add_bytes(family_zip, "build-info.txt", build_info, timestamp)
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
                    "supportFiles": support_files,
                },
            ))
        if not family_archives:
            continue
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


DIRECTORY_GUIDE_FILENAME = "bundle-directory-guide.pdf"


def render_bundle_directory_guide(release_manifest: dict, systems: list[dict], document_names: list[str]) -> str:
    """Describe the files actually being shipped, for deployment staff."""
    def code(value: str) -> str:
        return "`" + value.replace("`", "&#96;").replace("|", "&#124;").replace("\n", " ") + "`"

    lines = [
        "# NeuroBridge 交付包目录说明", "",
        f"应用版本：{release_manifest['applicationVersion']}", "",
        f"源码提交：{code(release_manifest['git']['commit'])}", "",
        "面向部署人员。先阅读本说明，再按目标系统解压对应文件；本说明介绍文件的位置与用途，具体安装操作请阅读对应部署指南。", "",
        "## 1. 解压总包后，先看这些位置", "",
        "| 位置 | 用途 |", "| --- | --- |",
        f"| {code(DIRECTORY_GUIDE_FILENAME)} | 本目录说明，位于总 ZIP 根目录 |",
    ]
    for system in systems:
        label = "Windows" if system['platform'] == 'windows' else "银河麒麟"
        lines.append(f"| {code(system['fileName'])} | {label}系统压缩包；只解压需要部署的平台 |")
    if document_names:
        lines.append("| `docs/external/` | 共用接口、采集包格式说明或联调附件，具体文件见第 3 节 |")
    lines.extend([
        "| `metadata/` | 交付清单、构建来源、验证日志和 PDF 原名称对照；供核验与排错 |", "",
        "## 2. 解压顺序与每层文件", "",
        "1. 解压总 ZIP，找到本 PDF 和所需系统 ZIP。",
        "2. 解压系统 ZIP，选择与目标系统版本对应的版本 ZIP。",
        "3. 解压版本 ZIP：先阅读 `docs/` 中的部署指南，再解压其安装文件分组 ZIP。",
        "4. 分组 ZIP 内是安装包与 `checksums.sha256`；核验后按部署指南安装。", "",
        "平台目录、系统 ZIP、版本 ZIP 和安装文件分组 ZIP 是不同层级；PDF 与日志工具可能位于版本 ZIP，不在最内层安装包旁。以下路径与名称均由本次交付清单生成。", "",
    ])
    purposes = {'install-with-logs.ps1': '安装并保存失败日志；-Export 导出安装日志',
                'diagnostic-context.ps1': '日志环境采集模块，须与安装日志入口保存在一起',
                'build-info.txt': '此交付包的应用版本与源码提交'}
    for system in systems:
        for variant in system['variants']:
            lines.extend([f"### {variant['name']}", "",
                          f"解压路径：{code(system['fileName'])} → {code(variant['fileName'])}。", "",
                          "| 在版本 ZIP 中找到 | 用途 |", "| --- | --- |"])
            for architecture in variant['architectureArchives']:
                lines.append(f"| {code(architecture['fileName'])} | 安装文件分组 {code(architecture['architecture'])}，需继续解压 |")
            for document in variant.get('documents', []):
                lines.append(f"| {code(document)} | 部署指南或共用接口文档；按文件标题选择 |")
            for helper in variant.get('supportFiles', []):
                lines.append(f"| {code(helper)} | {purposes.get(helper, '随包支持文件')} |")
            lines.extend(["", "继续解压安装文件分组：", "",
                          "| 分组 ZIP | 内部安装包 | 格式 / 状态 |", "| --- | --- | --- |"])
            for architecture in variant['architectureArchives']:
                for package in architecture['packages']:
                    lines.append(f"| {code(architecture['fileName'])} | {code(package['fileName'])} | {package['format'].upper()} / {package.get('status', 'candidate')} |")
            lines.extend(["", "每个安装文件分组内的 `checksums.sha256` 仅用于核验同目录安装包。", ""])
            if system['platform'] == 'windows':
                lines.extend(["Windows：选择匹配系统的分组；EXE 和 MSI 二选一。安装日志入口及其模块在版本 ZIP 中，解压后保存在同一目录。", ""])
            else:
                architectures = {entry['architecture'] for entry in variant['architectureArchives']}
                if 'all' in architectures:
                    lines.extend(["麒麟：`all` 是共用引导 DEB 的分组标识。安装时检查目标系统与 CPU/ABI；该名称不表示生成的运行时可在任意架构复用，也不代表各架构已完成现场验收。", ""])
                else:
                    lines.extend(["麒麟：选择与目标 CPU 架构匹配的分组，再按部署指南安装。", ""])
    lines.extend(["## 3. 共用附件与核验资料", "",
                  "| 总包中的位置 | 用途 |", "| --- | --- |"])
    for name in document_names:
        lines.append(f"| {code(name)} | 共用文档或联调附件 |")
    lines.extend([
        "| `metadata/bundle-manifest.json` | 各层 ZIP、随包文档及安装包的交付清单和摘要 |",
        "| `metadata/build-manifest.json` | 构建来源和目标结果 |",
        "| `metadata/release-logs.jsonl` | 构建与交付验证记录 |",
        "| `metadata/validation/` | 本次随包验证日志 |",
        "| `metadata/document-filenames.json` | PDF 英文文件名与原中文名称对照 |", "",
        "PDF 使用英文文件名以减少麒麟解压后的文件名乱码；打开后仍保留中文标题和正文。", "",
        "## 4. 安装出错时，去哪里找日志", "",
        "| 场景 | 日志位置 / 导出入口 |", "| --- | --- |",
    ])
    platforms = {system['platform'] for system in systems}
    if 'windows' in platforms:
        lines.extend([
            "| Windows 安装失败 | 版本 ZIP 中的 `install-with-logs.ps1`；安装用 `-Installer`，导出用 `-Export`；日志在 `%LOCALAPPDATA%\\NeuroBridge\\installer-logs` |",
            "| Windows 运行问题 | 安装目录 `windows/export-logs.ps1`；导出用 `-OutputDirectory` |",
        ])
    if 'kylin' in platforms:
        lines.extend([
            "| 麒麟安装失败 | 日志在 `/var/log/neurobridge-bootstrap/`；导出入口 `/usr/lib/neurobridge-bootstrap/export-install-logs.sh`，参数 `--output-dir` |",
            "| 麒麟运行问题 | 日志在 `/var/log/neurobridge/`；导出入口 `/opt/neurobridge/kylin/export-logs.sh`，参数 `--output-dir` |",
        ])
    lines.extend(["", "安装未成功或服务未创建时，优先使用安装日志导出入口。诊断包标识软件/安装包版本、系统环境和导出时间；提交问题时请提供对应诊断包。", "",
                  "交付状态与现场验证记录以清单和部署指南为准；构建或解压成功不代表现场采集验收通过。", ""])
    return "\n".join(lines)


def render_directory_pdf(markdown: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="neurobridge-directory-guide-") as temporary:
        directory = Path(temporary)
        source = directory / "directory-guide.md"
        output = directory / DIRECTORY_GUIDE_FILENAME
        source.write_text(markdown, encoding="utf-8")
        subprocess.run([str(ROOT / "tools/render-protocol-pdf.sh"), str(source), str(output),
                        str(ROOT / 'tools/bundle-directory-guide.css')], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["pdfinfo", str(output)], check=True, stdout=subprocess.DEVNULL)
        result = output.read_bytes()
        if not result.startswith(b"%PDF-"):
            raise ValueError("directory guide renderer did not produce a PDF")
        return result


def build_bundle(release_directory: Path, documents_root: Path, output: Path) -> dict:
    release_manifest = json.loads((release_directory / "release-manifest.json").read_text(encoding="utf-8"))
    timestamp = zip_timestamp(release_manifest)
    grouped, validation = collect_native_packages(release_directory, release_manifest)
    available_platforms = {platform for platform, _, _ in grouped}
    files, bound_documents = collect_bundle_documents(documents_root, available_platforms)
    aliases = {PurePosixPath(name).name: delivery_pdf_name(PurePosixPath(name).name) for name in files if name.endswith(".pdf")}
    for documents in bound_documents.values():
        aliases.update({name: delivery_pdf_name(name) for name in documents})
    aliases['交付包目录说明.pdf'] = DIRECTORY_GUIDE_FILENAME
    platform_documents = documents_for_platform_archives(files, bound_documents, available_platforms)
    files = {str(PurePosixPath(name).parent / delivery_pdf_name(PurePosixPath(name).name)) if name.endswith(".pdf") else name: value for name, value in files.items()}
    files["metadata/document-filenames.json"] = json.dumps(aliases, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    build_info = f"application_version={release_manifest['applicationVersion']}\nsource_commit={release_manifest['git']['commit']}\n".encode('utf-8')
    system_archives, system_manifest = build_system_archives(grouped, timestamp, platform_documents, build_info)
    files.update(system_archives)
    files[DIRECTORY_GUIDE_FILENAME] = render_directory_pdf(render_bundle_directory_guide(
        release_manifest, system_manifest, sorted(name for name in files if name.startswith("docs/"))))
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
        "documents": sorted(name for name in files if name.startswith("docs/") or name == DIRECTORY_GUIDE_FILENAME),
        "systemDocuments": sorted(name for name in files if name.startswith(("windows/", "kylin/")) and name.endswith(".pdf")),
        "validationFiles": sorted(name for name in files if name.startswith("metadata/validation/")),
        "metadataFiles": ["metadata/document-filenames.json"],
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
    except (OSError, KeyError, ValueError, json.JSONDecodeError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
