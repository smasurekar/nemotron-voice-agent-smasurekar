# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prompt catalog loading and placeholder rendering.

Placeholders are substituted by literal replacement rather than ``str.format``:
the prompts contain JSON examples full of braces, and ``format`` would either
choke on them or require escaping every one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from prototypes.text_frontend_backend_agent.config import Config
from prototypes.text_frontend_backend_agent.errors import ConfigError

PERSONA = "{persona}"
DOMAIN_POLICY = "{domain_policy}"
CAPABILITIES = "{capabilities}"
UNSUPPORTED_REPLY = "{unsupported_reply}"
AGENT_NAME = "{agent_name}"


@dataclass(frozen=True, slots=True)
class PromptCatalog:
    """Prompt texts keyed by catalog key."""

    prompts: dict[str, str]

    def get(self, key: str) -> str:
        """Return the prompt for ``key``."""
        if key not in self.prompts:
            raise ConfigError(f"prompt key not found in catalog: {key!r}")
        return self.prompts[key]


def load_catalog(path: str | Path, inline: dict[str, str] | None = None) -> PromptCatalog:
    """Load a ``prompts.yaml`` catalog, applying inline overrides."""
    prompts: dict[str, str] = {}
    catalog_path = Path(path)
    if catalog_path.is_file():
        data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"prompt catalog must be a mapping: {catalog_path}")
        for key, item in data.items():
            if isinstance(item, dict) and "content" in item:
                prompts[str(key)] = str(item["content"])
            elif isinstance(item, str):
                prompts[str(key)] = item
    elif not inline:
        raise ConfigError(f"prompt catalog not found: {catalog_path}")
    prompts.update({str(key): str(value) for key, value in (inline or {}).items()})
    if not prompts:
        raise ConfigError("prompt catalog is empty")
    return PromptCatalog(prompts=prompts)


def render(template: str, config: Config) -> str:
    """Substitute the domain placeholders in ``template``."""
    capabilities = "\n".join(f"- {item}" for item in config.domain.capabilities)
    replacements = {
        PERSONA: config.persona,
        DOMAIN_POLICY: config.domain.policy.strip(),
        CAPABILITIES: capabilities or "- (no capability list configured)",
        UNSUPPORTED_REPLY: config.domain.unsupported_reply,
        AGENT_NAME: config.name,
    }
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered.strip()
