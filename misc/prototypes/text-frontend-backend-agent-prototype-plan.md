# Plan — Generic Text Frontend/Backend Agent Prototype

**Status:** implemented · **Date:** 2026-09-22 · **Revision:** 5 (review rounds 1–3 folded in, then built and wired to Inference Hub — see §20)
**Code:** `src/prototypes/text_frontend_backend_agent/` · **Runbook:** [`text-frontend-backend-agent-runbook.md`](text-frontend-backend-agent-runbook.md)

> Built as specified. Deviations, all small and noted in place: `PendingTurn` also carries
> `frontend_assistant` (the frontend's own tool-call message, needed to close the four-message
> group in §6.1 after an external round-trip); the frontend prompt's gym example says "a place in
> the 7:00 AM class" rather than "book" so the word cannot collide with the library example under
> the §11 domain test; and `make_agent`/`assemble_agent` is the injection seam the tests and the
> future tau2 adapter use instead of `build_agent`. 90 offline tests, all rules in §12 covered.

Derived from `src/examples/frontend_backend_agent/` (voice, airline-specific) and written so the
result drops into τ²-bench later as a **Pattern B scaffold agent**
(`tau2-bench-smasurekar/misc/custom-agent-integration.md` §5), the way `tau2-hermes` wraps Hermes.

---

## 1. What changes versus the existing example

| Dimension | `src/examples/frontend_backend_agent/` | This prototype |
|---|---|---|
| Modality | audio → ASR → Talker → TTS → audio | text in / text out |
| Runtime | Pipecat pipeline, frames, `FunctionCallParams` | plain `async` Python, no Pipecat import |
| Domain | airline (flights, PNR, booking server) | domain-agnostic; domain enters only as prompt text + injected tools |
| Backend | hand-written Python tool dispatch (`airline/thinker.py`) | generic tool-calling loop over an injected tool set |
| Tools | fixed internal catalog | **external tools injected at construction**, routed to the backend only |
| Frontend tools | `call_backend`, `cancel_backend` | **`call_backend` only** (see §9) |
| Filler text | spoken after a latency threshold | never returned to the caller; emitted to an event sink |
| Frontend | mandatory | optional (`mode: backend_only` → backend runs standalone with its own history) |
| State | implicit in the Pipecat session | **explicit `SessionState`, passed in and returned** (§6) |
| Config | `.env` + `services.*.yaml` + `examples_registry.yaml` | one `agent.yaml` entry point + a referenced prompt catalog (§5) |

**Non-goals for this prototype:** τ² registration/registry edits, voice, streaming, Pipecat
integration, the airline domain, Docker/Compose profiles, `examples_registry.yaml` entries.
This code is additive under `src/prototypes/` and touches no existing module.

---

## 2. Design constraints (restating the requirements as testable rules)

1. **R1 — No hardcoded target-domain assumptions.** No Python branch, schema, or prompt clause may
   assume a particular domain. Domain policy, persona and capability list are config inputs
   (`{persona}`, `{domain_policy}`, `{capabilities}`). Prompt *examples* are unavoidable — a
   contract this strict cannot be taught abstractly — so the rule on them is: **no single domain may
   appear twice**, and none may be the domain under evaluation. `test_prompts.py` asserts the
   shipped frontend prompt's examples span ≥ 5 distinct, unrelated domains and that no domain noun
   appears outside an example block.
2. **R2 — Text only.** One message in, one final text (or one tool-call batch) out.
3. **R3 — Frontend prompt parity.** The frontend prompt keeps the *structure and strictness* of
   today's `talker` prompt (identity/privacy block, no-reasoning-leak block, hard tool-call
   contract, modes, filler rules, delegation-argument rules, worked examples), with airline
   specifics replaced by placeholders. The backend prompt is generic and short.
4. **R4 — Delegation-only frontend.** The frontend LLM is called with exactly one tool,
   `call_backend`. External tools are never passed to it; a tool call naming anything else is
   rejected in code and **fails closed** (§12 rule 2), not merely discouraged in the prompt.
5. **R5 — Single final output.** Per outward step, exactly one user-visible payload leaves the
   agent. If the frontend answers directly → that text. If it delegates → **only** the backend's
   final text. `filler_text` is emitted to the event sink and is *not a field on the returned
   envelope* (§7.1).
6. **R6 — External tools → backend, with real tool messages.** Callers can register tools they
   execute themselves; the agent surfaces the backend's tool calls as structured calls and accepts
   correlated tool results back, under an enforced protocol (§7.2).
7. **R7 — Frontend-off mode.** `mode: backend_only` gives a plain, stateful, standalone backend
   agent that is runnable on its own. Default configuration is frontend + backend.
8. **R8 — YAML-configured.** Models, endpoints, prompts, modes, limits, logging — all YAML.
9. **R9 — Pluggable chat interface.** A small independent REPL over a transport-agnostic core.
10. **R10 — Explicit session state.** One agent instance is immutable and reusable across sessions;
    all mutable per-conversation data lives in a `SessionState` the caller holds and passes (§6).
    The concurrency claim is conditional — see §6.2.
11. **R11 — Accounting.** Every LLM call's token usage, cost and latency is captured and
    aggregated onto the outward step and onto the session (§8).

---

## 3. Architecture

```
                        ┌──────────────────── FrontendBackendAgent ────────────────────┐
  caller (REPL / τ²)    │                     (immutable: config, clients, tools)       │
        │               │                                                              │
        │ send(text,    │   ┌─────────────┐   delegate(query, filler)  ┌─────────────┐  │
        │      session) │   │  Frontend   │ ─────────────────────────▶ │   Backend   │  │
        ├──────────────▶│   │   Agent     │ ◀───────────────────────── │    Agent    │  │
        │               │   │ (1 tool)    │        final text          │ (all tools) │  │
        │               │   └──────┬──────┘                            └──────┬──────┘  │
        │               │          │ filler / delegation / tool events        │         │
        │◀──────────────┤          ▼                                          │         │
        │ (AgentTurn,   │   ┌──────────────┐                   tool_calls ◀───┤         │
        │  SessionState)│   │  EventSink   │ (never on AgentTurn)              │         │
        │               │   └──────────────┘                   tool_results ─▶│         │
        └───────────────┴──────────────────────────────────────────────────────┴────────┘
                                                                     │
                                                      caller-executed tools (τ² env)
                                                      or local ToolRegistry (REPL)
```

