# Plan: identifier normalization for ASR transcripts and tool arguments (voice Frontend/Backend Agent)

**Status:** implemented; offline replay done (§9.1), τ³ runs pending (§9.2) · **Date:** 2026-09-26 · **Revision:** 3

Revision 3. Implementation notes (where the code refines §4):
- §4.2 step 2: after a whole word, only single letters join it on the left ("A a rav" → `aarav`); after
  a short token, any short tokens do ("J am es" → `james`). A 2+-letter stop word ends the part.
- §4.2 step 1: `max_part_tokens` counts tokens other than single letters, so a spelled name of any
  length stays one part (`G O N Z A L E Z`). Above the limit, the two anchors form separate spans.
- Dotted letters (`S.A.N.`, `A.H.`) are one spelled token without the dots, and never a filler.
- `i` and `am` are not `en` stop words: "I" is a spelled letter, and the ASR splits "James" as
  "J am es". No transcript in the two recorded runs has "I am" before an ID.
- Punctuation outside a span is kept byte for byte (`_9957 .`, not `_9957.`).
- The §9.1 scorer is `id_error_analysis/replay_score.py` next to the observation doc.

Revision 2. Review points applied:
- the new event fields are redacted by `logging.redact_content` (§4.5);
- the tool-argument hook requires `tools.source: client` (§5);
- only permanent failures (not found, invalid) feed the retry guard, never transient errors (§4.3);
- after `max_local_rounds` the canonical arguments go out, and only local interception is bypassed (§4.3);
- the English word lists are an explicit `en` ruleset (§4.1);
- a fragment is answered with a request for the complete ID, not for a "missing part" (§4.3);
- this arm does not claim fix 3 (§1).
**Code:** `src/prototypes/voice_frontend_backend_agent/` (new `normalization/` package, runner wiring,
config, prompts, eval profile)
**Motivation:** §10.1 "User-ID login" and §13 in
`voice-agent-evaluation-dump/tau-3-voice/2026-09-25_13-25-31Z_fba-voice/observations/2026-09-25_fba-voice-paired-airline-regular-sil800.md`
(evidence scripts: `id_error_analysis/` next to it).
**Related:** `frontend-backend-agent-backend-history-plan.md` (backend conversation history). The two
features are independent flags and are evaluated separately first (§9).

## 0. Decisions

Agreed in discussion (2026-09-26):

| # | Decision |
|---|---|
| N1 | One `normalization/` package with pure, shared rules and **two hooks**: the ASR transcript before the agent, and the backend's tool arguments before they go to the client. The tool-argument hook is needed because the biggest failure (right characters, wrong case) is introduced by the frontend, after a correct ASR result (task 24). |
| N2 | Name: `normalization/`. The transcript side is `TranscriptNormalizer` (inverse text normalization, spoken → written). The existing TTS `TextNormalizer` can move into the same package later (`speech_text.py`); not in this change. |
| N3 | The wire keeps the **raw** ASR text (`conversation.item.input_audio_transcription.completed` is unchanged). Only the agent sees the normalized text. Both are logged. |
| N4 | A tool call whose argument fails its format check, or repeats an already-failed call, is **answered locally** with an instructive tool result and never sent to the client. This is the agent deciding not to call; the benchmark is unchanged. |
| N5 | All code is generic. Everything specific to the τ³ airline domain (tool, argument, ID pattern, case, failure prefix) is **config**, set only in a new eval profile. Defaults are off. |
| N6 | Joining an ID across **separate** user turns (holding a turn while an ID is incomplete) is phase 2 (§11). Within one agent turn (one utterance, or utterances already merged by `cancel_and_merge` / `response.create`), joining is in scope. |
| N7 | Filtering the harness's vocal tics ("ahem… hkh" → "H K K") is out of scope: it is a harness artifact. |
| N8 | Spelled letters conflicting with a name said as a word (fix 4, +2 tasks) is prompt-only and is deferred (§11). |

Open, with a proposed default:
- **D1. Retry-guard scope:** only tools that have an argument rule (`scope: rules`, proposed), or every
  tool (`all`). `rules` keeps the arm's effect attributable to the ID fixes.

