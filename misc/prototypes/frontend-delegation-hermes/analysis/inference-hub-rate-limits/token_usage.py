"""Backend nemotron-3-ultra token usage (TPM) from the FDH tau3 voice eval run logs (no network, stdlib only).

Source of truth: Hermes worker logs agent/workers/*.log, one line per LLM call that returned usage:
    "API call #N: model=nvidia/nvidia/nemotron-3-ultra ... in=I out=O total=T latency=Ls [cache=C/I (x%)]"
`in` is the full prompt (cached prompt tokens included), `out` includes reasoning tokens. Calls that got a 429
or whose stream broke return no usage and are not counted (they are counted separately as throttled retries).

Cross-check: agent/gateway_events.jsonl `backend_run_done.usage_delta` (one per backend run = user turn) reports
input WITHOUT the cached tokens, so  sum(worker in - cache) == sum(gateway input_tokens)  per run.

Time zones: worker logs use the machine's local time. Runs on the other machine (provenance/gateway_env.txt
FDH_HERMES_PYTHON under /home/smasurekar) log IST (+05:30); /localhome runs log UTC. The offset is checked against
the gateway's epoch timestamps (each run's last API call vs its backend_run_done), and all output times are UTC.

Metrics
* per call: tokens (in, out, total, cached).
* per session (= concurrency 1): avg TPM = tokens / (first conversation turn -> last call, min 60 s);
  peak TPM = most tokens in any 60 s window.
* per run and across all runs on one UTC timeline: busiest 60 s window (all sessions together), how many sessions
  overlap that window ("in win"; a session spans its first conversation turn to its last call, so one ending and
  another starting inside the same 60 s both count), and the most sessions live at one instant ("max live").
* projection to N sessions: N x mean session avg TPM; N x p95 session peak TPM (worst case, all bursts aligned);
  observed busiest window / live sessions x N.

    python3 token_usage.py              # prints tables, writes results/token_usage.json
    python3 token_usage.py --target 128
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
DUMP = Path("/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump/tau-3-voice")
# The 10 runs in context.md (Run 1..10).
RUNS = [
    ("Run 1 - Airline", "2026-10-05_05-33-27Z_fdh-voice-geval/fdh_voice_geval_airline_regular"),
    ("Run 2 - Retail", "2026-10-05_05-59-44Z_fdh-voice-geval/fdh_voice_geval_retail_regular_r2"),
    ("Run 3 - Retail", "2026-10-05_05-59-44Z_fdh-voice-geval/fdh_voice_geval_retail_regular_r3"),
    ("Run 4 - Telecom", "2026-10-05_05-59-44Z_fdh-voice-geval/fdh_voice_geval_telecom_regular"),
    ("Run 5 - Airline", "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_airline_regular"),
    ("Run 6 - Retail", "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_fixes"),
    ("Run 7 - Retail", "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_rerun43"),
    ("Run 8 - Telecom", "2026-10-07_16-18-35Z_fdh-voice-geval-fixes/fdh_voice_geval_telecom_regular"),
    ("Run 9 - Retail", "2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_retail_regular_gpt55"),
    ("Run 10 - Telecom", "2026-10-08_11-04-25Z_fdh-voice-geval-gpt55usr/fdh_voice_geval_telecom_regular_gpt55"),
]
MODEL = "nvidia/nvidia/nemotron-3-ultra"
IST = timedelta(hours=5, minutes=30)
WINDOW = 60.0  # seconds

TS = r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[,.]\d{3})"
RE_CALL = re.compile(TS + r" INFO \[worker (\S+) .*?API call #\d+: model=(\S+) .*? in=(\d+) out=(\d+) total=(\d+)"
                     r" latency=[\d.]+s(?: cache=(\d+)/\d+)?")
RE_TURN = re.compile(TS + r" INFO \[worker (\S+) .*?agent.turn_context: conversation turn:")
RE_RETRY = re.compile(r"Retrying API call in")


@dataclass
class Session:
    sid: str
    start: float | None = None  # first conversation turn, epoch s (UTC)
    calls: list = field(default_factory=list)  # (epoch s, in, out, total, cached)

    @property
    def end(self) -> float:
        return self.calls[-1][0]

    @property
    def tokens(self) -> int:
        return sum(c[3] for c in self.calls)

    @property
    def minutes(self) -> float:
        return max(WINDOW, self.end - (self.start or self.calls[0][0])) / 60

    @property
    def avg_tpm(self) -> float:
        return self.tokens / self.minutes

    @property
    def peak_tpm(self) -> int:
        return busiest_window([(c[0], c[3]) for c in self.calls])[0]


def parse_ts(text: str, offset: timedelta) -> float:
    dt = datetime.strptime(text.replace(",", "."), "%Y-%m-%d %H:%M:%S.%f")
    return (dt - offset).replace(tzinfo=timezone.utc).timestamp()


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def busiest_window(events: list[tuple[float, int]]) -> tuple[int, float]:
    """Most tokens in any [t, t + 60 s) window starting at a call; returns (tokens, window start)."""
    events = sorted(events)
    best, best_t, total, j = 0, 0.0, 0, 0
    for i, (t, _) in enumerate(events):
        while j < len(events) and events[j][0] - t < WINDOW:
            total += events[j][1]
            j += 1
        if total > best:
            best, best_t = total, t
        total -= events[i][1]
    return best, best_t


def q(vals, p):
    vals = sorted(vals)
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def stats(vals) -> dict:
    return {"n": len(vals), "mean": round(statistics.fmean(vals)), "p50": round(q(vals, 0.5)),
            "p95": round(q(vals, 0.95)), "max": round(max(vals))}


def machine_offset(run: Path) -> timedelta:
    env = (run / "provenance" / "gateway_env.txt").read_text()
    return IST if "FDH_HERMES_PYTHON=/home/smasurekar" in env else timedelta(0)


def gateway(run: Path) -> tuple[int, dict]:
    """Sum of gateway input_tokens, and each session's last backend_run_done epoch."""
    inp, last = 0, {}
    with (run / "agent" / "gateway_events.jsonl").open() as fh:
        for line in fh:
            e = json.loads(line)
            if e.get("event") == "backend_run_done":
                inp += (e.get("usage_delta") or {}).get("input_tokens", 0)
                last[e["session_id"]] = max(last.get(e["session_id"], 0), e["ts"])
    return inp, last


