# Runbook — Generic Text Frontend/Backend Agent Prototype

**Status:** runbook · **Date:** 2026-09-22 · **Code:** `src/prototypes/text_frontend_backend_agent/`
**Design:** [`text-frontend-backend-agent-prototype-plan.md`](text-frontend-backend-agent-prototype-plan.md)

Every command runs as-is from the repository root:

```bash
cd /home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar
```

Two conventions, stated once because they appear everywhere:

- **`PYTHONPATH=src`** is required outside pytest — only pytest is configured to add `src/`.
- **`--config` paths resolve relative to the config file**, not the shell, which is why the demo
  configs say `path: "../prompts.yaml"`.

---

## 1. Install

```bash
uv sync --dev
```

Brings in the `prototypes` extra (`openai`, `rich`). To install this repo into a *different*
environment later (the τ² checkout), name the extra explicitly — dependency groups do not travel:

```bash
uv pip install -e "/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar[prototypes]"
```

## 2. Tests and lint — no credentials, no network

```bash
uv run pytest tests/unit/prototypes -v
uvx ruff@0.15.6 check src/prototypes tests/unit/prototypes
uvx ruff@0.15.6 format --check src/prototypes tests/unit/prototypes
```

115 tests, every LLM a scripted fake. The full repository suite (`uv run pytest tests/ -q`) passes
too.

## 3. Chat

```bash
set -a && . ./.env && set +a          # NVIDIA_API_KEY — Inference Hub keys are sk-…, not nvapi-…

PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.chat \
  --config src/prototypes/text_frontend_backend_agent/config/examples/echo_tools.yaml \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS
```

```
╭──────────────────── Order Assistant ────────────────────────────╮
│  frontend  nvidia/nvidia/nemotron-3.5-lightning  reasoning off  │
│   backend  nvidia/nvidia/nemotron-3-ultra        reasoning on   │
│  endpoint  https://inference-api.nvidia.com/v1                  │
│     tools  get_order, cancel_order                              │
│ execution  internal                                             │
╰──────────────────── frontend_backend ───────────────────────────╯
/reset  start a new session
/usage  token, latency and cost totals
/state  dump SessionState as JSON
/tools  tools handed to the backend
/quit   exit

you: where is my order 5512?
·  19:10:10.558  +1.25s  delegate → backend              The user is asking for the current status
                                                         of order 5512.
·  19:10:10.608  +1.30s  filler (logged, not delivered)  Let me check that.
·  19:10:11.754  +2.44s  backend wants tools             get_order({"order_id": "5512"})
·  19:10:11.754  +2.44s  tool executed                   get_order(…) → {"status": "shipped", …}
·  19:10:12.675  +3.36s  backend final text              Order 5512 has shipped. It was sent on
                                                         September 19, 2026 via Parcelflow.
·  19:10:12.676  +3.36s  usage                           3 calls · 3907 tok · 3.36 s · cost unknown
╭─ agent 19:10:12.685 ────────────────────────────────────────────────────────╮
│ Order 5512 has shipped. It was sent on September 19, 2026 via Parcelflow.   │
╰──────────────────────────────── 3 calls · 3907 tok · 3.36 s · cost unknown ─╯
```

The `·` lines are the internal trace, rendered from the event sink — never from the returned turn.
`--no-show-internal` hides the trace and leaves only the answer, which is exactly what any caller
receives.

Each trace line carries its **emission** time and the elapsed time into the turn, so it is obvious
where the seconds went (above: 1.25 s to decide to delegate, 1.2 s in the backend's first call,
0.9 s to write the answer). Same timestamps land in `--event-log` JSONL. Turn them off with
`--no-timestamps`.

The **filler line is gray** and labelled *logged, not delivered* — it is the phrase a voice agent
would have spoken to cover latency, and in this text agent it is recorded and discarded. Only the
backend's answer reaches the user. `filler_text` is optional, so a model may omit it entirely.

