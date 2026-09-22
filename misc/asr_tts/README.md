# Standalone ASR / TTS scripts

Minimal scripts that call the same NVIDIA speech endpoints the voice pipeline
uses, without Pipecat, WebRTC, or Docker. Useful for checking credentials,
comparing models, and seeing exactly what the `grpc.nvcf.nvidia.com:443`
entries in the service catalog do.

| File | Purpose |
|---|---|
| [`common.py`](common.py) | Shared auth + catalog loading. Mirrors `src/utils.py` and pipecat's `_initialize_client` |
| [`tts_synthesize.py`](tts_synthesize.py) | Text → WAV via Magpie / Chatterbox TTS |
| [`asr_transcribe.py`](asr_transcribe.py) | WAV → transcript via Nemotron / Parakeet streaming ASR |
| [`sample.wav`](sample.wav) | Ready-made speech input for ASR — 12s, mono 16-bit, 16 kHz |

## Does `grpc.nvcf.nvidia.com` need a key?

**Measured answer: not for these functions today — but send a valid one or none,
never a wrong one.**

| `NVIDIA_API_KEY` | What is sent | Result (verified) |
|---|---|---|
| unset | no `authorization` header | ✅ ASR and TTS both work |
| valid `nvapi-...` | `Bearer nvapi-...` | ✅ works, and is the supported path |
| invalid / wrong family | `Bearer <junk>` | ❌ gRPC `PERMISSION_DENIED` |

When the key is unset, `nvidia_api_key()` returns the sentinel `"not-needed"` and
`make_auth` omits the header entirely — NVCF then serves the request from the
`function-id` alone. Anonymous access is undocumented and presumably
rate-limited, so **set a real key for anything beyond a smoke test**; just do not
leave a stale or wrong-family key in `.env`, which fails harder than no key.

Speech on NVCF is **gRPC over TLS with two metadata headers** — there is no URL
path or query string carrying credentials:

```python
metadata = [
    ["function-id",   "877104f7-e885-42b9-8de8-f6e4c6303969"],  # which NVCF function
    ["authorization", f"Bearer {NVIDIA_API_KEY}"],              # who you are
]
auth = riva.client.Auth(None, use_ssl=True, uri="grpc.nvcf.nvidia.com:443", metadata_args=metadata)
```

- **`function_id`** is the per-model NVCF function UUID from the catalog. It is
  *routing*, not a secret — it selects which hosted model answers.
