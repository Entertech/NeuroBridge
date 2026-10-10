from __future__ import annotations

import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from neurobridge.diagnostics import runtime_context


class RuntimeDiagnosticContextTests(unittest.TestCase):
    def test_windows_native_os_architecture_is_not_python_process_bitness(self):
        with patch('neurobridge.diagnostics.sys.platform', 'win32'), \
             patch('neurobridge.diagnostics.sys.getwindowsversion', create=True,
                   return_value=SimpleNamespace(major=10, minor=0, build=19045)), \
             patch('neurobridge.diagnostics.platform.win32_ver', return_value=('10', '', '', '')), \
             patch('neurobridge.diagnostics.struct.calcsize', return_value=4), \
             patch.dict(os.environ, {'PROCESSOR_ARCHITEW6432': 'AMD64', 'PROCESSOR_ARCHITECTURE': 'x86'}, clear=True):
            context = runtime_context()
        self.assertEqual(context['osBuild'], '19045')
        self.assertEqual(context['osArchitecture'], 'AMD64')
        self.assertEqual(context['osBits'], 64)
        self.assertEqual(context['pythonArchitecture'], '32-bit')

    def test_linux_release_is_allowlisted_and_unavailable_release_is_explicit(self):
        with patch('neurobridge.diagnostics.sys.platform', 'linux'), \
             patch('neurobridge.diagnostics.platform.freedesktop_os_release',
                   return_value={'ID': 'kylin', 'PRETTY_NAME': 'Kylin V10', 'VERSION_ID': 'V10',
                                 'BUILD_ID': 'SP1', 'SECRET': 'do-not-export'}):
            context = runtime_context()
        self.assertEqual(context['osName'], 'Kylin V10')
        self.assertEqual(context['osVersion'], 'V10')
        self.assertEqual(context['osBuild'], 'SP1')
        self.assertNotIn('do-not-export', json.dumps(context))
        with patch('neurobridge.diagnostics.sys.platform', 'linux'), \
             patch('neurobridge.diagnostics.platform.freedesktop_os_release', side_effect=OSError('unavailable')):
            self.assertEqual(runtime_context()['environmentQueryError'], 'unavailable')
