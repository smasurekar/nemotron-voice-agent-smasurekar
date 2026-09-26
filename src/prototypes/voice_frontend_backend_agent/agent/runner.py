# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""``AgentPort`` over the text prototype's ``FrontendBackendAgent``.

One runner per Realtime session. It owns the session's immutable
``SessionState`` and swaps it only when a call returns, so cancelling an
in-flight ``respond()`` (barge-in while thinking) leaves the conversation
exactly as it was. The agent itself is assembled per session with
``assemble_agent`` from the shared LLM clients, because each session has its
own tools and instructions.

With ``normalization`` on, the runner is also where both hooks run: the
transcript hook on the text of each agent turn, and the tool-argument hook on
every batch of backend tool calls before it reaches the client. Calls answered
locally never leave the runner; their results are fed back to the agent at once
(all calls local) or merged with the client's outputs on ``resume``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from prototypes.text_frontend_backend_agent.agent import FrontendBackendAgent, assemble_agent
from prototypes.text_frontend_backend_agent.backend_context import backend_system_prompt
from prototypes.text_frontend_backend_agent.config import Config
from prototypes.text_frontend_backend_agent.events import EventSink, InternalEvent
from prototypes.text_frontend_backend_agent.llm import ChatClient
from prototypes.text_frontend_backend_agent.messages import (
    AgentTurn,
    Message,
    RoleTotals,
    ToolCall,
    ToolResult,
    UsageTotals,
)
from prototypes.text_frontend_backend_agent.prompts import load_catalog, render
from prototypes.text_frontend_backend_agent.session import SessionState
from prototypes.text_frontend_backend_agent.tools import ToolSpec
from prototypes.voice_frontend_backend_agent.agent.history_repair import repair_interrupted_answer
from prototypes.voice_frontend_backend_agent.agent.instructions import ResolvedInstructions, session_agent_config
from prototypes.voice_frontend_backend_agent.agent.port import AgentReply, ReplyUsage, RoleUsage
from prototypes.voice_frontend_backend_agent.agent.tools import realtime_tools_to_specs, to_outgoing, to_results
from prototypes.voice_frontend_backend_agent.config import InstructionsConfig, ToolsConfig
from prototypes.voice_frontend_backend_agent.normalization import (
    ARGUMENT_NORMALIZED,
    CALL_ANSWERED_LOCALLY,
    LOCAL_ROUNDS_EXHAUSTED,
    TRANSCRIPT_NORMALIZED,
    NormalizationSettings,
)
from prototypes.voice_frontend_backend_agent.normalization.arguments import (
    ArgumentNormalizer,
    FailureKey,
    Screening,
)
from prototypes.voice_frontend_backend_agent.normalization.prompts import with_frontend_note
from prototypes.voice_frontend_backend_agent.normalization.transcript import TranscriptNormalizer


@dataclass(frozen=True, slots=True)
class AgentClients:
    """LLM clients shared by every session (the OpenAI client is safe to share)."""

    backend: ChatClient
    frontend: ChatClient | None = None


def _role_usage(totals: RoleTotals) -> RoleUsage:
    usage = totals.usage
    return RoleUsage(
        calls=totals.calls,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        total_tokens=usage.total_tokens or usage.prompt_tokens + usage.completion_tokens,
        latency_ms=totals.latency_ms,
    )


def _reply(turn: AgentTurn, totals: UsageTotals | None = None, calls: tuple[ToolCall, ...] | None = None) -> AgentReply:
    """The port reply for ``turn``; ``totals`` sums several agent steps, ``calls`` replaces its calls."""
    totals = totals or turn.usage
    usage = totals.usage
    reply_usage = ReplyUsage(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        frontend=_role_usage(totals.frontend),
        backend=_role_usage(totals.backend),
    )
    outgoing = turn.tool_calls if calls is None else calls
    if outgoing:
        return AgentReply(calls=to_outgoing(outgoing), usage=reply_usage)
    return AgentReply(text=turn.final_text or "", usage=reply_usage)


def _rewrite_pending_calls(state: SessionState, calls: Sequence[ToolCall]) -> SessionState:
    """Store canonical arguments in the suspended backend group (paired and ``backend_only`` alike)."""
    pending = state.pending
    if pending is None or not pending.backend_history.messages:
        return state
    by_id = {call.id: call for call in calls}
    messages = list(pending.backend_history.messages)
    last = messages[-1]
    rewritten = tuple(by_id.get(call.id, call) for call in last.tool_calls)
    if rewritten == last.tool_calls:
        return state
    messages[-1] = replace(last, tool_calls=rewritten)
    history = replace(pending.backend_history, messages=tuple(messages))
    return replace(state, pending=replace(pending, backend_history=history))


