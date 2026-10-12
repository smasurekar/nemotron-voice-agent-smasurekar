# Telecom with the gpt-5.5 user stays at 0.50 after the reworded F4: what is really failing

**Status:** analysis and a fix plan for review. Nothing is implemented.

> **Superseded fix choice (2026-10-11):** after the submission discussion, the chosen fix is a single telecom
> operator brief through the existing `domain_notes` mechanism. It is disclosed word for word, and airline and retail
> prompts stay byte-identical. See [`telecom-domain-note-plan.md`](telecom-domain-note-plan.md). The generic Fix 1
> and Fix 2 in §6 below remain as fallbacks only.

**Run analysed:**
`voice-agent-evaluation-dump/tau-3-voice/2026-10-10_02-53-58Z_fdh-voice-geval-f4roam-gpt55usr/fdh_voice_geval_telecom_regular_gpt55roam`
(called `gpt55roam` below). It is compared on the same telecom tasks with three earlier runs:

- `gpt55fix` (10-09): same fixes, old F4 wording, 82 tasks;
- `gpt55` (10-08): no F1, F2, F4 or F8;
- `gpt52` (10-07): gpt-5.2 user.

Numbers come from [`telecom_deep_analysis.py`](telecom_deep_analysis.py), with output in
[`results/summary.txt`](results/summary.txt) and [`results/per_task.tsv`](results/per_task.tsv). It also reads the
backend decision traces (`logs/fdh_workers/<session>.trace.jsonl`, present for all 114 `gpt55roam` sessions).

**Constraint for every fix below:** one prompt, one code base and one config for all domains. Each fix must be
generic, must stay silent in airline and retail, and must not ask a user who already gave the answer.

## 1. Bottom line

1. **Roaming is still the main issue, and it accounts for the whole gap to gpt-5.2.**
   - Non-roaming tasks score the same with both user simulators: gpt-5.5 0.72, gpt-5.2 0.68.
   - Roaming tasks score 0.33 with gpt-5.5 and 0.67 with gpt-5.2.
   - The gpt-5.2 user volunteers "I'm in France" in **64 of 64** roaming calls. The gpt-5.5 user does so in only 17 of 64.
2. **The reworded F4 fired in only 1 roaming call in 4.** By "fired" I mean the backend asked where the user is.
   - The backend asked in **16 of 64** calls (25%). In 13 of those the question reached the user first (20%); 3 were
     cut off or arrived after the user had already said it.
   - When the agent asked, the call passed **0.85** of the time.
   - In **34 of 64** roaming calls (53%) the location never came up at all. Every one of them failed (0/34).
   - The old wording asked in 2 of 39 calls (`gpt55fix`); before F4 it asked in 1 of 64 (`gpt55`). So the rewording
     works, but not often enough.
3. **There are two specific reasons it didn't fire** (§4):
   - **(a) Another cause is visible in the account records.** When the line is over its data limit, the backend says
     "I found the issue", offers more data, and never asks. It asked in 13% of these calls, against 42% when the
     records showed no cause.
   - **(b) MMS problems.** The policy's MMS section never mentions location. Location matters only indirectly: MMS
     needs mobile data, and the mobile-data section lists roaming. The rule says "if your policy names such a cause
     for the kind of problem the user reports", so it does not apply. The backend asked in 17% of MMS calls.
4. **The real failure is the end of the call, not the start.**
   - In the never-known calls the backend does every other fix. It then sees the classic roaming symptom:
     *"excellent 5G signal, mobile data on, speed test 'no connection'"*.
   - It then **transfers to a human**, even though the rule says not to transfer while the question is unasked.
   - **28 of the 34** never-known calls ended in a transfer; the other 6 ended at `max_steps` or by the user stopping.
   - **47 of the 57 failed calls** (82%) ended in a transfer. A prompt rule written at the start of a long prompt does
     not hold at that late moment.
