from __future__ import annotations

import getpass
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "linux/configure-ssh-operations.sh").read_text(encoding="utf-8")


def unprivileged(script: str) -> str:
    # Execute generated helpers without touching accounts, sudo, SSH or services.
    return script.replace("[[ ${EUID} -eq 0 ]]", "true").replace(
        '[[ -n "$project_owner" && "$project_owner" != root ]]',
        '[[ -n "$project_owner" ]]',
    )


def template(variable: str) -> str:
    match = re.search(r'cat >"\$' + variable + r'" <<\'EOF\'\n(.*?)\nEOF', SOURCE, re.S)
    assert match is not None
    return match[1]


class BranchUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="neurobridge ssh ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seed, self.remote, self.work = (self.root / name for name in ("seed", "origin.git", "work"))
        self.marker = self.root / "deployed"
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "SUDO_USER": getpass.getuser(),
            "DEPLOY_MARKER": str(self.marker),
        }
        self.git(self.root, "init", "-b", "baseline", str(self.seed))
        (self.seed / "linux").mkdir()
        for name in ("pyproject.toml", "requirements.lock", "linux/install-ubuntu.sh"):
            (self.seed / name).write_text("# Fixture\n")
        reload = self.seed / "linux/reload-ubuntu.sh"
        reload.write_text('#!/bin/bash\ngit -C "$(dirname "$0")/.." rev-parse HEAD > "$DEPLOY_MARKER"\n')
        reload.chmod(0o755)
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "baseline")
        self.git(self.root, "clone", "--bare", str(self.seed), str(self.remote))
        self.git(self.seed, "remote", "add", "origin", str(self.remote))
        self.git(self.root, "clone", "--single-branch", "--branch", "baseline", str(self.remote), str(self.work))

        self.helper = self.root / "update"
        start = SOURCE.index("\n{\ncat <<'EOF'\n#!/usr/bin/env bash\n# Deploy the trusted SSH operator")
        end = SOURCE.index('\n} >"$update_tmp"', start) + len('\n} >"$update_tmp"')
        subprocess.run(
            ["bash", "-c", SOURCE[start:end]],
            env={**self.env, "project_dir": str(self.work), "update_tmp": str(self.helper)},
            check=True,
        )
        script = unprivileged(self.helper.read_text()).replace(
            'runuser -u "$project_owner" -- /usr/bin/git', "/usr/bin/git"
        )
        self.helper.write_text(script)

    def git(self, directory: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(directory), *args], env=self.env,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def run_update(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.helper), *args], env=self.env, capture_output=True, text=True)

    def publish(self, branch: str = "codex/headband-wire") -> str:
        self.git(self.seed, "switch", "-C", branch, "baseline")
        (self.seed / "feature.txt").write_text("remote feature\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "remote feature")
        self.git(self.seed, "push", "origin", f"HEAD:refs/heads/{branch}")
        return self.git(self.seed, "rev-parse", "HEAD")

    def test_default_uses_current_branch_and_query_does_not_fetch_or_deploy(self) -> None:
        query = self.run_update("--print-branch")
        self.assertEqual((query.returncode, query.stdout.strip()), (0, "baseline"))
        self.assertFalse((self.work / ".git/FETCH_HEAD").exists())
        (self.work / "untracked").touch()
        self.assertEqual(self.run_update("--print-branch").returncode, 0)
        (self.work / "untracked").unlink()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_text().strip(), self.git(self.work, "rev-parse", "HEAD"))
        self.assertEqual(self.git(self.work, "branch", "--show-current"), "baseline")

    def test_remote_only_branch_can_be_selected_from_a_single_branch_clone(self) -> None:
        commit = self.publish()
        result = self.run_update("--branch", "codex/headband-wire")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.work, "branch", "--show-current"), "codex/headband-wire")
        self.assertEqual(self.marker.read_text().strip(), commit)

    def test_current_branch_query_is_not_confused_by_a_tag_with_the_same_name(self) -> None:
        self.git(self.work, "tag", "baseline")
        result = self.run_update("--print-branch")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "baseline"))

    def test_default_fast_forwards_current_branch_even_with_wrong_upstream(self) -> None:
        self.publish()
        self.assertEqual(self.run_update("--branch", "codex/headband-wire").returncode, 0)
        self.git(self.work, "branch", "--set-upstream-to=origin/baseline")
        (self.seed / "feature.txt").write_text("second remote version\n")
        self.git(self.seed, "commit", "-am", "second version")
        self.git(self.seed, "push", "origin", "HEAD:refs/heads/codex/headband-wire")
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_text().strip(), self.git(self.seed, "rev-parse", "HEAD"))

    def test_missing_remote_branch_does_not_switch_or_deploy_stale_fetch_head(self) -> None:
        self.git(self.work, "fetch", "origin", "baseline")
        result = self.run_update("--branch", "missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot fetch", result.stderr)
        self.assertEqual(self.git(self.work, "branch", "--show-current"), "baseline")
        self.assertFalse(self.marker.exists())

    def test_dirty_worktree_stops_before_fetch(self) -> None:
        (self.work / "untracked").touch()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local changes", result.stderr)
        self.assertFalse((self.work / ".git/FETCH_HEAD").exists())
        self.assertFalse(self.marker.exists())

    def test_local_ahead_or_divergent_branch_is_not_deployed(self) -> None:
        for divergent in (False, True):
            with self.subTest(divergent=divergent):
                branch = "codex/divergent" if divergent else "codex/ahead"
                self.publish(branch)
                self.assertEqual(self.run_update("--branch", branch).returncode, 0)
                self.marker.unlink()
                (self.work / "local.txt").write_text("local-only commit\n")
                self.git(self.work, "add", ".")
                self.git(self.work, "commit", "-m", "local only")
                local_commit = self.git(self.work, "rev-parse", "HEAD")
                if divergent:
                    (self.seed / "remote.txt").write_text("remote divergence\n")
                    self.git(self.seed, "add", ".")
                    self.git(self.seed, "commit", "-m", "remote only")
                    self.git(self.seed, "push", "origin", f"HEAD:refs/heads/{branch}")
                result = self.run_update()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ahead of or diverged", result.stderr)
                self.assertEqual(self.git(self.work, "rev-parse", "HEAD"), local_commit)
                self.assertFalse(self.marker.exists())

    def test_detached_head_requires_explicit_branch_but_can_be_queried_safely(self) -> None:
        self.git(self.work, "switch", "--detach")
        for args in ((), ("--print-branch",)):
            result = self.run_update(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("detached HEAD", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.run_update("--branch", "baseline").returncode, 0)

    def test_invalid_arguments_and_tag_only_names_never_deploy(self) -> None:
        self.git(self.seed, "tag", "only-tag")
        self.git(self.seed, "push", "origin", "refs/tags/only-tag")
        for args in (
            ("--branch",), ("baseline",), ("--branch", "baseline", "extra"),
            ("--branch", ""), ("--branch", "-bad"), ("--branch", "HEAD"),
            ("--branch", "@{-1}"), ("--branch", "bad\nname"), ("--branch", "only-tag"),
        ):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_update(*args).returncode, 0)
                self.assertFalse(self.marker.exists())

    def test_valid_shell_metacharacters_in_branch_names_are_passed_literally(self) -> None:
        branch = "codex/topic;literal"
        commit = self.publish(branch)
        result = self.run_update("--branch", branch)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_text().strip(), commit)


