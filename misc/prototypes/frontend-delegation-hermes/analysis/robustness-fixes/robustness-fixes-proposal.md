# FDH: making the agent robust to a terse, less patient caller

A root-cause and fix-design study. The trigger was one change: τ³'s user simulator and judge moved from gpt-5.2 to
gpt-5.5, and telecom fell from 0.675 to **0.439** (retail 0.623 → 0.658, within noise). This document explains why in
terms of our code, and proposes fixes that should help the gpt-5.5 caller without hurting the gpt-5.2 numbers.

- **Date:** 2026-10-09. Read-only study: no code was changed.
- **Code:** `src/prototypes/voice_delegation_hermes_agent/` at the commit the runs measured (`124acf7`, config hash
  `a30f6df59a8b`), eval profile `config/profiles/realtime_eval.yaml` + `config/gateway.eval.yaml`.
- **Builds on:** [`tau3-geval-failure-fixes-plan.md`](../../tau3-geval-failure-fixes-plan.md) (its §3 no-regression
  rule and §3.2 rejected designs apply here unchanged) and the collated analysis
  `voice-agent-evaluation-dump/tau-3-voice/2026-10-07_09-34-31Z_fdh-voice-geval-fixes/observations/collated/fdh_geval_fixes_collated_analysis.md`
  (§2.1 persisting issues, §9 retail with gpt-5.5).
- **Runs measured** (paths under `voice-agent-evaluation-dump/tau-3-voice/`):

  | Tag | Run | Pass^1 |
  |---|---|---|
  | **T55** | `2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_telecom_regular_gpt55` | 0.439 |
  | **R55** | `2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_retail_regular_gpt55` | 0.658 |
  | **T52** | `2026-10-07_16-18-35Z_fdh-voice-geval-fixes/fdh_voice_geval_telecom_regular` | 0.675 (0.725 clean) |
  | **R52** | `2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_fixes` | 0.614 (0.623 collated) |
  | **A52** | `2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_airline_regular` | 0.740 |

  Sources: `agent/events.jsonl` (voice-server events, joined to tasks through `observations/data/tasks.json`) and the
  backend decision traces `<session>.trace.jsonl`. The traces are **not in the dump**; they are in
  `nemotron-voice-agent-smasurekar/logs/fdh_workers/` (all 114 T55, 114 R55, 114 R52 and 50 A52 sessions; none for
  T52, which ran on another host). Counts below are mine unless marked "README".

---

## 1. Summary: ranked fixes

Ranked by expected gain ÷ risk. "Passing gpt-5.2 exposure" is how often the fix's trigger fired in tasks that
**passed** in A52 + R52 + T52: the number of passing-task moments whose behaviour would change.

| Rank | Fix | Flag | Type | Problem it solves (evidence) | Expected effect, gpt-5.5 | Expected effect, gpt-5.2 | Risk | Passing gpt-5.2 exposure |
|---|---|---|---|---|---|---|---|---|
| **1** | **Replay a cut answer when the user only backchanneled or pinged** | `delegation.replay_unheard_answer.triggers: [backchannel, ping]` | Code, voice server | Answer cut by "Okay." → silence → "Are you still there?" (T55 166 times, 92 then pinged; R55 140/98). 93% of T55 pings come right after an unheard answer | Removes the main dead-air chain; targets most of the 12 telecom early hang-ups and R55's `breakdown` (7) | Small gain: the same chain happens 85 times in passing tasks, 41 then pinged | Low: fires only when the user added nothing new | 85 backchannel + ping-after-cut moments |
| **2** | **Never answer a question or a liveness ping with silence** | `delegation.guards.liveness_reply` | Code, voice server | 46 T55 pings and 20 R55 pings got a silent DIRECT turn; in WORKING, pings are sent as `task`, their filler is dropped and they **abort the backend's model call** (T52 34, R52 25, A52 23 redirects) | Covers the silent-DIRECT hang-ups (13 of 23 T55 user-stop failures had an unanswered ping) | Small gain: fewer aborted backend calls during slow runs | Very low: enforces a rule the prompt already states | 13 silent pings + 34 silent "?" turns; WORKING pings re-routed to `status` (179 in T52 + R52, all tasks) |
| **3** | **Tell the backend which policy actions belong to the user** | `hermes.user_side_actions` (gateway) | Code (failure-triggered result) + a derived prompt line | The backend calls device actions it doesn't have (114 calls in 65/114 T55 sessions); 33 of 39 failing T55 transfers came after such a call; it then invents menu paths and wrong app names | Targets the permission/APN stalls (README rank 3: 9) and part of the transfers | None in airline and retail (0 triggers); telecom small gain | Very low: changes only the text of a call that already failed | 0 in A52/R52 (T52 has no traces) |
| **4** | **Ask for situational facts the tools can't show** | `prompt_features.situational_facts` (gateway) | Prompt, backend | Location is never asked: in T55 the agent asked in 4/64 abroad tasks. When the location reached the backend, 13/19 passed; when not, 0/45 | Largest single target (README rank 1: 33 failures), but prompt-level | Neutral: the gpt-5.2 user volunteers it (47/50, README) | Low–medium: one extra question in some calls; adherence uncertain | Not measurable offline (prompt); G2 replay required |
| **5** | **One-time checkpoint before the first transfer** | `tools.transfer_checkpoint` | Code, tool relay (soft, once per session) | Transfers with diagnostics never done (T55: 39 failing transfers; collated `transfer_wrong` 18 in T52); C1 (prompt) did not hold | Catches transfers that skip the ask/diagnose steps (28 roaming failures ended in transfer, README) | Should keep the 48 passing transfers; costs one model step each | Medium: every first transfer is delayed once | 48 passing transfers (A52 17, R52 15, T52 16) |
| **6** | **The frontend may not claim a completed action** | `delegation.guards.claim_needs_delegate` | Code, voice server | Frontend said "the cancellation has been completed" / "I processed the return" with no such write (R55 tasks 14, 78) | Fixes those 2 retail tasks | Neutral | Low–medium: 6 correct restatements in passing gpt-5.2 tasks would be delegated instead | 6 DIRECT claims |

