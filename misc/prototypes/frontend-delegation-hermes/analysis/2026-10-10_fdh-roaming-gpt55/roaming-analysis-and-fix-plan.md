# Telecom roaming tasks with the gpt-5.5 user: why they fail, and a plan to fix it

Status: **implemented 2026-10-10** (§9). Date: 2026-10-10.

Scope: the telecom "user abroad" (roaming) tasks of the FDH voice agent, with the gpt-5.5 τ³ user simulator.
Other failure types are out of scope.

## 1. Bottom line

- **The agent never asks the caller where they are.** On the 39 roaming tasks that all three runs scored, the agent
  asked zero location questions in every run: gpt-5.5 with fixes, gpt-5.5 without, and gpt-5.2. On the full 114-task
  runs it asked zero times out of 64 roaming tasks, for both gpt-5.5 and gpt-5.2.
- **gpt-5.2 passed only because its user said "I'm in France" in the opening turn** (64/64 calls). The gpt-5.5 user
  shares information only when asked: it said so in 20 of 64 calls (full 10-08 run), mostly late, and in 44 it never
  said it.
- **When the user never says it, every call fails:** 0 of 44 (full gpt-5.5 run) and 0 of 25 (fixes run). When the user
  does say it, the agent handles roaming well: 0.65 to 0.93.
- **The F4 `<situational_facts>` block had no effect.** It was in the backend prompt of every failed call (25/25), yet
  the backend's reasoning hardly considered location (2 of 25 calls, both reading the line's `roaming_enabled` as
  "roaming checked"). The agent works through the checklist and then transfers: 22 of 27 failures end in
  `transfer_to_human_agents` with "all troubleshooting completed".
- **The fix, as asked:** one generic backend rule. *If your policy makes a problem depend on where the user is, and the
  user has not told you where they are, ask once, early. If they already told you, do not ask.* It **replaces the
  wording of the existing `situational_facts` switch**, which stays on by default; no new switch. It uses the same
  config for every domain, and in practice it fires only in telecom. It is safe for gpt-5.2 runs, because that user always
  volunteers the location, so the rule stays silent.
- **Size of the prize.** Roaming is 64 of the 114 telecom tasks. If the 44 never-told calls passed at the rate the
  agent already achieves once it knows (0.65 to 0.86), gpt-5.5 telecom would rise from **0.439 to about 0.69–0.77**.

## 2. Data

| Run | Folder (under `voice-agent-evaluation-dump/tau-3-voice/`) | User | Agent |
|---|---|---|---|
| gpt55fix | `2026-10-09_16-53-22Z_fdh-voice-geval-f1f2f4f8-gpt55usr/fdh_voice_geval_telecom_regular_gpt55fix` (stopped at 82/114) | gpt-5.5 | `fcac2b8`, F1/F2/F4/F8 on |
| gpt55 | `2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_telecom_regular_gpt55` | gpt-5.5 | `124acf7` |
| gpt52 | `2026-10-07_16-18-35Z_fdh-voice-geval-fixes/fdh_voice_geval_telecom_regular` | gpt-5.2 | `124acf7` |

- Backend decision traces: the local `logs/fdh_workers/<session>.trace.jsonl`. They exist for all gpt55 and gpt55fix
  roaming sessions, and for none of the gpt52 sessions (that run predates the trace).
- Script: [`roaming_analysis.py`](roaming_analysis.py). Outputs: [`results/summary.txt`](results/summary.txt) and
  [`results/per_task.tsv`](results/per_task.tsv).
- Rerun it from the repo root:

```bash
python3 misc/prototypes/frontend-delegation-hermes/analysis/2026-10-10_fdh-roaming-gpt55/roaming_analysis.py \
    --dump ../voice-agent-evaluation-dump/tau-3-voice --traces logs/fdh_workers
```

## 3. Numbers

**The 82 telecom tasks scored in all three runs (39 roaming):**

| Roaming tasks (39) | gpt55fix | gpt55 | gpt52 |
|---|---|---|---|
| Pass^1 | **0.308** | 0.333 | 0.769 |
| Pass^1 of the 43 non-roaming tasks | 0.814 | 0.814 | 0.767 |
| agent asked where the user is | **0** | **0** | **0** |
| user said they are abroad, at any time | 14 | 14 | 39 |
| ... of those, in the opening turn | 5 | 6 | 39 |
| Pass^1 when the user said it | 0.857 | 0.929 | 0.769 |
| Pass^1 when the user never said it | **0.000 (n=25)** | **0.000 (n=25)** | – (n=0) |
| a roaming tool was used | 13 | 14 | 36 |
| ended in `transfer_to_human_agents` | 22 | 18 | 4 |
| never said it, but a line record with `roaming_enabled: false` was in the agent's tool results | 24 / 25 | 22 / 25 | – |

**Full 114-task runs:**

