#!/usr/bin/env python3
"""B-side receive probe. Prints counts and metadata, never physiological values."""

import argparse
import asyncio
from collections import Counter
import json
import sys
import time
import uuid


class ProbeError(Exception):
    pass


async def receive(ws, timeout):
    raw = await asyncio.wait_for(ws.recv(), timeout)
    if not isinstance(raw, str):
        raise ProbeError("Expected a UTF-8 JSON text frame.")
    message = json.loads(raw)
    if not isinstance(message, dict) or message.get("protocolVersion") != "1.0":
        raise ProbeError("Unexpected protocol envelope.")
    if message.get("code") != 200:
        raise ProbeError("Gateway error code={}: {}".format(message.get("code"), message.get("message", "")))
    data = message.get("data")
    if not isinstance(data, dict):
        raise ProbeError("Missing response/event data object.")
    return data


async def request(ws, action, params):
    request_id = str(uuid.uuid4())
    await ws.send(json.dumps({
        "protocolVersion": "1.0", "messageType": "request",
        "requestId": request_id, "action": action, "params": params,
    }))
    deadline = time.monotonic() + 8
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("Timed out waiting for " + action)
        data = await receive(ws, remaining)
        if data.get("requestId") == request_id:
            result = data.get("result")
            if not isinstance(result, dict):
                raise ProbeError("Missing action result.")
            return result


async def probe(url, seconds=20, max_batches=5):
    import websockets

    async with websockets.connect(
        url, subprotocols=["neurobridge.v1"], ping_interval=None,
        ping_timeout=None, open_timeout=5, close_timeout=3, max_size=8 * 1024 * 1024,
    ) as ws:
        if ws.subprotocol != "neurobridge.v1":
            raise ProbeError("Gateway did not negotiate neurobridge.v1.")
        print("WebSocket OK; subprotocol=neurobridge.v1", flush=True)
        status = await request(ws, "getStatus", {})
        available = status.get("availableStreams", [])
        streams = [s for s in ("eeg.raw", "hr.raw", "eeg", "hr") if s in available]
        print("Status: connection={} algorithm={} mode={}; streams={}".format(
            status.get("connectionState"), status.get("algorithmState"),
            status.get("mode"), ",".join(streams) or "none",
        ), flush=True)
        if not streams:
            print("NO DATA: status query succeeded, but no data streams are currently available.")
            return 2
        result = await request(ws, "subscribe", {"streams": streams, "includeInvalid": True})
        subscription = result.get("subscriptionId")
        if not isinstance(subscription, str) or not subscription:
            raise ProbeError("Missing subscriptionId.")
        print("Subscription OK; observing up to {}s / {} batches.".format(seconds, max_batches), flush=True)
        counts = Counter()
        deadline = time.monotonic() + seconds
        try:
            while counts["batches"] < max_batches:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    data = await receive(ws, remaining)
                except asyncio.TimeoutError:
                    break
                if data.get("event") != "data" or data.get("subscriptionId") != subscription:
                    continue
                payload = data.get("payload", {})
                if not isinstance(payload, dict):
                    raise ProbeError("Invalid data payload.")
                present = []
                for key in ("eegRaw", "hrRaw"):
                    item = payload.get(key)
                    if isinstance(item, dict) and isinstance(item.get("byteLength"), int) and item["byteLength"] > 0 and item.get("bytesBase64"):
                        present.append(key)
                        counts[key] += 1
                if isinstance(payload.get("algorithm"), dict) and payload["algorithm"]:
                    present.append("algorithm")
                    counts["algorithm"] += 1
                counts["batches"] += 1
                mode = data.get("mode")
                valid = data.get("valid") is True and bool(present) and mode in ("live", "replay")
                if valid:
                    counts["valid"] += 1
                    counts[mode] += 1
                print("DATA batch={} mode={} valid={} timestampMs={} fields={}".format(
                    counts["batches"], mode, data.get("valid"), data.get("timestampMs"),
                    ",".join(present) or "empty",
                ), flush=True)
        finally:
            try:
                await request(ws, "unsubscribe", {"subscriptionId": subscription})
            except Exception:
                # Closing this connection also removes its subscriptions.
                pass
        print("Summary: batches={} valid={} live={} replay={} eegRaw={} hrRaw={} algorithm={}".format(
            counts["batches"], counts["valid"], counts["live"], counts["replay"],
            counts["eegRaw"], counts["hrRaw"], counts["algorithm"],
        ))
        if counts["live"]:
            print("PASS: received valid live data on this B-side connection.")
            return 0
        if counts["replay"]:
            print("REPLAY ONLY: receive path works; live device capture is not verified.")
            return 0
        print("NO VALID DATA: handshake/status/subscription alone do not verify data reception.")
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="ws://<gateway>:<port>/neurobridge/v1/ws")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--max-batches", type=int, default=5)
    args = parser.parse_args()
    if args.seconds <= 0 or args.max_batches <= 0:
        parser.error("--seconds and --max-batches must be positive")
    try:
        return asyncio.run(probe(args.url, args.seconds, args.max_batches))
    except ModuleNotFoundError as error:
        if error.name != "websockets":
            raise
        print("Install dependency first: python -m pip install websockets==12.0", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print("FAIL: {}: {}".format(type(error).__name__, error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
