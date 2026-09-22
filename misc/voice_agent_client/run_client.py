#!/usr/bin/env python3
"""Run a JSONL case file against the Realtime gateway and write artifacts.

    ../../.venv/bin/python run_client.py --preflight
    ../../.venv/bin/python run_client.py --cases cases.text.jsonl
    ../../.venv/bin/python run_client.py --cases cases.audio.jsonl --out runs/audio

Each case gets its own session, so a failure is attributable to one case rather
than to whatever the previous one left behind. Results land in `--out` as one
JSON file per case (transcript, timings, tool calls, full event log) plus a WAV
for any case that produced audio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import websockets  # noqa: F401  (import check only - the error below is friendlier)
except ImportError:
    repo_root = HERE.parents[1]
    sys.exit(
        "Missing module 'websockets'.\n\n"
        "Run this with the repo virtualenv, which already has it:\n\n"
        f"    {repo_root / '.venv/bin/python'} {Path(sys.argv[0]).name} "
        + " ".join(sys.argv[1:])
        + "\n\nor: pip install -r requirements.txt"
    )

import console  # noqa: E402
from audio_io import write_wav  # noqa: E402
from client import (  # noqa: E402
    DEFAULT_API_KEY_ENV,
    PROFILES,
    Case,
    CaseResult,
    RealtimeClient,
    RealtimeError,
    load_cases,
)

DEFAULT_BASE_URL = os.getenv("REALTIME_BASE_URL", "http://127.0.0.1:7860")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal OpenAI Realtime client for the Nemotron voice agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="http(s) base URL; the ws(s) URL is derived from it (default: %(default)s)",
    )
    parser.add_argument(
        "--profile",
        default="nemotron-local",
        choices=sorted(PROFILES),
        help="endpoint profile (default: %(default)s)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Realtime model profile; overrides the one the endpoint profile names",
    )
    parser.add_argument(
        "--expect-model",
        default=None,
        help="abort unless the endpoint advertises this model id (fail closed)",
    )
    parser.add_argument("--cases", default=None, help="JSONL case file to run")
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="run only these case ids (repeatable)",
    )
    parser.add_argument("--text", default=None, help="run one ad-hoc text turn instead of a case file")
    parser.add_argument("--audio", default=None, help="run one ad-hoc audio turn instead of a case file")
    parser.add_argument(
        "--modality",
        default="text",
        choices=("text", "audio"),
        help="output modality for an ad-hoc turn (default: %(default)s)",
    )
    parser.add_argument(
        "--instructions",
        default="Answer in one short sentence.",
        help="instructions for an ad-hoc turn",
    )
    parser.add_argument(
        "--turn-mode",
        default="auto",
        choices=("auto", "manual"),
        help="VAD-committed or explicitly committed input for an ad-hoc audio turn",
    )
    parser.add_argument("--preflight", action="store_true", help="report the deployment identity and exit")
    parser.add_argument("--out", default=None, help="artifact directory (default: runs/<case file stem>)")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV, help="credential variable (default: %(default)s)")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS verification; needed for the server's default self-signed certificate",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="response-idle budget in seconds (default: %(default)s)")
    parser.add_argument(
        "--trailing-silence-ms",
        type=int,
        default=1500,
        help="silence appended after speech so VAD can end the turn (default: %(default)s)",
    )
    parser.add_argument(
        "--no-pacing",
        action="store_true",
        help="send audio as fast as the socket accepts it instead of in real time",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print every server event as it arrives")
    parser.add_argument(
        "--full-text",
        action="store_true",
        help="print long replies in full instead of clipping them to the artifact",
    )
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    return parser.parse_args(argv)


def build_cases(args: argparse.Namespace) -> tuple[list[Case], Path]:
    """Resolve the case list and the directory its audio references are relative to."""
    if args.cases:
        cases = load_cases(args.cases)
        if args.case_id:
            wanted = set(args.case_id)
            cases = [case for case in cases if case.case_id in wanted]
            missing = wanted - {case.case_id for case in cases}
            if missing:
                raise SystemExit(f"unknown case id(s): {', '.join(sorted(missing))}")
        return cases, Path(args.cases).resolve().parent

    if args.text or args.audio:
        case = Case(
            case_id="adhoc",
            instructions=args.instructions,
            text=args.text or "",
            audio=args.audio,
            output_modalities=(args.modality,),
            turn_mode=args.turn_mode,
        )
        return [case], Path.cwd()

    raise SystemExit("nothing to run: pass --cases, --text, --audio, or --preflight")


REPLY_CLIP_LINES = 8


@dataclass(frozen=True, slots=True)
class Row:
    """One finished case, as the summary and coverage views need it."""

    case_id: str
    input_kind: str
    output_kind: str
    verdict: str
    outcome: str
    latency: float | None

    @classmethod
    def build(cls, case: Case, verdict: str, outcome: str, latency: float | None) -> "Row":
        return cls(
            case_id=case.case_id,
            input_kind="audio" if case.audio else "text",
            output_kind=case.output_modalities[0],
            verdict=verdict,
            outcome=outcome,
            latency=latency,
        )
GLYPHS = {"PASS": "\u2714", "WARN": "\u26a0", "FAIL": "\u2716"}


def reply_latency(timings: dict) -> float | None:
    """Time from the endpoint owning the turn to its first output byte.

    Measured from the commit when there is one: automatic VAD can commit and
    start answering while the client is still streaming the clip, which makes
    `input_sent_s` produce a negative - and meaningless - latency.
    """
    first = timings.get("first_audio_s") or timings.get("first_text_s")
    if first is None:
        return None
    start = timings.get("input_committed_s", timings.get("input_sent_s"))
    if start is None:
        return None
    return first - start if first >= start else None


def print_event(event: dict) -> None:
    """`--verbose` trace: one dimmed line per server event."""
    etype = event.get("type", "")
    if etype == "response.output_audio.delta":
        return
    detail = ""
    if etype.endswith(".delta"):
        detail = repr(event.get("delta", ""))[:60]
    elif etype == "error":
        detail = json.dumps(event.get("error", {}))[:200]
    line = f"{console.INDENT}  {console.style('<-', 'dim')} {etype} {console.style(detail, 'dim')}"
    print(line.rstrip(), flush=True)


def print_endpoint(info: dict, client: RealtimeClient) -> None:
    """The report header: what we are talking to, before any case runs."""
    auth = (
        console.style("bearer credential sent", "green")
        if info["authenticated"]
        else console.style("none", "dim") + f" ({client.api_key_env} unset)"
    )
    print(console.rule("Endpoint"))
    print(console.field("model", str(info["model"] or "?"), value_styles=("bold",)))
    print(console.field("socket", client.ws_url))
    print(
        console.field(
            "audio",
            f"{console.audio_format(info['input_format'])} in  \u2192  "
            f"{console.audio_format(info['output_format'])} out"
            + (f"   voice {info['voice']}" if info.get("voice") else ""),
        )
    )
    print(
        console.field(
            "turns",
            f"{info['turn_detection'] or 'unknown'}"
            + (f"   asr {info['transcription']}" if info.get("transcription") else ""),
        )
    )
    print(console.field("auth", auth))
    print(console.field("session", str(info["session_id"] or "?"), value_styles=("dim",)))


def print_plan(cases: list[Case], source: str, out_dir: Path) -> None:
    print(console.rule("Run"))
    print(console.field("cases", f"{len(cases)} from {source}"))
    print(console.field("output", console.relative(out_dir)))


def _status(result: CaseResult) -> tuple[str, str]:
    """The case's verdict as a glyph plus a colour name."""
    if result.completed and not result.errors:
        return "PASS", "green"
    if result.completed:
        return "WARN", "yellow"
    return "FAIL", "red"


