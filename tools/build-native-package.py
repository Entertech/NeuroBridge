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
import os
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
    for item in sorted(item for item in path.rglob("*") if item.is_file() or item.is_symlink()):
        value.update(item.relative_to(path).as_posix().encode("utf-8"))
        if item.is_symlink():
            value.update(b"symlink:" + os.readlink(item).encode("utf-8"))
        else:
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


def target_from_id(target_id: str) -> dict[str, str]:
    """Describe one native package without consulting the release matrix.

    The shipped Kylin deliverable is the bootstrap package, so the release
    matrix no longer lists the prebuilt server/desktop packages. The builder
    that produces those prebuilt packages is still tested against its own
    target shape.
    """
    parts = target_id.split("-")
    if parts[0] == "windows" and len(parts) == 4:
        version, architecture, fmt = parts[1:]
        platform = "windows"
        edition = "none"
    elif parts[0] == "kylin" and len(parts) == 4:
        edition, architecture, fmt = parts[1:]
        platform = "kylin"
        version = "V10"
    else:
        raise ValueError(f"unrecognised native package target: {target_id}")
    if fmt not in {"exe", "msi", "deb", "rpm"}:
        raise ValueError(f"unrecognised native package format: {fmt}")
    return {"id": target_id, "platform": platform, "osVersion": version, "edition": edition, "architecture": architecture, "format": fmt, "runner": "test"}


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
    if target["platform"] == "kylin":
        validate_runtime_links(runtime)
    shutil.copytree(runtime, payload / "runtime", symlinks=target["platform"] == "kylin", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def validate_runtime_links(runtime: Path) -> None:
    root = runtime.resolve()
    for item in runtime.rglob("*"):
        if not item.is_symlink():
            continue
        link = Path(os.readlink(item))
        try:
            resolved = item.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError(f"runtime contains a broken or cyclic link: {item.relative_to(runtime)}") from error
        if link.is_absolute() or not resolved.is_relative_to(root):
            raise ValueError(f"runtime link must be relative and stay inside the bundle: {item.relative_to(runtime)}")


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
            <MajorUpgrade AllowSameVersionUpgrades="yes" DowngradeErrorMessage="A newer NeuroBridge version is already installed." />
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
          <RelatedBundle Action="Upgrade" Id="{upgrade_code}" />
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


# Shared by the DEB and RPM maintainer scripts.  A source deployment writes the
# same unit path (/etc/systemd/system/neurobridge.service) as these packages, so
# a package may only ever stop or delete the unit that it installed itself;
# anything else belongs to another deployment shape and must be left alone.
UNIT_OWNERSHIP = """unit=/etc/systemd/system/neurobridge.service
unit_is_ours() {
  [ -f "$unit" ] || return 1
  grep -q -- 'ExecStart=/opt/neurobridge/runtime/bin/python' "$unit" 2>/dev/null
}"""

REMOVE_HELPER = """remove_our_unit() {
  unit_is_ours || return 0
  rm -f "$unit"
  systemctl daemon-reload 2>/dev/null || true
}"""

STOP_HELPER = """stop_our_unit() {
  unit_is_ours || return 0
  systemctl disable --now neurobridge.service 2>/dev/null || true
  # Restart=always with RestartSec=3 means a stop that is still in flight can
  # resurrect the gateway while dpkg/rpm is deleting its payload.
  attempts=0
  while [ "$attempts" -lt 10 ] && systemctl is-active --quiet neurobridge.service; do
    attempts=$((attempts + 1))
    sleep 1
  done
  if systemctl is-active --quiet neurobridge.service; then
    systemctl kill --signal=SIGTERM neurobridge.service 2>/dev/null || true
    sleep 2
  fi
  systemctl is-active --quiet neurobridge.service && return 1
  return 0
}"""

STOP_REFUSAL = """if ! stop_our_unit; then
  echo "neurobridge.service is still active; refusing to continue so the running" >&2
  echo "gateway is not removed from under itself. Stop it and retry:" >&2
  echo "  systemctl stop neurobridge.service" >&2
  exit 1
fi"""


def indent_block(text: str, spaces: int) -> str:
    """Indent an embedded shell block so textwrap.dedent still sees one margin."""
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


# dpkg calls prerm with "upgrade <new-version>" while the payload being
# upgraded is still the running service.  Stopping there would take the
# gateway down for the whole unpack, and a stop that fails its verification
# would abort the upgrade and leave the old package installed.  The service is
# only stopped for a real removal; the unit file itself is refreshed by the
# following package's postinst.
DEB_UPGRADE_GUARD = """case "${1:-}" in
  upgrade|failed-upgrade|abort-upgrade) exit 0 ;;
esac"""


SERIAL_ACCESS = """configure_serial_access() {
  serial_found=false
  serial_authorized=false
  for device in /dev/ttyACM* /dev/ttyUSB*; do
    [ -c "$device" ] || continue
    serial_found=true
    device_group=$(stat -Lc '%G' -- "$device") || return 1
    [ "$device_group" != root ] || continue
    getent group "$device_group" >/dev/null 2>&1 || continue
    if ! usermod -aG "$device_group" neurobridge; then
      echo "Cannot grant neurobridge access to $device (group $device_group)." >&2
      return 1
    fi
    serial_authorized=true
  done
  if [ "$serial_found" = false ]; then
    echo "No USB serial device found; connect the headset and reinstall this package to grant its device group, then restart neurobridge.service. Installation does not verify capture readiness." >&2
  elif [ "$serial_authorized" = false ]; then
    echo "No usable non-root USB serial group found; check device ownership before reinstalling. Refusing to grant the root group." >&2
    return 1
  fi
}
configure_serial_access || exit 1"""


def deb_scripts(root: Path, target: dict[str, str]) -> None:
    debian = root / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(deb_control(target), encoding="utf-8")
    (debian / "postinst").write_text(f"""#!/bin/sh
set -eu
getent group neurobridge >/dev/null 2>&1 || addgroup --system neurobridge || true
id -u neurobridge >/dev/null 2>&1 || adduser --system --ingroup neurobridge --no-create-home --shell /usr/sbin/nologin neurobridge || true
{SERIAL_ACCESS}
install -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge/recordings /var/log/neurobridge /etc/neurobridge
[ -e /etc/neurobridge/gateway.toml ] || install -o root -g neurobridge -m 0640 /opt/neurobridge/gateway.toml.example /etc/neurobridge/gateway.toml
install -m 0644 /opt/neurobridge/packaging/neurobridge.service /etc/systemd/system/neurobridge.service
systemctl daemon-reload || true
systemctl enable neurobridge.service || true
exit 0
""", encoding="utf-8")
    (debian / "prerm").write_text(f"""#!/bin/sh
set -u
{DEB_UPGRADE_GUARD}
{UNIT_OWNERSHIP}
{STOP_HELPER}
{STOP_REFUSAL}
exit 0
""", encoding="utf-8")
    (debian / "postrm").write_text(f"""#!/bin/sh
set -u
{UNIT_OWNERSHIP}
{REMOVE_HELPER}
case "${{1:-}}" in
  remove|purge) remove_our_unit ;;
esac
exit 0
""", encoding="utf-8")
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
    preun = indent_block(UNIT_OWNERSHIP + "\n" + STOP_HELPER, 10)
    refusal = indent_block(STOP_REFUSAL, 10)
    postun = indent_block(UNIT_OWNERSHIP + "\n" + REMOVE_HELPER, 10)
    serial_access = indent_block(SERIAL_ACCESS, 8)
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
{serial_access}
        install -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge/recordings /var/log/neurobridge /etc/neurobridge
        test -e /etc/neurobridge/gateway.toml || install -o root -g neurobridge -m 0640 /opt/neurobridge/gateway.toml.example /etc/neurobridge/gateway.toml
        install -m 0644 /opt/neurobridge/packaging/neurobridge.service /etc/systemd/system/neurobridge.service
        systemctl daemon-reload || true
        systemctl enable neurobridge.service || true

        %preun
        # $1 is 1 while this package is being upgraded and 0 when it is removed.
        # An upgrade must not stop the service: the new package's %post rewrites
        # the unit and re-enables it, and systemd restarts the process itself.
        if [ "$1" -eq 0 ]; then
{preun}
{refusal}
        fi

        %postun
        if [ "$1" -eq 0 ]; then
{postun}
          remove_our_unit
        fi

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
    shutil.copytree(stage / "opt/neurobridge", payload, symlinks=True)
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