## 1. Problem, grounded in the 800 ms run

Per task (§13.1), among the 39 tasks that never logged in:

| Category | Tasks | What goes wrong | What fixes it here |
|---|---|---|---|
| 1. Right characters, wrong case | 19 | The frontend writes `Mia_Kim_4397` / `MIA_KIM_4397`; the database IDs are lower case. | Tool-argument hook: lower-case (deterministic, §4.3). |
| 2. Right characters, extra separators | 5 | `EM_MA_KIM_9957`, `A_A_R_A_V_G_A_R_C_I_A_1177`, `DAIKIMULLER1116` | Mostly the transcript hook: tokens between two "underscore" words are joined (`em ma` → `emma`) before the frontend sees them (§4.2). |
| 3. ASR had the full ID, no lookup used it | 7 | Split over utterances, or garbled/dropped by the frontend. | **Not claimed by this arm.** The transcript hook only helps when the whole ID is in one agent turn (one utterance, or utterances merged by the turn manager). Cross-turn assembly is phase 2 (§11); giving the backend the raw user wording is the backend-history plan. |
| 4. Spelling wrong, name heard as words | 2 | s/f, b/d/v confusions | Deferred (N8). |
| 5. ASR never had the ID | 6 | Acoustics | Out of scope. |

Two limits to be honest about:
- **A separator-stripped ID cannot be re-split in code.** `aaravgarcia1177` has no marker for where the
  first name ends. The tool-argument hook cannot recover it; it can only turn it into a local
  "ask the user" instead of a failed lookup (§4.3). Category 2 is recovered only where the transcript
  still had the spoken "underscore" boundaries.
- **A correct ID is not a pass.** Only 4 of the 11 tasks that logged in passed (§4 of the observation doc).

Separately, failed lookups count toward tau2's 10-error limit, which ended 8 tasks at 800 ms. 54
lookups at 800 ms repeated an ID that had already failed, and several sent fragments (`YA`, `eth`,
`699`, `_7340`). The local answers (N4) remove both from the error budget.

## 2. What is generic and what is τ³-specific

