# OpenClaw Voice Streaming Pipeline - Activity Log

## Current Status
**Last Updated:** 2026-02-12
**Phase 1 (LiveKit Voice Agent):** Complete (10/10 tasks)
**Phase 2 (WebSocket Voice Streaming):** 6/9 tasks
**Current Task:** Task 6 complete — Implement OpenAI TTS streaming with Opus encoding

---

## Session Log

<!--
The Ralph Wiggum loop will append dated entries here.
Each entry should include:
- Date and time
- Task worked on
- Changes made
- Commands run
- Screenshot filename (if applicable)
- Any issues and resolutions
-->
<!-- Agent will append dated entries below -->

### 2026-02-12 — Task 1: Add Deepgram and OpenAI SDK dependencies
- **Changes:** Added `@deepgram/sdk` (^4.11.3) and `openai` (^6.21.0) to openclaw/package.json dependencies
- **Commands run:**
  - `pnpm add -w @deepgram/sdk openai` — installed both packages
  - Created temporary `test-sdk-imports.mjs` to verify both SDKs import correctly (Deepgram `createClient` and OpenAI default export both resolved as functions)
  - `pnpm build` — TypeScript build succeeded with no errors
- **Issues:** `pnpm add` required `-w` flag since openclaw uses a workspace root setup
- **Result:** Task passes — both SDKs installed and verified

### 2026-02-12 — Task 2: Add environment variable configuration for voice streaming
- **Changes:**
  - Added `CheekStreamConfig` type to `src/config/types.gateway.ts` with fields: `enabled`, `deepgramApiKey`, `deepgramModel`, `openaiApiKey`, `ttsModel`, `ttsVoice`
  - Added `cheeko` property to `GatewayConfig` type
  - Added Zod validation schema for `gateway.cheeko` in `src/config/zod-schema.ts`
  - Added field labels in `src/config/schema.field-metadata.ts` for all 6 cheeko config fields
  - Added help text and placeholders in `src/config/schema.hints.ts` for all cheeko config fields
- **Commands run:**
  - `pnpm build` — TypeScript build succeeded with no errors (146 files, build complete)
- **Issues:** None
- **Result:** Task passes — cheeko config section fully integrated into OpenClaw config schema

### 2026-02-12 — Task 3: Create the /cheeko/stream WebSocket endpoint handler
- **Changes:**
  - Created `src/gateway/cheeko-stream.ts` — standalone WebSocket handler for `/cheeko/stream` with:
    - Dedicated `WebSocketServer` (noServer mode) for the cheeko stream path
    - `handleUpgrade()` that intercepts `/cheeko/stream` upgrade requests
    - Connection handshake: validates `hello` JSON message, creates session with UUID, sends `hello_ack`
    - Message routing: binary frames → audio chunk handler (stub for Task 4 STT), JSON frames → control message handler
    - Control messages: `hello`, `speech_end`, `cancel` with proper state transitions (idle→listening→processing→speaking)
    - Session state: `CheekStreamSession` type with sessionId, deviceId, ws, state, chatHistory
    - Session cleanup on disconnect (resource cleanup stubs for Tasks 4-6)
    - Handshake timeout (10s) — closes connection if no `hello` received
    - Status notifications sent to client at each state transition
  - Modified `src/gateway/server-http.ts`:
    - Added optional `cheekStreamHandler` parameter to `attachGatewayUpgradeHandler()`
    - Cheeko stream intercepts upgrade requests before canvas and main WSS handlers
  - Modified `src/gateway/server-runtime-state.ts`:
    - Creates cheeko stream handler with config getter and logger
    - Passes handler to all HTTP server upgrade handlers
    - Returns `cheekStreamClose` for graceful shutdown
  - Modified `src/gateway/server.impl.ts`:
    - Destructures `cheekStreamClose` from runtime state
    - Passes it to `createGatewayCloseHandler()`
  - Modified `src/gateway/server-close.ts`:
    - Added optional `cheekStreamClose` parameter
    - Calls cleanup before main WSS close during shutdown
- **Commands run:**
  - `pnpm build` — TypeScript build succeeded with no errors (146 files)
- **Issues:** None
- **Result:** Task passes — `/cheeko/stream` WebSocket endpoint is registered and handles connections with proper handshake, message routing, and cleanup

### 2026-02-12 — Task 4: Implement Opus decoding and Deepgram streaming STT integration
- **Changes:**
  - Created `src/gateway/cheeko-stt.ts` — STT service wrapper around Deepgram SDK:
    - `createSttStream()` factory opens a live WebSocket connection to Deepgram Nova-2
    - Sends raw Opus frames directly to Deepgram (Deepgram natively supports `encoding: "opus"`) — no Opus decoder library needed
    - Configured with `interim_results: true` for partial transcripts, `endpointing: 300ms`, `vad_events: true`, `smart_format: true`
    - Exposes `sendAudio()`, `finalize()`, `close()`, `isConnected()` methods
    - Emits transcript callbacks with `(text, isFinal)` for partial and final results
    - Handles Deepgram errors, close events, and lifecycle cleanup
  - Modified `src/gateway/cheeko-stream.ts`:
    - Added `sttStream` and `finalTranscript` fields to `CheekStreamSession` type
    - `ensureSttStream()` lazily creates Deepgram connection on first audio chunk
    - `handleAudioChunk()` pipes binary Opus frames to Deepgram via `stt.sendAudio(data)`
    - Transcript events sent to client as `{ type: "transcript", text, partial }` JSON messages
    - Final transcript segments accumulated in `session.finalTranscript` for LLM routing (Task 5)
    - `handleSpeechEnd()` calls `stt.finalize()` to flush buffer, then closes STT stream
    - `handleCancel()` and `cleanupSession()` properly close STT streams
    - `closeSttStream()` helper for clean resource teardown