Two orthogonal axes, each a config switch:

- **Frontend on/off** — `FrontendBackendAgent` composes `FrontendAgent` + `BackendAgent`;
  with the frontend off it degrades to a thin pass-through around `BackendAgent`.
- **Tool execution internal/external** — who runs the backend's tool calls. The *external*
  (suspend/resume) form is primitive; the *internal* driver is a short loop over it.

### 3.1 The turn state machine

`BackendAgent.step()` is a resumable state machine, not a blocking loop:

```python
# input  : BackendInput  = Query(text) | ToolResults(list[ToolResult])
# output : BackendStep   = NeedsTools(tool_calls) | Final(text)
async def step(self, inp: BackendInput, state: BackendState) -> tuple[BackendStep, BackendState]
```

- `execution: internal` → `InternalToolDriver` loops `step()` ↔ `ToolRegistry.execute()` until
  `Final`, bounded by `backend.max_tool_iterations`.
- `execution: external` (τ²) → `NeedsTools` propagates out of `send()` as
  `AgentTurn(tool_calls=[...], final_text=None)`; the caller executes and calls
  `send_tool_results([...], session)` to resume.

Both modes share one code path, so the REPL exercises the same machine τ² will drive. A suspended
backend turn must survive across the `send()` boundary, which is why the pending batch is a field
of `SessionState` (§6) rather than a local variable.

### 3.2 Why the frontend cannot leak filler

`FrontendAgent.decide()` returns a closed union — `DirectAnswer(text)` or `Delegate(query, filler_text)`
— not a message. Only `DirectAnswer.text` and `Final.text` are ever copied onto
`AgentTurn.final_text`. `Delegate.filler_text` is handed to the `EventSink` and to nothing else;
`AgentTurn` has no field capable of carrying it. R5 is then a property of the types, testable
without an LLM.

---

## 4. Module layout

```
src/prototypes/__init__.py
src/prototypes/text_frontend_backend_agent/
  __init__.py              public API: FrontendBackendAgent, build_agent, SessionState, AgentTurn, ToolSpec
  config.py                dataclasses + YAML loader + ${ENV_VAR} interpolation + validation
  messages.py              Message/Role, ToolCall, ToolResult, AgentTurn, Usage/UsageTotals
  session.py               SessionState, PendingTurn, to_dict/from_dict, from_message_history (§6.3)
  history.py               immutable group-aware history model + safe pruning (§10)
  events.py                InternalEvent types, EventSink protocol, Logging/Jsonl/Collecting sinks
  llm.py                   ChatClient protocol + OpenAIChatClient (async) -> ChatResponse w/ usage
  prompts.py               prompt catalog loader + placeholder rendering
  delegation.py            call_backend schema + argument normalization
  frontend.py              FrontendAgent -> DirectAnswer | Delegate
  backend.py               BackendAgent.step() state machine + BackendState
  tools.py                 ToolSpec, ToolRegistry, InternalToolDriver, result serialization
  protocol.py              tool-call/result correlation + validation errors (§7.2)
  errors.py                FrontendContractError, ToolProtocolError, StateReplayError
  agent.py                 FrontendBackendAgent: composition, routing, guardrails, accounting
  config/
    agent.yaml             default config (frontend + backend, internal tools, no domain)
    prompts.yaml           frontend + backend prompt catalog (referenced by agent.yaml)
    examples/
      echo_tools.yaml      demo config with two toy tools, for the REPL
  cli/
    __init__.py
    chat.py                REPL loop: `python -m prototypes.text_frontend_backend_agent.cli.chat`
    ui.py                  ChatUI protocol + RichUI / PlainUI (all presentation, no agent logic)
    external_demo.py       driver for `execution: external` (send / send_tool_results)
    probe_reasoning.py     endpoint check: reasoning toggle + tool calling
  README.md
tests/unit/prototypes/
  test_config.py test_session.py test_state_replay.py test_history_pruning.py test_prompts.py
  test_frontend.py test_backend.py test_agent_turn.py test_delegation_args.py
  test_no_filler_leak.py test_frontend_disabled.py test_external_tools.py
  test_tool_protocol.py test_usage_accounting.py test_immutability.py
  _fakes.py                scripted FakeChatClient (no network in any test)
```

Conventions inherited from the repo: SPDX header on every file, `from __future__ import annotations`,
Google-style docstrings (ruff `D`, line-length 120), `loguru`, `pytest` with `pythonpath = ["src"]`
(already configured), dataclasses over Pydantic for internal types.

### 4.1 Dependencies

The package imports only `pyyaml`, `loguru`, `openai` (async client) and stdlib. `pyyaml` and
`loguru` are already direct dependencies of the project; **`openai` is currently only transitive**
via `pipecat-ai[...,openai]`, and this plan adds it explicitly rather than relying on that:

```toml
[project.optional-dependencies]
prototypes = ["openai>=1.109.1"]   # direct: src/prototypes/ uses AsyncOpenAI

[dependency-groups]
dev = [..., "nemotron-voice-agent[prototypes]"]   # so `uv sync --dev` still covers it locally
```

It must be a **project extra**, not only a `[dependency-groups]` entry: dependency groups are a
workspace/dev-time concept and are *not* install metadata, so
`uv pip install -e /path/to/this/repo` from inside the τ² environment would not pull `openai`.
With an extra, the τ² integration installs `uv pip install -e "/path/to/this/repo[prototypes]"`
and gets the dependency by declaration rather than by luck. Locally: `uv sync --dev` or
`uv sync --extra prototypes`.

No direct `httpx` use — everything goes through `AsyncOpenAI`, so `httpx` stays transitive and
unclaimed.

**No `pipecat` import anywhere** — the τ² environment will not have it, and a Pipecat import at
module scope is the single most likely thing to make the future integration painful. Enforced by
a test that walks the package AST for forbidden imports.

---

## 5. Configuration (R8)

**One entry point, `agent.yaml`**, which references a prompt catalog by path — matching the repo's
existing `prompts.yaml` / `services.*.yaml` convention rather than inlining several hundred lines of
prompt into the settings file. `prompts.inline` may override any key for a throwaway experiment, so
a genuinely single-file config is possible when wanted. `${VAR}` / `${VAR:-default}` are resolved
from the environment so no key lands in git.

