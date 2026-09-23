# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The frontend agent: answer directly, or delegate. Nothing else.

``decide()`` returns a closed union rather than a message, which is what makes
"filler text can never reach the caller" a property of the types: there is no
path from :class:`Delegate.filler_text` to an outward envelope.
"""

from __future__ import annotations

from dataclasses import dataclass

from prototypes.text_frontend_backend_agent import events
from prototypes.text_frontend_backend_agent.config import Config
from prototypes.text_frontend_backend_agent.delegation import CALL_BACKEND, FRONTEND_TOOLS
from prototypes.text_frontend_backend_agent.errors import FrontendContractError
from prototypes.text_frontend_backend_agent.events import EventSink, InternalEvent
from prototypes.text_frontend_backend_agent.history import History
from prototypes.text_frontend_backend_agent.llm import ChatClient
from prototypes.text_frontend_backend_agent.messages import Message, ToolCall, UsageTotals
from prototypes.text_frontend_backend_agent.tools import decode_arguments

_REPAIR_INSTRUCTION = (
    "Your previous turn broke the contract: {problem}. Reply again for the same user turn. "
    f"Either answer the user directly in plain text, or emit exactly one {CALL_BACKEND} tool call "
    "whose 'query' states the complete current request. Never do both, and never write a tool call as text."
)

# Text containing any of these is a tool call (or just its arguments) typed out as prose: the
# model imitating the prompt's examples, or a template that failed to parse. Never an answer.
_TYPED_TOOL_CALL_MARKERS = (CALL_BACKEND, "filler_text", "tool_calls", "<tool_call", "<function=")


@dataclass(frozen=True, slots=True)
class DirectAnswer:
    """The frontend answered the user itself."""

    text: str


@dataclass(frozen=True, slots=True)
class Delegate:
    """The frontend handed the task to the backend."""

    query: str
    filler_text: str
    assistant_message: Message


@dataclass(frozen=True, slots=True)
class ContractFallback:
    """The frontend broke its contract and a safe answer was substituted."""

    text: str
    problem: str


Decision = DirectAnswer | Delegate | ContractFallback


class FrontendAgent:
    """Talks to the user; owns no tools except delegation."""

    def __init__(self, *, client: ChatClient, system_prompt: str, config: Config, sink: EventSink) -> None:
        """Bind the frontend to its client, prompt and event sink."""
        self._client = client
        self._system_prompt = system_prompt
        self._config = config
        self._sink = sink

    async def decide(self, user_text: str, history: History, *, session_id: str) -> tuple[Decision, UsageTotals]:
        """Choose between answering directly and delegating.

        On a contract violation the frontend is reprompted up to
        ``max_repair_attempts`` times and then **fails closed**: the model's raw
        text is never returned as the answer, because that is precisely when it
        is most likely to contain tool-call markup or a half-formed promise.
        """
        totals = UsageTotals()
        delegation = self._config.frontend.delegation
        attempt_messages = [*history.messages, Message.user(user_text)]
        problem = ""
        for attempt in range(delegation.max_repair_attempts + 1):
            response = await self._client.complete(
                messages=[Message.system(self._system_prompt), *attempt_messages],
                tools=list(FRONTEND_TOOLS),
            )
            totals = totals.add_call(
                "frontend", usage=response.usage, latency_ms=response.latency_ms, cost=response.cost
            )
            decision, problem = self._interpret(response.content, response.tool_calls, session_id=session_id)
            if decision is not None:
                return decision, totals
            self._sink.emit(
                InternalEvent(
                    events.FRONTEND_REPAIR,
                    session_id,
                    {"attempt": attempt + 1, "problem": problem, "rejected_text": response.content},
                )
            )
            attempt_messages = [
                *attempt_messages,
                Message.assistant(response.content or ""),
                Message.user(_REPAIR_INSTRUCTION.format(problem=problem)),
            ]
        self._sink.emit(InternalEvent(events.FRONTEND_CONTRACT_VIOLATION, session_id, {"problem": problem}))
        if delegation.on_contract_violation == "error":
            raise FrontendContractError(problem)
        return ContractFallback(text=delegation.fallback_text, problem=problem), totals

    def _interpret(
        self, content: str | None, tool_calls: tuple[ToolCall, ...], *, session_id: str
    ) -> tuple[Decision | None, str]:
        """Map one raw completion onto a decision, or report why it is invalid."""
        if tool_calls:
            unknown = [call.name for call in tool_calls if call.name != CALL_BACKEND]
            if unknown:
                return None, f"called unavailable tool(s) {unknown}; only {CALL_BACKEND} exists"
            if len(tool_calls) > 1:
                return None, "emitted several delegation calls; exactly one is allowed"
            call = tool_calls[0]
            arguments = decode_arguments(call)
            query = str(arguments.get("query") or "").strip()
            if not query:
                return None, "called call_backend without a non-empty 'query'"
            if content and content.strip():
                self._sink.emit(
                    InternalEvent(events.FRONTEND_CONTRACT_VIOLATION, session_id, {"discarded_text": content.strip()})
                )
            filler = str(arguments.get("filler_text") or "").strip()
            return Delegate(
                query=query,
                filler_text=filler,
                assistant_message=Message.assistant_tool_calls((call,)),
            ), ""
        text = (content or "").strip()
        if not text:
            return None, "returned neither text nor a tool call"
        if any(marker in text for marker in _TYPED_TOOL_CALL_MARKERS):
            return None, f"wrote a {CALL_BACKEND} tool call as text instead of emitting a real tool call"
        return DirectAnswer(text=text), ""
