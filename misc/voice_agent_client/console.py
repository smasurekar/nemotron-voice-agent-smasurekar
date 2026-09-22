"""Terminal rendering for the client's run report.

Plain ANSI and stdlib text wrapping - no Rich, no dependency. Colour is opt-out
and auto-disabled when stdout is not a terminal, so piping the run into a file
or a CI log gives clean text.

The layout rules are the whole point of this module:

* one screen-width column, never a wall of `key=value`
* labels in a fixed gutter so values line up down the page
* long model answers wrapped with a hanging indent instead of ragged overflow
* artifact paths shown relative to the working directory
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_CODES = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
}

LABEL_WIDTH = 9
INDENT = "  "
MAX_WIDTH = 100

_colour = True


def init(*, no_colour: bool = False) -> None:
    """Decide once whether this run may emit colour.

    NO_COLOR is the de-facto standard opt-out; a non-tty stdout means the
    output is being captured, where escape codes are noise.
    """
    global _colour
    _colour = not no_colour and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def style(text: str, *names: str) -> str:
    if not _colour or not names:
        return text
    codes = ";".join(_CODES[name] for name in names if name in _CODES)
    return f"\x1b[{codes}m{text}\x1b[0m" if codes else text


def plain_len(text: str) -> int:
    """Visible width, ignoring escape codes."""
    return len(_ANSI.sub("", text))


def width() -> int:
    return min(shutil.get_terminal_size((88, 24)).columns, MAX_WIDTH)


def rule(title: str = "", *, char: str = "─") -> str:
    """A titled horizontal rule: the section separator for a report."""
    if not title:
        return style(char * width(), "dim")
    head = f"{char}{char} {title} "
    tail = char * max(0, width() - plain_len(head))
    return style(char * 2, "dim") + " " + style(title, "bold", "cyan") + " " + style(tail, "dim")


def field(label: str, value: str, *, value_styles: tuple[str, ...] = ()) -> str:
    """One aligned `label   value` line, wrapped into the label's gutter."""
    gutter = INDENT + " " * LABEL_WIDTH
    body = wrap(value, first_prefix=INDENT + label.ljust(LABEL_WIDTH), prefix=gutter)
    if value_styles:
        head, _, rest = body.partition("\n")
        label_part = head[: len(INDENT + label.ljust(LABEL_WIDTH))]
        head = label_part + style(head[len(label_part) :], *value_styles)
        if rest:
            rest = "\n".join(style(line, *value_styles) for line in rest.split("\n"))
            return f"{head}\n{rest}"
        return head
    return body


def wrap(text: str, *, first_prefix: str, prefix: str) -> str:
    """Wrap to the terminal, keeping blank-line paragraph breaks intact."""
    limit = max(30, width() - len(prefix))
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph.strip(), width=limit) or [""])
    out = []
    for position, line in enumerate(lines):
        # A blank paragraph break stays blank; prefixing it would leave a line
        # of trailing spaces in the transcript.
        out.append(((first_prefix if position == 0 else prefix) + line) if line else "")
    return "\n".join(out) if out else first_prefix


def clip(text: str, max_lines: int) -> tuple[str, int]:
    """Cut a long answer to `max_lines` rendered lines, reporting the remainder."""
    rendered = text.split("\n")
    if max_lines <= 0 or len(rendered) <= max_lines:
        return text, 0
    return "\n".join(rendered[:max_lines]), len(rendered) - max_lines


def relative(path: str | Path) -> str:
    """Show a path relative to the working directory when that is shorter."""
    resolved = Path(path).resolve()
    try:
        candidate = resolved.relative_to(Path.cwd())
    except ValueError:
        return str(resolved)
    return str(candidate) if len(str(candidate)) < len(str(resolved)) else str(resolved)


def row(left: str, right: str) -> str:
    """One line with `right` pushed to the terminal's right edge."""
    gap = width() - plain_len(left) - plain_len(right)
    if gap < 2:
        return left
    return left + " " * gap + right


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.2f}s"


def rate(hz: int | None) -> str:
    return f"{hz / 1000:g} kHz" if hz else "?"


def audio_format(spec: object) -> str:
    """Render a session audio format object as `PCM16 24 kHz`."""
    if not isinstance(spec, dict):
        return "?"
    kind = str(spec.get("type") or "?").replace("audio/pcm", "PCM16").replace("audio/", "").upper()
    return f"{kind} {rate(spec.get('rate'))}" if spec.get("rate") else kind
