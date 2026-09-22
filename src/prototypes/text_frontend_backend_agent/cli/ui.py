# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Presentation for the chat REPL — and nothing else.

Every byte the REPL prints goes through a :class:`ChatUI`, so the loop in
``chat.py`` holds no formatting and the formatting holds no agent logic. Two
implementations ship:

* :class:`RichUI` — panels, colour, a spinner and tables, when ``rich`` is
  installed and the output is a terminal;
* :class:`PlainUI` — line-oriented output that works anywhere, including pipes,
  CI logs and environments without ``rich``.

The split matters beyond taste: internal events (delegation, filler, tool calls)
are rendered *only* from the event sink and always in a visually separate,
clearly-labelled region. The agent's reply is the one thing rendered as the
conversation. Nothing the UI can do makes filler text look like an answer.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from prototypes.text_frontend_backend_agent import events as event_kinds
from prototypes.text_frontend_backend_agent.config import Config
from prototypes.text_frontend_backend_agent.events import InternalEvent
from prototypes.text_frontend_backend_agent.messages import UsageTotals
from prototypes.text_frontend_backend_agent.session import SessionState

UI_CHOICES = ("auto", "rich", "plain")

#: label, colour and whether the kind is a headline event, per event kind.
EVENT_STYLE: dict[str, tuple[str, str]] = {
    event_kinds.DELEGATION: ("delegate → backend", "cyan"),
    event_kinds.FILLER: ("filler (logged, not delivered)", "bright_black"),
    event_kinds.DIRECT_ANSWER: ("frontend answered directly", "green"),
    event_kinds.BACKEND_TOOL_CALLS: ("backend wants tools", "magenta"),
    event_kinds.TOOL_EXECUTED: ("tool executed", "blue"),
    event_kinds.BACKEND_FINAL: ("backend final text", "green"),
    event_kinds.FRONTEND_CONTRACT_VIOLATION: ("contract violation", "red"),
    event_kinds.FRONTEND_REPAIR: ("repair reprompt", "red"),
    event_kinds.PENDING_DISCARDED: ("pending work discarded", "yellow"),
    event_kinds.ITERATION_CAP: ("iteration cap hit", "red"),
    event_kinds.BACKEND_ERROR: ("backend error", "red"),
    event_kinds.STEP_USAGE: ("usage", "bright_black"),
}

_HELP_ROWS = (
    ("/reset", "start a new session"),
    ("/usage", "token, latency and cost totals"),
    ("/state", "dump SessionState as JSON"),
    ("/tools", "tools handed to the backend"),
    ("/quit", "exit"),
)


@dataclass(frozen=True, slots=True)
class SessionBanner:
    """What the header shows about how this run is wired."""

    name: str
    mode: str
    tools: tuple[str, ...]
    frontend_model: str
    frontend_url: str
    frontend_reasoning: bool
    backend_model: str
    backend_url: str
    backend_reasoning: bool
    execution: str

    @classmethod
    def from_config(cls, config: Config, tool_names: Sequence[str]) -> SessionBanner:
        """Read a banner off the resolved configuration."""
        return cls(
            name=config.name,
            mode=config.mode,
            tools=tuple(tool_names),
            frontend_model=config.frontend.llm.model if config.frontend_enabled else "—",
            frontend_url=config.frontend.llm.base_url if config.frontend_enabled else "—",
            frontend_reasoning=_reasoning_on(config.frontend.llm.extra_body) if config.frontend_enabled else False,
            backend_model=config.backend.llm.model,
            backend_url=config.backend.llm.base_url,
            backend_reasoning=_reasoning_on(config.backend.llm.extra_body),
            execution=config.backend.tools.execution,
        )


def _reasoning_on(extra_body: dict[str, Any]) -> bool:
    return bool((extra_body.get("chat_template_kwargs") or {}).get("enable_thinking"))


