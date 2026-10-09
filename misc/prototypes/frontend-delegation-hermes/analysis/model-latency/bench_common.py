"""Shared helpers for the per-model concurrency-1 benchmarks (FDH tau3 voice eval models).

The Inference Hub key is read in-process from the agent repo's .env (NVIDIA_API_KEY). It is never
printed or written: results only contain timings, token counts and request shapes.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
FIXTURES = HERE / "fixtures"
AGENT_REPO = HERE.parents[4]  # .../nemotron-voice-agent-smasurekar
ENV_FILE = AGENT_REPO / ".env"
DUMP = Path("/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump/tau-3-voice")
BASE_URL = "https://inference-api.nvidia.com/v1"


def hub_key() -> str:
    """NVIDIA_API_KEY from the environment, else parsed from the agent repo's .env (never echoed)."""
    if os.environ.get("NVIDIA_API_KEY"):
        return os.environ["NVIDIA_API_KEY"]
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :]
        if line.startswith("NVIDIA_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise SystemExit("NVIDIA_API_KEY not found in environment or .env")


def hub_client(timeout: float = 120.0):
    from openai import OpenAI

    return OpenAI(api_key=hub_key(), base_url=BASE_URL, timeout=timeout, max_retries=0)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pct(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def summarize(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows), "n_ok": sum(1 for r in rows if r.get("ok"))}
    ok = [r for r in rows if r.get("ok")]
    for key in keys:
        vals = [r[key] for r in ok if r.get(key) is not None]
        if vals:
            out[key] = {
                "p50": round(pct(vals, 0.5), 3),
                "p90": round(pct(vals, 0.9), 3),
                "mean": round(statistics.fmean(vals), 3),
                "min": round(min(vals), 3),
                "max": round(max(vals), 3),
            }
    return out


def stream_chat(client, request: dict[str, Any]) -> dict[str, Any]:
    """One streaming chat completion. Returns TTFT (first token of any kind: reasoning, content or
    tool call), first content/tool token, total time, usage and decode rate."""
    req = dict(request)
    req["stream"] = True
    req["stream_options"] = {"include_usage": True}
    t0 = time.perf_counter()
    first_any = first_visible = None
    usage = None
    finish = None
    n_chunks = 0
    try:
        stream = client.chat.completions.create(**req)
        for chunk in stream:
            now = time.perf_counter()
            if getattr(chunk, "usage", None):
                usage = chunk.usage.model_dump()
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            extra = getattr(delta, "model_extra", None) or {}
            reasoning = extra.get("reasoning_content") or extra.get("reasoning")
            if delta.content or delta.tool_calls or reasoning:
                n_chunks += 1
                if first_any is None:
                    first_any = now
                if (delta.content or delta.tool_calls) and first_visible is None:
                    first_visible = now
            if choice.finish_reason:
                finish = choice.finish_reason
        t_end = time.perf_counter()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "total_s": time.perf_counter() - t0}
    out_tokens = (usage or {}).get("completion_tokens")
    decode_s = (t_end - first_any) if first_any else None
    details = (usage or {}).get("completion_tokens_details") or {}
    return {
        "ok": True,
        "ttft_s": round(first_any - t0, 3) if first_any else None,
        "ttft_visible_s": round(first_visible - t0, 3) if first_visible else None,
        "total_s": round(t_end - t0, 3),
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": out_tokens,
        "reasoning_tokens": details.get("reasoning_tokens"),
        "decode_tok_s": round(out_tokens / decode_s, 1) if out_tokens and decode_s and decode_s > 0 else None,
        "e2e_tok_s": round(out_tokens / (t_end - t0), 1) if out_tokens else None,
        "finish_reason": finish,
        "stream_chunks": n_chunks,
    }


def plain_chat(client, request: dict[str, Any]) -> dict[str, Any]:
    """One non-streaming chat completion (what the FDH frontend and tau2/litellm do)."""
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(**request)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "total_s": time.perf_counter() - t0}
    total = time.perf_counter() - t0
    usage = resp.usage.model_dump() if resp.usage else {}
    out_tokens = usage.get("completion_tokens")
    return {
        "ok": True,
        "total_s": round(total, 3),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": out_tokens,
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "e2e_tok_s": round(out_tokens / total, 1) if out_tokens else None,
        "finish_reason": resp.choices[0].finish_reason if resp.choices else None,
    }


def write_result(name: str, meta: dict[str, Any], rows: list[dict[str, Any]], keys: list[str]) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {"benchmark": name, **meta, "summary": summarize(rows, keys), "rows": rows}
    path = OUT / f"bench_{name}.json"
    path.write_text(json.dumps(doc, indent=2))
    print(json.dumps(doc["summary"], indent=2))
    print(f"wrote {path}")
    return doc


# ---- tau2 replay helpers (user simulator / decision LLM) ----

def tau2_debug_requests(call_name: str, n: int, runs: list[str] | None = None) -> list[dict[str, Any]]:
    """Real tau2 requests (llm_debug/*_<call_name>_*.json, the last call of that kind per simulation),
    spread over the given run folders. Content stored as a list of lines is joined back with newlines."""
    runs = runs or [
        "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_rerun43",
        "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_airline_regular",
    ]
    files: list[Path] = []
    for run in runs:
        files += sorted((DUMP / run / "tau2").glob(f"*/artifacts/*/sim_*/llm_debug/*_{call_name}_*.json"))
    step = max(1, len(files) // n)
    out = []
    for path in files[::step][:n]:
        doc = json.loads(path.read_text())
        req = doc["request"]
        msgs = []
        for m in req["messages"]:
            content = m.get("content")
            if isinstance(content, list):
                content = "\n".join(str(x) for x in content)
            msgs.append({"role": m["role"], "content": content})
        out.append({
            "source": str(path.relative_to(DUMP)),
            "messages": msgs,
            "kwargs": {k: v for k, v in (req.get("kwargs") or {}).items() if k in ("temperature", "seed")},
            "recorded_generation_time_s": (doc.get("response") or {}).get("generation_time_seconds"),
            "recorded_usage": (doc.get("response") or {}).get("usage"),
        })
    return out


def replay_tau2_calls(name: str, hub_model: str, call_name: str, n: int, stream: bool = False) -> None:
    """Sequentially replay n real tau2 calls against the Hub (litellm sends them non-streaming)."""
    items = tau2_debug_requests(call_name, n)
    client = hub_client(timeout=120)
    started = now_utc()
    rows = []
    for i, item in enumerate(items):
        req = {"model": hub_model, "messages": item["messages"], **item["kwargs"]}
        row = (stream_chat if stream else plain_chat)(client, req)
        row.update({"i": i, "source": item["source"], "recorded_generation_time_s": item["recorded_generation_time_s"]})
        rows.append(row)
        print(json.dumps(row))
        time.sleep(0.5)
    write_result(name, {"model": hub_model, "call_name": call_name, "started_utc": started, "ended_utc": now_utc(),
                        "concurrency": 1, "streaming": stream}, rows,
                 ["ttft_s", "total_s", "prompt_tokens", "completion_tokens", "reasoning_tokens", "decode_tok_s",
                  "e2e_tok_s"])
