"""Locked northbound projections, injected into application use cases."""

from typing import Any
from .protocol import envelope
from ...ports.errors import ProtocolError
from ...application.gateway import STREAM_NOT_AVAILABLE_REASON, REPLAY_NOT_AVAILABLE_REASON


class GatewayWireCodec:
    envelope = staticmethod(envelope)

    def filtered_payload(self, raw: dict, algorithm_payload: dict | None, streams: frozenset[str]) -> dict:
        payload: dict = {}
        if "eeg.raw" in streams and "eegRaw" in raw:
            payload["eegRaw"] = raw["eegRaw"]
        if "hr.raw" in streams and "hrRaw" in raw:
            payload["hrRaw"] = raw["hrRaw"]
        if algorithm_payload:
            algorithm: dict = {}
            if "eeg" in streams:
                algorithm.update({key: value for key, value in algorithm_payload.items() if key not in {"hr", "pressure", "coherence", "arousal"}})
            if "hr" in streams:
                algorithm.update({key: value for key, value in algorithm_payload.items() if key in {"hr", "pressure", "coherence", "arousal"}})
            if algorithm:
                payload["algorithm"] = algorithm
        return payload

    def event_data(self, gateway, event: str, subscription_id: str | None, timestamp_ms: int, mode: str, valid: bool, payload: dict) -> dict:
        data = {"event": event, "gatewayBootId": gateway.boot_id, "subjectId": gateway.config.recording.subject_id, "mode": mode, "timestampMs": timestamp_ms, "valid": valid, "payload": payload}
        if subscription_id:
            data["subscriptionId"] = subscription_id
        return data

    def northbound_status(self, gateway) -> dict[str, Any]:
        """Project transport-specific state onto the locked v0.2 status schema."""

        status = {
            name: gateway.status[name]
            for name in ("connectionState", "wearState", "batteryPercent", "signalQuality", "algorithmState")
        }
        status["connectionState"] = {
            "validated": "connected",
            "connected": "connected",
            "connecting": "connecting",
            "validating": "connecting",
            "not_connected": "disconnected",
            "validation_failed": "disconnected",
            "disconnected": "disconnected",
        }.get(str(status["connectionState"]), "disconnected")
        return status


    def status_result(self, gateway):
        from time import time
        return {"gatewayBootId": gateway.boot_id, "subjectId": gateway.config.recording.subject_id,
                "mode": gateway.mode(), **self.northbound_status(gateway),
                "availableStreams": sorted(gateway.available_streams()), "serverTimeMs": int(time() * 1000)}

    def error(self, request_id, error):
        data = {"reason": error.reason, "retryable": error.retryable, "details": error.details}
        if request_id:
            data["requestId"] = request_id
        return envelope(error.code, data, error.message)

    def offline_data_error(self, gateway) -> ProtocolError:
        if not gateway.supports_replay:
            if gateway.status["connectionState"] == "validation_failed":
                message = "Serial device validation failed: no complete valid data frame was received after starting acquisition."
            else:
                message = "Serial data source is not connected; live data is unavailable and replay is not supported."
            return ProtocolError(409, STREAM_NOT_AVAILABLE_REASON, message, True)
        return ProtocolError(503, REPLAY_NOT_AVAILABLE_REASON, "No replay data is available.", True)
