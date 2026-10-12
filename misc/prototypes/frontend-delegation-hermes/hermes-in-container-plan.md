# Plan: Run Hermes Inside the `fdh-voice` Container (Python 3.14), Minimal Version

**Status:** plan, feasibility spike done (§2), two independent reviews applied (§8) · **Date:** 2026-10-12 ·
**Revision:** 5 (review 3: ready to implement)
**Agent:** `src/prototypes/voice_delegation_hermes_agent/` at `c907673`
**Hermes:** `../hermes-agent-smasurekar` at `af26acab73` (fork of `nousresearch/hermes-agent`)
**Target:** a separate branch, mergeable into the experimental branch

## 1. Goal and Scope

Today the delegation agent runs in three places (runbook §0):

| Piece | Where | Python |
|---|---|---|
| `fdh-voice` (voice server, frontend LLM, turn manager) | Container, using the borrowed `nemotron-voice-agent:latest` image | 3.12 |
| Backend gateway | **Host process** | 3.12 (this repository's venv) |
| Hermes workers, one per session | **Host processes**, spawned by the gateway | 3.14 (`~/.cache/fdh/hermes-venv-314`) |

The gateway and Hermes run on the host only because Hermes needs Python 3.14 and the voice image is
Python 3.12.

**Goal:** one image, on **Python 3.14**, that holds the τ³ voice server, the gateway, and Hermes. One
`docker compose up` starts it, with no host venv and no host process.

**The change:** four hand-written files (about 200 lines), one generated lock file, and documentation updates
(§4). The following stay unchanged:
- the code in `src/`;
- `pyproject.toml` and `uv.lock` (the root project stays `<3.14`);
- `docker/Dockerfile`, the root `docker-compose.yml`, and the example profiles.

The browser server `fdh-voice-web` keeps the host gateway for now (§7).

**What stays the same, and what does not:**
- **The same:** the code, the prompt templates, the profiles, and the models. `config_hash` matches a host run
  at the same commit with the same environment values (§3, V5b).
- **Different at runtime:**
  - Python 3.14 instead of 3.12 on the voice and gateway side.
  - Some voice-side library versions (§2).
  - Hermes's transitive library versions, compared with the host `hermes-venv-314`.
  - Four environment-dependent lines that Hermes adds to its system prompt
    (`agent/prompt_builder.py:_local_host_hints`): the active profile path, the home directory, the working
    directory, and the scratch directory. They become `/root`- and `/app`-based paths, and
    `backend_system_sha256` does not cover them.
- **Effect:** these differences are expected to be small. They are disclosed and measured in V5c, so treat the
  first container runs as a new deployment and do not assume byte-identical behavior.
- **Default profile change:** the container defaults to the τ³ submission pair, `realtime_eval.yaml` with
  `gateway.eval.yaml`. The runbook's current default is `tau3_eval.yaml` with `gateway.yaml`. This is a
  user-visible default and is documented (§4.5).

### Implementation at a Glance

This is a small packaging change. No application code changes, and every step is a copy of a snippet in §4.

1. Write `docker/fdh/overrides.txt` (2 lines) and generate `docker/fdh/requirements.txt` with the §4.4 command.
2. Write `docker/Dockerfile.fdh` from §4.1.
3. Write `docker/fdh/entrypoint.sh` from §4.2. The script is complete in the plan.
4. Write `docker/docker-compose.frontend-delegation-hermes.yaml` from §4.3.
5. Update the runbook and README sections listed in §4.5.
6. Run V1 to V8 (§5). Most gates are one command each.

**Estimated effort:** about 1 day to write and build, plus about 1 day for validation and docs (V5b needs one
host run at the same commit).

## 2. Feasibility Spike (Done 2026-10-12)

A scratch Python 3.14.7 venv on the RTX host held the Hermes checkout plus every package that the voice
prototype imports. The second review repeated the spike from the regenerated lock.

| Check | Result |
|---|---|
| Resolve Hermes and the `prototypes-voice` extra together on 3.14 | ✅ 104 packages plus Hermes. Hermes's exact pins win, for example `openai==2.24.0`, `pydantic==2.13.4`, and `rich==14.3.3` |
| cp314 binary wheels for `onnxruntime`, `soxr`, `grpcio`, `numpy`, and `pydantic-core` | ✅ |
| Import the voice server, gateway, worker, Silero VAD, Riva ASR/TTS, and Hermes `run_agent` | ✅ |
| `pytest tests/unit/prototypes/delegation tests/unit/prototypes/voice`, with `src` read-only | **474 passed** (1160 subtests). `test_fdh_hermes_adapter` (3) fails under pytest because of the `utils` collision. Run with `unittest` and the worker's `PYTHONPATH`, it passes 3/3 (V2) |
| `test_fdh_hermes_multisession`, which uses real Hermes worker processes | ✅ Runs (not skipped) when `FDH_HERMES_PYTHON` is set |

**Versions compared with `uv.lock` (today's image).**

The following stay the same:
- `pipecat-ai` 1.7.0 and `onnxruntime` 1.24.4, so Silero VAD is unchanged.
- `nvidia-riva-client` 2.27.0, `soxr` 1.0.0, and `pydantic` 2.13.4.
- `httpx` 0.28.1, `websockets` 15.0.1, and `jinja2` 3.1.6.

The following change on the voice side:
- `openai` 2.51.0 → 2.24.0 (the frontend LLM client).
- `protobuf` 7.35.1 → 6.33.6 (the Riva gRPC stubs).
- `grpcio` 1.83 → 1.84 and `numpy` 2.4.6 → 2.5.4.
- `fastapi` 0.141 → 0.143, `starlette` 1.3 → 1.7, and `uvicorn` 0.52 → 0.54.

**Constraints the files must respect:**

1. **Hermes refuses wheel builds.** Its `setup.py` guard covers only `bdist_wheel` and `sdist`. An **editable**
   install is allowed (version `0.0.0`), and its finder maps Hermes's root modules (`run_agent`, `utils`), so
   `FDH_HERMES_REPO` is not needed.
2. **The `utils` module collides.** `src/utils.py` shadows Hermes's top-level `utils`.
   - Only the worker process may import Hermes. The worker pool already gives workers a `PYTHONPATH` that holds
     only a `prototypes` symlink (`sidecar/worker_pool.py:216-229`), and it imports Hermes lazily
     (`worker/hermes_adapter.py`).
   - The voice server and the gateway run with `PYTHONPATH=/app/src` and never import Hermes.
   - `/app` has no top-level `utils.py`.
3. **`protobuf` pins conflict.** `nvidia-riva-client 2.27.0` pins `protobuf==6.33.5`, and the repository's CVE
   floor is `>=6.33.6`.
   - The lock is compiled with overrides (§4.4). `grpcio-tools` comes from `nvidia-riva-client` and is pinned
     to 1.81.1, which declares `protobuf>=6.33.5,<7` and `grpcio>=1.81.1`.
   - `uv pip check` then reports only the `nvidia-riva-client` / `protobuf` line. The main repository also
     overrides this pin.

## 3. Design

```text
nemotron-voice-agent-fdh:latest   (docker/Dockerfile.fdh, Python 3.14, /app/.venv on PATH)
 container fdh-voice (project nemotron-voice-agent-fdh, external network nemotron-voice-agent_default)
 ├─ gateway          127.0.0.1:8790, max_sessions 8, PYTHONPATH=/app/src        ← started first
 ├─ voice server     :7860 → host 8775, max_sessions 4, PYTHONPATH=/app/src     ← started once the gateway is healthy
 │                    FDH_BACKEND_URL=ws://host.docker.internal:8790/v1/backend, mapped to 127.0.0.1 in the container
 └─ Hermes workers   spawned by the gateway with FDH_HERMES_PYTHON=/app/.venv/bin/python
                     PYTHONPATH = prototypes symlink only; Hermes is installed editable from /opt/hermes
speech: nemo-speech container (root project, unchanged)     LLMs: Inference Hub (unchanged)
```

**Two processes in one container.** The voice server talks to the gateway over the same WebSocket as today.

**The backend URL string stays the same.**
- `config_hash` hashes the effective delegation config (`config.py:361-364`). That config includes `backend.url`
  and absolute file paths, so the hash depends on both the URL and the mount path.
- With `ws://host.docker.internal:8790/v1/backend` kept and that name mapped to `127.0.0.1` through
  `extra_hosts`, the hash equals a host run at the same commit with the same environment (verified in review 2).
- A `ws://127.0.0.1:…` URL would change the hash.

**Session limits stay as in the runbook: gateway 8, voice 4.**
- The gateway frees a slot only after `runtime.close()` stops the worker, which can take up to 12 + 3 s
  (`gateway_server.py:184-189`). Equal limits would give back-to-back τ³ sessions `error{code: capacity}`.
- The gateway's `--max-sessions` flag overrides the YAML value (`gateway_server.py:224-230`), and the voice
  server's capacity check passes with 8 ≥ 4 (`server.py:266-271`).
- `max_sessions` does not enter `config_hash`.

**The network is shared without sharing the project.** The service has its own project name and joins the root
project's network, `nemotron-voice-agent_default`, as an external network. This avoids orphan warnings, and the
root project's `down --remove-orphans` cannot remove `fdh-voice`.
- **Order matters:** a root `down` while `fdh-voice` is still attached fails to remove the network ("active
  endpoints"). Always take down `fdh-voice` first (§4.5, runbook §10).

**Hermes source.** A Compose `additional_contexts` entry is copied to `/opt/hermes`:
- **Path:** `hermes: ${HERMES_AGENT_DIR:-../../hermes-agent-smasurekar}`. A relative value resolves against
  `docker/`, not the repository root, so set `HERMES_AGENT_DIR` to an **absolute** path in `.env`.
- **It copies the working tree, not `HEAD`.** Before a build, check that `git -C <hermes> status` is clean and
  that `HEAD` is the commit recorded in the lock header.
- **Hermes's `.dockerignore` applies:** it excludes `.git` (1.2 GB), `.venv`, `tests/`, `*.md` (except
  `skills/**`), and `plugins/**/SKILL.md`. That has no visible effect on the delegation prompts, and the
  editable build still succeeds without `README.md`.

## 4. The Change

| File | Kind | Size |
|---|---|---|
| `docker/Dockerfile.fdh` | New | About 50 lines |
| `docker/fdh/entrypoint.sh` | New | About 50 lines |
| `docker/docker-compose.frontend-delegation-hermes.yaml` | New | About 60 lines |
| `docker/fdh/overrides.txt` | New | 2 lines |
| `docker/fdh/requirements.txt` | New, generated | About 226 KB with hashes (under the 1000 KB pre-commit limit) |
| `misc/prototypes/frontend-delegation-hermes/runbook.md` | Updated | Refer to §4.5 |
| `src/prototypes/voice_delegation_hermes_agent/README.md` | Updated | Refer to §4.5 |

### 4.1 `docker/Dockerfile.fdh`

**Base and environment**
- Base image: the OSRB-approved `nvcr.io/nvidia/base/ubuntu:noble-20260610`.
- System packages: `ca-certificates` and `curl`.
- uv is not an apt package. Copy a **pinned** release from its image:
  `COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv`. The `uv pip check` message format depends on
  the uv version, so pin it.
- Set `WORKDIR /app`, `ENV UV_PYTHON_INSTALL_DIR=/opt/python`, and `PATH=/app/.venv/bin:$PATH`. The
  `/opt/python` location keeps a later non-root user possible.
- Install Python: `uv python install 3.14.7`, then `uv venv --python 3.14.7 /app/.venv`.

**Dependencies**
1. `COPY docker/fdh/requirements.txt`, then
   `uv pip sync --python /app/.venv/bin/python --require-hashes requirements.txt`. `sync` installs exactly the
   locked set and does not resolve again, so it needs no override (verified).
2. `COPY --from=hermes . /opt/hermes`, then
   `uv pip install --python /app/.venv/bin/python --no-deps -e /opt/hermes`. This runs **after** `sync`,
   because `sync` removes unlisted packages.
   - **Disclosed:** Hermes's `build-system.requires` makes the editable build fetch `setuptools==83.0.0` and
     `wheel` without hash checking at build time.

**Files**
- `COPY src/ src/`. Compose also mounts it read-only for development.
- `COPY --chmod=755 docker/fdh/entrypoint.sh /app/entrypoint.sh`. `COPY --chmod` and `additional_contexts` need
  BuildKit, which is the default with Compose v2.
- Keep the `legal/` `COPY` lines from `docker/Dockerfile`.

**Build-time smoke.** Use **three separate `RUN` instructions**, so one failure cannot hide another. The RUN
shell is `/bin/sh` (dash), so write plain POSIX with no `<(...)`.

```dockerfile
RUN cd /app && PYTHONPATH=/app/src python -c "import prototypes.voice_delegation_hermes_agent.server, prototypes.voice_delegation_hermes_agent.sidecar.gateway_server"
RUN cd /tmp && HERMES_HOME=/tmp/hsmoke python -I -c "import run_agent" && rm -rf /tmp/hsmoke
RUN set -e; \
    known='The package `nvidia-riva-client` requires `protobuf==6.33.5`, but `6.33.6` is installed'; \
    out=$(uv pip check --python /app/.venv/bin/python 2>&1) || true; \
    printf '%s\n' "$out"; \
    printf '%s\n' "$out" | grep -qxF "$known"; \
    bad=$(printf '%s\n' "$out" | grep '^The package' | grep -vxF "$known" || true); \
    [ -z "$bad" ]
```

- **Second `RUN`:** it runs outside `/opt/hermes`, and `python -I` ignores `PYTHONPATH`, so a broken editable
  install fails the build. `HERMES_HOME` keeps it from writing `/root/.hermes` into the image.
- **Third `RUN`:**
  - `uv pip check` always exits 1 here because of the known line, hence `|| true`.
  - The `grep -qxF "$known"` line makes sure a uv error with no output cannot pass.
  - `--python` is explicit. Without it, uv can silently check the base interpreter.
  - The `known` text matches the real output of uv 0.12.3 (review 3).

**Image settings**
- **System libraries:** the spike ran on a full host. `docker/Dockerfile` also installs `libgl1`,
  `libglib2.0-0`, and `libxcb1`. Start without them and add them only if V2 or V6 fails on a missing library.
- **Optional:** a `docker/Dockerfile.fdh.dockerignore` that excludes `models/`, `logs/`, `.venv/`, and
  `client/`. The repository has no `.dockerignore`. BuildKit only transfers the paths that `COPY` uses, so this
  is for clarity.
- **Entry:** `EXPOSE 7860` and `ENTRYPOINT ["/app/entrypoint.sh"]`.

### 4.2 `docker/fdh/entrypoint.sh`

The entrypoint uses bash. It does not use `set -e`: under `set -e`, a non-zero `wait -n` would exit the script
before it stops the other process. The trap is installed **first**, so a `docker stop` during startup stops
both processes and does not wait out the grace period.

```bash
#!/usr/bin/env bash
set -uo pipefail
cd /app
mkdir -p logs
export PYTHONPATH=/app/src FDH_HERMES_PYTHON=/app/.venv/bin/python
CFG=src/prototypes/voice_delegation_hermes_agent/config
gw="" voice=""

stop() {
  [ -n "$voice" ] && kill -TERM "$voice" 2>/dev/null
  [ -n "$gw" ] && kill -TERM "$gw" 2>/dev/null
  [ -n "$voice" ] && wait "$voice"
  [ -n "$gw" ] && wait "$gw"
}
trap 'stop; exit 143' TERM INT

FDH_GATEWAY_HOST=127.0.0.1 FDH_GATEWAY_PORT=8790 \
  python -m prototypes.voice_delegation_hermes_agent.sidecar.gateway_server \
    --config "$CFG/${FDH_GATEWAY_CONFIG:-gateway.eval.yaml}" --max-sessions "${FDH_GATEWAY_MAX_SESSIONS:-8}" &
gw=$!
ready=0
for _ in $(seq 60); do
  curl -fs -m 2 http://127.0.0.1:8790/health >/dev/null && { ready=1; break; }
  kill -0 "$gw" 2>/dev/null || break
  sleep 0.5
done
[ "$ready" = 1 ] || { echo "gateway not healthy" >&2; stop; exit 1; }

python -m prototypes.voice_delegation_hermes_agent.server \
  --config "$CFG/profiles/${FDH_PROFILE:-realtime_eval.yaml}" --port 7860 &
voice=$!

rc=0
wait -n "$gw" "$voice" || rc=$?
stop
exit "$rc"
```

**Simulated in review 2:**
- SIGTERM sends TERM to both children, and the script exits with 143.
- If the gateway is killed, the script exits with 137. If the gateway fails at start, it exits with 1.
- uvicorn 0.54 re-raises SIGTERM, so the exit code is 143.
- A second `stop` call is harmless.

**Writable paths.** Only `/app/src` is read-only. The gateway's relative defaults resolve against `/app`:
- `home_root` is `.cache/fdh-hermes-homes`, and `log_dir` is `logs/fdh_workers`.
- Hermes's scratch-directory hint becomes the absolute `/app/.cache/fdh-hermes-homes/sess_…/cache/scratch`
  (V5c).
- The socket directory is `/tmp/fdh-workers`. Its `prototypes` symlink points into the read-only
  `/app/src/prototypes`, which only needs read access.

**Shutdown.** `stop()` waits for both children. The gateway's lifespan then closes each remaining session
**one after another** (`gateway_server.py:97-99`): about 12 s each to stop, then 3 s before a kill. With up to 4
voice sessions open, Compose must allow about 75 s (§4.3).

### 4.3 `docker/docker-compose.frontend-delegation-hermes.yaml`

**Project and network**
- `name: nemotron-voice-agent-fdh`.
- `networks: {default: {external: true, name: nemotron-voice-agent_default}}`. Compose does not create external
  networks, so start `nemo-speech` from the root project **first**, before `up`, `run` (V2), or anything else in
  §5.
- ASR and TTS then resolve `nemo-speech:50051` from the catalog
  (`examples/frontend_backend_agent/services.local.yaml`, singlegpu), as they do today.

**Service `fdh-voice`**
- `container_name: fdh-voice`, so the runbook's `docker logs fdh-voice` and `docker exec fdh-voice …` keep
  working. `compose run` ignores it, which is fine for V2.
- **Build:** `context: ..`, `dockerfile: docker/Dockerfile.fdh`,
  `additional_contexts: {hermes: "${HERMES_AGENT_DIR:-../../hermes-agent-smasurekar}"}`, and
  `image: nemotron-voice-agent-fdh:latest`.
- `init: true`. A small init process reaps orphaned children and forwards signals to the entrypoint.
- `ports: ["8775:7860"]`.
- `env_file: ../.env` for `NVIDIA_API_KEY`. The existing `.env` is only read, never written.
- `extra_hosts: ["host.docker.internal:127.0.0.1"]`.
- `volumes: ["../src:/app/src:ro", "../logs:/app/logs"]`.
- `stop_grace_period: 75s`. This covers 4 open sessions closed one after another; Docker's default is 10 s.
- `restart: unless-stopped`.

**Environment.** The defaults equal runbook §3 and §4, apart from the documented profile default (§1).
- **Interpolated values:** write each entry as `VAR: ${VAR:-default}`. A value from the shell then wins over
  `--env-file`, which wins over the default. That is what makes the per-domain `FDH_EVENT_LOG=… docker compose …
  up -d` in V5a, and the runbook's `export FDH_EVENT_LOG=…` lines, work.
- **`PYTHONPATH`:** set `PYTHONPATH: /app/src` as a fixed entry, so `docker exec fdh-voice python -m prototypes…`
  works. Workers are unaffected, because `worker_pool._env` replaces `PYTHONPATH`.
- **Keep the gateway's `--max-sessions` flag:** `FDH_MAX_SESSIONS=4` also reaches the gateway
  (`gateway.yaml:10`). Only the entrypoint's flag keeps the gateway at 8.

| Variable | Default |
|---|---|
| `PYTHONPATH` | `/app/src` (fixed) |
| `FDH_PROFILE` | `realtime_eval.yaml` |
| `FDH_GATEWAY_CONFIG` | `gateway.eval.yaml` |
| `FDH_MAX_SESSIONS` | 4 (voice) |
| `FDH_GATEWAY_MAX_SESSIONS` | 8 |
| `FDH_BACKEND_URL` | `ws://host.docker.internal:8790/v1/backend` |
| `PIPELINE_TLS` | `false` |
| `FRONTEND_LLM_MODEL` | `nvidia/nvidia/nemotron-3.5-lightning` |
| `FRONTEND_LLM_BASE_URL` | `https://inference-api.nvidia.com/v1` |
| `BACKEND_LLM_MODEL` | `nvidia/nvidia/nemotron-3-ultra` |
| `BACKEND_LLM_BASE_URL` | `https://inference-api.nvidia.com/v1` |
| `FDH_PROACTIVE_AFTER_S` | 15 |
| `FDH_EVENT_LOG` | `logs/fdh_voice_events.realtime_eval.jsonl` |
| `FDH_GATEWAY_LOG` | `logs/fdh_gateway_events.realtime_eval.jsonl` |
| `FDH_WORKER_LOG_DIR` | `logs/fdh_workers` |

**Log names**
- The τ³ campaign tooling expects the `*.realtime_eval.jsonl` names.
- A host gateway that still runs for `fdh-voice-web` must use a **different** `FDH_GATEWAY_LOG`, otherwise both
  gateways append to the same file.
- For fingerprint checks, override `FDH_EVENT_LOG` per domain (V5a).

**Health check**
- Test: `curl -fs -m 5 localhost:7860/health`.
- Timing: `start_period: 90s`, `interval: 30s`, `retries: 3`.
- **Startup:** uvicorn runs the warm-up before it binds, so the port refuses connections until ASR, TTS, and the
  frontend warm-up pass.
- **Gateway:** an unreachable gateway only logs a warning (`server.py:263-265`), but the entrypoint already
  refuses to start the voice server without a healthy gateway.
- **If `nemo-speech` is not up yet,** the warm-up fails, the container exits, and it restarts until speech is
  up.

**Interpolation.** Compose reads `${…}` defaults, including `HERMES_AGENT_DIR`, from the project directory,
which is `docker/`. Always pass the repository-root `.env` explicitly with `--env-file .env`. That path is
relative to the current directory.

Use:

```bash
F=docker/docker-compose.frontend-delegation-hermes.yaml
docker compose --profile frontend-backend-agent/single-gpu up -d nemo-speech      # unchanged, runbook §2
docker rm -f fdh-voice 2>/dev/null                                                 # a runbook-style container holds 8775
docker compose --env-file .env -f $F up -d --build
curl -s localhost:8775/health
docker exec fdh-voice curl -s 127.0.0.1:8790/health                                # gateway, inside the container
# stop: fdh first, then speech
docker compose --env-file .env -f $F down
docker compose --profile frontend-backend-agent/single-gpu down
```

**Root-owned logs.** Logs written by the container, including `fdh_workers` and traces, are owned by root on
the host. Use `sudo chown -R "$USER" logs` before cleanup if needed.

### 4.4 `docker/fdh/overrides.txt` and `docker/fdh/requirements.txt`

`overrides.txt` is committed so the lock can be regenerated:

```text
protobuf>=6.33.6,<7
grpcio-tools==1.81.1
```

Regenerate the lock from the repository root, with an **absolute** Hermes path. Record the command and the
Hermes commit in the file header. The snippet runs in a subshell so a dirty checkout does not close your shell:

```bash
(
  set -e
  HERMES_AGENT_DIR=$(realpath ../hermes-agent-smasurekar)
  if git -C "$HERMES_AGENT_DIR" status --porcelain | grep -q .; then echo "Hermes checkout is dirty" >&2; exit 1; fi
  printf '%s\n' "hermes-agent @ file://$HERMES_AGENT_DIR" 'pipecat-ai[silero]==1.7.0' \
    'nvidia-riva-client>=2.27.0,<3' 'fastapi>=0.141.1' 'uvicorn>=0.52.0' 'websockets>=14.0' 'numpy>=2.0.0' \
    'soxr>=1.0.0' 'pyyaml>=6.0.3' 'loguru>=0.7.3' 'openai>=1.109.1' 'rich>=13.9.4' 'python-dotenv>=1.2.2' 'jinja2>=3.1' |
  uv pip compile --no-config - --python-version 3.14 --python-platform x86_64-manylinux_2_28 --generate-hashes \
    --override docker/fdh/overrides.txt --no-emit-package hermes-agent -o docker/fdh/requirements.txt
)
```

**Notes:**
- **`--no-config` is required** (found during implementation). Without it, uv applies this repository's
  `[tool.uv] override-dependencies`, whose loose floors replace Hermes's exact pins: `cryptography>=46.0.5`
  replaced Hermes's `==50.0.1` and the lock got 50.0.2. The image's `uv pip check` step caught it. With
  `--no-config`, only `cryptography` changes, and the repository's CVE floors are still met.
- The input list is the `prototypes-voice` extra from `pyproject.toml`. Regenerate whenever the Hermes commit,
  that extra, or `overrides.txt` changes.
- Review 2 compiled this lock: 104 packages, `protobuf` 6.33.6, `grpcio` 1.84.0, `grpcio-tools` 1.81.1. The
  output contains no host paths.

### 4.5 Documentation (Same PR, as `AGENTS.md` Requires)

This is a user-visible workflow change, so the documentation changes in this PR. The PR also carries the
Documentation Writer Review receipt.

**Runbook (`misc/prototypes/frontend-delegation-hermes/runbook.md`)**

| Section | Change |
|---|---|
| §0 What runs where | `fdh-voice` holds the gateway and Hermes. `fdh-voice-web` still uses a host gateway (appendix). The default pair is now `realtime_eval.yaml` with `gateway.eval.yaml` |
| §1.2 Hermes venv | Needed only for the host appendix and development. The image needs a clean Hermes checkout and an absolute `HERMES_AGENT_DIR` in `.env` |
| §1.4 Voice image, §1.5 Shell variables | The τ³ path builds `nemotron-voice-agent-fdh` through the Compose file. `DOCKER_HOST_IP` is needed only for the host appendix |
| §3 Gateway | Replaced by the Compose command and `docker exec fdh-voice curl -s 127.0.0.1:8790/health`. The host procedure moves to an appendix |
| §3.1 `backend_probe` | Run it inside the container: `docker exec fdh-voice python -m prototypes.voice_delegation_hermes_agent.cli.backend_probe --url ws://127.0.0.1:8790/v1/backend …`. `PYTHONPATH` comes from Compose (§4.3); with an older container, add `-e PYTHONPATH=/app/src` |
| §4 `fdh-voice` | Replaced by the Compose command; profile and gateway config are switched with `FDH_PROFILE`/`FDH_GATEWAY_CONFIG` and `up -d` (recreate) |
| §5 `fdh-voice-web` | Points to the host-gateway appendix, with a distinct `FDH_GATEWAY_LOG` |
| §6.1 to §6.5 | Replace "then §3 (gateway) and §4 (`fdh-voice`)" and "restart it to switch" (lines 505, 582, 624, 689, and 719) with the Compose recreate. `fingerprint_check` (line 542, advice on line 549) reads a per-domain event log |
| §7 Logs | New default log names; per-domain event logs for fingerprint checks; root-owned logs |
| §9 Configuration knobs | Add `FDH_GATEWAY_MAX_SESSIONS`, `HERMES_AGENT_DIR`, and the Compose defaults |
| §10 Stop and clean up | `fdh-voice` down first, then the root project |
| §11 Troubleshooting | Gateway not healthy at start; restart loop until `nemo-speech` is up; capacity limits; the network-removal error |

**Prototype README (`src/prototypes/voice_delegation_hermes_agent/README.md`)**

| Line | Change |
|---|---|
| 36 | "voice server (Py 3.12)" becomes "Py 3.14 in the `fdh-voice` image (3.12 in the repository venv)" |
| 44 | "`sidecar/` \| gateway (host)" becomes "gateway (in `fdh-voice`, or host for `fdh-voice-web`)" |
| Run links | Add the Compose file |
| Tests (175-185) | Add the in-image run (V2) |

## 5. Validation

| Gate | Check | Pass |
|---|---|---|
| V1 build | `docker compose --env-file .env -f $F build` | Builds; the build-time smoke and the `uv pip check` filter pass |
| V2 unit tests in the image | Refer to the command below | Pytest: 474 passed, 0 skipped. Adapter: `Ran 3 tests … OK` |
| V3 repository CI unchanged | The `AGENTS.md` validation commands | Pass, because `pyproject.toml` and `uv.lock` do not change |
| V4 health | `curl localhost:8775/health` | `"backend": {"link": "websocket"}`, with the features from runbook §6.2 |
| V5a fingerprint, per domain | For each domain, recreate with `FDH_EVENT_LOG=logs/fdh_voice_events.realtime_eval.<domain>.v5.jsonl`, run one task, then `cli/fingerprint_check <that log> --profile …/profiles/realtime_eval.yaml --gateway-config …/gateway.eval.yaml` | Exit 0 for each domain |
| V5b against a host run | A **new** runbook-style host run at the same commit (`c907673`), with the same profile, gateway config, and environment. Diff `config_hash` and the fingerprint fields of `fdh_session_start` per domain. The existing `logs/fdh_voice_events.realtime_eval.jsonl` (197 sessions from older code, `config_hash a30f6df59a8b`) is **not** a valid baseline | Identical |
| V5c backend prompt | Diff `request.messages[0]` (system) of the first record in `logs/fdh_workers/*.trace.jsonl` against the host run's trace, after replacing `sess_[A-Za-z0-9_-]+` with `sess_X` | Only 4 lines differ: active profile path, home directory, working directory, and scratch directory. Record them |
| V6 τ³ smoke | One τ³ task per domain from tau2 against port 8775, combined with V5a | Completes; this is a smoke test, not a benchmark |
| V7 shutdown and failure | (a) `docker compose … stop` with **4** open sessions; (b) `docker stop` during startup; (c) `docker exec fdh-voice pkill -f gateway_server` | (a) The `gateway_stop` event is logged within 75 s, and no worker outlives the container. (b) Stops promptly, not after 75 s. (c) The container exits non-zero and restarts |
| V8 capacity | Four back-to-back τ³ sessions, then four parallel sessions | No `capacity` errors |

**V2 command.** Run it from the repository root:

```bash
docker compose --env-file .env -f $F run --rm --no-deps --entrypoint "" \
  -v ./tests:/app/tests:ro -v ./misc:/app/misc:ro -v ./pyproject.toml:/app/pyproject.toml:ro \
  -e FDH_HERMES_PYTHON=/app/.venv/bin/python fdh-voice sh -c '
    uv pip install --python /app/.venv/bin/python pytest &&
    PYTHONPATH=src python -m pytest tests/unit/prototypes/delegation tests/unit/prototypes/voice -q \
      --deselect tests/unit/prototypes/delegation/test_fdh_hermes_adapter.py &&
    mkdir -p /tmp/fdh-workers/pythonpath && ln -sfn /app/src/prototypes /tmp/fdh-workers/pythonpath/ &&
    PYTHONPATH=/tmp/fdh-workers/pythonpath:tests/unit/prototypes/delegation python -m unittest test_fdh_hermes_adapter'
```

## 6. Decisions Needed

| # | Decision | Recommendation |
|---|---|---|
| D1 | Hermes source: the personal fork, or upstream `nousresearch/hermes-agent` at a pinned commit | Upstream at a pinned commit, if the fork carries no needed changes (to verify) |
| D2 | The default profile pair | `realtime_eval.yaml` with `gateway.eval.yaml`, the τ³ submission pair, documented as a default change |
| D3 | Architecture | x86_64 only for now |

## 7. Deferred to Follow-Up PRs

**Not covered by this PR:**
- **`fdh-voice-web`** (8776, TLS, `browser_demo.yaml`) keeps the host gateway, documented in the runbook
  appendix. Moving it needs its own container or a gateway port published for both.
- **The host-side `backend_probe`** cannot reach the in-container gateway. Use the `docker exec` form with
  `--url ws://127.0.0.1:8790/v1/backend` (§4.5).

**Follow-up work:**
- A root `docker-compose.yml` profile through an `include:`, instead of the standalone `-f` file.
- Running as a non-root `user:`, to avoid root-owned logs. The Python install already lives in `/opt/python`.
- A drift-guard test that checks the lock inputs against the `prototypes-voice` extra.
- A Hermes MIT entry in `THIRD_PARTY_NOTICES.txt` and the apt source download, before any image is shared.
- Hash-pinning Hermes's build requirements (`setuptools`, `wheel`).
- Shellcheck in pre-commit for the entrypoint.
- An in-process gateway (one process, no localhost WebSocket), warm workers, aarch64, and a CI image build.

## 8. Review Log

### Review 1 (of Revision 2), Applied in Revision 3

| Finding | Fix (section) |
|---|---|
| `--require-hashes` cannot be combined with a range override | `uv pip sync --require-hashes`; Hermes is installed editable after it (§4.1) |
| `config_hash` changes with `backend.url` | Keep the URL string; map `host.docker.internal` to `127.0.0.1` (§3, §4.3) |
| `fingerprint_check` does not read `config_hash` or compare against a host run | V5 split into V5a, V5b, and V5c (§5) |
| Equal session limits cause capacity errors | Gateway 8, voice 4 (§3, §4.2) |
| "τ³ behavior unchanged" was overclaimed | Reworded; runtime differences disclosed and measured (§1, V5c) |
| `set -e` with `wait -n`, no wait for shutdown, 10 s grace period | Entrypoint rewritten; grace period raised (§4.2, §4.3) |
| V2 could not pass and would skip the real-Hermes test | Mounts, `pytest`, and `FDH_HERMES_PYTHON` added (§5) |
| The documentation deferral breaks `AGENTS.md` | Runbook updated in this PR (§4.5) |
| Shared project name, container name, `.env` interpolation, `<(...)` under dash, `PATH`, build smoke, health check, log names, `grpcio-tools` metadata | §3, §4.1, §4.3, and §4.4 |

### Review 2 (of Revision 3), Applied in Revision 4

Review 2 found no blockers. It verified the earlier B1, B2, and M1 fixes by running them: the lock compile,
`uv pip sync`, `config_hash` under both environments, the `--max-sessions` override, and an entrypoint
simulation.

| Finding | Fix (section) |
|---|---|
| V5a fails on a multi-domain or stale log; V5b had no valid baseline | Per-domain event logs; a new host run at the same commit as the baseline (§5) |
| V2 exits 1 because of the adapter test; the `unittest` step had no command | `--deselect`, then a `unittest` command with the worker's `PYTHONPATH`; pass condition stated (§5) |
| The documentation scope was incomplete | Runbook §1.4, §1.5, §3.1, §5, §6.x, and §9 added; README lines listed; default change stated (§1, §4.5) |
| `uv pip check` could check the wrong interpreter; "allow only the known line" was not spelled out | `--python` plus a POSIX filter; uv pinned (§4.1) |
| No trap during the entrypoint's startup | Trap installed first; `curl -m 2` (§4.2) |
| A 45 s grace period covers only one session | 75 s; V7a with 4 sessions (§4.3, §5) |
| The literal hash values could not be reproduced | Removed; the hash depends on the commit and mount path (§3) |
| Wrong scratch-directory wording; V5c line count | Four differing lines; normalize `sess_*` (§1, §4.2, §5) |
| A relative `HERMES_AGENT_DIR` resolves against `docker/`; a root `down` fails while `fdh-voice` is attached; shared gateway log | Absolute path; documented stop order; distinct log names (§3, §4.3) |
| The build smoke wrote `/root/.hermes` into the image | `HERMES_HOME=/tmp/hsmoke` (§4.1) |
| Nits: file count, `exit 1` in a snippet, unhashed build requirements, `.dockerignore`, `init: true` | §1, §4.1, §4.3, §4.4, §7 |

### Review 3 (of Revision 4), Applied in Revision 5

Review 3 found revision 4 ready to implement after two small fixes. It verified the entrypoint by simulation
(normal stop, a stop during startup, gateway failure at start, gateway killed), `init: true` signal forwarding,
the V2 command, per-domain logs leaving `config_hash` unchanged, every CLI flag, and the runbook and README line
numbers.

| Finding | Fix (section) |
|---|---|
| The build-smoke lines could abort the build every time (`set -e`) or mask a failed import (`&&` chaining) | Three separate `RUN` instructions, `\|\| true` on `uv pip check`, and a check that the known line is present (§4.1) |
| `docker exec … backend_probe` failed with "module not found" | `PYTHONPATH: /app/src` in the Compose environment; `--url ws://127.0.0.1:8790/v1/backend` (§4.3, §4.5, §7) |
| The external network must exist before `run` and `up` | Stated (§4.3) |
| How per-domain `FDH_EVENT_LOG` reaches Compose; `FDH_MAX_SESSIONS` also reaches the gateway | `${VAR:-default}` entries; the `--max-sessions` flag must stay (§4.3) |
| How uv gets into the image; runbook line 541 versus 542; redundant `PYTHONPATH=` with `-I` | `COPY --from=ghcr.io/astral-sh/uv:0.12.3`; line numbers fixed; dropped (§4.1, §4.5) |