| | gpt-5.5 (10-08) | gpt-5.2 (10-07 16:18) |
|---|---|---|
| telecom Pass^1 | 0.439 | 0.675 |
| roaming tasks (64) | 0.203 | 0.672 |
| non-roaming tasks (50) | 0.740 | 0.680 |
| agent asked where the user is | 0 / 64 | 0 / 64 |
| user said it / Pass^1 then | 20 / 0.650 | 64 / 0.672 |
| user never said it / Pass^1 then | **44 / 0.000** | 0 / – |

The non-roaming tasks score the same or better with gpt-5.5. **The whole gpt-5.5 telecom gap is the roaming tasks,
and within those it is the calls where nobody mentions the location.**

Note: the run README's comparison table gives gpt52 0.841 on the 82 tasks and 0.846 on roaming. Recomputed from
that run's `results.json`, it is 63/82 = 0.768 and 30/39 = 0.769. The conclusion does not change.

## 4. What a failing call looks like

Example: gpt55fix `sess_ab25c71e09b74ac9b82b`, task `[mms_issue]bad_network_preference|user_abroad_roaming_disabled_off`.
1. The user says only "I can't send picture messages" and gives their phone number. The τ³ instructions say they are
   in France, but this user tells only what it is asked.
2. The agent looks up the customer and the line. The record shows `"roaming_enabled": false`.
3. It works the device checklist: status bar, network mode (2G → 4G/5G fixed), APN, Wi-Fi calling, app permissions.
4. MMS still fails, so it transfers: "Troubleshooting completed: …". It never asked "are you abroad?".

To pass, the agent needed one question. When the user answers "yes, I'm in France", the policy takes over: the phone's
Data Roaming toggle plus the line's roaming (`enable_roaming`).

## 5. Why the agent does not ask, and why F4 did not help

1. **The policy the agent gets has no "ask" step.** τ³ runs the *manual* telecom policy (`tech_support_manual.md`).
   It lists "Roaming Issues (When User is Abroad)" as one cause among many and does not say to ask. The *workflow*
   variant has an explicit "Step 2.1.2: Ask the user if they are outside their usual service area", but that variant
   is not used. gpt-5.2 never needed the step, because its user volunteered the fact.
2. **F4's trigger never fires.** F4 says to ask "when you reach that cause or step". The agent never "reaches" roaming:
   it works the causes it can check (device screens, line record) and then concludes "all troubleshooting done".
3. **F4 lets the agent treat a record as the answer.** "Do not ask about anything your tools already show." The line
   record has a `roaming_enabled` field, and the device shows a network type and signal. The traces show the agent
   reading these as answers:
   - "Line active, device activated, roaming enabled" (`4fdbef3d`);
   - "excellent 5G signal on home network" (`76966824`).

   Neither says where the user is.
4. **F4 is too broad.** It covers location, device screens and "what they have already tried" at once. The agent
   already does the device part plenty, so the location part gets lost.
5. **F4 sits at the end** of a roughly 20k-token system prompt, after `<before_transfer>`, and has no tie to the
   transfer decision.

## 6. The fix (plan)

### 6.1 The rule

The rule as asked: *if the user hasn't said where they are, and that matters, the agent asks; if the user already
said it, the agent does not ask.* It is written generically, with no telecom words.

**No new switch.** The rule replaces the text of the existing `situational_facts` block (F4) in
`prompts.backend.yaml`. The switch keeps its name and its defaults: on in `gateway.yaml` and `gateway.eval.yaml`, off
in the controls. The block moves from the end of the prompt to right after `<policy>`, and keeps its tag. New text
(draft; examples invented):

```text
<situational_facts>
Your policy may make the cause of a problem, or its fix, depend on where the user is right now,
for example whether they are away from home or in another country. None of your tools can show
where the user is: a setting or a record does not tell you, and neither does the signal or network
their device shows. If your policy names such a cause for the kind of problem the user reports, and
the user has not already told you where they are, ask them in one short question early: right after
you have identified them where your policy requires it, and before other checks. If the user has
already told you, do not ask; use what they said. Ask once, then follow your policy for the answer.
Do not transfer the user or say that nothing more can be done while that question is still unasked,
unless your policy requires the transfer right away.
</situational_facts>
```

How each word choice answers a cause in §5:

| Wording | Cause it answers |
|---|---|
| "ask … early … before other checks" | the agent never reaches the step (§5.1, §5.2) |
| "a setting or a record does not tell you, and neither does the signal" | records read as answers (§5.3) |
| only *where the user is* | F4 too broad (§5.4); also keeps airline safe (§6.3) |
| right after `<policy>`, not at the end | F4 placed last, far from the policy (§5.5) |

The old F4 text is removed entirely, including its sentence asking the user to read out everything a screen shows.
That sentence had no measurable effect: the non-roaming tasks scored 0.814 both with F4 on and without it.
| "Do not transfer … while that question is still unasked" | transfers after the checklist (§5.5) |
| "If the user has already told you, do not ask" | your requirement; gpt-5.2 safety |

### 6.2 Config: one set for every domain