```yaml
agent:
  name: "Assistant"
  persona: |                    # renders {persona} in the frontend prompt
    You are a helpful assistant and the only component that talks to the user.
  mode: frontend_backend        # frontend_backend (default) | backend_only

prompts:
  path: "prompts.yaml"          # relative to this file (both live in config/)
  inline: {}                    # optional per-key override: {frontend: "...", backend: "..."}

frontend:
  prompt_key: frontend
  llm:
    model: "${FRONTEND_LLM_MODEL:-nvidia/nvidia/nemotron-3.5-lightning}"
    base_url: "${FRONTEND_LLM_BASE_URL:-https://inference-api.nvidia.com/v1}"
    api_key: "${NVIDIA_API_KEY}"          # Inference Hub keys are sk-...
    temperature: 0.0
    max_tokens: 1024
    # Reasoning OFF: the frontend only routes, on the turn the user waits through.
    extra_body: {chat_template_kwargs: {enable_thinking: false}}
  delegation:
    max_repair_attempts: 1      # reprompts when the frontend violates the contract
    on_contract_violation: fallback_text   # fallback_text | error   (§12 rule 2)
    fallback_text: "Sorry, I could not process that. Could you rephrase?"
  history:
    max_groups: 20              # logical groups, not raw messages (§10)

backend:
  prompt_key: backend
  stateful: auto                # auto -> false when frontend on, true when backend_only
  llm:
    model: "${BACKEND_LLM_MODEL:-nvidia/nvidia/nemotron-3-ultra}"
    base_url: "${BACKEND_LLM_BASE_URL:-https://inference-api.nvidia.com/v1}"
    api_key: "${NVIDIA_API_KEY}"
    temperature: 0.0
    max_tokens: 4096
    # Reasoning ON: the backend does the task work, where thinking pays.
    extra_body: {chat_template_kwargs: {enable_thinking: true}, reasoning_budget: 1024}
  tools:
    execution: internal         # internal | external (external = caller executes; τ² mode)
    # native tool calling only; text-parsed tool calls are deferred (§18)
    max_tool_iterations: 8
    max_result_chars: 0         # 0 = no truncation; see §7.2 for the truncation envelope
    parallel_execution: false   # internal driver: sequential by default (§7.3)
    on_incomplete_results: error          # error | synthesize_error_result  (§7.2)
    on_user_message_while_pending: error  # error | discard_pending          (§9)
  history:
    max_groups: 40              # used only when stateful

domain:
  policy: ""                    # injected verbatim into both prompts ({domain_policy})
  capabilities: []              # short user-facing "I can ..." list for the frontend prompt
  unsupported_reply: "I can only help with the tasks I am set up for."

accounting:
  pricing: {}                   # optional {model: {prompt_per_1k, completion_per_1k}} for local cost
  record_latency: true

logging:
  level: INFO
  event_sink: logging           # logging | jsonl | none
  event_sink_path: "${FBA_EVENT_LOG:-}"
  log_filler_text: true
  log_delegation_query: true
```

`mode`, frontend presence and `backend.stateful` are reconciled in one place
(`config.resolve_runtime_modes()`) so they cannot disagree.

---

## 6. Explicit session state (R10)

The agent instance holds only config, LLM clients, prompts and tool specs — it is immutable after
construction. Everything mutable is in `SessionState`, which the caller owns and passes on every
call.

```python
@dataclass(frozen=True, slots=True)
class PendingTurn:
    """An in-flight backend turn suspended awaiting caller-executed tool results."""
    delegation_query: str            # "" in backend_only mode
    user_message: str                # needed to close the frontend group on completion (§6.1)
    backend_history: History         # working context for this delegation
    outstanding: tuple[str, ...]     # tool_call ids awaiting results, in emission order
    iterations: int                  # against backend.max_tool_iterations

@dataclass(frozen=True, slots=True)
class SessionState:
    session_id: str
    frontend_history: History        # empty in backend_only mode
    backend_history: History         # persistent only when backend.stateful
    pending: PendingTurn | None
    usage: UsageTotals
```

`SessionState` is frozen and replaced (not mutated) on every step, so a caller can snapshot, fork or
checkpoint it. `to_dict()` / `from_dict()` round-trip it (for checkpointing our *own* state);
reconstructing from a foreign transcript is a different operation with a different signature — §6.3.

### 6.1 What lands in frontend history after a delegation

The frontend's context for turn *N+1* must contain the outcome of turn *N*, or its next
`call_backend` query will not be self-contained — the single most important correctness property of
the whole scaffold. On completion of a delegated turn the agent appends one **complete logical
group** (§10) to `frontend_history`:

1. the user message;
2. the assistant message carrying the `call_backend` tool call (verbatim arguments, including
   `filler_text` — it is part of what the model actually said, and the frontend should see its own
   turn);
3. a `tool` message whose `tool_call_id` matches, whose content is the backend's final text;
4. **a synthetic assistant message containing the backend's final text verbatim** — the
   continuation that completes the group.

Step 4 exists for two reasons: without it the group is malformed under §10's own definition
(a tool-call batch with results and no continuation), and the frontend's history would not contain
the answer the user actually saw — so on turn *N+1* the model could contradict it. It is
**synthetic**: no LLM call, no outward emission, no rephrasing. The text is copied byte-for-byte
from `Final.text`, which is the same string already returned on `AgentTurn.final_text`.

`test_frontend.py::test_delegated_group_is_complete` asserts the four messages, the id match, and
that the assistant continuation equals the returned `final_text` exactly.

**Non-delegated turns close a group too.** Every outward step appends exactly one complete group to
`frontend_history`, so §10's invariant holds for all paths, not just the delegated one:

| Path | Group appended |
|---|---|
| direct answer | user message + assistant message with `DirectAnswer.text` |
| delegation | the four messages above |
| contract-violation fallback (§12 rule 2) | user message + assistant message with the *fallback text that was returned* — never the model's rejected output, which goes to the sink only |
| backend error fallback (rule 22) | user message + assistant message with the graceful text that was returned |

In every case the assistant message equals `AgentTurn.final_text` byte-for-byte. The rule is
therefore one line: **whatever the caller was shown is what history records.**
`test_frontend.py::test_every_path_appends_one_group` parametrizes over the four paths.

