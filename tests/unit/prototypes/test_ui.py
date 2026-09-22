# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# ruff: noqa: D100, D101, D102, D103, D107

"""Chat UI: selection, event rendering, and the reply/internal separation."""

from __future__ import annotations

import pytest
from _fakes import make_config
from rich.console import Console

from prototypes.text_frontend_backend_agent import events as event_kinds
from prototypes.text_frontend_backend_agent.cli.ui import (
    EVENT_STYLE,
    PlainUI,
    RichUI,
    SessionBanner,
    build_ui,
    clock,
    summarize_event,
)
from prototypes.text_frontend_backend_agent.events import InternalEvent
from prototypes.text_frontend_backend_agent.messages import Usage, UsageTotals
from prototypes.text_frontend_backend_agent.session import SessionState

FILLER = "Let me check that."
ANSWER = "Order 5512 is shipped."


def _recording_console() -> Console:
    return Console(record=True, force_terminal=True, width=100, color_system="truecolor")


def _totals() -> UsageTotals:
    return UsageTotals().add_call("frontend", usage=Usage(10, 20, 30), latency_ms=120.0, cost=0.01)


def test_build_ui_plain_is_explicit() -> None:
    assert isinstance(build_ui("plain"), PlainUI)


def test_build_ui_rich_when_requested() -> None:
    assert isinstance(build_ui("rich", console=_recording_console()), RichUI)


def test_build_ui_auto_falls_back_off_a_terminal() -> None:
    piped = Console(file=open("/dev/null", "w"), force_terminal=False)  # noqa: SIM115
    assert isinstance(build_ui("auto", console=piped), PlainUI)


def test_build_ui_auto_uses_rich_on_a_terminal() -> None:
    assert isinstance(build_ui("auto", console=_recording_console()), RichUI)


def test_every_event_kind_has_a_style() -> None:
    known = {
        value
        for name, value in vars(event_kinds).items()
        if name.isupper() and isinstance(value, str) and not name.startswith("_")
    }
    assert known <= set(EVENT_STYLE), f"unstyled event kinds: {sorted(known - set(EVENT_STYLE))}"


@pytest.mark.parametrize("kind", sorted(EVENT_STYLE))
def test_summarize_event_never_raises(kind: str) -> None:
    label, colour, _ = summarize_event(InternalEvent(kind, "s1", {"text": "x", "query": "q", "step": "1 call"}))
    assert label and colour


def test_filler_is_labelled_as_not_delivered() -> None:
    label, _, detail = summarize_event(InternalEvent(event_kinds.FILLER, "s1", {"text": FILLER}))
    assert "not delivered" in label
    assert detail == FILLER


def test_rich_renders_filler_in_the_trace_and_answer_in_the_panel() -> None:
    console = _recording_console()
    ui = RichUI(console=console)
    ui.events([InternalEvent(event_kinds.FILLER, "s1", {"text": FILLER})])
    trace = console.export_text()
    console.file.truncate(0)
    ui.reply(ANSWER, _totals())
    panel = console.export_text()[len(trace) :]

    assert FILLER in trace and "not delivered" in trace
    assert ANSWER in panel
    assert FILLER not in panel, "filler must never appear in the answer region"


def test_rich_hides_internal_events_when_disabled() -> None:
    console = _recording_console()
    RichUI(show_internal=False, console=console).events([InternalEvent(event_kinds.FILLER, "s1", {"text": FILLER})])
    assert console.export_text().strip() == ""


def test_plain_hides_internal_events_when_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    PlainUI(show_internal=False).events([InternalEvent(event_kinds.FILLER, "s1", {"text": FILLER})])
    assert capsys.readouterr().out == ""


def test_plain_reply_is_the_only_line_without_internals(capsys: pytest.CaptureFixture[str]) -> None:
    ui = PlainUI(show_internal=False)
    ui.events([InternalEvent(event_kinds.FILLER, "s1", {"text": FILLER})])
    ui.reply(ANSWER, _totals())
    assert capsys.readouterr().out == f"agent> {ANSWER}\n"


def test_banner_reports_the_reasoning_split() -> None:
    config = make_config()
    banner = SessionBanner.from_config(config, ["get_order"])
    assert banner.tools == ("get_order",)
    assert banner.frontend_reasoning is False  # test config sets no extra_body
    console = _recording_console()
    RichUI(console=console).banner(banner)
    text = console.export_text()
    assert "get_order" in text and "reasoning off" in text


def test_usage_table_splits_roles() -> None:
    console = _recording_console()
    RichUI(console=console).usage(_totals())
    text = console.export_text()
    assert "frontend" in text and "backend" in text and "total" in text


def test_state_renders_json() -> None:
    console = _recording_console()
    RichUI(console=console).state(SessionState(session_id="abc"))
    assert "session_id" in console.export_text()


def test_events_carry_emission_timestamps() -> None:
    console = _recording_console()
    event = InternalEvent(event_kinds.DELEGATION, "s1", {"query": "q"}, timestamp=1_790_000_000.25)
    RichUI(console=console).events([event], since=1_790_000_000.0)
    text = console.export_text()
    assert clock(1_790_000_000.25) in text
    assert "+0.25s" in text


def test_timestamps_can_be_switched_off() -> None:
    console = _recording_console()
    event = InternalEvent(event_kinds.DELEGATION, "s1", {"query": "q"}, timestamp=1_790_000_000.25)
    RichUI(timestamps=False, console=console).events([event], since=1_790_000_000.0)
    text = console.export_text()
    assert clock(1_790_000_000.25) not in text
    assert "delegate" in text


def test_plain_events_are_timestamped(capsys: pytest.CaptureFixture[str]) -> None:
    event = InternalEvent(event_kinds.DELEGATION, "s1", {"query": "q"}, timestamp=1_790_000_000.25)
    PlainUI().events([event], since=1_790_000_000.0)
    out = capsys.readouterr().out
    assert clock(1_790_000_000.25) in out and "+0.25s" in out


def test_plain_reply_stays_undecorated(capsys: pytest.CaptureFixture[str]) -> None:
    PlainUI().reply(ANSWER, _totals())
    assert capsys.readouterr().out == f"agent> {ANSWER}\n"


def test_event_as_dict_includes_time_for_jsonl_logs() -> None:
    payload = InternalEvent(event_kinds.FILLER, "s1", {"text": FILLER}, timestamp=1_790_000_000.25).as_dict()
    assert payload["timestamp"] == 1_790_000_000.25
    assert payload["time"] == clock(1_790_000_000.25)
    assert payload["text"] == FILLER


def test_filler_is_gray() -> None:
    label, colour, _ = summarize_event(InternalEvent(event_kinds.FILLER, "s1", {"text": FILLER}))
    assert colour == "bright_black"
    assert "not delivered" in label