5. **Below roaming there is a smaller second layer** (14 non-roaming failures). It is the same "gives up and
   transfers too early" pattern: missed MMS app permissions, APN resets and network-mode fixes. It hurts gpt-5.2
   runs too (gpt-5.2 MMS non-roaming 0.21), so it is not new. It is a ceiling, not the cause of the drop.
6. **Ruled out as causes of the drop:**
   - **Cut-off backend answers.** 51% of backend turns are cut off, but the share is the same in `gpt55` and
     `gpt55fix` (50%, 53%), and in non-roaming tasks it does not lower the pass rate.
   - **Calls to device tools the backend doesn't have** (for example `check_status_bar`; 97 in this run, 114 in
     `gpt55`). These waste time but are constant across runs.

   Both are worth fixing later; neither explains 0.50.

**What would fix it:** make the location question happen in nearly every roaming call where the user hasn't said it,
with two generic changes (§6):

- **Fix 1, prompt.** Reword `situational_facts` again, under the same switch, to close gaps (a) and (b).
- **Fix 2, code.** Add a one-time *transfer checkpoint* at the moment of giving up, under the existing
  `transfer_check` switch. It asks the backend to check unasked user facts and remaining policy steps before the
  transfer goes out. No new flag.

**Rough ceiling:** with the location known, roaming calls pass about 0.6–0.85, which puts telecom at about
**0.67–0.76**, against 0.50 now and 0.675 for gpt-5.2. Fix 2 can also recover some of the 14 non-roaming failures.

## 2. Pass^1 by issue type

| Group | n | gpt55roam | gpt55fix (82 tasks) | gpt55 | gpt52 |
|---|---|---|---|---|---|
| MMS, not roaming | 14 | 0.43 | 0.71 (n=7) | 0.50 | **0.21** |
| MMS, roaming | 35 | **0.17** | 0.20 (n=10) | 0.03 | 0.60 |
| mobile data, not roaming | 7 | 0.86 | 1.00 | 0.86 | 1.00 |
| mobile data, roaming | 29 | **0.52** | 0.34 | 0.41 | 0.76 |
| service | 29 | 0.83 | 0.79 | 0.83 | 0.83 |
| **all** | 114 | **0.50** | 0.57 | 0.44 | 0.68 |

- Service and non-roaming mobile data score the same across all runs.
- MMS roaming moved only from 0.03 to 0.17.
- `gpt55fix` covers only 82 tasks, and the tasks it skipped are mostly MMS roaming, so its 0.57 is not comparable.

## 3. How often the fix fired

Roaming tasks (n=64). "Asked first" means the agent's question came before the user said anything about location.

| | gpt55roam | gpt55fix (n=39) | gpt55 | gpt52 |
|---|---|---|---|---|
| backend answer asked where the user is (trace) | **16 (25%)**, pass 0.69 | 2 (5%) | 1 (2%) | – (no traces) |
| agent asked first (heard in the audio) | **13 (20%)**, pass **0.85** | 0 | 0 | 0 |
| user volunteered first | 17 (27%), pass 0.59 | 14 (36%), pass 0.86 | 20 (31%), pass 0.65 | **64 (100%)**, pass 0.67 |
| location never known | **34 (53%)**, pass **0.00** | 25 (64%), 0.00 | 44 (69%), 0.00 | 0 |

