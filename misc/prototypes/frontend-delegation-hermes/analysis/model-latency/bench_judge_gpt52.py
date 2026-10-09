"""Judge LLM: azure/openai/gpt-5.2 on the Hub, as tau2's full-duplex hallucination check calls it
(tau2.evaluator.hallucination_reviewer: system FULL_DUPLEX_SYSTEM_PROMPT + user prompt with the task's user
instructions and the consolidated conversation; non-streaming, call_name llm_judge_hallucination_check).
The NL-assertion judge (TAU2_JUDGE_MODEL) is the same Hub model.

Prompts are rebuilt with tau2's own code from finished simulations of the eval. Run with the tau2 venv:
    /localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar/.venv/bin/python bench_judge_gpt52.py -n 5
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import sys
import time
from contextlib import redirect_stdout

import bench_common as bc

TAU2 = "/localhome/local-smasurekar/smasurekar/tau2-bench-smasurekar"
RUN = bc.DUMP / "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_rerun43/tau2/fdh_voice_geval_retail_regular_rerun43"


def build(n: int) -> list[dict]:
    sys.path.insert(0, f"{TAU2}/src")
    with redirect_stdout(io.StringIO()):  # tau2 prints its registry on import
        from tau2.data_model.simulation import SimulationRun
        from tau2.data_model.tasks import Task
        from tau2.evaluator.hallucination_reviewer import FULL_DUPLEX_SYSTEM_PROMPT, FULL_DUPLEX_USER_PROMPT
        from tau2.utils.display import MarkdownDisplay
    tasks = {t["id"]: t for t in json.loads((RUN / "results.json").read_text())["tasks"]}
    files = sorted(glob.glob(str(RUN / "simulations" / "*.json")))
    step = max(1, len(files) // n)
    out = []
    for path in files[::step][:n]:
        sim = SimulationRun.model_validate_json(open(path).read())
        task = Task.model_validate(tasks[sim.task_id])
        conv = MarkdownDisplay.display_ticks_consolidated(sim.ticks, user_visible_only=True)
        user = FULL_DUPLEX_USER_PROMPT.format(user_instructions=str(task.user_scenario), conversation=conv)
        out.append({"task_id": sim.task_id, "messages": [{"role": "system", "content": FULL_DUPLEX_SYSTEM_PROMPT},
                                                          {"role": "user", "content": user}]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5)
    args = ap.parse_args()
    items = build(args.n)
    client = bc.hub_client(timeout=180)
    started = bc.now_utc()
    rows = []
    for i, item in enumerate(items):
        row = bc.plain_chat(client, {"model": "azure/openai/gpt-5.2", "messages": item["messages"]})
        row.update({"i": i, "task_id": item["task_id"]})
        rows.append(row)
        print(json.dumps(row))
        time.sleep(0.5)
    bc.write_result("judge_gpt_5_2", {"model": "azure/openai/gpt-5.2", "call_name": "llm_judge_hallucination_check",
                                      "started_utc": started, "ended_utc": bc.now_utc(), "concurrency": 1,
                                      "streaming": False, "source_run": str(RUN)}, rows,
                    ["total_s", "prompt_tokens", "completion_tokens", "reasoning_tokens", "e2e_tok_s"])


if __name__ == "__main__":
    main()