### 6.2 What "safe across concurrent sessions" actually requires

The claim holds only if every shared object is safe, so this is a stated contract, not an
assumption:

- `History` and `UsageTotals` are frozen dataclasses over `tuple`, not `list` — deep immutability,
  not just a frozen outer shell. `Message`, `ToolCall`, `ToolResult` likewise. Updates return new
  instances (`history.append(...) -> History`). `test_immutability.py` walks the dataclass tree and
  asserts no mutable container field anywhere reachable from `SessionState`.
- The **`EventSink` must be reentrant** if shared. `LoggingSink` and `JsonlSink` (append-only, one
  `write` per event, lock held) satisfy this; `CollectingSink` does not order events across
  sessions and is documented as single-session/test-only. Events carry `session_id` so a shared
  sink stays attributable.
- **Injected tool callables must be reentrant** when `execution: internal` and one agent serves
  concurrent sessions. The prototype cannot enforce this; `ToolSpec` documents it and the README
  states it. (In τ² mode this is moot — tools execute caller-side.)
- LLM clients: `AsyncOpenAI` is safe for concurrent use; one client per role is constructed and
  shared.

### 6.3 Reconstructing state from a foreign transcript

`from_dict()` cannot do this — τ² hands `get_init_state(message_history=...)` a
`list[Message]` recording only what crossed the boundary, which is strictly less than
`SessionState` (the delegation query, the filler, and the frontend's own tool-call turn were never
emitted). Pretending otherwise would silently fabricate history. So there is a separate API with a
deterministic, documented, lossy rule:

```python
SessionState.from_message_history(messages, *, config) -> SessionState
```

The rule is **mode-specific**, because the two modes disagree about which history the transcript
even belongs to.

`mode: frontend_backend` (paired):

| Transcript element | Replay rule |
|---|---|
| user message | appended to `frontend_history` |
| assistant message with text | appended to `frontend_history` (it is what the user saw) |
| assistant message with tool calls | **not** replayed — those were backend calls, invisible to the frontend |
| tool result | **not** replayed — it belongs to a delegation that is stateless by default |
| — | `frontend_history` is a plain user/assistant alternation; no `call_backend` turns are fabricated |
| — | `backend_history` is empty (per-delegation, stateless by default; with `backend.conversation_history` on, this replay is lossy) |

`mode: backend_only`:

| Transcript element | Replay rule |
|---|---|
| everything | appended to `backend_history` in transcript order, **including assistant tool-call messages together with their tool results** — dropping either side would strand the other and violate §10 |
| — | `frontend_history` stays empty; there is no frontend |

Both modes: `pending` is always `None`, and a transcript ending with tool calls that have no
results raises `StateReplayError` rather than replaying a half-open group.

The cost is explicit: a replayed session's frontend sees the *answers* of earlier turns but not the
*queries* it sent. Since §6.1 step 4 already puts the answer in history, the self-containment
property (§6.1) survives replay. `test_state_replay.py` covers a text-only transcript, a
transcript with tool calls, and the unresolved-tail rejection.

---

## 7. Public API and the tool protocol

### 7.1 Surface

```python
agent   = build_agent(config_path="…/agent.yaml", tools=[ToolSpec(...)], event_sink=sink)
session = agent.new_session()

turn, session = await agent.send("where is my order 5512?", session)
turn, session = await agent.send_tool_results([ToolResult(...)], session)   # external only
```

```python
@dataclass(frozen=True, slots=True)
class AgentTurn:
    final_text: str | None              # user-visible text, or None when tool_calls is set
    tool_calls: tuple[ToolCall, ...]    # backend tool calls awaiting caller execution
    usage: UsageTotals                  # aggregated over every LLM call in this outward step
```

That is the whole envelope. **No `events` field** — diagnostics go to the `EventSink` passed at
construction (`LoggingSink` by default, `JsonlSink` for runs, `CollectingSink` for tests and the
REPL's `--show-internal`). This is what makes R5 structural: there is no field on `AgentTurn` that
filler text could occupy.

Invariant asserted in code and in `test_agent_turn.py`: `final_text` and `tool_calls` are never both
set and never both empty — deliberately identical to τ²'s `check_communication_error()` rule, so the
future adapter is a field-for-field copy rather than a translation.

`ToolSpec` carries `name`, `description`, JSON-schema `parameters`, and an optional `callable`
(required only for `execution: internal`). A τ² `Tool` maps onto it directly.

### 7.2 Tool-message protocol (R6) — specified and enforced

```
delegated query (or user message in backend_only)
  → assistant message with 1..N tool calls        [NeedsTools]
  → exactly N correlated tool results             [ToolResults]
  → assistant final text  [Final]  OR  another tool-call batch  [NeedsTools]
```

**Identity.** `ToolCall.id` is whatever the provider returned. An id is generated
(`call_<uuid4hex[:16]>`) **only when the provider omitted one**. Duplicate ids within one batch are
a provider error: the batch is rejected and one repair attempt is made. `test_tool_protocol.py`
covers provider-id passthrough explicitly — rewriting ids would silently break τ² correlation.

**Validation on `send_tool_results`**, against `session.pending.outstanding`:

| Condition | Behaviour |
|---|---|
| no `pending` | `ToolProtocolError("no tool calls are outstanding")` |
| unknown id | `ToolProtocolError`, listing the id and the expected set |
| duplicate id in one batch | `ToolProtocolError` |
| missing id(s) | `on_incomplete_results: error` → `ToolProtocolError`; `synthesize_error_result` → a deterministic error result is inserted for each missing id and the turn continues |
| all ids matched | results ordered to match `outstanding`, appended, backend resumed |

`ToolProtocolError` is raised to the caller — a driver bug, not a model error; it never becomes a
user-visible turn.

**Serialization** must be deterministic so transcripts diff cleanly and tests are exact, which
rules out `default=str`: an arbitrary `__repr__` can carry a memory address or an unstable field
order, and two identical runs would then differ. Tool results are therefore **required to be
JSON-serializable**, with one explicit conversion table and no catch-all:

| Result type | Serialization |
|---|---|
| `str` | passed through unchanged |
| `dict` / `list` / `int` / `float` / `bool` / `None` | `json.dumps(value, sort_keys=True, ensure_ascii=False)` |
| dataclass instance | `dataclasses.asdict()`, then as above |
| anything else | **not** stringified — a `ToolResultSerializationError` is caught and returned as the error envelope below, naming the offending type |

An execution failure (or a non-serializable result) becomes
`{"error": {"type": "<ExceptionClass>", "message": "<str(exc)>"}}` through the same dumper.

**Truncation** under `max_result_chars` never cuts serialized JSON in place — that would hand the
model malformed content. The whole result is replaced by a valid envelope:
`{"truncated": true, "original_chars": <n>, "content": "<first max_result_chars chars>"}`.
`test_tool_protocol.py::test_truncation_is_valid_json` parses the output of a truncated dict result
and a truncated string result.

### 7.3 Internal execution of a tool batch

When the backend emits several tool calls and `execution: internal`, `InternalToolDriver` runs them
**sequentially, in emission order** — the default, and deliberately the conservative one. Injected
tools are arbitrary caller code and may mutate shared state; the airline Thinker in the parent
example hit exactly this and had to serialize its mutating tools by hand
(`airline/thinker.py::_dispatch_parallel_tool_calls`). A generic scaffold has no way to know which
injected tools commute, so it may not assume any of them do.

`backend.tools.parallel_execution: true` opts into `asyncio.gather`, and the README states the
contract it implies: the tools must be safe to run concurrently against the same session. Results
are ordered to match `outstanding` either way, so the model sees identical input under both
settings. `test_external_tools.py::test_internal_batch_is_sequential` asserts observed execution
order for a three-call batch.

External execution is unaffected — ordering there is the caller's business (τ² executes the batch
itself).

