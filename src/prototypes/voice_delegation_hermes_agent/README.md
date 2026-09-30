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
- **τ³ failure fixes:** [`tau3-failure-fixes-plan.md`](../../../misc/prototypes/frontend-delegation-hermes/tau3-failure-fixes-plan.md).
  Every fix is behind a switch that is off by default (refer to "Failure-Fix Switches" below).

## Layout

| Path | Process | Role |
|---|---|---|
| `server.py`, `config.py` | voice server (Py 3.12) | Plugs `DelegationTurnManager` into the voice package's `build_app` through `SessionHooks`; loads `delegation_agent.yaml` and profiles |
| `prompt_features.py` | voice server, gateway | Prompt variants: the `prompt_features` maps and the prompt hashes of the deployed fingerprint |
| `engine/` | voice server | `turn_manager.py` (frontend, backend and output lanes), `transcript.py` (shared history, playback outcomes), `output_scheduler.py` (hold and stale rules), `backend_link.py` (WebSocket to the gateway), `spelling_hold.py` (M3.2 predicate), `spoken_text.py` (G2 answer cleanup) |
| `frontend/` | voice server | `delegate_tool.py` (the tool contract), `decider.py` (LLM decider with repair, hedging and guards; the rule-based stub), `status_verbalizer.py`, `prompts.py` |
| `tools/relay.py` | voice server | Backend tool calls as Realtime `function_call` batches (τ³) or local callables (browser); argument normalization |
| `backend/` | all (stdlib only) | The shared contract (`protocol.py`, `writer.py`), `controller.py` (state machine, run epochs, outcome table), `context_queue.py` (exactly-once history delivery), `agent_like.py` (`FakeAgent`) |
| `sidecar/` | gateway (host) | `gateway_server.py` (`WS /v1/backend`, `/health`), `session_runtime.py`, `worker_pool.py` (spawn / stop / kill / respawn), `homes.py` (per-worker `HERMES_HOME`), `inprocess_link.py` (stub backend) |
| `worker/` | Hermes worker (Py 3.14) | `worker_main.py` (Unix-socket control loop), `tool_futures.py` (keyed result futures), `hermes_adapter.py` (the only Hermes import), `fake_host.py` |
| `cli/` | tools | `delegation_replay.py` (frontend decision gate), `backend_probe.py` (gateway without audio), `report_adapter.py` (event log → `fba_voice_metrics.py` records), `fingerprint_check.py` (deployed fingerprint of an arm; exit 1 on a mismatch), `spelling_hold_replay.py` (spelling-hold predicate over an event log) |
| `config/` | — | `delegation_agent.yaml`, `gateway.yaml` (+ `gateway.fake.yaml`, `gateway.stub_gate.yaml`, and the backend prompt variants `gateway.spelling_v2.yaml`, `gateway.spoken_output.yaml`, `gateway.write_consent.yaml`), `prompts.yaml` (frontend), `prompts.backend.yaml` (backend), `voice/*.yaml` (voice-package profiles), `profiles/*.yaml` |

## Profiles

| Profile | Use |
|---|---|
| `profiles/tau3_eval.yaml` | τ³ / OpenAI Realtime clients: client-executed tools, no greeting, argument normalization, port 8775 |
| `profiles/browser_demo.yaml` | Browser page: TLS, greeting, demo tools run in the server, `FDH_BACKEND_DELAY_S` (default 5 s) before each backend run, port 8776 |
| `profiles/tau3_eval_silent_ack.yaml` | Ablation: the filler of a delegated turn is not spoken |
| `profiles/stub.yaml`, `profiles/stub_gate.yaml` | No GPU / LLM / Hermes (with `--stub-speech`); `stub_gate` makes the fake backend call a tool so τ³ Gate A passes |
| `profiles/tau3_eval_baseline.yaml` | Control arm of the failure fixes: `tau3_eval.yaml` with every new switch pinned off. Pair it with `gateway.yaml` |
| `profiles/tau3_arm_*.yaml` | One failure fix each, on top of the baseline: `m1_status`, `m3_spelling` (with `gateway.spelling_v2.yaml`), `m2_replay`, `g1_filler_dedupe`, `g2_short_answers` (with `gateway.spoken_output.yaml`). G4 is the baseline profile with `gateway.write_consent.yaml` |
| `profiles/tau3_airline_canary.yaml` | M1 and M3 together (with `gateway.spelling_v2.yaml`) |

