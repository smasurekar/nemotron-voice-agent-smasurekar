# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Jinja2 rendering of the backend prompt catalog (``config/prompts.backend.yaml``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import yaml

REQUIRED_KEYS = (
    "backend_soul",
    "backend_system",
    "context_user",
    "context_frontend",
    "context_status",
    "context_controller",
    "context_delivery_note",
    "context_requeued_request",
    "context_side_effect",
    "context_block",
    "backend_delegation_message",
    "backend_steer",
    "backend_error_spoken",
    "backend_unavailable",
)


class TemplateError(ValueError):
    """A missing or broken template."""


class BackendTemplates:
    """``render(key, **vars)`` over a YAML catalog of Jinja templates (undefined variables fail)."""

    def __init__(self, path: str | Path) -> None:
        """Load and compile every template; missing required keys fail now."""
        self.path = Path(path)
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise TemplateError(f"{self.path}: expected a mapping of templates")
        missing = [key for key in REQUIRED_KEYS if key not in data]
        if missing:
            raise TemplateError(f"{self.path}: missing template(s) {missing}")
        env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False, keep_trailing_newline=False)  # noqa: S701 - plain text prompts
        try:
            self._templates = {key: env.from_string(str(value)) for key, value in data.items()}
        except jinja2.TemplateError as exc:
            raise TemplateError(f"{self.path}: {exc}") from exc

    def render(self, key: str, **variables: Any) -> str:
        """Render ``key``; raises :class:`TemplateError` for unknown keys or undefined variables."""
        template = self._templates.get(key)
        if template is None:
            raise TemplateError(f"unknown template {key!r}")
        try:
            return template.render(**variables).strip()
        except jinja2.TemplateError as exc:
            raise TemplateError(f"{key}: {exc}") from exc
