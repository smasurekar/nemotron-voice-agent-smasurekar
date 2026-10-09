# Runbook — Frontend Delegation to a Hermes Backend (web + OpenAI Realtime / τ³-voice)

**Code:** `src/prototypes/voice_delegation_hermes_agent/` · **Plan:** [`prototype-plan.md`](prototype-plan.md) ·
**Spec:** [`workflow.csv`](workflow.csv)

This runbook starts two deployments that share one backend gateway:

- the **OpenAI Realtime server for τ³-voice** (port 8775);
- the **browser page** (port 8776), with the 5 s simulated backend delay.

Every command runs from the repository root unless it says otherwise. The τ³ campaign runbook for this
prototype (reportable runs, archiving) is a separate, later document. §6 shows how to point τ³ at the
server; §6.1 lists the evaluation arms of [`tau3-failure-fixes-plan.md`](tau3-failure-fixes-plan.md) and how
to start one; §6.3 lists the arms of [`tau3-geval-failure-fixes-plan.md`](tau3-geval-failure-fixes-plan.md) and
its validation gates.

**Last verified 2026-09-29** on one RTX 5000 Ada host, following this runbook top to bottom:

- §2–5 all started cleanly (`nemo-speech`, the gateway, `fdh-voice`, `fdh-voice-web`).
- The §3.1 probe passed: new session, continue, status while WORKING, steer during a slow tool.
- The §4.1 text smoke and the §4.2 audio smoke passed. First audio 1.6–2.3 s after end of speech; answer
  5–7 s after end of speech.
- The browser server passed a scripted check with the 5 s delay.
- The §8 stub gates passed: `tau2 contract: OK` and `GATE A PASSED`.
- The §6 τ³ smoke (`mock`, `control`, 1 task) ran clean: 0 agent or user errors, `USER_STOP`, and all tool
  calls round-tripped through τ³. Its reward was 0.0: ASR lowercased the requested title "Important
  Meeting", and the mock domain has no tool that can rename a task.

**Re-verified 2026-09-30** after the Hermes checkout (v0.21.0) moved to Python `<3.14`: with the venv
rebuilt on Python 3.13 (§1.2), the gateway (§3), the §3.1 probe and `fdh-voice` (§4) started and passed.
On the RTX 5000 Ada host (`smasurekar`), the checkout later moved back to Python 3.14 (`<3.15`, lockfile
`>=3.14` only), and that host uses the 3.14 venv again (§1.2).

**Re-verified 2026-09-30** after the τ³ failure fixes ([`tau3-failure-fixes-plan.md`](tau3-failure-fixes-plan.md)): image
rebuilt (§1.4), the gateway (§3), `fdh-voice` (§4, `tau3_eval.yaml`) and `fdh-voice-web` (§5) restarted; every new
switch reported off on `/health`; the §4.1 text smoke passed; and `fingerprint_check` (§6.1) printed
`fingerprint: OK` on the smoke session. The arms themselves have not been run yet.

**Changed 2026-09-30:** the τ³ failure fixes are now on by default in the FDH YAML defaults, except M2
(experimental). `tau3_eval.yaml` with `gateway.yaml` runs every fix except M2; the control is now
`tau3_eval_baseline.yaml` with `gateway.baseline.yaml` (§6.1). Python code defaults stay off. The arms have
still not been evaluated, and the M1 delay (`FDH_PROACTIVE_AFTER_S`, default 15 s) still needs calibration
per host (§6.1). After the change, the gateway `/health` with `gateway.yaml` reported every backend variant
`true`, and the voice `/health` for `tau3_eval.yaml` and `browser_demo.yaml` reported every switch `true`
except `replay_unheard_answer` and `prompt_features.replay_intent`.

**Changed 2026-10-01:** the τ³ identity fixes ([`tau3-identity-fixes-plan.md`](tau3-identity-fixes-plan.md)) are
implemented and on by default:
- **I1**, a telecom phone-number note in the Hermes system prompt (`gateway.yaml` `prompt_features.domain_notes`);
- **I3** and **I4**, recovery notes appended to a failed airline or retail identity lookup's result (the new
  default voice profile `voice/tau3_fixes.yaml`).

The identity arms are compared with a new control, the fixes-on deployment without I1, I3 and I4
(`tau3_identity_control.yaml` with `gateway.identity_control.yaml`); each fix has its own arm (§6.1). The
all-off control (`tau3_eval_baseline.yaml` with `gateway.baseline.yaml`) also turns the identity fixes off.
Verified offline only: unit tests, and a replay of the 2026-09-30 tool outputs through the new hook (notes only
on the plan's 11 airline and 27 retail passing tasks with a miss, none on telecom or on a session whose first
lookup succeeded). The stack has not been restarted with this change and no arm has run.

**Changed 2026-10-05:** the τ³-specific rules are now domain-generic
([`tau3-voice-domain-specific-changes-genericization.md`](tau3-voice-domain-specific-changes-genericization.md)):
- **I1** is now a generic `<phone_numbers>` block in the Hermes system prompt (`prompt_features.phone_format`),
  rendered in every session. The telecom `<domain_notes>` note and the domain detection are gone
  (`domains: {}`, `domain_notes: false`), so `backend_domain` is always `""`.
- Prompt examples use invented values (`QUENTIN`, `XB7TP2`, `jordan_lee_82`), and the I3/I4 notes are renamed
  `lookup_not_found_hint` and `lookup_not_found_hint_words` and reworded generically.
- The new submission pair `realtime_eval.yaml` with `gateway.eval.yaml` derives tool-argument rules,
  result-hint targets, and spelling-hold patterns from each session's tool schemas (§6.2). The `tau3_*`
  profiles stay as historical and control configurations.
- The deployed fingerprint adds `session_tools_sha256`, the hash of the session's tool schemas.

Verified offline only (unit tests). The stack has not been restarted with this change, and its effect on
reward has not been measured; it requires validation before a reportable run.

**Changed 2026-10-07:** the fixes of [`tau3-geval-failure-fixes-plan.md`](tau3-geval-failure-fixes-plan.md)
are implemented behind switches that are **on by default** in the YAML defaults (`gateway.yaml`,
`gateway.eval.yaml` and `voice/realtime_eval.yaml`), so `realtime_eval.yaml` with `gateway.eval.yaml` runs every
fix. The control, the geval runs' behaviour, is now `realtime_eval_control.yaml` with `gateway.eval_control.yaml`
(§6.3). The Python code defaults stay off:
- **A1 / A1-lite** (`format_retry`): a not-found lookup by a phone field is tried once more with the same
  digits in the other format. The relay looks it up itself only on tools whose schema declares read-only,
  idempotent semantics; every other read tool gets a hint. No τ² tool declares it, so on τ³ A1 runs as the hint.
- **A2** (`format_correction` plus the hint variant `lookup_not_found_hint_format`), **A3**
  (`result_hints.field_provenance`), **B1** (`write_consent_v2`), **B2** (`policy_check`), **B3**
  (`request_scope`), **C1** (`transfer_check`) and **C2** (`capability_check`): one arm each (§6.3).
- **P0.1**: the τ³ `report.sh` marks a run with any backend 429 invalid. **P0.3**: `gateway.eval.yaml` writes a
  backend decision trace per session (`logs/fdh_workers/<session>.trace.jsonl`); `FDH_BACKEND_TRACE=false`
  turns it off.
- The voice event log's `tool_calls_out` now carries `call_ids` and `arguments` (the input of the G1 replay).

