# FDH voice agent: which fixes would remove the most failures (ranked)

**Date:** 2026-10-09. Folder: `analysis/2026-10-09_fdh-eval-diagnosis-and-remedies/`.

**Question:** across every failure we have diagnosed, which generic fixes would remove the most of them, without hurting
any domain?

**Basis:** 207 failed tasks from the six fixes-on τ³ voice runs (agent `124acf7`). Each was diagnosed per task, and each
was then mapped to the one fix that addresses its **first irrecoverable problem**.

| User simulator | Airline | Retail | Telecom | Failures |
|---|---|---|---|---|
| gpt-5.2 | 0.740 (13 failed) | 0.623 (43) | 0.675 (37) | 93 |
| gpt-5.5 | 0.780 (11 failed) | 0.658 (39) | 0.439 (64) | 114 |

Run folders and per-task diagnoses: the collated report,
`voice-agent-evaluation-dump/tau-3-voice/2026-10-07_09-34-31Z_fdh-voice-geval-fixes/observations/collated/fdh_geval_fixes_collated_analysis.md`
(§2.1, §9, §10, §12). The design detail for fixes F1–F6 is in
[`../robustness-fixes/robustness-fixes-proposal.md`](../robustness-fixes/robustness-fixes-proposal.md).

---

## 0. Start here (context for a new session)

**What this is.** The end point of a multi-day analysis of the FDH voice agent on τ³ voice. FDH is a Frontend Delegation
Agent: a fast frontend LLM decides, and a Hermes backend LLM does the tool work.

**The history, in order:**
1. **Control runs (fixes off), 2026-10-05/06:** airline 0.660, retail r3 0.711, telecom 0.684.
2. **A failure-fix plan (A1–C2) was implemented and run with the fixes on, 2026-10-07/08:** airline 0.740, retail 0.623,
   telecom 0.675 (that telecom run is invalid because of HTTP 429s).
3. **The same agent was re-run with a different τ³ user simulator and judge (gpt-5.5 instead of gpt-5.2), 2026-10-08/09:**
   airline 0.780, retail 0.658, **telecom 0.439**.
   - The telecom drop: the gpt-5.5 caller doesn't volunteer that it is abroad, and the agent never asks.
4. **Every failure of the six fixes-on runs (207) was diagnosed per task.** Each was mapped to one generic fix, and the
   fixes were ranked: this document.

**Models:**

| Role | Model |
|---|---|
| Backend | `nvidia/nemotron-3-ultra` (Hermes, reasoning on) |
| Frontend | `nvidia/nemotron-3.5-lightning` |
| ASR / TTS | local NeMo Speech (`nemotron-speech-streaming-en-0.6b`, `magpie-tts-multilingual`) |
| τ³ user simulator / judge | gpt-5.2 or gpt-5.5 |
| τ³ user voice | `gpt-4o-mini-tts` |
| Interruption decisions | `gpt-4.1` |

**Agent version for all fixes-on runs:** `124acf7`, `config_hash a30f6df59a8b`, profile `realtime_eval.yaml` +
`gateway.eval.yaml`.

**Ground rules the user set:**
- Fixes must be **generic** (no domain or task names), obvious improvements, with minimal trade-offs.
- Fixes must **not lower any domain**, with either simulator.
- Write reports in **simple, well-organised language**.

**To pick up the work:**
1. Read this file.
2. Read the collated report (§7, item C1). It has §2.1 ranked issues, §9–§12 for the gpt-5.5 runs, and §11 fixes.
3. Read the fix design (§7, item D1).
4. For any single task, open that run's `per_task_diagnoses.md` and `data/timelines/task_NNN.txt`.

---

## 1. The ranking

**Expected flips** = failures likely to become passes if the fix works as designed: each mapped failure is weighted
high 0.8, medium 0.5, low 0.2 (§6).
**Rank** = expected flips × breadth (how many domains and simulators it helps) ÷ risk to tasks that pass today.

