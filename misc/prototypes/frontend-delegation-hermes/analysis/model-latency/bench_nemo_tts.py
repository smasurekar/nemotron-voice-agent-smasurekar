"""Voice Agent TTS: magpie-tts-multilingual, voice John, on the local NeMo Speech container
(nvcr.io/nvidia/nemo-speech.cpp, gRPC Riva API, host port 50051; the agent reaches it as nemo-speech:50051).

Mirrors the agent's client (voice_frontend_backend_agent/speech/tts_riva.py): SynthesizeOnline (streaming) per
sentence, LINEAR_PCM at 22050 Hz, en-US, voice_name John. Measures time to first audio chunk, total time and
RTF (total / audio duration). Sentences are typical fillers and backend answers from the eval.

Does NOT start any container. If the service is not reachable the script says so and exits.
    .venv/bin/python bench_nemo_tts.py -n 8
"""

from __future__ import annotations

import argparse
import json
import time

import bench_common as bc

SERVER = "localhost:50051"
RATE = 22050
TEXTS = [
    "Let me check that for you.",
    "One moment.",
    "I'll look up your account with your name and zip code.",
    "I found your order. It was delivered on Monday, and the headphones are eligible for a return.",
    "Your return is set up. The refund of fifty four dollars will go back to your original credit card in five to seven business days.",
    "Could you spell your last name for me, please?",
    "Sure.",
    "Is there anything else I can help you with today?",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=8)
    ap.add_argument("--voice", default="John")
    args = ap.parse_args()
    import grpc
    import riva.client
    import riva.client.proto.riva_tts_pb2 as rtts

    try:
        grpc.channel_ready_future(grpc.insecure_channel(SERVER)).result(timeout=3)
    except Exception:  # noqa: BLE001
        raise SystemExit(f"NeMo Speech not reachable at {SERVER}; not running (no container is started).")
    auth = riva.client.Auth(None, False, SERVER)
    stub = riva.client.SpeechSynthesisService(auth).stub

    def synth(text: str) -> dict:
        req = rtts.SynthesizeSpeechRequest(text=text, language_code="en-US", sample_rate_hz=RATE,
                                           encoding=riva.client.AudioEncoding.LINEAR_PCM, voice_name=args.voice)
        t0 = time.perf_counter()
        first, nbytes = None, 0
        for resp in stub.SynthesizeOnline(iter([req]), metadata=auth.get_auth_metadata()):
            if resp.audio and first is None:
                first = time.perf_counter()
            nbytes += len(resp.audio)
        total = time.perf_counter() - t0
        audio_s = nbytes / 2 / RATE
        return {"ok": nbytes > 0, "ttfa_ms": round((first - t0) * 1000, 1) if first else None,
                "total_s": round(total, 3), "audio_s": round(audio_s, 3),
                "rtf": round(total / audio_s, 4) if audio_s else None, "chars": len(text)}

    synth("Hello.")  # warm-up, like the agent
    started = bc.now_utc()
    rows = []
    for i in range(args.n):
        row = {"i": i, "text": TEXTS[i % len(TEXTS)], **synth(TEXTS[i % len(TEXTS)])}
        rows.append(row)
        print(json.dumps(row))
    bc.write_result("nemo_tts_magpie_multilingual", {"model": "magpie-tts-multilingual", "voice": args.voice,
                                                     "server": SERVER, "sample_rate": RATE, "started_utc": started,
                                                     "ended_utc": bc.now_utc(), "concurrency": 1},
                    rows, ["ttfa_ms", "total_s", "audio_s", "rtf"])


if __name__ == "__main__":
    main()