def summarize_event(event: InternalEvent) -> tuple[str, str, str]:
    """Return ``(label, colour, detail)`` for one internal event."""
    label, colour = EVENT_STYLE.get(event.kind, (event.kind, "bright_black"))
    data = event.data
    if event.kind in (event_kinds.DELEGATION,):
        detail = str(data.get("query", ""))
    elif event.kind in (event_kinds.FILLER, event_kinds.DIRECT_ANSWER, event_kinds.BACKEND_FINAL):
        detail = str(data.get("text", ""))
    elif event.kind == event_kinds.BACKEND_TOOL_CALLS:
        calls = data.get("calls") or []
        detail = ", ".join(f"{call['name']}({json.dumps(call.get('arguments', {}))})" for call in calls)
    elif event.kind == event_kinds.TOOL_EXECUTED:
        detail = f"{data.get('name')}({json.dumps(data.get('arguments', {}))}) → {data.get('result', '')}"
    elif event.kind == event_kinds.STEP_USAGE:
        detail = str(data.get("step", ""))
    else:
        detail = json.dumps({k: v for k, v in data.items() if v not in (None, "", [])}, ensure_ascii=False)
    return label, colour, detail


@runtime_checkable
class ChatUI(Protocol):
    """Everything the REPL is allowed to put on screen."""

    def banner(self, banner: SessionBanner) -> None:
        """Render the header."""

    def ask(self) -> str:
        """Read one user line. Raises ``EOFError`` at end of input."""

    def thinking(self) -> Any:
        """Context manager shown while a turn is in flight."""

    def events(self, events: Sequence[InternalEvent], *, since: float | None = None) -> None:
        """Render internal events, if the UI is showing them.

        ``since`` is the wall-clock time the turn started, used to show how long
        into the turn each event happened.
        """

    def reply(self, text: str, usage: UsageTotals) -> None:
        """Render the agent's user-visible answer."""

    def tool_calls(self, names: Sequence[str]) -> None:
        """Render a turn that handed tool calls back to the caller."""

    def usage(self, totals: UsageTotals) -> None:
        """Render session accounting."""

    def state(self, session: SessionState) -> None:
        """Render the session state."""

    def info(self, message: str) -> None:
        """Render a neutral notice."""

    def error(self, message: str) -> None:
        """Render an error."""


class PlainUI:
    """Line-oriented output that works in any terminal, pipe or log."""

    def __init__(self, *, show_internal: bool = True, timestamps: bool = True) -> None:
        """Configure whether internal events and their timestamps are printed."""
        self._show_internal = show_internal
        self._timestamps = timestamps

    def banner(self, banner: SessionBanner) -> None:
        """Print a one-block header."""
        print(f"{banner.name} · mode={banner.mode} · tools={', '.join(banner.tools) or '(none)'}")
        if banner.mode != "backend_only":
            print(f"  frontend  {banner.frontend_model}  reasoning={'on' if banner.frontend_reasoning else 'off'}")
        print(f"  backend   {banner.backend_model}  reasoning={'on' if banner.backend_reasoning else 'off'}")
        print(f"  endpoint  {banner.backend_url}  ·  tool execution: {banner.execution}")
        print("commands: " + "  ".join(name for name, _ in _HELP_ROWS))

    def ask(self) -> str:
        """Read one line from stdin."""
        return input("you> ").strip()

    @contextmanager
    def thinking(self) -> Any:
        """No spinner in plain mode."""
        yield

    def events(self, events: Sequence[InternalEvent], *, since: float | None = None) -> None:
        """Print each event on one line, optionally time-stamped."""
        if not self._show_internal:
            return
        for event in events:
            label, _, detail = summarize_event(event)
            stamp = f"{clock(event.timestamp)} {_delta(event.timestamp, since):>7} " if self._timestamps else ""
            print(f"  · {stamp}{label:<32}{detail}")

    def reply(self, text: str, usage: UsageTotals) -> None:
        """Print the answer, never time-stamped.

        Plain mode is the machine-readable path: with ``--no-show-internal`` its
        output must be exactly the bytes a caller receives, so no decoration is
        added here. Timestamps belong to the trace region.
        """
        print(f"agent> {text}")

    def tool_calls(self, names: Sequence[str]) -> None:
        """Explain that this mode needs an external driver."""
        print(f"agent> [external execution: the caller must run {', '.join(names)} and call send_tool_results()]")

    def usage(self, totals: UsageTotals) -> None:
        """Print accounting."""
        print(f"session: {totals.summary()}")

    def state(self, session: SessionState) -> None:
        """Print the session state as JSON."""
        print(json.dumps(session.to_dict(), indent=2, ensure_ascii=False))

    def info(self, message: str) -> None:
        """Print a notice."""
        print(message)

    def error(self, message: str) -> None:
        """Print an error."""
        print(f"error: {message}")


