"""User simulator LLM: azure/openai/gpt-5.2 on the Inference Hub (tau2 voice_streaming_user_simulator).

tau2 calls it through litellm as model "openai/azure/openai/gpt-5.2" (=> Hub model "azure/openai/gpt-5.2"),
non-streaming, temperature 0, a seed, call_name user_streaming_response. This replays real requests
from the eval's llm_debug files, sequentially.

    .venv/bin/python bench_user_sim_gpt52.py -n 6           # non-streaming, like tau2 (total time)
    .venv/bin/python bench_user_sim_gpt52.py -n 4 --stream  # TTFT / tok/s
"""
import argparse

import bench_common as bc

ap = argparse.ArgumentParser()
ap.add_argument("-n", type=int, default=6)
ap.add_argument("--stream", action="store_true")
a = ap.parse_args()
bc.replay_tau2_calls("user_sim_gpt_5_2" + ("_stream" if a.stream else ""), "azure/openai/gpt-5.2",
                     "user_streaming_response", a.n, stream=a.stream)
