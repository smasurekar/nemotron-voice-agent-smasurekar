# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The agent port the engine talks to.

The engine never sees the text prototype directly. It asks an :class:`AgentPort`
to respond to user text or to resume with tool outputs, and gets back an
:class:`AgentReply` carrying spoken text *or* tool calls, never both, mirroring
the text prototype's ``AgentTurn``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class OutgoingCall:
    """One tool call to surface as a Realtime ``function_call`` item."""

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ReplyUsage:
    """Token usage for ``response.done.usage``."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AgentReply:
    """Spoken text xor tool calls."""

    text: str | None = None
    calls: tuple[OutgoingCall, ...] = ()
    usage: ReplyUsage = field(default_factory=ReplyUsage)

    def __post_init__(self) -> None:
        """Enforce the text/calls exclusivity."""
        if (self.text is None) == (not self.calls):
            raise ValueError("AgentReply carries exactly one of text or calls")


@runtime_checkable
class AgentPort(Protocol):
    """One session's conversational agent."""

    @property
    def backend_only(self) -> bool:
        """Whether the agent runs without a frontend (no filler source)."""

    def configure(self, *, tools: Sequence[Mapping[str, Any]], instructions: str) -> None:
        """(Re)build for the session's tools and instructions; conversation state is kept."""

    async def respond(self, text: str) -> AgentReply:
        """Answer one user turn. Cancellation must leave the conversation state untouched."""

    async def resume(self, outputs: Mapping[str, str]) -> AgentReply:
        """Continue a turn with the client's function-call outputs, keyed by ``call_id``."""

    def repair_last_answer(self, full_text: str, replacement: str) -> None:
        """Rewrite every stored copy of the last spoken answer (raises ``HistoryRepairError``)."""

    def seed_assistant(self, text: str) -> None:
        """Record assistant speech the agent did not produce (greetings)."""
