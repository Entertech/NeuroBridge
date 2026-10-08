#!/usr/bin/env python3
"""Build one unsigned native installer from the checked-out NeuroBridge source.

The release workflow consumes the directory produced by this command as a
``neurobridge-input-<targetId>`` artifact.  This command deliberately refuses
to fall back to the source ZIP/TAR candidate builder: a target is successful
only when its native package manager (WiX, dpkg-deb or rpmbuild) produced the
declared installer format.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neurobridge.versioning import APPLICATION_VERSION
from tools.release_pipeline import matrix


EXCLUDED_DIRS = {".git", ".github", ".runtime", "__pycache__", ".pytest_cache", ".venv", "build", "dist"}
WINDOWS_ARCH = {"x86": "x86", "x86_64": "x64"}
DEB_ARCH = {"x86_64": "amd64", "arm64": "arm64", "loongarch64": "loong64", "mips64el": "mips64el", "sw64": "sw64"}
RPM_ARCH = {"x86_64": "x86_64", "arm64": "aarch64", "loongarch64": "loongarch64", "mips64el": "mips64el", "sw64": "sw64"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def digest_tree(path: Path) -> str:
    value = hashlib.sha256()
    for item in sorted(item for item in path.rglob("*") if item.is_file()):
        value.update(item.relative_to(path).as_posix().encode("utf-8"))
        value.update(bytes.fromhex(digest(item)))
    return value.hexdigest()


def command(args: list[str], *, cwd: Path = ROOT, log: Path | None = None) -> str:
    rendered = subprocess.list2cmdline(args)
    if log:
        with log.open("a", encoding="utf-8") as output:
            output.write(f"$ {rendered}\n")
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if log:
        with log.open("a", encoding="utf-8") as output:
            output.write(result.stdout)
            output.write(result.stderr)
            output.write(f"exit={result.returncode}\n")
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {rendered}")
    return result.stdout.strip()


def target_for(target_id: str) -> dict[str, str]:
    try:
        return next(item for item in matrix() if item["id"] == target_id)
    except StopIteration as error:
        raise ValueError(f"unknown release target: {target_id}") from error


def copy_source(stage: Path, target: dict[str, str], runtime: Path) -> None:
    payload = stage / "opt" / "neurobridge"
    payload.mkdir(parents=True)
    for name in ("neurobridge", "web", "requirements.lock", "pyproject.toml", "sdk.lock", "config"):
        source = ROOT / name
        destination = payload / name
        if source.is_dir():
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*EXCLUDED_DIRS, "*.pyc"))
        elif source.is_file():
            shutil.copy2(source, destination)
    platform_dir = ROOT / ("windows" if target["platform"] == "windows" else "packaging/kylin")
    shutil.copytree(platform_dir, payload / platform_dir.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if target["platform"] == "kylin":
        service_dir = payload / "packaging"
        service_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "packaging/kylin/neurobridge.service", service_dir / "neurobridge.service")
    # The deployed template decides which serial source and algorithm path the
    # installed service uses, so it must match the platform.  The candidate
    # builder already picks it this way; shipping the Kylin template to Windows
    # would select the POSIX serial source and a /opt algorithm binary.
    template = "config/gateway.toml.example" if target["platform"] == "kylin" else "windows/gateway.toml.example"
    shutil.copy2(ROOT / template, payload / "gateway.toml.example")
    shutil.copytree(runtime, payload / "runtime", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def runtime_requirements(target: dict[str, str], runtime: Path) -> None:
    if target["platform"] == "windows":
        required = (runtime / "python.exe", runtime / "neurobridge_affective_bridge.exe")
    else:
        required = (runtime / "bin/python", runtime / "bin/neurobridge_affective_bridge")
    missing = [str(item.relative_to(runtime)) for item in required if not item.is_file()]
    if missing:
        raise ValueError(f"runtime is missing required files: {', '.join(missing)}")


def source_reference(commit: str) -> list[dict[str, str]]:
    # The source commit is immutable and is the only source input created by
    # this repository.  The digest records the exact ref in the verification
    # contract without copying a source archive into the installer.
    try:
        source_archive = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=ROOT, stderr=subprocess.DEVNULL)
        commit_digest = hashlib.sha256(source_archive).hexdigest()
    except (OSError, subprocess.CalledProcessError):
        commit_digest = hashlib.sha256(commit.encode("ascii")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    return [{"kind": "other", "url": f"https://github.com/Entertech/NeuroBridge/commit/{commit}", "retrievedAt": now, "sha256": commit_digest}]


def wix_id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def xml_escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def wix_directory_tree(root: Path, source_root: Path, install_id: str) -> tuple[list[str], list[str]]:
    directories: list[str] = []
    components: list[str] = []

    def visit(directory: Path, parent_id: str) -> None:
        for item in sorted(directory.iterdir(), key=lambda value: value.name.lower()):
            if item.is_dir():
                directory_id = wix_id("D_", item.relative_to(source_root).as_posix())
                directories.append(f'<Directory Id="{directory_id}" Name="{xml_escape(item.name)}">')
                visit(item, directory_id)
                directories.append("</Directory>")
            elif item.is_file():
                relative = item.relative_to(source_root).as_posix()
                component_id = wix_id("C_", relative)
                file_id = wix_id("F_", relative)
                if relative == "windows/service.py":
                    file_id = "SERVICE_SCRIPT"
                service = ""
                if relative.replace("/", "\\") == r"runtime\python.exe":
                    service = (
                        '<ServiceInstall Name="NeuroBridge" DisplayName="NeuroBridge Gateway" '
                        'Description="USB serial gateway to loopback WebSocket" Type="ownProcess" '
                        'Start="auto" ErrorControl="normal" Arguments="&quot;[#SERVICE_SCRIPT]&quot;" />'
                        '<ServiceControl Name="NeuroBridge" Start="install" Stop="both" Remove="uninstall" Wait="yes" />'
                    )
                components.append(
                    f'<Component Id="{component_id}" Guid="*" Directory="{parent_id}">'
                    f'<File Id="{file_id}" Source="{xml_escape(str(item))}" KeyPath="yes" />{service}</Component>'
                )

    visit(source_root, install_id)
    return directories, components


def write_wix_msi(stage: Path, target: dict[str, str], output: Path, log: Path) -> None:
    if shutil.which("wix") is None:
        raise RuntimeError("WiX v7 CLI 'wix' is required to build Windows installers")
    source_root = stage / "opt" / "neurobridge"
    install_id = "INSTALLFOLDER"
    directories, components = wix_directory_tree(source_root, source_root, install_id)
    standard = "ProgramFiles64Folder" if target["architecture"] == "x86_64" else "ProgramFilesFolder"
    upgrade_code = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Entertech/NeuroBridge"))
    wix = textwrap.dedent(
        f'''\
        <Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
          <Package Name="NeuroBridge" Manufacturer="Entertech" Version="{APPLICATION_VERSION}" UpgradeCode="{upgrade_code}">
            <SummaryInformation Description="NeuroBridge gateway" />
            <MajorUpgrade DowngradeErrorMessage="A newer NeuroBridge version is already installed." />
            <MediaTemplate EmbedCab="yes" />
            <Feature Id="MainFeature" Title="NeuroBridge" Level="1">
              <ComponentGroupRef Id="ApplicationFiles" />
            </Feature>
          </Package>
          <Fragment>
            <StandardDirectory Id="{standard}">
              <Directory Id="{install_id}" Name="NeuroBridge">
                {''.join(directories)}
              </Directory>
            </StandardDirectory>
            <ComponentGroup Id="ApplicationFiles">
              {''.join(components)}
            </ComponentGroup>
          </Fragment>
        </Wix>
        '''
    )
    wix_path = stage / "neurobridge.wxs"
    wix_path.write_text(wix, encoding="utf-8")
    command(["wix", "build", "-acceptEula", "wix7", str(wix_path), "-arch", WINDOWS_ARCH[target["architecture"]], "-o", str(output)], cwd=stage, log=log)


def write_wix_bundle(msi: Path, output: Path, log: Path) -> None:
    bundle = msi.parent / "neurobridge-bundle.wxs"
    upgrade_code = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Entertech/NeuroBridge/bundle"))
    bundle.write_text(textwrap.dedent(f'''\
      <Wix xmlns="http://wixtoolset.org/schemas/v4/wxs" xmlns:bal="http://wixtoolset.org/schemas/v4/wxs/bal">
        <Bundle Name="NeuroBridge" Version="{APPLICATION_VERSION}" Manufacturer="Entertech" UpgradeCode="{upgrade_code}">
          <BootstrapperApplication>
            <bal:WixStandardBootstrapperApplication Theme="hyperlinkLicense" LicenseUrl="https://github.com/Entertech/NeuroBridge" />
          </BootstrapperApplication>
          <Chain>
            <MsiPackage SourceFile="{xml_escape(str(msi))}" />
          </Chain>
        </Bundle>
      </Wix>
    '''), encoding="utf-8")
    command(["wix", "build", "-acceptEula", "wix7", str(bundle), "-ext", "WixToolset.BootstrapperApplications.wixext/7.0.0", "-o", str(output)], cwd=msi.parent, log=log)


def build_windows(stage: Path, target: dict[str, str], output: Path, log: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="neurobridge-wix-") as directory:
        msi = Path(directory) / f"neurobridge-{APPLICATION_VERSION}-{target['id']}.msi"
        write_wix_msi(stage, target, msi, log)
        if target["format"] == "msi":
            shutil.copy2(msi, output)
        else:
            write_wix_bundle(msi, output, log)


def deb_control(target: dict[str, str]) -> str:
    package_name = f"neurobridge-{target['edition']}"
    return textwrap.dedent(f'''\
        Package: {package_name}
        Version: {APPLICATION_VERSION}
        Section: utils
        Priority: optional
        Architecture: {DEB_ARCH[target['architecture']]}
        Maintainer: Entertech <support@entertech.cn>
        Description: NeuroBridge USB serial gateway ({target['edition']})
    ''')


def deb_scripts(root: Path, target: dict[str, str]) -> None:
    debian = root / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(deb_control(target), encoding="utf-8")
    (debian / "postinst").write_text("""#!/bin/sh\nset -eu\ngetent group neurobridge >/dev/null 2>&1 || addgroup --system neurobridge || true\nid -u neurobridge >/dev/null 2>&1 || adduser --system --ingroup neurobridge --no-create-home --shell /usr/sbin/nologin neurobridge || true\ninstall -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge/recordings /var/log/neurobridge /etc/neurobridge\n[ -e /etc/neurobridge/gateway.toml ] || install -o root -g neurobridge -m 0640 /opt/neurobridge/gateway.toml.example /etc/neurobridge/gateway.toml\ninstall -m 0644 /opt/neurobridge/packaging/neurobridge.service /etc/systemd/system/neurobridge.service\nsystemctl daemon-reload || true\nsystemctl enable neurobridge.service || true\nexit 0\n""", encoding="utf-8")
    (debian / "prerm").write_text("#!/bin/sh\nset -u\nsystemctl disable --now neurobridge.service 2>/dev/null || true\nexit 0\n", encoding="utf-8")
    (debian / "postrm").write_text("#!/bin/sh\nset -u\nif [ \"$1\" = remove ]; then rm -f /etc/systemd/system/neurobridge.service; systemctl daemon-reload || true; fi\nexit 0\n", encoding="utf-8")
    for item in debian.iterdir():
        item.chmod(0o755 if item.name != "control" else 0o644)


