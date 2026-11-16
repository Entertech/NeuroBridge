import unittest
from unittest.mock import patch

from tools.release_pipeline import CONFIG, matrix, select_targets
from tools.release_scope import changed_business_paths, scope_for


SYSTEMS = ("windows-10", "windows-11", "kylin-v10")


class ReleaseScopeTests(unittest.TestCase):
    def test_explicit_windows_version_includes_both_formats(self):
        targets = select_targets(matrix(), "windows-11")
        self.assertEqual([target["format"] for target in targets], ["exe", "msi"])
        self.assertEqual({target["osVersion"] for target in targets}, {"11"})
        with self.assertRaisesRegex(ValueError, "systems must be distinct"):
            select_targets(matrix(), "windows-11,windows-11")
        with self.assertRaisesRegex(ValueError, "systems must be distinct"):
            select_targets(matrix(), "windows-7")

    def test_affected_scope_covers_platform_specific_and_shared_changes(self):
        cases = [
            (["windows/service.py"], ["windows-10", "windows-11"]),
            (["packaging/kylin/bootstrap-install.sh"], ["kylin-v10"]),
            (["neurobridge/algorithm/runner.py"], list(SYSTEMS)),
            (["mac/algorithm_bridge/CMakeLists.txt"], list(SYSTEMS)),
            (["third_party/AffectiveCloud-Algorithm-SDK/cpp/package/cmake/import_library.cmake"], list(SYSTEMS)),
            (["third_party/NumCpp/cmake/NumCppTools.cmake", "windows/service.py"], list(SYSTEMS)),
            (["windows/README.md", ".github/workflows/release.yml"], []),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes), patch("tools.release_scope.changed_business_paths", return_value=changes):
                plan = scope_for("head", dict.fromkeys(SYSTEMS, "previous"), config=CONFIG)
                self.assertEqual(plan["systems"], expected)

    def test_omitted_system_compares_against_its_own_last_delivery(self):
        baselines = dict.fromkeys(SYSTEMS, "last-release")
        baselines["windows-11"] = "older-release"
        changes = {"last-release": [], "older-release": ["windows/remote-support.ps1"]}
        with patch("tools.release_scope.changed_business_paths", side_effect=lambda base, head: changes[base]):
            plan = scope_for("head", baselines, config=CONFIG)
        self.assertEqual(plan["systems"], ["windows-11"])
        self.assertEqual(plan["changedFiles"]["windows-11"], ["windows/remote-support.ps1"])

    def test_release_version_metadata_alone_does_not_select_a_system(self):
        pylib = "pyproject.toml"
        runtime = "config/kylin-runtime-manifest.toml"
        registry = "neurobridge/version_registry.toml"

        def git(*args):
            if args[1] == "diff":
                return "\0".join([pylib, runtime, registry])
            ref, path = args[-1].split(":", 1)
            version = "0.3.2" if ref == "base" else "0.4.0"
            if path == pylib:
                return f'[project]\nversion = "{version}"'
            if path == runtime:
                return f'[runtime]\napplication_version = "{version}"\nfile_name = "runtime-{version}.zip"'
            return f'[application]\nversion = "{version}"\n[northbound_wire_protocol]\nversion = "1.1"'

        with patch("tools.release_scope.run", side_effect=git):
            self.assertEqual(changed_business_paths("base", "head"), [])
