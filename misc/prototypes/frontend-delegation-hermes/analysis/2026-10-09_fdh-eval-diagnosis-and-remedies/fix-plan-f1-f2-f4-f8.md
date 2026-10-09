# FDH: implementation plan for the top three fixes (F1 + F2, F4, F8)

**Date:** 2026-10-09. **Revision 5:** it answers three design reviews (§0.2, §0.3, §0.5) and records the owner's
decisions (§0.4). This is a plan only; no code has changed.

**Status (2026-10-09): implemented, on by default (owner decision, §10.4), not yet evaluated.** Step 0 and the
F1 + F2, F4 and F8 fixes are in the code with their arm configs and a control that reproduces agent `124acf7`. §10 lists what was built, what was deferred and where the code differs from
this text. The runbook's §6.4 says how to start each arm.

**One submission configuration.** τ³ takes a single agent configuration for all domains. Every fix is therefore built
to run in every domain from one profile (`realtime_eval_combined.yaml` + `gateway.eval_combined.yaml`, §5.2). Where
a fix should matter only in some calls, the trigger or the wording limits it. There is never a per-domain switch.

**Fixes covered** (ranks from [`fdh-eval-diagnosis-and-remedies.md`](fdh-eval-diagnosis-and-remedies.md) §1):

| Rank | Fix | Failures targeted | Expected flips |
|---|---|---|---|
| #1 | F1 + F2: no dead air | 48 | ≈ 18 |
| #2 | F4: ask for facts no tool can show | 29 | ≈ 20 |
| #3 | F8: identity recovery | 26 | ≈ 14, an upper bound (§4.1) |

**Code:** `src/prototypes/voice_delegation_hermes_agent/` at `124acf7`. All file and line references below are to that
commit. The eval profile is `config/profiles/realtime_eval.yaml` + `config/gateway.eval.yaml` (config hash
`a30f6df59a8b`).

---

## 0. Ground rules and review answers

### 0.1 Safety rules every change follows

1. **Off by default, one flag per behaviour.** With every new flag off, the voice server's decisions and both rendered
   prompts are byte-identical to the deployed `124acf7`. The goldens are captured from that commit before any edit
   (§1).
2. **Generic.** No domain, task or tool names in code, prompts or config. Anything tool-specific is derived per session
   from the session's tool schemas and instructions, and every derived set is logged. Example texts are invented.
3. **Language.** The F1/F2 phrase lists are domain-independent but language-specific. §2.2 defines how the language is
   chosen and what happens when there is no list for it.
4. **Narrow triggers.** A fix acts only in a moment that is already broken: a silent turn, a lost answer, a failed
   identification lookup, or a session whose policy has caller-side steps.
5. **Never replace a decision that speaks or delegates content.** F1 and F2 change only *silent* decisions, plus one
   re-label: a liveness-only turn in WORKING goes from `task` to `status`.
6. **Logged and fingerprinted.** Every firing writes an event with its rule name. Every flag appears in
   `DelegationConfig.features` (`config.py:265`) or `backend_features` (`controller.py:242-253`).
7. **Gated.** Each fix passes, in order: unit tests, an offline census, a decision replay where it is prompt-level, then
   a paired A/B whose safety and mechanism checks are **mandatory** (§5.4).
8. **When in doubt, do nothing.** An uncertain classification (an ambiguous lookup result, an unknown output shape, a
   missing lexicon) falls back to today's behaviour, never to a new action.
9. **Scope of the evidence.** Nothing here depends on a particular user-simulator model at runtime. Robustness beyond the
   two simulators tested (gpt-5.2 and gpt-5.5) is unproven. The judge stays fixed within each comparison (§5.3).

### 0.2 First review, answered in revision 2

| # | Point | Resolution | Section |
|---|---|---|---|
| 1 | Evidence collection was tied to A3 | Separate switches for collection and for the A3 line | §4.2 |
| 2 | The classifier was under-specified | Full matching rules, never-strip words, a test table | §2.2 |
| 3 | A name inside a failed email counted as tried | Superseded by §0.3 #4: spellings are never hidden, only annotated | §4.2 |
| 4 | Any read could clear identity history | Superseded by §0.3 #1–#3 | §4.3 |
| 5 | F4's scope depends on policy formatting | Superseded by the owner decision (§0.4): the block renders everywhere and its wording limits it; immediate transfers kept | §3.2, §3.3 |
| 6 | The census needed reconstruction | Strengthened by §0.3 #5 | §6 |
| 7 | Replay validation was incomplete | Shared render, full-sequence comparison, F8 extraction, patching by call ID | §7 |
| 8 | Compatibility and rollout | Dedupe, exhausted alternatives, threshold 0, silent-only replay, goldens, combined arm. Tightened by §0.3 #6–#8. | §2.3, §2.4, §5 |

### 0.3 Second review, answered in this revision

| # | Point | Checked | Resolution | Section |
|---|---|---|---|---|
| 1 | F8 tool selection is too broad | Confirmed: in telecom, `get_bills_for_customer(customer_id)` and `get_data_usage(customer_id, line_id)` qualified | An identification tool must *return a person* by its name or description, and take no other entity's ID. A config override exists, and the derived set is logged and hand-reviewed. | §4.3 |
| 2 | Success detection is unsafe | Confirmed: an empty list or an unknown shape counted as `found` | `found` only for exactly one person record or one ID string. Empty means `not_found`. Several records, or an unknown shape, are neutral and never close an episode. | §4.3 |
| 3 | Episode handling is incomplete | Agreed | Spellings before the first lookup join the next episode. A person change before success is not inferred; items carry turn context and an age cap. Late results after a close are logged only. | §4.3 |
| 4 | "Already used" is ambiguous | Agreed: removing separators makes `john.doe` equal `johndoe` | No spelling is ever hidden. Each shows where it was sent (which field, exact or different separators) or "not sent". The code never assigns a spelling to a field. | §4.2 |
| 5 | The census needs exact joins | Keys verified in the dump | Joins by session ID, call ID and answer ID, with hash cross-checks. The original tool output is taken from `tool_output_in`, never by splitting text. | §6, §7 |
| 6 | Acceptance gates are incomplete; margins allow 4–5 pp regressions | Agreed | Safety and mechanism checks are now pass/fail. The primary regression test pools every cell with a 2-point margin; the per-cell check is a guard against large drops. Trials are added by a rule fixed in advance. | §5.4 |
| 7 | The identifier prompt changes telecom too | Confirmed: it renders in every session | `identity_name` runs in all 6 cells. With shared controls: 96 runs. | §5.3 |
| 8 | Flag and language behaviour is unclear | Confirmed: the F1 ping/repeat triggers only fired through R1 | F1's triggers are independent of R1. One table defines every dedupe rule. The language source and locale fallback are defined. | §2.2–§2.4 |

### 0.4 Owner decisions (2026-10-09)

| Question | Decision | Effect on the plan |
|---|---|---|
| Where does the F4 rule apply? | **In every domain**, because τ³ takes one submission. The wording must be generic, so that it asks in telecom-like calls and does nothing elsewhere. | The block renders in every session. Its wording limits it to situations the policy makes conditional and no tool can show. The code-derived scope switch is removed. All 6 cells are A/B-tested. (§3) |
| If the replay shows the F4 rule is mostly ignored | **Add the F5 checkpoint** | F5 is designed here as F4's fallback, with its own gate (§3.6) |
| How to spend the live runs | **Targeted cells first**, with an early stop | Three stages with stop rules (§5.3) |
| How strict the no-harm bar is | **About 2 points**, as proposed | The §5.4 thresholds are fixed |

### 0.5 Third review, answered in this revision (each point verified before acting)