---

## 8. Cost, usage and timing (R11)

Not deferrable to the τ² adapter: the frontend and backend calls are made *inside* the scaffold, so
if they are not measured here, nothing downstream can recover them.

```python
@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage          # prompt_tokens, completion_tokens, total_tokens, cached_tokens
    cost: float | None    # provider-reported, else computed from accounting.pricing, else None
    latency_ms: float
    model: str
    finish_reason: str
```

`UsageTotals` sums `Usage` and `latency_ms` and keeps a per-role breakdown (`frontend`, `backend`)
and a call count. **Cost is `float | None` and unknown is contagious**: if any contributing call
returned `cost=None` (no provider figure and no `accounting.pricing` entry), the total is `None`
and `cost_unknown_calls` records how many. Treating a missing cost as `0.0` would silently
understate a benchmark total, which is the one failure mode this section exists to prevent — a
partial sum is also available as `cost_known_subtotal` for diagnostics, never as "the cost". Every outward step returns `AgentTurn.usage` for that step;
`SessionState.usage` accumulates across the session. Repair reprompts and failed attempts are
counted — otherwise the scaffold under-reports exactly where it is most expensive.

`ChatClient` stays a protocol, so the τ² adapter supplies a
`tau2.utils.llm_utils.generate()`-backed implementation and maps `AgentTurn.usage` onto
`AssistantMessage.cost` / `usage`. `test_usage_accounting.py` scripts a delegated turn with two
backend tool rounds and asserts the totals equal the sum of the four fake responses.

---

## 9. Cancellation — no model-facing tool at all

Revision 1 had `cancel_backend`; revision 2 replaced it with an opt-in `discard_pending`. Both are
wrong, for the same reason, and revision 3 removes the concept from the model's surface entirely:

- In `execution: internal`, `send()` blocks until the backend turn completes. No user message can
  arrive mid-turn, so there is nothing to discard.
- In `execution: external`, a user message arriving while results are outstanding is intercepted by
  `on_user_message_while_pending` **before the frontend LLM runs**. The frontend is never given the
  turn, so it can never choose to call a discard tool.

There is therefore no reachable path on which a model-facing cancel/discard tool fires. Shipping one
would put a clause in the prompt and a schema in the tool list that only ever produce noise.

**What remains** is the driver-level policy, which is reachable and is not model-facing:
`backend.tools.on_user_message_while_pending: error` (default — treat it as a driver bug) or
`discard_pending` (drop the suspended turn, emit a `PendingDiscarded` event, process the new
message). The frontend prompt says nothing about cancellation; a user saying "never mind" is just
another turn the frontend may answer directly or delegate.

Real cancellation needs a concurrent, cancellable session API (background task +
`await agent.cancel(session)`). **Out of scope**: τ² is turn-based and would never exercise it, and
it would add the hardest concurrency in the package for no evaluated benefit. Noted in §16 as the
follow-on if a live streaming deployment ever needs it.

---

## 10. Safe history pruning

Slicing a flat message list can separate an assistant tool-call batch from its tool results,
producing history most providers reject outright. `history.py` models history as **logical groups**,
and pruning operates on groups:

```
Group = user message
      + assistant message (text, or a tool-call batch)
      [+ all tool results for that batch
       + the assistant continuation (text, or another batch → repeat)]
```

Rules:

- system/prompt messages are pinned and never pruned;
- `max_groups` counts groups, not messages;
- pruning drops whole groups oldest-first and never splits one;
- a group is only prunable once complete — the in-flight group is never a candidate;
- if a single group alone exceeds the budget it is kept (dropping it would strand its results).

Applies to `frontend_history` (whose groups contain `call_backend` calls, their results, and the
§6.1 step-4 continuation) and to `backend_history` in stateful mode. `test_history_pruning.py`
covers a single tool-call group, a **parallel** batch of three calls, a multi-round group
(batch → results → batch → results → final), and asserts after every prune that every assistant tool
call has its results and every tool result has its call.

---

## 11. Prompts (R1, R3)

`config/prompts.yaml`, same shape as the example's `prompts.yaml` (`key: {description, content}`),
rendered with `{persona}`, `{domain_policy}`, `{capabilities}`, `{unsupported_reply}`.

### `frontend` — section-by-section port of today's `talker` prompt

