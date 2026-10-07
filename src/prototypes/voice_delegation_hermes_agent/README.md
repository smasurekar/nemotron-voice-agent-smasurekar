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
  Every fix is behind a switch. The YAML defaults turn every fix on except M2 (experimental); the Python
  code defaults stay off (refer to "Failure-Fix Switches" below).
- **τ³ identity fixes:** [`tau3-identity-fixes-plan.md`](../../../misc/prototypes/frontend-delegation-hermes/tau3-identity-fixes-plan.md).
  I1, I3, and I4 follow the same switch rules and are on in the YAML defaults.
- **Domain-generic rules:** [`tau3-voice-domain-specific-changes-genericization.md`](../../../misc/prototypes/frontend-delegation-hermes/tau3-voice-domain-specific-changes-genericization.md).
  The I1 telecom note is replaced by a generic `<phone_numbers>` block, and `profiles/realtime_eval.yaml`
  derives tool-argument rules, result-hint targets, and spelling-hold patterns from each session's tool
  schemas instead of tool or domain names. Its effect on reward is not measured yet and requires validation.
- **τ³ geval failure fixes:** [`tau3-geval-failure-fixes-plan.md`](../../../misc/prototypes/frontend-delegation-hermes/tau3-geval-failure-fixes-plan.md).
  A1 to A3, B1 to B3, C1, and C2 are implemented behind switches that are on in the YAML defaults
  (`gateway.yaml`, `gateway.eval.yaml`, `voice/realtime_eval.yaml`) and off in the Python code defaults. The
  control `realtime_eval_control.yaml` with `gateway.eval_control.yaml` turns them off, and each arm turns one on
  over it (refer to "Geval-Fix Switches" below). None of the plan's gates has run yet, so none is validated.

## Layout

| Path | Process | Role |
|---|---|---|
| `server.py`, `config.py` | voice server (Py 3.12) | Plugs `DelegationTurnManager` into the voice package's `build_app` through `SessionHooks`; loads `delegation_agent.yaml` and profiles |
| `prompt_features.py` | voice server, gateway | Prompt variants: the `prompt_features` maps and the prompt hashes of the deployed fingerprint |
| `engine/` | voice server | `turn_manager.py` (frontend, backend and output lanes), `transcript.py` (shared history, playback outcomes), `output_scheduler.py` (hold and stale rules), `backend_link.py` (WebSocket to the gateway), `spelling_hold.py` (M3.2 predicate), `spoken_text.py` (G2 answer cleanup) |
| `frontend/` | voice server | `delegate_tool.py` (the tool contract), `decider.py` (LLM decider with repair, hedging and guards; the rule-based stub), `status_verbalizer.py`, `prompts.py` |
| `tools/relay.py` | voice server | Backend tool calls as Realtime `function_call` batches (τ³) or local callables (browser); argument normalization |
| `tools/result_hints.py` | voice server | I3 and I4: the per-session miss counter and the recovery note appended to a failed lookup's result (`tools: auto` watches every read tool of the session) |
| `tools/format_retry.py`, `tools/field_provenance.py` | voice server | A1 and A1-lite: the format-retry trigger for a not-found phone lookup; A3: the spelled-or-heard line of a repeated person-lookup miss |
| `backend/` | all (stdlib only) | The shared contract (`protocol.py`, `writer.py`), `controller.py` (state machine, run epochs, outcome table), `context_queue.py` (exactly-once history delivery), `agent_like.py` (`FakeAgent`) |
| `sidecar/` | gateway (host) | `gateway_server.py` (`WS /v1/backend`, `/health`), `session_runtime.py`, `worker_pool.py` (spawn / stop / kill / respawn), `homes.py` (per-worker `HERMES_HOME`), `inprocess_link.py` (stub backend) |
| `worker/` | Hermes worker (Py 3.14) | `worker_main.py` (Unix-socket control loop), `tool_futures.py` (keyed result futures), `hermes_adapter.py` (the only Hermes import), `fake_host.py` |
| `cli/` | tools | `delegation_replay.py` (frontend decision gate), `backend_probe.py` (gateway without audio), `report_adapter.py` (event log → `fba_voice_metrics.py` records), `fingerprint_check.py` (deployed fingerprint of an arm; exit 1 on a mismatch), `spelling_hold_replay.py` (spelling-hold predicate over an event log), `relay_replay.py` (geval gate G1), `backend_decision_replay.py` (geval gate G2) |
| `config/` | — | `delegation_agent.yaml`, `gateway.yaml` (+ `gateway.fake.yaml`, `gateway.stub_gate.yaml`, the control `gateway.baseline.yaml`, the pinned evaluation gateway `gateway.eval.yaml`, and the backend prompt variants `gateway.spelling_v2.yaml`, `gateway.spoken_output.yaml`, `gateway.write_consent.yaml`; the identity-fix control `gateway.identity_control.yaml`; the geval-fix arms `gateway.eval_<fix>.yaml`), `prompts.yaml` (frontend), `prompts.backend.yaml` (backend), `voice/*.yaml` (voice-package profiles), `profiles/*.yaml` |

