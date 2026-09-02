"""Validate a local algorithm bridge and atomically enable it in gateway config."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from .config import load


MANAGED_SECTION = "algorithm"


def _algorithm_block(enabled: bool, command: Path) -> str:
    command_literal = str(command).replace("\\", "\\\\").replace('"', '\\"')
    return f'[algorithm]\nenabled = {str(enabled).lower()}\ncommand = ["{command_literal}"]'


def _replace_algorithm_section(text: str, block: str) -> str:
    lines = text.splitlines()
    retained: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == f"[{MANAGED_SECTION}]":
            index += 1
            while index < len(lines) and not (
                lines[index].strip().startswith("[") and lines[index].strip().endswith("]")
            ):
                index += 1
            continue
        retained.append(lines[index])
        index += 1
    return "\n".join(retained).rstrip() + "\n\n" + block.rstrip() + "\n"


def smoke_test_bridge(bridge: Path, timeout_seconds: float = 5.0) -> dict[str, object]:
    """Prove the native process starts and answers without persisting input/output values."""

    if bridge.is_symlink() or not bridge.is_file() or not os.access(bridge, os.X_OK):
        raise ValueError(f"Algorithm bridge must be an executable regular file, not a symlink: {bridge}")
    request = json.dumps(
        {"timestampMs": 0, "eegRawBase64": "", "hrRawBase64": ""},
        separators=(",", ":"),
    ) + "\n"
    try:
        result = subprocess.run(
            [str(bridge)],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Algorithm bridge smoke test could not complete: {type(error).__name__}") from error
    if result.returncode != 0:
        raise RuntimeError(f"Algorithm bridge smoke test exited with code {result.returncode}")
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise RuntimeError(f"Algorithm bridge smoke test expected one response line; received {len(lines)}")
    try:
        response = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise RuntimeError("Algorithm bridge smoke test returned invalid JSON") from error
    if not isinstance(response, dict) or not isinstance(response.get("algorithm"), dict):
        raise RuntimeError("Algorithm bridge smoke test response has no algorithm object")
    if response.get("bridgeError") or response.get("pocError"):
        raise RuntimeError("Algorithm bridge smoke test reported an internal error")
    return {
        "sha256": sha256(bridge.read_bytes()).hexdigest(),
        "responseFields": sorted(str(key) for key in response),
        "stderrBytes": len(result.stderr.encode("utf-8")),
    }


def apply_algorithm_config(path: Path, *, bridge: Path, enabled: bool = True) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Configuration must be an existing regular file, not a symlink: {path}")
    bridge = bridge.resolve(strict=True)
    if not bridge.is_file() or not os.access(bridge, os.X_OK):
        raise ValueError(f"Algorithm bridge is not executable: {bridge}")
    original_stat = path.stat()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.before-algorithm-{timestamp}")
    suffix = 0
    while backup.exists():
        suffix += 1
        backup = path.with_name(f"{path.name}.before-algorithm-{timestamp}-{suffix}")
    shutil.copy2(path, backup, follow_symlinks=False)
    os.chmod(backup, stat.S_IMODE(original_stat.st_mode))
    if hasattr(os, "chown"):
        os.chown(backup, original_stat.st_uid, original_stat.st_gid)

    updated = _replace_algorithm_section(
        path.read_text(encoding="utf-8"),
        _algorithm_block(enabled, bridge),
    )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        temporary_path = Path(temporary_name)
        os.chmod(temporary_path, stat.S_IMODE(original_stat.st_mode))
        if hasattr(os, "chown"):
            os.chown(temporary_path, original_stat.st_uid, original_stat.st_gid)
        validated = load(temporary_path)
        if validated.algorithm.enabled != enabled or validated.algorithm.command != (str(bridge),):
            raise ValueError("Generated algorithm configuration did not pass validation")
        os.replace(temporary_path, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test and enable a project-local NeuroBridge algorithm bridge"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--check-only", action="store_true", help="Run smoke test without modifying configuration")
    args = parser.parse_args()
    if args.timeout_seconds <= 0 or args.timeout_seconds > 60:
        parser.error("--timeout-seconds must be greater than 0 and at most 60")
    details = smoke_test_bridge(args.bridge, args.timeout_seconds)
    print("Algorithm bridge smoke test: OK")
    print(f"bridge={args.bridge.resolve()}")
    print(f"bridgeSha256={details['sha256']}")
    print(f"responseFields={','.join(details['responseFields'])}")
    print(f"stderrBytes={details['stderrBytes']}")
    print("rawPayloadLogged=false")
    print("algorithmValuesLogged=false")
    if args.check_only:
        print("configChanged=false")
        return 0
    backup = apply_algorithm_config(args.config, bridge=args.bridge, enabled=True)
    print("configChanged=true")
    print(f"config={args.config}")
    print(f"backup={backup}")
    print("algorithmEnabled=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
