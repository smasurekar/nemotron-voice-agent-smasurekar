# Nemotron Voice Agent — Repo Overview

A getting-started guide to what this repository is and how it is put together.

---

## 1. What is this repo about?

This is an **NVIDIA AI Blueprint**: a working, end-to-end **real-time voice agent** you can clone, run, and modify.

You speak into a browser. The app listens, understands, thinks, and talks back — with sub-second latency and natural interruption handling.

It is built as a **cascaded speech pipeline**:

```
Mic (browser) → [ASR: speech → text] → [LLM: think + reply] → [TTS: text → speech] → Speaker
                                    ↑
                          VAD + turn detection decide
                          when the user stopped talking
```

There is also an **Omni** variant where a single multimodal model (Nemotron Omni) replaces the ASR + LLM stages and takes audio in directly.

Key points:

- All models are **NVIDIA Nemotron / Parakeet / Magpie** open models, served as **NIM microservices** — either NVIDIA-hosted in the cloud or self-hosted on your own GPU.
- Nothing is locked in. Any component (ASR, LLM, TTS) can be swapped by editing a YAML catalog.
- Runs on **cloud (no GPU)**, a **workstation GPU**, **DGX Spark**, or **Jetson Thor** (edge).
- Ships **5 example pipelines** — pick the one closest to your use case and adapt it.

### The 5 examples

| Example | What it shows | Where it runs |
|---|---|---|
| **Generic Assistant** (`src/examples/generic/`) | Baseline English cascaded pipeline (ASR + LLM + TTS) with tool calling. Start here. | Cloud, Workstation, DGX Spark, Jetson Thor |
| **Multilingual Assistant** (`src/examples/multilingual/`) | Multilingual ASR + TTS, one fixed language per session. | Cloud, Workstation, DGX Spark |
| **Omni Assistant** (`src/examples/omni_assistant/`) | Nemotron Omni replaces ASR + LLM; Magpie TTS speaks the reply. | Cloud, Workstation, DGX Spark, Jetson Thor |
| **Omni Assistant Subagents** (`src/examples/omni_assistant_subagents/`) | Multi-agent Omni setup: extra agents handle images, audio, video, and live webcam while the voice loop stays fast. | Cloud, Workstation, DGX Spark |
| **Frontend/Backend Agent** (`src/examples/frontend_backend_agent/`) | A fast "talker" LLM handles conversation; a slower "thinker" agent + a booking backend does the real work. Pattern for adding voice to an existing text agent. | Cloud, Workstation |

---

## 2. What ASR, TTS and LLM models are used?

### ASR (speech → text)

| Model | Catalog key | Notes |
|---|---|---|
| **Nemotron ASR Streaming (English)** | `nemotron-asr-streaming-english` | Default. Lowest latency, English only. |
| **Nemotron ASR Streaming (Multilingual)** | `nemotron-asr-streaming-multilingual` | ~40 locales, low latency, quality varies by language. |
| **Parakeet CTC 1.1B** | `parakeet-ctc` | English only. |
| **Parakeet 1.1B RNNT Multilingual** | `parakeet-rnnt` | 25+ languages, better accuracy, higher latency. |

> The Omni examples have **no separate ASR** — the Omni model consumes audio directly.

### TTS (text → speech)

| Model | Catalog key | Notes |
|---|---|---|
| **Magpie TTS Multilingual** | `magpie-multilingual-tts` | Default. 12 locales, many voices/emotions. |
| **Magpie TTS Zeroshot** | `magpie-zeroshot-tts` | Zero-shot voice cloning + built-in male/female voices. Restricted NGC access. |
| **Chatterbox TTS Multilingual** | `chatterbox-multilingual-tts` | 23 locales, one voice per locale. Heavy (~52 GB VRAM). |

### LLM

| Model | Catalog keys | Notes |
|---|---|---|
| **Nemotron 3.5 Lightning 30B A3B** | `nemotron-lightning`, `nemotron-lightning-reasoning` | Default for cloud cascaded pipelines. |
| **Nemotron 3 Nano 30B A3B** | `nemotron-nano`, `nemotron-nano-reasoning` | Default for self-hosted cascaded pipelines. |
| **Nemotron 3 Super 120B A12B** | `nemotron-super`, `nemotron-super-reasoning` | Highest capability, needs 2 × 80 GB GPUs locally. |
| **Nemotron 3 Nano Omni 30B A3B (Reasoning)** | `nemotron-omni-nvfp4` | Multimodal (audio + vision). Used by the two Omni examples. |

