# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prompt variants: named ``{% if features.<name> %}`` blocks in the prompt catalogs.

Each catalog gets a ``features`` mapping from config (``prompt_features`` in
``delegation_agent.yaml`` for the frontend catalog, in ``gateway.yaml`` for the backend
one). A feature that is off, or not set, renders nothing, so with every feature off the
prompts are byte-identical to the baseline (tau3-failure-fixes-plan.md section 1).
Stdlib only: the gateway imports it too.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

#: Frontend (``prompts.yaml``) variants and their defaults.
FRONTEND_FEATURES: dict[str, bool] = {"replay_intent": False}
#: Backend (``prompts.backend.yaml``) variants and their defaults.
BACKEND_FEATURES: dict[str, bool] = {
    "spelling_v2": False,
    "spoken_output": False,
    "write_consent": False,
    "domain_notes": False,
}


class PromptFeatures(dict[str, bool]):
    """A feature map where an unknown name reads as off (``features.x`` in Jinja)."""

    def __missing__(self, key: str) -> bool:
        """An unset feature is off."""
        return False


def features(values: Mapping[str, object] | None, known: Mapping[str, bool]) -> PromptFeatures:
    """``known`` defaults overridden by ``values``; an unknown name is an error."""
    merged = dict(known)
    for name, value in (values or {}).items():
        if name not in known:
            raise ValueError(f"unknown prompt feature {name!r}; known: {sorted(known)}")
        merged[name] = _bool(value, name)
    return PromptFeatures(merged)


def sha256_text(text: str) -> str:
    """The fingerprint of one rendered prompt or catalog (first 16 hex digits)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "yes", "no", "on", "off"):
        return value.lower() in ("true", "1", "yes", "on")
    raise ValueError(f"prompt feature {name!r}: {value!r} is not a boolean")