def load_run(run: Path) -> dict:
    off = machine_offset(run)
    sessions: dict[str, Session] = {}
    retries = 0
    for log in sorted((run / "agent" / "workers").glob("*.log")):
        with log.open(errors="ignore") as fh:
            for line in fh:
                if RE_RETRY.search(line):
                    retries += 1
                elif m := RE_CALL.match(line):
                    if m[3] == MODEL:
                        s = sessions.setdefault(m[2], Session(m[2]))
                        s.calls.append((parse_ts(m[1], off), int(m[4]), int(m[5]), int(m[6]), int(m[7] or 0)))
                elif m := RE_TURN.match(line):
                    s = sessions.setdefault(m[2], Session(m[2]))
                    if s.start is None:
                        s.start = parse_ts(m[1], off)
    sessions = {k: s for k, s in sessions.items() if s.calls}
    gw_in, gw_last = gateway(run)
    # Clock check: a session's last API call lands just before its last backend_run_done.
    skew = [gw_last[k] - s.end for k, s in sessions.items() if k in gw_last]
    return {"offset_h": off.total_seconds() / 3600, "sessions": sessions, "retries": retries,
            "gateway_in": gw_in, "clock_skew_s_p50": round(q(skew, 0.5), 2) if skew else None}


def live_in(sessions, t0: float) -> int:
    return sum(1 for s in sessions if (s.start or s.calls[0][0]) < t0 + WINDOW and s.end >= t0)


def aggregate(sessions: list[Session]) -> dict:
    tokens, t0 = busiest_window([(c[0], c[3]) for s in sessions for c in s.calls])
    first = min(s.start or s.calls[0][0] for s in sessions)
    last = max(s.end for s in sessions)
    return {"busiest_60s_tokens": tokens, "busiest_60s_utc": utc(t0), "live_sessions": live_in(sessions, t0),
            "mean_tpm_over_span": round(sum(s.tokens for s in sessions) / ((last - first) / 60)),
            "max_live_sessions": max_concurrency(sessions)}


