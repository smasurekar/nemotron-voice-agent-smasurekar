# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

r"""Voice server of the frontend-delegation prototype: ``WS /v1/realtime`` (OpenAI Realtime GA).

It reuses the voice package's server (``build_app``: health, TLS, the browser page at
``/``) and swaps the text agent for :class:`DelegationTurnManager` through
``SessionHooks``. The backend runs in the gateway (``sidecar.gateway_server``), which
starts one Hermes worker process per session.

    PYTHONPATH=src uv run python -m prototypes.voice_delegation_hermes_agent.server \
        --config src/prototypes/voice_delegation_hermes_agent/config/profiles/tau3_eval.yaml

``--stub-speech`` replaces Riva ASR/TTS; ``--stub-backend`` runs an in-process gateway
with a fake backend agent and the rule-based frontend (no GPU, no LLM, no Hermes).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from loguru import logger

from prototypes.text_frontend_backend_agent.prompts import load_catalog
from prototypes.voice_delegation_hermes_agent.config import (
    DEFAULT_CONFIG_PATH,
    DelegationConfig,
    load_delegation_config,
)
from prototypes.voice_delegation_hermes_agent.engine.backend_link import BackendLink, WebSocketBackendLink
from prototypes.voice_delegation_hermes_agent.engine.spelling_hold import SpellingHoldPredicate
from prototypes.voice_delegation_hermes_agent.engine.turn_manager import DelegationTurnManager, SessionParts
from prototypes.voice_delegation_hermes_agent.frontend.decider import Decider, LLMDecider, RuleDecider
from prototypes.voice_delegation_hermes_agent.frontend.llm import ChatModel, OpenAIChatModel
from prototypes.voice_delegation_hermes_agent.frontend.prompts import PromptCatalog, load_prompts
from prototypes.voice_delegation_hermes_agent.frontend.status_verbalizer import (
    LLMVerbalizer,
    StatusVerbalizer,
    TemplateVerbalizer,
)
from prototypes.voice_delegation_hermes_agent.tools.result_hints import ResultHints
from prototypes.voice_frontend_backend_agent.engine.turn_api import TurnContext
from prototypes.voice_frontend_backend_agent.normalization.arguments import ArgumentNormalizer
from prototypes.voice_frontend_backend_agent.normalization.transcript import TranscriptNormalizer
from prototypes.voice_frontend_backend_agent.server import ServerOptions, SessionHooks, build_app
from prototypes.voice_frontend_backend_agent.speech.ports import SpeechServices


class DelegationRuntime:
    """Resources shared by every session of one server (clients, prompts, normalizer templates)."""

    def __init__(self, config: DelegationConfig, *, chat_model: ChatModel | None = None) -> None:
        """Load prompts and build the shared model client (injectable for tests)."""
        self.config = config
        self.prompts: PromptCatalog = load_prompts(
            config.delegation.prompts_path, prompt_features=config.prompt_features
        )
        needs_llm = config.frontend.decider == "llm" or config.backend.verbalizer_mode == "llm"
        self.chat_model = (
            chat_model if chat_model is not None else (OpenAIChatModel(config.frontend.llm) if needs_llm else None)
        )
        voice = config.voice
        self._script = _load_script(config.frontend.script)
        self._argument_templates = ("", "", "")
        self._hint_templates = ("", "")  # result_hints: (message_key, escalate_message_key) texts
        arguments = voice.normalization.tool_arguments
        if arguments.enabled:
            catalog = load_catalog(voice.agent.prompts_path, voice.agent.prompts.inline)
            guard = arguments.retry_guard
            escalate = arguments.escalate_invalid_message_key
            self._argument_templates = (
                catalog.get(arguments.invalid_message_key),
                catalog.get(guard.message_key) if guard.enabled else "",
                catalog.get(escalate) if escalate else "",
            )
            hints = arguments.result_hints
            if hints.enabled:
                self._hint_templates = (
                    catalog.get(hints.message_key),
                    catalog.get(hints.escalate_message_key) if hints.escalate_message_key else "",
                )
        self.gateway_health: dict[str, Any] = {}

    def parts(self, context: TurnContext) -> SessionParts:
        """Per-session collaborators (plan 12.1: every one chosen by config)."""
        config, voice = self.config, self.config.voice
        arguments = (
            ArgumentNormalizer(
                voice.normalization.tool_arguments,
                transcript=voice.normalization.transcript,
                invalid_template=self._argument_templates[0],
                already_failed_template=self._argument_templates[1],
                escalate_invalid_template=self._argument_templates[2],
            )
            if voice.normalization.tool_arguments.enabled
            else None
        )
        hints = voice.normalization.tool_arguments.result_hints
        result_hints = (
            ResultHints(hints, message=self._hint_templates[0], escalate_message=self._hint_templates[1])
            if arguments is not None and hints.enabled
            else None
        )
        hold = config.delegation.spelling_hold
        return SessionParts(
            decider=self._decider(),
            verbalizer=self._verbalizer(),
            link=self._link(),
            prompts=self.prompts,
            transcript_normalizer=TranscriptNormalizer(voice.normalization.transcript)
            if voice.normalization.transcript.enabled
            else None,
            argument_normalizer=arguments,
            result_hints=result_hints,
            local_tools=tuple(voice.tools.config_tool_specs) if config.tools.executor == "local" else (),
            spelling_hold=SpellingHoldPredicate(
                voice.normalization.transcript, complete_patterns=hold.complete_patterns, arguments=arguments
            )
            if hold.enabled
            else None,
        )

    def turn_manager(self, context: TurnContext) -> DelegationTurnManager:
        """The ``SessionHooks.turn_manager_factory``."""
        return DelegationTurnManager(context, self.config, self.parts(context))

    def _decider(self) -> Decider:
        frontend = self.config.frontend
        if frontend.decider == "scripted":
            return RuleDecider(self._script)
        assert self.chat_model is not None  # noqa: S101 - built when decider is llm
        delegation = self.config.delegation
        return LLMDecider(
            self.chat_model,
            self.prompts,
            timeout_ms=frontend.timeout_ms,
            max_repair_attempts=delegation.max_repair_attempts,
            on_contract_error=delegation.on_contract_error,
            tool_choice=frontend.tool_choice,
            parallel_tool_calls=frontend.parallel_tool_calls,
            hedge_after_ms=frontend.hedge_after_ms,
        )

    def _verbalizer(self) -> StatusVerbalizer:
        backend = self.config.backend
        if backend.verbalizer_mode == "llm" and self.chat_model is not None:
            return LLMVerbalizer(self.chat_model, self.prompts, timeout_ms=backend.verbalizer_timeout_ms)
        return TemplateVerbalizer(self.prompts)

    def _link(self) -> BackendLink:
        backend = self.config.backend
        if backend.link == "in_process_fake":
            from prototypes.voice_delegation_hermes_agent.sidecar.gateway_config import load_gateway_config
            from prototypes.voice_delegation_hermes_agent.sidecar.inprocess_link import InProcessBackendLink

            assert backend.gateway_config is not None  # noqa: S101 - validated in config
            return InProcessBackendLink(load_gateway_config(backend.gateway_config))
        return WebSocketBackendLink(backend.url, open_timeout_s=min(10.0, backend.open_timeout_s))

    async def startup(self) -> None:
        """Warm the frontend connection; check the gateway (reachable, enough capacity)."""
        await self._warmup()
        backend = self.config.backend
        if backend.link != "websocket":
            return
        url = _health_url(backend.url)
        try:
            self.gateway_health = await asyncio.to_thread(_get_json, url)
        except Exception as exc:  # noqa: BLE001 - the gateway may start later; sessions will report it
            logger.warning(f"backend gateway not reachable at {url} ({exc}); sessions will fail until it is up")
            return
        capacity = int(self.gateway_health.get("max_sessions") or 0)
        wanted = self.config.voice.server.max_sessions
        if capacity and capacity < wanted:
            raise RuntimeError(
                f"server.max_sessions ({wanted}) exceeds the gateway's max_sessions ({capacity}); "
                "lower FDH_MAX_SESSIONS here or raise it on the gateway"
            )
        logger.info(f"backend gateway {url}: {self.gateway_health}")

    async def _warmup(self) -> None:
        """First request of a fresh process pays connection setup (measured: >4 s): pay it here."""
        if not self.config.frontend.warmup or self.chat_model is None or self.config.frontend.decider != "llm":
            return
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self.chat_model.complete([{"role": "user", "content": "Say OK."}]), timeout=20)
        except Exception as exc:  # noqa: BLE001 - a failed warm-up only costs the first turn's latency
            logger.warning(f"frontend warm-up failed ({type(exc).__name__}: {exc}); the first turn may be slow")
            return
        logger.info(f"frontend warm-up done in {time.perf_counter() - started:.2f}s")

    async def shutdown(self) -> None:
        """Close the shared model client."""
        close = getattr(self.chat_model, "aclose", None)
        if close is not None:
            await close()

    def health(self) -> dict[str, Any]:
        """Extra ``/health`` fields."""
        backend = self.config.backend
        return {
            "prototype": "frontend-delegation-hermes",
            "config_hash": self.config.config_hash,
            "features": self.config.features,
            "backend": {"link": backend.link, "url": backend.url if backend.link == "websocket" else ""},
            "frontend": {"decider": self.config.frontend.decider, "model": self.config.frontend.llm.model},
        }


def build_delegation_app(
    config: DelegationConfig,
    *,
    options: ServerOptions | None = None,
    services: SpeechServices | None = None,
    chat_model: ChatModel | None = None,
) -> FastAPI:
    """The voice package's app with the delegation turn manager plugged in."""
    runtime = DelegationRuntime(config, chat_model=chat_model)
    hooks = SessionHooks(
        turn_manager_factory=runtime.turn_manager,
        startup=runtime.startup,
        shutdown=runtime.shutdown,
        health_extra=runtime.health,
        title="Voice Frontend Delegation to Hermes (prototype)",
    )
    app = build_app(config.voice, options=options, services=services, session_hooks=hooks)
    app.state.delegation = runtime
    return app


