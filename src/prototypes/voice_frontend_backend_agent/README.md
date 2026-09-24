# Voice Frontend/Backend Agent (prototype)

An **OpenAI Realtime (GA) WebSocket server** that wraps the text prototype
([`text_frontend_backend_agent`](../text_frontend_backend_agent/README.md)) with nemo-speech ASR and TTS.
tau3-bench's audio-native `openai` client (`pine-` models) can drive it exactly as it drives `gpt-realtime`.

- Plan: [`misc/prototypes/voice-frontend-backend-agent-prototype-plan.md`](../../../misc/prototypes/voice-frontend-backend-agent-prototype-plan.md)
- Docker runbook: [`misc/prototypes/voice-frontend-backend-agent-runbook.md`](../../../misc/prototypes/voice-frontend-backend-agent-runbook.md)

This is a prototype. It is not registered in `examples_registry.yaml` and has no Compose profile of its own.

## What it does

```
client audio ─► decode (pcm / pcmu / pcma) ─► audio clock ─► VAD ─► segmenter ─► Riva ASR (one stream per utterance)
                                                                                      │ transcript
client ◄─ response.* events ◄─ encode ◄─ resample ◄─ Riva TTS (one RPC per sentence) ◄┴─ FrontendBackendAgent (text prototype)
                                                                                           │ backend tool calls
client ◄─ function_call items ─────────────────────────────────────────────────────────────┘
client ─► function_call_output + response.create ─► send_tool_results() ─► ...
```

- **Paired mode (default).** Frontend LLM plus backend LLM. The frontend's `filler_text` is **silenced and
  time-logged** (`filler.mode: log_only`); `filler.mode: speak` makes it audible, concurrently with the backend.
- **Backend-only mode.** `agent.mode: backend_only` (profile `profiles/backend_only.yaml`): one stateful backend
  with its own history and no frontend LLM.
- **External tools.** Tools from `session.update.tools` are executed by the **client**. They surface as Realtime
  `function_call` items with the backend's own `call_id`. The backend resumes on `function_call_output` plus
  `response.create`.
- **External prompt.** `session.update.instructions` becomes the backend's `{domain_policy}` by default, with a
  cascade addendum. The frontend gets a capability list built from the client's tools.
- **Audio clock.** All turn timing counts input samples, not wall time, so tau2's wall-clock pauses change nothing.
- **Barge-in.** Confirmed user speech while a response is active cancels generation, even when nothing
  unplayed is buffered. Every stored copy of the answer is cut to what the user heard, plus
  `" [interrupted by the user]"`.

## Run it (host-native, from the repository root)

```bash
uv sync --dev                       # includes the prototypes-voice extra
# LLMs: the text agent.yaml reads NVIDIA_API_KEY and optional {FRONTEND,BACKEND}_LLM_{MODEL,BASE_URL}
# Speech: a nemo-speech server reachable on localhost:50051
FBA_ASR_SERVER=localhost:50051 FBA_TTS_SERVER=localhost:50051 PYTHONPATH=src \
  uv run python -m prototypes.voice_frontend_backend_agent.server \
    --config src/prototypes/voice_frontend_backend_agent/config/voice_agent.yaml
curl -s localhost:8765/health
```

No GPU, speech server or LLM needed (energy VAD, stub ASR, tone TTS, scripted agent):

```bash
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.server --stub-speech --stub-agent scripted
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.tau2_replay --pause-s 1
```

For the container, follow the [runbook](../../../misc/prototypes/voice-frontend-backend-agent-runbook.md).

## Chat client

`cli/voice_chat.py` is an independent Realtime client. It imports nothing from `engine/` or `agent/`.

```bash
# typed turns, client-executed demo tools (the tau3 flow, run locally)
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.voice_chat \
  --url ws://localhost:8765/v1/realtime --io text \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS
# scripted WAV turns (16-bit WAV, any rate); the agent's audio is written to reply.wav
... voice_chat --io wav --in turn1.wav --in turn2.wav --out reply.wav --format pcmu
# microphone and speaker (workstation only)
sudo apt install libportaudio2          # macOS: brew install portaudio
uv sync --dev --extra prototypes-voice-mic
... voice_chat --io mic
```

Filler that the server generates but does not speak (`filler.mode: log_only`) is shown in gray, marked
`(silent)`; `--hide-filler` turns this off. `--show-events` prints every event type (never audio). `--instructions-file policy.md` sends a policy as
`session.update.instructions`.

## Configuration

`config/voice_agent.yaml` is the single entry point. Profiles in `config/profiles/` use `extends:`.

| Profile | Changes |
|---|---|
| `tau3_eval.yaml` | pins every eval-relevant key to its base value |
| `backend_only.yaml` | `agent.overrides.agent.mode: backend_only` |
| `cloud_speech.yaml` | ASR and TTS through NVCF (`NVIDIA_API_KEY` required, checked at load time) |
| `live_demo.yaml` | audible filler, greeting, internal demo tools, real-time output pacing |

