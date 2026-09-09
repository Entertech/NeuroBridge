"""Windows headset restart probing; saved identity only chooses command order."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import logging
from pathlib import Path

LOG = logging.getLogger(__name__)


class WindowsHeadsetResume:
    def __init__(self, state_path: Path, identity_provider, algorithm_context):
        self.state_path = state_path
        self.identity_provider = identity_provider
        self.algorithm_context = algorithm_context

    def identity(self, path):
        value = self.identity_provider(path)
        # Include physical location/COM as well as USB ids: many devices report
        # the same generic USB serial number. This is a hint, never validation.
        return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def prefer_resume(self, path):
        try:
            value = json.loads(self.state_path.read_text(encoding='utf-8'))
            return value == {'stoppedIdentity': self.identity(path)}
        except (OSError, ValueError, TypeError, RuntimeError):
            return False

    def command_sent(self, name, path):
        try:
            if name == 'stop':
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.state_path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'stoppedIdentity': self.identity(path)}), encoding='utf-8')
                temporary.replace(self.state_path)
            elif name == 'start':
                self.state_path.unlink(missing_ok=True)
        except Exception:
            LOG.warning('Cannot save serial resume preference; next probe uses ACK first', exc_info=True)

    async def probe(self, client, path, observe, ack, stopping):
        resume_first = self.prefer_resume(path)
        order = ('E1', 'ACK') if resume_first else ('ACK', 'E1')
        LOG.info('Serial restart probe: path=%s order=%s savedStopHint=%s', path, ','.join(order), resume_first)
        started = False
        accepted = False
        try:
            for action in order:
                if stopping():
                    return None, b''
                if action == 'ACK':
                    response = await ack(client)
                    if response:
                        accepted = True
                        return None, response
                    continue
                # No provisional CONNECTED event or recording session is created.
                # An isolated algorithm is ready before the speculative E1 write.
                async with self.algorithm_context() as ready:
                    if not ready:
                        LOG.warning('Serial E1 recovery skipped: path=%s reason=algorithm_not_ready', path)
                        continue
                    if stopping():
                        return None, b''
                    self.command_sent('start', path)
                    started = True
                    written = await asyncio.to_thread(client.write, b'\xe1')
                    if written != 1:
                        raise OSError('Incomplete serial recovery E1 write')
                    await asyncio.to_thread(client.flush)
                    LOG.info('Serial recovery E1 sent: path=%s responseExpected=false waitingFor=valid_28_byte_frame', path)
                    stream = await observe(client, path)
                if stream is not None:
                    accepted = True
                    LOG.info('Serial E1 recovery validated by live frame: path=%s', path)
                    return stream, b''
                LOG.warning('Serial E1 recovery produced no valid frame: path=%s', path)
            return None, b''
        finally:
            if started and not accepted:
                # A delayed device may start after the observation deadline.
                # Close an unsuccessful/cancelled probe without leaving capture on.
                try:
                    written = await asyncio.to_thread(client.write, b'\xe0')
                    if written != 1:
                        raise OSError('Incomplete serial recovery cleanup E0 write')
                    await asyncio.to_thread(client.flush)
                except Exception:
                    LOG.warning('Serial recovery cleanup E0 failed: path=%s', path, exc_info=True)
