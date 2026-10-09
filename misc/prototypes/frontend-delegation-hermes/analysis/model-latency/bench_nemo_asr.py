"""Voice Agent ASR: nemotron-speech-streaming-en-0.6b on the local NeMo Speech container
(nvcr.io/nvidia/nemo-speech.cpp, gRPC Riva API, host port 50051; the agent reaches it as nemo-speech:50051).

Mirrors the agent's client (voice_frontend_backend_agent/speech/asr_riva.py): one streaming_response_generator
per utterance, LINEAR_PCM 16 kHz mono, en-US, automatic punctuation, interim results. Audio: the simulated
user's own utterances from one eval conversation (tau2 both.wav, user channel, 8 kHz telephone audio upsampled
to 16 kHz, segments from user_labels.txt).

Two measurements per utterance:
* paced: audio is sent in 80 ms chunks at real time; we time last-chunk-sent -> final transcript
  (the eval's user_stop_to_asr_final_ms analogue, minus VAD);
* burst: all audio at once; processing time / audio duration = RTF.

Does NOT start any container. If the service is not reachable the script says so and exits.
    .venv/bin/python bench_nemo_asr.py -n 6
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np

import bench_common as bc

SERVER = "localhost:50051"
SIM = next((bc.DUMP / "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_rerun43/tau2").glob(
    "*/artifacts/task_100/sim_*"))
CHUNK_MS = 80


def user_segments(n: int) -> list[tuple[str, np.ndarray]]:
    w = wave.open(str(SIM / "audio" / "both.wav"))
    rate, ch = w.getframerate(), w.getnchannels()
    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, ch)
    labels = [line.split("\t") for line in (SIM / "audio" / "user_labels.txt").read_text().splitlines() if line.strip()]
    # pick the user channel: the one with more energy inside the user's labelled segments
    s0, e0 = float(labels[0][0]), float(labels[0][1])
    seg = pcm[int(s0 * rate): int(e0 * rate)].astype(np.float64)
    user_ch = int(np.argmax((seg ** 2).mean(axis=0)))
    out = []
    for start, end, text in labels:
        s, e = float(start), float(end)
        if e - s < 1.5:
            continue
        x = pcm[int(s * rate): int((e + 0.3) * rate), user_ch].astype(np.float32)
        t_in = np.arange(len(x)) / rate
        t_out = np.arange(int(len(x) * 16000 / rate)) / 16000
        y = np.interp(t_out, t_in, x).astype(np.int16)
        out.append((text, y))
        if len(out) >= n:
            break
    return out


def run_stream(service, cfg, audio: np.ndarray, paced: bool) -> dict:
    import riva.client  # noqa: F401

    chunk = int(16000 * CHUNK_MS / 1000)
    q: queue.Queue = queue.Queue()
    marks: dict[str, float] = {}

    def feeder():
        for i in range(0, len(audio), chunk):
            q.put(audio[i:i + chunk].tobytes())
            if paced:
                time.sleep(CHUNK_MS / 1000)
        marks["last_sent"] = time.perf_counter()
        q.put(None)

    def chunks():
        while True:
            item = q.get()
            if item is None:
                return
            yield item

    t0 = time.perf_counter()
    threading.Thread(target=feeder, daemon=True).start()
    finals, first_partial = [], None
    for resp in service.streaming_response_generator(audio_chunks=chunks(), streaming_config=cfg):
        for r in resp.results:
            if not r.alternatives:
                continue
            if first_partial is None and r.alternatives[0].transcript.strip():
                first_partial = time.perf_counter()
            if r.is_final and r.alternatives[0].transcript.strip():
                finals.append(r.alternatives[0].transcript.strip())
    t_end = time.perf_counter()
    dur = len(audio) / 16000
    return {"audio_s": round(dur, 2), "total_s": round(t_end - t0, 3),
            "final_after_last_chunk_ms": round((t_end - marks["last_sent"]) * 1000, 1),
            "first_partial_s": round(first_partial - t0, 3) if first_partial else None,
            "rtf": round((t_end - t0) / dur, 4) if not paced else None, "transcript": " ".join(finals)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=6)
    args = ap.parse_args()
    import grpc
    import riva.client

    try:
        grpc.channel_ready_future(grpc.insecure_channel(SERVER)).result(timeout=3)
    except Exception:  # noqa: BLE001
        raise SystemExit(f"NeMo Speech not reachable at {SERVER}; not running (no container is started).")
    service = riva.client.ASRService(riva.client.Auth(None, False, SERVER))
    cfg = riva.client.StreamingRecognitionConfig(
        config=riva.client.RecognitionConfig(encoding=riva.client.AudioEncoding.LINEAR_PCM, language_code="en-US",
                                             model="", max_alternatives=1, enable_automatic_punctuation=True,
                                             verbatim_transcripts=False, sample_rate_hertz=16000,
                                             audio_channel_count=1),
        interim_results=True)
    run_stream(service, cfg, np.zeros(8000, dtype=np.int16), paced=False)  # warm-up, like the agent
    started = bc.now_utc()
    rows = []
    for i, (label, audio) in enumerate(user_segments(args.n)):
        for mode in ("paced", "burst"):
            row = {"ok": True, "i": i, "mode": mode, "label": label[:80], **run_stream(service, cfg, audio, mode == "paced")}
            rows.append(row)
            print(json.dumps(row))
    paced = [r for r in rows if r["mode"] == "paced"]
    burst = [r for r in rows if r["mode"] == "burst"]
    bc.write_result("nemo_asr_nemotron_speech_streaming", {
        "model": "nemotron-speech-streaming-en-0.6b", "server": SERVER, "started_utc": started,
        "ended_utc": bc.now_utc(), "concurrency": 1, "chunk_ms": CHUNK_MS, "audio_source": str(SIM / "audio"),
        "summary_paced": bc.summarize(paced, ["final_after_last_chunk_ms", "first_partial_s", "audio_s"]),
        "summary_burst": bc.summarize(burst, ["rtf", "total_s", "audio_s"])}, rows, ["audio_s"])


if __name__ == "__main__":
    main()
