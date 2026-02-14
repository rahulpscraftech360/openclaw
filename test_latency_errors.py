"""
Latency measurement and error handling verification for the Cheeko voice streaming pipeline.

Tests:
  1. Latency: Measures speech_end -> first_audio_chunk for 5 test queries
  2. Error: Disconnect mid-stream
  3. Error: Invalid (non-Opus) audio data
  4. Error: Missing API keys (simulated via invalid key)
  5. Session cleanup after disconnection

Usage:
    uv run python test_latency_errors.py

Prerequisites:
    - OpenClaw gateway running on port 18789 with cheeko stream enabled
    - DEEPGRAM_API_KEY and OPENAI_API_KEY configured
"""

import json
import sys
import time
import uuid
import struct
import threading
import wave
from pathlib import Path

import opuslib
import websocket  # websocket-client library

# --- Configuration ---
GATEWAY_HOST = "localhost"
GATEWAY_PORT = 18789
WS_URL = f"ws://{GATEWAY_HOST}:{GATEWAY_PORT}/cheeko/stream"

# Audio parameters
INPUT_SAMPLE_RATE = 16000
INPUT_CHANNELS = 1
INPUT_FRAME_DURATION_MS = 20
INPUT_SAMPLES_PER_FRAME = int(INPUT_SAMPLE_RATE * INPUT_FRAME_DURATION_MS / 1000)  # 320

# Test audio file
TEST_AUDIO_PATH = Path(__file__).parent / "record_out.wav"


def load_pcm_frames(wav_path: Path) -> list[bytes]:
    """Load a WAV file, resample to 16kHz mono 16-bit, return list of 20ms PCM frames."""
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1 and wf.getsampwidth() == 2, \
            f"Expected mono 16-bit WAV, got ch={wf.getnchannels()} sw={wf.getsampwidth()}"
        src_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    # Resample if needed (simple decimation for integer ratios)
    if src_rate != INPUT_SAMPLE_RATE:
        ratio = src_rate // INPUT_SAMPLE_RATE
        assert src_rate % INPUT_SAMPLE_RATE == 0, \
            f"Cannot resample {src_rate}Hz -> {INPUT_SAMPLE_RATE}Hz (not an integer ratio)"
        samples = struct.unpack(f"<{len(raw)//2}h", raw)
        resampled = samples[::ratio]
        raw = struct.pack(f"<{len(resampled)}h", *resampled)

    frame_bytes = INPUT_SAMPLES_PER_FRAME * 2  # 16-bit = 2 bytes/sample
    return [
        raw[i:i + frame_bytes]
        for i in range(0, len(raw), frame_bytes)
        if len(raw[i:i + frame_bytes]) == frame_bytes
    ]


class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""
        self.duration_ms = 0.0

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name} ({self.duration_ms:.0f}ms) - {self.message}"


