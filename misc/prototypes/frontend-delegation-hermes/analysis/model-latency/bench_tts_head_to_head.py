"""Head-to-head TTS latency: gpt-4o-mini-tts (Inference Hub) vs magpie-tts-multilingual (local NeMo Speech).

Same sentences, same machine, same minute, concurrency 1, interleaved (A, B, A, B ...) so Hub load drifts hit both
arms equally. Each sentence is synthesised `--rounds` times per arm.

Arms:
  magpie        local nemo-speech :50051, SynthesizeOnline (streaming), 22.05 kHz, voice John (as the voice agent)
  gpt4omini     Hub openai/openai/gpt-4o-mini-tts, pcm 24 kHz, streamed body, persona instructions (as tau2's user TTS)
  gpt4omini_bare  the same without `instructions` (does the style prompt cost time?)

Measured per call: time to first audio byte (TTFA), total time, audio seconds, RTF (total / audio), and for the Hub
whether audio actually arrives incrementally (first byte vs total). Results: results/bench_tts_head_to_head.json.

    .venv/bin/python bench_tts_head_to_head.py --rounds 2
"""

from __future__ import annotations

import argparse
import json
import time

import bench_common as bc

TEXTS = [  # short fillers, mid-length turns, long answers
    "One moment.",
    "Let me check that for you.",
    "Could you spell your last name for me, please?",
    "My zip code is nine four one zero seven.",
    "I found your order. It was delivered on Monday, and the headphones are eligible for a return.",
    "No, I'd rather keep the original payment method. Can you just refund it to the credit card instead of a gift card, please?",
    "Your return is set up. The refund of fifty four dollars will go back to your original credit card in five to seven business days.",
    "Sure, my email is mia dot garcia four seven at example dot com, and the order number is W, four three one six, one five two.",
]
HUB_MODEL = "openai/openai/gpt-4o-mini-tts"
STYLE = ("You are a middle-aged caller, calm and slightly impatient, with a neutral American accent."
         "\nRead the text exactly as written, as a caller on a phone line.")
SERVER = "localhost:50051"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--no-bare", action="store_true", help="skip the gpt-4o-mini-tts arm without instructions")
    args = ap.parse_args()

    import grpc
    import riva.client
    import riva.client.proto.riva_tts_pb2 as rtts

    grpc.channel_ready_future(grpc.insecure_channel(SERVER)).result(timeout=3)
    auth = riva.client.Auth(None, False, SERVER)
    stub = riva.client.SpeechSynthesisService(auth).stub
    hub = bc.hub_client(timeout=30)

    def magpie(text: str) -> dict:
        req = rtts.SynthesizeSpeechRequest(text=text, language_code="en-US", sample_rate_hz=22050,
                                           encoding=riva.client.AudioEncoding.LINEAR_PCM, voice_name="John")
        t0 = time.perf_counter(); first = None; nbytes = 0; chunks = 0
        for resp in stub.SynthesizeOnline(iter([req]), metadata=auth.get_auth_metadata()):
            if resp.audio:
                first = first or time.perf_counter(); nbytes += len(resp.audio); chunks += 1
        return _row(t0, first, nbytes, 22050, chunks)

    def gpt(text: str, instructions: str | None) -> dict:
        kw = {"instructions": instructions} if instructions else {}
        t0 = time.perf_counter(); first = None; nbytes = 0; chunks = 0
        try:
            with hub.audio.speech.with_streaming_response.create(model=HUB_MODEL, voice="ash", input=text,
                                                                 response_format="pcm", **kw) as resp:
                for chunk in resp.iter_bytes(4096):
                    if chunk:
                        first = first or time.perf_counter(); nbytes += len(chunk); chunks += 1
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}", "total_s": round(time.perf_counter() - t0, 3)}
        return _row(t0, first, nbytes, 24000, chunks)

    magpie("Hello.")  # warm-up both, like the agents do
    gpt("Hello.", STYLE)
    arms = [("magpie", lambda t: magpie(t)), ("gpt4omini", lambda t: gpt(t, STYLE))]
    if not args.no_bare:
        arms.append(("gpt4omini_bare", lambda t: gpt(t, None)))
    started = bc.now_utc(); rows = []
    for r in range(args.rounds):
        for i, text in enumerate(TEXTS):
            order = arms if (r + i) % 2 == 0 else arms[::-1]
            for name, fn in order:
                row = {"arm": name, "round": r, "i": i, "chars": len(text), **fn(text)}
                rows.append(row); print(json.dumps(row))
                time.sleep(0.3)

    summary = {}
    for name, _ in arms:
        R = [x for x in rows if x["arm"] == name and x.get("ok")]
        summary[name] = {k: {"p50": bc.pct([x[k] for x in R], .5), "p90": bc.pct([x[k] for x in R], .9)}
                         for k in ("ttfa_s", "total_s", "audio_s", "rtf", "stream_share")}
        summary[name]["n_ok"] = len(R); summary[name]["n_err"] = sum(1 for x in rows if x["arm"] == name and not x.get("ok"))
        # by sentence length
        summary[name]["ttfa_by_text_p50"] = {i: bc.pct([x["ttfa_s"] for x in R if x["i"] == i], .5) for i in range(len(TEXTS))}
    out = {"meta": {"started_utc": started, "ended_utc": bc.now_utc(), "machine": "/localhome", "concurrency": 1,
                    "rounds": args.rounds, "texts": TEXTS, "hub_model": HUB_MODEL, "local_server": SERVER},
           "summary": summary, "rows": rows}
    bc.OUT.mkdir(parents=True, exist_ok=True)
    (bc.OUT / "bench_tts_head_to_head.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(summary, indent=1))


def _row(t0: float, first: float | None, nbytes: int, rate: int, chunks: int) -> dict:
    total = time.perf_counter() - t0; audio_s = nbytes / 2 / rate
    ttfa = (first - t0) if first else None
    return {"ok": nbytes > 0, "ttfa_s": round(ttfa, 4) if ttfa is not None else None, "total_s": round(total, 4),
            "audio_s": round(audio_s, 3), "rtf": round(total / audio_s, 4) if audio_s else None, "chunks": chunks,
            # 0 = everything arrived at once (no real streaming); near 1 = first byte came early
            "stream_share": round(1 - ttfa / total, 3) if ttfa and total else None}


if __name__ == "__main__":
    main()
