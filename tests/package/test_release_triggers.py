"""Prevent unchanged-version merges from entering either packaging workflow."""

from pathlib import Path
import re
import subprocess
import unittest

from tests.package.test_native_inputs_workflow import job_block


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"


def condition(block: str) -> str:
    return next(line.removeprefix("    if: ") for line in block.splitlines() if line.startswith("    if:"))


def evaluate(expression: str, values: dict[str, object]) -> bool:
    for name in sorted(values, key=len, reverse=True):
        expression = expression.replace(name, repr(values[name]))
    return bool(eval(expression.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}, {}))


class ReleaseTriggerTests(unittest.TestCase):
    def test_only_version_advancing_master_push_calls_release(self) -> None:
        block = job_block("release", WORKFLOWS / "ci.yml")
        expression = condition(block)
        for event, ref, upgraded, expected in (
            ("push", "refs/heads/master", "false", False),
            ("push", "refs/heads/master", "true", True),
            ("push", "refs/heads/feature", "true", False),
            ("pull_request", "refs/pull/21/merge", "true", False),
            ("workflow_dispatch", "refs/heads/master", "true", False),
            ("push", "refs/heads/master", "", False),
        ):
            with self.subTest(event=event, ref=ref, upgraded=upgraded):
                self.assertEqual(evaluate(expression, {
                    "github.event_name": event, "github.ref": ref,
                    "needs.version-gate.outputs.should_release": upgraded,
                }), expected)
        self.assertIn("uses: ./.github/workflows/release.yml", block)
        self.assertIn("needs: [version-gate, windows-launcher, native-sdk-regression, unit-and-websocket]", block)
        # Default needs semantics block a failed/cancelled test; don't override it.
        self.assertNotRegex(expression, r"always\(|failure\(|cancelled\(")

    def test_push_gate_compares_the_version_before_this_push(self) -> None:
        block = job_block("version-gate", WORKFLOWS / "ci.yml")
        self.assertIn("github.event.pull_request.base.sha || github.event.before", block)
        self.assertIn('gate --base "$BASE_SHA"', block)
        self.assertIn("str(gate['shouldRelease']).lower()", block)
        self.assertIn("should_release: ${{ steps.gate.outputs.should_release }}", block)

    def test_packaging_workflows_do_not_subscribe_to_test_completion(self) -> None:
        for name in ("release.yml", "kylin-bootstrap.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotIn("workflow_run", text)
                self.assertIn("  workflow_dispatch:", text)
        bootstrap = (WORKFLOWS / "kylin-bootstrap.yml").read_text(encoding="utf-8")
        triggers = bootstrap.split("on:\n", 1)[1].split("permissions:", 1)[0]
        self.assertEqual(triggers.strip(), "workflow_dispatch:")
        release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_call:", release)
        self.assertIn("SOURCE_SHA: ${{ github.sha }}", release)

    def test_manual_candidates_cannot_publish(self) -> None:
        expression = condition(job_block("release-publish", WORKFLOWS / "release.yml"))
        for event, upgraded, aggregate, expected in (
            ("push", "true", "success", True),
            ("push", "false", "success", False),
            ("push", "true", "failure", False),
            ("workflow_dispatch", "true", "success", False),
        ):
            with self.subTest(event=event, upgraded=upgraded, aggregate=aggregate):
                self.assertEqual(evaluate(expression, {
                    "github.event_name": event, "github.ref": "refs/heads/master",
                    "needs.release-plan.outputs.should_release": upgraded,
                    "needs.release-aggregate.result": aggregate,
                }), expected)

    def test_cleanup_keeps_test_diagnostics_in_the_shared_run(self) -> None:
        block = job_block("release-cleanup", WORKFLOWS / "release.yml")
        script = block.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in script.splitlines())
        script = re.sub(r"\$\{\{[^}]+\}\}", "fixture", script)
        fake_gh = """gh() {
          if [[ "$*" == *'--method DELETE'* ]]; then
            printf '%s\\n' "${@: -1}"
          else
            printf '%s\\n' '1\trelease-plan-source' '2\trelease-target-kylin' '3\tverified-release-source' '4\twindows-algorithm-x64-source' '5\twindows-algorithm-build-log-source'
          fi
        }
"""
        result = subprocess.run(["bash", "-e", "-c", fake_gh + script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), [
            "repos/fixture/actions/artifacts/1", "repos/fixture/actions/artifacts/2",
        ])


if __name__ == "__main__":
    unittest.main()
