# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Voice-server configuration of the delegation prototype (plan section 11).

``load_delegation_config(path)`` reads a delegation YAML chain (``extends:``,
``${VAR:-default}``, strict keys against :data:`DEFAULTS`), resolves ``voice_profile``
against the declaring file and loads it with the voice package's
``load_voice_config`` unchanged. The gateway has its own file (``gateway.yaml``).
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from prototypes.text_frontend_backend_agent.config import interpolate_env
from prototypes.voice_frontend_backend_agent.config import VoiceConfig, deep_merge, load_voice_config

CONFIG_DIR = Path(__file__).resolve().parent / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "delegation_agent.yaml"
PROFILES_DIR = CONFIG_DIR / "profiles"

#: Keys resolved against the file that declares them.
IN_PATH_KEYS = ("voice_profile", "delegation.prompts.path", "backend.gateway_config")
#: Mappings whose keys are not checked (passed through as data).
_FREE_FORM = ("frontend.llm.extra_body",)

DEFAULTS: dict[str, Any] = {
    "voice_profile": "voice/tau3.yaml",
    "frontend": {
        "decider": "llm",
        "script": "",
        "llm": {
            "model": "nvidia/nvidia/nemotron-3.5-lightning",
            "base_url": "https://inference-api.nvidia.com/v1",
            "api_key": "",
            "temperature": 0.0,
            "max_tokens": 256,
            "timeout_s": 10,
            "extra_body": {},
        },
        "tool_choice": "named",
        "parallel_tool_calls": False,
        "timeout_ms": 4000,
        "hedge_after_ms": 1500,
        "warmup": True,
        "history": {"max_groups": 20},
        "backend_activity": {"max_items": 6, "preview_chars": 120},
    },
    "delegation": {
        "on_contract_error": "delegate",
        "max_repair_attempts": 1,
        "speak_when_delegating": True,
        "guards": {
            "backend_question_needs_delegate": True,
            "backchannel_words": [
                "mm-hmm",
                "mhm",
                "uh-huh",
                "uh huh",
                "hmm",
                "mm",
                "okay",
                "ok",
                "okay sure",
                "sure",
                "alright",
                "all right",
                "right",
                "thanks",
                "thank you",
                "got it",
                "cool",
            ],
        },
        "prompts": {"path": "prompts.yaml"},
    },
    "backend": {
        "link": "websocket",
        "url": "ws://localhost:8790/v1/backend",
        "open_timeout_s": 45,
        "gateway_config": "gateway.fake.yaml",
        "simulated_delay": {"seconds": 0, "where": "per_delegation"},
        "steer_mode": "auto",
        "status_verbalizer": {"mode": "llm", "timeout_ms": 3000},
    },
    "session": {"update_after_start": "error"},
    "output": {
        "hold_max_ms": 4000,
        "on_stale": {"backend_answer": "keep", "status": "drop", "filler": "drop", "apology": "keep"},
    },
    "tools": {"executor": "wire", "result_timeout_s": 120, "batch_window_ms": 30},
    "instructions": {"apply_to": ["backend"]},
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "frontend.decider": ("llm", "scripted"),
    "frontend.tool_choice": ("named", "required", "auto"),
    "delegation.on_contract_error": ("delegate", "direct_empty"),
    "backend.link": ("websocket", "in_process_fake"),
    "backend.simulated_delay.where": ("per_delegation", "per_tool_call"),
    "backend.steer_mode": ("auto", "steer_only"),
    "backend.status_verbalizer.mode": ("llm", "template"),
    "session.update_after_start": ("error", "ignore"),
    "tools.executor": ("wire", "local"),
}
_STALE_KINDS = ("backend_answer", "status", "filler", "apology")


class DelegationConfigError(ValueError):
    """Invalid delegation configuration."""


@dataclass(frozen=True, slots=True)
class FrontendLLMConfig:
    """The frontend model endpoint."""

    model: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int
    timeout_s: float
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FrontendConfig:
    """The ``frontend`` section."""

    decider: str
    script: str
    llm: FrontendLLMConfig
    tool_choice: str
    parallel_tool_calls: bool
    timeout_ms: int
    hedge_after_ms: int
    warmup: bool
    history_max_groups: int
    activity_max_items: int
    activity_preview_chars: int


@dataclass(frozen=True, slots=True)
class DelegationSettings:
    """The ``delegation`` section."""

    on_contract_error: str
    max_repair_attempts: int
    speak_when_delegating: bool
    backend_question_needs_delegate: bool
    backchannel_words: tuple[str, ...]
    prompts_path: Path


@dataclass(frozen=True, slots=True)
class BackendSettings:
    """The ``backend`` section."""

    link: str
    url: str
    open_timeout_s: float
    gateway_config: Path | None
    delay_seconds: float
    delay_where: str
    steer_mode: str
    verbalizer_mode: str
    verbalizer_timeout_ms: int


