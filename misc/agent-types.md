# Agent Types in the Nemotron Voice Agent Repo

A survey of every agent pattern shipped in this repository. "Agent" here means two
distinct things, so they are separated below:

1. **Example agents** — the five selectable voice-agent examples under `src/examples/`,
   each registered in [`examples_registry.yaml`](../examples_registry.yaml) and exposed
   as a Docker Compose profile. These are the runnable voice agents.
2. **Internal agents / subagents** — the roles *inside* a single example (Speaker,
   Thinker, Media Analyzer, …) that make up the multi-agent examples.

There is also a third, unrelated category — **Claude Code skills** under `skills/` — which
are developer-workflow helpers, not runtime voice agents.

---

## 1. Example agents (runnable voice agents)

| # | Registry key | Label | Source | Architecture | Multi-agent? |
|---|---|---|---|---|---|
| 1 | `generic-assistant` | Generic Assistant | `src/examples/generic/` | Cascaded ASR → LLM → TTS | No |
| 2 | `multilingual-assistant` | Multilingual Assistant | `src/examples/multilingual/` | Cascaded, language locked per session | No |
| 3 | `omni-assistant` | Nemotron Omni Assistant | `src/examples/omni_assistant/` | Omni (ASR+LLM fused) → TTS | No |
| 4 | `omni-assistant-subagents` | Nemotron Omni Assistant Subagents | `src/examples/omni_assistant_subagents/` | Omni + `pipecat.workers` multi-agent | Yes — parallel/specialist |
| 5 | `frontend-backend-agent` | Frontend Backend Agent | `src/examples/frontend_backend_agent/` | Talker LLM + Thinker backend agent | Yes — delegation/hierarchical |

The registry's `selection:` field controls which of these the UI selector exposes
(`all`, or lock to one). `EXAMPLE_SELECTION` overrides it at runtime.

---

### 1.1 Generic Assistant (`generic-assistant`)

The baseline. English-only cascaded pipeline keeping ASR, LLM, tools, and TTS as
separate Pipecat services: `NvidiaSTTService` → `NvidiaLLMService` (with function
calling) → `NvidiaTTSService`.

- **Entry point:** `examples.generic.pipeline:bot`
- **Slots:** `llm`, `asr`, `tts`
- **Defaults:** `nemotron-lightning` LLM, `nemotron-asr-streaming-english` ASR, `magpie-multilingual-tts` TTS
- **Agent shape:** single LLM agent with a flat tool belt. Tools are declared in
  `generic/tools.yaml` and bound to handlers in `generic/tool_handlers.py`;
  `generic/tools.py` only admits a tool when the YAML entry, its `function.name`,
  and a registered handler all agree.
- **Shipped tools:** `convert_currency`, `calculate_bmi`, `get_current_date_time`,
  `get_stock_price`, `generate_random_number`, `get_weather`, `get_news_headlines`
- **Profiles:** Cloud, Workstation, DGX Spark, Jetson Thor
- **Use when:** getting started, prototyping, or as the starting point for your own domain.

### 1.2 Multilingual Assistant (`multilingual-assistant`)

Same cascaded shape as Generic, but the whole session is pinned to one language
(chosen in the UI, default `de-DE`) — ASR, TTS voice, and LLM all operate in it.

- **Entry point:** `examples.multilingual.pipeline:bot`
- **Capabilities:** `session_languages`
- **Defaults:** `nemotron-super` LLM, `nemotron-asr-streaming-multilingual` ASR, `magpie-multilingual-tts` TTS
- **Agent shape:** single LLM agent plus a `MultilingualProcessor`
  (`multilingual/multilingual_processor.py`) that injects a per-turn language reminder
  at request time only, keeping chat history clean of scaffolding.
- **Prompt add-on:** `fixed_session_language_addon`
- **Profiles:** Cloud, Workstation, DGX Spark (extensible to Jetson Thor)

### 1.3 Nemotron Omni Assistant (`omni-assistant`)

Collapses the ASR and LLM stages into one audio-input model: Nemotron 3 Nano Omni
consumes user audio directly and emits assistant text, which Magpie TTS speaks.
Text and audio inputs only.

- **Entry point:** `examples.omni_assistant.pipeline:bot`
- **Slots:** `llm`, `tts` (no ASR slot — the LLM does it)
- **Key pieces:** `nvidia_omni_multimodal_service.py` (`NvidiaOmniLLMService`),
  `audio_only_smart_turn_strategy.py` (audio-only turn finalization),
  `user_mute_processor.py`
