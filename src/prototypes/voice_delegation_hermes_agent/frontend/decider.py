# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Frontend deciders: one ``delegate`` decision per user turn (plan section 6).

* :class:`LLMDecider` asks the frontend model with ``tool_choice: required``, repairs a
  broken reply once, and falls back to ``delegation.on_contract_error`` on a second
  failure or a timeout.
* :class:`RuleDecider` is the deterministic stand-in (``frontend.decider: scripted``)
  for tests and ``--stub-backend`` smoke runs.

:func:`apply_guards` runs after either (the ``backend_question_needs_delegate`` guard).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from prototypes.voice_delegation_hermes_agent.backend.protocol import IDLE, REQUEST_STATUS, REQUEST_TASK, WORKING
from prototypes.voice_delegation_hermes_agent.frontend.delegate_tool import (
    TOOL_NAME,
    ContractError,
    Decision,
    delegate_tool,
    parse_reply,
)
from prototypes.voice_delegation_hermes_agent.frontend.llm import ChatModel, ChatReply
from prototypes.voice_delegation_hermes_agent.frontend.prompts import PromptCatalog


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Everything one decision sees."""

    system_prompt: str
    #: The frontend projection of the shared transcript, ending with the latest user turn.
    messages: list[dict[str, Any]]
    user_text: str
    state: str
    current_request: str = ""
    elapsed_s: int | None = None
    recent_activity: Sequence[str] = field(default_factory=tuple)
    backend_asked_question: bool = False
    #: M2: the newest backend answer was not heard (or only partly); shown only with ``replay_intent``.
    last_answer_unheard: bool = False


class Decider(Protocol):
    """Turns a :class:`DecisionContext` into a :class:`Decision`."""

    async def decide(self, context: DecisionContext) -> Decision:
        """One decision; never raises for model misbehaviour (only for cancellation)."""
        ...


def fallback_decision(on_contract_error: str, repair: str) -> Decision:
    """The decision used when the model cannot produce a valid one."""
    if on_contract_error == "direct_empty":
        return Decision(delegate=False, filler_text="", repair=repair)
    return Decision(delegate=True, filler_text="", request=REQUEST_TASK, repair=repair)


def apply_guards(
    decision: Decision,
    context: DecisionContext,
    *,
    backend_question_needs_delegate: bool,
    backchannel_words: Sequence[str] = (),
) -> Decision:
    """Code guards on top of the prompt (plan section 3).

    1. A pure backchannel (the whole turn is one of ``backchannel_words``) is never delegated and gets no
       filler, unless the backend's last heard message asked a question.
    2. IDLE + the backend's last heard message asked a question: the turn is its answer, so it is delegated.
    """
    words = re.sub(r"[^\w\s'-]", "", context.user_text.lower()).strip()
    if backchannel_words and not context.backend_asked_question and words in backchannel_words:
        if decision.delegate or decision.filler_text:
            return replace(decision, delegate=False, filler_text="", request=REQUEST_TASK, repair="guard_backchannel")
        return decision
    if (
        backend_question_needs_delegate
        and not decision.delegate
        and context.state == IDLE
        and context.backend_asked_question
    ):
        return replace(decision, delegate=True, request=REQUEST_TASK, repair="guard_backend_question")
    return decision


class LLMDecider:
    """The frontend model with the single ``delegate`` tool."""

    def __init__(
        self,
        model: ChatModel,
        prompts: PromptCatalog,
        *,
        timeout_ms: int,
        max_repair_attempts: int,
        on_contract_error: str,
        tool_choice: str = "named",
        parallel_tool_calls: bool | None = False,
        hedge_after_ms: int = 0,
    ) -> None:
        """Bind a shared model client and the session's settings.

        ``tool_choice``: ``named`` forces the single ``delegate`` call (default), ``required`` / ``auto``
        pass through to the endpoint.
        """
        self._model = model
        self._prompts = prompts
        self._tool = delegate_tool(prompts)
        self._timeout = timeout_ms / 1000.0
        self._max_repairs = max_repair_attempts
        self._on_error = on_contract_error
        self._tool_choice: str | dict[str, Any] = (
            {"type": "function", "function": {"name": TOOL_NAME}} if tool_choice == "named" else tool_choice
        )
        self._parallel = parallel_tool_calls
        self._hedge_after = hedge_after_ms / 1000.0

    async def decide(self, context: DecisionContext) -> Decision:
        """Ask the model; repair once; fall back on failure or timeout."""
        started = time.perf_counter()
        try:
            decision = await asyncio.wait_for(self._attempts(context), timeout=self._timeout)
        except TimeoutError:
            decision = fallback_decision(self._on_error, "timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an endpoint failure must not fail the turn
            decision = fallback_decision(self._on_error, f"error:{type(exc).__name__}")
        return replace(decision, latency_ms=(time.perf_counter() - started) * 1000.0)

    def messages(self, context: DecisionContext) -> list[dict[str, Any]]:
        """The request messages (exposed for the replay CLI and tests)."""
        state = self._prompts.render(
            "frontend_backend_state",
            state=context.state,
            current_request=context.current_request,
            elapsed_s=context.elapsed_s,
            recent_activity=list(context.recent_activity),
            backend_asked_question=context.backend_asked_question,
            last_answer_unheard=context.last_answer_unheard,
        )
        return [{"role": "system", "content": f"{context.system_prompt}\n\n{state}"}, *context.messages]

    async def _complete(self, messages: list[dict[str, Any]]) -> ChatReply:
        """One completion; with hedging, a second identical request races the first after ``hedge_after_ms``."""

        def request() -> asyncio.Task[ChatReply]:
            return asyncio.ensure_future(
                self._model.complete(
                    messages, tools=[self._tool], tool_choice=self._tool_choice, parallel_tool_calls=self._parallel
                )
            )

        first = request()
        if self._hedge_after <= 0:
            return await first
        tasks = {first}
        try:
            done, _ = await asyncio.wait(tasks, timeout=self._hedge_after)
            if not done:
                tasks.add(request())
            while tasks:
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.exception() is None:
                        return task.result()
                if not tasks:
                    return done.pop().result()  # every request failed: raise the last error
            raise RuntimeError("no completion")  # pragma: no cover
        finally:
            for task in tasks:
                task.cancel()

    async def _attempts(self, context: DecisionContext) -> Decision:
        messages = self.messages(context)
        usage = {"input_tokens": 0, "output_tokens": 0}
        repair = ""
        for attempt in range(self._max_repairs + 1):
            reply = await self._complete(messages)
            for key in usage:
                usage[key] += int(reply.usage.get(key, 0))
            try:
                decision = parse_reply(reply.tool_calls, reply.content)
            except ContractError as exc:
                if attempt >= self._max_repairs:
                    return replace(fallback_decision(self._on_error, "contract_fallback"), usage=usage)
                repair = "repaired"
                messages = [
                    *messages,
                    {"role": "assistant", "content": reply.content or json.dumps(reply.tool_calls)},
                    {"role": "user", "content": f"{self._prompts.render('contract_repair')}\n(Problem: {exc})"},
                ]
                continue
            return replace(decision, repair=decision.repair or repair, usage=usage)
        return replace(fallback_decision(self._on_error, "contract_fallback"), usage=usage)  # pragma: no cover


_ACKS = {
    "ok", "okay", "sure", "thanks", "thank you", "great", "mm-hmm", "mm hmm", "mhm", "uh-huh", "uh huh",
    "right", "alright", "all right", "got it", "cool", "fine", "yeah okay", "okay sure", "sounds good",
}  # fmt: skip
_STATUS = re.compile(
    r"\b(what'?s the update|any update|update so far|progress|how long|still working|is it done|are you there)\b"
)


class RuleDecider:
    """Deterministic stand-in: acknowledgements stay local, progress questions are status, the rest delegates.

    ``script`` (optional) is a list of decisions consumed in order before the rules apply.
    """

    def __init__(self, script: Sequence[dict[str, Any]] = (), *, filler: str = "Sure, one moment.") -> None:
        """Optionally preload scripted decisions."""
        self._script = list(script)
        self._filler = filler

    async def decide(self, context: DecisionContext) -> Decision:
        """Apply the script, then the rules."""
        if self._script:
            item = self._script.pop(0)
            return Decision(
                delegate=bool(item.get("delegate", True)),
                filler_text=str(item.get("filler_text", "")),
                request=str(item.get("request", REQUEST_TASK)),
            )
        text = re.sub(r"[^\w\s'-]", "", context.user_text.lower()).strip()
        if not text or text in _ACKS:
            return Decision(delegate=False, filler_text="")
        if context.state == WORKING and _STATUS.search(text) and " of " not in text:
            return Decision(delegate=True, filler_text="Let me check where that is.", request=REQUEST_STATUS)
        return Decision(delegate=True, filler_text=self._filler, request=REQUEST_TASK)