| Rank | Fix | Failures it targets | Expected flips | Where | Type | Risk to passing tasks |
|---|---|---|---|---|---|---|
| **1** | **No dead air: F1 replay a cut-off answer + F2 never reply with silence** | 48 (+40 as a secondary cause) | **≈ 18** | All 3 domains, both simulators; mostly gpt-5.5 | Code | **Very low** |
| **2** | **F4 Ask for facts no tool can show** (where the caller is, what the screen shows; read status screens in full) | 29 | **≈ 20** | Telecom gpt-5.5 (28) | Prompt | Low–medium (one extra question; adherence must be measured) |
| **3** | **F8 Identity recovery** (spell letter by letter, combine fragments across tries, try another identifier) | 26 | **≈ 14** | Retail (25), both simulators | Code + prompt | **Low** (runs only after a lookup miss) |
| **4** | **F5 Soft checkpoint before the first transfer** (includes account-side steps the tools already show, e.g. usage over the limit) | 29 | **≈ 15** | Telecom gpt-5.2 (17) + all others | Code | **Medium** (adds one step to all 48 passing transfers) |
| **5** | **F9 Calculator / date / compare tool** for totals, date windows, cheapest and fastest | 11 | **≈ 6** | Airline + retail | Tool | **Low** (optional for the model to use) |
| **6** | **F3 Tell the backend which actions are the caller's** (instead of "tool does not exist") | 10 | **≈ 4** | Telecom | Code | **Very low** (only after a failed call; 0 triggers in airline and retail) |
| **7** | **F10 Short jittered retry on 429** (instead of a fixed 60 s) | 7 | **≈ 4** | Telecom gpt-5.2 (429 window) | Infra | **None** to behaviour |
| **8** | **F6 The frontend may not claim a completed action** without a tool result | 2 | **≈ 1** | Retail gpt-5.5 | Code | Low (6 correct statements would be delegated) |
| **—** | **F7 Consent gate on writes**: **redesign before use** | 16 | ≈ 10 | Retail, airline | Code | **Very high as specified**: it would block 215 of 220 writes in passing gpt-5.2 tasks (§3) |
| — | Not fixable by a generic agent change | 29 | — | Mostly retail | — | — |

### What it adds up to

These are optimistic: they assume each fix works as designed. Count on roughly half the gain until A/B runs confirm it.

| Fixes applied (cumulative) | Airline 5.2 | Retail 5.2 | Telecom 5.2 | Airline 5.5 | Retail 5.5 | Telecom 5.5 |
|---|---|---|---|---|---|---|
| Today | 0.740 | 0.623 | 0.675 | 0.780 | 0.658 | 0.439 |
| + #1 No dead air | 0.776 | 0.635 | 0.682 | 0.838 | 0.690 | 0.502 |
| + #2 Ask for unseen facts | 0.776 | 0.635 | 0.683 | 0.838 | 0.690 | **0.672** |
| + #3 Identity recovery | 0.776 | **0.717** | 0.688 | 0.838 | **0.729** | 0.672 |
| + #4–#8 (F5, F9, F3, F6) | **0.836** | **0.746** | **0.788** | **0.858** | **0.754** | **0.725** |

---

## 2. Each fix in plain terms

### #1 No dead air (F1 + F2): code, very low risk

- **The problem.**
  1. The caller says "Okay." (or a side phrase like "Give me a moment.") just as the agent's answer starts.
  2. The answer is cut, and the frontend then replies to "Are you still there?" with **silence**.
  3. After two unanswered pings the caller hangs up.
- **How often:**
  - silent frontend turns: 10.4% of decisions with gpt-5.5 (5.7% with gpt-5.2);
  - 6 of the 7 airline gpt-5.5 hang-ups happened while the backend was idle.
- **The fix:**
  - **F1:** if the answer was cut and the caller only backchanneled, pinged or asked "could you repeat that?", say the
    unheard part again.
  - **F2:** any turn with a question, a ping or new content gets a spoken reply or is delegated, never an empty turn.
    Side phrases are separated from content first. While the backend is working, a ping becomes a status reply.
- **Why it is obviously right:** a voice agent should never leave a caller in silence, and a caller who missed an
  answer should hear it.
- **Risk:** it changes only moments where the agent said nothing, or where an answer was lost. About 85 such moments
  exist in passing gpt-5.2 tasks, and they get a reply instead of silence.

