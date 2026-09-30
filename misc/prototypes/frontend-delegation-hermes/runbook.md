# Runbook — Frontend Delegation to a Hermes Backend (web + OpenAI Realtime / τ³-voice)

**Code:** `src/prototypes/voice_delegation_hermes_agent/` · **Plan:** [`prototype-plan.md`](prototype-plan.md) ·
**Spec:** [`workflow.csv`](workflow.csv)

This runbook starts two deployments that share one backend gateway:

- the **OpenAI Realtime server for τ³-voice** (port 8775);
- the **browser page** (port 8776), with the 5 s simulated backend delay.

Every command runs from the repository root unless it says otherwise. The τ³ campaign runbook for this
prototype (arms, reportable runs, archiving) is a separate, later document. §6 only shows how to point τ³
at the server.

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

## 0. What runs where

```
 browser page ──wss──► fdh-voice-web  (container, :8776 → 7860, TLS, demo tools, 5 s delay) ─┐
 tau2 / Realtime ─ws─► fdh-voice      (container, :8775 → 7860, client tools)               ─┤ ws://<docker0>:8790/v1/backend
                                                                                             ▼
                               backend gateway (host process, Hermes-free, :8790 on docker0)
                                 └─ one Hermes worker process per Realtime session (Python 3.13)
                                      └─ AIAgent → Inference Hub nvidia/nvidia/nemotron-3-ultra (reasoning on)
 speech: nemo-speech (container, :50051, ASR + TTS) ◄── both voice containers
 frontend LLM: Inference Hub nvidia/nvidia/nemotron-3.5-lightning (reasoning off) ◄── both voice containers
```

| Component | Where | Port | Config |
|---|---|---|---|
| `nemo-speech` | container (Compose profile `frontend-backend-agent/single-gpu`) | 50051 | `docker/docker-compose.nemo-speech-cpp.yaml` |
| Backend gateway + Hermes workers | host process | 8790, bound to the `docker0` address | `config/gateway.yaml` |
| τ³ Realtime voice server `fdh-voice` | container (`nemotron-voice-agent:latest`, `src/` mounted read-only) | 8775 | `config/profiles/tau3_eval.yaml` |
| Browser voice server `fdh-voice-web` | container | 8776 (https) | `config/profiles/browser_demo.yaml` |

Why the gateway runs on the host: Hermes needs its own venv and Python (3.13 here; the checkout requires
`>=3.11,<3.14`), while the voice image ships Python 3.12 with this repository's dependencies (plan D1).
The voice containers reach the host through `host.docker.internal`, which the Compose `app-base` maps to
the `docker0` address.

Both profiles use:

- 800 ms end-of-turn silence (`honor_client_values: false`, so the client's 500 ms is ignored);
- transcript normalization (on by default);
- the frontend tool `delegate(delegate, filler_text, request)`, with the filler spoken.

The τ³ profile also normalizes tool arguments. The browser profile cannot, because its tools run in the
server (`tools.source: config`).

## 1. Prerequisites (once)

**1.1 `.env`** must define `NVIDIA_API_KEY` (an Inference Hub key, `sk-...`). Keep any existing `.env`:

```bash
test -f .env || cp .env.example .env      # then set NVIDIA_API_KEY
grep -c '^NVIDIA_API_KEY=.\+' .env        # expect 1
```

**1.2 Hermes checkout and its Python 3.13 environment.** A separate venv under `~/.cache/fdh`, so the
checkout's own `.venv` is not touched. The Hermes checkout caps Python at `<3.14` (Rust-backed dependencies
have no cp314 wheels yet), so `uv sync --python 3.14` fails:

```bash
export HERMES_REPO=$PWD/../hermes-agent-smasurekar     # adjust if the checkout lives elsewhere
(cd "$HERMES_REPO" && UV_PROJECT_ENVIRONMENT=$HOME/.cache/fdh/hermes-venv-313 uv sync --python 3.13)
$HOME/.cache/fdh/hermes-venv-313/bin/python -c "import run_agent; print('hermes ok')"   # run from $HERMES_REPO
```

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
from the LAN. The backend model is pinned on the command line.

```bash
nohup env PYTHONPATH=src \
  FDH_GATEWAY_HOST=$DOCKER_HOST_IP FDH_GATEWAY_PORT=8790 FDH_MAX_SESSIONS=8 \
  FDH_HERMES_PYTHON=$HOME/.cache/fdh/hermes-venv-313/bin/python \
  BACKEND_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra BACKEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  FDH_GATEWAY_LOG=logs/fdh_gateway_events.jsonl FDH_WORKER_LOG_DIR=logs/fdh_workers \
  uv run python -m prototypes.voice_delegation_hermes_agent.sidecar.gateway_server \
    --config src/prototypes/voice_delegation_hermes_agent/config/gateway.yaml \
  > logs/fdh_gateway.out 2>&1 &
echo $! > logs/fdh_gateway.pid
sleep 3; curl -s http://$DOCKER_HOST_IP:8790/health; echo
```