@dataclass(frozen=True, slots=True)
class _Settled:
    """One ``respond``/``resume`` outcome, committed only when the call completes."""

    reply: AgentReply
    state: SessionState
    held: dict[str, str]
    keys: dict[str, FailureKey]


class TextAgentRunner:
    """One session's agent: the text prototype plus the session's state."""

    def __init__(
        self,
        *,
        base_config: Config,
        tools_config: ToolsConfig,
        instructions_config: InstructionsConfig,
        clients: AgentClients,
        sink: EventSink,
        session_id: str,
        seed_greeting: str = "",
        normalization: NormalizationSettings | None = None,
    ) -> None:
        """Build the initial agent (no client tools, no instructions yet)."""
        self._base = base_config
        self._tools_config = tools_config
        self._instructions_config = instructions_config
        self._clients = clients
        self._sink = sink
        self._state = SessionState(session_id=session_id)
        self._normalization = normalization or NormalizationSettings()
        transcript = self._normalization.transcript
        self._transcript = TranscriptNormalizer(transcript) if transcript.enabled else None
        self._arguments = self._argument_normalizer()
        #: Retry-guard keys of calls that failed permanently in this session.
        self._failed: frozenset[FailureKey] = frozenset()
        #: Local results of the outstanding calls that were not sent, merged in on ``resume``.
        self._held: dict[str, str] = {}
        #: Retry-guard keys of the outstanding calls that were sent.
        self._sent_keys: dict[str, FailureKey] = {}
        self._agent: FrontendBackendAgent | None = None
        self.config: Config = base_config
        self.resolved_instructions: ResolvedInstructions | None = None
        self.tool_specs: tuple[ToolSpec, ...] = ()
        self.configure(tools=(), instructions="")
        if seed_greeting:
            self.seed_assistant(seed_greeting)

    # -- AgentPort ------------------------------------------------------------

    @property
    def backend_only(self) -> bool:
        """Whether the frontend is disabled."""
        return not self._base.frontend_enabled

    @property
    def state(self) -> SessionState:
        """The current (immutable) session state."""
        return self._state

    def configure(self, *, tools: Sequence[Mapping[str, Any]], instructions: str) -> None:
        """Rebuild the agent for new tools/instructions; the conversation state is kept."""
        if self._tools_config.source == "client":
            specs = realtime_tools_to_specs(tools)
        else:
            specs = self._tools_config.config_tool_specs
        config, resolved = session_agent_config(
            self._base, self._instructions_config, session_instructions=instructions, tools=specs
        )
        if self._transcript is not None:
            config = with_frontend_note(config, self._normalization.transcript.frontend_note_key)
        self._agent = assemble_agent(
            config,
            tools=specs,
            event_sink=self._sink,
            frontend_client=self._clients.frontend if config.frontend_enabled else None,
            backend_client=self._clients.backend,
        )
        self.config = config
        self.resolved_instructions = resolved
        self.tool_specs = tuple(specs)

    async def respond(self, text: str) -> AgentReply:
        """Run one user turn; the state is replaced only if the call completes."""
        assert self._agent is not None  # noqa: S101 - built in __init__
        text = self._normalize_transcript(text)
        turn, state = await self._agent.send(text, self._state)
        settled = await self._settle(turn, state, self._failed)
        self._commit(settled, self._failed)
        return settled.reply

    async def resume(self, outputs: Mapping[str, str]) -> AgentReply:
        """Continue a suspended turn with the client's outputs (plus any held local results)."""
        assert self._agent is not None  # noqa: S101 - built in __init__
        failed = self._failed
        if self._arguments is not None:
            failed = failed | {
                self._sent_keys[call_id]
                for call_id, output in outputs.items()
                if call_id in self._sent_keys and self._arguments.is_permanent_failure(output)
            }
        turn, state = await self._agent.send_tool_results(to_results({**self._held, **outputs}), self._state)
        settled = await self._settle(turn, state, failed)
        self._commit(settled, failed)
        return settled.reply

    def repair_last_answer(self, full_text: str, replacement: str) -> None:
        """Rewrite every stored copy of the interrupted answer."""
        self._state = repair_interrupted_answer(
            self._state,
            full_text=full_text,
            replacement=replacement,
            frontend_enabled=self._base.frontend_enabled,
            backend_history=self._base.backend.conversation_history.keeps_backend_turns,
        )

    def seed_assistant(self, text: str) -> None:
        """Append assistant speech the agent did not produce (a greeting) to the owning history."""
        message = Message.assistant(text)
        if self._base.frontend_enabled:
            self._state = replace(self._state, frontend_history=self._state.frontend_history.append(message))
        else:
            self._state = replace(self._state, backend_history=self._state.backend_history.append(message))

    # -- normalization ---------------------------------------------------------

    def _argument_normalizer(self) -> ArgumentNormalizer | None:
        settings = self._normalization.tool_arguments
        if not settings.enabled:
            return None
        catalog = load_catalog(self._base.prompts_path, self._base.prompts.inline)
        guard = settings.retry_guard
        return ArgumentNormalizer(
            settings,
            transcript=self._normalization.transcript,
            invalid_template=catalog.get(settings.invalid_message_key),
            already_failed_template=catalog.get(guard.message_key) if guard.enabled else "",
        )

    def _emit(self, kind: str, **data: Any) -> None:
        self._sink.emit(InternalEvent(kind, self._state.session_id, data))

    def _normalize_transcript(self, text: str) -> str:
        if self._transcript is None:
            return text
        normalized = self._transcript.normalize(text)
        if normalized.changed:
            spans = [{"spoken": span.spoken, "written": span.written} for span in normalized.spans]
            self._emit(TRANSCRIPT_NORMALIZED, raw=text, text=normalized.text, spans=spans)
        return normalized.text

    def _commit(self, settled: _Settled, failed: frozenset[FailureKey]) -> None:
        self._state = settled.state
        self._held = settled.held
        self._sent_keys = settled.keys
        self._failed = failed

    async def _settle(self, turn: AgentTurn, state: SessionState, failed: frozenset[FailureKey]) -> _Settled:
        """Screen the turn's tool calls; answer all-local batches at once, at most ``max_local_rounds`` times."""
        totals = turn.usage
        rounds = 0
        while self._arguments is not None and turn.tool_calls:
            screening = self._arguments.screen(turn.tool_calls, failed)
            state = _rewrite_pending_calls(state, screening.calls)
            self._emit_rewrites(screening)
            sent = screening.sent
            if not sent and rounds >= self._arguments.settings.max_local_rounds:
                # Only the local interception is bypassed: the canonical calls go out.
                self._emit(LOCAL_ROUNDS_EXHAUSTED, tools=[call.name for call in screening.calls], rounds=rounds)
                reply = _reply(turn, totals, screening.calls)
                return _Settled(reply, state, {}, dict(screening.keys))
            for answer in screening.local:
                self._emit(
                    CALL_ANSWERED_LOCALLY,
                    call_id=answer.call_id,
                    tool=answer.tool,
                    argument=answer.argument,
                    value=answer.value,
                    reason=answer.reason,
                    local_round=rounds + 1,
                )
            held = {answer.call_id: answer.message for answer in screening.local}
            if sent:
                keys = {call_id: key for call_id, key in screening.keys.items() if call_id not in held}
                return _Settled(_reply(turn, totals, sent), state, held, keys)
            rounds += 1
            results = tuple(ToolResult(tool_call_id=call_id, content=message) for call_id, message in held.items())
            assert self._agent is not None  # noqa: S101 - built in __init__
            turn, state = await self._agent.send_tool_results(results, state)
            totals = totals.merge(turn.usage)
        return _Settled(_reply(turn, totals), state, {}, {})

    def _emit_rewrites(self, screening: Screening) -> None:
        for rewrite in screening.rewrites:
            self._emit(
                ARGUMENT_NORMALIZED,
                call_id=rewrite.call_id,
                tool=rewrite.tool,
                argument=rewrite.argument,
                before=rewrite.before,
                after=rewrite.after,
            )

    # -- introspection (tests, logs) -------------------------------------------

    def rendered_prompts(self) -> dict[str, str]:
        """The exact system prompts this session's agent sends."""
        catalog = load_catalog(self.config.prompts_path, self.config.prompts.inline)
        prompts = {"backend": backend_system_prompt(catalog, self.config)}
        if self.config.frontend_enabled:
            prompts["frontend"] = render(catalog.get(self.config.frontend.prompt_key), self.config)
        return prompts
