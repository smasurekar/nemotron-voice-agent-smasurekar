# Standalone OpenAI Realtime voice-agent client

A small test client for this repo's `WS /v1/realtime` gateway, plus ready-made
input files. No browser, no WebRTC, no Docker — one WebSocket, one session per
case, artifacts on disk.

It is deliberately written as a stripped-down twin of the evaluation repo's
static-lane adapter
(`voice-agent-evaluation/src/voice_agent_eval/products/openai_realtime/`), so
what you see here is the same exchange an eval run performs. Use it to check a
deployment, a prompt, or a tool set before spending a full benchmark on it.

| File | Purpose |
|---|---|
| [`run_client.py`](run_client.py) | CLI: preflight, run a case file or one ad-hoc turn, write artifacts |
| [`client.py`](client.py) | The client itself — session setup, audio/text turns, client tools, event log |
| [`profiles.py`](profiles.py) | Per-endpoint deviations from the GA schema, as data. Mirrors the eval repo's `profiles.py` |
| [`audio_io.py`](audio_io.py) | WAV in/out, mono downmix, HQ resampling to the session's wire rate |
| [`console.py`](console.py) | The run report — aligned fields, wrapping, colour. Stdlib only |
| [`mock_gateway.py`](mock_gateway.py) | A fake gateway that emits the same events, for testing the client with no GPU |
| [`cases.matrix.jsonl`](cases.matrix.jsonl) | Every input/output modality combination, in one run |
| [`cases.text.jsonl`](cases.text.jsonl) | Text-in cases: text out, audio out, instruction following |
| [`cases.audio.jsonl`](cases.audio.jsonl) | Spoken-question cases: 24 kHz, 16 kHz, full duplex, manual turn |
| [`cases.tools.jsonl`](cases.tools.jsonl) | Client-owned function tools, answered from canned outputs |
| [`question.wav`](question.wav) | 24 kHz mono, 2.3 s — *"What is the capital of France?"* |
| [`question_16k.wav`](question_16k.wav) | The same question at 16 kHz, to exercise resampling |
| [`question_weather.wav`](question_weather.wav) | 24 kHz mono, 2.5 s — *"What is the weather in San Jose right now?"* |
| [`question_two_part.wav`](question_two_part.wav) | 24 kHz mono, 4.0 s — two questions in one clip, for the manual-turn case |

## Setup

`websockets`, `numpy` and `soxr` live in the repo virtualenv, not in system
Python. Use that interpreter — the CLI detects a wrong one and prints the
corrected command rather than a bare `ModuleNotFoundError`:

```bash
cd misc/voice_agent_client
```

Start a gateway in another terminal (from the repo root):

```bash
uv run python src/realtime_server.py --host 127.0.0.1 --port 7860
```

TLS is on by default with a self-signed certificate, hence `--insecure` below.
Start the server with `PIPELINE_TLS=false` and use an `http://` base URL to
drop it. If the server has `REALTIME_API_KEY` set, export the same value here;
an `ek_` client secret works in the same variable.

## Usage

**Check what you are talking to first.** Preflight opens a throwaway session
and reports the deployment's identity, so a wrong port fails loudly instead of
returning believable answers from the wrong pipeline:

```bash
../../.venv/bin/python run_client.py --base-url https://127.0.0.1:7860 --insecure --preflight
```

```text
── Endpoint ────────────────────────────────────────────────────────────────────
  model    nvidia/nemotron-realtime
  socket   wss://127.0.0.1:7860/v1/realtime?model=nvidia%2Fnemotron-realtime
  audio    PCM16 24 kHz in  →  PCM16 24 kHz out   voice John
  turns    semantic_vad   asr nemotron-speech-streaming-en-0.6b
  auth     none (REALTIME_API_KEY unset)
  session  sess_8ce1852f462048d1ae8ad31e2a7a027c
```

**Prove all four modality combinations in one run.** `cases.matrix.jsonl`
covers text/audio in against text/audio out, plus a manual-commit turn and a
client tool:

```bash
../../.venv/bin/python run_client.py --base-url https://127.0.0.1:7860 --insecure \
    --profile nemotron-local-client-tools --cases cases.matrix.jsonl
```

```text
── Modality coverage ───────────────────────────────────────────────────────────
            → text out        → audio out
  text in   ✔ 2/2             ✔ 1/1
  audio in  ✔ 2/2             ✔ 1/1
```