def print_case_header(index: int, total: int, case: Case) -> None:
    """Announce the case before it runs, so a slow turn shows progress."""
    shape = f"{'audio' if case.audio else 'text'} in \u2192 {case.output_modalities[0]} out"
    print()
    print(
        console.row(
            f"{console.style(f'{index}/{total}', 'dim')} {console.style(case.case_id, 'bold')}",
            console.style(shape, "dim"),
        )
    )


def print_case(result: CaseResult, artifact: Path, wav: Path | None, *, full_text: bool) -> None:
    """The case body: verdict, what was heard and said, timings, artifacts."""
    verdict, colour = _status(result)
    timings = result.timings
    latency = reply_latency(timings)

    status = [console.style(f"{GLYPHS[verdict]} {verdict}", colour, "bold")]
    if latency is not None:
        status.append(console.style(f"reply in {console.duration(latency)}", "dim"))
    if "response_done_s" in timings:
        status.append(console.style(f"turn took {console.duration(timings['response_done_s'])}", "dim"))
    status.append(console.style(result.termination_reason, "dim"))
    print(console.field("status", console.style("  \u00b7  ", "dim").join(status)))

    # What went in, before what came back: a reply is not readable without the
    # turn that produced it, and the artifact carries the same three fields.
    if result.instructions:
        print(console.field("system", result.instructions, value_styles=("dim",)))
    if result.input_text:
        print(console.field("asked", result.input_text))
    if result.input_audio:
        print(console.field("sent", result.input_audio))
    if result.user_transcript:
        print(console.field("heard", result.user_transcript, value_styles=("italic",)))
    if result.text:
        body = console.field("said", result.text)
        if not full_text:
            body, hidden = console.clip(body, REPLY_CLIP_LINES)
            if hidden:
                body += console.style(
                    f"\n{console.INDENT}{' ' * console.LABEL_WIDTH}... {hidden} more line(s), full text in the artifact",
                    "dim",
                )
        print(body)

    for call in result.tool_calls:
        state = console.style("answered", "green") if call.answered else console.style("UNANSWERED", "red", "bold")
        print(console.field("tool", f"{call.name}({call.arguments}) \u2192 {state}"))

    # Names only: the directory they live in is printed once, in the Run block.
    if wav is not None:
        print(
            console.field(
                "audio",
                f"{wav.name}   {result.audio_duration_s:.2f}s @ "
                f"{console.rate(result.output_sample_rate_hz)}",
            )
        )
    print(console.field("artifact", console.style(artifact.name, "dim")))
    for error in result.errors:
        print(console.field("error", error, value_styles=("red",)))


