# OpenClaw Voice Streaming Pipeline - Product Requirements Document

## Overview
Build a server-side voice streaming pipeline that allows a Python client to have real-time voice conversations with OpenClaw. The client captures microphone audio (push-to-talk via spacebar), streams Opus-encoded audio over WebSocket to a new gateway endpoint (`/cheeko/stream`), where the server handles STT (Deepgram Nova-2) → LLM (existing OpenClaw agent) → TTS (OpenAI). The server streams Opus audio responses back to the client for playback. This architecture keeps the client thin, making it reusable for ESP32/firmware later.

## Target Audience
- Developers testing the Cheeko voice device pipeline
- Future ESP32 hardware devices that need server-side audio processing
- Anyone wanting hands-free voice interaction with OpenClaw on Windows/Linux/Mac

## Core Features
1. **WebSocket Streaming Endpoint** — New `/cheeko/stream` endpoint in OpenClaw gateway accepting audio chunks and returning audio responses
2. **Streaming STT** — Deepgram Nova-2 real-time transcription of incoming Opus audio
3. **LLM Integration** — Route transcripts through existing OpenClaw chat pipeline
4. **Streaming TTS** — OpenAI TTS converts LLM text responses to audio, streamed back incrementally
5. **Python Client** — Push-to-talk (spacebar) mic capture, Opus encoding, WebSocket transport, and audio playback

## Tech Stack
- **Gateway Endpoint**: TypeScript/Node.js (inside existing OpenClaw `openclaw/src/gateway/`)
- **Client**: Python 3.11+ with `pyaudio`, `websockets`, `opuslib`, `keyboard`
- **STT**: Deepgram Nova-2 (streaming WebSocket API)
- **TTS**: OpenAI `gpt-4o-mini-tts` (streaming)
- **Audio Codec**: Opus (16kHz mono input, 24kHz mono output)
- **Transport**: WebSocket with JSON control frames + binary audio frames
- **Package Manager**: pnpm (gateway), uv (Python client)

## Architecture

```
Python Client (push-to-talk via spacebar)
    │
    │ WebSocket (ws://gateway:18789/cheeko/stream)
    │ ├── Binary frames: Opus audio chunks (20ms, 16kHz mono)
    │ └── JSON frames: control messages (speech_end, cancel, status)
    │
    ▼
OpenClaw Gateway (/cheeko/stream endpoint)
    ├── Deepgram Nova-2 STT (streaming WebSocket)
    │     └── Decodes Opus → PCM → pipes to Deepgram
    ├── OpenClaw LLM Agent (existing chat pipeline)
    │     └── Receives transcript → generates response
    └── OpenAI TTS (streaming)
          └── Text chunks → Opus audio → stream back to client
    │
    ▼
Python Client (audio playback)
    └── Decode Opus → PCM → speaker output
```

### Protocol Design

**Client → Server:**
- Binary frame: Raw Opus packet (20ms of audio)
- JSON `{"type": "speech_end"}` — User released spacebar, finalize STT
- JSON `{"type": "cancel"}` — Abort current response
- JSON `{"type": "hello", "deviceId": "...", "token": "..."}` — Initial handshake

**Server → Client:**
- JSON `{"type": "transcript", "text": "...", "partial": bool}` — STT result
- Binary frame: Opus audio chunk (TTS response)
- JSON `{"type": "audio_end"}` — TTS response complete
- JSON `{"type": "status", "stage": "stt"|"thinking"|"speaking"}` — Pipeline stage
- JSON `{"type": "error", "message": "..."}` — Error notification

## Data Model

### Session
- `deviceId`: string — unique client identifier
- `sessionId`: string — conversation session UUID
- `sttStream`: Deepgram WebSocket connection
- `ttsStream`: OpenAI TTS stream (active during response)
- `chatHistory`: array — conversation messages for LLM context
- `state`: enum — idle | listening | processing | speaking

### Audio Parameters
- Input: Opus, 16kHz, mono, 20ms frames
- Output: Opus, 24kHz, mono, variable frame size
- Deepgram input: PCM 16-bit, 16kHz, mono (decoded from Opus)
- OpenAI TTS output: PCM 24kHz (encoded to Opus before sending)

## Security Considerations
- Device authentication via token in handshake message
- Reuse existing gateway auth (`gateway.auth` config)
- Rate limiting on audio input (max continuous stream duration)
- Session isolation (each device gets its own STT/TTS streams)

## Third-Party Integrations
- **Deepgram** — Streaming STT via WebSocket API (Nova-2 model)
- **OpenAI** — TTS via streaming API (`gpt-4o-mini-tts` model)

## Constraints & Assumptions
- OpenClaw gateway must be running on port 18789
- Deepgram API key required (`DEEPGRAM_API_KEY` in env)
- OpenAI API key required (`OPENAI_API_KEY` in env)
- Python client requires working microphone and speakers
- `opuslib` requires system libopus (already available from existing client.py deps)
- Existing client.py already has pyaudio and opuslib as dependencies

## Success Criteria
- User can hold spacebar, speak into mic, and hear AI voice response through speakers
- Full round-trip working: audio capture → STT → LLM → TTS → playback
- End-to-end latency target: <1500ms (speech_end to first audio chunk)
- Session persistence across multiple turns in same connection