| Generic (code) | τ³ airline (profile config) |
|---|---|
| The `normalization/` package, both hooks, events | `tool: get_user_details`, `argument: user_id` |
| Spoken → written rules: separator words, number words, joining short tokens, dropping fillers | Separator word list `{underscore: "_"}` |
| Scoped rewriting: only spans anchored by a separator word | Written span case `lower` |
| Argument clean-up (strip characters, collapse separators, case), full-match pattern check | `pattern: '^[a-z]+_[a-z]+_\d{4}$'`, `format_hint`, `label: "user ID"` |
| Answering a call locally; a bounded local loop | – |
| Retry guard on identical canonical arguments | `permanent_failure_pattern: '^Error: .*\bnot found\b'` (tau2's lookup error) |
| Rulesets selected by name (`ruleset: en`) | – (the `en` ruleset is the generic English one) |
| Prompt note mechanism (a catalog key appended to the frontend prompt) | The note's wording names identifiers generally; no airline terms |

## 3. Package layout and names

```
voice_frontend_backend_agent/normalization/
  __init__.py
  rules.py        # pure token rules: tokenize, spoken digits, separators, joining, fillers, case
  transcript.py   # TranscriptNormalizer: finds anchored spans and rewrites them (ASR hook)
  arguments.py    # ArgumentNormalizer: per-tool argument clean-up, validation, retry guard
  prompts.py      # appends the configured note to the frontend prompt
```

- Everything in `rules.py`, `transcript.py` and `arguments.py` is a pure function or a frozen
  dataclass, except the retry guard's per-session memory, which lives in the runner (§4.3).
- No turn-manager or wire changes. Both hooks are called from `agent/runner.py`
  (`TextAgentRunner`), which already owns the per-session state.

## 4. Design

### 4.1 Rules (`rules.py`)

**Language.** The word lists (digit and number words, `double`, fillers, stop words) are
language-specific, so they form a named **ruleset**. This change ships one, `en`, defined in
`rules.py`. Config selects it with `normalization.transcript.ruleset: en` and may extend its lists
(`extra_filler_words`, `extra_stop_words`). An unknown ruleset is a `ConfigError`. When the ASR
`language_code` does not start with the ruleset's language (`en-`), loading warns: the lists would not
match that language's transcripts. A new language means adding a ruleset, not changing the hooks.

Tokenization: split on whitespace; strip `.,?!;:` and surrounding quotes from each token; keep the
original token text for the log. Matching is case-insensitive.

| Rule | Behaviour | Config |
|---|---|---|
| Separator words | `underscore` → `_`. A separator word **anchors** a span (§4.2). | `separator_words` map |
| Digit words (`en`) | `zero`/`oh`, `one` … `nine` → digits; `double X` → `XX`. `oh` only inside a digit run. | `number_words: true` |
| Compound numbers | `forty three` → `43`, `nineteen` → `19` (tens, teens, tens + unit). | `compound_numbers: true` |
| Joining | Tokens of one part are concatenated without spaces: `em ma` → `emma`, `K im` → `kim`, `M A` → `ma`. | – |
| Fillers (`en`) | `uh`, `um`, `er`, `ah`, `hmm` inside a span are dropped. | `extra_filler_words` |
| Case | The written span is lower-, upper- or kept-case. | `case: keep` (default) |

**Letter names are not mapped** (`em` → `m`) by default. The ASR writes syllables of a name as short
tokens ("EM MA" for Emma), and mapping them would produce `mma`.

### 4.2 Transcript hook (`transcript.py`)

Called in `TextAgentRunner.respond(text)` on the final text of the agent turn, so it sees utterances
already merged by `cancel_and_merge` or by a manual `response.create`. The wire and the turn manager's
`agent_turn_start` keep the raw text (N3).

A **span** is built around each run of anchors:
1. Between two anchors, every token is part of the ID (up to `max_part_tokens`, default 6; more means
   it is not an ID and the span is abandoned).
2. Left of the first anchor: if the adjacent token is a word of 3+ letters, it is the whole first part.
   Otherwise take the run of short tokens (1–2 letters) leftwards. Stop at a stop word of the
   ruleset (`en`: `is`, `my`, `id`, `it`, `me`, `the`, `of`, `so`, `to`, `user`, `name`, `number`,
   `code`, …).
3. Right of the last anchor: a run of digit words or numerals becomes the digit tail. Otherwise one
   word, or a run of short tokens, as in step 2.
4. The span is replaced in place by its written form. Text outside spans is untouched.

Examples (from the observation doc):

| Raw ASR | Normalized (`case: lower`) |
|---|---|
| `my user ID is Mia underscore Kim underscore four three nine seven.` | `my user ID is mia_kim_4397.` |
| `M A underscore K im underscore nine nine five seven reason` | `ma_kim_9957 reason` (the ASR error `ma` stays: not ours to fix) |
| `Em ma underscore Kim underscore nine nine five seven` | `emma_kim_9957` |
| `Underscore nine nine five seven .` | `_9957 .` (an incomplete ID, visible as such) |
| `I need two passengers` | unchanged (no anchor) |

`TranscriptNormalizer.normalize(text) -> NormalizedText(text, spans: tuple[Span, ...])`, where each
`Span` records the spoken and written forms. An unchanged text has no spans and emits no event.

### 4.3 Tool-argument hook (`arguments.py` + runner)

Rule shape (one per tool argument, profile config):

```yaml
- tool: get_user_details
  argument: user_id                    # top-level key of the call's JSON arguments
  label: user ID                       # used in local messages
  format_hint: firstname_lastname_1234 # used in local messages
  spoken_form: true                    # apply the §4.1 rules to the value ("four three" → 43)
  strip: " .-"                         # characters removed
  collapse_separators: "_"             # "a__b" → "a_b", leading/trailing "_" removed
  case: lower                          # keep | lower | upper
  pattern: '^[a-z]+_[a-z]+_\d{4}$'     # full match after clean-up; "" = no check
  on_invalid: answer_locally           # answer_locally | send
```

In the runner, after each agent step that returns tool calls (in `respond` and in `resume`):

1. **Clean up.** For each call with a rule, compute the canonical value. If it changed, rewrite the
   call's `arguments_json` and the stored `ToolCall` in `state.pending.backend_history` (its last
   message), so the backend's history matches what was sent. This works in paired and `backend_only`
   mode alike: in both, the suspended calls live in `pending.backend_history`. Emit
   `argument_normalized`.
