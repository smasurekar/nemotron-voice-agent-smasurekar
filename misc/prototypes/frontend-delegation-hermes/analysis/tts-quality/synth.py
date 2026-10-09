"""Step 1: synthesise every test sentence REPEATS times per arm, interleaved, concurrency 1.

Magpie: local NeMo Speech gRPC (localhost:50051), SynthesizeOnline, LINEAR_PCM 22050 Hz, en-US, voice John
        (exactly as the voice agent and model-latency/bench_tts_head_to_head.py call it).
gpt-4o-mini-tts: Hub openai/openai/gpt-4o-mini-tts, audio.speech.create, pcm 24 kHz, with the tau2 caller persona
        instructions (STYLE from bench_tts_head_to_head.py), voices ash and coral.
Writes results/audio/<arm>/<id>_r<k>.wav and results/synth.json.

    .venv/bin/python synth.py
"""

from __future__ import annotations

import json
import time

import common as C
from testset import rows

HUB_MODEL = "openai/openai/gpt-4o-mini-tts"
STYLE = ("You are a middle-aged caller, calm and slightly impatient, with a neutral American accent."
         "\nRead the text exactly as written, as a caller on a phone line.")
SERVER = "localhost:50051"


def main() -> None:
    import bench_common as bc
    import grpc
    import riva.client
    import riva.client.proto.riva_tts_pb2 as rtts

    grpc.channel_ready_future(grpc.insecure_channel(SERVER)).result(timeout=3)
    auth = riva.client.Auth(None, False, SERVER)
    stub = riva.client.SpeechSynthesisService(auth).stub
    hub = bc.hub_client(timeout=30)

    def magpie(text: str) -> bytes:
        req = rtts.SynthesizeSpeechRequest(text=text, language_code="en-US", sample_rate_hz=22050,
                                           encoding=riva.client.AudioEncoding.LINEAR_PCM, voice_name="John")
        return b"".join(r.audio for r in stub.SynthesizeOnline(iter([req]), metadata=auth.get_auth_metadata()) if r.audio)

    def gpt(voice: str):
        def f(text: str) -> bytes:
            return hub.audio.speech.create(model=HUB_MODEL, voice=voice, input=text, response_format="pcm",
                                           instructions=STYLE).read()
        return f

    fns = {"magpie_john": magpie, "gpt4omini_ash": gpt("ash"), "gpt4omini_coral": gpt("coral")}
    started = C.now_utc(); out = []
    tests = rows(); arms = list(fns)
    for rep in range(C.REPEATS):
        for i, t in enumerate(tests):
            order = arms if (rep + i) % 2 == 0 else arms[::-1]
            for arm in order:
                row = {"arm": arm, "id": t["id"], "rep": rep, "ok": False, "attempts": 0}
                for attempt in range(3):
                    row["attempts"] = attempt + 1
                    t0 = time.perf_counter()
                    try:
                        pcm = fns[arm](t["text"])
                        if len(pcm) < 2000:
                            raise RuntimeError(f"short audio {len(pcm)} bytes")
                        C.write_wav(C.wav_path(arm, t["id"], rep), pcm, C.ARMS[arm][1])
                        row.update(ok=True, total_s=round(time.perf_counter() - t0, 3),
                                   audio_s=round(len(pcm) / 2 / C.ARMS[arm][1], 3), error=None)
                        break
                    except Exception as exc:  # noqa: BLE001
                        row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                        time.sleep(2)
                out.append(row); print(json.dumps(row), flush=True)
                if arm != "magpie_john":
                    time.sleep(0.3)
    C.RES.mkdir(parents=True, exist_ok=True)
    (C.RES / "synth.json").write_text(json.dumps({
        "meta": {"started_utc": started, "ended_utc": C.now_utc(), "hub_model": HUB_MODEL, "style": STYLE,
                 "local_server": SERVER, "repeats": C.REPEATS, "arms": C.ARMS,
                 "skipped_arm": "nvidia/nvidia/magpie-tts-multilingual-357m on the Hub: audio.speech.create returns "
                                "400 'litellm.BadRequestError: LLM Provider NOT provided' (2 tries, 2026-10-08)"},
        "rows": out}, indent=1))


if __name__ == "__main__":
    main()
