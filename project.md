[Max] **Option C: Full Streaming Implementation Plan** 🔧

Architecture Overview

```
Python Client (ESP32 Simulator)
    ↓ WebSocket (wss://gateway:18789/cheeko/stream)
    ↓ Audio chunks (PCM 16kHz, 20ms frames)
OpenClaw Gateway WebSocket Endpoint
    ├─→ Deepgram/Groq STT (streaming)
    ├─→ Claude/GPT (streaming)
    └─→ ElevenLabs/Deepgram TTS (streaming)
    ↓ Audio chunks back
Python Client (playback simulation)
```
**Key Feature:** Pipeline parallelism — TTS starts as soon as first LLM tokens arrive


Phase 1: Protocol Design (Day 1)

Message Schema

**Client → Server (Audio Input)**

```
{
  "type": "audio_chunk",
  "deviceId": "cheeko-sim-001",
  "sessionId": "uuid-v4",
  "seq": 42,
  "timestamp": 1739371234567,
  "audio": {
    "data": "<base64-encoded-pcm>",
    "format": "pcm16",
    "sampleRate": 16000,
    "channels": 1,
    "durationMs": 20
  }
}
```
**Client → Server (Control)**

```
{
  "type": "speech_end",
  "deviceId": "cheeko-sim-001",
  "sessionId": "uuid-v4",
  "timestamp": 1739371234567
}

{
  "type": "cancel",
  "deviceId": "cheeko-sim-001",
  "sessionId": "uuid-v4"
}
```
**Server → Client (Transcript)**

```
{
  "type": "transcript",
  "partial": false,
  "text": "What's the weather like today?",
  "confidence": 0.95,
  "timestamp": 1739371234567
}
```
**Server → Client (Audio Response)**

```
{
  "type": "audio_chunk",
  "seq": 1,
  "audio": {
    "data": "<base64-encoded-pcm>",
    "format": "pcm16",
    "sampleRate": 24000,
    "channels": 1
  },
  "metadata": {
    "totalChunks": 0,  // 0 = streaming, unknown total
    "final": false
  }
}

{
  "type": "audio_end",
  "totalChunks": 45,
  "durationMs": 2340
}
```
**Server → Client (Status)**

```
{
  "type": "status",
  "stage": "stt" | "thinking" | "speaking",
  "message": "Processing your question..."
}
```

Phase 2: Gateway WebSocket Endpoint (Day 2-3)

File: `/gateway/endpoints/cheeko_stream.ts`