Rendering lives in `cli/ui.py`. `--ui` selects it: `auto` (default — rich on a terminal, plain
lines when piped to a file or CI log), `rich`, or `plain`. `--no-timestamps` and
`--no-show-internal` trim the trace.

**Frontend off** — one standalone, stateful backend, no delegation:

```bash
PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.chat \
  --config src/prototypes/text_frontend_backend_agent/config/examples/backend_only.yaml \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS
```

**Backend conversation history** — paired mode, but the backend keeps the conversation history
across delegated turns (`include: full`; plan
[`frontend-backend-agent-backend-history-plan.md`](frontend-backend-agent-backend-history-plan.md)):

```bash
PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.chat \
  --config src/prototypes/text_frontend_backend_agent/config/examples/backend_history.yaml \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS
```

With `config/agent.yaml`, `FBA_BACKEND_HISTORY=true` turns it on. The example configs are
standalone and do not read that variable; set `backend.conversation_history.enabled` in them. Each delegated turn logs a `backend_context` event
with the effective `include` (`off` when disabled) and the history size.

## 4. Smoke-test without a key

A scripted OpenAI-compatible endpoint ships with the package, so the whole loop — real HTTP client,
real wire format, real turn machine — can be exercised with no credential and no spend:

```bash
PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.stub_server &

PYTHONPATH=src NVIDIA_API_KEY=stub \
FRONTEND_LLM_BASE_URL=http://127.0.0.1:8799/v1 BACKEND_LLM_BASE_URL=http://127.0.0.1:8799/v1 \
uv run python -m prototypes.text_frontend_backend_agent.cli.chat \
  --config src/prototypes/text_frontend_backend_agent/config/examples/echo_tools.yaml \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS

kill %1
```

Ask `where is my order 5512?` and expect `Order 5512 is shipped.` The stub knows only that one
scenario; it is a wiring check, not a model.

## 5. External tool execution (the τ²-bench shape)

Here the agent returns its backend tool calls instead of running them, and the caller resumes with
`send_tool_results()`. The REPL cannot drive this, so a driver ships alongside it:

```bash
PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.external_demo \
  --config src/prototypes/text_frontend_backend_agent/config/examples/external_tools.yaml \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS \
  --message "where is my order 5512?"
```

`cli/external_demo.py` is ~40 lines and is the reference for the loop a harness adapter writes.
The rules it relies on, all enforced so a driver bug fails loudly instead of scoring zero quietly:

- results must cover **exactly** the outstanding ids; missing, duplicate or unknown ids raise
  `ToolProtocolError` (relax with `backend.tools.on_incomplete_results: synthesize_error_result`);
- a user message while results are outstanding raises `ToolProtocolError` unless
  `backend.tools.on_user_message_while_pending: discard_pending`;
- `turn.final_text` and `turn.tool_calls` are never both set — the τ² `AssistantMessage` rule.

## 6. Endpoint, models and reasoning

Defaults target the **NVIDIA Inference Hub**, with a different model and reasoning setting per
role. Every one is an environment variable, so a change needs no config edit:

| Variable | Default | |
|---|---|---|
| `NVIDIA_API_KEY` | *(required)* | Hub keys are `sk-…`, not `nvapi-…` |
| `FRONTEND_LLM_MODEL` | `nvidia/nvidia/nemotron-3.5-lightning` | reasoning **off** |
| `BACKEND_LLM_MODEL` | `nvidia/nvidia/nemotron-3-ultra` | reasoning **on** |
| `FRONTEND_LLM_BASE_URL` / `BACKEND_LLM_BASE_URL` | `https://inference-api.nvidia.com/v1` | |
| `FBA_EVENT_LOG` | *(unset)* | events go to loguru |

The doubled `nvidia/nvidia/` prefix in Hub model ids is not a typo; see
[`misc/runbook.md`](../runbook.md) for the stock-vs-Hub mapping.

