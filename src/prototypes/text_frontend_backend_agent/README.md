# Generic Text Frontend/Backend Agent (prototype)

A domain-agnostic, text-in/text-out reimplementation of
[`src/examples/frontend_backend_agent/`](../../examples/frontend_backend_agent/), built so a
scaffold-evaluation harness (τ²-bench) can drive it.

Design and rationale: [`misc/prototypes/text-frontend-backend-agent-prototype-plan.md`](../../../misc/prototypes/text-frontend-backend-agent-prototype-plan.md).
How to run it: [`misc/prototypes/text-frontend-backend-agent-runbook.md`](../../../misc/prototypes/text-frontend-backend-agent-runbook.md).

## What it is

Two LLMs with different jobs:

- the **frontend** talks to the user and owns exactly one tool, `call_backend`;
- the **backend** owns every real tool, does the work, and writes the final answer.

Per user turn, exactly one payload leaves the agent: the frontend's own answer, or the backend's
final text. Frontend filler (`"let me check that"`) is recorded on the event sink and can never
reach the caller — `AgentTurn` has no field it could occupy.

Differences from the voice example it derives from: text only, no Pipecat, no domain, tools are
injected rather than built in, and the frontend can be switched off entirely.

## Quick start

```bash
uv sync --dev                       # openai comes from the `prototypes` extra
export NVIDIA_API_KEY=sk-...        # Inference Hub key (sk-, not nvapi-)
PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.chat \
  --config src/prototypes/text_frontend_backend_agent/config/examples/echo_tools.yaml \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS
```

`--no-show-internal` prints only the reply: exactly what any other caller receives.

The terminal interface lives in its own module, `cli/ui.py`, behind a `ChatUI` protocol —
`RichUI` (panels, colour, spinner, tables) and `PlainUI` (line-oriented, dependency-free). Pick one
with `--ui rich|plain|auto`; `auto` uses rich on a terminal and plain when piped. `chat.py` holds
the loop and no formatting; `ui.py` holds the formatting and no agent logic. Internal events are
rendered only from the event sink and always in a separate, labelled region, each with its emission
time and elapsed-into-the-turn delta (`--no-timestamps` to drop them). The filler line is gray and
marked *"logged, not delivered"* — the phrase a voice agent would have spoken to cover latency,
here recorded and discarded.

## API

```python
from prototypes.text_frontend_backend_agent import build_agent, ToolSpec

agent = build_agent("path/to/agent.yaml", tools=[ToolSpec(...)], event_sink=sink)
session = agent.new_session()

turn, session = await agent.send("where is my order 5512?", session)
# turn.final_text  -> the answer, or
# turn.tool_calls  -> calls for you to execute under `execution: external`
turn, session = await agent.send_tool_results([ToolResult(...)], session)
```

The agent instance is immutable and reusable; all conversation state is in the `SessionState` you
pass in and get back. `turn.final_text` and `turn.tool_calls` are never both set and never both
empty — the same rule τ² enforces in `check_communication_error()`.

## Configuration

One entry point, [`config/agent.yaml`](config/agent.yaml), which references
[`config/prompts.yaml`](config/prompts.yaml). `${VAR}` / `${VAR:-default}` resolve from the
environment. The switches that change behaviour most:

| Key | Effect |
|---|---|
| `agent.mode` | `frontend_backend` (default) or `backend_only` (standalone backend with its own history, no frontend) |
| `backend.conversation_history.enabled` | paired mode only, off by default (`FBA_BACKEND_HISTORY` in `agent.yaml`). When on, the backend keeps its history across delegated turns; `include: full \| backend_turns \| transcript` picks what it sees. Example: [`config/examples/backend_history.yaml`](config/examples/backend_history.yaml) |
| `backend.tools.execution` | `internal` (this process runs tools) or `external` (the caller does — τ² mode) |
| `backend.tools.parallel_execution` | sequential by default; injected tools may mutate shared state |
| `frontend.delegation.on_contract_violation` | `fallback_text` (default) or `error` |
| `domain.policy` / `domain.capabilities` | the only place a domain is named |
| `*.llm.extra_body.chat_template_kwargs.enable_thinking` | reasoning, per role — see below |