Verified offline: unit tests; the real-Hermes trace test; a G1 replay of the geval telecom traffic through the
A1 arm (a hint in exactly the plan's 20 sessions, all on the phone field, no other output changed). No arm has run
and no gate beyond G0/G1 has been applied, so no fix is reportable yet. Track I (retry back-off, fallback model,
progress cadence) is not implemented.

**Changed 2026-10-09:** the fixes of
[`fix-plan-f1-f2-f4-f8.md`](analysis/2026-10-09_fdh-eval-diagnosis-and-remedies/fix-plan-f1-f2-f4-f8.md) are
implemented and **on by default** in the YAML defaults (`realtime_eval.yaml`, `voice/realtime_eval.yaml`,
`gateway.yaml`, `gateway.eval.yaml`), so `realtime_eval.yaml` with `gateway.eval.yaml` runs every fix. The control is
the new pair `realtime_eval_124acf7.yaml` with `gateway.eval_124acf7.yaml`, which renders and decides exactly as agent
`124acf7` (checked against goldens captured from that commit, §8). Every older control (`realtime_eval_control.yaml`,
`gateway.eval_control.yaml`, `gateway.baseline.yaml`, `gateway.identity_control.yaml`) pins the new switches off, and
the Python code defaults stay off. One arm per fix over the new control (§6.4):
- **F1 + F2, no dead air** (`realtime_eval_liveness.yaml`): a silent reply to "are you still there?" or "could you
  repeat that?" is delegated or replaced by a replay of the unheard answer; a silent reply to a question is
  delegated; a ping while the backend works is a status request, which never interrupts the running model call.
- **F4, situational facts** (`gateway.eval_situational.yaml`): a backend prompt block, in every session, that asks
  about a situation the policy makes conditional and no tool can show.
- **F8, identity recovery** (`realtime_eval_identity.yaml`, and the sub-arm `gateway.eval_identity_name.yaml`): after
  a failed identification lookup, the note lists the lookups tried, what the user spelled and where each spelling was
  sent, the user's corrections, and the other identification lookups the tools offer.
- **Logging (always on):** `delegation_guard` names the real guard; `backend_answer` carries its text;
  `playback_outcome` carries `answer_id` and the heard text; `transcript_normalized` spans carry their `kind`;
  `backend_configured` and the trace `session` record both carry `tools_canonical_sha256`.

Verified: unit tests (`tests/` all green); the stack restarted with `realtime_eval.yaml` + `gateway.eval.yaml`; both
`/health` endpoints reported the new switches on; the §4.1 text smoke passed; and the voice log and the backend trace
of the smoke session carried the same `tools_canonical_sha256`. No arm has run, so no fix is reportable yet. The
`config_hash` of `realtime_eval.yaml` changed with the new settings: pair runs by profile name.

## 0. What runs where

```
 browser page ──wss──► fdh-voice-web  (container, :8776 → 7860, TLS, demo tools, 5 s delay) ─┐
 tau2 / Realtime ─ws─► fdh-voice      (container, :8775 → 7860, client tools)               ─┤ ws://<docker0>:8790/v1/backend
                                                                                             ▼
                               backend gateway (host process, Hermes-free, :8790 on docker0)
                                 └─ one Hermes worker process per Realtime session (Python $FDH_HERMES_PY)
                                      └─ AIAgent → Inference Hub nvidia/nvidia/nemotron-3-ultra (reasoning on)
 speech: nemo-speech (container, :50051, ASR + TTS) ◄── both voice containers
 frontend LLM: Inference Hub nvidia/nvidia/nemotron-3.5-lightning (reasoning off) ◄── both voice containers
```

| Component | Where | Port | Config |
|---|---|---|---|
| `nemo-speech` | container (Compose profile `frontend-backend-agent/single-gpu`) | 50051 | `docker/docker-compose.nemo-speech-cpp.yaml` |
| Backend gateway + Hermes workers | host process | 8790, bound to the `docker0` address | `config/gateway.yaml` (an arm may use `config/gateway.<variant>.yaml`, §6.1, or `config/gateway.eval_<fix>.yaml`, §6.3) |
| τ³ Realtime voice server `fdh-voice` | container (`nemotron-voice-agent:latest`, `src/` mounted read-only) | 8775 | `config/profiles/tau3_eval.yaml`, `config/profiles/realtime_eval.yaml` (§6.2), or an arm profile (§6.1, §6.3) |
| Browser voice server `fdh-voice-web` | container | 8776 (https) | `config/profiles/browser_demo.yaml` |

Why the gateway runs on the host: Hermes needs its own venv and Python (3.13 or 3.14, whichever the
Hermes checkout's lockfile supports; see §1.2), while the voice image ships Python 3.12 with this repository's dependencies (plan D1).
The voice containers reach the host through `host.docker.internal`, which the Compose `app-base` maps to
the `docker0` address.

Both profiles use:

- 800 ms end-of-turn silence (`honor_client_values: false`, so the client's 500 ms is ignored);
- transcript normalization (on by default), including spelled-run joining (τ³ failure fix M3.1);
- the τ³ failure fixes M1 (proactive status), M3.2 (spelling hold), G1 (filler de-duplication) and G2
  (answer cleanup), on by default; M2 (replay) stays off (§6.1, §9);
- the frontend tool `delegate(delegate, filler_text, request)`, with the filler spoken.

The τ³ profile also normalizes tool arguments, with the escalating local "invalid ID" wording (M3.3), and
never holds a complete six-character code (`spelling_hold.complete_patterns`; `realtime_eval.yaml` derives
the patterns from the tool schemas, §6.2). The browser profile cannot normalize tool arguments,
because its tools run in the server (`tools.source: config`).

## 1. Prerequisites (once)

**1.1 `.env`** must define `NVIDIA_API_KEY` (an Inference Hub key, `sk-...`). Keep any existing `.env`:

```bash
test -f .env || cp .env.example .env      # then set NVIDIA_API_KEY
grep -c '^NVIDIA_API_KEY=.\+' .env        # expect 1
```

**1.2 Hermes checkout and its Python environment.** A separate venv under `~/.cache/fdh`, so the
checkout's own `.venv` is not touched. The Python version depends on the Hermes checkout, so it differs
between machines. `FDH_HERMES_PY` selects it:

| Hermes checkout | `FDH_HERMES_PY` | Hosts |
|---|---|---|
| `requires-python` `<3.14` (v0.21.0: Rust-backed dependencies had no cp314 wheels) | `3.13` | hosts on that checkout, such as `ipp2-0543` (2026-10-07: `hermes-venv-313`, with `FDH_HERMES_REPO` set) |
| `requires-python` `<3.15`, with a lockfile that supports only `python_full_version >= '3.14'` | `3.14` | RTX 5000 Ada host `smasurekar` (2026-09-30) |

`uv sync` with the wrong version fails with `The current Python platform is not compatible with the
lockfile's supported environments`. Set the variable in every shell that runs §1.2 or §3:

```bash
export HERMES_REPO=$PWD/../hermes-agent-smasurekar     # adjust if the checkout lives elsewhere
grep -m1 requires-python "$HERMES_REPO/pyproject.toml"; grep -A1 -m1 resolution-markers "$HERMES_REPO/uv.lock"
export FDH_HERMES_PY=3.14                               # 3.13 if the checkout caps Python at <3.14
export FDH_HERMES_VENV=$HOME/.cache/fdh/hermes-venv-${FDH_HERMES_PY/./}   # hermes-venv-314 or hermes-venv-313
(cd "$HERMES_REPO" && UV_PROJECT_ENVIRONMENT=$FDH_HERMES_VENV uv sync --python $FDH_HERMES_PY)
(cd "$HERMES_REPO" && $FDH_HERMES_VENV/bin/python -c "import run_agent; print('hermes ok')")
```

A failed `uv sync` still creates an empty venv. Remove it (`rm -rf $FDH_HERMES_VENV`) before you switch
versions, so the gateway cannot pick it up.

If the last command fails with `ModuleNotFoundError: run_agent` from another directory, set
`FDH_HERMES_REPO=$HERMES_REPO` when starting the gateway (§3). The pool then adds the checkout to the
workers' `PYTHONPATH`.

**1.3 Speech models** (idempotent; it only downloads what is missing or fails its checksum):

```bash
bash scripts/download-nemo-speech-models.sh
ls models/nemo-speech   # magpie-tts nano-codec nemotron-speech-streaming-en-0.6b.q8_0.gguf tn_configs ...
```

**1.4 Voice image.** `nemotron-voice-agent:latest` must exist; build it if it does not. `src/` is mounted
read-only into the container, so code changes need no rebuild:

```bash
docker image inspect nemotron-voice-agent:latest >/dev/null 2>&1 || docker compose build frontend-backend-agent-single-gpu
```

**1.5 Common shell variables** for the rest of this runbook:

```bash
export DOCKER_HOST_IP=$(ip -4 -o addr show docker0 | awk '{print $4}' | cut -d/ -f1)   # e.g. 172.17.0.1
export HOST_IP=$(hostname -I | awk '{print $1}')                                     # for the browser URL
mkdir -p logs
echo "docker0=$DOCKER_HOST_IP host=$HOST_IP"
```

## 2. Start speech (`nemo-speech`)

```bash
docker compose --profile frontend-backend-agent/single-gpu up -d nemo-speech
docker logs -f nemotron-voice-agent-nemo-speech-1 2>&1 | grep -m1 'listening on 0.0.0.0:50051'   # ~20 s
```

## 3. Start the backend gateway (host)

The gateway binds only to the `docker0` address: it has no authentication, so it must not be reachable
from the LAN. The backend model is pinned on the command line. `FDH_GATEWAY_CONFIG` selects the gateway
file: `gateway.yaml` (every backend prompt variant on, except `domain_notes` and the
[`tau3-geval-failure-fixes-plan.md`](tau3-geval-failure-fixes-plan.md) variants) for normal runs, `gateway.eval.yaml`
(the same variants, pinned, plus the backend decision trace) with `realtime_eval.yaml` (§6.2),
`gateway.baseline.yaml` (every variant off) for the control, or the gateway config an arm needs (§6.1, §6.3).
`FDH_GATEWAY_LOG` names the gateway event log: the τ³ campaign tooling expects
`logs/fdh_gateway_events.realtime_eval.jsonl` for `gateway.eval.yaml` and its arms.

```bash
export FDH_GATEWAY_CONFIG=${FDH_GATEWAY_CONFIG:-gateway.yaml}
nohup env PYTHONPATH=src \
  FDH_GATEWAY_HOST=$DOCKER_HOST_IP FDH_GATEWAY_PORT=8790 FDH_MAX_SESSIONS=8 \
  FDH_HERMES_PYTHON=${FDH_HERMES_VENV:?set it in §1.2}/bin/python \
  BACKEND_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra BACKEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  FDH_GATEWAY_LOG=${FDH_GATEWAY_LOG:-logs/fdh_gateway_events.jsonl} FDH_WORKER_LOG_DIR=logs/fdh_workers \
  uv run python -m prototypes.voice_delegation_hermes_agent.sidecar.gateway_server \
    --config src/prototypes/voice_delegation_hermes_agent/config/$FDH_GATEWAY_CONFIG \
  > logs/fdh_gateway.out 2>&1 &
echo $! > logs/fdh_gateway.pid
sleep 3; curl -s http://$DOCKER_HOST_IP:8790/health; echo
```

Expected health:

- `"ok": true` and `"max_sessions": 8`;
- `"agent_kind": "hermes"`;
- `"hermes": {"model": "nvidia/nvidia/nemotron-3-ultra", "base_url": "https://inference-api.nvidia.com/v1",
  "reasoning": true}`;
- `"backend_features"` with `spelling_v2`, `spoken_output`, `write_consent` and `phone_format` `true` and
  `domain_notes` `false` with `gateway.yaml` and `gateway.eval.yaml` (all `false` with `gateway.baseline.yaml`,
  one `true` with a `gateway.<variant>.yaml`, `phone_format` and `domain_notes` `false` with
  `gateway.identity_control.yaml`), and `"backend_catalog_sha256"`, the hash of `prompts.backend.yaml`.
  `format_correction`, `write_consent_v2`, `policy_check`, `request_scope`, `transfer_check` and
  `capability_check` are `true` with `gateway.yaml` and `gateway.eval.yaml`, `false` with
  `gateway.eval_control.yaml`, `gateway.baseline.yaml` and `gateway.identity_control.yaml`, and one `true` with a
  `gateway.eval_<fix>.yaml` arm (§6.3);
- `"hermes"` also reports `"backend_trace"`: `true` with `gateway.eval.yaml`, `gateway.eval_control.yaml` and the arms (unless
  `FDH_BACKEND_TRACE=false`), `false` otherwise;
- `"domains": []`: no domain detection (every gateway config extends `gateway.yaml`, which sets `domains: {}`).

The gateway loads its code and prompts at startup: restart it (§10, then this section) after a code or
prompt change, or to switch `FDH_GATEWAY_CONFIG`.

**3.1 Optional backend-only check** (a real Hermes worker, text only, about 30 s). It drives the gateway
directly, the way the voice server does. It covers a new task with a tool call, a continue, a status
request while WORKING, and a steer during a slow tool:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.backend_probe \
  --url ws://$DOCKER_HOST_IP:8790/v1/backend
```

## 4. Start the τ³ Realtime voice server (`fdh-voice`, port 8775)

`FDH_PROFILE` selects the profile: `tau3_eval.yaml` for normal runs (every τ³ failure fix except M2),
`realtime_eval.yaml` for the domain-agnostic submission profile (§6.2), or the control or an arm profile
(§6.1, §6.3). `FDH_PROACTIVE_AFTER_S` sets the M1 delay; calibrate it per host (§6.1). `FDH_EVENT_LOG` names
the event log: the τ³ campaign tooling expects `logs/fdh_voice_events.realtime_eval.jsonl` for
`realtime_eval.yaml` and its arms.

```bash
export FDH_PROFILE=${FDH_PROFILE:-tau3_eval.yaml}
docker compose --profile frontend-backend-agent/single-gpu run --rm -d --no-deps --name fdh-voice -p 8775:7860 \
  -v "$PWD/logs:/app/logs" -e PYTHONPATH=/app/src -e PIPELINE_TLS=false \
  -e FDH_BACKEND_URL=ws://host.docker.internal:8790/v1/backend -e FDH_MAX_SESSIONS=4 \
  -e FRONTEND_LLM_MODEL=nvidia/nvidia/nemotron-3.5-lightning -e FRONTEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e FDH_EVENT_LOG=${FDH_EVENT_LOG:-logs/fdh_voice_events.jsonl} -e FDH_PROACTIVE_AFTER_S=${FDH_PROACTIVE_AFTER_S:-15} \
  frontend-backend-agent-single-gpu \
  uv run python -m prototypes.voice_delegation_hermes_agent.server \
    --config src/prototypes/voice_delegation_hermes_agent/config/profiles/$FDH_PROFILE --port 7860
sleep 15; curl -s localhost:8775/health; echo
docker logs fdh-voice 2>&1 | grep -E 'backend gateway|ready|VAD|features'
```

Expected:

- `/health` shows `"status": "ok"`, `"prototype": "frontend-delegation-hermes"`,
  `"backend": {"link": "websocket", ...}` and `"features"`: with `tau3_eval.yaml`, `proactive_status`,
  `clean_answers`, `spelling_hold`, `filler_dedupe`, `spelled_runs` and `result_hints` `true`, `replay_unheard_answer` and
  `prompt_features.replay_intent` `false`; with `realtime_eval.yaml`, also `schema_rules`,
  `schema_result_hint_tools`, `schema_spelling_hold_patterns`, `field_provenance` and
  `schema_format_retry_fields` `true` and `format_retry` `"call"` (all three `false` with
  `realtime_eval_control.yaml`); with `tau3_eval_baseline.yaml`, every switch `false`; with an arm
  profile, only the arm's own switches `true` (`format_retry` is `"call"` or `"hint"` in the A1 arms).
- The logs show `frontend warm-up done in …s`, `backend gateway http://host.docker.internal:8790/health:
  {... 'ok': True ... 'hermes': {'model': 'nvidia/nvidia/nemotron-3-ultra', ... 'reasoning': True}}` and
  `VAD silence=800ms`.
- The warm-up opens the HTTPS connection to the frontend endpoint at startup. Without it, the first user
  turn of a fresh server timed out (more than 4 s) and lost its filler.

Keep `FDH_MAX_SESSIONS` for all voice servers together at or below the gateway's (8): a voice server
refuses to start when its own limit exceeds the gateway's.

**4.1 Text smoke test through the whole stack.** The client registers the demo tools and executes them
itself, as τ³ does. It exercises the frontend decision, the spoken filler, the delegation, the Hermes tool
call over the Realtime wire and the spoken answer:

```bash
( sleep 2; echo "can you check the status of order 5512"; sleep 25; echo "okay thanks"; sleep 6; \
  echo "what about order 5513"; sleep 3; echo "what's the update so far"; sleep 25 ) | \
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.voice_chat \
  --url ws://localhost:8775/v1/realtime --model pine-fdh-smoke --io text --ui plain \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS
```

This client uses manual-response mode: every line is followed by `response.create`, and a turn with
nothing to say still gets an empty response, as Realtime requires.

Expected, in order:

1. a filler ("Sure, let me check that...");
2. a `get_order` function call answered by the client;
3. the order status;
4. nothing for "okay thanks";
5. a filler for order 5513;
6. a spoken status update for "what's the update so far", if the backend is still working. If it
   already finished, the update is answered from history instead.

The event log shows each step: `grep -E '"kind": "(delegation_decision|backend_action|tool_output_in|backend_answer|status_spoken)"' logs/fdh_voice_events.jsonl`.

**4.2 Audio smoke test (real ASR, VAD and TTS).** This synthesizes three user turns with the local TTS and
plays them in τ³'s wire format (μ-law 8 kHz). Expect the same flow as §4.1, with `you` lines transcribed by
the ASR:

```bash
mkdir -p /tmp/fdh-wav && uv run python - <<'PY'
import wave, riva.client
tts = riva.client.SpeechSynthesisService(riva.client.Auth(uri="localhost:50051", use_ssl=False))
for i, text in enumerate(["Can you give me the status of order five five one two?", "Okay, thanks.",
                          "What about order five five one three?"], 1):
    audio = tts.synthesize(text, voice_name="", language_code="en-US", sample_rate_hz=22050).audio
    with wave.open(f"/tmp/fdh-wav/u{i}.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(audio)
PY
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.voice_chat   --url ws://localhost:8775/v1/realtime --model pine-fdh-audio --io wav --format pcmu   --in /tmp/fdh-wav/u1.wav --in /tmp/fdh-wav/u2.wav --in /tmp/fdh-wav/u3.wav --out /tmp/fdh-wav/agent.wav   --ui plain --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS
```

## 5. Start the browser voice server (`fdh-voice-web`, port 8776, 5 s delay)

```bash
docker compose --profile frontend-backend-agent/single-gpu run --rm -d --no-deps --name fdh-voice-web -p 8776:7860 \
  -v "$PWD/logs:/app/logs" -e PYTHONPATH=/app/src -e PIPELINE_TLS=true \
  -e FDH_BACKEND_URL=ws://host.docker.internal:8790/v1/backend -e FDH_MAX_SESSIONS=4 \
  -e FDH_BACKEND_DELAY_S=5 \
  -e FDH_EVENT_LOG=logs/fdh_voice_web_events.jsonl \
  frontend-backend-agent-single-gpu \
  uv run python -m prototypes.voice_delegation_hermes_agent.server \
    --config src/prototypes/voice_delegation_hermes_agent/config/profiles/browser_demo.yaml --port 7860 --tls
sleep 15; curl -sk https://localhost:8776/health; echo
```

Open `https://$HOST_IP:8776/`. Use the IP, not a `*.nvidia.com` hostname, which HSTS forces through a
proxy. Accept the self-signed certificate and allow the microphone. From a laptop, you can instead tunnel
with `ssh -N -L 8776:localhost:8776 <user>@<host>` and open `https://localhost:8776/`.

The page greets you. Try the workflow table (the demo tools know order `5512`, shipped, and `5513`, processing; `cancel_order` works on `5513`; any other number is "not found"). Each
delegation that starts a backend run waits 5 s first, so the backend stays WORKING long enough to try
things mid-task:

| You say | Expected |
|---|---|
| "Can you give me the status of order 5512?" | A filler now; the answer after about 5 s plus the model time (new session) |
| While it works: "What's the update so far?" | "Let me check…", then a spoken progress sentence; the running task continues |
| While it works: "And order 5513 as well" | A filler; the running task is steered and answers both |
| "Okay sure" | No reply (acknowledgement) |
| After an answer: "Please cancel order 5513" | A filler; a new run continues the same Hermes session |
| Talk over an answer | The answer stops; the backend learns what you heard (a delivery note) |

Change the delay with `-e FDH_BACKEND_DELAY_S=<seconds>` (0 disables it). The τ³ profile never delays.

A check without a browser: `curl -sk https://localhost:8776/` returns the page. `curl -sk
https://localhost:8776/health` shows `"prototype": "frontend-delegation-hermes"` and the same `"features"`
as `tau3_eval.yaml` (§4), and `docker logs
fdh-voice-web` shows `tools=local delay=5s VAD silence=800ms`.

## 6. Point τ³-voice at the Realtime server

Run this in the `tau2-bench-smasurekar` checkout, after its own setup (`uv sync --extra voice --extra dev`,
Inference Hub variables; see its `misc/prototypes/voice-frontend-backend-agent-tau3-runbook.md` §2). The
existing `tau3_run` helper only knows the older arms, so call the underlying command directly. Model names
must start with `pine-`, and `--max-concurrency 1` is required for reportable runs:

```bash
IHUB=https://inference-api.nvidia.com/v1
RUN=fdh_voice_dlg_mock_control_smoke
PINE_REALTIME_BASE_URL=ws://localhost:8775/v1/realtime PINE_API_KEY=unused \
uv run python misc/prototypes/fba_voice_eval/tau2_ihub.py run --domain mock --audio-native \
  --audio-native-provider openai --audio-native-model pine-fdh-voice-dlg-mock-control-smoke \
  --speech-complexity control --user-llm openai/azure/openai/gpt-5.2 \
  --user-llm-args "{\"temperature\": 0.0, \"api_base\": \"$IHUB\"}" --review-model openai/azure/openai/gpt-5.2 \
  --task-split-name base --num-trials 1 --max-concurrency 1 --num-tasks 1 --save-to "$RUN" --verbose-logs
```

**Handshake check without a full run** (Gate A: tau2's real provider, one tool call). Run it in the tau2
checkout:

```bash
PINE_REALTIME_BASE_URL=ws://localhost:8775/v1/realtime PINE_API_KEY=unused \
  uv run python ../nemotron-voice-agent-smasurekar/src/prototypes/voice_frontend_backend_agent/cli/tau2_gates/gate_a_handshake.py
# last line: GATE A PASSED  (against a real server it needs a tone the ASR transcribes; see §8 for the stub gate)
```

### 6.1 Evaluation arms (τ³ failure fixes and identity fixes)

[`tau3-failure-fixes-plan.md`](tau3-failure-fixes-plan.md) adds generic fixes for the airline failures, and
[`tau3-identity-fixes-plan.md`](tau3-identity-fixes-plan.md) adds I1, I3 and I4 for identification in all three
domains, each behind its own switch. Since 2026-09-30 the YAML defaults turn every fix on except M2, so the default
deployment is `tau3_eval.yaml` with `gateway.yaml`. An arm is a voice profile (§4, `FDH_PROFILE`) plus a
gateway config (§3, `FDH_GATEWAY_CONFIG`). Each arm turns on one fix over the control, and is compared with
a fresh control on the same host (plan §8). The following table lists the pairs:

| Arm | `FDH_PROFILE` | `FDH_GATEWAY_CONFIG` | Turns on |
|---|---|---|---|
| Default (every fix except M2) | `tau3_eval.yaml` | `gateway.yaml` | M1, M3, G1, G2, G4, I1, I3 and I4 together; not an evaluated arm |
| Control | `tau3_eval_baseline.yaml` | `gateway.baseline.yaml` | nothing: prompts byte-identical to agent commit `3a7e04a` |
| M1 proactive status | `tau3_arm_m1_status.yaml` | `gateway.baseline.yaml` | one short status line per run after `FDH_PROACTIVE_AFTER_S` (default 15) s of silence while WORKING |
| M3 spelling | `tau3_arm_m3_spelling.yaml` | `gateway.spelling_v2.yaml` | spelled-run joining, spelling hold (six-character code pattern), escalating local "invalid ID" wording, backend variant `spelling_v2` |
| Airline canary (M1 + M3) | `tau3_airline_canary.yaml` | `gateway.spelling_v2.yaml` | M1 and M3 together, after each passed its own arm |
| M2 replay (experimental) | `tau3_arm_m2_replay.yaml` | `gateway.baseline.yaml` | replays an unheard answer on a "status" request in IDLE (RC2), frontend variant `replay_intent` |
| G1 filler de-duplication | `tau3_arm_g1_filler_dedupe.yaml` | `gateway.baseline.yaml` | a repeated filler is dropped (WORKING) or replaced |
| G2 short answers | `tau3_arm_g2_short_answers.yaml` | `gateway.spoken_output.yaml` | markdown cleanup before TTS, backend variant `spoken_output` |
| G4 write consent | `tau3_eval_baseline.yaml` | `gateway.write_consent.yaml` | backend variant `write_consent` |
| Identity control | `tau3_identity_control.yaml` | `gateway.identity_control.yaml` | the default without I1, I3 and I4 (M1, M3, G1, G2 and G4 on): the deployment of the runs the identity plan analysed |
| I1 phone-number rule | `tau3_identity_control.yaml` | `gateway.yaml` | backend variant `phone_format`: a `<phone_numbers>` block in every session (lookups as XXX-XXX-XXXX unless the tool description shows another format, one retry in the other common form, else a grouped read-back; numbers passed unchanged to tools that store or change them). Before 2026-10-05, this arm rendered a telecom-only `<domain_notes>` block instead |
| I3 recovery note | `tau3_arm_i3_recovery_hint.yaml` | `gateway.identity_control.yaml` | `result_hints`: the I3 note appended to every failed lookup result of `find_user_id_by_name_zip`, `find_user_id_by_email` or `get_user_details` (no escalation) |
| I4 word-assisted spelling (on top of I3) | `tau3_arm_i4_word_spelling.yaml` | `gateway.identity_control.yaml` | I3 on the first miss, the I4 note ("S as in Sam") on later misses; compare it with the I3 arm |

`tau3_eval.yaml` and `tau3_eval_baseline.yaml` no longer behave the same: the baseline pins every new
switch off, and it must run with `gateway.baseline.yaml`, not `gateway.yaml`. Likewise
`tau3_identity_control.yaml` runs with `gateway.identity_control.yaml`. Their config hashes differ.
To switch arms, stop both servers (§10) and start them again with the two variables, for example:

```bash
export FDH_PROFILE=tau3_arm_m3_spelling.yaml FDH_GATEWAY_CONFIG=gateway.spelling_v2.yaml
# then §3 (gateway) and §4 (fdh-voice)
```

**Before M1: calibrate `FDH_PROACTIVE_AFTER_S` on the target host** (plan §2): from a control run, take the
wall-clock and τ² simulated gaps of WORKING spans with no audio; the line must play at about 20 s simulated
or earlier, and not before the 75th percentile of normal filler-to-answer gaps.

**Before M3: re-measure the spelling hold** on the run's own events (plan §9; the canary is blocked if the
held/continued counts move by more than 10%). On the 2026-09-29 airline events it gives held 103
(51 continued, 52 not), not held: complete user ID 10, complete code 7:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.spelling_hold_replay \
  <events.jsonl> --config src/prototypes/voice_delegation_hermes_agent/config/profiles/tau3_arm_m3_spelling.yaml
```

**Frontend decisions for M2:** `delegation_replay` runs 3 more cases (`replay_unheard|IDLE`) when the profile
turns `replay_intent` on:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.delegation_replay \
  --config src/prototypes/voice_delegation_hermes_agent/config/profiles/tau3_arm_m2_replay.yaml --only replay
```

**I1, I3 and I4: what to check in a run.** Run I1 on telecom and I3/I4 on airline and retail, each against a fresh
identity control. I1 now renders in every domain, so also check the other domains for regressions. The arms add
one identity fix to the identity control, not to the all-off control. In `backend_configured`, `backend_domain`
is `""` and `session_tools_sha256` must be the same for every session of one domain. For I3/I4, `result_hint`
events (`message_key`, `n`) show each appended note; a session whose first lookup succeeded has none. Gate each arm per task against a fresh control (plan §7.3): no repeatable loss, and
inspect every changed sentinel, first the 6 that passed without ever being identified (airline 0, 26; retail
25, 60, 65, 80).

**Before scoring an arm: check its deployed fingerprint** (plan §1, rule 5). Every session must have the
arm's switches, the gateway's variants and one set of prompt hashes; exit 1 means the arm is re-run, not
scored:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.fingerprint_check \
  logs/fdh_voice_events.jsonl \
  --profile src/prototypes/voice_delegation_hermes_agent/config/profiles/$FDH_PROFILE \
  --gateway-config src/prototypes/voice_delegation_hermes_agent/config/$FDH_GATEWAY_CONFIG
# last line: fingerprint: OK
```

Use one event log per arm and domain (`-e FDH_EVENT_LOG=...` in §4): the prompt hashes include the τ² policy
and tools. Events written before this change have no fingerprint, and the check fails on them. Since the
identity fixes, the check also compares `backend_domain`: logs written before 2026-10-01 report it missing.
Since 2026-10-05, it also requires `session_tools_sha256`, so logs written before that date fail the check.

### 6.2 Domain-Agnostic Evaluation Profile

`realtime_eval.yaml` with `gateway.eval.yaml` is the single frozen profile for every domain. No setting names a
tool or a domain. The voice profile `voice/realtime_eval.yaml` derives three things per session from the tool
schemas in the client's `session.update`, after the gateway applies the tools:

- **Tool-argument rules** (`normalization.tool_arguments.rules: auto`): for read tools only (name prefix
  `get_`, `find_`, `search_`, `list_`, `lookup_`, `check_` or `calculate`, or a description that starts with
  Get, Find, Search, List or Look up), each `id` or `*_id` string argument whose description quotes an example
  gets a rule from that example. Write tools are never rewritten. A well-formed value stays unchanged, and only a
  clearly incomplete value is answered locally.
- **Result-hint targets** (`result_hints.tools: auto`): every read tool. A note is appended only when the
  not-found text names a user, customer, member, client or profile, and a local "invalid" answer counts as a
  miss only for a person argument such as `user_id` or `customer_id`.
- **Spelling-hold patterns** (`delegation.spelling_hold.complete_patterns: auto`): the strict shapes of the
  `id`, `*_id` and `*_ids` examples, matched case-insensitively. Examples with a name-like part (such as
  `ab_cd_12`) give no pattern: their length varies, so no pattern ever rules a spelled user ID complete
  after the example's digit count. Such a turn waits at most `hold_ms` when the hold's evidence rules apply.

The retry guard covers every tool (`scope: all`) with the pattern `(?i)\b(not found|no such|does not exist)\b`.
`gateway.eval.yaml` pins `spelling_v2`, `spoken_output`, `write_consent` and `phone_format` on, and
`domain_notes` off, so a later edit to `gateway.yaml` cannot change an evaluation. Before the first
`session.update`, a session has no schema-derived rules.

To start it, set the two variables and follow §3 and §4:

```bash
export FDH_PROFILE=realtime_eval.yaml FDH_GATEWAY_CONFIG=gateway.eval.yaml
# then §3 (gateway) and §4 (fdh-voice)
```

In the event log, each session has a `session_rules` event with `session_tools_sha256`, the `schema_auto`
switches and the derived rules. Review them per domain, and run `fingerprint_check` (§6.1) with
`--profile .../profiles/realtime_eval.yaml --gateway-config .../gateway.eval.yaml`. This profile has not been
evaluated: compare it with `tau3_eval.yaml` on non-scored data before reporting any reward.

**Reporting.** `fba_voice_metrics.py` expects one backend operation per turn. Convert the event log first;
the adapter credits each run to the turn that started it and exits 1 on an orphaned tool call:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.report_adapter \
  logs/fdh_voice_events.jsonl --out logs/fdh_voice_events.legacy.jsonl
```

### 6.3 Geval failure-fix arms and their gates

[`tau3-geval-failure-fixes-plan.md`](tau3-geval-failure-fixes-plan.md) adds fixes for the three top failure
classes of the geval runs. Every fix is on by default (`realtime_eval.yaml` with `gateway.eval.yaml`) and off in the
control, `realtime_eval_control.yaml` with `gateway.eval_control.yaml`. Each arm turns on one fix over that
control. No arm has been evaluated, and the plan ships a fix only after its gates pass (plan §5).

| Arm | `FDH_PROFILE` | `FDH_GATEWAY_CONFIG` | Turns on |
|---|---|---|---|
| Control | `realtime_eval_control.yaml` | `gateway.eval_control.yaml` | nothing new (the geval runs' deployment); the backend decision trace (P0.3) is on |
| A1 format retry | `realtime_eval_arm_a1_format_retry.yaml` | `gateway.eval_control.yaml` | `format_retry` `mode: call`: after a not-found lookup by a phone field (by argument name or description, never by value shape) with ten digits, the relay looks up the same digits in the other format once on a tool that declares `annotations: {readOnlyHint: true, idempotentHint: true}`; any other read tool gets the A1-lite hint. On τ³ every tool gets the hint |
| A1-lite format hint | `realtime_eval_arm_a1_lite_format_hint.yaml` | `gateway.eval_control.yaml` | `format_retry` `mode: hint`: `[Same digits in the other common format: …]` appended to the not-found result; the relay never calls a tool itself |
| A2 format correction | `realtime_eval_arm_a2_format_correction.yaml` | `gateway.eval_a2_format_correction.yaml` | backend variant `format_correction` (a separator or case change is not a "variant") and the first recovery note `lookup_not_found_hint_format` |
| A3 field provenance | `realtime_eval_arm_a3_field_provenance.yaml` | `gateway.eval_control.yaml` | `result_hints.field_provenance`: from the second miss of a person lookup on, one line saying which values the user spelled, which were only heard, and which are unknown |
| B1 consent to the exact change | `realtime_eval_control.yaml` | `gateway.eval_b1_write_consent_v2.yaml` | backend variant `write_consent_v2` (inside `<consent>`) |
| B2 policy check | `realtime_eval_control.yaml` | `gateway.eval_b2_policy_check.yaml` | backend variant `policy_check` (`<before_change>`) |
| B3 request scope | `realtime_eval_control.yaml` | `gateway.eval_b3_request_scope.yaml` | backend variant `request_scope` (`<before_change>`) |
| C1 transfer check | `realtime_eval_control.yaml` | `gateway.eval_c1_transfer_check.yaml` | backend variant `transfer_check` (`<before_transfer>`) |
| C2 capability check | `realtime_eval_control.yaml` | `gateway.eval_c2_capability_check.yaml` | backend variant `capability_check` (`<before_transfer>`) |
| All fixes (the default) | `realtime_eval.yaml` | `gateway.eval.yaml` | A1 (`mode: call`), A2, A3, B1, B2, B3, C1 and C2 together |

Start an arm like any other (§3 and §4), with the event-log names the τ³ campaign tooling expects:

```bash
export FDH_PROFILE=realtime_eval_arm_a1_format_retry.yaml FDH_GATEWAY_CONFIG=gateway.eval_control.yaml
export FDH_EVENT_LOG=logs/fdh_voice_events.realtime_eval.jsonl FDH_GATEWAY_LOG=logs/fdh_gateway_events.realtime_eval.jsonl
# then §3 (gateway) and §4 (fdh-voice)
```

**Gates (plan §5).** Run them in this order; a fix that fails one is not shipped:

- **P0.1, a clean run:** the τ³ `report.sh` runs `fdh_logs.py ratelimit` and marks a run `INVALID` when any of its
  sessions hit a backend 429 (`Error code: 429` or `Retrying API call` in `logs/fdh_workers/<session>-<n>.log`).
  Re-run an invalid run; prefer quiet hours.
- **G0:** `uv run pytest tests/unit/prototypes/delegation/test_fdh_geval_fixes.py -q`.
- **G1, the code paths (A1, A1-lite):** replay a clean control's recorded tool calls through the arm's relay.
  It needs a log written since 2026-10-07 (`tool_calls_out` with `call_ids` and `arguments`; older sessions are
  skipped) and the session's tool schemas. Every found-after-retry result is printed for the identity check:

  ```bash
  PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.relay_replay \
    logs/fdh_voice_events.realtime_eval.jsonl \
    --profile src/prototypes/voice_delegation_hermes_agent/config/profiles/realtime_eval_arm_a1_format_retry.yaml \
    --session-update tests/unit/prototypes/voice/fixtures/tau2_session_update/{airline,retail,telecom}.json \
    --out logs/g1_a1.json            # last line: G1: OK
  ```

  A3 needs the user turns, so G1 does not replay it: review the `field_provenance` events of an A3 run against a
  hand-labelled sample instead.
- **G2, the backend decisions (A1-lite, A2, A3, B1–C2):** list the write and transfer decisions of a clean
  control's traces, label them **before** any arm is replayed (`U` unchanged, `P` expected policy correction,
  `T` targeted, `skip`; plan §5), then replay them with the arm's backend prompt (3 samples at temperature 0).
  `--dry-run` only checks that the arm's prompt replaces the control's in every request:

  ```bash
  PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.backend_decision_replay \
    extract logs/fdh_workers --out logs/g2_decisions.jsonl          # then edit the labels
  PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.backend_decision_replay \
    replay logs/g2_decisions.jsonl --traces logs/fdh_workers \
    --gateway-config src/prototypes/voice_delegation_hermes_agent/config/gateway.eval_b1_write_consent_v2.yaml \
    --out logs/g2_b1.json            # last line: G2: OK
  ```

  Keep each control run's traces apart (move `logs/fdh_workers/*.trace.jsonl` aside per run): a trace is
  named by session, and the replay reads every trace in the directory.
- **G3, G4:** the mock domain and invented conversations (G3), then the paired A/B (G4: an A/A control run
  first, five trials per domain, the arm interleaved with the control). Check every arm's fingerprint as in §6.1
  with the arm's `--profile` and `--gateway-config`.

### 6.4 Arms of fix-plan-f1-f2-f4-f8.md (F1 + F2, F4, F8)

The default deployment, `realtime_eval.yaml` with `gateway.eval.yaml`, runs every fix (the combined arm of plan
stage 3). The control is the deployed `124acf7` pair, `realtime_eval_124acf7.yaml` with `gateway.eval_124acf7.yaml`;
every arm adds one fix to it. Run them in the plan's stages (plan §5.3: targeted cells first, a fresh control in the same window, the same user
simulator and judge in arm and control) and accept them only by the plan's §5.4 checks.

| Arm | `FDH_PROFILE` | `FDH_GATEWAY_CONFIG` | Turns on |
|---|---|---|---|
| All fixes (the default) | `realtime_eval.yaml` | `gateway.eval.yaml` | F1 + F2, F4, F8 and the F8 name line together (plan stage 3) |
| Control | `realtime_eval_124acf7.yaml` | `gateway.eval_124acf7.yaml` | nothing new (agent `124acf7`) |
| liveness (F1 + F2) | `realtime_eval_liveness.yaml` | `gateway.eval_124acf7.yaml` | `delegation.guards.{liveness_reply, question_never_silent, ping_as_status}`; `replay_unheard_answer` with `triggers: [backchannel, ping, repeat]`, `ttl_s: 60`, `whole_if_heard_below: 0.2` |
| situational (F4) | `realtime_eval_124acf7.yaml` | `gateway.eval_situational.yaml` | backend variant `situational_facts` (`<situational_facts>` in every session) |
| identity (F8) | `realtime_eval_identity.yaml` | `gateway.eval_124acf7.yaml` | `result_hints.ledger` (`identification_tools: auto`), `max_hints: 6`, A3's `field_provenance` off |
| identity_name (F8 sub-arm) | `realtime_eval_124acf7.yaml` | `gateway.eval_identity_name.yaml` | backend variant `identity_from_identifier` (every session) |

If an arm does not pass, turn its switch off in the default files (`profiles/realtime_eval.yaml`,
`voice/realtime_eval.yaml`, `gateway.yaml`, `gateway.eval.yaml`) and re-run the default.

```bash
export FDH_PROFILE=realtime_eval_liveness.yaml FDH_GATEWAY_CONFIG=gateway.eval_124acf7.yaml
export FDH_EVENT_LOG=logs/fdh_voice_events.realtime_eval.jsonl FDH_GATEWAY_LOG=logs/fdh_gateway_events.realtime_eval.jsonl
# then §3 (gateway) and §4 (fdh-voice); check the fingerprint as in §6.1 with the arm's --profile and --gateway-config
```

What to check in an arm's events:
- **liveness:** `delegation_guard` with `guard` `liveness_reply`, `question_never_silent` or `ping_as_status` (and
  `turn_class`, `backend_state`); `answer_replayed` / `answer_replay_skipped` with `trigger`. A `lexicon_missing`
  event means the session's ASR language has no phrase list (only `en` ships), so every F1/F2 rule is inactive in it.
- **identity:** `session_rules.identification_tools` (review it per domain: a bill or usage lookup must not be
  there); `identity_ledger` per identification output (`output_class`: `not_found`, `error`, `ambiguous`, `found`,
  `unknown`; `episode`; `note_chars`).
- **situational / identity_name:** the gateway `/health` and `backend_configured.backend_features` show the variant.

The G2 replay (§6.3) now compares a `U` decision's **complete, ordered** tool-call list (`calls`, written by
`extract` since 2026-10-09); decisions extracted earlier keep the old rule. It renders the arm's prompt with the
same function as the gateway (`prompt_features.render_backend_system`).

## 7. Logs and what to look at

| File | Content |
|---|---|
| `logs/fdh_voice_events.jsonl`, `logs/fdh_voice_web_events.jsonl` | Voice server events per session: `fdh_session_start` (config hash, `features`, `invalid_message_keys`), `frontend_prompt` (prompt hash), `backend_configured` (backend fingerprint), `delegation_decision`, `backend_action`, `tool_calls_out`, `tool_output_in`, `call_answered_locally` (`reason`, `message_key`), `backend_answer`, `status_spoken`, `playback_outcome`, `delivery_note`, `history_sync`, `turn_latency`, `filler_timing`, `barge_in`. Only with the fix's switch on (the defaults or an arm): `status_proactive` (M1), `spelling_hold` (M3), `answer_replayed` / `answer_replay_skipped` (M2), `filler_deduped` (G1), `answer_cleaned` (G2), `result_hint` (I3/I4: `tool`, `message_key`, `n`). `backend_configured` carries `backend_domain` (always `""`) and `session_tools_sha256`. With an `auto` switch on (`realtime_eval.yaml`): `session_rules` (`session_tools_sha256`, `schema_auto`, the derived rules, `format_retry_arguments`, `read_only_tools`). Since 2026-10-07, `tool_calls_out` carries `call_ids` and `arguments`; the geval-fix arms add `format_retry` (`tool`, `argument`, `before`, `after`, `mode`, `outcome`: `hint`, `found`, `not_found`, `transient`, `timeout` or `cancelled`; the relay's own retry is a `tool_calls_out` with `format_retry: true` and call id `<id>.fmt`) and `field_provenance` (the line added to a note) |
| `logs/fdh_gateway_events.jsonl` | Gateway: `gateway_start` (`backend_features`, `backend_catalog_sha256`), sessions, `backend_fingerprint`, worker spawn/ready/exit (`start_ms`, `rss_mb`), run epochs and outcomes, context delivery states, watchdog, respawns |
| `logs/fdh_workers/*.log` | Each Hermes worker's stdout/stderr. "Auxiliary Nous client unavailable" lines are harmless. `Error code: 429` or `Retrying API call` makes the run invalid (§6.3, P0.1) |
| `logs/fdh_workers/<session>.trace.jsonl` | With `hermes.backend_trace` (on in `gateway.eval.yaml`): one `session` record (the tool schemas as the model sees them, the rendered `backend_system`), then a `request` (the messages as sent) and a `response` (content, tool calls, finish reason, usage) per model call, joined by `api_request_id`. The input of the G2 replay (§6.3). Every `request` repeats the whole conversation, so the file grows with each model call |
| `logs/fdh_gateway.out` | Gateway process output |
| `docker logs fdh-voice`, `docker logs fdh-voice-web` | Voice server output (latency breakdowns, errors) |

Offline frontend decision check (gate G3, live frontend model, about 1 minute, 51 cases). The latest
result is 0.98 accuracy and 1.00 on `request` in the WORKING state:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.delegation_replay \
  --concurrency 2 --out logs/fdh_delegation_replay.json
```

## 8. No-GPU / no-LLM smoke mode

There is no speech, no LLM and no Hermes: stub ASR/TTS, the rule-based frontend and an in-process fake
backend. `stub_gate.yaml` makes the fake backend call the first tool on every run, so τ³'s Gate A sees a
function call.

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.server \
  --config src/prototypes/voice_delegation_hermes_agent/config/profiles/stub_gate.yaml --stub-speech --port 8779 &
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.tau2_replay \
  --url ws://localhost:8779/v1/realtime --synthetic-turns 3 --pause-s 1        # last line: tau2 contract: OK
# Gate A, in the tau2 checkout:
PINE_REALTIME_BASE_URL=ws://localhost:8779/v1/realtime PINE_API_KEY=unused \
  uv run python ../nemotron-voice-agent-smasurekar/src/prototypes/voice_frontend_backend_agent/cli/tau2_gates/gate_a_handshake.py
```

Unit tests: `uv run pytest tests/unit/prototypes/delegation tests/unit/prototypes/voice -q`.
`test_fdh_fix_plan.py` also checks that the control `realtime_eval_124acf7.yaml` with `gateway.eval_124acf7.yaml`
renders and decides exactly as agent `124acf7` (`fixtures/golden_eval_124acf7.json`; regenerate only for an intended change of the deployed
behaviour: `PYTHONPATH=src uv run python tests/unit/prototypes/delegation/_fdh_golden_eval.py --write`).
`test_fdh_golden_prompts.py` checks that, with every prompt variant off, every prompt is byte-identical to
agent commit `3a7e04a`; regenerate the golden files only for an intended baseline change
(`PYTHONPATH=src uv run python tests/unit/prototypes/delegation/_fdh_golden.py --write`).

## 9. Configuration knobs

Profiles are in `src/prototypes/voice_delegation_hermes_agent/config/`. Unknown keys fail at load time.

| Setting | Where | Default |
|---|---|---|
| End-of-turn silence | `voice/base.yaml` `turn_detection.silence_duration_ms` | 800 (client values ignored) |
| Transcript / tool-argument normalization | `voice/base.yaml`, `voice/tau3.yaml`, `voice/tau3_spelling.yaml`, `voice/tau3_fixes.yaml` (the default), `voice/realtime_eval.yaml` `normalization.*` | on / on (τ³), off (browser); `rules: auto` in `voice/realtime_eval.yaml` |
| Simulated backend delay | `backend.simulated_delay.{seconds,where}`, env `FDH_BACKEND_DELAY_S` (browser profile) | 0, 5 in `browser_demo` |
| Frontend model | env `FRONTEND_LLM_MODEL`, `FRONTEND_LLM_BASE_URL`; `frontend.llm.*` | `nvidia/nvidia/nemotron-3.5-lightning`, thinking off |
| Frontend tool choice, hedging, timeout | `frontend.tool_choice`, `frontend.hedge_after_ms`, `frontend.timeout_ms` | `named`, 1500, 4000 |
| Filler spoken when delegating | `delegation.speak_when_delegating` (`tau3_eval_silent_ack.yaml` sets false) | true |
| Guards | `delegation.guards.backend_question_needs_delegate`, `backchannel_words` | on, 17 words |
| Steering | `backend.steer_mode` | `auto` (redirect during a model call, else steer) |
| Status speech | `backend.status_verbalizer.mode` | `llm` (template fallback) |
| Output hold / stale policy | `output.hold_max_ms`, `output.on_stale` | 4000; keep answers and apologies, drop stale status and fillers |
| Tool/instruction change after the agent exists | `session.update_after_start` | `error` |
| Backend model | `gateway.yaml` `hermes.model`, `base_url`, `request_overrides`; env `BACKEND_LLM_MODEL`, `BACKEND_LLM_BASE_URL` | `nvidia/nvidia/nemotron-3-ultra` on the Inference Hub, `enable_thinking: true`, budget 1024 |
| Capacity | env `FDH_MAX_SESSIONS` (gateway and each voice server) | 8 |
| Hermes budgets | `hermes.max_iterations`, `run_budget_seconds`, `run_hard_deadline_s` | 30, 300 (soft), 330 (hard, kills the worker) |
| Worker processes | `workers.start_timeout_s`, `stop_timeout_s`, `kill_grace_s`, `warm`, `recovery.*` | 15, 12, 3, 0; respawn at most 2 per session |
| M1 proactive status | `output.proactive_status.{enabled,after_s,max_per_run}` (`after_s` from env `FDH_PROACTIVE_AFTER_S`); lines in `prompts.yaml` `status_proactive_lines` | on, 15 s, 1 per run |
| M3 spelled runs | `voice/*.yaml` `normalization.transcript.spelled_runs.{enabled,min_tokens,case}` | on, 3, `keep` (`voice/tau3_spelling.yaml`, `voice/browser.yaml`, `voice/realtime_eval.yaml`; off in `voice/tau3.yaml`) |
| M3 spelling hold | `delegation.spelling_hold.{enabled,hold_ms,complete_patterns}` | on, 1500 ms, `[]` (six-character code pattern in `tau3_eval.yaml` and `tau3_arm_m3_spelling.yaml`; `auto` in `realtime_eval.yaml`) |
| M3 local "invalid ID" wording | `voice/*.yaml` `normalization.tool_arguments.invalid_message_key`, `escalate_invalid_message_key` | `tool_argument_invalid_readback`, `tool_argument_invalid_spell_all` (`voice/tau3_spelling.yaml`, which the default voice profile `voice/tau3_fixes.yaml` extends, and `voice/realtime_eval.yaml`); `tool_argument_invalid`, none in `voice/tau3.yaml` |
| I3/I4 recovery notes | `voice/*.yaml` `normalization.tool_arguments.result_hints.{enabled,tools,failure_pattern,count_local_invalid,max_hints,message_key,escalate_message_key}`; texts in `voice_frontend_backend_agent/config/prompts.voice.yaml` | on in `voice/tau3_fixes.yaml` (the default; `voice/tau3_recovery_hint.yaml` is I3 without escalation): `find_user_id_by_name_zip`, `find_user_id_by_email`, `get_user_details`; `^Error: .*\bnot found\b`; local invalid answers count; 3; `lookup_not_found_hint`, `lookup_not_found_hint_words`. In `voice/realtime_eval.yaml`: `tools: auto`, a not-found text that names a user, customer, member, client or profile, local invalid answers only for a person argument. Off in `voice/tau3.yaml`, `voice/tau3_spelling.yaml` and the code defaults |
| I1 phone numbers | `gateway.yaml` `prompt_features.phone_format`; text in `prompts.backend.yaml` `backend_system` (`<phone_numbers>`) | on (off in `gateway.baseline.yaml` and `gateway.identity_control.yaml`) |
| Domain notes | `gateway.yaml` `prompt_features.domain_notes`, `domains.<name>.tools_any`; note texts in `prompts.backend.yaml` `backend_domain_notes` | off, `domains: {}`, no notes ship |
| M2 replay an unheard answer | `delegation.replay_unheard_answer.{enabled,ttl_s}` (needs `prompt_features.replay_intent`) | off, 30 s |
| G1 filler de-duplication | `delegation.filler_dedupe.{enabled,recent}`; lines in `prompts.yaml` `filler_alternatives` | on, 3 |
| G2 answer cleanup | `output.clean_answers` | on |
| Frontend prompt variants | `delegation_agent.yaml` `prompt_features.replay_intent` | off |
| Backend prompt variants | `gateway.yaml` `prompt_features.{spelling_v2,spoken_output,write_consent,phone_format,domain_notes}` (`gateway.baseline.yaml` turns all off; `gateway.<variant>.yaml` turns one on over it; `gateway.eval.yaml` pins them) | all on except `domain_notes` |
| Geval-fix backend variants (A2, B1–C2) | `gateway.yaml` `prompt_features.{format_correction,write_consent_v2,policy_check,request_scope,transfer_check,capability_check}`; text in `prompts.backend.yaml` (`spelling_v2`, `<consent>`, `<before_change>`, `<before_transfer>`) | on (`gateway.eval.yaml` pins them on; `gateway.eval_control.yaml` turns them off; `gateway.eval_<fix>.yaml` turns one on over the control) |
| A1 / A1-lite format retry | `voice/*.yaml` `normalization.tool_arguments.format_retry.{enabled,mode,fields,hint_message_key,found_message_key,not_found_message_key}` (needs the retry guard); texts `format_retry_*` in `voice_frontend_backend_agent/config/prompts.voice.yaml` | on in `voice/realtime_eval.yaml` (`mode: call`, `fields: phone`); off in `voice/realtime_eval_control.yaml` and the code defaults |
| A3 field provenance | `voice/*.yaml` `normalization.tool_arguments.result_hints.field_provenance` | on in `voice/realtime_eval.yaml` (with the A2 note `lookup_not_found_hint_format`); off in `voice/realtime_eval_control.yaml` and the code defaults |
| Backend decision trace (P0.3) | `gateway.yaml` `hermes.backend_trace`, env `FDH_BACKEND_TRACE` | off; on in `gateway.eval.yaml` and `gateway.eval_control.yaml` |
| F2 no-silence guards | `delegation.guards.{liveness_reply,question_never_silent,ping_as_status}`; phrase lists `delegation.guards.lexicon.<language>` (picked by the voice profile's ASR `language_code`: the exact tag, then the base language); lines in `prompts.yaml` `liveness_lines` | on in `realtime_eval.yaml` and `realtime_eval_liveness.yaml`, off in the controls and the code defaults; `en` ships in the code defaults |
| F1 replay triggers | `delegation.replay_unheard_answer.{triggers,whole_if_heard_below}` (`status_intent` is M2 and needs `replay_intent`; `backchannel`, `ping`, `repeat` do not) | `[status_intent]`, 0 (controls, code defaults); `[backchannel, ping, repeat]`, 0.2, `ttl_s: 60` in `realtime_eval.yaml` and `realtime_eval_liveness.yaml` |
| F4 situational facts | `gateway.yaml` `prompt_features.situational_facts` | on (`gateway.yaml`, `gateway.eval.yaml`, `gateway.eval_situational.yaml`); off in `gateway.eval_124acf7.yaml` and every other control |
| F8 identification ledger | `voice/*.yaml` `normalization.tool_arguments.result_hints.{ledger,ledger_message_key,identification_tools,ledger_max_age_turns,ledger_correction_cues}`; text `lookup_ledger` in `voice_frontend_backend_agent/config/prompts.voice.yaml` | on in `voice/realtime_eval.yaml` and `voice/realtime_eval_identity.yaml` (`identification_tools: auto`, `max_hints: 6`, A3 off); off in `voice/realtime_eval_124acf7.yaml`, `voice/realtime_eval_control.yaml` and the code defaults |
| F8 name inside an identifier | `gateway.yaml` `prompt_features.identity_from_identifier` | on (`gateway.yaml`, `gateway.eval.yaml`, `gateway.eval_identity_name.yaml`); off in `gateway.eval_124acf7.yaml` and every other control |

## 10. Stop and clean up

```bash
docker stop fdh-voice fdh-voice-web          # --rm removes them
kill "$(cat logs/fdh_gateway.pid)"           # the gateway stops its workers (SIGTERM, then SIGKILL)
pgrep -f sidecar.gateway_server && pkill -f sidecar.gateway_server   # if the pid was only the uv wrapper
docker compose --profile frontend-backend-agent/single-gpu stop nemo-speech   # optional
```

Runtime state that must never be committed: `.cache/fdh-hermes-homes/` (per-worker `HERMES_HOME`, removed
when each worker exits), `/tmp/fdh-workers/` (sockets), and `logs/`.

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Voice server logs "backend gateway not reachable" | The gateway is not running, or it is bound to `127.0.0.1`. Start it with `FDH_GATEWAY_HOST=$DOCKER_HOST_IP` (§3). Check with `docker exec fdh-voice curl -s http://host.docker.internal:8790/health` |
| Voice server exits with `server.max_sessions (N) exceeds the gateway's max_sessions` | Lower `-e FDH_MAX_SESSIONS` on the voice server, or raise it on the gateway |
| `session.update` fails with `worker_start_timeout` or `backend_unavailable` | Read `logs/fdh_workers/*.log`. Usually the Hermes venv is missing or wrong (§1.2): check `FDH_HERMES_PYTHON`, set `FDH_HERMES_REPO` |
| Worker log: `tool surface ... tool_search` | Stale `HERMES_HOME`. Homes are regenerated per worker with `tools.tool_search.enabled: "off"`; delete `.cache/fdh-hermes-homes/` |
| τ³: `Session configuration failed` | The server sent `error` on `session.update`; the voice log's `backend_error` has the code |
| τ³: `RuntimeError: Not connected to API` | Keepalive pings closed the session while tau2 was frozen. The profiles set `ws_ping_interval_s: 0`, so check you used `tau3_eval.yaml` |
| No reply to a real question | Check `delegation_decision`: `repair: timeout` means the frontend endpoint was slow (4 s budget, hedged at 1.5 s); `guard_backchannel` means the turn was a pure backchannel |
| Answer never spoken | The user was still speaking, and answers are held until they stop (`output_hold`). Check `playback_outcome` for `not_heard` |
| Browser: no microphone | Use `https://<IP>:8776/` (not http, not a proxied hostname) and accept the certificate |
| tau2: `No module named 'pyaudio'` | In the tau2 checkout: `uv sync --extra voice --extra dev` (needs the `portaudio19-dev` package) |
| Gate A against the real server fails on the transcript | Gate A streams a tone, which real ASR does not transcribe. Use the stub gate (§8) for Gate A, and §4.2 or §6 for the real stack |
| `fingerprint_check` fails with `missing [...]` | The voice server or the gateway runs code from before the fingerprint change: restart both (§10, §3, §4) |
| `fingerprint_check` fails with `the arm declares` | A server runs another arm's profile or gateway config: check `FDH_PROFILE` / `FDH_GATEWAY_CONFIG` and `curl` both `/health` endpoints. The control pairs with `gateway.baseline.yaml`, not `gateway.yaml` |
| Voice server fails with `replay_unheard_answer.enabled needs prompt_features.replay_intent` | M2 needs its frontend prompt variant: use `tau3_arm_m2_replay.yaml` |
| Status update says "still working" after the answer | This should not happen: a status line is dropped when its run has already finished (`status_dropped` in the event log). If you see it, report the session id |
| A status sentence sounds templated | The status LLM call exceeded `backend.status_verbalizer.timeout_ms` (3000); `status_spoken.source` is `template_fallback` |
| `report.sh` prints `P0.1 … INVALID (re-run it)` | A backend call of that run was rate-limited (429) and waited for `Retry-After`, so the run is not a clean measurement. Re-run it in a quieter window |
| No `logs/fdh_workers/*.trace.jsonl` with `gateway.eval.yaml` | Check `"backend_trace": true` in the gateway `/health`, then the worker log for `backend trace:` warnings (a Hermes version without the plugin hook registry writes no trace) |
| `relay_replay` reports every session `skipped` | The event log was written before 2026-10-07 (`tool_calls_out` without `call_ids` and `arguments`), or no `--session-update` matches the sessions' `session_tools_sha256` (`sessions_without_tools`) |
| `backend_decision_replay replay` fails with `occurs 0 times in the system messages` | The decisions or traces come from another control, or Hermes changed how it embeds the ephemeral system prompt. Re-extract from the control's own traces |
| An F1/F2 arm shows no `delegation_guard` or `answer_replayed` events | Look for `lexicon_missing`: the session's ASR `language_code` has no `delegation.guards.lexicon` entry, so the rules are inactive by design |
| `identity_ledger` never appears in an identity arm | `session_rules.identification_tools` is empty or misses the lookup: check the tool's name or description says it returns a person, or list the tools in `result_hints.identification_tools` |
| Manual-mode client gets `conversation_already_has_active_response` | It sent `response.create` while the previous turn was still deciding or speaking. Wait for `response.done`, as Realtime requires |
