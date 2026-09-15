"""Resolve and validate the immutable OS/device/access deployment mapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import re
import sys
from typing import Mapping

from ..config import GatewayConfig
from ..domain.capabilities import ProfileCapabilities
from .kylin_headset_local import CAPABILITIES as KYLIN_CAPABILITIES
from .macos_headband_wired import CAPABILITIES as MACOS_CAPABILITIES
from .ubuntu_headband_wired import CAPABILITIES as UBUNTU_CAPABILITIES
from .windows_headset_local import CAPABILITIES as WINDOWS_CAPABILITIES


@dataclass(frozen=True, slots=True)
class RuntimePlatform:
    os_family: str
    architecture: str
    version: str | None = None  # None only for explicitly injected test runtimes.


@dataclass(frozen=True, slots=True)
class DeploymentProfile:
    profile_id: str
    os_family: str
    architecture: str
    transport: str
    device_protocol: str
    access_mode: str
    capabilities: ProfileCapabilities
    delivery_stage: str


_PROFILES = {
    "macos_headband_wired": DeploymentProfile("macos_headband_wired", "macos", "any", "bluetooth", "headband_ble", "wired_b_side", MACOS_CAPABILITIES, "M2"),
    "ubuntu_headband_wired": DeploymentProfile("ubuntu_headband_wired", "ubuntu", "x86_64", "bluetooth", "headband_ble", "wired_b_side", UBUNTU_CAPABILITIES, "M2"),
    "kylin_headset_local": DeploymentProfile("kylin_headset_local", "kylin", "x86_64", "serial", "headset_rev181", "local_browser", KYLIN_CAPABILITIES, "M1"),
    "windows_headset_local": DeploymentProfile("windows_headset_local", "windows", "x86_64", "serial", "headset_rev181", "local_browser", WINDOWS_CAPABILITIES, "M3"),
}


def detect_runtime_platform(os_release: Mapping[str, str] | None = None) -> RuntimePlatform:
    architecture = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())
    if sys.platform == "darwin":
        return RuntimePlatform("macos", architecture)
    if sys.platform == "win32":
        return RuntimePlatform("windows", architecture)
    if sys.platform.startswith("linux"):
        release = dict(os_release or _read_os_release())
        identity = f"{release.get('ID', '')} {release.get('NAME', '')}".lower()
        if "kylin" in identity or "银河麒麟" in identity:
            return RuntimePlatform("kylin", architecture, release.get("VERSION_ID", ""))
        if release.get("ID", "").lower() == "ubuntu":
            return RuntimePlatform("ubuntu", architecture)
        return RuntimePlatform("linux", architecture)
    return RuntimePlatform(sys.platform, architecture)


def resolve_profile(config: GatewayConfig, runtime: RuntimePlatform | None = None) -> DeploymentProfile:
    runtime = runtime or detect_runtime_platform()
    profile_id = config.profile or _legacy_profile_id(config, runtime)
    try:
        profile = _PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported deployment profile: {profile_id}") from exc
    mismatches: list[str] = []
    if profile.os_family == "kylin" and runtime.version is not None and not re.fullmatch(r"[Vv]?10(?:\..*)?", runtime.version):
        mismatches.append("version expected=Kylin V10; runtime version unsupported or missing")
    if config.data_source.window_interval_ms != 600:
        mismatches.append("window_interval_ms expected=600 for the current northbound contract")
    if runtime.os_family != profile.os_family:
        mismatches.append(f"os expected={profile.os_family} actual={runtime.os_family}")
    if profile.architecture != "any" and runtime.architecture != profile.architecture:
        mismatches.append(f"architecture expected={profile.architecture} actual={runtime.architecture}")
    if config.data_source.type != profile.transport:
        mismatches.append(f"transport expected={profile.transport} actual={config.data_source.type}")
    if config.access.mode != profile.access_mode:
        mismatches.append(f"access expected={profile.access_mode} actual={config.access.mode}")
    if profile.capabilities.supports_local_ui != config.local_ui.enabled:
        mismatches.append(f"local_ui expected={profile.capabilities.supports_local_ui} actual={config.local_ui.enabled}")
    if not profile.capabilities.supports_replay and config.recording.replay_recording_id:
        mismatches.append("replay is not supported")
    if mismatches:
        raise ValueError(f"Deployment profile {profile_id} conflicts with runtime/config: {'; '.join(mismatches)}")
    return profile


def _legacy_profile_id(config: GatewayConfig, runtime: RuntimePlatform) -> str:
    expected = {
        "macos": "macos_headband_wired",
        "ubuntu": "ubuntu_headband_wired",
        "kylin": "kylin_headset_local",
        "windows": "windows_headset_local",
    }.get(runtime.os_family)
    if expected is None:
        raise ValueError("profile must be explicit on an unsupported operating system")
    return expected


def _read_os_release() -> dict[str, str]:
    try:
        lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"')
    return result
