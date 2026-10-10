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
            support_files = ["install-with-logs.ps1"] if platform == "windows" else []
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


def render_bundle_prd(release_manifest: dict, systems: list[dict]) -> str:
    lines = [
        "# NeuroBridge 交付包说明 PRD",
        "",
        f"应用版本：{release_manifest['applicationVersion']}",
        f"源码提交：{release_manifest['git']['commit']}",
        "",
        "需求状态：本文随源码生成；安装、接入与故障恢复须在真实目标机验收。",
        "",
        "## 1. 支持范围",
        "",
        "Windows：仅 Windows 10/11 x86_64（Intel/AMD 64 位）。Windows 7、32 位和 ARM 不支持。",
        "麒麟：仅银河麒麟 V10 x86_64（Intel/AMD 64 位）引导 DEB。",
        "麒麟 32 位 x86、ARM/aarch64、龙芯/LoongArch/MIPS、申威等其他架构不支持。",
        "V10 是系统版本，不代表所有架构通用；其他 64 位架构也不能安装此 x86_64 包。",
        "在麒麟终端执行 uname -m，当前包要求输出 x86_64。",
        "本包包含的实际系统、版本与架构列在下方，未列出的目标不随包交付。",
        "",
        "## 2. 根目录与解压结构",
        "",
        "PRD.md：本说明，请先阅读。",
        "docs/external/：共用接口文档、采集包格式说明和联调页面附件。",
        "metadata/：源码/构建信息、验证日志、归档摘要，以及 PDF 原名称对照 document-filenames.json。",
        "",
        "```text",
    ]
    for system in systems:
        lines.extend([f"{system['fileName']}：{system['platform']} 系统的压缩包。", ""])
        for variant in system['variants']:
            lines.append(f"  解压 {system['fileName']} -> {variant['fileName']}")
            for architecture in variant['architectureArchives']:
                lines.append(f"    解压 {variant['fileName']} -> {architecture['fileName']}")
                for package in architecture['packages']:
                    lines.append(f"      {package['fileName']}：{package['format']} 安装包")
                lines.append("      checksums.sha256：同目录安装包 SHA-256，安装前核验。")
            for document in variant['documents']:
                lines.append(f"    {document}：该系统的部署指南或共用协议文档。")
            for helper in variant.get('supportFiles', []):
                lines.append(f"    {helper}：Windows 安装日志与失败日志导出入口。")
        lines.append("")
    lines.extend([
        "```",
        "",
        "## 3. 解压与安装",
        "",
        "依次解压：总 ZIP -> 所需系统 ZIP -> 系统版本 ZIP -> x86_64 ZIP。",
        "版本 ZIP 中的 docs/ 包含部署指南；安装文件和 checksums.sha256 在架构 ZIP 中。",
        "Windows：选择对应系统版本，MSI 与 EXE 二选一；安装日志工具位于版本 ZIP 根目录。",
        "麒麟：先核验 sha256sum -c checksums.sha256，再 sudo dpkg -i ./<安装包文件名>.deb。",
        "麒麟引导包在本机构建运行时，需具备交付指南要求的系统依赖和管理员权限。",
        "耳机未连接时仍可安装；服务等待设备，接入后才开始实时采集。安装成功不代表现场采集验收通过。",
        "设备连接、断线恢复、服务重启等仍须在真实目标机验证。",
        "",
        "## 4. 日志与失败诊断",
        "",
        "麒麟安装日志：/var/log/neurobridge-bootstrap/；运行日志：/var/log/neurobridge/。",
        '麒麟失败日志导出：sudo /usr/lib/neurobridge-bootstrap/export-install-logs.sh --output-dir "$HOME/下载"',
        "服务尚未创建或软件包配置失败时也可以导出；串口节点及权限诊断在 tty-status.txt。",
        "Windows：在版本 ZIP 目录执行 powershell -ExecutionPolicy Bypass -File .\\install-with-logs.ps1 -Installer .\\<x86_64目录>\\<安装包>。",
        "Windows 导出：powershell -ExecutionPolicy Bypass -File .\\install-with-logs.ps1 -Export。",
        "PDF 副本使用英文文件名以避免解压乱码，中文标题和正文保留；请勿据名称猜测系统兼容性。",
        "",
        "## 5. 验收要求",
        "",
        "总包根目录必须包含本 PRD；目录结构、安装包名称与源码提交必须和实际文件及 metadata 清单一致。",
        "不得生成或宣称支持矩阵之外的系统/架构，也不得把源码/模拟测试写成现场验收通过。",
        "麒麟现场须验证无耳机安装、接入后实时采集、拔插重连、启动失败回滚、卸载和服务/整机重启。",
        "耳机离线且存在历史录制时不得启动录播；浏览器断开/恢复须保持可观测状态。",
        "",
    ])
    return '\n'.join(lines)


def build_bundle(release_directory: Path, documents_root: Path, output: Path) -> dict:
    release_manifest = json.loads((release_directory / "release-manifest.json").read_text(encoding="utf-8"))
    timestamp = zip_timestamp(release_manifest)
    grouped, validation = collect_native_packages(release_directory, release_manifest)
    available_platforms = {platform for platform, _, _ in grouped}
    files, bound_documents = collect_bundle_documents(documents_root, available_platforms)
    aliases = {PurePosixPath(name).name: delivery_pdf_name(PurePosixPath(name).name) for name in files if name.endswith(".pdf")}
    for documents in bound_documents.values():
        aliases.update({name: delivery_pdf_name(name) for name in documents})
    platform_documents = documents_for_platform_archives(files, bound_documents, available_platforms)
    files = {str(PurePosixPath(name).parent / delivery_pdf_name(PurePosixPath(name).name)) if name.endswith(".pdf") else name: value for name, value in files.items()}
    files["metadata/document-filenames.json"] = json.dumps(aliases, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    system_archives, system_manifest = build_system_archives(grouped, timestamp, platform_documents)
    files.update(system_archives)
    files["PRD.md"] = render_bundle_prd(release_manifest, system_manifest).encode("utf-8")
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
        "documents": sorted(name for name in files if name.startswith("docs/") or name == "PRD.md"),
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
    except (OSError, KeyError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