class RichUI:
    """Panels, colour and a spinner, for an interactive terminal."""

    def __init__(self, *, show_internal: bool = True, timestamps: bool = True, console: Any = None) -> None:
        """Create the console (importing ``rich`` lazily)."""
        from rich.console import Console

        self._console = console or Console()
        self._show_internal = show_internal
        self._timestamps = timestamps

    def banner(self, banner: SessionBanner) -> None:
        """Render the header panel and the command hints."""
        from rich.panel import Panel
        from rich.table import Table

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bright_black", justify="right")
        grid.add_column()
        if banner.mode != "backend_only":
            grid.add_row("frontend", f"{banner.frontend_model}  {_reasoning_tag(banner.frontend_reasoning)}")
        grid.add_row("backend", f"{banner.backend_model}  {_reasoning_tag(banner.backend_reasoning)}")
        grid.add_row("endpoint", banner.backend_url)
        grid.add_row("tools", ", ".join(banner.tools) or "[bright_black](none)[/]")
        grid.add_row("execution", banner.execution)
        self._console.print(
            Panel(grid, title=f"[bold]{banner.name}[/]", subtitle=f"[bright_black]{banner.mode}[/]", expand=False)
        )
        hints = Table.grid(padding=(0, 2))
        hints.add_column(style="bold cyan")
        hints.add_column(style="bright_black")
        for name, description in _HELP_ROWS:
            hints.add_row(name, description)
        self._console.print(hints)

    def ask(self) -> str:
        """Prompt for one line."""
        from rich.prompt import Prompt

        return Prompt.ask("[bold green]you[/]", console=self._console).strip()

    def thinking(self) -> Any:
        """Show a spinner while the turn runs."""
        return self._console.status("[bright_black]working…[/]", spinner="dots")

    def events(self, events: Sequence[InternalEvent], *, since: float | None = None) -> None:
        """Render the internal trace as an indented, dimmed, time-stamped table."""
        if not self._show_internal or not events:
            return
        from rich.table import Table

        table = Table.grid(padding=(0, 1))
        table.add_column(width=2)
        if self._timestamps:
            table.add_column(width=12, style="bright_black")
            table.add_column(width=7, style="bright_black", justify="right")
        table.add_column(width=32, overflow="fold")
        table.add_column(overflow="fold")
        for event in events:
            label, colour, detail = summarize_event(event)
            row = [f"[{colour}]·[/]"]
            if self._timestamps:
                row += [clock(event.timestamp), _delta(event.timestamp, since)]
            row += [f"[{colour}]{label}[/]", f"[bright_black]{_clip(detail)}[/]"]
            table.add_row(*row)
        self._console.print(table)

    def reply(self, text: str, usage: UsageTotals) -> None:
        """Render the answer in its own panel, with the step's accounting."""
        from rich.panel import Panel

        title = "[bold]agent[/]"
        if self._timestamps:
            title = f"{title} [bright_black]{clock(time.time())}[/]"
        self._console.print(
            Panel(
                text or "[bright_black](empty)[/]",
                title=title,
                subtitle=f"[bright_black]{usage.summary()}[/]",
                border_style="green",
                title_align="left",
                subtitle_align="right",
            )
        )

    def tool_calls(self, names: Sequence[str]) -> None:
        """Explain that this mode needs an external driver."""
        from rich.panel import Panel

        self._console.print(
            Panel(
                f"This config uses [bold]execution: external[/]: the caller runs {', '.join(names)} "
                "and resumes with send_tool_results().\nThe REPL only drives internal execution — "
                "see cli/external_demo.py.",
                title="[bold]tool calls returned[/]",
                border_style="magenta",
                title_align="left",
            )
        )

    def usage(self, totals: UsageTotals) -> None:
        """Render a per-role accounting table."""
        from rich.table import Table

        table = Table(title="session usage", title_justify="left", header_style="bold")
        table.add_column("role")
        table.add_column("calls", justify="right")
        table.add_column("prompt", justify="right")
        table.add_column("completion", justify="right")
        table.add_column("latency", justify="right")
        table.add_column("cost", justify="right")
        for role, role_totals in (("frontend", totals.frontend), ("backend", totals.backend)):
            table.add_row(
                role,
                str(role_totals.calls),
                str(role_totals.usage.prompt_tokens),
                str(role_totals.usage.completion_tokens),
                f"{role_totals.latency_ms / 1000:.2f} s",
                "unknown" if role_totals.cost_unknown_calls else f"${role_totals.cost_known_subtotal:.4f}",
            )
        table.add_section()
        table.add_row(
            "[bold]total[/]",
            f"[bold]{totals.calls}[/]",
            f"[bold]{totals.usage.prompt_tokens}[/]",
            f"[bold]{totals.usage.completion_tokens}[/]",
            f"[bold]{totals.latency_ms / 1000:.2f} s[/]",
            "[bold]unknown[/]" if totals.cost is None else f"[bold]${totals.cost:.4f}[/]",
        )
        self._console.print(table)

    def state(self, session: SessionState) -> None:
        """Render the session state as highlighted JSON."""
        from rich.syntax import Syntax

        payload = json.dumps(session.to_dict(), indent=2, ensure_ascii=False)
        self._console.print(Syntax(payload, "json", theme="ansi_dark", word_wrap=True))

    def info(self, message: str) -> None:
        """Render a notice."""
        self._console.print(f"[bright_black]{message}[/]")

    def error(self, message: str) -> None:
        """Render an error panel."""
        from rich.panel import Panel

        self._console.print(Panel(message, title="[bold]error[/]", border_style="red", title_align="left"))