2. **Check.** If `pattern` does not match and `on_invalid: answer_locally`, the call is not sent. Its
   result is the rendered `tool_argument_invalid` message. Emit `call_answered_locally` with
   `reason: invalid`.
3. **Retry guard.** If `(tool, canonical arguments)` already produced a **permanent** failure in this
   session (step 5), the call is not sent. Its result is the rendered `tool_call_already_failed`
   message. Emit `call_answered_locally` with `reason: already_failed`.
4. **Split.** Calls that pass go out as today. Local results are held by the runner.
   - If no call is left to send, the runner immediately continues the agent with the local results
     (`send_tool_results`), within the same `respond`/`resume` call, and repeats from step 1.
     At most `max_local_rounds` (default 3) consecutive all-local rounds. After that, local
     interception (steps 2–3) is bypassed for that step only: the calls go out with their
     **canonical** arguments from step 1, so the wire and the stored history still agree. Emit
     `local_rounds_exhausted`. A stuck model cannot loop.
   - Otherwise the reply carries only the sent calls. When the client's outputs arrive
     (`resume(outputs)`), the held local results are merged in, so the text agent's
     `validate_tool_results` sees every outstanding call.
5. **Remember permanent failures.** In `resume`, each output the client returned for a sent call is
   classified. Only an output matching `permanent_failure_pattern` (the lookup found nothing, or the
   value was rejected as invalid) adds `(tool, canonical arguments)` to the session's failed set. Any
   other error (a timeout, a server or transport error) is transient and never blocks a retry.
   Outputs the client never returned (the `result_timeout_s` path) are not classified at all. The
   pattern is per profile, with no default: with the guard on, an empty pattern is a `ConfigError`,
   so nothing is blocked by accident.

Invariants:
- The runner still replaces its state only when a call completes. A barge-in cancelling `respond`
  mid-local-loop leaves the state and the failed set unchanged.
- The turn manager only ever sees calls that went out, so its outstanding set, `tools_out` and the
  `result_timeout_s` synthesis are unchanged.
- Usage from all agent steps of one `respond`/`resume` is summed into the one `AgentReply`.

Local messages are catalog keys with literal placeholders `{label}`, `{value}`, `{format_hint}`,
`{spelled}` and `{tool}`. `{spelled}` is the value read out character by character
(`m, i, a, underscore, k, i, m, underscore, 4, 3, 9, 7`). Proposed wording:
- `tool_argument_invalid`: "Not looked up: "{value}" is not a complete {label} (format
  {format_hint}). No tool was called. Ask the user to say their complete {label} again. Do not guess
  or fill in any part of it."

  An invalid value can be any fragment (`YA`, `699`, `_7340`), so the code cannot tell which part is
  missing. The message asks for the whole ID and makes no claim about the missing part. The
  read-back of a doubtful part belongs to the retry guard's message, where the value is complete.
- `tool_call_already_failed`: "Not looked up again: {tool} with {label} "{value}" already failed in
  this conversation. Do not retry it unchanged. Read it back character by character ({spelled}) and
  ask the user only about the part that may be wrong."

### 4.4 Prompt note

`normalization.transcript.frontend_note_key` names a catalog key appended to the frontend prompt when
the transcript hook is enabled (`normalization/prompts.py`, applied in `TextAgentRunner.configure`
through the per-session inline prompts, so `rendered_prompts()` shows it). Proposed
`identifier_note_voice`:

- User turns may already contain an identifier in written form, such as `mia_kim_4397`. Copy it into
  query exactly, character for character.
- For identifiers only, this refines "the latest user turn overrides older values": if an earlier
  turn gave a complete identifier and a later turn gives only part of it, or a different spelling
  that is not stated as a correction, keep the earlier one and add the later wording, for example
  "user ID mia_kim_4397 (latest attempt: mia_fancy_4739)". An explicit correction still overrides.