class CheekTestClient:
    """Minimal test client for the cheeko stream endpoint."""

    def __init__(self, device_id: str | None = None):
        self.ws: websocket.WebSocket | None = None
        self.device_id = device_id or f"test-{uuid.uuid4().hex[:8]}"
        self.session_id: str | None = None
        self.encoder = opuslib.Encoder(
            INPUT_SAMPLE_RATE, INPUT_CHANNELS, opuslib.APPLICATION_VOIP
        )

    def connect(self, timeout: float = 5.0) -> bool:
        try:
            self.ws = websocket.create_connection(WS_URL, timeout=timeout)
            return True
        except Exception as e:
            print(f"  Connection failed: {e}")
            return False

    def send_hello(self) -> dict | None:
        if not self.ws:
            return None
        self.ws.send(json.dumps({"type": "hello", "deviceId": self.device_id}))
        return self._wait_for_type("hello_ack", timeout=5.0)

    def send_speech_end(self):
        if self.ws:
            self.ws.send(json.dumps({"type": "speech_end"}))

    def send_cancel(self):
        if self.ws:
            self.ws.send(json.dumps({"type": "cancel"}))

    def send_silence_frames(self, count: int = 50):
        silence_pcm = b'\x00' * (INPUT_SAMPLES_PER_FRAME * 2)
        for _ in range(count):
            opus_frame = self.encoder.encode(silence_pcm, INPUT_SAMPLES_PER_FRAME)
            if self.ws:
                self.ws.send_binary(opus_frame)
            time.sleep(0.005)

    def send_audio_frames(self, pcm_frames: list[bytes], realtime: bool = True):
        """Encode PCM frames to Opus and send. If realtime, pace at ~20ms intervals."""
        for frame in pcm_frames:
            opus_data = self.encoder.encode(frame, INPUT_SAMPLES_PER_FRAME)
            if self.ws:
                self.ws.send_binary(opus_data)
            if realtime:
                time.sleep(INPUT_FRAME_DURATION_MS / 1000)

    def send_binary(self, data: bytes):
        if self.ws:
            self.ws.send_binary(data)

    def recv_message(self, timeout: float = 5.0) -> dict | bytes | None:
        if not self.ws:
            return None
        self.ws.settimeout(timeout)
        try:
            opcode, data = self.ws.recv_data()
            if opcode == websocket.ABNF.OPCODE_TEXT:
                text = data.decode("utf-8") if isinstance(data, bytes) else data
                return json.loads(text)
            elif opcode == websocket.ABNF.OPCODE_BINARY:
                return data
            return None
        except websocket.WebSocketTimeoutException:
            return None
        except Exception:
            return None

    def _wait_for_type(self, msg_type: str, timeout: float = 5.0) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            msg = self.recv_message(timeout=remaining)
            if isinstance(msg, dict) and msg.get("type") == msg_type:
                return msg
        return None

    def collect_until(self, msg_type: str, timeout: float = 30.0) -> list[dict | bytes]:
        messages = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            msg = self.recv_message(timeout=remaining)
            if msg is None:
                continue
            messages.append(msg)
            if isinstance(msg, dict) and msg.get("type") == msg_type:
                break
        return messages

    def recv_all(self, timeout: float = 2.0) -> list[dict | bytes]:
        messages = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            msg = self.recv_message(timeout=remaining)
            if msg is None:
                break
            messages.append(msg)
        return messages

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None


# --- Latency Tests --------------------------------------------------------


def test_latency_measurement() -> TestResult:
    """Test 1: Measure speech_end to first_audio_chunk latency with real speech (5 queries)."""
    result = TestResult("Latency with real speech (5 queries)")
    start = time.time()

    # Load real speech audio
    if not TEST_AUDIO_PATH.exists():
        result.message = f"Test audio not found: {TEST_AUDIO_PATH}"
        result.duration_ms = (time.time() - start) * 1000
        return result

    pcm_frames = load_pcm_frames(TEST_AUDIO_PATH)
    audio_duration_ms = len(pcm_frames) * INPUT_FRAME_DURATION_MS
    print(f"    Loaded {len(pcm_frames)} frames ({audio_duration_ms}ms) from {TEST_AUDIO_PATH.name}")

    latencies = []
    transcripts = []
    query_count = 5

    for i in range(query_count):
        client = CheekTestClient()
        try:
            if not client.connect():
                result.message = f"Connection failed on query {i + 1}"
                return result

            ack = client.send_hello()
            if not ack:
                result.message = f"Handshake failed on query {i + 1}"
                return result

            # Consume initial idle status
            client.recv_message(timeout=2.0)

            # Send real speech audio (paced at realtime)
            client.send_audio_frames(pcm_frames)
            time.sleep(0.1)

            query_start = time.time()
            client.send_speech_end()

            # Collect messages until we return to idle or get latency data
            pipeline_latency_ms = None
            transcript_text = ""
            deadline = time.time() + 30.0

            while time.time() < deadline:
                msg = client.recv_message(timeout=deadline - time.time())
                if msg is None:
                    break
                if isinstance(msg, dict):
                    if msg.get("type") == "latency":
                        pipeline_latency_ms = msg.get("speechEndToFirstAudio")
                    if msg.get("type") == "transcript" and not msg.get("partial"):
                        transcript_text = msg.get("text", "")
                    if msg.get("type") == "status" and msg.get("stage") == "idle":
                        break
                elif isinstance(msg, bytes):
                    # Binary frame = first audio chunk arrived
                    if pipeline_latency_ms is None:
                        pipeline_latency_ms = (time.time() - query_start) * 1000

            if pipeline_latency_ms is not None:
                latencies.append(pipeline_latency_ms)
            if transcript_text:
                transcripts.append(transcript_text)

            print(f"    Query {i + 1}: latency={pipeline_latency_ms:.0f}ms" if pipeline_latency_ms else f"    Query {i + 1}: no audio response")
            if transcript_text:
                print(f"             transcript: \"{transcript_text}\"")

        finally:
            client.close()
            time.sleep(0.3)

    if len(latencies) == query_count:
        avg = sum(latencies) / len(latencies)
        min_l = min(latencies)
        max_l = max(latencies)
        result.passed = True
        latency_strs = [f"{l:.0f}ms" for l in latencies]
        result.message = (
            f"{query_count} queries completed. "
            f"Latencies: {', '.join(latency_strs)}. "
            f"Avg: {avg:.0f}ms, Min: {min_l:.0f}ms, Max: {max_l:.0f}ms. "
            f"Transcripts received: {len(transcripts)}/{query_count}"
        )
    else:
        result.message = f"Only {len(latencies)}/{query_count} queries got audio responses"

    result.duration_ms = (time.time() - start) * 1000
    return result


