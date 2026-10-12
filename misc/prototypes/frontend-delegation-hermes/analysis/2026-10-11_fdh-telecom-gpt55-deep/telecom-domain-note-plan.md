# Telecom domain note: minimal plan

## Context for reviewers

**Setup**

- **Agent:** FDH voice agent (`src/prototypes/voice_delegation_hermes_agent/`). A frontend LLM talks to the user and
  a Hermes backend LLM runs the tools and follows the policy.
- **Eval:** τ³ voice, with the gpt-5.5 user simulator. The submission will be a **custom** submission, and it must be
  extremely credible: one code base and one config for every domain, and everything disclosed.

**Problem**

| Run | Telecom Pass^1 |
|---|---|
| gpt-5.5 user | **0.50** |
| gpt-5.2 user | 0.675 |

Airline and retail are fine, at 0.74 and 0.73. The whole telecom gap is in the roaming tasks, where the caller is
abroad:

| User simulator | Volunteers the location | Roaming Pass^1 |
|---|---|---|
| gpt-5.2 | always | 0.67 |
| gpt-5.5 | in 27% of calls | 0.33 |

No tool shows the user's location. The generic prompt rule (`situational_facts`, F4) made the agent ask in only 25%
of calls: it skips the question when a record shows another cause, and on MMS. When it does ask, 0.85 of calls pass.
When the location never comes up (53% of calls), **every** call fails, mostly by transferring to a human after every
other fix.

**Decision**

Add one telecom-only operator brief through the existing `domain_notes` mechanism. Airline and retail prompts stay
byte-identical. Validate on telecom tasks the leaderboard doesn't score, then run the scored set once, and disclose
the note word for word.

**Paths**

| What | Path |
|---|---|
| Analysis (numbers, failure causes, examples) | `misc/prototypes/frontend-delegation-hermes/analysis/2026-10-11_fdh-telecom-gpt55-deep/telecom-gpt55-deep-analysis.md` |
| Analysis script and output | same folder: `telecom_deep_analysis.py`, `results/summary.txt`, `results/per_task.tsv` |
| Previous roaming analysis (the F4 rewording) | `misc/prototypes/frontend-delegation-hermes/analysis/2026-10-10_fdh-roaming-gpt55/roaming-analysis-and-fix-plan.md` |
| Run analysed (gpt-5.5 user, reworded F4) | `voice-agent-evaluation-dump/tau-3-voice/2026-10-10_02-53-58Z_fdh-voice-geval-f4roam-gpt55usr/` (see its `README.md`) |
| Earlier runs compared | `…/tau-3-voice/2026-10-09_16-53-22Z_fdh-voice-geval-f1f2f4f8-gpt55usr/` (old F4), `…/2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/` (no F1/F2/F4/F8), `…/2026-10-07_16-18-35Z_fdh-voice-geval-fixes/` (gpt-5.2 user) |
| Backend decision traces | `nemotron-voice-agent-smasurekar/logs/fdh_workers/<session>.trace.jsonl` |
| Submission rules and our audit (see §0, the disclosure requirement) | `tau2-bench-smasurekar/misc/prototypes/voice-tau3-leaderboard-submission.md` |
| Official submission guide | `tau2-bench-smasurekar/docs/leaderboard-submission.md` |
| Telecom tasks: all 2,285 and the splits (`base` 114 is scored) | `tau2-bench-smasurekar/data/tau2/domains/telecom/tasks.json`, `split_tasks.json` |
| Telecom policy used | `tau2-bench-smasurekar/data/tau2/domains/telecom/main_policy.md`, `tech_support_manual.md` |
| Files the plan changes | `src/prototypes/voice_delegation_hermes_agent/config/prompts.backend.yaml`, `gateway.yaml`, `gateway.eval.yaml`, `gateway.eval_control.yaml`, `gateway.eval_124acf7.yaml`; `tests/unit/prototypes/delegation/test_fdh_identity_fixes.py` |

All dump paths are under `/localhome/local-smasurekar/smasurekar/`. Repo paths are relative to each repo root.

**What to review**

- the note wording (§1.1);
- the credibility of the approach, using the submission doc §0;
- the held-out validation and its pass targets (§4);
- the decisions (§7) and the review responses (§8).

**Status:** **implemented 2026-10-11** (§9), on by default. The held-out validation (§4) and the scored runs (§5) are
run by the eval owner.

**Why:** [`telecom-gpt55-deep-analysis.md`](telecom-gpt55-deep-analysis.md). With the gpt-5.5 user, the location never
comes up in 53% of the telecom roaming calls, and all of those calls fail.

