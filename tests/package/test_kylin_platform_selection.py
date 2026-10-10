"""Exercise host selection and native dependency builds without a Kylin host."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import unittest
from tests.package.test_kylin_bootstrap_lifecycle import SandboxTests, ROOT
from tools.kylin_inputs import catalog, verified_input
from neurobridge.config import load
from neurobridge.profiles.resolver import RuntimePlatform, resolve_profile, KYLIN_ARCHITECTURES


class PlatformSelectionTests(SandboxTests):
    def select(self, box, compiler=False):
        box.platform(box.root / 'selection')
        return subprocess.run(
            ['bash', '-c', f'NB_INPUT_LOCK="{box.root}/selection/kylin-bootstrap-inputs.toml"; . "{box.root}/selection/platform.sh"; nb_select_platform' + (' && nb_require_compiler' if compiler else '')],
            env=box.env, capture_output=True, text=True)

    def test_all_profiles_and_aliases_select_dependencies(self):
        for cpu, canonical, bits in [('x86_64','x86_64','64'), ('arm64','aarch64','64'),
                ('loongarch64','loongarch64','64'), ('mips64','mips64el','64'),
                ('sw_64','sw64','64'), ('i686','x86','32'), ('armv7l','armhf','32')]:
            with self.subTest(cpu=cpu):
                box = self.sandbox()
                box.env.update(NB_CPU=cpu, NB_BITS=bits)
                result = self.select(box)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'architecture={canonical} bits={bits}', result.stderr)
                self.assertIn('python=' + ('python_x86_64' if canonical == 'x86_64' else 'python_source'), result.stderr)

    def test_unknown_cpu_os_and_mixed_abi_fail_closed(self):
        for mutation, reason in [('cpu','unsupported_cpu'), ('os','unsupported_os'),
                                 ('bits','abi_bitness_mismatch'), ('endian','unsupported_abi')]:
            box = self.sandbox()
            if mutation == 'cpu': box.env['NB_CPU'] = 'riscv64'
            if mutation == 'os': (box.root / 'etc/os-release').write_text('ID=kylin\nVERSION_ID=9\n')
            if mutation == 'bits': box.env['NB_BITS'] = '32'
            if mutation == 'endian':
                (box.root / 'stubs/od').write_text('#!/bin/sh\necho "127 69 76 70 2 2"\n')
            result = self.select(box)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(reason, result.stderr)
            self.assertFalse(box.unit.exists())

    def test_foreign_compiler_is_rejected(self):
        box = self.sandbox()
        box.env['NB_CPU'] = 'aarch64'
        self.executable(box.root / 'stubs/c++', '#!/bin/sh\necho x86_64-linux-gnu\n')
        result = self.select(box, compiler=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('compiler_target_mismatch', result.stderr)

    @staticmethod
    def executable(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)

    def test_mismatched_runtime_is_rejected_before_stopping_existing_service(self):
        box = self.sandbox()
        box.existing(active=True, enabled=True)
        script = box.installer()
        box.env['NB_CPU'] = 'aarch64'
        result = box.run(script)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Runtime OS/CPU mismatch', result.stdout + result.stderr)
        self.assertTrue(box.state()['active'])
        self.assertEqual(box.state()['groups'], [])
        self.assertEqual((box.app / 'version.txt').read_text(), 'old-code')

    def test_catalog_matches_runtime_profiles_and_corrupt_input_is_rejected(self):
        data = catalog()
        self.assertEqual(set(data['profiles']), KYLIN_ARCHITECTURES)
        item = data['artifacts']['python_source']
        box = self.sandbox()
        (box.root / item['filename']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            verified_input(item, cache=box.root, offline=box.root)
        config = load(ROOT / 'config/gateway.toml.example')
        for arch in KYLIN_ARCHITECTURES:
            profile = resolve_profile(config, RuntimePlatform('kylin', arch, 'V10'))
            self.assertEqual(profile.architecture, arch)
            self.assertFalse(profile.capabilities.supports_replay)

    def test_native_python_source_build_and_missing_input_diagnostics(self):
        for present in (True, False):
            box = self.sandbox()
            box.env.update(NB_CPU='aarch64', NEUROBRIDGE_BOOTSTRAP='1', NEUROBRIDGE_PORTABLE_PYTHON='1')
            config = box.root / 'config'
            config.mkdir()
            (config / 'kylin-serial-requirements.lock').write_text('')
            helper = box.root / 'packaging/kylin'
            box.platform(helper)
            lock = helper / 'kylin-bootstrap-inputs.toml'
            source = box.root / 'source-fixture/Python'
            source.mkdir(parents=True)
            self.executable(source / 'configure', '#!/bin/sh\nfor arg in "$@"; do case "$arg" in --prefix=*) echo "${arg#--prefix=}" > .prefix;; esac; done\n')
            native_python = box.root / 'python-fixture'
            self.executable(native_python, '''#!/bin/bash
if [[ ${1:-} == -m && ${2:-} == venv ]]; then
 mkdir -p "$3/bin"; ln -s "$0" "$3/bin/python"
fi
if [[ ${1:-} == --version ]]; then echo 'Python 3.11.16'; fi
''')
            archive = helper / 'offline/Python-3.11.16.tar.xz'
            archive.parent.mkdir()
            with tarfile.open(archive, 'w:xz') as output: output.add(source, arcname='Python')
            data = lock.read_text().replace(catalog()['artifacts']['python_source']['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
            (config / 'kylin-bootstrap-inputs.toml').write_text(data)
            if not present: archive.unlink()
            for compiler in ('cc', 'c++'):
                self.executable(box.root / ('stubs/' + compiler), '#!/bin/sh\nif [ "$1" = -dumpmachine ]; then echo aarch64-linux-gnu; else echo "GCC fixture"; fi\n')
            self.executable(box.root / 'stubs/make', f'#!/bin/sh\nif [ "$1" = install ]; then prefix=$(cat .prefix); mkdir -p "$prefix/bin"; cp "{native_python}" "$prefix/bin/python3"; fi\n')
            script = box.root / 'linux/setup-kylin-python.sh'
            self.executable(script, box.relocate((ROOT / 'linux/setup-kylin-python.sh').read_text()))
            result = box.run(script)
            combined = result.stdout + result.stderr
            if present:
                self.assertEqual(result.returncode, 0, combined)
                self.assertIn('phase=build_python_complete architecture=aarch64', combined)
                self.assertTrue((box.root / '.venv/bin/python').exists())
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('missing_or_unsafe_input key=python_source', combined)
            self.assertIn('phase=python_setup_end exit_code=', combined)
            self.assertNotIn('downloading the pinned project-local runtime', combined)

    def test_native_cmake_source_path_and_corrupt_archive(self):
        for corrupt in (False, True):
            box = self.sandbox()
            box.env['NB_CPU'] = 'aarch64'
            box.platform(box.root / 'selection')
            for compiler in ('cc', 'c++'):
                self.executable(box.root / ('stubs/' + compiler), '#!/bin/sh\nif [ "$1" = -dumpmachine ]; then echo aarch64-linux-gnu; else echo "GCC fixture"; fi\n')
            source = box.root / 'cmake-source-fixture'
            source.mkdir()
            self.executable(source / 'bootstrap', '#!/bin/sh\nfor arg in "$@"; do case "$arg" in --prefix=*) echo "${arg#--prefix=}" > .prefix;; esac; done\n')
            archive = box.root / 'cmake-3.31.6.tar.gz'
            with tarfile.open(archive, 'w:gz') as output: output.add(source, arcname='cmake')
            lock = box.root / 'selection/kylin-bootstrap-inputs.toml'
            lock.write_text(lock.read_text().replace(catalog()['artifacts']['cmake_source']['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest()))
            if corrupt: archive.write_bytes(b'corrupt')
            fake_cmake = box.root / 'cmake-fixture'
            self.executable(fake_cmake, '#!/bin/sh\necho "cmake version 3.31.6"\n')
            self.executable(box.root / 'stubs/make', f'#!/bin/sh\nif [ "$1" = install ]; then prefix=$(cat .prefix); mkdir -p "$prefix/bin"; cp "{fake_cmake}" "$prefix/bin/cmake"; fi\n')
            full = (ROOT / 'linux/setup-kylin-algorithm.sh').read_text()
            start = full.index('prepare_project_cmake() {')
            end = full.index('\nnb_require_compiler ||', start)
            script = box.root / 'run-cmake.sh'
            self.executable(script, f'''#!/bin/bash
set -euo pipefail
NB_INPUT_LOCK="{lock}"
. "{box.root}/selection/platform.sh"
nb_select_platform
cmake_version=3.31.6
cmake_archive=cmake-3.31.6.tar.gz
package_dir="{box.root}"
toolchain_dir="{box.root}/toolchain"
cmake_home="$toolchain_dir/native-aarch64"
cmake_is_usable() {{ [[ $(cmake --version) == 'cmake version 3.31.6' ]]; }}
fail() {{ echo "ERROR: $*" >&2; exit 1; }}
''' + full[start:end] + '\nprepare_project_cmake\n')
            result = box.run(script)
            combined = result.stdout + result.stderr
            if corrupt:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('input_hash_mismatch key=cmake_source', combined)
                self.assertFalse((box.root / 'toolchain').exists())
            else:
                self.assertEqual(result.returncode, 0, combined)
                self.assertIn('phase=build_cmake_complete architecture=aarch64', combined)
                self.assertTrue((box.root / 'toolchain/native-aarch64/bin/cmake').exists())

    def test_serial_container_starts_without_ble_dependency_imports(self):
        import sys
        script = '''
import importlib.abc, sys
class RejectBle(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'bleak', 'dbus_fast'}:
            raise ModuleNotFoundError('BLE dependency is intentionally unavailable')
sys.meta_path.insert(0, RejectBle())
from pathlib import Path
from neurobridge.config import load
from neurobridge.bootstrap import build_container
from neurobridge.profiles.resolver import RuntimePlatform
container = build_container(load(Path(sys.argv[1])), RuntimePlatform('kylin', 'aarch64', 'V10'))
assert type(container.parser).__name__ == 'HeadsetRev181Parser'
'''
        result = subprocess.run([sys.executable, '-c', script, str(ROOT / 'config/gateway.toml.example')], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
