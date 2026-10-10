from __future__ import annotations

import unittest
import runpy
from pathlib import Path
from unittest import mock

import neurobridge
from neurobridge.business.gateway import PROTOCOL_VERSION
from neurobridge.versioning import REGISTRY, APPLICATION_VERSION, application_version


class VersionRegistryTests(unittest.TestCase):
    def test_runtime_versions_are_loaded_from_the_registry(self) -> None:
        self.assertEqual(neurobridge.__version__, APPLICATION_VERSION)
        self.assertEqual(PROTOCOL_VERSION, REGISTRY["northbound_wire_protocol"]["version"])

    def test_external_document_requires_explicit_authorization_by_default(self) -> None:
        policy = REGISTRY["change_policy"]
        self.assertTrue(policy["external_documents_require_explicit_user_request"])
        self.assertEqual(policy["default_external_document_action"], "record_only")

    def test_protocol_lifecycle_uses_one_released_and_prerelease_version_sequence(self) -> None:
        lifecycle = REGISTRY["protocol_lifecycle"]
        self.assertEqual(REGISTRY["documents"]["external_northbound"]["current_version"], lifecycle["released_version"])
        self.assertEqual(REGISTRY["documents"]["internal_prerelease"]["version"], lifecycle["prerelease_version"])

    def test_platform_versions_are_independent_and_old_registries_still_work(self):
        self.assertEqual(application_version('windows'), '0.3.0')
        self.assertEqual(application_version('kylin'), '0.4.0')
        self.assertEqual(application_version('windows', {'application': {'version': '0.2.0'}}), '0.2.0')
        with mock.patch('sys.platform', 'win32'):
            loaded = runpy.run_path(str(Path(neurobridge.__file__).with_name('versioning.py')))
        self.assertEqual(loaded['APPLICATION_VERSION'], application_version('windows'))