def build_deb(stage: Path, target: dict[str, str], output: Path, log: Path) -> None:
    if shutil.which("dpkg-deb") is None:
        raise RuntimeError("dpkg-deb is required to build DEB installers")
    # Keep the service unit beside the payload and install it from postinst.
    service = stage / "opt/neurobridge/packaging/neurobridge.service"
    shutil.copy2(ROOT / "packaging/kylin/neurobridge.service", service)
    deb_scripts(stage, target)
    command(["dpkg-deb", "--build", "--root-owner-group", str(stage), str(output)], cwd=stage.parent, log=log)


def rpm_spec(target: dict[str, str], topdir: Path) -> Path:
    spec = topdir / "SPECS/neurobridge.spec"
    spec.parent.mkdir(parents=True, exist_ok=True)
    package_name = f"neurobridge-{target['edition']}"
    spec.write_text(textwrap.dedent(f'''\
        Name: {package_name}
        Version: {APPLICATION_VERSION}
        Release: 1
        Summary: NeuroBridge USB serial gateway ({target['edition']})
        License: Proprietary
        BuildArch: {RPM_ARCH[target['architecture']]}
        AutoReqProv: no

        %description
        NeuroBridge USB serial gateway.

        %install
        mkdir -p %{{buildroot}}/opt/neurobridge
        cp -a %{{_sourcedir}}/payload/. %{{buildroot}}/opt/neurobridge/

        %post
        getent group neurobridge >/dev/null 2>&1 || groupadd --system neurobridge || true
        id -u neurobridge >/dev/null 2>&1 || useradd --system --gid neurobridge --home-dir /nonexistent --shell /usr/sbin/nologin neurobridge || true
        install -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge/recordings /var/log/neurobridge /etc/neurobridge
        test -e /etc/neurobridge/gateway.toml || install -o root -g neurobridge -m 0640 /opt/neurobridge/gateway.toml.example /etc/neurobridge/gateway.toml
        install -m 0644 /opt/neurobridge/packaging/neurobridge.service /etc/systemd/system/neurobridge.service
        systemctl daemon-reload || true
        systemctl enable neurobridge.service || true

        %preun
        if [ "$1" -eq 0 ]; then systemctl disable --now neurobridge.service 2>/dev/null || true; fi

        %postun
        if [ "$1" -eq 0 ]; then rm -f /etc/systemd/system/neurobridge.service; systemctl daemon-reload || true; fi

        %files
        /opt/neurobridge
    '''), encoding="utf-8")
    return spec