The grid prints whenever a run covers more than one combination, so a red cell
names the capability that broke rather than a case id you have to look up.

**Run a case file.** Each case gets its own session:

```bash
../../.venv/bin/python run_client.py --base-url https://127.0.0.1:7860 --insecure \
    --cases cases.text.jsonl
../../.venv/bin/python run_client.py --base-url https://127.0.0.1:7860 --insecure \
    --cases cases.audio.jsonl
../../.venv/bin/python run_client.py --base-url https://127.0.0.1:7860 --insecure \
    --profile nemotron-local-client-tools --cases cases.tools.jsonl
```

**One ad-hoc turn**, without writing a case file:

```bash
../../.venv/bin/python run_client.py --base-url https://127.0.0.1:7860 --insecure \
    --text "What is two plus two?"
../../.venv/bin/python run_client.py --base-url https://127.0.0.1:7860 --insecure \
    --audio question.wav --modality audio
```

A run prints one block per case, then a summary table:

```text
── Run ─────────────────────────────────────────────────────────────────────────
  cases    4 from cases.audio.jsonl
  output   misc/voice_agent_client/runs/cases.audio

1/4 audio-question-24k                                     audio in → text out
  status   ✔ PASS  ·  reply in 1.02s  ·  turn took 3.62s  ·  response_completed
  system   Answer the question in one short sentence.
  sent     question.wav   2.29s mono PCM16 @ 24 kHz
  heard    What is the capital of France?
  said     Paris is the capital of France.
  artifact audio-question-24k.json

...

── Summary ─────────────────────────────────────────────────────────────────────
        case                 i→o   outcome                       reply latency
  PASS  audio-question-24k   a→t   response_completed                    1.02s
  PASS  audio-question-16k   a→t   response_completed                    1.02s
  PASS  audio-full-duplex    a→a   response_completed                    1.16s
  PASS  audio-manual-turn    a→t   response_completed                    1.17s

  result   4/4 cases completed cleanly   25.64s wall clock
  output   misc/voice_agent_client/runs/cases.audio
```

**reply latency** is measured from `input_audio_buffer.committed` — the moment
the endpoint owns the turn — to the first output byte, not from the start of
the session. Automatic VAD often commits while the client is still streaming
the clip, so measuring from the end of the upload would report a latency lower
than zero. `turn took` is wall time from the session opening to `response.done`.

Verdicts are `PASS` (completed, no errors), `WARN` (completed, but something
was reported — an unanswered tool call, a failed transcription) and `FAIL` (no
usable response). Only `PASS` counts as clean.

**Other useful flags:** `--case-id` (run one case), `--verbose` (print every
server event as it arrives), `--full-text` (do not clip long replies),
`--expect-model` (abort unless the endpoint advertises that model), `--out`
(artifact directory), `--timeout` (response-idle budget), `--no-pacing` (send
audio as fast as the socket takes it), `--no-color`. `--help` lists them all.

Colour switches itself off when stdout is not a terminal, and honours
`NO_COLOR`, so piping a run into a log file gives clean text.

Exit status is `0` when every case completed cleanly, `1` when a case failed,
and `2` for a setup problem — a bad case file, an unreachable endpoint, or an
`--expect-model` mismatch.

## Testing with no deployment

`mock_gateway.py` answers the same event sequence with canned replies. It is
the fastest way to check that a case file does what you meant before pointing
it at real hardware:

```bash
../../.venv/bin/python mock_gateway.py --port 7899 &
../../.venv/bin/python run_client.py --base-url http://127.0.0.1:7899 --cases cases.tools.jsonl
```

It is a wire-shape stand-in, not a pipeline: it does not validate the way the
real gateway validates, so a case that passes there still has to be run live.

## Artifacts

Results land in `--out` (default `runs/<case file stem>/`), one JSON per case
plus a WAV for every case that produced audio. Each artifact records the input
(`instructions`, `input_text`, `input_audio`) beside the output, so a result is
readable without the case file that produced it:

```json
{
  "case_id": "audio-manual-turn",
  "model": "nvidia/nemotron-realtime",
  "session_id": "sess_d42f7c3f1069...",
  "instructions": "Answer every question asked, each in one short sentence.",
  "input_text": "", "input_audio": "question_two_part.wav   3.99s mono PCM16 @ 24 kHz",
  "text": "Paris is the capital of France, and 2 + 2 equals 4.",
  "user_transcript": "What is the capital of France  and what is  two plus two?",
  "timings": {"connect_s": 0.067, "session_ready_s": 0.104, "input_sent_s": 6.305,
              "input_committed_s": 6.372, "first_text_s": 7.519, "response_done_s": 7.617},
  "tool_calls": [], "usage": {}, "errors": [],
  "completed": true, "termination_reason": "response_completed",
  "events": ["... every server event except the audio deltas ..."]
}
```

`first_audio_s` / `first_text_s` minus `input_committed_s` is the latency the
report prints; the full event log is there for when a case fails and the
summary line is not enough.

## Which combinations exist, and where

All four combinations are Realtime-gateway features. The browser RTVI path is
audio-in, audio-out only — the pipeline plumbing underneath is shared, but
nothing on that path selects a modality.

| Combination | `WS /v1/realtime` | `WS /api/ws` (RTVI) |
|---|---|---|
| text in → text out | yes | no |
| text in → audio out | yes | no |
| audio in → text out | yes | no |
| audio in → audio out | yes | yes |

RTVI still *reports* assistant text (`bot-llm-text`, `bot-tts-text`) and user
transcripts, but TTS always runs: that is audio output with a text sidecar, not
a text-only response. Its inbound message set is `client-ready`,
`disconnect-bot`, `client-message`, `ui-event` and `ui-snapshot`, and this
repo's `on_client_message` handlers accept only `set-voice`, `webcam-state` and
`webcam-chunk` — there is no text-turn message.

## Case file format

One JSON object per line. `case_id` plus either `text` or `audio`:

| Field | Meaning |
|---|---|
| `case_id` | Artifact name, and what `--case-id` selects |
| `notes` | Free text. Ignored by the client; say what the case is for |
| `instructions` | `session.instructions` for this case |
| `text` / `audio` | The turn. `audio` is a WAV path resolved next to the case file |
| `output_modalities` | Exactly `["text"]` or `["audio"]` — the gateway rejects both |
| `max_output_tokens` | `"inf"` or 1–4096 |
| `turn_mode` | `auto` (VAD commits) or `manual` (`turn_detection: null`, client commits) |
| `tools` / `tool_choice` | Client-owned function definitions, passed through verbatim |
| `tool_outputs` | Canned result per function name — what the client returns as `function_call_output` |
| `voice` | An exact voice from the deployment's TTS catalog |

## What this client does, and why

| Behavior | Reason |
|---|---|
| One session per case, never reused | State leaks between cases otherwise, and a failure stops being attributable |
| Waits for `session.updated` before the first append | Audio sent into an unconfigured session makes VAD collapse the whole buffer into one event |
| Builds `session.update` from what `session.created` advertised | Formats, voice and turn detection are the deployment's to choose; echoing them keeps one case file portable across profiles |
| Sends `tools: []`, `tool_choice: "none"` when a case declares no tools | Stops the deployment's own trusted tools from answering a case meant to be tool-free |
| Paces audio in 20 ms frames in real time | A burst-uploaded turn does not measure the latency a caller would hear. `--no-pacing` when you only want the answer |
| Keeps appending silence until the response is terminal | Server VAD needs continued signal to detect end of speech; stopping at the last speech sample leaves the turn open |
| `--timeout` is an idle budget, re-armed by any event | A slow but progressing answer is not a failure; a silent endpoint still fails on time |
| Overrides `tool_choice: "none"` on the response after a tool result | A session with `required` would otherwise force the same call again instead of answering from its result |
| Reads credentials from an environment variable only | Case files are meant to be shared and committed |

## Observed on a live deployment

Measured against the generic cascaded profile on 2026-09-18. These are the
deployment's behaviors, not client bugs — the point of writing them down is
that they look like bugs the first time you hit them.