```
import WebSocket from 'ws';
import { Deepgram } from '@deepgram/sdk';
import Anthropic from '@anthropic-ai/sdk';
import ElevenLabs from 'elevenlabs-node';

interface CheekoDevice {
  deviceId: string;
  sessionId: string;
  ws: WebSocket;
  sttStream?: any;
  ttsStream?: any;
  claudeStream?: any;
}

const devices = new Map<string, CheekoDevice>();

export function setupCheekoStreamEndpoint(wss: WebSocket.Server) {
  wss.on('connection', async (ws, req) => {
    const deviceId = authenticateDevice(req); // JWT or API key
    if (!deviceId) {
      ws.close(4001, 'Unauthorized');
      return;
    }

    console.log(`[Cheeko] Device connected: ${deviceId}`);

    const device: CheekoDevice = {
      deviceId,
      sessionId: generateSessionId(),
      ws,
    };
    devices.set(deviceId, device);

    // Initialize Deepgram streaming STT
    const deepgram = new Deepgram(process.env.DEEPGRAM_API_KEY);
    const sttStream = deepgram.transcription.live({
      model: 'nova-2',
      language: 'en',
      punctuate: true,
      interim_results: false,
      endpointing: 300, // VAD silence threshold
    });

    device.sttStream = sttStream;

    // STT event handlers
    sttStream.on('transcriptReceived', async (transcript) => {
      const text = transcript.channel.alternatives[0].transcript;
      if (!text || text.trim() === '') return;

      console.log(`[STT] ${deviceId}: ${text}`);

      // Send transcript to client
      ws.send(JSON.stringify({
        type: 'transcript',
        partial: false,
        text,
        confidence: transcript.channel.alternatives[0].confidence,
        timestamp: Date.now(),
      }));

      // Send status
      ws.send(JSON.stringify({
        type: 'status',
        stage: 'thinking',
        message: 'Processing...',
      }));

      // Start Claude streaming
      await processWithClaude(device, text);
    });

    // WebSocket message handler
    ws.on('message', async (data) => {
      const msg = JSON.parse(data.toString());

      switch (msg.type) {
        case 'audio_chunk':
          // Decode base64 and pipe to STT
          const audioBuffer = Buffer.from(msg.audio.data, 'base64');
          sttStream.send(audioBuffer);
          break;

        case 'speech_end':
          // Flush STT stream
          sttStream.finish();
          break;

        case 'cancel':
          // Stop all streams
          device.claudeStream?.abort();
          device.ttsStream?.destroy();
          break;
      }
    });

    ws.on('close', () => {
      console.log(`[Cheeko] Device disconnected: ${deviceId}`);
      sttStream.finish();
      devices.delete(deviceId);
    });
  });
}

async function processWithClaude(device: CheekoDevice, userMessage: string) {
  const anthropic = new Anthropic({
    apiKey: process.env.ANTHROPIC_API_KEY,
  });

  // Get session history from OpenClaw
  const history = await getSessionHistory(device.sessionId);

  device.ws.send(JSON.stringify({
    type: 'status',
    stage: 'speaking',
    message: 'Responding...',
  }));

  // Stream Claude response
  const stream = await anthropic.messages.stream({
    model: 'claude-sonnet-4',
    max_tokens: 1024,
    messages: [
      ...history,
      { role: 'user', content: userMessage },
    ],
    system: 'You are Cheeko, a friendly AI companion for kids. Keep responses concise (1-2 sentences for simple questions).',
  });

  device.claudeStream = stream;

  let fullResponse = '';
  let sentenceBuffer = '';

  // Process Claude chunks and stream to TTS
  for await (const chunk of stream) {
    if (chunk.type === 'content_block_delta' && chunk.delta.type === 'text_delta') {
      const text = chunk.delta.text;
      fullResponse += text;
      sentenceBuffer += text;

      // Stream to TTS when we have a complete sentence
      if (isSentenceComplete(sentenceBuffer)) {
        await streamToTTS(device, sentenceBuffer);
        sentenceBuffer = '';
      }
    }
  }

  // Flush remaining text
```