- **About the volunteered calls:**
  - 7 of the 17 gpt-5.5 users said it in their opening sentence.
  - The other 10 said it late, often after a long round of checks ("Still not sending… I'm abroad in France, if that
    matters").
  - Late knowledge passes less often (0.59) than an early question (0.85).
- **Non-roaming tasks:** the agent asked where the user was in 3 of 50 calls. Airline: 0 of 50, as intended.

Backend ask rate by kind of call (`gpt55roam`, roaming tasks):

| | n | backend asked | never known |
|---|---|---|---|
| mobile data | 29 | 10 (34%) | 11 |
| MMS | 35 | **6 (17%)** | 23 |
| records show no cause | 26 | 11 (42%) | 9 |
| records show data over the limit | 38 | **5 (13%)** | 25 |

## 4. Why the rule did not fire

### 4.1 A visible cause in the records replaces the question

In 38 roaming tasks the line is also over its data limit. Right after identification the backend sees
`data_used_gb 15.1` against a 15 GB plan. Its first answer is almost always one of these:

> "I found the issue. You've used 15.1 GB out of your 15 GB monthly data limit… You have two options: add up to 2 GB…"

It has found a cause, so it treats the problem as explained and skips "ask early, before other checks". After the
refuel, data still doesn't work, because roaming is the second cause. The backend then works through the device
checks (APN, VPN, network mode, reboots).

### 4.2 MMS reaches location only indirectly

The manual policy's MMS section lists these causes: no service or mobile data, APN/MMSC, 2G, Wi-Fi Calling, and app
permissions. It sends the agent to the mobile-data section for the first one, and only that section lists "Roaming
Issues (When User is Abroad)".

The current rule triggers only when the policy "names such a cause for the kind of problem the user reports". The
backend reads the MMS section, finds no location cause, and starts the status-bar checks:

> "Thanks, John. I found your account and the line ending in 2002 is active. To troubleshoot the MMS issue, could you
> please check what icons are showing in your phone's status bar…"

Example: `sess_199a251bb22945278b3f` (MMS, nine sub-issues, abroad with roaming off). The backend:

1. refuels data;
2. turns airplane mode off;
3. asks for reboots;
4. calls `check_status_bar`, `check_network_status` and `check_sim_status`. These are user-device tools the backend
   doesn't have, and each call returns "does not exist".

It never asks where the user is.

### 4.3 At the end, the backend transfers instead of reconsidering

In never-known calls, the transfer summaries describe the roaming symptom almost word for word:

> `sess_9d986c6cafaf4fc6aa83` (mobile data, over the limit, abroad, roaming off on line and phone): "Customer paid
> overdue bill…, 2GB data refuel added. APN settings correct, VPN off, SIM active, multiple reboots performed. Speed
> test still shows 'no connection' despite excellent 5G signal and data enabled. **Likely network-side provisioning
> issue.**"

> MMS calls: "Customer cannot send MMS despite completing all troubleshooting steps: good cellular signal (4 bars,
> 5G), mobile data enabled, APN reset…, Wi-Fi Calling off, Messaging app has SMS and Storage permissions…"

These summaries list every step that was done and leave out the one cause that needs the user's answer. The rule's
last sentence ("Do not transfer… while that question is still unasked") sits early in a long system prompt. At turn
30 or later the model doesn't apply it.

## 5. All 57 failures, by cause (`gpt55roam`)

| Bucket | n | How they ended | Fixable by |
|---|---|---|---|
| R1 roaming, location never known, only the roaming steps missed | **25** | 21 transfers, 4 `max_steps` | Fix 1 + Fix 2 |
| R1 roaming, location never known, other steps missed too | 9 | 7 transfers, 2 user stops | Fix 1 + Fix 2 (partly) |
| R2 roaming, location known, roaming still not fixed (phone toggle or line not done) | 5 | 5 transfers | Fix 2 (remaining steps) |
| R3 roaming fixed, another step missed (refuel, network mode, permission) | 4 | 4 transfers | Fix 2 (remaining steps) |
| N1 not roaming, a policy step missed | 13 | 9 transfers, 4 user stops | Fix 2 (remaining steps) |
| N2 not roaming, every action done, assertion failed | 1 | transfer | – |

- **Location never known: 34 of 57 failures (60%).**
- **Ended in a transfer: 47 of 57 (82%).**
- **Steps missed in failed calls, other than the roaming ones:**

  | Step | Failed calls missing it |
  |---|---|
  | `refuel_data` | 12 |
  | `grant_app_permission(storage)` | 10 |
  | `set_network_mode_preference` | 9 |
  | `grant_app_permission(sms)` | 8 |
  | `reset_apn_settings` | 7 |
  | `reseat_sim_card` | 6 |

  These are the last items in the policy's MMS and service sections. The backend gives up before reaching them.

