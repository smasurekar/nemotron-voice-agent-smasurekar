# Plan: τ³-voice failure fixes for the Frontend Delegation Agent (FDH)

**Status:** implemented behind switches, all off by default; not yet evaluated (§8) · **Date:** 2026-09-30 ·
**Revision:** 4 (implementation, §13; review 2 findings and decisions applied in revision 3, §10)
**Context for a new reader:** Appendix A records everything this plan was built from: the original proposal,
the failure analysis, the log measurements, the code facts, the ranking history, both reviews and the
decisions. Read it first if you pick this plan up without the conversation that produced it.
**Code:** `src/prototypes/voice_delegation_hermes_agent/` (voice server, gateway prompts) and
`src/prototypes/voice_frontend_backend_agent/normalization/` (shared normalization, additive and off by default)
**Builds on:** [`prototype-plan.md`](prototype-plan.md) (revision 5), [`runbook.md`](runbook.md)
**Evidence:** airline run `voice-agent-evaluation-dump/tau-3-voice/2026-09-29_17-04-09Z_fdh-voice`
(`fdh_voice_dlg_airline_regular`, agent commit `3a7e04a`, Pass^1 0.660, 33 of 50 passed). The per-task
analysis is in `observations/airline_failed_tasks_analysis.md` in that dump.

The airline baseline already beats the comparable FBA arm (0.62). This plan therefore puts **not regressing
the 33 passing airline tasks** first. It adds the fewest generic changes that target the voice-layer failures,
one at a time, each behind a switch that reproduces the baseline exactly. No change is airline-specific.

The retail run (`tau2-bench-smasurekar/data/simulations/fdh_voice_dlg_retail_regular`) is incomplete and ran on
a different machine. It does not set any default in this plan.

---

## 0. Scope and order

| Step | ID | Change | Target failed tasks | Status |
|---|---|---|---|---|
| 1 | **M1** | One proactive short status per run while WORKING, calibrated on the target host | 14, 18, 20, 21, 22, 23 | Canary |
| 2 | **M3** | Spelled-run normalization + an evidence-gated spelling hold + a better local "invalid ID" wording | 7, 17, 35, 37, 44 | Canary |
| 3 | — | Evaluate steps 1–2: failed tasks + passing sentinels (§8) | — | Gate |
| 4 | **M2** | Replay an unheard answer: run-bound, short TTL, explicit intent | 20, 22, 23 | Experimental |
| 5 | G1 | Filler de-duplication | 21, 23, 35 | Separate ablation |
| 6 | G2 | Short spoken answers, only after semantic confirmation tests | none directly | Separate ablation |
| 7 | G4 | Consent and read-back rules before writes (prompt) | 14, 40 | Separate ablation |

**Dropped:**

- G3 (parallel tool calls). Both τ² policies require one tool call at a time (`airline/policy.md:11`,
  `retail/policy.md:20`). `parallel_tool_call_guidance: False` in `sidecar/homes.py` stays as it is.
- The M4 validation bypass. Invalid identifiers are never sent to a tool. Only the local wording changes,
  and that is part of M3.
- Barge-in threshold and resume. Backend policy prompt rules. Hermes run retry. Few-shot tuning of the
  status examples.

**Decisions (review 2, 2026-09-30):**

- **RC2 approved narrowly.** The scope is in §4.
- **Spelled-word case:** keep the ASR case (§3.1).
- **Canary host:** the original airline host, if available. Run a new control and the treatment there, and
  never compare a treatment with the historical 2026-09-29 run. If that host is unavailable, use a dedicated
  stable host and first establish a fresh paired control there. Do not use the current retail host while
  it shows database-client exhaustion ("too many clients").

## 1. Rules for every change

1. **A switch that restores the baseline exactly.**
   - Every behaviour is off by default in code.
   - Every prompt change is a **prompt variant**: a Jinja `{% if features.<name> %}` block in the
     catalog, driven by a feature map in config. The backend map is `prompt_features` in `gateway.yaml`,
     passed to the `prompts.backend.yaml` renders. The frontend map is `prompt_features` in
     `delegation_agent.yaml`, passed to the `prompts.yaml` renders.
   - The local tool-result wording is selected by the existing `invalid_message_key` / `message_key`
     settings.
2. **A byte-identical baseline, tested.** Before any prompt edit, write golden files of the rendered
   frontend system prompt, the backend `backend_soul` / `backend_system` (with the airline policy) and the
   local tool-result messages at `3a7e04a`. A test renders them with every feature off and asserts byte
   equality. The profile `config/profiles/tau3_eval_baseline.yaml` (with its gateway config) turns every new
   switch off. It is the control arm for every evaluation.
3. **One change per arm.** Each change is evaluated alone against the control before it is combined.
4. **The gateway protocol does not change.** Shared normalization code gets additive settings only, off by
   default, so the other voice prototype is unchanged. New gateway reply fields are optional; the protocol
   passes optional fields through.
