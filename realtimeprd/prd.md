# Cheeko Realtime Voice Mode - Product Requirements Document

## Overview
Add realtime multimodal model support to the Cheeko voice streaming pipeline as an alternative to the current STT+LLM+TTS pipeline. Integrate both **Gemini 2.5 Flash Live API** and **OpenAI GPT Realtime API** as configurable providers. In realtime mode, a single model handles speech recognition, reasoning, and speech synthesis in one bidirectional stream — eliminating inter-service latency and simplifying the audio pipeline. The existing STT+LLM+TTS pipeline remains intact as the default `"pipeline"` mode.

## Target Audience
- Developers testing the Cheeko voice device pipeline (lower latency, simpler architecture)
- Future ESP32 hardware devices that benefit from a single-model voice backend
- Users who want the most natural, lowest-latency voice interaction with OpenClaw

## Core Features
1. **Gemini 2.5 Flash Live API Integration** — Bidirectional WebSocket session with native audio I/O, function calling, and auto-reconnect on session timeout
2. **OpenAI GPT Realtime API Integration** — WebSocket-based realtime session with native Opus support, function calling, and 60-minute sessions
3. **Configurable Mode Selection** — Server-side config (`gateway.cheeko.mode`) switches between `pipeline`, `gemini`, or `openai-realtime`
4. **OpenClaw Agent Context** — Realtime models receive the same system prompt and tool definitions as the existing OpenClaw chat pipeline
5. **Transparent Auto-Reconnect** — Server automatically reconnects to the realtime model on session timeout (Gemini: ~10min, OpenAI: 60min) without client awareness
6. **Configurable Voices** — Per-provider voice selection via config (Gemini: voice name, OpenAI: voice name)
7. **Latency Measurement** — Same `speechEndToFirstAudio` instrumentation as pipeline mode for A/B comparison
8. **Web + Native Client Support** — Both the web voice chat page and Python push-to-talk client work with realtime mode

## Tech Stack
- **Gateway Endpoint**: TypeScript/Node.js (existing `openclaw/src/gateway/`)
- **Gemini SDK**: `@google/genai` (npm) — Live API WebSocket client
- **OpenAI SDK**: `openai` (already installed, ^6.21.0) — Realtime API WebSocket client
- **Audio Codec**: Opus (via opusscript, already installed) for Gemini PCM↔Opus conversion; OpenAI supports Opus natively
- **Transport**: Existing `/cheeko/stream` WebSocket endpoint — server-side routing to realtime model
- **Client**: No client changes needed — same binary Opus frames + JSON control protocol
- **Package Manager**: pnpm (gateway), uv (Python client)

## Architecture

```
Python/Web Client (push-to-talk)
    │
    │ WebSocket (ws://gateway:18789/cheeko/stream)
    │ ├── Binary frames: Opus audio chunks (20ms, 16kHz mono)
    │ └── JSON frames: control messages (hello, speech_end, cancel)
    │
    ▼
OpenClaw Gateway (/cheeko/stream)
    │
    ├── [mode: "pipeline"] ──► Deepgram STT → OpenClaw LLM → TTS (existing)
    │
    ├── [mode: "gemini"] ──► Gemini 2.5 Flash Live API (WebSocket)
    │   ├── Decode client Opus → PCM 16kHz → send to Gemini
    │   ├── Receive PCM 24kHz from Gemini → encode to Opus → send to client
    │   ├── Function calls: execute via OpenClaw agent tools → return results
    │   └── Auto-reconnect with session resumption on timeout
    │
    └── [mode: "openai-realtime"] ──► OpenAI GPT Realtime API (WebSocket)
        ├── Send client Opus frames directly (native Opus input support)
        ├── Receive Opus frames from OpenAI → forward to client
        ├── Function calls: execute via OpenClaw agent tools → return results
        └── 60-minute session limit (reconnect if exceeded)
```

### Protocol (unchanged for clients)

**Client → Server:**
- Binary frame: Raw Opus packet (20ms of audio)
- JSON `{"type": "hello", "deviceId": "...", "token": "..."}` — Initial handshake
- JSON `{"type": "speech_end"}` — User released spacebar
- JSON `{"type": "cancel"}` — Abort current response

**Server → Client (same as today + new events):**
- JSON `{"type": "transcript", "text": "...", "partial": bool}` — STT/model transcript
- Binary frame: Opus audio chunk (model response)
- JSON `{"type": "audio_end"}` — Response complete
- JSON `{"type": "status", "stage": "listening"|"thinking"|"speaking"}` — Pipeline stage
- JSON `{"type": "latency", "speechEndToFirstAudio": <ms>}` — Latency measurement
- JSON `{"type": "error", "message": "..."}` — Error notification

