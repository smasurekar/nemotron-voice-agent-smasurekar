# Runbook: Voice Frontend/Backend Agent (OpenAI Realtime server) in Docker

**Status:** runbook · **Date:** 2026-09-23 · **Code:** `src/prototypes/voice_frontend_backend_agent/`
**Design:** [`voice-frontend-backend-agent-prototype-plan.md`](voice-frontend-backend-agent-prototype-plan.md) ·
**Package README:** [`src/prototypes/voice_frontend_backend_agent/README.md`](../../src/prototypes/voice_frontend_backend_agent/README.md)

This runbook serves the prototype's OpenAI Realtime endpoint (`ws://<host>:8765/v1/realtime`) from the
**existing** `nemotron-voice-agent:latest` image and Compose services. It does not modify the Dockerfile or
the Compose files. It then points tau3-bench (`tau2-bench-smasurekar`) at that endpoint.

**Default setup:**

| Component | Where | Model / endpoint | Reasoning |
|---|---|---|---|
| ASR + TTS | local `nemo-speech` container (Nemotron Speech) | Nemotron Speech Streaming + Magpie TTS, `nemo-speech:50051` | n/a |
| Frontend LLM | NVIDIA Inference Hub | `nvidia/nvidia/nemotron-3.5-lightning` at `https://inference-api.nvidia.com/v1` | **off** |
| Backend LLM | NVIDIA Inference Hub | `nvidia/nvidia/nemotron-3-ultra` at `https://inference-api.nvidia.com/v1` | **on** (budget 1024) |

These are also the code's defaults, set in `src/prototypes/text_frontend_backend_agent/config/agent.yaml`:
the frontend has `chat_template_kwargs.enable_thinking: false`, and the backend has `enable_thinking: true`
with `reasoning_budget: 1024`. Only nemo-speech runs locally, so no local LLM is needed.
The local-vLLM alternative is in §7.

## Where to run the commands

| Tag | Directory |
|---|---|
| **[repo]** | `/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar` (repository root; every Compose command runs here) |
| **[tau2]** | `/localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar` (tau3-bench checkout) |

Inside the container the working directory is `/app`, which is the repository root, and `./src` is
bind-mounted at `/app/src`. The server therefore runs your current working-tree code without an image
rebuild.

## All the commands at a glance

| # | Step | Where | Command |
|---|---|---|---|
| P1 | Create `.env` (once), set `HF_TOKEN` and `NVIDIA_API_KEY` | [repo] | `test -f .env \|\| cp .env.example .env` |
| P2 | Download speech models (once) | [repo] | `bash scripts/download-nemo-speech-models.sh` |
| P3 | Start nemo-speech (local ASR/TTS) | [repo] | `docker compose --profile frontend-backend-agent/single-gpu up -d nemo-speech` |
| P4 | Check it | [repo] | `docker compose --profile frontend-backend-agent/single-gpu logs -f nemo-speech` |
| 1 | Start the Realtime server | [repo] | `docker compose --profile frontend-backend-agent/single-gpu run --rm -d --name fba-voice -p 8765:7860 ... frontend-backend-agent-single-gpu uv run python -m prototypes.voice_frontend_backend_agent.server ...` (full command in §1) |
| 2 | Check it | [repo] | `curl -s localhost:8765/health` and `docker logs -f fba-voice` |
| 3 | Talk to it (text) | [repo] | `PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.voice_chat --io text` |
| 3B | Talk to it from a browser (microphone, speaker) | [repo] | start `fba-voice-web` (§3B), then open **https://10.176.172.158:8766/** (the IP, not the hostname) |
| 4 | Run tau3 | [tau2] | `uv run tau2 run --domain mock --audio-native --audio-native-provider openai --audio-native-model pine-nemotron-fba ...` (§4) |
| 5 | Stop | [repo] | `docker stop fba-voice fba-voice-web`, then `docker compose --profile frontend-backend-agent/single-gpu stop nemo-speech` |

