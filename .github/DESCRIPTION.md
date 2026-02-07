# Repository Description

## Short (GitHub Settings)

```
Voice interface for OpenClaw AI agents using LiveKit WebRTC. Talk to your personal AI assistant through the browser.
```

## Full Description

OpenClaw Voice Agent bridges LiveKit's real-time WebRTC infrastructure with OpenClaw's AI gateway, enabling natural voice conversations with your AI assistant through any browser.

### Features

- **Voice Input** - Speak naturally, transcribed in real-time via Deepgram
- **AI Processing** - Powered by OpenClaw's agent orchestration and memory
- **Voice Output** - Responses synthesized via Microsoft Edge TTS (free)
- **Low Latency** - Sentence-level streaming for fast responses
- **Browser-Based** - No app install, works in Chrome/Edge/Firefox

### Tech Stack

- LiveKit (WebRTC audio streaming)
- Deepgram Nova-3 (Speech-to-Text)
- Silero VAD (Voice Activity Detection)
- OpenClaw Gateway (LLM orchestration)
- Edge TTS (Text-to-Speech)
- Python + Vanilla JS

### Quick Start

```bash
# Terminal 1: LiveKit Server
livekit-server --dev

# Terminal 2: Token Server
python token_server.py

# Terminal 3: Voice Agent
python agent.py connect --room openclaw-voice

# Terminal 4: Frontend
cd frontend && python -m http.server 8000

# Open http://localhost:8000
```

## Topics/Tags

`livekit` `openclaw` `voice-assistant` `webrtc` `speech-to-text` `text-to-speech` `ai-agent` `deepgram` `edge-tts` `python`
