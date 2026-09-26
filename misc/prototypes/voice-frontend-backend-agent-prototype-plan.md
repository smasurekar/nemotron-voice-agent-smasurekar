# Plan — Voice Frontend/Backend Agent Prototype (OpenAI Realtime server, τ³-voice ready)

**Status:** implemented (P1–P5, P7; Gate A passed; P6 and Gate B are manual runs, see the runbook) · **Date:** 2026-09-23 · **Revision:** 5 (implementation notes, see revision log)
**Code:** `src/prototypes/voice_frontend_backend_agent/` · **Runbook:** [`voice-frontend-backend-agent-runbook.md`](voice-frontend-backend-agent-runbook.md)
**Builds on:** [`text-frontend-backend-agent-prototype-plan.md`](text-frontend-backend-agent-prototype-plan.md)
(`src/prototypes/text_frontend_backend_agent/`), which this prototype imports without changing it.
**Evaluation target:** τ³-bench voice (`tau2-bench-smasurekar`), run through its `pine-` custom Realtime
endpoint route (`tau2-bench-smasurekar/misc/prototypes/voice-custom-agent-openai-realtime-integration.md`).

This prototype puts ASR and TTS around the text prototype's agent and serves the result as a WebSocket
server that speaks the OpenAI Realtime protocol. τ³-bench's audio-native `openai` client can then drive
it exactly as it drives `gpt-realtime`. The agent logic stays in the text prototype: frontend/backend
routing, delegation contract, external tools, session state, accounting. This package adds speech, turn
taking, the Realtime wire protocol, filler handling and serving.

---

## 0. Decisions (confirmed 2026-09-23)

All five decisions below were confirmed as recommended. For D4, filler timing must be recorded **from
the start**: every record is anchored to the session start and to the start of the user's turn, on both the
wall clock and the input-audio clock, from the first turn onward (§9.2).

| # | Decision | Confirmed choice |
|---|---|---|
| D1 | Run the session as a Pipecat pipeline, or as a plain asyncio engine that uses Pipecat/Riva components as libraries | **Plain asyncio engine** (§4.1). Pipecat's Silero VAD, stream resampler and Riva clients are used as libraries; there is no `Pipeline`/`PipelineWorker` |
| D2 | Extend the shared `src/realtime/` gateway, or add a separate prototype server | **Separate prototype server** that *reuses* `src/realtime/events.py`, `audio.py`, `conversation.py` helpers read-only (§4.2). Shared gateway behaviour and docs stay untouched |
| D3 | Which prompts receive `session.update.instructions` (the τ³ policy) | **Backend only** by default (`instructions.apply_to: [backend]`), with `frontend` available as an option (§11) |
| D4 | Filler text in paired mode | **Silenced and time-logged by default** (`filler.mode: log_only`). `filler.mode: speak` makes it audible for live use (§9) |
| D5 | Serving | **Existing image and compose services, no Compose or Dockerfile edits**: `docker compose run` on the existing `frontend-backend-agent*` service with a command override (§15) |

---

## 1. Requirements as testable rules

| # | Requirement (from the request) | Rule |
|---|---|---|
| V1 | Voice-to-voice on top of the text prototype | Audio in → ASR (nemo-speech / Riva gRPC) → `FrontendBackendAgent` → TTS (nemo-speech / Riva gRPC) → audio out. The agent is the text prototype's, imported unchanged |
| V2 | τ³-voice compatible Realtime server | Passes the τ² client contract in §3 (handshake, `audio/pcmu` 8 kHz, server VAD, GA event names, per-item transcripts, function calls, barge-in clock) |
| V3 | Standard nemo-speech speech services | ASR/TTS endpoints come from the same catalogs as `src/examples/frontend_backend_agent/` (`nemo-speech:50051` on single-GPU, NVCF in cloud) |
| V4 | Everything from YAML | One `voice_agent.yaml` entry point: server, protocol, audio, VAD, ASR, TTS, filler, tools, instructions, logging, and the text agent config (by reference plus overrides) |
| V5 | Existing Docker container | Runs in `nemotron-voice-agent:latest` through an existing compose service. Dockerfile and Compose files are not modified |
| V6 | External tools via Realtime → backend → tool messages | `session.update.tools` become backend `ToolSpec`s with `execution: external`. Backend tool calls go out as Realtime `function_call` items; `function_call_output` + `response.create` resume the backend through `send_tool_results()` |
| V7 | Frontend can be disabled | `agent.mode: backend_only` (text prototype) gives a standalone, **stateful** backend. The voice layer does not branch on mode anywhere except filler (none exists in that mode) |
| V8 | Paired-mode filler silenced for eval, optional for live use | `filler.mode: log_only` (default): no audio, no transcript, no event on the wire, only a timestamped log record. `filler.mode: speak`: filler is synthesized and streamed as its own assistant item |
| V9 | External prompt for the backend LLM | `session.update.instructions` (τ³ policy) is rendered into the backend prompt per session; a YAML file/inline prompt is the fallback when no instructions are sent |
| V10 | Pluggable chat interface | An independent Realtime *client* (`cli/voice_chat.py`) with swappable audio I/O (mic/speaker, WAV file, text-only), able to register and execute client tools |
| V11 | Reuse the text prototype | Imports `assemble_agent`, `Config`, `SessionState`, `ToolSpec`, `ToolResult`, `EventSink`, `History`, the `OpenAIChatClient`, `demo_tools` and `cli/ui.py` pieces. No copy-paste of agent logic |
| V12 | High quality, modular | Ports-and-adapters: every external dependency (VAD, ASR, TTS, codec, agent, clock, wire) sits behind a small protocol with a fake for tests. No network in unit tests |

---

## 2. What exists today and what it cannot do

### 2.1 Text prototype (`src/prototypes/text_frontend_backend_agent/`)

Already provides everything the *agent* side needs:

- `FrontendBackendAgent.send(text, session)` / `send_tool_results(results, session)` → `(AgentTurn, SessionState)`.
- `AgentTurn` is `final_text` **xor** `tool_calls`. Filler never lands on it; it goes to the `EventSink` as a
  `filler` event carrying a wall-clock timestamp.
- `execution: external` suspends the backend in `SessionState.pending`, which is exactly the Realtime
  function-call round trip.
- `mode: backend_only` with a stateful backend history.
- Immutable `SessionState`. **Cancelling an in-flight `send()` is therefore free**: the caller keeps the
  previous state and nothing is half-written. The voice layer relies on this for barge-in (§8.3).
- `assemble_agent(config, tools=…, event_sink=…, frontend_client=…, backend_client=…)` builds an agent
  from injected clients cheaply, so the voice layer can build **one agent per Realtime session** (per-session
  tools and instructions) while sharing the HTTP clients.

### 2.2 Shared Realtime gateway (`src/realtime/`, `WS /v1/realtime` in `src/server.py`)

A Pipecat-pipeline shim for the registered examples. By design it:

| τ³ needs | `src/realtime/` today |
|---|---|
| client `tools` in `session.update` | ignored (`session.py` `map_session_update_to_flat_config`) |
| `conversation.item.create` `function_call_output` | rejected with `unsupported_item` (`serializer.py`) |
| `audio/pcmu` 8 kHz input/output | rejected: "G.711 … not supported in v1" (`audio.py`) |
| `turn_detection` with `threshold`, `prefix_padding_ms`, `silence_duration_ms` | rejected: "tuning is not supported" (`session.py` `validate_server_vad`) |
| `response.create` before any assistant response | rejected during the welcome window |
| function-call output items and `response.function_call_arguments.*` | not emitted. Tools are internal and reported through `nvidia.tool.*` |
| audio-clock turn timing | Pipecat user-turn strategies run on wall-clock timers |

Changing those behaviours in the shared gateway would change a documented, user-facing surface
(`docs/how-to/use-realtime-gateway.md`) for every example. The prototype instead **reuses the
protocol-neutral helpers** from that package, which it only reads and never modifies:

- `realtime.events`: event-name constants, `server_event()`, `error_event()`, `new_event_id()`.
- `realtime.audio`: `decode_base64_audio`, `encode_base64_audio`, `AudioResampler` (Pipecat SOXR stream).
- `realtime.conversation`: `new_item_id()`, `new_response_id()`.

### 2.3 Official voice example (`src/examples/frontend_backend_agent/`)

Supplies the speech configuration conventions:

- `services.local.yaml` / `services.cloud.yaml`: `asr` and `tts` entries (`server`, `model`, `function_id`,
  `voice_id`, `synthesis_mode`). For `frontend-backend-agent/single-gpu`, both point at `nemo-speech:50051`.
- The filler-threshold idea (`THINKER_FILLER_THRESHOLD_SECONDS`, 0.3 s: speak a filler only if the backend is
  still busy after the threshold). This becomes `filler.speak_after_ms`.
- The TTS text transforms (`NemotronSpeechTextFilter`, pronunciation transform). These are reused
  optionally through a `TextNormalizer` port (§13.4).

---

## 3. The τ³ client contract this server must satisfy

Derived from `tau2-bench-smasurekar` code; `file:line` references are in that repo.

| Topic | Contract |
|---|---|
| Endpoint | `--audio-native-model pine-<name>` makes tau2 connect to `$PINE_REALTIME_BASE_URL?model=pine-<name>` with `Authorization: Bearer $PINE_API_KEY` (`voice/audio_native/openai/provider.py:139-148,198-201`). Plain `ws://` works |
| Handshake | The first server frame **must** be `session.created` (with `session.id`). tau2 then sends one `session.update` and waits for `session.updated`. An `error` in this phase aborts the run. There is a 30 s connect+configure budget |
| `session.update` | `instructions` (voice etiquette + domain policy), flat Realtime `tools` `[{type:function,name,description,parameters}]`, `tool_choice:"auto"`, `output_modalities:["audio"]`, `audio.input.format {type:"audio/pcmu"}`, `audio.output.format {type:"audio/pcmu"}`, `audio.output.voice:"alloy"`, `audio.input.transcription {model,language}`, `audio.input.noise_reduction`, `audio.input.turn_detection {type:"server_vad",threshold:0.5,prefix_padding_ms:300,silence_duration_ms:500}`, optional `reasoning.effort` |
| User audio | `input_audio_buffer.append` every 20 ms with 160 bytes of μ-law (8 kHz). **Silence (`0x7f`) is streamed continuously.** tau2 never sends `commit` or a user `response.create`: turn detection is entirely the server's job |
| Wall-clock gaps | User-simulator LLM/TTS calls block tau2's tick loop, so appends can pause for seconds of wall-clock time. **All timing must use the audio clock** (bytes received ÷ 8000), not wall-clock timers |
| Agent audio | Only `response.output_audio.delta` (`item_id`, base64 `delta`) counts as speech. It may be sent faster than real time; tau2 buffers it and plays 200 ms per tick |
| Agent text | Only `response.output_audio_transcript.delta` (`item_id`, `delta`) is scored. The `.done` transcript is ignored. The scored text for a tick is proportional to bytes played ÷ bytes received **for that `item_id`**. Audio without a transcript scores as empty; a transcript without audio is never shown |
| Items | **A fresh `item_id` per utterance.** After a barge-in, tau2 skips further deltas carrying the interrupted `item_id` |
| Tools | `response.function_call_arguments.done` with top-level `call_id`, `name`, `arguments` (JSON string) becomes a tau2 `ToolCall`. tau2 executes it against the domain DB, then sends `conversation.item.create {type:function_call_output, call_id, output}` (errors are `"Error: …"` strings) and `response.create` after the **last** output of a batch |
| Barge-in | Only triggered by the server's `input_audio_buffer.speech_started` with `audio_start_ms` on the **cumulative input-audio clock** while agent audio is still pending. tau2 then drops buffered audio and sends `conversation.item.truncate` (its `audio_end_ms` is not reliable, so we do not use it) |
| Other events | `response.created`, `output_item.*`, `content_part.*`, `response.done` (usage only), `error` after the handshake: recorded, not fatal |
| Greeting | tau2 injects a text-only "Hi! How can I help you today?" that the server never hears. **The server must not greet** |
| Ending | The agent ends a call by calling tool `transfer_to_human_agents`; the user simulator ends with `###STOP###`. A `MAX_STEPS` ending scores 0 |
| Prompt | tau2's instruction text is written for an audio-native model. A cascade-specific addendum must be added server-side (§11) |
| Scoring of tools | tau2 executes the tools, so **the server must never execute domain tools itself**; if it did, the DB would not change and DB-checked tasks would fail |

---

## 4. Architecture

```
                               voice_frontend_backend_agent (one process, N sessions)
 ┌────────────┐   WS /v1/realtime   ┌──────────────────────────── RealtimeSession (per WS) ─────────────────────────────┐
 │ τ³ client  │ ◀─────────────────▶ │ wire/            engine/                                  agent/                  │
 │ or         │  JSON events        │ ┌──────────┐    ┌─────────────────────────────────────┐  ┌────────────────────┐  │
 │ voice_chat │                     │ │ decoder  │──▶ │ InputPath: codec → AudioClock → VAD │  │ AgentRunner        │  │
 └────────────┘                     │ │ (client  │    │   → UtteranceSegmenter → ASR stream │  │  per-session       │  │
                                    │ │  events) │    │                     │ final text    │  │  FrontendBackend-  │  │
                                    │ └──────────┘    │                     ▼               │  │  Agent (text pkg)  │  │
                                    │ ┌──────────┐    │ TurnManager (state machine §8) ─────┼─▶│  send / send_tool_ │  │
                                    │ │ encoder  │◀── │   ▲ tool outputs       │ speak(text)│  │  results           │  │
                                    │ │ (server  │    │   │                    ▼            │  │  FillerTap (sink)  │  │
                                    │ │  events) │    │ ResponseWriter ◀── OutputPath: TTS  │  └────────────────────┘  │
                                    │ └──────────┘    │  (items, deltas,     → resample →   │                           │
                                    │                 │   function calls)     codec         │                           │
                                    │                 └─────────────────────────────────────┘                           │
                                    └─────────────────────────────────────────────────────────────────────────────────────┘
                                           shared, immutable: VoiceConfig · base agent Config · LLM clients · speech clients
```

