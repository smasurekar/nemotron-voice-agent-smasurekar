# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A pluggable chat REPL over the agent's public API.

This module holds no agent logic and no formatting: it reads a line, calls
``send()``, and hands the result to a :class:`~prototypes.text_frontend_backend_agent.cli.ui.ChatUI`.
Internal lines come from a ``CollectingSink``, never from the returned turn — so
with ``--no-show-internal`` the output is exactly the byte stream any other
caller receives.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import time
from collections.abc import Sequence
from pathlib import Path

from prototypes.text_frontend_backend_agent.agent import FrontendBackendAgent, build_agent
from prototypes.text_frontend_backend_agent.cli.ui import UI_CHOICES, ChatUI, SessionBanner, build_ui
from prototypes.text_frontend_backend_agent.errors import FrontendBackendAgentError
from prototypes.text_frontend_backend_agent.events import CollectingSink, JsonlSink
from prototypes.text_frontend_backend_agent.session import SessionState
from prototypes.text_frontend_backend_agent.tools import ToolSpec

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "agent.yaml"


def load_tools(spec: str) -> list[ToolSpec]:
    """Import a ``module:attr`` path and return the tool list it names."""
    if not spec:
        return []
    module_name, _, attribute = spec.partition(":")
    if not attribute:
        raise SystemExit("--tools must look like 'package.module:ATTRIBUTE'")
    module = importlib.import_module(module_name)
    tools = getattr(module, attribute, None)
    if not isinstance(tools, list | tuple) or not all(isinstance(item, ToolSpec) for item in tools):
        raise SystemExit(f"{spec} is not a list of ToolSpec")
    return list(tools)


class ChatSession:
    """One REPL conversation bound to one agent, one session state and one UI."""

    def __init__(
        self,
        agent: FrontendBackendAgent,
        ui: ChatUI,
        *,
        sink: CollectingSink,
        mirror: JsonlSink | None = None,
    ) -> None:
        """Bind the REPL to an agent, its collecting sink and a renderer."""
        self._agent = agent
        self._ui = ui
        self._sink = sink
        self._mirror = mirror
        self.state: SessionState = agent.new_session()

    def reset(self) -> None:
        """Start a new session state."""
        self.state = self._agent.new_session()

    async def say(self, text: str) -> None:
        """Send one user message and render the single outward payload."""
        started_at = time.time()
        with self._ui.thinking():
            turn, self.state = await self._agent.send(text, self.state)
        self._flush_events(since=started_at)
        if turn.is_tool_call:
            self._ui.tool_calls([call.name for call in turn.tool_calls])
            return
        self._ui.reply(turn.final_text or "", turn.usage)

    def _flush_events(self, *, since: float | None = None) -> None:
        drained = self._sink.drain()
        if self._mirror is not None:
            for event in drained:
                self._mirror.emit(event)
        self._ui.events(drained, since=since)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m prototypes.text_frontend_backend_agent.cli.chat",
        description="Chat with the generic text Frontend/Backend Agent prototype.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to agent.yaml")
    parser.add_argument("--tools", default="", help="tool list to inject, as 'package.module:ATTRIBUTE'")
    parser.add_argument("--event-log", default="", help="also append internal events to this JSONL file")
    parser.add_argument(
        "--ui",
        choices=UI_CHOICES,
        default="auto",
        help="auto (rich on a terminal, plain otherwise), rich, or plain",
    )
    parser.add_argument(
        "--timestamps",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="prefix trace lines with emission time and elapsed-since-turn-start",
    )
    parser.add_argument(
        "--show-internal",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="show internal events (delegation, filler, tool calls); --no-show-internal prints only replies",
    )
    return parser


async def run(argv: Sequence[str] | None = None) -> int:
    """Run the REPL until end of input or ``/quit``."""
    args = build_parser().parse_args(argv)
    sink = CollectingSink()
    tools = load_tools(args.tools)
    agent = build_agent(args.config, tools=tools, event_sink=sink)
    ui = build_ui(args.ui, show_internal=args.show_internal, timestamps=args.timestamps)
    mirror = JsonlSink(args.event_log) if args.event_log else None
    chat = ChatSession(agent, ui, sink=sink, mirror=mirror)

    ui.banner(SessionBanner.from_config(agent.config, [tool.name for tool in tools]))
    while True:
        try:
            line = ui.ask()
        except (EOFError, KeyboardInterrupt):
            ui.info("bye")
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit"):
            ui.info("bye")
            return 0
        if line == "/reset":
            chat.reset()
            ui.info("session reset")
            continue
        if line == "/usage":
            ui.usage(chat.state.usage)
            continue
        if line == "/state":
            ui.state(chat.state)
            continue
        if line == "/tools":
            ui.info(", ".join(tool.name for tool in tools) or "(none)")
            continue
        try:
            await chat.say(line)
        except FrontendBackendAgentError as exc:
            ui.error(f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a bad endpoint should not kill the REPL
            ui.error(f"{type(exc).__name__}: {exc}")


def main() -> None:
    """Entry point for ``python -m ...cli.chat``."""
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
