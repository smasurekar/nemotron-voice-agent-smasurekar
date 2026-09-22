# Frontend/Backend Agent — Deep Dive

How the `src/examples/frontend_backend_agent/` example works, why it exists, and exactly how the
Talker/Thinker split is executed — with no agentic framework involved.

All paths below are relative to `src/examples/frontend_backend_agent/` unless stated otherwise.

---

## 1. The motive: why split frontend from backend at all?

### The problem

A cascaded voice agent is a latency budget, not an architecture diagram:

```
mic → ASR → LLM → TTS → speaker
```

Every millisecond the LLM spends thinking is silence on the call. Humans tolerate ~300–500 ms of
silence before a conversation feels broken. But real task work — searching flights, hitting a
database, validating a booking, checking policy — takes **seconds**, not milliseconds.

So you get a conflict:

| You want | But |
|---|---|
| A model that replies in < 500 ms | Small/fast models are bad at multi-step planning |
| A model that plans and calls tools correctly | Reasoning models are slow and stream badly into TTS |
| Tool calls that hit a real backend | Network + DB latency is unavoidable |

A single LLM cannot be both. Any single-LLM design forces you to pick which one you sacrifice.

### The second problem (arguably the real one)

Most teams adopting voice **already have a text agent**. A support agent, a booking agent, a RAG
service, a LangGraph/CrewAI/custom workflow that already works, is already tested, already has
guardrails and an owner. Rewriting it as a voice-shaped pipecat pipeline is a non-starter.

### The answer

Split responsibilities across two LLMs with completely different jobs:

```
                  ┌──────────────────────── FRONTEND (user-facing) ────────────────────────┐
  user audio ──▶ ASR ──▶ Talker LLM ──▶ TTS ──▶ user audio
                           │  ▲
        call_backend(query)│  │ response_text
                           ▼  │
                  ┌───────────┴──────────── BACKEND (never speaks) ────────────────────────┐
                    Thinker planner LLM ──▶ python tools ──▶ HTTP booking-server ──▶ SQLite
```

- **Frontend (Talker)** — owns *conversation*. Fast, non-reasoning, small context, TTS-friendly
  phrasing, barge-in handling, persona. It is the **only** component whose words reach the user.
- **Backend (Thinker)** — owns *work*. Reasoning enabled, intent detection, slot extraction,
  domain policy, state machine, real tool execution. It **never speaks to the user**; it returns
  structured payloads.

The boundary between them is one natural-language string in, one JSON payload out. That is
deliberately the narrowest possible contract, because it means **the backend can be anything** —
this repo happens to ship an airline booking agent, but you could put your existing text agent,
an HTTP microservice, or a LangGraph app behind the same boundary and change nothing on the
frontend side. That is the whole point of the pattern: *add voice to an existing agent without
rewriting it.*

### What this buys you

1. **Perceived latency decoupled from real latency.** The Talker can speak within ~300 ms while the
   backend is still working (see filler mechanics, §5).
2. **Model right-sizing.** Talker runs `enable_thinking: false`; Thinker runs
   `enable_thinking: true, thinking_budget: 1024`. Two different configurations of the same model
   family (`services.cloud.yaml`), or two entirely different models/endpoints.
3. **Blast radius containment.** The Talker has no database credentials, no domain state, no
   knowledge of flights. Prompt-injecting the Talker gets you nothing but words.
4. **Independent iteration.** You can rewrite the whole booking flow without touching the voice
   prompt, and vice versa.
5. **Testability.** The backend is plain async Python with a `Protocol` boundary — unit-testable
   without audio, without a pipeline (`tests/unit/test_frontend_backend_agent.py`).

### What it costs you

- Two LLM calls per task turn (three if the Talker re-runs to phrase the result) instead of one.
- A prompt-contract you must keep in sync — the README warns explicitly that prompt edits can
  silently break routing, and tells you to re-test both layers.
- The Talker can paraphrase backend results badly unless constrained (hence the very long
  "preserve flight IDs, prices, PNRs" rules in `prompts.yaml`).

---

## 2. Correcting the mental model: how Talker and Thinker actually relate

You guessed:

> there is one talker LLM, which responds to query quickly and thinker LLM later gives final response

That is the right *intuition* about the user experience, but the mechanism is different, and the
difference matters a lot if you plan to extend this.

