from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from neurobridge.business.gateway import ClientSession, Gateway, REPLAY_NOT_AVAILABLE_REASON
from neurobridge.config import load
from neurobridge.replay_control import apply, updated_config, ReplayControlError
from test_gateway import config
from test_ssh_branch_update import template, unprivileged

ROOT = Path(__file__).resolve().parents[1]


class ReplayPolicyConfigTests(unittest.TestCase):
    def test_default_is_compatible_and_only_boolean_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gateway.toml'
            path.write_text('[recording]\n')
            self.assertTrue(load(path).recording.replay_enabled)
            for value in ['false', 'true']:
                path.write_text('[recording]\nreplay_enabled = ' + value)
                self.assertEqual(load(path).recording.replay_enabled, value == 'true')
            for value in ['"false"', '0', '[]']:
                path.write_text('[recording]\nreplay_enabled = ' + value)
                with self.assertRaises(ValueError): load(path)

    def test_editor_preserves_all_other_values_and_comments(self):
        samples = [
            '# deploy\n[recording] # capture\nreplay_enabled = true # policy\nsubject_id = "fixture"\n[ble]\nenabled = true\n',
            '[recording]\nreplay_speed = 1.0\n[algorithm]\nenabled = false\n',
            '# deploy\n[ble]\nenabled = false\n',
            '["recording"]\n"replay_enabled" = true\n',
            '[recording]',
            '[recording]\r\nreplay_enabled = true # policy\r\n',
        ]
        for source in samples:
            for enabled in [False, True]:
                with self.subTest(source=source, enabled=enabled):
                    result = updated_config(source, enabled)
                    expected = tomllib.loads(source)
                    expected.setdefault('recording', {})['replay_enabled'] = enabled
                    self.assertEqual(tomllib.loads(result), expected)
                    if '# policy' in source: self.assertIn('# policy', result)

    def test_ambiguous_multiline_or_inline_tables_are_not_rewritten(self):
        for source in ['recording = {replay_enabled = true}\n', 'note = """\n[recording]\n"""\n[recording]\nreplay_enabled = true\n']:
            with self.assertRaises(ValueError): updated_config(source, False)

    def test_running_service_restart_and_config_persistence_preserve_mode_and_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gateway.toml'
            path.write_text('[recording]\nreplay_enabled = true # preserve\n[ble]\nenabled = false\n')
            path.chmod(0o640)
            old = path.stat()
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0)
            with patch('neurobridge.replay_control.subprocess.run', side_effect=run):
                result = apply('disable', path)
            self.assertEqual(result, {'replayEnabledConfigured':False,'serviceActive':True,'applied':True})
            self.assertFalse(load(path).recording.replay_enabled)
            self.assertIn('# preserve', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual((path.stat().st_uid,path.stat().st_gid), (old.st_uid, old.st_gid))
            self.assertIn(['/usr/bin/systemctl', 'restart', 'neurobridge.service'], calls)
            with patch('neurobridge.replay_control.subprocess.run', side_effect=run):
                self.assertTrue(apply('enable', path)['replayEnabledConfigured'])

    def test_stopped_service_is_not_started_and_status_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gateway.toml'; path.write_text('[recording]\n')
            with patch('neurobridge.replay_control.subprocess.run', return_value=subprocess.CompletedProcess([],3)) as run:
                result = apply('disable', path)
                self.assertFalse(result['applied'])
                self.assertEqual(run.call_count,1)
                before = path.read_bytes()
                status = apply('status', path)
                self.assertFalse(status['replayEnabledConfigured'])
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(run.call_count,2)

    def test_failed_restart_keeps_requested_policy_and_reports_not_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gateway.toml';path.write_text('[recording]\nreplay_enabled=true\n')
            with patch('neurobridge.replay_control.subprocess.run', side_effect=[subprocess.CompletedProcess([],0),subprocess.CompletedProcess([],1)]):
                with self.assertRaisesRegex(ReplayControlError, 'was saved.*failed'):
                    apply('disable',path)
            self.assertFalse(load(path).recording.replay_enabled)

    def test_invalid_configuration_and_symlink_do_not_change_files_or_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gateway.toml';path.write_text('[recording]\nreplay_enabled="bad"\n')
            before=path.read_bytes()
            with patch('neurobridge.replay_control.subprocess.run') as run:
                with self.assertRaises(ValueError): apply('disable',path)
                self.assertEqual(path.read_bytes(),before)
                link=Path(directory)/'link';link.symlink_to(path)
                with self.assertRaises(ValueError): apply('enable',link)
                run.assert_not_called()


class ReplayGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_policy_bypasses_history_and_returns_locked_error(self):
        with tempfile.TemporaryDirectory() as directory:
            base=config(Path(directory))
            gateway=Gateway(replace(base,recording=replace(base.recording,replay_enabled=False)))
            await gateway.start()
            session=ClientSession(); gateway.sessions.add(session)
            with patch.object(gateway.store,'replay_recording_id',side_effect=AssertionError('Disabled replay must not read history')):
                self.assertEqual(gateway.status_result()['availableStreams'],['status'])
                for action in ['getLatest','subscribe']:
                    sent=[]
                    async def send(item):sent.append(item)
                    await gateway.handle(session,json.dumps({'protocolVersion':'1.0','messageType':'request','requestId':action,'action':action,'params':{'streams':['eeg']}}),send)
                    self.assertEqual(sent[0]['code'],503)
                    self.assertEqual(sent[0]['data']['reason'],REPLAY_NOT_AVAILABLE_REASON)
                    self.assertEqual(sent[0]['message'],'头环未连接')
                gateway._start_replay_if_needed()
                self.assertIsNone(gateway._replay_task)
                self.assertEqual(gateway.latest_replay_algorithm(),(None,None))
                await gateway.update_status('connectionState','connected')
                recording=gateway.store.recording_id
                await gateway.receive_packet('ff31',b'e'*20)
                self.assertEqual(gateway.store.recording_id,recording)
                self.assertIn('eeg.raw',gateway.status_result()['availableStreams'])
                await gateway.update_status('connectionState','disconnected')
                await gateway.update_status('connectionState','connected')
                self.assertNotEqual(gateway.store.recording_id,recording)
            await gateway.stop()

    async def test_policy_survives_config_reload_and_normal_replay_is_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'gateway.toml'
            path.write_text('[recording]\ndirectory='+json.dumps(str(root/'data'))+'\n[algorithm]\nenabled=false\n')
            gateway=Gateway(load(path));await gateway.start()
            gateway.store.start(1000)
            gateway.store.save_algorithm(timestamp_ms=1200,valid=True,invalid_reasons=[],algorithm={'attention':7})
            gateway.store.stop();await gateway.stop()
            with patch('neurobridge.replay_control.service_active',return_value=False):apply('disable',path)
            gateway=Gateway(load(path));await gateway.start()
            self.assertFalse(gateway.replay_available);await gateway.stop()
            with patch('neurobridge.replay_control.service_active',return_value=False):apply('enable',path)
            gateway=Gateway(load(path));await gateway.start()
            self.assertTrue(gateway.replay_available)
            self.assertEqual(gateway.latest_replay_algorithm()[0]['attention'],7)
            await gateway.stop()


class ReplaySshCommandTests(unittest.TestCase):
    def test_ssh_gate_accepts_only_three_fixed_actions_and_audits_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);args=root/'args';audit=root/'audit'
            helper=root/'python'
            helper.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$ARG_LOG"\nexit "${COMMAND_EXIT:-0}"\n');helper.chmod(0o755)
            gate=root/'gate'
            gate.write_text(unprivileged(template('command_tmp')).replace('/opt/neurobridge/venv/bin/python',str(helper)).replace('/usr/bin/logger', 'echo').replace('"operator=$operator action=$action request=$request result=$result"','"operator=$operator action=$action request=$request result=$result" >> "$AUDIT_LOG"'))
            sudo=root/'sudo';sudo.write_text('#!/bin/bash\n[[ $1 != -- ]] || shift\nexec bash "$@"\n');sudo.chmod(0o755)
            cli=root/'cli';cli.write_text(template('ops_cli_tmp').replace('exec sudo --','exec '+str(sudo)+' --').replace('/usr/local/sbin/neurobridge-ops-command',str(gate)))
            env={**os.environ,'ARG_LOG':str(args),'AUDIT_LOG':str(audit),'SUDO_USER':'fixture'}
            for action in ['enable','disable','status']:
                result=subprocess.run(['bash',str(cli),'replay',action],env=env,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(args.read_text().splitlines(),['-I','-m','neurobridge.replay_control',action])
            args.unlink();audit.unlink()
            result=subprocess.run(['bash',str(cli),'replay','disable'],env={**env,'COMMAND_EXIT':'19'},capture_output=True)
            self.assertEqual(result.returncode,19)
            self.assertIn('result=failed:19',audit.read_text())
            self.assertNotIn('result=success',audit.read_text())
            args.unlink()
            for options in [[],['unknown'],['enable','extra'],['--config','other']]:
                result=subprocess.run(['bash',str(cli),'replay',*options],env=env,capture_output=True)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(args.exists())
                result=subprocess.run(['bash',str(gate),'replay',*options],env=env,capture_output=True)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(args.exists())


class OperationsRefreshTests(unittest.TestCase):
    def test_refresh_replaces_existing_commands_without_creating_ssh_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            local=root/'usr/local';(local/'bin').mkdir(parents=True);(local/'sbin').mkdir()
            etc=root/'etc/sudoers.d';etc.mkdir(parents=True)
            source=root/'checkout/linux';source.mkdir(parents=True)
            (source/'operations').mkdir()
            for name in ['neurobridge-ops','neurobridge-ops-command']:
                (source/'operations'/name).write_text((ROOT/'linux/operations'/name).read_text())
            refresh=source/'refresh-operations-commands.sh'
            script=(ROOT/'linux/refresh-operations-commands.sh').read_text()
            script=script.replace('[[ ${EUID} -eq 0 && $# -eq 0 ]]','[[ $# -eq 0 ]]').replace('/etc/sudoers.d',str(etc)).replace('/usr/local',str(local)).replace('install -o root -g root','install')
            refresh.write_text(script)
            subprocess.run(['bash',str(refresh)],check=True)
            self.assertFalse((local/'bin/neurobridge-ops').exists())
            sudoers=etc/'neurobridge-operator';sudoers.write_text('# existing authorization\n')
            gate=local/'sbin/neurobridge-ops-command';gate.write_text('# old gate\n')
            with gate.open() as old_inode:
                subprocess.run(['bash',str(refresh)],check=True)
                self.assertEqual(old_inode.read(),'# old gate\n')
            self.assertIn('replay)',gate.read_text())
            self.assertIn('replay enable|disable|status',(local/'bin/neurobridge-ops').read_text())
            self.assertEqual(sudoers.read_text(),'# existing authorization\n')
            self.assertEqual(gate.stat().st_mode & 0o777,0o755)