def build_rpm(stage: Path, target: dict[str, str], output: Path, log: Path) -> None:
    if shutil.which("rpmbuild") is None:
        raise RuntimeError("rpmbuild is required to build RPM installers")
    topdir = stage.parent / "rpmbuild"
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
        (topdir / name).mkdir(parents=True, exist_ok=True)
    payload = topdir / "SOURCES/payload"
    shutil.copytree(stage / "opt/neurobridge", payload)
    spec = rpm_spec(target, topdir)
    command(["rpmbuild", "-bb", "--target", RPM_ARCH[target["architecture"]], "--define", f"_topdir {topdir}", str(spec)], cwd=stage.parent, log=log)
    candidates = sorted((topdir / "RPMS").rglob("*.rpm"))
    if len(candidates) != 1:
        raise RuntimeError(f"rpmbuild produced {len(candidates)} RPM files")
    shutil.copy2(candidates[0], output)


def validate_native(path: Path, target: dict[str, str], log: Path) -> None:
    expected = {"exe": b"MZ", "msi": bytes.fromhex("d0cf11e0a1b11ae1"), "deb": b"!<arch>\n", "rpm": bytes.fromhex("edabeedb")}[target["format"]]
    with path.open("rb") as source:
        if source.read(len(expected)) != expected:
            raise ValueError(f"{path.name} is not a native {target['format']} installer")
    with log.open("a", encoding="utf-8") as output:
        output.write(f"native format validation: passed ({target['format']})\n")
    if target["format"] == "deb":
        command(["dpkg-deb", "--info", str(path)], log=log)
    elif target["format"] == "rpm":
        command(["rpm", "-qp", "--info", str(path)], log=log)


