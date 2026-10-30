#!/usr/bin/env python3
"""Prepare locked build inputs, without building or publishing the application.

All downloads have immutable versions and reviewed digests. Hosted image drift,
missing snapshot packages and corrupt cached inputs fail instead of falling back.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "config/build-environment.lock.json"
TOOLS = ROOT / ".runtime/build-tools"


def load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def fetch(item: dict, cache: Path | None = None) -> Path:
    cache = cache or TOOLS / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    filename = item["filename"]
    if Path(filename).name != filename or not item["url"].startswith("https://"):
        raise ValueError("Unsafe locked download")
    algorithm = "sha512" if "sha512" in item else "sha256"
    destination = cache / filename
    if destination.exists():
        if digest(destination, algorithm) != item[algorithm]:
            raise ValueError(f"Cached build input digest mismatch: {filename}")
        return destination
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
        partial = Path(stream.name)
        try:
            with urllib.request.urlopen(item["url"], timeout=60) as response:
                shutil.copyfileobj(response, stream)
            stream.close()
            if digest(partial, algorithm) != item[algorithm]:
                raise ValueError(f"Downloaded build input digest mismatch: {filename}")
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
    return destination


def extract(archive: Path, destination: Path):
    """Extract only reviewed tool archives, rejecting traversal and links."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                target = (root / item.filename).resolve()
                if not target.is_relative_to(root) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Unsafe build-tool ZIP member")
            source.extractall(root)
    else:
        with tarfile.open(archive) as source:
            for item in source.getmembers():
                if not (root / item.name).resolve().is_relative_to(root) or not (item.isfile() or item.isdir()):
                    raise ValueError("Unsafe build-tool TAR member")
            source.extractall(root, filter="data")


def command(args: list[str], **kwargs) -> str:
    print("$ " + subprocess.list2cmdline(list(map(str, args))), flush=True)
    result = subprocess.run(list(map(str, args)), check=True, text=True, **kwargs)
    return (result.stdout or "").strip()