**Approach:** the agreed direction from the 2026-10-11 discussion. It replaces the generic Fix 1 and Fix 2 of the
analysis §6, which stay as fallbacks only.

**Disclosure:** this note is the one disclosed under the IMPORTANT §0 of the tau2 fork's
`misc/prototypes/voice-tau3-leaderboard-submission.md`.

## 1. What changes

The change is **one telecom operator brief, through the existing `domain_notes` mechanism.**

- No new flag, no new code, no changes to Python logic.
- Airline and retail match no domain, so they get no `<domain_notes>` block. Their backend prompt stays
  byte-for-byte what it is today.

### 1.1 The note (wording confirmed 2026-10-11)

```
Customers' location affects mobile service. For any problem with service, mobile data or messaging,
ask early, right after you have identified the customer, whether they are at home or travelling,
unless they have already said or your policy requires an immediate transfer. Ask once, and use the
answer only for troubleshooting.
```

**Why this wording:**

- **Operator level, not task-shaped.** A real telecom company would brief its agents this way without having seen
  τ-bench.
- **No τ-bench terms.** There are no fault names, task IDs or database values, and no MMS → data → roaming chain.
  It uses none of the `TAU_WORDS` either (roaming, apn, refuel, telecom, …).
- **It doesn't restate or change the τ-bench policy.** The policy already says what to do once the customer is
  travelling; the note only makes sure the question is asked.
- **"Unless they have already said"** keeps gpt-5.2 runs safe: that user volunteered the location in 64 of 64 calls.
- **"Or your policy requires an immediate transfer"** (added in review) makes clear the note never delays a transfer
  the policy requires. It is conditional, so it doesn't invite extra transfers.
- **"Right after you have identified the customer"** fixes the timing. "For any problem with service, mobile data
  or messaging" covers MMS directly. The current generic rule depends on the model reasoning about the policy, which
  is exactly where it failed (analysis §4).
- **"Use the answer only for troubleshooting"** stops the answer from being reused for anything else.

### 1.2 Files

| File | Change |
|---|---|
| `config/prompts.backend.yaml` | `backend_domain_notes: {}` becomes `{telecom: "{% if features.domain_notes %}<note>{% endif %}"}`, the same gating pattern the tests already use. Update the header comment ("No notes ship" → "one note ships: telecom, operator brief, disclosed word for word"). |
| `config/gateway.yaml` | `domain_notes: true` (was `false`). `domains: {telecom: {tools_any: [get_customer_by_phone]}}`. `get_customer_by_phone` exists only in τ telecom's `tools.py`, and it was the rule used before (submission doc §8.4). |
| `config/gateway.eval.yaml` | `domain_notes: true`, and the same `domains` rule, which is currently pinned to `{}`. |
| `config/gateway.eval_no_note.yaml` (new, **committed**) | The ablation arm: `extends: gateway.eval.yaml`, `domain_notes: false`. It is committed with the candidate, not kept as a temporary file, so the OFF run uses the same frozen tree as the ON run. |
| `config/gateway.eval_control.yaml`, `gateway.eval_124acf7.yaml` | Pin `domain_notes: false`. They extend `gateway.eval.yaml`, so without the pin they would inherit the note. `gateway.baseline.yaml` and `gateway.identity_control.yaml` already pin it to `false`. |
| `prompt_features.py` | Update the comment only. The `domain_notes` default stays `False` in code; the config turns it on. |
| `tests/unit/prototypes/delegation/test_fdh_identity_fixes.py` | See §2. |
| Runbook, analysis `README.md` | One line each. |
| `telecom_deep_analysis.py` (this folder) | Measurement additions, see §4.3. |

**Unchanged:**

- `situational_facts` (F4) keeps its current wording and stays on. It has been silent in airline (0 of 50 calls),
  and the telecom note makes it redundant in telecom only.
- Every other fix.
- All Python code of the agent.

## 2. Tests

The two tests that pin "no note ships" (`test_no_domain_detection_and_no_note_ships`, `_catalog_with_note`) are
replaced by:

1. **Exactly one domain is detected.** `gateway.yaml` and `gateway.eval.yaml` detect only `telecom` (by
   `get_customer_by_phone`), and only `telecom` has a note.
2. **The note renders only with its switch.** With `domain_notes` off, the telecom prompt equals today's golden
   prompt.
3. **Airline and retail are untouched.** For tool lists without `get_customer_by_phone`, the rendered
   `backend_system` with `domain_notes` on equals the render with it off.