### Realtime Session Lifecycle

1. Client connects to `/cheeko/stream`, sends `hello`
2. Server checks `gateway.cheeko.mode` config
3. If `gemini` or `openai-realtime`: open a persistent realtime session to the provider
4. Configure session with OpenClaw agent system prompt + tool definitions
5. Audio flows bidirectionally through the realtime session
6. On function call from model: server executes tool, returns result to model
7. On session timeout: server transparently reconnects, re-sends system config
8. On client disconnect: close realtime session, clean up resources

## Data Model

### Extended CheekStreamConfig
```typescript
export type CheekStreamConfig = {
  // --- Existing fields (unchanged) ---
  enabled?: boolean;
  deepgramApiKey?: string;
  deepgramModel?: string;
  ttsProvider?: "openai" | "elevenlabs";
  openaiApiKey?: string;
  ttsModel?: string;
  ttsVoice?: string;
  elevenlabsApiKey?: string;
  elevenlabsVoiceId?: string;
  elevenlabsModelId?: string;

  // --- New fields for realtime mode ---
  /** Voice pipeline mode: "pipeline" (STT+LLM+TTS), "gemini", or "openai-realtime". Default: "pipeline". */
  mode?: "pipeline" | "gemini" | "openai-realtime";

  /** Gemini API key (fallback: GEMINI_API_KEY env var). */
  geminiApiKey?: string;
  /** Gemini Live model ID (default: gemini-2.5-flash-native-audio-preview-12-2025). */
  geminiModel?: string;
  /** Gemini voice name (default: Kore). */
  geminiVoice?: string;

  /** OpenAI Realtime model ID (default: gpt-realtime). */
  openaiRealtimeModel?: string;
  /** OpenAI Realtime voice (default: alloy). */
  openaiRealtimeVoice?: string;
};
```

### Realtime Session State
```typescript
type RealtimeSession = {
  provider: "gemini" | "openai-realtime";
  connection: WebSocket | null;  // Connection to the realtime model
  sessionId: string;             // Provider session ID
  connected: boolean;
  reconnecting: boolean;
  resumptionToken?: string;      // For Gemini session resumption
  toolCallHandlers: Map<string, ToolCallHandler>;
};
```

### Audio Parameters
| | Pipeline Mode | Gemini Realtime | OpenAI Realtime |
|---|---|---|---|
| Client → Server | Opus 16kHz mono | Opus 16kHz mono | Opus 16kHz mono |
| Server → Provider | Opus/PCM to Deepgram | PCM 16kHz to Gemini | Opus to OpenAI |
| Provider → Server | PCM 24kHz from TTS | PCM 24kHz from Gemini | Opus from OpenAI |
| Server → Client | Opus 24kHz | Opus 24kHz (encoded) | Opus 24kHz (passthrough) |

## Security Considerations
- API keys for Gemini/OpenAI stored in config (same pattern as existing keys)
- Realtime model sessions are per-client (no session sharing)
- Function call execution uses same authorization as existing OpenClaw agent pipeline
- Rate limiting on session creation (prevent abuse of expensive realtime APIs)

## Third-Party Integrations
- **Google Gemini** — Live API via `@google/genai` SDK (WebSocket)
- **OpenAI** — Realtime API via `openai` SDK (WebSocket)
- **Deepgram** — STT (existing, pipeline mode only)
- **OpenAI TTS / ElevenLabs** — TTS (existing, pipeline mode only)

## Constraints & Assumptions
- OpenClaw gateway must be running on port 18789 with cheeko stream enabled
- Gemini API key required for `gemini` mode (`GEMINI_API_KEY` or config)
- OpenAI API key required for `openai-realtime` mode (`OPENAI_API_KEY` or config)
- Gemini Live API sessions timeout at ~10min (WebSocket) / 15min (audio) — auto-reconnect handles this
- OpenAI Realtime sessions timeout at 60min
- Gemini does NOT support Opus natively — server must decode Opus→PCM and encode PCM→Opus
- OpenAI supports Opus natively on input and output — passthrough is possible
- Both providers support function calling — tool definitions extracted from OpenClaw agent config
- Context window: Gemini 128k tokens, OpenAI 32k tokens