Loading rules:

- `${VAR:-default}` is resolved per file.
- Input paths resolve against the file that declares them; output paths resolve against the working directory.
- Mappings deep-merge; lists and scalars replace; `null` resets a key to its default.
- Unknown keys are errors.
- The text agent's `agent.yaml` is referenced, not copied. `agent.overrides` is deep-merged into it.

Environment knobs:

| Variable | Default | Effect |
|---|---|---|
| `FBA_VOICE_PORT` | `8765` | server port |
| `FBA_ASR_SERVER`, `FBA_TTS_SERVER` | catalog | replace the catalog server, e.g. `localhost:50051` host-native |
| `FBA_VOICE_EVENT_LOG` | off | JSONL event log (voice events, the text agent's internal events, timing). Each `agent_turn_done` has `step` (`respond`/`resume`) and per-role `frontend`/`backend` usage: `calls`, `prompt_tokens`, `completion_tokens`, `cached_tokens`, `total_tokens`, `latency_ms` |
| `FBA_FILLER_LOG` | off | JSONL filler timing records (always also in loguru and the event log) |
| `FBA_VOICE_PROMPTS` | `prompts.voice.yaml` | voice prompt catalog |
| `FBA_VOICE_TOKEN` | empty | bearer token when `server.require_bearer: true` |

## Protocol differences from OpenAI

| Topic | This server |
|---|---|
| Dialect | GA only. A `session.update` with beta fields (`modalities`, `input_audio_format`, `pcm16`, and so on) is rejected with an error naming the GA field, and nothing is applied |
| `semantic_vad` | **Approximated** by silence-based `server_vad` (eagerness high/medium/auto/low → silence 300/500/500/800 ms). It does not model semantic completeness. `session.updated` shows the effective parameters under `audio.input.turn_detection.x_nvidia_effective` |
| Ignored fields | `model`, `noise_reduction`, `transcription.model`, `reasoning`, `idle_timeout_ms` (logged) |
| `response.create` overrides | `instructions` / `tools` per response: `error unsupported_response_override` |
| `conversation.item.create` | user `input_text` messages and `function_call_output` only |
| `truncate` | `audio_end_ms` is clamped, never rejected. History uses the server's own playout estimate |
| `x_nvidia.filler` | non-standard event carrying unspoken filler text; sent only when the WebSocket URL has `?x_nvidia_filler=1` (`voice_chat` sets it, tau2 does not) |
| Greeting | none by default (tau2 injects its own); `protocol.greeting` enables one |
| Tools | `tools.source: client` (external) or `config` (internal); no mixing (plan §18.1) |
| Audio | `audio/pcm` 8/16/24/48 kHz, `audio/pcmu`, `audio/pcma` |

## Filler timing log

Every delegation writes one `filler` record from the session's first turn, in both modes. Each record has
the filler text and anchors on two clocks: `session_start_wall`, and `turn_start`/`turn_end` as wall time
plus input-audio ms. Latencies: `asr_final`, `filler_ready` (since session start, turn start and turn end,
plus frontend latency), `backend_done`, `first_answer_audio`, `filler_audio_start`, `would_have_spoken`,
`spoken`, and `outcome` (`answer`, `tool_calls`, `cancelled` or `error`). Set `FBA_FILLER_LOG` to also write
a JSONL file.

## Layout

| Directory | Contents |
|---|---|
| `wire/` | Realtime protocol only: parsing, session view, event builders, single-writer queue, response ordering |
| `audio/` | G.711 tables (no `audioop`), formats, a sample-driven soxr resampler |
| `speech/` | ports plus Riva ASR/TTS, Silero/energy VAD, the catalog resolver, stubs |
| `engine/` | input path, segmenter, output path, playback tracker, turn manager, session |
| `agent/` | runner over `assemble_agent`, tool mapping, instructions, filler tap, history repair, sinks |
| `cli/` | `voice_chat`, `tau2_replay`, and `tau2_gates/` (manual tau3 gates, run with tau2's interpreter) |

`wire/`, `agent/`, `audio/` and the engine state machines import no Pipecat, Riva or FastAPI (a test enforces
this). The text prototype is imported and never modified.

## Tests

```bash
uv run pytest tests/unit/prototypes/voice -v
```

Tests run offline with fakes. The `session.update` fixtures are captured verbatim from tau2
(`cli/tau2_gates/capture_session_updates.py`). Prompt snapshots are in
`tests/unit/prototypes/voice/fixtures/prompt_snapshots`; regenerate them after a reviewed prompt change with
`UPDATE_PROMPT_SNAPSHOTS=1`. Test files are named `test_voice_*.py` because pytest imports test modules by
basename.
