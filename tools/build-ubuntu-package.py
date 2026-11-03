#!/usr/bin/env python3
"""Ubuntu 24.04 amd64 native DEB; prepare downloads separately from assembly."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TARGET = "ubuntu-24.04-x86_64-deb"
PREFIX = Path("opt/neurobridge")
LOCK = ROOT / "config/ubuntu-package-inputs.toml"
DEPENDENCIES = ("python3.12 (>= 3.12), python3.12 (<< 3.13), libc6 (>= 2.39), "
                "libstdc++6 (>= 13.2), libgcc-s1, bluez, dbus, iproute2, "
                "dnsmasq-base, logrotate, systemd, adduser")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            value.update(f"{path.relative_to(root).as_posix()} {digest(path)}\n".encode())
    return value.hexdigest()


def run(*args: str, **kwargs) -> str:
    print("$ " + subprocess.list2cmdline(args), flush=True)
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **kwargs)
    print(result.stdout, end="", flush=True)
    result.check_returncode()
    return result.stdout.strip()


def write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def wheels(cache: Path, *, download: bool = False) -> list[dict]:
    items = tomllib.loads(LOCK.read_text())["wheels"]
    cache.mkdir(parents=True, exist_ok=True)
    for item in items:
        path = cache / item["filename"]
        if not path.exists() and download:
            if not item["url"].startswith("https://files.pythonhosted.org/"):
                raise ValueError("Unexpected wheel source")
            with urllib.request.urlopen(item["url"], timeout=120) as source:
                content = source.read()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError(f"Download SHA-256 mismatch: {path.name}")
            path.write_bytes(content)
        if not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError(f"Missing or invalid locked wheel: {path}; run --prepare-inputs first")
    return items


def require_host() -> None:
    release = platform.freedesktop_os_release() if sys.platform == "linux" else {}
    if (release.get("ID"), release.get("VERSION_ID"), platform.machine(), sys.version_info[:2]) != ("ubuntu", "24.04", "x86_64", (3, 12)):
        raise ValueError("Build on Ubuntu 24.04 x86_64 with Python 3.12; cross packaging is not supported")
    for tool in ("dpkg-deb", "patchelf", "readelf", "ldd", "cmake", "c++"):
        if not shutil.which(tool):
            raise ValueError(f"Missing build prerequisite: {tool}")


def stage_payload(stage: Path, native: Path, cache: Path) -> None:
    app = stage / PREFIX / "app"
    app.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "neurobridge", app / "neurobridge", ignore=ignore)
    # B-side download/capture pages are shared assets; no Kylin installer or UI.
    shutil.copytree(ROOT / "web", app / "web", ignore=ignore)
    defaults = (ROOT / "config/gateway.ubuntu.toml.example").read_text()
    defaults = defaults.replace("[algorithm]\n", '[algorithm]\ncommand = ["/opt/neurobridge/runtime/bin/neurobridge_affective_bridge"]\n')
    write(app / "defaults.toml", defaults)
    write(stage / "usr/share/neurobridge/gateway.toml.example", defaults)
    python = stage / PREFIX / "python"
    python.mkdir()
    run(sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-compile",
        "--target", str(python), *(str(cache / item["filename"]) for item in wheels(cache)))
    runtime = stage / PREFIX / "runtime/bin"
    runtime.mkdir(parents=True)
    # Fixed private search path, no activation scripts or build-machine venv.
    write(runtime / "python", '#!/bin/sh\nset -eu\nunset PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE\nexport PYTHONPATH=/opt/neurobridge/app:/opt/neurobridge/python\nexport PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1\ncd /\nexec /usr/bin/python3.12 -s "$@"\n', 0o755)
    for name, module in {"neurobridge": "neurobridge.__main__", "neurobridge-dhcp-config": "neurobridge.dhcp"}.items():
        write(runtime / name, f'#!/bin/sh\nexec /opt/neurobridge/runtime/bin/python -m {module} "$@"\n', 0o755)
    # The locked SDK's default CMake build links AffectiveAlgorithm statically.
    # Do not assume a shared SDK library or ship an unrelated build artifact.
    if not (native / "affective-sdk/libAffectiveAlgorithm.a").is_file():
        raise ValueError("Expected statically linked SDK build is missing")
    for source in (native / "bin/neurobridge_affective_bridge",):
        target = runtime / source.name
        shutil.copy2(source, target)
        target.chmod(0o755)
        if "Advanced Micro Devices X86-64" not in run("readelf", "-h", str(target)):
            raise ValueError(f"Wrong native architecture: {source}")
        run("patchelf", "--remove-rpath", str(target))
        if run("patchelf", "--print-rpath", str(target)):
            raise ValueError("Unexpected native RPATH")
        if "not found" in run("ldd", str(target)):
            raise ValueError("Unresolved native dependencies")
    for unit in (ROOT / "linux/ubuntu/systemd").glob("*.service"):
        text = unit.read_text().replace("/opt/neurobridge/venv/bin/", "/opt/neurobridge/runtime/bin/")
        write(stage / "usr/lib/systemd/system" / unit.name, text)
    write(stage / "etc/logrotate.d/neurobridge", (ROOT / "linux/ubuntu/logrotate/neurobridge").read_text())
    write(stage / "usr/share/doc/neurobridge-ubuntu/README.md", (ROOT / "packaging/ubuntu/README.md").read_text())
    # Preserve dependency notices along with installed wheel dist-info licenses.
    for source in (ROOT / "third_party").rglob("LICENSE*"):
        if source.is_file():
            write(stage / "usr/share/doc/neurobridge-ubuntu/licenses" / source.relative_to(ROOT / "third_party"), source.read_text(errors="replace"))


def stage_control(stage: Path, version: str) -> None:
    write(stage / "DEBIAN/control", f"Package: neurobridge-ubuntu\nVersion: {version}\nSection: net\nPriority: optional\nArchitecture: amd64\nMaintainer: Entertech <support@entertech.cn>\nDepends: {DEPENDENCIES}\nConflicts: neurobridge-bootstrap, neurobridge\nDescription: NeuroBridge Ubuntu headband wired gateway\n Precompiled Ubuntu 24.04 amd64 gateway, private Python dependencies and algorithm.\n")
    write(stage / "DEBIAN/conffiles", "/etc/logrotate.d/neurobridge\n")
    common = (ROOT / "packaging/ubuntu/maintainer-common.sh").read_text()
    for name in ("preinst", "postinst", "prerm", "postrm"):
        write(stage / "DEBIAN" / name, (ROOT / "packaging/ubuntu" / name).read_text().replace("@COMMON@", common), 0o755)


def smoke(stage: Path) -> None:
    # Verify staged imports and packaged profile independently of the checkout.
    app, deps = stage / PREFIX / "app", stage / PREFIX / "python"
    code = ("import sys; sys.path[:0] = sys.argv[1:3]; "
            "import bleak, serial, websockets, dbus_fast; "
            "from neurobridge.configuration.runtime import load_runtime_config; "
            "c=load_runtime_config(sys.argv[3]); "
            "assert c.profile=='ubuntu_headband_wired'; "
            "assert c.algorithm.command==('/opt/neurobridge/runtime/bin/neurobridge_affective_bridge',); "
            "print('private dependencies and packaged Ubuntu config: passed')")
    run("/usr/bin/python3.12", "-I", "-c", code, str(app), str(deps), str(stage / "usr/share/neurobridge/gateway.toml.example"))
    output = run(str(stage / PREFIX / "runtime/bin/neurobridge_affective_bridge"), input='{"eegRawBase64":"","hrRawBase64":""}\n')
    response = json.loads(output)
    if not isinstance(response, dict) or "bridgeError" in response or "algorithm" not in response:
        raise ValueError("Bridge smoke did not return an object")


def build(cache: Path, output: Path) -> Path:
    require_host()
    commit = run("git", "rev-parse", "HEAD")
    if run("git", "status", "--porcelain"):
        raise ValueError("Distributable build requires a clean source checkout")
    wheels(cache)
    version = tomllib.loads((ROOT / "neurobridge/version_registry.toml").read_text())["application"]["version"]
    output.mkdir(parents=True, exist_ok=True)
    # Never leave a stale verification report after a failed rebuild.
    (output / "verification.json").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="neurobridge-ubuntu-") as temporary:
        work = Path(temporary)
        run("bash", str(ROOT / "linux/ubuntu/build-algorithm-bridge.sh"), str(ROOT / "third_party"), str(work / "native"))
        stage = work / "stage"
        stage.mkdir()
        stage_payload(stage, work / "native/build/bridge", cache)
        stage_control(stage, version)
        smoke(stage)
        at = datetime.now(timezone.utc).isoformat()
        toolchain = "; ".join(run(*command).splitlines()[0] for command in (("cmake", "--version"), ("c++", "--version"), ("dpkg-deb", "--version"), (sys.executable, "--version")))
        build_info = {"sourceCommit": commit, "applicationVersion": version, "targetId": TARGET,
                      "builtAt": at, "toolchain": toolchain, "signing": "unsigned",
                      "runtimeSha256": tree_digest(stage / PREFIX), "inputSha256": digest(LOCK),
                      "sdkLockSha256": digest(ROOT / "sdk.lock"), "requirementsSha256": digest(ROOT / "requirements.lock")}
        write(stage / PREFIX / "build-info.json", json.dumps(build_info, indent=2) + "\n")
        # dpkg archives own all payloads as root regardless of the build operator.
        package = output / f"neurobridge-{version}-ubuntu-24.04-x86_64.deb"
        run("dpkg-deb", "--root-owner-group", "--build", str(stage), str(package))
        if run("dpkg-deb", "--field", str(package), "Architecture") != "amd64":
            raise ValueError("DEB architecture mismatch")
        run("dpkg-deb", "--info", str(package))
        run("dpkg-deb", "--contents", str(package))
        # Smoke the final DEB's extracted bytes, not only the staging tree.
        run("dpkg-deb", "--extract", str(package), str(work / "extracted"))
        smoke(work / "extracted")
        checksum = digest(package)
        write(output / (package.name + ".sha256"), f"{checksum}  {package.name}\n")
        write(output / "build-info.json", json.dumps(build_info, indent=2) + "\n")
        write(output / "validation.log", f"sourceCommit={commit}\nUbuntu 24.04 amd64 DEB: control metadata, ELF architecture, relocatable RPATH, resolved libraries, private imports, fixed profile and extracted bridge JSON smoke passed.\nInstall/upgrade/remove evidence is generated separately by verify-ubuntu-package.sh. Real systemd/BLE/Ethernet/hardware acceptance pending.\n")
        report = {key: build_info[key] for key in ("sourceCommit", "builtAt", "toolchain", "runtimeSha256", "inputSha256")}
        report.update(targetId=TARGET, targetArchitecture="x86_64", fileName=package.name,
                      sha256=checksum, automatedValidation="passed", validationLog="validation.log",
                      sourceReferences=[{"kind": "dependency", "url": item["url"], "sha256": item["sha256"], "retrievedAt": at} for item in wheels(cache)])
        write(output / "verification.json", json.dumps(report, indent=2) + "\n")
        return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / ".runtime/ubuntu-package-inputs")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/ubuntu")
    parser.add_argument("--prepare-inputs", action="store_true", help="download and SHA-256 verify wheels; no build")
    args = parser.parse_args()
    if args.prepare_inputs:
        wheels(args.cache.resolve(), download=True)
    else:
        print(build(args.cache.resolve(), args.output_dir.resolve()))


if __name__ == "__main__":
    main()