def max_concurrency(sessions: list[Session]) -> int:
    edges = sorted([(s.start or s.calls[0][0], 1) for s in sessions] + [(s.end, -1) for s in sessions],
                   key=lambda e: (e[0], e[1]))
    live = best = 0
    for _, d in edges:
        live += d
        best = max(best, live)
    return best


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", type=int, default=128, help="sessions to project to (default 128)")
    args = ap.parse_args()

    result, everyone = {"runs": {}}, []
    per_call, per_in, per_out, sess_avg, sess_peak = [], [], [], [], []
    tot_calls = tot_retries = 0
    print(f"{'run':<17}{'tz':>4}{'skew':>6}{'sess':>6}{'calls':>7}{'429':>5}  {'sess avg TPM':>12}{'sess peak TPM':>14}"
          f"  {'run busiest 60s':>16} {'in win':>6}{'max live':>9}  {'worker in-cache':>15}{'gateway in':>12}")
    for name, rel in RUNS:
        r = load_run(DUMP / rel)
        S = list(r["sessions"].values())
        everyone += S
        calls = [c for s in S for c in s.calls]
        agg = aggregate(S)
        w_in = sum(c[1] - c[4] for c in calls)
        avg, peak = [s.avg_tpm for s in S], [s.peak_tpm for s in S]
        per_call += [c[3] for c in calls]
        per_in += [c[1] for c in calls]
        per_out += [c[2] for c in calls]
        sess_avg += avg
        sess_peak += peak
        tot_calls += len(calls)
        tot_retries += r["retries"]
        result["runs"][name] = {"path": rel, "log_tz_offset_h": r["offset_h"],
                                "clock_skew_s_p50": r["clock_skew_s_p50"], "sessions": len(S), "calls": len(calls),
                                "throttled_retries": r["retries"], "session_avg_tpm": stats(avg),
                                "session_peak_tpm": stats(peak), **agg,
                                "worker_in_minus_cache": w_in, "gateway_in": r["gateway_in"]}
        print(f"{name:<17}{r['offset_h']:>4.1f}{r['clock_skew_s_p50']:>6}{len(S):>6}{len(calls):>7}{r['retries']:>5}"
              f"  {statistics.fmean(avg):>12,.0f}{max(peak):>14,}  {agg['busiest_60s_tokens']:>16,}"
              f" {agg['live_sessions']:>6}{agg['max_live_sessions']:>9}  {w_in:>15,}{r['gateway_in']:>12,}")

    allagg = aggregate(everyone)
    mean_avg = statistics.fmean(sess_avg)
    pooled = sum(s.tokens for s in everyone) / sum(s.minutes for s in everyone)
    result["all"] = {
        "sessions": len(everyone), "calls": tot_calls, "throttled_retries": tot_retries,
        "throttled_pct_of_calls": round(100 * tot_retries / tot_calls, 2),
        "tokens_per_call": stats(per_call), "input_per_call": stats(per_in), "output_per_call": stats(per_out),
        "session_avg_tpm": stats(sess_avg), "session_avg_tpm_pooled": round(pooled),
        "session_peak_tpm": stats(sess_peak), "all_runs_timeline": allagg,
    }
    scaled = allagg["busiest_60s_tokens"] / allagg["live_sessions"] * args.target
    result["projection"] = {"target_sessions": args.target, "mean_tpm": round(mean_avg * args.target),
                            "worst_case_all_p95_bursts": round(q(sess_peak, 0.95) * args.target),
                            "scaled_observed_busiest": round(scaled)}

    a, p = result["all"], result["projection"]
    print(f"\nAll runs: {a['sessions']} sessions, {a['calls']:,} calls, {a['throttled_retries']} throttled retries "
          f"({a['throttled_pct_of_calls']}% of calls)")
    print(f"{'':<26}{'mean':>10}{'p50':>10}{'p95':>10}{'max':>10}")
    for label, key in [("tokens per call", "tokens_per_call"), ("  input per call", "input_per_call"),
                       ("  output per call", "output_per_call"), ("session avg TPM", "session_avg_tpm"),
                       ("session busiest-60s TPM", "session_peak_tpm")]:
        s = a[key]
        print(f"{label:<26}{s['mean']:>10,}{s['p50']:>10,}{s['p95']:>10,}{s['max']:>10,}")
    print(f"session avg TPM, pooled (all tokens / all session-minutes): {a['session_avg_tpm_pooled']:,}")
    t = a["all_runs_timeline"]
    print(f"All runs on one UTC timeline: busiest 60 s = {t['busiest_60s_tokens']:,} tokens at {t['busiest_60s_utc']}"
          f" UTC with {t['live_sessions']} live sessions (max live at any time: {t['max_live_sessions']})")
    print(f"Projection to {p['target_sessions']} sessions: mean {p['mean_tpm'] / 1e6:.2f}M TPM | observed busiest scaled "
          f"{p['scaled_observed_busiest'] / 1e6:.2f}M | worst case (all p95 bursts aligned) "
          f"{p['worst_case_all_p95_bursts'] / 1e6:.2f}M")

    OUT.mkdir(exist_ok=True)
    (OUT / "token_usage.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nwrote {OUT / 'token_usage.json'}")


if __name__ == "__main__":
    main()
