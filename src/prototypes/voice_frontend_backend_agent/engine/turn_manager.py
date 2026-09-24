# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The turn state machine (plan section 8).

States are derived, not stored::

    IDLE            nothing running
    THINKING        an agent task is running (respond or resume)
    AWAITING_TOOLS  function calls went out; waiting for outputs + response.create
    SPEAKING        a response is generating, or its audio is not fully heard yet

Rules, all on the audio clock except the two timeouts:

* Confirmed speech while SPEAKING interrupts: generation is cancelled even with
  nothing unplayed buffered, the response closes as ``cancelled/turn_detected``,
  and every stored copy of the answer is cut to what was heard (section 8.3).
* Confirmed speech while THINKING, before any tool call has left, cancels the
  agent task and merges the next transcript onto this one (``cancel_and_merge``).
  The runner's state is immutable, so the cancelled turn leaves no trace. After
  a tool call has left, the turn is never cancelled; new input is queued.
* Speech during a tool-call response in ``filler.mode: speak`` cancels only the
  filler's audio; the function calls are still emitted.
* One response at a time; its jobs (filler item, answer item, function calls,
  ``response.done``) run in order on one task, so items never interleave.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from prototypes.voice_frontend_backend_agent.agent.filler import FillerLog, FillerTimingRecord, Stamp
from prototypes.voice_frontend_backend_agent.agent.port import AgentPort, AgentReply, OutgoingCall
from prototypes.voice_frontend_backend_agent.agent.sinks import EventLog
from prototypes.voice_frontend_backend_agent.clock import WallClock
from prototypes.voice_frontend_backend_agent.config import VoiceConfig
from prototypes.voice_frontend_backend_agent.engine.output_path import OutputPath, PreparedSpeech
from prototypes.voice_frontend_backend_agent.engine.playback import ResponseProgress
from prototypes.voice_frontend_backend_agent.errors import HistoryRepairError, WireProtocolError
from prototypes.voice_frontend_backend_agent.wire import server_events as ev
from prototypes.voice_frontend_backend_agent.wire.response_writer import (
    ConversationOrder,
    MessageItemWriter,
    ResponseWriter,
)
from prototypes.voice_frontend_backend_agent.wire.session_view import SessionSettings

Emit = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class UserTurn:
    """One user turn and everything measured about it."""

    turn_id: int
    text: str
    turn_start: Stamp | None = None
    turn_end: Stamp | None = None
    asr_final: Stamp | None = None
    agent_start: Stamp | None = None
    tools_out: bool = False
    filler: FillerTimingRecord | None = None
    filler_task: asyncio.Task[None] | None = None
    filler_shown: bool = False


@dataclass(slots=True)
class UserInput:
    """A committed user utterance or text item, with its timing anchors."""

    text: str
    turn_start: Stamp | None = None
    turn_end: Stamp | None = None
    asr_final: Stamp | None = None
    item_id: str | None = None


@dataclass(slots=True)
class _SpeakJob:
    prepared: PreparedSpeech
    on_first_audio: Callable[[], None] | None = None
    on_done: Callable[[], None] | None = None


@dataclass(slots=True)
class _CallsJob:
    calls: tuple[OutgoingCall, ...]


@dataclass(slots=True)
class _FinishJob:
    usage: dict[str, Any]
    on_done: Callable[[], None] | None = None
    status: str = "completed"


@dataclass(slots=True)
class _Response:
    writer: ResponseWriter
    jobs: asyncio.Queue[_SpeakJob | _CallsJob | _FinishJob] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    speak_task: asyncio.Task[None] | None = None
    current_item: MessageItemWriter | None = None
    current_prepared: PreparedSpeech | None = None
    prepared: list[PreparedSpeech] = field(default_factory=list)
    tools_committed: bool = False
    skip_speech: bool = False


@dataclass(slots=True)
class _ToolWait:
    turn: UserTurn
    outstanding: tuple[str, ...]
    outputs: dict[str, str] = field(default_factory=dict)
    response_done: bool = False
    resume_requested: bool = False
    timer: asyncio.TimerHandle | None = None

    @property
    def complete(self) -> bool:
        return all(call_id in self.outputs for call_id in self.outstanding)