**The Thinker is not a background process that injects a late answer.** It is a **tool call, awaited
inline, within a single Talker turn.**

Concretely:

- The Thinker is exposed to the Talker as exactly one function: **`call_backend(query, filler_text)`**.
  Yes — to answer your question directly: **the Thinker LLM (plus its whole tool stack) *is* one of
  the Talker's tools.** It is the *only* substantive one.
- When the Talker calls it, pipecat runs the registered handler and the turn blocks on
  `await thinker.call(...)` (`src/tool_handlers.py:97`).
- The "fast response" is **not** a separate Talker answer. It is the **`filler_text` argument the
  Talker wrote inside the same tool call**, which the handler speaks *only if* the backend takes
  longer than a threshold (default 0.3 s).
- When the Thinker returns, its payload goes back as the tool result, and the Talker is re-invoked
  to phrase it. That phrasing is the final spoken answer.

So the sequence is: **one Talker inference → (maybe) a canned filler → backend work → second Talker
inference that speaks the result.** Not two independent LLMs racing.

```
t=0.00  user stops speaking
t≈0.25  Talker inference #1 completes → emits tool_call call_backend(query="…", filler_text="Let me pull that up.")
t=0.25  handler starts thinker.call(), schedules filler timer for +0.30s
t=0.55  backend still busy → filler_text is pushed as LLMTextFrame → TTS → "Let me pull that up."
t=1.90  Thinker returns {"type":"tool_result", "response_text":"I found 3 flights: …"}
t=1.90  filler timer cancelled if it had not fired
t=1.90  tool result appended to Talker context → Talker inference #2
t≈2.15  Talker speaks the final answer, lightly rephrased
```

If the backend answers in under 0.3 s, the filler never fires and the user hears only the real
answer. That is the whole trick: **the filler is a hedge, not a stage.**

> There is an escape hatch. Set `FRONTEND_BACKEND_DIRECT_TOOL_RESPONSE=1` and the handler speaks the
> backend's `response_text` verbatim and suppresses Talker inference #2 with
> `FunctionCallResultProperties(run_llm=False)` (`src/tool_handlers.py:120-123`). You trade natural
> phrasing for one less LLM round-trip.

---

## 3. Tool inventory — who can call what

This is a strict two-level hierarchy. Neither level can see the other's tools.

### Tools available to the **Talker** (frontend LLM)

Declared in `airline/tools.py`, attached to the context at `pipeline.py:259` via
`LLMContext(messages, tools=TOOLS_SCHEMA, tool_choice="auto")`.

| Tool | Arguments | Purpose |
|---|---|---|
| `call_backend` | `query` (required, string), `filler_text` (optional, string) | Hand a self-contained natural-language request to the backend agent. |
| `cancel_backend` | *none* | Abort in-flight backend work and clear pending search/booking state. |

That is the complete list. **Two tools. No domain tools. No database access. No flight vocabulary
at the tool layer at all.** The Talker cannot search a flight even if it wanted to.

Note what is *not* in the schema: no `origin`, `date`, `pnr_code`, `seat_pref`, no `intent`. The
prompt explicitly forbids the Talker from inventing structured fields
(`prompts.yaml`, "call_backend arguments" section). Slot extraction is the backend's job, on
purpose — it keeps the fast model from having to be good at parsing.

### Tools available to the **Thinker** (backend planner LLM)

Not an OpenAI tool schema at all — these are described in prose in the `thinker` prompt, and the
planner returns JSON naming one of them. Dispatched in `airline/thinker.py:236-250`.

| Tool | Implementation | Required params | What it does |
|---|---|---|---|
| `flight_search` | `airline/flight_search.py` | `origin_airport`, `dest_airport`, `date` (opt: `sorting`) | `GET /flights` on booking-server; stores top 5 in session state. |
| `booking` | `airline/booking_tool.py` (`BookingTool`) | a flight selected from prior search results | Runs the booking state machine; `POST /pnrs` on confirm. |
| `pnr_status` | `airline/pnr_status.py` | `pnr_code` | `GET /pnrs/{pnr}` on booking-server. |
| `response_hint` | `airline/thinker.py:252` (`_planner_response_hint`) | `reason`, `action`, `context`, `response_text` | Not a tool at all — the planner's way of saying "ask the user for X" or "I can't do that". |

