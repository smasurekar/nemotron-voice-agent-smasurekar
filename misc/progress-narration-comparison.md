# Progress Narration: `frontend_backend_agent` vs. Coding Agents (Codex / Claude Code)

How do agents avoid leaving the user in silence while slow work runs? Three production systems
solve the same problem three different ways. This document compares them, with full file
paths, and ends with a concrete recommendation.

---

## Summary

All three systems answer the same question — *"say something while the slow thing happens"* —
but they differ on **where the text comes from**, **who decides to show it**, and **when it is
committed**.

| | Codex | Claude Code | `frontend_backend_agent` |
|---|---|---|---|
| Narration is… | free-form assistant text | free-form assistant text | a **tool argument** (`filler_text`) |
| Channel | same stream as the final answer | same stream as the final answer | separate, harness-synthesized TTS frames |
| Committed… | **after** prior tool results are known | **after** prior tool results are known | **before** any work starts |
| Who decides to emit | the model | the model | the **harness**, after a latency timer |
| Suppressible on fast paths | no | no | **yes** (0.3 s gate) |
| Agents involved | 1 | 1 | **2** (Talker + Thinker) |
| Prompt instruction | *"send a brief preamble"* | announce-then-act (implied) | *"Never say progress text … as normal text"* |
| Output medium | terminal text | terminal text | **speech (TTS)** |

The headline: **Codex explicitly requires the behavior that this example explicitly forbids.**
Both are correct for their medium.

---

## Part 1 — How the coding agents do it

### Codex

One agent, one response. The system prompt asks for the preamble, the model emits it as an
assistant-text item ahead of the tool-call items, and the TUI paints it from streamed deltas
before any tool executes.

Paths are relative to `/home/smasurekar/Desktop/Swapnil/github_repos/codex`:

| File | Line | Role |
| --- | --- | --- |
| `codex-rs/protocol/src/prompts/base_instructions/default.md` | `:31` | `### Preamble messages` — the instruction |
| `codex-rs/protocol/src/prompts/base_instructions/default.md` | `:37` | "Build on prior context… create a sense of momentum" |
| `codex-rs/protocol/src/prompts/base_instructions/default.md` | `:173` | `## Sharing progress updates` |
| `codex-rs/protocol/src/models.rs` | `:800` | `MessagePhase::Commentary` vs `FinalAnswer` |
| `codex-rs/codex-api/src/sse/responses.rs` | `:360` | SSE `response.output_text.delta` → `ResponseEvent::OutputTextDelta` |
| `codex-rs/core/src/session/turn.rs` | `:2554` | delta handler → `emit_streamed_assistant_text_delta` |
| `codex-rs/core/src/session/turn.rs` | `:281` / `:482` | agentic loop / exit on `!needs_follow_up` |
| `codex-rs/tui/src/chatwidget/protocol.rs` | `:78` | renders the delta in the terminal |

Full write-up: `/home/smasurekar/Desktop/Swapnil/github_repos/codex/misc/preamble-messages-explained.md`

### Claude Code

Identical mechanism on the Anthropic Messages API: one assistant message carries a `text`
content block followed by `tool_use` blocks; `content_block_delta` events render the sentence
before any tool runs; the loop repeats until `stop_reason == "end_turn"`. The CLI is
closed-source, so the instruction itself is not readable — only the protocol behavior.

---

## Part 2 — How `frontend_backend_agent` does it

Paths relative to
`/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar/src/examples/frontend_backend_agent`.

### 2.1 The narration is a tool parameter

`airline/tools.py:29-36`

```python
"filler_text": {
    "type": "string",
    "description": (
        "A brief, natural filler sentence to speak only if internal booking/search/PNR work "
        "takes longer than the configured latency threshold. Do not include final answers or "
        "specific results."
    ),
},
```

The model emits it **inside** the call, with empty assistant content (`prompts.yaml:140-151`):