| Talker section today | Prototype |
|---|---|
| "Your name is Ava … G Force Airlines … TTS-brief" | `{persona}`; "reply in one or two short sentences, plain text, no markdown" |
| Identity and privacy | kept verbatim, model/provider names generalized |
| Reasoning and markup are private | kept verbatim (still needed: text models emit `<think>`/`<tool_call>` as prose) |
| "You have exactly two tools" | "you have exactly one tool: `call_backend`" |
| Hard tool-call contract | kept, with "flight-task" → "task-related" |
| Direct / Unsupported / Tool modes | kept; "Unsupported" renders `{capabilities}` + `{unsupported_reply}` |
| Cancel mode | removed (§9) |
| PNR/rebooking specifics | **dropped** |
| `call_backend` argument rules (self-contained query, no delta queries, latest turn wins, never invent values, confirmation discipline) | kept — generic, and what makes delegation work |
| "Internal names are private" | kept, with generic marker names |
| "After the tool returns … answer from response_text" | **dropped.** The backend's final text is returned verbatim; the frontend never gets a second turn on it (R5). It enters history per §6.1 so the *next* turn is informed. |
| Worked examples | see below |

**Examples and R1.** The contract ("emit a tool call, never prose, with a self-contained query")
cannot be taught without concrete turns, so examples stay — but no domain may carry two of them,
and none may be a domain under evaluation. The planned set, one failure mode each:

| Failure mode | Example domain |
|---|---|
| speaking instead of calling | library book renewal |
| reasoning leak (`<think>` as text) | gym class booking |
| tool call typed as text | parcel tracking |
| delta-only query | restaurant reservation change |
| invented confirmation | utility meter reading |
| unsupported request → answer directly, no tool call | IT helpdesk hardware procurement |

`test_prompts.py` asserts ≥ 5 distinct example domains, no domain repeated, and that the
non-example body contains no noun from a fixed list; τ²'s `airline`, `retail`, `telecom` and
`banking` are excluded by name so a known benchmark domain never leaks into the prompt.

**This test is a regression guard, not proof of genericity.** A fixed noun list cannot show that no
domain assumption survives anywhere in the prose, and a fixed example set cannot be guaranteed
disjoint from some future custom evaluation domain. The test catches drift; genericity is
established by prompt review, which is an explicit P3 gate (§15), and re-checked whenever a new
evaluation domain is adopted.

### `backend`

Short and generic: *you are the backend agent; the user never sees anything but your final answer;
you receive a self-contained request; use the provided tools; when you have the answer, reply with
the final user-facing text and no tool call; ask for missing information by returning that question
as your final text; follow `{domain_policy}`.* (A text-parsed `<tool_call>` prompt variant is deferred — §18.)