The Thinker's tools are the ones that touch the network and mutate state. The Talker's tools are
the ones that touch the conversation. Clean split.

### Tools available to the booking-server

None — it is a plain FastAPI + SQLite service (`airline/database/server.py`), the leaf of the tree.
It exposes `/flights`, `/pnrs`, `/pnrs/{pnr}`, `/pnrs/{pnr}/rebook`, `/cancel`, `/standby`,
`/price`, `/health`. The Thinker only uses three of them; the rest exist so you can extend the
example.

---

## 4. "No agentic framework" — what that actually means here

There is no LangChain, no LangGraph, no CrewAI, no AutoGen, no `AgentExecutor`. There isn't even a
`while True:` agent loop. What you get instead is four plain-Python mechanisms:

### 4.1 Tool calling = pipecat function registration

```python
# pipeline.py:203-214
for name, handler in build_handlers(thinker, filler_threshold_seconds=...).items():
    cancel_on_interruption = name != "call_backend"
    talker_llm.register_function(
        name, handler,
        cancel_on_interruption=cancel_on_interruption,
        timeout_secs=THINKER_TOOL_TIMEOUT_SECONDS,   # 30 s
    )
```

Pipecat's `NvidiaLLMService` already does the OpenAI tool-calling round-trip: it sends the schema,
parses `tool_calls` out of the response, invokes the registered coroutine, appends the result to
context, and re-runs the LLM. That *is* the agent loop. There is nothing to add.

Note `cancel_on_interruption=False` for `call_backend`: a barge-in does **not** kill in-flight
backend work. Cancellation is explicit (§6) rather than implicit, so a user talking over the filler
doesn't silently throw away a booking.

### 4.2 The backend "agent" = one LLM call returning JSON

`src/planner.py` is the entire planner. It is ~40 lines of logic:

```python
# src/planner.py:47-66 (condensed)
user_payload = {
    "query": query,                       # the Talker's rephrased request
    "structured_fields": slots,           # usually empty
    "session_state": state,               # search_context, search_results, booking_draft, flags
    "runtime_context": {"today": ..., "tomorrow": ...},
}
context = LLMContext([
    {"role": "system", "content": thinker_prompt + runtime_date_context},
    {"role": "user",   "content": json.dumps(user_payload)},
])
raw = await self._llm.run_inference(context, max_tokens=self._max_tokens)
return parse_plan_json(raw)
```

**Single-shot, stateless, no conversation history.** Every Thinker call gets a fresh two-message
context. All continuity comes from `session_state`, which is ordinary Python dataclass state
(`airline/state.py`), not from a growing message list. This is what makes the backend cheap and
deterministic despite being LLM-driven.

`parse_plan_json` (`src/planner.py:69`) is the defensive layer: strips `<think>…</think>` blocks,
strips ``` fences, and if `json.loads` still fails, falls back to slicing between the first `{` and
last `}`.

### 4.3 Plan execution = a dict lookup

```python
# airline/thinker.py:236-250
if tool_name == "booking":       return await BookingTool(...).continue_booking(planned_slots)
if tool_name == "pnr_status":    return await pnr_status(backend=..., slots=planned_slots)
if tool_name == "flight_search": return await flight_search(state=..., backend=..., slots=...)
if tool_name == "response_hint": return self._planner_response_hint(tool_call)
return response_hint(reason="unsupported_request", ...)
```

An if-chain with a safe default. The LLM proposes; Python disposes. An unknown tool name degrades
to a spoken "I can help search flights, book a selected flight, or check a PNR status" instead of
crashing.

### 4.4 The wire format = two dataclass-free constructors

`src/protocol.py` defines the *only* two payload shapes that may cross back to the Talker:

```python
response_hint(reason=..., action=..., response_text=..., context=..., params_needed=[...])
tool_result(tool=..., status=..., data={...}, response_text=..., context=...)
```

`response_hint` = "I need something from the user" or "I can't do that."
`tool_result`   = "I did the work, here is the outcome."

Both carry `response_text`, the single field the Talker is allowed to speak from.
`is_speakable_payload()` gates it: no `response_text`, no speech.

There is also `ThinkerLifecycleEvent` with markers `ThinkerStarted` / `IntermediateResponse` /
`ThinkerCompleted` / `ThinkerAborted`, capped at 200 entries per session. These are **internal
observability only** — the Talker prompt explicitly forbids ever speaking them.

### Why hand-rolled instead of a framework?

- The control flow is one level deep. A graph framework would add a dependency and an abstraction
  for a call that is literally `await`.
- Latency budget. Every framework layer is Python overhead in a path that must finish in ~300 ms.
- Determinism where it matters. Booking is a hand-written state machine
  (`airline/booking_tool.py`), not LLM-driven. The LLM decides *which* tool; the state machine
  decides *what is legal*. Booking is hard-gated: no search → no selection → no booking.
- Portability. The backend is `async def` + a `Protocol`. Swapping `HTTPBookingBackend` for your
  own agent is one constructor argument at `pipeline.py:190`.

---

## 5. Execution walkthrough — one full turn

User says: *"Find me a flight from New York to San Francisco on June seventh."*

**1. ASR** — `NvidiaSTTService` streams the transcript; the user aggregator finalizes the turn.

**2. Talker inference #1** — Context = talker prompt + runtime date context + last 20 turns
(`CHAT_HISTORY_RECENT_TURNS`, sliding window applied at `pipeline.py:286`). The prompt's hard
contract says: flight-related turn ⇒ emit a tool call, `content` must be `""`. Output:

```json
{"name": "call_backend",
 "arguments": {"query": "The user wants flights from New York (JFK) to San Francisco on June 7, 2026.",
               "filler_text": "Let me pull that up."}}