**Not recommended now** (§6): raising `proactive_status` (pings are in IDLE, not WORKING: 341/352 in T55); a hard
pre-transfer gate that re-checks state tools; backchannel-tolerant barge-in; changing the frontend history format;
capping answer length in code.

**One instrumentation fix to do first (no behaviour change):** `engine/turn_manager.py:617` logs every guard as
`guard="backend_question_needs_delegate"`, including `guard_backchannel`. The 305 `delegation_guard` events in T55
are mislabelled; log `guarded.repair` instead, and log the new guards' names.

---

## 2. What we found, in short

1. **The backend never received a "check whether the user is traveling" step.** τ³'s `telecom` domain loads the
   *manual* (`tech_support_manual.md`), not the *workflow* that contains "Step 2.1.2: Verify if user is traveling". The
   manual makes roaming conditional on a fact the agent must already know, and no tool shows the user's location. The
   T55 README's "the workflow's Step 2.1.2 is skipped" should read: "the agent was never told to ask".
2. **Most of the dead air is not backend work.** In T55, 97% of the pings happened while the backend was IDLE, and 93%
   right after the agent's last answer had been cut or not heard. A cut answer is never repeated unless the backend is
   asked again; when the user's next turn is "Okay." or "Are you still there?", the frontend often stays silent.
3. **Answers are cut almost every time.** Only 17% of T55 backend answers (704/4,092) were heard in full; 10% of T52's (166/1,718).
   Over 300 characters, ≤2% are heard in full in every run. The τ³ client stops our audio on every `speech_started`,
   including a one-word "Okay.".
4. **The backend tries to run the user's device actions itself.** It calls `check_status_bar`, `can_send_mms`,
   `check_apn_settings` and others, gets "Tool does not exist", and then often concludes it cannot help and transfers.
5. **The frontend sometimes answers for the backend.** It says "Yes, I'm still here" (fine), but also states results
   ("The cancellation has been completed.") without any tool having done it.

---

## 3. Root causes in code

### 3a. How the τ³ policy reaches the backend, and why it doesn't ask

**Path (complete, untruncated, backend only).**
- τ³ sends the policy as `session.update.instructions`. `delegation_agent.yaml:86-87` (`instructions.apply_to:
  [backend]`) and `engine/turn_manager.py:333` route it to the gateway; the frontend gets it only when `frontend` is in
  `apply_to` (`turn_manager.py:414-415`), which no eval profile sets.
- The gateway renders it verbatim inside `<policy>` (`config/prompts.backend.yaml:37-42`, rendered by
  `backend/controller.py:263-269`). It is the Hermes *ephemeral* system prompt, sent whole on every request.
- **Checked in a trace** (`logs/fdh_workers/sess_000d722546e54290bf46.trace.jsonl`, `session` record,
  `ephemeral_system_prompt`, 29,620 characters): voice preamble, `<main_policy>`, then `<tech_support_policy>` = the
  full `tech_support_manual.md`, then our blocks (`<phone_numbers>`, `<spoken_output>`, `<consent>`,
  `<before_change>`, `<before_transfer>`). Nothing is summarised or truncated (context 131,072; the prompt is ~7.5k
  tokens). The backend has 13 tools, none of them a device action.

