# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check an endpoint: does the reasoning toggle work, and does tool calling survive it?

Run this before trusting a new model or endpoint. It answers three questions the
agent depends on:

1. does ``extra_body.chat_template_kwargs.enable_thinking`` actually change
   anything (reasoning present/absent, completion-token count);
2. does reasoning arrive in its own ``reasoning_content`` field rather than
   inside ``content`` (if it ever came back inline, it would reach the user);
3. does the model still emit a well-formed ``call_backend`` tool call with
   reasoning on, which is the frontend's whole contract.

    PYTHONPATH=src uv run python -m prototypes.text_frontend_backend_agent.cli.probe_reasoning
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections.abc import Sequence
from typing import Any

from prototypes.text_frontend_backend_agent.delegation import FRONTEND_TOOLS

DEFAULT_MODEL = "nvidia/nvidia/nemotron-3.5-lightning"
DEFAULT_BASE_URL = "https://inference-api.nvidia.com/v1"
ARITHMETIC_PROMPT = "What is 17 * 23? Answer with the number."
DELEGATION_SYSTEM = (
    "You are a frontend agent. For any task request you MUST call call_backend with a "
    "self-contained query and no assistant text."
)
TASK_PROMPT = "where is my order 5512?"


def _reasoning_of(message: Any) -> str:
    """Return the provider's reasoning text, wherever it put it."""
    direct = getattr(message, "reasoning_content", None)
    if direct:
        return str(direct)
    extra = getattr(message, "model_extra", None) or {}
    return str(extra.get("reasoning_content") or "")


async def _probe(client: Any, model: str, *, thinking: bool, with_tools: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": 1024,
        "temperature": 0.0,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": thinking}},
    }
    if thinking:
        request["extra_body"]["reasoning_budget"] = 1024
    if with_tools:
        request["messages"] = [
            {"role": "system", "content": DELEGATION_SYSTEM},
            {"role": "user", "content": TASK_PROMPT},
        ]
        request["tools"] = list(FRONTEND_TOOLS)
        request["tool_choice"] = "auto"
    else:
        request["messages"] = [{"role": "user", "content": ARITHMETIC_PROMPT}]
    started = time.perf_counter()
    response = await client.chat.completions.create(**request)
    message = response.choices[0].message
    reasoning = _reasoning_of(message)
    return {
        "reasoning_chars": len(reasoning),
        "reasoning_inline": "<think>" in (message.content or ""),
        "tool_calls": [call.function.name for call in (message.tool_calls or [])],
        "content": message.content,
        "completion_tokens": response.usage.completion_tokens,
        "latency_ms": (time.perf_counter() - started) * 1000,
    }


def _line(label: str, result: dict[str, Any]) -> str:
    reasoning = f"YES {result['reasoning_chars']} chars" if result["reasoning_chars"] else "no"
    calls = ",".join(result["tool_calls"]) or "-"
    return (
        f"  {label:24} reasoning={reasoning:<16} inline_think={'YES' if result['reasoning_inline'] else 'no':<4} "
        f"tool_calls={calls:<14} completion_tokens={result['completion_tokens']:<5} "
        f"{result['latency_ms']:6.0f} ms"
    )


async def run(argv: Sequence[str] | None = None) -> int:
    """Probe the endpoint and report whether it meets the agent's requirements."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=os.getenv("FRONTEND_LLM_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.getenv("FRONTEND_LLM_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--samples", type=int, default=3, help="tool-call samples per reasoning mode")
    args = parser.parse_args(argv)

    from openai import AsyncOpenAI

    api_key = os.getenv("NVIDIA_API_KEY", "")
    if not api_key:
        raise SystemExit("NVIDIA_API_KEY is not set")
    print(f"model={args.model}\nbase_url={args.base_url}\nkey={api_key[:3]}… ({len(api_key)} chars)\n")

    async with AsyncOpenAI(api_key=api_key, base_url=args.base_url, timeout=120) as client:
        print("plain completion:")
        off = await _probe(client, args.model, thinking=False, with_tools=False)
        on = await _probe(client, args.model, thinking=True, with_tools=False)
        print(_line("enable_thinking=false", off))
        print(_line("enable_thinking=true", on))

        print("\nwith the frontend delegation tool:")
        tool_off = await _probe(client, args.model, thinking=False, with_tools=True)
        tool_on = await _probe(client, args.model, thinking=True, with_tools=True)
        print(_line("enable_thinking=false", tool_off))
        print(_line("enable_thinking=true", tool_on))

        stability: dict[bool, list[list[str]]] = {}
        for thinking in (False, True):
            runs = await asyncio.gather(
                *[_probe(client, args.model, thinking=thinking, with_tools=True) for _ in range(args.samples)]
            )
            stability[thinking] = [run["tool_calls"] for run in runs]

    toggle_works = on["reasoning_chars"] > 0 and off["reasoning_chars"] == 0
    no_inline = not (on["reasoning_inline"] or tool_on["reasoning_inline"])
    good = ["call_backend"]
    tools_ok = all(calls == good for runs in stability.values() for calls in runs) and tool_on["tool_calls"] == good

    print("\nverdict:")
    print(f"  reasoning toggle responds          {'PASS' if toggle_works else 'FAIL'}")
    print(f"  reasoning stays out of content     {'PASS' if no_inline else 'FAIL'}")
    print(f"  tool calls survive both modes      {'PASS' if tools_ok else 'FAIL'}  {stability}")
    return 0 if (toggle_works and no_inline and tools_ok) else 1


def main() -> None:
    """Entry point for ``python -m ...cli.probe_reasoning``."""
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