```

**3. Handler entry** (`src/tool_handlers.py:47`) — extracts `query`, pulls `filler_text` out, and
treats every *other* argument as `slots` (legacy compatibility path; normally empty).
`_normalize_arguments` first repairs the case where the model wrapped everything in `original_args`.

**4. Lifecycle + filler timer** — `ThinkerBackend.call` (`airline/thinker.py:65`) mints a 12-hex
`call_id`, cancels any previous in-flight call, records `ThinkerStarted`, and fires the
`on_started` callback. That callback schedules `emit_filler_after_threshold()` — an
`asyncio.sleep(0.3)` followed by pushing the filler through the normal
`LLMFullResponseStartFrame → LLMTextFrame → LLMFullResponseEndFrame` sequence so TTS treats it as
ordinary Talker speech.

**5. Planner inference** — the Thinker LLM, with reasoning on, returns:

```json
{"tool": "flight_search",
 "params": {"origin_city": "New York", "origin_airport": "JFK",
            "dest_city": "San Francisco", "dest_airport": "SFO", "date": "2026-06-07"}}
```

Note the prompt makes the Thinker normalize city names to IATA codes *before* the tool runs.

**6. Tool execution** — `flight_search` validates slots (missing ones → `response_hint` asking the
user), calls `HTTPBookingBackend.search_flights` → `GET /flights`, dedupes/sorts/truncates to 5,
writes `state.search_context` + `state.search_results`, resets any stale booking draft, and returns:

```json
{"type": "tool_result", "tool": "flight_search", "status": "success",
 "data": {...},
 "response_text": "I found 3 flights: G Force Airline's AA701 at 8:00 AM for $312, … Which flight would you like to book?",
 "context": "flight_search"}
```

**7. Return path** — `ThinkerCompleted` recorded; the `finally` block cancels the pending filler
task if it hasn't fired; the payload goes to `params.result_callback(payload)`.

**8. Talker inference #2** — Pipecat appends the tool result to context and re-runs the Talker. The
prompt instructs: *answer from `response_text`, you may lightly rephrase, but preserve flight IDs,
prices, and PNRs*, plus TTS formatting rules (12-hour times, "June 7, 2026" not ISO, no markdown).

**9. TTS** — Before synthesis, two filters run: `NemotronSpeechTextFilter` (shared), then
`apply_frontend_backend_agent_pronunciation_for_tts` (`src/tts_filter.py`), which spells out
airline artifacts so they're intelligible: `PNR` → `P N R`, `ABC123` → `A B C 1 2 3`,
`AA701` → `A A 7 0 1`, `JFK` → its spoken airport name, and strips list markers.

---

## 5.1 How `filler_text` actually reaches the speaker mid-call

This is the least obvious mechanism in the example, so it is worth spelling out. The short version:
**the filler is injected directly into the pipeline as frames, bypassing LLM inference entirely.**

### The pipeline is a graph of independent processors

```python
# pipeline.py:267
Pipeline([
    transport.input(), stt, user_aggregator,
    talker_llm,          # ← the tool handler runs inside this processor
    tts,                 # ← the next processor downstream
    transport.output(),
    *([audio_recorder] if audio_recorder else []),
    assistant_aggregator,
])
```

Each processor is its own asyncio task passing frames to the next. When the Talker emits a tool
call, pipecat invokes the registered handler and hands it `params.llm` — **a live reference to the
`NvidiaLLMService` object sitting in that graph**, not a client or an API wrapper.

So the handler can push frames from the LLM's position in the pipeline at any moment, including
while it is still awaiting the backend.

### The injection is a three-frame impersonation of an LLM response

```python
# src/tool_handlers.py:146
async def _emit_talker_response(llm, text: str) -> None:
    if _task_cancellation_requested():
        return
    started = False
    try:
        started = True
        await llm.push_frame(LLMFullResponseStartFrame())
        await llm.push_frame(LLMTextFrame(text=text))
    finally:
        if started:
            await llm.push_frame(LLMFullResponseEndFrame())
