# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""``AgentPort`` over the text prototype's ``FrontendBackendAgent``.

One runner per Realtime session. It owns the session's immutable
``SessionState`` and swaps it only when a call returns, so cancelling an
in-flight ``respond()`` (barge-in while thinking) leaves the conversation
exactly as it was. The agent itself is assembled per session with
``assemble_agent`` from the shared LLM clients, because each session has its
own tools and instructions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from prototypes.text_frontend_backend_agent.agent import FrontendBackendAgent, assemble_agent
from prototypes.text_frontend_backend_agent.backend_context import backend_system_prompt
from prototypes.text_frontend_backend_agent.config import Config
from prototypes.text_frontend_backend_agent.events import EventSink
from prototypes.text_frontend_backend_agent.llm import ChatClient
from prototypes.text_frontend_backend_agent.messages import AgentTurn, Message, RoleTotals
from prototypes.text_frontend_backend_agent.prompts import load_catalog, render
from prototypes.text_frontend_backend_agent.session import SessionState
from prototypes.text_frontend_backend_agent.tools import ToolSpec
from prototypes.voice_frontend_backend_agent.agent.history_repair import repair_interrupted_answer
from prototypes.voice_frontend_backend_agent.agent.instructions import ResolvedInstructions, session_agent_config
from prototypes.voice_frontend_backend_agent.agent.port import AgentReply, ReplyUsage, RoleUsage
from prototypes.voice_frontend_backend_agent.agent.tools import realtime_tools_to_specs, to_outgoing, to_results
from prototypes.voice_frontend_backend_agent.config import InstructionsConfig, ToolsConfig


@dataclass(frozen=True, slots=True)
class AgentClients:
    """LLM clients shared by every session (the OpenAI client is safe to share)."""

    backend: ChatClient
    frontend: ChatClient | None = None


def _role_usage(totals: RoleTotals) -> RoleUsage:
    usage = totals.usage
    return RoleUsage(
        calls=totals.calls,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        total_tokens=usage.total_tokens or usage.prompt_tokens + usage.completion_tokens,
        latency_ms=totals.latency_ms,
    )


def _reply(turn: AgentTurn) -> AgentReply:
    usage = turn.usage.usage
    reply_usage = ReplyUsage(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
        frontend=_role_usage(turn.usage.frontend),
        backend=_role_usage(turn.usage.backend),
    )
    if turn.tool_calls:
        return AgentReply(calls=to_outgoing(turn.tool_calls), usage=reply_usage)
    return AgentReply(text=turn.final_text or "", usage=reply_usage)


class TextAgentRunner:
    """One session's agent: the text prototype plus the session's state."""

    def __init__(
        self,
        *,
        base_config: Config,
        tools_config: ToolsConfig,
        instructions_config: InstructionsConfig,
        clients: AgentClients,
        sink: EventSink,
        session_id: str,
        seed_greeting: str = "",
    ) -> None:
        """Build the initial agent (no client tools, no instructions yet)."""
        self._base = base_config
        self._tools_config = tools_config
        self._instructions_config = instructions_config
        self._clients = clients
        self._sink = sink
        self._state = SessionState(session_id=session_id)
        self._agent: FrontendBackendAgent | None = None
        self.config: Config = base_config
        self.resolved_instructions: ResolvedInstructions | None = None
        self.tool_specs: tuple[ToolSpec, ...] = ()
        self.configure(tools=(), instructions="")
        if seed_greeting:
            self.seed_assistant(seed_greeting)

    # -- AgentPort ------------------------------------------------------------

    @property
    def backend_only(self) -> bool:
        """Whether the frontend is disabled."""
        return not self._base.frontend_enabled

    @property
    def state(self) -> SessionState:
        """The current (immutable) session state."""
        return self._state

    def configure(self, *, tools: Sequence[Mapping[str, Any]], instructions: str) -> None:
        """Rebuild the agent for new tools/instructions; the conversation state is kept."""
        if self._tools_config.source == "client":
            specs = realtime_tools_to_specs(tools)
        else:
            specs = self._tools_config.config_tool_specs
        config, resolved = session_agent_config(
            self._base, self._instructions_config, session_instructions=instructions, tools=specs
        )
        self._agent = assemble_agent(
            config,
            tools=specs,
            event_sink=self._sink,
            frontend_client=self._clients.frontend if config.frontend_enabled else None,
            backend_client=self._clients.backend,
        )
        self.config = config
        self.resolved_instructions = resolved
        self.tool_specs = tuple(specs)

    async def respond(self, text: str) -> AgentReply:
        """Run one user turn; the state is replaced only if the call completes."""
        assert self._agent is not None  # noqa: S101 - built in __init__
        turn, state = await self._agent.send(text, self._state)
        self._state = state
        return _reply(turn)

    async def resume(self, outputs: Mapping[str, str]) -> AgentReply:
        """Continue a suspended turn with the client's outputs."""
        assert self._agent is not None  # noqa: S101 - built in __init__
        turn, state = await self._agent.send_tool_results(to_results(outputs), self._state)
        self._state = state
        return _reply(turn)

    def repair_last_answer(self, full_text: str, replacement: str) -> None:
        """Rewrite every stored copy of the interrupted answer."""
        self._state = repair_interrupted_answer(
            self._state,
            full_text=full_text,
            replacement=replacement,
            frontend_enabled=self._base.frontend_enabled,
            backend_history=self._base.backend.conversation_history.keeps_backend_turns,
        )

    def seed_assistant(self, text: str) -> None:
        """Append assistant speech the agent did not produce (a greeting) to the owning history."""
        message = Message.assistant(text)
        if self._base.frontend_enabled:
            self._state = replace(self._state, frontend_history=self._state.frontend_history.append(message))
        else:
            self._state = replace(self._state, backend_history=self._state.backend_history.append(message))

    # -- introspection (tests, logs) -------------------------------------------

    def rendered_prompts(self) -> dict[str, str]:
        """The exact system prompts this session's agent sends."""
        catalog = load_catalog(self.config.prompts_path, self.config.prompts.inline)
        prompts = {"backend": backend_system_prompt(catalog, self.config)}
        if self.config.frontend_enabled:
            prompts["frontend"] = render(catalog.get(self.config.frontend.prompt_key), self.config)
        return prompts
