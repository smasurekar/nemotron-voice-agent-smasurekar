# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Why telecom stays at 0.50 with the gpt-5.5 user after the reworded F4: hit rate, failure causes, transfers.

Reads the tau2 results of four telecom geval runs and, where present, the backend decision traces
(``hermes.backend_trace``, local ``logs/fdh_workers``). Writes ``results/summary.txt`` and
``results/per_task.tsv`` (one row per task of the ``gpt55roam`` run).

    A=misc/prototypes/frontend-delegation-hermes/analysis/2026-10-11_fdh-telecom-gpt55-deep
    python3 $A/telecom_deep_analysis.py --dump ../voice-agent-evaluation-dump/tau-3-voice --traces logs/fdh_workers
"""

import argparse
import collections
import json
import re
from pathlib import Path

RUNS = {
    "gpt55roam": "2026-10-10_02-53-58Z_fdh-voice-geval-f4roam-gpt55usr/fdh_voice_geval_telecom_regular_gpt55roam/tau2/"
    "fdh_voice_geval_telecom_regular_gpt55roam",
    "gpt55fix": "2026-10-09_16-53-22Z_fdh-voice-geval-f1f2f4f8-gpt55usr/fdh_voice_geval_telecom_regular_gpt55fix/tau2/"
    "fdh_voice_geval_telecom_regular_gpt55fix",
    "gpt55": "2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_telecom_regular_gpt55/tau2/"
    "fdh_voice_geval_telecom_regular_gpt55",
    "gpt52": "2026-10-07_16-18-35Z_fdh-voice-geval-fixes/fdh_voice_geval_telecom_regular/tau2/"
    "fdh_voice_geval_telecom_regular",
}
#: A spoken agent question about where the user is; a barge-in may cut it before the "?".
AGENT_ASKS = re.compile(
    r"\bare you (currently |right now )?(travel\w*|abroad|overseas|outside|away|at home|in your home|in a different|"
    r"in another|out of)|\bwhere are you\b|[^.?!]*\b(abroad|travel\w*|overseas|another country|home area|"
    r"coverage area|location)\b[^.?!]*\?",
    re.I,
)
USER_SAYS = re.compile(r"abroad|travel|overseas|another country|outside the|france|out of the country", re.I)
#: The same question in a backend final answer (before text-to-speech).
BACKEND_ASKS = re.compile(
    r"[^.?!\n]*\b(abroad|travel\w*|overseas|another country|home area|coverage area|outside (your|the)|"
    r"away from home|in a different (city|country|location)|where you are|where are you)\b[^.?!\n]*\?",
    re.I,
)
ROAMING_ACTIONS = {"toggle_roaming", "enable_roaming"}
TRANSFER = "transfer_to_human_agents"


def _events(sim: dict) -> list[tuple]:
    """(who, kind, text-or-name) per tick, consecutive speech chunks of one side merged."""
    out: list[tuple] = []
    for tick in sim["ticks"]:
        for who, key in (("A", "agent_chunk"), ("U", "user_chunk")):
            if text := (tick.get(key) or {}).get("content"):
                if out and out[-1][:2] == (who, "say"):
                    out[-1] = (who, "say", out[-1][2] + text)
                else:
                    out.append((who, "say", text))
        for who, key in (("A", "agent_tool_calls"), ("U", "user_tool_calls")):
            out += [(who, "call", call.get("name")) for call in tick.get(key) or []]
    return out


def load(run_dir: Path) -> dict:
    """Per task: reward, end, missed actions, and who brought up the user's location first."""
    out = {}
    for x in json.loads((run_dir / "results.json").read_text())["simulation_index"]:
        if x.get("reward") is None:
            continue
        sim = json.loads((run_dir / "simulations" / f"{x['id']}.json").read_text())
        ev = _events(sim)
        ask = next((i for i, e in enumerate(ev) if e[:2] == ("A", "say") and AGENT_ASKS.search(e[2])), None)
        said = next((i for i, e in enumerate(ev) if e[:2] == ("U", "say") and USER_SAYS.search(e[2])), None)
        checks = sim["reward_info"].get("action_checks") or []
        out[x["task_id"]] = {
            "reward": x["reward"],
            "end": x["termination_reason"],
            "session": sim.get("provider_session_id"),
            "agent_asked_first": ask is not None and (said is None or ask < said),
            "user_said_first": said is not None and (ask is None or said < ask),
            "transfer": any(e[1:] == ("call", TRANSFER) for e in ev if e[0] == "A"),
            "missed": sorted(
                {
                    c["action"]["name"] + (f"({p})" if (p := c["action"]["arguments"].get("permission")) else "")
                    for c in checks
                    if not c["action_match"]
                }
            ),
        }
    return out