Three layers, each ignorant of the one above it:

1. **`wire/`**: the Realtime protocol only. It parses and validates client events into typed commands and
   builds server events from typed calls. It knows nothing about audio models or agents. It is pure
   functions plus a `WireWriter` that serializes and sends, and it is unit-tested with golden JSON.
2. **`engine/`**: speech and turn taking on an **audio clock**: codec, VAD, utterance segmentation, ASR,
   TTS, barge-in, and the turn state machine. It depends on the `speech/` ports and on an `AgentPort`.
3. **`agent/`**: the adapter over the text prototype: per-session agent assembly, tool mapping,
   instruction rendering, filler tap, and history repair after barge-in.

### 4.1 Why not a Pipecat pipeline (D1)

The existing examples are Pipecat pipelines, and the obvious move is to reuse one here. Four τ³
requirements argue against it:

1. **Audio clock.** Pipecat user-turn stop strategies and idle handling are wall-clock timers. tau2 pauses
   the input stream for seconds of wall-clock time while its user simulator thinks. A wall-clock
   endpointer would close turns (or fail to) at the wrong moments. The Silero `VADAnalyzer` itself *is*
   frame-counted, so we reuse it directly.
2. **Outward function calls.** Pipecat's LLM/function-call machinery assumes the pipeline executes tools.
   Here the backend's tool calls must leave the process and the turn must suspend until the client answers.
   The text prototype already models that suspension; a pipeline would need it re-plumbed through frames.
3. **Item and transcript discipline.** τ³ scores `transcript delta ↔ audio bytes` per `item_id`. The
   existing observer derives transcripts from frame traffic after the fact. Writing each sentence's
   transcript delta and its audio under one item id directly is simpler and verifiable.
4. **Testability.** A plain engine with injected ports runs fully offline in unit tests with a fake clock.

What we *do* reuse from Pipecat/Riva as libraries: `SileroVADAnalyzer` (8 kHz and 16 kHz native),
`create_stream_resampler` (through `realtime.audio.AudioResampler`), and `riva.client` `ASRService.
streaming_response_generator` / `SpeechSynthesisService.synthesize_online`, which are the same clients
Pipecat's `NvidiaSTTService`/`NvidiaTTSService` wrap and which talk to `nemo-speech` over Riva gRPC.

### 4.2 Why a separate server (D2)

The shared gateway is a documented product surface with deliberately narrower semantics (§2.2). The
prototype gets its own FastAPI app with `WS /v1/realtime` and `GET /health`. It imports the shared helpers
listed in §2.2 read-only, and does not register itself in `examples_registry.yaml`. If the prototype
graduates, the right move is to lift `wire/` into `src/realtime/` behind a capability flag. That is a
separate, documented change (§20).

---

## 5. Module layout

```
src/prototypes/voice_frontend_backend_agent/
  __init__.py                 public API: build_app, VoiceConfig, load_voice_config
  config.py                   VoiceConfig dataclasses + loader (reuses text pkg interpolate_env, deep-merge overrides)
  errors.py                   VoiceConfigError, WireProtocolError(code, param), SpeechServiceError
  clock.py                    AudioClock (cumulative input samples → ms), Clock protocol for wall time (fakeable)

  wire/                       -- OpenAI Realtime protocol, no audio/agent knowledge
    __init__.py
    client_events.py          parse(dict) -> typed commands: SessionUpdate, AudioAppend, AudioCommit, AudioClear,
                              ItemCreate(UserText | FunctionCallOutput), ItemTruncate, ItemDelete,
                              ResponseCreate, ResponseCancel, OutputAudioBufferClear; validation → WireProtocolError
    session_view.py           RealtimeSessionView: merge session.update patches (GA schema only; beta-only fields rejected),
                              public echo for session.created/updated, extract tools/instructions/formats/VAD
    server_events.py          builders for every server event we emit (reuses realtime.events.server_event/error_event)
    ids.py                    event/item/response/call ids (reuses realtime.conversation.new_item_id/new_response_id)
    writer.py                 WireWriter: single-writer send queue, GA event names only, send lock
    response_writer.py        ResponseWriter: owns one response's lifecycle and ordering guarantees (§7.3)

  audio/                      -- formats only
    __init__.py
    g711.py                   numpy μ-law / A-law encode/decode tables (no audioop: removed in Python 3.13)
    formats.py                AudioFormat(type, rate) from session view; to/from PCM16 at engine rate
    pcm.py                    base64 helpers (re-export realtime.audio), silence, duration math

  speech/                     -- ports + adapters for VAD / ASR / TTS
    __init__.py
    ports.py                  VoiceActivityDetector, StreamingRecognizer, Synthesizer, TextNormalizer protocols
    vad_silero.py             SileroVadAdapter over pipecat SileroVADAnalyzer (frame-counted, audio clock)
    vad_energy.py             RMS-energy VAD (deterministic; tests and no-model fallback)
    asr_riva.py               RivaStreamingRecognizer (riva.client ASRService, per-utterance stream, thread→async bridge)
    tts_riva.py               RivaSynthesizer (riva.client SpeechSynthesisService.synthesize_online, chunked)
    text_normalizer.py        optional reuse of examples.shared.nemotron_speech_text_filter + sentence splitter
    catalog.py                resolve asr/tts entries from an example's services.{local,cloud}.yaml or inline YAML

  engine/                     -- audio-clocked turn taking
    __init__.py
    input_path.py             decode → AudioClock → VAD → UtteranceSegmenter (prefix padding, silence_duration_ms)
    segmenter.py              UtteranceSegmenter: pure state machine over VAD frames in audio time
    output_path.py            text → sentences → TTS → resample → encode → deltas; playback-position estimate
    turn_manager.py           TurnManager: the state machine in §8 (IDLE/LISTENING/THINKING/SPEAKING/AWAITING_TOOLS)
    playback.py               ResponseProgress: generating / sent_ms + sentence spans / heard_ms (§8.3)
    session.py                RealtimeSession: wires wire/ + engine/ + agent/ for one WebSocket

  agent/                      -- adapter over the text prototype (no speech or wire knowledge)
    __init__.py
    port.py                   AgentPort protocol: respond(text) / resume(outputs) -> AgentReply; snapshot/restore
    runner.py                 TextAgentRunner: per-session assemble_agent(), SessionState ownership, cancellation
    tools.py                  Realtime tool schema <-> ToolSpec; function_call_output -> ToolResult
    instructions.py           render session instructions into backend (and optionally frontend) prompts (§11)
    filler.py                 FillerTap: session-routed EventSink that intercepts `filler` events (§9)
    history_repair.py         rewrite every stored copy of an interrupted answer to the heard prefix (§8.3)
    sinks.py                  SessionRoutingSink: one shared sink, per-session listeners, JSONL timing log

  server.py                   FastAPI app factory + uvicorn entrypoint (`python -m ...server --config ...`)

  config/
    voice_agent.yaml          default: paired mode, filler log_only, no greeting, τ³-compatible
    prompts.voice.yaml        voice-adapted frontend/backend prompts (derived from the text catalog, §11)
    profiles/
      tau3_eval.yaml          explicit eval profile (same as default, pins every eval-relevant knob)
      live_demo.yaml          filler speak, greeting on, pcm16 24 kHz default, internal demo tools
      backend_only.yaml       agent.mode backend_only
      cloud_speech.yaml       NVCF ASR/TTS instead of nemo-speech

  cli/
    __init__.py
    voice_chat.py             independent Realtime client REPL (§14)
    audio_io.py               AudioIO protocol + MicSpeakerIO (sounddevice, optional), WavFileIO, NullIO
    client_tools.py           load ToolSpec list (module:attr), execute function calls, send outputs
    tau2_replay.py            replays a tau2-shaped session (pcmu 160-byte appends, silence, tools) against a server

  README.md

tests/unit/prototypes/voice/
  _fakes.py                   FakeVAD (scripted), FakeRecognizer, FakeSynthesizer, FakeAgentPort, FakeClock, WS harness
  test_config.py test_g711.py test_session_view.py test_client_events.py test_server_events.py
  test_segmenter.py test_playback.py test_response_writer.py test_turn_manager.py
  test_function_call_roundtrip.py test_filler_policy.py test_barge_in.py test_backend_only.py
  test_instructions.py test_history_repair.py test_tau2_contract.py test_ws_end_to_end.py test_no_shared_mutation.py
```

Conventions follow the text prototype: SPDX headers, `from __future__ import annotations`, Google-style
docstrings, frozen dataclasses for value types, `loguru`, and no network in tests.

**Dependency rule, enforced by an AST test:** `wire/`, `agent/`, `engine/segmenter.py`,
`engine/turn_manager.py` and `audio/` import neither `pipecat`, `riva` nor `fastapi`. Only
`speech/*_riva.py`, `speech/vad_silero.py`, `audio` resampling and `server.py` touch those. The text
prototype stays Pipecat-free, and the voice package imports it, never the reverse.

### 5.1 Dependencies

Every package the voice package imports **directly** is declared, instead of being relied on
transitively. Today `nvidia-riva-client`, `websockets` and `numpy` appear only in the `dev` group (Riva is
otherwise pulled in through `pipecat-ai[nvidia]`), and `fastapi`/`uvicorn` arrive only through Pipecat's
`runner`/`websocket` extras. Two new extras in `pyproject.toml`, following the text prototype's pattern
(an extra, not a dependency group, so `uv pip install -e ".[prototypes-voice]"` works outside this repo's
dev environment):

```toml
[project.optional-dependencies]
prototypes = ["openai>=1.109.1", "rich>=13.9.4"]                  # unchanged
prototypes-voice = [
  "nemotron-voice-agent[prototypes]",       # the text agent it wraps
  "pipecat-ai[silero]==1.7.0",              # SileroVADAnalyzer, create_stream_resampler (pin matches the project)
  "nvidia-riva-client>=2.27.0,<3",          # ASR/TTS gRPC (bounds match [tool.uv] override)
  "fastapi>=0.115", "uvicorn>=0.30",        # server
  "websockets>=14.0",                       # chat client / tau2_replay transport
  "numpy>=2.0.0",                           # G.711 tables, PCM math
  "pyyaml>=6.0.3", "loguru>=0.7.3",
]
prototypes-voice-mic = ["nemotron-voice-agent[prototypes-voice]", "sounddevice>=0.5"]

[dependency-groups]
dev = [..., "nemotron-voice-agent[prototypes]", "nemotron-voice-agent[prototypes-voice]"]
```

Lower bounds for `fastapi`/`uvicorn` are set to the versions currently resolved in `uv.lock` at
implementation time, not to the placeholders above. `uv.lock` is regenerated and committed with the change.

- **Image impact: none.** Every `prototypes-voice` package is already installed in the image, through
  Pipecat or the `dev` group that `uv sync --frozen` installs. The extra only *declares* them, so the
  `docker compose run` recipe (§15) needs no rebuild beyond the normal one after `uv.lock` changes.
  `prototypes-voice-mic` is **not** in `dev`, so `sounddevice` never enters the image.
- **Microphone support** (`--io mic` only) needs PortAudio, a system library that pip cannot install:
  `sudo apt install libportaudio2` (Debian/Ubuntu; `portaudio19-dev` if building from source) or
  `brew install portaudio` (macOS), then `uv sync --dev --extra prototypes-voice-mic`. `sounddevice` is
  imported lazily inside `MicSpeakerIO`, so `--io wav|text`, the server and the tests work without it. A
  missing PortAudio produces a one-line error naming the install command. The README and runbook
  document this, including that mic I/O is meant for a workstation, not the container.
- The §5 AST layering test also checks the *declared-imports* rule: every top-level module imported
  anywhere in the package must be the stdlib, the text prototype, `realtime`/`utils`/`examples.shared`
  from this repo, or a distribution listed in `prototypes-voice(-mic)`.

---

## 6. Configuration (V4)

One entry point. The text agent's configuration is **referenced, not copied**, and specific keys are
overridden through a deep merge applied before `build_config()`. As a result, every text-prototype knob
(models, reasoning, delegation, history, accounting) keeps a single source of truth.