```
import WebSocket from 'ws';
import { Deepgram } from '@deepgram/sdk';
import Anthropic from '@anthropic-ai/sdk';
import ElevenLabs from 'elevenlabs-node';

interface CheekoDevice {
  deviceId: string;
  sessionId: string;
  ws: WebSocket;
  sttStream?: any;
  ttsStream?: any;
  claudeStream?: any;
}

const devices = new Map<string, CheekoDevice>();

export function setupCheekoStreamEndpoint(wss: WebSocket.Server) {
  wss.on('connection', async (ws, req) => {
    const deviceId = authenticateDevice(req); // JWT or API key
    if (!deviceId) {
      ws.close(4001, 'Unauthorized');
      return;
    }

    console.log(`[Cheeko] Device connected: ${deviceId}`);

    const device: CheekoDevice = {
      deviceId,
      sessionId: generateSessionId(),
      ws,
    };
    devices.set(deviceId, device);

    // Initialize Deepgram streaming STT
    const deepgram = new Deepgram(process.env.DEEPGRAM_API_KEY);
    const sttStream = deepgram.transcription.live({
      model: 'nova-2',
      language: 'en',
      punctuate: true,
      interim_results: false,
      endpointing: 300, // VAD silence threshold
    });

    device.sttStream = sttStream;

    // STT event handlers
    sttStream.on('transcriptReceived', async (transcript) => {
      const text = transcript.channel.alternatives[0].transcript;
      if (!text || text.trim() === '') return;

      console.log(`[STT] ${deviceId}: ${text}`);

      // Send transcript to client
      ws.send(JSON.stringify({
        type: 'transcript',
        partial: false,
        text,
        confidence: transcript.channel.alternatives[0].confidence,
        timestamp: Date.now(),
      }));

      // Send status
      ws.send(JSON.stringify({
        type: 'status',
        stage: 'thinking',
        message: 'Processing...',
      }));

      // Start Claude streaming
      await processWithClaude(device, text);
    });

    // WebSocket message handler
    ws.on('message', async (data) => {
      const msg = JSON.parse(data.toString());

      switch (msg.type) {
        case 'audio_chunk':
          // Decode base64 and pipe to STT
          const audioBuffer = Buffer.from(msg.audio.data, 'base64');
          sttStream.send(audioBuffer);
          break;

        case 'speech_end':
          // Flush STT stream
          sttStream.finish();
          break;

        case 'cancel':
          // Stop all streams
          device.claudeStream?.abort();
          device.ttsStream?.destroy();
          break;
      }
    });

    ws.on('close', () => {
      console.log(`[Cheeko] Device disconnected: ${deviceId}`);
      sttStream.finish();
      devices.delete(deviceId);
    });
  });
}

async function processWithClaude(device: CheekoDevice, userMessage: string) {
  const anthropic = new Anthropic({
    apiKey: process.env.ANTHROPIC_API_KEY,
  });

  // Get session history from OpenClaw
  const history = await getSessionHistory(device.sessionId);

  device.ws.send(JSON.stringify({
    type: 'status',
    stage: 'speaking',
    message: 'Responding...',
  }));

  // Stream Claude response
  const stream = await anthropic.messages.stream({
    model: 'claude-sonnet-4',
    max_tokens: 1024,
    messages: [
      ...history,
      { role: 'user', content: userMessage },
    ],
    system: 'You are Cheeko, a friendly AI companion for kids. Keep responses concise (1-2 sentences for simple questions).',
  });

  device.claudeStream = stream;

  let fullResponse = '';
  let sentenceBuffer = '';

  // Process Claude chunks and stream to TTS
  for await (const chunk of stream) {
    if (chunk.type === 'content_block_delta' && chunk.delta.type === 'text_delta') {
      const text = chunk.delta.text;
      fullResponse += text;
      sentenceBuffer += text;

      // Stream to TTS when we have a complete sentence
      if (isSentenceComplete(sentenceBuffer)) {
        await streamToTTS(device, sentenceBuffer);
        sentenceBuffer = '';
      }
    }
  }

  // Flush remaining text
```

```
 if (sentenceBuffer.trim()) {
    await streamToTTS(device, sentenceBuffer);
  }

  // Save to session history
  await saveToSession(device.sessionId, userMessage, fullResponse);

  // Signal end
  device.ws.send(JSON.stringify({
    type: 'audio_end',
    timestamp: Date.now(),
  }));
}

async function streamToTTS(device: CheekoDevice, text: string) {
  const elevenlabs = new ElevenLabs({
    apiKey: process.env.ELEVENLABS_API_KEY,
  });

  // ElevenLabs streaming
  const audioStream = await elevenlabs.textToSpeech({
    voice_id: 'pNInz6obpgDQGcFmaJgB', // Kid-friendly voice
    text,
    model_id: 'eleven_turbo_v2', // Fastest model
    output_format: 'pcm_24000',
  });

  let seq = 0;
  for await (const chunk of audioStream) {
    device.ws.send(JSON.stringify({
      type: 'audio_chunk',
      seq: seq++,
      audio: {
        data: chunk.toString('base64'),
        format: 'pcm16',
        sampleRate: 24000,
        channels: 1,
      },
      metadata: { final: false },
    }));
  }
}

function isSentenceComplete(text: string): boolean {
  // Check for sentence endings
  return /[.!?]\s*$/.test(text.trim());
}

function authenticateDevice(req: any): string | null {
  const token = req.headers['x-device-token'];
  // Validate JWT or API key
  return 'cheeko-sim-001'; // Placeholder
}

function generateSessionId(): string {
  return `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

async function getSessionHistory(sessionId: string): Promise<any[]> {
  // Query OpenClaw session storage
  return [];
}

async function saveToSession(sessionId: string, user: string, assistant: string) {
  // Save to OpenClaw session storage
}
```

Phase 3: Python Client Simulator (Day 1)

File: `cheeko_client_simulator.py`

```
#!/usr/bin/env python3
"""
Cheeko ESP32 Simulator - Python WebSocket Client
Tests full streaming pipeline with microphone input
"""

import asyncio
import websockets
import json
import base64
import pyaudio
import wave
import time
from pathlib import Path

