"""Bounded, value-free HRV diagnostics shared by acquisition and transport."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
import json
import logging
import math
import time

LOG = logging.getLogger('neurobridge.hrv')
_UNSET = object()
_STATES = {'missing', 'null', 'zero', 'number', 'non_finite', 'invalid_type', 'object', 'not_called', 'unavailable'}
_CONTEXT = {'batchId', 'connectionSessionId', 'recordingSessionId', 'gatewayBootId', 'subscriptionId',
            'requestId', 'windowStartMs', 'windowEndMs', 'timestampMs', 'mode', 'action', 'valid',
            'hrRequested', 'hrInputBytes', 'hrInputPackets', 'bridgePid', 'outcome', 'reason',
            'persistenceAccepted', 'persistenceGuaranteed'}


def value_state(value=_UNSET):
    if value is _UNSET:
        return 'missing'
    if value is None:
        return 'null'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 'invalid_type'
    if not math.isfinite(value):
        return 'non_finite'
    return 'zero' if value == 0 else 'number'


def hrv_fields(algorithm):
    if not isinstance(algorithm, Mapping):
        return {'algorithmObjectState': 'null' if algorithm is None else 'invalid_type',
                'hrState': 'missing', 'hrValueState': 'missing', 'hrvState': 'missing'}
    hr = algorithm.get('hr', _UNSET)
    return {'algorithmObjectState': 'object',
            'hrState': 'object' if isinstance(hr, Mapping) else value_state(hr),
            'hrValueState': value_state(hr.get('value', _UNSET)) if isinstance(hr, Mapping) else 'missing',
            'hrvState': value_state(hr.get('hrv', _UNSET)) if isinstance(hr, Mapping) else 'missing'}


class HrvTrace:
    def __init__(self, interval_seconds=30):
        self.interval_seconds = interval_seconds
        self._last = OrderedDict()

    def observe(self, stage, algorithm, *, upstream=_UNSET, expected_hr=None, native=None, **context):
        # Diagnostics are never allowed to fail acquisition or delivery.
        try:
            fields = hrv_fields(algorithm)
            if upstream is not _UNSET:
                before = hrv_fields(upstream)['hrvState']
                fields['upstreamHrvState'] = before
                fields['change'] = 'unchanged' if before == fields['hrvState'] else f"{before}->{fields['hrvState']}"
            if native is not None:
                for key in ('sdkHrvState', 'serializedHrvState'):
                    state = native.get(key) if isinstance(native, Mapping) else None
                    fields[key] = state if isinstance(state, str) and state in _STATES else 'unavailable'
            safe = {}
            for key, value in context.items():
                if key not in _CONTEXT or value is None:
                    continue
                if isinstance(value, (bool, int)):
                    safe[key] = value
                elif isinstance(value, str):
                    safe[key] = ''.join(c for c in value if c.isprintable())[:96]
            if "timestampMs" not in safe and "windowEndMs" in safe:
                safe["timestampMs"] = safe["windowEndMs"]
            fields.update(stage=stage, expectedHr=expected_hr, **safe)
            lost = fields.get('upstreamHrvState') in {'number', 'zero'} and fields['hrvState'] not in {'number', 'zero'}
            abnormal = (expected_hr is True and fields['hrvState'] not in {'number', 'zero'}) or (lost and context.get('hrRequested') is not False)
            signature = tuple((k, fields.get(k)) for k in ('algorithmObjectState', 'hrState', 'hrValueState', 'hrvState',
                'upstreamHrvState', 'sdkHrvState', 'serializedHrvState', 'valid', 'outcome', 'reason'))
            key = (stage, context.get('mode'), context.get('action'), context.get('hrRequested'), expected_hr)
            now = time.monotonic()
            previous = self._last.get(key)
            debug = LOG.isEnabledFor(logging.DEBUG)
            emit = debug or previous is None or previous[0] != signature or (self.interval_seconds > 0 and now - previous[1] >= self.interval_seconds)
            if not emit:
                self._last[key] = (previous[0], previous[1], previous[2] + 1)
                return
            fields['suppressed'] = previous[2] if previous else 0
            self._last[key] = (signature, now, 0)
            self._last.move_to_end(key)
            while len(self._last) > 128:
                self._last.popitem(last=False)
            LOG.log(logging.WARNING if abnormal else logging.INFO, 'HRV trace: %s', json.dumps(fields, separators=(',', ':')))
        except Exception:
            return

    def envelope(self, stage, message, *, upstream=_UNSET, **context):
        try:
            data = message.get('data', {})
            if data.get("event") in {"status", "replayEnded"}:
                return
            result = data.get('result', data)
            if not isinstance(result, Mapping) or 'payload' not in result:
                return
            payload = result['payload']
            algorithm = payload.get('algorithm') if isinstance(payload, Mapping) else None
            before = _UNSET
            if upstream is not _UNSET:
                source = upstream.get('data', {})
                source = source.get('result', source)
                before = source.get('payload', {}).get('algorithm')
            self.observe(stage, algorithm, upstream=before,
                mode=result.get('mode'), timestampMs=result.get('timestampMs'), valid=result.get('valid'),
                gatewayBootId=data.get('gatewayBootId', result.get('gatewayBootId')),
                subscriptionId=data.get('subscriptionId'), requestId=data.get('requestId'),
                action=data.get('action'), **context)
        except Exception:
            return

    def serialized(self, wire, source, **context):
        try:
            self.envelope('websocket_sent', json.loads(wire), upstream=source, **context)
        except Exception:
            self.observe('websocket_sent', None, outcome='diagnostic_decode_failed')