Expected health:

- `"ok": true` and `"max_sessions": 8`;
- `"agent_kind": "hermes"`;
- `"hermes": {"model": "nvidia/nvidia/nemotron-3-ultra", "base_url": "https://inference-api.nvidia.com/v1",
  "reasoning": true}`.

**3.1 Optional backend-only check** (a real Hermes worker, text only, about 30 s). It drives the gateway
directly, the way the voice server does. It covers a new task with a tool call, a continue, a status
request while WORKING, and a steer during a slow tool:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.backend_probe \
  --url ws://$DOCKER_HOST_IP:8790/v1/backend
```

## 4. Start the τ³ Realtime voice server (`fdh-voice`, port 8775)

```bash
docker compose --profile frontend-backend-agent/single-gpu run --rm -d --no-deps --name fdh-voice -p 8775:7860 \
  -v "$PWD/logs:/app/logs" -e PYTHONPATH=/app/src -e PIPELINE_TLS=false \
  -e FDH_BACKEND_URL=ws://host.docker.internal:8790/v1/backend -e FDH_MAX_SESSIONS=4 \
  -e FRONTEND_LLM_MODEL=nvidia/nvidia/nemotron-3.5-lightning -e FRONTEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e FDH_EVENT_LOG=logs/fdh_voice_events.jsonl \
  frontend-backend-agent-single-gpu \
  uv run python -m prototypes.voice_delegation_hermes_agent.server \
    --config src/prototypes/voice_delegation_hermes_agent/config/profiles/tau3_eval.yaml --port 7860
sleep 15; curl -s localhost:8775/health; echo
docker logs fdh-voice 2>&1 | grep -E 'backend gateway|ready|VAD'
```

Expected:

- `/health` shows `"status": "ok"`, `"prototype": "frontend-delegation-hermes"` and
  `"backend": {"link": "websocket", ...}`.
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
https://localhost:8776/health` shows `"prototype": "frontend-delegation-hermes"`, and `docker logs
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

**Reporting.** `fba_voice_metrics.py` expects one backend operation per turn. Convert the event log first;
the adapter credits each run to the turn that started it and exits 1 on an orphaned tool call:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.cli.report_adapter \
  logs/fdh_voice_events.jsonl --out logs/fdh_voice_events.legacy.jsonl
```

## 7. Logs and what to look at

| File | Content |
|---|---|
| `logs/fdh_voice_events.jsonl`, `logs/fdh_voice_web_events.jsonl` | Voice server events per session: `fdh_session_start` (config hash), `delegation_decision`, `backend_action`, `tool_calls_out`, `tool_output_in`, `backend_answer`, `status_spoken`, `playback_outcome`, `delivery_note`, `history_sync`, `turn_latency`, `filler_timing`, `barge_in` |
| `logs/fdh_gateway_events.jsonl` | Gateway: sessions, worker spawn/ready/exit (`start_ms`, `rss_mb`), run epochs and outcomes, context delivery states, watchdog, respawns |
| `logs/fdh_workers/*.log` | Each Hermes worker's stdout/stderr. "Auxiliary Nous client unavailable" lines are harmless |
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

## 9. Configuration knobs

Profiles are in `src/prototypes/voice_delegation_hermes_agent/config/`. Unknown keys fail at load time.

| Setting | Where | Default |
|---|---|---|
| End-of-turn silence | `voice/base.yaml` `turn_detection.silence_duration_ms` | 800 (client values ignored) |
| Transcript / tool-argument normalization | `voice/base.yaml`, `voice/tau3.yaml` `normalization.*` | on / on (τ³), off (browser) |
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

## 10. Stop and clean up

```bash
docker stop fdh-voice fdh-voice-web          # --rm removes them
kill "$(cat logs/fdh_gateway.pid)"           # the gateway stops its workers (SIGTERM, then SIGKILL)
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
| Status update says "still working" after the answer | This should not happen: a status line is dropped when its run has already finished (`status_dropped` in the event log). If you see it, report the session id |
| A status sentence sounds templated | The status LLM call exceeded `backend.status_verbalizer.timeout_ms` (3000); `status_spoken.source` is `template_fallback` |
| Manual-mode client gets `conversation_already_has_active_response` | It sent `response.create` while the previous turn was still deciding or speaking. Wait for `response.done`, as Realtime requires |
