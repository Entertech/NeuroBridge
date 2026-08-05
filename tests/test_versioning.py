from __future__ import annotations

import unittest

import neurobridge
from neurobridge.business.gateway import PROTOCOL_VERSION
from neurobridge.versioning import REGISTRY


class VersionRegistryTests(unittest.TestCase):
    def test_runtime_versions_are_loaded_from_the_registry(self) -> None:
        self.assertEqual(neurobridge.__version__, REGISTRY["application"]["version"])
        self.assertEqual(PROTOCOL_VERSION, REGISTRY["northbound_wire_protocol"]["version"])

    def test_external_document_requires_explicit_authorization_by_default(self) -> None:
        policy = REGISTRY["change_policy"]
        self.assertTrue(policy["external_documents_require_explicit_user_request"])
        self.assertEqual(policy["default_external_document_action"], "record_only")

    def test_protocol_lifecycle_uses_one_released_and_prerelease_version_sequence(self) -> None:
        lifecycle = REGISTRY["protocol_lifecycle"]
        self.assertEqual(REGISTRY["documents"]["external_northbound"]["current_version"], lifecycle["released_version"])
        self.assertEqual(REGISTRY["documents"]["internal_prerelease"]["version"], lifecycle["prerelease_version"])