**Which policy text the backend got.** `tau2-bench-smasurekar/src/tau2/domains/telecom/environment.py:96-136`:
`get_environment(policy_type="manual")` loads `TELECOM_TECH_SUPPORT_POLICY_MANUAL_PATH`; `src/tau2/registry.py:330`
registers that as `telecom` (the workflow variant is `telecom-workflow`). The ask-step exists only in
`data/tau2/domains/telecom/tech_support_workflow.md:145-146` ("Ask the user if they are outside their usual service
area"), which our runs never send.

**Why the backend relies on the user volunteering the fact.**
- The manual states the roaming branch as a condition, not as a step: "If the user is outside their carrier's primary
  coverage area (roaming) and mobile data isn't working, guide them to use `toggle_roaming()`"
  (`tech_support_manual.md:137-139`); "**Roaming Issues (When User is Abroad)**" (`:114-116`); main policy "If a user
  is traveling … check if the line is roaming enabled" (`main_policy.md:153-155`).
- No tool reveals the location. The agent's `get_details_by_id` shows `roaming_enabled` of the line; the user's
  `check_network_status` shows the *Data Roaming setting* (`src/tau2/domains/telecom/user_tools.py:144-153`); the
  location (`surroundings.is_abroad`, `user_tools.py:69-73`, used at `:234-235`) is never shown.
- **Our backend prompt has no rule for running a diagnostic workflow.** `<before_transfer>` (C1,
  `prompts.backend.yaml:119-129`) says "check the steps that your policy and any troubleshooting guide … give … Do the
  remaining steps that apply". A step guarded by "if the user is abroad" looks *not applicable* when nobody said so.
  Nothing says "a condition that only the user can confirm must be asked". The policy's only nudge is "try all the
  possible ways" (`tech_support_manual.md:9`).
- **The gpt-5.5 user follows its guidelines literally**: "Only share information that is explicitly provided … Start
  with minimal information and only add details when specifically asked … Disclose information progressively"
  (`tau2-bench-smasurekar/data/tau2/user_simulator/simulation_guidelines_voice_tools.md:75-84`), and "Do NOT volunteer
  new information during these check-ins" (`:63`). gpt-5.2 volunteered "I'm abroad" in 47/50 paired tasks; gpt-5.5 in
  21 (README).

**Measured (T55 traces):** of 64 abroad tasks, the backend asked about travel/location in **4**. The location reached
the backend (asked or volunteered) in 19 tasks, 13 passed; it never did in 45, **0 passed**. Home tasks: 37/50.

### 3b. DIRECT vs delegate on "are you still there?" / "any update?"

**The decision is the frontend LLM's, with two code guards.**
- Prompt: `config/prompts.yaml:19-28` (delegate on update requests and questions), `:29-35` (delegate=false for
  acknowledgements, backchannels, "hold on, honey", "one sec"; "While the backend is WORKING, an acknowledgement … is
  delegate=false with filler_text ''"), `:43-45` ("Never leave a real question unanswered: a question is
  delegate=true"), `:47-51` ("are you still there?" is `request: status` while WORKING).
- Guards: `frontend/decider.py:69-94`. `guard_backchannel` (`:82-86`) forces delegate=false and an empty filler on a
  pure backchannel unless the backend's last *heard* message ended with "?". `guard_backend_question` (`:87-93`)
  forces delegate=true in IDLE after a heard backend question. **No guard looks at pings or questions.**

**Why a DIRECT reply can be empty.**
1. **The model chooses it.** T55 has 46 pings answered `delegate=false, filler=""` (34 in failed tasks), R55 20, T52
   12, R52 10, A52 0. All but one were in IDLE or NO_SESSION. In 34 of the 46, the turn before had also been
   DIRECT-empty: once the model goes silent it tends to stay silent.
2. **The frontend can't see its own silences.** `engine/transcript.py:188-214`: DIRECT-empty turns produce no
   assistant message, not-heard speech is dropped (`:198`, `:203`), and consecutive user turns are **merged into one
   user message** (`:205-206`; `turn_manager.py:588-591` appends the new turn to it). Task T55 #64 shows the result:
   the model sees one message "Hold on . Second Okay . Hello , any update ? Are you still there ?" and treats the whole
   as a hold request (`prompts.yaml:32`).
3. **IDLE looks like "nothing to do".** The state line says `IDLE (finished; ready for the next request)`
   (`prompts.yaml:75`). With `replay_intent` off, the line "your last answer was not heard" is not shown
   (`prompts.yaml:89-91`, `turn_manager.py:660-665`), so the model doesn't know the user never heard the answer.
4. **In WORKING, the filler is dropped by design.** `_dedupe_filler` (`turn_manager.py:798-812`) drops a repeated
   delegated filler with reason `working` (`:810`). The model usually labels pings `request: task` (T52: 77 of 99
   WORKING pings; R52: 102 of 120), so no status is generated either: T52 37 and R52 69 WORKING pings got no audio at
   all (collated retail 20: "filler … dropped by filler_deduped (reason 'working')").
5. **A `task` ping in WORKING aborts the backend.** `backend/controller.py:290-298` sends it as a steer;
   `steer_mode: auto` redirects during a model call (`delegation_agent.yaml:67`). The trace of T55 #9 shows "[response
   interrupted] … The user just added: 'Still there?'". Pings caused 34 redirects in T52, 25 in R52 and 23 in A52, each
   restarting a model call and lengthening the silence that caused the ping.
6. **`proactive_status` cannot help IDLE silence.** `_maybe_proactive_status` (`turn_manager.py:1073-1105`) returns
   unless `backend.state == WORKING` (`:1077`), at most `max_per_run: 1` per run (`profiles/realtime_eval.yaml:25`).
   Backend runs are short (README p90 3.6 s), so it fired **2 times in 138 T55 sessions**. Pings in T55: 341 of 352
   in IDLE, 10 in WORKING.
7. **A requested status can be dropped.** `_speak_status` (`turn_manager.py:972-996`) drops the verbalized status when
   the run has finished meanwhile (`status_dropped`, reason `run_finished`). The answer is then the only reply, and it
   is often cut (§3c).

### 3c. What happens to a backend answer cut by a barge-in

- **The τ³ client stops our audio on every `speech_started`.** `tau2-bench-smasurekar/src/tau2/voice/audio_native/openai/discrete_time_adapter.py:267-305`
  truncates the playing item and sends `conversation.item.truncate`. Our server emits `speech_started` after
  `turn_detection.min_speech_ms: 120` (`voice_frontend_backend_agent/config/voice_agent.yaml:57`), so "Okay." or
  "Mm-hmm" is enough.
- **Server side:** `on_speech_started` (`turn_manager.py:434-448`) → `_interrupt_output` (`:1195-1215`) stops the
  running item, settles **every** unplayed item (the queued answer too) as `partial` or `not_heard`, and discards the
  audio (`:1215`). `on_truncate` (`:516-531`) settles the client's truncate the same way.
- **Only the backend learns about it, and only on its next run.** `SharedTranscript.settle` (`transcript.py:142-166`)
  adds a `delivery_note`, rendered as "[your last answer was cut off; the user heard only: "…"]"
  (`prompts.backend.yaml:151-152`) at the start of the next run. `<spoken_output>` asks the backend to "repeat only
  its key point" (`prompts.backend.yaml:66-67`). Nothing repeats the answer unless a new run starts.
- **The replay feature exists but is off and LLM-gated.** `delegation.replay_unheard_answer: {enabled: false}`
  (`profiles/realtime_eval.yaml:19`). Even when on, `_replay` (`turn_manager.py:667-719`) fires only if the frontend
  model returns `delegate=true, request=status` in IDLE (`:671`), which needs the `replay_intent` prompt
  (`config.py:432-434`). It was never evaluated.
- **What the runs show.**
  - Answers heard in full: T55 17%, T52 10%, R55 19%, R52 11%, A52 10% (by length: under 150 characters, 25% in T55; 300+
    characters, ≤2% in every run). Most partial answers were cut in their first 20%.
  - **The dead-air chain:** an answer is cut → the user's turn is a pure backchannel → `guard_backchannel` makes it
    DIRECT-empty → nobody speaks. T55: 166 times (112 in failed tasks), and the user then pinged within two turns in
    92. R55 140 (98 pinged), R52 68 (28), T52 36 (20), A52 24 (14). T55 #64 is the textbook case: the answer "I found
    your account, John…" was cut after 7 characters, its re-runs after 0 and 1 characters by "Okay.", and three pings
    then got silence until the caller hung up.

### 3d. User-side device steps

- **The backend has no device tools; the policy lists 25 of them** under "What the user can do on their device"
  (`tech_support_manual.md:12-53`). It is told to "guide the user to use `toggle_roaming()`", etc.
- **It calls them anyway.** In T55 traces: **114 calls to non-existent tools** (`check_network_status` 30,
  `check_status_bar` 15, `can_send_mms` 13, `run_speed_test` 12, `check_apn_settings` 10, …) in 65 of 114 sessions
  (45 failed). Hermes answers "Tool 'check_status_bar' does not exist. Available tools: …"
  (`hermes-agent-smasurekar/agent/conversation_loop.py:928-939`, from `agent/turn_tool_round.py:108` and
  `agent/turn_tool_validation.py:131`). That message never says that the user can do it. **33 of the 39 failing T55
  transfers** came after such a failed call. Example T55 #9: two failed device calls, a refuel, "Still can't send
  MMS" → transfer within one run; the MMS diagnostics (APN, permissions, Wi-Fi calling, network mode) were never
  given to the user.
- **When it does guide the user, it invents a UI walkthrough instead of the named action.** T55 #31 and #42:
  "Open Settings > Connections > Mobile Networks > Access Point Names…", 20–30 s of speech that is cut repeatedly; the
  user asks "Do you want me to reset the APN settings?" four times. #31: "find … Messages", while the policy says
  `grant_app_permission(app_name="messaging", …)` (`tech_support_manual.md`, last section); the user's tool replies
  "App 'Messages' not found". The policy itself says "You should not provide any information, knowledge, or procedures
  not provided by the user or available tools" (`main_policy.md:9`).
- **Two-step fixes lose their second step.** APN reset requires a reboot (`tech_support_manual.md`, "Incorrect APN
  Settings"); of the 10 failed T55 tasks that needed an APN reset, 8 also miss the `reboot_device`. Steps are given in
  long answers that are cut (§3c), and the next run doesn't track which step is pending.
- The frontend's fillers make it worse: "Let me check your APN settings" repeated while the backend is waiting for
  the user to do it (T55 #31, #42).

### 3e. Other fragility to a terse, impatient caller

1. **Short turns arrive as fragments.** "Five five" / "Dash one two three…" (T55 #64) cancels and merges decisions
   (`decision_cancelled` 1,256 in T55); each fragment can also start `speech_started` and cut audio.
2. **A first request can be lost.** T55 #3: ASR heard "Send MMS messages." and the frontend chose DIRECT-empty in
   NO_SESSION; the two pings were not transcribed; the call ended at 74 s with no lookup. R55 has 9 failed calls with
   NO_SESSION DIRECT turns (tasks 42, 81, 85: no lookup at all).
3. **The frontend echoes noise.** T55 #3 spoke the user's own garbled words as a filler ("Huh, this is inspect"); 3
   such echoes in T55, 5 in R55.
4. **Long answers.** Backend answers have a median of 131 (T55) to 291 (A52) characters, p90 317–680, despite
   `<spoken_output>`'s "at most two short sentences" (`prompts.backend.yaml:63-71`).
5. **Logging hides guard activity.** `turn_manager.py:617` (see §1).

---

## 4. The fixes

Each fix follows the plan's §3 rule: off by default, one flag, its own arm, accepted only if its gates pass. Example
texts are invented and generic.

### Fix 1 — Replay a cut answer when the user only backchanneled or pinged

**Problem.** The dead-air chain in §3c: T55 166 occurrences (92 followed by a ping), R55 140 (98), and in passing
gpt-5.2 tasks 85 (41 followed by a ping). 93% of T55 pings came right after an unheard answer. The collated analysis
lists "answers cut by barge-ins and never replayed" as a persisting issue (§2.1, §9.4).

**Mechanism (code, voice server).**
- New key `delegation.replay_unheard_answer.triggers` (list; default `[]` = today). Values:
  - `backchannel`: when `guard_backchannel` fires (`decider.py:82-86`) and the backend is IDLE;
  - `ping`: when the turn is a pure liveness ping (Fix 2's detector) and the backend is IDLE or NO_SESSION.
- In `_decide` (`turn_manager.py:618`), call `_replay` when a trigger matches, instead of requiring
  `decision.delegate and decision.request == REQUEST_STATUS` (`:671`). Reuse `_replay_check` (`:721-748`) unchanged:
  same run, no task turn since, TTL, replayed at most once, the unheard part from the cut sentence (`_unheard_part`,
  `:1296-1307`). If more than about 80% of the answer was unheard, replay the whole answer.
- `config.py:432-434`: allow `enabled: true` without `prompt_features.replay_intent` when `triggers` is not empty
  (the frontend prompt stays byte-identical).
- Raise `ttl_s` from 30 to 60: the gpt-5.5 caller pings 20–30 s after the cut.
- Log `answer_replayed` with `trigger`.

**Why generic.** It depends only on playback outcomes, the backend state and a backchannel or ping turn. No domain, tool
or task input. Repeating what was cut off when the listener only said "mm-hmm" or "hello?" is what a human agent does.

**Expected effect.**
- gpt-5.5: removes most IDLE dead air; targets most of README rank 2 (12 early hang-ups) and R55's `breakdown` (7).
  Estimate +4 to +8 telecom tasks and +2 to +5 retail tasks (overlaps Fix 2).
- gpt-5.2: small gain. The 41 passing-task pings it pre-empts were answered by a new backend run instead (median
  1.2–1.4 s, and that answer was itself cut 85–90% of the time).

**Risk to passing tasks, and the offline check.**
- The replayed text is the backend's own last answer, so no new facts are introduced. It can be cut again; then the
  chain is the same as today. A replayed confirmation question becomes *heard*, which B1's consent rule wants.
- Possible harm: replaying after the user meant "okay, I'm doing it" during a device step. Mitigation: replay only
  the unheard part, once per answer.
- **Offline check:** run the trigger logic over `events.jsonl` of A52, R52 and T52 (all fields are logged: the
  `playback_outcome` of answers, `delegation_decision`, `backend_state`). For each of the 85 passing-task triggers,
  list the replay text and the user's next turn in the control. Accept if no trigger falls after a task turn or a
  newer run (the guards must already prevent this) and if, when the control user's next turn was a new request
  (not a ping or repeat), the replay is short (one sentence) in at least 90% of cases. Add unit tests for both
  triggers to the existing M2 tests.

**Flag:** `delegation.replay_unheard_answer: {enabled: true, ttl_s: 60, triggers: [backchannel, ping]}`; arms can
test `[ping]` alone first (its exposure is lower).

### Fix 2 — Never answer a question or a liveness ping with silence

**Problem.** §3b: 46 silent pings in T55 (34 in failed tasks), 20 in R55. 13 of the 23 T55 user-stop failures and 8 of
29 R55 ones had an unanswered ping. In WORKING, pings are mislabelled `task`, their filler is dropped, and they abort the
running model call (T52 34, R52 25, A52 23 redirects).

**Mechanism (code, `frontend/decider.py:apply_guards`, plus `_dedupe_filler`).** A new guard, `liveness_reply`, runs
after the existing two and before Fix 1:
1. **Detector (deterministic):** the turn is a *pure ping* if, after removing ping phrases (config list
   `delegation.guards.liveness_patterns`, default English: "hello", "are you (still) there", "you there", "any
   update(s)", "did you find anything", "still working", "how long") and filler words ("um", "okay", "sorry", "so"),
   nothing is left. A turn with other words is not a pure ping and the model's decision stands, except in step 3.
2. **Pure ping:**
   - WORKING → `delegate=true, request=status`. A status never steers (`controller.py:509-539`), so the running model
     call is not aborted. `_dedupe_filler` must not drop this filler: it swaps in an alternative instead
     (`turn_manager.py:810`).
   - IDLE or NO_SESSION → Fix 1's replay if it applies; otherwise `delegate=true, request=task`. The backend answers
     with the delivery note in front, as it does today for the 279 pings it already gets.
3. **Any DIRECT-empty turn whose ASR text contains "?"** (the ASR punctuates) → `delegate=true` (IDLE/NO_SESSION) or
   `request=status` (WORKING). This enforces `prompts.yaml:44-45` ("a question is delegate=true").
4. Log `delegation_guard` with `guard="liveness_reply"` (and fix the `:617` label).

**Why generic.** It is a deterministic version of a rule the frontend prompt already has. No domain words. Liveness
phrases are language-level and live in config.

**Expected effect.**
- gpt-5.5: answers every check-in; with Fix 1, closes the silent-DIRECT path to "This is ridiculous, I'll call back".
  Estimate +2 to +5 tasks per domain beyond Fix 1.
- gpt-5.2: fewer aborted backend calls during slow (429-throttled) runs, so shorter silences. Small gain.

**Risk and offline check.**
- Converting a silent IDLE ping into a delegation adds one backend run (median 0.8–1.4 s) that can only say "yes,
  I'm here" plus the key point. The backend can make tool calls in that run, but the delivery note says what to
  repeat.
- Re-labelling WORKING pings as `status` removes a steer. A ping that carried new information is not a *pure* ping, so
  it keeps today's path.
- **Exposure in passing gpt-5.2 tasks:** 13 silent pings (T52 8, R52 5, A52 0) and 34 DIRECT-empty "?" turns (T52 10,
  R52 16, A52 8); and WORKING pings that change from `task` to `status` (T52 77 + R52 102 = 179, passing and failing
  tasks together).
- **Offline check:**
  - (a) Run the detector over every `delegation_decision` of the five runs; hand-check all 34 "?" turns and a sample of
    50 detected pings for false positives (e.g. "Hello, I want to…" must not be pure).
  - (b) Add 20 invented cases to `delegation_cases.jsonl` and run `cli/delegation_replay.py` to confirm the guard
    output.
  - (c) Pre-A/B gate: zero pure-ping detections on turns with content words.

**Flag:** `delegation.guards.liveness_reply: true` (with `liveness_patterns`); sub-flag
`delegation.guards.question_never_silent: true` for step 3, so each part can be measured on its own.

### Fix 3 — Tell the backend which policy actions belong to the user

**Problem.** §3d: 114 calls to non-existent device tools in T55 (65/114 sessions; 45 failed). 33/39 failing T55
transfers came after one. When the backend does guide the user, it invents menu paths and app names. This feeds
README rank 3 (permissions/APN, 9 failures) and rank 1's transfers.

**Mechanism (code; deterministic, derived from the session's instructions and tools).**
1. **Derive the user-side action names** at `session.configure` (gateway, beside `_system_prompt`,
   `controller.py:263-269`): identifier-shaped names in the instructions written as a call (`name(` or
   `` `name()` ``) or in bold, that are **not** session tools. For τ³ this gives the 25 device actions for telecom and
   an **empty set for airline and retail** (checked on both policies), so their prompts stay byte-identical.
2. **Failure-triggered result (main part):** when Hermes rejects an unknown tool name that is in that set, the tool
   result reads, instead of "Tool 'X' does not exist…": "`X` is an action the user performs on their own device; you
   cannot run it. Ask the user to do it (use the action's name and arguments exactly as your instructions give them),
   then wait for them to tell you the result." Where: wrap `_invalid_tool_name_error_content`
   (`hermes-agent-smasurekar/agent/conversation_loop.py:928`) in the worker at configure time
   (`worker/hermes_adapter.py:235-275`; one process per session, so the wrap is per session). Later, add a proper
   Hermes hook upstream.
3. **Prompt line (optional, `user_side_actions_note`):** when the set is not empty, add one rendered line to
   `backend_system`: "Your instructions describe actions that the user performs on their device ({names}). You
   cannot call them. Ask the user to do one at a time, name it as your instructions do, include its exact
   arguments, and do not describe menus or settings paths that your instructions do not give."

**Why generic.** It is derived from the session's own instructions and tool schemas, never from a domain or tool name.
Any agent whose policy describes caller-side steps ("restart the router", "open the app's settings") has the same
gap.

**Expected effect.**
- gpt-5.5 telecom: fewer "impossible → transfer" endings; device steps named exactly. Estimate +3 to +8 tasks
  (overlaps Fixes 4 and 5).
- gpt-5.2: telecom small gain. Airline and retail: no change (0 ghost calls in A52, R52 and R55 traces; empty set).

**Risk and offline check.**
- Part 2 changes only the text of a call that has already failed, in sessions where today's text leads to transfers.
  Part 3 changes the telecom prompt, so it is gated like a prompt fix.
- **Offline check:**
  - (a) Confirm the derived set is empty for airline and retail and that the rendered `backend_system` hash is
    unchanged.
  - (b) With `cli/backend_decision_replay.py`, re-send each T55 request that follows a ghost call with the new result
    text (5 samples) and count how often the next step asks the user versus transfers. In the 20 *passing* T55
    sessions, check that the replayed decisions still lead to the same writes.
  - (c) Because T52 has no traces, record traces in the next gpt-5.2 telecom control to measure exposure there.

**Flags:** `hermes.user_side_actions: {unknown_tool_result: true, prompt_note: false}`; prompt feature
`user_side_actions_note`.

### Fix 4 — Ask for situational facts the tools can't show

**Problem.** §3a: the backend asked about travel in 4 of 64 abroad tasks. Location surfaced → 13/19 passed; not
surfaced → 0/45. This is README rank 1 (33 failures, 28 of them transfers).

**Mechanism (prompt, backend).** A new block in `prompts.backend.yaml` after `<before_transfer>`, flag
`situational_facts`:

```
<diagnosis>
When your instructions give causes or steps that apply only in a situation the user is in (for example where they
are, what they see on their screen or device, or what they have already tried), and none of your tools shows that
situation, ask the user about it when you reach that cause or step, one short question at a time. Do not treat a
situation as absent because the user has not mentioned it. Ask before you conclude that nothing else can be done.
</diagnosis>
```

**Why generic.** It names a class of facts, not a domain. It encodes what a careful human agent does with a
troubleshooting manual: check every "if the user is…" branch by asking.

**Expected effect.**
- gpt-5.5 telecom: the biggest lever (README rank 1). If the backend asks in half of the affected tasks and 70% then
  pass (13/19 today), about +10 tasks. Prompt adherence is the uncertainty (B1 and C1 were only partly followed).
- gpt-5.2: neutral. The user usually volunteers it; an extra question gets "yes, I'm in …".

**Risk and offline check.**
- An extra question per diagnosis, so slightly longer calls. A rule that is too broad could make the backend ask
  irrelevant questions in airline or retail.
- **Offline check (G2):**
  - (a) Re-send, with the arm's prompt, each T55 request where the control backend *concluded or transferred*
    without the location known (45 tasks); count how often the next step is a question about it.
  - (b) Re-send every write and transfer decision of passing A52, R52 and T55 sessions (traces exist) and require the
    same decision (the plan's `U` label). This shows whether the rule adds questions where none were needed.

**Flag:** `prompt_features.situational_facts` (gateway).

### Fix 5 — One-time checkpoint before the first transfer

**Problem.** Telecom transfers with steps open: collated `transfer_wrong` 11 → 18 with C1 on (T52); T55 has 39 failing
transfers. C1 is a system-prompt rule that is far from the decision point and was "not followed reliably".

**Mechanism (code, tool relay; soft, once per session).** The first time in a session the backend calls a hand-off
tool, the relay answers locally (the call is not sent to the client):

```
Not transferred yet. Before you transfer: if your policy requires a transfer in this situation, call the transfer
tool again now. Otherwise, check the troubleshooting steps in your instructions for the problem the user reported,
ask about any situation that only the user can confirm, and do the steps you have not done; then transfer if it is
still needed.
```

The second call goes through unchanged.
- Where: `tools/relay.py` on the outgoing call, beside A1's local answers.
- The hand-off tool is identified from its schema description (transfer/escalate to a human), or by a config list
  `tools.handoff_tools`. Never by τ³ tool name.
- Log `transfer_checkpoint`.

**Why generic.** "Before you hand off, confirm you have tried what the guide says" is a standard support checklist. It
defers to the policy for required transfers, so it does not repeat the rejected hard gate (plan §3.2, E2).

**Expected effect.**
- gpt-5.5: catches transfers that skipped Fix 4's question or diagnostics; complements Fixes 3 and 4. Estimate +5 to
  +10 telecom tasks (overlapping).
- gpt-5.2: the 48 passing transfers should be re-called at once (one extra model step, about 1–3 s each).

**Risk and offline check.**
- This is the riskiest of the six: it fires on *every* session's first transfer, including the 48 passing gpt-5.2
  transfers, which are policy-required. The model may do extra steps or extra writes first. A transfer delayed by
  extra steps is counted as a regression (plan C1 rule).
- **Offline check (G2):** for every passing transfer with a trace (A52 17, R52 15, T55 17), append the checkpoint
  result and re-sample the next step (5 samples). Require that the model calls the transfer tool again in ≥ 95% of
  samples with no write in between. For the T55 failing transfers, count how often it instead asks or diagnoses.
- Ship only if the passing-transfer check passes. Otherwise drop it and keep Fix 4.

**Flag:** `tools.transfer_checkpoint: {enabled: true, max_per_session: 1}`.

### Fix 6 — The frontend may not claim a completed action

**Problem.** R55 #14 ("Yes, I processed the return for your Gaming Mouse…") and #78 ("The cancellation has been
completed."): DIRECT replies claiming writes that never happened (collated §9.4). The frontend prompt already forbids
stating facts the backend didn't say (`prompts.yaml:13-15`).

**Mechanism (code, `apply_guards`).** If `delegate=false` and the filler matches a completion-claim pattern (config
`delegation.guards.claim_patterns`: "has been / have been …", "I've (added|cancelled|processed|updated|…)",
"successfully", "is complete(d)"), and does not start with "let me" or "I'll", then set `delegate=true`, replace the
filler with a neutral alternative (`filler_alternatives`), and log `guard="claim_needs_delegate"`. The backend, which
knows what it did, answers.

**Why generic.** "Only the component that did the action may report it" is an architectural rule, not a domain rule.

**Expected effect.** gpt-5.5: fixes R55 14 and 78 (+2 retail). gpt-5.2: neutral (the claims there were true
restatements).

**Risk and offline check.**
- Six DIRECT claims in passing gpt-5.2 tasks (R52 5, A52 1; all after real writes and correct) would become
  delegations. That is slower (one backend run) but truthful.
- **Offline check:** list all claim matches in the five runs (R55 9, R52 6, A52 1, none DIRECT in telecom) and confirm
  that no `let me …` statement or question is matched.

**Flag:** `delegation.guards.claim_needs_delegate: true`.

---

## 5. Validation plan

### 5.1 Before any run (offline, no LLM calls unless noted)

| Step | What | Pass criterion |
|---|---|---|
| V0 | Fix the guard label (`turn_manager.py:617`); add `trigger` / `guard` fields to new events | Logging only |
| V1 | Unit tests: Fix 1 triggers and guards; Fix 2 detector (pure vs mixed pings, "?" turns, WORKING → status, dedupe keeps the status filler); Fix 3 name derivation (empty for airline and retail policies); Fix 5 once per session; Fix 6 patterns | All green; all flags off = byte-identical prompts and decisions (golden tests as in the earlier plans) |
| V2 | **Trigger census** on the five runs' `events.jsonl`: re-run the detectors and count firings by run and pass/fail; hand-check every passing-task firing of Fixes 2 and 6 and 50 of Fix 1 | No firing on a turn with new content; counts within ±10% of §1 |
| V3 | `cli/delegation_replay.py` with ~20 invented ping, backchannel and question cases | Guards produce the expected decision in 100% |
| V4 | **G2 backend decision replay** (`cli/backend_decision_replay.py`, LLM, 5 samples) for Fixes 3, 4 and 5 on A52, R52 and T55 traces | `U` decisions of passing tasks unchanged in ≥ 95% of samples; passing transfers re-called after the checkpoint in ≥ 95%; targeted T55 decisions show the intended change (reported) |
| V5 | Record backend traces in the next gpt-5.2 telecom control (T52 has none) | Exposure of Fixes 3–5 measured on gpt-5.2 telecom |

### 5.2 A/B runs

- **Arms** (each against the same control; voice-side and backend-side fixes are independent, but are still tested
  separately first):
  1. `voice-liveness`: Fixes 1 + 2 (one arm: both act on the same dead-air chain; Fix 2 alone is a sub-arm if budget
     allows);
  2. `user-actions`: Fix 3;
  3. `diagnosis`: Fix 4;
  4. `transfer-checkpoint`: Fix 5 (only if V4 passes);
  5. `claim-guard`: Fix 6;
  6. `combined`: every fix that passed alone.
- **Design:** 3 domains × 2 user simulators (gpt-5.2, gpt-5.5) × **≥3 trials**, or paired re-runs of the same task IDs
  with the same seeds. Concurrency 4 at night or on a dedicated quota; the P0.1 429 gate must pass, or the run is
  invalid.
- **Order** (cheapest decisive signal first):
  1. telecom gpt-5.5 for `voice-liveness`, `user-actions` and `diagnosis` (the expected gains are largest there);
  2. retail gpt-5.5;
  3. all three domains on gpt-5.2 as the no-regression check;
  4. `combined`.
- **Acceptance:** per domain and simulator, Pass^1 not lower than control beyond the measured same-code noise (retail
  differs on 35–40% of tasks between identical runs; with 3 trials × 114 tasks, one SE of Pass^1 is ≈ 0.026). Every
  pass → fail task is diagnosed and attributed. The targeted counters move: silent pings → ~0; "cut → backchannel →
  ping" chains down by at least half; ghost-call → transfer sessions down; abroad tasks where the location reached the
  backend up from 19/64.
- **Report:** the numbers in §1 per arm, plus R_R, I_A, call length and user-stop count. Fixes 1 and 2 should reduce
  user stops.

---

## 6. Rejected or deferred, and why

| Candidate | Verdict | Reason |
|---|---|---|
| (i) Backend runs the policy's diagnostic questions/checks proactively | **Accepted as Fix 4 (+ Fix 5)** | Asking is the only way to learn facts no tool shows (§3a). Kept to "ask about situations", not "run every check": the backend can't run device checks, and each one costs the user a turn |
| (ii) Never answer a liveness ping with silence, in code | **Accepted as Fix 2** | Deterministic and narrow. Also fixes WORKING pings aborting the backend |
| (iii) Replay or summarise an unheard answer after a barge-in | **Accepted as Fix 1, replay only** | A *summary* needs an LLM call and could add or drop facts; replaying the unheard part of the backend's own text is deterministic. Replaying after *every* cut is rejected: 81–90% of answers are cut in passing tasks too, and the user's new request must win |
| (iv) Raise or tune proactive status during long backend work | **Rejected for this problem** (keep as track I3) | Pings happen in IDLE (341/352 in T55); proactive status fires only in WORKING (`turn_manager.py:1077`) and fired twice in T55. It would help slow, 429-throttled gpt-5.2 runs, but that is infra and validated separately (plan track I) |
| (v) Pre-transfer gate that re-checks diagnostic state tools | **Rejected as stated; replaced by Fix 5** | The device state can only be checked by the user, so code cannot "re-check" it. A hard gate was already rejected on 36 passing transfers (plan §3.2, E2). Fix 5 is a one-time soft checkpoint that defers to the policy |
| (vi) Frontend must not claim an action without a tool result | **Accepted as Fix 6, narrowed** | Matching a claim to a *specific* tool result is not reliable (R55 #78 had other writes earlier). The narrow form sends claims to the backend instead |
| Backchannel-tolerant barge-in (don't emit `speech_started` for short speech while the agent talks) | **Deferred** | Would avoid many cuts at the source, but changes Realtime protocol timing that the τ³ client acts on (`discrete_time_adapter.py:267-305`), delays every real interruption, and needs its own latency study. Fix 1 recovers most of the loss after the fact |
| Show the frontend its own silent turns (stop merging consecutive user turns, `transcript.py:205-206`) | **Deferred** | Plausible cause of sticky silence (34/46 silent pings followed another silent turn), but it changes every frontend request in every domain; Fix 2 removes the harm deterministically. Test later with `delegation_replay` |
| Deterministic cap on spoken answer length (speak two sentences, hold the rest) | **Deferred** | Long answers are cut (≤2% of 300+ character answers heard in full), but splitting can separate a confirmation summary from its question, which B1 needs whole. Needs semantic chunking and its own gate |
| Pass τ³'s workflow policy, or add a roaming note | **Rejected** | τ³-specific and against the genericity rules; the agent must work with the policy it is given |
| Force-delegate every non-backchannel turn in NO_SESSION (T55 #3) | **Deferred** | The NO_SESSION DIRECT turns of passing gpt-5.2 tasks (14) are mostly "hold on a second", which should stay silent; Fix 2 already covers questions and pings there |

---

## Appendix: how the numbers were measured

- **Ping:** a user turn (`delegation_decision.text`) matching "still there / you there / any update / anything yet /
  did you find / hello," and similar. Counts are per decision.
- **Silent:** `delegate=false` with an empty filler, or the filler dropped by `filler_deduped`, with no
  `output_release` before the user's next `speech_started`.
- **"After an unheard answer":** the most recent `playback_outcome` of a `backend_answer` before the ping was
  `partial` or `not_heard`.
- **Dead-air chain:** a `backend_answer` settled `partial` or `not_heard`, then the next decision is DIRECT on a pure
  backchannel (the `backchannel_words` list) while not WORKING; "then pinged" = one of the next two decisions is a
  ping.
- **Ghost calls:** in each trace, tool calls in `response.assistant_message.tool_calls` whose name is not in the
  `session.tools` list.
- **Location surfaced:** the backend's final request contains a user message mentioning travel or the country, or the
  backend asked a travel/location question (regex on assistant content with "?").
- The scripts were throw-away (scratchpad) and are not committed; every count can be re-derived from the fields named
  above.
