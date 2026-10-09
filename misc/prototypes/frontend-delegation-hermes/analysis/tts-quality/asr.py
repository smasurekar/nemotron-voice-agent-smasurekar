"""Step 2: transcribe every synthesised clip with two ASR models. Both get the same 16 kHz mono audio.

  nemotron  local NeMo Speech (nemotron-speech-streaming-en-0.6b), Riva gRPC localhost:50051, streaming recogniser,
            LINEAR_PCM 16 kHz, en-US, automatic punctuation; audio sent in 80 ms chunks without pacing
            (same config as the voice agent / model-latency/bench_nemo_asr.py).
  gpt4o_transcribe  Hub openai/openai/gpt-4o-mini-transcribe, audio.transcriptions.create, 16 kHz WAV.

Resumable: results/asr.json is reused, only missing (arm, id, rep, asr) entries are run.

    .venv/bin/python asr.py
"""

from __future__ import annotations

import io
import json
import time
import wave

import common as C
from testset import rows

SERVER = "localhost:50051"
HUB_ASR = "openai/openai/gpt-4o-mini-transcribe"
OUT = C.RES / "asr.json"


def main() -> None:
    import bench_common as bc
    import grpc
    import riva.client

    grpc.channel_ready_future(grpc.insecure_channel(SERVER)).result(timeout=3)
    service = riva.client.ASRService(riva.client.Auth(None, False, SERVER))
    cfg = riva.client.StreamingRecognitionConfig(
        config=riva.client.RecognitionConfig(encoding=riva.client.AudioEncoding.LINEAR_PCM, language_code="en-US",
                                             model="", max_alternatives=1, enable_automatic_punctuation=True,
                                             verbatim_transcripts=False, sample_rate_hertz=16000,
                                             audio_channel_count=1),
        interim_results=False)
    hub = bc.hub_client(timeout=60)

    def nemotron(pcm16k) -> str:
        chunk = 1280
        gen = (pcm16k[i:i + chunk].tobytes() for i in range(0, len(pcm16k), chunk))
        finals = []
        for resp in service.streaming_response_generator(audio_chunks=gen, streaming_config=cfg):
            for r in resp.results:
                if r.is_final and r.alternatives and r.alternatives[0].transcript.strip():
                    finals.append(r.alternatives[0].transcript.strip())
        return " ".join(finals)

    def gpt_transcribe(pcm16k) -> str:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm16k.tobytes())
        r = hub.audio.transcriptions.create(model=HUB_ASR, file=("clip.wav", buf.getvalue(), "audio/wav"),
                                            language="en")
        return r.text.strip()

    data = json.loads(OUT.read_text()) if OUT.exists() else {"meta": {}, "rows": []}
    done = {(r["arm"], r["id"], r["rep"], r["asr"]) for r in data["rows"] if r.get("ok")}
    data["rows"] = [r for r in data["rows"] if r.get("ok")]
    data["meta"] = {"started_utc": C.now_utc(), "local_server": SERVER, "hub_asr": HUB_ASR, "input": "16 kHz mono"}
    fns = {"nemotron": nemotron, "gpt4o_transcribe": gpt_transcribe}
    for rep in range(C.REPEATS):
        for t in rows():
            for arm in C.ARMS:
                p = C.wav_path(arm, t["id"], rep)
                if not p.exists():
                    continue
                x, sr = C.read_wav(p)
                y = C.to_16k(x, sr)
                for asr, fn in fns.items():
                    if (arm, t["id"], rep, asr) in done:
                        continue
                    row = {"arm": arm, "id": t["id"], "rep": rep, "asr": asr, "ok": False}
                    for attempt in range(3):
                        try:
                            row.update(hyp=fn(y), ok=True); break
                        except Exception as exc:  # noqa: BLE001
                            row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"; time.sleep(2)
                    data["rows"].append(row); print(json.dumps(row), flush=True)
                    if asr == "gpt4o_transcribe":
                        time.sleep(0.2)
        OUT.write_text(json.dumps(data, indent=1))
    data["meta"]["ended_utc"] = C.now_utc()
    OUT.write_text(json.dumps(data, indent=1))


if __name__ == "__main__":
    main()
