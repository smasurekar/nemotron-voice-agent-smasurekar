# Model latency analysis: FDH τ³ voice eval runs

Scope: every model used by the FDH (Frontend Delegation Agent, Hermes backend) τ³ voice runs. Eight runs
are covered: four control runs (fixes off, 2026-10-05/06) and four runs with fixes on (2026-10-07/08).
For each model this note gives (a) a small benchmark at concurrency 1, run today, and (b) the
latency the model actually had during the eval runs, taken from the run logs.

- **Benchmark time:** 2026-10-08, 09:34–09:46 UTC, from this machine (`/localhome`, UTC clock).
  A benchmark shows the Hub's load *today*. It does not show the load during the runs.
- **Log numbers** come from `results/log_metrics.json` and the `results/log_*.csv` files.
  `analyze_logs.py` writes them. All log times below are converted to UTC.

## Summary

**Which model caused the slowness?** Mainly the backend LLM, `nvidia/nemotron-3-ultra`, and only on the
other machine. The frontend `nemotron-3.5-lightning` and the user TTS `gpt-4o-mini-tts` were also slower
in some windows. The local NeMo Speech models (ASR and TTS) were fast in every run. They did not cause
any slowness.

**Was it the Inference Hub, the machine or our code changes?**

1. **Most of the longer wall clock is not about model speed.** The fixes runs used lower concurrency.
   Their effective concurrency was 1.6–1.7, against 2.5–3.3 in the control runs. Each conversation took
   about as long as before: median 547–774 s against 494–698 s. Per-slot throughput is unchanged.
2. **The machine (or its network path) adds about 1 s to every Hub call.** The `/home` machine ran the
   old airline run and the new telecom run. In the same UTC hours, the backend model decodes at the same
   speed on both machines, about 275 tok/s. On the `/home` machine, each call carries a fixed overhead of
   2.1–2.3 s, against 1.1–1.2 s here. The same offset appears on 2026-10-05 (control code) and on
   2026-10-07 (fixes code), so our code changes did not cause it. The frontend LLM (+0.65 s) and the user
   TTS (+1.3 s) show the same offset on that machine. Neither of them goes through Hermes, so a Hermes
   version difference cannot explain it. The likely cause is network distance to the Hub (RTT), plus a
   new TCP+TLS connection on every Hermes call. From here a new connection costs only 50 ms. The
   reported "~50 tok/s" on that machine is an artefact: it divides tokens by a latency that includes
   this fixed overhead.
3. **The Inference Hub caused three separate slow windows.** These hit both machines at the same time:
   - Backend HTTP 429 ("Priority-based rate limit exceeded ... dev"). On 2026-10-07 it ran from 20:45 to
     00:15 UTC on *both* machines. On 2026-10-05 it hit the control runs r2 and telecom. Each 429 costs a
     60 s back-off.
   - Frontend decision p50 jumped from about 0.5 s to about 1.3 s at 2026-10-07 23:00 UTC on both
     machines, and stayed there until the runs ended. Today it is back to 0.50 s.
   - User TTS `gpt-4o-mini-tts` timed out on 2026-10-07 between 10:22 and 15:30 UTC. That was 171
     retries in the new airline run (p90 18 s), and 16 conversations failed on them.
4. **Our code changes did not slow any model down.** On the same machine, every model's latency is
   about the same in the control runs and the fixes runs, apart from the Hub windows above.

**Two corrections to earlier notes:**
- The "16 user-simulator `APITimeoutError`" in the new airline run (campaign README) came from the user
  **TTS** (`synthesize_voice` → `gpt-4o-mini-tts`, 15 s client timeout), not from `gpt-5.2`. All 16
  failed conversations end with `[Retry 2/3] synthesize_voice failed ... APITimeoutError`.
- The "267 429s" in retail_fixes is a count of log lines. Each failed call logs three lines. The real
  numbers are **89 throttled calls in 47 sessions**, plus 56 calls in 31 sessions in the new telecom run.

**gpt-4o-mini-tts vs Magpie TTS:** not on par. On the same sentences, first audio takes 1.34 s (p90 3.18 s) with
gpt-4o-mini-tts against 0.041 s with Magpie, about 30× slower. The Hub also sends the whole clip at once instead of
streaming it. See "Head-to-head" under the per-model sections.

## Summary table

Run keys: **C-** = control (fixes off), **F-** = fixes on.

| Key | Run | Machine |
|---|---|---|
| C-air | old airline | `/home` |
| C-r3 | old retail_r3 | `/localhome` |
| C-r2 | old retail_r2 | `/localhome` |
| C-tel | old telecom | `/localhome` |
| F-air | new airline | `/localhome` |
| F-ret | new retail_fixes | `/localhome` |
| F-r43 | new retail_rerun43 | `/localhome` |
| F-tel | new telecom | `/home` |

Log values are given as p50 / p90.

