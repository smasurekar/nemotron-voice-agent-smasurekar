# Plan: frontend verdict on barge-in while the agent is thinking (Voice Frontend/Backend Agent)

**Status:** revisions 1–4 implemented (§11.10: replay results; §8 τ³ runs pending) · **Date:** 2026-09-28 · **Revision:** 4.
Implementation notes (revision 3):
- §6: a `new` verdict whose probe can't be carried out (the state changed) logs a separate `barge_in_fallback`
  event (`reason: state_mismatch`) and answers the merged text with a normal `respond`. It isn't a
  `barge_in_verdict` reason, because the verdict has already been applied by then.
- §6: `thinking_cancelled` also uses the reasons `auto_response_off` and `client_cancelled` for those closes.
- §4.2: `ScriptedAgentPort` never delegates, so its `in_flight` is always `None` and a review never opens for it
  (D12). It has no scripted probes. The tests drive the real `TextAgentRunner` with gated fake LLM clients
  instead (`tests/unit/prototypes/voice/test_voice_barge_in_verdict.py`).
- §4.3: a verdict and the other closes are applied synchronously. The old agent task is cancelled without
  awaiting it, so no other event can run between closing the review and starting the new turn. The runner's
  call bookkeeping keeps the cancelled call's cleanup from touching the new call.
- §7.2: the `test_voice_tau2_contract.py` check with the `frontend_verdict` profile wasn't added. That test
  uses the scripted agent, for which a review never opens. The wire behaviour during reviews (statuses,
  no response during a review) is asserted in the harness tests instead.
Revision 2 replaces the separate classifier (revision 1) with a **frontend verdict**. The frontend call made for
the user's new words also returns `task: continue | new` next to its usual decision. This is one call instead of
two, and the `NEW` path makes no extra frontend call.
Revision 3 applies the review of revision 2:
- reviews close on ASR failure, audio clearing and disabled automatic responses (§2.1);
- no verdict applies while another utterance still awaits its transcript (R3);
- a review opens only after delegation (D12);
- `AgentReply.staged` tells the turn manager that a reply was staged (§4.2);
- the verdict configuration is wired through `server.py` (§4.2, §4.5);
- the new content fields are redacted (§6);
- the extra backend compute of the delayed cancel on NEW is stated (§5);
- an agent failure during a review closes it, and staging is bound to one call (§2.1, §4.2).
Revision 4 (§11) responds to the first live browser sessions, where the frontend chose `new` for "Okay ." and
"Okay , please check.", both with a query identical to the running one:
- a code guard, where an identical query means `continue`; this reverses D9;
- Jinja templates for the prompts, so they change with the configuration and with the in-progress state;
- a rewritten frontend prompt and in-progress note;
- the new words passed to the probe separately;
- a verdict replay CLI to measure prompt changes before a live run.

**Code:** `src/prototypes/voice_frontend_backend_agent/` (turn manager, session, runner, server, config, prompts,
profiles) and
`src/prototypes/text_frontend_backend_agent/` (the frontend step split out of `send()`, and an optional
in-progress note with a `task` field; both off by default and byte-identical when off). Revision 4 also
changes `prompts.py` (Jinja rendering) and `frontend.py` (per-call rendering), and adds `jinja2` to
`pyproject.toml`.
**Evaluation:** browser demo (voice runbook §3B) **and** τ³-bench (new `verdict` arm, §8.2).
**Related:** [`voice-frontend-backend-agent-prototype-plan.md`](voice-frontend-backend-agent-prototype-plan.md),
[`text-frontend-backend-agent-prototype-plan.md`](text-frontend-backend-agent-prototype-plan.md),
[`voice-frontend-backend-agent-runbook.md`](voice-frontend-backend-agent-runbook.md),
[`frontend-backend-agent-backend-history-plan.md`](frontend-backend-agent-backend-history-plan.md), and the τ³ runbook
`tau2-bench-smasurekar/misc/prototypes/voice-frontend-backend-agent-tau3-runbook.md`.

This plan adds a third value, `frontend_verdict`, to `barge_in.while_thinking`. When the user speaks while a
delegated request is still being worked on, the agent keeps working. Once the transcript is in, the frontend is
asked for its usual decision, with a note saying that a task is already running. Its `call_backend` then also
carries `task`:

- `continue`: the words only acknowledge or restate the running request. The frontend's reply is discarded and
  the running task finishes and is spoken once.
- `new`: the words add, change or replace something. The running task is cancelled and the frontend's decision
  is carried out, which is exactly what `cancel_and_merge` produces today, minus one frontend call.

The default stays `cancel_and_merge`, so every existing profile, including all τ³ arms and the text τ² runs,
behaves and sends the same bytes as today.

---

## 0. Decisions to confirm

