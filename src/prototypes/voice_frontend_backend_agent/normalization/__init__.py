# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Normalization between speech and the agent.

* :mod:`.transcript`: the ASR hook (spoken identifiers -> written form);
* :mod:`.arguments`: the tool-argument hook (canonicalize, validate, retry guard);
* :mod:`.rules`: the pure, language-specific token rules both hooks share;
* :mod:`.prompts`: the optional frontend prompt note.

The TTS-side ``TextNormalizer`` (``speech/ports.py``) may move here later.
See ``misc/prototypes/voice-frontend-backend-agent-normalization-plan.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prototypes.voice_frontend_backend_agent.normalization.arguments import ToolArgumentSettings
from prototypes.voice_frontend_backend_agent.normalization.transcript import TranscriptSettings

#: Event kinds (their user-content fields are redacted by ``logging.redact_content``).
TRANSCRIPT_NORMALIZED = "transcript_normalized"
ARGUMENT_NORMALIZED = "argument_normalized"
CALL_ANSWERED_LOCALLY = "call_answered_locally"
LOCAL_ROUNDS_EXHAUSTED = "local_rounds_exhausted"


@dataclass(frozen=True, slots=True)
class NormalizationSettings:
    """The ``normalization`` config section (every feature off by default)."""

    transcript: TranscriptSettings = field(default_factory=TranscriptSettings)
    tool_arguments: ToolArgumentSettings = field(default_factory=ToolArgumentSettings)

    def summary(self) -> dict[str, object]:
        """What is on, for ``session_start`` (no user content)."""
        guard = self.tool_arguments.retry_guard
        return {
            "transcript": self.transcript.enabled,
            "tool_arguments": [f"{rule.tool}.{rule.argument}" for rule in self.tool_arguments.rules]
            if self.tool_arguments.enabled
            else [],
            "retry_guard": self.tool_arguments.enabled and guard.enabled,
        }


__all__ = [
    "ARGUMENT_NORMALIZED",
    "CALL_ANSWERED_LOCALLY",
    "LOCAL_ROUNDS_EXHAUSTED",
    "TRANSCRIPT_NORMALIZED",
    "NormalizationSettings",
]