This addresses task 26 (`Amelia_Sanchez` → `Amelia_Fancy`). The backend needs no note: the local
tool results explain themselves.

### 4.5 Events

| Kind | When | Fields |
|---|---|---|
| `transcript_normalized` | the transcript hook changed the text | `raw`, `text`, `spans: [{spoken, written}]` |
| `argument_normalized` | an argument was rewritten | `call_id`, `tool`, `argument`, `before`, `after` |
| `call_answered_locally` | a call was not sent | `call_id`, `tool`, `argument`, `value`, `reason` (`invalid`, `already_failed`), `local_round` |
| `local_rounds_exhausted` | the local loop hit `max_local_rounds` | `tools`, `rounds` |
| `session_start` (extended) | session start | `normalization: {transcript: bool, tool_arguments: [tool.argument…], retry_guard: bool}` |

**Redaction.** Identifiers are user content, so these events must respect `logging.redact_content`.
`EventLog` drops a fixed set of content keys (`_CONTENT_KEYS` in `agent/sinks.py`: `text`,
`transcript`, `arguments`, …). The new content fields are added to that set: `raw`, `spans`,
`before`, `after` and `value` (`text` is already in it). With redaction on, the events keep only their
structure (`call_id`, `tool`, `argument`, `reason`, counts), which is still enough to count each fix.
A unit test writes every new event with redaction on and asserts that no identifier appears in the
file.

The text agent's `backend_tool_calls` event keeps the model's original arguments. Metrics that count
lookups should use the wire calls (the tau2 simulation file) or apply `argument_normalized`.

## 5. Configuration (`config/voice_agent.yaml`, new section)

```yaml
normalization:
  transcript:
    enabled: false
    separator_words: {underscore: "_"}
    ruleset: en                      # language word lists (§4.1); en is the only one shipped
    number_words: true
    compound_numbers: true
    extra_filler_words: []           # added to the ruleset's fillers
    extra_stop_words: []             # added to the ruleset's stop words
    max_part_tokens: 6
    case: keep                       # keep | lower | upper (written spans only)
    frontend_note_key: ""            # catalog key appended to the frontend prompt; "" = none
  tool_arguments:
    enabled: false
    rules: []                        # §4.3
    invalid_message_key: tool_argument_invalid
    max_local_rounds: 3
    retry_guard:
      enabled: false
      scope: rules                   # rules | all (D1)
      permanent_failure_pattern: ""  # required when enabled; transient errors never match (§4.3)
      message_key: tool_call_already_failed
```

Validation in `config.py` (`ConfigError` at load): `case` values, regexes compile, rule keys known,
`tool`/`argument` non-empty, `max_local_rounds >= 1`, a known `ruleset`, a non-empty
`permanent_failure_pattern` when the retry guard is on, catalog keys exist when the feature is
enabled (checked where the catalog is loaded, as for the other keys).

**`tools.source`.** The tool-argument hook works on calls the runner hands to the client, so it
requires `tools.source: client` (external execution). With `tools.source: config` the text agent
executes tools itself and the runner never sees the calls, so `tool_arguments.enabled: true` there is
a `ConfigError` rather than a silent no-op. Supporting internally executed tools would mean the same
hook inside the text prototype's tool driver; it is listed in §11. The transcript hook works with
either source.

New profile `config/profiles/tau3_eval_normalization.yaml` (extends `tau3_eval.yaml`, so backend
history stays pinned off):

```yaml
extends: tau3_eval.yaml
normalization:
  transcript: {enabled: true, case: lower, frontend_note_key: identifier_note_voice}
  tool_arguments:
    enabled: true
    rules:
      - {tool: get_user_details, argument: user_id, label: user ID, format_hint: firstname_lastname_1234,
         spoken_form: true, strip: " .-", collapse_separators: "_", case: lower,
         pattern: '^[a-z]+_[a-z]+_\d{4}$', on_invalid: answer_locally}
    retry_guard: {enabled: true, scope: rules, permanent_failure_pattern: '^Error: .*\bnot found\b'}
```