## Success Criteria
- Voice conversation works end-to-end through both Gemini and OpenAI realtime modes
- Latency is measurably better than the current STT+LLM+TTS pipeline (target: <800ms speech_end to first audio)
- Auto-reconnect works transparently on Gemini session timeout
- Function calling works during realtime sessions (at least basic tools)
- Web voice chat page and Python client both work with realtime mode
- Comprehensive E2E tests cover all three modes
- Error handling: missing API keys, provider errors, timeout, disconnect mid-stream

---

## Task List

```json
[
  {
    "category": "setup",
    "description": "Add @google/genai SDK dependency and realtime mode config schema",
    "steps": [
      "Run pnpm add -w @google/genai to install the Gemini SDK",
      "Verify import: create a minimal test that imports GoogleGenAI from @google/genai",
      "Extend CheekStreamConfig type in src/config/types.gateway.ts with new fields: mode, geminiApiKey, geminiModel, geminiVoice, openaiRealtimeModel, openaiRealtimeVoice",
      "Update Zod validation schema in src/config/zod-schema.ts for all new fields",
      "Update field labels in src/config/schema.field-metadata.ts",
      "Update help text and placeholders in src/config/schema.hints.ts",
      "Run pnpm build to verify TypeScript compiles with no errors"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Implement Gemini 2.5 Flash Live API realtime session manager",
    "steps": [
      "Create src/gateway/cheeko-realtime-gemini.ts with GeminiRealtimeSession class",
      "Implement connect(): open WebSocket to Gemini Live API via @google/genai SDK with session config (model, system instructions, voice, response modality AUDIO)",
      "Implement sendAudio(opusFrame): decode Opus to PCM 16kHz using opusscript, then send PCM to Gemini via session.sendRealtimeInput()",
      "Handle incoming audio: receive PCM 24kHz from Gemini, encode to Opus using opusscript, deliver via onAudioFrame callback",
      "Handle text transcripts from Gemini (model turn text parts) and deliver via onTranscript callback",
      "Implement interrupt(): send cancel signal to Gemini session when user sends cancel or starts speaking again",
      "Implement session resumption: listen for GoAway message, store resumption token, auto-reconnect with same session config",
      "Implement close(): clean up WebSocket, opusscript encoder/decoder instances",
      "Add latency instrumentation: measure time from sendAudio stop to first audio frame received",
      "Run pnpm build to verify TypeScript compiles"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Implement OpenAI GPT Realtime API session manager",
    "steps": [
      "Create src/gateway/cheeko-realtime-openai.ts with OpenAIRealtimeSession class",
      "Implement connect(): open WebSocket to OpenAI Realtime API endpoint, send session.update with model, instructions, voice, input_audio_format=opus, output_audio_format=opus",
      "Implement sendAudio(opusFrame): base64-encode Opus frame, send via input_audio_buffer.append event",
      "Implement endAudio(): send input_audio_buffer.commit event (equivalent to speech_end)",
      "Handle response.audio.delta events: base64-decode Opus frames, deliver via onAudioFrame callback",
      "Handle response.audio_transcript.delta events: deliver text via onTranscript callback",
      "Handle response.done event: signal completion via onComplete callback",
      "Implement interrupt(): send response.cancel event to stop current generation",
      "Implement session timeout handling: detect connection close, auto-reconnect with session config",
      "Add latency instrumentation: measure time from endAudio to first response.audio.delta",
      "Run pnpm build to verify TypeScript compiles"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Implement function calling bridge for realtime sessions",
    "steps": [
      "Create src/gateway/cheeko-realtime-tools.ts with tool bridge logic",
      "Extract tool definitions from OpenClaw agent config (same tools available in chat pipeline)",
      "Convert OpenClaw tool definitions to Gemini function declaration format",
      "Convert OpenClaw tool definitions to OpenAI Realtime tool format (JSON schema)",
      "Implement handleToolCall(name, args): execute the tool using existing OpenClaw agent infrastructure",
      "For Gemini: listen for BidiGenerateContentToolCall events, execute tool, respond with BidiGenerateContentToolResponse",
      "For OpenAI: listen for response.function_call_arguments.done events, execute tool, respond with conversation.item.create + response.create",
      "Add error handling: tool execution failures return error message to model",
      "Run pnpm build to verify TypeScript compiles"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Integrate realtime sessions into cheeko-stream.ts router",
    "steps": [
      "Add mode routing logic to cheeko-stream.ts: after hello handshake, check gateway.cheeko.mode config",
      "If mode is 'pipeline': use existing STT+LLM+TTS flow (no changes to current code path)",
      "If mode is 'gemini': create GeminiRealtimeSession, wire audio frames bidirectionally",
      "If mode is 'openai-realtime': create OpenAIRealtimeSession, wire audio frames bidirectionally",
      "For realtime modes: on binary frame from client, call session.sendAudio(frame)",
      "For realtime modes: on speech_end from client, call session.endAudio() (for OpenAI) or let Gemini VAD handle it",
      "For realtime modes: on cancel from client, call session.interrupt()",
      "Wire session callbacks: onAudioFrame sends binary to client, onTranscript sends JSON transcript, onComplete sends audio_end",
      "Wire session callbacks: onToolCall dispatches to cheeko-realtime-tools bridge",
      "Send latency measurement to client on first audio frame (same format as pipeline mode)",
      "Add proper cleanup: on client disconnect, close realtime session",
      "Send status updates to client: 'listening', 'thinking', 'speaking' at appropriate transitions",
      "Run pnpm build to verify TypeScript compiles"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Implement transparent auto-reconnect for Gemini session timeout",
    "steps": [
      "In GeminiRealtimeSession: handle GoAway message from Gemini (includes timeLeft field)",
      "Store the session resumption token from GoAway or session events",
      "When connection closes unexpectedly or GoAway received: set reconnecting=true, suppress client-facing errors",
      "Re-open WebSocket to Gemini with same session config + resumption token",
      "On successful reconnect: set reconnecting=false, resume audio forwarding",
      "If reconnect fails after 3 attempts: send error to client and close session",
      "For OpenAI: implement similar reconnect on unexpected close (simpler, no resumption token needed)",
      "Test: verify client doesn't see interruption during Gemini reconnect",
      "Run pnpm build to verify TypeScript compiles"
    ],
    "passes": false
  },
  {
    "category": "feature",
    "description": "Update web voice chat page to work with realtime mode",
    "steps": [
      "Read the existing web voice chat page source (find the HTML file in openclaw/src/gateway/)",
      "Verify the web client already sends Opus or PCM binary frames — the protocol is unchanged",
      "If the web client uses PCM format: ensure the server handles PCM-to-Opus conversion for realtime providers that need it, or PCM passthrough",
      "Test that the existing web voice chat page works with both gemini and openai-realtime modes without client-side changes",
      "If any client-side adjustments are needed (e.g., handling new message types), make minimal changes",
      "Run pnpm build to verify"
    ],
    "passes": false
  },
  {
    "category": "testing",
    "description": "E2E tests for Gemini realtime mode",
    "steps": [
      "Create test_realtime_gemini.py (or add to existing test suite)",
      "Test 1: WebSocket connection with mode=gemini, verify hello_ack",
      "Test 2: Send synthetic audio frames, verify audio response received",
      "Test 3: Send speech_end, verify model processes and responds",
      "Test 4: Send cancel during response, verify interruption works",
      "Test 5: Verify transcript messages received alongside audio",
      "Test 6: Verify latency measurement message received",
      "Test 7: Disconnect and reconnect, verify new session created",
      "Test 8: Function calling — if possible, trigger a tool call and verify result",
      "Run all tests and verify they pass"
    ],
    "passes": false
  },
  {
    "category": "testing",
    "description": "E2E tests for OpenAI realtime mode",
    "steps": [
      "Create test_realtime_openai.py (or add to existing test suite)",
      "Test 1: WebSocket connection with mode=openai-realtime, verify hello_ack",
      "Test 2: Send synthetic Opus audio frames, verify audio response received",
      "Test 3: Send speech_end (triggers input_audio_buffer.commit), verify response",
      "Test 4: Send cancel during response, verify interruption works",
      "Test 5: Verify transcript messages received alongside audio",
      "Test 6: Verify latency measurement message received",
      "Test 7: Disconnect and reconnect, verify new session created",
      "Test 8: Function calling — trigger a tool call and verify result",
      "Run all tests and verify they pass"
    ],
    "passes": false
  },
  {
    "category": "testing",
    "description": "Latency comparison benchmark across all three modes",
    "steps": [
      "Create test_latency_comparison.py benchmarking script",
      "Run 5 voice queries through pipeline mode, measure speech_end to first audio",
      "Run 5 voice queries through gemini mode, measure speech_end to first audio",
      "Run 5 voice queries through openai-realtime mode, measure speech_end to first audio",
      "Print comparison table: mode, avg latency, min, max, p50, p95",
      "Verify realtime modes achieve <800ms target (or document actual results)",
      "Run the existing test_e2e_cheeko.py to ensure pipeline mode is not broken (regression test)",
      "Run the existing test_latency_errors.py to ensure error handling is not broken"
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