### #2 Ask for facts no tool can show (F4): prompt, measure first

- **The problem:** the agent depends on the caller volunteering key facts.
  - In telecom it never asked whether the caller was abroad (0 of 114 calls).
  - Location known: 13 of 20 abroad tasks pass. Location never mentioned: 0 of 44 pass.
  - It also accepted partial readings of status screens.
- **The fix:**
  - before concluding a diagnosis or transferring, ask about situational facts no tool can reveal;
  - ask the caller to read a status screen in full.
- **Risk:** one extra question in some calls; the gpt-5.2 caller usually volunteers the fact anyway. It is a prompt rule,
  and earlier prompt rules were only partly followed, so verify adherence with a decision replay before a full run.

### #3 Identity recovery (F8): low risk

- **The problem:** retail callers spell names that ASR mangles ("Chen" → "Ken", "Rossi" → "ROSI"). The agent retries the
  same garbled value, never combines the right pieces from different attempts, and refuses username-only callers.
- **The fix:** after a lookup miss:
  - ask for a letter-by-letter spelling;
  - keep and combine the fragments heard across attempts;
  - offer the other identifier (email ↔ name + zip);
  - derive a name from a username.
- **Risk:** it runs only after a failed lookup, so tasks that identify on the first try are unaffected.

### #4 Soft checkpoint before the first transfer (F5): medium risk

- **The problem:** the agent transfers with work still open.
  - Mostly the data refuel: usage 15.1 / 15 GB was fetched but never acted on.
  - The earlier prompt rule (C1) did not hold.
- **The fix:**
  - once per call, before the first transfer, the relay asks the backend to review the remaining policy steps,
    including what its own tool results already show;
  - transfers the policy requires right away are not delayed.
- **Risk:** each of the 48 passing gpt-5.2 transfers gets one extra model step, so it needs the A/B gate.

### #5 Calculator / date / compare tool (F9): low risk

- **The problem:** the agent does arithmetic and comparisons in its head:
  - "within 24 h" for a booking made 29 h earlier;
  - "cheapest" after a partial search;
  - wrong totals and counts.
- **The fix:** a small deterministic tool for date differences, totals, min/max. Only the tool's availability changes.

### #6 Tell the backend which actions are the caller's (F3): very low risk

- **The problem:** the backend calls device actions it doesn't have (65 of 114 telecom gpt-5.5 sessions). It gets "does
  not exist", then invents menu paths or transfers.
- **The fix:** answer such a call with "the caller does this; ask them to, and have them read what they see". Nothing
  changes unless a call has already failed.

### #7 Short jittered retry on 429 (F10): infra, no behaviour risk

- Replace Hermes's fixed 60 s back-off with a short jittered retry, or a fallback endpoint.
- It only matters in rate-limited windows: 7 telecom gpt-5.2 failures.

### #8 The frontend may not claim a completed action (F6)

- The frontend said "the cancellation has been completed" with no tool call (retail gpt-5.5 14, 78).
- Fix: such a direct reply is delegated instead.

---

## 3. Why F7 (consent gate) is not ranked: it needs a redesign

**The idea:** block a write unless the exact change was summarised, the summary was **fully heard**, and a "yes"
followed.

**What the logs say about tasks that pass today (gpt-5.2):**

| Domain | Writes in passing tasks | Would be blocked as specified | Blocked even when "80% heard" counts as heard |
|---|---|---|---|
| Airline | 22 | 22 | 22 |
| Retail | 114 | 113 | 105 |
| Telecom | 84 | 80 | 76 |

Why the numbers are so high:
- Confirmation summaries are 250–900 characters, and the caller almost always talks over the end.
- Telecom often writes with no "yes" turn at all.

**Redesign, before any test:**
- speak a **separate, short confirmation line** with only the change details, and gate on that line being heard plus a
  yes;
- apply the gate only to writes the policy says need confirmation.

Then re-measure the exposure. Until then, B1 (the prompt rule) stays as it is.

---

## 4. What no generic fix covers (29)