## 6. Files

| File | Change |
|---|---|
| `normalization/{__init__,rules,transcript,arguments,prompts}.py` | new (§3, §4) |
| `agent/runner.py` | transcript hook in `respond`; argument hook, local loop, held results and failed set in `respond`/`resume`; prompt note in `configure` |
| `config.py` | `NormalizationConfig` (frozen), defaults, validation (including `tools.source: client`) |
| `agent/sinks.py` | `raw`, `spans`, `before`, `after`, `value` added to `_CONTENT_KEYS` |
| `config/voice_agent.yaml` | the `normalization` section, off |
| `config/prompts.voice.yaml` | `identifier_note_voice`, `tool_argument_invalid`, `tool_call_already_failed` |
| `config/profiles/tau3_eval_normalization.yaml` | new (§5) |
| `engine/session.py` | `session_start` records the normalization settings |
| `cli/normalization_replay.py` | new, offline replay (§9.1) |
| `README.md` | the section, the profile, the events |

## 7. Tests (unit, offline; `tests/unit/prototypes/voice/`)

- `test_normalization_rules.py`: digit words, `double`, compound numbers, fillers, case, no letter-name
  mapping; `extra_*` lists extend the `en` ruleset.
- `test_transcript_normalizer.py`: the §4.2 table, plus real transcripts from the 800 ms run as a
  fixture corpus; no change without an anchor; `max_part_tokens` abandon; stop words; idempotence
  (`normalize(normalize(x)) == normalize(x)`).
- `test_argument_normalizer.py`: case and separators; pattern pass/fail; unknown tool untouched;
  malformed JSON arguments passed through unchanged (logged).
- `test_voice_normalization.py` (session harness with fake clients):
  - the frontend receives the normalized text; the wire `transcription.completed` carries the raw text;
  - a rewritten call goes out with canonical arguments, and `pending.backend_history` holds the same;
  - an invalid call is never emitted on the wire; the backend gets the local message and answers;
  - mixed turn: one call sent, one local; `resume` merges both results;
  - an identical failed call is answered locally the second time;
  - `max_local_rounds` stops the loop, and the calls then go out with canonical arguments that match
    the stored history;
  - a transient error output (not matching `permanent_failure_pattern`) does not block the retry; a
    timed-out call (no output) is not recorded;
  - barge-in during a local loop leaves the state and the failed set unchanged;
  - `backend_only` mode behaves the same.
- Config tests: unknown ruleset, `tool_arguments` with `tools.source: config`, and the retry guard
  without a pattern are each a `ConfigError`; a non-`en-` ASR language warns.
- Redaction test: every new event written with `redact_content: true` contains no identifier.
- Profile test: `tau3_eval_normalization.yaml` differs from `tau3_eval.yaml` only in `normalization`
  (the pattern of `test_history_profile_differs_from_tau3_eval_only_by_the_flag`); update the shipped
  profile count and the catalog key set tests.

## 8. Documentation