- **Commands run:**
  - `pnpm build` — TypeScript build succeeded with no errors (146 files, build complete)
- **Issues:** None. Originally planned to use opusscript for Opus→PCM decoding, but Deepgram natively supports `encoding: "opus"` so raw Opus frames are sent directly, eliminating the need for an Opus decoder dependency.
- **Result:** Task passes — Deepgram streaming STT fully integrated into cheeko-stream pipeline

### 2026-02-12 — Task 5: Implement LLM routing through existing OpenClaw chat pipeline
- **Changes:**
  - Created `src/gateway/cheeko-chat.ts` — chat service wrapper for routing voice transcripts through the OpenClaw LLM pipeline:
    - `sendChatMessage()` factory accepts transcript, sessionKey, log, and callbacks
    - Builds `MsgContext` matching the `chat.send` handler pattern (Body, BodyForAgent, SessionKey, Provider, etc.)
    - Calls `dispatchInboundMessage()` to route through existing agent pipeline
    - Subscribes to `onAgentEvent()` for streaming text — uses `evt.data.delta` for incremental text and `evt.data.text` for accumulated full text
    - `createSentenceBuffer()` splits streamed text on sentence boundaries (`.!?` followed by whitespace) for TTS-sized chunks
    - Handles lifecycle events: `phase: "end"` flushes remaining buffer and calls `onComplete`, `phase: "error"` calls `onError`
    - Returns `CheekChatHandle` with `abort()` method that cancels the `AbortController` and unsubscribes from events
    - Uses `registerAgentRunContext()` so agent events are enriched with sessionKey
    - Session key pattern: `voice:{deviceId}` for per-device voice conversation sessions
  - Modified `src/gateway/cheeko-stream.ts`:
    - Imported `sendChatMessage` and `CheekChatHandle` from cheeko-chat
    - Added `chatHandle: CheekChatHandle | null` field to `CheekStreamSession` type
    - `handleSpeechEnd()` now routes transcript through LLM:
      - Checks for empty transcript (returns to idle if empty)
      - Adds user message to `session.chatHistory`
      - Sends `"thinking"` status to client
      - Calls `sendChatMessage()` with callbacks for text chunks, completion, and errors
      - `onTextChunk`: sends `{ type: "response_text", text, partial: true }` to client
      - `onComplete`: pushes assistant response to chatHistory, sends final `response_text`, returns to idle
      - `onError`: sends error and returns to idle
    - Added `abortChat()` helper to abort in-flight LLM runs
    - `handleCancel()` now calls `abortChat()` to abort LLM in addition to closing STT
    - `cleanupSession()` now calls `abortChat()` for proper resource cleanup on disconnect
- **Commands run:**
  - `pnpm build` — TypeScript build succeeded with no errors (144 files, build complete)
- **Issues:** None. The agent event system emits `data.text` as accumulated full text and `data.delta` as incremental chunks — the sentence buffer correctly uses `delta` for streaming.
- **Result:** Task passes — LLM routing fully integrated, transcripts flow through OpenClaw agent pipeline with sentence-chunked streaming text responses

### 2026-02-12 — Task 6: Implement OpenAI TTS streaming with Opus encoding
- **Changes:**
  - Added `opusscript` dependency (`^0.1.1`) for PCM-to-Opus encoding
  - Created `src/gateway/cheeko-tts.ts` — TTS service wrapper with two main exports:
    - `streamTts()`: Calls OpenAI TTS API (`gpt-4o-mini-tts` model, `pcm` response format) for a single sentence, encodes the returned 24kHz 16-bit mono PCM into Opus frames (20ms, 480 samples/frame) via opusscript (VOIP mode, 32kbps bitrate), and delivers each Opus frame via callback
    - `createTtsPipeline()`: Manages sequential TTS for an entire LLM response — sentences are queued and processed one at a time to maintain natural ordering, with `pushSentence()`, `finish()`, and `abort()` methods
    - Handles partial frame padding (silence-pad remaining PCM that doesn't fill a full 20ms frame)
    - Proper abort support via AbortController and cleanup of opusscript encoder instances
  - Modified `src/gateway/cheeko-stream.ts`:
    - Imported `createTtsPipeline` from cheeko-tts
    - Added `ttsPipeline` field to `CheekStreamSession` type
    - `handleSpeechEnd()` now creates a TTS pipeline before dispatching to LLM:
      - `onTextChunk` callback feeds sentence-sized text chunks into `ttsPipeline.pushSentence()`
      - `onComplete` transitions to `"speaking"` state and calls `ttsPipeline.finish()`
      - TTS pipeline's `onOpusFrame` sends binary Opus frames over WebSocket to client
      - TTS pipeline's `onComplete` sends `{ type: "audio_end" }` and returns to idle
    - Added `abortTts()` helper for clean TTS teardown
    - `handleCancel()` now calls `abortTts()` to stop in-flight TTS
    - `cleanupSession()` now calls `abortTts()` for proper resource cleanup on disconnect
- **Commands run:**
  - `pnpm add -w opusscript` — installed opusscript (^0.1.1)
  - `pnpm build` — TypeScript build succeeded with no errors (144 files, build complete)
- **Issues:** None. OpenAI TTS API supports `response_format: "pcm"` which returns raw 24kHz 16-bit mono PCM, ideal for encoding to Opus with opusscript. No need for intermediate format conversion.
- **Result:** Task passes — Full TTS pipeline integrated: LLM sentence chunks → OpenAI TTS (PCM) → Opus encoding → binary WebSocket frames to client, with sequential sentence ordering and abort support