§6 is a no-GPU smoke test: stub speech, scripted agent, and tau3 Gate A.

---

## Prerequisites: supporting services (nemo-speech)

These steps use the repository's documented Compose files unchanged (`docker/docker-compose.nemo-speech-cpp.yaml`
and the `frontend-backend-agent/single-gpu` recipe in `src/examples/frontend_backend_agent/README.md` and
`docs/01-getting-started.md`). The only difference from the full recipe is that only its `nemo-speech`
service (ASR and TTS on gRPC `:50051`) is started. The prototype joins the recipe's Compose network.

### P0. Requirements

- Docker Compose v2.20+ (`docker compose version`), an NVIDIA driver 580.x+, and one GPU for nemo-speech.
- The `hf` CLI, or `uvx`, for the model download.
- An NVIDIA Inference Hub key (`sk-...`, not `nvapi-...`) for the two LLMs.

### P1. Create `.env` [repo]

```bash
test -f .env || cp .env.example .env
```

Edit `.env`:

```bash
HF_TOKEN=hf_...          # speech model download (P2)
NVIDIA_API_KEY=sk-...    # Inference Hub key for the frontend and backend LLMs
```

The Inference Hub model ids really do carry a doubled `nvidia/nvidia/` prefix.

### P2. Download the nemo-speech models [repo]

Run this once, as your user, never with `sudo`:

```bash
bash scripts/download-nemo-speech-models.sh
```

Models land in `models/nemo-speech` (override with `NEMO_SPEECH_MODEL_LOC` in `.env`).

### P3. Start nemo-speech [repo]

```bash
docker compose --profile frontend-backend-agent/single-gpu up -d nemo-speech
```

This starts only the `nemo-speech` service of the single-GPU recipe. The recipe's local vLLM, booking server
and stock app stay stopped. The documented full recipe (`docker compose --profile
frontend-backend-agent/single-gpu up -d`) also works, but it additionally starts the ~28 GiB local vLLM, which
this setup does not use.

### P4. Check nemo-speech [repo]

```bash
docker compose --profile frontend-backend-agent/single-gpu ps
docker compose --profile frontend-backend-agent/single-gpu logs -f nemo-speech        # Ctrl-C to stop following
```

nemo-speech has no healthcheck. Wait until its logs show the gRPC server on `0.0.0.0:50051`.

---

## 1. Start the Realtime server [repo]

The command runs a one-off container of the existing `frontend-backend-agent-single-gpu` service (same image,
`.env` and network) and overrides its command. The server listens on **7860 inside** the container, so the
image's `/health` healthcheck stays meaningful, and is published on **host port 8765**.

```bash
mkdir -p logs
docker compose --profile frontend-backend-agent/single-gpu run --rm -d --name fba-voice \
  -p 8765:7860 \
  -v "$PWD/logs:/app/logs" \
  -e PYTHONPATH=/app/src \
  -e PIPELINE_TLS=false \
  -e FRONTEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e FRONTEND_LLM_MODEL=nvidia/nvidia/nemotron-3.5-lightning \
  -e BACKEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e BACKEND_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra \
  -e FBA_VOICE_EVENT_LOG=logs/fba_voice_events.jsonl \
  -e FBA_FILLER_LOG=logs/fba_filler.jsonl \
  frontend-backend-agent-single-gpu \
  uv run python -m prototypes.voice_frontend_backend_agent.server \
    --config src/prototypes/voice_frontend_backend_agent/config/profiles/tau3_eval.yaml \
    --port 7860
```

The four `*_LLM_*` lines equal the code defaults. They are passed explicitly so no value in `.env` can change
them. `NVIDIA_API_KEY` reaches the container through the service's `env_file: .env`. The ASR and TTS channels
also receive it as an `authorization` header, which nemo-speech ignores.

What the flags do:

| Flag | Why |
|---|---|
| `--profile frontend-backend-agent/single-gpu` | selects the recipe whose network holds `nemo-speech` |
| `run --rm -d --name fba-voice` | a detached one-off container named `fba-voice`, removed when it stops (`--name`, `-p` and `-v` are `docker compose run` options) |
| `-p 8765:7860` | `run` does not publish the service's own ports, so the port is published explicitly |
| `-v "$PWD/logs:/app/logs"` | puts the event and filler logs on the host (`[out-path]` keys resolve against `/app`) |
| `-e PYTHONPATH=/app/src` | needed for `python -m prototypes...` |
| `-e PIPELINE_TLS=false` | the image healthcheck then probes `http://localhost:7860/health` (the server speaks plain HTTP/WS) |
| `-e *_LLM_*` | pin the Inference Hub models and URL (frontend reasoning off, backend on, per `agent.yaml`) |
| `--config .../tau3_eval.yaml` | tau3 settings pinned: paired mode, silenced filler, client tools, no greeting |

Speech endpoints come from `src/examples/frontend_backend_agent/services.local.yaml` (section `singlegpu`: both
`nemo-speech:50051`). Other configs:

| Goal | `--config` |
|---|---|
| tau3, paired frontend + backend (default) | `src/prototypes/voice_frontend_backend_agent/config/profiles/tau3_eval.yaml` |
| tau3, paired, backend keeps the conversation history (`include: full`) | `src/prototypes/voice_frontend_backend_agent/config/profiles/tau3_eval_backend_history.yaml` |
| tau3, paired, backend history without the behavioural guidance (ablation) | `src/prototypes/voice_frontend_backend_agent/config/profiles/tau3_eval_backend_history_noguide.yaml` |
| tau3, paired, identifier normalization (written user IDs for the agent, local answers for malformed or already-failed IDs) | `src/prototypes/voice_frontend_backend_agent/config/profiles/tau3_eval_normalization.yaml` |
| tau3, backend only (no frontend LLM; the `FRONTEND_*` lines are then unused) | `src/prototypes/voice_frontend_backend_agent/config/profiles/backend_only.yaml` |
| live demo (audible filler, greeting, internal demo tools) | `src/prototypes/voice_frontend_backend_agent/config/profiles/live_demo.yaml` |

Every tau3 paired profile pins `backend.conversation_history`, and the two history profiles differ from
`tau3_eval.yaml` only in it, so `FBA_BACKEND_HISTORY` has no effect on them. For other paired configs,
`-e FBA_BACKEND_HISTORY=true` turns the history on. To run the arms side by side, start one container per arm with its own name, host port and log
paths: `fba-voice-hist` on port 8769 and `fba-voice-histng` on port 8770, logging to
`logs/fba_voice_<arm>_{events,filler}.jsonl`. The τ³ runbook
(`tau2-bench-smasurekar/misc/prototypes/voice-frontend-backend-agent-tau3-runbook.md`, §4.1) has the commands.
The event log records the arm in `session_start.backend_history` / `backend_history_guidance`, and in one
`backend_context` event per delegated turn.

`tau3_eval_normalization.yaml` is `tau3_eval.yaml` (history off) plus the `normalization` section. Run it as
the `norm` arm: container `fba-voice-norm` on port 8771, logging to `logs/fba_voice_norm_{events,filler}.jsonl`
(τ³ runbook §4.2). The event log records it in `session_start.normalization`. Before a τ³ run, replay the
profile offline over the unredacted event log of an earlier paired run (no ASR, no LLM):

```bash
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.normalization_replay \
  --events logs/fba_voice_events.jsonl \
  --config src/prototypes/voice_frontend_backend_agent/config/profiles/tau3_eval_normalization.yaml \
  --model pine-fba-voice-paired-airline-regular --out /tmp/norm_replay.jsonl   # summary on stderr
```

**Host-native equivalent** (server on the host, speech still in Docker on `localhost:50051`), run from [repo]
after `uv sync --dev`:

```bash
set -a && . ./.env && set +a          # NVIDIA_API_KEY=sk-...
FBA_ASR_SERVER=localhost:50051 FBA_TTS_SERVER=localhost:50051 PYTHONPATH=src \
  uv run python -m prototypes.voice_frontend_backend_agent.server \
    --config src/prototypes/voice_frontend_backend_agent/config/profiles/tau3_eval.yaml
```

## 2. Check the server [repo]

```bash
curl -s localhost:8765/health        # {"status":"ok",...}; 503 "starting" until the ASR/TTS warm-up passes
docker logs -f fba-voice             # look for "voice agent ready: ws://0.0.0.0:7860/v1/realtime"
```

At startup the server probes ASR and TTS with a short request. If nemo-speech is unreachable, the container
exits with an `ASR warm-up failed against asr: server=nemo-speech:50051 ...` error. Check P3 and P4 in that case.

## 3. Talk to it [repo]

These commands run on the host against the published port, so they need `uv sync --dev` once.

```bash
# Typed turns, text replies, client-executed demo tools (the tau3 function-call flow):
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.voice_chat \
  --url ws://localhost:8765/v1/realtime --io text \
  --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS

# tau2-shaped headless check (mu-law 8 kHz appends, continuous silence, wall-clock pauses, contract checks).
# The tones are only speech-like, so the real ASR returns little text; use --in <file.wav> for real speech.
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.tau2_replay \
  --url ws://localhost:8765/v1/realtime --in turn1.wav --in turn2.wav --pause-s 2
```

The microphone client (`--io mic`) needs PortAudio on the host (`sudo apt install libportaudio2`, then
`uv sync --dev --extra prototypes-voice-mic`). It is meant for a workstation, not the container.

## 3B. Talk to it from a browser: microphone and speaker [repo]

This tests the whole voice path (browser mic → VAD → nemo-speech ASR → frontend/backend LLMs → nemo-speech
TTS → browser speaker). It is a **second container**, `fba-voice-web`, on **host port 8766**, so the tau3
server from §1 can keep running on 8765. It differs from §1 in three ways:

- `--tls`: browsers allow the microphone only on `https` pages (or `http://localhost`). The server creates a
  self-signed certificate at startup; `PIPELINE_TLS=true` makes the image healthcheck probe `https`.
- `browser_demo.yaml`: the page cannot run tools, so the demo tools (`get_order`, `cancel_order`) run inside
  the server. The agent greets you. The filler stays **silent** (logged, and shown in gray on the page).
- The page itself is served by the server at `/` (`src/prototypes/voice_frontend_backend_agent/web/index.html`).

Needs nemo-speech (P3). Start it from [repo]:

```bash
mkdir -p logs
docker compose --profile frontend-backend-agent/single-gpu run --rm -d --name fba-voice-web \
  -p 8766:7860 \
  -v "$PWD/logs:/app/logs" \
  -e PYTHONPATH=/app/src \
  -e PIPELINE_TLS=true \
  -e FRONTEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e FRONTEND_LLM_MODEL=nvidia/nvidia/nemotron-3.5-lightning \
  -e BACKEND_LLM_BASE_URL=https://inference-api.nvidia.com/v1 \
  -e BACKEND_LLM_MODEL=nvidia/nvidia/nemotron-3-ultra \
  -e FBA_VOICE_EVENT_LOG=logs/fba_voice_web_events.jsonl \
  -e FBA_FILLER_LOG=logs/fba_voice_web_filler.jsonl \
  frontend-backend-agent-single-gpu \
  uv run python -m prototypes.voice_frontend_backend_agent.server \
    --config src/prototypes/voice_frontend_backend_agent/config/profiles/browser_demo.yaml \
    --port 7860 --tls
```

Check it:

```bash
curl -sk https://localhost:8766/health      # {"status":"ok",...}
docker logs -f fba-voice-web                # look for "browser mic page: https://0.0.0.0:7860/"
```

Then, in Chrome or Edge on your laptop:

1. Open **https://10.176.172.158:8766/**, the machine's IP (`hostname -I` shows it).
   Do **not** use the `ipp2-0543.ipp2a1.colossus.nvidia.com` hostname. `nvidia.com` is on Chrome's HSTS
   preload list (all subdomains), so Chrome blocks the self-signed certificate there with
   `ERR_CERT_AUTHORITY_INVALID` and offers no way to proceed. HSTS does not apply to IP addresses.
2. The certificate is self-signed, so Chrome warns once: choose **Advanced → Proceed to 10.176.172.158**.
3. Click **Start** and allow the microphone. The agent greets you; then speak.
4. **Use headphones.** Without them the agent can hear itself and interrupt its own answer.

The page shows what ASR heard (`you`), the agent's spoken answer (`agent`), and the silent filler in gray
(`filler … (silent)`). The level bar shows that the microphone is live. Speaking while the agent talks
interrupts it (barge-in). Try: "What is the status of order 5513?", then "Please cancel it." (Order 5512 has shipped and cannot be cancelled.)

If port 8766 is not reachable from your laptop (firewall), tunnel it and open **https://localhost:8766/**
instead:

```bash
ssh -N -L 8766:localhost:8766 ipp2-0543.ipp2a1.colossus.nvidia.com      # on your laptop
```

## 4. Run tau3-bench against it [tau2]

Add to `tau2-bench-smasurekar/.env`:

```bash
PINE_REALTIME_BASE_URL=ws://localhost:8765/v1/realtime   # use the server host's address if tau2 runs elsewhere
PINE_API_KEY=unused                                      # any value; enforced only with server.require_bearer
```

Start with one task on the `mock` domain, then scale up:

```bash
uv run tau2 run --domain mock --audio-native --audio-native-provider openai \
  --audio-native-model pine-nemotron-fba --speech-complexity control \
  --num-tasks 1 --num-trials 1 --max-concurrency 1 --max-steps-seconds 600 --verbose-logs

uv run tau2 run --domain airline --audio-native --audio-native-provider openai \
  --audio-native-model pine-nemotron-fba --speech-complexity control \
  --num-tasks 5 --num-trials 1 --max-concurrency 1 --max-steps-seconds 600 --verbose-logs
```

- Any model name starting with `pine-` routes tau2 to `PINE_REALTIME_BASE_URL`; the server accepts any name.
- Keep `--max-concurrency 1` until `server.max_sessions` has been load-tested against your nemo-speech
  (plan §13.5).
- The tau2 side needs its own user-simulator keys (LLM and ElevenLabs), as for any tau3 voice run.
- Results are under `data/simulations/<run>/` [tau2]. Server-side traces are in [repo] `logs/`:
  `fba_voice_events.jsonl` (turns, ASR latency, barge-ins, tool calls, per-turn latency) and `fba_filler.jsonl`
  (one timing record per delegation).

## 5. Stop [repo]

```bash
docker stop fba-voice fba-voice-web                                       # the prototype servers (--rm removes them)
docker compose --profile frontend-backend-agent/single-gpu stop nemo-speech  # local ASR/TTS
# or tear down everything the recipe created: docker compose --profile frontend-backend-agent/single-gpu down
```

## 6. No-GPU smoke test and tau3 Gate A

This needs no GPU, speech models or LLM: an energy VAD, a stub ASR ("utterance N"), tone TTS and a scripted
agent (call a tool, answer, transfer). It runs on the host from [repo].

```bash
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.server --stub-speech --stub-agent scripted &
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.tau2_replay --pause-s 1
# expected last line: "tau2 contract: OK"
```

**Gate A** drives the same stub server with tau2's real `OpenAIRealtimeProvider` and event parser, from [tau2]:

```bash
PINE_REALTIME_BASE_URL=ws://localhost:8765/v1/realtime PINE_API_KEY=unused \
  uv run python /localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/src/prototypes/voice_frontend_backend_agent/cli/tau2_gates/gate_a_handshake.py
# expected last line: "GATE A PASSED"
```