The `*-reasoning` keys are the **same weights** with chain-of-thought turned on (`enable_thinking: true`). Reasoning-off is the default because it is faster.

---

## 3. Which models are cloud API vs. deployed NIM?

Every model is declared in two files per example:

- **`services.cloud.yaml`** → NVIDIA-hosted endpoints. Always loaded.
- **`services.local.yaml`** → self-hosted Docker sidecars, split into `workstation` / `dgxspark` / `jetson` blocks. Merged on top of cloud, but **only if the endpoint is actually reachable**.

Two kinds of cloud endpoint are used:

- **LLMs** → OpenAI-compatible REST at `https://integrate.api.nvidia.com/v1` (build.nvidia.com).
- **ASR / TTS** → gRPC at `grpc.nvcf.nvidia.com:443` (NVCF), addressed by a `function_id`.

### Support matrix

| Model | Cloud (build.nvidia.com / NVCF) | Self-hosted | Serving runtime when self-hosted |
|---|---|---|---|
| **ASR** | | | |
| Nemotron ASR Streaming (EN) | ✅ NVCF gRPC | ✅ | Speech NIM `nemotron-asr-streaming:1.3.0` |
| Nemotron ASR Streaming (Multilingual) | ❌ | ✅ | Same NIM image, multilingual tag |
| Parakeet CTC 1.1B | ✅ NVCF gRPC | ✅ | Speech NIM `parakeet-1-1b-ctc-en-us:1.5.0` |
| Parakeet 1.1B RNNT Multilingual | ✅ NVCF gRPC | ✅ | Speech NIM `parakeet-1-1b-rnnt-multilingual:1.5.0` |
| **TTS** | | | |
| Magpie TTS Multilingual | ✅ NVCF gRPC | ✅ | Speech NIM `magpie-tts-multilingual:1.9.0` |
| Magpie TTS Zeroshot | ❌ (no cloud function) | ✅ | Speech NIM `magpie-tts-zeroshot:1.2.0` |
| Chatterbox TTS Multilingual | ✅ NVCF gRPC | ✅ | NIM `chatterbox-tts-multilingual:1.0.0` |
| **LLM** | | | |
| Nemotron 3.5 Lightning 30B A3B | ✅ REST | ❌ (cloud only) | — |
| Nemotron 3 Nano 30B A3B | ❌ (cloud endpoint deprecated) | ✅ | LLM NIM `nemotron-3-nano:2.0.9`, or **vLLM** (NVFP4) on DGX Spark / Jetson |
| Nemotron 3 Super 120B A12B | ✅ REST | ✅ | LLM NIM `nemotron-3-super-120b-a12b:2.0.9` (tp=2) |
| Nemotron 3 Nano Omni 30B A3B | ✅ REST | ✅ | **vLLM** (`vllm-openai:v0.20.0-cu130`), NVFP4 only → Blackwell GPU required |

**On Jetson Thor**, ASR and TTS do not use NIM containers — they are served together by the on-device **Riva Embedded SDK** (`riva-speech:2.26.0-l4t-aarch64`).

### Resolution rules worth knowing

- If a default key exists in **both** catalogs, the **self-hosted** entry wins — deploying a local NIM automatically promotes it.
- If that local endpoint is down at session start, it **falls back to cloud**.
- A key that exists only in `services.cloud.yaml` always resolves to cloud, even on a local deployment.
- Users can also switch model per-slot live from the **Services tab** in the UI.
- Any **OpenAI-compatible** LLM endpoint and any **NIM** ASR/TTS can be dropped in by adding a catalog entry.

---

## 4. Repo structure

