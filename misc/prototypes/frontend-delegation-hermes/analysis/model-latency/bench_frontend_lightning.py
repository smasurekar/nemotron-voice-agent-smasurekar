"""Frontend LLM: nvidia/nvidia/nemotron-3.5-lightning, the FDH delegation decision.

Builds exactly the request the voice server sends (frontend/llm.py OpenAIChatModel + frontend/decider.py
LLMDecider.messages): system prompt rendered from the realtime_eval profile's prompts with the retail
tool capabilities, the single `delegate` tool with tool_choice named, temperature 0, max_tokens 256,
extra_body {"chat_template_kwargs": {"enable_thinking": false}}, non-streaming, one shared client
(keep-alive), client timeout 10 s. Cases come from delegation_cases.jsonl.

Run with the agent repo venv (it imports the agent's prompt code):
    cd <agent repo> && PYTHONPATH=src .venv/bin/python misc/prototypes/frontend-delegation-hermes/analysis/model-latency/bench_frontend_lightning.py -n 6 --stream 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import bench_common as bc

sys.path.insert(0, str(bc.AGENT_REPO / "src"))
os.environ.setdefault("NVIDIA_API_KEY", bc.hub_key())  # in-process only, for ${NVIDIA_API_KEY} in the config

from prototypes.voice_delegation_hermes_agent.cli.delegation_replay import _capabilities  # noqa: E402
from prototypes.voice_delegation_hermes_agent.config import load_delegation_config  # noqa: E402
from prototypes.voice_delegation_hermes_agent.frontend.decider import DecisionContext, LLMDecider  # noqa: E402
from prototypes.voice_delegation_hermes_agent.frontend.prompts import load_prompts  # noqa: E402

PROFILE = bc.AGENT_REPO / "src/prototypes/voice_delegation_hermes_agent/config/profiles/realtime_eval.yaml"
CASES = bc.HERE.parent / "delegation_cases.jsonl"


def build_requests(n: int, domain: str) -> tuple[list[dict], dict]:
    config = load_delegation_config(str(PROFILE))
    llm = config.frontend.llm
    prompts = load_prompts(config.delegation.prompts_path, prompt_features=config.prompt_features)
    decider = LLMDecider(
        None, prompts, timeout_ms=config.frontend.timeout_ms, max_repair_attempts=1,
        on_contract_error="delegate", tool_choice=config.frontend.tool_choice,
        parallel_tool_calls=config.frontend.parallel_tool_calls,
    )
    note = prompts.render("normalization_note") if config.voice.normalization.transcript.enabled else ""
    system = prompts.render("frontend_system", capabilities=_capabilities(domain), normalization_note=note)
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    step = max(1, len(cases) // n)
    reqs = []
    for case in cases[::step][:n]:
        msgs = [*case["history"], {"role": "user", "content": case["user"]}]
        ctx = DecisionContext(
            system_prompt=system, messages=[m for m in msgs if m["role"] in ("user", "assistant")],
            user_text=case["user"], state=case["state"], current_request=case.get("current_request", ""),
            elapsed_s=4 if case["state"] == "WORKING" else None, recent_activity=case.get("recent_activity", []),
            backend_asked_question=bool(case.get("backend_asked_question")),
            last_answer_unheard=bool(case.get("last_answer_unheard")),
        )
        req = {"model": llm.model, "messages": decider.messages(ctx), "temperature": llm.temperature,
               "max_tokens": llm.max_tokens, "tools": [decider._tool], "tool_choice": decider._tool_choice}
        if decider._parallel is not None:
            req["parallel_tool_calls"] = decider._parallel
        if llm.extra_body:
            req["extra_body"] = dict(llm.extra_body)
        reqs.append({"case": case["id"], "request": req})
    settings = {"model": llm.model, "max_tokens": llm.max_tokens, "temperature": llm.temperature,
                "client_timeout_s": llm.timeout_s, "decision_timeout_ms": config.frontend.timeout_ms,
                "hedge_after_ms": config.frontend.hedge_after_ms, "tool_choice": config.frontend.tool_choice,
                "extra_body": llm.extra_body}
    return reqs, settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=6, help="non-streaming requests (what the agent does)")
    ap.add_argument("--stream", type=int, default=4, help="extra streaming requests (TTFT, tok/s)")
    ap.add_argument("--domain", default="retail")
    args = ap.parse_args()
    reqs, settings = build_requests(args.n + args.stream, args.domain)
    client = bc.hub_client(timeout=settings["client_timeout_s"])  # shared keep-alive client, like the agent
    started = bc.now_utc()
    rows = []
    # warm-up (the agent sends one at startup to open the connection); not counted
    bc.plain_chat(client, reqs[0]["request"])
    for i, item in enumerate(reqs):
        mode = "plain" if i < args.n else "stream"
        row = (bc.plain_chat if mode == "plain" else bc.stream_chat)(client, item["request"])
        row.update({"i": i, "mode": mode, "case": item["case"]})
        rows.append(row)
        print(json.dumps(row))
        time.sleep(0.5)
    plain = [r for r in rows if r["mode"] == "plain"]
    stream = [r for r in rows if r["mode"] == "stream"]
    doc = bc.write_result(
        "frontend_nemotron_3_5_lightning",
        {"started_utc": started, "ended_utc": bc.now_utc(), "concurrency": 1, "settings": settings,
         "summary_plain": bc.summarize(plain, ["total_s", "prompt_tokens", "completion_tokens", "e2e_tok_s"]),
         "summary_stream": bc.summarize(stream, ["ttft_s", "total_s", "decode_tok_s", "completion_tokens"])},
        rows,
        ["total_s", "prompt_tokens", "completion_tokens"],
    )
    print(json.dumps({"plain": doc["summary_plain"], "stream": doc["summary_stream"]}, indent=2))


if __name__ == "__main__":
    main()