```jsonc
{ content: "",
  tool_calls: [{ function: { name: "call_backend", arguments: {
      query: "The user wants help booking a flight from New York. …",
      filler_text: "Let me pull that up."
  }}}]}
```

Codex's equivalent turn would be `content: "Let me pull that up."` **plus** the tool call.

### 2.2 The prompt bans free-form narration

`prompts.yaml` — the "Hard tool-call contract":

| Line | Rule |
| --- | --- |
| `:33` | `Hard tool-call contract:` |
| `:34-35` | the assistant message must contain a `call_backend`/`cancel_backend` call and **no normal assistant text before or after** |
| `:36-37` | text that says "I'll search", "please hold", "this may take a moment" is **invalid** |
| `:42` | "The only user-facing progress text before the tool result is optional `filler_text` inside the tool call." |
| `:75` | "Never say progress text such as 'I'll search', 'I'll check', or 'let me look that up' as normal text." |
| `:83` | `filler_text` is "a candidate latency filler, not guaranteed immediate speech" |
| `:87` | "keep `filler_text` generic; do not include PNRs, flight IDs, passenger names, routes, dates, or guessed details" |

### 2.3 It is latency-gated and frequently never spoken

`src/tool_handlers.py:78-102`

```python
async def emit_filler_after_threshold() -> None:
    await asyncio.sleep(filler_threshold_seconds)      # default 0.3s
    await _emit_talker_response(params.llm, filler_text)

async def schedule_thinker_started_filler(event):      # fires on ThinkerStarted
    if not allow_talker_frames or event.marker != "ThinkerStarted" or not filler_text:
        return
    filler_task = asyncio.create_task(emit_filler_after_threshold())

try:
    payload = await thinker.call(query, slots=slots, on_started=schedule_thinker_started_filler)
finally:
    await _cancel_pending_filler(filler_task)          # :170 — races the backend and loses
```

If the backend answers in under `THINKER_FILLER_THRESHOLD_SECONDS`, the filler is cancelled
and never reaches TTS. **Codex has no equivalent gate.**

### 2.4 The harness fabricates the speech

`src/tool_handlers.py:156-167`

```python
async def _emit_talker_response(llm, text: str) -> None:
    await llm.push_frame(LLMFullResponseStartFrame())
    await llm.push_frame(LLMTextFrame(text=text))
    ...
    await llm.push_frame(LLMFullResponseEndFrame())
```

Codex only *forwards* deltas it received. This pipeline **manufactures** them — that is what
buys the deterministic timing control.

### 2.5 Two models, and no agentic loop at the front

| File | Line | Role |
| --- | --- | --- |
| `pipeline.py` | `:207` / `:216` | **Talker LLM** — user-facing, fast, sees only `call_backend` / `cancel_backend` |
| `pipeline.py` | `:254` | `NvidiaThinkerPlanner` — the **Thinker LLM** |
| `pipeline.py` | `:260` | `ThinkerBackend(backend=HTTPBookingBackend(...), planner=...)` |
| `pipeline.py` | `:71` | `THINKER_FILLER_THRESHOLD_SECONDS` default `0.3` |
| `pipeline.py` | `:72` | `THINKER_TOOL_TIMEOUT_SECONDS` default `30.0` |
| `pipeline.py` | `:274-276` | `build_handlers(thinker, filler_threshold_seconds=…)` |
| `pipeline.py` | `:303` / `:310` | `cancel_on_interruption` wiring (Realtime vs cascaded) |
| `pipeline.py` | `:407` | the Pipecat pipeline: transport → STT → Talker → TTS → transport |
| `airline/thinker.py` | `:65` | `ThinkerBackend.call()` |
| `airline/thinker.py` | `:131` | `_run_call` — the backend's own multi-step dispatch |
| `airline/thinker.py` | `:181` | `_dispatch_parallel_tool_calls` |
| `airline/backend.py` | — | `HTTPBookingBackend` → `booking-server` sidecar |
| `airline/database/` | — | the booking-server sidecar (FastAPI + seeded data) |

