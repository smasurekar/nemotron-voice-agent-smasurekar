# Voice Frontend Delegation to a Hermes Backend (prototype)

An OpenAI Realtime (GA) voice agent with two agents that are decoupled in time:

- **The frontend** (nemotron-3.5-lightning) runs on every user turn and makes exactly one call:
  `delegate(delegate, filler_text, request)`. `filler_text` is spoken immediately. `request=status`
  asks a working backend for progress without disturbing it.
- **The backend** is a Hermes `AIAgent` (nemotron-3-ultra, reasoning on), one per Realtime session, in its
  own Python 3.14 worker process behind a gateway. Depending on its state it starts a session, continues it
  (`run_conversation`), steers the running task (`steer` / `redirect`), or reports progress
  (`get_activity_summary`). It follows [`workflow.csv`](../../../misc/prototypes/frontend-delegation-hermes/workflow.csv).

- **Run it:** [`runbook.md`](../../../misc/prototypes/frontend-delegation-hermes/runbook.md). It covers the
  τ³ Realtime server on 8775 and the browser page on 8776 with a 5 s simulated delay.
- **Design:** [`prototype-plan.md`](../../../misc/prototypes/frontend-delegation-hermes/prototype-plan.md).
  §22 has the implementation notes and measurements.

## Layout

| Path | Process | Role |
|---|---|---|
| `server.py`, `config.py` | voice server (Py 3.12) | Plugs `DelegationTurnManager` into the voice package's `build_app` through `SessionHooks`; loads `delegation_agent.yaml` and profiles |
| `engine/` | voice server | `turn_manager.py` (frontend, backend and output lanes), `transcript.py` (shared history, playback outcomes), `output_scheduler.py` (hold and stale rules), `backend_link.py` (WebSocket to the gateway) |
| `frontend/` | voice server | `delegate_tool.py` (the tool contract), `decider.py` (LLM decider with repair, hedging and guards; the rule-based stub), `status_verbalizer.py`, `prompts.py` |
| `tools/relay.py` | voice server | Backend tool calls as Realtime `function_call` batches (τ³) or local callables (browser); argument normalization |
| `backend/` | all (stdlib only) | The shared contract (`protocol.py`, `writer.py`), `controller.py` (state machine, run epochs, outcome table), `context_queue.py` (exactly-once history delivery), `agent_like.py` (`FakeAgent`) |
| `sidecar/` | gateway (host) | `gateway_server.py` (`WS /v1/backend`, `/health`), `session_runtime.py`, `worker_pool.py` (spawn / stop / kill / respawn), `homes.py` (per-worker `HERMES_HOME`), `inprocess_link.py` (stub backend) |
| `worker/` | Hermes worker (Py 3.14) | `worker_main.py` (Unix-socket control loop), `tool_futures.py` (keyed result futures), `hermes_adapter.py` (the only Hermes import), `fake_host.py` |
| `cli/` | tools | `delegation_replay.py` (frontend decision gate), `backend_probe.py` (gateway without audio), `report_adapter.py` (event log → `fba_voice_metrics.py` records) |
| `config/` | — | `delegation_agent.yaml`, `gateway.yaml` (+ `gateway.fake.yaml`, `gateway.stub_gate.yaml`), `prompts.yaml` (frontend), `prompts.backend.yaml` (backend), `voice/*.yaml` (voice-package profiles), `profiles/*.yaml` |

## Profiles

| Profile | Use |
|---|---|
| `profiles/tau3_eval.yaml` | τ³ / OpenAI Realtime clients: client-executed tools, no greeting, argument normalization, port 8775 |
| `profiles/browser_demo.yaml` | Browser page: TLS, greeting, demo tools run in the server, `FDH_BACKEND_DELAY_S` (default 5 s) before each backend run, port 8776 |
| `profiles/tau3_eval_silent_ack.yaml` | Ablation: the filler of a delegated turn is not spoken |
| `profiles/stub.yaml`, `profiles/stub_gate.yaml` | No GPU / LLM / Hermes (with `--stub-speech`); `stub_gate` makes the fake backend call a tool so τ³ Gate A passes |

Every profile has 800 ms end-of-turn silence (the client's value is ignored), transcript normalization on,
and no WebSocket keepalive ping. The configuration knobs are listed in the runbook, §9.

## Tests

```bash
uv run pytest tests/unit/prototypes/delegation tests/unit/prototypes/voice -q
```

- `test_fdh_workflow_table.py` checks all 12 rows of `workflow.csv`.
- `test_fdh_gateway_processes.py` runs real worker processes with the fake agent.
- `test_fdh_hermes_multisession.py` runs real Hermes workers against a fake OpenAI server. It is skipped
  when the Hermes venv (`~/.cache/fdh/hermes-venv-314`) is missing.
- `test_fdh_hermes_adapter.py` needs the Hermes interpreter; the command is in its docstring.
