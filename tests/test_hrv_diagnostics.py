"""HRV field continuity, privacy, sampling and native-bridge correlation."""
import asyncio
from dataclasses import replace
import json
import logging
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from neurobridge.hrv_diagnostics import HrvTrace, hrv_fields
from neurobridge.algorithm.runner import AlgorithmRunner
from neurobridge.adapters.algorithms.affective_sdk import AffectiveSdkAlgorithmEngine
from neurobridge.application.gateway import ClientSession
from neurobridge.bootstrap import build_container
from neurobridge.config import AlgorithmConfig, load
from neurobridge.domain.algorithm import AlgorithmInput, AlgorithmResult, AlgorithmSession
from neurobridge.domain.result import WindowResult
from neurobridge.domain.signal import ParsedSignalBatch
from neurobridge.profiles.resolver import RuntimePlatform
from unit.test_algorithm_bridge_isolation import BridgeProcess

ROOT = Path(__file__).resolve().parents[1]


def rows(logs):
    return [json.loads(line.split('HRV trace: ', 1)[1]) for line in logs.output if 'HRV trace: ' in line]


class HrvLogTests(unittest.TestCase):
    def test_states_distinguish_missing_null_zero_nonfinite_and_invalid_without_values(self):
        for value, state in [(None,'null'),(0,'zero'),(17.375,'number'),(math.nan,'non_finite'),(math.inf,'non_finite'),('PRIVATE_HRV','invalid_type'),(True,'invalid_type')]:
            with self.subTest(state=state):
                self.assertEqual(hrv_fields({'hr':{'hrv':value}})['hrvState'],state)
        self.assertEqual(hrv_fields({'hr':{'value':80}})['hrvState'],'missing')
        self.assertEqual(hrv_fields({'hr':None})['hrState'],'null')

    def test_info_sampling_reports_changes_and_counts_suppressed_without_sensitive_values(self):
        trace=HrvTrace(30)
        metric={'hr':{'value':83.123456,'hrv':17.375},'secret':'PRIVATE','eeg':{'rawBytes':'PRIVATE'}}
        with self.assertLogs('neurobridge.hrv', 'INFO') as logs, patch('neurobridge.hrv_diagnostics.time.monotonic',side_effect=[0,1,2,31,32,33]):
            for _ in range(4):trace.observe('test',metric,expected_hr=True,batchId='batch-test',rawBytes='PRIVATE')
            trace.observe('test',{'hr':{'value':80,'hrv':None}},expected_hr=True)
            trace.observe('test',{'hr':{'value':80}},expected_hr=True)
        entries=rows(logs)
        self.assertEqual([x['hrvState'] for x in entries],['number','number','null','missing'])
        self.assertEqual(entries[1]['suppressed'],2)
        self.assertNotIn('PRIVATE',' '.join(logs.output))
        self.assertNotIn('83.123456',' '.join(logs.output))
        self.assertNotIn('17.375',' '.join(logs.output))

    def test_debug_has_every_window_and_zero_interval_keeps_changes(self):
        trace=HrvTrace(0)
        with self.assertLogs('neurobridge.hrv','DEBUG') as logs:
            for _ in range(3):trace.observe('debug',{'hr':{'hrv':0}})
        self.assertEqual(len(rows(logs)),3)
        trace=HrvTrace(0)
        with self.assertLogs('neurobridge.hrv','INFO') as logs:
            trace.observe('info',{'hr':{'hrv':0}})
            trace.observe('info',{'hr':{'hrv':0}})
            trace.observe('info',{'hr':{'hrv':None}})
        self.assertEqual(len(rows(logs)),2)

    def test_wire_stage_detects_loss_after_serialization_and_never_throws(self):
        trace=HrvTrace()
        original={'code':200,'data':{'mode':'live','timestampMs':600,'payload':{'algorithm':{'hr':{'value':80,'hrv':6.5}}}}}
        encoded=json.loads(json.dumps(original));encoded['data']['payload']['algorithm']['hr']['hrv']=None
        with self.assertLogs('neurobridge.hrv','INFO') as logs:
            trace.serialized(json.dumps(encoded),original,outcome='sent')
            trace.serialized('broken-json',original)
        self.assertEqual(rows(logs)[0]['change'],'number->null')
        self.assertEqual(rows(logs)[0]['stage'],'websocket_sent')
        self.assertEqual(rows(logs)[1]['outcome'],'diagnostic_decode_failed')

    def test_broken_logging_handler_does_not_change_delivery_or_algorithm(self):
        trace=HrvTrace()
        with patch('neurobridge.hrv_diagnostics.LOG.log',side_effect=OSError('log unavailable')):
            trace.observe('test',{'hr':{'hrv':None}},expected_hr=True)


class BridgeHrvTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_state_is_correlated_and_private_metadata_is_not_forwarded(self):
        process=BridgeProcess()
        process.stdout.feed_data(json.dumps({'algorithm':{'hr':{'value':80,'hrv':None}},
            'hrvDiagnostics':{'sdkHrvState':'non_finite','serializedHrvState':'null','secret':'PRIVATE'}}).encode()+b'\n')
        runner=AlgorithmRunner(AlgorithmConfig(True,('bridge',)))
        runner.process=process
        with self.assertLogs('neurobridge.hrv','INFO') as logs:
            result,reasons=await runner.evaluate_raw(b'',b'P',start_ms=0,end_ms=600,hr_packet_count=1,diagnostic_context={'batchId':'batch-test'})
        request=json.loads(process.writes[0])
        self.assertEqual(request['hrvDiagnostics'],'1')
        entry=rows(logs)[0]
        self.assertEqual((entry['batchId'],entry['sdkHrvState'],entry['hrvState']),('batch-test','non_finite','null'))
        self.assertEqual(entry['hrInputBytes'],1)
        self.assertNotIn('PRIVATE',' '.join(logs.output))
        self.assertEqual(result,{'hr':{'value':80,'hrv':None}})
        self.assertEqual(reasons,[])
        await runner.stop()

    async def test_missing_hrv_and_old_native_bridge_are_visible_without_changing_metrics(self):
        process=BridgeProcess();process.stdout.feed_data(b'{"algorithm":{"hr":{"value":80}}}\n')
        engine=AffectiveSdkAlgorithmEngine(AlgorithmConfig(True,('bridge',)))
        engine._runner.process=process
        with self.assertLogs('neurobridge.hrv','DEBUG') as logs:
            result=await engine.evaluate(AlgorithmInput('batch-test',{'hrRawBase64':'UA==','hrPacketCount':1,'windowStartMs':0,'windowEndMs':600},'1'))
        entries=rows(logs)
        self.assertEqual([x['stage'] for x in entries],['bridge_response','engine_result'])
        self.assertEqual(entries[0]['sdkHrvState'],'unavailable')
        self.assertTrue(all(x['hrvState']=='missing' for x in entries))
        self.assertTrue(result.valid)
        self.assertEqual(result.metrics,{'hr':{'value':80}})
        await engine.close()

    async def test_timeout_is_a_failed_exchange_not_a_null_sdk_value(self):
        process=BridgeProcess()
        runner=AlgorithmRunner(AlgorithmConfig(True,('bridge',),request_timeout_ms=10));runner.process=process
        with self.assertLogs('neurobridge.hrv','INFO') as logs:
            result,reasons=await runner.evaluate_raw(b'',b'P',start_ms=0,end_ms=600)
        self.assertIsNone(result);self.assertEqual(reasons,['ALGORITHM_ERROR'])
        self.assertEqual(rows(logs)[-1]['outcome'],'error')
        self.assertNotIn('sdkHrvState',rows(logs)[-1])


class GatewayHrvTests(unittest.IsolatedAsyncioTestCase):
    async def test_projection_marks_intentional_filter_and_accidental_field_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=load(ROOT/'config/gateway.ubuntu.toml.example')
            cfg=replace(cfg,recording=replace(cfg.recording,directory=Path(directory)))
            gateway=build_container(cfg,RuntimePlatform('ubuntu','x86_64')).gateway
            algorithm={'hr':{'value':80,'hrv':6.5},'attention':42}
            with self.assertLogs('neurobridge.hrv','DEBUG') as logs:
                eeg=gateway.filtered_payload({},algorithm,frozenset({'eeg'}),diagnostic_context={'timestampMs':600,'mode':'live'})
                with patch.object(gateway.wire,'filtered_payload',return_value={'algorithm':{'hr':{'value':80}}}):
                    hr=gateway.filtered_payload({},algorithm,frozenset({'hr'}),diagnostic_context={'timestampMs':600,'mode':'live'})
            entries=rows(logs)
            self.assertEqual(entries[0]['outcome'],'not_requested')
            self.assertFalse(entries[0]['hrRequested'])
            self.assertEqual(entries[1]['change'],'number->missing')
            self.assertNotIn('hr',eeg['algorithm'])
            self.assertNotIn('hrv',hr['algorithm']['hr'])

    async def test_replay_traces_saved_null_without_evaluating_sdk(self):
        from neurobridge.business.gateway import Gateway
        from test_gateway import config
        with tempfile.TemporaryDirectory() as directory:
            gateway=Gateway(config(Path(directory)))
            recording=gateway.store.start(1000)
            gateway.store.save_algorithm(timestamp_ms=1200,valid=True,invalid_reasons=[],algorithm={'hr':{'value':80,'hrv':None}})
            gateway.store.stop()
            session=ClientSession();gateway.sessions.add(session)
            sent=[];delivered=asyncio.Event()
            async def send(message):
                sent.append(message)
                if message['data'].get('event')=='data':delivered.set()
            try:
                with patch.object(gateway.algorithm,'evaluate',new=AsyncMock(side_effect=AssertionError('Replay must not run SDK'))):
                    with self.assertLogs('neurobridge.hrv','DEBUG') as logs:
                        await gateway.subscribe(session,{'streams':['hr']},send)
                        await asyncio.wait_for(delivered.wait(),1)
                        await gateway.close_session(session)
                entries=rows(logs)
                self.assertTrue({'replay_read','replay_projection','subscription_sent'} <= {entry['stage'] for entry in entries})
                self.assertTrue(all(entry['mode']=='replay' and entry['hrvState']=='null' for entry in entries))
                self.assertEqual(next(entry for entry in entries if entry['stage']=='replay_read')['recordingSessionId'],recording)
                self.assertIsNone(sent[0]['data']['payload']['algorithm']['hr']['hrv'])
            finally:
                await gateway.close_session(session)
                await gateway.stop()