```
.
├── src/                          # Python backend
│   ├── server.py                 # FastAPI app: REST + WebSocket + WebRTC signalling, static UI
│   ├── examples_registry.py      # Loads examples_registry.yaml, binds the active example
│   ├── utils.py                  # Catalog loading, prompt resolution, env parsing
│   ├── runtime_platform.py       # cloud / workstation / dgxspark / jetsonthor selection
│   ├── config_store.py           # In-memory runtime config discovered at startup
│   ├── attachment_store.py       # Session-scoped uploaded media (Omni subagents)
│   ├── webcam_frame_store.py     # Session-scoped webcam frames
│   ├── tracing.py                # OpenTelemetry setup
│   ├── eval_bot.py               # Pipecat Evals entrypoint
│   └── examples/
│       ├── shared/               # Reusable pieces across examples
│       │   ├── pipeline_utils.py     # Transport, VAD, Smart Turn, context building
│       │   ├── prewarm.py            # Warm services before first connection
│       │   ├── activity_check.py     # Proactive "are you still there?" checks
│       │   ├── audio_recorder.py     # Debug WAV dumps of ASR in / TTS out
│       │   ├── subagents.py          # YAML-declared subagent registry
│       │   └── nemotron_speech_text_filter.py
│       │
│       │   # Every example folder holds the same core file set:
│       │   #   pipeline.py           — builds and runs the pipeline
│       │   #   services.cloud.yaml   — cloud model catalog
│       │   #   services.local.yaml   — self-hosted model catalog
│       │   #   prompts.yaml          — system prompts
│       │   #   README.md             — example-specific docs
│       │
│       ├── generic/               # + tools.yaml / tools.py / tool_handlers.py
│       ├── multilingual/          # + multilingual_processor.py (locks session language)
│       ├── omni_assistant/        # + nvidia_omni_multimodal_service.py (audio-in LLM),
│       │                          #   audio_only_smart_turn_strategy.py, user_mute_processor.py
│       ├── frontend_backend_agent/
│       │   ├── src/               #   planner.py, protocol.py, tool_handlers.py, tts_filter.py
│       │   └── airline/           #   The reference "existing backend": flight search, PNR
│       │       └── database/      #   status, booking tools + a seeded SQL flight database
│       └── omni_assistant_subagents/
│           ├── subagents.yaml     # Subagent roles declared in YAML, not code
│           ├── media_dispatch_processor.py, conversation_log.py
│           └── subagents/        # The multi-agent example's internal roles:
│               ├── transport/    #   owns the client connection, RTVI events,
│               │                 #   webcam / media / gesture / thinking controllers
│               ├── speaker/      #   fast voice loop (JSON action stream, repeat guard)
│               ├── thinker/      #   slower reasoning agent
│               ├── media_analyzer/ # uploaded image / audio / video understanding
│               └── webcam/       #   live camera understanding
│
├── client/                       # React + TypeScript browser UI (Vite)
│   └── src/
│       ├── App.tsx, api.ts, main.tsx
│       ├── components/           # Sidebar, Header, VoiceSettings, SubagentsPanel,
│       │   ├── content/          #   WebcamVisionPanel, selectors…
│       │   │                     #   Conversation, Metrics, TTFB chart, Services,
│       │   │                     #   Prompts, Tools panels
│       │   └── status-panel/     #   Session / device / connection status
│       ├── context/, hooks/, styles/
│
├── docker/                       # One compose file per model sidecar
│   ├── docker-compose.nemotron-asr.yaml / .parakeet-asr.yaml
│   ├── docker-compose.magpie-tts.yaml / .magpie-zeroshot-tts.yaml / .chatterbox-tts.yaml
│   ├── docker-compose.nemotron3-nano.yaml / .nemotron3-super.yaml / .nemotron3-omni.yaml
│   ├── docker-compose.speech-jetson.yaml   # Riva embedded (ASR+TTS) for Thor
│   ├── docker-compose.coturn.yaml          # Optional TURN server
│   ├── docker-compose.phoenix.yaml         # Optional tracing UI
│   └── Dockerfile                          # App image
│
├── docker-compose.yml            # Root: defines every deployment profile
├── examples_registry.yaml        # Which examples exist, their slots and defaults
├── .env.example                  # All tunables (API key, TLS, VAD, timeouts, Jetson knobs)
│
├── docs/                         # Getting started, config guides, Jetson, troubleshooting
│   └── how-to/                   # configure-asr / -tts / -llm / -services / -prompts, tracing, TURN…
├── skills/                       # Agent skills for coding assistants
│   ├── deploy/, configure-pipeline/, upgrade-pipecat/
├── benchmarking_tools/           # BigBenchAudio eval, Full-Duplex-Bench, scaling/perf tests
├── tests/
│   ├── unit/                     # ~22 pytest files, no GPU needed
│   └── pipecat_evals/            # ci/ (fake bot, no services) + service/ (real models)
├── scripts/                      # CUDA MPS start/stop (Jetson), docs review receipt
├── notebooks/                    # Brev launchable notebook
└── misc/                         # This doc and other notes
```

