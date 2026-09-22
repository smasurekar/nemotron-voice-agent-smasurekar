# Nemotron Voice Agent — Runbook

Operator guide for bringing this repo up, with the focus on **cloud** and
**single-GPU** deployment profiles, and on serving the **LLM from NVIDIA Inference
Hub** (`https://inference-api.nvidia.com/v1`) using an API key from `.env`.

> **Two different NVIDIA hosted services — do not confuse them:**
>
> | | **NVIDIA Inference Hub** (what we want for the LLM) | **build.nvidia.com / NVCF** (what the repo ships) |
> |---|---|---|
> | LLM base URL | `https://inference-api.nvidia.com/v1` | `https://integrate.api.nvidia.com/v1` |
> | Lightning model id | `nvidia/nvidia/nemotron-3.5-lightning` | `nvidia/nemotron-3.5-lightning-30b-a3b` |
> | Super model id | `nvidia/nvidia/nemotron-3-super-v3` | `nvidia/nemotron-3-super-120b-a12b` |
> | API key format | `sk-...` | `nvapi-...` |
> | Speech (ASR/TTS) | — | `grpc.nvcf.nvidia.com:443` |
>
> The repo's stock `src/examples/<example>/services.cloud.yaml` points at build.nvidia.com. Switching the
> LLM to Inference Hub is a catalog edit — see [§3.4](#34-switching-the-llm-to-nvidia-inference-hub).
> **Read [§3.5](#35-the-one-key-constraint) first**: the repo reads a single
> `NVIDIA_API_KEY` for LLM *and* speech, so an `sk-...` Inference Hub key cannot
> also authenticate the *cloud* NVCF speech endpoints. **Running speech locally
> removes that conflict entirely and needs no code changes** —
> [§4 Lane B](#4-lane-b--local-speech--inference-hub-llm-no-code-changes).

---

## 0. File map — everything this runbook touches

All paths are **relative to the repo root** (`nemotron-voice-agent/`). This
runbook lives at `misc/runbook.md`, so links below step up one level.

### Files you edit

| Path | What it controls | Used in |
|---|---|---|
| [`.env`](../.env.example) | Secrets and runtime toggles. Created from `.env.example` | All lanes |
| [`.env.example`](../.env.example) | Template for the above — never holds real secrets | §3.2, §5.2 |
| [`examples_registry.yaml`](../examples_registry.yaml) | Which example/transport the UI exposes, and the **default** LLM/ASR/TTS catalog key per example | §3.3, §6.2 |
| [`src/examples/generic/services.cloud.yaml`](../src/examples/generic/services.cloud.yaml) | **Cloud** LLM/ASR/TTS endpoints — where the Inference Hub edit goes | §3.4, §6.1 |
| [`src/examples/generic/services.local.yaml`](../src/examples/generic/services.local.yaml) | **Self-hosted** endpoints, split into `server:` and `singlegpu:` sections | §4.5, §6.2 |

Each example has its own pair of catalogs — substitute the directory:

| Example | Cloud catalog | Local catalog |
|---|---|---|
| Generic Assistant | [`src/examples/generic/services.cloud.yaml`](../src/examples/generic/services.cloud.yaml) | [`src/examples/generic/services.local.yaml`](../src/examples/generic/services.local.yaml) |
| Multilingual | [`src/examples/multilingual/services.cloud.yaml`](../src/examples/multilingual/services.cloud.yaml) | [`src/examples/multilingual/services.local.yaml`](../src/examples/multilingual/services.local.yaml) |
| Omni Assistant | [`src/examples/omni_assistant/services.cloud.yaml`](../src/examples/omni_assistant/services.cloud.yaml) | [`src/examples/omni_assistant/services.local.yaml`](../src/examples/omni_assistant/services.local.yaml) |
| Omni Subagents | [`src/examples/omni_assistant_subagents/services.cloud.yaml`](../src/examples/omni_assistant_subagents/services.cloud.yaml) | [`src/examples/omni_assistant_subagents/services.local.yaml`](../src/examples/omni_assistant_subagents/services.local.yaml) |
| Frontend/Backend | [`src/examples/frontend_backend_agent/services.cloud.yaml`](../src/examples/frontend_backend_agent/services.cloud.yaml) | [`src/examples/frontend_backend_agent/services.local.yaml`](../src/examples/frontend_backend_agent/services.local.yaml) |

> **Note the directory names differ from the profile names:** profile
> `omni-assistant` ↔ directory `src/examples/omni_assistant/`, profile
> `frontend-backend-agent` ↔ directory `src/examples/frontend_backend_agent/`
> (hyphens in profiles, underscores in paths).

### Files you run

| Path | Purpose |
|---|---|
| [`docker-compose.yml`](../docker-compose.yml) | Root compose file. Defines every `--profile` recipe and `include`s the sidecar files below |
| [`scripts/download-nemo-speech-models.sh`](../scripts/download-nemo-speech-models.sh) | One-time speech GGUF download into `models/nemo-speech/`. Reads `HF_TOKEN` from `.env`. **Has a known 404 issue — [§9.1](#91-the-pinned-magpie-tts-weight-is-gone-from-hugging-face-404)** |

### Files you read (not edited)

| Path | Why it matters here |
|---|---|
| [`src/utils.py`](../src/utils.py) | `nvidia_api_key()` (§3.5) and the catalog merge / TCP-reachability logic (§4.1) |
| [`src/examples/generic/pipeline.py`](../src/examples/generic/pipeline.py) | Where ASR/LLM/TTS are constructed, and where the `LLM: model=… base_url=…` log line comes from |
| [`docker/docker-compose.nemotron35-lightning.yaml`](../docker/docker-compose.nemotron35-lightning.yaml) | Single-GPU Lightning vLLM sidecar — precision auto-selection and the reasoning/tool parser flags |
| [`docker/docker-compose.nemotron3-omni.yaml`](../docker/docker-compose.nemotron3-omni.yaml) | Single-GPU Omni vLLM sidecar |
| [`docker/docker-compose.nemo-speech-cpp.yaml`](../docker/docker-compose.nemo-speech-cpp.yaml) | Single-GPU ASR+TTS sidecar (`nemo-speech`, `nemo-speech-multilingual`, `nemo-speech-tts`) |
| [`docker/Dockerfile`](../docker/Dockerfile) | What is baked into `nemotron-voice-agent:latest` vs bind-mounted ([§6.2](#62-what-is-mounted-and-what-is-baked)) |
| [`models/nemo-speech/`](../models/) | Destination for the downloaded speech GGUFs (git-ignored) |
| [`examples_registry.yaml`](../examples_registry.yaml) | Visible examples/transports, and the `realtime_models:` profile table the Realtime gateway serves ([§11.1](#111-what-is-gated-which-models-resolve)) |
| [`src/realtime_server.py`](../src/realtime_server.py) | API-only Realtime server — no browser UI ([§11.4](#114-api-only-server)) |
| [`docs/how-to/use-realtime-gateway.md`](../docs/how-to/use-realtime-gateway.md) | Upstream reference for the OpenAI Realtime gateway |

### Companion scripts in this folder

| Path | Purpose |
|---|---|
| [`misc/asr_tts/`](asr_tts/README.md) | Standalone ASR/TTS scripts that call the NVCF speech endpoints directly, with a bundled `sample.wav` ([§8.4](#84-try-it-without-the-pipeline)) |

---

## 1. TL;DR — pick your lane

| Lane | GPU needed | LLM | ASR / TTS | Key(s) |
|---|---|---|---|---|
| **A. Cloud, stock** | **None** | build.nvidia.com | NVCF (cloud) | `NVIDIA_API_KEY=nvapi-...` |
| **A′. Cloud + Inference Hub LLM** | **None** | **Inference Hub** | NVCF (cloud) | ⚠️ needs a key valid for both — see [§3.5](#35-the-one-key-constraint) |
| **B. Local speech + Inference Hub LLM** ← *no code changes* | **~4–8 GB** (see §5.3) | **Inference Hub** | local NeMo-Speech.cpp | `NVIDIA_API_KEY=sk-...` + `HF_TOKEN` |
| **C. Single-GPU, all local** | **≥ 28 GiB free** (cascaded) / **≥ 24–66 GiB** (Omni) | local vLLM | local NeMo-Speech.cpp | `HF_TOKEN` only |

Commands:

```bash
docker compose --profile generic-assistant up -d               # Lane A / A′
docker compose --profile generic-assistant/single-gpu up -d \
  generic-assistant-single-gpu nemo-speech                      # Lane B
docker compose --profile generic-assistant/single-gpu up -d    # Lane C
```

**Want local ASR/TTS with an Inference Hub LLM, and no code changes? That is
Lane B** — see [§4](#4-lane-b--local-speech--inference-hub-llm-no-code-changes)
for the complete recipe. Local speech needs no credential, so `NVIDIA_API_KEY`
is only ever used by the LLM.

> **This machine:** `nvidia-smi` currently fails here (no working NVIDIA driver),
> so **only Lane A** will run as-is on this host. Lanes B and C need a GPU box.

---

## 2. Minimum VRAM requirements

All figures are **VRAM that must be free at container start**, not total card
size. Leave the OS, display, and other processes out of the budget.

### 2.1 Headline minimums

| Profile | Minimum VRAM | Source |
|---|---|---|
| `<example>` (cloud) | **0 GB — CPU only** | [`README.md`](../README.md) hardware table |
| `<example>/single-gpu`, cascaded (Generic / Multilingual / Frontend-Backend) | **28 GiB usable**, any of Blackwell / Ada / Hopper / Ampere | [`docs/how-to/configure-llm.md`](../docs/how-to/configure-llm.md) VRAM table |
| `<example>/single-gpu`, Omni — Blackwell workstation, DGX Spark, Jetson Thor | **24 GiB usable** | same |
| `<example>/single-gpu`, Omni — Ada / Hopper | **36 GiB usable** | same |
| `<example>/single-gpu`, Omni — Ampere | **66 GiB usable** | same |
| Hybrid (local NeMo-Speech.cpp + hosted LLM) | **~4–8 GB** (estimate — measure, see §5.3) | not published; derived |
| `<example>/server` (out of scope here, for contrast) | **80 GB** on one GPU, or 2 × 40 GB | [`docs/how-to/configure-llm.md`](../docs/how-to/configure-llm.md) server table |

**The practical floor for a full local single-GPU cascaded run is a 32 GB card,
and that is tight** — the 28 GiB requirement is checked *after* the NeMo-Speech.cpp
ASR+TTS sidecar has taken its share of the same GPU. A **40–48 GB card
(L40S, A6000 Ada, RTX PRO 6000) is the comfortable floor**; 80 GB (H100/A100) or
DGX Spark / Jetson Thor (128 GB unified) is roomy.

### 2.2 Why 28 GiB

The `*/single-gpu` vLLM services size themselves at startup:

```
gpu_memory_utilization = (free_VRAM - VLLM_VRAM_HEADROOM_MIB) / total_VRAM   # capped at 0.90
```

with `VLLM_VRAM_HEADROOM_MIB` defaulting to 4096. The container aborts if the
resulting usable budget is under the per-platform floor above. DGX Spark and
Jetson Thor Lightning instead pin a fixed `0.35` to protect unified memory.

Precision is chosen automatically from compute capability — there is no knob:

| Hardware | Lightning precision | Omni precision |
|---|---|---|
| Blackwell workstation | NVFP4 + DFlash spec-decode | NVFP4 |
| DGX Spark (GB10) | NVFP4 + DSpark spec-decode | NVFP4 |
| Jetson Thor | NVFP4 | NVFP4 |
| Ada / Hopper | BF16 checkpoint, online FP8 | FP8 |
| Ampere | BF16 | BF16 |
| Older than Ampere | **unsupported → use cloud** | unsupported |

### 2.3 Speech sidecar memory (single-GPU lanes)

The single-GPU speech stack is `nemo-speech.cpp` serving GGUF weights on the same
GPU (`device_ids: ["0"]`):

- ASR `nemotron-speech-streaming-en-0.6b.q8_0.gguf` (0.6B, q8)
- Magpie TTS `magpie_tts_multilingual_357m.v2602.f16.gguf` (357M, f16)
- NanoCodec decoder f16

The repo does **not** publish a VRAM number for this container. Weight sizes put
it in the **~2–4 GB** range, plus CUDA context and per-session buffers → budget
**~4–8 GB** until you measure it on your host (§5.3).

> For contrast, the **NIM** speech sidecars used by `*/server` are far heavier:
> ASR ≈ 15 GB, Magpie TTS ≈ 12.58 GiB, Chatterbox TTS ≈ 44.61 GiB.

---

## 3. Lane A — Cloud (no GPU)

Everything (ASR, LLM, TTS) runs on NVIDIA-hosted endpoints. Only the Pipecat app
runs locally. This is the fastest path and the one that works on this machine.

### 3.1 Prerequisites

- Docker + Docker Compose **v2.20+** (`docker compose version`)
- An API key from [build.nvidia.com](https://build.nvidia.com/)
- No GPU, no `docker login nvcr.io`, no `HF_TOKEN`

### 3.2 Steps

```bash
cd /path/to/nemotron-voice-agent

# 1. Create .env (does not clobber an existing one)
test -f .env || cp .env.example .env

# 2. Put your key in .env.
#    Stock catalog (build.nvidia.com):  NVIDIA_API_KEY=nvapi-...
#    To use Inference Hub for the LLM instead, see 3.4 below.
$EDITOR .env

# 3. Bring up one example (pick exactly one profile)
docker compose --profile generic-assistant            up -d   # baseline English cascaded
docker compose --profile multilingual-assistant       up -d   # multilingual, fixed language/session
docker compose --profile omni-assistant               up -d   # Omni replaces ASR+LLM
docker compose --profile omni-assistant-subagents     up -d   # + image/audio/video/webcam subagents
docker compose --profile frontend-backend-agent       up -d   # talker LLM + airline booking backend

# 4. Check health
docker compose ps
docker compose logs -f
```

Open **`https://<machine-ip>:7860`** (HTTPS is the default because browser mic +
WebRTC require a secure context). Use a wired headset.

`docker compose up` with **no** `--profile` is deliberately a no-op.

### 3.3 Which hosted models Lane A uses out of the box

From `src/examples/generic/services.cloud.yaml` — these are the **stock**
build.nvidia.com entries, before any Inference Hub edit:

| Slot | Default catalog key | Hosted model | Endpoint |
|---|---|---|---|
| LLM | `nemotron-lightning` | `nvidia/nemotron-3.5-lightning-30b-a3b` | `https://integrate.api.nvidia.com/v1` |
| LLM (alt) | `nemotron-super` | `nvidia/nemotron-3-super-120b-a12b` | same |
| LLM (alt, reasoning) | `nemotron-super-reasoning` | `nvidia/nemotron-3-super-120b-a12b` | same |
| ASR | `nemotron-asr-streaming-english` | `nemotron-asr-streaming` | `grpc.nvcf.nvidia.com:443` |
| TTS | `magpie-multilingual-tts` | `magpie-tts-multilingual` | `grpc.nvcf.nvidia.com:443` |

`*-reasoning` variants are the same weights with thinking enabled — see
[§7 Reasoning (thinking) on/off](#7-reasoning-thinking-onoff).

Switch models live from the **Services tab** in the UI, or change the default in
`examples_registry.yaml` (repo root) → `examples.<name>.defaults`.

### 3.4 Switching the LLM to NVIDIA Inference Hub

Edit the `llm:` block of `src/examples/<example>/services.cloud.yaml`. Only
`model_id` and `base_url` change; everything else stays as shipped.

**Model id mapping** — the same weights are named differently on each service:

| Model | Stock (build.nvidia.com) `model_id` | **Inference Hub** `model_id` |
|---|---|---|
| Nemotron 3.5 Lightning 30B A3B | `nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia/nvidia/nemotron-3.5-lightning` |
| Nemotron 3 Super 120B A12B | `nvidia/nemotron-3-super-120b-a12b` | `nvidia/nvidia/nemotron-3-super-v3` |

Both use `base_url: https://inference-api.nvidia.com/v1` and an `sk-...` key. Note
the **doubled `nvidia/nvidia/` prefix** on Inference Hub ids — it is not a typo.

```yaml
# src/examples/generic/services.cloud.yaml   (same file per example:
#   src/examples/{generic,multilingual,omni_assistant,omni_assistant_subagents,frontend_backend_agent}/services.cloud.yaml)
llm:
  nemotron-lightning:
    name: "Nemotron 3.5 Lightning 30B A3B (Inference Hub)"
    model_id: "nvidia/nvidia/nemotron-3.5-lightning"      # Inference Hub id
    base_url: "https://inference-api.nvidia.com/v1"       # Inference Hub endpoint
    system_prompt: ""
    extra_params: '{"extra_body":{"chat_template_kwargs":{"enable_thinking":false},"repetition_penalty":1.05}}'

  nemotron-lightning-reasoning:
    name: "Nemotron 3.5 Lightning 30B A3B (Inference Hub, Reasoning)"
    model_id: "nvidia/nvidia/nemotron-3.5-lightning"
    base_url: "https://inference-api.nvidia.com/v1"
    system_prompt: ""
    extra_params: '{"extra_body":{"chat_template_kwargs":{"enable_thinking":true},"reasoning_budget":16384,"repetition_penalty":1.05}}'

  nemotron-super:
    name: "Nemotron 3 Super 120B A12B (Inference Hub)"
    model_id: "nvidia/nvidia/nemotron-3-super-v3"         # Inference Hub id
    base_url: "https://inference-api.nvidia.com/v1"
    system_prompt: ""
    extra_params: '{"extra_body":{"chat_template_kwargs":{"enable_thinking":false},"repetition_penalty":1.05}}'

  nemotron-super-reasoning:
    name: "Nemotron 3 Super 120B A12B (Inference Hub, Reasoning)"
    model_id: "nvidia/nvidia/nemotron-3-super-v3"
    base_url: "https://inference-api.nvidia.com/v1"
    system_prompt: ""
    extra_params: '{"extra_body":{"chat_template_kwargs":{"enable_thinking":true},"reasoning_budget":16384,"repetition_penalty":1.05}}'
```

> **Super is a good cloud default.** It is the higher-capability model and is
> recommended for cloud deployments — and because it is hosted, its 2 × 80 GB
> self-hosting requirement ([§2.1](#21-headline-minimums)) never applies to you.
> It also covers **Chinese (`zh`)** on top of Lightning's en/de/es/fr/it/ja, and
> stays in the target language more reliably for multilingual agents. The
> trade-off is latency: Lightning is the faster default for spoken turns.

And in `.env`:

```bash
# .env   (repo root)
NVIDIA_API_KEY=sk-...          # Inference Hub key (note: sk-, not nvapi-)
```

Apply it — `./src` is bind-mounted read-only into the container, so edit on the
host and restart:

```bash
docker compose restart generic-assistant
docker compose logs generic-assistant | grep '^.*LLM: model='
```

The pipeline logs the resolved `model=` and `base_url=` on every session start
([`src/examples/generic/pipeline.py`](../src/examples/generic/pipeline.py)), which is the fastest confirmation you are
hitting Inference Hub.

> Do the same edit in the **`singlegpu:` → `llm:`** section of
> `src/examples/<example>/services.local.yaml` for Lane B — see [§4.5](#45-lane-b-catalog-edit-realtime-gateway).

### 3.5 The one-key constraint

**`NVIDIA_API_KEY` is a single shared secret for all three services.** LLM, ASR,
and TTS each call `nvidia_api_key()` from `src/utils.py`:

| Service | Where | Key source |
|---|---|---|
| LLM | `src/examples/generic/pipeline.py:184` → `PipecatNvidiaLLMService(api_key=nvidia_api_key(), ...)` (`:175` for Realtime) | `NVIDIA_API_KEY` |
| ASR | `src/examples/generic/pipeline.py:103` → `asr_kwargs["api_key"] = nvidia_api_key()` | `NVIDIA_API_KEY` |
| TTS | `src/examples/generic/pipeline.py:256` → `tts_kwargs["api_key"] = nvidia_api_key()` | `NVIDIA_API_KEY` |

There is no per-catalog-entry `api_key` field. So **Lane A′ (Inference Hub LLM +
cloud NVCF speech) only works if one key authenticates both services.** If your
`sk-...` key is Inference-Hub-only, the NVCF gRPC speech calls will fail auth.

Three ways out, best first:

1. **Use Lane B** (local NeMo-Speech.cpp speech). `NVIDIA_API_KEY` is then only
   consumed by the LLM, so the `sk-...` key is unambiguous. **Recommended — and
   the only option here that requires no code changes.** See
   [§4](#4-lane-b--local-speech--inference-hub-llm-no-code-changes).
2. **Use Lane C** with the Inference Hub LLM (local speech + local vLLM is
   overkill, but the same key isolation applies).
3. **Patch for two keys** if you genuinely need Inference Hub LLM *and* cloud
   NVCF speech. Minimal change — add an LLM-specific override in
   `src/utils.py`:

   ```python
   # src/utils.py
   def nvidia_llm_api_key(default: str = "not-needed") -> str:
       """LLM-only key; falls back to the shared NVIDIA_API_KEY."""
       return os.getenv("NVIDIA_LLM_API_KEY") or nvidia_api_key(default)
   ```

   then swap `nvidia_api_key()` → `nvidia_llm_api_key()` at the LLM construction
   sites only (`src/examples/*/pipeline.py`), and set both
   `NVIDIA_LLM_API_KEY=sk-...` and `NVIDIA_API_KEY=nvapi-...` in `.env`. This is
   a local modification, not upstream behaviour.

---

## 4. Lane B — Local speech + Inference Hub LLM (no code changes)

**Yes, this is possible with no code changes whatsoever.** ASR and TTS run
locally, the LLM comes from Inference Hub, and the only things you touch are
`.env` and one `base_url`/`model_id` pair in a YAML service catalog — the repo's
designed configuration surface, not source code.

It works because **local speech sidecars need no credential at all**, so
`NVIDIA_API_KEY` is consumed only by the LLM. That dissolves the one-key
constraint in [§3.5](#35-the-one-key-constraint) — there is no second service
competing for the variable.

| | Lane B |
|---|---|
| ASR + TTS | local `nemo-speech.cpp` sidecar — no key |
| LLM | Inference Hub — `NVIDIA_API_KEY=sk-...` |
| Code changes | **none** |
| Config changes | `.env` + one catalog entry |
| GPU | ~4–8 GB free ([§2.3](#23-speech-sidecar-memory-single-gpu-lanes)) |

### 4.1 Why it works without code changes

`src/utils.py` merges `services.cloud.yaml` with `services.local.yaml` and
**TCP-probes each local endpoint**. A reachable local entry shadows the cloud
entry of the same key; an unreachable one falls back to cloud.

So if you start the speech sidecar but **not** the vLLM sidecar:

| Slot | Local entry reachable? | Resolves to |
|---|---|---|
| ASR | yes — `nemo-speech:50051` is up | **local** |
| TTS | yes — same container | **local** |
| LLM | no — `nvidia-llm-vllm:8000` was never started | **cloud catalog** → Inference Hub |

No flag selects this. It is the catalog's own fallback behaviour.

### 4.2 Complete recipe

**Step 1 — `.env` (repo root).** Two values, and note the `sk-` key is for the
LLM only:

```bash
# .env   (repo root)
NVIDIA_API_KEY=sk-...        # Inference Hub — reaches only the LLM in this lane
HF_TOKEN=hf_...              # downloads the speech GGUFs
```

**Step 2 — point the cloud LLM entry at Inference Hub.** This is the one config
edit. In `src/examples/generic/services.cloud.yaml`, change two lines of the
existing `nemotron-lightning` entry:

```yaml
# src/examples/generic/services.cloud.yaml
llm:
  nemotron-lightning:
    name: "Nemotron 3.5 Lightning 30B A3B (Inference Hub)"
    model_id: "nvidia/nvidia/nemotron-3.5-lightning"   # was nvidia/nemotron-3.5-lightning-30b-a3b
    base_url: "https://inference-api.nvidia.com/v1"    # was https://integrate.api.nvidia.com/v1
    system_prompt: ""
    extra_params: '{"extra_body":{"chat_template_kwargs":{"enable_thinking":false},"repetition_penalty":1.05}}'
```

Full model-id mapping, including Super, is in
[§3.4](#34-switching-the-llm-to-nvidia-inference-hub).

**Step 3 — download the speech weights.** One-time, as your user, **not** sudo:

```bash
bash scripts/download-nemo-speech-models.sh
ls -l models/nemo-speech/magpie-tts/*.gguf || echo "TTS weight MISSING"
```

> ⚠️ **Known issue — the script's pinned Magpie TTS weight now 404s and the
> download silently succeeds anyway.** Run the `ls` check above; if it reports
> MISSING, apply the fix in [§9.1](#91-the-pinned-magpie-tts-weight-is-gone-from-hugging-face-404)
> before continuing.

**Step 4 — start the app and the speech sidecar only.** Naming services
explicitly is what leaves the vLLM sidecar out:

```bash
docker compose --profile generic-assistant/single-gpu up -d \
  generic-assistant-single-gpu nemo-speech
```

Per example, the pair of names changes:

| Profile | Start these two | vLLM service to *omit* |
|---|---|---|
| `generic-assistant/single-gpu` | `generic-assistant-single-gpu` `nemo-speech` | `nvidia-llm-vllm-lightning` |
| `multilingual-assistant/single-gpu` | `multilingual-assistant-single-gpu` `nemo-speech-multilingual` | `nvidia-llm-vllm-lightning` |
| `frontend-backend-agent/single-gpu` | `frontend-backend-agent-single-gpu` `nemo-speech` | `nvidia-llm-vllm-lightning` |
| `omni-assistant/single-gpu` | `omni-assistant-single-gpu` `nemo-speech-tts` | `nvidia-llm-vllm-omni` |

> **Omni caveat:** the Omni examples have **no separate ASR** — the Omni model
> *is* the ASR, so in an Omni hybrid the speech-to-text still happens in the
> cloud. Only TTS stays local. For genuinely local ASR, use a cascaded example.

**Step 5 — open** `https://<machine-ip>:7860` and verify with [§4.4](#44-verify-the-llm-really-went-to-inference-hub).

### 4.3 One thing to watch

With `NVIDIA_API_KEY` set, the pipeline attaches
`authorization: Bearer sk-...` to **every** gRPC speech call, including the ones
to your local sidecar (the header is added whenever the variable is non-empty —
see [§8.2](#82-authentication)). `nemo-speech.cpp` has no auth layer and is
expected to ignore the unexpected header, which is what makes this lane work.

**I have not been able to verify that on hardware** — this machine has no working
GPU driver. If the sidecar turns out to reject it, the fallback needs no code
change either: leave `NVIDIA_API_KEY` **unset** and pass the key to the LLM
through a per-session override, or run Lane C. Check
`docker compose logs nemo-speech` for auth errors on your first turn.

### 4.4 Verify the LLM really went to Inference Hub

```bash
curl -sk https://localhost:7860/api/services | python3 -m json.tool | head -40
docker compose logs generic-assistant-single-gpu | grep 'LLM: model='
```

The `llm` entry's `base_url` should read `https://inference-api.nvidia.com/v1` and
`model_id` should be `nvidia/nvidia/nemotron-3.5-lightning`. If it reads
`http://nvidia-llm-vllm:8000/v1`, a local vLLM container is running — stop it.

### 4.5 Lane B catalog edit (Realtime gateway)

The `WS /v1/realtime` gateway does **not** use reachability fallback. It pins one
catalog section via `REALTIME_SERVICE_PLATFORM` (`cloud` | `server` | `singlegpu`),
and the compose profile sets it to `singlegpu`. For a hybrid Realtime deployment,
edit the **`singlegpu:` LLM entry** in the example's
`src/examples/<example>/services.local.yaml`:

```yaml
# src/examples/generic/services.local.yaml
singlegpu:
  llm:
    nemotron-lightning:
      name: "Nemotron 3.5 Lightning 30B A3B (Inference Hub)"
      model_id: "nvidia/nvidia/nemotron-3.5-lightning"
      base_url: "https://inference-api.nvidia.com/v1"   # was http://nvidia-llm-vllm:8000/v1
      supports_tokenize: false                          # hosted endpoint: no /tokenize
      realtime_max_output_tokens: 2048
      system_prompt: ""
      extra_params: '{"extra_body":{"chat_template_kwargs":{"enable_thinking":false},"repetition_penalty":1.05}}'
```

`src/examples/*/services.local.yaml` is **not** bind-mounted read-only into the
app container by itself — it lives under `./src`, which *is* mounted `:ro`, so
edit it on the host and `docker compose restart <app-service>`.

---

## 5. Lane C — Single-GPU, fully local

Everything on one GPU: vLLM (Lightning or Omni) + NeMo-Speech.cpp. No
`NVIDIA_API_KEY`, no NGC login — **`HF_TOKEN` only**.

### 5.1 Prerequisites

- One supported GPU meeting the §2.1 minimum, **or** DGX Spark / Jetson Thor
- NVIDIA Container Toolkit working (`docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`)
- Python 3 and `hf` CLI (or `uvx`), plus `HF_TOKEN` in `.env`
- **~50 GiB free disk** and **≥ 32 GiB system RAM** (NIM/vLLM images and weights are large)

### 5.2 Steps

```bash
test -f .env || cp .env.example .env
$EDITOR .env                                  # set HF_TOKEN=hf_...

bash scripts/download-nemo-speech-models.sh   # one-time, as your user, not sudo
ls -l models/nemo-speech/magpie-tts/*.gguf || echo "TTS weight MISSING"  # see 8.1

docker compose --profile generic-assistant/single-gpu        up -d
# or: multilingual-assistant/single-gpu
#     omni-assistant/single-gpu
#     omni-assistant-subagents/single-gpu    (workstation / DGX Spark only — NOT Jetson Thor)
#     frontend-backend-agent/single-gpu

docker compose ps
docker compose logs -f nvidia-llm-vllm-lightning
```

**First run takes 30–60 minutes** (image pulls + weight download + engine build).
The **first voice turn** is also slow while sidecars warm up; later turns are fast.

### 5.3 Memory-fit procedure (do this before blaming OOM)

Never budget against the advertised card/unified size. Measure:

```bash
# 1. Baseline, nothing running
nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader
awk '/MemTotal|MemAvailable/' /proc/meminfo     # unified memory hosts: MemAvailable is the real cap

# 2. Start ONLY the speech sidecar, wait until warm, measure again.
#    The delta is your real speech reservation (this also answers §2.3).
docker compose --profile generic-assistant/single-gpu up -d nemo-speech
nvidia-smi --query-gpu=memory.free --format=csv,noheader

# 3. Remaining budget:
#    safe_vllm = min(cuda_free, MemAvailable) - speech_measured - operational_headroom
#    utilization = safe_vllm / cuda_TOTAL        <- fraction of TOTAL, never > 0.90
```

Only two `.env` knobs are supported; prefer the first:

| Knob | Default | Use when |
|---|---|---|
| `VLLM_VRAM_HEADROOM_MIB` | `4096` | You need more memory left for TTS/OS. Raise it. |
| `VLLM_GPU_MEMORY_UTILIZATION` | auto | Only when an explicit fraction is required. Round down. |

On DGX Spark / Jetson Thor, Lightning ignores both and uses a fixed `0.35`.

A healthy container is not proof of fit — complete a spoken turn while watching
memory. On OOM, lower utilization in `0.05` steps and retry.

---

## 6. Building and rebuilding the image

Every lane starts with `docker compose up -d`, which quietly does double duty: it
builds the app image the first time and then never rebuilds it again.

### 6.1 When Compose actually builds

[`docker-compose.yml`](../docker-compose.yml) gives each app service a `build:`
context of the repo root with [`docker/Dockerfile`](../docker/Dockerfile), tagged
`nemotron-voice-agent:latest`. Compose builds that image **only when it is
absent**:

| Situation | `docker compose up -d` (no flag) |
|---|---|
| Image absent — first run, or after `docker rmi` | **builds** from the working tree |
| Image present, source changed | **reuses the stale image, silently** |
| Image present, `--build` passed | rebuilds |

Nothing else triggers a rebuild — there is no `pull_policy: build` in the compose
file. Verified: touching `src/server.py` and re-running `up -d` reports only
`Container … Running`, with no build step and an unchanged image id.

### 6.2 What is mounted and what is baked

This is what makes a stale image confusing rather than obviously broken — half the
repo updates live, half does not:

| Path | How it reaches the container | Takes effect on |
|---|---|---|
| [`src/`](../src) | bind mount `:ro` → `/app/src` | `docker compose restart <app-service>` |
| [`examples_registry.yaml`](../examples_registry.yaml) | bind mount `:ro` | `docker compose restart <app-service>` |
| [`client/`](../client) (browser UI) | baked — built in the `client-builder` stage, copied to `/app/client/dist` | **rebuild** |
| [`pyproject.toml`](../pyproject.toml) + [`uv.lock`](../uv.lock) | baked — `uv sync --frozen` into `/app/.venv` | **rebuild** |
| `benchmarking_tools/scaling-perf/` | baked | **rebuild** |

So every catalog edit in this runbook — `services.cloud.yaml`,
`services.local.yaml`, `examples_registry.yaml` — needs only a restart, because
all of them sit under a bind mount. A `git pull` that touches the UI or the
dependency set needs a rebuild, and will otherwise leave you running new Python
against an old UI with no error to explain it.

### 6.3 Rebuilding

```bash
# Rebuild and restart in one step — same service list as your lane
docker compose --profile generic-assistant/single-gpu up -d --build \
  generic-assistant-single-gpu nemo-speech

# Build only, leaving running containers alone
docker compose --profile generic-assistant/single-gpu build generic-assistant-single-gpu

# Ignore the layer cache (dependency or base-image weirdness)
docker compose --profile generic-assistant/single-gpu build --no-cache generic-assistant-single-gpu
```

The first build takes several minutes (`npm ci`, `npm run build`, `uv sync`).
Later rebuilds reuse cached layers unless `pyproject.toml`, `uv.lock`, or
`client/package-lock.json` changed.

**After every `git pull`, rebuild.** It is nearly free when nothing relevant
changed, and it is the only thing that keeps the UI and dependencies in step with
the live-mounted `src/`.

> **Which image am I actually running?**
>
> ```bash
> docker image inspect nemotron-voice-agent:latest --format '{{.Id}} {{.Created}}'
> docker compose images
> ```

---

## 7. Reasoning (thinking) on/off

Nemotron models support a chain-of-thought "thinking" mode. It is **not** a
global switch — it is a per-catalog-entry request parameter, so you can expose a
reasoning-on and a reasoning-off variant of the *same weights* side by side and
switch between them per session.

> **Default and recommendation: reasoning OFF for spoken pipelines.** Thinking
> tokens are generated before any answer text, so they land directly on
> time-to-first-spoken-token. Turn it ON only for complex tool/agent tasks where
> the quality gain outweighs a noticeably longer silence before the bot replies.

### 7.1 The knob

Reasoning is controlled by `chat_template_kwargs.enable_thinking` inside the
`extra_params` JSON string on an `llm:` catalog entry. `extra_params` is merged
into every chat-completion request; anything under `extra_body` is passed through
to vLLM/NIM as a provider extension.

```yaml
# src/examples/generic/services.cloud.yaml   (Lane A/B)
# src/examples/generic/services.local.yaml   (Lane C, under `singlegpu:`)
llm:
  # Reasoning OFF — lowest latency (the shipped default)
  nemotron-lightning:
    name: "Nemotron 3.5 Lightning 30B A3B"
    model_id: "nvidia/nvidia/nemotron-3.5-lightning"
    base_url: "https://inference-api.nvidia.com/v1"
    extra_params: '{"extra_body":{"chat_template_kwargs":{"enable_thinking":false},"repetition_penalty":1.05}}'

  # Reasoning ON — better on complex tasks, higher time-to-first-response
  nemotron-lightning-reasoning:
    name: "Nemotron 3.5 Lightning 30B A3B (Reasoning)"
    model_id: "nvidia/nvidia/nemotron-3.5-lightning"
    base_url: "https://inference-api.nvidia.com/v1"
    extra_params: '{"extra_body":{"chat_template_kwargs":{"enable_thinking":true},"reasoning_budget":16384,"repetition_penalty":1.05}}'
```

| Field | Where | Effect |
|---|---|---|
| `chat_template_kwargs.enable_thinking` | `extra_body` | `false` = off, `true` = on |
| `reasoning_budget` | `extra_body` | Max thinking tokens when on. Repo ships `16384`. Lower it (e.g. `2048`) to cap the pre-answer delay |

Note the two entries share one `model_id` — **same weights, different request
parameters**. Nothing is redeployed when you switch.

### 7.2 Three ways to switch

**a. Per session, from the UI (no restart).** Open the **Services tab** and pick
the `(Reasoning)` variant of the LLM. This is the fastest way to A/B the latency
cost on your own hardware.

**b. Change the default for an example.** Edit `examples_registry.yaml` (repo root):

```yaml
# examples_registry.yaml   (repo root)
examples:
  generic-assistant:
    defaults:
      llm: [nemotron-lightning-reasoning]    # was nemotron-lightning
```

Restart the app service to pick it up:

```bash
docker compose restart generic-assistant
```

**c. Flip the entry itself.** Edit `enable_thinking` in the relevant catalog file
and restart. Which file depends on the lane:

| Lane | File | Section |
|---|---|---|
| A / A′ (cloud) | `src/examples/<example>/services.cloud.yaml` | `llm:` |
| B (hybrid) | `src/examples/<example>/services.cloud.yaml`; for Realtime also `src/examples/<example>/services.local.yaml` | `llm:` / `singlegpu: → llm:` |
| C (single-GPU) | `src/examples/<example>/services.local.yaml` | `singlegpu: → llm:` |

Which catalog keys exist per example is listed in `examples_registry.yaml`
(repo root). The shipped pairs are:

| Off | On | Inference Hub `model_id` for both |
|---|---|---|
| `nemotron-lightning` | `nemotron-lightning-reasoning` | `nvidia/nvidia/nemotron-3.5-lightning` |
| `nemotron-super` | `nemotron-super-reasoning` | `nvidia/nvidia/nemotron-3-super-v3` |

Each pair shares one `model_id` — only `enable_thinking` differs. The Omni entry
(`nemotron-omni-nvfp4`) has no reasoning variant in the stock catalogs; add one
with the same `model_id` and `enable_thinking:true` if you want it.

### 7.3 Self-hosted: the reasoning parser is mandatory

On hosted endpoints the parsers run server-side. **Self-hosted vLLM/NIM do not
enable them by default**, so Lane C (and any local sidecar) needs:

| Flag | Why |
|---|---|
| `--reasoning-parser nemotron_v3` | Splits `<think>…</think>` out of `content` into a separate reasoning field |
| `--enable-auto-tool-choice --tool-call-parser qwen3_coder` | OpenAI-style function calling; without it `tool_choice:"auto"` returns HTTP 400 |

The repo already sets these — raw vLLM gets them on `vllm serve` in
[`docker/docker-compose.nemotron35-lightning.yaml`](../docker/docker-compose.nemotron35-lightning.yaml),
and NIM gets them via
`NIM_PASSTHROUGH_ARGS`. **Do not strip them from a Compose override.**

Without the reasoning parser, `enable_thinking:false` still works but thinking
text (when on) leaks into `content` — and the TTS will read the model's internal
monologue out loud. That is the classic symptom.

### 7.4 Verifying which mode is live

```bash
docker compose logs <app-service> | grep 'LLM: model='
```

The pipeline logs `model=`, `base_url=`, and the full `extra_params=` on every
session start, so the `enable_thinking` value in effect is visible there.

Behavioural check: ask something that needs a couple of reasoning steps
("if I leave at 3pm and the flight is 5 hours, what time do I land in a zone
2 hours ahead?"). With reasoning ON you get a longer pause before speech starts
and a better answer; with it OFF, faster speech and more direct reasoning errors.

---

## 8. How ASR and TTS are wired (and what auth they need)

The LLM is plain OpenAI-compatible HTTP. **Speech is not** — ASR and TTS are
**gRPC over TLS**, spoken to with the Riva protocol, which is why the catalog
entries look so different from the `llm:` ones.

### 8.1 Anatomy of a speech catalog entry

```yaml
# src/examples/generic/services.cloud.yaml
tts:
  magpie-multilingual-tts:
    server: "grpc.nvcf.nvidia.com:443"                     # gRPC host:port, not a URL
    voice_id: "Magpie-Multilingual.EN-US.Aria"             # voice within the model
    model: "magpie-tts-multilingual"
    function_id: "877104f7-e885-42b9-8de8-f6e4c6303969"    # which NVCF function to invoke
    synthesis_mode: stitched                               # stitched | per_sentence
```

| Field | What it does |
|---|---|
| `server` | gRPC endpoint. `grpc.nvcf.nvidia.com:443` = NVIDIA Cloud Functions; `nemo-speech:50051` = local sidecar |
| `function_id` | **Routing, not a secret.** The NVCF function UUID that identifies the hosted model. Empty for self-hosted |
| `model` | Model name reported to the service |
| `voice_id` | TTS only — the speaker |
| `synthesis_mode` | TTS only. `stitched` reuses one stream across sentences (Magpie); `per_sentence` opens one call per sentence (Chatterbox) |

### 8.2 Authentication

There is no API key in the YAML. Auth happens in **gRPC metadata**, assembled in
pipecat's `_initialize_client` (`pipecat/services/nvidia/tts.py`, and the STT twin):

```python
metadata = [
    ["function-id",   "877104f7-e885-42b9-8de8-f6e4c6303969"],
    ["authorization", f"Bearer {NVIDIA_API_KEY}"],
]
auth = riva.client.Auth(None, use_ssl, "grpc.nvcf.nvidia.com:443", metadata)
```

So, concretely:

- **`NVIDIA_API_KEY` is the credential**, the same variable the LLM uses
  ([§3.5](#35-the-one-key-constraint)), sent as a gRPC bearer token.
- **It must be an `nvapi-...` key** from build.nvidia.com. An Inference Hub
  `sk-...` key is rejected with gRPC `PERMISSION_DENIED`.
- **An unset key is not the same as a bad key.** When `NVIDIA_API_KEY` is empty,
  `nvidia_api_key()` returns the sentinel `"not-needed"` and **no `authorization`
  header is sent at all**. Verified against the live endpoints: the NVCF speech
  functions currently serve those anonymous requests from the `function-id`
  alone. A *wrong* key fails where *no* key succeeds.

  | `NVIDIA_API_KEY` | Sent | ASR / TTS result |
  |---|---|---|
  | unset | no auth header | works |
  | valid `nvapi-...` | `Bearer nvapi-…` | works — the supported path |
  | wrong family or stale | `Bearer <junk>` | `PERMISSION_DENIED` |

  Anonymous access is undocumented and presumably rate-limited. **Set a real key
  for anything past a smoke test**, and never leave a stale one in `.env`.
- **TLS is inferred, not configured.** `src/utils.py::is_nvcf` returns true for
  any server containing `nvcf.nvidia.com`, and that becomes `use_ssl`.
- **Self-hosted sidecars need no key.** `function_id` is `""`, no bearer token is
  attached, and TLS is off — the same code path with empty metadata.

### 8.3 Where it happens in the pipeline

[`src/examples/generic/pipeline.py`](../src/examples/generic/pipeline.py) builds
both services from the resolved catalog entry:

| Step | Line | What |
|---|---|---|
| ASR kwargs (`api_key`, `server`, `use_ssl`) | ~100 | `asr_ssl = is_nvcf(asr_server)` |
| `model_function_map` for ASR | ~110 | `{"function_id": …, "model_name": …}` |
| `NvidiaSTTService(**asr_kwargs, stop_history=400)` | ~126 | `stop_history` tunes end-of-utterance |
| TTS kwargs + `NvidiaTTSSettings(voice, synthesis_mode, language)` | ~234 | plus `NemotronSpeechTextFilter` and the IPA dictionary |
| `NvidiaTTSService(**tts_kwargs)` | ~269 | |

Audio flows: mic → VAD/turn detection → **ASR** → LLM → **TTS** → speaker. TTS
streams via `synthesize_online`, so speech starts before the full reply exists —
the same call [`src/examples/shared/prewarm.py`](../src/examples/shared/prewarm.py)
uses to warm the service at connect time.

### 8.4 Try it without the pipeline

[`misc/asr_tts/`](asr_tts/README.md) has standalone scripts that call these exact
endpoints — useful for validating a key or comparing models:

```bash
# Use the repo venv — these deps are not in system Python
.venv/bin/python misc/asr_tts/asr_transcribe.py                      # bundled speech sample
.venv/bin/python misc/asr_tts/tts_synthesize.py --text "Hello." --out out.wav
.venv/bin/python misc/asr_tts/asr_transcribe.py --audio out.wav      # round trip
```

They reuse the repo's own catalog and auth logic, and translate gRPC failures
into plain causes. See [`misc/asr_tts/README.md`](asr_tts/README.md).

---

## 9. Known issues

### 9.1 The pinned Magpie TTS weight is gone from Hugging Face (404)

**Affects:** every `*/single-gpu` recipe — both [Lane B](#4-lane-b--local-speech--inference-hub-llm-no-code-changes)
and [Lane C](#5-lane-c--single-gpu-fully-local). Cloud lanes are unaffected.

[`scripts/download-nemo-speech-models.sh`](../scripts/download-nemo-speech-models.sh)
pins Magpie TTS at version **`v2602`**, but NVIDIA has since republished the repo
with **`v2607`**. Verified against Hugging Face:

| File | HTTP |
|---|---|
| `magpie_tts_multilingual_357m.v2602.f16.gguf` | **404 — gone** |
| `magpie_tts_multilingual_357m.v2607.f16.gguf` | 200 |

**Why it is easy to miss — reproduced here.** `hf download --include <pattern>`
that matches nothing still exits `0`:

```console
$ hf download nvidia/magpie_tts_multilingual_357m \
    --include magpie_tts_multilingual_357m.v2602.f16.gguf --local-dir ./magpie-tts
path=./magpie-tts
$ echo $?
0
$ find ./magpie-tts -type f
./magpie-tts/.cache/huggingface/trees/1980…json      # metadata only, no .gguf
```

The script then prints `Models ready at …` with the TTS weight absent. Nothing
complains until `nemo-speech` starts and cannot open `--tts.magpie-model`.

The version is hard-coded in four places:

| File | Line(s) |
|---|---|
| [`scripts/download-nemo-speech-models.sh`](../scripts/download-nemo-speech-models.sh) | 101 |
| [`docker/docker-compose.nemo-speech-cpp.yaml`](../docker/docker-compose.nemo-speech-cpp.yaml) | 28, 44, 58 |

**Symptoms**

- `docker compose logs nemo-speech` shows a missing/unopenable model file, or the
  container restart-loops
- `ls models/nemo-speech/magpie-tts/` has `magpie_tts_multilingual_357m.nemo` and
  `extracted/`, but **no `.f16.gguf`**
- The pipeline starts, ASR transcribes, and the bot never speaks

**Verify before you deploy:**

```bash
ls -l models/nemo-speech/magpie-tts/*.gguf || echo "TTS weight MISSING"
```

#### Recommended fix — bump the pin to v2607

Cleanest and permanent. Repoint all four references, then re-run the download.
**Verified working** — see [§9.2](#92-verification-of-the-v2607-fix) for the
evidence:

```bash
# From the repo root
sed -i 's/magpie_tts_multilingual_357m\.v2602\.f16\.gguf/magpie_tts_multilingual_357m.v2607.f16.gguf/g' \
  scripts/download-nemo-speech-models.sh \
  docker/docker-compose.nemo-speech-cpp.yaml

# Confirm all four changed
grep -rn "v260[27]" scripts/download-nemo-speech-models.sh docker/docker-compose.nemo-speech-cpp.yaml

bash scripts/download-nemo-speech-models.sh
ls -l models/nemo-speech/magpie-tts/*.gguf      # must now exist
```

This edits two tracked files, so it shows up in `git diff`. Worth raising
upstream — the root cause is a pinned filename with no fallback.

#### Alternative — no tracked files touched

If you need the working tree clean (for example you are running the strict
no-code-changes [Lane B](#4-lane-b--local-speech--inference-hub-llm-no-code-changes)),
download `v2607` and present it under the name the compose file expects:

```bash
hf download nvidia/magpie_tts_multilingual_357m \
  --include magpie_tts_multilingual_357m.v2607.f16.gguf \
  --local-dir models/nemo-speech/magpie-tts

cd models/nemo-speech/magpie-tts
ln -sf magpie_tts_multilingual_357m.v2607.f16.gguf \
       magpie_tts_multilingual_357m.v2602.f16.gguf
```

The bind-mount is read-only *inside* the container, but the symlink is created on
the host beforehand, so the sidecar resolves it normally. Use `cp` instead of
`ln -sf` if you would rather not rely on symlink resolution through the mount.

> **`HF_TOKEN` is optional for these repos.** All four are public; the CLI warns
> `You are sending unauthenticated requests to the HF Hub` and proceeds. A token
> only raises rate limits and download speed. The script still sources `.env`, so
> set `HF_TOKEN` there if downloads are throttled.

> **Check the version before assuming either fix.** NVIDIA may republish again.
> List what actually exists:
>
> ```bash
> curl -s "https://huggingface.co/api/models/nvidia/magpie_tts_multilingual_357m" \
>   | python3 -c "import json,sys; [print(s['rfilename']) for s in json.load(sys.stdin)['siblings'] if s['rfilename'].endswith('.gguf')]"
> ```

---

### 9.2 Verification of the v2607 fix

Both the failure and the fix were reproduced on 2026-09-18, against the live
Hugging Face repos, using the real script (patched on a scratch copy — the repo
working tree was not modified).

**The bug:**

| Pattern | `hf download` exit | `.gguf` written |
|---|---|---|
| `…v2602.f16.gguf` (as shipped) | **0** | **none** — only cache metadata |
| `…v2607.f16.gguf` (fixed) | 0 | 543 MB file |

**The fix, end to end.** The patched script ran to completion and printed
`Models ready at …`. All four weights the `nemo-speech` sidecar opens were
present afterwards:

| File the sidecar opens | Size | Status |
|---|---|---|
| `nemotron-speech-streaming-en-0.6b.q8_0.gguf` | 668 M | OK |
| `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` | 708 M | OK |
| `magpie-tts/magpie_tts_multilingual_357m.v2607.f16.gguf` | 543 M | **OK — was missing before the fix** |
| `nano-codec/nemo_nano_codec_22khz_1.89kbps_21.5fps.decoder.f16.gguf` | 76 M | OK |

The `.nemo` tar extraction into `magpie-tts/extracted/` also succeeded
(pronunciation dictionaries, heteronyms, speaker JSON), so the `v2607` archive is
layout-compatible with what the sidecar expects.

**Measured disk footprint** — `models/nemo-speech/` totals **4.7 GB**:

| Path | Size |
|---|---|
| `magpie-tts/` (gguf + `.nemo` + `extracted/`) | 3.3 G |
| `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` | 708 M |
| `nemotron-speech-streaming-en-0.6b.q8_0.gguf` | 668 M |
| `nano-codec/` | 76 M |

Add roughly the same again for `~/.cache/huggingface`, so budget **~9 GB** for a
first download. Both ASR models are fetched regardless of which example you run.

> **Not verified:** that `nemo-speech` actually *loads* `v2607` at runtime. That
> needs a GPU, and this machine has no working NVIDIA driver. The file downloads,
> is the expected size, and its `.nemo` extracts correctly — but confirm the
> sidecar starts on your GPU host before treating the fix as closed.

---

## 10. Post-deploy verification (all lanes)

```bash
docker compose ps                                  # all services healthy?
curl -fk https://localhost:7860/health             # app health endpoint
curl -sk https://localhost:7860/api/services       # which catalog entries resolved
```

Then in the browser at `https://<machine-ip>:7860`:

1. Accept the self-signed cert warning (expected — `PIPELINE_TLS=true` by default).
2. Grant microphone permission.
3. The bot greets you first (`ENABLE_WELCOME_MESSAGE`, default on).
4. Say something; check the transcript panel and that audio comes back.
5. Interrupt mid-reply — it should stop speaking promptly (VAD + Smart Turn).
6. Open the **Services tab** and confirm the LLM row shows the endpoint you expect.

Useful toggles in `.env`:

| Variable | Default | Notes |
|---|---|---|
| `PIPELINE_TLS` | `true` | `false` = plain HTTP; headless/API testing only, browser mic will not work remotely |
| `PIPELINE_APP_PORT` | `7860` | Host port |
| `EXAMPLE_SELECTION` | per-profile | `all` exposes every example in the UI selector |
| `CHAT_HISTORY_RECENT_TURNS` | `10` | Non-prompt turns retained |
| `ENABLE_ASR_AUDIO_DUMP` / `ENABLE_TTS_AUDIO_DUMP` | `false` | Writes to `AUDIO_DUMP_PATH` for debugging |
| `ENABLE_TRACING` | `false` | Pair with `--profile tracing` for Phoenix OTel |
| `TURN_*` | — | Pair with `--profile turn` when clients are behind NAT |

Overlay profiles compose with any recipe:

```bash
docker compose --profile generic-assistant --profile tracing up -d
docker compose --profile generic-assistant --profile turn    up -d
```

---

## 11. OpenAI Realtime API gateway

**There is nothing to enable.** `src/server.py` registers the Realtime routes
unconditionally — no feature flag, no separate port, no extra compose profile.
The same process and port that serve the browser UI also serve the gateway.

| Route | Registered at | Purpose |
| --- | --- | --- |
| `WS /api/ws` | [`src/server.py`](../src/server.py) | Pipecat RTVI — what the bundled web UI uses |
| `WS /v1/realtime` | [`src/server.py:1612`](../src/server.py#L1612) | OpenAI Realtime–compatible session |
| `POST /v1/realtime/client_secrets` | [`src/server.py:1498`](../src/server.py#L1498) | Mints short-lived `ek_` client secrets |

Port is `7860`, published by [`docker-compose.yml:61`](../docker-compose.yml#L61)
as `${PIPELINE_APP_PORT:-7860}:7860`. With the `generic-assistant` profile up:

```
wss://localhost:7860/v1/realtime?model=nvidia%2Fnemotron-realtime
```

Use `wss://`, not `ws://` — `PIPELINE_TLS` defaults to `true`
([`docker-compose.yml:94`](../docker-compose.yml#L94)) with a self-signed cert, so
clients need `-k` / `rejectUnauthorized: false`. Set `PIPELINE_TLS=false` only for
an isolated local test.

### 11.1 What *is* gated: which models resolve

The gateway filters Realtime profiles by the **visible pipeline set**, i.e. by
`EXAMPLE_SELECTION`. Profiles are declared in
[`examples_registry.yaml:28-88`](../examples_registry.yaml). The compose
`generic-assistant` profile pins `EXAMPLE_SELECTION=generic-assistant`
([`docker-compose.yml:105`](../docker-compose.yml#L105)), so exactly two models
resolve — verified by running the resolver:

```
EXAMPLE_SELECTION=generic-assistant
  visible realtime models: nvidia/nemotron-realtime, nvidia/nemotron-realtime-client-tools
  default model:           nvidia/nemotron-realtime
  nvidia/nemotron-realtime-omni → RealtimeModelProfileNotAvailable
```

`model` is **optional**: omit it and you get the pipeline's unique default
(`default: true` at [`examples_registry.yaml:32`](../examples_registry.yaml#L32)).
Clients cannot pass URLs, credentials, or arbitrary provider model ids — a profile
binds pipeline + prompt + catalog selectors server-side. Use
`session.instructions` to customize behavior; a live `session.update` cannot
change the model profile or its service route.

| Realtime model id | Pipeline | Needs `EXAMPLE_SELECTION` to include |
| --- | --- | --- |
| `nvidia/nemotron-realtime` (default) | Generic, trusted server tools | `generic-assistant` |
| `nvidia/nemotron-realtime-client-tools` | Generic, client-owned functions | `generic-assistant` |
| `nvidia/nemotron-realtime-multilingual` | Multilingual | `multilingual-assistant` |
| `nvidia/nemotron-realtime-omni` | Direct Omni | `omni-assistant` |
| `nvidia/nemotron-realtime-omni-subagents` | Omni multi-agent | `omni-assistant-subagents` |
| `nvidia/nemotron-realtime-frontend-backend` | Frontend/Backend | `frontend-backend-agent` |

### 11.2 Two settings worth knowing

| Setting | Default | Notes |
| --- | --- | --- |
| `REALTIME_API_KEY` | **unset** | Unset means **the WebSocket is unauthenticated**. Fine on a private box; set it for anything reachable. Read at [`src/realtime/auth.py:58`](../src/realtime/auth.py#L58); template at [`.env.example:12`](../.env.example). Send the master key or an `ek_` secret as Bearer; never ship the master key to a browser. |
| `REALTIME_SERVICE_PLATFORM` | `cloud` | Pins the catalog section (`cloud` \| `server` \| `singlegpu`). Compose sets it per recipe — `cloud` / `server` / `singlegpu` at [`docker-compose.yml:95,113,122`](../docker-compose.yml#L95). This is what makes the gateway follow your Lane A / Lane C choice automatically. |

`REALTIME_MCP_ALLOWED_SERVER_URLS` is a JSON array of exact, trusted Streamable
HTTP MCP URLs the gateway may contact; empty by default.

### 11.3 Lane B caveat

`registry-default` resolves **within** the pinned `REALTIME_SERVICE_PLATFORM`
section and does **not** probe endpoints. Lane B relies on the catalog's TCP
reachability fallback, so a Realtime client on a Lane B setup will *not* inherit
the shadowing the RTVI path gets — make the explicit catalog edit in
[§4.5](#45-lane-b-catalog-edit-realtime-gateway). *(Not verified on hardware; no
GPU driver on the authoring machine.)*

### 11.4 API-only server

To serve the Realtime API without the browser UI:

```bash
uv run python src/realtime_server.py --host 0.0.0.0 --port 7860 --workers 4
```

[`src/realtime_server.py`](../src/realtime_server.py) exposes only `GET /health`
and the two Realtime routes. `/health` proves process liveness only — it does not
prove ASR/LLM/TTS are ready; the first actionable Realtime event checks those.

Upstream reference: [`docs/how-to/use-realtime-gateway.md`](../docs/how-to/use-realtime-gateway.md).
Scope is WebSocket only — WebRTC, SIP, sideband control, and OpenAI-managed MCP
connectors are not implemented.

---

## 12. Pointing the LLM at any other OpenAI-compatible endpoint

Any OpenAI-compatible URL works. Two places to change, depending on lane:

| Lane | File | Section |
|---|---|---|
| Cloud | `src/examples/<example>/services.cloud.yaml` | `llm:` |
| Single-GPU / hybrid | `src/examples/<example>/services.local.yaml` | `singlegpu: → llm:` |

```yaml
# src/examples/<example>/services.cloud.yaml
llm:
  my-hosted-llm:
    name: "My Hosted Nemotron"
    model_id: "nvidia/nemotron-3.5-lightning-30b-a3b"
    base_url: "https://my-gateway.example.com/v1"
    system_prompt: ""
    extra_params: '{"temperature":0.6,"top_p":0.95,"max_tokens":1024,"extra_body":{"repetition_penalty":1.05,"chat_template_kwargs":{"enable_thinking":false}}}'
```

Then make it the default in `examples_registry.yaml` (repo root):

```yaml
# examples_registry.yaml   (repo root)
examples:
  generic-assistant:
    defaults:
      llm: [my-hosted-llm]
```

**Model id is provider-specific.** The same weights are named differently per
service — see the mapping table in
[§3.4](#34-switching-the-llm-to-nvidia-inference-hub). Copy the id from your
provider's model card; a wrong id surfaces as HTTP 404 on the first turn.

**Auth:** the bearer token always comes from `NVIDIA_API_KEY` in `.env`
(`src/utils.py::nvidia_api_key`, defaulting to `not-needed` for unauthenticated
local endpoints). There is no per-entry `api_key` field, and the same value is
also sent to ASR and TTS — see [§3.5](#35-the-one-key-constraint).

**Restart after any catalog edit:**

```bash
docker compose restart generic-assistant     # or the app service for your profile
```

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up` does nothing | No `--profile` given | Profiles are mandatory and mutually exclusive — pass exactly one recipe |
| `HTTP 429` from the LLM | Hosted-endpoint rate limit | Back off, or self-host (Lane C) |
| `HTTP 401/403` on the LLM | Wrong key family for the endpoint — Inference Hub wants `sk-...`, build.nvidia.com wants `nvapi-...` | Match key to `base_url` ([§3.4](#34-switching-the-llm-to-nvidia-inference-hub)) |
| `HTTP 404` / model not found on the LLM | Model id does not match the provider | Check the mapping table in [§3.4](#34-switching-the-llm-to-nvidia-inference-hub). Inference Hub ids carry a doubled `nvidia/nvidia/` prefix (`…/nemotron-3.5-lightning`, `…/nemotron-3-super-v3`) |
| LLM works but ASR/TTS fail auth | One `NVIDIA_API_KEY` is shared by all three services | Use Lane B (local speech), or apply the two-key patch in [§3.5](#35-the-one-key-constraint) |
| Long silence before every reply | Reasoning is ON | Switch to the non-`(Reasoning)` catalog entry, or lower `reasoning_budget` ([§7](#7-reasoning-thinking-onoff)) |
| `HTTP 400` on `tool_choice:"auto"` | Self-hosted server started without the tool parser | Compose already sets `--enable-auto-tool-choice --tool-call-parser qwen3_coder`; don't strip it from overrides |
| Reasoning text gets spoken aloud | Reasoning parser missing on a self-hosted server | Needs `--reasoning-parser nemotron_v3`; see [§7.3](#73-self-hosted-the-reasoning-parser-is-mandatory) |
| `Minimum capability: 89. Current capability: 80` | FP8/NVFP4 on too-old a GPU | NVFP4 needs Blackwell+. Use a supported GPU or go cloud |
| vLLM `Engine core initialization failed` | Page-cache pressure | `sudo sync && sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'`, then retry |
| Omni: `No available memory for the cache blocks` | Utilization too **low** — no room left for KV cache | **Raise** `VLLM_GPU_MEMORY_UTILIZATION`. (True CUDA OOM during load is the opposite: lower it, or move TTS off the GPU) |
| `nemo-speech` will not start / bot never speaks | The pinned Magpie TTS GGUF (`v2602`) is gone from Hugging Face; the download exits 0 without fetching it | [§9.1](#91-the-pinned-magpie-tts-weight-is-gone-from-hugging-face-404) — bump the pin to `v2607` |
| Single-GPU hangs or OOMs at start | GGUFs missing, or budget sized without speech loaded | Re-run [`scripts/download-nemo-speech-models.sh`](../scripts/download-nemo-speech-models.sh); redo §5.3 |
| CUDA-graph capture fails at startup | Concurrency too high | Lower `LLM_MAX_NUM_SEQS` to `64`–`128` |
| Browser won't open the mic | Not a secure context | Keep `PIPELINE_TLS=true` and use `https://` |
| Remote client connects but no audio | NAT / firewall | Add `--profile turn` and open UDP 3478 + 49160-49200 |
| Choppy or laggy first turn | Sidecar cold start | Expected on local recipes; later turns are fast |

Full table: [`docs/06-troubleshooting.md`](../docs/06-troubleshooting.md).

---

## 14. Teardown

```bash
docker compose --profile <same-profile-you-started> down          # stop
docker compose --profile <same-profile-you-started> down -v       # + drop volumes (vLLM cache)
```

Downloaded speech GGUFs in `models/nemo-speech/` and the HF cache in
`~/.cache/huggingface` survive `down` — delete them manually to reclaim disk.

---

## 15. Reference

| Topic | Doc |
|---|---|
| Full deployment walkthrough | [`docs/01-getting-started.md`](../docs/01-getting-started.md) |
| Config index | [`docs/02-configuration-guide.md`](../docs/02-configuration-guide.md) |
| LLM models, VRAM, tuning | [`docs/how-to/configure-llm.md`](../docs/how-to/configure-llm.md) |
| ASR models / VRAM | [`docs/how-to/configure-asr.md`](../docs/how-to/configure-asr.md) |
| TTS models / VRAM | [`docs/how-to/configure-tts.md`](../docs/how-to/configure-tts.md) |
| Jetson Thor | [`docs/03-jetson-thor.md`](../docs/03-jetson-thor.md) |
| Realtime gateway | [`docs/how-to/use-realtime-gateway.md`](../docs/how-to/use-realtime-gateway.md) |
| Single-GPU memory fit (agent skill) | [`skills/deploy/references/single-gpu.md`](../skills/deploy/references/single-gpu.md) |
| Standalone ASR/TTS scripts | [`misc/asr_tts/README.md`](asr_tts/README.md) |
| Repo architecture primer | [`misc/repo-overview.md`](./repo-overview.md) |