| # | Point | Verified | Resolution | Section |
|---|---|---|---|---|
| 1 | Trace and event call IDs differ | Yes: traces use `chatcmpl-tool-…`, events use `fdh_<session>_<epoch>_<n>`. The trace's `task_id` is `<session>:<epoch>`. | Join by epoch, then by call order with name and arguments checked. New runs log both IDs. | §1, §6, §7 |
| 2 | The hash and playback joins are wrong | Yes: for the same 13 tools, trace `tools_sha256` = `a76c…` and event `session_tools_sha256` = `d9b1…`. A `delivery_note`'s `seq` is the note's own. The system-prompt hash does match (`e69f6c719285ddae` in both). | Compare sorted tool names, not tool hashes. Link a delivery note to the `playback_outcome` logged just before it. New runs log a canonical tools hash on both sides and `answer_id` on the playback. | §1, §6 |
| 3 | `found` accepts any `*_id`, a null ID or an error's request ID | Yes, as written | Explicit order of rules, and a non-empty **person** identifier is required | §4.3 |
| 4 | F5's setting sat in the gateway config, but the relay runs in the voice server | Yes: `tools/relay.py` is created by the voice server's turn manager | The setting moves to the voice profile. The replay builds a matching assistant call and tool result. | §3.6, §5.2, §7 |
| 5 | The acceptance math is not exact | Agreed | Weighting, pairing, bootstrap unit, noise estimate and the extra-trial rule are defined | §5.4 |
| 6 | Mechanism denominators are undefined; identity counts too narrowly | Agreed | Each check names its denominator and the zero-control case. Identity also counts confirmed combinations and alternative lookups. | §5.4 |

---

## 1. Step 0: instrumentation and deployed goldens (can start now)

| Change | Where |
|---|---|
| Log the real guard name (`guard=guarded.repair`) | `engine/turn_manager.py:616-617` |
| Add `trigger` to `answer_replay_skipped` / `answer_replayed` | `turn_manager.py:676`, `:698-708` |
| Add `answer_id` and `heard_text` (cap 300 characters) to `playback_outcome`; add the cleaned spoken text to `backend_answer`; add `kind` to each `transcript_normalized` span | `turn_manager.py:1217-1235`, the `backend_answer` log, `:650` |
| Log the output class of each tool output (`found` / `not_found` / … per §4.3), computed only when F8 is on | `tools/relay.py:179` |
| Log the backend's own tool-call ID (`backend_call_id`, the Hermes `chatcmpl-tool-…` ID) beside each `fdh_…` call ID in `tool_calls_out` | `worker/tool_futures.py:91`, where the `fdh_…` ID is made. The handler `call(name, args, task_id)` does not receive Hermes' tool-call ID today, so this needs the ID passed through the Hermes tool handler (a `hermes-agent` change). If that is not possible, the order-based join (§6) stays the permanent method; it is already specified and checked. |
| Log one canonical tools hash on both sides: sha256 of the sorted list of `{name, description, parameters}` serialised as canonical JSON. Field `tools_canonical_sha256` in the trace `session` record and in `backend_configured`. | `worker/hermes_adapter.py` (trace), `controller.py:242-253` |
| Add the new flags to the fingerprint | `config.py:265-280`, `prompt_features.py:21-34` |

**Deployed goldens:**
- From a `124acf7` worktree, render and store under `tests/unit/prototypes/delegation/golden/`:
  - the frontend prompt;
  - `backend_system` for three invented policies;
  - a scripted 20-turn decision trace.
- `scripts/golden_sha_check.py` then renders the logged τ³ policies with all new flags off. It must reproduce every
  `backend_system_sha256` and `frontend_prompt_sha256` logged in A4–A9.

---

## 2. #1 No dead air (F1 + F2): voice server, code only

### 2.1 What goes wrong, in code terms

1. The τ³ client stops our audio on every `speech_started`, so "Okay." cuts the answer (`turn_manager.py:1195-1215`).
2. A pure backchannel is forced into a silent DIRECT turn (`frontend/decider.py:82-86`). Replay is off
   (`profiles/realtime_eval.yaml:19`) and gated on an LLM `status` label (`turn_manager.py:671`).
3. Pings sometimes get silence from the frontend model, and no guard looks at pings or questions (`decider.py:69-94`).
4. In WORKING, pings labelled `task` abort the backend's model call (`controller.py:290-298`), and a repeated filler is
   dropped (`turn_manager.py:810`).

### 2.2 The turn classifier

New function `classify_turn(text, lexicon) -> TurnClass` in `frontend/decider.py`.

**Language selection:**
- **Source:** the session's ASR language. That is the voice profile's ASR `language_code` (e.g. `en-US`), overridden by
  the client's `session.update` input-transcription language when the client sets one.
- **Fallback:** the exact tag (`en-US`), then the base language (`en`), then none.
- **With no lexicon,** `classify_turn` returns `unclassified`. Every F1/F2 rule ignores such turns, so the behaviour is
  today's. A `lexicon_missing` event is logged once per session.
- **Shipped:** `en` only (`delegation.guards.lexicon.en`), the same convention as the normaliser's `ruleset: en`
  (`voice_frontend_backend_agent/config/voice_agent.yaml:129`).

**Normalisation:**
1. Lower-case. Map Unicode apostrophes and dashes to ASCII.
2. Record `has_question_mark`.
3. Replace characters other than letters, digits, `'` and `-` with spaces, then split into tokens.

**Matching:**
- Phrases are tokenised the same way and match whole-token runs only.
- Scanning is left to right; at each position the longest phrase across all lists is tried first.
- Matched runs are removed and their list recorded. Unmatched tokens are content, unless they are `softeners`.
- **Never strippable:** `no`, `not`, `never`, `don't`, `can't`, `won't`, `yes`, `yeah`, `yep`, and any digit token,
  unless they sit inside a listed phrase.

**Result:**

| Class | Condition |
|---|---|
| `liveness` | no content, and at least one `ping` or `repeat` match (`repeat=True` if a repeat phrase matched) |
| `backchannel` | no content; only backchannel and softener matches |
| `hold` | no content; at least one `hold` match, and no ping or repeat |
| `content` | anything else |
| `unclassified` | no lexicon for the session's language |

`has_question` is `has_question_mark`, or the first content token is a question word (`what`, `when`, `where`, `why`,
`how`, `who`, `which`) or an auxiliary (`can`, `could`, `would`, `will`, `do`, `does`, `did`, `is`, `are`) followed by
`you` or `i`.

**Lexicon (`en`):**

| List | Phrases (each a whole-token sequence) |
|---|---|
| `ping` | hello · are you there · are you still there · you there · still there · anyone there · is anyone there · can you hear me · any update · any updates · anything yet · did you find anything · are you still working · still working · how long · still waiting · i'm still here · i'm still waiting · what's happening · what's going on |
| `repeat` | repeat · repeat that · can you repeat that · could you repeat that · can you say that again · could you say that again · say that again · say it again · come again · pardon · what was that · i didn't catch that · i didn't hear that · i didn't hear you · i can't hear you · you cut out · you're breaking up · i missed that |
| `hold` | hold on · hang on · wait · one moment · one second · one sec · a moment · a second · second · sec · moment · give me a moment · give me a second · just a moment · just a second · let me check · let me see |
| `backchannel` | `delegation.guards.backchannel_words` (`delegation_agent.yaml:102-103`) |
| `softeners` | please · sorry · um · uh · er · so · just · hey · oh · well |

**Classifier tests** (`test_fdh_decider.py`, table-driven):

| Turn | Expected |
|---|---|
| `Hello? Are you still there?` | liveness |
| `Okay. Hello, any update?` | liveness |
| `Hold on. Second. Okay. Any update? Are you still there?` | liveness |
| `Sorry, could you repeat that?` / `could you repeat that` | liveness, repeat |
| `Okay.` / `Mm-hmm.` / `okay sure` | backchannel |
| `Okay?` | backchannel, has_question (R2 does not apply) |
| `Give me a moment.` / `Second.` | hold |
| `No.` / `Yes.` / `Not okay.` / `That's not right.` / `No, I'm still here` | content |
| `Hello, I want to change my order` | content |
| `Any update on order 1234?` | content, has_question |
| `what do you need from me` / `can you check my balance` | content, has_question |
| `I can't hear you` | liveness, repeat |
| `hellooo` | content |
| any text in a session whose language is `de-DE`, with no `de` lexicon | unclassified |
| any text in a session whose language is `en-GB` | classified with `en` (fallback) |