def backend(traces: Path, session: str) -> dict | None:
    """From the backend trace: did a final answer ask where the user is, turns, cut-offs, unknown tools."""
    path = traces / f"{session}.trace.jsonl"
    if not path.exists():
        return None
    recs = [json.loads(line) for line in path.read_text().splitlines()]
    answers = []
    for rec in recs:
        msg = rec.get("assistant_message") or {}
        if rec["kind"] == "response" and not msg.get("tool_calls") and msg.get("content"):
            answers.append(msg["content"])
    first_user = {}
    for rec in recs:
        if rec["kind"] == "request":
            first_user.setdefault(rec["turn_id"], str(rec["messages"][-1].get("content")))
    unknown = sum(
        rec["kind"] == "request"
        and rec["messages"][-1].get("role") == "tool"
        and "does not exist" in str(rec["messages"][-1].get("content"))
        for rec in recs
    )
    return {
        "backend_asked": any(BACKEND_ASKS.search(a) for a in answers),
        "turns": len(first_user),
        "cut_off": sum(t.count("your last answer was cut off") for t in first_user.values()),
        "unknown_tool_calls": unknown,
    }


def rate(rows: list) -> str:
    """Pass^1 of ``rows``."""
    return f"{sum(r['reward'] for r in rows) / len(rows):.2f}" if rows else "  -"


def cell(rows: list, total: int) -> str:
    """``n (share%) pass x``."""
    return f"{len(rows):3d} ({100 * len(rows) / max(total, 1):3.0f}%) pass {rate(rows)}"


def issue(task: str) -> str:
    """Issue type, with ``+roam`` for the user-abroad tasks."""
    return re.match(r"\[(\w+)\]", task).group(1) + (" +roam" if "user_abroad" in task else "")


def cause(task: str, r: dict) -> str:
    """The failure bucket of a failed task."""
    other = set(r["missed"]) - ROAMING_ACTIONS
    if "user_abroad" in task:
        if not (r["agent_asked_first"] or r["user_said_first"]):
            return "R1 roaming, location never known" + (" (+ other steps missed)" if other else " (only roaming)")
        if set(r["missed"]) & ROAMING_ACTIONS:
            return "R2 roaming, location known, roaming still not fixed"
        return "R3 roaming fixed, another step missed"
    return "N1 not roaming, a step missed" if r["missed"] else "N2 not roaming, every action done, assertion failed"