- **Simulator artifacts:** the caller picked an option it was told not to, gave the wrong cancel reason, or used a
  near-silent voice.
- **ASR losses of non-ID details** with no recovery path.
- **Backend reasoning mistakes no generic mechanism prevents:**
  - complex payment or policy combinations;
  - an in-place route change (a route guard would be airline-specific);
  - picking the wrong order or variant;
  - doing writes in the wrong order.

---

## 5. How to roll out (no regression in any domain)

1. **First, instrumentation:** log the real guard name (`engine/turn_manager.py:617` labels every guard
   `backend_question_needs_delegate`).
2. **Batch A** (code, very low risk): #1 No dead air, #6 caller's actions, #7 429 retry, #8 no claimed actions.
3. **Batch B:** #3 identity recovery and #5 compute tool.
4. **Batch C** (needs a decision replay first): #2 ask for unseen facts, #4 transfer checkpoint.
5. **F7 only after the redesign** and a new exposure measurement.

**Validation gate for every batch:**
- offline trigger counts on passing tasks;
- unit tests;
- an A/B with **≥ 3 trials** (or paired runs) on **both simulators** and **all three domains**, at concurrency 4, in a
  429-free window.

**A batch is kept only if no domain drops beyond measured noise:** about ±0.065 for airline and ±0.045 for
retail/telecom per run; retail flips 35–40% of tasks between identical runs.

---

## 6. Method and caveats

- **Diagnoses:** each failed task was diagnosed from its transcript, agent events and backend logs, and compared with the
  same task in another run.
  - Per-run files: `observations/per_task_diagnoses.md` in each run folder.
- **Fix mapping** (`data/fix_map.json`):
  - one primary fix per failure, plus secondary fixes;
  - a flip likelihood of high, medium or low;
  - mapped from the diagnoses' notes, not re-run.
- **Expected flips** use weights 0.8 / 0.5 / 0.2. They are estimates, not measurements, and the projected scores assume
  each fix works as designed.
- **F7 exposure:** `data/f7_exposure_detail.json` and `scripts/f7_exposure.py`.
  - Answers are matched to playback by id or time order.
  - "Yes" is detected from a word list.
- **Single trials everywhere,** and the gpt-5.2 telecom run had 429 stalls (7 failures are infra).
- **Stretched mappings:**
  - telecom gpt-5.2 keys 37 and 46 are F5 (no transfer happened);
  - retail gpt-5.5 46 is F1 with F6 as a secondary;
  - airline gpt-5.5 7 is F9, rated low.

## Files (this folder)

| File | What it is |
|---|---|
| `fdh-eval-diagnosis-and-remedies.md` | This report |
| `fix-plan-f1-f2-f4-f8.md` | Implementation plan for fixes #1-#3 (F1 + F2, F4, F8), its reviews, decisions and implementation record |
| `data/all_diag.json` | The 207 per-task diagnoses, merged from the six runs |
| `data/fix_map.json` | The fix mapping per failure |
| `data/f7_exposure_detail.json` | F7 exposure per write in passing gpt-5.2 tasks |
| `scripts/rank_fixes.py` | Prints the tables in §1 from `fix_map.json` |
| `scripts/f7_exposure.py` | The F7 exposure measurement |

---

## 7. Artifact index (for backtracking)

Paths are given **relative to their repository**, so they work wherever the repos are cloned.

| Short form | Repository | Path inside it used here |
|---|---|---|
| `DUMP` | `voice-agent-evaluation-dump` (eval results) | `DUMP` = `<repo>/tau-3-voice` |
| `AGENT` | `nemotron-voice-agent-smasurekar` (the voice agent; this file lives here) | `AGENT` = `<repo>` |
| `TAU2` | `tau2-bench-smasurekar` (τ³ benchmark and eval scripts) | `TAU2` = `<repo>` |
| `HERMES` | `hermes-agent-smasurekar` (backend agent framework) | `HERMES` = `<repo>` |

On the analysis machine the four repos are siblings in one parent folder, so from this file `AGENT` is `../../../../..`.

### A. Runs analysed