Transfers in the whole run:

| | count | pass |
|---|---|---|
| telecom | 67 / 114 | 0.30 |
| airline | 17 / 50 | 16 / 17 |
| retail | 10 / 114 | 4 / 10 |

Many telecom transfers in passing non-roaming calls are **required** (SIM PIN locked, contract ended). A transfer
guard must therefore let a confirmed transfer through.

## 6. Generic fix plan

Both fixes use existing switches that are already on by default: `situational_facts` and `transfer_check`. Neither
adds a flag, and the same config serves every domain.

### Fix 1: reword `situational_facts` again (prompt only)

It closes the two gaps from §4.1 and §4.2. Proposed block, in the same place, right after `</policy>`:

```
<situational_facts>
Your policy may make the cause of a problem, or its fix, depend on where the user is right now, for
example whether they are away from home or in another country. None of your tools can show where the
user is: a setting or a record does not tell you, and neither does the signal or network their device
shows. This applies when your policy names such a cause for the problem the user reports, or for
anything that problem depends on (for example, a feature that needs another service to work). If it
applies and the user has not already told you where they are, ask them in one short question right
after you have identified them where your policy requires it, before other checks, even if a record
already shows another likely cause: a problem can have several causes. If the user has already told
you, do not ask; use what they said. Ask once, then follow your policy for the answer.
</situational_facts>
```

**What changes from the current text:**

- **"or for anything that problem depends on"** closes the MMS gap: MMS depends on mobile data, and the mobile-data
  section names roaming. The wording is generic and doesn't name a domain or a feature.
- **"even if a record already shows another likely cause: a problem can have several causes"** closes the
  over-the-limit gap.
- **The transfer sentence moves to Fix 2**, where it acts at the moment of the transfer.
- **Unchanged:** location only; "if the user has already told you, do not ask"; no benchmark words. The `TAU_WORDS`
  test still applies.

**Risk:**

- **Airline:** its policy is about travel. The new "anything that problem depends on" clause could make the backend
  ask "where are you now?" there. `gpt55roam` asked 0 of 50 times; offline check C (§7) must show this stays near 0.
- **Retail:** a delivery address is not "where the user is right now", but the same check applies.

### Fix 2: a one-time transfer checkpoint (code, under the existing `transfer_check` switch)

**Where:** `worker/tool_futures.py` `ToolFutures.call()`, the single point every backend tool call passes through
before it reaches the voice server and tau2. The worker gets its settings from the gateway, which knows the
`transfer_check` feature. Implementation needs to confirm that the feature map reaches the worker's configure
message. If it doesn't, it is one more field in the existing configure payload, not a new user-facing flag.

**What it does:**

1. The **first** call in a session to a transfer tool doesn't go out. The worker returns this note to the backend as
   that call's result:

   ```
   Not transferred yet (this check runs once per call). If your policy requires this transfer now, call
   the transfer tool again right away. Otherwise, before transferring, check: (1) a step your policy gives
   for the user's problem, or for anything it depends on, that you have not done yet; (2) a fact only the
   user can tell you that a cause in your policy depends on, such as where they are right now, that you
   have not asked. Do or ask those first; transfer only if the problem remains.
   ```

2. The **second** transfer call goes out unchanged, so required transfers still happen, one backend step later.
3. An event `transfer_checkpoint` is logged with `{tool, outcome: held | passed}`, so a report can count firings.

**How a transfer tool is detected, generically, from the session's tool schemas:**

- the tool name contains `transfer`, and the name or description mentions a human or an agent;
- this covers `transfer_to_human_agents` in every τ domain;
- the detected list is logged at configure time, like the per-session rules from `66f6c67`.

**Why code and not a stronger prompt:**

- Two prompt versions already carried a "don't transfer while unasked" sentence, and the backend transferred 28
  times anyway.
