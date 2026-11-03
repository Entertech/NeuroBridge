"""Platform ownership and field-upgrade compatibility of Linux entrypoints."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LinuxLayoutTests(unittest.TestCase):
    def test_platform_scripts_do_not_call_the_other_platform(self):
        for platform, other in [('kylin', 'ubuntu'), ('ubuntu', 'kylin')]:
            for script in (ROOT / 'linux' / platform).rglob('*'):
                if script.is_file() and script.suffix != '.md':
                    with self.subTest(script=script.name):
                        self.assertNotIn(f'linux/{other}/', script.read_text())
        shared = (ROOT / 'linux/common/build-algorithm-bridge.sh').read_text()
        self.assertNotIn('packaging/kylin', shared)
        self.assertNotIn('/etc/os-release', shared)
        self.assertNotIn('nb_select_platform', shared)

    def test_legacy_forwarders_preserve_arguments_exit_status_and_cwd(self):
        for original in (ROOT / 'linux').glob('*.sh'):
            if original.name == 'build-algorithm-bridge.sh':
                continue  # OS dispatcher is validated with the native build.
            target = re.search(r'/linux/(?:kylin|ubuntu)/[^"\s]+', original.read_text())
            self.assertIsNotNone(target, original.name)
            relative = target.group()[1:]
            self.assertTrue((ROOT / relative).is_file())
            with self.subTest(script=original.name), tempfile.TemporaryDirectory(prefix='linux layout ') as directory:
                checkout = Path(directory) / 'checkout'
                alias = checkout / 'linux' / original.name
                alias.parent.mkdir(parents=True)
                shutil.copy2(original, alias)
                real = checkout / relative
                real.parent.mkdir(parents=True)
                real.write_text('printf "%s\\0" "$PWD" "$@"; exit 37\n')
                args = ['argument with spaces', 'literal;semicolon', '$(literal)']
                result = subprocess.run(['bash', str(alias), *args], cwd=directory, capture_output=True)
                self.assertEqual(result.returncode, 37, result.stderr)
                self.assertEqual(result.stdout.split(b'\0')[:-1], [os.fsencode(Path(directory).resolve()), *map(os.fsencode, args)])

    def test_uninstall_recognizes_old_and_new_units_but_rejects_other_checkout(self):
        source = (ROOT / 'linux/kylin/uninstall-kylin-project.sh').read_text()
        function = source.split('unit_references_this_checkout() {', 1)[1].split('\n}', 1)[0]
        with tempfile.TemporaryDirectory(prefix='linux unit % ') as directory:
            root = Path(directory) / 'checkout'
            unit = Path(directory) / 'neurobridge.service'
            for relative in ('linux/start-kylin-gateway.sh', 'linux/kylin/start-kylin-gateway.sh'):
                for escape in (False, True):
                    path = str(root / relative)
                    unit.write_text('ExecStart=' + (path.replace('%', '%%') if escape else path) + '\n')
                    result = subprocess.run(['bash', '-c', 'sudo() { "$@"; }; check() {' + function + '\n}; check'],
                                            env={**os.environ, 'root_dir':str(root), 'start_script':str(root / 'linux/kylin/start-kylin-gateway.sh'), 'unit_path':str(unit)})
                    self.assertEqual(result.returncode, 0)
            unit.write_text('ExecStart=/another/checkout/linux/start-kylin-gateway.sh\n')
            result = subprocess.run(['bash', '-c', 'sudo() { "$@"; }; check() {' + function + '\n}; check'],
                                    env={**os.environ, 'root_dir':str(root), 'start_script':str(root / 'linux/kylin/start-kylin-gateway.sh'), 'unit_path':str(unit)})
            self.assertNotEqual(result.returncode, 0)