The frontend only routes — delegate or answer, and state the request so it stands alone — on the
turn the user is waiting through, so it runs fast and without thinking. The backend does the task
work (choose tools, read results, decide when it has an answer), which is where thinking pays.
Reasoning is set per role in `extra_body`:

```yaml
frontend:
  llm:
    extra_body:
      chat_template_kwargs: {enable_thinking: false}
backend:
  llm:
    extra_body:
      chat_template_kwargs: {enable_thinking: true}
      reasoning_budget: 1024
```

Reasoning comes back in its own `reasoning_content` field, which this package never reads, so **it
cannot leak into a user-visible answer**. Measured over the 3-turn demo conversation: frontend
3 calls / 126 completion tokens, backend 7 calls / 550.

**Verify any endpoint before trusting it** — run this after changing model, endpoint or
`extra_body`:

```bash
PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.probe_reasoning \
  --model nvidia/nvidia/nemotron-3-ultra
```

```
verdict:
  reasoning toggle responds          PASS
  reasoning stays out of content     PASS
  tool calls survive both modes      PASS
```

It checks the three things the agent depends on and exits non-zero if any fails: the toggle
changes something; reasoning stays out of `content` (inline `<think>` would reach the user); and
`call_backend` still comes back as a real tool call. The endpoint must support **native tool
calling** — text-parsed tool calls are deliberately not implemented (plan §18).

## 7. Your own tools and domain

Two files, no package code:

1. **A tools module** exposing a `list[ToolSpec]`, modelled on `demo_tools.py`; pass it as
   `--tools your.module:TOOLS`.
2. **A config** copied from `config/examples/echo_tools.yaml` with `agent.persona`,
   `domain.policy` and `domain.capabilities` set — the only places a domain is ever named.

```bash
cp src/prototypes/text_frontend_backend_agent/config/examples/echo_tools.yaml \
   src/prototypes/text_frontend_backend_agent/config/examples/my_domain.yaml
```

Keep the copy in that directory, or `prompts.path: "../prompts.yaml"` stops resolving.

## 8. As a library

```python
import asyncio
from prototypes.text_frontend_backend_agent import ToolSpec, build_agent

tools = [ToolSpec(
    name="get_order",
    description="Look up one order by its id.",
    parameters={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    callable=lambda order_id: {"order_id": order_id, "status": "shipped"},
)]

async def main() -> None:
    agent = build_agent(
        "src/prototypes/text_frontend_backend_agent/config/examples/echo_tools.yaml", tools=tools
    )
    session = agent.new_session()
    turn, session = await agent.send("where is order 5512?", session)
    print(turn.final_text, "|", turn.usage.summary())

asyncio.run(main())
```