- **Agent shape:** single fused speech-in / text-out agent. The user transcript is
  read off the Omni response instead of a separate ASR pipeline.
- **Profiles:** Cloud, Workstation, DGX Spark, Jetson Thor
- **Use when:** comparing a classic cascaded pipeline against an Omni-based one.

### 1.4 Nemotron Omni Assistant Subagents (`omni-assistant-subagents`)

**Multi-agent, specialist fan-out.** Built on Pipecat's multi-agent framework
(`pipecat.workers`). A transport agent owns I/O and TTS, a speaker agent owns spoken
output, and worker agents handle uploaded media, live webcam vision, and deliberate
reasoning — so the voice loop stays responsive while specialists work.

- **Entry point:** `examples.omni_assistant_subagents.pipeline:bot`
- **Capabilities:** `attachments`, `webcam`
- **Registry of roles:** `omni_assistant_subagents/subagents.yaml` — the single source
  of truth for the Speaker's delegation block, the `/api/subagents` UI roster, and each
  worker's reasoning mode. Loaded by the framework-agnostic
  `src/examples/shared/subagents.py` (`SubagentSpec`, reasoning modes `on` / `off` / `on_demand`).
- **Features:** visual barge-in, deferred media dispatch, rolling webcam scene
  summaries, on-demand high-resolution capture, proactive hand-gesture behavior.
- **Profiles:** Cloud, Workstation, DGX Spark

