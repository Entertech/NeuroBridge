"""Contract tests for the native release input workflow.

The Windows release matrix is declared twice: in ``release/release_matrix.toml``
as the release contract, and in ``.github/workflows/native-inputs.yml`` as the
build matrix.  DEC-010 fixes the Windows matrix at Windows 7/10/11 x x86/x86_64
with EXE and MSI for every combination, so a target that silently disappears from
the workflow would still be counted as an expected release target.  These tests
keep both declarations aligned, and keep every blocked target honest about why it
produced no installer instead of reusing one generic sentence.

Parsing is done with a small line reader rather than a YAML dependency because
``requirements.lock`` intentionally pins only runtime packages.
"""

from __future__ import annotations

import re
from pathlib import Path
import unittest

from tools.release_pipeline import matrix

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/native-inputs.yml"

ID_RE = re.compile(r"^\s+- id: (windows-\S+)\s*$")
REASON_RE = re.compile(r'^\s+blocked_reason: "(.*)"\s*$')

# The only combinations that the locked toolchain can build today: Windows 10/11
# on x86_64.  See doc/tech/源码原生安装包构建.md.
BUILDABLE = {
    "windows-10-x86_64-exe",
    "windows-10-x86_64-msi",
    "windows-11-x86_64-exe",
    "windows-11-x86_64-msi",
}


def windows_entries() -> dict[str, str]:
    """Map each Windows target in the workflow to its blocked_reason ('' when buildable)."""
    entries: dict[str, str] = {}
    current: str | None = None
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        match = ID_RE.match(line)
        if match:
            current = match.group(1)
            entries[current] = ""
            continue
        match = REASON_RE.match(line)
        if match and current is not None:
            entries[current] = match.group(1)
    return entries


class NativeInputsWorkflowTests(unittest.TestCase):
    def test_workflow_matrix_matches_the_release_contract(self) -> None:
        declared = {target["id"] for target in matrix() if target["platform"] == "windows"}
        entries = windows_entries()
        self.assertEqual(
            set(entries),
            declared,
            "native-inputs.yml and release_matrix.toml disagree; both must list the "
            "full DEC-010 Windows matrix",
        )
        self.assertEqual(len(entries), 12)

    def test_buildable_targets_are_the_ones_the_locked_toolchain_supports(self) -> None:
        buildable = {target_id for target_id, reason in windows_entries().items() if reason == ""}
        self.assertEqual(
            buildable,
            BUILDABLE,
            "the set of buildable Windows targets changed; if that is intended, update "
            "this test and doc/tech/源码原生安装包构建.md together",
        )

    def test_every_blocked_target_records_a_matching_reason(self) -> None:
        for target_id, reason in windows_entries().items():
            if reason == "":
                continue
            with self.subTest(target=target_id):
                self.assertTrue(reason.strip(), f"{target_id} is blocked without a reason")
                if target_id.startswith("windows-7-"):
                    self.assertIn(
                        "Python 3.8",
                        reason,
                        f"{target_id} must name the Windows 7 Python 3.8 port as its blocker",
                    )
                if "-x86-" in target_id:
                    self.assertIn(
                        "32-bit algorithm build chain",
                        reason,
                        f"{target_id} must name the missing 32-bit algorithm build chain",
                    )
                if target_id.startswith("windows-7-") and "-x86-" not in target_id:
                    self.assertNotIn(
                        "32-bit",
                        reason,
                        f"{target_id} is x86_64 and must not cite the 32-bit chain",
                    )

    def test_blocked_targets_use_distinct_reasons_per_blocker(self) -> None:
        reasons = {target_id: reason for target_id, reason in windows_entries().items() if reason}
        self.assertEqual(len(reasons), 8)
        windows7_x64 = reasons["windows-7-x86_64-exe"]
        windows10_x86 = reasons["windows-10-x86-exe"]
        self.assertNotEqual(
            windows7_x64,
            windows10_x86,
            "a Windows 7 x86_64 target and a Windows 10 x86 target are blocked by "
            "different causes and must not share one generic sentence",
        )


if __name__ == "__main__":
    unittest.main()