```yaml
# config/voice_agent.yaml  (defaults == τ³-eval-safe)
server:
  host: "0.0.0.0"
  port: ${FBA_VOICE_PORT:-8765}
  path: "/v1/realtime"
  require_bearer: false            # τ³ sends "Bearer $PINE_API_KEY"; set a token to enforce
  bearer_token: "${FBA_VOICE_TOKEN:-}"
  max_sessions: 8
  session_update_timeout_s: 30     # before the first session.update

agent:
  config: "../../text_frontend_backend_agent/config/agent.yaml"   # [in-path] -> src/prototypes/text_frontend_backend_agent/config/agent.yaml
  overrides:                        # deep-merged into the text agent.yaml before validation (§6.1)
    agent: {mode: frontend_backend}           # frontend_backend (default) | backend_only
    prompts: {path: "${FBA_VOICE_PROMPTS:-prompts.voice.yaml}"}   # [in-path] -> this directory
    backend:
      tools:
        on_incomplete_results: synthesize_error_result
        on_user_message_while_pending: discard_pending
    logging: {event_sink: none}     # the voice layer installs its own routing sink

protocol:
  greeting: {enabled: false, text: ""}        # τ³: never greet
  seed_history_with_client_greeting: "Hi! How can I help you today?"   # tau2 says this text-only; "" disables
  auto_response: true               # respond on VAD turn end (server_vad create_response semantics)
  resume_on: response_create        # response_create | last_function_output
  emit_input_transcription: true    # conversation.item.input_audio_transcription.completed
  emit_usage: true                  # map AgentTurn.usage onto response.done.response.usage

audio:
  engine_rate: 16000                # internal PCM16 mono rate (VAD/ASR)
  default_input_format:  {type: "audio/pcm", rate: 24000}   # used when the client does not specify
  default_output_format: {type: "audio/pcm", rate: 24000}
  output_chunk_ms: 100              # size of each response.output_audio.delta
  pace_output: false                # false: send as synthesized (τ³ buffers); true: real-time pacing for live clients

turn_detection:                     # defaults; session.update server_vad fields override per session
  vad: silero                       # silero | energy
  threshold: 0.5
  prefix_padding_ms: 300
  silence_duration_ms: 500
  min_speech_ms: 120                # shorter bursts are discarded as noise (no turn, no barge-in)
  min_transcript_chars: 1           # empty ASR result -> no response
  honor_client_values: true         # take threshold/prefix/silence from session.update

barge_in:
  enabled: true
  history: truncate_heard           # truncate_heard | keep_full  (rewrites every stored copy, §8.3)
  interruption_marker: " [interrupted by the user]"   # appended to the heard prefix; "" for none
  while_thinking: cancel_and_merge  # cancel_and_merge | ignore  (never cancels after tool calls went out, §8.2)

asr:
  source: catalog                   # catalog | inline
  catalog:
    example_dir: "../../../examples/frontend_backend_agent"   # [in-path] -> src/examples/frontend_backend_agent
    services: local                 # local -> services.local.yaml | cloud -> services.cloud.yaml
    platform: singlegpu             # local only: top-level section (server | singlegpu); must be empty for cloud
    key: nemotron-asr-streaming-english
    server_override: "${FBA_ASR_SERVER:-}"   # non-empty wins over the catalog server (host-native: localhost:50051)
  inline: {server: "localhost:50051", model: "", function_id: "", language_code: "en-US", use_ssl: false}
  interim_results: true             # -> conversation.item.input_audio_transcription.delta

tts:
  source: catalog
  catalog:
    example_dir: "../../../examples/frontend_backend_agent"   # [in-path]
    services: local
    platform: singlegpu
    key: magpie-multilingual-tts
    server_override: "${FBA_TTS_SERVER:-}"
  inline: {server: "localhost:50051", voice_id: "", model: "", function_id: "", language_code: "en-US", use_ssl: false}
  voice_map: {alloy: ""}            # client voice name -> TTS voice id ("" = catalog default); unknown -> default
  sample_rate: 22050                # request rate; resampled to the client format
  # (asr/tts) max_concurrent_streams: 0 -> default (ASR = max_sessions, TTS = 2 x max_sessions), §13.5
  normalize_text: true              # reuse NemotronSpeechTextFilter
  sentence_split: true              # one TTS request + transcript delta per sentence

filler:
  mode: log_only                    # log_only (default, τ³) | speak
  speak_after_ms: 300               # speak only if the backend is still busy after this delay
  log_path: "${FBA_FILLER_LOG:-}"   # [out-path] JSONL file; timing is always recorded (loguru + event_log) even when ""

tools:
  source: client                    # client (Realtime session.tools, external execution) | config (internal); no mixing, §10
  config_tools: ""                  # "module:attr" -> list[ToolSpec] with callables (internal execution)
  result_timeout_s: 120             # outstanding function calls with no output -> synthesized error result

instructions:
  apply_to: [backend]               # backend | frontend (any subset)
  placement: policy_slot            # policy_slot ({domain_policy}) | replace_prompt | append
  fallback_file: ""                 # [in-path] used when the client sends no instructions
  cascade_addendum_key: cascade_voice_addendum     # key in prompts.voice.yaml, appended to rendered prompts
  strip_patterns: []                # optional regexes removed from client instructions (e.g. audio-native-only lines)
  frontend_capabilities: from_tools # from_tools (default) | static (domain.capabilities) | none  -- §11

logging:
  level: INFO
  event_log: "${FBA_VOICE_EVENT_LOG:-}"    # [out-path] JSONL of internal + timing events, one line per event
  log_wire: false                           # log every wire event (debug; never logs audio payloads)
```

`[in-path]` and `[out-path]` mark path-typed keys; their resolution rules are in §6.1. `config_tools`
is a Python import reference (`module:attr`), not a path.

Validation happens in one place (`config.py`). Unknown keys are an error, enums are checked, and
`filler.mode: speak` together with `agent.mode: backend_only` produces a warning, since there is no filler
in that mode. Per-session values from `session.update` (formats, VAD tuning, tools, instructions, voice)
are resolved into an immutable `SessionSettings` and never mutate `VoiceConfig`.

### 6.1 Loading, relative paths, and profile merging (exact semantics)

`load_voice_config(path)` is the only loader. The server's and tests' `--config` accepts either the base
file or a profile. The steps below run in this order.

**1. Read and interpolate each file on its own.** `yaml.safe_load`, then the text prototype's
`interpolate_env` (`${VAR}`, `${VAR:-default}`). Interpolation happens per file, before any merge, so a
value's meaning never depends on which file overrides it.

**2. Resolve input paths against the declaring file.** Every `[in-path]` value that is non-empty and
relative is resolved against **the directory of the YAML file whose text contains it** (after
interpolation, so a relative `${FBA_VOICE_PROMPTS}` value is also file-relative), and made absolute *before*
merging. After a merge, a path therefore still means what it meant in the file that wrote it. The
`[in-path]` keys form a closed list:

| Key | Resolved default (from `config/voice_agent.yaml`) |
|---|---|
| `agent.config` | `src/prototypes/text_frontend_backend_agent/config/agent.yaml` |
| `agent.overrides.prompts.path` | `src/prototypes/voice_frontend_backend_agent/config/prompts.voice.yaml` |
| `asr.catalog.example_dir`, `tts.catalog.example_dir` | `src/examples/frontend_backend_agent` |
| `instructions.fallback_file` | (empty → unused) |
| `extends` | (see step 3) |

An absolute path, or `~` (expanded), is used as is. An empty string means "unset", not "this directory".
A missing file is a `VoiceConfigError` naming the key, the declaring file and the resolved path, raised at
load time and not at first use.

**`[out-path]` keys** (`filler.log_path`, `logging.event_log`) are outputs, not inputs, and resolve
against the **process working directory**. `AGENTS.md` requires commands to run from the repository root;
in the container the working directory is `/app`. Their parent directories are created on first write.

**3. Profiles merge through `extends`.** A profile is an ordinary voice config with one extra top-level
key, `extends: <in-path>`, naming its base (e.g. `config/profiles/tau3_eval.yaml` has
`extends: ../voice_agent.yaml`). Loading resolves the chain depth-first: load the base (recursively), then
merge the profile onto it. Chains may be several levels deep; a cycle is a `VoiceConfigError`. A profile
may be partial; the base may not, because it must pass validation on its own (a test loads it directly).

**4. Deep-merge rule, used for both profile → base and `agent.overrides` → text `agent.yaml`:**

| Base value | Override value | Result |
|---|---|---|
| mapping | mapping | recursive merge, key by key |
| anything | scalar or list | the override replaces it (lists are **not** concatenated: `instructions.apply_to: [backend]` in a profile means exactly that) |
| anything | `null` | key reset to its schema default (`null` is not a stored value) |
| mapping | scalar/list, or scalar/list → mapping | `VoiceConfigError` (type change) |
| absent | anything | added, then validated like any key |

**5. Text agent config.** Load `agent.config` with the text prototype's own reader (`yaml.safe_load` +
`interpolate_env`), deep-merge the fully merged `agent.overrides` mapping onto it (rule 4), and call the
text prototype's `build_config(raw, source_dir=<directory of agent.config>)`. Relative paths *inside the
text agent.yaml* (its own `prompts.path: "prompts.yaml"`) therefore keep resolving against the text
config's directory, as they do today. Override paths are already absolute from step 2, and
`Config.prompts_path` returns absolute paths unchanged (`text_frontend_backend_agent/config.py`
`prompts_path`). The text prototype is not modified.

**6. Validate once, on the merged result.** Unknown keys, enum values, type changes, rejected
combinations (`tools.source: merge` → §18.1; `asr.catalog.platform` set with `services: cloud`), and
catalog lookups: the ASR/TTS `key` must exist in the resolved `services.{local,cloud}.yaml` section.
`server_override`, when non-empty, replaces only the catalog entry's `server`, and SSL follows the
NVCF-host rule.

**Shipped files and what each changes** (every profile is `extends: ../voice_agent.yaml` plus only the
keys shown):

| File | Keys it sets |
|---|---|
| `config/voice_agent.yaml` | the complete base above (paired, filler `log_only`, client tools, no greeting, nemo-speech single-GPU) |
| `config/profiles/tau3_eval.yaml` | pins every eval-relevant key to its base value explicitly (`filler.mode`, `protocol.greeting`, `tools.source`, `instructions.*`, `barge_in.*`), so later edits to the base cannot silently change eval behaviour |
| `config/profiles/backend_only.yaml` | `agent.overrides.agent.mode: backend_only` |
| `config/profiles/cloud_speech.yaml` | `asr.catalog: {services: cloud, platform: ""}`, `tts.catalog: {services: cloud, platform: ""}` (same keys, `nemotron-asr-streaming-english` and `magpie-multilingual-tts`, exist in `services.cloud.yaml`) |
| `config/profiles/live_demo.yaml` | `filler.mode: speak`, `protocol.greeting.enabled: true`, `tools.source: config`, `tools.config_tools: prototypes.text_frontend_backend_agent.demo_tools:TOOLS`, `audio.pace_output: true` |

`test_config.py` loads **every shipped file** (base and each profile) from both the repository root and an
unrelated working directory, and asserts identical resolved `[in-path]` values. It also asserts that every
referenced file and catalog key exists, that a profile chain resolves, and that a cycle, a type change and an
unknown key each fail with a message naming the file and key.

---

## 7. Protocol mapping (V2)