### 2.3 F2: never answer a ping or a question with silence

**Where:** a new step in `apply_guards` (`decider.py:69-94`), after the two existing guards.

| Rule | Flag | Condition | New decision |
|---|---|---|---|
| R1 | `liveness_reply` | silent decision (`delegate=false`, `filler=""`) and turn is `liveness` | IDLE / NO_SESSION: `delegate=true, request=task`. WORKING: `delegate=true, request=status`. Filler: the next `liveness_lines` entry. F1 may pre-empt this (§2.4). |
| R2 | `question_never_silent` | silent decision, turn is `content`, and `has_question` | `delegate=true, request=task`. Filler: from `filler_alternatives`. |
| R3 | `ping_as_status` | `delegate=true, request=task`, state WORKING, and turn is `liveness` | `request=status`; the model's filler is kept |

**Fillers and dedupe: one table, no other rules** (`_dedupe_filler`, `turn_manager.py:798-812`):

| Filler source | Repeated filler | All alternatives recent |
|---|---|---|
| R1 (`liveness_lines`) | Not deduped; the lines rotate cyclically. The flag is `liveness_reply` alone. | Not applicable (rotation) |
| R2 (`filler_alternatives`) | The next one not in the recent list | The least recently used alternative. Never empty. |
| R3 (the model's filler) | Replaced from `filler_alternatives` | The least recently used alternative. Never empty. |
| Every other decision | Today: replaced in IDLE, dropped in WORKING | Today: dropped |

Each rule's dedupe behaviour is switched by that rule's own flag, so turning one rule off restores today's dedupe for
its turns.

**New catalog list** `liveness_lines` in `config/prompts.yaml`. It contains no facts, and the frontend system prompt is
unchanged:

```
Yes, I'm here.
I'm still here, one moment.
Yes, I'm with you.
```

**Log:** `delegation_guard` with `guard`, `turn_class` and `backend_state`.

### 2.4 F1: replay a cut answer when the caller added nothing new

**Where:** `_decide` / `_replay` in `engine/turn_manager.py:618-719`; config at `config.py:174-178`, `:377-380`,
`:432-436`.

**`delegation.replay_unheard_answer.triggers`** (default `[status_intent]` = today). Each trigger is **independent of
R1–R3**:

| Trigger | Fires when (always on the decision after the existing guards and R1–R3) |
|---|---|
| `status_intent` | today's path: the model chose `delegate=true, request=status` in IDLE (needs `prompt_features.replay_intent`) |
| `backchannel` | turn class `backchannel` and the decision is silent |
| `ping` | turn class `liveness` without `repeat`, the state is IDLE or NO_SESSION, and the decision is silent **or** R1 produced it |
| `repeat` | as `ping`, with `repeat=True` |

**Precedence:** if a trigger matches and `_replay_check` passes, F1 replaces the decision with the replay.

| Case | Outcome |
|---|---|
| R1 on, replay passes | replay |
| R1 on, replay fails | R1's delegation |
| R1 off, replay passes | replay |
| R1 off, replay fails | today's silence |

- **Never replaces** a decision the model made that speaks or delegates content.
- **Gate:** `_replay_check` (`turn_manager.py:721-748`), unchanged: IDLE; same run; no newer run; no task since; TTL;
  once; not heard in full.
- **What is spoken:** `_unheard_part` (`:1296-1307`). `whole_if_heard_below` defaults to **0**; the arm sets 0.2. The
  TTL defaults to 30 s; the arm sets 60 s.
- **Order in `_decide`:**
  1. existing guards;
  2. R1–R3;
  3. F1;
  4. `_apply`.
- **Log:** `answer_replayed` / `answer_replay_skipped` with `trigger` and `reason`.

### 2.5 Tests (`test_fdh_voice_turns.py`, `test_fdh_delegation_config.py`)

**Replay behaviour:**
- Cut answer + "Okay." → replay of the unheard part.
- Cut answer + ping → replay; a second ping → R1 delegates.
- Heard answer + "Okay." → silence. Heard answer + ping → R1 delegates with a liveness line.
- Cut answer + a new request → no replay. Task since the answer, or a newer run → no replay.
- The model delegates "could you repeat that?" → no replay.

**Flag matrix** (each flag alone and in pairs):
- `ping` trigger with `liveness_reply: false` → a silent ping is replayed. If the replay gate fails → silence (today).
- `liveness_reply` alone (no replay triggers) → a silent ping is delegated.
- `ping_as_status` alone → a WORKING ping is relabelled. A repeated filler is replaced; with every alternative recent,
  the least recently used one is spoken.
- `question_never_silent` alone, every alternative recent → the least recently used filler, never empty.
- Every flag off → matches the `124acf7` golden decision trace.

**Language:**
- An `en-US` session classifies.
- A `de-DE` session without a lexicon → every rule inactive, `lexicon_missing` logged once.
- A client override of the language is honoured.

**Config:**
- `[backchannel]` without `replay_intent` loads.
- `[status_intent]` without `replay_intent` fails, as today.

### 2.6 Offline gate (on reconstructed data, §6)

| Rule | Pass criterion |
|---|---|
| R1 | 0 firings on `content`. Hand-check every firing in a passing task, and 50 others. |
| R2 | Hand-check every firing in passing tasks; none is side-talk, rhetorical, or addressed to someone else |
| R3 | 0 firings on content; check a sample of 50 |
| F1 | 0 firings after a task turn or a newer run. Replay ≤ 1 sentence in ≥ 90% of cases where the control caller's next turn was a new request. |
| Coverage | ≥ 30 of the 48 F1/F2 failures fire at the diagnosed point. Failures with `unknown` data are reported separately. |

---

## 3. #2 Ask for facts no tool can show (F4): backend prompt, in every domain, limited by its wording

### 3.1 What goes wrong

The policy states some branches as conditions on the caller's situation, and no tool shows that situation. The backend
treats an unmentioned condition as absent. Two measurements of the gpt-5.5 telecom run, both kept:

| Measurement | Method | Location known | Location not known |
|---|---|---|---|
| Transcripts (collated §10.3) | location appears in the conversation | 13 of 20 passed | 0 of 44 passed |
| Backend traces (`robustness-fixes-proposal.md` §3a) | location appears in the backend's final request, or it asked | 13 of 19 passed | 0 of 45 passed |

The difference is one task: the location was said in the conversation but never reached the backend's request.

### 3.2 Scope: in every session, limited by its wording (owner decision, §0.4)

- The block renders in **every** session when `situational_facts` is on, so one submission profile serves all domains.
- **No code decides per domain or per policy.** The earlier `caller_actions` switch and its policy heuristic are
  removed from F4.
- **Its wording limits it to situations where all three hold:**
  1. the policy makes a cause or step conditional on the caller's situation;
  2. that situation matters for the problem the caller reported;
  3. it is not already known, and no tool can show it.
- **Why that keeps it quiet where it isn't needed.** In a call where every fact the policy depends on comes from a tool
  (a booking, an order, a membership level), condition 3 fails and the rule asks nothing. In a call whose policy
  depends on the caller's device or whereabouts, it asks.
- **Shared render function** `render_backend_system(templates, instructions)`, used by both the controller
  (`controller.py:263-269`) and the replay CLI (§7), so the two cannot diverge.

### 3.3 The prompt block

**Where:** after `</before_transfer>` in `config/prompts.backend.yaml` (`:136-137`).

**Feature:** `situational_facts` (off by default).

```jinja
{%- if features.situational_facts %}

<situational_facts>
This applies only when your instructions make a cause or a step depend on the user's own situation,
for example where they are, what their device or screen shows, or what they have already tried, and
none of your tools can show that situation. If such a situation matters for the problem the user
reported and you do not already know it from the user or from your tools, do not treat it as absent:
ask the user, one short question at a time, when you reach that cause or step, and before you
conclude that nothing else can be done. Do not ask about anything your tools already show or that
does not change what you would do. This never delays a transfer that your policy requires right away.
When you ask the user to check something on their own device or screen, ask them to read out
everything it shows, exactly as shown, and check each item against your instructions.
</situational_facts>
{%- endif %}
```

- **"This applies only when…" and "do not ask about anything your tools already show":** these keep the rule silent in
  calls whose facts all come from tools.
- **"Never delays a transfer that your policy requires right away":** this keeps C1's immediate-transfer rule
  (`prompts.backend.yaml:121-122`).
- **No τ³ words:** no "roaming", "abroad" or "APN".

### 3.4 Tests

**Goldens:**
- F4 off → byte-identical to the `124acf7` goldens.
- F4 on → the block renders, identically, for every invented policy (new golden).

**Render test:** the controller and the replay CLI produce the same `backend_system` for the same inputs.

### 3.5 Offline gate: G2 decision replay (§7), in all three domains

**Data:**
- Traces of the gpt-5.5 runs: A7 airline, A8 retail, A9 telecom.
- Traces of the gpt-5.2 airline and retail runs (A4, A5), where present in `logs/fdh_workers/`.
- A6 (gpt-5.2 telecom) has no traces, so its gate is `unknown` until the fresh control.

**Labels:**
- **T:** the transfer or concluding answer in the 28 F4-mapped A9 tasks.
- **U:** every write and transfer decision of the passing sessions in **every** traced run.

**Criteria (5 samples, temperature 0):**
1. **U, every domain:** the full tool-call sequence is identical in ≥ 95% of samples, checked per domain.
2. **T:** asks about the situation, or for a full screen reading, in ≥ 50% of samples. Below that, add F5 (§3.6) before
   any A/B.
3. **Quiet where not needed:** among 30 sampled final answers from passing airline and 30 from passing retail sessions,
   at most 2 per domain add a question the control did not ask. Every case is hand-checked.
4. **Hand check in telecom:** 20 samples from passing telecom sessions; at most 1 irrelevant question per session.
5. **Limit:** one-step replay cannot measure how many questions a whole call adds; the A/B does (§5.4).

### 3.6 Fallback: F5, a one-time checkpoint before the first transfer (owner decision)

**Used only if** F4 fails criterion 2 of §3.5. F5 enforces the same idea at the moment it matters, in code.

**Mechanism (tool relay, `tools/relay.py`, beside the existing local answers):**
- The first time in a session that the backend calls a **hand-off tool**, the relay answers locally and does not send
  the call to the client:

  ```
  Not transferred yet. If your policy requires a transfer in this situation, call the transfer tool
  again now. Otherwise, first check the steps your instructions give for the problem the user
  reported, ask about any situation that only the user can confirm, and do the steps you have not
  done; then transfer if it is still needed.
  ```

- The second hand-off call goes through unchanged. It fires at most once per session.
- **Where the setting lives:** the relay runs in the **voice server** (`tools/relay.py`, created by the turn manager).
  The setting therefore goes in the voice server's profile, under `tools.transfer_checkpoint:
  {enabled, max_per_session: 1, handoff_tools: auto}`:
  - parsed into `ToolSettings` (`config.py`);
  - passed to the relay when the turn manager constructs it;
  - logged in `DelegationConfig.features`.
  The gateway needs no change.
- **Hand-off tools are found from the session's schemas:** a write tool whose name or description says it transfers or
  escalates to a human or an agent. They can also be listed in `tools.transfer_checkpoint.handoff_tools`. The derived set is logged in
  `session_rules`. No tool names are shipped in code.
- **Log:** `transfer_checkpoint` with the call ID.

**It is generic:** every domain uses it, as one setting, and a session with no hand-off tool is unaffected.

**Risk:** it fires on the first transfer of every call, including transfers that pass today and that the policy
requires.

**Gate before any A/B (G2, §7):**
- For every passing transfer with a trace, in all three domains: append the checkpoint result and re-sample the next
  step 5 times. The transfer tool must be called again in **≥ 95%** of samples, with **no write in between**.
- For the F4-mapped A9 failures: the share of samples that ask or diagnose instead (reported).

**Safety check in the A/B** (added to §5.4): the time from the first transfer attempt to the actual transfer in passing
tasks is at most 1 extra backend step, and the number of transfers made is ≤ the control's.

---

## 4. #3 Identity recovery (F8): an evidence ledger in the failed-lookup note, plus one prompt line

### 4.1 What goes wrong, and what F8 can and cannot fix

After a failed person lookup, the existing notes give advice: I3/I4 (`tools/result_hints.py`) and A3
(`tools/field_provenance.py`). The backend still fails in three ways:

| Failure type | Tasks |
|---|---|
| (a) A spelled value was never sent | retail 5.2: 18, 36, 63; retail 5.5: 24, 35, 63 |
| (b) The right pieces were sent in different lookups but never together | retail 5.2: 4, 9, 62, 85, 90, 106; retail 5.5: 1 |
| (c) Another way to identify the user was not used | retail 5.2: 5, 6, 7, 86 |

**Out of reach:** letters that never reached ASR (retail 5.5 tasks 49 and 52; also retail 5.2 38, retail 5.5 38 and
74). The ≈ 14 flips is an upper bound. The census (§4.6) measures the subset where the correct value was transcribed.

### 4.2 The evidence store and the ledger

**Evidence store:**
- `FieldProvenance` (`field_provenance.py:85-87`) is created when `result_hints.field_provenance` **or**
  `result_hints.ledger` is on. The condition at `turn_manager.py:184` changes.
- Each stored turn gains a `turn_id`.
- The A3 line prints only with `field_provenance`; the ledger only with `ledger`.

**Module `tools/identity_ledger.py`.** One per session. It records:

| Item | Fields |
|---|---|
| `Attempt` | `episode`, `call_id`, `tool`, `{field: value}` as sent, `outcome` (§4.3), `origin` (`model` / `format_retry`) |
| `Spelling` | `episode` (assigned per §4.3), `turn_id`, `written`, `question` (the heard assistant question before it, ≤ 80 characters) |
| `Correction` | `episode`, `turn_id`, the user-turn text verbatim (≤ 160 characters), when the turn has a cue from `ledger.correction_cues` (English: `not`, `no,`, `it's`, `with two`, `double`, `instead`, `wrong`) |

**Where a spelling was sent (review §0.3 #4).** No spelling is ever hidden. Each is compared with every value sent in
the **same episode** and gets exactly one status:

| Status | Rule |
|---|---|
| `sent as <field> in lookup <n>` | equal to a sent field value, ignoring letter case only |
| `sent as <field> in lookup <n>, with different separators` | equal after removing `.`, `-`, `_` and spaces, but not exactly. Shown, so the backend sees that `john.doe` and `johndoe` differ. |
| `not sent` | neither |

If several statuses apply, the earliest lookup is shown. A spelling that appears inside a longer sent value (a name
inside an email) is `not sent`. **The code never decides which field a spelling belongs to**; the question it answered
is shown as context.

**The note:** new catalog key `lookup_ledger` in `voice_frontend_backend_agent/config/prompts.voice.yaml`, appended
after the I3/I4 note in `relay._annotate` (`tools/relay.py:328-345`):

```
[Identification so far in this call (not found yet):
 Lookups tried: 1) {tool} {field}="{value}" ...; 2) ...                       (newest 4; 2nd miss on)
 What the user spelled: "CHEN" (asked: "Could you spell your last name?") - not sent;
   "ROSSI" (asked: "...") - sent as last_name in lookup 1; ...               (chronological; newest 6)
 User corrections: "It's Chen, not Ken. C, H, E, N"; ...                      (newest 2; omitted if empty)
 Other identification lookups your tools offer: {tool}({required args}).      (2nd miss on; omitted if empty)
 Prefer what the user spelled over a word you heard. A later spelling replaces an earlier one only
 if the user said so; otherwise ask which is right. Before a lookup that combines values from
 different attempts, read the combination back and wait for the user to confirm it. Do not send
 values the user has not given.]
```

- **Facts only:** the code never says which field is wrong and never builds a candidate or a combination.
- **Retries:** `retry_guard` (`config/voice/realtime_eval.yaml:28-31`) still blocks identical retries.

### 4.3 Lifecycle

**Identification tools (review §0.3 #1).** A session tool qualifies only if **all** of these hold:
1. It is a read tool (`schema_rules.is_read_tool`).
2. **It returns a person:** the object of its name or description is a person entity (user, customer, member, client,
   profile, person, account holder). The object is the first noun after the verb token (`get`, `find`, `lookup`,
   `search`, `retrieve`, `fetch`), in the snake-case name or the first sentence of the description. This accepts
   "find user id by email" and "get customer by phone". It rejects "get bills for customer" and "get data usage",
   whose object is a bill or usage.
3. **It takes no other entity's ID:** no required argument that is an ID of a non-person entity (`*_id` where
   `schema_rules.is_person_argument` is false, e.g. `line_id`, `order_id`).
4. It has at least one required argument that is a personal detail (`name`, `email`, `phone`, `zip`, `postal`,
   `birth`, `dob`, by whole token in the argument name) or a person ID.

Further rules:
- **Override:** `result_hints.identification_tools: auto | [names]`, default `auto`. An explicit list is
  deployment-specific config, never shipped in code.
- The derived set is logged in `session_rules` as `identification_tools`.
- `scripts/f8_tool_check.py` prints the set for each τ³ domain for hand review before any run. The expected sets are
  recorded in the census report, not in code. A generic tool such as "get details by id" fails rule 2 and is
  excluded.

**Output classes** (pure function `classify_lookup_output`; logged in step 0):

| Class | Rule | Effect |
|---|---|---|
| `not_found` | matches `result_hints.failure_pattern`; or parses as JSON and is empty (`[]`, `{}`, `null`, `""`); or is empty text | miss: note appended |
| `invalid` | local invalid answer for a person argument (`ResultHints.on_local_invalid`) | miss: no note on that local answer; counted |
| `error` | starts with `Error` and is not `not_found` | neutral |
| `found` | Only after every rule above has failed to match (see the order below). The output is either: a JSON **object** with a **person identifier** key (a key that `schema_rules.is_person_argument` accepts, e.g. `user_id`, `customer_id`) whose value is a non-empty string or number; or a JSON **list with exactly one** such object; or the whole output is one whitespace-free token of at most 64 characters, from a tool whose name or description says it returns a person's ID. Seen in A7–A9: `yusuf_rossi_9620`, `{"customer_id": "C1001", …}`, `{"user_id": …}`. A bare `id`, a `request_id`, or any other non-person `*_id` never qualifies. | success: closes the episode |
| `ambiguous` | a JSON list with two or more records | neutral: does not close the episode, no note |
| `unknown` | anything else | neutral |
| `cancelled` | `relay.on_cancel` before an output | recorded, never counted |

**Order of rules:** the first match wins.
1. `cancelled`.
2. `invalid` (local).
3. `not_found`: the failure pattern, an empty JSON value, or empty text.
4. `error`: any other text starting with `Error`, or a JSON object with an `error` or `errors` key.
5. `ambiguous`: a list of two or more records.
6. `found`.
7. `unknown`.

Only `found` closes an episode, so an empty, ambiguous, erroneous or unexpected result can never clear the history.
Tests add: `{"user_id": null}` → `unknown`; `{"error": "...", "request_id": "r1"}` → `error`;
`{"id": "x"}` → `unknown`.

**Episodes (review §0.3 #3):**
- **Spellings before the first lookup:** spellings and corrections go into a *pending pool*. When an episode opens (its
  first attempt), it takes every pool item since the previous episode closed (or since the session started), and the
  pool empties. Items arriving while an episode is open belong to it.
- **Close:** on `found`. A closed episode's items are kept for logs and never shown again. New items go to the pending
  pool.
- **Person change before success:** not inferred, because there is no generic signal. Two safeguards instead:
  - every spelling shows the question it answered;
  - items older than the last `ledger.max_age_turns` (default 8) user turns are dropped from the note.
  A test documents this behaviour.
- **Late parallel results:** outputs are applied in arrival order. A failure's note is rendered at its own arrival. A
  result that arrives **after** its episode closed is logged to that episode and gets the existing I3/I4 note only, no
  ledger.
- **Retries:** `format_retry` calls are attempts with `origin=format_retry`. The original's note is rendered when the
  retry settles (`relay._settle_retry`, `tools/relay.py:405-428`).
- **I3/I4 counter:** with `ledger` on, `ResultHints` watches and resets only on identification tools and `found`.
  Today, any successful read resets it (`result_hints.py:94-96`). With `ledger` off, today's behaviour is unchanged.
- **`max_hints`:** the arm raises it from 3 to 6.

### 4.4 One prompt line for identifiers that contain a name (sub-arm)

**Feature:** `identity_from_identifier`, inside the `spelling_v2` block (`prompts.backend.yaml:27-32`). It renders in
**every** session, telecom included, so it is A/B-tested in all six cells (§5.3).

```
If the user gives an identifier that visibly contains a name (for example jane_doe_1234) and your
instructions require a lookup by name, read that name back to the user and, once they confirm it,
use it for the lookup.
```

### 4.5 Tests (`test_fdh_identity_fixes.py`)

**Identification tools:** invented schemas covering:
- "find user id by email(email)" → in;
- "get customer by phone(phone)" → in;
- "get bills for customer(customer_id)" → out (object is bills);
- "get data usage(customer_id, line_id)" → out (object is usage; another entity's ID);
- "get details by id(id)" → out;
- "get order details(order_id)" → out;
- "get user details(user_id)" → in;
- an explicit override list → used as given.

**Output classes:** each row of the §4.3 table, including `[]`, `{}`, `""`, a list of two customers (`ambiguous`), a
list of one customer (`found`), a bare ID string from an ID-returning tool (`found`), the same string from a tool that
does not return an ID (`unknown`), free text (`unknown`), and `Error: timeout` (`error`).

**Episodes:**
- A spelling before the first lookup joins episode 1.
- `found` closes it, and a later miss starts empty.
- `ambiguous` and `unknown` never close it.
- A non-identification read between misses changes nothing.
- A late failure after `found` gets no ledger.
- Items older than 8 user turns are dropped.
- **Documented behaviour:** a person change before success keeps the old items, labelled with their questions.

**Where a spelling was sent:**

| Spelled | Sent | Status |
|---|---|---|
| "CHEN" | "Hen" | not sent |
| "KOVACS" | `last_name="kovacs"` | sent as last_name |
| "JOHN.DOE" | `johndoe` | sent with different separators |
| "DAIKI" | inside a sent email | not sent |
| "ROSSI" | sent as `first_name` | sent as first_name (the field is shown, not judged) |

**Store:** with `field_provenance: false, ledger: true`, turns are collected and no A3 line appears.

**Off:** all F8 flags off → notes and prompts byte-identical to the goldens.

### 4.6 Offline gates

1. **Tool check:** `scripts/f8_tool_check.py`; a hand review of each domain's set must pass before the census.
2. **Census** (`scripts/f8_ledger_census.py`, on reconstructed data, §6):

   | Output | Detail |
   |---|---|
   | Exposure | misses in passing tasks |
   | Output classes | how many outputs fall into each class, per domain, and a hand check of every `ambiguous` and `unknown` |
   | Recoverable | per F8 failure: was the correct value transcribed (spelled or as a word)? `yes` / `no` / `unknown` |
   | Coverage | among `yes`: the correct value appears in the note as a spelling or attempt, or in a quoted correction, or the right alternative tool is listed |
   | **Pass** | coverage ≥ 50% of `yes`; 0 notes show an item from a closed episode; 0 `found` classifications on an empty or multi-record output |
3. **G2 replay (§7):**
   - **T:** the next step sends the correct value or asks to confirm it, in ≥ 50% of samples.
   - **No-harm:** post-lookup decisions in passing tasks keep the full tool-call sequence in ≥ 95% of samples.
   - **§4.4:** U decisions of passing sessions in all three domains are unchanged in ≥ 95% of samples.

---

## 5. Rollout and validation

### 5.1 Order

| Step | What | Gates before the live run |
|---|---|---|
| 0 | Instrumentation and goldens (§1) | unit tests; `golden_sha_check.py` |
| A | `liveness` (F1 + F2) | unit tests, §2.6 |
| B | `identity` (F8 ledger); `identity_name` sub-arm (§4.4) | unit tests, §4.6 |
| C | `situational` (F4) | unit tests, scope check, §3.5 |
| D | `combined` | its own full A/B |

### 5.2 Config files (new; existing defaults stay off)

| File | Settings |
|---|---|
| `config/profiles/realtime_eval_liveness.yaml` | `guards.{liveness_reply, question_never_silent, ping_as_status}: true`; `replay_unheard_answer: {enabled: true, ttl_s: 60, triggers: [backchannel, ping, repeat], whole_if_heard_below: 0.2}` |
| `config/voice/realtime_eval_identity.yaml` + `config/profiles/realtime_eval_identity.yaml` | `result_hints.{ledger: true, field_provenance: false, max_hints: 6, identification_tools: auto}` |
| `config/gateway.eval_identity_name.yaml` | `identity_from_identifier: true` |
| `config/gateway.eval_situational.yaml` | `situational_facts: true` |
| Only if §3.6 is needed: `config/profiles/realtime_eval_situational_f5.yaml` (voice: `tools.transfer_checkpoint: {enabled: true, max_per_session: 1}`) + `config/gateway.eval_situational.yaml` | F4 and F5 together |
| `config/profiles/realtime_eval_combined.yaml` + `config/gateway.eval_combined.yaml` | the arms that passed: **the single submission profile** for all domains |
| Control | `realtime_eval.yaml` + `gateway.eval.yaml` (`a30f6df59a8b`) |

### 5.3 Run design and budget

- **Fresh concurrent controls:** the same tasks, the same window, host and endpoints. Old runs are never controls.
- **Fixed simulator and judge:** within a comparison, the user simulator, judge, review model and τ³ commit are
  identical and recorded. gpt-5.2 and gpt-5.5 results are never pooled across simulators.
- **Paired trials:** the same task IDs, ≥ 3 trials per arm and control, concurrency 4. The 429 gate must pass.
- **Traces on everywhere** (`hermes.backend_trace: true`).

| Arm | Cells | Why |
|---|---|---|
| liveness | 6 | voice server, all domains |
| identity | 6 | any identification miss |
| situational (or situational_f5) | 6 | the rule renders in every domain (§0.4) |
| identity_name | 6 | the prompt line renders in every domain |
| combined | 6 | the submission profile; interaction check |

Each stage runs a **fresh control** on every cell it uses, at the same time as the arms.

**Stages (owner decision: targeted first, §0.4):**

| Stage | Arm cells | Control cells | Runs (× 3 trials) |
|---|---|---|---|
| 1. Targeted | liveness: telecom 5.5, airline 5.5. identity: retail 5.2, retail 5.5. situational: telecom 5.5. identity_name: retail 5.2. (6 cells) | telecom 5.5, airline 5.5, retail 5.2, retail 5.5 (4 cells) | 30 |
| 2. No-harm | the remaining cells of every arm that passed stage 1: at most 18 cells (liveness 4, identity 4, situational 5, identity_name 5) | all 6 | at most 72 |
| 3. Combined | the combined profile, 6 cells | all 6 | 36 |

**Stop rules:**
- **After stage 1,** an arm stops if any of its targeted cells fails a safety check (§5.4-2) or a mechanism check
  (§5.4-3), or if its per-cell point estimate is below −0.02. A stopped arm is bisected by its flags or dropped, and
  its stage-2 runs are skipped.
- **Stage 2** decides the regression checks (§5.4-1) over all six cells.
- **Stage 3** must pass every check on its own. It is the evidence for the submission.

**Budget:**
- At most **138 runs** if every arm passes every stage.
- Stage 1 alone is 30 runs.
- Each arm stopped after stage 1 saves 12 to 15 runs in stage 2.
- Extra trials (§5.4-1c) and bisections come on top.
- If F4 needs F5 (§3.6), `situational_f5` replaces `situational` in every stage at the same cost.

### 5.4 Prespecified acceptance (fixed before any run)

An arm ships only if **every** check below passes.

**1. Regression (no harm to Pass^1):**
**Definitions (fixed now):**
- **Task score.** For a task in a cell, *d* = (the task's mean reward over the arm's trials) − (its mean reward over
  the control's trials). Trial *i* of the arm and trial *i* of the control run in the same window, so trials are paired
  by window. The task mean averages over them. A trial that the 429 gate invalidates is removed together with its
  pair.
- **Cell difference.** The mean of *d* over the cell's tasks.
- **Pooled difference, per simulator.** The mean of the three cell differences, **each domain weighted equally**, so
  that retail and telecom (114 tasks each) do not outweigh airline (50).
- **Bootstrap.** 10,000 draws. In each draw, resample **tasks with replacement within each cell**, keeping each task's
  arm and control trials together (the task is the cluster). Recompute the cell and pooled differences. The one-sided
  90% lower bound is the 10th percentile.
- **Control noise.** From the same window: control trials 1 vs 2, 2 vs 3 and 1 vs 3. Compute the same *d* statistic
  (control *a* minus control *b*) and its bootstrap standard deviation. Take the mean over the three pairs; this is
  *σ₀* per cell, and the pooled *σ₀* is computed the same way.

**Criteria:**
- (a) **Pooled, primary.** For each simulator, the pooled lower bound is **≥ −0.02**.
- (b) **Per cell, guard.** Each cell's difference is **≥ −0.02**, and its lower bound is **≥ −0.05**.
- (c) **Extra trials, by one rule.**
  - After 3 trials, if 1.28 × pooled *σ₀* > 0.02, add 2 more paired trials (arm and control) for that arm and
    simulator, then decide. In that case the bound cannot reach −0.02 even when the arm changes nothing.
  - No other extension is allowed, and the decision is made before looking at the arm's difference.
- (d) **Flips.** Count tasks that pass in the majority of control trials and fail in the majority of arm trials, and
  the reverse. Pass→fail flips must not exceed fail→pass flips by more than the control-vs-control count. Every
  pass→fail task is diagnosed.

**2. Safety checks (mandatory):**

| Arm | Check | Must be |
|---|---|---|
| liveness | replays after a task turn or a newer run | 0 |
| liveness | R1 or R3 firings on a `content` turn (hand-checked sample of 50) | 0 |
| liveness | caller hang-ups during or right after a replay, vs control | not up |
| identity | notes showing an item from a closed episode | 0 |
| identity | `found` on an empty or multi-record output | 0 |
| identity | `too_many_errors` endings | ≤ control |
| identity | lookups per identification (median) | ≤ control + 1 |
| identity | wrong-person writes (a write's person ID differs from the task's expected identity) | 0 |
| situational | added questions per passing session (mean), per domain | telecom ≤ 1; airline and retail ≤ 0.2 |
| situational | transfers that the policy requires right away (control tasks whose first backend run transferred): arm transfers in its first run too | ≥ 95% |
| identity_name | U-type changes in passing sessions, from the A/B traces | within the §4.6 replay result |
| situational_f5 (if used) | extra backend steps between the first transfer attempt and the transfer, in passing tasks | ≤ 1; transfers made ≤ control |
| all | the deployed fingerprint matches the arm's config (`backend_features`, `features`) | identical in every session |

**3. Mechanism checks (mandatory; the arm must do what it is for):**

Each check is a **rate** with a named denominator, pooled over the stage's cells for that arm. A check whose
denominator is under 10 in both arm and control is reported as **not evaluable** and does not block. The arm
then relies on its regression and safety checks.

| Arm | Rate (numerator / denominator) | Must be | If the control rate is 0, or the rate is already at its limit |
|---|---|---|---|
| liveness | silent replies / `liveness` turns | arm ≤ 0.1 × control, or ≤ 2% | control 0 → arm ≤ 2% |
| liveness | backchannel or ping turns followed by silence within 2 turns / answers settled `partial` or `not_heard` while IDLE | arm ≤ 0.5 × control | control 0 → arm ≤ 2% |
| liveness | steers caused by a `liveness` turn / `liveness` turns in WORKING | arm ≤ 0.1 × control | control 0 → arm 0 |
| situational | sessions where the situation reached the backend (§3.1, trace method) / sessions whose policy has a situation-dependent branch and where the backend concluded or transferred (telecom) | arm ≥ control + 0.15 (absolute) | control ≥ 0.85 → arm ≥ control |
| identity | recovered misses / identification misses followed by at least one more attempt. A miss is *recovered* when the next attempt (1) sends a spelled value that was `not sent`, (2) uses a combination the user confirmed after a read-back, (3) uses an identification tool not used before, or (4) asks the user to confirm a value or offers another way, and the episode then ends `found`. | arm ≥ control + 0.10 (absolute) | control ≥ 0.90 → arm ≥ control |
| identity_name | callers identified / sessions where the user gave an identifier containing a name before any successful lookup | arm ≥ control | control 0 → reported only |

Absolute differences replace the earlier "1.5 × control" multipliers, which break when the control is near 0 or near
1.

**4. Decision:**
- A failed regression or safety check means the arm is bisected by its flags or dropped:
  - liveness: R1, R2, R3, then the triggers one at a time;
  - identity: ledger lines, then `max_hints`.
- A failed mechanism check means the arm is not shipped, even if Pass^1 rose.
- The combined arm must pass all checks on its own. If it fails, drop the most recently added arm and re-test.

### 5.5 Documentation (AGENTS.md rule)

- Update `misc/prototypes/frontend-delegation-hermes/runbook.md` and `analysis/README.md`.
- Add this file to the main report's "Files" table.
- Each code PR carries the Documentation Writer Review receipt.

---

## 6. Census data: sources, exact joins and reconstruction

**Join keys (verified on A9):**

| Join | Key | Cross-check |
|---|---|---|
| task ↔ session | `observations/data/tasks.json`: `key`, `sim_id`, `session` | the session appears in `agent/sessions.txt` |
| collated retail task ↔ source run | the collated run's `observations/scripts/merge_retail.py` mapping (71 tasks from `…_retail_regular_fixes`, 43 from `…_retail_regular_rerun43`) | the session is found in that source run's `events.jsonl`, and in no other |
| session ↔ backend trace | file `logs/fdh_workers/<session_id>.trace.jsonl`; its `session` record has `session_id` | (1) The first 16 hex digits of sha256(trace `ephemeral_system_prompt`) equal `backend_configured.backend_system_sha256` (verified equal). (2) The sorted tool names in trace `tools` equal the sorted `backend_configured.tools`. The two tool **hashes** are not compared: they hash different representations. (3) The trace's `ts` lies within the session's event time span. Any mismatch → `unknown`. New runs also compare `tools_canonical_sha256` (§1). |
| backend run ↔ trace requests | the trace `request`/`response` field `task_id` is `<session_id>:<epoch>`, and `backend_run_done` has `epoch`. The run's responses are those with that `task_id`, in `api_call_count` order; the last is the run's final response. This also covers runs with no tool calls. | `backend_run_done.tool_call_ids` has as many entries as the run's trace tool calls |
| trace tool call ↔ event call ID | Within one epoch, the n-th tool call in the trace responses (in `api_call_count` order, then position in `tool_calls`) ↔ the event call `fdh_<session>_<epoch>_<k>` that is n-th in `backend_run_done.tool_call_ids` | name equal, and the canonical JSON of the arguments equal to `tool_calls_out.arguments`; otherwise `unknown`. New runs log `backend_call_id` (§1), so the join becomes direct. |
| answer ↔ playback | In `transcript.settle` (`engine/transcript.py:142-166`) a delivery note gets a **new** `seq`, so the note's `seq` is not the answer's. The note is logged right after the answer's own `playback_outcome` in the same `_settle` call (`turn_manager.py:1217-1235`). So: the `playback_outcome` with `entry_kind=backend_answer` that immediately precedes a `delivery_note` in the session ↔ that note's `answer_id`. Heard answers have no note: link the n-th `backend_answer` playback ↔ the n-th `backend_answer` event, skipping answers already linked by a note. | `heard_chars ≤ chars`; any out-of-order link → `unknown`. New runs log `answer_id` on `playback_outcome` (§1). |
| tool call ↔ output | `tool_calls_out.call_ids` ↔ `tool_output_in.call_id` | — |

**Reconstruction:**

| Needed | How | If missing |
|---|---|---|
| Answer text | The trace's final response content for the run, cleaned with `clean_answers` (`engine/spoken_text.py`); its length must equal `answer_cleaned.chars_after` or `backend_answer.chars` | `unknown` |
| Heard text | The first `heard_chars` characters of the answer text | `unknown` if the answer is unknown |
| Spelled spans and their kind | Re-run the `124acf7` normaliser, with the run's own `agent/config/`, on `transcript_normalized.raw`; its output must reproduce the logged `text`. A turn with no event had no spans. | `unknown` if not reproduced |
| Original tool output | `tool_output_in.output` for the call ID, logged before annotation (`tools/relay.py:179`). Never derived by splitting the annotated text. | `unknown` if absent |
| What the backend saw | The trace's tool message whose `tool_call_id` is the `chatcmpl-tool-…` ID mapped from the event call ID (join above). It must start with the original output byte for byte; the remainder is the appended note. | `unknown` if the mapping or the prefix fails |
| A6 backend data | none (no traces) | every backend-side gate for A6 is `unknown` |

**Rules:**
- Every census table has an `unknown` column. Criteria are computed on known rows.
- If more than 10% of a gate's rows are `unknown`, the gate is **inconclusive**, not passed.

---

## 7. Replay tooling changes (`cli/backend_decision_replay.py`)

1. **Same render as production:** `render_arm_prompt` (`:190-197`) calls `render_backend_system` (§3.2) with the
   arm's prompt features.
   - **Test:** the control render reproduces the logged `backend_system_sha256` for every traced session (A4, A5,
     A7–A9).
2. **Full-sequence comparison:** U passes only if the complete ordered list of tool calls (name + canonical JSON
   arguments) matches the control's. A response with no tool call must stay without one.
3. **F8 extraction:** `extract --after-identification-miss` lists every response that directly follows a tool message
   whose `tool_call_id` belongs to a call classified `not_found` or `invalid` (§4.3, on the original output from §6).
4. **F4 extraction:** `extract --final-answers` also lists responses with no tool call.
5. **Patching by call ID:** `--patch-result <fdh call_id>=<note file>` maps the event call ID to the trace's
   `chatcmpl-tool-…` ID (§6), then rebuilds that tool message as:
   - the original output (from `tool_output_in`, §6);
   - then the original I3/I4 note: the trace's remainder after the original output, with the A3 line removed by its
     logged text (`field_provenance` event, `line`);
   - then the ledger note.
   It asserts that the original output is unchanged byte for byte. A call whose original cannot be joined is skipped
   and counted.
6. **F5 (only if §3.6 is needed):** `--append-checkpoint` takes a traced request whose response made the session's
   first hand-off call. Hand-off tools are derived from the trace's `tools` by the same function the relay uses. It
   then builds the next request exactly as Hermes would:
   - the original messages;
   - the assistant message from the response, unchanged, keeping the `tool_calls` entry and its ID;
   - one `tool` message with that `tool_call_id` and the checkpoint text as content. Any other tool calls in the same
     response keep their original results.
   It re-samples from there. The check: the next response calls a hand-off tool again, and nothing in between is a
   write.
7. **Stated limit:** one model step is re-sampled. Whole-call quantities (lookups per identification, questions per
   call, hang-ups) come only from the A/B.

---

## 8. What this plan deliberately does not do

| Not done | Reason |
|---|---|
| Change barge-in sensitivity | It changes protocol timing the τ³ client acts on. F1 recovers the loss afterwards. |
| Show the frontend its own silent turns, or stop merging user turns (`transcript.py:205-206`) | It changes every frontend request; F2 removes the harm deterministically |
| F3's prompt note | A separate fix |
| F5 by default | Only as F4's fallback (§3.6), because it touches every transferring call |
| Per-domain switches | τ³ takes one submission; fixes are limited by their triggers and wording instead (§0.4) |
| Assign spellings to fields, or build identity values or combinations in code | Unreliable (A3 showed this) and risky: `too_many_errors`, wrong-account writes. The ledger shows facts only. |
| Detect a person change automatically | No generic signal; the ledger shows context and ages items out instead |
| Recover letters ASR never produced | Not possible from text |
| τ³ wording in prompts or notes | The only τ³ facts are in the offline check scripts |

## 9. Decisions

All four are made; see §0.4. Open questions arising during implementation go into §0.4 with their date.

---

## 10. Implementation record (2026-10-09)

### 10.1 Built

| Plan item | Where | Tests |
|---|---|---|
| §1 step 0: real guard name; `trigger` on replay events; `answer_id` and heard text on `playback_outcome`; text on `backend_answer`; `kind` on spans; `tools_canonical_sha256` in `backend_configured` and the trace `session` record | `engine/turn_manager.py`, `worker/hermes_adapter.py`, `prompt_features.tools_canonical_sha256` | `JoinAndReplayTests`; checked live: the smoke session's voice log and trace carried the same value |
| §1 deployed goldens (eval profile at `124acf7`) | `tests/.../_fdh_golden_eval.py`, `fixtures/golden_eval_124acf7.json` (captured before any edit; the telecom backend prompt hashes to the logged `e69f6c719285ddae`) | `DeployedGoldenTests` |
| §2.2 classifier and lexicon | `frontend/turn_class.py`; `delegation.guards.lexicon` (English in the code defaults) | `TurnClassTests` |
| §2.3 F2 rules R1-R3 and the filler table | `frontend/decider.apply_guards`; `turn_manager._guard_filler`, `_dedupe_filler(never_empty=...)`; `prompts.yaml` `liveness_lines` | `GuardTests`, `LivenessTurnTests` |
| §2.4 F1 triggers, `whole_if_heard_below` | `turn_manager._replay_trigger`, `_replay`, `_replay_check`; `config.ReplaySettings` | `LivenessTurnTests`, `LivenessConfigTests` |
| §3 F4 block (every session), shared render | `prompts.backend.yaml` `<situational_facts>`; `prompt_features.render_backend_system` (gateway and G2 replay) | `SituationalFactsTests` |
| §4.2-4.3 F8 ledger, identification tools, output classes, episodes | `tools/identity_ledger.py`; `tools/relay.py`; `tools/result_hints.py` (reset only on an identification `found`); `server._ledger`; `prompts.voice.yaml` `lookup_ledger` | `IdentificationToolTests`, `OutputClassTests`, `LedgerTests`, `LedgerRelayTests`, `IdentityVoiceTests` |
| §4.4 name inside an identifier | `prompts.backend.yaml` (`identity_from_identifier`) | `IdentityProfileTests` |
| §5.2 arm configs | control `profiles/realtime_eval_124acf7.yaml` + `voice/realtime_eval_124acf7.yaml` + `gateway.eval_124acf7.yaml`; arms `profiles/realtime_eval_{liveness,identity}.yaml`, `voice/realtime_eval_identity.yaml`, `gateway.eval_{situational,identity_name}.yaml`; combined = the default `realtime_eval.yaml` + `gateway.eval.yaml` | profile inventory tests, `DefaultsTests` |
| §7.1-7.2 replay: same render as the gateway; full ordered call list for `U` | `cli/backend_decision_replay.py` (`Decision.calls`) | `SituationalFactsTests`, `JoinAndReplayTests` |

### 10.2 Deferred (needed before the gates that use them)

- **§6 census scripts:** `f12_trigger_census.py`, `f8_ledger_census.py`, `f8_tool_check.py`, `golden_sha_check.py`.
  The step-0 logging means new runs need no reconstruction; old runs still do (§6).
- **§7.3-7.6 replay extensions:** `extract --after-identification-miss`, `extract --final-answers`, `--patch-result`,
  `--append-checkpoint`.
- **§3.6 F5:** built only if F4 fails its §3.5 gate (owner decision, §0.4).
- **`backend_call_id`:** the Hermes tool handler (`worker/tool_futures.py:91`, `call(name, args, task_id)`) does not
  receive Hermes' tool-call ID, so logging it needs a `hermes-agent` change. The order-based join (§6) is the method.

### 10.3 Differences from the text

- **Ledger evidence:** the ledger collects its own turn evidence (`IdentityLedger.add_turn`, fed by the turn
  manager), so `FieldProvenance` is still created only for A3. The effect is the one §4.2 asks for: the identity arm
  turns A3's line off and keeps collection.
- **Language source:** the voice profile's ASR `language_code`. The Realtime wire layer does not parse a client
  transcription language today, so a client override is not read.
- **Ledger notes without an I3/I4 note:** an identification output classed `not_found` that the I3/I4 failure pattern
  does not match (for example `[]`) gets the ledger note alone. Ledger notes are capped at `max_hints` per episode.
- **Identification tools on the τ³ fixtures:** airline `get_user_details`; retail `find_user_id_by_email`,
  `find_user_id_by_name_zip`, `get_user_details`; telecom `get_customer_by_phone`, `get_customer_by_id`,
  `get_customer_by_name` (bill, usage and generic `get_details_by_id` lookups excluded). Pinned by
  `IdentificationToolTests`.

### 10.4 On by default (owner decision, 2026-10-09)

This replaces rule 1 of §0.1 ("off by default") for the shipped YAML, following the repository's practice since
`124acf7` (fixes on by default, with a control):
- **On** in `profiles/realtime_eval.yaml` (F1 + F2), `voice/realtime_eval.yaml` (F8 ledger, A3 off, `max_hints: 6`),
  `gateway.yaml` and `gateway.eval.yaml` (F4, F8 name line). The default pair is therefore stage 3's combined arm.
- **Control:** `realtime_eval_124acf7.yaml` with `gateway.eval_124acf7.yaml`, byte-identical to agent `124acf7` in
  its prompts and guarded decisions (`DeployedGoldenTests`). The arms extend it.
- **Off** in every older control (`realtime_eval_control.yaml`, `voice/realtime_eval_control.yaml`,
  `gateway.eval_control.yaml`, `gateway.baseline.yaml`, `gateway.identity_control.yaml`) and in the Python defaults.
- **Not yet evaluated:** §5 still applies before any number is reported. A fix that fails its arm is switched off in
  the default files.
