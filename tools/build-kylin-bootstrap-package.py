#!/usr/bin/env python3
"""Build the small Kylin installer that fetches its runtime at install time.

The package this builds contains the manifest, the fetch script and the
install script.  It does not contain a Python runtime or the algorithm bridge;
those are produced once by ``tools/build-kylin-runtime-archive.sh`` and arrive
on each machine through the manifest URL or a local copy of the same archive.

Building the package needs only ``dpkg-deb`` or ``rpmbuild``.  It does not need
a Kylin machine, because nothing inside the package is compiled.
"""

from __future__ import annotations

import argparse
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
PAYLOAD_DIR = Path("/usr/lib/neurobridge-bootstrap")
DEB_ARCH = "amd64"
RPM_ARCH = "x86_64"


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


def manifest_ready() -> bool:
    """A package may only advertise a download when the manifest names a real archive."""
    text = MANIFEST.read_text(encoding="utf-8")
    sha = _quoted(text, "sha256")
    url = _quoted(text, "url")
    return len(sha) == 64 and bool(url)


def _quoted(text: str, key: str) -> str:
    for line in text.splitlines():
        prefix = f'{key} = "'
        if line.startswith(prefix) and line.endswith('"'):
            return line[len(prefix):-1]
    raise ValueError(f"manifest has no quoted '{key}' entry")


def stage_payload(root: Path) -> None:
    payload = root / PAYLOAD_DIR.relative_to("/")
    payload.mkdir(parents=True)
    shutil.copy2(MANIFEST, payload / "kylin-runtime-manifest.toml")
    for script in (FETCH, INSTALL):
        destination = payload / script.name
        shutil.copy2(script, destination)
        destination.chmod(0o755)


def deb_control() -> str:
    return textwrap.dedent(f"""\
        Package: neurobridge-bootstrap
        Version: {APPLICATION_VERSION}
        Section: utils
        Priority: optional
        Architecture: {DEB_ARCH}
        Maintainer: Entertech <support@entertech.cn>
        Depends: ca-certificates
        Description: NeuroBridge installer that fetches its runtime
         Installs the NeuroBridge gateway by downloading, or reading a local
         copy of, the runtime archive recorded in its manifest.
    """)


def write_deb(root: Path, output: Path) -> None:
    if shutil.which("dpkg-deb") is None:
        raise RuntimeError("dpkg-deb is required to build the bootstrap deb")
    debian = root / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(deb_control(), encoding="utf-8")
    (debian / "postinst").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'echo "NeuroBridge bootstrap package installed."\n'
        'echo "Run, as root: /usr/lib/neurobridge-bootstrap/bootstrap-install.sh"\n'
        'echo "A machine that cannot reach the publish location passes --local-archive <file>."\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (debian / "postinst").chmod(0o755)
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
        Summary: NeuroBridge installer that fetches its runtime
        License: Proprietary
        BuildArch: {RPM_ARCH}
        AutoReqProv: no

        %description
        Installs the NeuroBridge gateway by downloading, or reading a local
        copy of, the runtime archive recorded in its manifest.

        %install
        mkdir -p %{{buildroot}}/usr/lib/neurobridge-bootstrap
        cp -a %{{_sourcedir}}/payload/. %{{buildroot}}/usr/lib/neurobridge-bootstrap/

        %post
        echo "NeuroBridge bootstrap package installed."
        echo "Run, as root: /usr/lib/neurobridge-bootstrap/bootstrap-install.sh"
        echo "A machine that cannot reach the publish location passes --local-archive <file>."

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
    for required in (MANIFEST, FETCH, INSTALL):
        if not required.is_file():
            raise ValueError(f"missing bootstrap input: {required.relative_to(ROOT)}")
    if not manifest_ready():
        raise ValueError(
            "config/kylin-runtime-manifest.toml has no archive sha256 or download URL. "
            "Build and publish the runtime archive before building the installer."
        )
    filename = f"neurobridge-bootstrap-{APPLICATION_VERSION}-kylin-v10-x86_64.{fmt}"
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