def main() -> None:
    """Write results/summary.txt and results/per_task.tsv."""
    p = argparse.ArgumentParser()
    p.add_argument("--dump", required=True, help="tau-3-voice dump folder")
    p.add_argument("--traces", default="", help="backend trace folder (logs/fdh_workers)")
    args = p.parse_args()
    here = Path(__file__).resolve().parent / "results"
    here.mkdir(exist_ok=True)
    data = {k: load(Path(args.dump) / v) for k, v in RUNS.items()}
    traces = Path(args.traces) if args.traces else None
    for rows in data.values():
        for r in rows.values():
            r["be"] = backend(traces, r["session"]) if traces and r["session"] else None
    lines = ["1. Pass^1 by issue type (all scored tasks of each run)", ""]
    groups = sorted({issue(t) for t in data["gpt55roam"]})
    lines.append(f"{'group':26s}" + "".join(f"{k:>18s}" for k in RUNS))
    for g in [*groups, "ALL"]:
        cells = []
        for rows in data.values():
            sel = [r for t, r in rows.items() if g in ("ALL", issue(t))]
            cells.append(f"{len(sel):4d}  {rate(sel):>5s}     ")
        lines.append(f"{g:26s}" + "".join(f"{c:>18s}" for c in cells))

    lines += ["", "2. Who brought up the user's location, roaming tasks (audio; backend answer from the trace)", ""]
    for k, rows in data.items():
        ab = [r for t, r in rows.items() if "user_abroad" in t]
        n = len(ab)
        known = [r for r in ab if r["agent_asked_first"] or r["user_said_first"]]
        be = [r for r in ab if r["be"]]
        lines += [
            f"{k} (n={n}, pass {rate(ab)})",
            f"  agent asked first           {cell([r for r in ab if r['agent_asked_first']], n)}",
            f"  user volunteered first      {cell([r for r in ab if r['user_said_first']], n)}",
            f"  location never known        {cell([r for r in ab if r not in known], n)}",
        ]
        if be:
            lines.append(f"  backend answer asked (trace){cell([r for r in be if r['be']['backend_asked']], len(be))}")
    roam = data["gpt55roam"]
    lines += ["", "   gpt55roam, roaming tasks, backend asked / never known, by:"]
    by = collections.defaultdict(list)
    for t, r in roam.items():
        if "user_abroad" in t:
            by[issue(t)].append(r)
            visible = "data_usage_exceeded" in t
            by["records show data over the limit" if visible else "no visible cause in the records"] += [r]
    for g, rows in by.items():
        asked = sum(bool(r["be"] and r["be"]["backend_asked"]) for r in rows)
        never = sum(not (r["agent_asked_first"] or r["user_said_first"]) for r in rows)
        share = 100 * asked / len(rows)
        lines.append(f"     {g:34s} n={len(rows):2d}  backend asked {asked:2d} ({share:3.0f}%)  never known {never:2d}")

    lines += ["", "3. Failure causes, gpt55roam (57 failed tasks)", ""]
    buckets = collections.defaultdict(list)
    for t, r in roam.items():
        if not r["reward"]:
            buckets[cause(t, r)].append((t, r))
    for b, rows in sorted(buckets.items()):
        ended = collections.Counter("transfer" if r["transfer"] else r["end"] for _, r in rows)
        lines.append(f"  {len(rows):3d}  {b:60s} ended: {dict(ended)}")
    missed = collections.Counter(
        m for rows in buckets.values() for _, r in rows for m in set(r["missed"]) - ROAMING_ACTIONS
    )
    lines.append(f"  non-roaming steps missed in failed tasks: {dict(missed.most_common())}")

    lines += ["", "4. Transfers, cut-off answers and calls to tools the backend does not have (traced runs)", ""]
    for k, rows in data.items():
        traced = [r for r in rows.values() if r["be"]]
        if not traced:
            lines.append(f"  {k:10s} no backend traces")
            continue
        turns = sum(r["be"]["turns"] for r in traced)
        tr = [r for r in rows.values() if r["transfer"]]
        lines.append(
            f"  {k:10s} transfers {len(tr):3d}/{len(rows)} (pass {rate(tr)}); backend turns {turns}, "
            f"answer cut off {100 * sum(r['be']['cut_off'] for r in traced) / turns:.0f}%; "
            f"unknown-tool calls {sum(r['be']['unknown_tool_calls'] for r in traced)}"
        )
    (here / "summary.txt").write_text("\n".join(line.rstrip() for line in lines) + "\n")
    keys = ["reward", "end", "agent_asked_first", "user_said_first", "transfer", "missed", "session"]
    with (here / "per_task.tsv").open("w") as fh:
        fh.write("task_id\tcause\tbackend_asked\t" + "\t".join(keys) + "\n")
        for t, r in sorted(roam.items()):
            asked = r["be"]["backend_asked"] if r["be"] else ""
            why = "" if r["reward"] else cause(t, r)
            fh.write(f"{t}\t{why}\t{asked}\t" + "\t".join(str(r[c]) for c in keys) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
