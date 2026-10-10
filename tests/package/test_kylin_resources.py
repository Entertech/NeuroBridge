"""Exercise resource selection and first DEB configure without target hardware."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from tests.package.test_kylin_bootstrap import ROOT
from tests.package.test_kylin_bootstrap_lifecycle import SandboxTests


class KylinResourceTests(SandboxTests):
    def prepare(self):
        box = self.sandbox()
        scripts = box.root / 'scripts'
        box.platform(scripts)
        data, content = box.resources(scripts)
        box.env.update(NB_TEST_SCRIPTS=str(scripts), NB_FIXTURE_CONTENT=content.decode(),
                       NB_CURL_CALLS=str(box.root / 'curl.json'))
        curl = box.root / 'stubs/curl'
        curl.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, sys
pathlib.Path(os.environ['NB_CURL_CALLS']).write_text(json.dumps(sys.argv[1:]))
if os.environ.get('NB_CURL_FAIL'): sys.exit(22)
out = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
out.write_text(os.environ.get('NB_CURL_CONTENT', os.environ['NB_FIXTURE_CONTENT']))
''')
        curl.chmod(0o755)
        return box, scripts, data, content

    def resolve(self, box, *args, body=None, cwd=None):
        return subprocess.run(['bash', '-c', '''set -euo pipefail
NB_INPUT_LOCK=$NB_TEST_SCRIPTS/kylin-bootstrap-inputs.toml
. "$NB_TEST_SCRIPTS/platform.sh"
. "$NB_TEST_SCRIPTS/resources.sh"
nb_parse_resources "$@"
nb_select_platform
''' + (body or '''nb_prepare_resources "$NB_TEST_SCRIPTS/source" "$NB_SANDBOX/resolved"
nb_stage_resources "$NB_SANDBOX/resolved" "$NB_SANDBOX/build"
'''), 'resources', *args], env=box.env, cwd=cwd, capture_output=True, text=True, timeout=20)

    def test_defaults_and_resource_names_use_bundled_files_without_network(self):
        for args in ((), ('cmake',), ('python', 'cmake', 'eigen', 'pyserial', 'websockets')):
            with self.subTest(args=args):
                box, scripts, data, content = self.prepare()
                # Legacy extra wheels must never enter the staged pip input.
                wheel = box.root / 'build/wheelhouse/bleak.whl'
                wheel.parent.mkdir(parents=True)
                wheel.write_bytes(b'legacy')
                result = self.resolve(box, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr.count('mode=bundled'), 5)
                self.assertFalse(Path(box.env['NB_CURL_CALLS']).exists())
                self.assertEqual({p.name for p in wheel.parent.iterdir()},
                                 {data['artifacts'][k]['filename'] for k in ('pyserial', 'websockets')})
                self.assertTrue(all(p.read_bytes() == content for p in (box.root / 'resolved').iterdir()))

    def test_explicit_local_file_wins_and_relative_paths_with_spaces_work(self):
        box, scripts, data, content = self.prepare()
        filename = data['artifacts']['cmake_x86_64']['filename']
        bundled = scripts / 'source/packaging/kylin/offline' / filename
        bundled.write_bytes(b'bad default')
        local = box.root / 'USB resources/CMake copy.tar.gz'
        local.parent.mkdir()
        local.write_bytes(content)
        result = self.resolve(box, 'cmake', 'USB resources/CMake copy.tar.gz', cwd=box.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name=cmake key=cmake_x86_64 mode=local', result.stderr)
        self.assertEqual(bundled.read_bytes(), b'bad default')
        self.assertFalse(Path(box.env['NB_CURL_CALLS']).exists())

    def test_explicit_url_downloads_even_with_bundled_resource(self):
        box, scripts, data, content = self.prepare()
        url = 'https://example.com/cmake.tar.gz?signature=do-not-log'
        result = self.resolve(box, 'cmake', url)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(Path(box.env['NB_CURL_CALLS']).read_text())
        self.assertEqual(calls[-1], url)
        self.assertEqual(calls[calls.index('--proto') + 1], '=https')
        self.assertEqual(calls[calls.index('--proto-redir') + 1], '=https')
        self.assertIn('mode=download', result.stderr)
        self.assertNotIn('do-not-log', result.stdout + result.stderr)

    def test_multiple_resources_can_mix_local_download_and_bundled_sources(self):
        box, scripts, data, content = self.prepare()
        python = box.root / 'Python copy.tar.gz'
        python.write_bytes(content)
        wheel = box.root / 'pyserial copy.whl'
        wheel.write_bytes(content)
        result = self.resolve(box, 'python', str(python), 'cmake', 'https://example.com/cmake.tar.gz',
                              'eigen', 'pyserial', str(wheel), 'websockets')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count('mode=local'), 2)
        self.assertEqual(result.stderr.count('mode=download'), 1)
        self.assertEqual(result.stderr.count('mode=bundled'), 2)

    def test_download_errors_and_bad_checksums_do_not_fall_back(self):
        for change, reason in (({'NB_CURL_FAIL': '1'}, 'resource_download_failed'),
                               ({'NB_CURL_CONTENT': 'wrong'}, 'input_hash_mismatch')):
            with self.subTest(change=change):
                box, scripts, data, content = self.prepare()
                box.env.update(change)
                result = self.resolve(box, 'cmake', 'https://example.com/cmake.tar.gz')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
                self.assertFalse(list((box.root / 'resolved').glob('*cmake*')))
                self.assertFalse((box.root / 'build').exists())

    def test_missing_or_corrupt_default_never_downloads(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                box, scripts, data, content = self.prepare()
                bundled = scripts / 'source/python-runtime' / data['artifacts']['python_x86_64']['filename']
                if corrupt:
                    bundled.write_bytes(b'bad')
                else:
                    bundled.unlink()
                result = self.resolve(box)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(Path(box.env['NB_CURL_CALLS']).exists())

    def test_invalid_names_duplicates_urls_and_paths_fail_before_fetch(self):
        for args in (('typo',), ('cmake', 'cmake'), ('cmake', ''),
                     ('cmake', '--unknown'), ('cmake', 'missing.tar.gz'),
                     ('cmake', 'http://example.com/file'), ('cmake', 'https://'),
                     ('cmake', 'https://user:password@example.com/file')):
            with self.subTest(args=args):
                box, scripts, data, content = self.prepare()
                result = self.resolve(box, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('password', result.stdout + result.stderr)
                self.assertFalse(Path(box.env['NB_CURL_CALLS']).exists())
                self.assertFalse((box.root / 'resolved').exists())

    def test_symlink_local_input_is_rejected(self):
        box, scripts, data, content = self.prepare()
        target = box.root / 'file'
        target.write_bytes(content)
        link = box.root / 'link'
        link.symlink_to(target)
        result = self.resolve(box, 'cmake', str(link))
        self.assertNotEqual(result.returncode, 0)

    def test_every_cpu_profile_selects_matching_python_and_cmake(self):
        for cpu, bits, profile in (('x86_64', '64', 'x86_64'), ('aarch64', '64', 'aarch64'),
                                   ('loongarch64', '64', 'loongarch64'), ('mips64el', '64', 'mips64el'),
                                   ('sw_64', '64', 'sw64'), ('i686', '32', 'x86'), ('armv7l', '32', 'armhf')):
            with self.subTest(cpu=cpu):
                box, scripts, data, content = self.prepare()
                box.env.update(NB_CPU=cpu, NB_BITS=bits)
                result = self.resolve(box)
                self.assertEqual(result.returncode, 0, result.stderr)
                for name in ('python', 'cmake'):
                    selected = data['profiles'][profile][name]
                    self.assertTrue((box.root / 'resolved' / data['artifacts'][selected]['filename']).is_file())

    def test_list_reports_size_integrity_and_missing_resources_without_writes(self):
        box, scripts, data, content = self.prepare()
        (scripts / 'source/wheelhouse' / data['artifacts']['websockets']['filename']).unlink()
        result = self.resolve(box, body='nb_list_resources "$NB_TEST_SCRIPTS/source"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'\t{len(content)}\tverified', result.stdout)
        self.assertIn('\t0\tmissing', result.stdout)
        self.assertFalse((box.root / 'resolved').exists())

    def test_prepared_inputs_are_rechecked_against_current_lock(self):
        box, scripts, data, content = self.prepare()
        prepared = box.root / 'prepared'
        prepared.mkdir()
        for item in data['artifacts'].values():
            (prepared / item['filename']).write_bytes(content)
        (prepared / data['artifacts']['cmake_x86_64']['filename']).write_bytes(b'bad')
        box.env['NB_PREPARED'] = str(prepared)
        result = self.resolve(box, body='nb_prepare_resources "$NB_TEST_SCRIPTS/source" "$NB_SANDBOX/resolved" "$NB_PREPARED"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('input_hash_mismatch', result.stderr)

    def wrapper(self, box, scripts):
        tree = box.root / 'package-tree'
        payload = tree / 'usr/lib/neurobridge-bootstrap'
        payload.parent.mkdir(parents=True)
        # Paths inside an extracted DEB are relative to that tree; only host
        # var paths and the root guard need relocation in the standalone entry.
        wrapper = (ROOT / 'packaging/kylin/install-bootstrap.sh').read_text()
        wrapper = wrapper.replace("[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'Run as root.'", ':')
        wrapper = wrapper.replace('/var/tmp/', str(box.root / 'var/tmp') + '/')
        (scripts / 'install-bootstrap.sh').write_text(wrapper)
        for name in ('run-logged.sh', 'bootstrap-build.sh'):
            (scripts / name).write_text(box.relocate((ROOT / 'packaging/kylin' / name).read_text()))
        tools = scripts / 'source/tools'
        tools.mkdir(parents=True)
        builder = tools / 'build-kylin-runtime-archive.sh'
        builder.write_text('''#!/bin/bash
set -e
while [[ $# -gt 0 ]]; do
 case $1 in --output-dir) output=$2;; esac
 shift
done
mkdir -p "$output"
echo fixture > "$output/runtime.tar.gz"
echo fixture > "$output/kylin-runtime-manifest.toml"
''')
        builder.chmod(0o755)
        installer = scripts / 'bootstrap-install.sh'
        installer.write_text('#!/bin/bash\nexit 0\n')
        installer.chmod(0o755)
        shutil.copytree(scripts, payload)
        box.env.update(NB_PACKAGE_TREE=str(tree), NB_DPKG_CALLS=str(box.root / 'dpkg.jsonl'),
                       NB_INSTALLED=str(box.root / 'installed'))
        stub = box.root / 'stubs/dpkg-deb'
        stub.write_text(f'#!{sys.executable}\n' + '''import os, shutil, sys
if sys.argv[1] == '--field': print('neurobridge-bootstrap')
else: shutil.copytree(os.environ['NB_PACKAGE_TREE'], sys.argv[-1])
''')
        stub.chmod(0o755)
        stub = box.root / 'stubs/dpkg'
        stub.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, shutil, subprocess, sys
with open(os.environ['NB_DPKG_CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('NB_DPKG_FAIL'): sys.exit(7)
src = pathlib.Path(os.environ['NB_PACKAGE_TREE']) / 'usr/lib/neurobridge-bootstrap'
dst = pathlib.Path(os.environ['NB_INSTALLED'])
shutil.copytree(src, dst)
sys.exit(subprocess.call(['bash', str(dst / 'bootstrap-build.sh')]))
''')
        stub.chmod(0o755)
        package = box.root / 'bootstrap.deb'
        package.write_bytes(b'package fixture')
        return scripts / 'install-bootstrap.sh', package, payload

    def test_first_configure_uses_override_once_and_cleans_transient_selection(self):
        box, scripts, data, content = self.prepare()
        wrapper, package, payload = self.wrapper(box, scripts)
        cmake = payload / 'source/packaging/kylin/offline' / data['artifacts']['cmake_x86_64']['filename']
        cmake.write_bytes(b'bad bundled input')
        local = box.root / 'usb cmake.tar.gz'
        local.write_bytes(content)
        result = box.run(wrapper, '--package', str(package), 'cmake', str(local))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(Path(box.env['NB_DPKG_CALLS']).read_text().splitlines()), 1)
        self.assertEqual(result.stdout.count('PHASE build-runtime'), 1)
        self.assertIn('key=cmake_x86_64 mode=local', result.stdout)
        self.assertIn('key=cmake_x86_64 mode=prepared', result.stdout)
        self.assertIn('exit_code=0', result.stdout)
        self.assertEqual(cmake.read_bytes(), b'bad bundled input')
        self.assertFalse(list((box.root / 'var/tmp').glob('neurobridge-package.*')))

    def test_failed_first_install_preflight_never_invokes_dpkg_or_changes_old_service(self):
        box, scripts, data, content = self.prepare()
        box.existing(active=True, enabled=True)
        wrapper, package, payload = self.wrapper(box, scripts)
        local = box.root / 'bad cmake.tar.gz'
        local.write_bytes(b'wrong checksum')
        result = box.run(wrapper, '--package', str(package), 'cmake', str(local))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(Path(box.env['NB_DPKG_CALLS']).exists())
        self.assertTrue(box.state()['active'])
        self.assertEqual((box.app / 'version.txt').read_text(), 'old-code')
        self.assertIn('input_hash_mismatch', result.stdout)
        self.assertIn('exit_code=1', result.stdout)
        self.assertFalse(list((box.root / 'var/tmp').glob('neurobridge-package.*')))

    def test_list_entry_does_not_build_download_or_create_install_logs(self):
        box, scripts, data, content = self.prepare()
        entry = scripts / 'bootstrap-build.sh'
        entry.write_text(box.relocate((ROOT / 'packaging/kylin/bootstrap-build.sh').read_text()))
        result = box.run(entry, '--list-resources')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('\tverified'), 5)
        self.assertFalse((box.root / 'var/log/neurobridge-bootstrap').exists())
        self.assertFalse(Path(box.env['NB_CURL_CALLS']).exists())

    def test_package_failure_preserves_exit_code_and_removes_transient_inputs(self):
        box, scripts, data, content = self.prepare()
        wrapper, package, payload = self.wrapper(box, scripts)
        box.env['NB_DPKG_FAIL'] = '1'
        result = box.run(wrapper, '--package', str(package))
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn('exit_code=7', result.stdout)
        self.assertFalse(list((box.root / 'var/tmp').glob('neurobridge-package.*')))
