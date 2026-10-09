"""Backend LLM: nvidia/nvidia/nemotron-3-ultra, called the way Hermes calls it.

Hermes (agent/chat_completion_helpers.py, _open_chat_stream) sends stream=True with
stream_options={"include_usage": true}, the gateway's request_overrides
extra_body={"chat_template_kwargs": {"enable_thinking": true}, "reasoning_budget": 1024}
(config/gateway.yaml), the session's tools, and creates a NEW OpenAI client per request
(worker log: "OpenAI client created (chat_completion_stream_request, shared=False)"), so every
call pays a fresh TCP+TLS handshake. This benchmark does the same (new client per request).

Requests are real backend requests replayed from Hermes trace files (logs/fdh_workers/*.trace.jsonl,
backend_trace: true). `--extract` picks N requests whose recorded prompt was 10.5k-12.5k tokens
(the eval's typical ~11-12k) into fixtures/backend_requests.jsonl; the run replays them sequentially.

    .venv/bin/python bench_backend_nemotron_ultra.py --extract   # once
    .venv/bin/python bench_backend_nemotron_ultra.py -n 8
"""

from __future__ import annotations

import argparse
import json
import random
import time

import bench_common as bc

MODEL = "nvidia/nvidia/nemotron-3-ultra"
EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": True}, "reasoning_budget": 1024}
FIXTURE = bc.FIXTURES / "backend_requests.jsonl"
KEEP = ("role", "content", "tool_calls", "tool_call_id", "name")


def extract(n: int) -> None:
    traces = sorted((bc.AGENT_REPO / "logs" / "fdh_workers").glob("*.trace.jsonl"))
    picks = []
    rng = random.Random(7)
    rng.shuffle(traces)
    for path in traces:
        recs = [json.loads(line) for line in path.open()]
        session = next((r for r in recs if r.get("kind") == "session"), None)
        if not session:
            continue
        reqs = {r["api_request_id"]: r for r in recs if r.get("kind") == "request"}
        for r in recs:
            if r.get("kind") != "response" or r.get("api_request_id") not in reqs:
                continue
            usage = r.get("usage") or {}
            if 10500 <= int(usage.get("prompt_tokens") or 0) <= 12500:
                req = reqs[r["api_request_id"]]
                picks.append(
                    {
                        "source": path.name,
                        "api_request_id": req["api_request_id"],
                        "recorded_usage": usage,
                        "recorded_api_duration_s": float(r.get("api_duration") or 0),
                        "messages": [{k: m[k] for k in KEEP if k in m} for m in req["messages"]],
                        "tools": session["tools"],
                    }
                )
                break  # one request per session
        if len(picks) >= n:
            break
    bc.FIXTURES.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text("\n".join(json.dumps(p) for p in picks) + "\n")
    print(f"wrote {len(picks)} requests to {FIXTURE}")


def run(n: int) -> None:
    fixtures = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()][:n]
    rows = []
    started = bc.now_utc()
    for i, fx in enumerate(fixtures):
        client = bc.hub_client(timeout=120)  # Hermes: a new client (new connection) per request
        request = {"model": MODEL, "messages": fx["messages"], "tools": fx["tools"], "extra_body": EXTRA_BODY}
        row = bc.stream_chat(client, request)
        row.update({"i": i, "source": fx["source"], "recorded_prompt_tokens": fx["recorded_usage"].get("prompt_tokens"),
                    "recorded_api_duration_s": fx["recorded_api_duration_s"]})
        client.close()
        rows.append(row)
        print(json.dumps(row))
        time.sleep(1.0)
    bc.write_result(
        "backend_nemotron_3_ultra",
        {"model": MODEL, "started_utc": started, "ended_utc": bc.now_utc(), "concurrency": 1, "streaming": True,
         "extra_body": EXTRA_BODY, "new_client_per_request": True, "fixture": str(FIXTURE)},
        rows,
        ["ttft_s", "ttft_visible_s", "total_s", "decode_tok_s", "e2e_tok_s", "prompt_tokens", "completion_tokens",
         "reasoning_tokens"],
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("-n", type=int, default=8)
    args = ap.parse_args()
    extract(args.n) if args.extract else run(args.n)