Run with `PYTHONPATH=src uv run python your_script.py`. The agent instance is immutable and
reusable across sessions; all conversation state is in the `SessionState` you pass in and get back.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'prototypes'` | `src/` not on the path | prefix with `PYTHONPATH=src` |
| `openai.AuthenticationError: 401` | key does not match the endpoint | Inference Hub needs `sk-…`; `integrate.api.nvidia.com` needs `nvapi-…`. Not interchangeable |
| `404` / model not found | stock model id against the Hub | Hub ids carry the doubled prefix: `nvidia/nvidia/nemotron-3-ultra` |
| `ConfigError: prompt catalog not found` | `prompts.path` is relative to the **config file** | keep configs in `config/examples/`, or use an absolute path |
| `ConfigError: backend.stateful: true is incompatible …` | `stateful` contradicts `agent.mode` | leave `stateful: auto` |
| Fallback text every turn | the frontend model is not emitting real tool calls | read the `contract violation` trace lines; confirm native tool calling with `cli.probe_reasoning` |
| `ToolProtocolError: no tool calls are outstanding` | `send_tool_results()` under `execution: internal` | tools run for you in that mode; only call it under `external` |
| Panel says tool calls were returned | config is `execution: external`, REPL drives `internal` | use `cli.external_demo` (§5) |
| Cost shows `unknown` | provider reports none and no local price table | set `accounting.pricing: {<model>: {prompt_per_1k: …, completion_per_1k: …}}` |

## 10. Deliberately absent

- **No cancel/discard tool** — nothing is cancellable in a turn-based API (plan §9). The reachable
  case is the `on_user_message_while_pending` policy.
- **No streaming, no voice, no Pipecat import** — a test walks the package AST to keep it so.
- **No τ² registration yet** — the seams exist (`ChatClient`, `SessionState`,
  `from_message_history()`, `cli/external_demo.py`); the adapter is its own change.

## 11. A longer session, end to end

A real session against the Inference Hub with the shipped `echo_tools.yaml` config, captured
verbatim (spinner frames removed). Start it with the §3 command and type the same lines:

```
╭─────────────────────── Order Assistant ────────────────────────╮
│  frontend  nvidia/nvidia/nemotron-3.5-lightning  reasoning off │
│   backend  nvidia/nvidia/nemotron-3-ultra  reasoning on        │
│  endpoint  https://inference-api.nvidia.com/v1                 │
│     tools  get_order, cancel_order                             │
│ execution  internal                                            │
╰─────────────────────── frontend_backend ───────────────────────╯
/reset  start a new session
/usage  token, latency and cost totals
/state  dump SessionState as JSON
/tools  tools handed to the backend
/quit   exit
```

```
you: hi there
·   19:13:01.673  +2.40s delegate → backend               The user said hello and is greeting the
                                                          Order Assistant.
·   19:13:01.673  +2.40s filler (logged, not delivered)   Hello! How can I help?
·   19:13:10.409 +11.14s backend final text               Hello! I'm the Order Assistant. How can I
                                                          help you today?
·   19:13:10.409 +11.14s usage                            2 calls · 2988 tok · 11.13 s · cost
                                                          unknown
╭─ agent 19:13:10.413 ─────────────────────────────────────────────────────────────────────────────╮
│ Hello! I'm the Order Assistant. How can I help you today?                                        │
╰──────────────────────────────────────────────────── 2 calls · 2988 tok · 11.13 s · cost unknown ─╯
```

**Turn 1 — a greeting that should not have been delegated.** The prompt's Direct mode says to
answer small talk without a tool, and the frontend delegated anyway: you can see `delegate →
backend` and a gray `filler` line, then an 11 s round trip for "Hello!". This is a real
prompt-adherence miss, left in rather than edited out — it is the kind of thing the trace exists to
make visible, and the sort of case prompt tuning targets. Everything downstream still behaved: one
answer out, filler recorded and discarded.

```
you: where is my order 5512?
·   19:13:11.611  +1.20s delegate → backend               The user is asking for the current status
                                                          of order 5512.
·   19:13:15.770  +5.36s backend wants tools              get_order({"order_id": "5512"})
·   19:13:15.770  +5.36s tool executed                    get_order({"order_id": "5512"}) →
                                                          {"carrier": "Parcelflow", "found": true,
                                                          "order_id": "5512", "shipped_on":
                                                          "2026-09-19", "status": "shipped"}
·   19:13:16.691  +6.28s backend final text               Order 5512 has been shipped. It was
                                                          shipped on September 19, 2026 via
                                                          Parcelflow.
