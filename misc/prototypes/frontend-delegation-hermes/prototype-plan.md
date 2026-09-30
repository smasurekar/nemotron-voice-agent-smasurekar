# Plan — Frontend Delegation to a Hermes Backend (voice, OpenAI Realtime server, τ³-voice ready)

**Status:** implemented (revision 5 records implementation notes and measurements, §22; revision 6 adds the
τ³ failure fixes behind switches, §19) ·
**Date:** 2026-09-30 · **Revision:** 6
**Code (proposed):** `src/prototypes/voice_delegation_hermes_agent/` · **Runbook (P6):** `runbook.md` (this folder)
**Behaviour spec:** [`workflow.csv`](workflow.csv) (user query type × backend state → frontend and backend actions),
amended by requirement changes RC1 (§0.1) and RC2 (§0.2, behind an experimental switch)
**Builds on:** the voice frontend/backend prototype (`src/prototypes/voice_frontend_backend_agent/`,
[`../voice/prototype-plan.md`](../voice/prototype-plan.md), [`../voice/runbook.md`](../voice/runbook.md)).
**Backend:** Hermes `AIAgent` (`hermes-agent-smasurekar`, `run_agent.py:241`).
**Evaluation target:** τ³-bench voice (`tau2-bench-smasurekar`) through its `pine-` custom Realtime route
(`misc/prototypes/voice-custom-agent-openai-realtime-integration.md`,
`misc/prototypes/voice-frontend-backend-agent-tau3-runbook.md`). A dedicated τ³ runbook for this prototype is
written later (out of scope here).

The existing prototype is request/response. The frontend either answers or delegates, and the voice turn
then waits for the backend. Speech during that wait counts as a barge-in, and after a tool call has gone out
it is queued. This prototype separates the two agents in time:

- The **frontend** runs on *every* user turn. It makes exactly one call:
  `delegate(delegate: bool, filler_text: str, request: "task"|"status")`. `filler_text` is spoken immediately
  and may be empty. `request` defaults to `"task"`, and only a WORKING backend reads it (RC1).
- The **backend** is one long-lived Hermes `AIAgent` per Realtime session, hosted in **its own worker
  process**. It runs in the background. The session's controller decides from the backend state whether to
  start the session, continue it (`run_conversation`), steer the running task (`steer`/`redirect`), or report
  progress (`get_activity_summary`).
- Backend output (tool calls, answers, status) is pushed to the voice layer whenever it is ready.

---

## 0. Decisions (confirmed 2026-09-29)

| # | Decision | Confirmed | Rejected alternative |
|---|---|---|---|
| D1 | Where Hermes runs | **Out of the voice server process**, on Python 3.14 in the Hermes venv (§4.2). Reason: this repo pins `requires-python >=3.12,<3.14` and the image ships Python 3.12, while every Hermes core dependency is gated `python_version >= '3.14'`. The Hermes checkout's `.venv` is 3.12, so its dependencies are not installed. | In-process import. This needs a Python bump for this repo, which is not viable. |
| D2 | How a WORKING backend tells "status question" from "new task / follow-up" (the CSV routes these to `get_activity_summary()` vs `steer()`) | **RC1: a third field on the frontend tool**, `request: "task"\|"status"`, default `"task"` (§0.1). | (a) A backend classifier LLM call. Rejected: no extra LLM layer. (b) Keyword rules. Rejected: "status of order 1235" is a new task, not a status question. (c) Always steer. Rejected: it contradicts the CSV row "backend task would not be hampered". |
| D3 | Voice engine | **New `DelegationTurnManager`** in the new package, plugged into the existing `RealtimeSession` and `build_app` through **additive** hooks (§4.3, the only edit to the voice package). All of `wire/`, `speech/`, `audio/`, `InputPath`, `OutputPath`, `playback`, `ResponseWriter`, the event log and `web/index.html` are reused unchanged. | Fork the voice package (about 2,700 lines of engine), or bend `AgentPort`. `AgentPort` has a single reply per turn and cannot express backend output that arrives outside a turn. |
| D4 | Is `filler_text` audible in τ³ runs? | **Yes, always spoken.** Here it is the frontend's only voice (direct answers use `delegate=false` + `filler_text`). Pass^1 is therefore compared with the audible-filler `verdictspk` arm only, never with the silent `paired` arm (§16). | `delegation.speak_when_delegating: false` for a "silent ack" ablation arm. |
| D5 | 5 s simulated delay (web only) | **Per delegation that starts a backend run**, before `run_conversation`. The backend is WORKING during the delay, so "what's the update?" and steering can be tried live. | Per tool call (`simulated_delay.where: per_tool_call`). Also supported. |
| D6 | Steering primitive | **`auto`**: `redirect()` while a model request is in flight (the text reaches the backend now), otherwise `steer()` (delivered after the current tool batch). A leftover `pending_steer` is re-run as the next turn. | `steer` only. Simpler, but a steer is only delivered at a tool boundary. |
| D7 | Who turns `get_activity_summary()` into speech | **Frontend LLM, one no-tools call** on the `status_verbalize` template. It is used only for WORKING + `request=status` and falls back to a deterministic template on timeout or error. | Template only. Rejected: it sounds robotic. |
| D8 | Multi-session isolation | **One Hermes worker process per Realtime session, behind a backend gateway** (§4.2). Hermes' tool registry is process-global (`tools/registry.py:428-440`); a process per session gives each `AIAgent` its own registry, `HERMES_HOME`, futures and lifecycle. A hung or crashed worker is killed without touching other sessions. Capacity is configurable at both layers (`FDH_MAX_SESSIONS`, default 8). | (a) One session per sidecar (revision 3). Rejected: the voice server must serve many sessions. (b) Many agents in one process with a shared dispatcher. Rejected: global registry collisions, and a hung thread cannot be killed. |
| D9 | Where conversation state lives | **In the gateway**, per session: the controller (state machine, epochs, settled Hermes history, context queue, tool routing). The worker is a **disposable agent host**: one `AIAgent`, its tool bridge, and commands `run`/`steer`/`redirect`/`status`/`interrupt`/`close`. It keeps no conversation state between runs (`run_conversation` receives the history each time). A killed worker is replaced from the gateway's settled state. | Controller inside the worker. Rejected: killing a hung worker would lose history and the context queue. |

### 0.1 Requirement change RC1 (approved by the user, 2026-09-29)

The original requirement names the frontend tool `delegate(delegate: bool, filler_text: str)`. RC1 changes
it to `delegate(delegate: bool, filler_text: str, request: "task"|"status")`.

- **Approved:** the user chose "Add a field to delegate" when asked how a WORKING backend should tell a status
  question from a steer without an additional LLM call.
- **Backwards compatibility:** `request` is optional and defaults to `"task"`. A model that omits it behaves
  exactly as the original two-field contract, except in the one CSV cell "asks for updates × WORKING".
- **Scope:** the backend reads `request` only in the WORKING state. In NO_SESSION and IDLE it is ignored.
- **Tests and specs:** the workflow-table test (§15) checks the three-field contract. `workflow.csv` itself is
  not edited, because it is the user's spec.

### 0.2 Requirement change RC2 (approved narrowly, review 2 of the τ³ failure fixes, 2026-09-30)

RC2 extends RC1 for M2, "replay an unheard answer"
([`tau3-failure-fixes-plan.md`](tau3-failure-fixes-plan.md) §4).

- **Scope:** the voice server interprets `request="status"` in IDLE only when
  `delegation.replay_unheard_answer.enabled` is true **and** every replay guard passes (same run and
  request, no task since the answer, within `ttl_s`, not replayed before). It then speaks the answer again
  and sends no `delegate` to the gateway. If any guard fails, the turn is delegated as before.
- **Unchanged:** the gateway still ignores `request` in NO_SESSION and IDLE (§5.4). `workflow.csv` is not
  edited. M2 is off by default and experimental.

---

## 1. Requirements as testable rules

| # | Requirement | Rule |
|---|---|---|
| V1 | Frontend has exactly one tool | The frontend request carries one tool, `delegate(delegate: boolean, filler_text: string, request: "task"\|"status")` (RC1). `delegate` and `filler_text` are required; `request` is optional (default `"task"`, ignored when `delegate=false`). `tool_choice: "required"`. Replies that break the contract are repaired (§6.3). |
| V2 | Frontend decides on every user turn | Every committed user utterance (after VAD 800 ms + ASR + normalization) produces exactly one `delegation_decision`, whatever the backend state. |
| V3 | "NA" rows in the CSV | These are `delegate(delegate=false, filler_text=…)`. `filler_text` may be `""`, and then nothing is spoken and no response is created. |
| V4 | Filler spoken with delegation | When `delegate=true`, `filler_text` is synthesized **concurrently** with sending the delegation to the backend. It never waits for the backend. |
| V5 | Backend = Hermes, one per session | One `AIAgent` per Realtime session, in its own worker process. Backend state is always one of `NO_SESSION \| IDLE \| WORKING` (§5). |
| V6 | Backend chooses its action from its own state | The mapping in §3 is implemented in `BackendController`. It is a pure state machine tested against every CSV cell. |
| V7 | Shared conversation history, exactly once, failure-safe | Every `SharedTranscript` entry reaches each consumer exactly once and in order. The frontend gets it by full re-projection. The backend gets it through `queued → in_flight → committed` tracking that requeues on failure (§7). Past Hermes messages are never mutated. |
| V8 | Truthful spoken history | Only what was actually heard is presented as said: heard / partial / not heard is recorded for every spoken item. Hermes-native answers that were cut or never played get delivery notes. Controller apologies are never presented as Hermes speech (§7.4). |
| V9 | OpenAI Realtime compatible (GA) | Same wire contract as the existing server (`session.created` first, transactional `session.update`→`session.updated`, `audio/pcmu` + `audio/pcm`, GA event names, per-item transcripts, `function_call` round trip, `speech_started` barge-in, `conversation.item.truncate`, manual `response.create`). The existing Gate A and `tau2_replay` checks must pass. |
| V10 | τ³ tools run by τ³ | Hermes' domain tool calls are bridged to Realtime `function_call` items. τ³ executes them and returns `function_call_output`. Hermes has no built-in tools enabled. |
| V11 | 800 ms VAD | `turn_detection.silence_duration_ms: 800`, `honor_client_values: false` in the **base** voice profile, so it applies to τ³ (which sends 500) and to the browser page (which hard-codes 500). |
| V12 | Normalization on by default, configurable | Transcript normalization is `enabled: true` in the base voice profile. Tool-argument normalization is `enabled: true` with the τ³ rule set in the τ³ profile, and off in the browser profile (it requires client tools). Both can be turned off per profile. |
| V13 | Realtime server + browser, 5 s delay only in browser | Two profiles on one server binary: `tau3_eval.yaml` (no delay) and `browser_demo.yaml` (TLS, in-process demo tools, `simulated_delay.seconds: ${FDH_BACKEND_DELAY_S:-5}`). A test asserts that the delay is 0 in every non-browser profile. |
| V14 | Modular | Every external dependency and every policy decision sits behind a small `Protocol` with at least two implementations (a real one and a fake) and is chosen from config (§12.1). No network in unit tests. |
| V15 | Highly configurable | Every behaviour, threshold, prompt, model, capacity, timeout and port comes from YAML, with `${ENV:-default}` interpolation and `extends:` profiles. No literals in code for tunables. Unknown keys are errors, and the effective config is logged at session start (§11.1). |
| V16 | No double-talk | Queued agent audio is never released while the user is speaking (§10.1). |
| V17 | Deterministic backend lifecycle | Every exit path of a run (normal, failure, exception, watchdog, worker crash, close, disconnect) leaves `WORKING` exactly once, resolves every outstanding tool call, and emits exactly one `backend_run_done` (§5). |
| V18 | Multi-session | Many concurrent Realtime sessions (configurable), each with an isolated Hermes worker. Sessions never share tool schemas, tool results, `HERMES_HOME` or failures (§4.2, §15). |

---

## 2. What exists and what gets reused