def write_evidence(output: Path | None = None, **extra):
    job = os.environ.get("GITHUB_JOB", "local")
    output = output or ROOT / ".runtime/build-environment" / f"{job}.json"
    locks = [LOCK_PATH, ROOT / "requirements.lock", ROOT / "requirements-build.lock", ROOT / "requirements-runtime-build.lock",
             ROOT / "requirements-ci.lock", ROOT / "sdk.lock", ROOT / "config/kylin-bootstrap-inputs.toml"]
    previous = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {}
    python_packages = {}
    for name in load_lock()["python_tools"]:
        try:
            python_packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    evidence = {
        **previous,
        "schemaVersion": 1, "recordedAt": datetime.now(timezone.utc).isoformat(),
        "sourceCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "workflowRunId": os.environ.get("GITHUB_RUN_ID", "local"),
        "workflowRunAttempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"), "job": job,
        "runner": {key: os.environ.get(key, "unavailable") for key in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")},
        "platform": platform.platform(), "python": platform.python_version(),
        "lockSha256": {str(path.relative_to(ROOT)): digest(path) for path in locks},
        "pythonPackages": python_packages,
        **extra,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def check_host(env: dict | None = None):
    env = os.environ if env is None else env
    lock = load_lock()
    image = lock["runner_images"].get(env.get("ImageOS"))
    if not image or env.get("ImageVersion") != image["version"]:
        raise ValueError("Hosted runner image drift: review config/build-environment.lock.json; no floating-image fallback")
    allowed = {lock["python"]["windows"]} if image["label"].startswith("windows") else {
        lock["python"]["linux"], lock["python"]["tests"]}
    if platform.python_version() not in allowed:
        raise ValueError(f"Build Python drift: {platform.python_version()} not in {sorted(allowed)}")
    if env.get("RUNNER_ARCH") != "X64":
        raise ValueError("Build coordinator must be X64; target architectures are a separate contract")


def pip_command(wheel: Path, *args):
    # A wheel has pip/__main__.py, not a top-level __main__.py. Add the
    # verified wheel explicitly, so even an embedded Python with no pip works.
    bootstrap = "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=['pip',*sys.argv[2:]]; runpy.run_module('pip',run_name='__main__')"
    command([sys.executable, "-I", "-c", bootstrap, str(wheel), "--isolated", "--disable-pip-version-check", *args],
            env={**os.environ, "PIP_CONFIG_FILE": os.devnull})


def python_dependencies(runtime=False, ci=False, editable=False):
    wheel = fetch(load_lock()["downloads"]["pip"])
    requirements = [ROOT / "requirements-build.lock"]
    if runtime:
        requirements.append(ROOT / "requirements-runtime-build.lock")
    if ci:
        requirements.append(ROOT / "requirements-ci.lock")
    args = ["install", "--require-hashes", "--only-binary=:all:", "--index-url", "https://pypi.org/simple"]
    for path in requirements:
        args.extend(["-r", str(path)])
    pip_command(wheel, *args)
    if editable:
        # Build isolation would resolve build-system.requires again from PyPI.
        pip_command(wheel, "install", "--no-build-isolation", "--no-deps", "-e", str(ROOT))
    pip_command(wheel, "check")
    write_evidence()


def linux_packages(profile: str):
    check_host()
    lock = load_lock()
    spec = lock["apt"]["profiles"][profile]
    image = os.environ["ImageOS"]
    if (image, spec["suite"]) not in {("ubuntu22", "jammy"), ("ubuntu24", "noble")}:
        raise ValueError("APT profile and runner distribution differ")
    with tempfile.TemporaryDirectory(prefix="neurobridge-apt-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        (directory / "lists/partial").mkdir(parents=True)
        sources = directory / "sources.list"
        # Signed archive metadata and package hashes remain mandatory. Only the
        # expiry of a historical, intentionally frozen snapshot is ignored.
        sources.write_text("".join(
            f"deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/{lock['apt']['snapshot']}/ {spec['suite']}{suffix} main universe\n"
            for suffix in ("", "-updates", "-security")))
        options = ["-o", f"Dir::Etc::sourcelist={sources}", "-o", "Dir::Etc::sourceparts=-",
                   "-o", f"Dir::State::lists={directory / 'lists'}", "-o", "Acquire::Check-Valid-Until=false",
                   "-o", "APT::Update::Error-Mode=any"]
        command(["sudo", "apt-get", *options, "update"])
        command(["sudo", "apt-get", *options, "install", "-y", "--no-install-recommends", "--allow-downgrades",
                 *(f"{name}={version}" for name, version in spec["packages"].items())])
    installed = {}
    for name, version in spec["packages"].items():
        actual = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", name], text=True)
        if actual != version:
            raise ValueError(f"APT version drift: {name}: expected {version}, found {actual}")
        installed[name] = actual
    # Includes the full resolved OS dependency closure, rather than only direct tools.
    packages = subprocess.check_output(["dpkg-query", "-W", "-f=${Package}=${Version}\n"], text=True)
    tool_commands = {
        "docs": {"pandoc": ["--version"], "pdfinfo": ["-v"]},
        "deb": {"dpkg-deb": ["--version"]},
        "native-test": {"cmake": ["--version"], "g++-13": ["--version"], "gcc-13": ["--version"]},
    }
    actual_tools = {name: subprocess.check_output([f"/usr/bin/{name}", *args], stderr=subprocess.STDOUT, text=True).splitlines()[0]
                    for name, args in tool_commands[profile].items()}
    # Only wrap the selected tools; prepending all of /usr/bin could shadow
    # the setup-python interpreter with the distribution's older Python.
    bin_dir = TOOLS / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    names = {"docs": ("pandoc", "pdfinfo", "pdffonts", "pdftoppm"), "deb": ("dpkg-deb",),
             "native-test": ("cmake", "ctest", "gcc-13", "g++-13")}[profile]
    for name in names:
        wrapper = bin_dir / name
        wrapper.write_text(f'#!/bin/sh\nexec /usr/bin/{name} "$@"\n')
        wrapper.chmod(0o755)
    if path := os.environ.get("GITHUB_PATH"):
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(str(bin_dir) + "\n")
    write_evidence(aptProfile=profile, aptSnapshot=lock["apt"]["snapshot"], aptPackages=installed,
                   systemPackages=packages.splitlines(), systemToolVersions=actual_tools)


def append_evidence(log: Path):
    """Keep actual environment evidence inside the existing per-package log."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    check_host()
    write_evidence()
    evidence = ROOT / ".runtime/build-environment" / f"{os.environ['GITHUB_JOB']}.json"
    with log.open("a", encoding="utf-8") as stream:
        stream.write("\nbuild_environment=" + json.dumps(json.loads(evidence.read_text(encoding="utf-8")), sort_keys=True) + "\n")


def github_value(file: str, name: str, value: str):
    if path := os.environ.get(file):
        if "\n" in value or "\r" in value:
            raise ValueError("Invalid GitHub environment value")
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def docs_tools():
    linux_packages("docs")
    item = load_lock()["downloads"]["chrome"]
    destination = TOOLS / "chrome"
    # Always extract verified bytes; never trust a stale executable cache.
    shutil.rmtree(destination, ignore_errors=True)
    extract(fetch(item), destination)
    executable = destination / "chrome-headless-shell-linux64/chrome-headless-shell"
    executable.chmod(0o755)
    actual = subprocess.check_output([str(executable), "--version"], text=True).strip()
    if item["version"] not in actual.split():
        raise ValueError("Chrome for Testing version mismatch")
    github_value("GITHUB_ENV", "CHROME_BIN", str(executable))
    write_evidence(chrome=actual, aptSnapshot=load_lock()["apt"]["snapshot"])


def actionlint():
    item = load_lock()["downloads"]["actionlint"]
    extract(fetch(item), TOOLS / "actionlint")
    executable = TOOLS / "actionlint/actionlint"
    actual = subprocess.check_output([str(executable), "--version"], text=True)
    if item["version"] != actual.splitlines()[0]:
        raise ValueError("actionlint version mismatch")


def windows_tools():
    if sys.platform != "win32":
        raise ValueError("Windows installer tools require Windows")
    downloads = load_lock()["downloads"]
    dotnet_root, wix_root = TOOLS / "dotnet", TOOLS / "wix"
    for path in (dotnet_root, wix_root, TOOLS / "wix-extension", TOOLS / "nuget-feed", TOOLS / "nuget-cache", TOOLS / "dotnet-home"):
        shutil.rmtree(path, ignore_errors=True)
    extract(fetch(downloads["dotnet"]), dotnet_root)
    feed = TOOLS / "nuget-feed"
    feed.mkdir(parents=True, exist_ok=True)
    for key in ("wix", "wixtoolset.bootstrapperapplications.wixext"):
        shutil.copy2(fetch(downloads[key]), feed / downloads[key]["filename"])
    extract(fetch(downloads["wixtoolset.bootstrapperapplications.wixext"]), TOOLS / "wix-extension")
    # Private SDK/runtime and a local, hash-verified NuGet feed. No global
    # preinstalled SDK, user NuGet sources or moving transitive packages.
    config = feed / "NuGet.Config"
    config.write_text('<configuration><packageSources><clear/><add key="locked" value="."/></packageSources></configuration>')
    env = windows_environment()
    dotnet = dotnet_root / "dotnet.exe"
    actual = subprocess.check_output([str(dotnet), "--version"], env=env, text=True).strip()
    if actual != downloads["dotnet"]["version"]:
        raise ValueError(".NET SDK version mismatch")
    command([str(dotnet), "tool", "install", "wix", "--version", downloads["wix"]["version"],
             "--tool-path", str(wix_root), "--configfile", str(config)], env=env)
    version = subprocess.check_output([str(wix_root / "wix.exe"), "--version"], env=env, text=True).strip()
    if not version.startswith(downloads["wix"]["version"] + "+") and version != downloads["wix"]["version"]:
        raise ValueError("WiX version mismatch")
    command([str(wix_root / "wix.exe"), "eula", "accept", "wix7"], env=env)
    for key in ("DOTNET_ROOT", "DOTNET_ROOT_X64", "DOTNET_MULTILEVEL_LOOKUP", "DOTNET_ROLL_FORWARD", "DOTNET_CLI_HOME", "NUGET_PACKAGES"):
        github_value("GITHUB_ENV", key, env[key])
    if path := os.environ.get("GITHUB_PATH"):
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(str(wix_root) + "\n" + str(dotnet_root) + "\n")
    write_evidence(dotnet=actual, wix=version)


def windows_environment() -> dict:
    return {**os.environ, "DOTNET_ROOT": str(TOOLS / "dotnet"), "DOTNET_ROOT_X64": str(TOOLS / "dotnet"),
            "DOTNET_MULTILEVEL_LOOKUP": "0", "DOTNET_ROLL_FORWARD": "LatestPatch",
            "DOTNET_CLI_HOME": str(TOOLS / "dotnet-home"), "NUGET_PACKAGES": str(TOOLS / "nuget-cache"),
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
            "PATH": str(TOOLS / "wix") + os.pathsep + str(TOOLS / "dotnet") + os.pathsep + os.environ.get("PATH", "")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-host", "python", "docs", "deb", "native-test", "actionlint", "windows-tools", "record"))
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--editable", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "check-host":
        try:
            check_host()
        except ValueError as error:
            write_evidence(hostCheck="failed", reason=str(error))
            raise
        write_evidence(hostCheck="passed")
    elif args.command == "python":
        python_dependencies(args.runtime, args.ci, args.editable)
    elif args.command in ("deb", "native-test"):
        linux_packages(args.command)
    elif args.command == "docs":
        docs_tools()
    elif args.command == "actionlint":
        actionlint()
    elif args.command == "windows-tools":
        windows_tools()
    else:
        write_evidence(args.output)


if __name__ == "__main__":
    main()
