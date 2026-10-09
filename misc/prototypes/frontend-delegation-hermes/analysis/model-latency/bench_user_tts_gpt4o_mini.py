"""Tau3 eval user-voice TTS: openai/openai/gpt-4o-mini-tts on the Hub (tau2_ihub_overrides.ihub_tts).

tau2 sends audio.speech.create(model, voice=<persona voice>, input=<utterance>, instructions=<persona style +
"Read the text exactly as written, as a caller on a phone line.">, response_format="pcm") with a 15 s client
timeout and no SDK retries (tau2's @tts_retry retries 3x), then reads the whole body. This benchmark sends the
same request shape with a streamed body to get time to first audio byte as well as the total time, and the
real-time factor (synthesis time / audio duration; PCM16 mono at 24 kHz).

    .venv/bin/python bench_user_tts_gpt4o_mini.py -n 6
"""

from __future__ import annotations

import argparse
import json
import time

import bench_common as bc

MODEL = "openai/openai/gpt-4o-mini-tts"
RATE = 24000
STYLE = ("You are a middle-aged caller, calm and slightly impatient, with a neutral American accent."
         "\nRead the text exactly as written, as a caller on a phone line.")
TEXTS = [
    "Hi, yes, I need to return a pair of headphones I ordered last week.",
    "My zip code is nine four one zero seven.",
    "Okay.",
    "Sure, my email is mia dot garcia four seven at example dot com, and the order number is W, four three one six, one five two.",
    "No, I'd rather keep the original payment method. Can you just refund it to the credit card instead of a gift card, please?",
    "Yes, go ahead.",
    "Wait, sorry, could you repeat that last part about the delivery date?",
    "Thanks, that's everything I needed today.",
]
VOICES = ["ash", "coral", "sage", "echo", "shimmer", "onyx", "nova", "ash"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=6)
    args = ap.parse_args()
    client = bc.hub_client(timeout=15)
    started = bc.now_utc()
    rows = []
    for i in range(args.n):
        text = TEXTS[i % len(TEXTS)]
        t0 = time.perf_counter()
        first = None
        nbytes = 0
        try:
            with client.audio.speech.with_streaming_response.create(
                model=MODEL, voice=VOICES[i % len(VOICES)], input=text, instructions=STYLE, response_format="pcm"
            ) as resp:
                for chunk in resp.iter_bytes(4096):
                    if chunk and first is None:
                        first = time.perf_counter()
                    nbytes += len(chunk)
            total = time.perf_counter() - t0
            audio_s = nbytes / 2 / RATE
            row = {"ok": nbytes > 0, "ttfa_s": round(first - t0, 3) if first else None, "total_s": round(total, 3),
                   "audio_s": round(audio_s, 3), "rtf": round(total / audio_s, 3) if audio_s else None,
                   "chars": len(text)}
        except Exception as exc:  # noqa: BLE001
            row = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "total_s": time.perf_counter() - t0}
        row["i"] = i
        rows.append(row)
        print(json.dumps(row))
        time.sleep(0.5)
    bc.write_result("user_tts_gpt_4o_mini_tts", {"model": MODEL, "started_utc": started, "ended_utc": bc.now_utc(),
                                                 "concurrency": 1, "response_format": "pcm", "client_timeout_s": 15},
                    rows, ["ttfa_s", "total_s", "audio_s", "rtf"])


if __name__ == "__main__":
    main()