The frontend issues **one** delegation per user turn and blocks. The multi-step loop lives
inside the Thinker, invisible to the Talker. Codex instead keeps the *same* model in the loop
(`turn.rs:281`), which is precisely why it can write context-aware preambles.

### 2.6 Cancellation is first-class

| File | Line | Role |
| --- | --- | --- |
| `src/protocol.py` | `:12` | `LifecycleMarker = ThinkerStarted \| IntermediateResponse \| ThinkerCompleted \| ThinkerAborted` |
| `src/protocol.py` | `:15-39` | `ThinkerLifecycleEvent` |
| `src/protocol.py` | `:42` / `:68` | `response_hint()` / `tool_result()` payload builders |
| `src/protocol.py` | `:90` | `is_speakable_payload()` |
| `airline/tools.py` | `:44` | `CANCEL_BACKEND_TOOL` |
| `src/tool_handlers.py` | `:136` | `handle_cancel_backend` |
| `src/tool_handlers.py` | `:103-116` | aborted call → `run_llm=False`, nothing is spoken |
| `airline/thinker.py` | `:108` | `cancel_active()` |
| `airline/thinker.py` | `:118` | `cancel_pending_booking()` |

README rationale: *"Treat cancellation as a required path… This prevents stale backend results
from reaching the user later."* Voice users barge in mid-sentence; terminal users do not.

### 2.7 Optional direct-response bypass

`src/tool_handlers.py:130-134` and `:192`

```python
if allow_talker_frames and _direct_tool_response_enabled() and is_speakable_payload(payload):
    await _emit_talker_response(params.llm, str(payload.get("response_text") or ""))
    await params.result_callback(payload, properties=FunctionCallResultProperties(run_llm=False))
    return
```

With `FRONTEND_BACKEND_DIRECT_TOOL_RESPONSE=1`, the backend's `response_text` is spoken
directly and the **second Talker inference is skipped entirely**. No analogue in Codex.

---

## Part 3 — Advantages of the `frontend_backend_agent` design

### 3.0 The main one: a swappable backend behind a domain-free voice layer

This is the structural payoff, and it is the reason the two-model cost is worth paying at all.
Everything else in this section is a tactic; this is the architecture.

The README states the intent directly:

> "The airline backend agent is the reference backend, but the architecture is reusable: treat
> the frontend LLM as a generic conversational layer in front of another backend agent that
> exposes compatible call/cancel behavior."

Voice is the hard, expensive, heavily-tuned part of the stack — ASR, endpointing, barge-in,
TTS pronunciation, latency budgets, turn aggregation. That work is **domain-independent**, and
it should be built once. This design makes that literal: the Talker never learns what a PNR
is, never sees `flight_search`, never holds booking state. Swap the backend and you keep the
entire voice layer, the latency gate, the cancellation semantics, and the tuning.

Compare Codex: there is exactly one model and it must hold the domain, the tool surface, and
the user-facing voice simultaneously. There is nothing to swap — changing the domain means
re-prompting the same agent that also owns the conversation.

### 3.1 How narrow the swap surface actually is

Three methods and two payload shapes. That is the whole contract.

`src/tool_handlers.py:25-41` — the runtime interface a backend must satisfy:

```python
class ThinkerBackend(Protocol):
    """Minimal runtime interface required by the frontend tool handlers."""

    async def call(self, query: str, slots: dict | None = None, *,
                   on_started: Callable[[ThinkerLifecycleEvent], Awaitable[None]] | None = None,
                   ) -> dict[str, Any]: ...
    def cancel_active(self, reason: str = "new_user_query") -> bool: ...
    def cancel_pending_booking(self) -> bool: ...
```

`src/protocol.py:42-92` — the two payload shapes it must return:

| Builder | Line | Meaning |
| --- | --- | --- |
| `response_hint(...)` | `:42` | intermediate — missing info, unsupported request, cancellation |
| `tool_result(...)` | `:68` | final — a completed domain action |
| `is_speakable_payload(...)` | `:90` | gate: has a `type` and a non-empty `response_text` |

Both carry a `response_text` the Talker relays verbatim. A new domain implements
`call` / `cancel_active` / `cancel_pending_booking`, returns those two dicts, and inherits the
entire voice stack unchanged. `airline/thinker.py:65-129` is just the reference implementation
of that Protocol.

Note what is **not** in the contract: no tool schemas, no state machine, no LLM requirement at
all. A backend could be a rules engine, a workflow service, or a remote agent over HTTP —
`airline/backend.py`'s `HTTPBookingBackend` already demonstrates the network case.

### 3.2 Caveat: swappable in principle, not yet in packaging

Worth being accurate about how much work a real swap is today. The *runtime contract* above is
genuinely narrow. The *repo layout and prompt* are not yet aligned with it:

| Issue | Evidence |
| --- | --- |
| The Talker prompt is heavily airline-coupled | **87 of 317** lines of the `talker` prompt (`prompts.yaml:4-321`) mention flight / PNR / booking / airport / seat / meal / passenger |
| The generic tool schema lives under the domain package | `call_backend` / `cancel_backend` are domain-free, but `TOOLS_SCHEMA` sits in `airline/tools.py`, not `src/` |
| The "reusable" layer imports the domain layer | `src/tts_filter.py:12` imports `airline.airports`; `src/planner.py:120` hardcodes `schema_name="airline_plan"` |

So a second domain today means rewriting the Talker prompt and untangling three imports — not
reimplementing the voice pipeline. That is still a large win, but the advertised
"generic conversational layer" is an architectural property the code has not fully cashed in.
Moving `tools.py` into `src/`, parameterizing `schema_name`, making the TTS filter
injectable, and splitting the Talker prompt into a generic contract plus a domain appendix
would close the gap — and those are small, mechanical changes.

### 3.3 The remaining advantages

1. **No awkward filler on fast paths.** "Let me pull that up… here are your flights" 200 ms
   apart sounds broken in speech. The gate at `src/tool_handlers.py:80` removes it. Codex
   cannot do this.
2. **Deterministic timing.** The filler fires exactly when configured, not whenever the model
   felt chatty. Tunable per deployment via `THINKER_FILLER_THRESHOLD_SECONDS`.
3. **Small fast model in front, large model behind.** The Talker only needs tool-routing
   competence → low time-to-first-audio. Planning cost moves to a separately configurable
   `thinker-llm` service entry.
4. **Speech safety by construction.** `prompts.yaml:87` forbids PNRs, flight IDs, passenger
   names, routes, and dates in the filler. Because the filler is committed *before* any result
   exists, it structurally cannot leak or hallucinate one. A mid-stream Codex preamble can.
5. **Stale results can never be spoken.** `ThinkerAborted` + `run_llm=False`
   (`src/tool_handlers.py:103-116`) suppresses output from an abandoned call.
6. **Testable protocol.** `response_hint` / `tool_result` (`src/protocol.py:42-87`) are
   structured payloads you can assert on; free-form narration is not.
7. **Latency bypass available** — `FRONTEND_BACKEND_DIRECT_TOOL_RESPONSE` skips a whole
   inference (§2.7).

## Part 4 — Disadvantages of the `frontend_backend_agent` design

1. **The filler is pre-committed, therefore generic.** Written before the work starts, it can
   never do what Codex's `default.md:37` asks — "connect the dots with what's been done so
   far." You get "Give me one moment," never "Found six flights; now checking seat
   availability."
2. **Exactly one filler per delegation.** A 25-second backend run yields one sentence and then
   silence. `IntermediateResponse` exists as a marker (`src/protocol.py:12`,
   `airline/thinker.py:139`) but is **never routed to TTS**.