Defaults target the **NVIDIA Inference Hub** (`https://inference-api.nvidia.com/v1`, `sk-…` keys;
the doubled `nvidia/nvidia/` prefix in model ids is not a typo), with a different model per role:

| Role | Model | Reasoning |
|---|---|---|
| frontend | `nvidia/nvidia/nemotron-3.5-lightning` | off |
| backend | `nvidia/nvidia/nemotron-3-ultra` | on |

Lightning routes fast on the turn the user is waiting through; Ultra does the task work and tool
use with thinking on. Override either with `FRONTEND_LLM_MODEL` / `BACKEND_LLM_MODEL`.

**Reasoning is OFF for the frontend and ON for the backend.** The frontend only routes — delegate
or answer, and restate the request so it stands alone — on the turn the user is waiting through,
so it runs fast and without thinking. The backend does the task work (choose tools, read results,
decide when it has an answer), which is where thinking pays. Reasoning comes back in its own
`reasoning_content` field, which this package never reads, so nothing leaks into a user-visible
answer. Measured over a 3-turn demo conversation: frontend 3 calls / 126 completion tokens,
backend 7 calls / 550.

### Backend conversation history

By default, the paired backend is stateless: each delegation starts from the system prompt and the
frontend's `query`. `backend.conversation_history.enabled: true` gives it the conversation history.
In `config/agent.yaml` the flag reads `FBA_BACKEND_HISTORY` (default `false`). `include` selects what it sees:

| `include` | Backend history across turns | Per-turn request |
|---|---|---|
| `full` (default) | kept | the user's verbatim words since the backend's last reply, turns the frontend answered itself, and the `query` |
| `backend_turns` | kept | the `query` only |
| `transcript` | none (fresh per delegation) | the conversation so far, plus the `query` |

- The request comes from the catalog template `request_key` (default `backend_history_request`).
- The backend system prompt always gets the context note `backend_history_context_<include>`.
  `guidance_key: auto` also appends the behavioural guidance `backend_history_guidance_<include>`.
  Set another catalog key to use your own text, or `guidance_key: ""` to leave the guidance out.
- `backend.stateful: auto` is derived: true for `backend_only`, or for paired mode with the history
  on and `include` of `full` or `backend_turns`. An explicit `stateful: true` in paired mode is an
  error that names this flag.
- `enabled` is parsed strictly, so the string `"false"` from the environment is false. Turning it on
  with `agent.mode: backend_only` is a `ConfigError`, because that mode already keeps the history.
- With the history kept, a pending turn dropped by `discard_pending` is closed with synthesized tool
  results, so the backend does not lose calls that the caller may already have run.
- Every delegated turn emits a `backend_context` event (`enabled`, `include` or `"off"`, `guidance`,
  `history_groups`, `history_messages`, `earlier_turns`, `request_chars`). It carries no content.

Design: [`misc/prototypes/frontend-backend-agent-backend-history-plan.md`](../../../misc/prototypes/frontend-backend-agent-backend-history-plan.md).

## Contracts for callers

- **Injected tool callables must be reentrant** if one agent serves concurrent sessions under
  `execution: internal`. This package cannot enforce that.
- **Event sinks**: `LoggingSink` and `JsonlSink` are safe to share; `CollectingSink` is
  single-session/test-only.
- **Tool results must be JSON-serializable** (`str`, scalars, `dict`, `list`, or a dataclass).
  Anything else becomes an error result rather than a `repr()` — see `tools.serialize_result`.

## There is no cancel tool

Under a turn-based API nothing can be cancelled: internal execution blocks until the turn
completes, and in external execution a user message arriving while results are outstanding is
intercepted by `backend.tools.on_user_message_while_pending` before the frontend LLM runs. That
policy (`error` or `discard_pending`) is the whole of cancellation here.

## Tests

```bash
uv run pytest tests/unit/prototypes -v
```

145 offline tests, no network, no GPU, no credentials: every LLM is a scripted `FakeChatClient`.

## Verify an endpoint before trusting it

```bash
PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.probe_reasoning
```

Checks that the reasoning toggle responds, that reasoning stays out of `content`, and that
`call_backend` still comes back as a real tool call in both modes. Exits non-zero on any failure.