def build(target: dict[str, str], runtime: Path, output_dir: Path) -> Path:
    runtime_requirements(target, runtime)
    output_dir.mkdir(parents=True, exist_ok=True)
    log = output_dir / "validation.log"
    log.write_text(json.dumps({"targetId": target["id"], "sourceCommit": command(["git", "rev-parse", "HEAD"])}, ensure_ascii=False) + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="neurobridge-native-") as directory:
        stage = Path(directory) / "package-root"
        copy_source(stage, target, runtime)
        input_sha = digest_tree(stage)
        filename = f"neurobridge-{APPLICATION_VERSION}-{target['id']}.{target['format']}"
        output = output_dir / filename
        if target["platform"] == "windows":
            build_windows(stage, target, output, log)
        elif target["format"] == "deb":
            build_deb(stage, target, output, log)
        else:
            build_rpm(stage, target, output, log)
        validate_native(output, target, log)
        runtime_sha = digest_tree(runtime)
    commit = command(["git", "rev-parse", "HEAD"])
    report = {
        "targetId": target["id"],
        "targetArchitecture": target["architecture"],
        "sourceCommit": commit,
        "fileName": output.name,
        "sha256": digest(output),
        "automatedValidation": "passed",
        "validationLog": log.name,
        "toolchain": f"native-package-builder; platform={sys.platform}; python={sys.version.split()[0]}; format={target['format']}",
        "runtimeSha256": runtime_sha,
        "inputSha256": input_sha,
        "sourceReferences": source_reference(commit),
        "builtAt": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        package = build(target_for(args.target), args.runtime_dir.resolve(), args.output_dir.resolve())
        print(package)
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"native package build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