| What you see | What is happening |
|---|---|
| Automatic-mode transcripts drop the last word or two | `semantic_vad` endpoints the turn before the clip's final syllable. `turn_mode: "manual"` on the same audio transcribes it in full |
| A multi-sentence clip in automatic mode gets committed mid-clip, and the remaining speech cancels the reply with `turn_detected` | Working as designed: the leftover audio is a new turn, and `interrupt_response` is true. Use one utterance per automatic case, or manual turns. This is why every automatic case uses a single-question clip, and `question_two_part.wav` is only used by the manual-turn cases |
| `tool_choice: "auto"` produces "Calling get_weather for San Jose." with no actual call | Model behavior on that profile, not a transport problem — no `function_call` item is emitted. `tool_choice: "required"` calls reliably |
| `preflight` reports `auth=False` | No `REALTIME_API_KEY` in this shell. Fine against a server that has none set; it will fail the handshake against one that does |

## What maps to what in the repo

| The client does | The repo does it at |
|---|---|
| `WS /v1/realtime` handshake, `session.*`, `response.*` | [`src/realtime/gateway.py`](../../src/realtime/gateway.py), [`src/realtime/session.py`](../../src/realtime/session.py) |
| `Authorization: Bearer` / `ek_` client secrets | [`src/realtime/auth.py`](../../src/realtime/auth.py) |
| `?model=` profile selection | `realtime_models:` in [`examples_registry.yaml`](../../examples_registry.yaml) |
| Client-owned function tools | [`src/realtime/client_tools.py`](../../src/realtime/client_tools.py) |
| The whole endpoint contract, in prose | [`docs/how-to/use-realtime-gateway.md`](../../docs/how-to/use-realtime-gateway.md) |
| The same exchange, as a pytest suite | [`tests/integration/test_realtime_openai_sdk_compat.py`](../../tests/integration/test_realtime_openai_sdk_compat.py) |
| The same exchange, as an eval adapter | `voice-agent-evaluation/src/voice_agent_eval/products/openai_realtime/client.py` |

## Troubleshooting

| Output | Meaning |
|---|---|
| `[SSL: CERTIFICATE_VERIFY_FAILED]` | The server is using its self-signed certificate. Add `--insecure` |
| `preflight failed: ... Connect call failed` | Nothing is listening there, or you used `http://` against a TLS server |
| `HTTP 401` during the handshake | The server has `REALTIME_API_KEY` set. Export the same value, or an `ek_` secret |
| `endpoint advertises model X, expected Y` | `--expect-model` caught the wrong deployment. Check the port and `--model` |
| `no endpoint progress for Ns` | Nothing arrived within the idle budget. Re-run with `--verbose` to see where it stopped |
| `response_cancelled` + `turn_detected` | VAD heard a new turn while the reply was playing. See the table above |
| `unanswered_tool_call` | The model called a function with no matching `tool_outputs` entry. Add it, keyed by the exact function name |
| `profile ... does not support manual turns` | Direct Omni pipelines are automatic-only. Drop `turn_mode` or use a cascaded profile |

## About the sample audio

Every clip is a **spoken question** with a checkable answer, not arbitrary
speech. That matters: with a non-question clip the model can only improvise, and
you cannot tell a good run from a bad one. Here, `audio-question-24k` and
`matrix-text-in-text-out` ask the same thing through different paths, so their
answers should agree.

| File | Says | Used by |
|---|---|---|
| `question.wav` | "What is the capital of France?" | the 24 kHz, full-duplex and matrix audio cases |
| `question_16k.wav` | the same, at 16 kHz | `audio-question-16k` — the answer should match the 24 kHz case |
| `question_weather.wav` | "What is the weather in San Jose right now?" | `tool-spoken` — a spoken question that needs a client tool |
| `question_two_part.wav` | "What is the capital of France? And what is two plus two?" | the manual-turn cases — two questions that must stay one turn |

They were synthesized by the deployment itself, through this client:

```bash
../../.venv/bin/python run_client.py --base-url https://127.0.0.1:7860 --insecure \
    --instructions "You are a speech recorder. Repeat the user's sentence exactly and say nothing else." \
    --text "Repeat this sentence exactly, word for word, and say nothing else: What is the capital of France?" \
    --modality audio --out /tmp/tts
```

The output WAV is already mono PCM16 at the session's wire rate, so it can be
fed straight back in as input. Regenerate them that way after a voice change, or
substitute any mono 16-bit WAV — the client resamples to the session rate, or
convert first:

```bash
ffmpeg -i input.mp3 -ac 1 -ar 24000 -sample_fmt s16 input.wav
```