```

`push_frame` defaults to downstream, so the next processor to receive these is `tts`. Verified
against the pinned pipecat 1.5.0: `processors/frame_processor.py:724` declares
`direction: FrameDirection = FrameDirection.DOWNSTREAM`, and `services/tts_service.py:705,711`
shows `TTSService.process_frame` handling `LLMFullResponseStartFrame` and `LLMFullResponseEndFrame`
as the start/flush boundaries around buffered text.

| Frame | What TTS does with it |
|---|---|
| `LLMFullResponseStartFrame` | Begin a new response aggregation |
| `LLMTextFrame(text=...)` | The actual words to buffer/synthesize |
| `LLMFullResponseEndFrame` | Flush — synthesize now, don't wait for more tokens |

**That triple is byte-identical to what a real streaming inference emits.** TTS cannot tell the
difference, which is exactly the point: the filler needs no special-case handling anywhere
downstream. No inference happened — the text was already sitting in a local variable, written by
the Talker back at step 2 of the turn.

`tests/unit/test_frontend_backend_agent.py:855-860` asserts this exact sequence, including
`skip_tts is None` (so it *is* spoken) and `append_to_context is True`.

### Why it fires "mid-process": it is a concurrent task, not a step

```python
# src/tool_handlers.py:68-87 (condensed)
async def emit_filler_after_threshold():
    await asyncio.sleep(filler_threshold_seconds)   # 0.3 s
    await _emit_talker_response(params.llm, filler_text)

async def schedule_thinker_started_filler(event):
    if event.marker != "ThinkerStarted" or not filler_text:
        return
    if filler_started or (filler_task is not None and not filler_task.done()):
        return                                       # dedupe duplicate ThinkerStarted
    filler_started = True
    if filler_threshold_seconds <= 0:
        await _emit_talker_response(params.llm, filler_text)   # fire immediately
        return
    filler_task = asyncio.create_task(emit_filler_after_threshold())

try:
    payload = await thinker.call(query, slots=slots, on_started=schedule_thinker_started_filler)
finally:
    await _cancel_pending_filler(filler_task)
```

`asyncio.create_task` is the whole answer to "how does it happen mid-process". The handler's
`await thinker.call(...)` yields control to the event loop; the filler task's `asyncio.sleep(0.3)`
runs on that same loop concurrently. Whichever finishes first wins:

```
handler task:  ──await thinker.call(...)──────────────────────────▶ returns at t=1.90
filler task:        └─create_task─▶ sleep(0.3) ─▶ push 3 frames ──▶ TTS speaks at t=0.55
                                                    │
                                   if thinker had returned first, the
                                   finally block cancels this task mid-sleep
                                   and no frame is ever pushed
