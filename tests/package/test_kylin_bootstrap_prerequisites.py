"""Only native CPython profiles need development libraries in the all package."""
from pathlib import Path
import subprocess

from tests.package.test_kylin_bootstrap import BUILDER, ROOT
from tests.package.test_kylin_bootstrap_lifecycle import SandboxTests


class BootstrapPrerequisiteTests(SandboxTests):
    def prepare(self, cpu='aarch64', bits='64', triplet='aarch64-linux-gnu'):
        box = self.sandbox()
        box.env.update(NB_CPU=cpu, NB_BITS=bits, TMPDIR=str(box.root))
        scripts = box.root / 'scripts'
        box.platform(scripts)
        box.env['NB_PROBE_LOG'] = str(box.root / 'probes.txt')
        compiler = f'''#!/bin/bash
case "$1" in
 -dumpmachine) echo '{triplet}'; exit 0;;
 --version) echo 'GCC fixture'; exit 0;;
esac
printf '%s\n' "$*" >> "$NB_PROBE_LOG"
cat >> "$NB_PROBE_LOG"
for arg in "$@"; do
 case " ${{NB_MISSING_LIBS:-}} " in
  *" $arg "*) echo "fatal error: unavailable $arg" >&2; exit 1;;
 esac
done
exit 0
'''
        for name in ('cc', 'c++'):
            path = box.root / 'stubs' / name
            path.write_text(compiler)
            path.chmod(0o755)
        return box, scripts

    def check(self, box, scripts):
        return subprocess.run(['bash', '-c', f'''set -euo pipefail
NB_INPUT_LOCK="{scripts}/kylin-bootstrap-inputs.toml"
. "{scripts}/platform.sh"
nb_select_platform
nb_require_python_development
'''], env=box.env, text=True, capture_output=True, timeout=20)

    def test_common_deb_dependencies_do_not_force_native_python_libraries(self):
        control = BUILDER.deb_control()
        depends = next(line for line in control.splitlines() if line.startswith('Depends:'))
        self.assertIn('g++', depends)
        self.assertIn('make', depends)
        for package in ('libssl-dev', 'libsqlite3-dev', 'libbz2-dev', 'liblzma-dev', 'libffi-dev', 'zlib1g-dev'):
            self.assertNotIn(package, depends)

    def test_bundled_x86_64_skips_all_native_python_probes(self):
        box, scripts = self.prepare('x86_64', triplet='x86_64-linux-gnu')
        box.env['NB_MISSING_LIBS'] = '-lssl -lsqlite3 -lbz2 -llzma -lffi -lz'
        result = self.check(box, scripts)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mode=bundled development_libraries=not_required', result.stderr)
        self.assertFalse(Path(box.env['NB_PROBE_LOG']).exists())

    def test_all_source_profiles_check_headers_and_linker_inputs(self):
        for cpu, bits, triplet in (
            ('aarch64', '64', 'aarch64-linux-gnu'),
            ('loongarch64', '64', 'loongarch64-linux-gnu'),
            ('mips64el', '64', 'mips64el-linux-gnu'),
            ('sw_64', '64', 'sw_64-linux-gnu'),
            ('i686', '32', 'i686-linux-gnu'),
            ('armv7l', '32', 'arm-linux-gnueabihf'),
        ):
            with self.subTest(cpu=cpu):
                box, scripts = self.prepare(cpu, bits, triplet)
                result = self.check(box, scripts)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('mode=source result=ready', result.stderr)
                calls = Path(box.env['NB_PROBE_LOG']).read_text()
                self.assertEqual(calls.count('-x c - -o'), 6)
                for header in ('openssl/ssl.h', 'sqlite3.h', 'bzlib.h', 'lzma.h', 'ffi.h', 'zlib.h'):
                    self.assertIn(f'#include <{header}>', calls)
                self.assertFalse(list(box.root.glob('neurobridge-python-prereq.*')))

    def test_missing_libraries_report_all_failures_and_only_needed_deb_packages(self):
        box, scripts = self.prepare()
        box.env['NB_MISSING_LIBS'] = '-lsqlite3 -lffi'
        result = self.check(box, scripts)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing_python_development architecture=aarch64 deb_packages=libsqlite3-dev libffi-dev', result.stderr)
        self.assertIn('sudo apt-get install libsqlite3-dev libffi-dev', result.stderr)
        self.assertIn('sudo dpkg --configure neurobridge-bootstrap', result.stderr)
        self.assertIn('fatal error: unavailable -lsqlite3', result.stderr)
        self.assertEqual(Path(box.env['NB_PROBE_LOG']).read_text().count('-x c - -o'), 6)
        self.assertFalse(list(box.root.glob('neurobridge-python-prereq.*')))

    def test_bootstrap_failure_is_logged_before_source_build_or_service_changes(self):
        box, scripts = self.prepare()
        box.existing(active=True, enabled=True)
        box.env['NB_MISSING_LIBS'] = '-lssl'
        (scripts / 'build-info.txt').write_text('applicationVersion=0.0.0\nsourceCommit=test\n')
        for name in ('bootstrap-build.sh', 'run-logged.sh'):
            (scripts / name).write_text(box.relocate((ROOT / 'packaging/kylin' / name).read_text()))
        result = box.run(scripts / 'bootstrap-build.sh')
        self.assertNotEqual(result.returncode, 0)
        log = next((box.root / 'var/log/neurobridge-bootstrap').glob('install-*.log')).read_text()
        self.assertIn('applicationVersion=0.0.0', log)
        self.assertIn('missing_python_development', log)
        self.assertIn('exit_code=1', log)
        self.assertNotIn('PHASE prepare-source', log)
        self.assertTrue(box.state()['active'])
        self.assertTrue(box.state()['enabled'])
        self.assertEqual(box.state()['groups'], [])
        self.assertEqual((box.app / 'version.txt').read_text(), 'old-code')
        self.assertFalse(list((box.root / 'var/tmp').glob('neurobridge-build.*')))