def _health_url(ws_url: str) -> str:
    base = ws_url.replace("wss://", "https://").replace("ws://", "http://")
    return base.split("/v1/", 1)[0].rstrip("/") + "/health"


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - operator-configured local URL
        return json.loads(response.read().decode())


def _load_script(value: str) -> list[dict[str, Any]]:
    if not value:
        return []
    path = Path(value)
    text = path.read_text(encoding="utf-8") if path.exists() else value
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("frontend.script must be a JSON list of decisions")
    return data


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="delegation_agent.yaml or a profile")
    parser.add_argument("--host", default=None, help="override server.host")
    parser.add_argument("--port", type=int, default=None, help="override server.port")
    parser.add_argument("--max-sessions", type=int, default=None, help="override server.max_sessions")
    parser.add_argument("--backend-url", default=None, help="override backend.url (the gateway WebSocket)")
    parser.add_argument("--tls", action="store_true", help="serve https/wss (the browser microphone needs it)")
    parser.add_argument("--tls-cert", default="", help="PEM certificate for --tls")
    parser.add_argument("--tls-key", default="", help="PEM private key for --tls")
    parser.add_argument("--stub-speech", action="store_true", help="energy VAD + stub ASR + tone TTS (no GPU)")
    parser.add_argument(
        "--stub-backend", action="store_true", help="in-process gateway with a fake backend and the rule-based frontend"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Load configuration and serve."""
    import uvicorn

    from prototypes.voice_frontend_backend_agent.server import _tls_files

    try:
        from dotenv import load_dotenv

        load_dotenv(Path.cwd() / ".env", override=False)
    except ImportError:  # pragma: no cover - python-dotenv is a core dependency
        pass
    args = _parse_args(argv)
    config = load_delegation_config(args.config)
    server = config.voice.server
    server = replace(
        server,
        host=args.host or server.host,
        port=args.port if args.port is not None else server.port,
        max_sessions=args.max_sessions if args.max_sessions is not None else server.max_sessions,
    )
    config = replace(config, voice=replace(config.voice, server=server))
    if args.backend_url:
        config = replace(config, backend=replace(config.backend, url=args.backend_url))
    if args.stub_backend:
        config = replace(
            config,
            backend=replace(config.backend, link="in_process_fake", verbalizer_mode="template"),
            frontend=replace(config.frontend, decider="scripted"),
        )
    logger.remove()
    logger.add(sys.stderr, level=config.voice.logging.level)
    options = ServerOptions(stub_speech=args.stub_speech, tls=args.tls)
    ssl_kwargs = _tls_files(args) if args.tls else {}
    app = build_delegation_app(config, options=options)
    logger.info(f"loaded {', '.join(str(path) for path in config.source_files)} (config hash {config.config_hash})")
    logger.info(
        f"frontend={config.frontend.decider}:{config.frontend.llm.model} backend={config.backend.link}:"
        f"{config.backend.url} tools={config.tools.executor} delay={config.backend.delay_seconds:g}s "
        f"VAD silence={config.voice.turn_detection.silence_duration_ms}ms"
    )
    logger.info(f"features {config.features}")
    uvicorn.run(
        app,
        host=server.host,
        port=server.port,
        log_level=config.voice.logging.level.lower(),
        ws_max_size=16 * 1024 * 1024,
        ws_ping_interval=server.ws_ping_interval_s or None,
        ws_ping_timeout=server.ws_ping_timeout_s,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
