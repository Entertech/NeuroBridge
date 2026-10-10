#!/usr/bin/env python3
"""Build the Kylin package that both produces and installs the runtime.

The package carries two things.  One is the per-machine installer: a manifest,
the fetch script and the install script, which download the runtime or read a
local copy of it.  The other is the one-time build: the gateway source, the
vendored algorithm SDK, the pinned Python archive and wheels, and the setup
scripts that turn them into a runtime.  Every package configure invokes ``bootstrap-build.sh`` to build and install
the runtime on the target machine. A previously exported runtime can also be
installed explicitly through ``bootstrap-install.sh``.

The package does not contain a compiled runtime.  Building it needs only
``dpkg-deb`` or ``rpmbuild`` and does not need a Kylin machine.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neurobridge.versioning import APPLICATION_VERSION
from tools.kylin_inputs import CACHE, catalog, verified_input


MANIFEST = ROOT / "config" / "kylin-runtime-manifest.toml"
FETCH = ROOT / "packaging" / "kylin" / "fetch-runtime.sh"
INSTALL = ROOT / "packaging" / "kylin" / "bootstrap-install.sh"
BUILD = ROOT / "packaging" / "kylin" / "bootstrap-build.sh"
PAYLOAD_DIR = Path("/usr/lib/neurobridge-bootstrap")
DEB_ARCH = "all"
RPM_ARCH = "noarch"

# postrm/%postun run after the package payload has been deleted, so these
# helpers must be embedded in the maintainer scripts rather than sourced.
UNIT_HELPERS = """unit=/etc/systemd/system/neurobridge.service
unit_is_ours() {
  [ -f "$unit" ] || return 1
  grep -q '^ExecStart=/opt/neurobridge/runtime/bin/python ' "$unit"
}
stop_our_unit() {
  unit_is_ours || return 0
  systemctl stop neurobridge.service || return 1
  if systemctl is-active --quiet neurobridge.service; then
    echo "neurobridge.service is still active; stop it before removing this package." >&2
    return 1
  fi
  systemctl disable neurobridge.service || return 1
}
remove_our_unit() {
  unit_is_ours || return 0
  rm -f -- "$unit" || return 1
  systemctl daemon-reload
}
remove_our_serial_rule() {
  rule=/etc/udev/rules.d/70-neurobridge-usb-serial.rules
  [ ! -L "$rule" ] && [ -f "$rule" ] || return 0
  grep -Fxq '# Managed by neurobridge-bootstrap: USB serial access' "$rule" || return 0
  rm -f -- "$rule" || return 1
  udevadm control --reload-rules || return 1
  udevadm trigger --action=change --subsystem-match=tty --sysname-match='ttyUSB*' || return 1
  udevadm trigger --action=change --subsystem-match=tty --sysname-match='ttyACM*' || return 1
  udevadm settle --timeout=10
}"""

# What the one-time build needs from the repository, copied into the package so
# the Kylin machine that runs it does not need its own checkout.  Paths are
# relative to the repository root.
SOURCE_FILES = (
    "requirements.lock",
    "pyproject.toml",
    "sdk.lock",
)
SOURCE_DIRS = (
    "neurobridge",
    "web",
    "config",
    "linux",
    # The bridge sources live under mac/ even though the Kylin build compiles
    # them: linux/build-algorithm-bridge.sh points cmake at mac/algorithm_bridge.
    "mac/algorithm_bridge",
    "packaging/kylin",
    "tools",
    "third_party",
)
# The pinned Python archive and wheels live under packaging/kylin/offline,
# because python-runtime/ and wheelhouse/ are gitignored and a CI checkout does
# not have them.  The setup scripts read them from the top of the source tree,
# so they are staged there inside the package.
OFFLINE_RUNTIME = ROOT / "packaging/kylin/offline"
EXCLUDED = {".git", "__pycache__", ".pytest_cache", "*.pyc"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"command failed ({result.returncode}): {' '.join(args)}")


def stage_payload(root: Path) -> None:
    payload = root / PAYLOAD_DIR.relative_to("/")
    payload.mkdir(parents=True)
    shutil.copy2(MANIFEST, payload / "kylin-runtime-manifest.toml")
    shutil.copy2(ROOT / "config/kylin-bootstrap-inputs.toml", payload)
    for script in (FETCH, INSTALL, BUILD, ROOT / "packaging/kylin/run-logged.sh", ROOT / "packaging/kylin/export-install-logs.sh", ROOT / "packaging/kylin/diagnostic-context.sh", ROOT / "packaging/kylin/platform.sh"):
        destination = payload / script.name
        shutil.copy2(script, destination)
        destination.chmod(0o755)
    shutil.copy2(ROOT / "packaging/kylin/70-neurobridge-usb-serial.rules", payload)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (payload / "build-info.txt").write_text(f"application_version={APPLICATION_VERSION}\nsource_commit={commit}\n", encoding="utf-8")
    source = payload / "source"
    for relative in SOURCE_FILES:
        origin = ROOT / relative
        if not origin.is_file():
            raise ValueError(f"bootstrap source is missing: {relative}")
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
    for relative in SOURCE_DIRS:
        origin = ROOT / relative
        if not origin.is_dir():
            raise ValueError(f"bootstrap source is missing: {relative}")
        shutil.copytree(
            origin, source / relative,
            # The wheelhouse under offline/ is staged at the top of the source
            # tree, where the setup script looks for it.  The CMake archive stays
            # here: setup-kylin-algorithm.sh reads it from offline/ directly.
            ignore=shutil.ignore_patterns(*EXCLUDED, "wheelhouse"),
            dirs_exist_ok=True,
        )
    stage_offline_inputs(source)


def stage_offline_inputs(source: Path) -> None:
    """Place the pinned Python archive and wheels where the setup scripts look.

    The scripts read ``python-runtime/`` and ``wheelhouse/`` at the top of the
    source tree.  Those directories are gitignored, so the package carries its
    own copy under ``packaging/kylin/offline`` and this lays it out for them.
    """
    data = catalog()
    wheel_dest = source / "wheelhouse"
    python_dest = source / "python-runtime"
    offline_dest = source / "packaging/kylin/offline"
    for directory in (wheel_dest, python_dest, offline_dest):
        directory.mkdir(parents=True, exist_ok=True)
    checksums = []
    for key, item in data["artifacts"].items():
        origin = verified_input(item, CACHE, OFFLINE_RUNTIME)
        destination = wheel_dest if key in ("pyserial", "websockets") else offline_dest
        if key == "python_x86_64":
            destination = python_dest
        shutil.copy2(origin, destination / item["filename"])
        if destination == wheel_dest:
            checksums.append(f'{item["sha256"]}  wheelhouse/{item["filename"]}')
    (source / "config/kylin-wheelhouse.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")


def deb_control() -> str:
    return textwrap.dedent(f"""\
        Package: neurobridge-bootstrap
        Version: {APPLICATION_VERSION}
        Section: utils
        Priority: optional
        Architecture: {DEB_ARCH}
        Maintainer: Entertech <support@entertech.cn>
        Depends: ca-certificates, curl, g++, make, tar, xz-utils, udev, libssl-dev, libsqlite3-dev, libbz2-dev, liblzma-dev, libffi-dev, zlib1g-dev
        Description: NeuroBridge installer that builds or fetches its runtime
         Detects Galaxy Kylin V10 OS/CPU and builds a matching runtime from bundled
         locked dependencies and source. A manually reused runtime must match the
         target OS/CPU; new architectures require physical acceptance.
    """)


def write_deb_metadata(root: Path) -> None:
    """Write the Debian control files before the package is archived.

    Split out from the ``dpkg-deb`` invocation so the staged tree can be
    inspected, and so a build can be checked without a Debian toolchain.
    """
    debian = root / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(deb_control(), encoding="utf-8")
    # Each configure deploys the source carried by this package, including
    # same-version rebuilds and repairs. An existing interpreter says nothing
    # about whether the gateway or algorithm matches this package.
    (debian / "postinst").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'if [ "${1:-}" = "configure" ]; then\n'
        "  /usr/lib/neurobridge-bootstrap/bootstrap-build.sh\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (debian / "postinst").chmod(0o755)
    for name, action in (
        ("prerm", 'case "${1:-}" in\n  remove) stop_our_unit ;;\nesac\n'),
        ("postrm", 'case "${1:-}" in\n  remove|purge) remove_our_unit; remove_our_serial_rule ;;\nesac\n'),
    ):
        script = debian / name
        script.write_text("#!/bin/sh\nset -eu\n" + UNIT_HELPERS + "\n" + action, encoding="utf-8")
        script.chmod(0o755)


def write_deb(root: Path, output: Path) -> None:
    if shutil.which("dpkg-deb") is None:
        raise RuntimeError("dpkg-deb is required to build the bootstrap deb")
    write_deb_metadata(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(output)], cwd=root.parent)


def write_rpm(payload: Path, output: Path, work: Path) -> None:
    if shutil.which("rpmbuild") is None:
        raise RuntimeError("rpmbuild is required to build the bootstrap rpm")
    topdir = work / "rpmbuild"
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
        (topdir / name).mkdir(parents=True)
    shutil.copytree(payload, topdir / "SOURCES/payload")
    spec = topdir / "SPECS/neurobridge-bootstrap.spec"
    spec.write_text(textwrap.dedent(f"""\
        Name: neurobridge-bootstrap
        Version: {APPLICATION_VERSION}
        Release: 1
        Summary: NeuroBridge installer that builds or fetches its runtime
        License: Proprietary
        BuildArch: {RPM_ARCH}
        AutoReqProv: no
        Requires: ca-certificates, curl, gcc-c++, make, tar, xz, systemd-udev, openssl-devel, sqlite-devel, bzip2-devel, xz-devel, libffi-devel, zlib-devel

        %description
        Detects Galaxy Kylin V10 OS/CPU and builds a matching runtime from bundled source
        and installs it.  On every other machine, installs a runtime archive
        downloaded or copied from that machine.

        %install
        mkdir -p %{{buildroot}}/usr/lib/neurobridge-bootstrap
        cp -a %{{_sourcedir}}/payload/. %{{buildroot}}/usr/lib/neurobridge-bootstrap/

        %post
        /usr/lib/neurobridge-bootstrap/bootstrap-build.sh || exit 1

        %preun
        {textwrap.indent(UNIT_HELPERS, '        ').lstrip()}
        if [ "$1" -eq 0 ]; then
          stop_our_unit || exit 1
        fi

        %postun
        {textwrap.indent(UNIT_HELPERS, '        ').lstrip()}
        if [ "$1" -eq 0 ]; then
          remove_our_unit || exit 1
          remove_our_serial_rule || exit 1
        fi

        %files
        /usr/lib/neurobridge-bootstrap
    """), encoding="utf-8")
    run(["rpmbuild", "-bb", "--target", RPM_ARCH, "--define", f"_topdir {topdir}", str(spec)], cwd=work)
    built = sorted((topdir / "RPMS").rglob("*.rpm"))
    if len(built) != 1:
        raise RuntimeError(f"rpmbuild produced {len(built)} rpm files")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built[0], output)


def build(fmt: str, output_dir: Path) -> Path:
    for required in (MANIFEST, FETCH, INSTALL, BUILD):
        if not required.is_file():
            raise ValueError(f"missing bootstrap input: {required.relative_to(ROOT)}")
    # The timestamp distinguishes one build from the next.  The version inside
    # the package stays APPLICATION_VERSION; only the file name carries the
    # build time, so two builds of the same version cannot be confused.
    built_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"neurobridge-bootstrap-{APPLICATION_VERSION}-{built_at}-kylin-v10-all.{fmt}"
    output = output_dir / filename
    with tempfile.TemporaryDirectory(prefix="neurobridge-bootstrap-") as directory:
        work = Path(directory)
        root = work / "package-root"
        stage_payload(root)
        if fmt == "deb":
            write_deb(root, output)
        else:
            write_rpm(root / PAYLOAD_DIR.relative_to("/"), output, work)
    if output.stat().st_size < 1024:
        raise RuntimeError(f"bootstrap package is implausibly small: {output}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("deb", "rpm"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        package = build(args.format, args.output_dir.resolve())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"bootstrap package build failed: {error}", file=sys.stderr)
        return 1
    print(f"{package} sha256={digest(package)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