3. **Brittle prompt contract.** The frontend must emit `content: ""` *and* a well-formed tool
   call on every flight turn. The README dedicates a section — *"Prompt edits can silently
   break the architecture contract"* — with a six-point re-test checklist.
   `_normalize_arguments` (`src/tool_handlers.py:179-189`) exists purely to recover from LLMs
   that wrap the payload under `original_args`.
4. **Lossy handoff.** The Talker must restate the request as a self-contained `query`;
   `prompts.yaml:90-110` spends ~20 lines on failure modes (delta-only queries, blended PNR
   corrections, "add this to the previous booking"). A single-agent loop has no handoff to lose
   information across.
5. **Two inferences per turn** — more cost, more tail latency, more failure modes.
6. **The frontend is blind to results.** It relays `response_text` and cannot reason over what
   the backend found. That is why `cancel_backend` must exist as an explicit tool.
7. **Unavailable in Realtime mode.** `build_handlers`' docstring
   (`src/tool_handlers.py:52-55`): Realtime sessions set `allow_talker_frames=False`, because a
   delegated tool must close Response A and publish its correlated `function_call_output`
   before Response B can be created. **No filler at all on that path.**
8. **More moving parts**: 2 LLMs + planner + HTTP sidecar + seeded database, versus one model
   and a loop.

## Part 5 — Advantages / disadvantages of the coding-agent design

**Advantages**

1. **Context-aware.** Generated *after* prior tool results are known, so it carries real signal.
2. **Free.** No second model, no extra call, no extra schema surface.
3. **Self-pacing.** The agentic loop naturally re-narrates at every iteration, so long tasks
   stay narrated throughout (`turn.rs:281`).
4. **No handoff loss** — the same model holds the full context end to end.
5. **Simple.** One prompt section and a streaming renderer.

**Disadvantages**

1. **No latency gate.** It narrates a 30 ms file read exactly as eagerly as a 30 s build.
2. **Non-deterministic.** The model may skip the preamble or over-narrate; the prompt can only
   nudge (`default.md:39` has to carve out an explicit exception for trivial reads).
3. **Not speech-safe.** Mid-stream text can name specifics before they are confirmed — fine to
   skim in a terminal, bad to hear asserted aloud.
4. **No cancellation semantics.** Nothing prevents stale narration once it is on screen.
5. **Unusable as-is for voice** — no barge-in handling, no TTS gating, no abort path.

---

## Part 6 — Recommendation

**The frontend/backend split is the right call for a voice agent — keep it.** Its main
justification is §3.0: the voice layer is the expensive, heavily-tuned part of the stack, it is
domain-independent, and this design lets you build it once and swap domains behind a
three-method contract. That, not the filler mechanism, is what the architecture is buying.
The latency gate is the best single tactic in either codebase and Codex should copy it.

**`filler_text` as an LLM-generated tool argument is the piece worth replacing.** The prompt
requires it to be content-free (`prompts.yaml:87`: no PNRs, flight IDs, passenger names,
routes, dates, or guessed details). So the system spends model attention, two schema
properties, and meaningful prompt-contract brittleness to produce "Let me pull that up." A
static rotation of eight phrases would be indistinguishable to the user at zero token cost.

*Counter-argument worth weighing:* an LLM-generated filler can match the register and the
**language** of the turn, which matters for multilingual TTS. If that is the driving
requirement, the mechanism is defensible — but it is a narrow justification for this much
prompt-contract weight.

### Suggested evolution

1. **Keep the latency gate** exactly as is (`src/tool_handlers.py:80`).
2. **Source the text from the backend, not the frontend's guess.** The plumbing already
   exists: `IntermediateResponse` is defined at `src/protocol.py:12` and emitted at
   `airline/thinker.py:139` — it is simply never routed to TTS. Wiring it up turns "One
   moment" into "Found six options; checking seats."
3. **Static fallback** for the first ~1 s, before the backend has anything to report. This
   covers the window `filler_text` covers today without asking the Talker to produce it.