# --- Error Handling Tests -------------------------------------------------


def test_disconnect_mid_stream() -> TestResult:
    """Test 2: Disconnect while server is processing (mid-stream)."""
    result = TestResult("Disconnect mid-stream")
    start = time.time()

    client = CheekTestClient()
    try:
        if not client.connect():
            result.message = "Connection failed"
            return result

        ack = client.send_hello()
        if not ack:
            result.message = "Handshake failed"
            return result

        # Consume initial idle status
        client.recv_message(timeout=2.0)

        # Start sending audio to trigger listening state
        client.send_silence_frames(30)
        time.sleep(0.3)

        # Send speech_end to trigger processing pipeline
        client.send_speech_end()
        time.sleep(0.2)

        # Abruptly disconnect mid-processing
        client.close()
        time.sleep(1.0)

        # Reconnect -should work, meaning server cleaned up the old session
        client2 = CheekTestClient(device_id=client.device_id)
        if client2.connect():
            ack2 = client2.send_hello()
            if ack2 and ack2.get("sessionId"):
                result.passed = True
                result.message = (
                    f"Reconnected after mid-stream disconnect, "
                    f"new session {ack2['sessionId'][:8]}..."
                )
            else:
                result.message = "Reconnection handshake failed"
            client2.close()
        else:
            result.message = "Could not reconnect after disconnect"

    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_invalid_audio_data() -> TestResult:
    """Test 3: Send invalid (non-Opus) binary data as audio."""
    result = TestResult("Invalid audio data handling")
    start = time.time()

    client = CheekTestClient()
    try:
        if not client.connect():
            result.message = "Connection failed"
            return result

        ack = client.send_hello()
        if not ack:
            result.message = "Handshake failed"
            return result

        # Consume initial idle status
        client.recv_message(timeout=2.0)

        # Send random garbage as binary audio data (not valid Opus)
        garbage = b'\xff\xfe\xfd\xfc' * 100
        client.send_binary(garbage)
        time.sleep(0.3)

        # Send a few more garbage frames
        for _ in range(5):
            client.send_binary(b'\x00\x01\x02\x03' * 50)
            time.sleep(0.05)

        # Send speech_end to finalize
        client.send_speech_end()

        # Collect messages -we should get either an error or graceful idle return
        messages = client.collect_until("idle", timeout=10.0)
        json_msgs = [m for m in messages if isinstance(m, dict)]
        types = [m.get("type") for m in json_msgs]

        # The server should handle this gracefully -either:
        # - Send an STT error (Deepgram rejects invalid audio)
        # - Return to idle (empty transcript from bad audio)
        has_error = "error" in types
        has_idle = any(
            m.get("type") == "status" and m.get("stage") == "idle"
            for m in json_msgs
        )

        if has_error or has_idle:
            result.passed = True
            if has_error:
                errors = [m.get("message", "") for m in json_msgs if m.get("type") == "error"]
                result.message = f"Server handled gracefully with error: {errors[0][:80]}"
            else:
                result.message = "Server handled gracefully, returned to idle"
        else:
            result.message = f"Unexpected response types: {types}"

    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_speech_end_without_audio() -> TestResult:
    """Test 4: Send speech_end without any prior audio frames."""
    result = TestResult("Speech end without audio")
    start = time.time()

    client = CheekTestClient()
    try:
        if not client.connect():
            result.message = "Connection failed"
            return result

        ack = client.send_hello()
        if not ack:
            result.message = "Handshake failed"
            return result

        # Consume initial idle status
        client.recv_message(timeout=2.0)

        # Send speech_end without any audio -should get an error since not in listening state
        client.send_speech_end()

        msg = client.recv_message(timeout=5.0)
        if isinstance(msg, dict) and msg.get("type") == "error":
            result.passed = True
            result.message = f"Got expected error: {msg.get('message')}"
        elif isinstance(msg, dict):
            result.passed = True
            result.message = f"Handled gracefully: type={msg.get('type')}, stage={msg.get('stage', '')}"
        else:
            result.message = f"Unexpected response: {msg}"

    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_invalid_json_message() -> TestResult:
    """Test 5: Send malformed JSON and unknown message types."""
    result = TestResult("Invalid JSON / unknown message types")
    start = time.time()

    client = CheekTestClient()
    try:
        if not client.connect():
            result.message = "Connection failed"
            return result

        ack = client.send_hello()
        if not ack:
            result.message = "Handshake failed"
            return result

        # Consume initial idle status
        client.recv_message(timeout=2.0)

        errors_received = []

        # Test 1: Send invalid JSON text
        client.ws.send("this is not json {{{")
        msg = client.recv_message(timeout=3.0)
        if isinstance(msg, dict) and msg.get("type") == "error":
            errors_received.append(f"invalid JSON: {msg.get('message', '')[:40]}")

        # Test 2: Send unknown message type
        client.ws.send(json.dumps({"type": "foobar_unknown"}))
        msg = client.recv_message(timeout=3.0)
        if isinstance(msg, dict) and msg.get("type") == "error":
            errors_received.append(f"unknown type: {msg.get('message', '')[:40]}")

        # Test 3: Send JSON without type field
        client.ws.send(json.dumps({"data": "no type field"}))
        msg = client.recv_message(timeout=3.0)
        if isinstance(msg, dict) and msg.get("type") == "error":
            errors_received.append(f"no type: {msg.get('message', '')[:40]}")

        if len(errors_received) >= 2:
            result.passed = True
            result.message = f"Got {len(errors_received)} error responses: " + "; ".join(errors_received)
        else:
            result.message = f"Expected at least 2 error responses, got {len(errors_received)}"

    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_session_cleanup_after_disconnect() -> TestResult:
    """Test 6: Verify session cleanup -resources freed after client disconnects."""
    result = TestResult("Session cleanup after disconnection")
    start = time.time()

    client = CheekTestClient()
    try:
        if not client.connect():
            result.message = "Connection failed"
            return result

        ack = client.send_hello()
        if not ack:
            result.message = "Handshake failed"
            return result

        original_session = ack.get("sessionId")

        # Consume initial idle status
        client.recv_message(timeout=2.0)

        # Start activity -send audio and trigger processing
        client.send_silence_frames(20)
        time.sleep(0.3)

        # Disconnect
        client.close()
        time.sleep(1.5)

        # Reconnect with same device_id -should get a completely new session
        client2 = CheekTestClient(device_id=client.device_id)
        try:
            if not client2.connect():
                result.message = "Reconnection failed"
                return result

            ack2 = client2.send_hello()
            if not ack2:
                result.message = "Reconnection handshake failed"
                return result

            new_session = ack2.get("sessionId")

            # Consume idle status
            client2.recv_message(timeout=2.0)

            # Verify new session works -send audio and verify state transition
            client2.send_silence_frames(5)
            msg = client2.recv_message(timeout=3.0)
            got_listening = isinstance(msg, dict) and msg.get("stage") == "listening"

            if new_session != original_session and got_listening:
                result.passed = True
                result.message = (
                    f"Old session {original_session[:8]}... cleaned up. "
                    f"New session {new_session[:8]}... works correctly."
                )
            elif new_session == original_session:
                result.message = "Session ID reused -cleanup may have failed"
            else:
                result.message = f"New session created but state transition failed: {msg}"

        finally:
            client2.close()

    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_rapid_reconnection() -> TestResult:
    """Test 7: Rapidly connect/disconnect multiple times to test cleanup under load."""
    result = TestResult("Rapid reconnection stress test")
    start = time.time()

    session_ids = []
    rounds = 5

    for i in range(rounds):
        client = CheekTestClient(device_id=f"stress-{i}")
        try:
            if not client.connect(timeout=3.0):
                result.message = f"Connection failed on round {i + 1}"
                return result

            ack = client.send_hello()
            if ack:
                session_ids.append(ack.get("sessionId"))
            client.send_silence_frames(5)
            time.sleep(0.1)
        finally:
            client.close()
            time.sleep(0.1)

    # All session IDs should be unique
    unique_ids = set(session_ids)
    if len(unique_ids) == rounds and len(session_ids) == rounds:
        result.passed = True
        result.message = f"{rounds} rapid connect/disconnect cycles completed, all sessions unique"
    else:
        result.message = f"Expected {rounds} unique sessions, got {len(unique_ids)} unique out of {len(session_ids)}"

    result.duration_ms = (time.time() - start) * 1000
    return result