## Profiles

| Profile | Use |
|---|---|
| `profiles/realtime_eval.yaml` | The single domain-agnostic evaluation profile for OpenAI Realtime clients (`voice/realtime_eval.yaml`), port 8775. Same fixes as `tau3_eval.yaml`, but tool-argument rules (`rules: auto`), result-hint targets (`tools: auto`), and spelling-hold patterns (`complete_patterns: auto`) come from the session's tool schemas; the retry guard covers every tool. Pair it with `gateway.eval.yaml` |
| `profiles/tau3_eval.yaml` | Historical τ³ profile, kept as a control: client-executed tools, no greeting, argument normalization with a name-keyed `get_user_details` rule, every failure fix except M2 (a six-character code as a complete spelled value), every identity fix (`voice/tau3_fixes.yaml`), port 8775. Pair it with `gateway.yaml` |
| `profiles/browser_demo.yaml` | Browser page: TLS, greeting, demo tools run in the server, `FDH_BACKEND_DELAY_S` (default 5 s) before each backend run, port 8776 |
| `profiles/tau3_eval_silent_ack.yaml` | Ablation: the filler of a delegated turn is not spoken |
| `profiles/stub.yaml`, `profiles/stub_gate.yaml` | No GPU / LLM / Hermes (with `--stub-speech`); `stub_gate` makes the fake backend call a tool so τ³ Gate A passes |
| `profiles/tau3_eval_baseline.yaml` | Control arm of the failure fixes: `tau3_eval.yaml` with every new switch pinned off. Pair it with `gateway.baseline.yaml` |
| `profiles/tau3_arm_*.yaml` | One failure fix each, on top of the baseline: `m1_status`, `m2_replay`, and `g1_filler_dedupe` (with `gateway.baseline.yaml`), `m3_spelling` (with `gateway.spelling_v2.yaml`), `g2_short_answers` (with `gateway.spoken_output.yaml`). G4 is the baseline profile with `gateway.write_consent.yaml`. Identity fixes, over the identity control: `i3_recovery_hint` (`voice/tau3_recovery_hint.yaml`) and `i4_word_spelling` (`voice/tau3_fixes.yaml`, I3 and I4), both with `gateway.identity_control.yaml`; I1 is the identity control with `gateway.yaml` |
| `profiles/tau3_identity_control.yaml` | Control arm of the identity fixes: `tau3_eval.yaml` (every failure fix except M2) without I1, I3 and I4. Pair it with `gateway.identity_control.yaml` |
| `profiles/tau3_airline_canary.yaml` | M1 and M3 together (with `gateway.spelling_v2.yaml`) |
| `profiles/realtime_eval_arm_*.yaml` | One geval fix each over `realtime_eval_control.yaml`, the control: `a1_format_retry`, `a1_lite_format_hint`, and `a3_field_provenance` (with `gateway.eval_control.yaml`), `a2_format_correction` (with `gateway.eval_a2_format_correction.yaml`). Each uses `voice/realtime_eval_<fix>.yaml`. B1 to C2 use `realtime_eval_control.yaml` with `gateway.eval_b1_write_consent_v2.yaml`, `gateway.eval_b2_policy_check.yaml`, `gateway.eval_b3_request_scope.yaml`, `gateway.eval_c1_transfer_check.yaml`, or `gateway.eval_c2_capability_check.yaml` |
| `profiles/realtime_eval_control.yaml` | Control of the geval fixes: `realtime_eval.yaml` with A1, A2 and A3 off (`voice/realtime_eval_control.yaml`). Pair it with `gateway.eval_control.yaml` (A2, B1 to B3, C1, and C2 off): the geval runs' deployment |

