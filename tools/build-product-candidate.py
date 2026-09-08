#!/usr/bin/env python3
"""Build unsigned, inspectable Kylin or Windows product candidates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import platform
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neurobridge.versioning import APPLICATION_VERSION, NORTHBOUND_PROTOCOL_VERSION


INCLUDE = ("neurobridge", "web", "requirements.lock", "pyproject.toml")
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", ".venv", "recordings", "logs"}
RUNTIME_REQUIRED = {
    "kylin": ("bin/python", "bin/neurobridge_affective_bridge"),
    "windows": ("python.exe", "neurobridge_affective_bridge.exe"),
}


def copy_payload(destination: Path, platform_name: str) -> None:
    for relative in (*INCLUDE, *(("windows",) if platform_name == "windows" else ())):
        source = ROOT / relative
        target = destination / relative
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(*EXCLUDED_PARTS, "*.pyc"))
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def dependencies() -> list[dict[str, str]]:
    result = []
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#") or "==" not in value:
            continue
        name, version = value.split(";", 1)[0].strip().split("==", 1)
        result.append({"type": "library", "name": name, "version": version})
    return result


def native_dependencies(platform_name: str) -> list[dict[str, str]]:
    lock = tomllib.loads((ROOT / "sdk.lock").read_text(encoding="utf-8"))
    sdk = lock["affective_algorithm_sdk"]
    build = sdk["build"]
    components = [{"type": "library", "name": "AffectiveCloud-Algorithm-SDK", "version": sdk["version"]},
                  {"type": "library", "name": "NumCpp", "version": build["numcpp_version"]}]
    if platform_name == "kylin":
        components.append({"type": "library", "name": "Eigen", "version": build["eigen_versions"]["galaxy_kylin_v10_x86_64"]})
    return components


def source_commit() -> str:
    configured = os.environ.get("GITHUB_SHA")
    try:
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if configured and configured != actual:
            raise ValueError("GITHUB_SHA differs from the checked-out source commit")
        return actual
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def source_dirty() -> bool:
    return bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True).strip())


def validate_runtime(platform_name: str, runtime: Path) -> None:
    missing = [relative for relative in RUNTIME_REQUIRED[platform_name] if not (runtime / relative).is_file()]
    if missing:
        raise ValueError(f"{platform_name} runtime is missing required files: {', '.join(missing)}")
    if platform_name == "kylin":
        non_executable = [relative for relative in RUNTIME_REQUIRED[platform_name] if not os.access(runtime / relative, os.X_OK)]
        if non_executable:
            raise ValueError(f"Kylin runtime files must be executable: {', '.join(non_executable)}")


def build(platform_name: str, output: Path, runtime: Path | None) -> Path:
    if runtime is not None:
        validate_runtime(platform_name, runtime)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="neurobridge-candidate-") as temporary:
        stage = Path(temporary) / f"neurobridge-{APPLICATION_VERSION}-{platform_name}-x86_64"
        payload = stage / "payload"
        payload.mkdir(parents=True)
        copy_payload(payload, platform_name)
        packaging = ROOT / "packaging" / platform_name
        for source in packaging.iterdir():
            if source.is_file():
                shutil.copy2(source, stage / source.name)
        config_source = ROOT / ("config/gateway.toml.example" if platform_name == "kylin" else "windows/gateway.toml.example")
        shutil.copy2(config_source, stage / "gateway.toml.example")
        if runtime is not None:
            shutil.copytree(runtime, payload / "runtime", dirs_exist_ok=True)
        metadata = stage / "metadata"
        metadata.mkdir()
        shutil.copy2(ROOT / "sdk.lock", metadata / "sdk.lock")
        created = datetime.fromtimestamp(int(os.environ.get("SOURCE_DATE_EPOCH", "0")), timezone.utc).isoformat()
        manifest = {
            "schemaVersion": 1,
            "product": "NeuroBridge",
            "applicationVersion": APPLICATION_VERSION,
            "northboundProtocolVersion": NORTHBOUND_PROTOCOL_VERSION,
            "sourceCommit": source_commit(),
            "sourceDirty": source_dirty(),
            "buildKind": "unsigned-development-candidate",
            "targetAcceptancePassed": False,
            "toolchain": {"python": platform.python_version(), "hostPlatform": platform.platform()},
            "nativeDependencyVerification": "not-verified-against-bundled-binaries",
            "platform": platform_name,
            "architecture": "x86_64",
            "signed": False,
            "runtimeBundled": runtime is not None,
            "createdAt": created,
            "files": {str(path.relative_to(stage)): sha256(path.read_bytes()).hexdigest()
                      for path in sorted(stage.rglob("*")) if path.is_file()},
        }
        (metadata / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sbom = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {"component": {"type": "application", "name": "NeuroBridge", "version": APPLICATION_VERSION}},
            "components": dependencies() + native_dependencies(platform_name),
        }
        (metadata / "sbom.cdx.json").write_text(json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (metadata / "THIRD_PARTY_LICENSES.txt").write_text(
            "Candidate dependency inventory; attach authoritative license texts before release.\n"
            + "\n".join(f"{item['name']} {item['version']}" for item in dependencies())
            + "\n",
            encoding="utf-8",
        )
        base = output / stage.name
        if platform_name == "kylin":
            archive = Path(str(base) + ".tar.gz")
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(stage, arcname=stage.name)
        else:
            archive = Path(str(base) + ".zip")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        bundle.write(path, Path(stage.name) / path.relative_to(stage))
    digest = sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("kylin", "windows"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--require-clean", action="store_true", help="Reject development worktrees for traceable CI candidates")
    args = parser.parse_args()
    if args.require_clean and source_dirty():
        parser.error("Candidate requires a clean, committed source tree")
    if args.runtime_dir is not None and not args.runtime_dir.is_dir():
        parser.error("--runtime-dir must be an existing directory")
    print(build(args.platform, args.output_dir, args.runtime_dir))


if __name__ == "__main__":
    main()