4. **The control and ablation configs never render it.** `gateway.eval_control.yaml`, `gateway.eval_124acf7.yaml`,
   `gateway.eval_no_note.yaml` and `gateway.baseline.yaml` all have `domain_notes` false, and
   `gateway.eval_no_note.yaml` differs from `gateway.eval.yaml` in that switch only.
5. **The note's content follows the rules.** It renders as one `<domain_notes>` block right after `</policy>`, and
   contains no `TAU_WORDS`, no fault names (`user_abroad`, `break_`, `_issue`) and no digits.

The full `pytest tests/` must pass, and pre-commit must pass on the changed files.

## 3. Checks after implementation (no eval needed)

1. **Restart** the gateway with `gateway.eval.yaml`. `/health` shows `domain_notes: true`, and
   `backend_catalog_sha256` changes.
2. **Probe a telecom session** (runbook §3.1, with telecom tool schemas). The trace shows `backend_domain:
   "telecom"` and the note right after `</policy>`.
3. **Prove airline and retail are unchanged.** Render `backend_system` with this run's airline and retail policies
   and tool lists. The hashes must equal the `gpt55roam` values: airline `a13de147cfce3178`, retail
   `7b790db22f8e371f`.

   This is the evidence the disclosure needs (§0.1 item 3). It shows **the note cannot change airline or retail
   behaviour**, so no airline or retail run is needed to *validate the note*. Airline and retail are still run again
   in §5, but for a different reason: the submission needs every domain scored with the same frozen commit and the
   same model tag. That run is not a check of the note.

## 4. Validation on unscored telecom tasks (before any scored run)

Following the credibility rules, the note is checked on telecom tasks the leaderboard never scores, not on `base`.

### 4.1 Freeze the candidate first

Commit the candidate before any validation run: the note, the configs including `gateway.eval_no_note.yaml`, and
the tests. The held-out ON and OFF runs, and later the scored runs, all use **this
exact commit**, with a clean working tree. Record the agent, tau2 and Hermes commits in each run's provenance.

If the note is reworded (§4.4), that is a **new candidate commit**, validated on a **new, disjoint held-out sample**,
and both attempts are reported.

### 4.2 Sample and runs

**Sample: 100 tasks** from the `full` tasks not in `base`, with at least 2 faults, chosen with a fixed seed. Save the
list as `heldout_telecom_ids.txt` in this folder, and commit it with the candidate, before any run.

Roaming and non-roaming are balanced explicitly, close to `base`'s mix (64 roaming, 50 non-roaming). Within each cell,
tasks are spread over the number of faults and the persona.

| Cell | `base` (114) | Held-out (100) | Available outside `base` (≥ 2 faults) |
|---|---|---|---|
| MMS, roaming | 35 | 31 | 1,453 |
| mobile data, roaming | 29 | 25 | 160 |
| MMS, not roaming | 14 | 17 | 476 |
| mobile data, not roaming | 7 | 9 | 50 |
| service (never roaming) | 29 | **12 (all)** | 12 |
| **roaming / not roaming** | 64 / 50 | **56 / 44** | |

Limitation, to be stated in the disclosure: only 12 service tasks exist outside `base`. Service is therefore
under-represented, and the non-roaming cells carry more MMS and mobile-data tasks than `base`.

**Runs (carried out by the eval owner; not scheduled in this plan):** two telecom runs on this sample, both
**mandatory**. The OFF run is the ablation the disclosure needs; historical `base` scores are not a substitute,
because they come from a different task set and a different build.

| Run | Gateway config |
|---|---|
| ON | `gateway.eval.yaml` |
| OFF | `gateway.eval_no_note.yaml` |

- Both runs use the same frozen commit, the same stack and the same gpt-5.5 user settings.
- Before each run, the gateway `/health` must show the expected `domain_notes` value and catalog hash.
- The sample needs tau2's `--task-split-name full` (the IDs are outside `base`), with the IDs from
  `heldout_telecom_ids.txt`.

**Agent model tag (out of scope here, tracked in the submission doc).**

- tau2 records the tag as the agent's model name (`agent_info.llm`), and `tau2 submit prepare` rejects a submission
  whose domains have different names. So every run that is submitted needs **one** tag with no domain name in it.
- The tag must name only our system. It must **not** use another company's routing prefix, since publishing our
  trajectories under it would misattribute them.