# Audio config (matches ESP32 I2S)
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_MS = 20  # 20ms chunks
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_MS / 1000)  # 320 samples

# Gateway config
GATEWAY_URL = "ws://localhost:18789/cheeko/stream"
DEVICE_TOKEN = "test-device-001"

class CheekoSimulator:
    def __init__(self):
        self.ws = None
        self.audio = pyaudio.PyAudio()
        self.input_stream = None
        self.output_stream = None
        self.device_id = "cheeko-sim-001"
        self.session_id = None
        self.seq = 0
        self.is_speaking = False
        
    async def connect(self):
        """Connect to OpenClaw Gateway"""
        headers = {"X-Device-Token": DEVICE_TOKEN}
        self.ws = await websockets.connect(GATEWAY_URL, extra_headers=headers)
        print(f"✅ Connected to {GATEWAY_URL}")
        
    async def start_input_stream(self):
        """Start microphone capture"""
        self.input_stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        print("🎤 Microphone active - speak now!")
        
    async def start_output_stream(self):
        """Start speaker playback"""
        self.output_stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=24000,  # TTS output rate
            output=True,
        )
        
    async def send_audio_loop(self):
        """Capture and send audio chunks"""
        while True:
            try:
                # Read audio from mic
                audio_data = self.input_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                
                # Encode to base64
                audio_b64 = base64.b64encode(audio_data).decode('utf-8')
                
                # Send to gateway
                msg = {
                    "type": "audio_chunk",
                    "deviceId": self.device_id,
                    "sessionId": self.session_id,
                    "seq": self.seq,
                    "timestamp": int(time.time() * 1000),
                    "audio": {
                        "data": audio_b64,
                        "format": "pcm16",
                        "sampleRate": SAMPLE_RATE,
                        "channels": CHANNELS,
                        "durationMs": CHUNK_MS,
                    }
                }
                
                await self.ws.send(json.dumps(msg))
                self.seq += 1
                
                await asyncio.sleep(CHUNK_MS / 1000.0)  # 20ms delay
                
            except Exception as e:
                print(f"❌ Send error: {e}")
                break
                
    async def receive_loop(self):
        """Receive and process responses"""
        async for message in self.ws:
            msg = json.loads(message)
            msg_type = msg.get('type')
            
            if msg_type == 'transcript':
                print(f"📝 Transcript: {msg['text']}")
                
            elif msg_type == 'status':
                stage = msg.get('stage')
                if stage == 'thinking':
                    print("🤔 Thinking...")
                elif stage == 'speaking':
                    print("🔊 Speaking...")
                    self.is_speaking = True
                    
            elif msg_type == 'audio_chunk':
                # Decode and play audio
                audio_data = base64.b64decode(msg['audio']['data'])
                self.output_stream.write(audio_data)
                
            elif msg_type == 'audio_end':
                print("✅ Response complete")
```

```
               self.is_speaking = False
                
    async def run(self):
        """Main loop"""
        await self.connect()
        await self.start_input_stream()
        await self.start_output_stream()
        
        # Run send and receive concurrently
        await asyncio.gather(
            self.send_audio_loop(),
            self.receive_loop(),
        )
        
    def cleanup(self):
        """Clean shutdown"""
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
        self.audio.terminate()
        print("🛑 Stopped")

if __name__ == "__main__":
    sim = CheekoSimulator()
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        print("\n⏸️  Interrupted")
    finally:
        sim.cleanup()
```

Phase 4: Testing & Metrics (Day 4-5)

Test Cases

**TC-1: Single Question**

• User: "What's 2 + 2?"
• Expected: Response within 800ms
• Measure: Time from speech_end to first audio_chunk
**TC-2: Long Response**

• User: "Tell me a story about a robot"
• Expected: First sentence starts <1s, streaming continues
• Measure: Time to first audio vs total response time
**TC-3: Interruption**

• User speaks while Cheeko is responding
• Expected: Previous response cancelled, new question processed
• Measure: Cancel latency <100ms
**TC-4: Network Jitter**

• Simulate 100ms variable latency
• Expected: Audio playback remains smooth (buffering)
• Measure: Dropout count
**TC-5: Reconnection**

• Disconnect mid-response
• Expected: Automatic reconnect, session resumes
• Measure: Recovery time <2s
Success Metrics

**Cold start latency**
• Target: <1000ms
• Measurement: speech_end → first audio byte

**Streaming latency**
• Target: <200ms
• Measurement: LLM token → TTS audio

**End-to-end (simple)**
• Target: <800ms
• Measurement: "What's 2+2?"

**End-to-end (complex)**
• Target: <1500ms
• Measurement: Multi-sentence response

**Audio quality**
• Target: MOS >4.0
• Measurement: Subjective listening test

**Reliability**
• Target: 99.5%
• Measurement: 1000 test conversations

Latency Breakdown (Target)

```
Speech End Detection: 50-100ms (VAD)
    ↓