5. **Deployed fingerprints.** The voice server and the gateway are launched separately, so each logs what
   it actually runs.
   - **Voice server:** `fdh_session_start` gains `features` (the frontend `prompt_features` map and every new
     behaviour switch) and `invalid_message_keys`. A new event `frontend_prompt` carries
     `frontend_prompt_sha256`, a hash of the rendered frontend system prompt, logged at `session.update`
     (the prompt depends on the client's tools, which `fdh_session_start` does not know yet).
   - **Gateway:** at startup, logs and exposes on its health endpoint `backend_features` (its
     `prompt_features`) and `backend_catalog_sha256`. For every session, `session.configured` carries
     `backend_soul_sha256` and `backend_system_sha256`, hashes of the rendered prompts with the session's
     policy. The voice server logs these in `backend_configured`.
   - **Per arm:** each arm declares its expected fingerprint (feature maps plus the four hashes). The report
     step fails the arm if any session's fingerprint differs. This proves a treatment voice server was never
     paired with a control gateway, or the reverse.

---

## 2. M1: One proactive status per run (canary)

**Problem.** Nothing is heard for a long time while Hermes works. In 4 of the 5 tasks with long waits the user
asked "are you still there?", and τ² ends the call after 40 s of inactivity (simulated time). Only 4
`status_spoken` events happened in the whole run.

**Change** (`engine/turn_manager.py`):

- **Timer:** one per session. It starts when the backend enters WORKING and resets on every audio release
  and every `speech_started`.
- **When it fires:** the backend is WORKING, the user is not speaking, the output lane is empty and idle, no
  decision is pending, and `after_s` has passed since the last reset.
- **What it says:** one very short line (5–8 words) from `status_proactive_lines` in `prompts.yaml`, for
  example "Still checking, one moment." No LLM call.
- **How it plays:** as a `status` item with `meta.epoch`. The existing `_status_current` check drops it if
  the run finished before it played.
- **Limit:** `max_per_run: 1` for the canary.
- **History:** it is added as `status_speech`, so Hermes sees it as a progress update (existing path).

```yaml
output:
  proactive_status: {enabled: false, after_s: 15, max_per_run: 1}   # code defaults; after_s is calibrated (below)
```

**Calibration on the target host (before the canary).** The airline and retail runs ran on different machines,
and the ratio between simulated time and wall clock depends on host, services and concurrency.

1. Run the control profile for the canary task set on the target host.
2. For every WORKING span with no audio, record the wall-clock gap from the voice events. Record the
   simulated-time gap from the τ² message timestamps of the same span.
3. Fit the sim/wall ratio on that host. Set `after_s` so that the status plays well before 40 s simulated
   inactivity: at most about 20 s simulated, and not below the 75th percentile of normal
   filler-to-answer gaps on passing tasks, so that fast answers are never preceded by a status line.
4. Record both values and the ratio in §9. Every evaluation reports proactive-status gaps in both clocks.

**Event:** `status_proactive {turn_id, run_id, epoch, text, silence_wall_s}`. The report adapter adds the
simulated gap from the τ² timeline.

**Tests** (fake clock, `test_fdh_voice_turns.py`): fires once after `after_s` of silence while WORKING; never
twice in one run; not while the user speaks, while output is queued or playing, or when IDLE; resets on
audio release; dropped if the run finished first.

---

## 3. M3: Spelled identifiers and names (canary)

Four parts. Each has its own switch, so a regression can be traced to one part.

### 3.1 Spelled-run normalization (`normalization/transcript.py`, shared)

- **Today:** the normalizer joins a span only if it contains a separator word ("underscore"). Spelled codes
  and names ("I, F, O, Y, Y, Z", "R O S S I") pass through as single letters.
- **The new rule:** after the anchor pass, join a run of at least `min_tokens` consecutive single letters
  or single-digit tokens (digit words count) in the text the anchor pass left alone.
  - Commas and filler words ("uh") may appear inside the run.
  - The run must contain at least one letter, so digit-only runs are unchanged.
  - A stop word of two or more letters ("and", "is") ends the run, so
    "I, F, O, Y, Y, Z and N, Q, N, U, five, R" becomes `IFOYYZ and NQNU5R`.
- **Case:** letters keep the ASR case (`case: keep`, decided in review 2): `ROSSI`, not `Rossi`. The
  normalizer cannot tell a name from a reservation code, and title-casing would corrupt codes and mixed
  fragments. Any domain-specific name casing belongs in the preparation of a name tool argument, not in
  the transcript normalizer. That is out of scope for this plan.
- **Where it applies:** only in the text the agents see. The raw ASR text stays on the wire.

```yaml
normalization:
  transcript:
    spelled_runs: {enabled: false, min_tokens: 3, case: keep}   # shared base: off; on in FDH voice/tau3_spelling.yaml (M3 arm)
```

Tests (`test_transcript_normalizer.py`):

- The examples above are joined as shown.
- Two codes separated by "and" stay two codes.
- These are unchanged: a digit-only run, "I want a flight", and every existing underscore case.
- With the switch off, every output is byte-identical to today's.

### 3.2 Evidence-gated spelling hold (`engine/turn_manager.py`, `_decide`)

A turn is held for `hold_ms` before the frontend decides, **only if all three conditions hold**. A
"single character" below is one letter, one numeral digit, or one digit word.

1. **It ends mid-spelling.** The last token is a single character or a separator word. Trailing
   punctuation is ignored.
2. **It has strong spelling evidence**, at least one of:
   - the trailing run of single characters has at least two tokens **and at least one letter** (a
     digit-only run such as "five zero zero" is an amount or a count, not evidence);
   - a separator word ("underscore") is among the last four tokens;
   - the turn has at least two tokens and consists only of single characters and separators, with at least
     one letter;
   - the previous turn was merged by a hold (an active accumulator).
3. **The value looks incomplete.** Take the value at the end of the normalized turn: the last anchored span,
   or the trailing spelled run (§3.1). It is complete if it fully matches one of these:
   - the `normalization.tool_arguments.rules[*].pattern` of the profile (τ³: user ID
     `^[a-z]+_[a-z]+_\d{4}$`);
   - the profile's `spelling_hold.complete_patterns`, matched against the trailing spelled run.

   A complete value is not held.

If speech starts during the hold, the existing cancel-and-merge path in `on_speech_started` joins the text to
the next turn, so no new merge code is needed. Holds chain through the accumulator.

This rule does not fire on ordinary endings: "I," "one" or "five zero zero" alone fail condition 2, and a
complete `name_name_1234` or a complete reservation code fails condition 3.

**Canary patterns per domain** (`complete_patterns`, in the τ³ profile of each domain):

| Domain | Fixed-format identifier | Pattern (on the joined spelled run) | Status |
|---|---|---|---|
| Airline | Reservation code (6 letters or digits, for example `IFOYYZ`, `K1NW8N`) | `^[A-Za-z0-9]{6}$` | Canary |
| Retail | Order ID (`#W` + 7 digits) | added when retail becomes eligible (§8, stage 3) | Later |
| Any | User ID | already covered by the tool-argument rule | — |

**Names are variable-length, and there is no reliable completion signal for them.** A fully spelled name
("R O S S I") is therefore held for `hold_ms`. **This is deliberate:** a name is often followed by
"underscore …" or by a surname in the next ASR segment, and joining those is the point of the hold. The cost
is counted below.

A fixed-length pattern has one side effect. A 6-letter name ("A M E L I A") matches the reservation-code
pattern and is not held. A missed hold only falls back to today's behaviour; it is never worse than the
baseline.

**Measured on the airline events** (1,057 ASR finals; review 2 fixture, run with the repository's
`TranscriptNormalizer` for anchored spans, spelled runs emulated as in §3.1, the predicate above, and the
airline code pattern). "Continued" means the next `speech_started` came within 1.5 s:

| Outcome | Continued (merge) | Not continued (hold adds latency) |
|---|---|---|
| Held | 51 | 52 |
| Not held: complete user ID | 2 | 8 |
| Not held: complete 6-character code | 1 | 6 |

- **Continuations merged:** 51. Of these, 28 are underscore-ID fragments and 23 are spelled codes or names
  without a separator.
- **Holds that add latency:** 52, about one per task, at 1.5 s each. Of these:
  - 31 end on an incomplete underscore-ID fragment, such as "…underscore nine", where the lookup would
    fail anyway;
  - 21 end on a spelled run without a separator, such as a partial code ("GV one N six four"), an airport
    code ("J F K") or a name.
- **Evidence change:** requiring a letter removed the digit-only false holds of the review 1 rule, such as
  "Five zero zero." and "One four four."
- **Complete IDs:** they end on a digit-only run, so condition 2 already excludes almost all of them;
  condition 3 excludes the rest.
- **The fixture emulates the spelled-run joiner.** Re-run it with the implemented normalizer and
  predicate before the canary, and record the counts in §9. The canary is blocked if the held/continued
  counts move by more than 10%.

```yaml
delegation:
  spelling_hold: {enabled: false, hold_ms: 1500, complete_patterns: []}   # code defaults
# config/profiles/tau3_airline_canary.yaml: complete_patterns: ['^[A-Za-z0-9]{6}$']
```

**Tests:**

- Held:
  - "A, A, R, A, V, underscore, A, H" with speech during the hold is merged and decided once;
  - "R O S S I" is held;
  - "G V one N six" (5 characters) is held.
- Not held:
  - "…underscore six six nine nine" (complete user ID);
  - "I F O Y Y Z" with the airline pattern;
  - "I" alone, "one" alone and "five zero zero";
  - a turn ending in an ordinary word.
- The accumulator holds a single-letter follow-up after a merged hold.
- With the switch off, no turn is ever held.
- A fixture test runs the predicate over a checked-in sample of about 40 airline ASR finals (text only, no
  audio) with the expected held/not-held labels.

### 3.3 Local "invalid identifier" wording (no bypass)

Invalid identifiers are still always answered locally. Only the wording changes, and it gets firmer when the
same thing repeats.

- **New keys** in `voice_frontend_backend_agent/config/prompts.voice.yaml`. They are additive; the old keys
  stay, and the baseline selects them.
  - `tool_argument_invalid_readback`: "Not looked up: "{value}" is not a complete {label}. Read back what you
    heard, character by character ({spelled}), and ask the user to spell only the part that is missing or
    wrong. Do not mention the format."
  - `tool_argument_invalid_spell_all`: for the second and later invalid answers of the same tool in one
    session. "Not looked up again: the {label} is still incomplete ("{value}"). Ask the user to spell the
    whole {label} slowly, letter by letter, including separators. Do not guess."
- **New setting:** `normalization.tool_arguments.escalate_invalid_message_key`. It is empty by default,
  which keeps a single message. `ToolRelay` keeps the per-tool count of invalid answers next to `_failed`.

Tests: the first invalid call gets the read-back message; the second gets the spell-all message; the call is
never sent; with the baseline keys the messages are byte-identical to today's.

### 3.4 Backend prompt variant `spelling_v2` (`prompts.backend.yaml`)

When `prompt_features.spelling_v2` is true, this replaces the current one-line spelling rule:

> Spoken identifiers and names may arrive spelled out; spelled letters are already joined (for example
> ROSSI or IFOYYZ). If a lookup by an identifier or a name fails, do not retry with a guess or a variant.
> Read back what you heard, letter by letter, and ask the user to spell it. If you already found the user's
> account in this conversation, do not ask for their identifier again.

**Events:** `transcript_normalized` (existing, now with spelled-run spans); `spelling_hold {turn_id, hold_ms,
evidence, value, merged}`; `call_answered_locally` gains `message_key`.

---

## 4. M2: Replay an unheard answer (experimental, step 4)

**Status.** Experimental. The evidence is weak: an unheard answer happened in 82% of the failed tasks and 79%
of the passing ones, so it does not separate failures from passes. M2 is evaluated alone, after the gate in
§8, and only enabled if its own arm passes that gate.

**RC2 (approved narrowly in review 2, extends RC1).** The voice server interprets `request="status"` in IDLE
only when M2 is enabled **and** every replay guard below passes. If any guard fails, the turn follows
today's delegation path unchanged. The gateway keeps ignoring `request` in IDLE, `workflow.csv` is not
edited, and M2 stays off by default and experimental.

**State the voice server must keep (new).** Today the voice side keeps neither the latest finished run nor
anything per answer. These additions are needed:

| Where | Field | Set when |
|---|---|---|
| `Entry` (`engine/transcript.py`) | `run_id: str` | `add_spoken` for a `backend_answer`, from the gateway `answer.run_id` |
| `Entry` | `released_mono: float \| None` | `_send_output` logs `output_release` for the item carrying this entry (the first release only) |
| `Entry` | `replayed: bool` | set to true when the answer is replayed |
| `_Backend` (`engine/turn_manager.py`) | `last_finished_run_id: str` | `_gw_backend_run_done` |
| `_Backend` | `last_finished_request: str` | `_gw_backend_run_done`: the text of the first turn in `turn_ids`, or `current_request` if none is known |
| `_Backend` | `last_task_turn_id: int` | `_apply` when a turn is delegated with `request=task` (and on every delegation when M2 is off) |

`Entry.wire()` does not send these fields, so the gateway protocol is unchanged.

**Gateway message order** (`backend/controller.py`, settling a run): `answer` (with `run_id`), then
`backend_run_done`, then either `state: IDLE` or, for a leftover steer, a new `pending_steer` run and
`state: WORKING`. The guards therefore read state that is final only after `backend_run_done`, and the
backend state at decision time must be IDLE.

**Replay only when all of these hold:**

1. **Explicit intent.** The frontend decision is `delegate=true` with `request="status"`. The frontend prompt
   variant `replay_intent` adds: `"status"` also when nothing is running and the user asks what you found,
   asks for an update, or asks you to repeat. The model is told only `your last answer was not heard: yes`,
   never the text.
2. **Bound to the same run and request.** The backend is IDLE. `entry.run_id == last_finished_run_id`, and
   that run's request is `last_finished_request`.
3. **No other task since the answer.** `last_task_turn_id` is lower than the turn that produced the answer.
   No run started after `last_finished_run_id`: no `state: WORKING` with a newer epoch.
4. **Short TTL.** `now - entry.released_mono <= ttl_s` (wall clock, default 30). An answer that was never
   released (dropped while queued) has no `released_mono` and is not replayed.
5. **Not replayed before.** `entry.replayed` is false.

**Partial and not-heard answers are handled differently:**

- `not_heard`: replay the whole answer.
- `partial`: replay from the start of the sentence that was cut off. The frontend history still shows the
  heard part.
- `partial` where the heard text already contains the whole last sentence (the question): do not replay,
  and delegate as today.

**What a replay does:**

- Nothing is sent to the gateway as a `delegate`.
- The user turn is recorded on the context route.
- The replay is spoken as `frontend_speech`, so Hermes gets `[the voice frontend told the user] "…"`.
- The filler is skipped.

If any condition fails, the turn is delegated as today, and Hermes has the delivery note.

```yaml
delegation:
  replay_unheard_answer: {enabled: false, ttl_s: 30}
# prompts: prompt_features.replay_intent (frontend), false in the baseline
```

**Event:** `answer_replayed {turn_id, answer_id, run_id, outcome_before, age_s, chars}`, and
`answer_replay_skipped {turn_id, reason}` with the failed condition as the reason.

**Tests:**

- Replays: `not_heard` whole; `partial` from the cut sentence.
- Does not replay:
  - after an intervening delegated task;
  - after a newer run;
  - after the TTL;
  - a second time;
  - on `request=task`;
  - when the partial answer's question was heard.
- Gateway message ordering:
  - `answer` then `backend_run_done` then `state: IDLE`, with the status turn after all three: replays;
  - status turn decided after `answer` but before `backend_run_done` (run not finished): delegated as
    today;
  - `backend_run_done` then a `pending_steer` run (`state: WORKING`): no replay, and the WORKING status
    path applies;
  - `backend_run_done` for a newer run whose answer was empty or an apology: no replay of the older
    answer.
- State: `released_mono` is set on the first release only; an entry dropped while queued is never
  replayed; `run_id` comes from the gateway `answer`.
- With M2 off, every turn's decision and messages are identical to today's.
- `delegation_cases.jsonl` gets decision cases for "did you find anything?", "sorry, can you repeat that?"
  and "what about my other booking?" after an unheard answer. The last one expects `request=task`.

---

## 5. G1: Filler de-duplication (separate ablation)

**Problem.** 7 failed tasks repeated the same filler 4 or more times (task 23: 11 times).

**Change.** In `_apply`, for delegated fillers only, compare the normalized filler with the last `recent`
fillers of the session.

- On a repeat while WORKING: drop it.
- On a repeat otherwise: use the next unused line from `filler_alternatives`, or drop it if all were used
  recently.
- A dropped filler follows the `speak_when_delegating: false` path, including the empty response for a
  client `response.create`.

`delegation.filler_dedupe: {enabled: false, recent: 3}`. Event `filler_deduped`. Unit tests for drop, replace
and the `requested` turn.

## 6. G2: Short spoken answers (separate ablation, gated by semantic tests)

The goal is shorter answers without losing the details a valid confirmation needs.

- **Prompt variant `spoken_output`** (backend): a block after `</policy>`, with these rules:
  - At most two short sentences.
  - The question comes last.
  - No product or item IDs **unless the user must confirm by them**.
  - Give one or two options and offer the rest.
  - After a cut-off, repeat the key point.
  - **Confirmation override:** before a booking, modification, cancellation, return or exchange, state every
    detail the policy requires the user to confirm, even if that takes more than two sentences.
- **Cleanup before TTS** (`engine/spoken_text.py`, `output.clean_answers`):
  - Strip markdown emphasis, headings, code ticks and URLs.
  - Turn each list item into **its own sentence**, keeping each item's values in that sentence, so no
    item-to-value pairs are merged.
  - Apply it in `_gw_answer` before `add_spoken`, so the transcript records what was spoken.
- **Semantic tests before any τ³ run:**
  - A fixture of backend confirmation answers for booking, flight modification, cancellation, retail return
    and retail exchange.
  - Taken from the airline events where the text is available; otherwise written from the policy.
  - Assert that after cleanup every item name, price, payment method and quantity is still present, and
    still attached to its own item.

## 7. G4: Consent and read-back before writes (separate ablation)

Prompt variant `write_consent` (backend):

> Treat a "yes" as consent to a change only if your question with the change details was heard in full. If a
> note says it was cut off or not delivered, ask again. Before a change that writes a name or an identifier the
> user spoke, spell it back and wait for a yes.

Targets task 14 (a cancellation on a "go ahead" said to a filler) and task 40 ("Mei" written as "May").

---

## 8. Evaluation and non-regression gate

**Paired arms.** The control is `tau3_eval_baseline.yaml` on the same commit as the treatment. Both arms use
the same host, NIM and LLM endpoints, τ² commit, concurrency, user-simulator seed and speech complexity. Only
the one switch under test differs. Each evaluation is two paired repetitions.

**Task sets.**

- **Failed targets (17):** 7, 12, 14, 17, 18, 20, 21, 22, 23, 24, 29, 32, 35, 37, 39, 40, 44.
- **Passing sentinels (10).** Chosen from the 33 passing tasks to cover what the changes touch:
  - ID not-found recoveries: 4, 9, 43, 46, 48.
  - Long waits: 15, 38, 42.
  - Short, clean tasks: 0, 47.

**Staged gate for each change.**

| Stage | Run | Pass condition |
|---|---|---|
| 1. Sentinel | Failed targets + sentinels, 2 paired repetitions | Targets addressed by the change improve (more passes, or the targeted metric moves) **and** no sentinel regresses in either repetition |
| 2. Full | All 50 airline tasks, 2 paired repetitions | At most **one** previously passing task regresses (in both repetitions), **and** overall Pass^1 does not decrease in either repetition |
| 3. Retail check | Retail on the same host, once the retail control is complete and clean (no "too many clients" retries) | Pass^1 does not decrease against the retail control |

A change that fails any stage stays off, and the next change is evaluated against the same control.

**Host and fingerprint checks before scoring.**

- Both arms ran on the canary host (§0 decisions).
- Every session's deployed fingerprint matches its arm's declared fingerprint (§1, rule 5).
- There were no infrastructure errors. An arm with a mismatch or an infrastructure error is re-run, not
  scored.

**Metrics reported per arm.** Produced by the per-task stats script used for
`airline_per_task_agent_stats.csv`, plus the new events:

- Pass^1, with the per-task pass/fail diff against the control.
- "Any update?" prompts; tasks ending in "gave up / taking too long".
- Answers not heard or partial.
- ID not-found lookups and `call_answered_locally` by message key.
- Max repeated filler.
- Answer length.
- Mean and max user-stop → first audio.
- `status_proactive`, with its gap in wall and simulated time.
- `spelling_hold` (held, merged) and `answer_replayed` / `answer_replay_skipped`.

## 9. Measurements to record

| Item | Value |
|---|---|
| M1 target host, sim/wall ratio at the canary concurrency | to measure |
| M1 `after_s` (wall) and its simulated equivalent | to measure |
| M3 hold predicate on the airline finals, **fixture** (emulated joiner) | held 103: 51 continued, 52 not; not held: complete user ID 10, complete code 7 (§3.2) |
| M3 hold predicate on the airline finals, **implemented** | held 103: 51 continued, 52 not; not held: complete user ID 10, complete code 7. Identical to the fixture (0% change): the canary is not blocked (`cli/spelling_hold_replay.py`, profile `tau3_arm_m3_spelling.yaml`) |
| Golden prompt files at `3a7e04a` | created before the first prompt edit: `tests/unit/prototypes/delegation/fixtures/golden_prompts.json` (13 renders; re-rendered from a clean `3a7e04a` checkout: identical) |
| Canary host and its fresh control arm | to record |

## 10. Review traceability

### Review 1 (resolved in revision 2)

| Finding | Resolution |
|---|---|
| P0: G3 violates the one-tool-call policy | G3 dropped (§0). `homes.py` unchanged |
| P1: the ablation cannot reproduce the prompts | Prompt variants behind feature maps, golden-file byte-equality test, baseline profile (§1) |
| P1: M4 bypassed identifier validation | Bypass removed. The local answer stays and escalates its wording (§3.3) |
| P1: the hold predicate was too broad | Three-condition predicate with strong evidence and an incomplete-ID check. Airline counts in §3.2 |
| P1: M2 promoted on non-discriminative evidence; stale replay | M2 is experimental, step 4. Run binding, no intervening task, TTL, explicit intent, partial vs not-heard handling (§4) |
| P1: no airline non-regression gate | Paired arms, sentinels, staged gate, two repetitions (§8) |
| P1: the 15 s timer is not justified across machines | `max_per_run: 1`, calibration on the target host, both clocks reported (§2) |
| P2: G2 could remove details needed for consent | Confirmation override, one sentence per list item, semantic tests before any run (§6) |
| Retail should not drive defaults | Stated in the header. Retail only in gate stage 3 against a clean control |

### Review 2 (resolved in revision 3)

| Finding | Resolution |
|---|---|
| P1: M3 completeness only knew the user-ID pattern; codes and names would be held | `complete_patterns` per domain, airline code `^[A-Za-z0-9]{6}$` for the canary. Names are held deliberately (documented, with cost). Letter-required evidence. Fixture re-run with the repository normalizer; the implemented predicate is re-measured before the canary (§3.2, §9) |
| P1: M2 state model missing | `Entry.run_id`, `released_mono`, `replayed`; `_Backend.last_finished_run_id`, `last_finished_request`, `last_task_turn_id`; where each is set; gateway ordering tests (§4) |
| P2: deployed prompt/config fingerprints | Rule 5 (§1) and the pre-scoring check (§8) |
| Decisions: RC2 narrow, keep ASR case, original airline host | §0 decisions, §3.1, §4 |

## 11. Documentation

These change user-visible config, prompts and spoken behaviour. With each implemented step, update:

- `prototype-plan.md`: a revision entry, and RC2 (approved narrowly, §4) next to RC1 when M2 is
  implemented;
- `runbook.md`: the keys, the baseline profile, the new events;
- the prototype `README.md`;
- `misc/prototypes/voice/normalization-plan.md`: spelled runs and escalating invalid wording.

Per `AGENTS.md`, a documentation subagent reads `docs/AGENTS.md` and runs the documented validation.

## 12. Open questions (see also §13)

Review 2 resolved the earlier questions (§0 decisions). Still open:

1. Is the original airline host available? If not, which dedicated stable host (§0)?
2. The retail order-ID `complete_patterns` entry, once retail is eligible for stage 3.

---

## 13. Implementation (revision 4)

Everything in §1–§7 is implemented, off by default, with tests. `uv run pytest tests/` passes except one
test that already failed before this change (`test_voice_config.py`: it expects 13 voice-package profiles
and there are 14).

| Part | Where | Switch | Tests |
|---|---|---|---|
| Prompt variants | `prompt_features.py`; `frontend/prompts.py`, `sidecar/templates.py`; `prompts.yaml`, `prompts.backend.yaml` | `prompt_features` (`delegation_agent.yaml`, `gateway.yaml`) | `test_fdh_golden_prompts.py` (byte equality with `3a7e04a`, each variant changes only its block) |
| Fingerprints | `engine/turn_manager.py` (`fdh_session_start`, `frontend_prompt`, `backend_configured`), `backend/controller.py` (`session.configured`), `sidecar/gateway_server.py` (`/health`, `gateway_start`), `cli/fingerprint_check.py` | always on | `test_fdh_tau3_fixes.py` `Fingerprint*` |
| M1 | `engine/turn_manager.py` `_maybe_proactive_status` | `output.proactive_status` | `ProactiveStatusTests` |
| M3.1 | `voice_frontend_backend_agent/normalization/transcript.py` | `normalization.transcript.spelled_runs` | `test_transcript_normalizer.py` `SpelledRunTests` |
| M3.2 | `engine/spelling_hold.py`, `engine/turn_manager.py` `_spelling_hold`, `cli/spelling_hold_replay.py` | `delegation.spelling_hold` | `SpellingHold*Tests` (40 airline finals in `fixtures/spelling_hold_airline_finals.jsonl`) |
| M3.3 | `normalization/arguments.py`, `tools/relay.py`, `prompts.voice.yaml` | `normalization.tool_arguments.escalate_invalid_message_key`, `invalid_message_key` | `test_argument_normalizer.py`, `InvalidWordingTests` |
| M3.4, G2 prompt, G4 | `prompts.backend.yaml` | `spelling_v2`, `spoken_output`, `write_consent` | golden-prompt tests |
| M2 | `engine/turn_manager.py` `_replay`, `_replay_check`; `engine/transcript.py` (`run_id`, `released_mono`, `replayed`) | `delegation.replay_unheard_answer` + `replay_intent` (config fails without it) | `ReplayTests` (guards, partial, ordering); 3 cases in `delegation_cases.jsonl` (`requires_feature`) |
| G1 | `engine/turn_manager.py` `_dedupe_filler` | `delegation.filler_dedupe` | `FillerDedupeTests` |
| G2 cleanup | `engine/spoken_text.py` | `output.clean_answers` | `SpokenTextTests` (5 confirmation fixtures), `CleanAnswersTurnTests` |

**Profiles** (`config/profiles/`, gateway files in `config/`), one change per arm (runbook §6.1):

| Arm | Voice profile | Gateway config |
|---|---|---|
| Control | `tau3_eval_baseline.yaml` (same config hash as `tau3_eval.yaml`) | `gateway.yaml` |
| M1 | `tau3_arm_m1_status.yaml` (`after_s` from `FDH_PROACTIVE_AFTER_S`, default 15) | `gateway.yaml` |
| M3 | `tau3_arm_m3_spelling.yaml` (voice profile `voice/tau3_spelling.yaml`) | `gateway.spelling_v2.yaml` |
| M1 + M3 | `tau3_airline_canary.yaml` | `gateway.spelling_v2.yaml` |
| M2 | `tau3_arm_m2_replay.yaml` | `gateway.yaml` |
| G1 | `tau3_arm_g1_filler_dedupe.yaml` | `gateway.yaml` |
| G2 | `tau3_arm_g2_short_answers.yaml` | `gateway.spoken_output.yaml` |
| G4 | `tau3_eval_baseline.yaml` | `gateway.write_consent.yaml` |

**Where the implementation refines the text above:**

- §1 rule 5: `frontend_prompt_sha256` is in its own `frontend_prompt` event (see rule 5). The gateway also
  logs `backend_fingerprint` per session. Hashes are the first 16 hex digits of SHA-256.
- §1 rule 2: adding the new default keys changed `config_hash` of every profile against runs made before
  this change. Compare arms by the fingerprint, not by `config_hash` across commits.
- §3.1: `oh` is not a spelled digit (it is also the letter O); dotted letters ("R.A.J.") count as one
  spelled token. The tool-argument canonicalizer never joins spelled runs, so tool arguments are unchanged.
- §3.2: a turn started by a client `response.create` (text clients) is never held. The event also carries
  the `value` that was checked.
- §3.3: the wording is in `prompts.voice.yaml`; both messages also say "No tool was called".
- §4: the replay is its own output kind (`replay`): it goes first like the turn's own reply, and a stale
  replay is kept like an answer. A replayed turn also logs `delegation_decision` with `replay: true`.
  The "no other task" guard compares `last_task_turn_id` with the newest turn of the finished run
  (`backend_run_done.turn_ids`), so a steer that the run absorbed does not block the replay.
- §5: fillers are compared lower-cased without punctuation; only delegated fillers are recorded.

---

## Appendix A. Context: everything this plan was built from

This appendix lets another agent continue without the conversation that produced the plan. It is a record,
not a spec: where it and §0–§12 differ, §0–§12 win.

### A.1 Paths

| What | Path |
|---|---|
| Airline dump (evidence) | `/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump/tau-3-voice/2026-09-29_17-04-09Z_fdh-voice/` |
| Failure analysis in the dump | `observations/airline_failed_tasks_analysis.md`, `observations/failed_tasks/task_N.txt` (scenario, expected actions, reward, condensed timeline), `observations/airline_per_task_agent_stats.csv` |
| Voice-server events (airline) | `fdh_voice_dlg_airline_regular/agent/events.jsonl`; gateway events in `gateway_events.jsonl`; deployed config snapshot in `agent/config/`; provenance (agent commit `3a7e04a`, no uncommitted diff) in `fdh_voice_dlg_airline_regular/provenance/` |
| Retail run in progress (another machine) | `/localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar/data/simulations/fdh_voice_dlg_retail_regular` |
| Agent logs on this machine (retail run, not airline) | `/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/logs/` (`fdh_voice_events.jsonl`, `fdh_gateway_events.jsonl`, `fdh_workers/`) |
| τ² policies | `tau2-bench-smasurekar/data/tau2/domains/{airline,retail}/policy.md` |
| Hermes checkout | `/localhome/local-smasurekar/smasurekar/hermes-agent-smasurekar` |

The airline run was executed on a different machine from the retail run.

### A.2 The original proposal (from the user, before this plan)

The user proposed these changes before any analysis. The plan kept, reshaped or dropped each one (see §0):

1. **Long-running tasks.** A proactive verbalized status after about 15–20 s of WORKING with no spoken
   output. It must be very short. Four of the five tasks with long waits had "are you still there?", and τ²'s
   40 s inactivity limit is exactly this failure. The status path exists; only a timer is needed.
   → **M1**.
2. **Prompt/few-shot tuning for the status examples.** Cheap, but it had already failed with the current
   wording. → **Dropped**.
3. **Spelling mistakes in names.** Ask the user to spell the name after a failed lookup, and join spelled
   letters. The backend prompt already says to ask for spelling (`prompts.backend.yaml`), and transcript
   normalization handles spelled letters only when assembling IDs, not plain names like "R O S S I". The
   gaps: the prompt does not always win (retail task 9 kept retrying), and joining spelled names needs code.
   → **M3**.
4. **Fix 1, barge-in.** Stop playback only on real speech (at least ~500 ms, or an ASR word that is not a
   backchannel), and resume or re-speak the rest after a backchannel. Where: `barge_in` in
   `config/voice/base.yaml` plus the voice server's barge-in code. → **Dropped**, because most barge-ins
   were real speech (A.4).
5. **Fix 2, short Hermes answers.** A spoken-output rules block after `<policy>`:
   - at most 2 sentences (~35 words);
   - the question last;
   - no item IDs;
   - 1–2 options, offering more;
   - after a cut-off, repeat only the key point.

   Also strip markdown and lists before TTS. The data behind it: median answer 199 characters, max 1,190;
   the SOUL rule "1–3 short sentences" loses to the policy's "list the action details".
   → **G2** (with a confirmation override).

The question asked was: "Is anything else generic needed, based on the 17 failed airline tasks?"

### A.3 The airline run and its 17 failures (from the dump's analysis)

- **Run:** `fdh_voice_dlg_airline_regular`, the FDH `dlg` arm with filler spoken, speech complexity
  `regular`, concurrency 4. Pass^1 0.660 (33/50). All 17 failures failed the DB check; none failed only on
  COMMUNICATE. There were no agent errors, infrastructure errors or disconnects.
- **Comparison:** the best FBA arm (`2026-09-29_04-32-39Z_fba-voice`, `verdictspk_normhist`) scored 0.62,
  and 13 of these 17 tasks also fail there.

| Category | Tasks | Count |
|---|---|---|
| A. Slow or unheard responses (the user hangs up while the agent works) | 14, 18, 20, 21, 22, 23 | 6 |
| B. Spelled-ID capture loop (ASR mangles or splits a spelled ID) | 7, 12, 17, 35, 37 | 5 |
| C. Backend reasoning or policy error | 24, 29, 32, 39, 44 | 5 |
| D. ASR value error inside a write argument | 40 | 1 |

**How the calls ended:**

| Ending | Tasks |
|---|---|
| Gave up | 7, 14, 17, 18, 20, 21, 22, 35, 37 |
| Went out of scope | 23, 32 |
| User asked for a transfer | 44 |
| Agent transferred | 24 |
| Ended normally, but the task was not done | 12, 29, 39, 40 |

Per-task notes that drove specific changes:

- **7:** two spoken reservation IDs merged into `XEHM4B59XX6W`, and the user ID was misheard, giving 4
  not-found lookups. → M3.1, "and" ends a spelled run.
- **12:** the reasoning was correct, but the agent re-asked for a user ID it had already verified, ASR
  misheard it, and the bag write never ran. → M3.4, last sentence.
- **14:** it cancelled K1NW8N on a "go ahead" said in reply to a filler; the confirmation question was never
  heard. Mean latency 20.7 s. → G4, M1.
- **17:** the spelled ID was split across turns, and ASR dropped a letter; then about 20 s of silence.
  → M3.2, M1.
- **18:** 12 sequential `search_direct_flight` calls, mean latency 53 s (max 103 s), with "any numbers
  yet?". → M1. (G3 would have helped but is forbidden by policy.)
- **20, 22, 23:** answers not heard; the user asked for updates 2–4 times; in task 23 one filler repeated 11
  times. → M1, M2, G1.
- **21:** `sofia` was heard as `sophia` several times; one filler was repeated 7 times. → M3, G1.
- **24, 29, 32, 39, 44:** backend reasoning. Task 29 modified a reservation in place instead of cancelling and
  rebooking. Task 32 combined two steps and used a payment method the user never chose. Task 24 transferred
  while a second request could still be done. Task 44 said "I don't have access to flight duration data" and
  had one failed Hermes run. Task 39 is label-sensitive. → out of scope (policy rules dropped).
- **35:** "Let me look up your account details" played 4 times; the user complained. → G1.
- **37:** the spelled ID was split over about 6 turns. Local `invalid` answers made Hermes repeat "I need
  your complete user ID in the format firstname_lastname_1234"; no lookup ever ran, even after a complete
  single-turn spelling. → M3.2, M3.3.
- **40:** "Mei" was heard as "May" and written without a read-back. → G4.

**Contributing factors:**

| Factor | Failed | Passed |
|---|---|---|
| An ID lookup failed on an ASR-mangled ID | 8/17 (47%) | 9/33 (27%) |
| User gave up, went out of scope or asked for a transfer | 11/17 | 9/33 |
| At least one answer not heard | 14/17 (82%) | 26/33 (79%) — **not discriminative**; this is why M2 is experimental |
| Mean response latency | 16.3 s | 12.3 s |
| Same filler repeated 4+ times | 7 tasks | — |

### A.4 Extra measurements from the airline voice events (this plan's own analysis)

All from `fdh_voice_dlg_airline_regular/agent/events.jsonl`.

- **Event counts:**
  - `asr_final` 1,057; `delegation_decision` 620; `decision_cancelled` 360 (cancel-and-merge on new speech);
  - `barge_in` 396; `backend_answer` 419; `delivery_note` 378;
  - `status_spoken` **4**;
  - `call_answered_locally` 29 (reason `invalid` 25, `already_failed` 4);
  - `transcript_normalized` 170; `empty_transcript` 77.
- **What followed each barge-in** (the next ASR result in the session):

  | Next ASR result | Count |
  |---|---|
  | 3 or more words (real speech) | 241 |
  | 1–2 words | 73 |
  | Empty | 54 |
  | Backchannel | 23 |

  So only about 20–35% of barge-ins are backchannels or noise, which is why Fix 1 was dropped.
- **Mid-spelling ASR finals:** 144 of 1,057 (14%) end on a single letter, a digit, a digit word or
  "underscore".
  - The gap to the next `speech_started` is bimodal: 74 within 1.5 s, 83 within 2 s, and about 50 where the
    next speech comes 20 s or more later.
  - Trailing punctuation does not separate the groups: "." appears in 47 continuations and 29 ends.
- **Spelling-hold predicate iterations** (the §3.2 table is the final one):
  - "Ends mid-spelling" alone: too broad.
  - Strong evidence alone: it included complete IDs (86 held, of which 70 did not continue), because
    complete IDs end in digits.
  - Adding the completeness check and a required letter gave §3.2.
- **Fixture method** (not checked in; re-create it when implementing):
  - Load the events and group them by session.
  - For each `asr_final`, apply the §3.2 conditions. Use
    `prototypes.voice_frontend_backend_agent.normalization.transcript.TranscriptNormalizer(TranscriptSettings(enabled=True, case="lower"))`
    for anchored spans. Emulate spelled runs by joining the trailing run of single characters, mapping digit
    words to digits.
  - Label the next `speech_started` within 1.5 s as "continued".

### A.5 Code facts the plan relies on (at `2f387d2`, code identical to `3a7e04a`)

| Fact | Location |
|---|---|
| A status is spoken only on `request=status` while WORKING: the gateway builds a summary, and the voice server verbalizes it (LLM, template fallback) and drops it if the run finished | `backend/controller.py` `delegate()` / `_status_locked()`; `engine/turn_manager.py` `_gw_status()` / `_speak_status()`; `frontend/status_verbalizer.py` |
| Output lane: calls first; audio never over the user; backend speech held while a decision is pending; `hold_max_ms` 4000 with `on_stale` per kind | `engine/output_scheduler.py` |
| Cancel-and-merge: speech before a decision returns cancels it and prefixes its text to the next turn | `engine/turn_manager.py` `on_speech_started()` / `on_user_input()` |
| Barge-in settles each spoken entry as heard, partial or not heard; a non-heard `backend_answer` creates a `delivery_note` for Hermes's next run | `engine/turn_manager.py` `_interrupt_output()`; `engine/transcript.py` `settle()` |
| The frontend history shows only heard text, so the frontend model does not know an answer was unheard | `engine/transcript.py` `frontend_messages()` |
| Gateway settle order: `answer` → `backend_run_done` → `state` (or a `pending_steer` run) | `backend/controller.py` settle / `_answer()` |
| The transcript normalizer rewrites only spans anchored by a separator word ("underscore") | `voice_frontend_backend_agent/normalization/transcript.py` |
| Tool-argument guard: `get_user_details.user_id` must match `^[a-z]+_[a-z]+_\d{4}$`, otherwise it is answered locally with `tool_argument_invalid` ("…Ask the user to say their complete {label} again…"); the retry guard answers repeats of a not-found call with `tool_call_already_failed` (reads back `{spelled}`) | FDH `config/voice/tau3.yaml`; `voice_frontend_backend_agent/normalization/arguments.py` `screen()`; `voice_frontend_backend_agent/config/prompts.voice.yaml` (keys near line 383); `tools/relay.py` `_screen()` |
| Backend prompts: SOUL "one to three short sentences"; `backend_system` has one spelling rule, then `<policy>` | `config/prompts.backend.yaml` |
| The frontend prompt already says "Do not repeat the same acknowledgement twice in a row" (ignored in practice) | `config/prompts.yaml` `frontend_system` |
| Hermes `parallel_tool_call_guidance: False` (keep: policy forbids parallel calls) | `sidecar/homes.py` `REQUIRED_HERMES_CONFIG` |
| The voice config is already hashed and logged in `fdh_session_start` (`config_hash`); there are no prompt hashes yet | `config.py` `config_hash`; `engine/turn_manager.py` `start()` |
| Turn detection: 800 ms end-of-turn silence for every client | FDH `config/voice/base.yaml` |
| τ² policy: "You should only make one tool call at a time" | `airline/policy.md:11`; retail equivalent `retail/policy.md:20` |

### A.6 Ranking history (before review 1)

1. **First ranking,** by impact relative to size:
   1. proactive status;
   2. replay unheard answer;
   3. spelled ID and name joining;
   4. local invalid-ID cap;
   5. filler dedupe;
   6. short answers;
   7. parallel tool calls;
   8. write checks;
   9. barge-in fix;
   10. backend policy rules;
   11. Hermes run retry;
   12. status few-shot tuning.
2. **Classification:**
   - Must: 1+2, 3, 4.
   - Good to have: 5, 6, 7, 8.
   - Drop: 9, 10, 11, 12.
3. **Revision 1** implemented that classification (M1, M2, M3, M4; G1–G4).

### A.7 Review 1 (of revision 1) — findings

1. **P0: G3 violates both policies** (one tool call at a time). Drop it.
2. **P1: the ablation cannot reproduce the baseline** because the prompt edits are unconditional. Use prompt
   variants or feature flags.
3. **P1: M4 must never bypass identifier validation.** Keep invalid calls local; cap or escalate the
   wording; fix fragment accumulation in M3.
4. **P1: the hold predicate is too broad** ("I", "a", "one", "two"). Require strong evidence: two or more
   consecutive single characters, a separator, or an active accumulator.
5. **P1: M2 was promoted on non-discriminative evidence** (82% vs 79%), and it could replay a stale answer.
   Make it experimental with run binding, TTL, no intervening task, explicit intent, and separate
   partial/not-heard handling.
6. **P1: no airline non-regression gate.** Targets improve; no sentinel regression; at most one previously
   passing task regresses; Pass^1 does not decrease; same host, services, concurrency, seed, commit and
   prompt variant; two paired repetitions.
7. **P1: the 15 s timer is not justified across machines.** Use `max_per_run: 1`, calibrate on the target
   host, and report wall and simulated gaps.
8. **P2: G2 could remove details needed for consent.** Add semantic tests for booking, modification,
   cancellation, return and exchange confirmations.

The reviewer also noted that retail was 25/114 complete at the time, with 1 infrastructure error and "too
many clients" retries.

**Recommended sequence:**

1. M1 canary.
2. M3 with a gated hold.
3. Evaluate on failed tasks and sentinels.
4. M2 separately.
5. Drop the M4 bypass and G3.
6. G1, G2 and G4 as separate ablations.

### A.8 Review 2 (of revision 2) — findings and decisions

1. **P1: completeness only knows the user-ID pattern.** Reservation codes and names would be held. Add canary
   patterns, acknowledge the name hold or find a completion signal, and re-run the fixture with the real
   predicate.
2. **P1: M2 needs explicit state:**
   - `Entry.run_id`, `released_mono`, `replayed`;
   - voice-side `last_finished_run_id` and `last_finished_request`;
   - assignment on `backend_run_done` and `output_release`;
   - ordering tests.
3. **P2: log deployed feature maps and prompt hashes,** so treatment and control components cannot be mixed.

Retail at review 2: 27/114 complete, 26 scored, 1 infrastructure error, earlier database-client retries.
It is still unsuitable as a baseline.

**Decisions:**

- **RC2 approved narrowly.** Only with M2 enabled and all guards passing; otherwise today's delegation. The
  gateway ignores `request` in IDLE; M2 is off by default and experimental.
- **Keep the ASR case** (`ROSSI`). Name casing, if ever, happens in tool-argument preparation.
- **Canary host:** the original airline host with a new paired control; otherwise a dedicated stable host
  with a fresh control first; not the current retail host.