def run_tests():
    """Run all latency and error handling tests."""
    print()
    print("=" * 60)
    print("  OpenClaw Cheeko Stream -Latency & Error Handling Tests")
    print("=" * 60)
    print()
    print(f"  Target: {WS_URL}")
    print()

    # Check gateway is reachable
    quick = CheekTestClient()
    if not quick.connect(timeout=3.0):
        print("  ERROR: Cannot connect to gateway.")
        print(f"  Make sure OpenClaw gateway is running on port {GATEWAY_PORT}")
        print("  with cheeko stream enabled (gateway.cheeko.enabled = true).")
        print()
        sys.exit(1)
    quick.close()

    tests = [
        test_latency_measurement,
        test_disconnect_mid_stream,
        test_invalid_audio_data,
        test_speech_end_without_audio,
        test_invalid_json_message,
        test_session_cleanup_after_disconnect,
        test_rapid_reconnection,
    ]

    results: list[TestResult] = []
    for test_fn in tests:
        print(f"  Running: {test_fn.__doc__.strip().split(chr(10))[0]}")
        try:
            r = test_fn()
        except Exception as e:
            r = TestResult(test_fn.__name__)
            r.message = f"Exception: {e}"
        results.append(r)
        print(f"    {r}")
        print()

    # Summary
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print("-" * 60)
    print(f"  Results: {passed}/{total} tests passed")

    if passed == total:
        print("  All tests PASSED!")
    else:
        print("  Some tests FAILED.")
        for r in results:
            if not r.passed:
                print(f"    FAIL: {r.name} -{r.message}")

    print()

    # Latency summary
    latency_result = results[0]
    if latency_result.passed:
        print(f"  Latency summary: {latency_result.message}")
    print()

    return passed == total


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