**Gate B** is a full `tau2 run --domain mock --audio-native-model pine-gateb ...` against the stub server; it
needs the tau2 user-simulator keys. Check it with:

```bash
python /localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/src/prototypes/voice_frontend_backend_agent/cli/tau2_gates/check_gate_b.py \
  data/simulations/<run>/results.json
```

Stop the stub server with `kill %1`. The same stub flags work inside the container: add
`--stub-speech --stub-agent scripted` to the §1 command.

## 7. Variants

### Local vLLM instead of Inference Hub (fully local)

This uses the full single-GPU recipe: nemo-speech plus Nemotron 3.5 Lightning on vLLM, which needs at least
28 GiB of free GPU memory. `HF_TOKEN` is needed; `NVIDIA_API_KEY` is not.

```bash
docker compose --profile frontend-backend-agent/single-gpu up -d        # documented recipe; first start 30-60 min
curl -sf localhost:18001/health && echo "vLLM ready"                    # host port 18001 -> container 8000
```

Then run the §1 command with these four lines instead of the Inference Hub ones. vLLM may log that it
ignored the backend's `reasoning_budget` field.

```bash
  -e FRONTEND_LLM_BASE_URL=http://nvidia-llm-vllm-frontend-backend:8000/v1 \
  -e FRONTEND_LLM_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b \
  -e BACKEND_LLM_BASE_URL=http://nvidia-llm-vllm-frontend-backend:8000/v1 \
  -e BACKEND_LLM_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b \
```

For a host-native server, use `http://localhost:18001/v1` for both base URLs instead.

### Cloud speech (NVCF ASR/TTS)

Needs `NVIDIA_API_KEY` in `.env`. Start the services with `docker compose --profile frontend-backend-agent
up -d` [repo]. Then run the §1 command with `--profile frontend-backend-agent`, service
`frontend-backend-agent`, and `--config src/prototypes/voice_frontend_backend_agent/config/profiles/cloud_speech.yaml`.
A missing key or `function_id` fails at startup, not on the first turn.
- **Rebuilding the image** is not needed for code changes (the code comes from the `./src` bind mount). Rebuild
  only after dependency changes: `docker compose --profile frontend-backend-agent/single-gpu up --build -d`.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| `curl localhost:8765/health` refuses the connection | `docker ps` shows `fba-voice`? Was `-p 8765:7860` given, and `--port 7860` passed to the server? |
| Container exits with `ASR warm-up failed` | nemo-speech is not up or has no models: see P2, P3 and P4 (`docker compose ... logs nemo-speech`) |
| `NVCF server ... needs NVIDIA_API_KEY` | cloud speech without a key in `.env` |
| tau2 aborts with `Session configuration failed` | the server rejected the `session.update`; read the error text in `docker logs fba-voice` |
| Every turn ends in an `agent_error` / `401` in `docker logs fba-voice` | `NVIDIA_API_KEY` missing from `.env`, or not an Inference Hub `sk-...` key |
| Local-vLLM variant: first turns are slow or fail | the vLLM is still warming up; check `curl -sf localhost:18001/health` |
| No filler in the audio | expected: `tau3_eval` silences it (`filler.mode: log_only`); see `logs/fba_filler.jsonl` |
| `unknown key ...` at startup | a typo in a profile; unknown keys are errors by design |
| Browser page: "the microphone needs https" | open the `https://` URL, and make sure `--tls` was passed (§3B) |
| Browser page: "WebSocket error" right after Start | accept the certificate warning first (reload the page), and check `docker logs fba-voice-web` |
| Chrome: `ERR_CERT_AUTHORITY_INVALID` "because the website uses HSTS", no Proceed link | you used the `*.nvidia.com` hostname: open `https://10.176.172.158:8766/` (the IP) or use the `ssh -L` tunnel |
| Browser page loads nothing / times out | port 8766 is not reachable from the laptop: use the `ssh -L` tunnel in §3B |
| The agent keeps interrupting itself | its own voice reaches the microphone: use headphones |
