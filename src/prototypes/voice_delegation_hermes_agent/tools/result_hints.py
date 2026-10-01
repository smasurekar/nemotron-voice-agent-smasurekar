# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A recovery note appended to a failed identity lookup's result (tau3-identity-fixes-plan.md I3, I4).

One instance per session. It counts misses of the watched lookups (``result_hints.tools``):
an output matching ``failure_pattern``, and, with ``count_local_invalid``, a local
``invalid`` answer. The first external miss gets ``message_key`` (I3), later ones
``escalate_message_key`` (I4; "" = the first message again). At most ``max_hints``
notes until a watched lookup succeeds, which resets the count. Local answers, transient
errors, successes and other tools' outputs are never changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prototypes.voice_frontend_backend_agent.normalization.arguments import ResultHintSettings, render_message


@dataclass(frozen=True, slots=True)
class Hint:
    """One appended note."""

    output: str
    message_key: str
    miss: int


class ResultHints:
    """The per-session miss counter and the note it appends."""

    def __init__(self, settings: ResultHintSettings, *, message: str, escalate_message: str = "") -> None:
        """``message`` and ``escalate_message`` are the catalog texts of the two keys."""
        self._settings = settings
        self._tools = frozenset(settings.tools)
        self._failure = re.compile(settings.failure_pattern)
        self._message = render_message(message)
        self._escalate = render_message(escalate_message) if escalate_message else ""
        self._misses = 0
        self._hints = 0

    def watches(self, tool: str) -> bool:
        """Whether ``tool`` is one of the watched lookups."""
        return tool in self._tools

    def on_local_invalid(self, tool: str) -> None:
        """A local ``invalid`` answer for ``tool`` (never annotated; may count as a miss)."""
        if self._settings.count_local_invalid and self.watches(tool):
            self._misses += 1

    def on_output(self, tool: str, output: str) -> Hint | None:
        """The client's output of one call; a :class:`Hint` when a note is appended to it."""
        if not self.watches(tool):
            return None
        if self._failure.search(output):
            self._misses += 1
            if self._hints >= self._settings.max_hints:
                return None
            self._hints += 1
            first = self._misses == 1 or not self._escalate
            text, key = (
                (self._message, self._settings.message_key)
                if first
                else (self._escalate, self._settings.escalate_message_key)
            )
            return Hint(output=f"{output.rstrip()}\n\n{text}", message_key=key, miss=self._misses)
        if not output.lstrip().startswith("Error"):
            self._misses = 0
            self._hints = 0
        return None