See [§2.1](#21-omni-assistant-subagents-internal-agents) for the individual subagents.

### 1.5 Frontend/Backend Agent (`frontend-backend-agent`)

**Multi-agent, delegation/hierarchical.** A fast frontend LLM ("Talker") is the only
user-facing component; a specialized backend agent ("Thinker") does the slow planning
and tool work. This is the pattern for putting a real-time voice experience in front of
an *existing* text or agentic backend. The reference backend is a stateful airline
flight-booking agent.

- **Entry point:** `examples.frontend_backend_agent.pipeline:bot`
- **Slots:** `llm`, `thinker-llm`, `booking-server`, `asr`, `tts` — note the **two LLM
  slots** and the sidecar service slot.
- **Delegation tools (the only two the frontend has):** `call_backend(query, filler_text)`
  and `cancel_backend()` — defined in `airline/tools.py`, handled in `src/tool_handlers.py`.
- **Direct-answer path:** the Talker prompt (`prompts.yaml`, key `talker`) defines four
  modes per user turn — **Direct** (greetings, thanks, small talk: answered with *no*
  tool call), **Unsupported** (out-of-scope, declined directly), **Tool** (`call_backend`),
  and **Cancel** (`cancel_backend`). So delegation happens only when the turn is
  flight-task related.
- **Protocol:** `src/protocol.py` defines the Talker↔Thinker contract — payload types
  `response_hint` (missing info / intermediate) and `tool_result` (final), each carrying
  the `response_text` the frontend speaks, plus lifecycle markers `ThinkerStarted`,
  `IntermediateResponse`, `ThinkerCompleted`, `ThinkerAborted`.
- **Latency handling:** `call_backend.filler_text` is spoken only if the backend exceeds
  `THINKER_FILLER_THRESHOLD_SECONDS` (default `0.3`); the delayed filler task is cancelled
  if the backend returns first. `FRONTEND_BACKEND_DIRECT_TOOL_RESPONSE=1` pushes the
  backend's `response_text` straight to TTS with `run_llm=False`, skipping a second
  frontend LLM pass.
- **Sidecar:** `booking-server` (`airline/database/`), the backend agent's HTTP database.
- **Profiles:** Cloud, Workstation
- **Tunables:** `CHAT_HISTORY_RECENT_TURNS` (20), `THINKER_FILLER_THRESHOLD_SECONDS` (0.3),
  `THINKER_TOOL_TIMEOUT_SECONDS` (30.0)

See [§2.2](#22-frontendbackend-agent-internal-agents) for the internal roles.

---

## 2. Internal agents / subagents

### 2.1 `omni-assistant-subagents` internal agents

Declared in `subagents.yaml`; worker prompts live in `prompts.yaml` under `agent_prompts`.
A subagent is **delegatable** when it has a `source_token` (the `selected_input_source`
value the Speaker emits); otherwise it is **ambient** and runs on its own.

| Agent | Code | Routing | Reasoning | Role |
|---|---|---|---|---|
| **Transport agent** | `subagents/transport/` | infrastructure | — | Owns I/O, TTS, and the controllers that gate everything else |
| **Speaker agent** | `subagents/speaker/` | the router | — | The only agent that speaks; picks `selected_input_source` to delegate, or answers itself with `"none"` |
| **Media Analyzer** | `subagents/media_analyzer/` | delegatable (`uploaded_attachment`) | `on` | Reads an uploaded image/audio/video and returns a plain description of its contents. Reports only what is in the media — no planning or code |
| **Webcam Vision** | `subagents/webcam/` | ambient | `off` | Streams live webcam frames and continuously describes what is visible — the assistant's "eyes". Its latest value is the only truth about the current scene |
| **Thinker** | `subagents/thinker/` | ambient (auto-escalation) | `on_demand` | Deliberate reasoning escalation. Takes over automatically when the Speaker's self-rated confidence drops (`turn_action: think`) and re-answers with full reasoning |

Supporting transport-side controllers (`subagents/transport/`): `media_analysis_controller.py`,
`proactive_gesture_controller.py`, `webcam_controller.py`, `thinking_controller.py`,
`subagent_state_board.py` (renders the Speaker's pinned state note), `speaker_context.py`.
The gesture schema (`greet` / `stop` / `continue` / `down`) lives in `subagents/gestures.py`.

**Note on the two "Thinkers":** the `omni-assistant-subagents` Thinker is a *reasoning
escalation* for the same turn; the `frontend-backend-agent` Thinker is a *task-executing
backend agent*. Same name, different jobs.

### 2.2 `frontend-backend-agent` internal agents

| Agent | Code | Role |
|---|---|---|
| **Talker (frontend)** | `prompts.yaml` key `talker`, `pipeline.py` | The only user-facing LLM. Speaks, routes, and answers small talk directly. Holds exactly two tools, both internal |
| **Thinker (backend)** | `airline/thinker.py`, `src/planner.py`, `prompts.yaml` key `thinker` | Session-scoped backend agent. Owns intent, slot extraction, tool execution, booking state, policy checks, and final response text |

The backend's planner (`NvidiaThinkerPlanner` in `src/planner.py`) asks a Nemotron model
for a JSON plan given `{query, structured_fields, session_state, runtime_context}`, then
executes the chosen domain tool.

**Backend domain tools** (`airline/`): `flight_search` (`flight_search.py`), `booking`
(`booking_tool.py`), `pnr_status` (`pnr_status.py`). Booking is deliberately gated — the
user must search first and select a returned flight before booking can proceed.

**Reusability:** `frontend_backend_agent/src/` holds the domain-agnostic machinery
(planner, protocol, tool handlers, TTS filter, runtime context); `airline/` holds the
flight-booking domain. Swap `airline/` for any backend exposing compatible call/cancel
behavior.

---

## 3. Shared agent infrastructure

Cross-example building blocks in `src/examples/shared/`:

| Module | Purpose |
|---|---|
| `subagents.py` | Framework-agnostic subagent registry loaded from a per-example `subagents.yaml` |
| `pipeline_utils.py` | Common pipeline assembly helpers |
| `activity_check.py` | Idle/inactivity warnings (configured per example in the registry) |
| `prewarm.py` | Service prewarming |
| `nemotron_speech_text_filter.py` | Strips reasoning/markup before text reaches TTS |
| `json_parsing.py` | Tolerant JSON extraction from LLM output |
| `audio_recorder.py` | Session audio capture |

---

## 4. Not voice agents: Claude Code skills

`skills/` contains developer-workflow skills for working *on* this repo, not runtime agents:

- `skills/configure-pipeline/` — change pipeline configuration
- `skills/deploy/` — deployment, with per-example references including
  `frontend-backend-agent-deploy.md`, `omni-assistant-deploy.md`,
  `omni-assistant-subagents-deploy.md`, `generic-deploy.md`, `platform-deployment.md`
- `skills/upgrade-pipecat/` — a four-stage Pipecat upgrade workflow

---

## Quick reference: choosing a pattern

| If you need… | Use |
|---|---|
| A starting point / baseline | Generic Assistant |
| Non-English or per-session language locking | Multilingual Assistant |
| Fused speech-in LLM, fewest moving parts | Nemotron Omni Assistant |
| Image / audio / video / live webcam understanding | Omni Assistant Subagents |
| Voice in front of an existing text or agentic backend | Frontend/Backend Agent |