4. **Allow a second update** on long runs, so a 25-second backend call is not one sentence
   followed by silence.

That yields Codex-grade informativeness inside a voice-appropriate architecture, and removes
the most fragile clause from the frontend prompt.

### One-line verdict

The architecture: **right, keep it.** The latency gate: **best idea here, Codex should adopt
it.** `filler_text`: **the part to change** — either downgrade it to a static pool, or upgrade
it to backend-sourced progress. Today it occupies the worst middle position — it *costs* like
generated text but *informs* like a constant.

---

## Part 7 — Choosing between the two styles

| Situation | Use |
| --- | --- |
| Text/terminal agent, long open-ended multi-step tasks | Codex / Claude Code style — free, context-aware, self-pacing; over-narration costs only a glance |
| Voice agent, turn-shaped tasks, hard latency budget | `frontend_backend_agent` style — silence is unacceptable, but so is redundant speech |
| Voice agent, long-running backend work | hybrid: latency gate + backend-sourced progress updates (Part 6) |

---

## Appendix — Complete file reference

### `frontend_backend_agent`
Root: `/home/smasurekar/Desktop/Swapnil/github_repos/nemotron-voice-agent-smasurekar/src/examples/frontend_backend_agent`

| File | Key lines |
| --- | --- |
| `README.md` | architecture, tips, "Re-test tool-calling accuracy after prompt changes" |
| `pipeline.py` | `:71-72` thresholds, `:207`/`:216` Talker, `:254` planner, `:260` Thinker, `:274` handlers, `:303`/`:310` cancel-on-interruption, `:407` pipeline |
| `prompts.yaml` | `:4` `talker`, `:33` hard contract, `:42` filler-only rule, `:75` no-narration rule, `:83-87` filler constraints, `:140-151` example, `:322` `thinker` |
| `src/tool_handlers.py` | `:44` `build_handlers`, `:52-55` Realtime caveat, `:78-97` filler scheduling, `:130-134` direct response, `:156` frame synthesis, `:170` filler cancel, `:179` arg normalization, `:192` env flag |
| `src/protocol.py` | `:12` lifecycle markers, `:15` event, `:42`/`:68` payload builders, `:90` speakability |
| `src/planner.py` | `NvidiaThinkerPlanner` |
| `src/tts_filter.py` | pronunciation transforms |
| `src/runtime_context.py` | `runtime_today()` |
| `airline/tools.py` | `:10` `call_backend`, `:29` `filler_text`, `:44` `cancel_backend`, `:63` schema |
| `airline/thinker.py` | `:65` `call`, `:108` `cancel_active`, `:118` `cancel_pending_booking`, `:131` `_run_call`, `:139` `IntermediateResponse`, `:156` `_dispatch`, `:181` parallel dispatch |
| `airline/backend.py` | `HTTPBookingBackend` |
| `airline/state.py`, `airline/transform.py`, `airline/plan_parsing.py`, `airline/slot_parsing.py` | backend state and parsing |
| `airline/flight_search.py`, `airline/booking_tool.py`, `airline/pnr_status.py` | domain tools |
| `airline/database/` | booking-server sidecar (`server.py`, `api.py`, `schema.sql`, `seed.py`) |
| `services.local.yaml`, `services.cloud.yaml` | service catalog incl. `thinker-llm`, `booking-server` |

### Codex
Root: `/home/smasurekar/Desktop/Swapnil/github_repos/codex` — see
`misc/preamble-messages-explained.md` in that repo for the full index.

### Claude Code
Root: `/home/smasurekar/Desktop/Swapnil/github_repos/claude-code` — no source ships in the
repo; the CLI is a bundled `cli.js` in `@anthropic-ai/claude-code`. Announce-then-act appears
as few-shot examples in `plugins/pr-review-toolkit/agents/*.md` and
`plugins/plugin-dev/skills/agent-development/examples/`.