Every profile has 800 ms end-of-turn silence (the client's value is ignored), transcript normalization on,
and no WebSocket keepalive ping. The configuration knobs are listed in the runbook, §9.

### Failure-Fix Switches

Each switch below is on in the YAML defaults (`delegation_agent.yaml`, `gateway.yaml`, `voice/browser.yaml`,
`profiles/tau3_eval.yaml`, `voice/tau3_fixes.yaml`), except M2, which stays off. The Python code defaults are off.
`tau3_eval_baseline.yaml` with `gateway.baseline.yaml` pins every switch off. With every switch off, the
rendered prompts are byte-identical to agent commit `3a7e04a` (`test_fdh_golden_prompts.py`). The following
table lists the switches:

| Switch | Where | Fix |
|---|---|---|
| `output.proactive_status` | `delegation_agent.yaml` | M1: one `status_proactive_lines` line after `after_s` of silence while WORKING (`FDH_PROACTIVE_AFTER_S`, default 15; calibrate it per host) |
| `normalization.transcript.spelled_runs`, `normalization.tool_arguments.escalate_invalid_message_key` | `voice/tau3_spelling.yaml` (extended by the default voice profile `voice/tau3_fixes.yaml`; `voice/browser.yaml` has spelled runs only) | M3.1 and M3.3: spelled letters and digits are joined; the local "invalid ID" answer asks for a read-back, then for the whole ID |
| `delegation.spelling_hold` | `delegation_agent.yaml` | M3.2: wait `hold_ms` before deciding a turn that ends mid-spelling (`tau3_eval.yaml` never holds a complete six-character code; with `complete_patterns: auto`, `realtime_eval.yaml` never holds a value in the strict shape of an `id`, `*_id`, or `*_ids` example from the tool schemas, matched case-insensitively; examples with a name-like part, such as `ab_cd_12`, give no pattern because their length varies) |
| `delegation.replay_unheard_answer` + `prompt_features.replay_intent` | `delegation_agent.yaml` | M2 (experimental, RC2, off by default): replay an unheard answer on `request=status` in IDLE |
| `delegation.filler_dedupe` | `delegation_agent.yaml` | G1: drop or replace a repeated filler (`filler_alternatives`) |
| `output.clean_answers` | `delegation_agent.yaml` | G2: strip markdown and make one sentence per list item before TTS |
| `prompt_features.spelling_v2`, `spoken_output`, `write_consent` | `gateway.yaml` | M3.4, G2 and G4: backend prompt variants, all on; `gateway.baseline.yaml` turns all off, and `gateway.<name>.yaml` turns one on over it |
| `prompt_features.phone_format` | `gateway.yaml`, `gateway.eval.yaml` (off in `gateway.baseline.yaml` and `gateway.identity_control.yaml`) | I1, now domain-generic: a `<phone_numbers>` block in `backend_system`. Lookups by a ten-digit phone number use XXX-XXX-XXXX unless the tool description shows another format, with one retry in the other common form; tools that store or change a number get it unchanged |
| `prompt_features.domain_notes` + `domains` | `gateway.yaml`, `prompts.backend.yaml` (`backend_domain_notes`) | The mechanism remains, but no note ships: `backend_domain_notes: {}`, `domains: {}`, and `domain_notes: false`, so `backend_domain` is always `""` |
| `normalization.tool_arguments.result_hints` | `voice/tau3_fixes.yaml`, `voice/realtime_eval.yaml` (schema in the voice package's `voice_agent.yaml`) | I3 and I4: after a failed lookup, append `lookup_not_found_hint` on the first miss and `lookup_not_found_hint_words` on later misses, at most 3 until a lookup succeeds. `voice/tau3_fixes.yaml` watches `find_user_id_by_name_zip`, `find_user_id_by_email`, and `get_user_details`, and a local "invalid ID" answer counts as a miss. `voice/realtime_eval.yaml` uses `tools: auto` (every read tool); its `failure_pattern` fires only when the not-found text names a user, customer, member, client, or profile, and a local "invalid" answer counts only for a person argument such as `user_id`. Logged as `result_hint` events |

### Geval-Fix Switches

The switches below come from the geval fix plan. They are on in `gateway.yaml`, `gateway.eval.yaml` (pinned)
and `voice/realtime_eval.yaml`, and off in the voice package's `voice_agent.yaml` and in the Python code. The
control (`gateway.eval_control.yaml`, `voice/realtime_eval_control.yaml`) turns them off; each
`gateway.eval_<fix>.yaml` or `voice/realtime_eval_<fix>.yaml` turns one on over it. With every new switch off, each
combination of the five earlier backend switches renders the same prompt as before. The following table lists
the switches:

| Switch | Where | Fix |
|---|---|---|
| `prompt_features.format_correction` | `gateway.yaml` | A2: inside the `spelling_v2` text, a change of separators or letter case only is a format correction, not a variant. Its voice half is `result_hints.message_key: lookup_not_found_hint_format` |
| `prompt_features.write_consent_v2` | `gateway.yaml` | B1: inside `<consent>`, the confirmation the policy requires, to the exact change, before a write |
| `prompt_features.policy_check`, `request_scope` | `gateway.yaml` | B2 and B3: a `<before_change>` block. B2 checks the policy for the exact change and allows a change within its limits. B3 covers every record and option the request names |
| `prompt_features.transfer_check`, `capability_check` | `gateway.yaml` | C1 and C2: a `<before_transfer>` block. C1 transfers at once when the policy requires it, and otherwise does the remaining steps first. C2 checks the tool list before saying a request is impossible |
| `normalization.tool_arguments.format_retry` | `voice/realtime_eval_a1_*.yaml` (schema in `voice_agent.yaml`) | A1 and A1-lite: after a not-found lookup by a ten-digit phone field, the same digits in the other format (XXX-XXX-XXXX or digits only), once per value. `mode: call` makes the relay look it up itself, but only on a tool whose schema declares `annotations: {readOnlyHint: true, idempotentHint: true}`. Otherwise, and always with `mode: hint`, the relay appends `format_retry_hint` and the backend decides. Requires the retry guard's `permanent_failure_pattern` |
| `normalization.tool_arguments.result_hints.field_provenance` | `voice/realtime_eval_a3_field_provenance.yaml` | A3: from the second person-lookup miss on, the note also says which values the user spelled, which were heard as a word, and which are unknown. A turn with two or more spelled spans is unknown for every field |

The phone fields come from the session's tool schemas: string arguments of read tools whose name contains
`phone`, `mobile`, `msisdn`, or `telephone`, or whose description says "phone number". A value's shape never
qualifies an argument. On the τ³ tools, only telecom `get_customer_by_phone.phone_number` qualifies, and no tool
declares read-only semantics, so A1 runs as A1-lite there. The relay's own call has the id `<id>.fmt`. It goes
out in its own batch after the original batch's acknowledgement, and the original result is withheld until it
settles. The found, not-found, and hint texts are the `format_retry_found`, `format_retry_not_found`, and
`format_retry_hint` keys of the voice package's `prompts.voice.yaml`.

The voice server logs these events for the geval fixes:

- `format_retry`: `tool`, `argument`, `before`, `after`, `mode`, and `outcome` (`hint`, `found`, `not_found`,
  `transient`, `timeout`, or `cancelled`).
- `field_provenance`: the `line` appended to a note.
- `tool_calls_out` now also carries `call_ids` and `arguments`, plus `format_retry: true` on the relay's own call.
  `report_adapter` credits a `<id>.fmt` output to the run of the original call.

The gateway records a backend decision trace when `hermes.backend_trace` is on. The default is
`${FDH_BACKEND_TRACE:-false}` in `gateway.yaml` and `${FDH_BACKEND_TRACE:-true}` in `gateway.eval.yaml`, so every
arm records it. Each worker appends a `session` record, then a `request` and `response` pair per model call, to
`<workers.log_dir>/<session_id>.trace.jsonl` through Hermes' `pre_api_request` and `post_api_request` hooks. It
is logging only. The gateway `/health` and session-open summary report `hermes.backend_trace`.

Two replay tools check an arm without a live run:

- `cli/relay_replay.py` (G1) feeds every recorded tool call of a voice event log through the control relay and
  the arm relay and exits 1 on an unexpected difference. It needs the session's tool schemas as
  `--session-update` files, such as
  [`tau2_session_update/telecom.json`](../../../tests/unit/prototypes/voice/fixtures/tau2_session_update/telecom.json).
  It skips logs written before `tool_calls_out` carried `call_ids` and `arguments`.
- `cli/backend_decision_replay.py` (G2) runs `extract` to list the write and transfer decisions of control
  traces for labelling (`U`, `P`, `T`, or `skip`), and `replay` to re-send them with an arm's
  `--gateway-config` (`--samples 3` by default; `--dry-run` checks the prompt substitution only).

The following commands show both tools:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.relay_replay \
  logs/fdh_voice_events.realtime_eval.jsonl \
  --profile src/prototypes/voice_delegation_hermes_agent/config/profiles/realtime_eval_arm_a1_format_retry.yaml \
  --session-update tests/unit/prototypes/voice/fixtures/tau2_session_update/telecom.json
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.backend_decision_replay \
  extract logs/fdh_workers --out logs/g2_decisions.jsonl
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.backend_decision_replay \
  replay logs/g2_decisions.jsonl --traces logs/fdh_workers \
  --gateway-config src/prototypes/voice_delegation_hermes_agent/config/gateway.eval_b1_write_consent_v2.yaml
```

### Deployed Fingerprint

The voice server and the gateway log a deployed fingerprint (`fdh_session_start.features`, `frontend_prompt`,
`backend_configured`, which includes `backend_domain` and `session_tools_sha256`). `session_tools_sha256` is the
first 16 hex characters of the SHA-256 of the session's sorted `session.update` tool schemas. With an `auto`
switch on, the voice server also logs a `session_rules` event after the gateway applies the tools, with the
same hash and the derived rules. Adding these default keys changed the `config_hash` of every profile
compared to earlier runs. `features.result_hints` and `invalid_message_keys.result_hint` /
`result_hint_escalate` tell I3 from I4; `features.schema_rules`, `schema_result_hint_tools`, and
`schema_spelling_hold_patterns` report the `auto` switches. `features.field_provenance` (true or false),
`features.format_retry` (false, `call`, or `hint`), and `schema_format_retry_fields` report the geval voice switches.
To check that every session of an arm ran the expected switches and prompts, run `fingerprint_check`:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.fingerprint_check \
  logs/fdh_voice_events.jsonl \
  --profile src/prototypes/voice_delegation_hermes_agent/config/profiles/tau3_eval_baseline.yaml \
  --gateway-config src/prototypes/voice_delegation_hermes_agent/config/gateway.baseline.yaml
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
  `test_fdh_tau3_fixes.py` and `test_fdh_identity_fixes.py` cover the failure-fix and identity-fix switches.
- `test_fdh_geval_fixes.py` covers the geval-fix switches and the decision trace (gate G0) with invented tool
  schemas; `test_fdh_hermes_adapter.py` also checks the trace with real Hermes.
- `test_fdh_schema_rules.py` checks the rules derived from the recorded tool schemas against
  `fixtures/schema_rules_snapshot.json`.
  After an intended baseline change, regenerate the golden files with
  `PYTHONPATH=src uv run python tests/unit/prototypes/delegation/_fdh_golden.py --write`.