·   19:13:16.691  +6.28s usage                            3 calls · 4045 tok · 6.28 s · cost unknown
╭─ agent 19:13:16.700 ─────────────────────────────────────────────────────────────────────────────╮
│ Order 5512 has been shipped. It was shipped on September 19, 2026 via Parcelflow.                │
╰───────────────────────────────────────────────────── 3 calls · 4045 tok · 6.28 s · cost unknown ─╯
```

**Turn 2 — the normal path.** Frontend delegates a self-contained query, backend calls
`get_order`, reads the result, writes the answer. Three LLM calls (one frontend, two backend),
6.3 s.

```
you: when did it ship?
·   19:13:17.782  +1.08s frontend answered directly       The order shipped on September 19, 2026.
·   19:13:17.782  +1.08s usage                            1 calls · 2471 tok · 1.08 s · cost unknown
╭─ agent 19:13:17.786 ─────────────────────────────────────────────────────────────────────────────╮
│ The order shipped on September 19, 2026.                                                         │
╰───────────────────────────────────────────────────── 1 calls · 2471 tok · 1.08 s · cost unknown ─╯
```

**Turn 3 — answered from history, no backend call at all.** `frontend answered directly`, 1.1 s,
one LLM call. The frontend knew the ship date because the previous turn's backend answer was
written into its history as the tool result *and* an assistant message. That is the four-message
group from the design doc doing its job — without it, this turn would have needed another round
trip, or worse, invented a date.

```
you: can you cancel it?
·   19:13:18.477  +0.69s delegate → backend               The user wants to cancel order 5512. The
                                                          order was shipped on September 19, 2026,
                                                          so it has already shipped and cannot be
                                                          cancelled.
·   19:13:20.258  +2.47s backend wants tools              get_order({"order_id": "5512"})
·   19:13:20.259  +2.47s tool executed                    get_order({"order_id": "5512"}) →
                                                          {"carrier": "Parcelflow", "found": true,
                                                          "order_id": "5512", "shipped_on":
                                                          "2026-09-19", "status": "shipped"}
·   19:13:21.097  +3.31s backend final text               Order 5512 has already shipped on
                                                          September 19, 2026, so it cannot be
                                                          cancelled. You can start a return once the
                                                          package arrives.
·   19:13:21.097  +3.31s usage                            3 calls · 4303 tok · 3.31 s · cost unknown
╭─ agent 19:13:21.107 ─────────────────────────────────────────────────────────────────────────────╮
│ Order 5512 has already shipped on September 19, 2026, so it cannot be cancelled. You can start a │
│ return once the package arrives.                                                                 │
╰───────────────────────────────────────────────────── 3 calls · 4303 tok · 3.31 s · cost unknown ─╯
```

**Turn 4 — policy refusal, and note the query.** The frontend restated the whole situation
("was shipped on September 19, 2026, so it has already shipped and cannot be cancelled") rather
than sending a bare "cancel it". The backend still re-checked with `get_order` instead of trusting
that summary, then refused per `domain.policy` and offered a return. Good division of labour: the
frontend carries context, the backend owns the decision and the tools.

```
you: what about 5513?
·   19:13:25.972  +4.86s delegate → backend               The user is asking about the status of
                                                          order 5513.
·   19:13:26.643  +5.53s backend wants tools              get_order({"order_id": "5513"})
·   19:13:26.643  +5.53s tool executed                    get_order({"order_id": "5513"}) →
                                                          {"found": true, "order_id": "5513",
                                                          "placed_on": "2026-09-21", "status":
                                                          "processing"}
·   19:13:27.459  +6.35s backend final text               Order 5513 was placed on September 21,
                                                          2026 and is currently processing.
·   19:13:27.459  +6.35s usage                            3 calls · 4322 tok · 6.35 s · cost unknown
╭─ agent 19:13:27.468 ─────────────────────────────────────────────────────────────────────────────╮
│ Order 5513 was placed on September 21, 2026 and is currently processing.                         │
╰───────────────────────────────────────────────────── 3 calls · 4322 tok · 6.35 s · cost unknown ─╯
```

**Turn 5 — an elliptical follow-up.** "what about 5513?" becomes "The user is asking about the
status of order 5513." The frontend's job is exactly this: make the request stand alone so the
backend never needs the conversation.

```
you: yes, please cancel that one
·   19:13:28.171  +0.70s delegate → backend               The user wants to cancel order 5513, which
                                                          is currently processing and has not
                                                          shipped yet.
