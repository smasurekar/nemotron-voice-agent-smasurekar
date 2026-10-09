"""User-simulator interruption/backchannel decision LLM: azure/openai/gpt-4.1 on the Hub (tau2_ihub_overrides
I0 override: call_names interruption_decision and backchannel_decision; non-streaming, 1 output token).
Replays real requests from the eval's llm_debug files, sequentially.

    .venv/bin/python bench_decision_gpt41.py -n 6
"""
import argparse

import bench_common as bc

ap = argparse.ArgumentParser()
ap.add_argument("-n", type=int, default=6)
a = ap.parse_args()
bc.replay_tau2_calls("decision_gpt_4_1", "azure/openai/gpt-4.1", "interruption_decision", a.n)