Each run folder has:
- `agent/`: `events.jsonl`, `gateway_events.jsonl`, `workers/*.log`;
- `tau2/<run>/`: `simulations/*.json`, `artifacts/task_*/sim_*/audio/*_labels.txt`;
- `observations/`: `per_task_diagnoses.md`, `data/tasks.json`, `data/failure_classes.csv`, `data/timelines/task_NNN.txt`,
  `diagrams_chart/<run>.html`;
- the campaign `README.md` one level up.

| # | Role | Domain | Run folder (under `DUMP/`) | Pass^1 |
|---|---|---|---|---|
| A1 | Control (fixes off, gpt-5.2) | airline | `2026-10-05_05-33-27Z_fdh-voice-geval/fdh_voice_geval_airline_regular` | 0.660 |
| A2 | Control | retail | `2026-10-05_05-59-44Z_fdh-voice-geval/fdh_voice_geval_retail_regular_r3` (r2 invalid: `…_r2`) | 0.711 |
| A3 | Control | telecom | `2026-10-05_05-59-44Z_fdh-voice-geval/fdh_voice_geval_telecom_regular` | 0.684 |
| A4 | Fixes on, gpt-5.2 | airline | `2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_airline_regular` | 0.740 |
| A5 | Fixes on, gpt-5.2 | retail | `2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_collated`: a pseudo-run merging `…_retail_regular_fixes` (71 un-throttled tasks) and `…_retail_regular_rerun43` (43 tasks); see `observations/scripts/merge_retail.py` | 0.623 |
| A6 | Fixes on, gpt-5.2 | telecom | `2026-10-07_16-18-35Z_fdh-voice-geval-fixes/fdh_voice_geval_telecom_regular` (invalid: 429s) | 0.675 |
| A7 | Fixes on, gpt-5.5 | airline | `2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_airline_regular_gpt55` | 0.780 |
| A8 | Fixes on, gpt-5.5 | retail | `2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_retail_regular_gpt55` | 0.658 |
| A9 | Fixes on, gpt-5.5 | telecom | `2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_telecom_regular_gpt55` | 0.439 |

`report.sh` outputs (metrics tables, `join.csv`) are in each campaign's `_reports/`.

**Backend decision traces are not in the dump.** They are in `AGENT/logs/fdh_workers/<session>.trace.jsonl`; there are
none for A6, which ran on another machine.

### B. Per-run analyses

| # | Run | Analysis | Per-task diagnoses + Sankey |
|---|---|---|---|
| B1 | A1–A3 control | `DUMP/2026-10-05_05-59-44Z_fdh-voice-geval/observations/collated/fdh_geval_collated_analysis.md` (control collated) | each run's `observations/` |
| B2 | A4–A6 fixes, gpt-5.2 | covered in the collated report (C1, §0–§8) | each run's `observations/` |
| B3 | A8 retail gpt-5.5 | `…/fdh_voice_geval_retail_regular_gpt55/observations/2026-10-08_fdh-voice-retail-gpt55-analysis.md` | same folder |
| B4 | A9 telecom gpt-5.5 | `…/fdh_voice_geval_telecom_regular_gpt55/observations/2026-10-09_fdh-voice-telecom-gpt55-analysis.md` | same folder |
| B5 | A7 airline gpt-5.5 | `…/fdh_voice_geval_airline_regular_gpt55/observations/2026-10-09_fdh-voice-airline-gpt55-analysis.md` | same folder |

### C. Collated report (fixes on, all runs)

- **C1:** `DUMP/2026-10-07_09-34-31Z_fdh-voice-geval-fixes/observations/collated/fdh_geval_fixes_collated_analysis.md`
  - This is the master copy, identical in `2026-10-07_16-18-35Z_…/observations/collated/` and
    `2026-10-08_11-04-25Z_…/observations/collated/`.
  - Sections:
    - §0 short answers (slowness, airline vs others, retail noise);
    - §2.1 ranked persisting issues (with gpt-5.2 and gpt-5.5 columns);
    - §3 slowness;
    - §4 did each fix work;
    - §9–§12 the gpt-5.5 runs;
    - §10.3 the telecom roaming estimate;
    - §11 recommended fixes.
