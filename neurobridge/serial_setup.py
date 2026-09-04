"""Safely apply the confirmed serial strategy to an existing gateway config."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import tempfile

from .config import load
from .serial.adapter import discover_serial_candidates


MANAGED_SECTIONS = ("data_source", "serial")


def _replace_managed_sections(text: str, blocks: dict[str, str]) -> str:
    lines = text.splitlines()
    retained: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped in {f"[{name}]" for name in MANAGED_SECTIONS}:
            index += 1
            while index < len(lines) and not (
                lines[index].strip().startswith("[") and lines[index].strip().endswith("]")
            ):
                index += 1
            continue
        retained.append(lines[index])
        index += 1
    result = "\n".join(retained).rstrip() + "\n\n"
    result += "\n\n".join(blocks[name].rstrip() for name in MANAGED_SECTIONS) + "\n"
    return result


def apply_serial_config(
    path: Path,
    *,
    device: str,
    handshake_timeout_ms: int,
    command_response_timeout_ms: int,
    data_timeout_seconds: float,
    reconnect_delay_seconds: float,
    stats_interval_seconds: float,
) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Configuration must be an existing regular file, not a symlink: {path}")
    original_stat = path.stat()
    device_literal = device.replace("\\", "\\\\").replace('"', '\\"')
    blocks = {
        "data_source": '[data_source]\ntype = "serial"',
        "serial": (
            "[serial]\n"
            f'device = "{device_literal}"\n'
            'candidate_types = ["ttyACM", "ttyUSB"]\n'
            "baud_rate = 115200\n"
            f"handshake_timeout_ms = {handshake_timeout_ms}\n"
            f"command_response_timeout_ms = {command_response_timeout_ms}\n"
            f"data_timeout_seconds = {data_timeout_seconds:g}\n"
            f"reconnect_delay_seconds = {reconnect_delay_seconds:g}\n"
            f"stats_interval_seconds = {stats_interval_seconds:g}\n"
            "max_buffer_bytes = 65536\n"
            "dtr = false\n"
            "rts = false"
        ),
    }
    updated = _replace_managed_sections(path.read_text(encoding="utf-8"), blocks)
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
        load(temporary_path)
        os.replace(temporary_path, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure NeuroBridge for the confirmed USB serial headset")
    parser.add_argument("--config", type=Path, default=Path("/etc/neurobridge/gateway.toml"))
    parser.add_argument("--device", default="auto", help="auto or an absolute TTY path")
    parser.add_argument("--handshake-timeout-ms", type=int, default=1000)
    parser.add_argument("--command-response-timeout-ms", type=int, default=1000)
    parser.add_argument("--data-timeout-seconds", type=float, default=5)
    parser.add_argument("--reconnect-delay-seconds", type=float, default=3)
    parser.add_argument("--stats-interval-seconds", type=float, default=10)
    parser.add_argument("--check-only", action="store_true", help="Validate and list candidates without writing")
    args = parser.parse_args()
    if args.check_only:
        config = load(args.config)
    else:
        apply_serial_config(
            args.config,
            device=args.device,
            handshake_timeout_ms=args.handshake_timeout_ms,
            command_response_timeout_ms=args.command_response_timeout_ms,
            data_timeout_seconds=args.data_timeout_seconds,
            reconnect_delay_seconds=args.reconnect_delay_seconds,
            stats_interval_seconds=args.stats_interval_seconds,
        )
        config = load(args.config)
    candidates = discover_serial_candidates(config.serial)
    print(f"config={args.config}")
    print(f"dataSource={config.data_source.type}")
    print(f"device={config.serial.device}")
    print(f"candidateCount={len(candidates)}")
    for index, candidate in enumerate(candidates, start=1):
        print(f"candidate[{index}]={candidate}")


if __name__ == "__main__":
    main()