class OperationsGateTests(unittest.TestCase):
    def test_argument_forwarding_current_branch_and_failure_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_update = root / "update"
            fake_update.write_text(
                '#!/bin/bash\nif [[ ${1:-} == --print-branch ]]; then echo codex/current; exit 0; fi\n'
                'printf "%s\\n" "$@" > "$ARG_LOG"\nexit "${UPDATE_EXIT:-0}"\n'
            )
            logger = root / "logger"
            logger.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$AUDIT_LOG"\n')
            for file in (fake_update, logger):
                file.chmod(0o755)
            command = root / "command"
            command.write_text(unprivileged(template("command_tmp")).replace(
                "/usr/local/sbin/neurobridge-ops-update", shlex.quote(str(fake_update))
            ).replace("/usr/bin/logger", shlex.quote(str(logger))))
            cli = root / "cli"
            cli.write_text(template("ops_cli_tmp").replace(
                "exec sudo -- /usr/local/sbin/neurobridge-ops-command", "exec bash " + shlex.quote(str(command))
            ))
            args, audit = root / "args", root / "audit"
            env = {**os.environ, "ARG_LOG": str(args), "AUDIT_LOG": str(audit), "SUDO_USER": "fixture"}
            query = subprocess.run(["bash", str(cli), "branch"], env=env, capture_output=True, text=True)
            self.assertEqual((query.returncode, query.stdout.strip()), (0, "codex/current"))
            self.assertFalse(args.exists())
            for options, branch in (([], "codex/current"), (["--branch", "codex/selected"], "codex/selected")):
                result = subprocess.run(["bash", str(cli), "update", *options], env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(args.read_text().splitlines(), ["--branch", branch])
                self.assertIn(f"request=branch:{branch}", audit.read_text())
            audit.unlink()
            result = subprocess.run(["bash", str(cli), "update"], env={**env, "UPDATE_EXIT": "23"}, capture_output=True)
            self.assertEqual(result.returncode, 23)
            self.assertIn("result=failed:23", audit.read_text())
            self.assertNotIn("result=success", audit.read_text())
            args.unlink()
            for options in (("--branch",), ("--branch", "bad\nname"), ("--branch", "-bad"), ("unknown",)):
                result = subprocess.run(["bash", str(cli), "update", *options], env=env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(args.exists())


if __name__ == "__main__":
    unittest.main()
