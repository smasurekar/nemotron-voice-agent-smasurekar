# Plan: conversation history for the backend (Frontend/Backend Agent, text and voice)

**Status:** implemented (§8 evaluation runs pending) · **Date:** 2026-09-26 · **Revision:** 4.
Implementation notes:
- §4.4: a discarded pending turn is closed only in **paired** mode with the backend history on;
  `backend_only` keeps its earlier behaviour (the turn is dropped).
- §4.1: the two earlier-turn headings ("Conversation since your last reply", "Conversation so far")
  are constants in `backend_context.py`; the rest of the request wording is the catalog template.
- The guidance's precedence rule and the interruption note sit in the context note/guidance as
  specified; the text catalog omits the voice-only "[interrupted by the user]" sentence.

Revision 2. Review points applied:
- ablation without the behavioural guidance (the context note is always on, revision 3);
- narrower prompt rules;
- a same-commit text baseline;
- the resolved-config test expects the derived `stateful`;
- the flag pinned off in `backend_only.yaml`;
- the effective `include` in `backend_context`;
- the 800 ms edit is not committed automatically.

Revision 3:
- the addendum is split into an always-on context note and optional behavioural guidance, and only the
  guidance is ablated;
- only a clear, complete, explicit correction overrides a verified value.
**Code:** `src/prototypes/text_frontend_backend_agent/` (the change) and
`src/prototypes/voice_frontend_backend_agent/` (voice wiring, history repair, eval profile)
**Motivation:** recommendation 7 (§10.2, "Give the backend the conversation history, or at least the raw
transcript of recent turns"), and the transcript half of §10.1 item 2, in
`voice-agent-evaluation-dump/tau-3-voice/2026-09-25_13-25-31Z_fba-voice/observations/2026-09-25_fba-voice-paired-airline-regular-sil800.md`
(§5.4 item 4, §7.4, §7.6, §10.1, §10.2; revision of 2026-09-26 11:48).
**Related:** [`text-frontend-backend-agent-prototype-plan.md`](text-frontend-backend-agent-prototype-plan.md),
[`voice-frontend-backend-agent-prototype-plan.md`](voice-frontend-backend-agent-prototype-plan.md),
[`voice-frontend-backend-agent-runbook.md`](voice-frontend-backend-agent-runbook.md), and the τ³ runbook
`tau2-bench-smasurekar/misc/prototypes/voice-frontend-backend-agent-tau3-runbook.md`.

This plan adds one configuration flag that gives the paired backend the conversation history. When the
flag is off, the agent behaves exactly as it does today, down to the bytes sent to the backend LLM.

---

## 0. Decisions to confirm

| # | Decision | Proposed choice | Why |
|---|---|---|---|
| H1 | Where the change lives | **Text prototype** (`agent.py`, `config.py`, a new `backend_context.py`, the prompt catalogs). The voice layer only wires it, repairs the extra history and adds a profile | The voice agent imports the text agent unchanged (V11). One implementation serves text τ² and voice τ³, so the text run can check the effect cheaply first (§8.1) |
| H2 | Flag shape | `backend.conversation_history.enabled` (bool, **default `false`**) plus `include: full \| backend_turns \| transcript` (**default `full`**) | The observation doc names two separate remedies: memory of the backend's own work, and the raw user transcript. `include` allows testing each on its own, without code changes |
| H3 | Relation to the existing `backend.stateful` | `stateful` stays a **derived** value, and now means "the backend history persists across turns". It is true for `backend_only`, and for paired mode with `include` of `full` or `backend_turns`. Setting `stateful: true` explicitly in paired mode is still an error, but the message now points to the new flag | Only one switch per behaviour. The existing `backend_only` path and its tests don't change |
| H4 | Frontend | **Unchanged.** It still writes a self-contained `query` every turn | This keeps the A/B comparison down to one variable. The frontend-prompt change in observation §10.1 item 2 ("the latest user turn overrides older values" must not replace a complete earlier ID) is a separate change (§10) |
| H5 | Prompting the backend about its history | Two catalog texts per `include` value, appended to the backend system prompt **only when the flag is on**. (a) A short **context note**, always on with history, that says what the earlier messages are. (b) Optional **behavioural guidance** on duplicate writes, retries and precedence (`guidance_key: auto`; `""` disables it) | The base prompt says "You receive one self-contained request at a time", which is false once history is on and could make the model ignore the history, so the context note is not optional. Only the guidance changes behaviour beyond that, so only the guidance is ablated |
| H6 | Eval profile | A new `profiles/tau3_eval_backend_history.yaml` that extends `tau3_eval.yaml` and sets only the flag. `tau3_eval.yaml` pins the flag **off** explicitly | The two arms then differ by one key, and existing runs stay reproducible |
| H7 | Prerequisite | The 800 ms edit to `profiles/tau3_eval.yaml` is **uncommitted**. Confirm who owns it and where its commit boundary is (its own commit, or part of this change) before building on it. Don't commit it as a side effect of this work | The new profile extends it, and the baseline arm should be a committed state rather than a diff in `provenance/`. How it gets committed is the owner's decision |

---

## 1. Problem, grounded in the 800 ms run

In paired mode, every delegation starts the backend with an empty `History()`
(`agent.py:_run_paired`, `History()` passed to `_drive`). The backend sees only the system prompt
and the frontend's `query` for this turn. After the turn, its tool calls, their results and its
reasoning context are thrown away. `session.backend_history` exists but is used only in `backend_only`.

What the 800 ms run shows (observation doc §5.4 item 4, §7.4, §7.6):

| Symptom | Example | Which part of the history fixes it |
|---|---|---|
| The same record is fetched again every turn | Task 33: `get_reservation_details` plus 2× `search_direct_flight` on every turn | The backend's own tool results |
| A write is repeated | Task 33: `update_reservation_flights` ran twice | The backend's own tool calls, and the rule "never repeat a completed write" |
| Part of a multi-part request is forgotten | Task 33: `update_reservation_baggages` was never called, so the DB didn't match | The backend's earlier requests, and the user's own words |
| A value found earlier is lost | Task 5: `3JA7XV` was found; the next FE query said "JA7X"; lookups repeated until the 10-error limit | The backend's own tool results, and preferring verified values over a new paraphrase |
| The authenticated user is lost between turns | §5.4 item 4 | The backend's own tool results |
| ID fragments aren't joined; the frontend garbles the ID; "latest wording wins" drops a correct ID | §13.2: `ARAV_MED_6699`, `Amelia_Fancy_4739`, stale `11177` | The user's verbatim words, which the backend then joins itself |
| Retries of IDs that already failed (54 calls) | §5.2 | The backend's own failed calls |

"Logged in, then agent errors" is the main cause of **7 of the 32 failures** (tasks 3, 5, 11, 12,
14, 29, 33). Only 4 of the 11 tasks that logged in passed. The text runs support the same reading:
paired scores 0.71, and backend-only (a stateful backend on the same models) scores 0.79.

Measured on the same run (`agent/events.jsonl`, 60 sessions), for sizing:
- Delegations per session: median 9, p90 20, max 39. The frontend answered directly only 19 times,
  against 685 delegations.
- Tool output per session: median 346 characters, p90 16.9 k, max 62 k (about 16 k tokens). With the
  existing `backend.history.max_groups: 40`, τ³ sessions are effectively never pruned.
- History repair ran 408 times and failed 56 times (52 of them on the `call_backend` tool result, "stored
  text does not match"). Most delegated answers are cut off, so the backend's copy has to be repaired
  as well (§4.3).

---

## 2. Requirements as testable rules

| # | Rule |
|---|---|
| B1 | With `enabled: false`, the messages sent to the backend LLM are **identical** to today's for any conversation: the same system prompt, and the same `[system, user(query), …]` per delegation. There is a regression test for this |
| B2 | `include: full`: the backend history persists across delegated turns in `session.backend_history`: the requests, tool-call batches, tool results and final answers. Each turn's request also carries the user's verbatim words since the backend last replied |
| B3 | `include: backend_turns`: as `full`, but the request is the frontend `query` only, as today |
| B4 | `include: transcript`: the backend stays stateless per delegation. Its single request carries the conversation transcript (user words and spoken replies, within the frontend history window) plus the query |
| B5 | Turns the frontend answered itself (direct, unsupported, or contract fallback) reach the backend **once**, in the next delegated request. In `transcript` mode they are part of the full transcript |
| B6 | The backend history keeps tool pairing valid at all times: pruning drops whole groups, and a discarded pending turn is closed with synthesized results (§4.4) |
| B7 | Voice: when a delegated answer is cut off, the backend's copy is rewritten to what the user heard, together with the frontend's copy. Either both are repaired or neither is (§4.3) |
| B8 | Voice: cancelling an in-flight turn (barge-in while thinking) leaves the backend history untouched. This already holds, because the runner swaps state only when a call returns |
| B9 | External tool execution (τ² text and τ³ voice) works unchanged: the suspended turn carries the full backend history in `PendingTurn.backend_history`, and `send_tool_results` resumes it |
| B10 | The flag and `include` are validated strictly. `"false"` from an environment variable means false. `enabled: true` together with `agent.mode: backend_only` is a `ConfigError` |
| B11 | Every delegated turn emits one `backend_context` event, with the flag on **or off**, so the eval scripts can confirm which arm ran and how large the context was. With the flag off, the event reports `enabled: false` and `include: "off"`, never the configured default `full` |

---

## 3. Configuration

### 3.1 Text `agent.yaml`

```yaml
backend:
  prompt_key: backend
  stateful: auto                # derived: true for backend_only, or paired with backend history
  conversation_history:
    enabled: ${FBA_BACKEND_HISTORY:-false}   # paired mode only: give the backend the conversation history
    include: full               # full | backend_turns | transcript  (see the plan, section 3.2)
    guidance_key: auto          # auto -> catalog key "backend_history_guidance_<include>"; "" -> no guidance
                                # (the context note "backend_history_context_<include>" is always added when enabled)
    request_key: backend_history_request     # catalog template for the per-turn backend request
  ...
  history:
    max_groups: 40              # used whenever the backend keeps history (backend_only, or paired full/backend_turns)
```

The environment default gives text runs and ad-hoc voice runs a one-variable switch. Eval profiles
don't rely on it; they pin the value (§3.3).

### 3.2 `include` values

| Value | Backend history across turns | Per-turn request | Tokens | Tests the hypothesis |
|---|---|---|---|---|
| `full` (default) | yes | the user's words since the last backend reply, plus the frontend query | highest | both remedies together (the recommended arm) |
| `backend_turns` | yes | the frontend query only (as today) | high | "memory of its own work" alone |
| `transcript` | no (fresh per delegation) | the transcript of the conversation so far, plus the frontend query | low | "at least the raw user transcript" alone |

### 3.3 Voice

- `voice_agent.yaml`: no new key. The flag reaches the text agent through the existing deep merge of
  `agent.overrides` (`agent.overrides.backend.conversation_history`), so it inherits the environment
  default from `agent.yaml`.
- `profiles/tau3_eval.yaml`: pin `agent.overrides.backend.conversation_history.enabled: false`. The profile's
  header comment says it pins every eval-relevant key.
- New `profiles/tau3_eval_backend_history.yaml`:

  ```yaml
  # tau3 eval with the backend history on: identical to tau3_eval.yaml except for the flag.
  extends: tau3_eval.yaml
  agent:
    overrides:
      backend:
        conversation_history: {enabled: true, include: full}
  ```

  Profile chains already resolve (`config.py:_load_chain`), and `agent.overrides` is a free-form mapping
  that is deep-merged.
- `profiles/backend_only.yaml`: pin `agent.overrides.backend.conversation_history.enabled: false`. The
  profile extends `voice_agent.yaml`, so without the pin it inherits the `FBA_BACKEND_HISTORY` default
  from `agent.yaml`. With `FBA_BACKEND_HISTORY=true` in the environment, that would make the
  backend-only profile fail validation (`enabled` is a `ConfigError` in `backend_only`, §3.4).
  Backend-only is already stateful, so the pin changes nothing else.

### 3.4 Validation (`config.py`)

- New frozen dataclass `ConversationHistoryConfig(enabled: bool = False, include: str = "full",
  guidance_key: str = "auto", request_key: str = "backend_history_request")`, stored on
  `BackendConfig.conversation_history`.
- New helper `_bool(value, name)`: accepts `bool`, and the strings `true/false/1/0/yes/no/on/off` (any
  case, surrounding whitespace ignored). Anything else raises `ConfigError`. It is used for the new
  flag. Existing `bool(...)` call sites stay as they are, to keep this change narrow; the gap is noted
  in §10.
- `include` goes through `_one_of(..., ("full", "backend_turns", "transcript"))`.
- `resolve_runtime_modes(mode, stateful_raw, history)` becomes:
  - `backend_only` and `history.enabled` → `ConfigError("backend.conversation_history is for
    agent.mode: frontend_backend; backend_only already keeps the full backend history")`;
  - `stateful: auto` → `backend_only or (history.enabled and include != "transcript")`;
  - explicit `stateful: true` in paired mode → `ConfigError`, the message naming
    `backend.conversation_history.enabled`;
  - an explicit `stateful` that contradicts the derived value → `ConfigError`, as today.

---

## 4. Design

### 4.1 What the backend sees

`full`, turn *n*. The system prompt is followed by the backend history of turns 1…n−1, which is kept
as it was, and then one new user message:

```
system     backend prompt + policy [+ cascade addendum] + history context note [+ history guidance] (backend_history_*_full)
user       <turn 1 request>
assistant  tool_calls [get_user_details(...)]
tool       {...}
assistant  <turn 1 answer, as heard>
...
user       <turn n request>           <- rendered from backend_history_request
```

The turn *n* request is rendered from the catalog template `backend_history_request`:

```
Conversation since your last reply (speech-recognition transcript, oldest first):
User: <an utterance the frontend answered itself>
Assistant: <the frontend's reply>

The user's latest words (verbatim):
<user_message>

Request from the frontend:
<query>
```

- The first section is left out when there are no such turns, which is the usual case: 19 direct
  answers against 685 delegations.
- On the first delegation of a session, it holds the seeded greeting ("Hi! How can I help you today?").
- The voice catalog labels the words as a speech-recognition transcript; the text catalog calls them
  "the user's message".
- Placeholders: `{earlier_turns}`, `{user_message}`, `{query}`. Substitution is literal, like `render()`,
  because the prompts contain braces.

`backend_turns`: the new user message is the `query`, exactly as today.

`transcript`: no backend history. The single user message uses the same template, with
`{earlier_turns}` holding every user/assistant text pair in `frontend_history`. That window is bounded
by `frontend.history.max_groups` (20). The heading becomes "Conversation so far".

The source of the earlier turns is **`session.frontend_history`**. It already holds every exchange that
crossed the agent boundary, including the seeded greeting and interrupted answers repaired to what was
heard. The new module walks it backwards to the last group that contains a `call_backend` call:
- keep only user texts and final assistant texts;
- skip `call_backend` tool-call messages and their tool results.

This adds **no new session field**, so `SessionState.to_dict/from_dict` and replay are unchanged.

### 4.2 New module and changes in `agent.py`

New `text_frontend_backend_agent/backend_context.py`. It contains pure functions over immutable types
and has no I/O:

```python
def undelegated_turns(frontend_history: History) -> tuple[Message, ...]
def conversation_turns(frontend_history: History) -> tuple[Message, ...]
def render_request(template: str, *, query: str, user_message: str, turns: Sequence[Message],
                   heading: str) -> str
def backend_system_prompt(catalog: PromptCatalog, config: Config) -> str   # base render + context note + guidance
```

`backend_system_prompt` replaces the inline `render(catalog.get(config.backend.prompt_key), config)` in
`assemble_agent`. `TextAgentRunner.rendered_prompts()` in the voice layer uses the same function, so the
prompts it logs and tests are the prompts that are sent.

`FrontendBackendAgent` changes:

| Where | Change |
|---|---|
| `__init__` | Hold the request template (loaded once from the catalog when the flag is on) |
| `_run_paired` | Build the first input from §4.1. Pass `session.backend_history` instead of `History()` when `config.backend.stateful`. Emit `backend_context` (B11) |
| `_finish_backend` | Branch on `config.backend.stateful`, not on `frontend_enabled`. When stateful, store `backend_history.prune(backend.history.max_groups)` on the session. In paired mode, still close the frontend group exactly as today |
| `_resolve_pending_before_user_message` | When stateful and a pending turn is discarded, close it (§4.4) instead of dropping it |
| `send_tool_results` | No change. `pending.backend_history` already carries the full history (B9) |

`backend_only` stays on its current path (`_run_backend_only`) and its behaviour is unchanged.

### 4.3 Voice: repairing interrupted answers

In paired `full` or `backend_turns` mode, a delegated answer is stored in three places:
- the frontend's `call_backend` tool result;
- the frontend's assistant continuation;
- the **backend's final assistant message**.

The 800 ms run repaired 408 answers, so an unrepaired backend copy would make the backend believe the
user heard answers that were cut off.

`history_repair.repair_interrupted_answer(...)` gets a `backend_history: bool` parameter (the runner
passes `config.backend.stateful and frontend_enabled`):

1. Compute the repaired frontend history as today.
2. If the last frontend group is the delegated shape (4 messages) **and** `backend_history`, also
   compute `_repair_backend(state.backend_history, …)`. That function already exists and checks
   exact text equality.
3. Replace both histories only if both repairs succeeded. Otherwise raise `HistoryRepairError`, and
   neither history changes. The turn manager already logs `history_repair_failed` and carries on.
4. A direct-answer group never touches the backend history: the backend's last message belongs to an
   earlier turn.

The 56 existing repair failures (the frontend text doesn't match) are a separate, existing issue. The
"all or nothing" rule keeps the two histories consistent when repair fails. Investigating those failures
is listed in §10.

`seed_assistant` (the greeting) stays in the frontend history. The backend sees it through the
earlier-turns section on the first delegation.

### 4.4 Discarded pending turns

`on_user_message_while_pending: discard_pending` drops a suspended turn. In voice this almost never
happens: once tool calls are out, user speech is queued rather than cancelling the turn
(`turn_manager.on_speech_started`), and a missing result becomes a synthesized error after
`result_timeout_s`. In text τ² it can happen.

When the backend keeps history, the caller may already have run the discarded calls, including writes.
Dropping them would bring back the repeated-write failure. So when stateful, the agent closes the group:
- one synthesized tool result per outstanding id: `"No result: the user spoke before this call
  finished; its outcome is unknown."`;
- then an assistant message: `"[This turn was interrupted before it finished.]"`.

The group then passes `validate_tool_pairing`. The existing `pending_discarded` event gets
`"closed_in_backend_history": true`.

### 4.5 Prompts

Add to **both** `text_frontend_backend_agent/config/prompts.yaml` and
`voice_frontend_backend_agent/config/prompts.voice.yaml`. The text is domain-neutral, like the rest of
the catalogs.

- `backend_history_request`: the template in §4.1. The voice variant says "speech-recognition transcript".
Two layers per `include` value:

**Context note** `backend_history_context_<include>`. It is always appended when the flag is on and
can't be switched off. It states facts and sets no behaviour rules. It overrides the base prompt's "one
self-contained request at a time". For `full`:
  > Conversation so far. This is an ongoing conversation, not a single request: the messages above are
  > the earlier requests, with your tool calls, their results and your replies. Each new request shows
  > the user's own words and a summary written by the assistant that talks to the user. Take the earlier
  > messages into account. A reply that ends in "[interrupted by the user]" was cut off; the user heard
  > only the part before it.

- `backend_turns`: the same note without the sentence about the user's words.
- `transcript`: "The request includes the conversation so far …", with no tool results mentioned.

**Behavioural guidance** `backend_history_guidance_<include>`. It is optional (`guidance_key`) and is
the part that is ablated. For `full`:
  > - Use results you already have. Look a record up again only if something may have changed it since.
  > - Before any action that changes data, check the history above. Don't repeat an action that already
  >   succeeded unless the user explicitly asks for it again in a later turn. A restated or summarised
  >   request is not such an ask.
  > - Keep track of every part of what the user asked for, across turns. Don't report a request as done
  >   until every part is done.
  > - Identifiers, names and numbers: a value a tool confirmed earlier takes precedence over the summary,
  >   and over anything the user says later, **unless** the user makes a clear, complete and explicit
  >   correction (for example "no, my ID is …" followed by the whole value). Other kinds of later input
  >   don't replace a verified value:
  >   - a fragment or part of an identifier;
  >   - a repetition that differs only in a few letters;
  >   - a hesitant or uncertain restatement;
  >   - a speech-recognition variant (a homophone, or a commonly confused letter).
  >
  >   When such input conflicts with a verified value, keep the verified value and ask the user to
  >   clarify. Before any action that changes data, look up or confirm an accepted correction. Join an
  >   identifier the user spelled across several turns before comparing it.
  > - If a tool call failed because the identifier or input was invalid or not found, don't call it again
  >   with the same value. Ask the user to confirm or correct it instead. A call that failed for another
  >   reason (a timeout, a service error, "try again") may be retried.
- `backend_turns`: the same guidance, without the parts about the user's own words.
- `transcript`: only the precedence and spelled-identifier rules. There are no tool results, so
  "verified" means a value the conversation shows was confirmed.

The retry and duplicate-write bullets overlap observation §10.1 item 3 ("Stop the retry loop"). They
are included because they only work once the backend can see its own calls. The guidance can change
behaviour on its own, so §8 runs history with and without it (`guidance_key: ""`). Both arms keep the
context note. The ablation therefore compares "history, described correctly" with "history plus rules",
and never a history that contradicts the prompt.

The rules are deliberately narrow:
- A repeat is allowed when the user explicitly asks again.
- Retries are suppressed only after deterministic invalid-input or not-found errors, not after
  transient failures.
- Only a clear, complete, explicit correction replaces a verified value. Fragments, near-repeats and
  recognition variants lead to a clarifying question instead. This is the failure mode of task 26
  (`Amelia_Sanchez` → `Amelia_Fancy`), which a looser rule would reproduce.

The tests check that each rule's wording is in the rendered prompt. Whether the model follows them is
measured in §8, not unit-tested.

### 4.6 Events and logging

- New internal event `backend_context` (`events.py`), emitted once per delegated turn, before the first
  backend step:
  `{"enabled", "include", "guidance", "history_groups", "history_messages", "earlier_turns", "request_chars"}`.
  - `include` is the **effective** value: `"off"` when `enabled` is false. It is never the configured
    default (`full`) of a disabled flag.
  - `guidance` is the resolved guidance key, or `""` when the guidance is off. This lets the §8 arms
    that differ only by the guidance be told apart. The context note isn't reported separately: it is
    on exactly when `enabled` is.
  - The event carries no content, so it is safe under `redact_content`.
- Voice `session_start`: add `"backend_history": "off" | "<include>"` and `"backend_history_guidance": "<key>" | ""`, so
  `fba_voice_metrics.py` and `attribute_failures.py` can split arms without parsing config.
- `agent_turn_done` is unchanged. The per-role `backend.prompt_tokens` and `cached_tokens` already show
  the cost of the extra context.

---

## 5. Files

| File | Change |
|---|---|
| `text_frontend_backend_agent/config.py` | `ConversationHistoryConfig`, `_bool`, extended `resolve_runtime_modes` |
| `text_frontend_backend_agent/backend_context.py` | **New**: §4.2 functions |
| `text_frontend_backend_agent/agent.py` | §4.2 table, §4.4 |
| `text_frontend_backend_agent/events.py` | `BACKEND_CONTEXT = "backend_context"` |
| `text_frontend_backend_agent/config/agent.yaml` | the `conversation_history` block (§3.1) |
| `text_frontend_backend_agent/config/prompts.yaml` | 7 new keys: the request template, plus a context note and a guidance text for each `include` value (§4.5) |
| `text_frontend_backend_agent/config/examples/backend_history.yaml` | **New** example, in the same form as `backend_only.yaml` |
| `voice_frontend_backend_agent/agent/history_repair.py` | atomic dual repair (§4.3) |
| `voice_frontend_backend_agent/agent/runner.py` | pass `backend_history` to repair; `rendered_prompts` through `backend_system_prompt` |
| `voice_frontend_backend_agent/config/prompts.voice.yaml` | 7 new keys: the request template, plus a context note and a guidance text for each `include` value (§4.5) |
| `voice_frontend_backend_agent/config/profiles/tau3_eval.yaml` | pin the flag off (the 800 ms edit's commit boundary is settled first, H7) |
| `voice_frontend_backend_agent/config/profiles/backend_only.yaml` | pin the flag off (§3.3) |
| `voice_frontend_backend_agent/config/profiles/tau3_eval_backend_history.yaml` | **New** (§3.3) |
| `voice_frontend_backend_agent/config/profiles/tau3_eval_backend_history_noguide.yaml` | **New**: the §8 `paired-hist-noguide` arm (`guidance_key: ""`) |
| `voice_frontend_backend_agent/engine/session.py` (or wherever `session_start` is written) | the `backend_history` field (§4.6) |
| READMEs, plans and runbooks | §7 |

`frontend.py`, `delegation.py`, `backend.py`, `session.py`, `history.py`, the turn manager, the wire
layer and the speech code don't change.

---

## 6. Tests (unit, no network; existing fakes in `tests/unit/prototypes/_fakes.py` and `voice/_voice_fakes.py`)

Text (`tests/unit/prototypes/`):

| Test | Checks |
|---|---|
| `test_config.py` (extend) | the default is off; `"false"`/`"true"` strings from env; a bad `include` is rejected; `backend_only` plus `enabled` is an error; explicit `stateful: true` in paired mode names the new flag; `stateful` is derived for each `include` |
| `test_backend_history.py` (**new**) | B1: with the flag off, the recorded backend requests are byte-identical to a golden list for a 3-turn script. B2: in `full`, the second delegation's backend call contains turn 1's request, tool call, result and final. B3 and B4 message shapes. B5: a direct-answer turn appears once, in the next request, and not again. The greeting appears on the first delegation. Pruning at `max_groups` keeps pairing valid (`validate_tool_pairing`). The context note is present whenever the flag is on (including with `guidance_key: ""`) and never when it is off. The guidance is present only when on and not disabled. `backend_context` reports `enabled: false, include: "off", guidance: ""` when the flag is off, and the effective values when it is on |
| `test_external_tools.py` (extend) | B9: suspend and resume across two delegated turns with history on; `PendingTurn.backend_history` includes turn 1 |
| `test_tool_protocol.py` (extend) | §4.4: `discard_pending` with history on closes the group with synthesized results; pairing stays valid; the next turn sees it |
| `test_session.py` (extend) | `to_dict`/`from_dict` round-trips a paired session with a backend history |
| `test_state_replay.py` (extend) | `from_message_history` in paired mode leaves the backend history empty, as documented and lossy; with `transcript`, the first request still carries the replayed turns |

Voice (`tests/unit/prototypes/voice/`):

| Test | Checks |
|---|---|
| `test_voice_history_repair.py` (extend) | B7: an interrupted delegated answer is repaired in both histories. A direct-answer interruption leaves the backend untouched. A backend text mismatch raises and leaves **both** histories unchanged |
| `test_voice_modes.py` (extend) | paired with `full` through `TextAgentRunner`: the third backend call contains utterance 1 (like the existing backend-only test) |
| `test_voice_barge_in.py` (extend) | B8: `cancel_and_merge` before tools are out leaves `state.backend_history` unchanged; the merged text is the next request's "latest words" |
| `test_voice_config.py` (extend) | Compare the resolved configs of `tau3_eval_backend_history.yaml` and `tau3_eval.yaml`. The only **raw** difference is `agent.overrides.backend.conversation_history`. In the resolved text-agent `Config`, the expected differences are exactly `backend.conversation_history` and the derived `backend.stateful` (`false` → `true` for `full`). Any other difference fails. `backend_only.yaml` resolves with the flag off even when `FBA_BACKEND_HISTORY=true` |
| `test_voice_instructions.py` (extend) | `rendered_prompts()["backend"]` ends with the context note (and the guidance, unless disabled) after the cascade addendum, only when on |

Validation commands (AGENTS.md): `uvx ruff@0.15.6 check .`, `uvx ruff@0.15.6 format --check .`,
`uv run pytest tests/ -v`.

---

## 7. Documentation

User-visible surfaces: configuration (a new key), a new profile, the prompt catalogs, and event-log
fields. Per AGENTS.md, a documentation subagent runs in parallel with the implementation (reading
`docs/AGENTS.md`), and the PR carries the `## Documentation Writer Review` receipt.

- `text_frontend_backend_agent/README.md`: add a row to the "switches that change behaviour most" table,
  and fix the `agent.mode` row wording ("stateful backend" is no longer unique to `backend_only`).
- `voice_frontend_backend_agent/README.md`: the flag, the new profile, the `backend_context` event and the
  `session_start.backend_history` field in the event-log row.
- `text-frontend-backend-agent-prototype-plan.md` and `voice-frontend-backend-agent-prototype-plan.md`:
  a revision-log entry pointing here. Update the "paired backend is stateless by design" statements to
  "by default".
- `voice-frontend-backend-agent-runbook.md`: how to start the history arm.
- τ³ runbook (tau2-bench repo, separate change): add a `paired-hist` arm row (a container `fba-voice-hist`,
  a free port such as 8768, `profiles/tau3_eval_backend_history.yaml`, its own event-log paths). The
  τ² text integration plan's §1 table says "Paired mode: backend is stateless per delegation"; add
  "unless `backend.conversation_history.enabled`".

---

## 8. Evaluation plan

### 8.1 Text τ² first (cheap: typed user, no ASR)

All arms run on the **same commit**: paired airline `base`, 4 trials each.

| Arm | Config | Purpose |
|---|---|---|
| `paired` | `agent.yaml`, flag off | baseline, rerun on this commit |
| `paired-hist-noguide` | `backend_history.yaml` with `guidance_key: ""` | the history with the context note only |
| `paired-hist` | `backend_history.yaml` (`full`, guidance on) | history plus the guidance |

The historical `fba_paired_airline_base_4trials` (0.71) and `fba_backend_only_airline_base_4trials`
(0.79) are context only. The flag-off rerun is the reference, so that other code and prompt changes
since those runs don't count as history's effect. Rerunning backend-only on this commit is optional; do
it if the paired baseline moved.

Hypothesis: `paired-hist` closes most of the paired vs backend-only gap. The difference between
`paired-hist-noguide` and `paired-hist` is the guidance's own share. If neither arm moves, investigate
before spending a 6-hour voice run.

### 8.2 Voice τ³

Same commit, airline `regular`, 800 ms profile, concurrency 1:

| Arm | Profile | Purpose |
|---|---|---|
| `paired` | `tau3_eval.yaml` | baseline on the new commit (flag pinned off) |
| `paired-hist` | `tau3_eval_backend_history.yaml` | the change (history plus the guidance) |
| `paired-hist-noguide` | the same with `guidance_key: ""` | the history with the context note only. Include it at least when `paired-hist` moves the retry or duplicate-write counts, since the guidance targets those directly |
| optional `paired-transcript` | the same with `include: transcript` | the cheap variant, if `full` costs too much latency |

Run **at least 2–3 trials per arm**. The 6-gained / 6-lost swap between the 500 ms and 800 ms runs
shows that one trial can't resolve a 2–3 task difference (observation doc §6, §10.3 item 9).

Metrics, with the 800 ms values as the reference:

| Metric | 800 ms baseline | Expected with history |
|---|---|---|
| Pass^1 | 0.36 | up (bounded: most failures are at login; see below) |
| Main cause "logged in, then agent errors" | 7 tasks | down |
| Logged in → pass | 4 / 11 | up |
| Repeated identical successful writes per session | ≥1 (task 33) | 0 |
| Repeated identical reads per session (the same tool and arguments, both OK) | measure | down |
| Retries of known-failed IDs | 54 calls | down |
| Write tasks passed | 1 / 27 | up |
| Backend prompt tokens per task (cached) | 123.5 k (measure cached) | up, mostly cached |
| Backend turn latency, mean (p90) | 4.37 s (9.35) | watch: an increase above about 20% needs a look at `transcript` mode or pruning |
| L_R response latency | 4.06 s | watch |

Add two small counters to `attribute_failures.py` / `compare_runs.py` (tau2 repo): repeated identical
successful calls, and repeated writes. Split the results by `session_start.backend_history`.

### 8.3 Interaction with the lower-case ID fix

The login fixes in observation §10.1 (lower-case and canonicalise the ID: +19 and +5 tasks with a correct ID) is independent of this change, and would hide its effect
if it landed in the same comparison. Either:
- measure this change first, then the ID fix on top; or
- if the ID fix lands first, rerun the baseline arm on that commit.

Don't compare across commits that differ in both. The login barrier caps what history alone can show:
it acts mostly on the 11 logged-in tasks, plus the "fragments/frontend" tasks where joining the user's
words helps. Observation §10.1 credits fix 3 (spelled IDs assembled from the raw ASR, with the raw
transcript sent to the backend) with a correct ID in 7 more tasks. The `full` arm tests only the
backend half of that fix, so count separately how many of those 7 tasks reach a correct lookup.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Backend context grows with tool results (p90 17 k characters, max 62 k per session), so prompt tokens and latency rise | Append-only history is prefix-cache friendly (history repair rewrites only the tail). `max_groups` bounds it. `transcript` mode is the cheap fallback. §8.2 watches latency |
| The backend trusts a stale record after a write changed it | The guidance rule "look a record up again only if something may have changed it". Update tools return the new record anyway |
| The backend treats a failed identifier from history as correct | The guidance rule "don't call again with a value that failed as invalid or not found; ask the user". Tool errors stay in the history |
| The frontend query and the user's words disagree, or a noisy re-spelling looks like a correction | The precedence rule (§4.5): only a clear, complete, explicit correction replaces a verified value; anything else leads to a clarifying question. Both texts appear in the request |
| The repaired frontend and backend copies diverge | The atomic repair (§4.3) and its test |
| A write is lost from history on a discarded pending turn | §4.4 closes the group |
| The eval arms differ by more than the flag (and the derived `stateful`) | H6/H7 and a resolved-config test (§6) |
| A voice turn cancelled after tools went out | Not possible: `while_thinking` never cancels after tools are out, so the backend history can't be half-committed from the voice path |

---

## 10. Out of scope, and follow-ups

- The login fixes in observation §10.1: lower-casing, canonicalising and format-checking the ID in the
  backend tool adapter (item 1), joining spelled IDs across ASR results before the frontend (item 2,
  first bullet), read-back (item 3), spelling vs spoken name (item 4), ASR (item 5). Separate changes.
  This plan delivers only the "raw user transcript to the backend" part of item 2.
- The frontend-prompt change in observation §10.1 item 2: "the latest user turn overrides older values"
  must not let a partial or worse re-spelling replace a complete earlier ID (task 26).
- Relaxing the frontend's "never send a delta" rule once the backend has history (H4). A later ablation.
- Eliding older tool results (keeping only the last *k* groups' results verbatim) if §8.2 shows token
  or latency pressure.
- A strict boolean parse for every existing `bool(...)` config site (today `"false"` from the environment
  is `True`).
- The 56 `history_repair_failed` events at 800 ms ("call_backend tool result: stored text does not match").
  They already occur with the flag off and deserve their own look.

---

## 11. Implementation order

1. Settle the uncommitted 800 ms `tau3_eval.yaml` edit with its owner: who commits it, and in which commit (H7).
2. Config and validation, and `test_config.py` (§3.4).
3. `backend_context.py`, the `agent.py` changes, the prompt keys, and the text tests (§4.1–4.5, §6 text).
4. Voice: history repair, runner, profiles, `session_start` field, and the voice tests (§4.3, §3.3, §4.6).
5. Docs (§7), in parallel from step 3, then the PR with the Documentation Writer Review receipt.
6. Text τ² run (§8.1), then the voice τ³ arms (§8.2). Write up the results in the evaluation dump's
   `observations/`.