def print_matrix(rows: list["Row"]) -> None:
    """A 2x2 grid of what this run actually proved, input kind by output kind.

    The four combinations are independent capabilities of the gateway - text
    output skips TTS, text input skips ASR - so a run that covers several of
    them deserves a coverage view rather than a list to be counted by hand.
    """
    cells = {
        (inp, out): [row for row in rows if row.input_kind == inp and row.output_kind == out]
        for inp in ("text", "audio")
        for out in ("text", "audio")
    }
    if sum(1 for group in cells.values() if group) < 2:
        return  # one combination only: the case list already says everything

    print()
    print(console.rule("Modality coverage"))
    label_width = 10
    column = 18
    header = f"{console.INDENT}{'':{label_width}}{'\u2192 text out':{column}}\u2192 audio out"
    print(console.style(header, "dim"))
    for inp in ("text", "audio"):
        line = f"{console.INDENT}{inp + ' in':{label_width}}"
        for out in ("text", "audio"):
            group = cells[(inp, out)]
            if not group:
                cell, styles = "not covered", ("dim",)
            else:
                passed = sum(1 for row in group if row.verdict == "PASS")
                glyph = GLYPHS["PASS"] if passed == len(group) else GLYPHS["FAIL"]
                cell = f"{glyph} {passed}/{len(group)}"
                styles = ("green",) if passed == len(group) else ("red", "bold")
            line += console.style(cell, *styles) + " " * max(1, column - len(cell))
        print(line.rstrip())


