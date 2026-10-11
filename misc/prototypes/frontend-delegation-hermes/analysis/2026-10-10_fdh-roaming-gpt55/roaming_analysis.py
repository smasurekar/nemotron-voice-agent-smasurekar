# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Roaming (user abroad) tasks of the FDH telecom geval runs: who brought up the location, and what the agent did.

Compares, on the telecom tasks scored in all three runs, the gpt-5.5 run with the F1/F2/F4/F8 fixes
(2026-10-09), the gpt-5.5 run before them (2026-10-08) and the gpt-5.2 run (2026-10-07). Optionally
scans the backend decision traces (``hermes.backend_trace``, local ``logs/fdh_workers``) of the
fixes run. Writes ``results/summary.txt`` and ``results/per_task.tsv``.

    python3 misc/prototypes/frontend-delegation-hermes/analysis/2026-10-10_fdh-roaming-gpt55/roaming_analysis.py \
        --dump ../voice-agent-evaluation-dump/tau-3-voice --traces logs/fdh_workers
"""

import argparse
import collections
import json
import re
from pathlib import Path

RUNS = {
    "gpt55fix": "2026-10-09_16-53-22Z_fdh-voice-geval-f1f2f4f8-gpt55usr/fdh_voice_geval_telecom_regular_gpt55fix/tau2/"
    "fdh_voice_geval_telecom_regular_gpt55fix",
    "gpt55": "2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_telecom_regular_gpt55/tau2/"
    "fdh_voice_geval_telecom_regular_gpt55",
    "gpt52": "2026-10-07_16-18-35Z_fdh-voice-geval-fixes/fdh_voice_geval_telecom_regular/tau2/"
    "fdh_voice_geval_telecom_regular",
}
#: A question by the agent about where the user is (a sentence that mentions it and ends in "?").
AGENT_ASKS = re.compile(
    r"[^.?!]*\b(abroad|travel\w*|outside (your|the)|overseas|another country|home (network|country|area)|"
    r"coverage area|where are you|location)\b[^.?!]*\?",
    re.I,
)
USER_SAYS = re.compile(r"abroad|travel|overseas|another country|outside the|france", re.I)
TRACE_MENTION = re.compile(r"\b(roam\w*|abroad|travel\w*|home network|outside)\b", re.I)


def load(run_dir: Path) -> dict:
    """Per task: reward, end, and what the agent and user said and did."""
    out = {}
    for x in json.loads((run_dir / "results.json").read_text())["simulation_index"]:
        if x.get("reward") is None:
            continue
        d = json.loads((run_dir / "simulations" / f"{x['id']}.json").read_text())
        agent, user, acalls, ucalls, uresults, aresults = [], [], [], [], [], []
        for tk in d["ticks"]:
            if c := (tk.get("agent_chunk") or {}).get("content"):
                agent.append(c)
            if c := (tk.get("user_chunk") or {}).get("content"):
                user.append(c)
            acalls += [(c.get("name"), c.get("arguments") or {}) for c in tk.get("agent_tool_calls") or []]
            ucalls += [c.get("name") for c in tk.get("user_tool_calls") or []]
            uresults += [str(r.get("content")) for r in tk.get("user_tool_results") or []]
            aresults += [str(r.get("content")) for r in tk.get("agent_tool_results") or []]
        a, u = "".join(agent), "".join(user)
        m = USER_SAYS.search(u)
        out[x["task_id"]] = {
            "reward": x["reward"],
            "end": x["termination_reason"],
            "session": d.get("provider_session_id"),
            "agent_asked": bool(AGENT_ASKS.search(a)),
            "user_said": bool(m),
            "user_said_in_opening": bool(m) and m.start() < 200,
            "roaming_tool": any("roaming" in (n or "") for n in [c for c, _ in acalls] + ucalls),
            "transfer": any(n == "transfer_to_human_agents" for n, _ in acalls),
            "line_roaming_off_seen": any('"roaming_enabled": false' in r for r in aresults),
            "phone_roaming_off_read": any("Data Roaming Enabled: No" in r for r in uresults),
            "transfer_summary": next((c.get("summary", "") for n, c in acalls if n == "transfer_to_human_agents"), ""),
        }
    return out


def rate(rows: list, key: str = "reward") -> str:
    """Mean of ``key`` over ``rows``, three decimals."""
    return f"{sum(r[key] for r in rows) / len(rows):.3f}" if rows else "-"


def main() -> None:
    """Write results/summary.txt and results/per_task.tsv."""
    p = argparse.ArgumentParser()
    p.add_argument("--dump", required=True, help="tau-3-voice dump folder")
    p.add_argument("--traces", default="", help="worker trace folder of the gpt55fix run (optional)")
    args = p.parse_args()
    here = Path(__file__).resolve().parent / "results"
    data = {k: load(Path(args.dump) / v) for k, v in RUNS.items()}
    common = set.intersection(*(set(v) for v in data.values()))
    abroad = sorted(t for t in common if "user_abroad" in t)
    lines = [f"telecom tasks scored in all three runs: {len(common)}; user abroad: {len(abroad)}", ""]
    hdr = "metric (roaming tasks)                          " + "  ".join(f"{k:>9s}" for k in RUNS)
    lines.append(hdr)

    def row(label: str, fn) -> None:
        lines.append(f"{label:48s}" + "  ".join(f"{fn([data[k][t] for t in abroad]):>9}" for k in RUNS))

    row("Pass^1", rate)
    lines.append(
        f"{'Pass^1, not-abroad tasks':48s}"
        + "  ".join(f"{rate([data[k][t] for t in sorted(common) if 'user_abroad' not in t]):>9}" for k in RUNS)
    )
    row("agent asked where the user is (a question)", lambda r: sum(x["agent_asked"] for x in r))
    row("user said they are abroad (any time)", lambda r: sum(x["user_said"] for x in r))
    row("  ... in their opening turn", lambda r: sum(x["user_said_in_opening"] for x in r))
    row("Pass^1 when user said it", lambda r: rate([x for x in r if x["user_said"]]))
    row("Pass^1 when user never said it", lambda r: rate([x for x in r if not x["user_said"]]))
    row("  n user never said it", lambda r: sum(not x["user_said"] for x in r))
    row("a roaming tool was used (agent or user)", lambda r: sum(x["roaming_tool"] for x in r))
    row("ended in transfer_to_human_agents", lambda r: sum(x["transfer"] for x in r))
    for label, key in (
        ("never said + line roaming_enabled:false seen", "line_roaming_off_seen"),
        ("never said + phone 'Data Roaming: No' read", "phone_roaming_off_read"),
    ):
        row(label, lambda r, key=key: sum((not x["user_said"]) and x[key] for x in r))
    lines += ["", "Pass^1 by roaming variant (line roaming disabled/enabled at start, phone roaming on/off):"]
    for k in RUNS:
        by = collections.defaultdict(list)
        for t in abroad:
            by[re.search(r"user_abroad_roaming_\w+?_(?:on|off)", t).group(0)].append(data[k][t]["reward"])
        lines.append(f"  {k:9s} " + "  ".join(f"{v}: {sum(r):.0f}/{len(r)}" for v, r in sorted(by.items())))

    if args.traces:
        fails = [data["gpt55fix"][t] for t in abroad if not data["gpt55fix"][t]["user_said"]]
        seen = mention = with_block = 0
        examples = []
        for r in fails:
            f = Path(args.traces) / f"{r['session']}.trace.jsonl"
            if not f.exists():
                continue
            seen += 1
            hit = False
            for line in f.read_text().splitlines():
                rec = json.loads(line)
                text = rec["messages"][0]["content"] if rec["kind"] == "request" else ""
                if rec.get("api_call_count") == 1 and rec.get("task_id", "").endswith(":1"):
                    with_block += "<situational_facts>" in text
                m = TRACE_MENTION.search(json.dumps(rec)) if rec["kind"] == "response" else None
                if m and not hit:
                    hit = True
                    snippet = json.dumps(rec)[max(0, m.start() - 140) : m.end() + 80]
                    examples.append(f"  {r['session'][:13]} … {snippet}")
            mention += hit
        lines += [
            "",
            f"Backend traces of the gpt55fix calls where the user never said it: {seen} found, "
            f"<situational_facts> in the backend system prompt: {with_block}, "
            f"any backend response mentioning roaming/abroad/travel/home network: {mention}",
            *examples,
        ]
    lines += ["", "Transfer summaries, gpt55fix, user never said it:"]
    lines += [
        f"  - {data['gpt55fix'][t]['transfer_summary'][:200]}"
        for t in abroad
        if not data["gpt55fix"][t]["user_said"] and data["gpt55fix"][t]["transfer"]
    ]
    (here / "summary.txt").write_text("\n".join(lines) + "\n")
    with (here / "per_task.tsv").open("w") as fh:
        keys = [
            "reward",
            "end",
            "agent_asked",
            "user_said",
            "user_said_in_opening",
            "roaming_tool",
            "transfer",
            "line_roaming_off_seen",
            "phone_roaming_off_read",
            "session",
        ]
        fh.write("task_id\trun\t" + "\t".join(keys) + "\n")
        for t in abroad:
            for k in RUNS:
                fh.write(f"{t}\t{k}\t" + "\t".join(str(data[k][t][c]) for c in keys) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