def clock(timestamp: float) -> str:
    """Format an event's emission time as ``HH:MM:SS.mmm``."""
    return time.strftime("%H:%M:%S", time.localtime(timestamp)) + f".{int(timestamp % 1 * 1000):03d}"


def _delta(timestamp: float, since: float | None) -> str:
    if since is None:
        return ""
    return f"+{max(0.0, timestamp - since):.2f}s"


def _reasoning_tag(on: bool) -> str:
    return "[green]reasoning on[/]" if on else "[bright_black]reasoning off[/]"


def _clip(text: str, limit: int = 160) -> str:
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[: limit - 1]}…"


def rich_available() -> bool:
    """Whether ``rich`` can be imported."""
    try:
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


def build_ui(kind: str = "auto", *, show_internal: bool = True, timestamps: bool = True, console: Any = None) -> ChatUI:
    """Build a UI: ``rich`` when asked for and available, else :class:`PlainUI`.

    ``auto`` prefers rich on an interactive terminal and falls back to plain for
    pipes and logs, where panels and spinners are noise.
    """
    if kind == "plain":
        return PlainUI(show_internal=show_internal, timestamps=timestamps)
    if not rich_available():
        if kind == "rich":
            raise SystemExit("--ui rich needs the 'rich' package: uv sync --extra prototypes")
        return PlainUI(show_internal=show_internal, timestamps=timestamps)
    if kind == "rich":
        return RichUI(show_internal=show_internal, timestamps=timestamps, console=console)
    from rich.console import Console

    resolved = console or Console()
    if not resolved.is_terminal:
        return PlainUI(show_internal=show_internal, timestamps=timestamps)
    return RichUI(show_internal=show_internal, timestamps=timestamps, console=resolved)


def drained(events: Iterable[InternalEvent]) -> list[InternalEvent]:
    """Materialize an event iterable (helper for callers draining a sink)."""
    return list(events)