- **Gateway files: no change.**
  - `situational_facts: true` stays the default in `gateway.yaml` and `gateway.eval.yaml`.
  - The controls already pin it off: `gateway.eval_124acf7.yaml`, `gateway.eval_control.yaml`,
    `gateway.baseline.yaml` and `gateway.identity_control.yaml`.
  - The existing arm `gateway.eval_situational.yaml` (the `124acf7` control plus `situational_facts`) becomes the
    arm of this rule.
- **No domain detection and no domain notes.** The same files serve airline, retail and telecom runs.
- **Code.** Only `prompts.backend.yaml`: the new text, at its new place. No Python changes; the comment of
  `situational_facts` in `prompt_features.py` and `gateway.yaml` is updated.
- **Tests.**
  - The existing `situational_facts` tests are updated for the new text and place. The block renders only with
    its switch, right after `</policy>`, and the switch changes nothing else in the prompt.
  - It contains no benchmark words (the existing `TAU_WORDS` list: no "roaming" and no "telecom").
  - Controls stay byte-identical to the `124acf7` goldens; F4 is off there, so the goldens do not change.
  - The feature sets do not change.
- **Fingerprint.** The switch name is the same but the text is new, so `backend_system_sha256` changes. Pair runs by
  that hash or by agent commit, not by the switch name.
- **Docs.** Runbook §6.4 arm row and config row of F4 (new meaning); this folder; `analysis/README.md`.
- **Restart.** The gateway only. The voice container is unchanged.

### 6.3 Why it is safe

| Case | What the rule does |
|---|---|
| gpt-5.2 user, or any user who volunteers the location | "already told you, do not ask": no question. Checked offline (§7, check B) |
| telecom tasks not abroad (50 of 114) | at most one extra question ("No, I'm at home") at the start of a data or MMS problem; the policy then continues as today. In the gpt-5.5 run these tasks already score 0.74 |
| airline, retail | the policies name no cause that depends on where the user is now; "travel" in airline means flights and insurance. The rule stays silent. Checked offline (§7, check C) |
| a transfer the policy requires right away | not delayed ("unless your policy requires the transfer right away") |

### 6.4 If the offline check fails

**Decided:** one flag only. If check A in §7 fails (the backend still does not ask), make the wording stronger and run
the offline checks again. No F5 checkpoint and no second flag.

## 7. Validation before any live run

Everything here is offline, using the backend traces of the gpt55 and gpt55fix runs and replaying one backend request
with the new prompt (3 samples each). It needs a small extension of `cli/backend_decision_replay.py`: a mode that picks
the first backend request after the identification lookup succeeded, and a check of whether the reply asks where the
user is.

| Check | Sessions | Pass bar |
|---|---|---|
| A. Asks when not told | the 25 gpt55fix plus the 44 gpt55 roaming calls where the user never said it | asks in **≥ 80%** of samples (today 0%) |
| B. Silent when already told | the 20 gpt55 roaming calls where the user said it (6 in the opening turn) | asks again in **≤ 5%** |
| C. Silent elsewhere | airline and retail sessions of the gpt55 and gpt55fix runs | asks in **≤ 2%** |
| D. Write and transfer decisions unchanged | existing G2 `U` decisions, airline and retail | as today (exact) |

Then, live with the gpt-5.5 user, targeted first:
1. The 64 telecom roaming tasks. Target ≥ 0.60, up from 0.20.
2. Full telecom. Target ≥ 0.65, and the non-roaming tasks no more than 2 points below 0.74.
3. Airline and retail. No more than 2 points below the gpt55fix airline (0.78) and the 10-08 retail (0.658).
4. Optional: one gpt-5.2 telecom run to confirm no harm.

## 8. Decisions (all made, 2026-10-10)

1. **Flag:** reuse the existing `situational_facts` switch with the new text, on by default. No new flag.
2. **Scope:** only *where the user is* (for example abroad or travelling), not any user situation.
3. **If the offline check fails:** reword the prompt and test again. No F5 checkpoint.

## 9. Implementation record (2026-10-10)

- `config/prompts.backend.yaml`: the old `<situational_facts>` text was removed, and the §6.1 text was added right
  after `<policy>` (and `<domain_notes>`), behind the same `features.situational_facts`.
- Switch defaults are unchanged: on in `gateway.yaml` and `gateway.eval.yaml`; off in `gateway.eval_124acf7.yaml`,
  `gateway.eval_control.yaml`, `gateway.baseline.yaml` and `gateway.identity_control.yaml`. No new flag; no Python
  logic change (comments only in `prompt_features.py`, `gateway.yaml` and `gateway.eval_situational.yaml`).
- Tests (`test_fdh_fix_plan.py`, `SituationalFactsTests`):
  - with the switch off, the prompt equals the `124acf7` golden;
  - with it on, the only change is the block right after `</policy>`;
  - the controls render no block;
  - the block contains no benchmark words in any domain;
  - the replay CLI renders exactly what the gateway renders.
- The offline replay checks of §7 are not built yet (the `backend_decision_replay.py` extension). The first live
  check is the user's telecom run.