---

## Task List

```json
[
  {
    "category": "setup",
    "description": "Add Deepgram and OpenAI SDK dependencies to OpenClaw gateway",
    "steps": [
      "Add @deepgram/sdk and openai packages to openclaw/package.json dependencies",
      "Run pnpm install to resolve dependencies",
      "Verify imports work: create a minimal test file that imports both SDKs"
    ],
    "passes": true
  },
  {
    "category": "setup",
    "description": "Add environment variable configuration for voice streaming",
    "steps": [
      "Add DEEPGRAM_API_KEY and OPENAI_API_KEY to OpenClaw config schema (src/config/schema.ts)",
      "Add cheeko stream config section: enabled flag, deepgramModel, ttsModel, ttsVoice",
      "Document the new config options in config hints (src/config/schema.hints.ts)"
    ],
    "passes": true
  },
  {
    "category": "feature",
    "description": "Create the /cheeko/stream WebSocket endpoint handler",
    "steps": [
      "Create src/gateway/endpoints/cheeko-stream.ts with WebSocket upgrade handler",
      "Implement connection handshake: validate hello message, authenticate device, create session",
      "Implement message routing: binary frames to STT pipeline, JSON frames to control handler",
      "Register the endpoint in src/gateway/server-http.ts at path /cheeko/stream",
      "Add session cleanup on disconnect"
    ],
    "passes": true
  },
  {
    "category": "feature",
    "description": "Implement Opus decoding and Deepgram streaming STT integration",
    "steps": [
      "Add Opus decoder using opusscript or wasm-opus-decoder for Node.js",
      "Create STT service wrapper in src/gateway/services/cheeko-stt.ts",
      "Open streaming WebSocket connection to Deepgram Nova-2 on session start",
      "Decode incoming Opus frames to PCM 16-bit 16kHz and pipe to Deepgram",
      "Handle Deepgram transcript events: emit partial and final transcripts to client",
      "Handle speech_end control message: close Deepgram stream and finalize transcript"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Implement LLM routing through existing OpenClaw chat pipeline",
    "steps": [
      "Create chat service wrapper in src/gateway/services/cheeko-chat.ts",
      "On final transcript, create a chat.send request using existing gateway internals",
      "Subscribe to agent events for the run to capture streaming text response",
      "Buffer text into sentence-sized chunks for TTS (split on . ! ? boundaries)",
      "Maintain conversation history per session for multi-turn context",
      "Send status updates to client (thinking, speaking stages)"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Implement OpenAI TTS streaming with Opus encoding",
    "steps": [
      "Create TTS service wrapper in src/gateway/services/cheeko-tts.ts",
      "Use OpenAI streaming TTS API (gpt-4o-mini-tts model, pcm output format)",
      "Stream TTS as sentence chunks arrive from LLM (pipeline parallelism)",
      "Encode PCM output to Opus frames using opusscript encoder",
      "Send Opus frames as binary WebSocket messages to client",
      "Send audio_end JSON message when response is complete"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Build the Python voice client with push-to-talk",
    "steps": [
      "Create voice_client.py at project root based on existing client.py patterns",
      "Implement WebSocket connection to ws://localhost:18789/cheeko/stream",
      "Implement push-to-talk: keyboard listener for spacebar hold/release",
      "On spacebar hold: start mic capture (pyaudio, 16kHz mono) and Opus encoding",
      "Stream Opus frames as binary WebSocket messages while spacebar held",
      "On spacebar release: send speech_end JSON message",
      "Handle incoming messages: display transcripts, play Opus audio, show status",
      "Decode incoming Opus audio frames and play through speakers via pyaudio",
      "Add graceful cleanup on Ctrl+C"
    ],
    "passes": false
  },
  {
    "category": "testing",
    "description": "End-to-end voice conversation test",
    "steps": [
      "Start OpenClaw gateway with cheeko stream endpoint enabled",
      "Run voice_client.py and verify WebSocket connection succeeds",
      "Test push-to-talk: hold spacebar, say 'Hello', release spacebar",
      "Verify transcript appears in client console",
      "Verify audio response plays through speakers",
      "Test multi-turn: ask a follow-up question, verify context is maintained",
      "Test cancel: send cancel during response, verify audio stops"
    ],
    "passes": false
  },
  {
    "category": "testing",
    "description": "Latency measurement and error handling verification",
    "steps": [
      "Add timing instrumentation: measure speech_end to first_audio_chunk latency",
      "Run 5 test queries and log latency for each",
      "Test error scenarios: disconnect mid-stream, invalid audio, missing API keys",
      "Verify graceful error messages for each failure case",
      "Verify session cleanup after disconnection"
    ],
    "passes": false
  }
]
```

---

## Agent Instructions

1. Read `activity.md` first to understand current state
2. Find next task with `"passes": false`
3. Complete all steps for that task
4. Verify in browser using agent-browser
5. Update task to `"passes": true`
6. Log completion in `activity.md`
7. Repeat until all tasks pass

**Important:** Only modify the `passes` field. Do not remove or rewrite tasks.

---

## Completion Criteria
All tasks marked with `"passes": true`