def print_summary(rows: list["Row"], out_dir: Path, elapsed: float) -> None:
    """A closing table, so a long run can be read at a glance."""
    passed = sum(1 for row in rows if row.verdict == "PASS")
    total = len(rows)
    print()
    print(console.rule("Summary"))

    name_width = max([len(row.case_id) for row in rows] + [4])
    print(
        console.row(
            console.style(f"{console.INDENT}{'':4}  {'case'.ljust(name_width)}   i\u2192o   outcome", "dim"),
            console.style("reply latency", "dim"),
        )
    )
    for row in rows:
        colour = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}[row.verdict]
        shape = f"{row.input_kind[0]}\u2192{row.output_kind[0]}"
        left = (
            f"{console.INDENT}{console.style(row.verdict.ljust(4), colour, 'bold')}  "
            f"{row.case_id.ljust(name_width)}   {console.style(shape, 'dim')}   "
            f"{console.style(row.outcome, 'dim')}"
        )
        print(console.row(left, console.style(console.duration(row.latency), "dim")))

    verdict_colour = "green" if passed == total else "red"
    print()
    print(
        console.field(
            "result",
            console.style(f"{passed}/{total} cases completed cleanly", verdict_colour, "bold")
            + console.style(f"   {console.duration(elapsed)} wall clock", "dim"),
        )
    )
    print(console.field("output", console.relative(out_dir)))


async def run(args: argparse.Namespace) -> int:
    console.init(no_colour=args.no_color)
    client = RealtimeClient(
        base_url=args.base_url,
        profile=args.profile,
        model=args.model,
        api_key_env=args.api_key_env,
        tls_verify=not args.insecure,
        trailing_silence_ms=args.trailing_silence_ms,
        realtime_pacing=not args.no_pacing,
        on_event=print_event if args.verbose else None,
    )

    try:
        info = await client.preflight()
        client.check_model(info, args.expect_model)
    except RealtimeError as exc:
        print(console.rule("Endpoint"))
        print(console.field("socket", client.ws_url))
        # The failure goes to stderr so a wrapper can capture it, but stdout has
        # to be flushed first or a piped run prints the two streams out of order.
        sys.stdout.flush()
        print(console.field("failed", str(exc), value_styles=("red",)), file=sys.stderr)
        return 2

    print_endpoint(info, client)
    if args.preflight:
        return 0

    try:
        cases, audio_root = build_cases(args)
    except (ValueError, OSError) as exc:
        # A malformed case file is a typo, not a bug; say which line and stop.
        sys.stdout.flush()
        print(console.field("failed", f"cannot read cases: {exc}", value_styles=("red",)), file=sys.stderr)
        return 2

    stem = Path(args.cases).stem if args.cases else "adhoc"
    out_dir = Path(args.out) if args.out else HERE / "runs" / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    print_plan(cases, args.cases or "the command line", out_dir)

    started = time.monotonic()
    rows: list[Row] = []
    failures = 0

    for index, case in enumerate(cases, start=1):
        print_case_header(index, len(cases), case)
        try:
            result = await client.run_case(case, timeout_s=args.timeout, audio_root=str(audio_root))
        except (RealtimeError, OSError, ValueError) as exc:
            print(console.field("status", console.style(f"{GLYPHS['FAIL']} FAIL", "red", "bold")))
            print(console.field("error", str(exc), value_styles=("red",)))
            rows.append(Row.build(case, "FAIL", str(exc).split(":")[0][:40], None))
            failures += 1
            continue

        artifact = out_dir / f"{case.case_id}.json"
        artifact.write_text(json.dumps(result.to_json(), indent=2) + "\n", encoding="utf-8")
        wav = None
        if result.output_pcm16:
            wav = write_wav(
                out_dir / f"{case.case_id}.wav",
                result.output_pcm16,
                sample_rate_hz=result.output_sample_rate_hz,
            )

        print_case(result, artifact, wav, full_text=args.full_text)

        verdict, _ = _status(result)
        rows.append(Row.build(case, verdict, result.termination_reason, reply_latency(result.timings)))
        if verdict != "PASS":
            failures += 1

    print_matrix(rows)
    print_summary(rows, out_dir, time.monotonic() - started)
    return 1 if failures else 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