No JSON plan schema (that was the airline Thinker's private contract). The backend uses ordinary
tool calling, which is what τ² domains expect.

---

## 12. Behavioral rules to implement (and the test for each)

| # | Rule | Test |
|---|---|---|
| 1 | Frontend sees exactly one tool schema, `call_backend` | `test_frontend.py::test_tool_surface` |
| 2 | Frontend call naming an unknown tool → repair reprompt; on repeat failure **fail closed**: emit `FrontendContractViolation`, return `frontend.delegation.fallback_text` (or raise `FrontendContractError` under `on_contract_violation: error`). The model's raw text is **never** returned as the answer. | `test_frontend.py::test_unknown_tool_fails_closed` |
| 3 | Frontend returning content **and** a tool call → tool call wins, content goes to the sink | `test_frontend.py::test_content_plus_tool_call` |
| 4 | `filler_text` never reachable from `AgentTurn`; present in the sink | `test_no_filler_leak.py` |
| 5 | Delegation query reaches the backend verbatim, not merged with frontend history | `test_backend.py::test_query_is_self_contained` |
| 6 | Paired backend is stateless per delegation by default (fresh context each delegation), unless `backend.conversation_history.enabled` | `test_backend.py::test_stateless_paired` |
| 7 | A delegated turn appends a complete 4-message group to frontend history; the synthetic assistant continuation equals the returned `final_text` | `test_frontend.py::test_delegated_group_is_complete` |
| 8 | `backend_only` keeps full history across `send()` calls | `test_frontend_disabled.py` |
| 9 | `execution: external` surfaces `NeedsTools` as `AgentTurn.tool_calls` and resumes on results | `test_external_tools.py` |
| 10 | Parallel tool calls resume from one `send_tool_results` batch, correlated by id | `test_external_tools.py::test_parallel` |
| 11 | Provider tool-call ids are preserved; generated only when absent | `test_tool_protocol.py::test_id_passthrough` |
| 12 | Missing / duplicate / unknown / unexpected result ids handled per §7.2 | `test_tool_protocol.py` (4 cases) |
| 13 | Tool output and error serialization is deterministic | `test_tool_protocol.py::test_serialization` |
| 14 | `max_tool_iterations` exceeded → single graceful final text, event emitted | `test_backend.py::test_iteration_cap` |
| 15 | Usage/cost/latency aggregate over a step and accumulate on the session | `test_usage_accounting.py` |
| 16 | Pruning never splits a tool-call group (single, parallel, multi-round) | `test_history_pruning.py` |
| 17 | `SessionState` round-trips; two sessions on one agent stay isolated | `test_session.py` |
| 18 | User message while results are pending → per `on_user_message_while_pending` | `test_tool_protocol.py::test_message_while_pending` |
| 19 | `from_message_history()` replays per §6.3; unresolved tail raises `StateReplayError` | `test_state_replay.py` |
| 20 | Nothing reachable from `SessionState` holds a mutable container | `test_immutability.py` |
| 21 | Frontend prompt examples span ≥ 5 distinct domains, none repeated, none a benchmark domain (guard, not proof — §11) | `test_prompts.py` |
| 22 | Backend LLM/transport error → one graceful final text, event emitted, no exception across `send()` | `test_backend.py::test_error_path` |
| 23 | No `pipecat` import in the package | `test_config.py::test_no_pipecat_import` (AST walk) |
| 24 | Every path (direct, delegated, contract fallback, error fallback) appends exactly one complete group whose assistant message equals `final_text` | `test_frontend.py::test_every_path_appends_one_group` |
| 25 | Internal tool batches execute sequentially unless `parallel_execution: true`; result order matches emission order either way | `test_external_tools.py::test_internal_batch_is_sequential` |
| 26 | A `cost=None` contribution makes the aggregate cost `None`, never `0.0` | `test_usage_accounting.py::test_unknown_cost_is_contagious` |
| 27 | A truncated tool result is still valid JSON | `test_tool_protocol.py::test_truncation_is_valid_json` |
| 28 | A non-JSON-serializable tool result becomes the error envelope, not a `str()` dump | `test_tool_protocol.py::test_unserializable_result` |

Every test uses `FakeChatClient` (a scripted list of `ChatResponse`s). Zero network, zero GPU, zero
credentials — the suite runs in the existing CI job unchanged.

---

## 13. Chat interface (R9)

`cli/chat.py`: `--config`, `--tools <module:attr>` (loads a `list[ToolSpec]`), `--mode`,
`--show-internal/--no-show-internal` (attaches a `CollectingSink`), `--event-log <path>`.

```
$ uv run python -m prototypes.text_frontend_backend_agent.cli.chat --config …/examples/echo_tools.yaml
you> what's the status of order 5512?
  · delegate → "The user asks for the status of order 5512."     [internal]
  · filler   → "Let me check that."                              [internal, never returned]
  · tool     → get_order(order_id=5512) → {"status": "shipped"}  [internal]
  · usage    → 2 calls · 1,840 tok · 0.31 s                      [internal]
agent> Order 5512 shipped on the 19th and is out for delivery.
```

Internal lines come from the sink, not from the turn. `--no-show-internal` leaves only `agent>`
lines — the exact byte stream a caller receives. Commands: `/reset`, `/mode`, `/usage`, `/state`
(dump `SessionState`), `/quit`. The REPL holds no agent logic; it is a driver over the public API,
which is what makes it pluggable — swapping it for a FastAPI route or the τ² adapter changes nothing
below it.

---

## 14. τ²-bench readiness (design now, integrate later)

| τ² requirement (`custom-agent-integration.md`) | How the prototype satisfies it |
|---|---|
| `generate_next_message(message, state) -> (AssistantMessage, State)` | `send()` / `send_tool_results()` are state-in/state-out with `SessionState` (§6) |
| `get_init_state(message_history=None)` | `SessionState.from_message_history()` with the deterministic rule in §6.3 |
| `content` XOR `tool_calls` | `AgentTurn` invariant (§7.1) |
| `MultiToolMessage` must be unpacked | `send_tool_results()` takes a list and validates completeness (§7.2) |
| Tool calls structured, never text | `ToolCall` objects throughout; native tool calling is required of the endpoint (§18) |
| `ToolCall.id` unique and stable | provider ids preserved; generated only when absent (§7.2) |
| Tools & policy come from the environment | `tools=` at construction, `domain.policy` at config-merge — neither hardcoded |
| Factory must accept `**kwargs` | future `create_fba_agent(tools, domain_policy, **kwargs)`, ~40 lines over `build_agent` |
| Cost/usage must appear in results | captured per call, aggregated per step/session (§8); adapter maps onto the message |
| Domain leakage into the prompt | §11 excludes τ² domain names from prompt examples by test |
| Scaffold, not model, is measured | Pattern B; report as scaffold-assisted |

Deferred to the integration phase: the adapter package, registry wiring, the bring-up ladder
(Rungs 0–5), reporting.

---

## 15. Phases

| Phase | Deliverable | Gate |
|---|---|---|
| **P1 — Skeleton** | `config.py`, `messages.py`, `session.py`, `history.py`, `events.py`, `errors.py`, `llm.py`, `prompts.py`, `tools.py`, default YAML, `pyproject` dep group | rules 16, 17, 20, 23 green |
| **P2 — Backend standalone** | `backend.py` state machine, `protocol.py`, `InternalToolDriver`, `mode: backend_only` | rules 11–14, 22, 25, 27, 28 green; REPL holds a tool-using conversation with the frontend off (R7) |
| **P3 — Frontend + delegation** | `delegation.py`, `frontend.py`, `agent.py`, frontend prompt | rules 1–8, 21, 24 green; **frontend prompt reviewed for domain neutrality by a human** (§11); default config is frontend+backend |
| **P4 — External tools** | `execution: external`, suspend/resume via `SessionState.pending`, replay | rules 9, 10, 18, 19 green (R6) |
| **P5 — Accounting + chat + docs** | `UsageTotals` wiring, `cli/chat.py`, package `README.md`, example config | rules 15, 26 green; manual REPL run in both modes |
| **P6 — Hardening** | error paths, iteration caps, sink redaction, full suite | §16 validation clean |

P2 before P3 is deliberate: the backend must be independently correct and independently runnable
before the frontend is allowed to depend on it.

## 16. Validation

```bash
uvx ruff@0.15.6 check .
uvx ruff@0.15.6 format --check .
uv sync --dev --extra prototypes
uv run pytest tests/ -v
```

Client checks (`npm --prefix client …`) are not applicable — this prototype adds no client surface.
A live run against `integrate.api.nvidia.com` requires `NVIDIA_API_KEY` and will be reported as a
manual step with its transcript, never as an automated pass.

## 17. Documentation impact

`src/prototypes/` is not a shipped deployment surface: no `examples_registry.yaml` entry, no Compose
profile, no client change, no `.env` key change (config-file `${VAR}` references only). It does add
a `pyproject.toml` dependency group (§4.1), which is user-visible for contributors and will be noted
in the package `README.md`. Whether `docs/` needs a pointer is decided at implementation time per
`AGENTS.md` §Documentation; the PR carries a Documentation Writer Review receipt either way.

## 18. Deferred by decision

Concurrent cancellable session API (§9) · frontend rephrasing of the final answer (§19.1) ·
streaming · τ² registration and sweeps (§14) · **non-native (text-parsed) tool calling**: revision 3
carried a `native_tool_calling: false` switch with no grammar, no multi-call format, no
malformed-output handling and no tests. The NIM and OpenAI-compatible endpoints this prototype
targets all do native tool calling, so the switch is removed rather than half-specified. If a
text-only endpoint ever needs supporting, it arrives as its own change with a stated grammar and a
parser test suite — the `ChatClient` protocol is already the seam for it.

## 19. Open questions

1. **Frontend rephrasing of the final answer.** Today's Talker lightly rephrases backend text for
   TTS. §11 drops that — verbatim return. Right for τ² (fewer places to corrupt an answer) but a
   divergence from the voice agent. Proposal: verbatim by default, `frontend.rephrase_final: false`
   as a config seam, implemented only if a need appears. Note this interacts with §6.1 step 4: if
   rephrasing is ever enabled, the synthetic continuation must carry the *rephrased* text, since
   that is what the user saw.
2. **Cost.** Every delegated turn is 2+ LLM calls. §8 measures it; any reported τ² number must
   state it.

---

## 20. Revision log

### Revision 7 — backend conversation history

The paired backend is stateless **by default**, no longer by design. The new
`backend.conversation_history` flag (off by default) gives it the conversation history, and
`backend.stateful: auto` is now derived from `agent.mode` and that flag. Design, rules and tests:
[`frontend-backend-agent-backend-history-plan.md`](frontend-backend-agent-backend-history-plan.md).

### Revision 4 — third review round

Accepted all twelve points; six were genuine defects, four were factual slips with one-line fixes,
two were scope/wording.

| Review point | Severity | Resolution |
|---|---|---|
| Replay rules not mode-specific | defect | §6.3 — split into paired and `backend_only` tables; backend-only replays assistant tool calls *with* their results and leaves `frontend_history` empty |
| Dependency-group portability overstated | defect | §4.1 — `openai` moved to a `[project.optional-dependencies]` extra; dependency groups are not install metadata, so τ² installs `-e "…[prototypes]"` |
| `default=str` is not deterministic | defect | §7.2 — explicit conversion table; non-serializable results become the error envelope instead of a `repr` |
| Truncation can emit invalid JSON | defect | §7.2 — whole result replaced by `{"truncated": true, "original_chars": n, "content": "…"}`; rule 27 |
| Unknown cost aggregation undefined | defect | §8 — cost is `float \| None`, unknown is contagious, `cost_unknown_calls` recorded; never `0.0`; rule 26 |
| Internal batch execution unspecified | defect | §7.3 — sequential by default (generic injected tools may mutate shared state), `parallel_execution: true` opts in; rule 25 |
| Text tool-call grammar undefined | scope | §18 — `native_tool_calling: false` **removed** rather than half-specified; endpoints targeted here all do native tool calling |
| Direct-answer history unspecified | slip | §6.1 — table covering direct, delegated, contract-fallback and error-fallback paths; one rule: history records what the caller was shown; rule 24 |
| Prompt-catalog path resolves to `config/config/` | slip | §5 — `path: "prompts.yaml"` |
| Unreachable "small talk while pending" example | slip | §11 — replaced with an Unsupported-mode example, which had no coverage and is reachable |
| API example still says "cancel my last order" | slip | §7.1 — ordinary task request |
| Domain-neutrality test cannot prove genericity | wording | §11 — restated as a regression guard; human prompt review added as an explicit P3 gate |

### Revision 6 — chat interface

| Change | Detail |
|---|---|
| Presentation split out | `cli/ui.py` owns every byte the REPL prints, behind a `ChatUI` protocol; `chat.py` is now loop-only. `RichUI` (panels, colour, spinner, per-role usage table, highlighted `/state` JSON) and `PlainUI` (dependency-free, pipe/CI-safe), selected by `--ui rich\|plain\|auto` |
| `rich` declared | added to the `prototypes` extra rather than relied on transitively; `PlainUI` is the fallback when it is absent, so the package still runs in a minimal environment |
| Filler labelling | the filler event renders as *"filler (logged, not delivered)"* in the trace region; a test asserts it never appears in the answer region |

### Revision 5 — implementation follow-up

| Change | Detail |
|---|---|
| Inference Hub defaults | `https://inference-api.nvidia.com/v1`, `sk-…` keys (doubled `nvidia/nvidia/` prefix is real); the code-level `LLMConfig.base_url` default follows. Frontend `nemotron-3.5-lightning`, backend `nemotron-3-ultra` — both probed before adoption |
| Reasoning split | frontend **off**, backend **on** (`enable_thinking: true` + `reasoning_budget: 1024`) — the toggle was verified live before adoption on both models: off → no `reasoning_content`, ~4 completion tokens; on → 63–712 chars of reasoning; tool calling intact in both (16/16 correct `call_backend` across Lightning and Ultra) |
| Backend prompt: plain text | the backend's final text reaches the user verbatim, and with reasoning settings changed the model began emitting markdown (`**shipped**`). The prompt now forbids markdown explicitly |
| Frontend `max_tokens` 1024 → 2048 | reasoning shares the completion budget with the tool call |

### Revision 3 — second review round

| Review point | Resolution |
|---|---|
| `discard_pending` still unreachable | §9 — model-facing discard tool **removed entirely**; only the driver-level `on_user_message_while_pending` policy remains; frontend tool surface is now exactly one tool |
| Frontend history missed the visible answer | §6.1 — added step 4, a synthetic assistant message carrying the backend final text verbatim (no LLM call, no emission), completing the logical group; rule 7 asserts it equals `AgentTurn.final_text` |
| R1 contradicted by domain-specific examples | R1 restated as "no hardcoded target-domain assumptions"; §11 specifies six examples across six unrelated domains, excludes τ² benchmark domains, and rule 21 tests it |
| Unknown frontend tool "treated as text" | Rule 2 now **fails closed**: typed `FrontendContractViolation` event + configured fallback text, or `FrontendContractError`; raw model text is never returned |
| `from_dict()` cannot replay a τ² transcript | §6.3 — separate `from_message_history()` with a deterministic, explicitly lossy rule table and `StateReplayError` on an unresolved tail |
| "One self-contained YAML" vs two files; `{persona}` unconfigured | §5 — restated as one `agent.yaml` entry point referencing a prompt catalog, with `prompts.inline` for single-file use; `agent.persona` field added |
| `openai`/`httpx` relied on transitively | §4.1 — `openai` declared in a new `prototypes` dependency group; no direct `httpx` use |
| Frozen state is not deep-immutable | §6.2 — tuple-backed frozen `History`/`UsageTotals`/messages, rule 20 walks the tree; sink and tool-callable reentrancy stated as an explicit contract, concurrency claim made conditional |

### Revision 2 — first review round

| Review point | Resolution |
|---|---|
| Explicit session state | §6 — frozen `SessionState` passed in and returned; agent instance immutable |
| Complete tool-message protocol | §7.2 — sequence, id passthrough, validation matrix, deterministic serialization |
| Unimplementable cancellation | `cancel_backend` removed (finished in revision 3) |
| Filler-isolation contradiction | §7.1 — `AgentTurn.events` deleted; diagnostics to an `EventSink` |
| Missing cost/usage propagation | §8 — usage/cost/latency per call, aggregated per step and session |
| Unsafe history pruning | §10 — group-aware pruning that never splits a batch from its results |