| Model | Served from | Role | Benchmark today, concurrency 1 | Eval logs, p50 / p90 per run | 429s and timeouts in the runs |
|---|---|---|---|---|---|
| Backend LLM: nvidia/nemotron-3-ultra (Hermes AIAgent), reasoning on (budget 1024) | Inference Hub `nvidia/nvidia/nemotron-3-ultra`, streaming, new client per call | Answers each delegated user turn: tools, policy, final answer (~10–12k input tokens) | TTFT 0.92 s (p90 1.79). First visible token 1.68 s. Total 1.76 s (p90 3.04) for 255 output tokens (169 of them reasoning). ~275–350 tok/s decode | Per LLM call, in s: C-air **2.6/5.3**, C-r3 1.4/3.8, C-r2 1.6/7.1, C-tel 1.5/3.9, F-air 1.6/4.3, F-ret 1.5/3.7, F-r43 1.5/3.2, F-tel **2.6/4.7**. Decode rate from the fit: 218–313 tok/s in every run | 429 retries (sessions): C-r2 260 (96), C-tel 78 (28), F-ret 89 (47), F-tel 56 (31). Other runs 0 |
| Frontend LLM: nvidia/nemotron-3.5-lightning, reasoning off | Inference Hub `nvidia/nvidia/nemotron-3.5-lightning`, non-streaming, keep-alive | The delegation decision on every user turn: delegate or not, plus the filler | Non-streaming total 0.50 s (p90 0.53) for 1.6k in / 52 out | Decision, in ms: C-air **1495/3239**, C-r3 519/1400, C-r2 564/1521, C-tel 687/2200, F-air 788/2180, F-ret 536/1717, F-r43 **1305/2644**, F-tel **1321/2878** | Timeouts at 4 s: C-air 62, C-r3 1, C-r2 5, C-tel 46, F-air 6, F-ret 5, F-r43 14, F-tel 29 |
| Voice Agent ASR: nemotron-speech-streaming-en-0.6b | Local NeMo Speech container (`nemo-speech.cpp`, gRPC :50051) | Streaming speech-to-text of the user | Final transcript 15 ms after the last audio chunk (p90 22). RTF 0.044 | User stop to ASR final, in ms: 25/35, 28/44, 31/46, 31/46, 25/39, 28/42, 24/36, 21/31 | None |
| Voice Agent TTS: magpie-tts-multilingual (John) | Local NeMo Speech container | Speaks fillers and answers | First audio 35 ms (p90 42). RTF 0.13 | Decision to first audio, in ms: 58/439, 68/501, 70/711, 68/436, 69/238, 67/518, 67/87, 58/64 | None. The container logs codec errors on cancelled streams (barge-in) |
| User simulator LLM: azure/openai/gpt-5.2 | Inference Hub `azure/openai/gpt-5.2`, non-streaming | Generates the simulated caller's next utterance | Total 3.18 s (p90 3.88) for 3.2k in / 70 out. Streaming TTFT 1.95 s | Per reply, in s: 2.97/6.05, 2.39/4.93, 2.58/5.61, 2.77/5.81, 2.38/4.52, 2.33/4.02, 2.81/4.68, 2.97/4.85 | **0.** No gpt-5.2 timeout in any run |
| Judge LLM: azure/openai/gpt-5.2 | Inference Hub, non-streaming | Hallucination check (and NL assertions) after each conversation | Total 15.0 s (p90 17.7) for 4.2k in / ~1k out, 67 tok/s | Latency is not in the logs. Calls outside the conversations: 60, 131, 129, 138, 55, 134, 48, 131 | None seen |
| Tau3 Eval TTS (user voice): openai/gpt-4o-mini-tts | Inference Hub `openai/openai/gpt-4o-mini-tts`, pcm | Voices the simulated caller | First byte 1.86 s, total 1.93 s (p90 3.03). RTF 0.60. The Hub sends the whole body at once | Per utterance including retries, in s: C-air **4.64/7.70**, 2.82/5.96, 2.71/6.64, 2.39/5.03, F-air 3.71/**18.48**, 2.75/6.05, 2.79/5.41, F-tel **4.05/6.43** | `APITimeoutError` retries (15 s timeout): 6, 46, 73, 32, **171** (16 conversations failed), 63, 7, 37 |
| *(extra)* Interruption/backchannel decision: azure/openai/gpt-4.1 | Inference Hub, non-streaming, 1 output token | Decides if the simulated caller interrupts or backchannels | Total 1.09 s (p90 1.39) | Interrupt check, in s: 1.34/2.95, 1.10/2.89, 1.18/3.03, 1.10/2.35, 1.19/2.72, 1.03/1.98, 1.00/2.16, 1.46/2.45 | None |

Sources:
- Benchmarks: `results/bench_*.json`.
- Logs: `results/log_metrics.json` (per run), `results/log_*_by_hour.csv` (per UTC hour and machine).


<!-- collated:overview -->
**All models × all runs, p50** (C- = control/fixes off, F- = fixes on; **bold** = at least 1.4× today's concurrency-1 benchmark; local ASR/TTS ms values are never bold, they are far below anything audible):

| Model | Benchmark (c=1) | C-air | C-r3 | C-r2 | C-tel | F-air | F-ret | F-r43 | F-tel |
|---|---|---|---|---|---|---|---|---|---|
| Backend LLM nemotron-3-ultra, per call (s) | 1.76 | **2.60** | 1.40 | 1.60 | 1.50 | 1.60 | 1.50 | 1.50 | **2.60** |
| Backend, per user turn (s) | — | 3.61 | 1.90 | 2.34 | 1.78 | 2.50 | 2.02 | 1.97 | 3.31 |
| Frontend LLM nemotron-3.5-lightning (s) | 0.50 | **1.50** | 0.52 | 0.56 | 0.69 | **0.79** | 0.54 | **1.30** | **1.32** |
| ASR nemotron-speech-streaming (ms) | 15 | 25 | 28 | 31 | 31 | 25 | 28 | 24 | 21 |
| TTS magpie (ms, first audio) | 35 | 58 | 68 | 70 | 68 | 69 | 67 | 67 | 58 |
| User sim gpt-5.2 (s) | 3.18 | 2.97 | 2.39 | 2.58 | 2.77 | 2.38 | 2.33 | 2.81 | 2.97 |
| Judge gpt-5.2 (s) | 15.00 | — | — | — | — | — | — | — | — |
| User TTS gpt-4o-mini-tts (s) | 1.93 | **4.64** | **2.82** | **2.71** | 2.39 | **3.71** | **2.75** | **2.79** | **4.05** |
| Decision gpt-4.1 (s) | 1.09 | 1.34 | 1.10 | 1.18 | 1.10 | 1.19 | 1.03 | 1.00 | 1.46 |
| 429 retries (backend) | — | 0 | 0 | 260 | 78 | 0 | 89 | 0 | 56 |
| Frontend timeouts | — | 62 | 1 | 5 | 46 | 6 | 5 | 14 | 29 |
| User TTS timeouts | — | 6 | 46 | 73 | 32 | 171 | 63 | 7 | 37 |
| Machine | — | /home | /localhome | /localhome | /localhome | /localhome | /localhome | /localhome | /home |
| Concurrency | — | 4 | 4 | 4 | 4 | 4→2 | 3 | 2 | 2 |
<!-- /collated:overview -->

## Why the runs took longer: concurrency, not model speed

| Run | Machine | max_concurrency | Conversations | Wall (h) | Busy (h) | Effective concurrency | Conversation p50 / mean (s) |
|---|---|---|---|---|---|---|---|
| C-air | /home | 4 | 50 | 3.18 | 9.99 | 3.14 | 698 / 719 |
| C-r3 | /localhome | 4 | 114 | 9.79 | 24.05 | 2.46 | 687 / 760 |
| C-r2 | /localhome | 4 | 114 | 7.22 | 23.73 | 3.29 | 674 / 749 |
| C-tel | /localhome | 4 | 114 | 6.17 | 18.25 | 2.96 | 494 / 576 |
| F-air | /localhome | 4 → 2 | 50 | 5.11 | 8.90 | 1.74 | 669 / 641 |
| F-ret | /localhome | 3 | 114 | 9.37 | 22.67 | 2.42 | 605 / 716 |
| F-r43 | /localhome | 2 | 43 (hard subset) | 5.96 | 9.67 | 1.62 | 774 / 809 |
| F-tel | /home | 2 | 114 | 12.00 | 20.00 | 1.67 | 547 / 632 |

How these are computed (source: `simulations/*.json` start, end and duration, kept attempts only):
- Busy = the sum of conversation durations.
- Effective concurrency = busy / wall.

Conversation length is about the same before and after the fixes. The longer wall clock comes from
running fewer conversations at once.

## Task level: how each model shares one task's time

Every τ³ task was matched with the same task in the control run. The results are below. Per-task data is in
`results/task_timing/`, copied from the dump repo's
`2026-10-07_09-34-31Z_fdh-voice-geval-fixes/observations/data/task_timing/`.

**How the time is split.**
- tau2 advances the call in 0.2 s ticks.
- The agent (frontend, backend, ASR, TTS) runs in real time. Its slowness becomes silence *inside* the conversation.
- The simulator's own models pause the clock while tau2 waits for them. These are the user-sim gpt-5.2, the user TTS
  gpt-4o-mini-tts and the decision gpt-4.1. That waiting is measured from gaps in each task.log.

| Domain | Run | Tasks | Task wall, median (min) | Conversation | Decision LLM gpt-4.1 | User TTS gpt-4o-mini | User-sim LLM gpt-5.2 | Other (setup, judge, ticks) | Extra attempts (all tasks, min) | Total task-minutes |
|---|---|---|---|---|---|---|---|---|---|---|
| airline | control | 50 | 11.7 | 51% (5.9) | 21% (2.1) | 12% (1.3) | 8% (0.8) | 8% | 141 | 741 |
| airline | fixes | 50 | 11.2 | 51% (5.7) | 21% (2.1) | 9% (0.9) | 11% (1.0) | 8% | 210 | 746 |
| retail | control r3 | 114 | 11.5 | 53% (6.0) | 22% (2.6) | 8% (0.8) | 8% (0.8) | 9% | 345 | 1795 |
| retail | fixes run 1 | 114 | 10.1 | 56% (5.8) | 19% (1.9) | 8% (0.8) | 8% (0.7) | 9% | 277 | 1644 |
| retail | fixes re-run 43 | 43 | 12.9 | 58% (7.6) | 18% (2.3) | 7% (0.9) | 8% (0.9) | 8% | 97 | 680 |
| telecom | control | 114 | 8.3 | 50% (4.2) | 15% (1.2) | 8% (0.6) | 18% (1.5) | 9% | 279 | 1383 |
| telecom | fixes | 114 | 9.2 | 49% (4.3) | 15% (1.4) | 12% (1.0) | 17% (1.6) | 8% | 192 | 1397 |

Shares are of the scored attempts' wall time; the number in brackets is the per-task median in minutes.

**Takeaways:**
- **A task costs the same before and after the fixes.**
  - Median per task: airline 11.7 → 11.2 min, retail 11.5 → 10.1, telecom 8.3 → 9.2.
  - Total task-minutes: 741 → 746, 1795 → 1644, 1383 → 1397.
  - The longer wall clock is the same work spread over fewer parallel slots.
- **About half of each task is the conversation; the other half is the simulator waiting for its own Hub models.**
  The decision LLM gpt-4.1 is the largest single wait (15–22%), even though each call is only about 1.1 s, because it
  runs on every tick of agent speech.
- **Agent-side models stay inside "conversation".** Per task:

  | Domain | Backend busy time, median | First audio after the user stops, mean |
  |---|---|---|
  | airline | 82 s → 55 s | 10.0 s → 7.5 s |
  | retail run 1 | 59 s → 75 s | 6.5 s → 7.7 s |
  | telecom | 53 s → 85 s | 6.6 s → 8.8 s |

  - The retail and telecom increases are the 429 stalls, plus the `/home` machine for telecom.
  - Even so, conversations got only 0.2–1 min longer per task.
- **Re-runs cost 100–350 task-minutes per run in both arms.** Airline fixes is the exception (141 → 210 min), because
  of the gpt-4o-mini-tts timeouts.

## Machine or Hub? Same hours, two machines

The backend fit is latency = fixed + output tokens / decode rate. It uses the Hermes `API call #N`
lines, minus the calls that include a 60 s 429 back-off.
Source: `_backend_fit_*` and `_backend_small_output_calls_*` in `results/log_metrics.json`.

| Window (UTC) | Machine | Run (code) | Fixed cost per call | Decode rate | Calls with ≤150 output tokens, p50 |
|---|---|---|---|---|---|
| 2026-10-05 06–08 | /home | C-air (control) | **2.11 s** | 262 tok/s | **2.0 s** |
| 2026-10-05 06–08 | /localhome | C-tel (control) | 1.24 s | 283 tok/s | 1.1 s |
| 2026-10-07 17 – 10-08 04 | /home | F-tel (fixes) | **2.31 s** | 274 tok/s | **2.3 s** |
| 2026-10-07 17 – 10-08 04 | /localhome | F-ret + F-r43 (fixes) | 1.14 s | 283 tok/s | 1.1 s |

The Hub generates tokens at the same speed for both machines. The `/home` machine pays about 1 s more on
every call, with both the control and the fixes code. The hourly tables
(`results/log_backend_by_hour.csv`, `log_frontend_by_hour.csv`, `log_tau2_by_hour.csv`) show the same
offset hour by hour:
- backend p50: 2.5–3.0 s against 1.4–1.7 s;
- frontend p50: 1.16 s against 0.52 s;
- user TTS p50: 3.8–5.3 s against 2.2–3.2 s.

This test cannot tell the network path apart from that machine's CPU or Python 3.14 venv. To settle it,
run `bench_network_hub.py` and `bench_backend_nemotron_ultra.py` on the `/home` machine. If a new
connection there costs several hundred ms (it costs 50 ms here), the network is the cause.

Hub-side events (the same UTC hour on both machines):

| Event | /localhome | /home |
|---|---|---|
| Backend 429, 2026-10-07 | F-ret 20:45:29 – 00:15:48 | F-tel 20:45:30 – 00:13:53 |
| Frontend p50 step at 2026-10-07 23:00 | 0.49–0.53 s → 1.22–1.59 s (F-ret, F-r43) | 1.16 s → 1.87–2.18 s (F-tel) |
| Backend 429, 2026-10-05 | C-tel 09:48–12:17, C-r2 16:58–23:38 | — |
| User TTS timeouts, 2026-10-07 10–15 | F-air: p90 14–22 s per hour, 171 retries | (not running) |

## Per model

### Backend LLM: nvidia/nemotron-3-ultra (Hermes AIAgent), reasoning on

**How it is called.** Hermes (`agent/chat_completion_helpers.py`, `_open_chat_stream`) sends:
- `stream=True` with `stream_options.include_usage`;
- the gateway's `request_overrides`, i.e. `extra_body={"chat_template_kwargs": {"enable_thinking": true}, "reasoning_budget": 1024}` (`config/gateway.yaml`);
- the session's tools.

Hermes builds a **new OpenAI client for every request**: the worker logs show
`OpenAI client created (chat_completion_stream_request, shared=False)`. So every call pays a fresh
TCP+TLS connection.

**Benchmark** (`bench_backend_nemotron_ultra.py`, `results/bench_backend_nemotron_3_ultra.json`):
- 8 real backend requests replayed from Hermes trace files: 10.6–11.6k prompt tokens, 14–16 tools (`fixtures/backend_requests.jsonl`);
- sequential, new client per call;
- TTFT (first reasoning token) p50 0.92 s, p90 1.79 s;
- first visible token 1.68 s;
- total 1.76 s / 3.04 s;
- output 44–1045 tokens (p50 255, of which 169 reasoning);
- decode about 350 tok/s measured between chunks. The log fit gives about 275 tok/s, which is the better
  number.

**In the runs:**
- Per LLM call, p50 is 1.4–1.6 s on `/localhome` and 2.6 s on `/home`.
- A backend run is one delegated user turn, with ~1.4 LLM calls on average. Its p50 is 1.8–2.5 s on
  `/localhome` and 3.3–3.6 s on `/home`.
- A 429 shows up as a call of about 61–66 s, because of the 60 s back-off. That is why p99 is about 62 s
  in C-r2, C-tel, F-ret and F-tel.

An earlier note gave "3.2 s" (F-tel) and "3.3 s" (C-air). Those match the per-*run* p50 (3.3 s / 3.6 s),
not the per-call p50 (2.6 s).


<!-- collated:backend -->
**Collated across runs** (benchmark today at concurrency 1: total 1.76 s per call (TTFT 0.92 s); **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (s) | p90 (s) | p99 (s) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | 1423 | **2.60** | 5.30 | 9.26 | 1.5× | 0 × 429 |
| C-r3 retail r3 | control | /localhome | 4 | 3770 | 1.40 | 3.80 | 7.80 | 0.8× | 0 × 429 |
| C-r2 retail r2 (429s) | control | /localhome | 4 | 2943 | 1.60 | 7.10 | 65.86 | 0.9× | 429 retries 260 in 96 sessions |
| C-tel telecom | control | /localhome | 4 | 3348 | 1.50 | 3.90 | 61.80 | 0.9× | 429 retries 78 in 28 sessions |
| F-air airline | fixes | /localhome | 4→2 | 1505 | 1.60 | 4.30 | 8.50 | 0.9× | 0 × 429 |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | 3380 | 1.50 | 3.70 | 61.90 | 0.9× | 429 retries 89 in 47 sessions |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | 1344 | 1.50 | 3.20 | 6.40 | 0.9× | 0 × 429 |
| F-tel telecom (429s) | fixes | /home | 2 | 2911 | **2.60** | 4.70 | 63.60 | 1.5× | 429 retries 56 in 31 sessions |
<!-- /collated:backend -->


<!-- collated:backend_turn -->

Per delegated user turn:

**Collated across runs** (benchmark today at concurrency 1: one delegated user turn (≈1.4 LLM calls); **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (s) | p90 (s) | p99 (s) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | 911 | 3.61 | 13.02 | 45.56 | — | 0 × 429 |
| C-r3 retail r3 | control | /localhome | 4 | 2652 | 1.90 | 7.31 | 26.49 | — | 0 × 429 |
| C-r2 retail r2 (429s) | control | /localhome | 4 | 2049 | 2.34 | 61.64 | 79.86 | — | 429 retries 260 in 96 sessions |
| C-tel telecom | control | /localhome | 4 | 2495 | 1.78 | 8.76 | 64.00 | — | 429 retries 78 in 28 sessions |
| F-air airline | fixes | /localhome | 4→2 | 935 | 2.50 | 9.34 | 35.95 | — | 0 × 429 |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | 2362 | 2.02 | 8.44 | 64.18 | — | 429 retries 89 in 47 sessions |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | 927 | 1.97 | 6.48 | 22.68 | — | 0 × 429 |
| F-tel telecom (429s) | fixes | /home | 2 | 2027 | 3.31 | 14.53 | 69.23 | — | 429 retries 56 in 31 sessions |
<!-- /collated:backend_turn -->

### Frontend LLM: nvidia/nemotron-3.5-lightning, reasoning off

**How it is called.** `frontend/llm.py` and `decider.py` send:
- non-streaming requests on one shared keep-alive client (10 s client timeout);
- `tool_choice` named (`delegate`), temperature 0, `max_tokens` 256, `enable_thinking: false`;
- a 4 s decision deadline, with a hedge request after 1.5 s.

**Benchmark** (`bench_frontend_lightning.py`, `results/bench_frontend_nemotron_3_5_lightning.json`). It
builds the agent's exact request from the realtime_eval profile and `delegation_cases.jsonl`:
- non-streaming: p50 0.50 s, p90 0.53 s, for 1.6k in / 52 out;
- streaming with tool calls was slower and erratic (TTFT 0.95 s, one call 7 s). The agent does not
  stream.

**In the runs:**
- Eval prompts are longer than the benchmark's: p50 2.3–2.6k input tokens, 52–54 output tokens.
- Decision p50 was about 0.52–0.69 s on `/localhome` before 2026-10-07 23:00 UTC, and about 1.3 s after
  it.
- On `/home` it was 1.16–1.5 s (2.0 s after 23:00).
- Timeouts at 4 s, after which the turn is delegated without a filler, are highest where latency was
  high: C-air 62, C-tel 46, F-tel 29.


<!-- collated:frontend -->
**Collated across runs** (benchmark today at concurrency 1: 0.50 s; **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (s) | p90 (s) | p99 (s) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | 1075 | **1.50** | 3.24 | 4.00 | 3.0× | 62 timeouts (4 s) |
| C-r3 retail r3 | control | /localhome | 4 | 3152 | 0.52 | 1.40 | 2.32 | 1.0× | 1 timeouts (4 s) |
| C-r2 retail r2 (429s) | control | /localhome | 4 | 3054 | 0.56 | 1.52 | 2.27 | 1.1× | 5 timeouts (4 s) |
| C-tel telecom | control | /localhome | 4 | 2930 | 0.69 | 2.20 | 4.00 | 1.4× | 46 timeouts (4 s) |
| F-air airline | fixes | /localhome | 4→2 | 1112 | **0.79** | 2.18 | 3.43 | 1.6× | 6 timeouts (4 s) |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | 3070 | 0.54 | 1.72 | 3.03 | 1.1× | 5 timeouts (4 s) |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | 1078 | **1.30** | 2.64 | 4.00 | 2.6× | 14 timeouts (4 s) |
| F-tel telecom (429s) | fixes | /home | 2 | 2455 | **1.32** | 2.88 | 4.00 | 2.6× | 29 timeouts (4 s) |
<!-- /collated:frontend -->

### Voice Agent ASR: nemotron-speech-streaming-en-0.6b (local)

**How the agent reaches it.** The `fdh-voice` container talks to `nemo-speech:50051`, gRPC Riva API,
container `nvcr.io/nvidia/nemo-speech.cpp:0.1.0`. It opens one streaming recognize call per utterance:
16 kHz LINEAR_PCM, en-US, punctuation on.

**Benchmark** (`bench_nemo_asr.py`). Input: 6 utterances of the simulated caller from an eval
recording (8 kHz, upsampled):
- paced in real time: the final transcript arrives 15 ms (p90 22 ms) after the last chunk;
- sent all at once: RTF 0.044.

**In the runs:** `user_stop_to_asr_final_ms` has a p50 of 21–31 ms and a p90 of 31–46 ms in every run.
The ASR is never the bottleneck.


<!-- collated:asr -->
**Collated across runs** (benchmark today at concurrency 1: 15 ms after last audio; **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (ms) | p90 (ms) | p99 (ms) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | 875 | 25 | 35 | 50 | 1.7× | — |
| C-r3 retail r3 | control | /localhome | 4 | 2573 | 28 | 44 | 65 | 1.9× | — |
| C-r2 retail r2 (429s) | control | /localhome | 4 | 1977 | 31 | 46 | 70 | 2.1× | — |
| C-tel telecom | control | /localhome | 4 | 2424 | 31 | 46 | 68 | 2.1× | — |
| F-air airline | fixes | /localhome | 4→2 | 903 | 25 | 39 | 66 | 1.7× | — |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | 2303 | 28 | 42 | 67 | 1.9× | — |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | 911 | 24 | 36 | 52 | 1.6× | — |
| F-tel telecom (429s) | fixes | /home | 2 | 1979 | 21 | 31 | 47 | 1.4× | — |
<!-- /collated:asr -->

### Voice Agent TTS: magpie-tts-multilingual, voice John (local)

**How it is called.** The same container: `SynthesizeOnline` per sentence, 22.05 kHz, voice `John`.

**Benchmark** (`bench_nemo_tts.py`): first audio 35 ms (p90 42 ms), RTF 0.13.

**In the runs:** `filler_timing.decision_to_first_audio_ms` has a p50 of 58–70 ms.
- The p90 grows with concurrency on this GPU: 87 ms at concurrency 2 (F-r43) and 436–711 ms at
  concurrency 3–4.
- So there is some queueing on the shared container, but it is small next to the LLM time.

`nemo_speech_logs.txt` holds no timings. It only logs `codec worker failed` / `failed to write PCM
callback output`, 131–1122 per run. Those are streams cut off by barge-in.


<!-- collated:tts -->
**Collated across runs** (benchmark today at concurrency 1: 35 ms to first audio; **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (ms) | p90 (ms) | p99 (ms) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | 904 | 58 | 439 | 1534 | 1.7× | 131 cancelled-stream codec errors (barge-in) |
| C-r3 retail r3 | control | /localhome | 4 | 2690 | 68 | 501 | 2029 | 1.9× | 705 cancelled-stream codec errors (barge-in) |
| C-r2 retail r2 (429s) | control | /localhome | 4 | 2290 | 70 | 711 | 2133 | 2.0× | 574 cancelled-stream codec errors (barge-in) |
| C-tel telecom | control | /localhome | 4 | 2490 | 68 | 436 | 1502 | 1.9× | 457 cancelled-stream codec errors (barge-in) |
| F-air airline | fixes | /localhome | 4→2 | 964 | 69 | 238 | 1936 | 2.0× | 1122 cancelled-stream codec errors (barge-in) |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | 2503 | 67 | 518 | 2024 | 1.9× | 928 cancelled-stream codec errors (barge-in) |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | 957 | 67 | 87 | 1472 | 1.9× | 212 cancelled-stream codec errors (barge-in) |
| F-tel telecom (429s) | fixes | /home | 2 | 2143 | 58 | 64 | 524 | 1.7× | 141 cancelled-stream codec errors (barge-in) |
<!-- /collated:tts -->

### User simulator LLM: azure/openai/gpt-5.2

**How it is called.** tau2 uses litellm with `openai/azure/openai/gpt-5.2`: non-streaming,
temperature 0, a seed, call `user_streaming_response`.

**Benchmark** (`bench_user_sim_gpt52.py`). It replays 6 real requests from `llm_debug`: total 3.18 s
(p90 3.88 s). With streaming, TTFT is 1.95 s.

**In the runs.** The value is the time from the task.log line `action='generate_message'` to the next
gpt-5.2 response line. Each sim's last `llm_debug` file (`generation_time_seconds`) agrees within
0.3 s.
- p50 is 2.3–3.0 s and p90 is 4.0–6.1 s in every run, with no trend and no timeouts.
- The `/home` machine adds about 0.3–0.6 s.
- This model did not cause any slowness.


<!-- collated:usersim -->
**Collated across runs** (benchmark today at concurrency 1: 3.18 s; **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (s) | p90 (s) | p99 (s) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | 1049 | 2.97 | 6.05 | 9.54 | 0.9× | — |
| C-r3 retail r3 | control | /localhome | 4 | 2496 | 2.39 | 4.93 | 9.00 | 0.8× | — |
| C-r2 retail r2 (429s) | control | /localhome | 4 | 2516 | 2.58 | 5.61 | 11.10 | 0.8× | — |
| C-tel telecom | control | /localhome | 4 | 2543 | 2.77 | 5.81 | 9.70 | 0.9× | — |
| F-air airline | fixes | /localhome | 4→2 | 964 | 2.38 | 4.52 | 8.05 | 0.7× | — |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | 2469 | 2.33 | 4.01 | 6.90 | 0.7× | — |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | 976 | 2.81 | 4.67 | 8.38 | 0.9× | — |
| F-tel telecom (429s) | fixes | /home | 2 | 2395 | 2.97 | 4.84 | 8.25 | 0.9× | — |
<!-- /collated:usersim -->

### Judge LLM: azure/openai/gpt-5.2

**How it is called.** It is the same Hub model. tau2 runs the full-duplex hallucination check once per
conversation, after the conversation ends. The NL-assertion judge (`TAU2_JUDGE_MODEL`) uses the same
model.

**Benchmark** (`bench_judge_gpt52.py`). It rebuilds 5 hallucination-check prompts with tau2's own code:
total 15.0 s (p90 17.7 s), about 1k output tokens at 67 tok/s.

**In the logs.** Only the call count can be recovered: 48–138 per run, about 1 per conversation. Its
latency is not logged. It adds about 15 s per conversation, outside the conversation. It does not affect
any agent latency metric.


<!-- collated:judge -->
**Collated across runs** (benchmark today at concurrency 1: 15.0 s; **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (s) | p90 (s) | p99 (s) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | — | — | — | — | — | 60 calls (latency not logged) |
| C-r3 retail r3 | control | /localhome | 4 | — | — | — | — | — | 131 calls (latency not logged) |
| C-r2 retail r2 (429s) | control | /localhome | 4 | — | — | — | — | — | 129 calls (latency not logged) |
| C-tel telecom | control | /localhome | 4 | — | — | — | — | — | 138 calls (latency not logged) |
| F-air airline | fixes | /localhome | 4→2 | — | — | — | — | — | 55 calls (latency not logged) |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | — | — | — | — | — | 134 calls (latency not logged) |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | — | — | — | — | — | 48 calls (latency not logged) |
| F-tel telecom (429s) | fixes | /home | 2 | — | — | — | — | — | 131 calls (latency not logged) |
<!-- /collated:judge -->

### Tau3 Eval TTS (user voice): openai/gpt-4o-mini-tts

**How it is called.** tau2 (`tau2_ihub_overrides.ihub_tts`) calls `audio.speech.create` with:
- `response_format="pcm"`;
- a persona voice and style instructions;
- a **15 s** client timeout, no SDK retries, and tau2 retrying 3 times.

**Benchmark** (`bench_user_tts_gpt4o_mini.py`): first byte 1.86 s, total 1.93 s, RTF 0.60. The first
byte arrives at about the total time, so the Hub sends the whole body at once and streaming would not
help.

**In the runs.** The value is the time from `USER SIMULATOR:` to `Voice synthesized`, including
retries.
- p50 is 2.4–3.7 s on `/localhome` and 4.0–4.6 s on `/home`.
- In **F-air**, on 2026-10-07 10:22–15:30 UTC, p90 reached 13–22 s per hour, with **171** `APITimeoutError`
  retries. **16** conversations failed after three attempts. These are the "user-simulator timeouts" in the
  README.
- tau2 blocks its event loop during these calls, so a slow TTS call stalls the whole simulated
  conversation.


<!-- collated:usertts -->
**Collated across runs** (benchmark today at concurrency 1: 1.93 s total; **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (s) | p90 (s) | p99 (s) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | 1013 | **4.64** | 7.70 | 12.32 | 2.4× | 6 APITimeoutError retries |
| C-r3 retail r3 | control | /localhome | 4 | 2393 | **2.82** | 5.96 | 19.07 | 1.5× | 46 APITimeoutError retries |
| C-r2 retail r2 (429s) | control | /localhome | 4 | 2409 | **2.71** | 6.64 | 19.49 | 1.4× | 73 APITimeoutError retries |
| C-tel telecom | control | /localhome | 4 | 2444 | 2.39 | 5.03 | 18.29 | 1.2× | 32 APITimeoutError retries |
| F-air airline | fixes | /localhome | 4→2 | 914 | **3.71** | 18.48 | 37.00 | 1.9× | 171 APITimeoutError retries |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | 2358 | **2.75** | 6.05 | 20.44 | 1.4× | 63 APITimeoutError retries |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | 944 | **2.79** | 5.41 | 12.54 | 1.4× | 7 APITimeoutError retries |
| F-tel telecom (429s) | fixes | /home | 2 | 2301 | **4.05** | 6.43 | 20.33 | 2.1× | 37 APITimeoutError retries |
<!-- /collated:usertts -->

### Head-to-head: is gpt-4o-mini-tts on par with Magpie TTS?

**No. To first audio, gpt-4o-mini-tts is about 30× slower than Magpie (1.3 s vs 0.04 s), and it does not really stream.**

Both models got the same 8 sentences, from a 1-word filler to a 129-character answer. The test ran on the same
machine, at concurrency 1, with requests interleaved, on 2026-10-08 10:10–10:12 UTC.
- 16 calls per arm, 0 errors.
- Script: `bench_tts_head_to_head.py`. Output: `results/bench_tts_head_to_head.json`.

| | Magpie (local NeMo Speech) | gpt-4o-mini-tts (Hub), with persona instructions | gpt-4o-mini-tts, no instructions |
|---|---|---|---|
| Time to first audio, p50 / p90 | **0.041 s / 0.048 s** | 1.34 s / 3.18 s | 2.45 s / 4.58 s |
| Total time, p50 / p90 | 0.64 s / 1.20 s | 1.41 s / 3.26 s | 2.52 s / 4.65 s |
| Real-time factor (total ÷ audio length), p50 | 0.13 | 0.25 | 0.51 |
| Share of the total spent *after* the first byte | 94% (true streaming) | 4% (the whole clip arrives at once) | 2% |
| Does first audio grow with sentence length? | No: 35–46 ms for every sentence | Yes, and erratic: 0.9 s for "One moment.", up to 3.2 s for long sentences | Erratic: 1.5–5.7 s |

**During the eval runs** (from the logs above): gpt-4o-mini-tts took p50 2.4–4.6 s per utterance, with a p90 of up to
18 s and 6–171 timeouts per run. Magpie's first audio was p50 58–70 ms in every run, with no errors.

What this means:
- **Not on par for first audio.** Magpie starts speaking in about 40 ms. gpt-4o-mini-tts needs about 1–3 s before any
  audio exists.
  - The Hub buffers the full clip and sends it in one go: 96% of the wait comes before the first byte.
  - So sentence-by-sentence streaming would not hide the delay.
- **Per second of audio both are faster than real time** (RTF 0.13 vs 0.25). gpt-4o-mini-tts can keep up once it starts,
  but its start-up delay and its variance (p90 2.4× the p50) are the problem.
- **The persona instructions don't make it slower.** The "no instructions" arm was in fact slower in this sample.
  That gap is Hub variance, not a real cost.
- **In τ³ today**, gpt-4o-mini-tts only voices the *simulated caller*, and tau2 pauses the clock while it waits. Its
  latency therefore never reaches the agent's latency metrics.
  - It does cost wall time: 8–12% of each task (see "Task level").
  - Its timeouts force re-runs: 16 failed conversations in the airline fixes run.
- **If gpt-4o-mini-tts were used as the agent's voice**, every reply would start about 1.3 s later at the median
  (3+ s at p90), against about 0.06 s with Magpie. It would also add a remote failure point.
- **Not a voice-quality comparison.** This measures speed only.
  - **Quality** is in [`../tts-quality/tts-quality-analysis.md`](../tts-quality/tts-quality-analysis.md): 41 τ³-style sentences, intelligibility through our Nemotron ASR, automatic MOS, and a blind A/B page.
  - Result: gpt-4o-mini-tts is on par in naturalness, and somewhat better on spelled names, emails and IDs (Nemotron WER 6–7% vs 12%). Magpie is more consistent between takes. The gap between the two is latency, not quality.

### (Extra) Interruption/backchannel decision: azure/openai/gpt-4.1

**How it is called.** It is the I0 override in `tau2_ihub_overrides`: `interruption_decision` and
`backchannel_decision`, non-streaming, 1 output token. The two calls run in parallel during agent
speech.

**Benchmark** (`bench_decision_gpt41.py`): 1.09 s (p90 1.39 s).

**In the runs.** The value comes from `Listener reactions evaluated: interrupt=.. (Xs), backchannel=..
(Ys)`. Interrupt p50 is 1.0–1.5 s, p90 2.0–3.0 s. It is steady and caused no failures.


<!-- collated:decision -->
**Collated across runs** (benchmark today at concurrency 1: 1.09 s; **bold** = p50 at least 1.4× the benchmark):

| Run | Arm | Machine | Concurrency | n | p50 (s) | p90 (s) | p99 (s) | p50 ÷ benchmark | Errors / notes |
|---|---|---|---|---|---|---|---|---|---|
| C-air airline | control | /home | 4 | 4094 | 1.34 | 2.95 | 6.63 | 1.2× | — |
| C-r3 retail r3 | control | /localhome | 4 | 12258 | 1.10 | 2.89 | 6.77 | 1.0× | — |
| C-r2 retail r2 (429s) | control | /localhome | 4 | 9765 | 1.18 | 3.03 | 7.14 | 1.1× | — |
| C-tel telecom | control | /localhome | 4 | 6714 | 1.10 | 2.35 | 6.02 | 1.0× | — |
| F-air airline | fixes | /localhome | 4→2 | 4407 | 1.19 | 2.72 | 5.76 | 1.1× | — |
| F-ret retail run 1 (429s) | fixes | /localhome | 3 | 11292 | 1.03 | 1.98 | 4.25 | 0.9× | — |
| F-r43 retail re-run 43 | fixes | /localhome | 2 | 4691 | 1.00 | 2.16 | 4.80 | 0.9× | — |
| F-tel telecom (429s) | fixes | /home | 2 | 6672 | 1.46 | 2.45 | 4.40 | 1.3× | — |
<!-- /collated:decision -->

## How to re-run

All scripts live in this folder.
- They read `NVIDIA_API_KEY` in-process from the agent repo `.env` and never print it.
- They send requests one at a time, 5–10 requests per model.
- Results go to `results/`.

```bash
cd /localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/misc/prototypes/frontend-delegation-hermes/analysis/model-latency
PY=/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/.venv/bin/python   # openai + riva client
TAU2PY=/localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar/.venv/bin/python

# Inference Hub models (concurrency 1)
$PY bench_backend_nemotron_ultra.py --extract -n 8   # once: pick real 10.5-12.5k-token requests from logs/fdh_workers/*.trace.jsonl
$PY bench_backend_nemotron_ultra.py -n 8             # backend nemotron-3-ultra (streaming, reasoning budget 1024)
(cd ../../../../.. && PYTHONPATH=src $PY misc/prototypes/frontend-delegation-hermes/analysis/model-latency/bench_frontend_lightning.py -n 6 --stream 4)
$PY bench_user_sim_gpt52.py -n 6 && $PY bench_user_sim_gpt52.py -n 4 --stream
$TAU2PY bench_judge_gpt52.py -n 5                    # needs tau2's code to rebuild the judge prompt
$PY bench_user_tts_gpt4o_mini.py -n 6
$PY bench_tts_head_to_head.py --rounds 2           # gpt-4o-mini-tts vs Magpie, same sentences (needs nemo-speech on :50051)
$PY bench_decision_gpt41.py -n 6
python3 bench_network_hub.py -n 8                    # TCP/TLS cost to the Hub; run it on BOTH machines

# Local NeMo Speech (only if the nemo-speech container is already up on :50051; no container is started)
$PY bench_nemo_tts.py -n 8
$PY bench_nemo_asr.py -n 6

# Log analysis (stdlib only, no network): results/log_metrics.json and results/log_*.csv
python3 analyze_logs.py
```

To add a run, append it to `RUNS` in `analyze_logs.py`. The machine and time zone are detected from
`provenance/gateway_env.txt`: `FDH_HERMES_PYTHON` under `/home/smasurekar` means the other machine,
whose logs are in IST.

## Files

| File | What it is |
|---|---|
| `bench_common.py` | Key loading from `.env`, streaming and non-streaming timing, tau2 `llm_debug` replay, result writing |
| `bench_backend_nemotron_ultra.py` | Backend LLM benchmark; `fixtures/backend_requests.jsonl` holds the replayed requests |
| `bench_frontend_lightning.py` | Frontend LLM benchmark, using the agent's own request builder |
| `bench_nemo_asr.py`, `bench_nemo_tts.py` | Local NeMo Speech ASR and TTS benchmarks |
| `bench_user_sim_gpt52.py`, `bench_judge_gpt52.py` | gpt-5.2 as the user simulator and as the judge |
| `bench_user_tts_gpt4o_mini.py` | User-voice TTS |
| `bench_tts_head_to_head.py` | gpt-4o-mini-tts vs Magpie on the same sentences, interleaved; `results/bench_tts_head_to_head.json` |
| `bench_decision_gpt41.py` | Interruption/backchannel decision LLM |
| `bench_network_hub.py` | DNS, TCP and TLS cost to the Hub |
| `analyze_logs.py` | Log analysis over the 8 runs |
| `results/bench_*.json` and `.log` | Benchmark outputs: per-request rows, summary, UTC start and end |
| `results/log_metrics.json` | Per-run log metrics, plus the backend fits by run and by machine |
| `results/log_backend_calls.csv`, `log_frontend_decisions.csv` | Every backend LLM call and every frontend decision, in UTC |
| `results/log_backend_by_hour.csv`, `log_frontend_by_hour.csv`, `log_tau2_by_hour.csv`, `log_backend_429_by_hour.csv` | Per UTC hour × machine × run |
| `collated_tables.py` | Writes the "collated across runs" table in each model section and the all-models overview (between `<!-- collated:… -->` markers; re-runnable). Also writes `results/collated_tables.md` |
| `results/task_timing/` | Per-task time split, control vs fixes: `<domain>_<arm>.csv`, `compare_*.csv` (per-task deltas with `main_driver`), `compare_summary.txt`, `task_time_table.md`. The scripts are in the dump repo, `…fixes/observations/scripts/fdh_task_timing*.py` |