class TurnManager:
    """Owns the agent task, the active response, the tool round trip and barge-in."""

    def __init__(
        self,
        *,
        agent: AgentPort,
        output: OutputPath,
        emit: Emit,
        conversation: ConversationOrder,
        settings: Callable[[], SessionSettings],
        voice: Callable[[], str | None],
        config: VoiceConfig,
        clock: WallClock,
        audio_now: Callable[[], float],
        session_id: str,
        session_start: Stamp,
        filler_log: FillerLog,
        event_log: EventLog,
        show_silent_filler: bool = False,
    ) -> None:
        """Bind one session's collaborators."""
        self._agent = agent
        self._output = output
        self._emit = emit
        self._conversation = conversation
        self._settings = settings
        self._voice = voice
        self._config = config
        self._clock = clock
        self._audio_now = audio_now
        self._session_id = session_id
        self._session_start = session_start
        self._filler_log = filler_log
        self._event_log = event_log
        self._show_silent_filler = show_silent_filler
        self.progress = ResponseProgress()
        self._response: _Response | None = None
        self._agent_task: asyncio.Task[None] | None = None
        self._turn: UserTurn | None = None
        self._wait: _ToolWait | None = None
        self._queued: list[UserInput] = []
        self._pending_inputs: list[UserInput] = []
        self._merge_prefix = ""
        self._turn_counter = 0
        self._greeted = False
        self._closed = False

    # -- derived state ----------------------------------------------------------

    @property
    def state(self) -> str:
        """IDLE, THINKING, AWAITING_TOOLS or SPEAKING."""
        if self._agent_task is not None and not self._agent_task.done():
            return "THINKING"
        if self._wait is not None:
            return "AWAITING_TOOLS"
        if self.progress.active:
            return "SPEAKING"
        return "IDLE"

    @property
    def thinking(self) -> bool:
        """Whether an agent task is running."""
        return self._agent_task is not None and not self._agent_task.done()

    def _busy(self) -> bool:
        return self.thinking or self._wait is not None or self.progress.generating

    # -- inputs from the session ----------------------------------------------------

    def on_audio_advance(self, step_ms: float) -> None:
        """Input audio advanced: move the playout cursor."""
        self.progress.advance(step_ms)

    async def on_speech_started(self) -> None:
        """Confirmed user speech (``speech_started`` was already emitted)."""
        settings = self._settings()
        if not self._config.barge_in.enabled or not settings.turn_detection.interrupt_response:
            return
        turn = self._turn
        if self.thinking and turn is not None and not turn.tools_out:
            if self._config.barge_in.while_thinking == "cancel_and_merge":
                await self._cancel_thinking(turn, merge=True)
            return
        if self.progress.active:
            await self._interrupt_output(reason="turn_detected")

    def on_user_input(self, user_input: UserInput, *, from_audio: bool) -> None:
        """A committed utterance (ASR final) or a user text item."""
        text = user_input.text.strip()
        if self._merge_prefix:
            user_input.text = f"{self._merge_prefix} {text}".strip()
            self._merge_prefix = ""
        elif len(text) < max(1, self._config.turn_detection.min_transcript_chars):
            self._log("empty_transcript", text=text)
            return
        else:
            user_input.text = text
        auto = from_audio and self._config.protocol.auto_response and self._settings().turn_detection.create_response
        if not auto:
            self._pending_inputs.append(user_input)
            return
        if self._busy():
            self._queued.append(user_input)
            self._log("input_queued", state=self.state, text=user_input.text)
            return
        self._start_turn(user_input)

    def on_function_output(self, call_id: str, output: str) -> None:
        """A ``function_call_output`` item; raises for unknown or duplicate ``call_id``."""
        wait = self._wait
        if wait is None or call_id not in wait.outstanding:
            raise WireProtocolError(
                f"call_id {call_id!r} does not match an outstanding function call",
                code="invalid_value",
                param="item.call_id",
            )
        if call_id in wait.outputs:
            raise WireProtocolError(
                f"duplicate function_call_output for {call_id!r}", code="invalid_value", param="item.call_id"
            )
        wait.outputs[call_id] = output
        self._log("tool_output_in", call_id=call_id, output=output)
        if wait.complete and self._config.protocol.resume_on == "last_function_output":
            wait.resume_requested = True
        self._maybe_resume()

    def on_response_create(self) -> None:
        """Client ``response.create``."""
        if self._wait is not None:
            # tau2 may send this before our tool-call response.done went out: queue, never reject.
            self._wait.resume_requested = True
            self._maybe_resume()
            return
        if self.thinking or self.progress.generating:
            raise WireProtocolError(
                "a response is already in progress",
                code="conversation_already_has_active_response",
                param=None,
            )
        if self._pending_inputs:
            inputs, self._pending_inputs = self._pending_inputs, []
            merged = UserInput(
                text=" ".join(item.text for item in inputs),
                turn_start=inputs[0].turn_start,
                turn_end=inputs[-1].turn_end,
                asr_final=inputs[-1].asr_final,
            )
            self._start_turn(merged)
            return
        if self._config.protocol.greeting_enabled and not self._greeted and self._config.protocol.greeting_text:
            self.speak_greeting()
            return
        raise WireProtocolError(
            "response.create with no new user input to respond to", code="invalid_value", param=None
        )

    def delete_pending_input(self, item_id: str) -> bool:
        """Drop a user item not yet consumed by a response; ``False`` when there is none."""
        for bucket in (self._pending_inputs, self._queued):
            for index, item in enumerate(bucket):
                if item.item_id == item_id:
                    del bucket[index]
                    return True
        return False

    async def on_response_cancel(self) -> None:
        """Client ``response.cancel``: stop the active response (no-op when idle)."""
        turn = self._turn
        if self.thinking and turn is not None and not turn.tools_out:
            await self._cancel_thinking(turn, merge=False, reason="client_cancelled")
            return
        if self._response is not None and not self._response.writer.done:
            await self._interrupt_output(reason="client_cancelled")

    async def on_output_audio_clear(self) -> None:
        """``output_audio_buffer.clear``: stop sending remaining audio, like a cancel for playback."""
        if self.progress.active:
            await self._interrupt_output(reason="client_cancelled")

    def on_truncate(self, item_id: str, content_index: int, audio_end_ms: int) -> None:
        """Client truncate: ack with a clamped time; repair from our own playback estimate if not done yet."""
        span = self.progress.item(item_id)
        if span is None:
            if item_id not in self._conversation.item_ids:
                raise WireProtocolError(f"unknown item_id {item_id!r}", code="invalid_value", param="item_id")
            self._emit(ev.item_truncated(item_id, content_index, 0))
            return
        duration = max(0.0, span.end_ms - span.start_ms)
        clamped = int(max(0.0, min(float(audio_end_ms), duration)))
        self._log("truncate", item_id=item_id, client_audio_end_ms=audio_end_ms, clamped_ms=clamped)
        if span.kind == "answer" and not span.repaired and duration > 0:
            self._repair(span.item_id)
        self._emit(ev.item_truncated(item_id, content_index, clamped))

    def speak_greeting(self) -> None:
        """Speak the configured greeting as its own response and record it in the agent's history."""
        text = self._config.protocol.greeting_text
        if not text or self._greeted:
            return
        self._greeted = True
        self._agent.seed_assistant(text)
        response = self._ensure_response()
        response.jobs.put_nowait(_SpeakJob(self._prepare(text, kind="greeting")))
        response.jobs.put_nowait(_FinishJob(usage=ev.usage_object()))

    async def close(self) -> None:
        """Cancel everything the session started."""
        self._closed = True
        if self._wait is not None and self._wait.timer is not None:
            self._wait.timer.cancel()
        tasks = [self._agent_task]
        if self._turn is not None:
            tasks.append(self._turn.filler_task)
        if self._response is not None:
            tasks.extend([self._response.speak_task, self._response.task])
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        for task in tasks:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._response is not None:
            for prepared in self._response.prepared:
                await prepared.aclose()

    # -- turns ------------------------------------------------------------------------

    def _start_turn(self, user_input: UserInput) -> None:
        self._turn_counter += 1
        turn = UserTurn(
            turn_id=self._turn_counter,
            text=user_input.text,
            turn_start=user_input.turn_start,
            turn_end=user_input.turn_end,
            asr_final=user_input.asr_final,
            agent_start=Stamp.now(self._clock, self._audio_now()),
        )
        self._turn = turn
        self._log("agent_turn_start", turn_id=turn.turn_id, text=turn.text)
        self._agent_task = asyncio.create_task(self._run_agent(turn, None), name=f"agent-turn-{turn.turn_id}")

    async def _run_agent(self, turn: UserTurn, outputs: dict[str, str] | None) -> None:
        started = self._clock.monotonic()
        try:
            if outputs is None:
                reply = await self._agent.respond(turn.text)
            else:
                reply = await self._agent.resume(outputs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an agent failure fails the response, not the session
            logger.exception(f"[{self._session_id}] agent turn {turn.turn_id} failed")
            self._fail_turn(turn, exc)
            return
        self._log(
            "agent_turn_done",
            turn_id=turn.turn_id,
            latency_ms=int((self._clock.monotonic() - started) * 1000),
            outcome="text" if reply.text is not None else "tool_calls",
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            step="respond" if outputs is None else "resume",
            frontend=reply.usage.frontend.as_record(),
            backend=reply.usage.backend.as_record(),
        )
        self._deliver(turn, reply)

    def _deliver(self, turn: UserTurn, reply: AgentReply) -> None:
        usage = self._usage(reply)
        response = self._ensure_response()
        record = turn.filler
        if reply.text is not None:
            if record is not None and record.backend_done is None:
                record.backend_done = Stamp.now(self._clock)
                record.outcome = "answer"
            prepared = self._prepare(reply.text, kind="answer")
            response.jobs.put_nowait(
                _SpeakJob(
                    prepared,
                    on_first_audio=lambda: self._first_answer_audio(turn),
                    on_done=lambda: self._answer_sent(turn),
                )
            )
            response.jobs.put_nowait(_FinishJob(usage=usage))
            return
        turn.tools_out = True
        response.tools_committed = True
        self._wait = _ToolWait(turn=turn, outstanding=tuple(call.call_id for call in reply.calls))
        self._log("tool_calls_out", turn_id=turn.turn_id, calls=[call.name for call in reply.calls])
        if record is not None and not record.emitted:
            record.outcome = "tool_calls"
            self._emit_filler_record(record)
        response.jobs.put_nowait(_CallsJob(reply.calls))
        wait = self._wait
        response.jobs.put_nowait(_FinishJob(usage=usage, on_done=lambda: self._tool_response_done(wait)))

    def _fail_turn(self, turn: UserTurn, exc: BaseException) -> None:
        self._emit(
            ev.error(f"agent failed: {type(exc).__name__}: {exc}", code="agent_error", error_type="server_error")
        )
        if turn.filler is not None and not turn.filler.emitted:
            turn.filler.outcome = "error"
            self._emit_filler_record(turn.filler)
        if self._response is not None and not self._response.writer.done:
            self._response.jobs.put_nowait(_FinishJob(usage=ev.usage_object(), status="failed"))
        self._turn = None
        self._maybe_start_next()

    async def _cancel_thinking(self, turn: UserTurn, *, merge: bool, reason: str = "turn_detected") -> None:
        task = self._agent_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._agent_task = None
        if turn.filler_task is not None and not turn.filler_task.done():
            turn.filler_task.cancel()
        if turn.filler is not None and not turn.filler.emitted:
            turn.filler.outcome = "cancelled"
            self._emit_filler_record(turn.filler)
        if merge:
            self._merge_prefix = turn.text
        self._turn = None
        self._log("thinking_cancelled", turn_id=turn.turn_id, merge=merge, reason=reason)
        if self._response is not None and not self._response.writer.done:
            await self._cancel_response(self._response, reason=reason)

    # -- tools ------------------------------------------------------------------------

    def _tool_response_done(self, wait: _ToolWait) -> None:
        wait.response_done = True
        loop = asyncio.get_running_loop()
        wait.timer = loop.call_later(self._config.tools.result_timeout_s, self._on_result_timeout, wait)
        self._maybe_resume()

    def _on_result_timeout(self, wait: _ToolWait) -> None:
        if self._wait is not wait:
            return
        missing = [call_id for call_id in wait.outstanding if call_id not in wait.outputs]
        self._log("tool_result_timeout", missing=missing)
        self._resume(wait)

    def _maybe_resume(self) -> None:
        wait = self._wait
        if wait is None or not wait.response_done or not wait.resume_requested or not wait.complete:
            return
        self._resume(wait)

    def _resume(self, wait: _ToolWait) -> None:
        if wait.timer is not None:
            wait.timer.cancel()
        self._wait = None
        turn = wait.turn
        self._turn = turn
        self._agent_task = asyncio.create_task(
            self._run_agent(turn, dict(wait.outputs)), name=f"agent-resume-{turn.turn_id}"
        )

    # -- filler -------------------------------------------------------------------------

    def on_filler(self, text: str | None, stamp: Stamp) -> None:
        """The frontend delegated (``text`` is None) or produced filler text; runs on the loop."""
        turn = self._turn
        if turn is None or not self.thinking or self._closed:
            return
        if turn.filler is not None:
            if text is not None:
                turn.filler.text = text
                self._show_filler(turn)
            return
        filler = self._config.filler
        turn.filler = FillerTimingRecord(
            session_id=self._session_id,
            turn_id=turn.turn_id,
            mode=filler.mode,
            text=text or "",
            speak_after_ms=filler.speak_after_ms,
            session_start=self._session_start,
            turn_start=turn.turn_start,
            turn_end=turn.turn_end,
            asr_final=turn.asr_final,
            agent_start=turn.agent_start or stamp,
            filler_ready=stamp,
        )
        assert self._agent_task is not None  # noqa: S101 - thinking implies a task
        turn.filler_task = asyncio.create_task(self._filler_race(turn, self._agent_task), name="filler-race")
        self._show_filler(turn)

    def _show_filler(self, turn: UserTurn) -> None:
        """Tell an opted-in client about filler it will not hear (never sent to tau2)."""
        record = turn.filler
        if (
            not self._show_silent_filler
            or turn.filler_shown
            or record is None
            or record.mode == "speak"
            or not record.text.strip()
        ):
            return
        turn.filler_shown = True
        self._emit(ev.x_nvidia_filler(turn_id=turn.turn_id, text=record.text, mode=record.mode))

    async def _filler_race(self, turn: UserTurn, agent_task: asyncio.Task[None]) -> None:
        record = turn.filler
        assert record is not None  # noqa: S101 - set before the race starts
        elapsed = self._clock.monotonic() - record.filler_ready.mono
        timer = asyncio.create_task(asyncio.sleep(max(0.0, record.speak_after_ms / 1000.0 - elapsed)))
        try:
            done, _ = await asyncio.wait({agent_task, timer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            timer.cancel()
        if agent_task in done:
            record.would_have_spoken = False
            self._log("filler_skipped", turn_id=turn.turn_id, reason="skipped_backend_fast")
            return
        record.would_have_spoken = True
        if self._config.filler.mode != "speak" or not record.text.strip() or self._turn is not turn:
            return
        response = self._ensure_response()
        response.jobs.put_nowait(
            _SpeakJob(self._prepare(record.text, kind="filler"), on_first_audio=lambda: self._filler_audio(record))
        )

    def _filler_audio(self, record: FillerTimingRecord) -> None:
        record.spoken = True
        record.filler_audio_start = Stamp.now(self._clock)

    def _first_answer_audio(self, turn: UserTurn) -> None:
        now = Stamp.now(self._clock)
        self._log_latency(turn, now)
        if turn.filler is not None and not turn.filler.emitted:
            turn.filler.first_answer_audio = now
            self._emit_filler_record(turn.filler)

    def _answer_sent(self, turn: UserTurn) -> None:
        if turn.filler is not None and not turn.filler.emitted:
            self._emit_filler_record(turn.filler)

    def _emit_filler_record(self, record: FillerTimingRecord) -> None:
        if record.would_have_spoken is None and record.backend_done is not None:
            elapsed_ms = (record.backend_done.mono - record.filler_ready.mono) * 1000.0
            record.would_have_spoken = elapsed_ms > record.speak_after_ms
        self._filler_log.emit(record)

    # -- responses ------------------------------------------------------------------------

    def _prepare(self, text: str, *, kind: str) -> PreparedSpeech:
        settings = self._settings()
        prepared = self._output.prepare(
            text, kind=kind, modality=settings.output_modality, voice=self._voice(), out_format=settings.output_format
        )
        return prepared

    def _usage(self, reply: AgentReply) -> dict[str, Any]:
        if not self._config.protocol.emit_usage:
            return ev.usage_object()
        return ev.usage_object(
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cached_tokens=reply.usage.cached_tokens,
        )

    def _ensure_response(self) -> _Response:
        if self._response is not None and not self._response.writer.done:
            return self._response
        writer = ResponseWriter(self._emit, self._conversation, modality=self._settings().output_modality)
        response = _Response(writer=writer)
        writer.open()
        self.progress.generating = True
        response.task = asyncio.create_task(self._run_response(response), name=f"response-{writer.response_id}")
        self._response = response
        return response

    async def _run_response(self, response: _Response) -> None:
        writer = response.writer
        settings = self._settings()
        try:
            while True:
                job = await response.jobs.get()
                if isinstance(job, _SpeakJob):
                    response.prepared.append(job.prepared)
                    item = writer.begin_message(kind=job.prepared.kind)
                    response.current_item = item
                    response.current_prepared = job.prepared
                    response.skip_speech = False
                    response.speak_task = asyncio.create_task(
                        self._output.speak(
                            job.prepared,
                            item,
                            self.progress,
                            out_format=settings.output_format,
                            on_first_audio=job.on_first_audio,
                        )
                    )
                    try:
                        await response.speak_task
                        status = "completed"
                    except asyncio.CancelledError:
                        if not response.skip_speech:
                            raise
                        status = "incomplete"
                    finally:
                        await job.prepared.aclose()
                    item.close(status=status)
                    response.current_item = None
                    response.current_prepared = None
                    response.speak_task = None
                    if job.on_done is not None:
                        job.on_done()
                elif isinstance(job, _CallsJob):
                    for call in job.calls:
                        writer.function_call(call_id=call.call_id, name=call.name, arguments=call.arguments)
                else:
                    writer.finish(
                        job.status, usage=job.usage, reason="server_error" if job.status == "failed" else None
                    )
                    self._response_finished(response)
                    if job.on_done is not None:
                        job.on_done()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a TTS failure fails the response, not the session
            logger.exception(f"[{self._session_id}] response {writer.response_id} failed")
            self._emit(ev.error(f"response failed: {exc}", code="response_failed", error_type="server_error"))
            writer.finish("failed", reason="server_error")
            self._response_finished(response)
            if self._wait is not None and self._wait.turn.tools_out and not self._wait.response_done:
                self._tool_response_done(self._wait)

    def _response_finished(self, response: _Response) -> None:
        if self._response is response:
            self._response = None
        self.progress.generating = False
        self._log(
            "response_done",
            response_id=response.writer.response_id,
            status=response.writer.status,
            sent_ms=int(self.progress.sent_ms),
            heard_ms=int(self.progress.heard_ms),
        )
        if not self.thinking and self._wait is None:
            self._turn = None
        self._maybe_start_next()

    def _maybe_start_next(self) -> None:
        if self._closed or self._busy() or not self._queued:
            return
        inputs, self._queued = self._queued, []
        self._start_turn(
            UserInput(
                text=" ".join(item.text for item in inputs),
                turn_start=inputs[0].turn_start,
                turn_end=inputs[-1].turn_end,
                asr_final=inputs[-1].asr_final,
            )
        )

    async def _interrupt_output(self, *, reason: str) -> None:
        response = self._response
        heard_before = self.progress.heard_ms
        if response is not None and not response.writer.done:
            if response.tools_committed:
                # Only the filler's audio is cut; the function calls still go out.
                if response.speak_task is not None and not response.speak_task.done():
                    response.skip_speech = True
                    response.speak_task.cancel()
            else:
                await self._cancel_response(response, reason=reason)
        for span in self.progress.items:
            if span.kind == "answer" and not span.repaired and span.end_ms > heard_before:
                self._repair(span.item_id)
        self._log("barge_in", reason=reason, heard_ms=int(heard_before), sent_ms=int(self.progress.sent_ms))
        self.progress.discard_unplayed()

    async def _cancel_response(self, response: _Response, *, reason: str) -> None:
        for task in (response.speak_task, response.task):
            if task is not None and not task.done():
                task.cancel()
        for task in (response.speak_task, response.task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        for prepared in response.prepared:
            await prepared.aclose()
        while not response.jobs.empty():
            job = response.jobs.get_nowait()
            if isinstance(job, _SpeakJob):
                await job.prepared.aclose()
                if job.prepared.kind == "answer":
                    # Queued behind a filler and never started: nothing of it was heard.
                    self._repair_text(job.prepared.text, "", item_id=None)
        item, prepared = response.current_item, response.current_prepared
        if item is not None and not item.closed and prepared is not None and prepared.kind == "answer":
            if self.progress.item(item.item_id) is None:
                self._repair_text(prepared.text, "", item_id=item.item_id)
            else:
                self._repair(item.item_id)
        response.writer.finish("cancelled", reason=reason)
        if self._response is response:
            self._response = None
        self.progress.generating = False
        if self._wait is None and not self.thinking:
            self._turn = None

    def _repair(self, item_id: str) -> None:
        span = self.progress.item(item_id)
        if span is None or span.repaired:
            return
        span.repaired = True
        self._repair_text(span.full_text, self.progress.heard_text(item_id), item_id=item_id)

    def _repair_text(self, full_text: str, heard: str, *, item_id: str | None) -> None:
        if self._config.barge_in.history != "truncate_heard" or heard == " ".join(full_text.split()):
            return
        replacement = f"{heard}{self._config.barge_in.interruption_marker}".strip()
        try:
            self._agent.repair_last_answer(full_text, replacement)
        except HistoryRepairError as exc:
            logger.error(f"[{self._session_id}] history repair skipped: {exc}")
            self._log("history_repair_failed", item_id=item_id, error=str(exc))
            return
        self._log("history_repaired", item_id=item_id, heard=heard, full_text=full_text)

    # -- logging ------------------------------------------------------------------------

    def _log(self, kind: str, **data: Any) -> None:
        self._event_log.write(kind, self._session_id, {"audio_ms": int(self._audio_now()), **data})

    def _log_latency(self, turn: UserTurn, first_audio: Stamp) -> None:
        def ms(a: Stamp | None, b: Stamp | None) -> int | None:
            return None if a is None or b is None else int(round((b.mono - a.mono) * 1000))

        breakdown = {
            "turn_id": turn.turn_id,
            "user_stop_to_asr_final_ms": ms(turn.turn_end, turn.asr_final),
            "asr_final_to_agent_start_ms": ms(turn.asr_final, turn.agent_start),
            "agent_start_to_first_audio_ms": ms(turn.agent_start, first_audio),
            "user_stop_to_first_audio_ms": ms(turn.turn_end, first_audio),
        }
        logger.info(f"[{self._session_id}] latency {breakdown}")
        self._log("turn_latency", **breakdown)