| Piece | Source | Use |
|---|---|---|
| Realtime wire, session view, VAD/segmenter, ASR/TTS (Riva), G.711, pacing, `ResponseWriter`, `WireWriter` | `voice_frontend_backend_agent/{wire,speech,audio,engine}` | Reused; `RealtimeSessionView` gains an additive `preview()` (§4.3) |
| `RealtimeSession` (dispatch, ASR chain) and `server.build_app` (health, TLS, `/` page, `server.max_sessions` guard at `server.py:203`) | same | Reused through the additive hooks in §4.3 |
| Voice config loader `load_voice_config(path)` | `voice_frontend_backend_agent/config.py:951` | Reused unchanged. It takes a **path**, so this prototype ships its own voice-profile YAML files and references them by path (§11) |
| Normalization (`TranscriptNormalizer`, `ArgumentNormalizer.screen`, rules, prompts) | `voice_frontend_backend_agent/normalization/` | Reused unchanged (pure, based on `ToolCall`) |
| Event log (`EventLog`, `SessionRoutingSink`), `FillerLog`, `prompt_context`, tool conversion (`realtime_tools_to_specs`) | `voice_frontend_backend_agent/agent/` | Reused |
| `OpenAIChatClient`, Jinja prompt catalog (`prompts.load_catalog/render`), `ToolCall`, `UsageTotals`, `demo_tools.TOOLS` | `text_frontend_backend_agent/` | Reused |
| Browser page | `voice_frontend_backend_agent/web/index.html` | Served unchanged (VAD is overridden server-side) |
| τ³ gates (`gate_a_handshake.py`, `check_gate_b.py`, `tau2_replay`) | `voice_frontend_backend_agent/cli/` | Pointed at the new port |
| `AIAgent` control surface | Hermes `agent/interrupt_control.py` (`steer` :250, `redirect` :261, `interrupt` :115, `hard_interrupt` :215, `clear_interrupt` :221), `run_agent.py:882` (`get_activity_summary`), `run_agent.py:965` (`close`), `agent/turn_facade.py:22` (`run_conversation`) | Wrapped by `HermesAgentAdapter` inside the worker |

Facts about Hermes that shape the design (from the code):

- **Runs:** `run_conversation` is **synchronous and blocking**, and must not run twice at once on one agent.
  The worker runs it in one executor thread.
- **Busy state:** there is **no public busy flag**. The controller tracks `WORKING` itself.
- **`steer(text)`:** it is queued and delivered only **after a tool batch** or at the next iteration. If the
  turn ends first, it comes back as `result["pending_steer"]`. It returns `True` even when the agent is idle,
  so it is only called while WORKING.
- **`redirect(text)`:** it aborts only the in-flight model request and retries in the same turn with the new
  user text. It returns `False` in the gap between the model phase and the tool phase.
- **Interrupts:** an interrupt that lands while idle carries over into the next turn, so the worker always calls
  `clear_interrupt()` before `run_conversation`. `soft_interrupt()` **does not exist**. `hard_interrupt()` is
  used only by the watchdog and on close (not in the CSV flows).
- **Prompt-cache invariant** (`agent/AGENTS.md:54-58`): never alter past context, change toolsets or rebuild
  the system prompt mid-conversation. Anything injected rides a **user message or tool result**. This rules
  out rewriting past assistant rows, and changing tools after the agent exists (§4.3, §7).
- **Tool registry:** it is process-global (`tools/registry.py:428-440`), which is why D8 uses one process per
  session. `register(name, toolset, schema, handler, …)`, and the handler gets
  `(args, task_id=…, session_id=…)` (`model_tools.py:827`). The worker passes
  `task_id = "<session_id>:<epoch>"` to `run_conversation`, so every tool call is bound to its run (§5.2).
- **Construction:** `AIAgent(...)` raises if the model's context window is **below 64K**
  (`MINIMUM_CONTEXT_LENGTH`). It reads `HERMES_HOME/config.yaml`, SOUL.md and memory unless told not to. With
  `skip_context_files=True`, SOUL.md is loaded only when `load_soul_identity=True`
  (`agent/system_prompt.py:548`). `HERMES_HOME` comes from the environment (`hermes_constants.py:112`), so it
  is set per worker process.
- **Budgets:** `run_budget_seconds` is checked inside Hermes' loop, not enforced as a hard deadline. The
  controller adds its own watchdog (§5.3).
- **Usage:** the agent's `session_*_tokens` counters (`agent_init.py:2321-2325`) are cumulative for the life of
  the agent. Per-run usage is a delta between snapshots taken by the worker (§5.4).
- **Output:** Hermes and its libraries may print to stdout. The worker's control channel is therefore a Unix
  domain socket, never stdout (§9.2).

---

## 3. The workflow table, as implemented

The frontend always calls `delegate`. In the CSV, "NA" means `delegate=false`.

| User query | Backend state | Frontend call | Backend action (`BackendController`) |
|---|---|---|---|
| New task | NO_SESSION | `delegate(true, "Sure, let me check that.")` | `start`: the worker's `AIAgent(session_id)` begins its first `run_conversation(query)` → WORKING |
| New task | IDLE | `delegate(true, ack)` | `continue`: `run_conversation(query, conversation_history=settled)` → WORKING |
| New task | WORKING | `delegate(true, ack, request=task)` | `steer` → `redirect()` if a model request is active, else `steer()` (D6) |
| Follow-up | NO_SESSION | `delegate(true, ack)` | `start` (as above) |
| Follow-up | IDLE | `delegate(true, ack)` | `continue` |
| Follow-up | WORKING | `delegate(true, ack, request=task)` | `steer` (as above) |
| Acknowledges filler | any | `delegate(false, "" \| short reply)` | None now. The user's words and any reply become backend context entries, delivered exactly once with the next backend input (§7.3). |
| Asks for update | NO_SESSION | `delegate(true, ack)` | `start` with this query (Hermes answers that there is nothing in progress) |
| Asks for update | IDLE | `delegate(true, ack)` | `continue` (a follow-up that Hermes answers from history) |
| Asks for update | WORKING | `delegate(true, "Let me check where that is.", request=status)` | `status`: the worker's `get_activity_summary()` plus the controller's own activity record → `status` event → verbalized (D7). **The running task is not touched.** |

NO_SESSION is the **logical** state "no run has happened yet". When the `AIAgent` object is physically built
is a config choice (`hermes.agent_construct: eager|lazy`, §5.1). P0 measures construction time to set the
default.

Because the delegate tool carries no query text, the backend input is always the **user's own words**. The
delegated utterance travels in the `delegate` message. Earlier undelivered entries (acks, frontend lines,
status replies, delivery notes) travel as a context block in front of it (§7.3).

A code guard runs on top of the prompt, controlled by `delegation.guards.backend_question_needs_delegate: true`.
If the backend is IDLE and the last **heard** assistant message came from the backend and ends with a
question, a `delegate=false` decision is overridden to `delegate=true`, keeping the filler. The reason is that
in τ³ a "yes" often answers the backend's confirmation question ("Shall I cancel it?"), not the frontend's
filler. The override is logged as `delegation_guard`.

---

## 4. Architecture

```
          OpenAI Realtime (GA) WebSockets — τ³ clients or browser pages (N sessions)
                                   │
┌──────────── voice server (Python 3.12, this repo's image; server.max_sessions = N) ──────┐
│ RealtimeSession ×N (reused): wire parse, transactional session.update, VAD 800 ms, ASR    │
│   └─ DelegationTurnManager (new, one per session)                                         │
│        ├─ FrontendDecider ── LLM (lightning): delegate(bool, filler, request)              │
│        ├─ SharedTranscript  (seq-numbered, heard/partial/not-heard outcomes, §7)           │
│        ├─ OutputScheduler   (one audio response at a time, never over the user)           │
│        ├─ ToolRelay         (tool.call → function_call batches; outputs back)              │
│        │     └─ ArgumentNormalizer.screen (local answers for invalid ids)                  │
│        └─ BackendLink ── one WebSocket per session ──────────────────────────────┐        │
│ OutputPath (reused): TTS ► pcmu/pcm deltas + transcript deltas                      │        │
└─────────────────────────────────────────────────────────────────────────────────────┼────────┘
                                                                                      │ §9.1
┌──────────── backend gateway (Hermes-free; host process; gateway.max_sessions ≥ N) ──▼────────┐
│ FastAPI WS /v1/backend, GET /health                                                         │
│ SessionRuntime ×N: BackendController (states, epochs, settled history), ContextQueue,       │
│                    tool-call routing table, watchdog, outbound writer queue (serialized)    │
│ WorkerPool: spawn / assign / stop / kill; optional warm workers; per-worker HERMES_HOME     │
└────────────┬──────────────────────────────┬──────────────────────────────┬──────────────────┘
             │ Unix socket (§9.2)           │                              │
┌────────────▼──────────────┐  ┌────────────▼──────────────┐  ┌────────────▼──────────────┐
│ Hermes worker: session A  │  │ Hermes worker: session B  │  │ Hermes worker: session C  │
│ Python 3.14, own process  │  │ own process group         │  │ …                         │
│ AIAgent + tool registry   │  │ own HERMES_HOME           │  │                           │
│ ToolFutures (keyed)       │  │ own futures and epochs    │  │                           │
│ one outbound writer queue │  │                           │  │                           │
└───────────────────────────┘  └───────────────────────────┘  └───────────────────────────┘
```

### 4.1 Why the frontend and backend are decoupled in time

The CSV requires the frontend to handle user speech **while the backend is WORKING** (steer, status, acks).
That includes the time while τ³ tool calls are outstanding. The existing `TurnManager` queues input in
`AWAITING_TOOLS` and cancels or reviews it in `THINKING`, which cannot express this. So there are three lanes:

- **Frontend lane:** short and cancellable, one per user turn.
  - Barge-in *before* the decision returns uses `cancel_and_merge`: the call is cancelled and the merged text
    is decided again.
  - Once a delegation has been sent it cannot be taken back, so the next utterance becomes a steer. This is by
    design.
- **Backend lane:** long-running, never cancelled by user speech. Its outputs are events tagged with `run_id`
  and `epoch` (§5).
- **Output lane** (`OutputScheduler`, §10.1): serializes Realtime responses.
  - The order is the current turn's filler or direct reply first, then backend answers and status.
  - **Queued audio is never released while `user_speaking` is true.**
  - Function-call responses carry no audio. They are emitted as soon as no response is open, even while the
    user speaks.

### 4.2 Gateway and workers (D1, D8, D9)

**Gateway** (`sidecar/`, Hermes-free, host process):

- It can run in this repo's environment (Python 3.12). It launches workers with the Hermes 3.14 interpreter
  (`workers.python`).
- It runs one `SessionRuntime` per voice connection. The `SessionRuntime` holds:
  - the `BackendController` (§5);
  - the `ContextQueue` (§7.3);
  - the tool-call routing table (`call_id → epoch, batch`);
  - the watchdog;
  - one **outbound writer queue** to the voice server (§9.1).
- **Capacity:** `gateway.max_sessions`. A connection beyond it gets `error{code: "capacity"}` **only after**
  `max_sessions` sessions are live (starting, ready or busy). Workers that are stopping count until they have
  exited.
- **`WorkerPool`:**
  - Spawns with `start_new_session=True` (a separate process group).
  - Creates a per-worker `HERMES_HOME` under `workers.home_root/<session_id>/`, containing a rendered SOUL.md
    and a `config.yaml` with `context_length`. It is removed after exit unless `workers.keep_homes: true`.
  - Passes the socket path and settings as arguments and the environment.
  - Waits for `worker.ready` within `workers.start_timeout_s: 15`.
  - Stops a worker with `close` → wait `workers.stop_timeout_s: 10` → `SIGTERM` to the process group → wait
    `workers.kill_grace_s: 3` → `SIGKILL`.
  - Optional `workers.warm: 0` pre-started spare workers, if P0 shows startup is slow. A warm worker gets its
    `HERMES_HOME` and session binding at assignment.

**Worker** (`worker/`, one process per session):

- A stdlib control loop plus `hermes_adapter.py`, which is the only module that imports Hermes, loaded
  lazily when `agent_kind: hermes`.
- It owns:
  - one `AIAgent`;
  - its own process-global tool registry, holding only this session's tools and schemas;
  - `ToolFutures` (§8.1);
  - the current epoch;
  - one outbound writer queue to the gateway (§9.2).
- It keeps **no conversation state** between runs. Every `run` command carries the settled history (D9).
- With `agent_kind: fake` the same worker runs `FakeAgent` under any Python. CI uses this to test real
  processes, sockets, kills and crashes without Hermes.

**`BackendLink`** (voice side) has two implementations:

- `WebSocketBackendLink`: the real one, to the gateway.
- `InProcessBackendLink`: `SessionRuntime` with an in-process fake worker, for unit tests and the
  `--stub-backend` no-GPU smoke run.

### 4.3 The only edits to the existing voice package (additive, defaults unchanged)

Today there are three problems for a custom turn manager:

- `RealtimeSession.__init__` always calls `agent_factory(session_id)` (`engine/session.py:95`).
- `_on_session_update` applies the patch to `RealtimeSessionView` immediately (`view.apply` mutates the state)
  and then calls `self.agent.configure()` (`engine/session.py:230-246`).
- `build_app`'s lifespan always builds the text-agent LLM clients (`server.py:151`).

The edits:

1. **`engine/turn_api.py` (new, protocol only):**
   - `TurnManagerLike` `Protocol` with the methods the session calls today: `on_audio_advance`,
     `on_user_input`, `on_function_output`, `on_response_create`, `delete_pending_input`, `on_truncate`,
     `speak_greeting`, `on_utterance_dropped`, `on_speech_started`, `on_response_cancel`,
     `on_output_audio_clear`, `on_filler`, `close`, `state`.
   - Plus `async on_session_update(settings: SessionSettings, change: SessionChange) -> None`, where
     `SessionChange(first: bool, tools_changed: bool, instructions_changed: bool)`.
   - `TurnManagerFactory = Callable[[TurnContext], TurnManagerLike]`. `TurnContext` bundles what the session
     passes today (output path, emit, conversation order, settings getter, voice resolver, config, clock, audio
     clock, session id and start, filler log, event log).
2. **`wire/session_view.py`:** `preview(patch) -> SessionPreview`. It runs today's `apply` logic on a
   **deep copy** of the view state and returns `SessionPreview(result, settings, commit)`. `commit()` swaps the
   copied state in. `apply()` stays as it is and is used by the default path.
3. **`engine/session.py`:**
   - A new keyword `turn_manager_factory: TurnManagerFactory | None = None`, and `agent_factory` becomes
     optional.
   - When `turn_manager_factory` is given, **`agent_factory` is never called** and `self.agent` is `None`.
   - `_on_session_update` becomes `async` (it is only called from the already-async `_dispatch`).
     - **Default path:** exactly today's code (`apply` → `agent.configure` → `input.configure` → emit).
     - **Custom path (transactional):**
       1. `preview = view.preview(patch)`. Validation errors surface here and nothing has changed.
       2. `await self.turns.on_session_update(preview.settings, change)`.
       3. Only on success: `preview.commit()`, `input.configure(...)`, log `session_updated`, emit
          `session.updated`.
       4. On hook failure: the view, input path and backend are all unchanged, and the client gets `error`
          (`WireProtocolError`).
4. **`server.py`:** `build_app(..., session_hooks: SessionHooks | None = None)`. `SessionHooks` holds:
   - `turn_manager_factory`;
   - `startup` / `shutdown` async callables (shared clients, gateway health check);
   - `health_extra() -> dict`.

   When `session_hooks` is given, the lifespan **skips `build_clients`**. The existing `server.max_sessions`
   guard still applies.
5. **Tests:**
   - The existing end-to-end trace (`test_voice_ws_end_to_end`) is byte-identical with the defaults.
   - With hooks, no `AgentPort` or `AgentClients` is constructed.
   - A rejected update leaves `view.public()` and the input format unchanged.
   - `on_session_update` is awaited before `session.updated`.

The text package is not touched.