### How a deployment profile works

Profiles are named `<example>/<hardware>`, or just `<example>` for cloud:

```bash
docker compose --profile generic-assistant up -d               # cloud, no GPU
docker compose --profile generic-assistant/workstation up -d   # local NIM sidecars
docker compose --profile omni-assistant/jetson-thor up -d      # edge
```

The profile sets two env vars — `EXAMPLE_SELECTION` (which pipeline to run) and `PLATFORM` (which block of `services.local.yaml` to merge) — and pulls in the matching model sidecars. Then open `https://<machine-ip>:7860`.

---

## 5. Technology and frameworks used

### Backend (Python ≥ 3.12)

| Piece | What it does |
|---|---|
| **[Pipecat](https://github.com/pipecat-ai/pipecat) 1.5.0** | The core real-time voice orchestration framework. Everything is a frame flowing through a pipeline of processors. Provides `NvidiaSTTService`, `NvidiaLLMService`, `NvidiaTTSService`. |
| **FastAPI + Uvicorn** | HTTP/WebSocket server, session config, WebRTC signalling, serves the built UI. |
| **RTVI** | The Pipecat-standard *Real-Time Voice/Video Inference* protocol — the wire contract between the Python server and the browser client. See [below](#rtvi-how-the-server-and-browser-talk). |
| **Silero VAD** | Voice activity detection — detects speech vs. silence. |
| **Pipecat Smart Turn v3** (local model) | End-of-utterance detection — decides when the user has actually finished, not just paused. Falls back to plain Silero VAD if disabled. |
| **uv** | Dependency and virtualenv management (`uv.lock`). |
| **loguru / PyYAML / python-dotenv** | Logging, catalogs, env config. |
| **OpenTelemetry** (+ Arize Phoenix) | Optional distributed tracing of pipeline latency. |
| **langchain-nvidia-ai-endpoints** | NVIDIA endpoint helpers. |
| **ruff, pytest, pre-commit** | Lint, test, hooks. |

### Frontend

**React 19 + TypeScript + Vite + SCSS**, with the **Pipecat JS client SDK** (`@pipecat-ai/client-js`, `client-react`, `small-webrtc-transport`, `websocket-transport`), TanStack Query, and Recharts.

### Transport

- **WebRTC** (default) — lowest latency, needs HTTPS. Optional **coturn** TURN server for clients behind NAT/firewalls.
- **WebSocket** — simpler, and the only one that supports multiple Uvicorn workers for scale testing.

### RTVI: how the server and browser talk

Audio moves over WebRTC/WebSocket, but everything *else* — transcripts, metrics, state, custom events — moves over **RTVI (Real-Time Voice/Video Inference)**, Pipecat's standard client/server protocol. It is what makes the UI more than a microphone.

**Server side** (`pipecat.processors.frameworks.rtvi`):

- Every example's `pipeline.py` pushes custom events to the browser with `RTVIServerMessageFrame(data={...})` — for example `{"type": "user-bot-latency", "latency": 0.62, "first": true}` from the latency observer.
- Examples register handlers on `task.rtvi`:
  - `on_client_ready` — the browser is connected and ready; this is where the welcome message is triggered.
  - `on_client_message` — inbound commands from the UI, e.g. `set-voice` to switch the TTS voice mid-session.
- In the subagents example, only the **transport** agent sets `enable_rtvi=True`; the speaker agent runs with `enable_rtvi=False` so events are emitted once, from one place.
- Over WebSocket, `ProtobufFrameSerializer(ignore_rtvi_messages=False)` keeps RTVI messages on the wire. Evals use `RTVIEvalSerializer`.

**Client side** (`@pipecat-ai/client-js` / `client-react`): components subscribe with the `useRTVIClientEvent` hook:

| RTVI event | Used by |
|---|---|
| `ServerMessage` | Conversation, Metrics, VoiceSettings, Subagents, Webcam panels — carries all the custom payloads above |
| `Metrics` | Metrics panel (TTFB charts) |
| `UserStartedSpeaking`, `BotStoppedSpeaking`, `RemoteAudioLevel` | Conversation panel, voice visualizer |
| `Disconnected` | Conversation and webcam cleanup |

**Why it matters in practice:**

- The scaling benchmark (`benchmarking_tools/scaling-perf/`) reads its server-side timings (`server_e2e`, `asr_ttfb`, `tts_ttfb`, `llm_processing_time`) purely from RTVI messages, so it never has to scrape server logs. These stay accurate even when client-side measurement is skewed by trailing silence.
- RTVI is a **versioned contract between server and client**. The `skills/upgrade-pipecat/` workflow exists largely because bumping `pipecat-ai` means bumping `@pipecat-ai/*` in `client/package.json` to a protocol-compatible version and migrating any renamed events — the server and client must move together.

### Model serving

- **NVIDIA NIM** microservices for LLM, ASR (NIM for Speech), and TTS.
- **vLLM** where a NIM profile doesn't fit — Omni NVFP4, DGX Spark, Jetson Thor.
- **NVIDIA Riva Embedded** for combined ASR+TTS on Jetson Thor.
- **NVCF** (gRPC) and **build.nvidia.com** (REST) for the cloud path.

### Infrastructure

Docker Compose with profile-based recipes, NGC container registry, self-signed TLS by default (browsers require a secure context for microphone access).

---

## 6. Details that are easy to miss

Useful things that are not obvious from the README.

### Almost everything is YAML, not code

The repo deliberately keeps behaviour in config files so you can change it without touching Python:

| File | Controls |
|---|---|
| `examples_registry.yaml` | Which examples exist, their service "slots", their defaults, and which pipeline function to call |
| `services.cloud.yaml` / `services.local.yaml` | Which models are available and where they live |
| `prompts.yaml` | The system prompts |
| `tools.yaml` | The function-calling tool schemas |
| `subagents.yaml` | The subagent roster, routing rules, and reasoning mode |

### Tool calling

The Generic Assistant ships **7 demo tools**: `get_weather`, `get_stock_price`, `convert_currency`, `calculate_bmi`, `get_current_date_time`, `generate_random_number`, `get_news_headlines`. Some call real APIs with a mock fallback, so they work offline.

A safety detail worth copying: `build_tools_schema()` only advertises a tool to the model if a matching Python handler actually exists in `TOOL_HANDLERS`. The model can never be offered a tool that would fail to run.

### Prompts are swappable at runtime

`prompts.yaml` holds multiple named prompts, and the UI has a Prompts panel to switch between them live. The Generic Assistant, for instance, includes `generic_assistant`, `generic_assistant_without_tools`, and a `flowershop` persona — a ready-made demo of how a persona change alters the agent.

### The subagents example is a small multi-agent system

Five roles, each a separate worker:

- **Transport** — owns the browser connection and all RTVI traffic. Also runs the webcam, media, gesture, and thinking controllers.
- **Speaker** — the fast voice loop. Every turn it emits **one JSON envelope** declaring exactly one action: `respond`, `think`, `analyze_attachment`, `capture_highres`, or `clarify`, plus which input it wants (`none` / `live_webcam` / `uploaded_attachment`).
- **Thinker** — escalation path. When the Speaker's self-rated confidence drops it picks `think`, and the Thinker re-answers the turn with full reasoning on.
- **Media Analyzer** — describes uploaded images, audio, and video.
- **Webcam** — continuously describes the live camera. It is *ambient*: the Speaker never routes to it, it just reads its latest state.

The webcam worker also scores simple **hand gestures** per frame (`greet`, `stop`, `continue`, `down`), which a gesture controller turns into proactive actions — for example the agent greeting you when you wave.

### The Frontend/Backend example includes a whole fake airline

`airline/` is a complete reference backend, not a stub: flight search, PNR status, booking tools, and a seeded SQL database (`schema.sql`, `flights.jsonl`, `pnrs.jsonl`) with its own compose file. It also has a **TTS filter** that rewrites text so it *sounds* right when spoken — "PNR" becomes readable, and airport codes and flight numbers get expanded into spoken form.

The split is: a fast **talker** LLM keeps the conversation flowing while a slower **thinker** LLM (with a bigger token budget and reasoning on) does the actual work. That is what hides backend latency from the user.

### Latency tricks built into the pipeline

- **Prewarm** (`shared/prewarm.py`) — services are warmed up before the first connection so the event loop is never blocked. There is a readiness gate with a configurable timeout (`CONNECT_PREWARM_TIMEOUT_SECS`, default 45s).
- **Smart Turn v3** runs locally alongside a short Silero VAD window (`stop_secs=0.2`), so end-of-turn is decided by a model rather than by a fixed silence timer. Set `USE_SILERO_VAD_TURN_DETECTION=true` to fall back to plain VAD.
- **Chat history is trimmed** to the last N turns (`CHAT_HISTORY_RECENT_TURNS`, default 10; 20 for Frontend/Backend), while the system prompt is always kept.
- The Omni example has an **audio-only turn strategy**, because Omni takes raw audio and there is no transcript to trigger on.

### Small behaviours you can turn on or off

| Feature | How |
|---|---|
| **Welcome message** — the bot greets you on connect | Per example in `examples_registry.yaml`; `ENABLE_WELCOME_MESSAGE` overrides globally |
| **Activity check** — proactive "are you still there?" after idle time | `activity_check` in `examples_registry.yaml` (Generic: first nudge at 480s) |
| **Audio dumps** — save ASR input and TTS output as WAV for debugging | `ENABLE_ASR_AUDIO_DUMP` / `ENABLE_TTS_AUDIO_DUMP` |
| **Conversation log** — dump the Speaker's raw LLM context | `OMNI_CONVERSATION_LOG_ENABLED=1` |
| **Pronunciation dictionary** — force how words are spoken (IPA) | `TTS_IPA_FILE_PATH`, e.g. `{"NVIDIA": "ɛn.vɪ.diː.ʌ"}` |
| **Tracing** — latency spans in Arize Phoenix | `ENABLE_TRACING=true` + the `phoenix` compose profile |

### Testing has three separate layers

1. **Unit tests** (`tests/unit/`) — ~22 pytest files, no GPU, no services. Run in CI on every push.
2. **CI evals** (`tests/pipecat_evals/ci/`) — a fake deterministic bot that exercises the transport and RTVI path without touching a model.
3. **Service evals** (`tests/pipecat_evals/service/`) — real scenarios against real models (greeting, tool use, context memory, language locking, latency smoke). Uses a **local Ollama model as the judge** and Kokoro/Moonshine for synthetic audio turns.

CI itself is light: ruff lint + format, pytest, and an eslint/tsc/vite client build. No GPU required.

### Other bits

- **Jetson Thor** gets special treatment: CUDA **MPS** scripts (`scripts/start-mps.sh`) to share the GPU between vLLM and Riva, plus CPU pinning across its 14 cores via `taskset`.
- **`notebooks/brev_launchable.ipynb`** is a one-click cloud deployment path — clone, enter your key, pick a profile, launch.
- **HTTPS is on by default** (self-signed) because browsers refuse microphone access otherwise. `PIPELINE_TLS=false` gives plain HTTP for headless/API testing.
- **Multiple Uvicorn workers** (`UVICORN_WORKERS`) only work with the WebSocket transport — not WebRTC. The `workstation-perf` profile uses 200 workers for load testing.
- The repo pins **patched versions of several dependencies** (`override-dependencies` in `pyproject.toml`) specifically to close known CVEs.

---

## 7. Where to go next

| I want to… | Read |
|---|---|
| Deploy it for the first time | `docs/01-getting-started.md` |
| Change models or endpoints | `docs/how-to/configure-services.md`, then `configure-asr.md` / `-tts.md` / `-llm.md` |
| Change what the agent says | `docs/how-to/configure-prompts.md` and each example's `prompts.yaml` |
| Understand a specific example | That example's `README.md` under `src/examples/` |
| Understand the agent patterns | `misc/agent-types.md` |
| Cut latency | `docs/how-to/tune-pipeline-performance.md`, `docs/05-best-practices.md` |
| Fix a startup problem | `docs/06-troubleshooting.md` |
| Run on edge | `docs/03-jetson-thor.md` |

**License:** BSD 2-Clause. **Version:** 2.1.1.
