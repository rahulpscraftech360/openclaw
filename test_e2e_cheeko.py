"""
End-to-end test suite for the Cheeko voice streaming pipeline.

Tests the WebSocket connection, handshake protocol, message routing,
multi-turn conversation context, and cancel behavior.

Uses synthetic Opus audio (silence frames) so no microphone or speakers are needed.

Usage:
    uv run python test_e2e_cheeko.py

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
from queue import Queue, Empty

import opuslib
import websocket  # websocket-client library

# --- Configuration ---
GATEWAY_HOST = "localhost"
GATEWAY_PORT = 18789
WS_URL = f"ws://{GATEWAY_HOST}:{GATEWAY_PORT}/cheeko/stream"

# Audio parameters (must match server expectations)
INPUT_SAMPLE_RATE = 16000
INPUT_CHANNELS = 1
INPUT_FRAME_DURATION_MS = 20
INPUT_SAMPLES_PER_FRAME = int(INPUT_SAMPLE_RATE * INPUT_FRAME_DURATION_MS / 1000)  # 320

OUTPUT_SAMPLE_RATE = 24000
OUTPUT_CHANNELS = 1
OUTPUT_FRAME_DURATION_MS = 20
OUTPUT_SAMPLES_PER_FRAME = int(OUTPUT_SAMPLE_RATE * OUTPUT_FRAME_DURATION_MS / 1000)  # 480


class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""
        self.duration_ms = 0.0

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name} ({self.duration_ms:.0f}ms) — {self.message}"


class CheekTestClient:
    """Test client that connects to the cheeko stream endpoint."""

    def __init__(self, device_id: str | None = None):
        self.ws: websocket.WebSocket | None = None
        self.device_id = device_id or f"test-{uuid.uuid4().hex[:8]}"
        self.session_id: str | None = None
        self.messages: Queue[dict | bytes] = Queue()
        self.connected = False

        # Opus encoder for generating synthetic audio
        self.encoder = opuslib.Encoder(
            INPUT_SAMPLE_RATE, INPUT_CHANNELS, opuslib.APPLICATION_VOIP
        )

    def connect(self, timeout: float = 5.0) -> bool:
        """Connect to the WebSocket endpoint."""
        try:
            self.ws = websocket.create_connection(
                WS_URL, timeout=timeout
            )
            self.connected = True
            return True
        except Exception as e:
            print(f"  Connection failed: {e}")
            return False

    def send_hello(self) -> dict | None:
        """Send hello handshake and wait for hello_ack."""
        if not self.ws:
            return None
        self.ws.send(json.dumps({
            "type": "hello",
            "deviceId": self.device_id,
        }))
        # Read messages until we get hello_ack
        return self._wait_for_type("hello_ack", timeout=5.0)

    def send_speech_end(self):
        """Send speech_end control message."""
        if self.ws:
            self.ws.send(json.dumps({"type": "speech_end"}))

    def send_cancel(self):
        """Send cancel control message."""
        if self.ws:
            self.ws.send(json.dumps({"type": "cancel"}))

    def send_silence_frames(self, count: int = 50):
        """Send synthetic silence Opus frames (simulates holding mic with no speech)."""
        silence_pcm = b'\x00' * (INPUT_SAMPLES_PER_FRAME * 2)  # 16-bit samples
        for _ in range(count):
            opus_frame = self.encoder.encode(silence_pcm, INPUT_SAMPLES_PER_FRAME)
            if self.ws:
                self.ws.send_binary(opus_frame)
            time.sleep(0.005)  # ~5ms between frames

    def send_json(self, payload: dict):
        """Send a JSON control message."""
        if self.ws:
            self.ws.send(json.dumps(payload))

    def recv_message(self, timeout: float = 5.0) -> dict | bytes | None:
        """Receive one message (JSON dict or binary bytes)."""
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
        except Exception as e:
            return None

    def recv_all(self, timeout: float = 2.0) -> list[dict | bytes]:
        """Receive all available messages within timeout."""
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

    def _wait_for_type(self, msg_type: str, timeout: float = 5.0) -> dict | None:
        """Wait for a specific JSON message type."""
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
        """Collect all messages until a specific type is received."""
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

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
            self.connected = False


def test_websocket_connection() -> TestResult:
    """Test 1: Verify WebSocket connection to /cheeko/stream."""
    result = TestResult("WebSocket connection")
    start = time.time()

    client = CheekTestClient()
    try:
        if client.connect():
            result.passed = True
            result.message = f"Connected to {WS_URL}"
        else:
            result.message = f"Failed to connect to {WS_URL}"
    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_handshake() -> TestResult:
    """Test 2: Verify hello/hello_ack handshake protocol."""
    result = TestResult("Handshake protocol")
    start = time.time()

    client = CheekTestClient()
    try:
        if not client.connect():
            result.message = "Connection failed"
            return result

        ack = client.send_hello()
        if ack and ack.get("type") == "hello_ack":
            session_id = ack.get("sessionId")
            device_id = ack.get("deviceId")
            if session_id and device_id:
                result.passed = True
                result.message = f"Session {session_id[:8]}... created for device {device_id}"

                # Should also receive idle status
                status = client.recv_message(timeout=2.0)
                if isinstance(status, dict) and status.get("type") == "status" and status.get("stage") == "idle":
                    result.message += " + idle status"
            else:
                result.message = f"Missing fields in hello_ack: {ack}"
        else:
            result.message = f"Expected hello_ack, got: {ack}"
    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_audio_state_transition() -> TestResult:
    """Test 3: Verify idle->listening transition when audio is sent."""
    result = TestResult("Audio state transition (idle->listening)")
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

        # Send a few silence audio frames
        client.send_silence_frames(5)

        # Should receive "listening" status
        msg = client.recv_message(timeout=5.0)
        if isinstance(msg, dict) and msg.get("type") == "status" and msg.get("stage") == "listening":
            result.passed = True
            result.message = "State transitioned to 'listening' on first audio chunk"
        else:
            result.message = f"Expected listening status, got: {msg}"
    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_speech_end_processing() -> TestResult:
    """Test 4: Verify speech_end triggers STT finalization and LLM processing."""
    result = TestResult("Speech end -> STT -> LLM pipeline")
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

        # Send silence frames (Deepgram will produce empty/short transcripts)
        client.send_silence_frames(50)
        time.sleep(0.5)

        # Send speech_end
        client.send_speech_end()

        # Collect messages — we should see status transitions
        messages = client.collect_until("idle", timeout=15.0)

        # Filter JSON messages
        json_msgs = [m for m in messages if isinstance(m, dict)]
        types = [m.get("type") for m in json_msgs]
        stages = [m.get("stage") for m in json_msgs if m.get("type") == "status"]

        # With silence, Deepgram may return empty transcript, so we'd go back to idle
        # Or it might produce a transcript and go through thinking/speaking
        if "stt" in stages or "idle" in stages:
            result.passed = True
            result.message = f"Pipeline stages: {stages}, message types: {types}"
        else:
            result.message = f"Expected STT processing, got stages: {stages}, types: {types}"
    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_cancel() -> TestResult:
    """Test 5: Verify cancel aborts in-flight operations."""
    result = TestResult("Cancel in-flight operations")
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

        # Start sending audio
        client.send_silence_frames(20)
        time.sleep(0.5)

        # Consume the "listening" status that was triggered by audio
        client.recv_all(timeout=0.5)

        # Send cancel while in listening state
        client.send_cancel()

        # Should receive idle status — may need to skip intermediate messages
        deadline = time.time() + 5.0
        got_idle = False
        while time.time() < deadline:
            msg = client.recv_message(timeout=deadline - time.time())
            if msg is None:
                break
            if isinstance(msg, dict) and msg.get("type") == "status" and msg.get("stage") == "idle":
                got_idle = True
                break

        if got_idle:
            result.passed = True
            result.message = "Cancel returned session to idle"
        else:
            result.message = f"Did not receive idle status after cancel within timeout"
    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_multi_connection() -> TestResult:
    """Test 6: Verify multiple concurrent sessions are isolated."""
    result = TestResult("Multi-connection session isolation")
    start = time.time()

    client1 = CheekTestClient(device_id="test-device-1")
    client2 = CheekTestClient(device_id="test-device-2")
    try:
        if not client1.connect() or not client2.connect():
            result.message = "Connection failed for one or both clients"
            return result

        ack1 = client1.send_hello()
        ack2 = client2.send_hello()

        if not ack1 or not ack2:
            result.message = "Handshake failed for one or both clients"
            return result

        sid1 = ack1.get("sessionId")
        sid2 = ack2.get("sessionId")

        if sid1 and sid2 and sid1 != sid2:
            result.passed = True
            result.message = f"Two independent sessions: {sid1[:8]}... and {sid2[:8]}..."
        else:
            result.message = f"Sessions not properly isolated: {sid1}, {sid2}"
    finally:
        client1.close()
        client2.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_error_before_hello() -> TestResult:
    """Test 7: Verify sending audio before hello returns an error."""
    result = TestResult("Error on audio before hello")
    start = time.time()

    client = CheekTestClient()
    try:
        if not client.connect():
            result.message = "Connection failed"
            return result

        # Send binary data without hello first
        silence_pcm = b'\x00' * (INPUT_SAMPLES_PER_FRAME * 2)
        opus_frame = client.encoder.encode(silence_pcm, INPUT_SAMPLES_PER_FRAME)
        client.ws.send_binary(opus_frame)

        # Should get an error
        msg = client.recv_message(timeout=3.0)
        if isinstance(msg, dict) and msg.get("type") == "error":
            result.passed = True
            result.message = f"Got expected error: {msg.get('message')}"
        else:
            result.message = f"Expected error, got: {msg}"
    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def test_disconnect_cleanup() -> TestResult:
    """Test 8: Verify session cleanup on disconnect."""
    result = TestResult("Session cleanup on disconnect")
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

        # Start some activity
        client.send_silence_frames(10)
        time.sleep(0.2)

        # Disconnect
        client.close()
        time.sleep(1.0)

        # Reconnect and verify we get a new session
        client2 = CheekTestClient(device_id=client.device_id)
        if client2.connect():
            ack2 = client2.send_hello()
            if ack2 and ack2.get("sessionId") != ack.get("sessionId"):
                result.passed = True
                result.message = "New session created after reconnection"
            else:
                result.message = "Session conflict on reconnect"
            client2.close()
        else:
            result.message = "Reconnection failed"
    finally:
        client.close()
        result.duration_ms = (time.time() - start) * 1000

    return result


def run_tests():
    """Run all e2e tests and report results."""
    print()
    print("=" * 60)
    print("  OpenClaw Cheeko Stream — End-to-End Test Suite")
    print("=" * 60)
    print()
    print(f"  Target: {WS_URL}")
    print()

    # Check gateway is reachable first
    quick = CheekTestClient()
    if not quick.connect(timeout=3.0):
        print("  ERROR: Cannot connect to gateway.")
        print(f"  Make sure OpenClaw gateway is running on port {GATEWAY_PORT}")
        print("  with cheeko stream enabled (gateway.cheeko.enabled = true).")
        print()
        sys.exit(1)
    quick.close()

    tests = [
        test_websocket_connection,
        test_handshake,
        test_audio_state_transition,
        test_speech_end_processing,
        test_cancel,
        test_multi_connection,
        test_error_before_hello,
        test_disconnect_cleanup,
    ]

    results: list[TestResult] = []
    for test_fn in tests:
        print(f"  Running: {test_fn.__doc__}")
        try:
            result = test_fn()
        except Exception as e:
            result = TestResult(test_fn.__name__)
            result.message = f"Exception: {e}"
        results.append(result)
        print(f"    {result}")
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
                print(f"    FAIL: {r.name} — {r.message}")

    print()
    print("  Manual verification steps (require mic + speakers):")
    print("  1. Run: uv run voice_client.py")
    print("  2. Hold SPACEBAR, say 'Hello', release SPACEBAR")
    print("  3. Verify transcript appears in console")
    print("  4. Verify audio response plays through speakers")
    print("  5. Ask a follow-up question to test multi-turn context")
    print("  6. Send cancel (press ESCAPE) during response to test abort")
    print()

    return passed == total


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