A detailed reference is in [Appendix A](#appendix-a--openai-realtime-protocol-reference-used-by-this-server).
This section defines what the server *does*.

### 7.1 Client events

| Client event | Handling |
|---|---|
| `session.update` | **GA schema only** (§7.4). Merge into `RealtimeSessionView`. Resolve `SessionSettings`. On the first update, or when tools/instructions change, (re)build the per-session agent (§10, §11). Reply `session.updated` with the echoed view (tools echoed as received). Accept `audio.{input,output}.format` of `audio/pcm` (8/16/24/48 kHz), `audio/pcmu`, `audio/pcma`; `server_vad` with tuning; `semantic_vad` approximated by `server_vad` (§7.5, documented, warned); `turn_detection: null` = manual mode (commit + `response.create`). Ignored with a log line: `model`, `noise_reduction`, `transcription.model`, `reasoning`, `temperature` (the LLMs take theirs from YAML) |
| `input_audio_buffer.append` | Decode (codec by session format) → PCM16 at the engine rate → advance `AudioClock` → VAD → segmenter. Payload size capped |
| `input_audio_buffer.commit` | Manual mode: close the current utterance, emit `committed`. VAD mode: accepted as a flush hint. Empty buffer → `error input_audio_buffer_commit_empty` |
| `input_audio_buffer.clear` | Drop the unfinished utterance → `input_audio_buffer.cleared` |
| `conversation.item.create` `message/user` with `input_text` | Text user turn (chat client, debugging): skips ASR and enters the same TurnManager path |
| `conversation.item.create` `function_call_output` | Match `call_id` against outstanding calls. Unknown → `error invalid_value` (param `item.call_id`, not fatal). Store it, then ack with `conversation.item.added` + `conversation.item.done` |
| `conversation.item.create` other types | `error unsupported_item` (not fatal) |
| `conversation.item.truncate` | Mark the item truncated in history using **our own** playback estimate (§8.3). `audio_end_ms` is recorded and **clamped, never rejected**, because τ³'s value only covers the current tick. Emit `conversation.item.truncated` |
| `conversation.item.delete` | Accepted for user-text items not yet consumed; otherwise `error` |
| `response.create` | If function outputs are pending and complete → resume the backend (§10). If idle with an unconsumed text item → respond. **If the tool-call response has not emitted `response.done` yet, queue it and do not reject it.** τ³ sends outputs and `response.create` on the next tick, which can overtake our `response.done`. A `response.create` while a *spoken* response is active → `error conversation_already_has_active_response`. Per-response overrides (`instructions`, `tools`) → `error unsupported_response_override` (v1) |
| `response.cancel` | Cancel the active response (§8.3) → `response.done status=cancelled`. Idle → no-op |
| `output_audio_buffer.clear` | Stop sending remaining audio of the active response (treated like cancel for playback purposes) |

### 7.2 Server event sequences

All sequences use GA names; this server never emits a beta name (§7.4).

**User turn (VAD):**
```
input_audio_buffer.speech_started   {audio_start_ms, item_id}            # audio clock, onset incl. no padding
conversation.item.input_audio_transcription.delta*   {item_id, delta}    # optional interim
input_audio_buffer.speech_stopped   {audio_end_ms, item_id}              # onset of the silence that ended the turn
input_audio_buffer.committed        {item_id, previous_item_id}
conversation.item.added / .done     {item: {id, type:message, role:user, content:[{type:input_audio, transcript:null}]}}
conversation.item.input_audio_transcription.completed {item_id, content_index:0, transcript, usage}
```

`conversation.item.added` + `conversation.item.done` is the GA pair. The sequences below write
`conversation.item.added` for brevity; `.done` always follows when the item is complete.

**Spoken reply (answer text from frontend or backend):**
```
response.created                    {response: {id, status:in_progress, output:[]}}
response.output_item.added          {response_id, output_index:k, item: {id:item_A, type:message, role:assistant, status:in_progress, content:[]}}
conversation.item.added             {item: …same…}
response.content_part.added         {item_id:item_A, output_index:k, content_index:0, part:{type:audio, transcript:""}}
  per sentence s:
    response.output_audio_transcript.delta {item_id:item_A, delta: s}   # transcript first, then its audio
    response.output_audio.delta*           {item_id:item_A, delta: b64}
response.output_audio.done
response.output_audio_transcript.done {transcript: full}
response.content_part.done
response.output_item.done           {item: {…, status:completed, content:[{type:output_audio, transcript}]}}
response.done                       {response: {id, status:completed, output:[…], usage}}
```

**Tool-call response (backend asked for tools):**
```
response.created
  [filler item as above, only when filler.mode=speak and the delay elapsed]            # output_index 0
  per call (output_index k):
    response.output_item.added     {item: {id:item_F, type:function_call, call_id, name, arguments:"", status:in_progress}}
    conversation.item.added        {item: …}
    response.function_call_arguments.delta {item_id:item_F, call_id, delta: <full JSON>}
    response.function_call_arguments.done  {item_id:item_F, call_id, name, arguments: <JSON string>}
    response.output_item.done      {item: {…, arguments, status:completed}}
response.done                      {response: {status:completed, output:[…function_call items…], usage}}
```
`call_id` is the backend's own `ToolCall.id`, which the text prototype preserves from the provider, so
correlation is end to end. `name` and `call_id` are **top-level** fields on `function_call_arguments.done`,
because that is where tau2 reads them.

**Resume after tools** (client sent every `function_call_output`, then `response.create`): a new
`response.created`, then either a spoken reply or another tool-call response.

**Barge-in** (only when the session's `turn_detection.interrupt_response` is true, which is the default):
`input_audio_buffer.speech_started` → close open parts/items (`output_audio.done`, `content_part.done`,
`output_item.done` with `status:incomplete`) → `response.done {status:"cancelled", status_details:{type:
"cancelled", reason:"turn_detected"}}` → later a normal user turn. With `create_response: false` the server
commits turns but waits for the client's `response.create` (manual-trigger clients).

`response.done` always carries a `usage` object (`total_tokens`, `input_tokens`, `output_tokens`,
`input_token_details`, `output_token_details`), filled from `AgentTurn.usage` or zeroed, because some clients
(Pipecat's) dereference it unconditionally.

### 7.3 Ordering guarantees (`ResponseWriter`)

- Exactly one active response per session. Its events are emitted in the order above, from a single task.
  `WireWriter` serializes all sends through one queue, so interleaving is impossible.
- For every item, every `output_audio_transcript.delta` precedes the audio deltas that speak it. The
  transcript is never ahead of the item's total audio by more than one sentence. This satisfies τ³'s
  proportional-transcript scoring.
- Every assistant utterance gets a fresh `item_id`, including a filler item and its answer item.
- `response.done` is always emitted exactly once per `response.created`, with status `completed`,
  `cancelled`, `incomplete` or `failed` (an agent or TTS error produces `failed` plus an `error` event).
- A test asserts these properties over every sequence the fakes can generate (`test_response_writer.py`).

---

### 7.4 GA only

τ³ parses GA names only, and the beta interface differs in more than event names, so a partial beta mode
would be wrong in ways that are hard to see. The differences include `conversation.item.created` versus the
`.added`/`.done` pair, the missing `name` on `function_call_arguments.done`, top-level
`input_audio_format`/`voice`/`turn_detection` versus `audio.*`, `modalities`, `temperature`, and content
types. `realtime.events.BETA_EVENT_ALIASES` covers only six event names, so this server **does not reuse
`emit_with_aliases`** and has no dialect switch:

- emitted events are GA only, and `test_server_events.py` asserts that no emitted `type` is a beta name;
- a `session.update` carrying beta-only fields (`modalities`, `input_audio_format`, `output_audio_format`,
  top-level `voice` / `turn_detection` / `input_audio_transcription`, `temperature`,
  `max_response_output_tokens`) or beta format strings (`pcm16`, `g711_ulaw`, `g711_alaw`) gets a non-fatal
  `error invalid_value` naming each field and its GA replacement, and is **not** applied (it would
  otherwise silently fall back to defaults). This is different from *unknown* keys (`reasoning`,
  `noise_reduction`, future GA fields), which are tolerated;
- the `OpenAI-Beta: realtime=v1` header and `openai-beta.*` subprotocol are ignored, never echoed.

Beta support, if ever needed, is a separate encoder/decoder pair behind the `wire/` interfaces, not an
alias table.

### 7.5 `semantic_vad` is an approximation

OpenAI's `semantic_vad` ends a turn using a model of whether the user *sounds finished*. This server has
no such model. It maps the request onto `server_vad` with a fixed silence duration chosen by `eagerness`:

| eagerness | silence_duration_ms used | threshold / prefix |
|---|---|---|
| `high` | 300 | base config |
| `medium`, `auto` | 500 | base config |
| `low` | 800 | base config |

This is a silence-based endpointer, **not** semantic turn detection: it ends turns during mid-sentence
pauses longer than the silence duration, and never ends a turn early because a thought is complete.
Consequences:

- one `WARNING` per session names the mapping;
- `session.updated` echoes the client's `semantic_vad` object unchanged, but adds the effective
  `server_vad` parameters under `audio.input.turn_detection.x_nvidia_effective`, so tools that inspect the
  session can see what actually runs;
- the event log records the effective parameters on every turn;
- the package README states the approximation in its protocol-differences table.

τ³ sends `server_vad` by default, so eval runs are unaffected.

## 8. Turn state machine (`engine/turn_manager.py`)

```
            speech_started                 turn end (silence ≥ silence_duration_ms, audio clock)
   IDLE ───────────────────▶ LISTENING ──────────────────────────────────────────────▶ THINKING
    ▲                          │  ▲                                                    │  │  │
    │   noise (< min_speech)   │  │ speech resumes (cancel_and_merge) ◀────────────────┘  │  │
    └──────────────────────────┘  │                                                       │  │
    ▲                             │ barge-in                                  final_text  │  │ tool_calls
    │   playback finished         │                                                       ▼  ▼
    └──────────────────────── SPEAKING ◀──────────── resume(final_text) ─── AWAITING_TOOLS
                                                                            (user speech queued, §8.2)
```

### 8.1 Audio clock

`AudioClock` counts **input samples received** (after decoding) since the session started. Every VAD,
segmenter, barge-in and playback computation uses it. There are no asyncio timers in the turn path. Two
wall-clock timers exist and neither affects turn logic: `session_update_timeout_s` and
`tools.result_timeout_s`. Wall-clock times are also *recorded* for latency logs.

Turn end = `silence_duration_ms` of consecutive non-speech VAD frames, measured in samples.
`speech_started.audio_start_ms` is the first speech frame on that clock. The utterance audio sent to ASR
starts `prefix_padding_ms` earlier (ring buffer).

### 8.2 THINKING and AWAITING_TOOLS

- THINKING runs `AgentPort.respond(transcript)` as a task. The text prototype's `send()` runs the frontend,
  then (if it delegates) the backend, and returns `final_text` or `tool_calls`.
- **Speech during THINKING** (`barge_in.while_thinking: cancel_and_merge`): cancel the agent task. Because
  `SessionState` is immutable, the pre-turn state is intact. The next transcript becomes
  `"<previous transcript> <new transcript>"`. This is safe **only until the first tool call has left the
  process**. From that point the client has executed tools and mutated its DB, so the turn is never
  cancelled after that. Instead the new utterance is queued and processed after the turn completes.
- AWAITING_TOOLS holds the pending `SessionState` and the outstanding `call_id`s. Outputs are collected. On
  `response.create` (or on the last output, if `resume_on: last_function_output`), the runner calls
  `send_tool_results()`. User speech in this state is queued, not dropped. τ³ answers in the next tick, so
  this state is short.
- `tools.result_timeout_s` synthesizes `{"error": "tool result timeout"}` for missing outputs and resumes
  (`on_incomplete_results: synthesize_error_result`), so a stalled client cannot wedge a session.

### 8.3 SPEAKING, barge-in and what the history records

#### Three separately tracked quantities

A response is not "active" only while unplayed audio exists. `ResponseProgress` (in `engine/playback.py`)
tracks three things independently for the active response:

| Quantity | Meaning | Updated by |
|---|---|---|
| `generating` | the agent task, TTS synthesis or the send loop is still running for this response | set at response start; cleared when the last delta is sent or the task ends |
| `sent_ms` + `sentence_spans` | audio delivered to the client so far; for each sentence, its text and its `[start_ms, end_ms)` span on the item's audio timeline | the send loop, per delta |
| `heard_ms` | the client's playout cursor | each input-audio advance of `dt` ms: `heard_ms = min(heard_ms + dt, sent_ms)`, starting from the first delta |

The `heard_ms` update models τ³'s playback: 1× in lock step with the user audio it sends, pausing when its
buffer runs dry. That is why it is capped by `sent_ms` *at each step* rather than computed from the first
delta's timestamp. A slow TTS that stalls between sentences therefore does not inflate `heard_ms`. The same
tracker drives `pace_output: true` for live clients.

`response_active = generating or heard_ms < sent_ms`.

#### Barge-in trigger

On **confirmed** user speech (VAD onset plus `min_speech_ms`, on the audio clock) while `response_active`,
barge-in fires, **even when no unplayed audio is buffered**, e.g. TTS still synthesizing the first sentence,
or sentence *k+1* being synthesized after sentence *k* has fully played:

1. emit `speech_started` (τ³ drops its buffered audio on this);
2. cancel generation: cancel the TTS/send task and any lookahead synthesis, and stop sending deltas for the
   item;
3. close the response: `output_audio.done`, `content_part.done`, `output_item.done status:incomplete`,
   `response.done status:cancelled / turn_detected`;
4. repair the history (below) from the `heard_ms` at step 1;
5. continue with the new user turn.

Two cases are explicitly excluded:

- Speech while the agent is still THINKING (no response output yet) is handled by §8.2 (`cancel_and_merge`
  before any tool call has gone out; queued after).
- Speech during a tool-call response in `filler.mode: speak` cancels only the filler item's audio; the
  function calls and the pending tool round trip are never cancelled.

Speech that starts when `response_active` is false is an ordinary turn.

#### What the history records: every stored copy is truncated

`heard_text` is built from `sentence_spans`: every sentence whose span ended at or before `heard_ms`, plus,
for the sentence being played, the proportional prefix `ceil((heard_ms − start)/(end − start) × len)` (τ³'s
scoring rule), snapped back to a word boundary. Sentences never sent, or never synthesized, contribute
nothing. With `barge_in.history: truncate_heard` (default), the stored text is `heard_text +
barge_in.interruption_marker` (default `" [interrupted by the user]"`, or the marker alone if nothing was
heard). The next turn therefore sees only what was heard, and knows the answer was cut off.

The text prototype stores the same spoken text in **more than one place**. `agent/history_repair.py` rewrites
all of them in one new `SessionState` (the state is immutable, so no mutation is involved):

| Mode / path | Copies of the spoken text in the stored state | Rewritten |
|---|---|---|
| paired, delegated turn | last `frontend_history` group: `tool` message for `call_backend` (content = backend final text, `agent.py` `_finish_backend`) **and** the synthetic assistant continuation | **both** |
| paired, direct answer / fallback | last `frontend_history` group: the assistant message | the assistant message |
| paired, backend state | none by default: a paired backend is stateless per delegation, and its working history is discarded when the turn finishes. With `backend.conversation_history` on (`full` or `backend_turns`), the backend's final assistant message | nothing to do by default; with the history on, the backend's final message, atomically with the frontend copies |
| `backend_only` | last `backend_history` group: the final assistant message (earlier tool-call/tool-result messages in the group are not spoken text and stay intact) | the final assistant message |

The target group is located structurally (last group, expected shape per the table) **and** verified by exact
text equality with the response's full text. Text prototype §6.1 guarantees that every stored copy equals
`AgentTurn.final_text` byte for byte. If verification fails, the repair raises in tests and logs an error
in production (leaving history unrepaired), so a silent partial rewrite cannot happen. `keep_full` disables
the repair.

---

## 9. Filler handling (V8)

The frontend's `call_backend(query, filler_text)` produces a `filler` event on the text prototype's
`EventSink`, emitted **before** the backend starts. The voice layer installs a `SessionRoutingSink` (a single
shared sink that dispatches by `session_id`), and each session's `FillerTap` receives the event. No change to
the text prototype is needed.

| `filler.mode` | Wire effect | Log effect |
|---|---|---|
| `log_only` (**default**, τ³) | **Nothing.** No item, no transcript, no audio, no custom event. The wire stream is byte-for-byte what it would be without a frontend filler | One `filler` timing record (below) |
| `speak` | If the backend is still busy `speak_after_ms` after the filler was produced (audio-clock-independent: a wall-clock delay on the agent side, as in the official example), a filler **message item** is opened in the current response and synthesized, with its own `item_id`, transcript deltas and audio. The answer or function calls follow as later output items of the same response. If the backend finishes first, the filler is skipped and logged as `skipped_backend_fast`. Runs **concurrently** with the backend (§9.1) | Same record, with `spoken: true/false` |

### 9.1 How an audible filler runs while the backend is still working

`agent.send()` is a single coroutine: frontend, then backend. The filler must therefore start from *inside*
that call without waiting for it, and without the backend waiting for the filler:

1. The text prototype emits the `filler` event synchronously from `FrontendBackendAgent._emit_delegation`,
   **before** it calls the backend. `SessionRoutingSink.emit()` must not block or await. It stamps the
   times, resolves the session, and calls `session.filler_tap.offer(event)`, which only does
   `loop.call_soon_threadsafe(...)` to schedule a `FillerTask`, and returns. The backend starts
   immediately.
2. `FillerTask` (its own asyncio task, owned by the session's current response) waits `speak_after_ms` on the
   monotonic clock, **racing** the agent task: whichever finishes first wins (`asyncio.wait(...,
   FIRST_COMPLETED)`).
   - The agent finished first: the filler is cancelled and never started (`skipped_backend_fast`).
   - The timer fired first: the filler's TTS starts **concurrently** with the still-running backend call,
     on the TTS executor (§13.5). The filler item's events (`output_item.added`, transcript and audio
     deltas, `.done`s) go out through the `ResponseWriter`, which opens the response (`response.created`)
     at that moment if it is not open yet.
3. When the agent turn completes, its output (answer item, or function-call items) is queued in the
   `ResponseWriter` **behind** the filler item. The writer finishes sending the filler item first (sending
   is faster than real time, so this costs tens of ms), then emits the next item. Items never interleave.
   The answer's TTS may already be synthesizing during the filler, since only sending is serialized, so the
   filler adds no synthesis latency to the answer.
4. Barge-in during the filler cancels the filler's audio. If the agent is still THINKING, §8.2 applies;
   function calls already queued are still emitted (§8.3 exclusions).
5. `filler.mode: log_only` does steps 1-2 without the TTS/send branch. The timer still resolves, so
   `would_have_spoken` is computed from the same race and not from a separate code path.

Tests (`test_filler_policy.py`): with a `FakeChatClient` whose backend response is gated on an
`asyncio.Event`, the filler's first audio delta is emitted **while the backend call is still pending**
(asserted by the event still being unset at that moment); the answer item's `output_item.added` comes
strictly after the filler item's `output_item.done`; a backend faster than `speak_after_ms` yields no filler
item.

### 9.2 Filler timing log (always on, in both modes)

Filler latency is a primary measurement, so timing is recorded **from the start**: from the first turn of
every session, whether the filler is spoken or silenced, and with no configuration needed. `filler.log_path`
only chooses the file. When it is empty, the records still go to loguru and to `logging.event_log`.

**Anchors.** Each session stores `session_start` (wall clock at WebSocket accept, and audio time 0). Each
user turn stores `turn_start` (VAD speech onset) and `turn_end` (end-of-turn decision). All three are kept on
two clocks:

- **wall**: `time.time()` epoch seconds with ms precision, plus the ISO-8601 UTC string, and a monotonic
  clock for durations;
- **audio**: input-audio ms since the session started, the clock τ³ uses (§8.1). Wall-clock pauses in τ³'s
  input make the two diverge, so both are needed to line the log up with τ³ artifacts.

**Record**, one per delegation (JSONL plus a single loguru line):

```json
{"kind":"filler","session_id":"sess_…","turn_id":7,"mode":"log_only","text":"Let me check that.",
 "session_start_wall":"2026-09-23T12:03:02.118Z",
 "turn_start":          {"wall":"2026-09-23T12:03:40.904Z","audio_ms":39870},
 "turn_end":            {"wall":"2026-09-23T12:03:43.900Z","audio_ms":41230},
 "asr_final":           {"wall":"2026-09-23T12:03:44.010Z","since_turn_end_ms":110},
 "filler_ready":        {"wall":"2026-09-23T12:03:44.512Z","since_session_start_ms":42394,
                         "since_turn_start_ms":3608,"since_turn_end_ms":612,"frontend_latency_ms":502},
 "backend_done":        {"wall":"2026-09-23T12:03:46.652Z","since_filler_ms":2140,"since_turn_end_ms":2752},
 "first_answer_audio":  {"wall":"2026-09-23T12:03:46.931Z","since_turn_end_ms":3031},
 "filler_audio_start":  null,
 "speak_after_ms":300,"would_have_spoken":true,"spoken":false}
```

- `filler_ready` is stamped when the frontend's `call_backend` returns: the moment a filler *could* start.
  This is the latency that filler exists to hide.
- `would_have_spoken` = `backend_done.since_filler_ms > speak_after_ms`. `filler_audio_start` is filled only
  under `speak` (the first filler delta sent). The difference `first_answer_audio − filler_ready` is the
  silence that a filler covers.
- `backend_done` and `first_answer_audio` are written as the turn progresses. The record is emitted once, when
  the turn ends: after the answer's first audio, after tool calls go out (with `backend_done: null,
  "outcome":"tool_calls"`), or on cancellation (`"outcome":"cancelled"`). No delegation goes unrecorded.
- The text prototype's `filler` event already carries a wall-clock timestamp from its emission point. The
  `FillerTap` stamps the monotonic time at the same moment, so the two agree.

In τ³ runs this answers how often, and for how long, a user would have heard a filler, without the filler
touching the scored transcript.

**Why log_only emits no event at all.** A custom wire event (e.g. `nvidia.filler`) would be ignored by tau2
today, but it would still land in `result.events` and in any other client's handler. The requirement is
"please don't give out any event", so the log is the only channel.

Rule tests: `test_filler_policy.py` checks that under `log_only` the wire trace of a delegated turn equals
the trace of the same turn with `filler_text` removed, and that under `speak` the filler item precedes the
answer, has a distinct `item_id`, and is skipped when the backend returns within `speak_after_ms`.

---

## 10. External tools (V6)

1. `session.update.tools` (flat Realtime shape) → `agent/tools.py` → `ToolSpec(name, description,
   parameters, callable=None)`. Duplicate names are an `error`. `tool_choice` other than `auto` is logged
   and treated as `auto`: the backend always sees all tools, and the frontend never sees any (the text
   prototype's rule R4).
2. The runner builds the per-session agent with `assemble_agent(config', tools=specs, …)`, where `config'`
   has `backend.tools.execution: external`. With `tools.source: config`, `config_tools` supplies
   `ToolSpec`s with callables and execution is `internal` (live demo without a client that runs tools).
   Exactly one execution mode applies per session, chosen by `tools.source`:

   | `tools.source` | Tools the backend sees | `backend.tools.execution` |
   |---|---|---|
   | `client` (default, τ³) | `session.update.tools` only; `config_tools` ignored | `external` |
   | `config` | `config_tools` only; client tools rejected with a non-fatal `error` on `session.update` | `internal` |

   **Mixing is deferred (§18.1).** The text prototype's execution mode is one global switch
   (`FrontendBackendAgent._drive`: `if tools_config.execution == "external"` suspends *every* batch), and
   `InternalToolDriver` runs the whole batch through `ToolRegistry`. No public seam routes individual calls
   by tool, so a `merge` source cannot be built without modifying the text prototype. The config loader
   rejects `tools.source: merge` with a `VoiceConfigError` that names §18.1.
3. `AgentTurn.tool_calls` → a tool-call response (§7.2). `SessionState.pending` is kept by the runner.
4. `function_call_output` items → `ToolResult(tool_call_id=call_id, content=output)`. `output` is passed
   **verbatim** (τ³ error strings start with `"Error: "`; the backend sees them as they are).
5. `response.create` → `send_tool_results(results, session)` → next `AgentTurn`.

In paired mode this is the text prototype's full delegation path: frontend → `call_backend(query)` →
backend emits a tool call → the call surfaces on the wire → the output comes back → the backend continues →
final text → TTS. The frontend history receives its complete four-message group (text plan §6.1) when the
backend finishes, so the next frontend turn knows the outcome.

`transfer_to_human_agents` is an ordinary client tool. Nothing special-cases it.

---

## 11. External prompt for the backend (V9)

Resolution per session, first match wins:

1. `session.update.instructions`, if non-empty (τ³ always sends it);
2. `instructions.fallback_file` from YAML;
3. the text agent config's own `domain.policy`.

Placement (`instructions.placement`):

- `policy_slot` (**default**): the text becomes `{domain_policy}` in the backend prompt, via a per-session
  `dataclasses.replace(config, domain=replace(config.domain, policy=text))`. The rest of the backend
  prompt (contract, plain-spoken output) stays in force.
- `replace_prompt`: the text *is* the backend system prompt (`prompts.inline.backend = text`). This is
  for experiments that want tau2's prompt with no scaffold wording.
- `append`: backend prompt + `\n\n` + text.

`instructions.apply_to: [backend]` is the default (D3, confirmed). Adding `frontend` also renders the policy
into the frontend's `{domain_policy}`, at the cost of frontend latency on every turn.

**The frontend must still know what the agent can do.** The text frontend prompt has an *Unsupported*
mode: "for requests outside the capability list above, briefly say: {unsupported_reply}". With no
capability list configured, `prompts.py` renders `- (no capability list configured)`, so a policy-less
frontend could turn away real τ³ requests as unsupported and never delegate them. That would be a silent,
score-destroying failure, and a risk of the backend-only choice. The fix keeps the policy out of the
frontend (D3) and gives the frontend the **capability list** instead:

- `instructions.frontend_capabilities: from_tools` (default) renders `{capabilities}` per session from the
  client's tools: one line per tool, `- <tool name>: <first sentence of its description>`, in declaration
  order, deduplicated. For τ³ airline, for example, this yields "book_reservation: Book a reservation.",
  "get_user_details: Get the details of a user.", and so on. This tells the frontend what is *in scope*
  without the policy's rules. `static` uses the YAML `domain.capabilities`; `none` leaves them empty (for
  experiments only).
- The voice frontend prompt (`prompts.voice.yaml`) narrows *Unsupported* to "clearly unrelated to every
  capability listed" and states "when unsure, delegate". An over-delegated turn costs latency; a wrongly
  refused turn costs the task.
- `transfer_to_human_agents` is listed like any tool, so the frontend delegates "can I talk to a person".

**Exact-prompt tests for every target domain.** `tests/unit/prototypes/voice/fixtures/tau2_session_update/`
holds the **verbatim** `session.update` payloads for `mock`, `airline`, `retail` and `telecom` (and
`banking` if present in the tau2 checkout), captured once from tau2 by the Gate A script (§18.0), with
their provenance recorded. `test_instructions.py` renders, for each domain and for both `apply_to:
[backend]` and `[backend, frontend]`:

- snapshot tests of the exact frontend and backend system prompts (golden files, reviewed when they change);
- assertions that the default frontend prompt contains every tool name as a capability, does not contain the
  policy text, and does contain the "when unsure, delegate" clause; that the backend prompt contains the
  policy verbatim (minus `strip_patterns`) plus the cascade addendum; and that no placeholder is left
  unrendered;
- a scripted-frontend check per domain: a representative in-scope request per domain (from the task
  files' first user turn) must reach `call_backend` with the `FakeChatClient` scripted to follow the prompt's
  rules. This is a contract check, not a model check.

The *model's* behaviour is measured in P6: a fixed τ³ slice per domain, run with `apply_to: [backend]` and
with `[backend, frontend]`, compares reward, the frontend's direct-answer rate, wrong "unsupported" replies
(detected from the event log), and turn latency. If the default loses on reward, switching to both is a
one-line profile change; the result goes into the runbook.

**Cascade addendum.** τ³'s text is written for an audio-native model ("you will hear the caller…"). The
`cascade_voice_addendum` prompt key in `prompts.voice.yaml` is appended to every rendered prompt that
received instructions. It says that input is an ASR transcript that may contain recognition errors, that
spelled-out letters and digits should be reassembled, that output is spoken (short sentences, no markdown,
no lists, IDs read character by character), and never to mention transcripts. `strip_patterns` can remove
lines from the client text that conflict with the cascade.

**Prompt catalog.** `config/prompts.voice.yaml` contains `frontend` and `backend` voice variants derived
from the text catalog: same structure, same rules, the same six domain-neutral examples, and the text
prototype's domain-neutrality test re-run against them. It also contains `cascade_voice_addendum`. The
text catalog is not edited.

---

## 12. Backend-only mode (V7)

`agent.overrides.agent.mode: backend_only` (or `profiles/backend_only.yaml`). The text prototype already
guarantees a stateful backend with its own history and no frontend client. The voice layer has **no
mode-specific branches** besides the following:

- there is no filler source, so the `FillerTap` is inert;
- history repair on barge-in targets `backend_history` instead of `frontend_history`
  (`history_repair.py` asks the runner which history owns assistant text).

`test_backend_only.py` runs the full τ³ contract test (§17) in this mode. "Runs on its own" is also
checked: the backend-only profile has no frontend LLM configured, and building must not require one.

---

## 13. Audio path

### 13.1 Formats and codec

`audio/g711.py` holds numpy lookup-table G.711 μ-law and A-law encode/decode (`audioop` is removed in
Python 3.13, and the repo's venv is 3.13). Supported client formats are `audio/pcm` (8/16/24/48 kHz),
`audio/pcmu` and `audio/pcma` (8 kHz), plus the legacy names. The engine's internal format is PCM16 mono
at `audio.engine_rate` (16 kHz). Resampling uses `realtime.audio.AudioResampler`, with one stream per
direction per session.

### 13.2 VAD and segmentation

`SileroVadAdapter` wraps `SileroVADAnalyzer` with `VADParams(confidence=threshold, start_secs, stop_secs=0)`.
The segmenter owns the stop timing (`silence_duration_ms`), so that stop timing is counted in samples
by our own code. `min_speech_ms` filters clicks. The energy VAD is the deterministic test double and a
fallback.

### 13.3 ASR

`RivaStreamingRecognizer` opens **one Riva streaming request per utterance**: the prefix padding, then the
utterance audio, then end of stream, then the final transcript. Utterance-scoped streams make the result
depend only on the utterance audio, not on seconds of silence or on wall-clock gaps. Interim results become
`input_audio_transcription.delta`. The blocking gRPC generator runs in a worker thread, bridged by an
`asyncio.Queue`. Server, model, function id and SSL come from the catalog (`is_nvcf`-style SSL detection is
reused from `utils`). The ASR is warmed at startup with a short silent request, so the first τ³ turn does
not pay connection setup.

### 13.4 TTS

The agent text is normalized (optional reuse of `NemotronSpeechTextFilter`) and split into sentences. Each
sentence is synthesized with `synthesize_online` and streamed chunk by chunk, resampled to the client rate,
encoded, and cut into `output_chunk_ms` deltas. Sentence *k+1* is synthesized while sentence *k* is being
sent (one-sentence lookahead). The transcript delta for a sentence is written just before its first audio
delta (§7.3). A voice from `session.update` is mapped through `tts.voice_map`; an unknown voice falls back to
the catalog default with a warning.

### 13.5 Speech endpoints: channel, authentication and concurrency

**Resolution per endpoint (ASR, TTS)** from the resolved catalog entry (§6.1), mirroring what
`NvidiaSTTService._initialize_client` and `NvidiaTTSService` do in Pipecat 1.7 so that one catalog entry
behaves identically in both stacks:

| Field | Local nemo-speech (`nemo-speech:50051`) | Cloud NVCF (`grpc.nvcf.nvidia.com:443`) |
|---|---|---|
| `server` | catalog `server`, or `server_override` | catalog `server` |
| SSL | `use_ssl = utils.is_nvcf(server)` → **false** (insecure channel) | → **true** (TLS with system roots) |
| metadata | `authorization` only if `NVIDIA_API_KEY` is set (harmless, ignored by nemo-speech); no `function-id` | `[["function-id", <catalog function_id>], ["authorization", "Bearer <NVIDIA_API_KEY>"]]` |
| channel | `riva.client.Auth(None, use_ssl, server, metadata)` | same |
| model | `model_name` / TTS voice from the catalog | same, and routed by `function-id` |

Validation at load time: an NVCF server with an empty `function_id`, or with `NVIDIA_API_KEY` unset or
empty, is a `VoiceConfigError` naming the entry. We deliberately do not use `utils.nvidia_api_key()`'s
`"not-needed"` default here, because a placeholder key would fail late with an opaque gRPC
`UNAUTHENTICATED`. The key is read from the environment (`.env` in Compose), never from YAML, and is
redacted in every log line. A startup probe (the same short warm-up request as §13.3) turns wrong
credentials or `function-id`s into a clear startup failure and a non-200 `/health`, instead of a failure on
the first τ³ turn. Local nemo-speech behaviour is unchanged: insecure channel, no required metadata.

**Concurrency model** (up to `server.max_sessions` sessions):

- **One `riva.client.Auth` (one gRPC channel) per endpoint per process**, shared by all sessions. gRPC
  Python channels are thread-safe and multiplex concurrent RPCs. `ASRService` and `SpeechSynthesisService`
  are thin stub wrappers over that channel, and one is built per endpoint.
- **Every streaming RPC is private to one utterance or sentence.** `streaming_response_generator(...)` and
  `synthesize_online(...)` each start a new RPC. The request iterator and the response generator are
  created, consumed and closed on **one worker thread** and never shared or resumed across threads. Results
  cross to the event loop through an `asyncio.Queue` via `loop.call_soon_threadsafe`.
- **A dedicated bounded `ThreadPoolExecutor` per endpoint**, not `asyncio.to_thread`'s shared default pool:
  a blocking stream occupies a thread for the utterance's duration, and must not starve other
  `to_thread` users. Size is `speech.max_concurrent_streams`, default `2 × max_sessions` for TTS (one-sentence
  lookahead) and `max_sessions` for ASR. When the pool is saturated, work queues (it is not dropped) and a
  `speech_backpressure` event is logged with the wait time.
- **Cancellation:** barge-in cancels the asyncio side at once. The worker is stopped by closing the request
  iterator (ASR) or by `response_iterator.cancel()` on the underlying gRPC call (TTS), so a cancelled
  sentence frees its thread and its server-side stream promptly. A test asserts that no stream thread
  outlives its cancelled response.
- **Server-side limits are an empirical question.** nemo-speech.cpp and NVCF may cap concurrent
  streams below `max_sessions`. P4 includes a load check: N concurrent sessions × scripted turns against the
  real endpoint, recording per-stream latency and errors. `max_sessions` in the shipped configs is set from
  that result (and stated in the runbook), not assumed. Until measured, the default is
  `max_sessions: 8` and the eval profile uses τ³ `--max-concurrency 1`.

Tests use a fake `riva` stub that records which thread touched which stream, and assert: no stream object is
used by two threads; K concurrent sessions each receive only their own transcripts and audio; the pool bound
holds; cancellation releases threads.

---

## 14. Chat interface (V10)

`cli/voice_chat.py` is a stand-alone **Realtime client**. It uses the same wire protocol as τ³ and imports
nothing from `engine/` or `agent/`, so it can talk to this server, to the shared `/v1/realtime` gateway, or
to any Realtime endpoint.

```
uv run python -m prototypes.voice_frontend_backend_agent.cli.voice_chat \
    --url ws://localhost:8765/v1/realtime --io mic            # mic | wav | text
    [--tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS]   # client-executed tools (the τ³ path)
    [--instructions-file policy.md] [--format pcmu|pcm24k] [--show-events]
```

- `AudioIO` protocol with `MicSpeakerIO` (sounddevice, optional extra), `WavFileIO` (`--in a.wav --out
  reply.wav`, scripted turns separated by silence) and `TextIO` (sends `input_text` items, prints
  transcripts, plays nothing). Adding a new I/O means one class.
- `--tools` reuses the text prototype's `ToolSpec` lists. The client registers them in `session.update`,
  executes `function_call_arguments.done` locally, and sends `function_call_output` + `response.create`,
  which is exactly τ³'s flow.
- Rendering reuses the text prototype's `cli/ui.py` split: a `ChatUI` for presentation, with the Rich
  variant when available and plain text otherwise. Lines show user transcripts, agent transcripts, tool
  calls/outputs, and with `--show-events` the raw event types.
- `cli/tau2_replay.py` is a headless driver for smoke tests. It streams a WAV as 160-byte μ-law appends
  every 20 ms with continuous silence, simulates wall-clock gaps, answers tools from a JSON fixture, and
  asserts the §3 contract on the received events.

---

## 15. Serving in the existing container (V5)

No Dockerfile or Compose edits. The image already contains `src/` (and Compose bind-mounts `./src`
read-only), every dependency, and the `.env` credentials. The prototype server runs as a one-off container
of an existing service, on the same Compose network as `nemo-speech` and the LLM:

```bash
# 1. Bring up the example's recipe once (speech + LLM + the stock app)
docker compose --profile frontend-backend-agent/single-gpu up -d

# 2. Run the prototype server from the same image/network, listening on 7860 inside the container
#    so the image's existing /health healthcheck stays meaningful; publish it on host port 8765.
docker compose --profile frontend-backend-agent/single-gpu run --rm -d --name fba-voice \
  -p 8765:7860 -e PYTHONPATH=/app/src -e PIPELINE_TLS=false \
  frontend-backend-agent-single-gpu \
  uv run python -m prototypes.voice_frontend_backend_agent.server \
    --config src/prototypes/voice_frontend_backend_agent/config/voice_agent.yaml --port 7860
```

- Cloud speech: the `frontend-backend-agent` profile plus `--config …/profiles/cloud_speech.yaml`, with ASR
  and TTS through NVCF and `NVIDIA_API_KEY` from `.env`.
- Host-native: `PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.server --config …`,
  with `FBA_ASR_SERVER=localhost:50051 FBA_TTS_SERVER=localhost:50051` (the catalog's `server_override`,
  §6.1) or `asr.source: inline`. The loader never guesses hostnames.
- The server exposes `GET /health` (200 once ASR/TTS/LLM warm-up has passed). Startup fails fast with a
  clear message if a speech endpoint is unreachable.

The runbook will state that `-p` and `--name` belong to `docker compose run`, and that the command overrides
the service's default `src/server.py`. The stock app from step 1 can be left running (it serves a different
port) or skipped with `up -d nemo-speech <llm-service>`.

**τ³ side:**

```bash
# tau2-bench-smasurekar/.env
PINE_REALTIME_BASE_URL=ws://<host>:8765/v1/realtime
PINE_API_KEY=unused                # or FBA_VOICE_TOKEN when require_bearer: true
uv run tau2 run --domain airline --audio-native --audio-native-provider openai \
  --audio-native-model pine-nemotron-fba --speech-complexity control \
  --num-tasks 5 --num-trials 1 --max-concurrency 1 --max-steps-seconds 600 --verbose-logs
```

---

## 16. Observability

- `logging.event_log` JSONL, one line per event. It merges the text prototype's internal events
  (delegation, filler, backend tool calls, usage), routed by `session_id`, with voice events:
  `speech_started/stopped` (audio ms + wall), `asr_final` (latency from speech stop), `agent_turn`
  (frontend/backend latency, tokens, cost), `tts_first_audio` (latency), `response_done` (status, audio ms
  sent, heard ms), `barge_in` (heard vs total, truncated text), `tool_calls_out`, `tool_outputs_in`
  (latency), `filler`.
- A per-turn latency breakdown, `user_stop → asr_final → agent_done → first_audio_sent`, is logged on one
  line so τ³ latency results can be attributed to a component.
- Audio payloads are never logged. Tool outputs and transcripts are logged by default because this is a
  prototype for evaluation; `logging.redact_content: true` switches that off.

---

## 17. Tests (all offline, fakes only)

| # | Rule | Test |
|---|---|---|
| 1 | First frame is `session.created`; `session.update` → `session.updated`; no `error` for τ³'s exact payload | `test_tau2_contract.py::test_handshake_with_tau2_payload` |
| 2 | `audio/pcmu` in/out round-trips; G.711 tables match the ITU reference vectors | `test_g711.py` |
| 3 | 160-byte μ-law appends with continuous silence and multi-second wall-clock gaps produce **identical** turn boundaries to a gap-free stream | `test_segmenter.py::test_wall_clock_gaps_do_not_matter` |
| 4 | `speech_started.audio_start_ms` is on the cumulative input clock | `test_segmenter.py::test_audio_start_ms_clock` |
| 5 | Every assistant utterance has a fresh `item_id`; transcript deltas precede their audio; exactly one `response.done` per `response.created` | `test_response_writer.py` (property-style over generated sequences) |
| 6 | Tool round trip: `function_call_arguments.done` carries top-level `call_id`, `name`, `arguments`; outputs + `response.create` resume the backend; `call_id` equals the backend's tool-call id | `test_function_call_roundtrip.py` |
| 7 | Parallel tool calls → one response with N function_call items; resume only after all outputs (+ `response.create`) | `test_function_call_roundtrip.py::test_parallel` |
| 8 | Unknown `call_id` → `error`, session survives; missing output after timeout → synthesized error result | `test_function_call_roundtrip.py::test_bad_and_missing_outputs` |
| 9 | `filler.mode: log_only` → wire trace **equal after normalization** to the no-filler trace; timing record written. Normalization (`_fakes.normalize_trace`): drop `event_id`; replace each generated id (`item_…`, `resp_…`, `sess_…`, `call_…` from the provider fake) by an ordinal placeholder in first-seen order per id kind; drop wall-clock fields; keep audio-clock fields (`audio_start_ms`, `audio_end_ms`) exact, since they are deterministic under the fake clock; keep audio payloads (fake TTS is deterministic). Everything else must match byte-for-byte, including event order | `test_filler_policy.py::test_log_only_is_invisible` |
| 10 | `filler.mode: speak` → filler item first with its own `item_id`; skipped if the backend is faster than `speak_after_ms` | `test_filler_policy.py::test_speak` |
| 10b | Every delegation, including the session's first turn and cancelled or tool-call turns, emits exactly one filler timing record with the session/turn anchors on both clocks, and durations consistent under a fake clock | `test_filler_policy.py::test_timing_record_from_first_turn` |
| 11 | Barge-in while speaking → `speech_started`, generation cancelled, `response.done cancelled`, history truncated to the heard prefix + marker | `test_barge_in.py` |
| 11a | Confirmed speech cancels an active response **with nothing unplayed**: (i) TTS still synthesizing the first sentence, `sent_ms = 0`; (ii) sentence *k* fully heard while *k+1* synthesizes. In both, no later delta for the item is sent and `heard_text` excludes unsent sentences | `test_barge_in.py::test_cancels_generation_without_buffered_audio` |
| 11b | `heard_ms` is capped by `sent_ms` at every step (a TTS stall does not advance it past delivered audio) | `test_playback.py::test_cursor_capped_by_sent_audio` |
| 11c | Paired delegated turn interrupted → **both** the `call_backend` tool message and the assistant continuation hold the truncated text; no message anywhere in the resulting `SessionState` contains the unheard suffix. Direct-answer and `backend_only` variants covered; a shape/text mismatch raises | `test_history_repair.py` |
| 12 | Speech during THINKING before any tool call → cancel and merge transcripts; after a tool call left → queued, never cancelled | `test_barge_in.py::test_while_thinking` |
| 13 | Per τ³ domain fixture: exact frontend/backend prompt snapshots for both `apply_to` settings; default frontend lists every client tool as a capability, excludes the policy, includes "when unsure, delegate"; backend has policy + addendum; no unrendered placeholder; scripted in-scope request delegates | `test_instructions.py` |
| 14 | `backend_only`: stateful backend history across turns, no frontend client built, full contract test passes | `test_backend_only.py` |
| 15 | No greeting; the first server audio follows the first user turn | `test_tau2_contract.py::test_no_greeting` |
| 16 | Per-session agents are isolated; two concurrent sessions with different tools/instructions never cross | `test_no_shared_mutation.py` |
| 17 | `wire/`, `agent/`, `audio/` and the engine state machines import no `pipecat`/`riva`/`fastapi` | `test_config.py::test_layering` (AST walk) |
| 18 | The text prototype's public API is used, and no text-prototype file changes | `test_config.py::test_text_prototype_untouched` (import surface only) + review |
| 19 | End to end over a real FastAPI WebSocket with fake speech and a scripted agent: a τ²-shaped client completes a 3-turn conversation with one tool round trip | `test_ws_end_to_end.py` |
| 20 | Every shipped config (base + each profile) loads from the repo root **and** from an unrelated cwd with identical resolved `[in-path]`s; referenced files and catalog keys exist; merge rule table (§6.1 step 4) holds; `extends` chains resolve; cycle / type change / unknown key / `tools.source: merge` fail with file+key in the message | `test_config.py` |
| 21 | `response.create` arriving before the tool-call response's `response.done` is queued, not rejected; `truncate` with an out-of-range `audio_end_ms` is clamped, not an error | `test_tau2_contract.py::test_early_response_create`, `::test_truncate_clamped` |
| 22 | Unknown `session.update` keys (`reasoning`, `noise_reduction`, future fields) are tolerated; beta-only fields and format strings → non-fatal `error` naming the GA replacement, not applied; no emitted event `type` is a beta name; `response.done` always has `usage`; `semantic_vad` → effective `server_vad` echoed under `x_nvidia_effective` | `test_session_view.py`, `test_server_events.py` |

---

## 18. Phases

| Phase | Deliverable | Gate |
|---|---|---|
| **P1 Wire** | `wire/*`, `audio/g711.py`, `config.py`, session view with τ³ payload, a minimal FastAPI app serving the handshake | rules 1, 2, 5, 20, 22 **+ τ³ gate A** |
| **P2 Engine (fake speech)** | segmenter, playback, turn manager, response writer, `AgentPort` + `FakeAgentPort` | rules 3, 4, 11, 12, 15 **+ τ³ gate B** |
| **P3 Agent adapter** | runner over `assemble_agent`, tools mapping, instructions, filler tap, history repair, `prompts.voice.yaml` | rules 6–10, 13, 14, 16, 18 |
| **P4 Speech adapters + server** | Silero VAD, Riva ASR/TTS, catalog resolver, FastAPI app, `/health`, warm-up | rule 19; manual run against `nemo-speech` |
| **P5 Chat client** | `voice_chat.py` (text, wav, mic), client tools, `tau2_replay.py` | manual conversation in both modes, filler `speak` and `log_only` |
| **P6 τ³ bring-up** | `mock` domain 1 task → airline 5 tasks → retail; both modes | tau2 runs end `AGENT_STOP`/`USER_STOP`; scored transcripts non-empty; tool calls recorded |
| **P7 Docs + hardening** | package README, runbook `misc/prototypes/voice-frontend-backend-agent-runbook.md`, timeouts, max_sessions | validation §19 clean |

### 18.0 Early validation against the real τ³ client (not only at P6)

The offline contract tests (`test_tau2_contract.py`) encode *our reading* of τ³. Two gates run the **real
tau2 adapter code** early, so a misreading surfaces in P1/P2 and not after the speech and agent work is
built on it. Both run from the tau2 checkout (its own venv) against the prototype server, with
`PINE_REALTIME_BASE_URL=ws://localhost:8765/v1/realtime`.

- **Gate A (end of P1): handshake and wire.** A ~40-line script in
  `cli/tau2_gates/gate_a_handshake.py`, run with tau2's interpreter, imports tau2's
  `OpenAIRealtimeProvider` (`src/tau2/voice/audio_native/openai/provider.py`) and calls its real `connect()`
  (tau2's exact `session.update`, with the tools and instructions of the `mock` domain), then drives
  `send_audio` with 2 s of μ-law silence and `receive_events`. Pass criteria: no exception; `session.id`
  read; no `error`. The server runs with a **scripted engine stub** that emits one canned response
  (transcript + tone audio + one function call) after the first append burst, so tau2's
  `parse_realtime_event` is exercised on every event type we emit and the function call is parsed into a
  `ToolCall` with the right `call_id`/`name`/`arguments`.
- **Gate B (end of P2): full τ³ run without real speech or LLM.** The real engine (audio clock, energy
  VAD, segmenter, turn manager, response writer, barge-in) runs with speech/agent stand-ins: `StubRecognizer`
  returns `"utterance N"`, `ToneSynthesizer` makes audio of length ∝ text, and a `ScriptedAgentPort` calls
  one read-only mock-domain tool on turn 1 and then answers, ending by calling `transfer_to_human_agents`
  on turn 3. Command:
  `tau2 run --domain mock --audio-native --audio-native-provider openai --audio-native-model pine-gateb
  --speech-complexity control --num-tasks 1 --num-trials 1 --max-concurrency 1 --max-steps-seconds 120`.
  Pass criteria, checked by `cli/tau2_gates/check_gate_b.py` on `sim_0.json`: termination is
  `AGENT_STOP`; the tool call and its result appear on the ticks; the agent's scored transcript is
  non-empty and never contains text from an interrupted item past the barge-in point; `audio_start_ms`
  values are monotonic on the input clock; there are no tau2 warnings about unknown or malformed events.
  Then repeat with `--speech-complexity regular`, so the user simulator's interruptions exercise barge-in
  against the real adapter.
- Both gates are **manual runs** (they need the tau2 environment and, for B, ElevenLabs keys for the
  simulated user's voice). They are reported with their artifacts, never as automated passes. Any
  divergence found is fixed in `wire/`/`engine/` *and* encoded as a new offline test in
  `test_tau2_contract.py` before the phase closes.

### 18.1 Deferred: mixed internal/external tool execution (`tools.source: merge`)

Not needed for τ³, where the client executes every tool, or for the live demo (`config`). It would need an
execution-routing seam in the text prototype, a change to that package's public contract, so it gets its
own reviewed change rather than riding along here. The shape, recorded so the decision is reversible:

- `ToolSpec.execution: "internal" | "external" | None` (`None` = inherit the global
  `backend.tools.execution`);
- in `FrontendBackendAgent._drive`, split each `NeedsTools` batch: run the internal subset through
  `InternalToolDriver`, and surface the external subset as `AgentTurn.tool_calls` with `PendingTurn` also
  holding the internal results already computed; `send_tool_results()` merges both sets back in emission
  order before resuming;
- new text-prototype rules: a mixed batch resumes only when every external id is answered, internal
  results are never re-executed on resume, and result order matches emission order.

Until then the voice package has no mixed-execution code path.

---

## 19. Validation and documentation

```bash
uvx ruff@0.15.6 check .
uvx ruff@0.15.6 format --check .
uv sync --dev
uv run pytest tests/ -v
```

Client `npm` checks do not apply: there is no browser-client change. GPU/speech/LLM-backed runs (P4–P6)
need `nemo-speech`, an LLM endpoint, `NVIDIA_API_KEY`, and ElevenLabs keys on the τ³ side. They are
reported as manual runs with their artifacts (`sim_*.json`, `both.wav`, the event log), never as automated
passes.

**Documentation impact.** `src/prototypes/` is not a shipped surface: there is no registry entry, Compose
profile, `.env` key or client change. Two user-visible additions remain, and both go in the package README
and runbook: the `prototypes-voice` optional extra, and the `docker compose run` recipe. Per `AGENTS.md`, a
documentation subagent reviews at implementation time whether `docs/how-to/use-realtime-gateway.md` needs a
pointer ("for client tools / τ³, see the prototype") and records the result in the PR's Documentation
Writer Review receipt.

---

## 20. Risks and open questions

1. **ASR on 8 kHz telephony audio.** Upsampling μ-law to 16 kHz loses the high band, so WER will be higher
   than on wideband input. We measure it in P6 (τ³ records both audio sides) before tuning.
   `speech-complexity control` first.
2. **Latency budget.** A paired turn costs ASR finalization + frontend + backend (with reasoning) + first
   TTS chunk. τ³ counts latency in ticks. Filler would hide it in live use but is silenced in eval by
   design. The latency log (§16) exists to attribute it. Backend reasoning budget is the first knob.
3. **Frontend without policy (D3).** Mitigated by tool-derived capabilities and a delegate-when-unsure rule
   (§11), and covered by per-domain exact-prompt tests. The residual risk is model behaviour, which P6
   measures against `apply_to: [backend, frontend]` per domain.
4. **Barge-in semantics under τ³.** τ³'s user simulator interrupts in `regular` complexity. `truncate_heard`
   mirrors its scoring rule, but echo/backchannel ("uh-huh") will cancel responses. `min_speech_ms` is the
   mitigation; a backchannel classifier is out of scope.
5. **Graduation path.** If the prototype proves out, `wire/` (client tools, G.711, VAD tuning,
   function-call items) is the part to lift into `src/realtime/` behind a capability flag. That is a
   product change with docs, outside this prototype.
6. **`seed_history_with_client_greeting`.** It inserts tau2's text-only greeting as an assistant message so
   the model's history matches what the user believes was said. It is τ³-specific and can be disabled.

---

## Appendix A — OpenAI Realtime protocol reference used by this server

Sources: openai-python 2.20 types (`openai/types/realtime/` GA, `openai/types/beta/realtime/` beta, in the
tau2 venv), Pipecat's GA Realtime client (`pipecat/services/openai/realtime/`), developers.openai.com Realtime
guides, and the tau2 client (`src/tau2/voice/audio_native/openai/`). **τ³'s client speaks GA only.** It
parses no beta names, and it needs `name` on `response.function_call_arguments.done`, a field that exists
only in GA.

### A.1 Connection

- `ws(s)://host/v1/realtime?model=<m>`, `Authorization: Bearer <key>`. GA sends no `OpenAI-Beta` header;
  beta sends `OpenAI-Beta: realtime=v1`. Browsers negotiate subprotocols (`realtime`,
  `openai-insecure-api-key.<k>`, `openai-beta.realtime-v1`). **The server must accept any `model`, must
  not require a subprotocol, and echoes `realtime` if offered.** It reuses the secret-safe selection logic
  of `src/realtime/gateway.py`.
- First server frame: `session.created {event_id, session}`. The server never closes the socket on its own
  initiative except for a fatal protocol error or shutdown, because τ³ treats a close as a failed run.

### A.2 Session object (GA ↔ beta)

The beta column is reference only: this server accepts and emits GA exclusively (§7.4).

| GA | beta |
|---|---|
| `type:"realtime"` (required on update) | — |
| `output_modalities: ["audio"] \| ["text"]` | `modalities: ["text","audio"]` |
| `audio.input.format {type:"audio/pcm",rate:24000} \| {type:"audio/pcmu"} \| {type:"audio/pcma"}` | `input_audio_format: pcm16 \| g711_ulaw \| g711_alaw` |
| `audio.output.format`, `audio.output.voice`, `audio.output.speed` | `output_audio_format`, `voice`, `speed` |
| `audio.input.turn_detection` | `turn_detection` |
| `audio.input.transcription {model,language,prompt}` | `input_audio_transcription` |
| `audio.input.noise_reduction` | `input_audio_noise_reduction` |
| — (removed) | `temperature` |
| `max_output_tokens` (int \| `"inf"`) | `max_response_output_tokens` |
| `tools: [{type:"function",name,description,parameters}]` (flat, no `function:` wrapper), `tool_choice: auto\|none\|required\|{type:"function",name}` | same tools; `tool_choice` string |

`turn_detection`: `{type:"server_vad", threshold, prefix_padding_ms, silence_duration_ms, idle_timeout_ms,
create_response, interrupt_response}` | `{type:"semantic_vad", eagerness, create_response,
interrupt_response}` | `null` (manual). `create_response` and `interrupt_response` default to true. This
server honours `threshold`, `prefix_padding_ms`, `silence_duration_ms`, `create_response` and
`interrupt_response`; approximates `semantic_vad` with silence-based `server_vad` (eagerness → silence
duration: low 800, medium/auto 500, high 300 ms; see §7.5 for what that does not do); and logs and ignores `idle_timeout_ms`, `noise_reduction`, `transcription.model`,
`reasoning` and any unknown key. Unknown keys are **tolerated**, never an error.

### A.3 Client events

`session.update`, `input_audio_buffer.append {audio}` (≤ 15 MB, no ack), `input_audio_buffer.commit`
(→ `committed`; empty → `input_audio_buffer_commit_empty`), `input_audio_buffer.clear` (→ `cleared`),
`conversation.item.create {previous_item_id?, item}` (message user/assistant/system with `input_text`/
`input_audio`/`output_text` parts; `function_call`; `function_call_output {call_id, output}`),
`conversation.item.truncate {item_id, content_index, audio_end_ms}` (→ `truncated`),
`conversation.item.delete` (→ `deleted`), `conversation.item.retrieve` (→ `retrieved`),
`response.create {response?}` (optional overrides: `instructions`, `tools`, `tool_choice`,
`output_modalities`, `audio.output`, `max_output_tokens`, `metadata`; out-of-band `conversation:"none"` +
`input`), `response.cancel {response_id?}`, and `output_audio_buffer.clear` (WebRTC/SIP). `event_id` is
optional on all of them and is echoed in `error.event_id`.

**τ³ sends only:** `session.update`, `input_audio_buffer.append`, `conversation.item.create`
(function_call_output), `response.create` (bare, after the last output of a batch), and
`conversation.item.truncate`. It never sends commit, clear, cancel or delete, which is why server-side VAD,
auto-response and auto-cancel are mandatory.

### A.4 Server events (GA; beta name in parentheses)

`session.created`, `session.updated`, `error {error:{type, code?, message, param?, event_id?}}`,
`input_audio_buffer.speech_started {audio_start_ms, item_id}`, `…speech_stopped {audio_end_ms, item_id}`,
`…committed {item_id, previous_item_id}`, `…cleared`, `conversation.item.added` + `conversation.item.done`
(`conversation.item.created`), `conversation.item.input_audio_transcription.delta|completed|failed`,
`conversation.item.truncated|deleted`, `response.created`, `response.output_item.added|done`,
`response.content_part.added|done` (`part.type` `audio`/`text`), `response.output_audio.delta|done`
(`response.audio.*`), `response.output_audio_transcript.delta|done` (`response.audio_transcript.*`),
`response.output_text.delta|done` (`response.text.*`), `response.function_call_arguments.delta|done`
(`done` has **no `name` in beta**), `response.done {response:{id, status, status_details, output, usage}}`,
and `rate_limits.updated`. Not emitted by this server: WebRTC/SIP `output_audio_buffer.*`, `mcp_*`, DTMF,
and `…timeout_triggered`.

Required fields that strict SDK models expect on delta/response events, which this server always sets:
`event_id`, `response_id`, `item_id`, `output_index`, `content_index`, and `call_id` on function-call
events. Item content types: `output_audio {transcript}` for assistant audio items, `input_audio
{transcript}` for user audio items.

### A.5 Function calling

Server, one response: `response.created` → `output_item.added {type:function_call, name, call_id,
arguments:"", status:in_progress}` → `function_call_arguments.delta*` → `function_call_arguments.done
{item_id, call_id, name, arguments, output_index, response_id}` → `output_item.done` → `response.done`. A
message item may precede the call in the same response (used by `filler.mode: speak`). Client:
`conversation.item.create function_call_output` for each call → server acks → `response.create` → new
response. **The server does not auto-respond to a `function_call_output`** unless `protocol.resume_on:
last_function_output` is set for clients that never send `response.create`.

### A.6 Audio

| Format | Rate | Bytes/s | 20 ms | 200 ms (τ³ tick) |
|---|---|---|---|---|
| `audio/pcm` / `pcm16` (s16le mono) | 24 kHz (GA fixes 24k; this server also accepts 8/16/48k) | 48 000 | 960 B | 9 600 B |
| `audio/pcmu` / `g711_ulaw` | 8 kHz | 8 000 | 160 B | 1 600 B |
| `audio/pcma` / `g711_alaw` | 8 kHz | 8 000 | 160 B | 1 600 B |

Audio is raw and base64-encoded, with no WAV header. Output delta size is free; this server uses
`audio.output_chunk_ms` (100 ms default).

### A.7 Interruption

With VAD and `interrupt_response: true`: `speech_started` → the server cancels the response itself (closes
parts/items as `incomplete`, `response.done cancelled / turn_detected`) → the client may `truncate` →
`conversation.item.truncated`. OpenAI rejects an `audio_end_ms` beyond the item's audio; this server clamps
it instead (§7.1). `response.cancel` is the manual equivalent (`client_cancelled`).

### A.8 IDs

`event_…`, `sess_…`, `item_…`, `resp_…`, `call_…`: opaque strings that no client validates. The backend's
`ToolCall.id` is used verbatim as `call_id` and must round-trip exactly. When a provider omits one, the text
prototype generates `call_<hex>`, which already matches.

---

## Revision log

### Revision 7 — identifier normalization

A new `normalization/` package adds two hooks in `agent/runner.py`, both off by default. The transcript hook
writes spoken identifiers in written form for the agent only (the wire keeps the raw ASR text). The
tool-argument hook (requires `tools.source: client`) canonicalizes configured tool arguments and answers a
malformed call, or a repeat of a permanently failed one, with a local tool result instead of sending it. The
voice layer adds the `normalization` config section, three prompt keys, `profiles/tau3_eval_normalization.yaml`,
four event kinds (content fields redacted by `logging.redact_content`), `session_start.normalization`, and the
offline `cli/normalization_replay.py`.
Design, rules and tests: [`voice-frontend-backend-agent-normalization-plan.md`](voice-frontend-backend-agent-normalization-plan.md).

### Revision 6 — backend conversation history

The paired backend is stateless **by default**. The text prototype's new `backend.conversation_history` flag
(off by default) gives it the conversation history. The voice layer wires it through `agent.overrides`, adds
`profiles/tau3_eval_backend_history.yaml`, pins the flag off in `tau3_eval.yaml` and `backend_only.yaml`,
repairs interrupted answers in both histories (both or neither), and reports the arm in `session_start`.
Design, rules and tests: [`frontend-backend-agent-backend-history-plan.md`](frontend-backend-agent-backend-history-plan.md).

### Revision 5 — implementation notes

Where the implementation differs from the text above, and why:

| Plan text | Implemented | Reason |
|---|---|---|
| Reuse `realtime.events` / `realtime.conversation` helpers (§2.2) | Event names and id helpers are defined in `wire/` | `src/realtime/__init__.py` eagerly imports the Pipecat gateway, so importing any `realtime.*` module would pull Pipecat into `wire/` and break the §5 layering rule |
| Resampling via `realtime.audio.AudioResampler` (§13.1) | `audio/resample.py` wraps `soxr.ResampleStream` directly, in float32 | Pipecat's stream resampler clears its state after 0.2 s of *wall-clock* inactivity (tau2 pauses for seconds). Output sentences also need an explicit flush. libsoxr's int16 path dithers with run-dependent state, so the stream runs in float32 and rounds itself. `soxr` is declared in `prototypes-voice` |
| Test files `tests/unit/prototypes/voice/test_config.py`, `_fakes.py`, ... (§5) | `test_voice_*.py` and `_voice_fakes.py` | pytest imports test modules by basename (no `__init__.py`), and the text prototype's tests already own `test_config.py` and `_fakes.py` |
| `FillerTap.offer` only schedules via `call_soon_threadsafe` (§9.1) | On the session's loop the handler runs inline (it records and schedules, never awaits); other threads use `call_soon_threadsafe` | With a fast backend the delegation finished before a scheduled callback ran, and its timing record was lost ("no delegation goes unrecorded") |
| Beta format strings listed as accepted "legacy names" (§13.1) | Rejected, per §7.4 | §7.4 (revision 4) supersedes the older §13.1 wording |
| LLM warm-up at startup (§15) | Only ASR and TTS are probed | An LLM probe costs tokens on every start; LLM failures surface on the first turn as an `error` event |
| Filler timing record per delegation (§9.2) | Also created from the `delegation` event | A frontend that delegates without `filler_text` still gets its record |

Validation performed: the full repository suite (`uv run pytest tests/`), ruff check and format on the new
code, a live stub-server run with `cli/tau2_replay.py` ("tau2 contract: OK"), and **τ³ Gate A** with tau2's
real `OpenAIRealtimeProvider` ("GATE A PASSED"). The `session.update` fixtures for mock, airline, retail and
telecom were captured from tau2 (banking was skipped: `rank_bm25` is missing from the tau2 venv). Not run,
because they need GPUs, nemo-speech, LLM endpoints and tau2 user-simulator keys: Gate B, P4's real-speech
load check, and P6.


### Revision 4 — review round 2

| Review point | Action needed? | Resolution |
|---|---|---|
| GA vs beta | Yes | §7.4 — GA only, no dialect switch; `emit_with_aliases` not reused; beta-only session fields/format strings → non-fatal error naming the GA field, not applied; unknown GA keys still tolerated; rule 22 |
| Cloud/NVCF auth | Yes | §13.5 — per-endpoint SSL (`is_nvcf`), `function-id` + `Bearer` metadata, `riva.client.Auth` construction matching Pipecat 1.7; fail-fast validation (no `"not-needed"` key for NVCF), redaction, startup probe; local nemo-speech unchanged |
| Dependency declarations | Yes (riva, websockets and numpy were only in `dev`; fastapi and uvicorn only transitive) | §5.1 — `prototypes-voice` extra declaring every direct import, `prototypes-voice-mic` for `sounddevice`; PortAudio install documented; image unaffected; declared-imports test |
| Earlier τ³ validation | Yes | §18.0 — Gate A (end of P1: tau2's real provider handshake + event parsing against a scripted stub) and Gate B (end of P2: full `tau2 run --domain mock` with the real engine and stub speech/agent, then `regular` complexity) |
| Frontend prompt visibility | Yes: an empty capability list plus *Unsupported* mode could refuse domain requests | §11 — policy stays backend-only (D3) but the frontend gets tool-derived capabilities and a delegate-when-unsure rule; per-domain verbatim τ³ fixtures with exact-prompt snapshots for both `apply_to` settings; P6 A/B measurement; rule 13 |
| Semantic VAD | Yes (documentation) | §7.5 — stated as a silence-based approximation, with the eagerness table, what it does not do, per-session warning, effective params echoed under `x_nvidia_effective`, README note |
| Riva concurrency | Yes | §13.5 — one channel per endpoint, one thread-confined RPC per utterance/sentence, dedicated bounded executors, cancellation that frees threads, fake-stub thread-affinity tests, P4 load check sets `max_sessions` |
| Filler execution | Yes | §9.1 — non-blocking sink → scheduled `FillerTask` racing the agent task, filler TTS concurrent with the backend, answer queued behind the filler item; rule 9 trace normalization defined |

### Revision 3 — review round 1

| Review point | Resolution |
|---|---|
| Paired barge-in left the unheard answer in the `call_backend` tool result | §8.3 — `history_repair` rewrites **every** stored copy: in paired mode both the tool message and the assistant continuation, located structurally and verified by exact text match (fails loudly on mismatch); direct-answer and `backend_only` paths tabulated; heard prefix + configurable `interruption_marker`; rule 11c |
| Barge-in only fired with unplayed audio buffered | §8.3 — `ResponseProgress` tracks `generating`, `sent_ms` + per-sentence spans, and `heard_ms` separately; `response_active = generating or heard_ms < sent_ms`; confirmed speech cancels generation even with nothing buffered; the cursor is capped by delivered audio at every step; rules 11a, 11b |
| `tools.source: merge` not implementable through the text prototype's public API | §10 — one execution mode per session (`client` → external, `config` → internal); `merge` rejected by the loader; the text-prototype seam it would need is specified and deferred in §18.1 |
| Invalid `<voice pkg>` path; undefined relative-path and profile semantics | §6 — real relative defaults, path-typed keys marked `[in-path]`/`[out-path]`; §6.1 — file-relative input paths resolved before merge, cwd-relative outputs, `extends` profile chains, an exact deep-merge table (shared with `agent.overrides`), text config built with its own `source_dir`, single validation pass; every shipped file listed with the keys it sets; rule 20 loads each from two working directories |

### Revision 2 — decisions confirmed

D1–D5 confirmed as recommended; filler timing recorded from the first turn with session/turn anchors on
wall and audio clocks (§9.2, rule 10b).