```

`ThinkerBackend.call` fires `on_started` *before* creating the work task
(`airline/thinker.py:88-92`), so the timer starts at the true beginning of backend work.

### The race is resolved in `finally`, not by checking a flag

```python
# src/tool_handlers.py:153
async def _cancel_pending_filler(task):
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
```

If the backend beat the threshold, the sleeping task is cancelled before it reaches its first
`push_frame`, so the user never hears a filler for a fast turn. The test at
`test_frontend_backend_agent.py:928` sets `filler_threshold_seconds=1.0` against a fast thinker and
asserts `llm.frames == []` — nothing was emitted at all.

### Three guards worth noting

- **Duplicate `ThinkerStarted`** — the `filler_started` flag plus the `task.done()` check mean a
  backend that fires the callback twice still produces exactly one filler
  (`_DoubleStartedThinker` test, line 899).
- **Pipeline teardown** — `_task_cancellation_requested()` checks `asyncio.current_task().cancelling()`
  and returns early, so frames are never pushed into a pipeline being shut down.
- **`finally` on the frame triple** — if `LLMTextFrame` raises, the `End` frame is still pushed, so
  TTS is never left waiting on an unterminated response.

### Where the filler ends up afterwards

`LLMTextFrame` carries `append_to_context=True`, and the `assistant_aggregator` is the **last**
processor in the pipeline — so it sees these frames too and records the filler as assistant speech
in `LLMContext`. That matters: when Talker inference #2 runs to phrase the backend result, it can
see it already said "Let me pull that up." and won't repeat the sentiment.

This is also why the `FRONTEND_BACKEND_DIRECT_TOOL_RESPONSE` path reuses the same
`_emit_talker_response` helper — a final answer and a filler travel through the identical
injection route; only the timing and the `run_llm=False` flag differ.

---

## 6. Cancellation, interruption, and concurrency

This is the subtle part of the design and where most of the non-obvious code lives.

### Stale-result suppression

If a user changes their mind mid-search, a late backend result arriving afterwards is worse than no
result — it makes the agent sound deranged. Three mechanisms prevent it:

**a. Explicit user cancellation → `cancel_backend`.** The prompt routes "stop", "never mind",
"cancel that" to `cancel_backend`, which calls both `cancel_active()` (cancels the asyncio task)
and `cancel_pending_booking()` (clears search/booking state), then speaks "Okay, I stopped that."

**b. Topic switch → also `cancel_backend`.** Non-obvious but deliberate: if flight work may still
be pending and the user asks "by the way, what's your name?", the prompt says call `cancel_backend`
rather than answer. The prompt even shows answering the small talk as the *incorrect* example,
with the rationale: *pending flight work can later inject stale results*.

**c. New call supersedes old.** `ThinkerBackend.call` (`airline/thinker.py:75-81`) cancels and
awaits any previous task before starting a new one. So a correction ("actually San Francisco")
implicitly kills the in-flight search.

When a call is cancelled, the handler catches `CancelledError` and returns a payload with
`response_text: ""` and `run_llm=False` (`src/tool_handlers.py:99-106`) — the Talker is *not*
re-invoked, so nothing is spoken about the aborted work.

### Parallel tool calls with a serialization guarantee

The Thinker prompt permits returning multiple independent `tool_calls`. Naively `gather()`-ing them
would corrupt state, because `flight_search` and `booking` both write `search_results` /
`booking_draft` (last-writer-wins). So `_dispatch_parallel_tool_calls`
(`airline/thinker.py:178-225`) partitions by a read-only set:

```python
_READ_ONLY_TOOLS = frozenset({"pnr_status", "response_hint"})
```

Read-only tools run concurrently; **mutating tools are chained sequentially in planner order** and
that chain runs concurrently with the read-only ones. Results are written back by index so planner
order is preserved for `combine_parallel_payloads`. The prompt separately forbids putting `booking`
in parallel with a `flight_search` it depends on.

This is a good example of the "no framework" philosophy paying off — the concurrency policy is
eight lines of explicit Python you can read and reason about, rather than a scheduler's behavior
you have to trust.

### Every failure mode still speaks

There is no path where the user hears silence:

| Failure | Handled at | User hears |
|---|---|---|
| Planner LLM error / non-JSON | `thinker.py:157-166` | "I could not plan that request. Could you say it again?" |
| Tool raises | `_tool_exception_hint`, `thinker.py:264` | "I could not complete that request right now." |
| Backend HTTP error | inside each tool | route-specific hint |
| Handler-level exception | `tool_handlers.py:107-118` | "I could not complete that request right now." |
| Empty `query` | `tool_handlers.py:50-61` | "What would you like me to check?" |
| Tool exceeds 30 s | pipecat `timeout_secs` | pipecat timeout path |
| No flights found | `flight_search.py:65-75` | "I could not find flights for that route." |

---

## 7. State: what lives where

| State | Owner | Lifetime |
|---|---|---|
| Conversation history | `LLMContext` in the pipeline | Session, sliding window of 20 non-prompt messages |
| `search_context`, `search_results` | `ThinkerSessionState` | Until next search or cancel |
| `booking_draft`, `waiting_for_preferences`, `waiting_for_confirmation` | `ThinkerSessionState` | Until booked or cancelled |
| `active_task`, `active_call_id` | `ThinkerSessionState` | One Thinker call |
| `lifecycle_events` | `ThinkerSessionState` | Session, capped at 200 |
| PNRs, flights, inventory | booking-server SQLite | Persistent across sessions |

Critically, the **Thinker planner LLM is stateless** — it sees a snapshot of session state as JSON
on every call (`_planner_state()`, `thinker.py:227`), never a transcript. And the **Talker holds no
domain state** — it only has the conversation.

### The booking state machine

`BookingTool.continue_booking` is deterministic Python, not LLM-driven:

```
no search_results ─────────────▶ response_hint: "I need to search flights first"
flight_selected unmatched ─────▶ response_hint: "Which flight from the results?"
no draft, no selection ────────▶ response_hint: "Which flight would you like to book?"
draft created, no prefs ───────▶ response_hint: "Seat or meal preference?"   [waiting_for_preferences]
prefs given / use_defaults ────▶ response_hint: booking summary + "Shall I confirm?"  [waiting_for_confirmation]
confirmed=true ────────────────▶ POST /pnrs → tool_result: "Your PNR is ABC123."
confirmed=false ───────────────▶ reset, "No problem, I have not booked it."
confirmed=None while waiting ──▶ re-ask "Should I confirm this booking?"
```

Changing the selected flight mid-draft rewinds the machine. The `confirmed=None` branch is what
makes hesitation safe: both prompts hammer that "whatever", "maybe", "I don't know", "what do you
think" are **not** confirmation — so an ambiguous grunt re-asks instead of charging a card.

---

## 8. Configuration

### Models (`services.cloud.yaml` / `services.local.yaml`)

Two separate slots, `llm` and `thinker-llm`, independently configurable — different models,
different endpoints, different token budgets:

| | Talker | Thinker |
|---|---|---|
| Default model | `nvidia/nemotron-3.5-lightning-30b-a3b` | same |
| `max_tokens` | 2048 | 4096 |
| Reasoning | `enable_thinking: false` | `enable_thinking: true`, `thinking_budget: 1024` |

Same weights, opposite configuration. That alone is most of the latency win. On workstation/DGX
Spark/Jetson the defaults switch to local NIM endpoints (`nvidia/nemotron-3-nano`, NVFP4 variants).

### Prompts (`prompts.yaml`)

- `talker` — `default: true`, user-selectable. ~330 lines, mostly a hard tool-call contract plus
  worked correct/incorrect examples. It is long because the failure mode it prevents — the Talker
  helpfully answering a flight question itself — is both tempting to the model and fatal to the
  architecture.
- `thinker` — `internal: true`, so it is never exposed in the UI prompt picker. Loaded via
  `_load_required_catalog_prompt("thinker")` (`pipeline.py:110`, defined at `pipeline.py:451`), which raises if missing.

### Environment variables

| Var | Default | Effect |
|---|---|---|
| `CHAT_HISTORY_RECENT_TURNS` | 20 | Talker context window (prompt messages always preserved) |
| `THINKER_FILLER_THRESHOLD_SECONDS` | 0.3 | Delay before `filler_text` is spoken; `0` = speak immediately |
| `THINKER_TOOL_TIMEOUT_SECONDS` | 30.0 | Pipecat handler timeout |
| `BOOKING_BACKEND_URL` | auto | Overrides booking-server URL; auto-resolves `booking-server:8001` vs `localhost:8001` by `APP_RUNTIME` |
| `FRONTEND_BACKEND_DIRECT_TOOL_RESPONSE` | unset | Speak `response_text` verbatim, skip Talker inference #2 |
| `FRONTEND_BACKEND_AGENT_TODAY` | unset | Pin "today" to `YYYY-MM-DD` for deterministic evals |

There is also a synthetic tool delay — module constants, not env vars: `THINKER_TOOL_DELAY_MIN_SECONDS`/`THINKER_TOOL_DELAY_MAX_SECONDS`, 0.1–0.5 s (`pipeline.py:192-193`) —
that randomizes backend latency so filler behavior is exercised even against a fast local backend.

### Deployment

```bash
docker compose --profile frontend-backend-agent up -d              # cloud NIMs + booking-server
docker compose --profile frontend-backend-agent/workstation up -d  # local NIMs + booking-server
```

The `booking-server` sidecar is required in both — it is the backing store, not an optional extra.

---

## 9. File map

```
pipeline.py                  Wiring: transport, ASR, Talker, Thinker, TTS, tool registration
prompts.yaml                 talker (public) + thinker (internal) prompts
services.cloud.yaml          Cloud model catalog — note the separate thinker-llm slot
services.local.yaml          workstation / dgxspark / jetson catalogs