- Voice `README.md`: the `normalization` section, the new events, the profile.
- `misc/prototypes/voice-frontend-backend-agent-runbook.md`: profile row and port.
- `misc/prototypes/voice-frontend-backend-agent-prototype-plan.md`: revision entry.
- tau2 repo `misc/prototypes/voice-frontend-backend-agent-tau3-runbook.md`: a `norm` arm
  (container `fba-voice-norm`, port 8771; arm names can't contain `_`).

## 9. Evaluation

### 9.1 Offline replay first (no ASR, no LLM, minutes)

`cli/normalization_replay.py` reads an event log and a profile, and writes, per session:
- each `asr_final` transcript, raw and normalized;
- each logged `get_user_details` argument, raw and after §4.3, with the local decision.

An analysis script next to `id_error_analysis/` (a variant of `fix.py`) scores it per task against the
expected IDs, on both existing runs (500 ms and 800 ms). Gates before any τ³ run:

| Gate | Expected |
|---|---|
| Every category-1 task gets a lookup with the correct ID | 19 / 19 at 800 ms (deterministic) |
| Category-2 and category-3 tasks whose normalized transcripts contain the exact ID | reported; the τ³ run will tell whether the frontend copies them |
| Rewritten spans in utterances with no ID | reviewed by hand; target 0 |
| Lookups that would have been answered locally (invalid + already failed) | reported, against the 10-error-limit tasks |

**Results (2026-09-26, `tau3_eval_normalization.yaml` over `logs/fba_voice_events.jsonl`):**

| Gate | 800 ms run | 500 ms run |
|---|---|---|
| Tasks with a lookup using the exact ID (recorded → canonical) | 11 → **30** (+19: every category-1 task) | 10 → **23** (+13: every category-1 task) |
| Correct recorded lookups lost (answered locally) | 0 | 0 |
| Tasks with an ASR final containing the exact written ID (raw → normalized) | 0 → 29 | 0 → 13 |
| `get_user_details` lookups answered locally: invalid, already failed | 109, 3 | 120, 7 |
| …of them in the `too_many_errors` tasks | 48 (8 tasks) | 67 (15 tasks) |
| Rewrites of non-ID speech (manual review of the 800 ms rewrites) | 0 | – |

- At 800 ms, the normalized transcripts contain the exact ID in one utterance for tasks 9, 15, 16,
  40, 41, 45 and 46, where no recorded lookup had it. Six are category 3 and one (16) is category 2.
  Whether the frontend now copies the ID is what the τ³ run measures.
- Only 3 already-failed lookups are blocked in the replay because it is conservative: a recorded
  failure counts only when the recorded arguments were already canonical. In a live run, every
  lookup is canonical, so the guard sees all repeats.
- First-order only: a local answer changes the rest of the conversation, which a log cannot show.

### 9.2 Voice τ³

- Arms: `tau3_eval` (baseline) and `tau3_eval_normalization`, same commit, airline `regular`,
  800 ms, more than one trial each (the §6 swap of the observation doc shows single-trial noise).
- Report: login funnel (§4 of the observation doc), pass rate, `too_many_errors` endings, local
  answers per task, lookups per task.
- Then a combined arm with backend history (a profile extending both), since history helps after
  login and normalization helps before it.

## 10. Risks

| Risk | Mitigation |
|---|---|
| A rewrite changes non-ID speech | Spans need a separator anchor; stop words; `max_part_tokens`; offline review gate (§9.1). |
| The frontend still re-cases the written ID | The argument hook lower-cases it anyway. |
| The model loops on local answers | `max_local_rounds`; the messages tell it to ask the user. |
| A correct ID is rejected by a too-strict pattern | Pattern is per profile; `on_invalid: send` for a log-only dry run. |
| Rewritten history confuses the backend | The stored call matches what was sent; the result refers to the canonical value. |
| Metrics that read `backend_tool_calls` see the model's original arguments | Documented (§4.5); use wire calls or `argument_normalized`. |
| The grader cares about calls not sent | tau2 grades database state and outputs; calls not sent change nothing in the database. |

## 11. Out of scope and phase 2

- **Joining an ID across separate turns:** hold the turn (bounded, e.g. 1.5 s extra) when the
  normalized text ends in an incomplete ID (a trailing separator, or no digit tail), then merge with
  the next utterance. Needs turn-manager changes and a latency budget. Backend history already gives the
  backend the raw earlier turns.
- Spelled letters vs spoken name (fix 4): prompt-only.
- Vocal-tic filtering (N7). ASR model or word boosting (fix 5).
- Moving the TTS `TextNormalizer` into `normalization/`.
- The same argument hook in the text prototype, and for internally executed tools (`tools.source:
  config`) in the voice prototype. τ² has typed IDs; the case failure is voice-specific.
- More rulesets than `en`.

## 12. Implementation order

1. `rules.py`, `transcript.py` with the unit tests and the real-transcript corpus.
2. `cli/normalization_replay.py` and the §9.1 gates. Tune the rules here, before any agent wiring.
3. `arguments.py` and its unit tests.
4. Config section, validation, catalog keys, profile.
5. Runner wiring (transcript hook, argument hook, local loop, retry guard, prompt note), session
   tests, events, `session_start`.
6. Docs (§8), then the τ³ arms (§9.2).