- **C2:** the collated Sankey, `fdh_geval_fixes_collated_failures.html`, and `collated_failure_classes.csv` (93 rows, gpt-5.2
  runs), in the same folder.
- **C3:** campaign data in `DUMP/2026-10-07_09-34-31Z_fdh-voice-geval-fixes/observations/`:
  - `data/paired_flips.txt`: per-task fixed/broken lists and same-code noise;
  - `data/slowness.txt`;
  - `data/task_timing/`: the per-task time split;
  - scripts: `scripts/fdh_paired_flips.py`, `fdh_slowness.py`, `fdh_task_timing*.py`, `fdh_sankeys.py`.
- **C4:** pipeline scripts, copied into each run's `observations/scripts/`:
  - `fdh_failures.py` builds `tasks.json` and the timelines;
  - `fdh_sankeys.py run|collated` builds the CSV, the per-task Markdown and the Sankeys.

### D. Design and plan documents (agent repo)

| # | File | What it is |
|---|---|---|
| D1 | `AGENT/misc/prototypes/frontend-delegation-hermes/analysis/robustness-fixes/robustness-fixes-proposal.md` | Code-level root causes (with file:line) and the design of fixes F1–F6: flags, exposure on passing tasks, validation plan |
| D2 | `AGENT/misc/prototypes/frontend-delegation-hermes/tau3-geval-failure-fixes-plan.md` | The earlier fix plan A1–C2 (in the fixes-on runs), including its no-regression rule and rejected designs |
| D3 | `AGENT/misc/prototypes/frontend-delegation-hermes/analysis/model-latency/model-latency-analysis.md` | Per-model speed: benchmarks and logs; why runs were slow |
| D4 | `AGENT/misc/prototypes/frontend-delegation-hermes/analysis/tts-quality/tts-quality-analysis.md` | gpt-4o-mini-tts vs Magpie quality |
| D5 | `AGENT/misc/prototypes/frontend-delegation-hermes/runbook.md` | How to run the agent |
| D6 | `TAU2/misc/prototypes/voice-frontend-delegation-hermes-tau3-runbook.md` | How to run the τ³ eval campaigns |
| D7 | `AGENT/misc/prototypes/frontend-delegation-hermes/analysis/README.md` | Index of all analyses |

### E. Code and benchmark files referred to

| # | File | Why it matters |
|---|---|---|
| E1 | `AGENT/src/prototypes/voice_delegation_hermes_agent/` | The agent under test |
| E2 | `…/engine/turn_manager.py` (line 617 mislabels guard events; barge-in discards queued items) | Instrumentation fix; F1 |
| E3 | `…/frontend/decider.py` (lines 82–86: the backchannel guard forces an empty DIRECT turn) | F2 |
| E4 | `…/config/profiles/realtime_eval.yaml` (line 19: `replay_unheard_answer` off) | F1 |
| E5 | `…/config/prompts.backend.yaml` (lines 119–129: `<before_transfer>` C1 rule) and `…/backend/controller.py` (how the policy reaches the backend) | F4, F5 |
| E6 | `…/config/gateway.eval.yaml` | Backend feature flags (fixes A1–C2) |
| E7 | `TAU2/src/tau2/domains/telecom/environment.py` (`policy_type="manual"`) | τ³ telecom sends `main_policy.md` + `tech_support_manual.md`, **not** `tech_support_workflow.md` (which has "verify if user is traveling") |
| E8 | `TAU2/data/tau2/domains/telecom/{main_policy,tech_support_manual,tech_support_workflow}.md` | The policy texts |

### F. This analysis (this folder)

| # | File | What it is |
|---|---|---|
| F-a | `data/all_diag.json` | All 207 per-task diagnoses (airline/retail/telecom × gpt-5.2/gpt-5.5) merged |
| F-b | `data/fix_map.json` | Per failure: primary fix, secondary fixes, flip likelihood, reason |
| F-c | `data/f7_exposure_detail.json`, `scripts/f7_exposure.py` | Consent-gate exposure on passing gpt-5.2 writes |
| F-d | `scripts/rank_fixes.py` | Re-prints the §1 tables from `fix_map.json` |