src/                         REUSABLE — the Frontend/Backend pattern itself
  protocol.py                response_hint / tool_result / lifecycle events
  planner.py                 ThinkerPlanner Protocol + NvidiaThinkerPlanner + JSON repair
  tool_handlers.py           call_backend / cancel_backend handlers, filler timing
  tts_filter.py              Domain pronunciation cleanup
  runtime_context.py         runtime_today() with eval override

airline/                     DOMAIN — replace this to use your own backend
  thinker.py                 ThinkerBackend: cancellation, dispatch, parallel policy
  tools.py                   The two Talker-visible tool schemas
  state.py                   ThinkerSessionState, BookingDraft
  backend.py                 BookingBackend Protocol + HTTPBookingBackend  ← the seam
  flight_search.py           Thinker tool
  booking_tool.py            Thinker tool — the booking state machine
  pnr_status.py              Thinker tool
  plan_parsing.py            Plan normalization + multi-tool payload combination
  slot_parsing.py            Slot/ordinal/PNR normalization
  branding.py                "Booking Server" → "G Force Airlines"
  airports.py                IATA ↔ city ↔ spoken name
  transform.py               Server rows → internal flight/booking records
  database/                  FastAPI + SQLite booking-server sidecar
```

---

## 10. Reusing the pattern with your own backend

The seam is `BookingBackend` in `airline/backend.py` and, one level up, the `ThinkerBackend`
protocol that `build_handlers` depends on (`src/tool_handlers.py:25-41`). To put your own agent
behind the voice frontend:

1. **Keep `src/` as-is.** `protocol.py`, `planner.py`, `tool_handlers.py` are domain-neutral.
2. **Implement the three-method `ThinkerBackend` interface:** `call(query, slots, on_started)`,
   `cancel_active(reason)`, `cancel_pending_booking()`. Inside `call`, do whatever you like —
   HTTP to an existing service, a LangGraph invocation, another Claude/Nemotron agent.
3. **Return `response_hint(...)` or `tool_result(...)`.** Anything with a non-empty `response_text`
   is speakable.
4. **Fire `on_started` early** if you want filler text to work. It is what starts the 0.3 s timer.
5. **Rewrite the `talker` prompt's domain sections**, keep its hard tool-call contract intact.
6. **Rewrite the `thinker` prompt** to describe your tools and output schema — or drop the planner
   LLM entirely and let your existing agent handle intent.

Step 6 is worth emphasizing: if your backend agent already does its own planning, you don't need
`NvidiaThinkerPlanner` at all. The minimum viable version of this pattern is *the Talker prompt +
`call_backend` + `filler_text` timing* — roughly 200 lines. Everything else here is the airline
demo.

### Non-obvious things worth stealing

- **Self-contained queries.** The Talker must send the complete current request, never a delta
  ("add a window seat to the previous booking"). This makes the backend stateless-ish per call and
  robust to dropped/superseded calls. The prompt shows delta-only queries as the incorrect example
  three separate times.
- **Filler as a tool argument, not a separate turn.** The model that will speak the filler writes
  it in the same breath as the request, so it is contextually appropriate — but costs zero extra
  latency because it rides along in the tool call. And it is *conditional*, so fast paths stay
  clean.
- **Internal names are private.** "Thinker", "call_backend", "Booking Server", lifecycle markers —
  all explicitly forbidden in spoken text. The Talker even has an identity guard against admitting
  it's Nemotron.
- **Determinism where correctness matters.** The LLM picks the tool; a hand-written state machine
  decides what's legal. The gate "search → select → book" cannot be talked around.
