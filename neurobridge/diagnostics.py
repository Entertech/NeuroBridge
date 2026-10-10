"""Allowlisted version and environment metadata for a running Python process."""

from datetime import datetime, timezone
from pathlib import Path
import platform
import struct
import sys

from .versioning import APPLICATION_VERSION


def _source_commit() -> str:
    root = Path(__file__).resolve().parent.parent
    commit = "unknown"
    try:
        for line in (root / "build-info.txt").read_text(encoding="utf-8").splitlines():
            if line.startswith("source_commit="):
                value = line.partition("=")[2]
                if len(value) == 40 and all(char in "0123456789abcdef" for char in value):
                    commit = value
    except (OSError, UnicodeError):
        pass
    return commit


# Read alongside module loading so an export from an old running process does
# not pick up a newly replaced build-info file and report the new commit.
SOURCE_COMMIT = _source_commit()


def runtime_context() -> dict:
    context = {
        "schemaVersion": 1,
        "diagnosticScope": "runtime",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "applicationVersion": APPLICATION_VERSION,
        "sourceCommit": SOURCE_COMMIT,
        "versionBasis": "loaded application module; environment of exporting process",
        "osName": platform.system(),
        "osVersion": platform.version(),
        "kernelVersion": platform.release(),
        "osArchitecture": platform.machine(),
        "pythonVersion": platform.python_version(),
        "pythonByteOrder": sys.byteorder,
        "pythonArchitecture": f"{struct.calcsize('P') * 8}-bit",
        "environmentQueryError": "",
    }
    try:
        if sys.platform == "win32":
            import os

            native = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE")
            context["osArchitecture"] = native or context["osArchitecture"]
            version = sys.getwindowsversion()
            context["osBuild"] = str(version.build)
            context["osVersion"] = f"{version.major}.{version.minor}.{version.build}"
            context["osName"] = "Windows " + platform.win32_ver()[0]
        elif sys.platform.startswith("linux"):
            release = platform.freedesktop_os_release()
            context.update(osId=release.get("ID", "unknown"), osName=release.get("PRETTY_NAME", "unknown"),
                           osVersion=release.get("VERSION", release.get("VERSION_ID", "unknown")),
                           osBuild=release.get("KYLIN_RELEASE_ID", release.get("BUILD_ID", "unknown")),
                           libcVersion=" ".join(platform.libc_ver()) or "unknown")
    except OSError as error:
        context["environmentQueryError"] = str(error)
    context["osBits"] = {"x86_64": 64, "AMD64": 64, "aarch64": 64, "ARM64": 64,
                         "loongarch64": 64, "loong64": 64, "mips64": 64, "mips64el": 64, "sw64": 64, "sw_64": 64,
                         "armv7l": 32, "armv8l": 32, "armhf": 32, "i386": 32, "i486": 32, "i586": 32, "i686": 32, "x86": 32}.get(context["osArchitecture"], "unknown")
    return context
