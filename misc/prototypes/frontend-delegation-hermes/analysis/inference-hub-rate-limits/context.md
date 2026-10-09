# Context: Inference Hub rate limiting on nemotron-3-ultra (for a new discussion thread)

## What we run
- **Agent:** FDH voice agent (Frontend Delegation Agent + Hermes backend), evaluated on τ³ voice (airline, retail, telecom).
- **Backend model:** `nvidia/nvidia/nemotron-3-ultra` on Inference Hub (`https://inference-api.nvidia.com/v1`).
  - Streaming; reasoning on, budget 1024.
  - Hermes opens a new client per call.
  - **This is the only model that was rate-limited.**
- **Other Hub models, never rate-limited in these runs:**
  - `nvidia/nvidia/nemotron-3.5-lightning` (frontend);
  - `azure/openai/gpt-5.2`, `openai/openai/gpt-5.5` (user simulator, judge);
  - `azure/openai/gpt-4.1` (decision model);
  - `openai/openai/gpt-4o-mini-tts` (user voice).
- **Concurrency:** 2–4 sessions per run, about 8 in total when two runs overlap on two machines.
  - Each session makes one backend call at a time.

## The error
```
Error code: 429 - Model capacity reached for nvidia/nvidia/nemotron-3-ultra.
Priority: dev, Rate limit type: tokens, Model TPM: 50000000, Remaining: 0
```
- **TPM:** tokens per minute (input + output).
- **50M** appears to be the model's shared pool at `dev` priority. **To confirm with the Hub team** whether it is per model, per team or per key.
- **Hermes retries after a fixed 60 s** (`Retrying API call in 60.0s`). In a voice call that is dead air, and the simulated caller often hangs up.

## Measured impact: 10 runs, 2026-10-05 → 10-09

| Run | Throttled calls | % of backend calls | Sessions hit | Tasks hit | When (UTC) |
|---|---|---|---|---|---|
| Run 1 – Airline | 0 | 0% | 0/60 | 0/50 | — |
| Run 2 – Retail | 260 | 8.1% | 96/130 (74%) | 85/114 (75%) | 10-05 16:58–23:38 |
| Run 3 – Retail | 0 | 0% | 0/135 | 0/114 | — |
| Run 4 – Telecom | 80 | 2.3% | 30/138 (22%) | 22/114 (19%) | 10-05 08:06–12:17 |
| Run 5 – Airline | 0 | 0% | 0/74 | 0/50 | — |
| Run 6 – Retail | 89 | 2.6% | 47/134 (35%) | 42/114 (37%) | 10-07 20:45 – 10-08 00:15 |
| Run 7 – Retail | 0 | 0% | 0/48 | 0/43 | — |
| Run 8 – Telecom | 56 | 1.9% | 31/131 (24%) | 23/114 (20%) | 10-07 20:45 – 10-08 00:13 |
| Run 9 – Retail | 0 | 0% | 0/127 | 0/114 | — |
| Run 10 – Telecom | 0 | 0% | 0/138 | 0/114 | — |
| **All** | **485** | **1.6%** | **204/1,115 (18%)** | **172/941 (18%)** | |

- **Clustered in 3 windows.** The 10-07 window hit **two machines at the same minutes**, so this is shared-pool capacity, not our client.
- **About 8 h of dead air in total** (485 × 60 s).
- **Throttled tasks pass far less often:** 79/172 (0.46), against 206/284 (0.73) for un-throttled tasks in the same 4 runs.
- Runs 2, 6 and 8 failed our 429 gate. Run 6's 43 throttled tasks were re-run (Run 7).

## Our usage (estimates from logs)

| | Value |
|---|---|
| Tokens per backend call | about 11–12k input + about 200 output |
| Calls per minute per session | about 3–6 |
| TPM per session | ≈ 35–80k |
| **8 sessions** | **≈ 0.3–0.6M TPM** (about 1% of 50M), so we did not exhaust the pool alone |
| **128 sessions (target)** | **≈ 5–10M TPM**; with 2× headroom, **about 20M TPM** |

A 1B TPM request is not needed: it is 20× the model's whole current limit. What we need is a **reserved allocation** or a
**priority above `dev`**.

## Draft Slack message (short)

> Hi team 👋 We're hitting **429s on `nvidia/nvidia/nemotron-3-ultra`** during our voice-agent eval runs:
> `Model capacity reached ... Priority: dev, Rate limit type: tokens, Model TPM: 50000000, Remaining: 0`
> We run about **8 concurrent sessions**; about 1.6% of calls get throttled, clustered in a few windows (screenshot).
> Our own usage is only about 0.3–0.6M TPM, so it looks like the shared dev pool is exhausted.
> 1. What exactly is the rate limit for this model: per key, team, or shared across `dev`?
> 2. Can we get an increased quota, and how do we request it?
> 3. Could we be supported at **128 concurrent sessions** (est. ~5–10M TPM: ~12k tokens/call, 3–6 calls/min/session), e.g. a **reserved ~20M TPM** or priority above `dev`?
> 4. What retry/back-off do you recommend (is there a `Retry-After` header)?
> Thanks! 🙏

## Open questions / next steps
- Answers from the Hub team on scope (per key/team/pool), quota request process, priority tiers and `Retry-After`.
- **Client side, independent of the quota:** replace Hermes's fixed 60 s back-off with a short jittered retry (2–5 s), or
  honour `Retry-After`. Optionally fall back to another endpoint.
- **Before launching long runs,** avoid the busy windows seen so far: roughly 08:00–12:00 and 17:00–00:30 UTC.

## Sources
- **Per-run counts:** `voice-agent-evaluation-dump/tau-3-voice/<campaign>/<run>/agent/workers/*.log` ("Retrying API call in"
  lines; one line = one throttled call).
- **Pass-rate impact:** `.../observations/data/tasks.json`.
- **Throttling script:** `2026-10-05_05-59-44Z_fdh-voice-geval/observations/scripts/fdh_ratelimit_stalls.py`.
- **Per-model latency study:** `nemotron-voice-agent-smasurekar/misc/prototypes/frontend-delegation-hermes/analysis/model-latency/model-latency-analysis.md`.
- **Collated eval analysis:** `voice-agent-evaluation-dump/tau-3-voice/2026-10-07_09-34-31Z_fdh-voice-geval-fixes/observations/collated/fdh_geval_fixes_collated_analysis.md` (§3 slowness).
