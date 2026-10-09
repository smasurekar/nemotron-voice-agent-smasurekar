"""Per-model latency from the FDH tau3 voice eval run logs (no network, stdlib only).

For each run folder (agent/ + tau2/ + provenance/) it extracts:

* Frontend nemotron-3.5-lightning: agent/events.jsonl `delegation_decision` (latency_ms, usage, repair=timeout).
* Backend nemotron-3-ultra (Hermes): agent/workers/*.log "API call #N ... in= out= latency=" (one line per LLM
  call), "Retrying API call ... 429", "conversation turn" -> "Turn ended ... api_calls=N";
  agent/gateway_events.jsonl backend_run_dispatched -> backend_run_done (one backend run = one user turn).
* ASR nemotron-speech-streaming: events.jsonl `turn_latency.user_stop_to_asr_final_ms`, `asr_final.latency_ms`.
* TTS magpie (John): events.jsonl `filler_timing.decision_to_first_audio_ms` (decision -> first TTS audio out),
  nemo_speech_logs.txt error counts.
* tau2 (per simulation task.log): user simulator gpt-5.2 = "action='generate_message'" -> next gpt-5.2
  get_response_cost line; user TTS gpt-4o-mini-tts = "USER SIMULATOR:" -> "Voice synthesized ... Duration";
  decision gpt-4.1 = "Listener reactions evaluated: interrupt=.. (Xs), backchannel=.. (Ys)"; retries
  "[Retry k/3] <fn> failed ... Error: <type>"; llm_debug generation_time_seconds (last call per sim);
  judge gpt-5.2 = gpt-5.2 cost lines in the main tau2 log that are in no task.log (count only; their
  latency is not logged); simulations/*.json start/end -> per-conversation duration.

Machine: provenance/gateway_env.txt FDH_HERMES_PYTHON under /home/smasurekar = the OTHER machine, whose local
time (worker and tau2 logs) is IST (+05:30); /localhome = this machine (UTC). All output times are UTC.

    python3 analyze_logs.py            # writes results/log_*.json|csv
"""

from __future__ import annotations

import csv
import glob
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
DUMP = Path("/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump/tau-3-voice")
RUNS = [
    ("control", "2026-10-05_05-33-27Z_fdh-voice-geval/fdh_voice_geval_airline_regular", "old_airline"),
    ("control", "2026-10-05_05-59-44Z_fdh-voice-geval/fdh_voice_geval_retail_regular_r3", "old_retail_r3"),
    ("control", "2026-10-05_05-59-44Z_fdh-voice-geval/fdh_voice_geval_retail_regular_r2", "old_retail_r2"),
    ("control", "2026-10-05_05-59-44Z_fdh-voice-geval/fdh_voice_geval_telecom_regular", "old_telecom"),
    ("fixes", "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_airline_regular", "new_airline"),
    ("fixes", "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_fixes", "new_retail_fixes"),
    ("fixes", "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_rerun43", "new_retail_rerun43"),
    ("fixes", "2026-10-07_16-18-35Z_fdh-voice-geval-fixes/fdh_voice_geval_telecom_regular", "new_telecom"),
]
IST = timedelta(hours=5, minutes=30)

TS = r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[,.]\d{3})"
RE_CALL = re.compile(TS + r" INFO \[worker (\S+) .*?API call #(\d+): model=(\S+) .*? in=(\d+) out=(\d+) total=\d+ latency=([\d.]+)s")
RE_RETRY = re.compile(TS + r" WARNING \[worker (\S+) .*?Retrying API call in ([\d.]+)s \(attempt (\d+)/(\d+)\).*?model=(\S+).*?error=(.*)")
RE_TURN = re.compile(TS + r" INFO \[worker (\S+) .*?agent.turn_context: conversation turn:")
RE_TURN_END = re.compile(TS + r" INFO \[worker (\S+) .*?Turn ended: reason=(\S+).*?api_calls=(\d+)/")
RE_T2 = re.compile(r"^" + TS + r" \| (\w+)\s*\| (\S+) - (.*)$")
RE_LISTEN = re.compile(r"Listener reactions evaluated: interrupt=(\w+) \(([\d.]+)s\), backchannel=(\w+) \(([\d.]+)s\)")
RE_VOICE = re.compile(r"Voice synthesized: .*Duration: ([\d.]+) seconds")
RE_RETRY_T2 = re.compile(r"\[Retry (\d+)/(\d+)\] (\S+) failed.*?Error: (\w+)")
RE_COST_MODEL = re.compile(r"model=([\w./-]+), custom_llm_provider")