@dataclass(frozen=True, slots=True)
class OutputSettings:
    """The ``output`` section."""

    hold_max_ms: int
    on_stale: dict[str, str]


@dataclass(frozen=True, slots=True)
class ToolSettings:
    """The ``tools`` section (``tools.source`` itself lives in the voice profile)."""

    executor: str
    result_timeout_s: float
    batch_window_ms: int


@dataclass(frozen=True, slots=True)
class DelegationConfig:
    """Everything the delegation voice server needs."""

    voice: VoiceConfig
    frontend: FrontendConfig
    delegation: DelegationSettings
    backend: BackendSettings
    update_after_start: str
    output: OutputSettings
    tools: ToolSettings
    instructions_apply_to: tuple[str, ...]
    source_files: tuple[Path, ...] = ()
    effective: dict[str, Any] = field(default_factory=dict)

    @property
    def config_hash(self) -> str:
        """Short hash of the effective settings (secrets excluded), for grouping runs."""
        blob = json.dumps(self.effective, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:12]


def load_delegation_config(path: str | Path = DEFAULT_CONFIG_PATH) -> DelegationConfig:
    """Load a delegation config file (or profile) and its voice profile."""
    merged, files = _load_chain(Path(path), ())
    _check_unknown(merged, DEFAULTS, files)
    return build_delegation_config(merged, files)


def build_delegation_config(merged: dict[str, Any], files: tuple[Path, ...] = ()) -> DelegationConfig:
    """Validate a merged mapping (paths already resolved) and load its voice profile."""
    for dotted, allowed in _ENUMS.items():
        value = _get(merged, dotted)
        if value not in allowed:
            raise DelegationConfigError(f"{dotted}: {value!r} is not one of {list(allowed)}")
    voice_path = Path(str(merged["voice_profile"]))
    if not voice_path.is_absolute():
        voice_path = (CONFIG_DIR / voice_path).resolve()
    voice = load_voice_config(voice_path)
    fr, dl, be, out, tl = (merged[k] for k in ("frontend", "delegation", "backend", "output", "tools"))
    llm = fr["llm"]
    frontend = FrontendConfig(
        decider=fr["decider"],
        script=str(fr.get("script") or ""),
        llm=FrontendLLMConfig(
            model=str(llm["model"]),
            base_url=str(llm["base_url"]),
            api_key=str(llm.get("api_key") or ""),
            temperature=_float(llm["temperature"], "frontend.llm.temperature"),
            max_tokens=_int(llm["max_tokens"], "frontend.llm.max_tokens"),
            timeout_s=_float(llm["timeout_s"], "frontend.llm.timeout_s"),
            extra_body=dict(llm.get("extra_body") or {}),
        ),
        tool_choice=fr["tool_choice"],
        parallel_tool_calls=_bool(fr["parallel_tool_calls"], "frontend.parallel_tool_calls"),
        timeout_ms=_int(fr["timeout_ms"], "frontend.timeout_ms", minimum=1),
        hedge_after_ms=_int(fr["hedge_after_ms"], "frontend.hedge_after_ms"),
        warmup=_bool(fr["warmup"], "frontend.warmup"),
        history_max_groups=_int(fr["history"]["max_groups"], "frontend.history.max_groups", minimum=1),
        activity_max_items=_int(fr["backend_activity"]["max_items"], "frontend.backend_activity.max_items"),
        activity_preview_chars=_int(
            fr["backend_activity"]["preview_chars"], "frontend.backend_activity.preview_chars", minimum=10
        ),
    )
    delegation = DelegationSettings(
        on_contract_error=dl["on_contract_error"],
        max_repair_attempts=_int(dl["max_repair_attempts"], "delegation.max_repair_attempts"),
        speak_when_delegating=_bool(dl["speak_when_delegating"], "delegation.speak_when_delegating"),
        backend_question_needs_delegate=_bool(
            dl["guards"]["backend_question_needs_delegate"], "delegation.guards.backend_question_needs_delegate"
        ),
        backchannel_words=tuple(str(word).lower() for word in dl["guards"]["backchannel_words"] or ()),
        prompts_path=_path(dl["prompts"]["path"]),
    )
    gateway_config = str(be.get("gateway_config") or "")
    backend = BackendSettings(
        link=be["link"],
        url=str(be["url"]),
        open_timeout_s=_float(be["open_timeout_s"], "backend.open_timeout_s", minimum=1),
        gateway_config=_path(gateway_config) if gateway_config else None,
        delay_seconds=_float(be["simulated_delay"]["seconds"], "backend.simulated_delay.seconds"),
        delay_where=be["simulated_delay"]["where"],
        steer_mode=be["steer_mode"],
        verbalizer_mode=be["status_verbalizer"]["mode"],
        verbalizer_timeout_ms=_int(be["status_verbalizer"]["timeout_ms"], "backend.status_verbalizer.timeout_ms"),
    )
    on_stale = dict(out["on_stale"])
    for kind, value in on_stale.items():
        if kind not in _STALE_KINDS or value not in ("keep", "drop"):
            raise DelegationConfigError(f"output.on_stale.{kind}: {value!r} (kinds {_STALE_KINDS}, keep | drop)")
    output = OutputSettings(hold_max_ms=_int(out["hold_max_ms"], "output.hold_max_ms", minimum=1), on_stale=on_stale)
    tools = ToolSettings(
        executor=tl["executor"],
        result_timeout_s=_float(tl["result_timeout_s"], "tools.result_timeout_s", minimum=1),
        batch_window_ms=_int(tl["batch_window_ms"], "tools.batch_window_ms"),
    )
    apply_to = tuple(merged["instructions"]["apply_to"] or ())
    if any(role not in ("backend", "frontend") for role in apply_to):
        raise DelegationConfigError(f"instructions.apply_to: {list(apply_to)} (allowed: backend, frontend)")
    # Cross-checks (plan 11.1).
    if tools.executor == "local" and voice.tools.source != "config":
        raise DelegationConfigError("tools.executor: local requires the voice profile's tools.source: config")
    if tools.executor == "wire" and voice.tools.source != "client":
        raise DelegationConfigError("tools.executor: wire requires the voice profile's tools.source: client")
    if backend.link == "in_process_fake" and backend.gateway_config is None:
        raise DelegationConfigError("backend.link: in_process_fake needs backend.gateway_config")
    effective = copy.deepcopy(merged)
    effective["frontend"]["llm"]["api_key"] = "***" if frontend.llm.api_key else ""
    return DelegationConfig(
        voice=voice,
        frontend=frontend,
        delegation=delegation,
        backend=backend,
        update_after_start=merged["session"]["update_after_start"],
        output=output,
        tools=tools,
        instructions_apply_to=apply_to,
        source_files=(*files, *voice.source_files),
        effective=effective,
    )


