"""Exercise the actual SDK process with synthetic, dual-channel EEG packets."""

import base64
import json
import math
import subprocess
import sys


HEADSET = "headset_rev181"
HEADBAND = "headband_ble"


def request(protocol, eeg=b""):
    return {"deviceProtocol": protocol, "eegRawBase64": base64.b64encode(eeg).decode("ascii"), "hrRawBase64": ""}


def eeg_windows():
    # Use the SDK's fixture rate and packet layout, independently of real-device timing.
    packets = []
    for sequence in range(400):
        packet = bytearray(sequence.to_bytes(2, "big"))
        for sample in range(sequence * 3, (sequence + 1) * 3):
            for frequency in (10, 18):
                value = int(8388607 + 12000 * math.sin(2 * math.pi * frequency * sample / 250))
                packet.extend(value.to_bytes(3, "big"))
        packets.append(packet)
    return [b"".join(packets[start:start + 50]) for start in range(0, len(packets), 50)]


def run_bridge(executable, requests):
    output = subprocess.run([executable], input="".join(json.dumps(item) + "\n" for item in requests),
                            capture_output=True, text=True, timeout=30, check=True)
    return [json.loads(line) for line in output.stdout.splitlines()]


def main(executable):
    windows = eeg_windows()
    ear_requests = [request(HEADSET), *(request(HEADSET, window) for window in windows)]
    ear = run_bridge(executable, ear_requests)
    band = run_bridge(executable, [request(HEADBAND, window) for window in windows])
    assert ear[0] == {"deviceProtocol": HEADSET, "algorithm": {}}
    assert all(row["deviceProtocol"] == HEADSET for row in ear)
    assert all(row["deviceProtocol"] == HEADBAND for row in band)
    ear_power = ear[-1]["algorithm"]["eeg"]["bandPower"]
    band_power = band[-1]["algorithm"]["eeg"]["bandPower"]
    assert not math.isclose(ear_power["gamma"], band_power["gamma"], abs_tol=1e-6), (ear_power, band_power)

    wrong_index = 3
    mixed = run_bridge(executable, [*ear_requests[:wrong_index], request(HEADBAND, windows[0]), *ear_requests[wrong_index:]])
    assert "bridgeError" in mixed[wrong_index] and mixed[wrong_index]["algorithm"] == {}
    assert mixed[:wrong_index] + mixed[wrong_index + 1:] == ear


if __name__ == "__main__":
    main(sys.argv[1])