| # | Decision | Proposed choice | Why |
|---|---|---|---|
| D1 | Who decides | **The frontend**, in the call it makes for the new words anyway: `call_backend` gains a `task` field (`continue` \| `new`), present only when a task is in progress | One prompt, one set of rules. The frontend sees its own running query, so "restates the same request" is judged in context. No extra call on the `NEW` path |
| D2 | Flag shape | `barge_in.while_thinking: frontend_verdict`, plus `barge_in.frontend_verdict: {note_key, timeout_ms}` | `while_thinking` is already the enum for this decision; a third value keeps one switch |
| D3 | When the decision is made | Cancelling is **delayed** from `speech_started` until the frontend has answered on the ASR final. Only the filler audio is stopped at `speech_started` | Today the task is cancelled at `speech_started`, before any transcript exists (§1), so no decision based on the transcript could prevent the cancel |
| D4 | Text package change | Split `FrontendBackendAgent.send()` into `decide_turn()` (frontend only, no state change, no events except repair) and `continue_turn()` (what `_run_paired` does after the decision). `send()` = both, unchanged. `decide_turn()` takes an optional `in_progress_note`; when `None`, the prompt and tool schema are exactly today's | This is the only way to get the frontend's decision without starting the backend. Gating on `None` keeps text τ² and existing voice runs byte-identical, which a test asserts (§7.1) |
| D5 | What the frontend receives | The same user text `cancel_and_merge` would send today (running turn text + new words), plus a system-prompt note (voice catalog key `frontend_task_in_progress`) with the running query, the filler text, and whether the filler was heard | With the same user text, a `new` verdict gives exactly today's merged turn. The note is the only added input, and it's worded in the voice catalog, not the text package |
| D6 | Answers other than a delegation | Direct answer or contract fallback → **new**. A missing or invalid `task` → **new**. Timeout or error → **new** via today's path (cancel, merge, normal `respond`) | Fails open to known behaviour. A missed no-op costs one restart; a wrong continue would lose a real request |
| D7 | What **continue** does | Silent: the frontend's reply and filler are discarded, the utterance isn't added to history, the filler isn't spoken again, and the running task's answer is delivered normally | Fixes the repeated filler seen in §1. A spoken acknowledgement is out of scope (§10) |
| D8 | The backend finishes while the frontend is still deciding | The runner **stages** the finished turn instead of committing it. `continue` commits it and speaks the answer; `new` discards it, which is identical to a cancel | No history repair and no duplicated user text: `new` always runs on the same state the frontend decided on (§4.3) |
| D9 | Duplicate re-delegation guard (revision 1, C8) | ~~Not needed~~ **Reversed in revision 4 (§11, V1)** | The assumption was that the frontend, seeing its own running query in the note, answers `continue` for a restatement. The live sessions in §11.1 show otherwise |
| D10 | Opt-in profiles | `browser_demo_frontend_verdict.yaml` (extends `browser_demo_slow_backend.yaml`) and `tau3_eval_frontend_verdict.yaml` (extends `tau3_eval.yaml`); each changes one key | Each A/B pair differs by exactly one key. Existing runs stay reproducible |
| D11 | `backend_only` + `frontend_verdict` | Config error | That mode has no frontend to ask |
| D12 | When a review may open | Only once the running turn **has delegated**: the runner has recorded its in-flight query (`AgentPort.in_flight`, §4.2) and no tool call has gone out. During the initial frontend call, or for a turn the frontend answered directly, speech keeps today's behaviour (`cancel_and_merge`) | Before the delegation there's no running query or filler for the note to describe, and no backend work worth keeping: the frontend call is short, and restarting it with the merged text is exactly today's behaviour |
| D13 | Utterances that never produce a transcript (ASR failure, `input_audio_buffer.clear`) | They count as resolved with no text. A review with no text utterance and no probe closes as CONTINUE | Nothing actionable was said, so the running task should go on. The review must never stay open and hold the answer forever |
| D14 | Automatic responses disabled (`protocol.auto_response` off, or the client's `turn_detection.create_response` false) | A review opens only if automatic responses are on at `speech_started`. If they are off by the time the transcript arrives, the review closes, reason `auto_response_off`, and today's `cancel_and_merge` is applied (cancel, merge prefix; the client's `response.create` starts the merged turn) | Without automatic responses there's no point where the frontend could be asked. Falling back to today's behaviour keeps the client-driven flow unchanged |

---

## 1. Problem, grounded in a browser session

**Session:** `sess_0717897ad9064de78ea7` (model tag `pine-browser`, profile `browser_demo_slow_backend.yaml`,
`filler.mode: speak`, simulated backend delay `FBA_BACKEND_DELAY_S`, default 5 s, backend history off,
2026-09-28 13:26–13:27 UTC).

**Logs** (repository root; container started as in the voice runbook §3B with
`FBA_VOICE_EVENT_LOG=logs/fba_voice_web_events.jsonl` and `FBA_FILLER_LOG=logs/fba_voice_web_filler.jsonl`):

| File | Records for this session | What it shows |
|---|---|---|
| `logs/fba_voice_web_events.jsonl` | 98 | `speech_started`, `asr_final`, `agent_turn_start`, `delegation`, `filler`, `thinking_cancelled`, `agent_turn_done`, `turn_latency` |
| `logs/fba_voice_web_filler.jsonl` | 8 | one `filler_timing` record per delegated turn: `spoken`, `outcome` |

Reproduce the view:

```bash
grep sess_0717897ad9064de78ea7 logs/fba_voice_web_events.jsonl \
  | jq -c 'select(.kind|test("asr_final|thinking_cancelled|delegation|filler$|agent_turn_done")) | {kind, turn_id, audio_ms, transcript, query, text}'
grep sess_0717897ad9064de78ea7 logs/fba_voice_web_filler.jsonl | jq -c '{turn_id, text, spoken, outcome}'
```

The request for order 1151 (turn 4) was cancelled and restarted **five times**:

| Turn | `thinking_cancelled` (audio ms) | Utterance that caused it (`asr_final`) | Re-delegated query | Filler spoken |
|---|---|---|---|---|
| 4 | 45000 | "Yes, please do that." | — (original: "The user wants to check the status of order 1151.") | "Let me check on that order." |
| 5 | 50900 | "Okay ." | "The user wants to check on order 1151." | same, again |
| 6 | 56360 | "You are checking now. Why are you telling me?" | same | same, again |
| 7 | 62920 | "To check" | same | same, again |
| 8 | 72520 | "You are checking only . Why are you saying again and again? Let me check on that order when you will give me the order status." | same | same, again |
| 9 | — (completed) | — | "The user wants to check the status of order 1151." | same; `outcome: answer` |

What the logs show:

1. **The cancel happens before any transcript exists.** Each `thinking_cancelled` has the same `audio_ms` as the
   `speech_started` before it, with `reason: turn_detected`. Its `asr_final` arrives 1.5–5 s later. This is
   `TurnManager.on_speech_started` → `_cancel_thinking(turn, merge=True)` (`engine/turn_manager.py`).
2. **Each utterance is only an acknowledgement or a complaint about the delay.** None adds or changes anything
   in the request, yet each one throws away the backend work done so far.
3. **Merging makes each restart look like a new request.** Turn 9's `agent_turn_start.text` is all six utterances
   joined together. The frontend isn't told that a task is already running or what filler it spoke, so it
   delegates again and speaks the same filler every time (six `filler_timing` records with `spoken: true`).
4. **Cost.** The request ended at audio 41504 ms, and the answer's first audio came at 85160 ms: **43.7 s**.
   A single uninterrupted run took 7.3 s (`agent_turn_done` turn 9, `latency_ms: 7310`). With **continue**,
   the answer would have come about 8 s after the request. The user heard the filler six times and six
   frontend calls were made (plus five partial backend runs).

**The same problem in τ³.** `tau3_eval.yaml` uses `while_thinking: cancel_and_merge` and `filler.mode: log_only`,
so the τ³ user never hears the filler. Under `--speech-complexity regular`, however, the simulated user produces
backchannels, vocal tics and non-directed speech (the S_BC, S_VT and S_ND selectivity metrics), and today any of
these cancels a thinking turn that hasn't sent a tool call yet. tau2's OpenAI provider reacts to its own
interruptions only with `conversation.item.truncate` and never sends `response.cancel`
(`tau2-bench-smasurekar/src/tau2/voice/audio_native/openai/provider.py`), so the server-side decision in this
plan applies to τ³ unchanged.

---

## 2. Behaviour

The states are unchanged (IDLE, THINKING, AWAITING_TOOLS, SPEAKING). `frontend_verdict` adds a **review** that is
attached to the current `UserTurn` while it is THINKING. A review holds the new utterances and one **probe**: a
`decide_turn()` call with the in-progress note.

```
speech_started while THINKING, the turn has delegated (D12), no tool call has gone out,
and automatic responses are on (D14)
  └─ open review (if none), awaiting += 1: stop filler audio only; agent task keeps running;
     the runner starts staging (a text result is held, not committed); the filler race
     must not queue filler for this turn from now on
speech_started while THINKING but before delegation (D12)  → today's cancel_and_merge
speech_started while a review is open                       → awaiting += 1 (no second review)
asr_final (auto_response path) while the turn is under review
  └─ awaiting -= 1; append utterance; cancel any running probe and start a new one on
     merged = running turn text + all review utterances
       probe → Delegate(task=continue)                 → CONTINUE
       probe → Delegate(task=new | missing | invalid)  → NEW
       probe → DirectAnswer / ContractFallback         → NEW
       probe timeout / error                           → NEW_FALLBACK
utterance dropped (ASR failure, audio cleared) or empty transcript  → awaiting -= 1, no text added (§2.1)
a probe result is applied only when awaiting == 0 (R3); until then it is kept as the ready verdict
CONTINUE → close review; discard the probe; drop utterances; stop staging; the running task goes on
           (if it already finished: commit the staged turn, speak its answer)
NEW      → close review; by what the running task did meanwhile:
             still running or staged → cancel it / discard the staged turn (state is back to what the probe saw);
                                       start a new turn with text = merged whose agent step is
                                       continue_turn(probe) (no second frontend call)
             returned tool calls     → calls already went out (below); queue the utterances (as today after tools_out)
NEW_FALLBACK → as NEW, but the new turn runs a normal respond(merged) (today's cancel_and_merge path)
agent task finishes while the turn is under review
  text  → staged by the runner (reply.staged), not spoken, until the verdict
  calls → committed and delivered at once (the turn is now uncancellable, as today); the verdict only routes the utterances
```

Rules:

- **R1 — Default unchanged.** With `cancel_and_merge` or `ignore`, no code path on the new branch runs, and
  `decide_turn()` is never called with a note. The existing barge-in and text tests pass unmodified.
- **R2 — One review per turn, one probe at a time.** A second `speech_started` during a review doesn't open
  another one. A new ASR final cancels the running probe and starts a new one on the longer merged text.
- **R3 — No verdict while an utterance is pending.** The review counts utterances that have started
  (`speech_started`) but have no final transcript yet (`awaiting`). A probe that finishes while `awaiting > 0`
  is kept as the **ready verdict** and not applied. When `awaiting` reaches 0:
  - if a new utterance was added since that probe started, the ready verdict is stale and a new probe runs;
  - otherwise the ready verdict is applied.

  So a CONTINUE can never close the review while the user is still mid-sentence with a correction.
- **R4 — Nothing is spoken during a review.** Neither the staged answer nor the probe's filler is spoken.
- **R5 — Tool calls are never held.** Once `tools_out` is set, the existing rule applies: the turn is never
  cancelled. With client tools (τ³) this means a review can only happen before the first tool call of a turn,
  which is the same window in which `cancel_and_merge` acts today.
- **R6 — Speaking is unchanged.** Speech during SPEAKING still interrupts the answer (`_interrupt_output`).
- **R7 — `response.cancel` from the client** still cancels thinking at once. An open review, its probe and any
  staged turn are cancelled and discarded (reason `client_cancelled`).
- **R8 — The probe has no side effects.** `decide_turn()` doesn't change `SessionState` and emits no `delegation`
  or `filler` events, so the running turn's filler record and the `FillerTap` → `on_filler` path aren't touched.
  Those events are emitted by `continue_turn()` when a NEW verdict carries the probe out.

### 2.1 How a review always closes

A review must never stay open, because an open review holds the answer and suppresses the filler. Every exit is
listed here, and §7.2 tests each one.

| Trigger | Where it is detected | Effect on the review |
|---|---|---|
| Probe verdict with `awaiting == 0` | turn manager (`_apply_verdict`) | CONTINUE / NEW / NEW_FALLBACK (§2) |
| ASR failure (`_finalize` catches the recognizer error and emits `input_transcription_failed`) | `engine/session.py` → new `turns.on_utterance_dropped(item_id, reason="asr_failed")` | `awaiting -= 1`, no text added |
| Input audio cleared (`input_audio_buffer.clear` → `_cancel_utterance`) while an utterance is open | `engine/session.py` → `turns.on_utterance_dropped(item_id, reason="audio_cleared")` | `awaiting -= 1`, no text added |
| Empty transcript (`< min_transcript_chars`) | turn manager (`on_user_input`, before the existing `empty_transcript` return) | `awaiting -= 1`, no text added |
| `awaiting` reaches 0 with no text utterance in the review and no probe | turn manager | close as CONTINUE, reason `no_transcript` (D13) |
| Automatic responses off when the transcript arrives (`from_audio` but not `auto`, i.e. `protocol.auto_response` or the client's `turn_detection.create_response` is false) | turn manager (`on_user_input`) | close, reason `auto_response_off`; apply today's `cancel_and_merge` (cancel the task, set the merge prefix, put the input in `_pending_inputs`) (D14) |
| Client `response.cancel` | `on_response_cancel` | close, reason `client_cancelled`; cancel the task and the probe, discard the staged turn (R7) |
| Tool calls go out during the review | `_run_agent` → `_deliver` | the review stays open only to route utterances: CONTINUE drops them, NEW queues them. No staging (R5) |
| Agent/backend failure of the running turn (`respond()` raises; `_run_agent` → `_fail_turn`) | turn manager (`_fail_turn`) | close, reason `agent_failed`: cancel the probe, `end_staging(commit=False)`, clear `turn.review`. Utterances already transcribed are queued as the merged input (running turn text + utterances); if `awaiting > 0`, that text becomes `_merge_prefix` for the pending transcript instead. This is what `cancel_and_merge` would have produced, because it would have cancelled the turn at `speech_started` |
| Session closes | `close()` | cancel the probe, discard the staged turn |

Every close stops staging in the runner (`end_staging`) and logs `barge_in_closed` with the reason.

---

## 3. Configuration

`config/voice_agent.yaml` / `DEFAULTS` in `config.py`:

```yaml
barge_in:
  enabled: true
  history: truncate_heard
  interruption_marker: " [interrupted by the user]"
  while_thinking: cancel_and_merge     # cancel_and_merge | ignore | frontend_verdict
  frontend_verdict:                    # used only with while_thinking: frontend_verdict
    note_key: frontend_task_in_progress   # key in the prompt catalog (prompts.voice.yaml)
    timeout_ms: 4000                   # probe deadline; on timeout, today's cancel_and_merge path
```

Validation (`config.py`):

- `_ENUMS["barge_in.while_thinking"]` gains `"frontend_verdict"`.
- `BargeInConfig` gains `frontend_verdict: FrontendVerdictConfig(note_key: str, timeout_ms: int)`.
- With `frontend_verdict`: `note_key` must be present in the catalog (checked like `_check_normalization_prompts`),
  `timeout_ms > 0`, and the frontend must be enabled (D11). The error messages name the key and the profile file.

New profiles:

```yaml
# config/profiles/browser_demo_frontend_verdict.yaml
extends: browser_demo_slow_backend.yaml
barge_in:
  while_thinking: frontend_verdict
```

```yaml
# config/profiles/tau3_eval_frontend_verdict.yaml
extends: tau3_eval.yaml
barge_in:
  while_thinking: frontend_verdict
```

---

## 4. Changes

### 4.1 Text prototype (`text_frontend_backend_agent/`)

**`agent.py`: split `send()` without changing it.**

```python
@dataclass(frozen=True, slots=True)
class FrontendStep:
    user_text: str
    session: SessionState          # the state the decision was made on (after pending resolution)
    decision: Decision             # DirectAnswer | Delegate | ContractFallback
    totals: UsageTotals

async def decide_turn(self, text: str, session: SessionState, *, in_progress_note: str | None = None) -> FrontendStep:
    """Run only the frontend; no state change, no delegation/filler events."""

async def continue_turn(self, step: FrontendStep) -> tuple[AgentTurn, SessionState]:
    """Carry out a decision: direct/fallback finish, or emit delegation + build_request + _drive."""

async def send(self, text, session):          # unchanged behaviour
    session = self._resolve_pending_before_user_message(session)
    if not self._config.frontend_enabled:
        return await self._run_backend_only(text, session)
    return await self.continue_turn(await self.decide_turn(text, session))
```

`_run_paired` is replaced by the two halves. The code moves as it is, and the events come out in the same
order (`direct_answer` / `delegation` / `filler` / `backend_context` after the frontend call).

**`frontend.py`: optional in-progress note.** `FrontendAgent.decide(user_text, history, *, session_id,
in_progress_note=None)`:

- `None`: today's call exactly (same system prompt, same `FRONTEND_TOOLS`).
- A string: the system message is `system_prompt + "\n\n" + in_progress_note`, and the tools are
  `FRONTEND_TOOLS_IN_PROGRESS`, i.e. `call_backend` with an extra required property
  `task: {"type": "string", "enum": ["continue", "new"]}`, described as "Only while a request is in progress:
  `continue` if the user's latest words don't change the request in progress, otherwise `new`".
- `Delegate` gains `task: str = ""`. `_interpret` reads it only when a note was given. A missing or invalid
  value is **not** a contract violation (no repair round, so latency is unchanged); it's recorded as
  `task_invalid` and the turn manager treats it as NEW (D6).

**`delegation.py`:** `CALL_BACKEND_TOOL_IN_PROGRESS` and `FRONTEND_TOOLS_IN_PROGRESS`, built from the existing
schema with the extra property. `FRONTEND_TOOLS` is untouched.

The text package gains no config key: the note is passed in by the caller. The text τ² adapter never passes one.

### 4.2 Voice agent: runner and port (`voice_frontend_backend_agent/agent/`)

`AgentPort` (`port.py`) gains:

```python
@property
def in_flight(self) -> InFlight | None:
    """The running turn's delegation (query, filler text), set once the frontend has delegated; else None."""

async def probe(self, text: str, *, filler_spoken: bool) -> Probe:
    """Frontend-only decision for speech during a running turn. Must not touch conversation state."""

async def proceed(self, probe: Probe) -> AgentReply:
    """Carry out a probe's decision on the state it was made on; commits like respond()."""

def begin_staging(self) -> None:
    """From now on, a text result of the running turn is held (reply.staged) instead of committed."""

def end_staging(self, *, commit: bool) -> None:
    """Stop staging. With a held result: commit it (CONTINUE) or drop it (NEW, as if cancelled)."""
```

`AgentReply` gains `staged: bool = False`.

**How the turn manager knows a reply was staged.** The runner makes the decision when `respond()` completes:

- if staging is on and the result is text, it keeps the `_Settled` and returns `AgentReply(text=..., staged=True)`;
- otherwise it commits as today and returns `staged=False`.

`_run_agent` reads `reply.staged` right after `await respond()`. There's no `await` between the runner's decision
and that read, so a review can't close in between. If the review closed before the turn completed,
`end_staging` has already turned staging off, and the reply comes back committed and unstaged.

`InFlight` holds `query` and `filler_text`. `Probe` holds:
- `verdict` (`continue` / `new`) and `reason` (`model` / `task_invalid` / `direct_answer` / `contract_fallback`);
- the `FrontendStep`, the running and probe queries;
- the latency and the frontend `RoleUsage`.

`TextAgentRunner` (`runner.py`):

- **Constructor:** gains `barge_in: FrontendVerdictSettings | None`, holding the resolved `note_key` template and
  whether the feature is on. It's `None` unless `while_thinking` is `frontend_verdict`.
- **`respond()`** becomes `decide_turn()` then `continue_turn()`. When the decision is a `Delegate`, it sets
  `self._in_flight` before the backend starts, and clears it in a `finally` when the call ends or is
  cancelled. The behaviour is otherwise unchanged. `proceed()` sets and clears `_in_flight` the same way.
- **`probe()`:** normalizes the transcript like `respond()`, and renders the note from the catalog with
  `{query}` and `{filler}` from `_in_flight` and `{filler_state}` from `filler_spoken`. It then calls
  `decide_turn(text, self._state, in_progress_note=note)`. `self._state` is still the state before the running
  turn, because that turn hasn't committed. It raises if `_in_flight` is `None`, which D12 prevents.
- **`proceed(probe)`:** checks that `self._state is probe.step.session` (identity). If it isn't, it raises, and
  the turn manager falls back to NEW_FALLBACK. Otherwise it runs `continue_turn(probe.step)`, then `_settle` /
  `_commit` as `respond()` does, so tool-argument normalization applies unchanged.
- **Staging:** a reply with **tool calls** is always committed, because its pending state is needed for
  `resume`, and it is never staged.
- **Staging is bound to one call.** `begin_staging()` applies only to the `respond()` / `proceed()` call in
  flight at that moment, and every new call starts with staging off. Even if a close path were missed, a stale
  flag could never stage a later turn. When the bound call raises, its staging is dropped along with it.

`ScriptedAgentPort` (`scripted.py`): `in_flight`, scripted probes (verdict, delay, error), `staged` replies, and
a record of the staging calls, for the tests and the `--stub-agent scripted` server mode.

**Wiring (`server.py`).** The per-session `factory` passes `barge_in=` to `TextAgentRunner`. The value is built
from `config.barge_in` and the prompt catalog at startup, next to `normalization=config.normalization`. The same
factory builds `ScriptedAgentPort`, which needs no settings. `TurnManager` already receives the whole
`VoiceConfig` through `engine/session.py`, so `while_thinking` and `timeout_ms` reach it without new wiring.
The startup log line (`mode=..., filler=..., tools=...`) gains `barge_in=<while_thinking>`.

### 4.3 Voice agent: turn manager and session (`engine/turn_manager.py`, `engine/session.py`)

**`turn_manager.py`:**

- `_Review` dataclass: `utterances`, `awaiting: int`, `probe_task`, `probe_basis: int` (how many utterances
  the running probe saw), `ready: Probe | None`, `staged: AgentReply | None`, `opened: Stamp`. Plus `UserTurn.review`.
- `on_speech_started`, in the `frontend_verdict` branch:
  - with an open review: `awaiting += 1`;
  - otherwise, if `self.thinking`, the turn has no tool call out, `self._agent.in_flight is not None` (D12) and
    automatic responses are on (D14): open the review with `awaiting = 1`, call `agent.begin_staging()`, and
    stop the filler audio (`_interrupt_output(reason="turn_detected")` when a filler is playing). Don't cancel
    the task;
  - otherwise (still in the initial frontend call): `_cancel_thinking(turn, merge=True)`, as today.
- `on_user_input`: while the current turn has an open review, handle the utterance **before** the
  `_merge_prefix` / `_busy()` handling:
  - automatic responses off: close the review (D14);
  - empty transcript: `awaiting -= 1` (§2.1);
  - otherwise: `awaiting -= 1`, append the utterance, cancel any running probe and start a new one under
    `asyncio.wait_for(timeout_ms)`.

  Then call `_maybe_settle_review()`.
- New `on_utterance_dropped(item_id, *, reason)`: `awaiting -= 1` (never below 0), log it, then
  `_maybe_settle_review()`.
- `_maybe_settle_review()`, which enforces R3 and D13:
  - `awaiting > 0` → wait;
  - no text utterance and no probe → CONTINUE (`no_transcript`);
  - `ready` exists and `probe_basis == len(utterances)` → `_apply_verdict(ready)`;
  - otherwise → wait for the running probe.

  The probe task stores its result in `ready` and calls `_maybe_settle_review()`.
- `_run_agent`: if `reply.staged`, store it on `turn.review.staged`, log `turn_staged` and return without
  delivering. Otherwise `_deliver` as today. Tool calls during a review follow R5.
- `_apply_verdict`: CONTINUE / NEW / NEW_FALLBACK as in §2.
  - CONTINUE: `agent.end_staging(commit=True)`, then `_deliver(turn, review.staged)` if a reply is staged.
  - NEW with the task still running or staged: `agent.end_staging(commit=False)`,
    `_cancel_thinking(turn, merge=False, reason="frontend_verdict_new")`, then `_start_turn` with an agent step
    that calls `agent.proceed(probe)` instead of `agent.respond(text)`.
  - NEW_FALLBACK: `merge=True` and the normal `respond` path.
- `_filler_race`: doesn't queue filler for a turn whose review has ever opened.
- `close()` and `on_response_cancel`: cancel an open probe and call `end_staging(commit=False)`.
- `_fail_turn`: with an open review, close it as `agent_failed` (§2.1) before the existing error handling, then
  hand the utterances on through `_queued` / `_merge_prefix`. `_maybe_start_next` runs as today.
- Update the module docstring rules.

**`session.py`:**

- `_finalize`: in the ASR-failure `except` branch, after emitting `input_transcription_failed`, call
  `self.turns.on_utterance_dropped(utterance.item_id, reason="asr_failed")`.
- `AudioClear` handling: `_cancel_utterance()` returns the cancelled utterance's `item_id` (or `None`), and when
  it isn't `None` the session calls `self.turns.on_utterance_dropped(item_id, reason="audio_cleared")`.

**Why the states stay consistent.** The probe decides on the state S0, from before the running turn. On NEW
the running turn is cancelled or its staged result is discarded, so the state is S0 again and `proceed` carries
out the probe on exactly S0. That is the same history and the same merged user text that `cancel_and_merge`
builds today. On CONTINUE the probe is dropped and the running turn commits S1 as usual.

### 4.4 Prompt (`config/prompts.voice.yaml`)

```yaml
frontend_task_in_progress: |
  A request is already in progress. The backend is working on:
    {query}
  While it works you told the user: "{filler}" ({filler_state}).
  The user has just spoken again. Their words are appended to the latest user message.
  Decide as usual (answer directly, or call {call_backend} with the complete current request), and when you
  call {call_backend}, also set "task":
  - "continue" when the latest words only acknowledge, agree, confirm, thank, repeat or restate the same
    request, ask about progress, express impatience, or are not addressed to you. Keep the query the same.
  - "new" when they add a request, change, correct or cancel any detail (a name, number, date, item, amount),
    or ask for something else. When unsure, use "new".
  <examples: 6–8 short pairs across unrelated domains (clinic booking, library, gym, parcel, utilities ...),
   none of them the evaluated domain: "okay" / "yes please" / "why is it taking so long" / "hold on, talking
   to my son" → continue; "actually make it Tuesday" / "also renew my card" / "never mind, stop" → new>
```

`{filler_state}` is either "the user heard it" or "not spoken aloud; the user didn't hear it". The latter is
always the case under `filler.mode: log_only`, e.g. in τ³. The examples follow the text plan's R1 rule
(no single domain twice, none the evaluated domain), and `test_prompts`-style checks cover it.

### 4.5 File list

| File | Change |
|---|---|
| `text_frontend_backend_agent/agent.py` | `FrontendStep`, `decide_turn`, `continue_turn`; `send` composed from them |
| `text_frontend_backend_agent/frontend.py` | `in_progress_note`; `Delegate.task`; `_interpret` reads `task` |
| `text_frontend_backend_agent/delegation.py` | `CALL_BACKEND_TOOL_IN_PROGRESS`, `FRONTEND_TOOLS_IN_PROGRESS` |
| `voice_frontend_backend_agent/config.py`, `config/voice_agent.yaml` | enum value, `FrontendVerdictConfig`, defaults, validation, commented block |
| `voice_frontend_backend_agent/config/prompts.voice.yaml` | `frontend_task_in_progress` |
| `voice_frontend_backend_agent/config/profiles/browser_demo_frontend_verdict.yaml`, `tau3_eval_frontend_verdict.yaml` | new (§3) |
| `voice_frontend_backend_agent/agent/port.py`, `runner.py`, `scripted.py` | `InFlight`, `Probe`, `AgentReply.staged`, `in_flight`, `probe`, `proceed`, `begin_staging` / `end_staging`; runner constructor `barge_in=` (§4.2) |
| `voice_frontend_backend_agent/server.py` | builds the runner's `barge_in` settings from `config.barge_in` and the catalog and passes them in `factory`; startup log line (§4.2) |
| `voice_frontend_backend_agent/engine/turn_manager.py` | review, `awaiting`, `on_utterance_dropped`, `_maybe_settle_review`, verdict routing (§4.3) |
| `voice_frontend_backend_agent/engine/session.py` | `on_utterance_dropped` on ASR failure and on `input_audio_buffer.clear` (§4.3) |
| `voice_frontend_backend_agent/agent/sinks.py` | `_CONTENT_KEYS` += `utterance`, `merged_text`, `running_query`, `probe_query` (§6) |
| `voice_frontend_backend_agent/README.md` | `barge_in` section: the three values, the flow, the events |

---

## 5. Latency and cost

| Path | Frontend calls | Backend compute vs today | User-visible latency vs today |
|---|---|---|---|
| CONTINUE (acknowledgement) | +1 probe; no restart | much less: one backend run instead of one per restart | much lower: the answer arrives after one backend run (§1: about 8 s instead of 43.7 s) |
| NEW (real change) | 1 (the probe **is** the new turn's frontend call); **no extra frontend call** | **more**: the old backend keeps running from `speech_started` until the verdict, i.e. for the utterance, the ASR final and the probe (in §1 the utterances lasted 0.5–5 s, plus about 0.5 s for the probe), and that work is then thrown away | the same: the frontend call starts at the ASR final, as today; the old task is cancelled at the verdict instead of at `speech_started` |
| NEW_FALLBACK (timeout / error / state mismatch) | 1 probe + 1 normal | as NEW, plus the timed-out probe | + the probe time; should be rare, and measured (§8) |

The probe uses the full frontend prompt (about 2.4–2.7k prompt tokens in §1). Probe tokens are logged per
probe (§6), and I2 reports them separately from the per-turn FE/BE numbers.

**Delayed cancellation on NEW.** Today a barge-in stops the backend at `speech_started`. With `frontend_verdict`
the backend goes on until the verdict, so on NEW the backend LLM calls made in that window are paid for and
discarded. This is the price of not cancelling on noise.
- **Internal tools** (`tools.source: config`, as in the browser demo): tool calls the backend makes in that
  window also run, including any writes, and on NEW their results are dropped from history together with the
  staged or cancelled turn. Today's cancel can also land after a tool has run, so the risk isn't new, but the
  window is longer.
- **Client tools** (`tools.source: client`, τ³): no tool runs unseen, because a tool call leaves the agent and
  makes the turn uncancellable (R5).

§8.2 measures the discarded backend work per task. A way to guard writes during a review is listed in §10.

---

## 6. Events (event log, `logs/fba_voice_*_events.jsonl`)

| Kind | Fields | When |
|---|---|---|
| `barge_in_review` | `turn_id`, `filler_spoken`, `backend_running` | a review opens (`speech_started` after delegation) |
| `barge_in_awaiting` | `turn_id`, `awaiting`, `change` (`speech_started` / `transcript` / `empty_transcript` / `asr_failed` / `audio_cleared`) | each change of the pending-utterance count |
| `barge_in_verdict` | `turn_id`, `utterance`, `merged_text`, `verdict` (`continue` / `new` / `new_fallback`), `reason` (`model` / `task_invalid` / `direct_answer` / `contract_fallback` / `timeout` / `error` / `state_mismatch` / `no_transcript`), `running_query`, `probe_query`, `latency_ms`, `held_ms` (time the verdict waited for `awaiting == 0`), `frontend` (role usage), `task_state` (`running` / `staged` / `tools_out`) | each verdict applied |
| `barge_in_closed` | `turn_id`, `reason` (`verdict` / `no_transcript` / `auto_response_off` / `agent_failed` / `client_cancelled` / `session_closed`) | every review close (§2.1) |
| `probe_discarded` | `turn_id`, `reason` (`superseded` / `stale`) | a probe cancelled by a newer utterance, or a ready verdict made stale (R3) |
| `turn_staged` / `staged_committed` / `staged_discarded` | `turn_id`, `chars` | a text reply arrives during a review / CONTINUE / NEW |
| `thinking_cancelled` | existing fields; `reason: frontend_verdict_new` or `frontend_verdict_fallback` | NEW / NEW_FALLBACK with the task still running |
| `filler_skipped` | existing; new `reason: barge_in_review` | the filler race finds a review |
| `delegation`, `filler`, `backend_context`, `agent_turn_done` | unchanged | emitted by `continue_turn` for the turn that actually runs (never for a CONTINUE probe) |

**Redaction.** `EventLog` drops the keys in `_CONTENT_KEYS` (`agent/sinks.py`) when `logging.redact_content` is
on. `text` and `query` are already in it. This plan adds `utterance`, `merged_text`, `running_query` and
`probe_query`, so every user-content field of the new events is dropped, like `asr_final.transcript`. The other
new fields are ids, counts, timings, enums and usage, and stay.

---

## 7. Tests

### 7.1 Text prototype (`tests/unit/prototypes/`)

- `test_frontend.py`: with `in_progress_note=None`, the recorded request (messages + tools) is **byte-identical**
  to today's. With a note, the note is appended to the system prompt, the `task` enum is present and required,
  and `task` is parsed. A missing or invalid `task` gives `Delegate.task == ""` with no repair round.
- `test_agent_turn.py`: `send()` == `continue_turn(decide_turn())` on the existing scenarios (direct, delegate,
  external tools, fallback), with the same events in the same order. `decide_turn()` emits no `delegation` /
  `filler` / `backend_context` and leaves the session unchanged.
- The existing text suite (`test_no_filler_leak.py`, `test_immutability.py`, `test_state_replay.py`, ...) passes unmodified.

### 7.2 Voice (`tests/unit/prototypes/voice/`)

New `test_voice_barge_in_verdict.py`, built on `_voice_fakes.py` and `ScriptedAgentPort`:

1. CONTINUE while running: the task isn't cancelled, one `respond`, one probe, no `proceed`, the answer is spoken once, the filler is spoken at most once, and the utterance isn't in history.
2. NEW while running: `thinking_cancelled(reason=frontend_verdict_new)`, then `proceed(probe)`. There's no second frontend call, the new turn's text equals today's merged text, and the new turn has its own filler.
3. The running turn finishes during a review, then CONTINUE: `turn_staged` → `staged_committed`, and the answer is spoken after the verdict.
4. The running turn finishes during a review, then NEW: `staged_discarded`, and `proceed` runs on the pre-turn state (identity check passes).
5. Tool calls return during a review: they're committed and go out at once; CONTINUE drops the utterance and NEW queues it.
6. Direct answer, contract fallback and invalid `task` → NEW. Probe timeout, probe error and state mismatch → NEW_FALLBACK (today's merge + `respond`).
7. A second ASR final during a probe: the first probe is cancelled (`probe_discarded: superseded`), and one verdict is reached on the longer merged text.
8. **R3 hold:** a probe finishes CONTINUE while a second utterance has started but has no transcript yet. The verdict isn't applied (the answer stays staged, no speech). When that transcript arrives, the ready verdict is stale (`probe_discarded: stale`), a new probe runs, and its NEW verdict applies.
9. **R3 release:** the same, but the second utterance is dropped (ASR failure). The ready CONTINUE is then applied, with `held_ms > 0`.
10. **Closing (§2.1)**, one test per row:
    - ASR failure as the only utterance → CONTINUE (`no_transcript`);
    - `input_audio_buffer.clear` mid-utterance → CONTINUE (`no_transcript`);
    - empty transcript → CONTINUE (`no_transcript`); this also checks that an empty ASR final reaches `on_user_input`, which the plan assumes;
    - automatic responses turned off by `session.update` during the review → closed (`auto_response_off`), task cancelled, merged input in `_pending_inputs`, and the client's `response.create` starts the merged turn;
    - session close → probe cancelled, staged turn discarded;
    - agent failure during a review, (a) after the transcript and (b) while `awaiting > 0` → `error` event,
      review closed (`agent_failed`), probe cancelled, staging off. The merged input starts the next turn,
      (a) from `_queued` and (b) through `_merge_prefix`, and **that next turn's reply is `staged=False` and
      is spoken**.

    After each one, `turn.review is None` and staging is off.
11. **D12:** speech during the initial frontend call (`in_flight is None`) → today's `cancel_and_merge`, no review, no probe. Speech during a direct-answer turn → the same.
12. The filler race doesn't queue filler once a review has opened, and filler audio that is playing is stopped at `speech_started`.
13. `response.cancel` during a review: the task, the probe and any staged turn are cancelled or discarded.
14. **Staged flag:** `respond` returns `staged=True` only while staging is on and the result is text. After `end_staging`, a turn that completes comes back `staged=False` and committed. Tool-call replies are never staged.
15. **Replay of `sess_0717897ad9064de78ea7`**: the five utterances from §1, with scripted `continue` probes, give one delegation, one filler and one answer.
16. `cancel_and_merge` and `ignore`: the existing `test_voice_barge_in.py` passes unchanged.

`test_voice_usage_log.py` (or a new sinks test): with `redact_content: true`, `barge_in_verdict` records keep
`verdict`, `reason` and the timings, and drop `utterance`, `merged_text`, `running_query` and `probe_query`.

A server test (`test_voice_ws_end_to_end.py` style): with the `frontend_verdict` profile, the runner that
`server.py`'s factory builds has `barge_in` settings with the resolved note template; with `tau3_eval.yaml` it has none.

`test_voice_config.py`: the enum accepts `frontend_verdict`; a missing `note_key`, `timeout_ms <= 0` and
`backend_only` + `frontend_verdict` fail with key-naming errors; both new profiles load; `tau3_eval.yaml` still
resolves to `cancel_and_merge`.

`test_voice_tau2_contract.py`: with the `frontend_verdict` profile the wire contract is unchanged (one response
per turn, no response opened during a review unless filler audio had started, `response.done` statuses as today).

A prompt test renders `frontend_task_in_progress` for both `filler_state` values and checks the domain rule.

---

## 8. Evaluation

### 8.1 Browser (agent runbook §3B)

Start the web container as in runbook §3B, but with `--config .../profiles/browser_demo_frontend_verdict.yaml`,
and keep the same log files (`logs/fba_voice_web_events.jsonl`, `logs/fba_voice_web_filler.jsonl`). Say the §1
script ("Can you please check on order one one five one", then "Yes, please do that", "Okay", "Why are you
telling me?"), then a real change ("Actually, order one one five two"). Acceptance, per session ID:

- One `delegation` and one `filler_timing` with `spoken: true` for the acknowledgement run, and
  `barge_in_verdict.verdict == "continue"` for every acknowledgement.
- Request end → first answer audio ≈ one backend run (about 5–8 s with the slow-backend profile), not a multiple of it.
- The correction gives `verdict: new`, `thinking_cancelled.reason: frontend_verdict_new`, and one `delegation`
  with the new ID, with no extra frontend call before it.

```bash
S=<session id from session_start>
grep $S logs/fba_voice_web_events.jsonl | jq -c 'select(.kind|test("barge_in|thinking_cancelled|delegation|staged")) | {kind, turn_id, verdict, reason, utterance, running_query, probe_query, query}'
```

### 8.2 τ³-bench (a new `verdict` arm in the τ³ runbook)

The arm follows the pattern of τ³ runbook §4.1/§4.2. Its only difference from `paired` is the one key
`while_thinking: frontend_verdict`.

| Arm | Container | Port | Agent profile | Event log |
|---|---|---|---|---|
| `verdict` (paired, frontend barge-in verdict; optional) | `fba-voice-verdict` | 8772 | `profiles/tau3_eval_frontend_verdict.yaml` | `logs/fba_voice_verdict_events.jsonl`, `logs/fba_voice_verdict_filler.jsonl` |

```bash
fba_voice_start verdict 8772 tau3_eval_frontend_verdict.yaml     # τ³ runbook §4.1 helper
```

Runs follow runbook §5–§6 with run name `fba_voice_verdict_<domain>_<complexity>` and model tag
`pine-fba-voice-verdict-<domain>-<complexity>`. Use `--speech-complexity regular`, because under `control` the
user seldom speaks while the agent is thinking and the arms don't differ. Compare only with a `paired` run on the
**same agent commit**, the same domain and the same task set.

What to compare (`paired` vs `verdict`):

| Metric | Source | Expectation |
|---|---|---|
| Pass^1 | tau2 | not lower (the guardrail) |
| L_R response latency, R_R response rate | tau2 interaction metrics | better or equal |
| S_BC / S_VT / S_ND selectivity | tau2 interaction metrics | better: backchannels and non-directed speech no longer restart the turn |
| `thinking_cancelled` per task, split by `reason` | agent event log (I2) | lower |
| `barge_in_verdict` per task, by `verdict` and `reason`; probe p50/p95 latency; probe tokens | agent event log (I2) | `new_fallback` ≈ 0; `task_invalid` rare |
| Wasted backend work: backend LLM calls in cancelled turns | agent event log (I2) | lower |
| **Wrong continues**: CONTINUE verdicts followed by a user correction, or by a task failure on the corrected value | manual review of the `barge_in_verdict` utterances and both queries (at least 20 sampled) | none, or rare and explained |

Changes in `tau2-bench-smasurekar` (separate change in that repository, after this plan is implemented):

- `misc/prototypes/fba_voice_eval/fba_voice_metrics.py` (I2): count `barge_in_verdict` by verdict and reason,
  probe latency stats and tokens, and `thinking_cancelled` by reason, per task and per run. Add them to the run
  report next to the existing barge-in counts (the `s.of("barge_in") or s.of("thinking_cancelled")` check), and
  add unit tests under `fba_voice_eval/tests/`. `session_turns` must count only turns that ran
  (`agent_turn_done`), not CONTINUE probes.
- τ³ runbook: the `verdict` row in the §0 arm table, a §4.3 start command, the port 8772 in the §11 "wrong port"
  row, and the `verdict` profile in the §11 "filler audible" profile list (it keeps `log_only`).

---

## 9. Documentation and validation

- Voice `README.md`: the `barge_in.while_thinking` values, the frontend-verdict flow (§2), the new events (§6).
- Text `README.md`: `decide_turn` / `continue_turn` and the optional `in_progress_note` (unused by text τ²).
- `misc/prototypes/voice/runbook.md` §3B: "browser demo with the frontend barge-in
  verdict", the profile path and the §8.1 check.
- Per `AGENTS.md`: start a documentation subagent with `docs/AGENTS.md` during implementation, and add the
  "Documentation Writer Review" receipt to the PR.
- Checks: `uvx ruff@0.15.6 check .`, `uvx ruff@0.15.6 format --check .`, `uv run pytest tests/unit/prototypes -v`.
  The browser (§8.1) and τ³ (§8.2) runs need nemo-speech, the Inference Hub and a GPU. Report them as run or
  not run; don't claim them from unit tests.

---

## 10. Out of scope / follow-ups

- **Short spoken acknowledgement on CONTINUE** ("Still on it"), e.g. taken from the probe's `filler_text`. It
  changes what the τ³ user hears, so it would be a separate arm.
- **Answering a side question while the task keeps running** (DirectAnswer + continue). Today that case is NEW.
- **Recording the dropped utterance in the frontend history** (e.g. as a note) so that later turns see it.
- **Verdicts after tool calls went out** (AWAITING_TOOLS / resume). Today that speech is queued; unchanged here.
- **A fast rule-based check for common acknowledgements**, to skip the probe. Measure the probe cost first (§8.2).
- **Guarding internal write tools during a review** (§5): for example, pause the backend loop before executing a
  config tool while a review is open, and resume it on CONTINUE. Only relevant with `tools.source: config`.
- **Probe timeout while an answer is held** (§11.1, session `…099bbe`): the fallback discards an answer that
  finished before the timeout. Speaking it may be better than restarting. Leave it until the replay (§11.7) and
  the next live sessions show how often it happens.

---

## 11. Revision 4: verdict accuracy (code guard, Jinja prompts, replay)

**Status:** implemented 2026-09-28. Where the code differs from the text below, and the replay results, are in
§11.10.

### 11.1 Evidence: the first live sessions

**Deployment:** `fba-voice-web` with `browser_demo_frontend_verdict.yaml` (`FBA_BACKEND_DELAY_S=5`), from
2026-09-28 17:09 UTC. **Logs:** `logs/fba_voice_web_events.jsonl`, `logs/fba_voice_web_filler.jsonl`.
Seven sessions; five barge-ins during a delegated turn:

| Session | New words | Verdict | Running query = probe query? | Right? |
|---|---|---|---|---|
| `sess_6df167087c2d41e3990d` | "Okay , please check." | `new` (`model`) | yes: "The user is asking for the status of order 1231." | ✗ restarted, a held answer discarded |
| `sess_6df167087c2d41e3990d` | "Okay ." | `new` (`model`) | yes | ✗ restarted again |
| `sess_283cd5130f06463688cc` | "Okay, I will wait." | `continue` | yes | ✓ |
| `sess_e6183bb8819a44099bbe` | "Go go go check" | `new_fallback` (`timeout`, > 4 s) | – | – not a prompt issue; a held answer discarded |
| `sess_e6183bb8819a44099bbe` | "Yes check." | `continue` | yes | ✓ |

- **Accuracy:** 2 of 4 model verdicts were wrong. In `…e3990d` the user heard "Let me check on that." three
  times, the symptom §1 set out to fix.
- **Latency:** successful probes took 480–660 ms, with about 2.8k prompt tokens and 65 completion tokens.
- **The mechanism itself worked:** staging, the pending-utterance hold, the fallback and the pre-delegation
  `cancel_and_merge` (D12) all behaved as specified.

Reproduce:

```bash
jq -c 'select(.kind=="barge_in_verdict") | {session_id, utterance, verdict, reason, running_query, probe_query, latency_ms}' logs/fba_voice_web_events.jsonl
```

**Causes, from the prompt:**

1. **The base prompt contradicts the field.** The frontend prompt says "always provide both query and
   filler_text; the schema accepts no other fields", describes the tool as `call_backend(query, filler_text)`,
   and no example shows `task`. The note is appended after about 2.8k tokens of that, and the small model
   (reasoning off) follows the base prompt.
2. **Every follow-up is a fresh delegation.** The base prompt sends "every follow-up, correction, preference,
   selection, or confirmation" to `call_backend`, so "Okay, please check" becomes a new call, and `new` looks
   like the fitting label.
3. **The new words aren't quoted.** The frontend sees only the merged text, and the note says the new words are
   "at the end".
4. **Direct mode covers "thanks" and small talk.** A direct answer during a review counts as `new` (D6), so
   "okay, thanks" would also restart the request.

### 11.2 Decisions

| # | Decision | Proposed choice | Why |
|---|---|---|---|
| V1 | Code guard | A delegation whose query equals the running query (case-folded, punctuation and extra whitespace removed) is `continue`, reason `same_query`, whatever `task` says. It's on by default via `barge_in.frontend_verdict.same_query_guard: true` | It would have fixed both wrong verdicts. Restarting the identical request is never useful. It can be switched off to measure the prompt alone |
| V2 | How close a match must be | Exact after normalization; no fuzzy matching | Fuzzy matching risks swallowing a real change such as "1151" → "1152". The prompt (V5) tells the model to copy the running query word for word when nothing changed, which makes the exact match likely |
| V3 | Prompt templating | Jinja 2, in the text package's `render()`, before the existing `{persona}` substitutions. `StrictUndefined`, `autoescape=False`, `keep_trailing_newline=True`, `trim_blocks`, `lstrip_blocks`, and a `DictLoader` over the prompt catalog | Prompts can then change with the configuration and with per-call state. Neither catalog contains `{{`, `{%` or `{#` today, so every existing prompt renders byte-identical, and a test asserts that |
| V4 | Per-call rendering | `FrontendAgent` keeps the frontend template and a base context, and renders on every `decide()`. `decide(..., in_progress=None \| dict)` replaces `in_progress_note: str` | The three conflicting base-prompt lines (§11.1, cause 1) must change on probe calls only. Appending a note can't remove them |
| V5 | Prompt content | Conditional blocks in the frontend template plus a rewritten note (§11.5). Direct mode is excluded during a review | Addresses causes 1–4 |
| V6 | New words | Passed to the probe separately: `AgentPort.probe(text, *, new_words, filler_spoken)` | The note can quote them on their own line (cause 3) |
| V7 | Dependency | Declare `jinja2>=3.1` in `pyproject.toml` and run `uv lock`. It is 3.1.6 today, installed only through another package | A direct import needs a declared dependency (`AGENTS.md`: `pyproject.toml` and `uv.lock` are the source of truth) |
| V8 | Measuring | A replay CLI (§11.7) runs recorded verdicts against the live frontend model before any redeploy | It costs about 20 frontend calls, instead of a live session per prompt change |

### 11.3 Code guard (`agent/runner.py`, `config.py`)

In `TextAgentRunner.probe()`, where the verdict is derived:

```python
if isinstance(decision, Delegate):
    probe_query = decision.query
    if self._barge_in.same_query_guard and same_request(decision.query, in_flight.query):
        verdict = VERDICT_CONTINUE
        reason = "model" if decision.task == TASK_CONTINUE else "same_query"
    elif decision.task == TASK_CONTINUE:
        verdict, reason = VERDICT_CONTINUE, "model"
    else:
        verdict, reason = VERDICT_NEW, ("model" if decision.task else "task_invalid")


def same_request(a: str, b: str) -> bool:
    """Equal after case-folding and removing punctuation and extra whitespace."""

    def norm(text: str) -> str:
        return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())

    return norm(a) == norm(b)
```

- **Config:** `barge_in.frontend_verdict.same_query_guard` (bool, default `true`), in `FrontendVerdictConfig`,
  `DEFAULTS` and `voice_agent.yaml`.
- **`Probe` gains `model_task`:** the raw `task` value, `""` when missing or invalid. `barge_in_verdict` logs it,
  so the log shows how often the guard overrode the model.
- **New reason:** `same_query`, added to the §6 list of `barge_in_verdict` reasons.

### 11.4 Jinja rendering (text package) and context (voice package)

**`text_frontend_backend_agent/prompts.py`:**

```python
def render(template: str, config: Config, context: Mapping[str, Any] | None = None,
           catalog: PromptCatalog | None = None) -> str:
    """Render Jinja (with ``context``; ``{% include %}`` resolves catalog keys), then the domain placeholders."""
```

- **Environment:** one module-level environment with the V3 settings. The `DictLoader` is built per catalog,
  and `render` is called without `context` everywhere it is today.
- **Errors:** a Jinja syntax or undefined-variable error raises `ConfigError` with the prompt key.

**`text_frontend_backend_agent/frontend.py` and `agent.py`:**
- `FrontendAgent(template=..., base_context=..., catalog=...)` replaces the pre-rendered `system_prompt`.
- `decide(user_text, history, *, session_id, in_progress=None)` renders with
  `{**base_context, "in_progress": in_progress}`. When `in_progress` is set, it offers
  `FRONTEND_TOOLS_IN_PROGRESS` and parses `task`.
- `decide_turn(text, session, *, in_progress=None)` passes it through.
- `assemble_agent(..., prompt_context=None)` passes the base context. The text package and the τ² adapter
  pass none.

**Context (built by the voice runner from `VoiceConfig`):**

| Variable | Value |
|---|---|
| `barge_in.while_thinking` | `cancel_and_merge` / `ignore` / `frontend_verdict` |
| `barge_in.note_key` | the in-progress note's catalog key |
| `filler.mode` | `speak` / `log_only` |
| `in_progress` | `None` on normal turns; on probe calls `{query, filler, filler_heard, new_words}` |

**Voice runner and port:**
- `TextAgentRunner.probe(text, *, new_words, filler_spoken)` builds `in_progress` from `_in_flight` and the
  arguments. `FrontendVerdictSettings.note()` and the `{query}` / `{filler}` / `{filler_state}` substitutions
  are removed.
- `rendered_prompts()` renders with `in_progress=None`.
- The turn manager passes `new_words`, the review's utterances joined.
- `AgentPort.probe`, `ScriptedAgentPort.probe` and the test fakes follow the new signature.

**Load-time check (voice `config.py`):** with `frontend_verdict`, render the frontend template twice under
`StrictUndefined`, once with `in_progress=None` and once with a sample `in_progress`. A broken template then
fails at startup, naming the key and the profile file.

### 11.5 Prompt changes (`config/prompts.voice.yaml`)

**The `frontend` template**, three conditional blocks and an include, with everything else unchanged:

```jinja
You have exactly one tool:
{% if in_progress %}
- call_backend(query, filler_text, task): sends one detailed, self-contained request to the backend
  agent; task says whether it continues the request already in progress.
{% else %}
- call_backend(query, filler_text): sends one detailed, self-contained request to the backend
  agent, which performs all actual work and writes the final answer.
{% endif %}
...
- Direct: answer greetings, thanks, small talk, and questions about who you are, without a tool.
{% if in_progress %}
  Exception: while a request is in progress, thanks, acknowledgements, confirmations and impatience
  are not Direct mode; they use call_backend with task "continue".
{% endif %}
...
call_backend arguments:
{% if in_progress %}
- always provide query, filler_text and task
{% else %}
- always provide both query and filler_text; the schema accepts no other fields
{% endif %}
...
{% if in_progress %}

{% include barge_in.note_key %}
{% endif %}
```

**`frontend_task_in_progress`, rewritten:**

```jinja
REQUEST IN PROGRESS
The backend is already working on:
  {{ in_progress.query }}
{% if in_progress.filler %}
While it works you told the user: "{{ in_progress.filler }}"{% if not in_progress.filler_heard %} (not spoken aloud; the user did not hear it){% endif %}.
{% endif %}
The user has just said: "{{ in_progress.new_words }}"
(The latest user message is the earlier request followed by these new words.)

For this turn, always call call_backend, and set "task":
1. If the new words leave the request unchanged, set "task": "continue" and copy the request in
   progress into query word for word. This covers:
   - confirmations and acknowledgements ("okay", "yes, please check", "go ahead");
   - thanks, impatience or questions about progress ("are you checking?", "why is it taking so long?");
   - repeating the same request;
   - words not addressed to you ("hold on, I'm talking to my son").
2. Set "task": "new" only if the new words change the request:
   - a different or corrected detail (name, number, date, item, amount);
   - an added request;
   - a cancellation ("never mind, stop");
   - something else entirely.
   Then write the complete new request in query as usual.
If you are unsure whether anything changed, compare your query with the request in progress: the
same request means "continue".

Examples (the request in progress is "Check the status of the user's gym membership renewal."):
- New words "Okay, please check." ->
  call_backend(query: "Check the status of the user's gym membership renewal.", filler_text: "One moment.", task: "continue")
- New words "Why is it taking so long?" -> the same query, task: "continue"
- New words "Actually, it's the swimming pass, not the gym." ->
  call_backend(query: "Check the status of the user's swimming pass renewal.", filler_text: "One moment.", task: "new")
- New words "Never mind, stop." ->
  call_backend(query: "The user wants to stop the membership renewal check.", filler_text: "Okay.", task: "new")
```

- **Rendered text:** with `in_progress=None`, the rendered frontend prompt is byte-identical to today's.
  Normal turns and the text package see no change.
- **Domain of the examples:** gym and swimming-pass memberships appear nowhere else in the prompts and aren't an
  evaluated domain. This keeps the domain-neutrality rule that `test_voice_instructions.py` checks.
- **The `task` enum description** in `delegation.py` stays as it is. Renaming the values (for example
  `same_request` / `changed_request`) is a fallback if V1 and V5 aren't enough.

### 11.6 Files

| File | Change |
|---|---|
| `pyproject.toml`, `uv.lock` | `jinja2>=3.1` (V7) |
| `text_frontend_backend_agent/prompts.py` | Jinja rendering, `DictLoader`, `ConfigError` on template errors |
| `text_frontend_backend_agent/frontend.py`, `agent.py` | per-call rendering; `in_progress` replaces `in_progress_note`; `assemble_agent(prompt_context=)` |
| `voice_frontend_backend_agent/agent/runner.py`, `port.py`, `scripted.py` | guard, `model_task`, `probe(new_words=)`, context building; `FrontendVerdictSettings.note()` removed |
| `voice_frontend_backend_agent/engine/turn_manager.py` | passes `new_words`; logs `model_task` |
| `voice_frontend_backend_agent/config.py`, `config/voice_agent.yaml` | `same_query_guard`; startup template check |
| `voice_frontend_backend_agent/config/prompts.voice.yaml` | §11.5 |
| `voice_frontend_backend_agent/cli/verdict_replay.py` | new (§11.7) |
| READMEs, both runbooks | the guard setting, the `same_query` reason, `model_task`, Jinja context variables, the replay CLI |

### 11.7 Verdict replay CLI (`cli/verdict_replay.py`)

It follows `cli/normalization_replay.py`:

```bash
PYTHONPATH=src uv run python -m prototypes.voice_frontend_backend_agent.cli.verdict_replay \
  --events logs/fba_voice_web_events.jsonl \
  --config src/prototypes/voice_frontend_backend_agent/config/profiles/browser_demo_frontend_verdict.yaml \
  [--model pine-browser] [--cases misc/prototypes/voice/verdict_cases.jsonl] --out /tmp/verdict_replay.jsonl
```

- **Recorded verdicts:** for each `barge_in_verdict` in the log, it rebuilds the probe input from `merged_text`,
  `utterance`, `running_query` and the turn's `filler` event (so the log must not be redacted). It then calls the
  live frontend model through the real runner with the current templates and guard setting. The output has the
  old verdict and reason next to the new ones, with the probe query and `model_task`.
- **Fixed cases** (optional file): corrections and cancellations that must stay `new` ("actually 1152", "also
  cancel it", "never mind, stop", "what's your name?"), plus the §1 and §11.1 acknowledgements that must be
  `continue`.
- **Summary on stderr:** accuracy against the expected verdict, how often the guard overrode the model, and
  probe latency p50 / p95.

**Gate before redeploying:** every §11.1 acknowledgement gives `continue`, every fixed correction or cancellation
gives `new`, and the guard is off for the prompt-only comparison. Then run the same with the guard on.

### 11.8 Tests

- **Text package:**
  - `render` is byte-identical to today's for every key of both catalogs (text and voice);
  - an `{% include %}` of a catalog key works, and an undefined variable raises `ConfigError`;
  - `decide(in_progress=...)` renders the conditional blocks and selects the three-field tool;
  - `decide(in_progress=None)` sends exactly today's request.
- **Voice package:**
  - guard cases: an identical query with `new` gives `continue` / `same_query`; an identical query differing
    only in case or punctuation gives the same; a changed identifier with `new` gives `new`; with the guard off,
    the model's task wins;
  - `model_task` is logged;
  - the probe receives `new_words`, and the rendered note quotes them;
  - the startup check fails on a broken template;
  - `test_voice_instructions.py` covers the rewritten note (domain rule, catalog keys);
  - a replay of `sess_6df167087c2d41e3990d` with scripted identical-query `new` probes gives one delegation
    and one answer.
- **Replay CLI:** a small fixture log, with the fake LLM client, gives the expected old/new table and summary.

### 11.9 Evaluation

1. Run the replay (§11.7) on `logs/fba_voice_web_events.jsonl` and the fixed cases, first with the guard off,
   then on.
2. Restart `fba-voice-web` and repeat the §8.1 browser script. The source is bind-mounted, so no rebuild is needed.
3. For τ³, restart the arm containers (`fba-voice-verdictspk`, and `verdict` if used). Compare with `paired` as
   in §8.2, adding the share of `same_query` verdicts to the metrics.

### 11.10 Implementation notes and replay results

**Where the code differs from §11.2–§11.8:**
- **Client text is escaped before splicing.** `prompts.literal()` escapes a client's policy before
  `session_agent_config` splices it into a prompt template, so that braces in it render verbatim. The placements
  involved are `append`, `replace_prompt`, and the frontend `apply_to`. The reason: the τ² `banking_knowledge`
  policy prompts contain `{{ ... }}`, which Jinja would otherwise parse. `{domain_policy}` needs no escaping
  because placeholders are substituted after rendering. A test checks every τ² policy file that contains
  Jinja-like markers.
- **The backend prompt gets the context too.** The backend templates also receive `prompt_context`
  (`backend_system_prompt(catalog, config, context)`), so any prompt can depend on the configuration.
  `in_progress` exists only for the frontend.
- **The runner's verdict settings shrink.** `FrontendVerdictSettings` now holds only `same_query_guard`. The
  context comes from `config.prompt_context(config)` and is passed to the runner as `prompt_context=`. The
  runner gains `probe_request(text, *, in_flight, new_words, filler_spoken)`, which the replay CLI uses.
- **When the frontend prompt renders.** It renders once per session for normal turns, and on each probe call.
- **Load-time check.** The frontend and backend templates are always rendered once. The in-progress render runs
  only with `frontend_verdict`.
- **Replay CLI.** It has `--guard config|on|off` and refuses a profile without `frontend_verdict`. The fixed
  cases are in `misc/prototypes/voice/verdict_cases.jsonl` (13 cases).

**Replay (§11.7) on `logs/fba_voice_web_events.jsonl` and the 13 cases**, against the live
`nemotron-3.5-lightning`, 2026-09-28:

| | Guard off (prompt only) | Guard on |
|---|---|---|
| Recorded barge-ins (§11.1) | 5/5 `continue`: both wrong verdicts and the timed-out one changed | same |
| Fixed cases correct | 12/13 | 12/13 |
| `same_query` overrides | 0 | 0 |
| Probe latency p50 / p95 | 498 / 918 ms | 497 / 12 500 ms (one 12.5 s outlier on the Hub) |

**The miss.** The only miss is "What's your name?" during a running order check. It gives `continue` with the
running query where the case expected `new`: the question goes unanswered and the order check carries on,
rather than being restarted. It isn't a correction or a cancellation, so the §11.7 gate passes. Answering side
questions while the task keeps running is already a follow-up (§10).

**Latency.** The 12.5 s outlier would exceed `timeout_ms` (4 s) live and fall back to `cancel_and_merge`.

**Deployment.** After the replay, `fba-voice-web` and `fba-voice-verdictspk` were restarted on this code.