# -- loading -----------------------------------------------------------------------------


def _load_chain(path: Path, seen: tuple[Path, ...]) -> tuple[dict[str, Any], tuple[Path, ...]]:
    path = path.expanduser().resolve()
    if path in seen:
        raise DelegationConfigError("extends cycle: " + " -> ".join(str(p) for p in (*seen, path)))
    if not path.exists():
        raise DelegationConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise DelegationConfigError(f"{path}: config root must be a mapping")
    raw = interpolate_env(raw)
    for key in IN_PATH_KEYS:
        value = _get(raw, key)
        if isinstance(value, str) and value.strip():
            candidate = Path(value.strip()).expanduser()
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            _set(raw, key, str(candidate.resolve()))
    parent = raw.pop("extends", "") or ""
    if parent:
        parent_path = Path(parent)
        if not parent_path.is_absolute():
            parent_path = path.parent / parent_path
        merged, files = _load_chain(parent_path, (*seen, path))
    else:
        merged, files = copy.deepcopy(DEFAULTS), ()
    return deep_merge(merged, raw, source=str(path), defaults=DEFAULTS), (*files, path)


def _check_unknown(merged: dict[str, Any], schema: dict[str, Any], files: tuple[Path, ...], prefix: str = "") -> None:
    for key, value in merged.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if key not in schema:
            raise DelegationConfigError(
                f"unknown key {dotted!r} (loaded from {', '.join(str(f) for f in files) or '<defaults>'})"
            )
        if dotted in _FREE_FORM:
            continue
        if isinstance(schema[key], dict) and isinstance(value, dict):
            _check_unknown(value, schema[key], files, dotted)


def _get(mapping: dict[str, Any], dotted: str) -> Any:
    node: Any = mapping
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set(mapping: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = mapping
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def _path(value: Any) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (CONFIG_DIR / path).resolve()


def _int(value: Any, key: str, *, minimum: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DelegationConfigError(f"{key}: {value!r} is not an integer") from exc
    if number < minimum:
        raise DelegationConfigError(f"{key}: {number} is below {minimum}")
    return number


def _float(value: Any, key: str, *, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DelegationConfigError(f"{key}: {value!r} is not a number") from exc
    if number < minimum:
        raise DelegationConfigError(f"{key}: {number} is below {minimum}")
    return number


def _bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "yes", "no"):
        return value.lower() in ("true", "1", "yes")
    raise DelegationConfigError(f"{key}: {value!r} is not a boolean")
