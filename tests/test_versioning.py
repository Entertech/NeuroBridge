from __future__ import annotations

import unittest

import neurobridge
from neurobridge.business.gateway import PROTOCOL_VERSION
from neurobridge.versioning import REGISTRY, runtime_version, platform_version


class VersionRegistryTests(unittest.TestCase):
    def test_runtime_versions_are_loaded_from_the_registry(self) -> None:
        self.assertEqual(neurobridge.__version__, runtime_version())
        self.assertEqual(PROTOCOL_VERSION, REGISTRY["northbound_wire_protocol"]["version"])

    def test_platform_runtime_and_delivery_versions_are_independent(self) -> None:
        self.assertEqual(runtime_version('win32'), platform_version('windows'))
        self.assertEqual(runtime_version('linux'), platform_version('kylin'))
        registry = {'platform_releases': {'windows': {'version': '1.2.3'}, 'kylin': {'version': '4.5.6'}}}
        self.assertEqual(platform_version('windows', registry), '1.2.3')
        self.assertEqual(platform_version('kylin', registry), '4.5.6')
        with self.assertRaises(ValueError):
            platform_version('unknown', registry)

    def test_external_document_requires_explicit_authorization_by_default(self) -> None:
        policy = REGISTRY["change_policy"]
        self.assertTrue(policy["external_documents_require_explicit_user_request"])
        self.assertEqual(policy["default_external_document_action"], "record_only")

    def test_protocol_lifecycle_uses_one_released_and_prerelease_version_sequence(self) -> None:
        lifecycle = REGISTRY["protocol_lifecycle"]
        self.assertEqual(REGISTRY["documents"]["external_northbound"]["current_version"], lifecycle["released_version"])
        self.assertEqual(REGISTRY["documents"]["internal_prerelease"]["version"], lifecycle["prerelease_version"])
