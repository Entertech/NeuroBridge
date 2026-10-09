"""Contract tests for the release input record.

Every platform job of the native-inputs workflow writes a ``status-<targetId>.txt``
record, and ``tools/render-input-summary.sh`` turns all of them into one plain-text
block that the workflow appends to the run summary.  That block is the only place
a missing installer is visible without downloading artifacts, and it is written so
it can be pasted into a chat message unchanged -- so its shape is a contract:
failures first, then declared blockers, then successes.

The tests run the real script rather than asserting on its text, because the
failure they guard against is a target silently dropping out of the record.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/render-input-summary.sh"


def record(target: str, outcome: str, reason: str, *, bom: bool = False, crlf: bool = False) -> bytes:
    newline = "\r\n" if crlf else "\n"
    body = f"target={target}{newline}outcome={outcome}{newline}reason={reason}{newline}"
    return ("\ufeff" if bom else "").encode("utf-8") + body.encode("utf-8")


class InputSummaryTests(unittest.TestCase):
    def render(self, records: dict[str, bytes], **env: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            statuses = Path(directory) / "statuses"
            statuses.mkdir(parents=True, exist_ok=True)
            for index, (name, payload) in enumerate(records.items()):
                folder = statuses / f"artifact-{index}"
                folder.mkdir(parents=True)
                (folder / name).write_bytes(payload)
            result = subprocess.run(
                ["bash", str(SCRIPT), str(statuses)],
                text=True,
                capture_output=True,
                env={**os.environ, **env},
                check=True,
            )
            return result.stdout

    def test_failures_come_before_blocked_and_built(self) -> None:
        output = self.render(
            {
                "status-windows-10-x86_64-exe.txt": record(
                    "windows-10-x86_64-exe", "built", "native package produced and validated"
                ),
                "status-windows-7-x86_64-exe.txt": record(
                    "windows-7-x86_64-exe", "blocked", "Windows 7 needs the Python 3.8 port"
                ),
                "status-kylin-server-x86_64-rpm.txt": record(
                    "kylin-server-x86_64-rpm", "failed", "native package build failed: rpmbuild exited 1"
                ),
            }
        )
        self.assertLess(output.index("【失败】"), output.index("【声明阻断】"))
        self.assertLess(output.index("【声明阻断】"), output.index("【成功】"))
        self.assertIn(
            "- kylin-server-x86_64-rpm — native package build failed: rpmbuild exited 1",
            output,
        )
        self.assertIn("共 3 个目标：成功 1 · 声明阻断 1 · 失败 1", output)

    def test_a_record_with_a_bom_and_crlf_is_still_read(self) -> None:
        # The Windows job writes its record with PowerShell; tolerance here keeps
        # a stray BOM or CRLF from silently dropping a failed target.
        output = self.render(
            {
                "status-kylin-server-x86_64-rpm.txt": record(
                    "kylin-server-x86_64-rpm", "failed", "native package build failed", bom=True, crlf=True
                )
            }
        )
        self.assertIn("共 1 个目标：成功 0 · 声明阻断 0 · 失败 1", output)
        self.assertIn("- kylin-server-x86_64-rpm — native package build failed", output)

    def test_an_unreadable_outcome_is_treated_as_a_failure(self) -> None:
        # Counting an unparsable record as a success is the one outcome that
        # would hide a missing installer, so the default must be "failed".
        output = self.render({"status-truncated.txt": record("kylin-server-x86_64-rpm", "", "record truncated")})
        self.assertIn("失败 1", output)
        self.assertIn("成功 0", output)

    def test_the_header_carries_the_run_identity(self) -> None:
        output = self.render(
            {},
            GITHUB_RUN_ID="37768877585",
            GITHUB_RUN_ATTEMPT="1",
            GITHUB_REF_NAME="codex/workflow-release-packaging",
            GITHUB_SHA="a1f77e2",
            SOURCE_REF="master",
        )
        self.assertIn("run 37768877585（第 1 次尝试）", output)
        self.assertIn("分支 codex/workflow-release-packaging · commit a1f77e2", output)
        self.assertIn("源码 ref master", output)

    def test_an_empty_run_says_so_instead_of_crashing(self) -> None:
        output = self.render({})
        self.assertIn("没有收集到任何目标记录", output)

    def test_a_missing_directory_is_reported_without_failing(self) -> None:
        result = subprocess.run(["bash", str(SCRIPT), "/nonexistent-statuses"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("没有找到目标记录目录", result.stdout)

    def test_blocked_reasons_are_truncated_for_a_message(self) -> None:
        output = self.render({"status-w.txt": record("windows-7-x86-exe", "blocked", "x" * 500)})
        line = next(line for line in output.splitlines() if line.startswith("- windows-7-x86-exe"))
        self.assertLess(len(line), 200)


if __name__ == "__main__":
    unittest.main()