Every profile has 800 ms end-of-turn silence (the client's value is ignored), transcript normalization on,
and no WebSocket keepalive ping. The configuration knobs are listed in the runbook, §9.

### Failure-Fix Switches

Each switch below is off by default and in `tau3_eval_baseline.yaml`. With every switch off, the rendered
prompts are byte-identical to agent commit `3a7e04a` (`test_fdh_golden_prompts.py`).

| Switch | Where | Fix |
|---|---|---|
| `output.proactive_status` | `delegation_agent.yaml` | M1: one `status_proactive_lines` line after `after_s` of silence while WORKING (`FDH_PROACTIVE_AFTER_S` in the arm profile) |
| `normalization.transcript.spelled_runs`, `normalization.tool_arguments.escalate_invalid_message_key` | `voice/tau3_spelling.yaml` | M3.1 and M3.3: spelled letters and digits are joined; the local "invalid ID" answer asks for a read-back, then for the whole ID |
| `delegation.spelling_hold` | `delegation_agent.yaml` | M3.2: wait `hold_ms` before deciding a turn that ends mid-spelling |
| `delegation.replay_unheard_answer` + `prompt_features.replay_intent` | `delegation_agent.yaml` | M2 (experimental, RC2): replay an unheard answer on `request=status` in IDLE |
| `delegation.filler_dedupe` | `delegation_agent.yaml` | G1: drop or replace a repeated filler (`filler_alternatives`) |
| `output.clean_answers` | `delegation_agent.yaml` | G2: strip markdown and make one sentence per list item before TTS |
| `prompt_features.spelling_v2`, `spoken_output`, `write_consent` | `gateway.yaml` | M3.4, G2 and G4: backend prompt variants; `gateway.<name>.yaml` turns one on |

The voice server and the gateway log a deployed fingerprint (`fdh_session_start.features`, `frontend_prompt`,
`backend_configured`). Adding these default keys changed the `config_hash` of every profile compared to earlier
runs. To check that every session of an arm ran the expected switches and prompts, run `fingerprint_check`:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.fingerprint_check \
  logs/fdh_voice_events.jsonl \
  --profile src/prototypes/voice_delegation_hermes_agent/config/profiles/tau3_eval_baseline.yaml \
  --gateway-config src/prototypes/voice_delegation_hermes_agent/config/gateway.yaml
```

The runbook, §6.1, lists each arm with its gateway config and the M1 calibration.

## Tests

```bash
uv run pytest tests/unit/prototypes/delegation tests/unit/prototypes/voice -q
```

- `test_fdh_workflow_table.py` checks all 12 rows of `workflow.csv`.
- `test_fdh_gateway_processes.py` runs real worker processes with the fake agent.
- `test_fdh_hermes_multisession.py` runs real Hermes workers against a fake OpenAI server. It is skipped
  when the Hermes venv (`~/.cache/fdh/hermes-venv-314`) is missing.
- `test_fdh_hermes_adapter.py` needs the Hermes interpreter; the command is in its docstring.
- `test_fdh_golden_prompts.py` checks the baseline prompts against `fixtures/golden_prompts.json`, and
  `test_fdh_tau3_fixes.py` covers the failure-fix switches. After an intended baseline change, regenerate the golden
  files with `PYTHONPATH=src uv run python tests/unit/prototypes/delegation/_fdh_golden.py --write`.
