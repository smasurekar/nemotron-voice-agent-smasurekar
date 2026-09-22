# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Driver for ``execution: external`` — the caller runs the tools.

This is the shape a scaffold evaluation harness drives: ``send()`` returns tool
calls instead of text, the caller executes them, and ``send_tool_results()``
resumes the same suspended turn. The REPL cannot do this (it only drives
``execution: internal``), so this module is the runnable reference.

    PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.external_demo \
      --config src/prototypes/text_frontend_backend_agent/config/examples/external_tools.yaml \
      --tools prototypes.text_frontend_backend_agent.demo_tools:TOOLS \
      --message "where is my order 5512?"
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
from collections.abc import Sequence
from pathlib import Path

from prototypes.text_frontend_backend_agent.agent import build_agent
from prototypes.text_frontend_backend_agent.cli.chat import load_tools
from prototypes.text_frontend_backend_agent.messages import ToolResult
from prototypes.text_frontend_backend_agent.tools import ToolSpec, error_payload, serialize_result

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "examples" / "external_tools.yaml"


async def execute_here(call_name: str, arguments: dict, tools: Sequence[ToolSpec]) -> str:
    """Run one tool in this process, standing in for the caller's environment."""
    spec = next((tool for tool in tools if tool.name == call_name), None)
    if spec is None or spec.callable is None:
        return error_payload(KeyError(f"caller has no tool named {call_name}"))
    try:
        outcome = spec.callable(**arguments)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return serialize_result(outcome)
    except Exception as exc:  # noqa: BLE001 - the caller decides how a failure is reported
        return error_payload(exc)


async def run(argv: Sequence[str] | None = None) -> int:
    """Send one message and service every tool round the agent asks for."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--tools", default="prototypes.text_frontend_backend_agent.demo_tools:TOOLS")
    parser.add_argument("--message", default="where is my order 5512?")
    args = parser.parse_args(argv)

    tools = load_tools(args.tools)
    agent = build_agent(args.config, tools=tools)
    if agent.config.backend.tools.execution != "external":
        raise SystemExit("this driver requires backend.tools.execution: external in the config")

    session = agent.new_session()
    print(f"user> {args.message}")
    turn, session = await agent.send(args.message, session)

    rounds = 0
    while turn.is_tool_call:
        rounds += 1
        results = []
        for call in turn.tool_calls:
            content = await execute_here(call.name, call.arguments, tools)
            print(f"  caller executed {call.name}({call.arguments}) -> {content}")
            results.append(ToolResult(tool_call_id=call.id, content=content))
        turn, session = await agent.send_tool_results(results, session)

    print(f"agent> {turn.final_text}")
    print(f"({rounds} external tool round(s) · session {session.usage.summary()})")
    return 0


def main() -> None:
    """Entry point for ``python -m ...cli.external_demo``."""
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