**Later `session.update` messages** (the delegation manager's `on_session_update`, inside the transaction):

| Change | No `AIAgent` built yet | `AIAgent` exists |
|---|---|---|
| Audio format, VAD, voice | Committed by the session | Committed by the session |
| `tools` | Sent as `session.configure`; the gateway forwards them to the worker, which registers them. Commit follows the worker's ack. | `session.update_after_start` policy: `error` (default; the hook raises `tools_locked`, so nothing is committed, because Hermes toolsets must not change mid-conversation) or `ignore` (the hook returns, the old tools stay active, a warning is logged, and the view is committed with the old tools so `session.updated` tells the truth) |
| `instructions` | Replaced (same path) | Same policy (the system prompt is byte-stable for the agent's life) |

τ³ sends a single `session.update`, so the policy never triggers there. "Restart the worker with new tools"
is a follow-up.

---

## 5. Backend controller (`backend/controller.py`, runs in the gateway)

### 5.1 States and transitions

```
NO_SESSION ──delegate──► WORKING(epoch n) ──settle ok, no pending_steer──► IDLE
                           ▲  │                                              │
                           │  └─ delegate → request field                    │
                           │       task   → redirect()/steer()               │
                           │       status → activity summary                 │
                           │  settle ok with pending_steer                   │
                           └──── new run, epoch n+1 ◄────────────────────────┘ (no IDLE in between)
IDLE ──delegate──► WORKING(epoch n+1)
settle failed/error/deadline/worker_died ──► IDLE (NO_SESSION if no run ever settled ok); spoken apology
any ──close / link lost──► CLOSING (cancel tool calls, stop worker) ──► CLOSED
```

**Construction** (`hermes.agent_construct`):

- `eager` (default, pending P0 timing): the `AIAgent` is built when `session.configure` arrives. The worker
  replies `configured` only after construction, and the transactional `session.update` waits for it.
- `lazy`: built on the first delegation, inside the run (logged as part of that run's latency).
- A construction failure, in either mode, is a `worker.error{phase: "construct"}`. The state stays
  NO_SESSION, the context is requeued (§7.3), and there is an apology. Eager-mode failure fails the
  `session.update` itself.

### 5.2 Run epochs, `settle_run_locked()` and `task_id` binding

- **`RunHandle`:** each run is `RunHandle(epoch, run_id, started_at, outcome_future, settled=False)`. `epoch`
  increases by one per run, per session.
- **`settle_run_locked()`** runs under the session lock and is idempotent:
  1. If there is no current run, its outcome is not in yet, or it is already `settled`, return.
  2. Otherwise mark it `settled`.
  3. Apply the outcome (§5.3): commit or roll back history, move context entries (§7.3), emit `answer` or the
     apology, emit `backend_run_done`.
  4. Set the state, or start the `pending_steer` run.
- **Every command** (`delegate`, `session.configure`, `status`, `close`) starts with
  `async with lock: settle_run_locked(); …`. The worker-message reader only records outcomes into
  `outcome_future` and schedules a settle. So a delegation is always classified against a settled state.
- **Binding tool calls to a run:**
  - The gateway sends `run{epoch, task_id="<session_id>:<epoch>", …}`.
  - The worker passes `task_id` to `run_conversation`, and the bridge handler parses the epoch from its
    `task_id` kwarg.
  - Every `tool.call` carries that epoch. The worker refuses (returns an error string to Hermes) any call whose
    epoch is not the worker's current epoch.
  - The gateway drops, and logs, a `tool.call` or `tool.result` for an epoch that is already settled.
  - A late call can therefore never be attributed to a newer run.
- **Steer racing the end of a run:** if the outcome is not in yet, the steer goes to the worker. If Hermes has
  already passed its last check, the text comes back as `pending_steer`, and settle starts a new run with it.
  If the outcome is in, settle runs first and the delegation starts or continues a run. In neither case is an
  instruction lost.

### 5.3 Run outcomes, failures and recovery

| Outcome | History | Context entries (§7.3) | Spoken |
|---|---|---|---|
| `ok` (`final_response` present) | Commit the returned `messages` as the new settled history | `in_flight(epoch)` → `committed` | The answer (a Hermes-native item) |
| `ok` but empty `final_response` | Commit `messages` | `committed` | Apology (a controller item, §7.4) |
| `failed`/`interrupted` **with** returned `messages` (Hermes caught it and returned) | Commit `messages`, because the executed tool calls are in them and their side effects are real | `committed` | Apology |
| `error` (exception), `deadline` with no result, `worker_died` | **Roll back** to the last settled history | `in_flight(epoch)` → `queued` again, in `seq` order ahead of newer entries | Apology |

**Side-effect note on rollback:** when a run is rolled back but some of its tool calls had already executed
(the gateway's routing table knows which), the controller appends a `context` entry, "[a previous attempt
already ran: cancel_order(order_id=…) → …]". Hermes then does not repeat a side effect it cannot see.

**Watchdog** (`hermes.run_hard_deadline_s: 330`, larger than Hermes' soft `run_budget_seconds: 300`):

1. When the deadline passes, send `interrupt{hard: true}` to the worker. The worker resolves its tool futures
   with an error and calls `hard_interrupt()`.
2. Wait `hermes.unwind_timeout_s: 10` for the run outcome.
3. If it arrives, settle it by the table above.
4. If it does not, **kill the worker** (the §4.2 stop sequence, starting at `SIGTERM`), then settle as
   `worker_died` (roll back, requeue, apology).
5. If `recovery.respawn: true` (default) and fewer than `recovery.max_respawns_per_session: 2` respawns have
   happened, spawn a fresh worker, re-send `session.configure` and continue from the settled history.
   Otherwise the session's backend is marked failed: the next delegation gets the `backend_unavailable`
   apology, and the Realtime session stays up.

**Worker crash** (the socket closes or the process exits unexpectedly): cancel the session's outstanding tool
calls (`tool.cancel` to the voice side), settle as `worker_died`, and apply the same respawn rule. Other
sessions are unaffected, because each has its own process and `SessionRuntime`.

**Close** (`session.close`, voice disconnect or gateway shutdown):

1. The state becomes CLOSING and new commands are refused.
2. Send `close` to the worker. The worker's own close sequence is:
   1. Resolve every tool future with `"Error: session closed"`.
   2. `hard_interrupt()`.
   3. Join the run thread (up to `unwind_timeout_s`).
   4. `AIAgent.close()` only if the thread has unwound; otherwise skip it and log.
   5. Exit.
3. The gateway waits `workers.stop_timeout_s`, then escalates `SIGTERM` → `SIGKILL`.
4. Remove the `HERMES_HOME` and free the capacity slot only after the process has exited.

### 5.4 Other rules

- **Usage per run:** the worker snapshots `session_prompt_tokens`, `session_completion_tokens`,
  `session_input_tokens`, `session_total_tokens` and the reasoning counter before and after each run (exact
  names verified in P0). It reports the **delta** in its outcome. A respawned worker starts from zero, which
  deltas handle naturally.
- **Steer during the simulated delay** (browser): the text is appended to the not-yet-started run input. There
  is no `steer()` call.
- **Steer text** (`backend_steer` template): the context block, then the user's words.
- **`redirect()` returns `False`** (the gap between model and tools): fall back to `steer()`.
- **Status:** the worker's `get_activity_summary()` (`current_tool`, `api_call_count`,
  `seconds_since_activity`, …) is merged with the controller's own `ActivityRecord` (task text, start time,
  tools called so far with short argument and result previews, outstanding tool calls). It goes out as a
  `status{summary}` event. Nothing is sent to Hermes.
- **`request` field (RC1):**
  - WORKING + `status` → status path; WORKING + `task` (or a missing or invalid value) → steer path.
  - `task` is the fallback because an unneeded steer costs less than a lost instruction.
  - NO_SESSION / IDLE ignore the field; every delegation starts or continues a run. Under RC2 (§0.2), the
    voice server can answer an IDLE `status` turn itself, so that turn never reaches the gateway.
  - The controller logs the field value and the action taken, so wrong labels can be measured (§14).

---

## 6. Frontend (`frontend/decider.py`, `config/prompts.yaml`)

### 6.1 Request

Each call sends:

1. A system prompt (Jinja, `frontend_system`) with:
   - role and voice style;
   - the decision policy, which is the CSV's four query types with examples, including the τ³ rule
     "answers to the backend's questions ⇒ delegate";
   - the `request` rule: `status` only when the user asks about progress of work **already running**
     ("what's the update?", "is it done yet?"). A new question about an entity ("status of order 1235") is a
     `task`;
   - the **regular-speech rules**: backchannels ("mm-hmm", "okay", "right") and non-directed speech ("hold on,
     honey", talking to someone else) ⇒ `delegate=false` with `filler_text=""`. Vocal tics and restarts ("um,
     so, uh, the order…") are content: judge the words after removing the tics;
   - the `{capabilities}` lines from the client tools (as today, `frontend_capabilities: from_tools`);
   - the normalization note;
   - few-shot examples from `prompts.yaml`.
2. The frontend projection of `SharedTranscript` (§7.2).
3. A final system block `frontend_backend_state`:
   - `state: NO_SESSION|IDLE|WORKING`;
   - `current_request`;
   - `elapsed_s`;
   - `recent_activity`: up to 6 tool calls, each one line;
   - `backend_asked_question: bool`.
4. `tools=[DELEGATE_TOOL]`, `tool_choice="required"`.

The τ³ policy (`session.update.instructions`) goes to the **backend only** by default
(`instructions.apply_to: [backend]`, as in the existing prototype D3).

### 6.2 Output handling

- **`delegate=true`:**
  1. Send `delegate{turn_id, request, run_input: [the user entry]}` over `BackendLink`.
  2. In parallel, queue `filler_text` (if not empty) as the current turn's response, and add it to the
     transcript as a `frontend_speech` entry (outcome pending, §7.4).
  3. If the backend answers with `action=status`, the `status` event is verbalized by the frontend LLM
     (`status_verbalize` template, no tools, D7) and spoken as its own response after the filler.
  4. Log `delegation_decision`.
- **`delegate=false`:**
  - Speak `filler_text` if it is not empty, otherwise create no response.
  - The user entry and any reply become backend context entries (§7.3).
- Filler timing is logged exactly like `FillerTimingRecord` today (turn start, decision, first audio on the
  wall clock and the audio clock), so the τ³ report's filler-latency columns still work.

### 6.3 Contract repair (`delegation.on_contract_error`)

| Model output | Action |
|---|---|
| Plain text, no tool call | `delegate=false`, `filler_text=text` (logged `contract_repair: text_as_direct`) |
| Tool call with bad JSON or missing field | One retry with a repair message, then `on_contract_error: delegate` (default: `delegate=true`, `filler_text=""`) |
| Timeout (`frontend.timeout_ms: 4000`) | Same as `on_contract_error` |

### 6.4 Offline decision replay (`cli/delegation_replay.py`)

- `delegation_cases.jsonl` contains:
  - at least 3 cases for each of the 12 CSV cells;
  - τ³-style "yes/no answers to backend questions";
  - split IDs;
  - **regular-speech cases:** backchannels during each backend state, vocal tics in front of real tasks, and
    non-directed speech (expected: `delegate=false`, empty filler, no steer).
- Each case is replayed through `FrontendDecider` against the live frontend model.
- It reports accuracy for each cell, for `delegate`, for `request` (WORKING cells) and for each regular-speech
  class, plus filler-length statistics.
- This is the gate for prompt changes, like `verdict_replay` today.

---

## 7. Conversation history (exactly-once, append-only, failure-safe, truthful)

### 7.1 `SharedTranscript` (voice server, one per session)

An append-only list. Each entry has a monotonic `seq`, wall and audio stamps, an `origin`, a `backend_route`
fixed at creation, and, for spoken entries, a **playback outcome**:

| Entry | Origin | `backend_route` |
|---|---|---|
| `user` (delegated) | user | `run_input`: travels in the `delegate` message |
| `user` (not delegated) | user | `context` |
| `frontend_speech` (filler or direct reply) | frontend | `context`, deliverable once its outcome is final |
| `status_speech` | frontend (verbalizer) | `context`, deliverable once final |
| `controller_speech` (apology, backend unavailable) | controller | `context`, deliverable once final, labelled "[the system told the user]" |
| `backend_answer` | hermes | `native` (already in Hermes' history); only its **delivery note** travels |
| `backend_tool` | hermes | `native` |
| `delivery_note` | derived | `context`: created when a Hermes-native answer ends `partial` or `not_heard` |

**Playback outcome** (`OutputScheduler` + playback clock) is one of `pending`, `heard`, `partial(heard_text)`
or `not_heard(reason: dropped_stale|superseded|cancelled_before_audio|session_closed)`.

### 7.2 Frontend projection (stateless, exact by construction)

- The frontend prompt is **rebuilt from the whole transcript on every call**, so there is no frontend cursor
  and nothing can be duplicated or skipped.
- `user` → role `user`.
- Spoken entries → role `assistant`, **only if heard or partial**, using the heard text. `not_heard` entries are
  left out. `pending` entries appear as "(being spoken) …".
- `controller_speech` appears as assistant text too, because the user heard it.
- Backend tool activity is summarized in the state block (§6.1), not turned into messages.
- Pruning: `frontend.history.max_groups: 20`, applied to the projection only; the transcript keeps everything.

### 7.3 Backend projection (`ContextQueue` in the gateway)

**Sending side (voice server):**

- `context` entries are sent with `history.append{entries[]}`, each with its `seq`, once their outcome is final
  (immediately for `user` entries).
- A `pending` entry blocks later `context` entries from being sent, so order is kept. Playback outcomes settle
  within seconds.
- `run_input` entries (the delegated user words) are carried in `delegate` and never wait.

**Receiving side:** every context entry in the gateway has a delivery state: `queued` → `in_flight(epoch)` →
`committed`, with a way back to `queued`.

- **Take:** when the controller builds the next backend user-side message (the user message of a run, or the
  steer/redirect text), it takes all `queued` entries in `seq` order and marks them `in_flight(epoch)`.
  `backend_context_block` renders them as ordered, labelled lines, for example:
  - `[while you were working] user: "okay sure"`
  - `[voice frontend said] "Great."`
  - `[the system told the user] "Sorry, I ran into a problem."`
  - `[your last answer was cut off after: "Your order was cancel…"]`
  - `[your previous answer was not delivered to the user]`
- **Commit:** entries become `committed` **only when their epoch settles with committed history** (§5.3: `ok`,
  or `failed`/`interrupted` with returned messages). Being submitted to a run, or accepted by `steer()`, is not
  enough.
- **Requeue:** on rollback (`error`, `deadline` with no result, `worker_died`), all `in_flight(epoch)` entries go
  back to `queued` in their original `seq` order, ahead of anything newer. The delegated user words of the
  rolled-back run are requeued too, as `[earlier request, not completed] user: "…"`. Nothing is lost and
  nothing is duplicated.
- **`pending_steer`:** its entries stay `in_flight` and move to the new run's epoch.
- **Dedup and ack:**
  - A duplicate `history.append` `seq` is ignored (`context_duplicate`).
  - The gateway sends `history.ack{committed_upto}` after each commit.
  - The voice side never resends committed entries. On reconnect (not in v1) it would resend
    `seq > committed_upto`.

**What Hermes' history looks like:**

- The gateway's settled history only grows by **Hermes' own results** (the committed `messages` of a run). The
  controller never inserts rows and never edits rows. Everything injected rides the next user message or steer
  text. There are no `repair_message_sequence` merges and no prompt-cache break.
- The τ³ seed greeting is a `frontend_speech` entry with `seq` 0, outcome `heard` (τ³ plays its own greeting),
  route `context`.

### 7.4 Truthful spoken history

- A Hermes-native answer is in Hermes' history the moment its run commits, whether or not the user heard it.
  So the **only** correction path is a `delivery_note`, and it is created exactly when the answer's outcome
  becomes:
  - `partial`: "cut off after …";
  - `not_heard: superseded`, `dropped_stale` or `cancelled_before_audio`: "not delivered".
- Frontend lines (fillers, direct replies, status) and controller apologies reach Hermes **only as labelled
  context**, and only with their real outcome:
  - `heard`: the text;
  - `partial`: the heard text plus "(cut off)";
  - `not_heard`: omitted entirely.
- Controller apologies are generated on the voice side from gateway `answer{kind: "apology", reason}` events.
  They are labelled "[the system told the user]" and never shown to either agent as Hermes speech.
- `test_fdh_history_truth.py` (§15) checks each outcome × origin combination.

---

## 8. Tools

### 8.1 Worker-side bridge (`worker/tool_futures.py`, `worker/hermes_adapter.py`)

**Registration** (per worker process, so no cross-session collision is possible):

1. On `configure{tools}`, the worker registers **one bridge handler per tool name** with the session's own
   schema, in toolset `fdh_client`.
2. The agent is built with `enabled_toolsets=["fdh_client"]`: no terminal/web/file/memory tools. Also set:
   - `skip_context_files=True`;
   - **`load_soul_identity=True`**, so the SOUL.md in the worker's `HERMES_HOME` is loaded;
   - `skip_memory=True`, `skip_background_review=True`, `quiet_mode=True`, `session_db=None`.

**`ToolFutures` (keyed result futures).** Sending the call and waiting for its result are separate steps:

1. **Handler thread (Hermes):**
   1. Parse `epoch` from the `task_id` kwarg. If it is not the current epoch, return
      `"Error: stale run; tool not executed"`.
   2. Allocate `call_id = fdh_<session>_<epoch>_<n>`.
   3. Create a `concurrent.futures.Future` and register `pending[call_id] = PendingCall(future, epoch, deadline)`
      under a `threading.Lock`.
   4. Put `tool.call{call_id, epoch, name, arguments}` on the worker's **outbound writer queue** through
      `loop.call_soon_threadsafe`. This is fire-and-forget.
   5. Wait on the **result future**: `future.result(timeout=0.1)` in a loop, checking
      `tools.interrupt.is_interrupted()` and the deadline on each pass.
2. **`tool.result{call_id, output}`** (worker loop): pop `pending[call_id]` under the lock and `set_result(output)`
   if not done.

Every other case resolves or drops explicitly:

| Case | Behaviour |
|---|---|
| Timeout (`tools.result_timeout_s: 120`) | Pop, resolve with `"Error: the tool did not return a result in time"`, emit `tool.cancel{call_id}`, log `tool_timeout` |
| Interrupt (watchdog, close) | Pop, resolve with `"Error: interrupted"` |
| Gateway link lost | Resolve **all** pending with `"Error: session closed"`, then run the close sequence (§5.3) |
| Duplicate result | Ignored, logged `tool_result_ignored{reason: "duplicate"}` |
| Late result (after timeout or cancel) | Ignored, `reason: "late"` |
| Unknown `call_id` | Ignored, `reason: "unknown"` |
| Result for an old epoch | Ignored, `reason: "stale_epoch"` |
| Writer queue closed when enqueuing | Resolve at once with the link-lost error, so a handler never waits for a result that was never requested |

**Gateway routing:** the gateway forwards `tool.call` to the voice side through the session's outbound writer
and records `call_id → (epoch, executed=False)`. On `tool.result` it marks `executed=True` (for the §5.3
side-effect note) and forwards to the worker. When a worker dies, all its open calls get `tool.cancel` to the
voice side.

**Batching:** Hermes may run a batch in parallel threads. The voice side's `ToolRelay` collects `tool.call`s
that arrive within `tools.batch_window_ms: 30` into **one** Realtime response with several `function_call`
items. τ³ executes them in order and sends a single `response.create` after the last one.

### 8.2 Voice side (`ToolRelay`) and `response.create`

1. `ArgumentNormalizer.screen` runs first. An invalid ID or a repeated failure (retry guard) is **answered
   locally** with the `tool_argument_invalid` / `tool_call_already_failed` text. It goes back as
   `tool.result{local: true}` and never reaches τ³.
2. Otherwise emit the batch as `function_call` items. Each batch is a `ToolBatch(batch_id, call_ids,
   outputs, ack_pending)`.
3. `function_call_output` → `tool.result` to the gateway. An output for an unknown call id is a wire `error`, as
   today. An output for a call that was already cancelled is logged and dropped.
4. `tool.cancel` closes that call in its batch.
5. `transfer_to_human_agents` passes through unchanged (τ³ ends the run on it).

**`response.create` is interpreted in the same order as today's `on_response_create`
(`turn_manager.py:326-354`):**

| # | Condition | Meaning |
|---|---|---|
| 1 | A `ToolBatch` has `ack_pending` (all outputs in, or τ³ sent it before our `response.done`) | **Acknowledgement of that batch.** Consumed; no generation starts, because the backend resumes when its futures resolve. |
| 2 | A response is in progress (a frontend decision is running, or audio is generating) | `error{code: "conversation_already_has_active_response"}` |
| 3 | Manual-response mode (`protocol.auto_response: false` or `turn_detection.create_response: false`) and there is pending user input | **Start the turn**: the merged pending utterances go to the frontend decider, exactly like today's `_pending_inputs` merge. |
| 4 | Greeting enabled and not yet spoken | Speak the greeting. |
| 5 | Otherwise | `error{code: "invalid_value"}`, "no new user input" |

In auto-response mode (τ³ and the browser), committed user input starts the frontend turn directly, so case 3
never fires there.

### 8.3 Browser (`tools.source: config`)

The page cannot execute tools. The same bridge is used, but `ToolRelay` hands calls to a `LocalToolExecutor`
running `demo_tools.TOOLS` in the voice server, so no `function_call` goes on the wire.

- With `simulated_delay.where: per_tool_call` the sleep sits here. The default `per_delegation` sleep sits in
  the controller (D5).
- **Tool-argument normalization is disabled in the browser profile**, because the voice config rejects it
  unless `tools.source: client` (`voice_frontend_backend_agent/config.py:733-738`). Transcript normalization
  stays on.

---

## 9. Protocols (`backend/protocol.py`, `backend/worker_protocol.py`; stdlib dataclasses, JSON, `v: 1`)

Every connection end has **exactly one outbound writer task** that drains an `asyncio.Queue`. Nothing else
writes to a socket. This covers:

- the voice `BackendLink`;
- each gateway `SessionRuntime` (one queue toward the voice server, one toward its worker);
- each worker.

Messages from one producer are therefore never interleaved or reordered. Worker threads enqueue with
`loop.call_soon_threadsafe`.

### 9.1 Voice ⇄ gateway

**Voice → gateway**

| Message | Fields |
|---|---|
| `session.open` | `session_id`, `settings{simulated_delay, budgets, steer_mode, prompts_hash}` |
| `session.configure` | `tools[]`, `instructions` (transactional, §4.3) |
| `history.append` | `entries[]` (`seq`, `origin`, `kind`, `text`, `outcome`, `labels`), `context` route only |
| `delegate` | `turn_id`, `request: task\|status`, `run_input[]` (the delegated user entry with `seq`), `stamps` |
| `tool.result` | `call_id`, `epoch`, `output`, `local` (bool) |
| `session.close` | |

**Gateway → voice**

| Message | Fields |
|---|---|
| `session.ready` / `session.configured` | `worker_pid`, `hermes_version`, `model`; or `error` |
| `history.ack` | `committed_upto` |
| `state` | `state`, `epoch`, `run_id` |
| `action` | `turn_id`, `kind: start\|continue\|steer\|redirect\|status\|queued_in_delay`, `request` (as received), `run_id` |
| `tool.call` | `call_id`, `epoch`, `run_id`, `name`, `arguments` (JSON string) |
| `tool.cancel` | `call_id`, `reason` |
| `activity` | `epoch`, `tool_started\|tool_completed`, `name`, `preview` |
| `answer` | `answer_id`, `run_id`, `epoch`, `kind: hermes\|apology`, `text`, `reason`, `usage` (per-run delta) |
| `backend_run_done` | `run_id`, `epoch`, `status: ok\|failed\|interrupted\|error\|deadline\|worker_died\|closed`, `history: committed\|rolled_back`, `turn_ids[]`, `usage` |
| `status` | `turn_id`, `summary{…}` |
| `worker` | `event: started\|ready\|died\|respawned\|stopped`, `pid`, `reason` |
| `error` | `code` (`capacity`, `worker_start_timeout`, `tools_locked`, …), `message`, `fatal` |

**Rules:**

- The voice side opens the link on the first `session.update` and waits up to `backend.open_timeout_s` (which
  must exceed `workers.start_timeout_s` plus construction time in eager mode) for `session.configured`.
- A link lost mid-session leads to a spoken apology, then `error`, and the Realtime session closes.
- There is no WS keepalive ping (the τ³ "Not connected to API" gotcha).

### 9.2 Gateway ⇄ worker (Unix domain socket, JSON lines)

The gateway creates the socket under `workers.socket_dir` and passes its path as an argument. The worker's
stdout and stderr go to a per-worker log file, never to the control channel.

| Gateway → worker | Worker → gateway |
|---|---|
| `configure{tools, instructions_rendered, hermes settings}` | `ready{pid, python, hermes_version}` (process up) |
| `run{epoch, task_id, user_message, conversation_history, delay_s}` | `configured` / `error{phase: construct}` |
| `steer{epoch, text}` / `redirect{epoch, text}` | `steer_result{epoch, accepted, via: steer\|redirect}` |
| `status{epoch}` | `status_result{summary}` |
| `tool.result{call_id, output}` | `tool.call{call_id, epoch, name, arguments}` / `tool.cancel{call_id, reason}` |
| `interrupt{hard}` | `activity{epoch, …}` |
| `close` | `run_outcome{epoch, status, final_response, messages?, pending_steer?, usage_delta, error?}` / `closed` |

The worker processes commands in order. `run` is started on the executor thread, and the other commands are
served while it runs. A `steer`/`redirect`/`status` for an epoch that is not the current one is answered
`accepted: false`, and the controller then treats the delegation against the settled state.

---

## 10. Realtime event sequences

| Situation | Server events |
|---|---|
| User turn | `speech_started` → `speech_stopped` → `committed` → `conversation.item.added` (user) → `…input_audio_transcription.completed` (raw text on the wire; normalized text only goes to the agents) |
| Filler / direct reply | `response.created` → `output_item.added` (assistant, audio) → `content_part.added` → transcript deltas **before or with** audio deltas → `output_audio.done` / `transcript.done` → `output_item.done` → `response.done` |
| `delegate=false`, empty filler | No response |
| Backend tool calls | `response.created` → for each call: `output_item.added` (function_call) → `function_call_arguments.delta` / `.done` → `output_item.done` → `response.done` |
| Backend answer / status / apology | A new response, as for filler, with a **fresh `item_id`** (the τ³ `skip_item_id` rule) |
| Barge-in on audio | Existing `_interrupt_output` path: cancel, `conversation.item.truncated`, `response.done{status: cancelled}`, then the playback outcome `partial` (§7.4) |

All usage goes into `response.done.usage`:

- a filler response carries the frontend usage;
- a status response carries the verbalizer usage (D7);
- an answer response carries the backend **per-run delta**.

### 10.1 `OutputScheduler` rules (no double-talk)

1. At most one audio response is open at a time. Function-call responses carry no audio and are not held.
2. **Queued audio is never released while `user_speaking` is true** (between `speech_started` and
   `speech_stopped`).
3. After `speech_stopped`, queued backend audio also waits for that turn's frontend decision, so the new
   turn's filler goes first. The wait is bounded by `frontend.timeout_ms`.
4. `output.hold_max_ms: 4000` is **not a release timer**. It marks an item stale. What happens to a stale item
   is set per kind by `output.on_stale`:
   - `backend_answer: keep` (default): kept, and flushed only once rules 2-3 allow;
   - `status: drop` (default);
   - `filler: drop` (default);
   - `apology: keep` (default).

   A dropped item gets outcome `not_heard: dropped_stale` (§7.4).
5. When a later `answer` of the same run chain supersedes a queued one (a steer re-run), the older queued
   answer gets `not_heard: superseded`.
6. Every hold, release and drop is logged with timing.

---

## 11. Configuration (`config/delegation_agent.yaml`, `config/voice/*.yaml`, `config/profiles/`, `config/gateway.yaml`)

`load_voice_config` takes a **path** (`voice_frontend_backend_agent/config.py:951`). So the shared
audio/ASR/TTS/VAD/normalization settings live in real voice-profile files that this package ships, and the
delegation config references them by path. `load_delegation_config(path)` works in three steps:

1. Load and merge the delegation file chain (`extends:`, `${ENV:-default}`, strict keys against the new
   `DEFAULTS`). This reuses the voice package's generic helpers (`deep_merge`, `_load_chain` semantics),
   either by import or through a small shared helper.
2. Resolve `voice_profile` against the declaring file and call **`load_voice_config(voice_profile)`
   unchanged**. That file `extends:` the voice package's `voice_agent.yaml`.
3. Return `DelegationConfig(voice=VoiceConfig, frontend=…, delegation=…, backend=…, session=…, output=…,
   tools=…, instructions=…)`.

The gateway has its own file, `config/gateway.yaml` (loaded by `load_gateway_config`), because it is a
separate process that may serve several voice servers.

```yaml
# config/voice/base.yaml  — a real voice-package profile
extends: ../../../voice_frontend_backend_agent/config/voice_agent.yaml
turn_detection: {silence_duration_ms: 800, honor_client_values: false}   # V11
normalization:
  transcript: {enabled: true, case: lower, frontend_note_key: identifier_note_voice}   # V12
server: {port: ${FDH_VOICE_PORT:-8775}, ws_ping_interval_s: 0, max_sessions: ${FDH_MAX_SESSIONS:-8}}
logging: {event_log: ${FDH_EVENT_LOG:-logs/fdh_voice_events.jsonl}}

# config/voice/tau3.yaml
extends: base.yaml
tools: {source: client}
protocol: {greeting: {enabled: false}, seed_history_with_client_greeting: "Hi! How can I help you today?"}
normalization:
  tool_arguments: {enabled: true, rules: [...]}   # copied from the voice package's tau3_eval.yaml

# config/voice/browser.yaml
extends: base.yaml
tools: {source: config, config_tools: "prototypes.text_frontend_backend_agent.demo_tools:TOOLS"}
normalization: {tool_arguments: {enabled: false}}   # requires tools.source: client
audio: {pace_output: true}
protocol: {greeting: {enabled: true}}
server: {port: ${FDH_VOICE_PORT:-8776}}
```

```yaml
# config/delegation_agent.yaml  (voice-server side; defaults == τ³-eval-safe)
voice_profile: voice/tau3.yaml
frontend:
  decider: llm                         # llm | scripted
  llm: {model: ${FRONTEND_LLM_MODEL:-nvidia/nvidia/nemotron-3.5-lightning}, base_url: ${FRONTEND_LLM_BASE_URL}, thinking: false}
  timeout_ms: 4000
  history: {max_groups: 20}
  backend_activity: {max_items: 6, preview_chars: 120}
delegation:
  on_contract_error: delegate          # delegate | direct_empty
  speak_when_delegating: true          # D4
  guards: {backend_question_needs_delegate: true}
  prompts: {path: ${FDH_PROMPTS:-prompts.yaml}}
backend:
  link: websocket                      # websocket | in_process_fake
  url: ${FDH_BACKEND_URL:-ws://localhost:8790/v1/backend}
  open_timeout_s: 45                   # > workers.start_timeout_s + eager construction (validated when both configs are known)
  simulated_delay: {seconds: 0, where: per_delegation}   # V13: only browser_demo sets 5
  steer_mode: auto                     # auto | steer_only   (D6)
  status_verbalizer: {mode: llm, timeout_ms: 2000}   # llm | template (D7); template is also the fallback
  context_block_key: backend_context_block
session:
  update_after_start: error            # error | ignore   (§4.3)
output:
  hold_max_ms: 4000                    # marks items stale; never releases audio over the user (§10.1)
  on_stale: {backend_answer: keep, status: drop, filler: drop, apology: keep}
tools: {executor: wire, result_timeout_s: 120, batch_window_ms: 30}   # executor: wire | local
instructions: {apply_to: [backend]}
```

```yaml
# config/gateway.yaml  (backend gateway + workers)
gateway:
  host: 127.0.0.1
  port: ${FDH_GATEWAY_PORT:-8790}
  max_sessions: ${FDH_MAX_SESSIONS:-8}  # must be ≥ the sum of server.max_sessions of the voice servers it serves
  log: ${FDH_GATEWAY_LOG:-logs/fdh_gateway_events.jsonl}
workers:
  mode: process_per_session            # process_per_session | in_process_fake (tests only)
  python: ${FDH_HERMES_PYTHON:-~/.cache/fdh/hermes-venv-314/bin/python}
  agent_kind: hermes                   # hermes | fake
  start_timeout_s: 15
  stop_timeout_s: 10
  kill_grace_s: 3
  warm: 0                              # pre-started spare workers (set from P0 measurements)
  home_root: ${FDH_HERMES_HOME_ROOT:-.cache/fdh-hermes-homes}   # one HERMES_HOME per worker; runtime state, never committed
  keep_homes: false
  socket_dir: ${FDH_SOCKET_DIR:-/tmp/fdh-workers}
  log_dir: logs/fdh_workers
recovery: {respawn: true, max_respawns_per_session: 2}
hermes:
  model: ${BACKEND_LLM_MODEL:-nvidia/nvidia/nemotron-3-ultra}
  base_url: ${BACKEND_LLM_BASE_URL:-https://inference-api.nvidia.com/v1}
  api_key_env: NVIDIA_API_KEY
  provider: custom
  context_length: 131072               # written to each worker's HERMES_HOME/config.yaml (64K floor)
  agent_construct: eager               # eager | lazy (§5.1)
  max_iterations: 30
  run_budget_seconds: 300              # Hermes soft budget
  run_hard_deadline_s: 330             # gateway watchdog (§5.3)
  unwind_timeout_s: 10
  load_soul_identity: true             # required with skip_context_files
  prompts: {path: ${FDH_PROMPTS:-prompts.yaml}}   # SOUL.md and backend templates
```

| Profile | Differences |
|---|---|
| `tau3_eval.yaml` | `extends: ../delegation_agent.yaml` (the defaults already target τ³) |
| `browser_demo.yaml` | `voice_profile: ../voice/browser.yaml`, `tools.executor: local`, **`backend.simulated_delay.seconds: ${FDH_BACKEND_DELAY_S:-5}`** |
| `tau3_eval_silent_ack.yaml` | Optional ablation: `delegation.speak_when_delegating: false` |
| `stub.yaml` | `backend.link: in_process_fake`, used with `--stub-speech` for the no-GPU smoke run |
| `gateway.fake.yaml` | `workers.agent_kind: fake`, `workers.python: ${FDH_TEST_PYTHON}` (any Python): real processes without Hermes, for CI and local checks |

Environment variables: `NVIDIA_API_KEY`, `FRONTEND_LLM_*`, `BACKEND_LLM_*`, `FDH_VOICE_PORT`, `FDH_MAX_SESSIONS`,
`FDH_BACKEND_URL`, `FDH_BACKEND_DELAY_S`, `FDH_EVENT_LOG`, `FDH_PROMPTS`, `FDH_GATEWAY_PORT`, `FDH_GATEWAY_LOG`,
`FDH_HERMES_PYTHON`, `FDH_HERMES_HOME_ROOT`, `FDH_SOCKET_DIR`. The `FDH_` prefix keeps them apart from the
existing `FBA_` ones.

### 11.1 Configurability rules

- **One schema per process, strict.**
  - Each config module holds `DEFAULTS` for every section.
  - Unknown keys fail at load time with the dotted path.
  - Types and enums are validated, and each section becomes a frozen dataclass.
  - Cross-checks are validated:
    - tool-argument normalization requires `tools.source: client`;
    - `tools.executor: local` requires `tools.source: config`;
    - `run_hard_deadline_s > run_budget_seconds`;
    - `stop_timeout_s > unwind_timeout_s`.
  - At runtime, the gateway reports `max_sessions` on `/health`. The voice server refuses to start if its
    `server.max_sessions` exceeds the gateway's (a `startup` hook check).
- **Everything tunable is config.** This covers:
  - models, URLs and timeouts;
  - VAD and normalization;
  - prompts (template keys, not text in code);
  - the `delegate` tool description and the `request` enum description, so the prompt can be tuned without
    code changes;
  - guards, hold and stale rules, batching window, simulated delay, steer mode, verbalizer mode;
  - capacities, worker start/stop/kill timeouts, warm pool, respawn policy;
  - Hermes budgets, deadlines, `context_length`, construction mode;
  - home, socket and log paths;
  - ports.
- **Layering.** The order is base file → `extends:` profile chain → `${ENV:-default}` → CLI flags (`--port`,
  `--tls`, `--stub-*`, `--backend-url`, `--max-sessions`). Later layers win. A profile holds only what differs.
- **Implementation choice by name.** Swap points are selected by string keys resolved through a small
  registry (§12.1), for example `backend.link`, `tools.executor`, `workers.mode` and `workers.agent_kind`.
  Adding an implementation means registering a name, with no edits to callers.
- **Prompts as data.**
  - All prompts are Jinja templates in `prompts.yaml`: frontend system, state block, `delegate` tool texts,
    status verbalizer, backend SOUL/system, delegation/steer wrappers, context block labels, apology lines.
  - Templates are checked at load time (undefined variables fail), as in the voice package.
- **Observable.**
  - `session_start` (voice) and `session_open` (gateway) log the effective merged config (secrets redacted)
    and a config hash.
  - The τ³ report groups runs by hash.
- **Tested.** `test_fdh_config.py` covers:
  - every shipped profile and the gateway config loads;
  - the 800 ms VAD, normalization and delay invariants;
  - the cross-checks;
  - every key in `DEFAULTS` is documented in the README config table.

---

## 12. Module layout

```
src/prototypes/voice_delegation_hermes_agent/
  __init__.py
  README.md
  server.py                     # voice server CLI: --config --port --tls --stub-speech --stub-backend --max-sessions
  config.py                     # load_delegation_config, DEFAULTS, DelegationConfig
  config/delegation_agent.yaml, config/gateway.yaml, config/prompts.yaml, config/voice/*.yaml, config/profiles/*.yaml
  engine/
    turn_manager.py             # DelegationTurnManager (TurnManagerLike + on_session_update)
    output_scheduler.py         # response serialization, user-speaking gate, stale rules, playback outcomes
    transcript.py               # SharedTranscript (seq, origin, routes, outcomes), frontend projection
    response_create.py          # response.create correlation (§8.2)
  frontend/
    delegate_tool.py            # DELEGATE_TOOL schema (delegate, filler_text, request) + parse/validate
    decider.py                  # FrontendDecider (LLM call, repair, guards)
    status_verbalizer.py
  backend/                      # stdlib-only, imports on 3.12 and 3.14 (shared contract)
    protocol.py                 # voice ⇄ gateway messages
    worker_protocol.py          # gateway ⇄ worker messages
    controller.py               # BackendController: states, RunHandle/epochs, settle_run_locked, outcome table
    context_queue.py            # queued → in_flight → committed, requeue, dedup, ack
    agent_like.py               # AgentLike Protocol + FakeAgent (scriptable; can hang, raise, or crash the process)
    writer.py                   # single outbound writer over an asyncio.Queue
    link.py                     # BackendLink Protocol, WebSocketBackendLink, InProcessBackendLink
  tools/relay.py                # ToolRelay (wire | local), ToolBatch, normalization screen
  sidecar/                      # backend gateway; Hermes-free (stdlib + fastapi/uvicorn)
    gateway_server.py           # FastAPI WS /v1/backend, GET /health, capacity guard
    gateway_config.py           # load_gateway_config
    session_runtime.py          # per session: controller, context queue, routing table, watchdog, writers
    worker_pool.py              # spawn / ready / assign / stop / SIGTERM / SIGKILL / warm pool / respawn
    homes.py                    # per-worker HERMES_HOME (SOUL.md, config.yaml), cleanup
  worker/                       # runs in the worker process
    worker_main.py              # stdlib control loop: socket, writer queue, executor, command handling
    tool_futures.py             # keyed PendingCall registry (stdlib)
    fake_host.py                # FakeAgent host (agent_kind: fake)
    hermes_adapter.py           # the ONLY Hermes import: AIAgent build, bridge handlers, task_id/epoch, usage snapshots
  cli/
    delegation_replay.py        # §6.4
    backend_probe.py            # P0 spike: drives one worker or the gateway directly
    report_adapter.py           # §14
misc/prototypes/frontend-delegation-hermes/
  workflow.csv, prototype-plan.md (this), runbook.md (P6), delegation_cases.jsonl
tests/unit/prototypes/delegation/   # files named test_fdh_*.py (pytest imports by basename)
```

Layering rules, enforced by a test like the voice package's:

- `backend/`, `worker/tool_futures.py`, `worker/worker_main.py` and `worker/fake_host.py` import only the stdlib
  (plus `backend/`).
- `sidecar/` imports `backend/`, the stdlib and FastAPI/uvicorn. It never imports Hermes.
- `worker/hermes_adapter.py` is the only module that imports Hermes.
- The voice side (`engine/`, `frontend/`, `tools/`, `server.py`) never imports `sidecar/`, `worker/` or Hermes.
  It does not import pipecat, riva or fastapi outside `server.py`.

### 12.1 Modularity: ports and swap points

Each unit depends only on `Protocol`s. Wiring happens in one place per process: `server.py` (voice),
`gateway_server.py` (gateway) and `worker_main.py` (worker). Nothing constructs its own collaborators.

| Port (`Protocol`) | Responsibility | Implementations (config key) |
|---|---|---|
| `TurnManagerLike` | the voice session's turn logic | voice `TurnManager` (default), `DelegationTurnManager` — `SessionHooks` |
| `FrontendDecider` | user turn + transcript + backend state → `Decision(delegate, filler_text, request)` | `llm` (OpenAI-compatible), `scripted` (tests/replay) — `frontend.decider` |
| `StatusVerbalizer` | status summary → one spoken sentence | `llm`, `template` — `backend.status_verbalizer.mode` |
| `BackendLink` | voice ⇄ gateway messages (§9.1) | `websocket`, `in_process_fake` — `backend.link` |
| `WorkerHandle` | gateway ⇄ one worker (§9.2) | `process` (Unix socket), `in_process` (tests) — `workers.mode` |
| `WorkerLauncher` | start / stop / kill a worker | `subprocess` (process group, timeouts), `fake` (tests) |
| `AgentLike` | the Hermes surface the worker uses (`run_conversation`, `steer`, `redirect`, `get_activity_summary`, `clear_interrupt`, `hard_interrupt`, `close`, `model_request_active`, `usage_snapshot`) | `HermesAgentAdapter`, `FakeAgent` — `workers.agent_kind` |
| `SteerPolicy` | WORKING + `task` → `redirect` or `steer` | `auto`, `steer_only` — `backend.steer_mode` |
| `ToolExecutor` | run one batch of backend tool calls | `wire` (Realtime `function_call`), `local` (in-process callables) — `tools.executor` |
| `ArgumentScreen` | pre-screen tool calls, answer locally | `ArgumentNormalizer` (reused), `passthrough` — `normalization.tool_arguments.enabled` |
| `TranscriptNormalizer` | ASR text → agent text | reused normalizer, `identity` — `normalization.transcript.enabled` |
| `ContextRenderer` | queued context entries → text block | `labelled_lines` (default); template key `backend.context_block_key` |
| `OutputScheduler` policy | order, user-speaking gate, stale rules, playback outcomes | `default` with configurable hold and stale rules |
| `DelayInjector` | simulated backend delay | `none`, `per_delegation`, `per_tool_call` — `backend.simulated_delay.where` |
| `EventSink` | structured events | JSONL `EventLog` (reused), in-memory (tests) |
| Speech / VAD / wire | ASR, TTS, VAD, codec | reused from the voice package (Riva, stubs, Silero, energy) |

Rules:

- Modules stay small, with one responsibility each. The pure logic (controller, context queue, tool futures,
  decider parsing, projector, scheduler, relay, `response.create` correlation) holds no I/O and can be tested
  without sockets.
- `backend/` is the contract shared by all three processes and imports only the stdlib.
- Swapping Hermes for another backend agent means implementing `AgentLike` in a new worker adapter, with no
  gateway or voice-side change.
- The layering test (§15) enforces the import rules.

---

## 13. Running it

**Hermes venv** (once; this does not touch the Hermes checkout's existing 3.12 `.venv`):

```bash
cd ../hermes-agent-smasurekar
UV_PROJECT_ENVIRONMENT=$HOME/.cache/fdh/hermes-venv-314 uv sync --python 3.14
cd -
```

**Gateway** (host-native, this repo's environment; it spawns workers with the 3.14 interpreter):

```bash
FDH_HERMES_PYTHON=$HOME/.cache/fdh/hermes-venv-314/bin/python FDH_MAX_SESSIONS=8 PYTHONPATH=src \
  uv run python -m prototypes.voice_delegation_hermes_agent.sidecar.gateway_server \
  --config src/prototypes/voice_delegation_hermes_agent/config/gateway.yaml
```

Workers inherit `PYTHONPATH=src` for the package's stdlib modules. Hermes comes from the 3.14 venv. One gateway
serves both voice servers, as long as its `max_sessions` is at least the sum of theirs.

**Voice server** (τ³): the same `docker compose --profile frontend-backend-agent/single-gpu run` command as
the voice runbook, with these changes:

- `--name fdh-voice -p 8775:7860 --add-host=host.docker.internal:host-gateway`
- `-e FDH_BACKEND_URL=ws://host.docker.internal:8790/v1/backend -e FDH_MAX_SESSIONS=4`
- `-e FDH_EVENT_LOG=logs/fdh_voice_events.jsonl`
- `uv run python -m prototypes.voice_delegation_hermes_agent.server --config …/profiles/tau3_eval.yaml --port 7860`

The gateway must then listen where the container can reach it: `gateway.host: 0.0.0.0`, restricted to the
Docker bridge by the host firewall, or bound to the bridge address.

**Browser:** `--name fdh-voice-web -p 8776:7860 -e PIPELINE_TLS=true -e FDH_MAX_SESSIONS=4`, the same
`FDH_BACKEND_URL`, `--config …/profiles/browser_demo.yaml --tls`. Open `https://<host-ip>:8776/`. Several tabs
work at once, up to the capacity.

No Dockerfile or Compose edits. Containerizing the gateway and workers (a `python:3.14` image with both repos
mounted) is a follow-up.

**τ³:** the existing `tau3_run` helper hard-codes its arms (`paired|bo|hist|histng|norm|verdictspk`) and
rejects anything else, so this plan does **not** use it. Until the dedicated τ³ runbook defines its own
helper, use the underlying command directly in the `tau2-bench-smasurekar` checkout:

```bash
RUN=fdh_voice_dlg_mock_control_smoke
PINE_REALTIME_BASE_URL=ws://localhost:8775/v1/realtime PINE_API_KEY=unused \
uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run --domain mock --audio-native \
  --audio-native-provider openai --audio-native-model pine-fdh-voice-dlg-mock-control-smoke \
  --speech-complexity control --user-llm openai/azure/openai/gpt-5.2 \
  --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" --review-model openai/azure/openai/gpt-5.2 \
  --task-split-name base --num-trials 1 --max-concurrency 1 --num-tasks 1 --save-to "$RUN" --verbose-logs
```

**Concurrency and τ³:**

- **Reportable runs use `--max-concurrency 1`.** The τ³ metrics join events to simulations by a unique model
  tag per run (check C1), and every latency figure assumes concurrency 1.
- `--max-concurrency 2` is a **functional isolation check only** (P6). Its rewards are recorded, but its
  latencies are not reported.

---

## 14. Observability and τ³ report compatibility

`fba_voice_metrics.py` assumes **one backend operation per frontend turn**: `agent_turn_done` carries a turn's
frontend and backend usage together. In this design:

- a turn can trigger zero runs (`delegate=false`), one run, a steer into another turn's run, or a status
  lookup;
- one run can span several turns (steers, `pending_steer` re-runs) and can be rolled back.

So the server does **not** fake `agent_turn_done`. It logs explicit link events, and a reporting adapter builds
the legacy per-turn records.

**Native events** (voice `logs/fdh_voice_events.jsonl`, gateway `logs/fdh_gateway_events.jsonl`, worker logs in
`logs/fdh_workers/`):

- Kept with the same names and meaning: `session_start` (with `model` and the config hash),
  `speech_stopped`, `tool_output_in` (with `call_id`), `filler_timing`, `turn_latency`.
- New turn-level events:
  - `delegation_decision{turn_id, delegate, request, filler_chars, backend_state, latency_ms, usage, repair}`
  - `delegation_guard`
  - `backend_action{turn_id, kind, run_id, epoch}`
- New run-level events:
  - `backend_run_started{run_id, epoch, turn_id, kind: start|continue|pending_steer|retry}`
  - `backend_run_input{run_id, turn_id, kind: steer|redirect|queued_in_delay}`
  - `backend_run_done{run_id, epoch, status, history, turn_ids[], usage_delta, tool_call_ids[]}`
- Output and history events:
  - `playback_outcome{seq, origin, outcome, heard_chars}`
  - `backend_answer{run_id, answer_id, kind}`
  - `status_spoken{turn_id}`
  - `context_state{seqs, from, to, epoch}` (queued/in_flight/committed/requeued)
  - `output_hold` / `output_release` / `output_dropped_stale`
- Tool events: `tool_timeout`, `tool_result_ignored`.
- Worker and gateway events (gateway log): `worker{event, pid, reason, start_ms, rss_mb}`,
  `capacity_refused`, `sidecar_link`.

**`cli/report_adapter.py`** reads the logs and writes `fdh_voice_<run>_events.legacy.jsonl` for
`fba_voice_metrics.py --event-log dlg=…`:

- **Attribution:** a run's usage, tool calls and answer are attributed to the turn that **started** it.
  - Retries after rollback keep the original starting turn.
  - Steer and status turns get frontend usage only, plus a `backend_link: steer|status` marker.
- **`agent_turn_done`:** one per user turn, with `frontend` usage from `delegation_decision` (plus the
  verbalizer for status turns) and `backend` usage from the attributed runs.
- **Latency:** `turn_latency` for a delegated turn is end of speech → first **heard** audio of the attributed
  answer. `first_audio_ms` (end of speech → first filler audio) is a separate column.
- **Extra columns:** runs per session, steers per run, status count, rollbacks, worker restarts, delegation
  rate, and `request` label distribution.
- **Self-checks:** exit code 1 if any tool `call_id` from the voice log has no run attribution, or any run has
  no starting turn.

---

## 15. Tests

**CI (offline, this repo's Python; fakes only)**

| File | Covers |
|---|---|
| `test_fdh_workflow_table.py` | **Parametrized over all 12 CSV rows** with the RC1 three-field contract. A scripted frontend decision × a `BackendController` in each state gives the expected `delegate` flag and the expected `AgentLike` call (`__init__`/`run_conversation`/`steer`/`redirect`/`get_activity_summary`). The CSV is loaded from `misc/…/workflow.csv`, so the spec and the tests cannot drift apart. |
| `test_fdh_controller.py` | **Epoch/settle race:** a delegation queued on the lock while the outcome arrives is classified against the settled state; `settle_run_locked` is idempotent; stale-epoch events are dropped. **Outcome table** (§5.3): commit vs rollback per status; the side-effect note after rollback. Also: `pending_steer` re-run; `redirect` → `steer` fallback; construction failure → NO_SESSION; watchdog → interrupt → kill → respawn limit; per-run usage deltas; the simulated delay absorbing steers. |
| `test_fdh_context_queue.py` | `queued → in_flight → committed` only on committed settle; requeue on `error`/`deadline`/`worker_died` in original order ahead of newer entries; failed-run user words requeued once; `pending_steer` carries `in_flight` entries; duplicates ignored; ack watermark |
| `test_fdh_history_truth.py` | Every origin × playback outcome: heard, partial and not-heard fillers, status and apologies reach Hermes only as labelled context, with their real outcome or not at all; superseded and dropped Hermes answers get "not delivered" notes; cut answers get "cut off" notes; no Hermes row is ever edited or inserted; the frontend projection shows heard text only |
| `test_fdh_tool_futures.py` | Keyed result future vs. send; `task_id` epoch check refuses stale calls; timeout → `tool.cancel`; interrupt; link lost resolves all; duplicate, late, unknown and stale-epoch results ignored; enqueue on a closed writer resolves at once |
| `test_fdh_writer.py` | Single writer per connection: concurrent producers (threads + tasks) never interleave frames; the order per producer is kept; close drains or fails pending sends deterministically |
| `test_fdh_decider.py` | `request` parsing (default `task`, invalid → `task`), contract repair table (§6.3), the `backend_question_needs_delegate` guard (heard messages only), state block rendering, `tool_choice` |
| `test_fdh_tool_relay.py` | Batching window → one response with N `function_call`s; local answers from `ArgumentNormalizer`; `tool.cancel` handling; `LocalToolExecutor` for the browser |
| `test_fdh_response_create.py` | The §8.2 order: batch ack consumed; **active response rejected before manual input or greeting**; manual-response mode starts merged pending input; greeting; no-input error |
| `test_fdh_session_update.py` | Transactional update: a rejected tools/instructions change leaves `view.public()`, input format and backend unchanged; `ignore` policy commits the old tools; `session.updated` only after the gateway's `session.configured` |
| `test_fdh_output_scheduler.py` | Never releases audio while the user speaks, even after `hold_max_ms`; waits for the new turn's filler; `on_stale` per kind; superseded answers; playback outcomes recorded; function calls not held; a fresh `item_id` after barge-in |
| `test_fdh_regular_speech.py` | With a scripted decider: backchannels and non-directed speech while WORKING give no response and no steer; a backchannel during a backend answer follows the barge-in rules; vocal tics in front of a task still delegate. With `SessionHarness` + energy VAD: `[pause]`-split utterances under 800 ms VAD. |
| `test_fdh_protocol.py` | Round trip for every voice⇄gateway and gateway⇄worker message; version mismatch → error |
| `test_fdh_gateway_processes.py` | **Real worker processes with `agent_kind: fake`** (any Python), over real Unix sockets: (1) two concurrent sessions register **the same tool names with different schemas** and each worker sees only its own; (2) tool results never cross sessions (interleaved calls with identical names); (3) one worker that hangs (killed after the deadline) or crashes (`os._exit`) does not affect the other session's run; (4) capacity refusal only after `max_sessions` workers are live, including starting and stopping ones; (5) client disconnect → close → SIGTERM → SIGKILL escalation, `HERMES_HOME` removed, slot freed; (6) start timeout → `worker_start_timeout`; (7) respawn after a crash continues from the settled history |
| `test_fdh_ws_end_to_end.py` | `build_app(session_hooks=…)`, `--stub-speech` and `InProcessBackendLink`: τ³ fixture `session.update` (mock domain) → user audio → filler audio → `function_call` → output + `response.create` (ack) → answer audio. Asserts the τ³ contract with the existing `tau2_replay` checker. Also two concurrent WebSocket sessions. |
| `test_fdh_config.py` | Every shipped profile and the gateway config loads; VAD is 800 ms / `honor_client_values: false` everywhere; normalization enabled by default; tool-argument normalization off in the browser profile; `simulated_delay.seconds == 0` everywhere except `browser_demo`; cross-checks (§11.1) |
| `test_fdh_report_adapter.py` | Synthetic logs with a steer, a status turn, a `pending_steer` re-run, a rollback + retry and a `delegate=false` turn → the expected legacy `agent_turn_done` records and self-checks |
| `test_fdh_layering.py` | The import rules in §12 |
| `test_voice_session_hooks.py` (voice package) | Defaults unchanged (byte-identical trace); with hooks no `AgentPort`/`AgentClients` is built; `preview()` never mutates the view; `on_session_update` awaited before `session.updated`; hook exceptions → wire `error` |

**Hermes integration** (Python 3.14 venv; skipped unless Hermes is importable; run in P0/P2 and before each
campaign)

| File | Covers |
|---|---|
| `test_fdh_hermes_adapter.py` | Against a fake OpenAI-compatible HTTP server: the bridge; `task_id` epoch binding; `load_soul_identity` loads the per-worker SOUL; the steer delivery point; `pending_steer`; `get_activity_summary` keys; usage snapshot names; `close()` after unwind |
| `test_fdh_hermes_multisession.py` | Two real Hermes workers with **overlapping tool names and different schemas**: each model request carries only its session's schema; results stay in their session; killing one worker mid-tool-call leaves the other's run intact |

---

## 16. Phases and gates

| Phase | Work | Gate |
|---|---|---|
| **P0 — Hermes spike** (about 1 day) | Build the 3.14 venv. Construct `AIAgent` against the Inference Hub `nemotron-3-ultra` with `provider=custom`: the 64K check passes with `context_length`, reasoning config works, it starts quietly with a per-process `HERMES_HOME`, and SOUL loads with `load_soul_identity=True`. `cli/backend_probe.py` checks, in a worker process: a bridged tool with a keyed future and `task_id` epoch; `steer` during a tool wait; `redirect` during a model call; `pending_steer`; `get_activity_summary` keys; `clear_interrupt`; usage counter names; `hard_interrupt` unwind time during a blocked tool. **Measure:** worker spawn → `ready` time, `AIAgent` construction time, RSS per worker. These set `start_timeout_s`, `agent_construct`, `warm` and a realistic `max_sessions`. | **G0:** all behave as §2 describes; spawn + construction fit inside τ³'s 30 s connect timeout with margin (or `warm > 0` is chosen); otherwise the plan is revised before P1 |
| P1 | `backend/` (protocols, controller, context queue, writer, `FakeAgent`, links) and tests | `test_fdh_workflow_table`, `_controller`, `_context_queue`, `_history_truth` (controller half), `_tool_futures`, `_writer`, `_protocol` green |
| P2 | `worker/` + `sidecar/` (gateway, runtime, pool, homes) | `test_fdh_gateway_processes` green in CI (fake workers); `test_fdh_hermes_adapter` and `_hermes_multisession` green in the 3.14 venv; `backend_probe` against the real gateway covers the 12 CSV cells with text, no audio |
| P3 | `frontend/` (decider, prompts, verbalizer) + `delegation_cases.jsonl` + replay | **G3:** at least 95 % on `delegate`, at least 95 % on `request` in WORKING cells, 100 % on "answer to backend question" cases, and at least 95 % on each regular-speech class |
| P4 | Voice hooks (§4.3, including `preview()`), `DelegationTurnManager`, `OutputScheduler`, `response.create` correlation, `ToolRelay`, config, profiles, server | Offline end-to-end green; the full `uvx ruff@0.15.6 check/format --check` and `uv run pytest tests/` green |
| P5 | Normalization wiring, event logs, `report_adapter.py` | Offline `tau2_replay` "tau2 contract: OK"; Gate A handshake on 8775; adapter self-checks pass on a stub session |
| P6 | `runbook.md` (this folder), package README, browser check with the 5 s delay and two tabs | **G6**, in order: (1) the §13 command on `mock control --num-tasks 1`; (2) `airline control --num-tasks 5` go/no-go; (3) **`airline regular --num-tasks 5`** (backchannels, vocal tics, non-directed speech); (4) **`mock control --num-tasks 4 --max-concurrency 2`** as the isolation check: no crossed tool results, no capacity errors, worker count back to 0 afterwards; (5) a browser session covering the 4 query types while WORKING, plus a second tab at the same time |
| P7 | Reportable runs (in the later τ³ runbook): `dlg` vs **`verdictspk`** (the audible-filler arm), all 3 domains, `regular`, same campaign, same commit, `--max-concurrency 1` | Pass^1 + latency table through `report_adapter.py` + `fba_voice_metrics.py`. The silent `paired` arm is shown only as a reference and never compared for Pass^1. |

---

## 17. Risks

| Risk | Mitigation |
|---|---|
| Worker startup (Python 3.14 + Hermes import + `AIAgent`) is slow, hurting τ³'s 30 s handshake or the first turn | P0 measurement; `agent_construct: lazy` or `workers.warm > 0`; `open_timeout_s` sized from measurements |
| Memory per worker limits concurrency | RSS measured in P0 and logged per worker; `max_sessions` set from it |
| A split utterance (a spelled ID across a VAD gap) is delegated half-finished, and the second half becomes a steer | 800 ms VAD. The steer arrives before the first tool call in most cases (redirect while the model is thinking). The retry guard in normalization. Measure the rate in P6 from `backend_run_input=steer` within 2 s of `backend_run_started`. |
| A steer is only delivered at a tool boundary, so a long tool-free generation ignores it | D6 `auto` uses `redirect` during model requests; `pending_steer` re-run |
| A rolled-back run had executed side-effecting tools | The side-effect note (§5.3) tells Hermes what already ran; the routing table records executions |
| The frontend mislabels `request` | Few-shot rules in the prompt, the G3 gate on `request`, and logging of every `request`→action pair |
| The frontend delegates "yes" answers wrongly, or holds them | The prompt rule, the code guard (§3), and the G3 replay gate |
| Regular speech (backchannels, non-directed speech) triggers steers or spurious replies | Prompt rules, regular-speech replay classes (G3), `test_fdh_regular_speech.py`, the `airline regular` step in G6 |
| Double-talk | The user-speaking gate (§10.1), where stale is never "release now" |
| Held answers pile up behind a talkative user | `on_stale` per kind, superseded answers, and the hold duration in the report |
| A hung Hermes run | The gateway watchdog kills the worker process (no thread abandonment); rollback, requeue, bounded respawn |
| Orphaned workers after a gateway crash | Workers exit when their socket closes; each has its own process group; the gateway clears stale sockets and homes under its roots at startup |
| Hermes identity or style leaking (markdown, long answers) | SOUL.md per worker, loaded via `load_soul_identity=True`, a voice-style block in `system_message`, TTS text normalizer |
| τ³ freezes (up to 90 s) while Hermes blocks on a tool future | 120 s tool timeout, no pings on any link |
| Frontend latency is now on every turn | `nemotron-3.5-lightning` with thinking off and a short prompt; `first_audio_ms` is tracked. Speculative backend start is a follow-up. |

## 18. Out of scope / follow-ups

- The dedicated τ³ runbook and its arm helper for this prototype.
- Restarting the worker when tools or instructions change after the agent exists.
- Link reconnection with resend of `seq > committed_upto`.
- Containerizing the gateway and workers.
- Streaming `filler_text` from partial tool-call arguments into TTS.
- Speculative backend start before the frontend decides.
- Using Hermes `SessionDB` persistence.
- `hard_interrupt` on "cancel that" (would need a CSV row).
- Making the browser page show backend state (an `x_nvidia.backend_state` event).

## 19. Revision log

- **Revision 6 (2026-09-30).** The τ³ failure fixes of
  [`tau3-failure-fixes-plan.md`](tau3-failure-fixes-plan.md) are implemented, each behind a switch that is off
  by default. With every switch off, the rendered prompts are byte-identical to agent commit `3a7e04a`.
  - Prompt variants: `prompt_features` in `delegation_agent.yaml` (frontend) and `gateway.yaml` (backend).
  - Voice server: proactive status (M1), spelling hold (M3.2), answer replay (M2, RC2 in §0.2), filler
    de-duplication (G1) and answer cleanup before TTS (G2).
  - Shared normalization (additive, off by default): spelled-run joining and escalating local "invalid ID"
    wording (M3.1, M3.3).
  - Deployed fingerprints in the voice and gateway logs, checked by `cli/fingerprint_check.py`. The control
    arm is `config/profiles/tau3_eval_baseline.yaml`; the arms are `config/profiles/tau3_arm_*.yaml`.
  - The gateway protocol is unchanged; `session.configured` carries optional fingerprint fields.
- **Revision 5 (2026-09-29).** Implemented. Implementation notes, P0 measurements and deviations are in §22.
- **Revision 4 (2026-09-29).** Addresses the re-review (§21).
  - D8 changed to one Hermes worker process per Realtime session behind a gateway, with configurable capacity at
    both layers.
  - D9 added: conversation state lives in the gateway and the worker is a disposable agent host, so a killed
    worker is replaced from settled state.
  - Transactional `session.update` via `RealtimeSessionView.preview()`.
  - Context delivery states `queued → in_flight → committed`, with requeue on rollback, and the run-outcome
    table (§5.3).
  - Playback outcomes and truthful spoken history (§7.4).
  - A single outbound writer per connection, and `task_id`-to-epoch binding.
  - The `response.create` order is fixed to match the existing implementation.
  - Per-worker `HERMES_HOME`, worker start/stop/kill timeouts, respawn policy, and multi-session and
    process-isolation tests.
- **Revision 3 (2026-09-29).** Addressed the first design review (§20).
- **Revision 2 (2026-09-29).** Decisions confirmed: RC1 (`request` field) instead of a classifier LLM call; D7
  frontend-LLM verbalization; modularity and configurability sections.
- **Revision 1 (2026-09-29).** First draft.

## 20. First review traceability (revision 3)

| Review item | Where addressed |
|---|---|
| 1. Tool contract changed | §0.1 RC1 (approval recorded), V1, §15 workflow test |
| 2. Realtime hook insufficient | §4.3 (`on_session_update`, no eager agent/clients, later-update policy) |
| 3. History not exact or chronologically safe | §7 (routes, frontend re-projection, backend delivery states, delivery notes, append-only) |
| 4. Completion race | §5.2 (epochs, idempotent `settle_run_locked()`, settle-first commands) |
| 5. Multi-session tool routing | Superseded by D8 in revision 4 (process per session) |
| 6. Hermes lifecycle | §5.3, §5.4, §8.1 (`load_soul_identity`), P0 checks |
| 7. Tool bridge future | §8.1 `ToolFutures` + cleanup table |
| 8. `response.create` | §8.2 (order corrected in revision 4) |
| 9. Output hold double-talk | §10.1, V16 |
| 10. Config and τ³ validation | §11, §13, §14, D4/§16 P7, §6.4/§15/G6 |

## 21. Re-review traceability (revision 4)

| Re-review item | Where addressed |
|---|---|
| 1. Multi-session architecture (process per session) | D8, D9, §4 diagram, §4.2, §5.3 (kill and respawn instead of abandoning threads), §9.2, §11 `gateway.yaml`, §13, `test_fdh_gateway_processes.py`, `test_fdh_hermes_multisession.py`, G6 step 4 |
| 2. Transactional `session.update` | §4.3 items 2-3 (`preview()` → hook → commit), later-update table, `test_fdh_session_update.py`, `test_voice_session_hooks.py` |
| 3. Failure-safe history | §5.3 outcome table, §7.3 delivery states and requeue, `test_fdh_context_queue.py` |
| 4. Truthful spoken history | §7.1 origins and playback outcomes, §7.4, §10.1 rule 4-5, `test_fdh_history_truth.py` |
| 5. Serialized output, `task_id` ↔ epoch | §9 single-writer rule, §5.2 binding, §8.1 handler step 1, `test_fdh_writer.py`, `test_fdh_tool_futures.py` |
| 6. `response.create` ordering | §8.2 table (active response rejected before manual input and greeting), `test_fdh_response_create.py` |
| 7. Operational cleanup | §4.2 capacity and worker lifecycle, §11 (capacities, timeouts, per-worker homes, cross-checks), §11.1, `test_fdh_gateway_processes.py` items 1, 4-6 |

## 22. Implementation notes (revision 5)

### 22.1 P0 spike results (Hermes `af26acab73`, Python 3.14.7, Inference Hub `nvidia/nvidia/nemotron-3-ultra`)

| Check | Result |
|---|---|
| Worker process: `import run_agent` | 0.5 to 1.9 s (cold cache); about 115 MB RSS after import |
| `AIAgent(...)` construction | 0.2 to 0.7 s; about 155 MB RSS per worker after a run |
| One tool call + answer (`get_order`) | 5.6 s end to end, 2 model calls |
| `task_id="<session>:<epoch>"` | Reaches the tool handler as the `task_id` kwarg |
| `steer()` during a blocking tool | Delivered after the tool as an out-of-band user row; both requests answered |
| `redirect()` during a model request | `_model_request_active` set, `redirect()` returns `True`; the run switches to the new request |
| `hard_interrupt()` during a 30 s blocking tool | The run unwinds in about 4 s (`interrupted=True`) |
| Usage counters | `session_prompt_tokens`, `session_completion_tokens`, `session_total_tokens`, `session_input_tokens`, `session_output_tokens`, `session_reasoning_tokens` (cumulative) |

**G0 passed.** Spawn plus construction is 1 to 3 s, far inside τ³'s 30 s connect timeout, so
`agent_construct: eager` and `workers.warm: 0` are the defaults.

### 22.2 Deviations and additions

- **Hermes tool search must be off.** Hermes' default config hides plugin tools behind
  `tool_search` / `tool_describe` / `tool_call`. The spike's first run needed 3 extra model calls to reach
  one tool. Each worker's `HERMES_HOME/config.yaml` sets `tools.tool_search.enabled: "off"`, plus the
  `agent.*` guidance switches from `tau2-bench-smasurekar/tau2-hermes`. `HERMES_YOLO_MODE=1` is exported
  before Hermes is imported, and the tool surface is checked at construction.
- **Frontend `tool_choice` is `named`, not `required`.** On the Inference Hub, `tool_choice: "required"`
  makes nemotron-3.5-lightning repeat the `delegate` call until `max_tokens`: 256 tokens and about 3 s,
  versus about 40 tokens with a named function choice. `frontend.tool_choice: named | required | auto`
  and `frontend.parallel_tool_calls` are configurable.
- **Hedged frontend requests** (`frontend.hedge_after_ms: 1500`). The endpoint's latency tail is long:
  7 of 51 replay decisions hit the 4 s timeout before hedging. If no reply arrives by the hedge time, an
  identical second request races the first, and the first valid reply wins. This cut p90 from 4.0 s to
  2.1 s.
- **Backchannel guard** (`delegation.guards.backchannel_words`). The model sometimes delegated "mm-hmm"
  while WORKING. A turn that is exactly one configured backchannel word is never delegated and gets no
  filler, unless the backend's last heard message asked a question. This is an exact-match guard, not
  keyword routing: status vs task still comes only from the frontend's `request` field.
- **The gateway link opens at session start**, not at the first `session.update`, so worker spawn overlaps
  τ³'s handshake. `session.updated` still waits for `session.configured`.
- **A lost gateway link does not close the Realtime session.** The voice side emits
  `error{code: backend_unavailable}`, and every later delegation is answered with a spoken apology. τ³
  then scores the task normally.
- **Supersede.** `not_heard: superseded` exists in the transcript model, but nothing triggers it yet:
  queued backend answers are all spoken in order, because dropping one could lose a result the user
  asked for.
- **Voice package edits** are exactly §4.3: `wire/session_view.py` (`preview`), `engine/turn_api.py` (new),
  `engine/session.py` (`turn_manager_factory`, transactional update), and `server.py` (`SessionHooks`).
  The existing voice tests still pass. `test_voice_config.py::...two_working_directories` fails on a
  clean `HEAD` as well: it expects 13 shipped voice profiles and finds 14. That failure predates this work.

- **Found while bringing up the stack (runbook §4–5):**
  - `frontend.warmup: true`: without it, the first decision of a fresh server paid the HTTPS connection
    setup and timed out.
  - A turn started by a client `response.create` that has nothing to say gets an empty response
    (`response.created` + `response.done`). Otherwise a manual-mode client waits forever.
  - A status line is dropped (`status_dropped`, outcome `not_heard: superseded`) when the run it described
    has already finished by the time it is ready or about to play.
  - `backend.status_verbalizer.timeout_ms` is 3000; 2000 was often exceeded on the Inference Hub.
  - `workers.fake_default_tool` and `profiles/stub_gate.yaml` let τ³ Gate A pass with no GPU or LLM.
- **Known limitation.** A single turn that is both a status question and a new task ("what's the update,
  and also check 5513") gets one `request` label. When it is labelled `status`, the new instruction still
  reaches the backend as context, but it is not steered into the running task.

### 22.3 Gate G3 (frontend decision replay, `cli/delegation_replay.py`, 51 cases)

| Measure | Result | Gate |
|---|---|---|
| Overall accuracy | 0.98 | — |
| `delegate` accuracy | 0.98 | ≥ 0.95 ✔ |
| `request` accuracy in WORKING cells | 1.00 | ≥ 0.95 ✔ |
| Answers to backend questions | 1.00 | 1.00 ✔ |
| Backchannel / non-directed / vocal tics | 1.00 / 1.00 / 1.00 | ≥ 0.95 ✔ |
| Decision latency p50 / p90 | 841 ms / 2.1 s | — |

The remaining miss is `follow-no_session-2` ("actually make it two bags instead of one" with no earlier
task). The model kept it local with no reply.