·   19:13:33.397  +5.93s backend wants tools              get_order({"order_id": "5513"})
·   19:13:33.397  +5.93s tool executed                    get_order({"order_id": "5513"}) →
                                                          {"found": true, "order_id": "5513",
                                                          "placed_on": "2026-09-21", "status":
                                                          "processing"}
·   19:13:34.178  +6.71s backend wants tools              cancel_order({"order_id": "5513"})
·   19:13:34.178  +6.71s tool executed                    cancel_order({"order_id": "5513"}) →
                                                          {"cancelled": true, "order_id": "5513"}
·   19:13:47.967 +20.50s backend final text               Order 5513 has been cancelled.
·   19:13:47.967 +20.50s usage                            4 calls · 5383 tok · 20.50 s · cost
                                                          unknown
╭─ agent 19:13:47.978 ─────────────────────────────────────────────────────────────────────────────╮
│ Order 5513 has been cancelled.                                                                   │
╰──────────────────────────────────────────────────── 4 calls · 5383 tok · 20.50 s · cost unknown ─╯
```

**Turn 6 — two tool rounds in one turn.** `get_order` to confirm it has not shipped, then
`cancel_order`, then the answer — all inside a single `send()`, with the backend suspending and
resuming around each batch. 20.5 s here, against 3–6 s for the other turns; Ultra's latency is
variable and this was the worst turn of the session.

```
you: can you also order me a new laptop?
·   19:13:49.362  +1.38s frontend answered directly       I can only check order status and cancel
                                                          orders that have not shipped. I cannot
                                                          place hardware orders.
·   19:13:49.362  +1.38s usage                            1 calls · 2908 tok · 1.38 s · cost unknown
╭─ agent 19:13:49.366 ─────────────────────────────────────────────────────────────────────────────╮
│ I can only check order status and cancel orders that have not shipped. I cannot place hardware   │
│ orders.                                                                                          │
╰───────────────────────────────────────────────────── 1 calls · 2908 tok · 1.38 s · cost unknown ─╯
```

**Turn 7 — unsupported, handled without a round trip.** The capability list is in the frontend
prompt, so it declines directly in 1.4 s rather than delegating something the backend cannot do
either.

**Finally `/usage`.** The per-role split is the number to watch: the frontend is cheap in output
(275 completion tokens over 7 calls) and expensive in input, because it carries the whole
conversation; the backend is the opposite (800 completion tokens over 10 calls) since it does the
thinking and the tool work. Cost reads `unknown` because the endpoint reports none and no
`accounting.pricing` table is configured — deliberately not shown as `$0.00`.

```
you: /usage
session usage
┏━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ role     ┃ calls ┃ prompt ┃ completion ┃ latency ┃    cost ┃
┡━━━━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ frontend │     7 │  17800 │        275 │ 12.31 s │ unknown │
│ backend  │    10 │   7545 │        800 │ 37.72 s │ unknown │
├──────────┼───────┼────────┼────────────┼─────────┼─────────┤
│ total    │    17 │  25345 │       1075 │ 50.02 s │ unknown │
└──────────┴───────┴────────┴────────────┴─────────┴─────────┘
```
### What the session demonstrates

| Behaviour | Turn |
|---|---|
| Delegation with a self-contained query | 2, 4, 5, 6 |
| Answering from history, no backend call | 3 |
| Backend answer written back into frontend history | 3 (uses turn 2's answer) |
| Multiple tool rounds inside one turn | 6 |
| Domain policy enforced by the backend | 4 |
| Capability list enforced by the frontend | 7 |
| Filler logged in gray, never delivered | 1 |
| A prompt-adherence miss, visible in the trace | 1 |

Every `agent` panel in that session is the *only* thing a caller would have received:
`--no-show-internal` would print those seven lines and nothing else.