def parse_ts(text: str, offset: timedelta) -> datetime:
    dt = datetime.strptime(text.replace(",", "."), "%Y-%m-%d %H:%M:%S.%f")
    return (dt - offset).replace(tzinfo=timezone.utc)


def q(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 3)


def stats(vals) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "p50": q(vals, 0.5), "p90": q(vals, 0.9), "p99": q(vals, 0.99),
            "mean": round(statistics.fmean(vals), 3), "max": round(max(vals), 3)}


def machine_of(run: Path) -> tuple[str, timedelta]:
    env = (run / "provenance" / "gateway_env.txt").read_text()
    other = "FDH_HERMES_PYTHON=/home/smasurekar" in env
    return ("other(/home, IST)" if other else "this(/localhome, UTC)"), (IST if other else timedelta(0))


def hour(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


def events(path: Path):
    if not path.exists():
        return
    with path.open() as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def analyze_agent(run: Path, key: str, machine: str, off: timedelta, rows: dict) -> dict:
    out: dict = {}
    # frontend + ASR + TTS from events.jsonl
    dec, dec_in, dec_out, timeouts, repairs = [], [], [], 0, Counter()
    asr_stop, asr_lat, tts_first, filler_user = [], [], [], []
    for e in events(run / "agent" / "events.jsonl"):
        kind = e.get("kind")
        ts = datetime.fromtimestamp(float(e.get("timestamp", 0)), tz=timezone.utc)
        if kind == "delegation_decision":
            lat = e.get("latency_ms")
            dec.append(lat)
            u = e.get("usage") or {}
            dec_in.append(u.get("input_tokens"))
            dec_out.append(u.get("output_tokens"))
            r = e.get("repair") or ""
            repairs[r or "clean"] += 1
            timeouts += r == "timeout"
            rows["frontend"].append({"run": key, "machine": machine, "utc": ts.isoformat(), "hour": hour(ts),
                                     "latency_ms": lat, "in": u.get("input_tokens"), "out": u.get("output_tokens"),
                                     "repair": r})
        elif kind == "turn_latency":
            asr_stop.append(e.get("user_stop_to_asr_final_ms"))
        elif kind == "asr_final":
            asr_lat.append(e.get("latency_ms"))
        elif kind == "filler_timing":
            tts_first.append(e.get("decision_to_first_audio_ms"))
            filler_user.append(e.get("user_stop_to_first_audio_ms"))
    out["frontend_decision_ms"] = stats(dec)
    out["frontend_tokens"] = {"in_p50": q(dec_in, 0.5), "out_p50": q(dec_out, 0.5)}
    out["frontend_repairs"] = dict(repairs)
    out["frontend_timeouts"] = timeouts
    out["asr_user_stop_to_final_ms"] = stats(asr_stop)
    out["asr_final_latency_ms"] = stats(asr_lat)
    out["tts_decision_to_first_audio_ms"] = stats(tts_first)
    out["filler_user_stop_to_first_audio_ms"] = stats(filler_user)

    # NeMo Speech container log: error lines only (it logs no timings)
    ns = run / "agent" / "nemo_speech_logs.txt"
    if ns.exists():
        c = Counter()
        with ns.open(errors="replace") as fh:
            for line in fh:
                if "codec worker failed" in line:
                    c["tts_codec_worker_failed"] += 1
                elif "failed to write PCM callback" in line:
                    c["tts_pcm_callback_failed"] += 1
                elif "libprotobuf ERROR" in line:
                    c["protobuf_invalid_utf8"] += 1
        out["nemo_speech_log_errors"] = dict(c)

    # backend: gateway runs
    disp, runs_ms, run_err = {}, [], Counter()
    for e in events(run / "agent" / "gateway_events.jsonl"):
        ev = e.get("event")
        if ev == "backend_run_dispatched":
            disp[e["run_id"]] = float(e["ts"])
        elif ev == "backend_run_done" and e.get("run_id") in disp:
            runs_ms.append((float(e["ts"]) - disp[e["run_id"]]) * 1000)
            run_err[e.get("status") or "?"] += 1
    out["backend_run_ms"] = stats(runs_ms)
    out["backend_run_status"] = dict(run_err)

    # backend: Hermes worker logs
    calls, tps, turns_ms, api_calls_per_turn = [], [], [], []
    r429, retries_other, sessions_429 = [], Counter(), set()
    for wlog in sorted((run / "agent" / "workers").glob("*.log")):
        turn_start = {}
        with wlog.open(errors="replace") as fh:
            for line in fh:
                if "API call #" in line:
                    m = RE_CALL.match(line)
                    if m:
                        ts = parse_ts(m.group(1), off)
                        inp, outp, lat = int(m.group(5)), int(m.group(6)), float(m.group(7))
                        calls.append(lat)
                        tps.append(outp / lat if lat > 0 else None)
                        rows["backend"].append({"run": key, "machine": machine, "utc": ts.isoformat(),
                                                "hour": hour(ts), "session": m.group(2), "in": inp, "out": outp,
                                                "latency_s": lat})
                elif "Retrying API call" in line:
                    m = RE_RETRY.match(line)
                    if m:
                        ts = parse_ts(m.group(1), off)
                        if "429" in m.group(7):
                            r429.append(ts)
                            sessions_429.add(m.group(2))
                            rows["backend_429"].append({"run": key, "machine": machine, "utc": ts.isoformat(),
                                                        "hour": hour(ts), "session": m.group(2),
                                                        "attempt": m.group(4)})
                        else:
                            retries_other[m.group(7)[:60]] += 1
                elif "conversation turn:" in line:
                    m = RE_TURN.match(line)
                    if m:
                        turn_start[m.group(2)] = parse_ts(m.group(1), off)
                elif "Turn ended:" in line:
                    m = RE_TURN_END.match(line)
                    if m and m.group(2) in turn_start:
                        turns_ms.append((parse_ts(m.group(1), off) - turn_start.pop(m.group(2))).total_seconds() * 1000)
                        api_calls_per_turn.append(int(m.group(4)))
    out["backend_llm_call_s"] = stats(calls)
    out["backend_llm_call_out_tok_per_s"] = stats(tps)
    out["backend_tokens"] = {"in_p50": q([r["in"] for r in rows["backend"] if r["run"] == key], 0.5),
                             "out_p50": q([r["out"] for r in rows["backend"] if r["run"] == key], 0.5)}
    out["backend_turn_ms"] = stats(turns_ms)
    out["backend_api_calls_per_turn"] = stats(api_calls_per_turn)
    out["backend_429_retries"] = len(r429)
    out["backend_429_sessions"] = len(sessions_429)
    out["backend_429_window_utc"] = [min(r429).isoformat(), max(r429).isoformat()] if r429 else None
    out["backend_other_retries"] = dict(retries_other)
    return out


def analyze_tau2(run: Path, key: str, machine: str, off: timedelta, rows: dict) -> dict:
    out: dict = {}
    tau = run / "tau2"
    simdir = next((p for p in tau.iterdir() if p.is_dir()), None)
    usersim, tts_total, tts_rtf, intr, back, retries = [], [], [], [], [], Counter()
    gpt52_task_ts: Counter = Counter()
    dbg = defaultdict(list)
    for tlog in glob.glob(str(simdir / "artifacts" / "*" / "sim_*" / "task.log")):
        pending_gen = pending_tts = None
        with open(tlog, errors="replace") as fh:
            for line in fh:
                m = RE_T2.match(line)
                if not m:
                    continue
                ts_s, msg = m.group(1), m.group(4)
                if "action='generate_message'" in msg and "Performing turn-taking action" in msg:
                    pending_gen = parse_ts(ts_s, off)
                elif "get_response_cost" in m.group(3):
                    mm = RE_COST_MODEL.search(msg)
                    model = mm.group(1) if mm else "?"
                    if model.endswith("gpt-5.2"):
                        gpt52_task_ts[ts_s] += 1
                        if pending_gen is not None:
                            t = parse_ts(ts_s, off)
                            d = (t - pending_gen).total_seconds()
                            usersim.append(d)
                            rows["tau2"].append({"run": key, "machine": machine, "utc": t.isoformat(),
                                                 "hour": hour(t), "kind": "user_sim_gpt52", "seconds": round(d, 3)})
                            pending_gen = None
                elif "USER SIMULATOR:" in msg:
                    pending_tts = parse_ts(ts_s, off)
                elif "Voice synthesized:" in msg and pending_tts is not None:
                    mm = RE_VOICE.search(msg)
                    t = parse_ts(ts_s, off)
                    d = (t - pending_tts).total_seconds()
                    tts_total.append(d)
                    if mm and float(mm.group(1)) > 0:
                        tts_rtf.append(d / float(mm.group(1)))
                    rows["tau2"].append({"run": key, "machine": machine, "utc": t.isoformat(), "hour": hour(t),
                                         "kind": "user_tts", "seconds": round(d, 3)})
                    pending_tts = None
                elif "Listener reactions evaluated" in msg:
                    mm = RE_LISTEN.search(msg)
                    if mm:
                        intr.append(float(mm.group(2)))
                        back.append(float(mm.group(4)))
                elif "[Retry" in msg:
                    mm = RE_RETRY_T2.search(msg)
                    if mm:
                        retries[f"{mm.group(3)}:{mm.group(4)}"] += 1
    for f in glob.glob(str(simdir / "artifacts" / "*" / "sim_*" / "llm_debug" / "*.json")):
        try:
            d = json.load(open(f))
            g = (d.get("response") or {}).get("generation_time_seconds")
            dbg[d.get("call_name")].append(g)
        except Exception:  # noqa: BLE001
            continue
    out["user_sim_gpt52_s"] = stats(usersim)
    out["user_sim_gpt52_llm_debug_s"] = stats(dbg.get("user_streaming_response", []))
    out["user_tts_s"] = stats(tts_total)
    out["user_tts_rtf"] = stats(tts_rtf)
    out["decision_gpt41_interrupt_s"] = stats(intr)
    out["decision_gpt41_backchannel_s"] = stats(back)
    out["decision_gpt41_llm_debug_s"] = stats(dbg.get("interruption_decision", []) + dbg.get("backchannel_decision", []))
    out["tau2_retries"] = dict(retries)

    # main log: judge-only gpt-5.2 calls and error types
    main = tau / f"{simdir.name}.log"
    judge, errs = 0, Counter()
    left = Counter(gpt52_task_ts)
    with main.open(errors="replace") as fh:
        for line in fh:
            m = RE_T2.match(line)
            if m and "get_response_cost" in m.group(3):
                mm = RE_COST_MODEL.search(m.group(4))
                if mm and mm.group(1).endswith("gpt-5.2"):
                    if left[m.group(1)] > 0:
                        left[m.group(1)] -= 1
                    else:
                        judge += 1
            for name in ("APITimeoutError", "RateLimitError", "InternalServerError", "APIConnectionError",
                         "ServiceUnavailableError", "Timeout"):
                if name in line:
                    errs[name] += 1
                    break
    out["judge_gpt52_calls_outside_sims"] = judge
    out["tau2_main_log_error_lines"] = dict(errs)

    # simulations: conversation durations and effective concurrency
    durs, starts, ends = [], [], []
    for f in glob.glob(str(simdir / "simulations" / "*.json")):
        try:
            d = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        if d.get("duration"):
            durs.append(float(d["duration"]))
            s = datetime.fromisoformat(d["start_time"]) - off
            e = datetime.fromisoformat(d["end_time"]) - off
            starts.append(s)
            ends.append(e)
    out["sim_duration_s"] = stats(durs)
    if starts:
        wall = (max(ends) - min(starts)).total_seconds()
        out["sims"] = {"n": len(durs), "first_start_utc": min(starts).isoformat() + "Z",
                       "last_end_utc": max(ends).isoformat() + "Z", "wall_h": round(wall / 3600, 2),
                       "busy_h": round(sum(durs) / 3600, 2),
                       "effective_concurrency": round(sum(durs) / wall, 2) if wall else None}
    return out


def hourly(rows, value, extra=None) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["machine"], r["hour"], r["run"])].append(r)
    table = []
    for (machine, hr, run), rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        row = {"utc_hour": hr, "machine": machine, "run": run, "n": len(rs),
               "p50": q([value(r) for r in rs], 0.5), "p90": q([value(r) for r in rs], 0.9)}
        if extra:
            row.update(extra(rs))
        table.append(row)
    return table