- **`NVIDIA_API_KEY`** is the credential, when present. NVCF speech wants an
  **`nvapi-...`** key from [build.nvidia.com](https://build.nvidia.com/). An
  Inference Hub **`sk-...`** key authenticates the LLM and is rejected here with
  `PERMISSION_DENIED` — see §3.5 of [`../runbook.md`](../runbook.md).
- **TLS is auto-detected**, not configured: `src/utils.py::is_nvcf` returns true
  for any server containing `nvcf.nvidia.com`, and that becomes `use_ssl`.

Self-hosted sidecars (`nemo-speech:50051`, `magpie-multilingual-tts-service:50051`)
are the same API with **`function_id: ""`, no bearer token, and TLS off** — which
is why the catalog entries look nearly identical between cloud and local.

## Setup

`riva.client`, `grpc`, `yaml` and `dotenv` live in the repo's virtualenv, **not**
in system Python. Use that interpreter — the scripts detect a wrong one and print
the corrected command rather than a bare `ModuleNotFoundError`:

```bash
cd misc/asr_tts

# The scripts read NVIDIA_API_KEY from the repo-root .env
echo 'NVIDIA_API_KEY=nvapi-...' >> ../../.env
```

## Usage

**Transcribe the bundled sample (no TTS key needed).** `--audio` is optional —
it defaults to `sample.wav` next to the script, and a bare filename is looked up
there too, so this works from any directory:

```bash
../../.venv/bin/python asr_transcribe.py
```

**Synthesize speech:**

```bash
../../.venv/bin/python tts_synthesize.py --text "Hello from Magpie." --out out.wav
```

**Transcribe it back (round trip):**

```bash
../../.venv/bin/python asr_transcribe.py --audio out.wav
```

**Try other catalog models** (any key under `tts:` / `asr:` in
[`../../src/examples/generic/services.cloud.yaml`](../../src/examples/generic/services.cloud.yaml)):

```bash
../../.venv/bin/python tts_synthesize.py --catalog-key chatterbox-multilingual-tts
../../.venv/bin/python asr_transcribe.py --catalog-key parakeet-rnnt
../../.venv/bin/python asr_transcribe.py --catalog-key parakeet-ctc
```

**Point at a self-hosted sidecar** (`--server` also drops the function-id, since
local NIM/NeMo-Speech.cpp has none):

```bash
../../.venv/bin/python tts_synthesize.py --server localhost:50051 --voice John
../../.venv/bin/python asr_transcribe.py --audio out.wav --server localhost:50052
```

**Use a different example's catalog:**

```bash
../../.venv/bin/python tts_synthesize.py --catalog ../../src/examples/multilingual/services.cloud.yaml
```

Run either with `--help` for all flags.

## About `sample.wav`

A ready-made ASR input so you can test transcription without first getting TTS
working.

| | |
|---|---|
| Content | Harvard Sentences (the standard OSR speech-test recording), US English, male |
| Source | `voiptroubleshooter.com/open_speech` — **public domain**, published for speech-quality testing |
| Format | mono, 16-bit PCM, **16 kHz**, 12.0 s |
| Prepared by | downloading the 8 kHz original, resampling 8→16 kHz, trimming leading silence, cutting to 12 s |

The Harvard Sentences are phonetically balanced and deliberately mundane ("The
birch canoe slid on the smooth planks…"), which makes them a good WER smoke test
— unusual vocabulary won't mask a configuration problem.

> **Verified transcript** (Nemotron ASR Streaming English):
> *"The birch canoe slid on the smooth planks. Glue the sheet to the dark blue
> background. It is easy to tell the depth of a well. These days a chicken leg
> is…"* — the last sentence is clipped by the 12 s cut. Use this as the expected
> output when checking a configuration change.

To regenerate or substitute your own, anything mono/16-bit works — see below.

## Audio format

- TTS writes **mono 16-bit PCM** at `--sample-rate` (default 22050).
- ASR requires **mono 16-bit PCM** and reads the rate from the WAV header, so
  TTS output feeds straight back in at any rate. Convert other files first:

  ```bash
  ffmpeg -i input.mp3 -ac 1 -ar 16000 -sample_fmt s16 input.wav
  ```

## What maps to what in the repo

| Script does | Repo does it at |
|---|---|
| Reads `NVIDIA_API_KEY` from `.env` | [`src/utils.py::nvidia_api_key`](../../src/utils.py) |
| `use_ssl` from the server name | [`src/utils.py::is_nvcf`](../../src/utils.py) |
| Builds `function-id` + bearer metadata | `pipecat/services/nvidia/{stt,tts}.py::_initialize_client` |
| Picks voice / model / function_id | [`src/examples/generic/services.cloud.yaml`](../../src/examples/generic/services.cloud.yaml) |
| Assembles the service kwargs | [`src/examples/generic/pipeline.py`](../../src/examples/generic/pipeline.py) (ASR ~line 100, TTS ~line 234) |
| Streams synthesis with `synthesize_online` | Same call in [`src/examples/shared/prewarm.py`](../../src/examples/shared/prewarm.py) TTS warm-up |
| `stop_history=400` endpointing | [`src/examples/generic/pipeline.py`](../../src/examples/generic/pipeline.py) `NvidiaSTTService(..., stop_history=400)` |

## Troubleshooting

| Output | Meaning |
|---|---|
| `gRPC PERMISSION_DENIED` | A key was sent and rejected — wrong family (`sk-` not `nvapi-`) or not entitled. Unsetting `NVIDIA_API_KEY` entirely also works for these functions |
| `gRPC UNAUTHENTICATED` | The endpoint does require a key; set `NVIDIA_API_KEY` in `../../.env` |
| `gRPC NOT_FOUND` | `function_id` does not match a deployed function |
| `gRPC UNAVAILABLE` | Network/TLS problem, or a local sidecar is not running |
| `gRPC INVALID_ARGUMENT` | Bad sample rate, language code, or voice name for that model |
| `(nothing recognized)` | Audio reached ASR but held no speech — check it is real speech, mono, 16-bit |

These are the same failures the pipeline hits; the scripts just surface them
without the WebRTC layer in the way.
