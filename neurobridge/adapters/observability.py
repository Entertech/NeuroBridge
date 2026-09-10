"""Read-only process metrics; missing platform counters are null, never zero."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import threading
import time


class RuntimeProgress:
    """Bounded, monotonic interval accounting for process-lifetime counters."""

    def __init__(self):
        self._started = self._previous_at = time.monotonic()
        self._previous: dict[str, int] = {}
        self._samples = 0

    def sample(self, counters: dict[str, int]) -> dict[str, object]:
        now = time.monotonic()
        elapsed = max(0, now - self._previous_at)
        delta = {key: max(0, value - self._previous.get(key, 0)) for key, value in counters.items()}
        self._samples += 1
        self._previous, self._previous_at = dict(counters), now
        return {
            "sampleSequence": self._samples,
            "uptimeSeconds": round(max(0, now - self._started), 3),
            "intervalSeconds": round(elapsed, 3),
            "delta": delta,
            "framesPerSecond": round(delta.get("frames", 0) / elapsed, 3) if elapsed > 0 else None,
        }


class ProcessMetrics:
    def __init__(self):
        self._wall = time.monotonic()
        self._cpu = time.process_time()

    def sample(self):
        wall, cpu = time.monotonic(), time.process_time()
        values = {"cpuPercentOfOneCore": round(100 * (cpu - self._cpu) / max(wall - self._wall, 1e-9), 3),
                  "threads": threading.active_count(), "tasks": len(asyncio.all_tasks()),
                  "rssBytes": None, "peakRssBytes": None, "fileDescriptors": None, "handles": None}
        self._wall, self._cpu = wall, cpu
        try:
            if sys.platform.startswith("linux"):
                values["rssBytes"] = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                values["fileDescriptors"] = len(os.listdir("/proc/self/fd"))
            elif sys.platform == "darwin":
                import resource
                values["peakRssBytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                values["fileDescriptors"] = len(os.listdir("/dev/fd"))
            elif sys.platform == "win32":
                import ctypes
                from ctypes import wintypes
                class MemoryCounters(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                        (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                        "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetCurrentProcess.restype = wintypes.HANDLE
                process = kernel.GetCurrentProcess()
                count = wintypes.DWORD()
                kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                if kernel.GetProcessHandleCount(process, ctypes.byref(count)):
                    values["handles"] = count.value
                memory = MemoryCounters()
                memory.cb = ctypes.sizeof(memory)
                psapi = ctypes.WinDLL("psapi", use_last_error=True)
                psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
                if psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
                    values["rssBytes"] = memory.WorkingSetSize
                    values["peakRssBytes"] = memory.PeakWorkingSetSize
        except (OSError, ValueError, IndexError):
            pass
        return values