def fit(rows) -> dict:
    """Least squares latency_s = a + out_tokens / r (a = fixed cost incl. TTFT, r = decode tok/s)."""
    rows = [r for r in rows if r["latency_s"] < 50]  # drop calls that include a 60 s 429 back-off
    xs = [r["out"] for r in rows]
    ys = [r["latency_s"] for r in rows]
    if len(xs) < 10:
        return {}
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0
    return {"n": len(xs), "fixed_s": round(my - b * mx, 3), "s_per_out_token": round(b, 5),
            "implied_decode_tok_s": round(1 / b, 1) if b > 0 else None}


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = defaultdict(list)
    report = {}
    for arm, rel, key in RUNS:
        run = DUMP / rel
        machine, off = machine_of(run)
        hermes = (run / "provenance" / "hermes_commit.txt").read_text().split()[0][:10]
        header = (run / "tau2").glob("*.log")
        conc = sorted({int(x) for f in header for x in re.findall(r"max_concurrency=(\d+)", f.read_text(errors="replace"))})
        info = {"arm": arm, "path": rel, "machine": machine, "hermes_commit": hermes, "max_concurrency": conc}
        info.update(analyze_agent(run, key, machine, off, rows))
        info.update(analyze_tau2(run, key, machine, off, rows))
        report[key] = info
        print(f"{key}: done")

    # backend: per machine x hour, and the a + out/r fit per run and per machine in the shared window
    be = rows["backend"]
    report["_backend_fit_per_run"] = {k: fit([r for r in be if r["run"] == k]) for k in report if not k.startswith("_")}
    w5 = [r for r in be if "2026-10-05T06" <= r["hour"] <= "2026-10-05T08"]
    report["_backend_fit_shared_window_2026-10-05T06_to_08"] = {
        m: fit([r for r in w5 if r["machine"] == m]) for m in sorted({r["machine"] for r in w5})}
    report["_backend_small_output_calls_2026-10-05T06_to_08"] = {
        m: stats([r["latency_s"] for r in w5 if r["machine"] == m and r["out"] <= 150 and r["latency_s"] < 50]) for m in sorted({r["machine"] for r in w5})}
    window = [r for r in be if "2026-10-07T17" <= r["hour"] <= "2026-10-08T04"]
    report["_backend_fit_shared_window_2026-10-07T17_to_10-08T04"] = {
        m: fit([r for r in window if r["machine"] == m]) for m in sorted({r["machine"] for r in window})}
    small = [r for r in window if r["out"] <= 150 and r["latency_s"] < 50]
    report["_backend_small_output_calls_2026-10-07T17_to_10-08T04"] = {
        m: stats([r["latency_s"] for r in small if r["machine"] == m]) for m in sorted({r["machine"] for r in small})}

    tps = lambda rs: {"tok_s_p50": q([r["out"] / r["latency_s"] for r in rs if r["latency_s"] > 0], 0.5),  # noqa: E731
                      "out_p50": q([r["out"] for r in rs], 0.5), "in_p50": q([r["in"] for r in rs], 0.5)}
    write_csv(OUT / "log_backend_by_hour.csv", hourly(be, lambda r: r["latency_s"], tps))
    write_csv(OUT / "log_frontend_by_hour.csv", hourly(rows["frontend"], lambda r: r["latency_ms"],
                                                       lambda rs: {"timeouts": sum(r["repair"] == "timeout" for r in rs)}))
    c429 = Counter((r["run"], r["machine"], r["hour"]) for r in rows["backend_429"])
    write_csv(OUT / "log_backend_429_by_hour.csv",
              [{"run": k[0], "machine": k[1], "utc_hour": k[2], "retries_429": v} for k, v in sorted(c429.items(), key=lambda kv: kv[0][2])])
    write_csv(OUT / "log_tau2_by_hour.csv", [dict(kind=kind, **r) for kind in ("user_sim_gpt52", "user_tts")
                                             for r in hourly([x for x in rows["tau2"] if x["kind"] == kind], lambda r: r["seconds"])])
    write_csv(OUT / "log_backend_calls.csv", be)
    write_csv(OUT / "log_frontend_decisions.csv", rows["frontend"])
    (OUT / "log_metrics.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {OUT}/log_metrics.json and log_*.csv")


if __name__ == "__main__":
    main()
