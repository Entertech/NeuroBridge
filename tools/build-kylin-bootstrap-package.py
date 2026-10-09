#!/usr/bin/env python3
"""Build the Kylin package that both produces and installs the runtime.

The package carries two things.  One is the per-machine installer: a manifest,
the fetch script and the install script, which download the runtime or read a
local copy of it.  The other is the one-time build: the gateway source, the
vendored algorithm SDK, the pinned Python archive and wheels, and the setup
scripts that turn them into a runtime.  On the single Kylin machine that
produces the runtime, ``bootstrap-build.sh`` runs that build and then installs
the result on the same machine.  Every other machine only runs the installer.

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


MANIFEST = ROOT / "config" / "kylin-runtime-manifest.toml"
FETCH = ROOT / "packaging" / "kylin" / "fetch-runtime.sh"
INSTALL = ROOT / "packaging" / "kylin" / "bootstrap-install.sh"
BUILD = ROOT / "packaging" / "kylin" / "bootstrap-build.sh"
PAYLOAD_DIR = Path("/usr/lib/neurobridge-bootstrap")
DEB_ARCH = "amd64"
RPM_ARCH = "x86_64"

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
    for script in (FETCH, INSTALL, BUILD):
        destination = payload / script.name
        shutil.copy2(script, destination)
        destination.chmod(0o755)
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
            ignore=shutil.ignore_patterns(*EXCLUDED, "offline"),
            dirs_exist_ok=True,
        )
    stage_offline_inputs(source)


def stage_offline_inputs(source: Path) -> None:
    """Place the pinned Python archive and wheels where the setup scripts look.

    The scripts read ``python-runtime/`` and ``wheelhouse/`` at the top of the
    source tree.  Those directories are gitignored, so the package carries its
    own copy under ``packaging/kylin/offline`` and this lays it out for them.
    """
    runtime_dir = OFFLINE_RUNTIME
    archives = sorted(runtime_dir.glob("cpython-*.tar.gz"))
    if len(archives) != 1:
        raise ValueError(f"expected one pinned Python archive in {runtime_dir}, found {len(archives)}")
    wheels = sorted((runtime_dir / "wheelhouse").glob("*.whl"))
    if not wheels:
        raise ValueError(f"no wheels found in {runtime_dir / 'wheelhouse'}")
    python_dest = source / "python-runtime"
    python_dest.mkdir()
    shutil.copy2(archives[0], python_dest / archives[0].name)
    wheel_dest = source / "wheelhouse"
    wheel_dest.mkdir()
    for wheel in wheels:
        shutil.copy2(wheel, wheel_dest / wheel.name)


def deb_control() -> str:
    return textwrap.dedent(f"""\
        Package: neurobridge-bootstrap
        Version: {APPLICATION_VERSION}
        Section: utils
        Priority: optional
        Architecture: {DEB_ARCH}
        Maintainer: Entertech <support@entertech.cn>
        Depends: ca-certificates, curl, g++, libeigen3-dev, tar
        Description: NeuroBridge installer that builds or fetches its runtime
         On one Galaxy Kylin machine, builds the runtime from the bundled
         source and installs it.  On every other machine, installs a runtime
         archive downloaded or copied from that machine.
    """)


def write_deb_metadata(root: Path) -> None:
    """Write the Debian control files before the package is archived.

    Split out from the ``dpkg-deb`` invocation so the staged tree can be
    inspected, and so a build can be checked without a Debian toolchain.
    """
    debian = root / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(deb_control(), encoding="utf-8")
    # dpkg passes "configure" on a fresh install and on upgrade.  Building the
    # runtime takes several minutes and needs the compiler, so it only runs when
    # the package is first installed; an upgrade keeps the runtime already built.
    (debian / "postinst").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'if [ "${1:-}" = "configure" ] && [ -z "${2:-}" ]; then\n'
        "  /usr/lib/neurobridge-bootstrap/bootstrap-build.sh\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (debian / "postinst").chmod(0o755)


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
        Requires: ca-certificates, curl, gcc-c++, eigen3-devel, tar

        %description
        On one Galaxy Kylin machine, builds the runtime from the bundled source
        and installs it.  On every other machine, installs a runtime archive
        downloaded or copied from that machine.

        %install
        mkdir -p %{{buildroot}}/usr/lib/neurobridge-bootstrap
        cp -a %{{_sourcedir}}/payload/. %{{buildroot}}/usr/lib/neurobridge-bootstrap/

        %post
        # $1 is 1 on a fresh install and 2 or more on an upgrade.  Only a fresh
        # install builds the runtime; an upgrade keeps the one already built.
        if [ "$1" -eq 1 ]; then
          /usr/lib/neurobridge-bootstrap/bootstrap-build.sh
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
    filename = f"neurobridge-bootstrap-{APPLICATION_VERSION}-{built_at}-kylin-v10-x86_64.{fmt}"
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