STT Processing: 100-200ms (Deepgram/Groq)
    ↓
LLM First Token: 200-400ms (Claude Sonnet)
    ↓
TTS First Chunk: 50-100ms (ElevenLabs Turbo)
    ↓
Network + Playback: 50-100ms
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL: 450-900ms ✅
```

Phase 5: Optimization (Day 6-7)

If Latency > 1s

**A. Switch to Faster Models**

• STT: Groq Whisper (50-100ms vs Deepgram 200ms)
• LLM: GPT-4o-mini (100-300ms vs Claude 200-400ms)
• TTS: Play.ht Turbo (30ms vs ElevenLabs 50ms)
**B. Aggressive Sentence Chunking**

```
// Stream to TTS after 5-7 words, not full sentences
if (wordCount(sentenceBuffer) >= 5) {
  await streamToTTS(device, sentenceBuffer);
  sentenceBuffer = '';
}
```
**C. Pre-cache Common Responses**

```
const COMMON_RESPONSES = {
  "hello": "audio/hello.pcm",
  "how are you": "audio/how_are_you.pcm",
};
```
**D. Parallel STT + TTS Warmup**

```
// Start TTS connection while STT is processing
Promise.all([
  sttStream.process(audio),
  ttsClient.connect(),
]);
```

File Structure

```
/gateway/
  endpoints/
    cheeko_stream.ts          # WebSocket endpoint
  services/
    stt_service.ts            # Deepgram/Groq wrapper
    tts_service.ts            # ElevenLabs/PlayHT wrapper
    session_service.ts        # Session management
  middleware/
    device_auth.ts            # JWT validation
  
/clients/
  cheeko_simulator.py         # Python test client
  requirements.txt            # pyaudio, websockets
  test_suite.py               # Automated tests
  
/firmware/ (later)
  main/
    websocket_client.c        # ESP-IDF WebSocket
    audio_pipeline.c          # I2S capture/playback
```

Dependencies

**Gateway (Node.js/TypeScript)**
```
{
  "dependencies": {
    "ws": "^8.16.0",
    "@deepgram/sdk": "^3.0.0",
    "@anthropic-ai/sdk": "^0.17.0",
    "elevenlabs-node": "^1.1.0",
    "jsonwebtoken": "^9.0.2"
  }
}
```
**Python Client**

```
pip install websockets pyaudio numpy
```

Timeline

**1**
• Task: Protocol design + Python client
• Owner: Max + Rahul
• Deliverable: Client code + spec doc

**2**
• Task: Gateway WebSocket endpoint
• Owner: Max
• Deliverable: `/cheeko/stream` working

**3**
• Task: STT/LLM/TTS integration
• Owner: Max
• Deliverable: Full pipeline functional

**4**
• Task: Testing + latency measurement
• Owner: Rahul
• Deliverable: Test results, metrics

**5**
• Task: Optimization (if needed)
• Owner: Max + Rahul
• Deliverable: <1s latency achieved

**6**
• Task: Edge case handling
• Owner: Both
• Deliverable: Reconnect, errors, etc

**7**
• Task: Documentation + handoff
• Owner: Both
• Deliverable: Ready for firmware port


Next Steps

**Rahul:**

1. Install dependencies: `pip install websockets pyaudio`
2. Test microphone: `python -c "import pyaudio; p=pyaudio.PyAudio(); print(p.get_default_input_device_info())"`
3. I'll push the Python simulator code tonight
**Max:**

1. Set up Gateway WebSocket endpoint (tonight)
2. Integrate Deepgram/ElevenLabs
3. Deploy to test server
**Target:** First voice test by **tomorrow evening (Feb 13)**