- Reaching our server under a neutral name needs a neutral connection path, for example a small
  OpenAI-Realtime-compatible provider adapter (the guide's "New Provider" route).
- This does not affect the note or its validation. Its held-out runs only need the same tag for ON and OFF.

### 4.3 Measurement (`telecom_deep_analysis.py` additions)

- **Inputs:** run folders become command-line arguments instead of the fixed four, and ON and OFF are paired task by
  task.
- **Early question:** the agent asks where the user is before its first troubleshooting step, meaning before the
  first device check or fix after identification. The script also reports the position of the first question:
  answer number after identification, and the number of tool calls before it.
- **Unnecessary repeats:**
  - the question asked more than once in a call;
  - the question asked after the user had already said where they are;
  - both counted from the audio and from the backend trace.
- **Non-roaming calls:** how often the question is asked there. This is expected and harmless, but reported.
- **Paired outcome:**
  - tasks that pass with ON and fail with OFF, and the reverse, per cell;
  - an exact McNemar p-value and a bootstrap 95% interval for the ON − OFF difference.

### 4.4 Gates (written down before the runs)

The gates are **primary** (pass or fail) or **screening** (look at the transcripts).

Run-to-run noise is large. Between two earlier runs whose prompts barely differ on non-roaming tasks
(`gpt55` → `gpt55roam`), 15 of 50 non-roaming tasks flipped (7 gained, 8 lost). One trial of 44 non-roaming tasks
cannot detect a 3-point change (about 1–2 tasks), so that check can only be a screen.

| Gate | Type | Target |
|---|---|---|
| ON: agent asks early, in roaming calls where the user hadn't said it | primary | ≥ 80% |
| ON: question asked after the user had already said, or asked twice | primary | ≤ 5% of calls |
| ON vs OFF, roaming tasks (n=56), paired | primary | ≥ +20 points, and more ON-only than OFF-only passes |
| ON vs OFF, all tasks (n=100), paired | primary | ≥ +10 points |
| ON vs OFF, non-roaming tasks (n=44) | **screening** | if lower by more than 3 points, read every OFF-only pass. It counts as a regression only if the transcript shows the note's question caused the failure (derailed the call, used up steps, or confused the user) |

- **If a primary gate fails:** reword the note once (a new candidate, §4.1), re-check on a fresh disjoint sample, and
  report both attempts.
- **If it still fails:** fall back to the generic transfer checkpoint (analysis §6, Fix 2), also checked on held-out
  tasks only.

**Claims:** results are reported as measured, with the interval. "No regression on non-roaming" is never claimed,
only "no evidence of a regression in 44 tasks, one trial".

## 5. Scoring and disclosure

The scored runs (schedule, trials, commands) are carried out by the eval owner and are not part of this plan. The
plan only fixes these conditions:

1. **No code change after §4.** The scored runs use the frozen candidate commit.
2. **All three domains are scored with that same commit.** Airline and retail are included because every domain
   must be scored with the same build and agent tag (§3 item 3), not to check the note.
3. **Every `base` run made with this commit is reported**, together with the held-out ON and OFF results and their
   intervals.
4. **Disclose** as in the submission doc §0.1:
   - the note word for word;
   - telecom only, detected by the rule `domains: {telecom: {tools_any: [get_customer_by_phone]}}`;
   - the airline and retail hashes are unchanged;
   - why it exists;
   - **that the need was found by failure analysis on the scored `base` split**;
   - the held-out validation: sample, cell mix and the service limitation, ON vs OFF, and every attempt.

## 6. Expected effect and risk

- **Telecom:** roaming calls where the location is known pass about 0.6–0.85 (analysis §3). With the question asked
  in about 80–90% of calls, telecom should land around **0.65–0.75**, against 0.50 now and 0.675 with gpt-5.2. The
  held-out runs decide; this is not a promise.
- **Airline and retail:** no effect by construction, since their prompts are byte-identical (§3 item 3).
- **Remaining risks:**
  - the agent may still skip the question on long, multi-fault calls; the primary early-question gate measures this;
  - one extra question in non-roaming telecom calls (asked of a user who is at home); the non-roaming screen
    measures this;
  - the telecom calls that still end in an early transfer (analysis §5: 13 non-roaming failures) are not addressed
    here. That is a separate, generic follow-up.

## 7. Decisions

Made on 2026-10-11:

| Decision | Outcome |
|---|---|
| Note wording | as written in §1.1, including the transfer clause |
| Held-out size, and the OFF run | 100 tasks; the OFF run is mandatory (review, §8) |
| Agent model tag | `nemotron-voice` for every submitted run, with no other company's prefix. The connection path is tracked in the submission doc, outside this plan |
| Eval runs (held-out and scored, trials, schedule) | carried out by the eval owner, outside this plan |

Nothing is left open in this plan.

## 8. Review responses (2026-10-11)

| Review point | Verdict | What changed |
|---|---|---|
| Wording: policy-required immediate transfers take precedence | **Accepted.** Cheap, and it makes the intent explicit. It is conditional, so it doesn't invite extra transfers | §1.1 |
| Disclose the exact note, the detection rule and the origin in `base` failures | **Accepted.** Already required by submission doc §0.1; now spelled out, with the exact rule string | §5 item 5 |
| Make the held-out ON/OFF ablation mandatory; drop reusing historical `base` scores | **Accepted.** The historical numbers come from a different task set and build | §4.2; old decision 3 removed |
| Complete gpt-5.5 commands; one shared agent model tag | **Partly.** One shared tag: accepted and required, because `tau2 submit prepare` rejects different agent names across domains. The tag must not use another company's prefix. It is tracked in the submission doc. Commands: dropped, since the eval owner runs the evals | §4.2 note on the tag |
| Freeze the candidate before validation | **Accepted.** The validated build must be the scored build; a rewording means a new candidate and a new sample | §4.1; the ablation config is committed, not temporary |
| Measure early questions and unnecessary repeats | **Accepted.** The script so far only had who spoke first | §4.3 |
| Balance roaming and non-roaming explicitly | **Accepted.** Matching by issue type, faults and persona alone doesn't fix the roaming share. Only 12 service tasks exist outside `base`, so service is under-represented; this is stated | §4.2 table |
| 100 tasks; treat the 3-point regression gate as screening | **Accepted, with evidence.** 15 of 50 non-roaming tasks flipped between two earlier runs whose prompts barely differ there; 3 points is about 1–2 tasks | §4.4 |
| Resolve §3 ("no new airline/retail run") against §5 (fresh scoring of all domains) | **Accepted.** No airline or retail run is needed to validate the note (the hash proof). They are run in §5 because the submission needs every domain on one commit and one tag | §3 item 3, §5 item 2 |

## 9. Implementation record (2026-10-11)

As in §1.2, uncommitted on top of the agent's current `HEAD`.

**Config**

- `prompts.backend.yaml`: `backend_domain_notes.telecom`, the §1.1 text gated by `features.domain_notes`, and the
  updated rules comment.
- `gateway.yaml` and `gateway.eval.yaml`: `domain_notes: true`, and
  `domains: {telecom: {tools_any: [get_customer_by_phone]}}`.
- New `gateway.eval_no_note.yaml` (the ablation).
- `gateway.eval_control.yaml` and `gateway.eval_124acf7.yaml` pin `domain_notes: false`.
- `prompt_features.py`: comment only. No Python logic changed.
- Not done: the held-out ID list (`heldout_telecom_ids.txt`) and the §4.3 additions to `telecom_deep_analysis.py`.
  They are needed before the held-out runs.

**Tests**

- `test_fdh_identity_fixes.py`: `TelecomNoteTests` pins the exact text, checks it is on by default and off in the
  controls and the ablation, that it renders only for telecom, and that it has no benchmark terms or digits. A
  controller test shows the note changes only the telecom prompt, one block right after `</policy>`.
- `test_fdh_schema_rules.py`: the submission-config guard now allows exactly the three disclosed telecom lines
  (detection rule ×2, note key) and ignores comments.
- `test_fdh_delegation_config.py`, `test_fdh_fix_plan.py`, `test_fdh_geval_fixes.py`: updated for `domain_notes` on
  by default and the new ablation file.
- `tests/`: 1059 passed, 7 skipped.

**Stack** (runbook §10, §3, §4)

| Component | Result |
|---|---|
| Gateway (`gateway.eval.yaml`) | `/health` ok, `domain_notes: true`, `domains: ["telecom"]`, `backend_catalog_sha256` `55639a8b238b7eec` |
| `fdh-voice` (`realtime_eval.yaml`) | `/health` ok, Docker `healthy`, `config_hash` `45362d8aa85a` (unchanged) |
| §3.1 probe | OK |

**Hash proof (§3 item 3), re-rendering real 2026-10-10 prompts**

| Domain | Note on (`gateway.eval.yaml`) | Note off (`gateway.eval_no_note.yaml`) |
|---|---|---|
| airline | `a13de147cfce3178`, unchanged | unchanged |
| retail | `7b790db22f8e371f`, unchanged | unchanged |
| telecom | `606356dbc62bbcf6` (was `c25362d11d4a716b`), one `<domain_notes>` block | `c25362d11d4a716b`, the previous telecom prompt exactly |

**Live telecom session** (gateway, telecom fixture): `backend_domain: "telecom"`, `backend_system_sha256`
`606356dbc62bbcf6`, and the trace shows the note right after `</policy>`.