- A note returned as a tool result is the most recent thing the model reads, at the exact moment of the decision.
- It costs one extra backend step (about 2.5 s), only on calls that transfer.

**Risk:**

- **Required transfers** (telecom SIM PIN or ended contract; airline 17 transfers, 16 of them passing; retail 10):
  the backend must call the transfer tool again rather than try a forbidden write. Offline check E (§7) gates this.
- **The checkpoint fires once per call.** If the backend then asks the location question, the user answers, and the
  call goes on normally.
- **The note never reaches the user.** It is a tool result, and the frontend speaks only the backend's final answer.

### What is deliberately left out

- **No domain-specific note or step list.** For example, no "for MMS check roaming": that would be telecom-only.
- **No forced question at a fixed turn.** That would ask airline and retail users too.
- **Cut-off answers (51%) and unknown-tool calls.** They are constant across runs, so they are a separate follow-up.

## 7. Checks before a live run

All offline checks reuse the traces from this run, with `cli/backend_decision_replay.py`. It already extracts
transfer decisions and re-sends a request with an arm's prompt; checks A–E need two additions: a "next answer asks
where the user is" matcher, and a "transfer result = checkpoint note" continuation. Each check uses 3 samples at
temperature 0.

| Check | Input | Pass if |
|---|---|---|
| A. Fix 1 asks early | the first post-identification answer of the 34 never-known roaming calls, with the Fix 1 prompt | asks where the user is in ≥ 80% (MMS and over-the-limit both ≥ 70%) |
| B. Fix 1 respects the user | the same point in the 17 calls where the user had already said it | asks in ≤ 5% |
| C. Fix 1 stays silent elsewhere | first 3 answers of all 50 airline and 30 sampled retail sessions | asks where the user is in ≤ 2% |
| D. Fix 2 recovers roaming | the 28 never-known transfer decisions, with the checkpoint note as the transfer result | next answer asks where the user is in ≥ 80% |
| E. Fix 2 keeps required transfers | all transfer decisions of passing tasks: telecom non-roaming 20, airline 16, retail 4 | calls the transfer tool again in ≥ 90%, and makes no new write call in any sample |
| F. No regressions | existing G2 `U` decisions | unchanged |

**Live runs, after A–F pass:**

1. **Smoke, about 10 tasks:** the 4 roaming tasks of the last smoke, 4 MMS never-known tasks, and 2 airline tasks
   that transfer. Look for a `transfer_checkpoint` event per transfer, and for the location question in the roaming
   tasks.
2. **Full telecom, airline and retail with the gpt-5.5 user.**

**Targets:**

| Domain | Target |
|---|---|
| telecom | ≥ 0.65 |
| roaming tasks | ≥ 0.60 |
| airline | ≥ 0.74 |
| retail | ≥ 0.70 |

Then one telecom run with the gpt-5.2 user to confirm no drop (0.675 baseline). That user always volunteers, so
Fix 1 should stay silent, and only Fix 2 can change anything.

## 8. Decisions needed

1. **Scope.** Recommended: both fixes together, because Fix 1 alone has already fallen short twice. The alternative
   is Fix 1 first, with Fix 2 only if needed.
2. **Switch for Fix 2.** Recommended: put it under the existing `transfer_check`, since it acts at transfer time. The
   alternative is `situational_facts`, which would then gate both the prompt block and the checkpoint.

## 9. Reproduce

```bash
A=misc/prototypes/frontend-delegation-hermes/analysis/2026-10-11_fdh-telecom-gpt55-deep
python3 $A/telecom_deep_analysis.py --dump ../voice-agent-evaluation-dump/tau-3-voice --traces logs/fdh_workers
```

`results/per_task.tsv` has one row per `gpt55roam` task with columns:

- `cause`: the §5 bucket;
- `backend_asked`;
- `agent_asked_first`;
- `user_said_first`;
- `transfer`;
- `missed`: the expected actions that weren't matched;
- `session`.
