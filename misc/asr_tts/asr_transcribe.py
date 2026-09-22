#!/usr/bin/env python
"""Transcribe a WAV with NVIDIA streaming ASR (Nemotron / Parakeet).

Standalone equivalent of the ASR half of src/examples/generic/pipeline.py.
Defaults come from src/examples/generic/services.cloud.yaml. Audio is fed in
small chunks so you can watch interim results arrive, the same way the live
pipeline consumes the microphone.

    ../../.venv/bin/python asr_transcribe.py --audio out.wav
    ../../.venv/bin/python asr_transcribe.py --audio out.wav --catalog-key parakeet-rnnt
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

from common import (  # noqa: I001 - common emits a friendly error for a wrong interpreter
    grpc,
    riva,
)
from common import describe, explain_grpc_error, load_catalog_entry, load_env, make_auth, nvidia_api_key


SCRIPT_DIR = Path(__file__).resolve().parent


def resolve_audio(arg: Path | None) -> Path:
    """Find the audio file, falling back to the copy next to this script.

    Lets `--audio sample.wav` work from any directory, and makes --audio
    optional so the bundled sample is the zero-argument default.
    """
    if arg is None:
        return SCRIPT_DIR / "sample.wav"
    if arg.exists():
        return arg
    beside = SCRIPT_DIR / arg.name
    if beside.exists():
        print(f"note: {arg} not found, using {beside}")
        return beside
    raise SystemExit(f"Audio file not found: {arg}")


def wav_chunks(path: Path, chunk_ms: int):
    """Yield raw PCM frames, and report the file's format."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise SystemExit(f"{path}: need mono 16-bit PCM, got {wav.getnchannels()}ch/{wav.getsampwidth() * 8}-bit")
        rate = wav.getframerate()
        frames_per_chunk = int(rate * chunk_ms / 1000)
        data = []
        while True:
            frame = wav.readframes(frames_per_chunk)
            if not frame:
                break
            data.append(frame)
    return rate, data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--audio",
        type=Path,
        default=None,
        help="mono 16-bit PCM WAV (default: the bundled sample.wav). A bare "
        "filename is also looked up next to this script.",
    )
    p.add_argument("--catalog-key", default="nemotron-asr-streaming-english", help="entry under asr: in services.cloud.yaml")
    p.add_argument("--catalog", type=Path, default=None, help="override the services.cloud.yaml path")
    p.add_argument("--language", default="en-US")
    p.add_argument("--chunk-ms", type=int, default=100)
    p.add_argument("--server", default="", help="override the catalog server, e.g. localhost:50052")
    p.add_argument("--no-interim", action="store_true", help="only print final transcripts")
    args = p.parse_args()

    load_env()
    audio = resolve_audio(args.audio)
    entry = load_catalog_entry("asr", args.catalog_key, args.catalog)

    server = args.server or entry["server"]
    function_id = "" if args.server else entry.get("function_id", "")
    api_key = nvidia_api_key()

    rate, chunks = wav_chunks(audio, args.chunk_ms)
    print(f"ASR: {describe(server, function_id, api_key)}")
    print(f"     audio={audio} rate={rate} chunks={len(chunks)}")
    print(f"     model={entry.get('model', '(default)')}")

    auth = make_auth(server, function_id, api_key)
    service = riva.client.ASRService(auth)

    # Same shape as pipecat's _create_recognition_config in
    # pipecat/services/nvidia/stt.py.
    config = riva.client.StreamingRecognitionConfig(
        config=riva.client.RecognitionConfig(
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            language_code=args.language,
            model="",
            max_alternatives=1,
            enable_automatic_punctuation=True,
            sample_rate_hertz=rate,
            audio_channel_count=1,
        ),
        interim_results=not args.no_interim,
    )
    # stop_history=400 is what src/examples/generic/pipeline.py passes.
    riva.client.add_endpoint_parameters_to_config(config, -1, -1.0, 400, -1.0, -1, -1.0)

    finals: list[str] = []
    try:
        for response in service.streaming_response_generator(audio_chunks=chunks, streaming_config=config):
            for result in response.results:
                if not result.alternatives:
                    continue
                text = result.alternatives[0].transcript.strip()
                if not text:
                    continue
                if result.is_final:
                    finals.append(text)
                    print(f"\nFINAL  : {text}")
                else:
                    print(f"interim: {text[:100]}", end="\r")
    except grpc.RpcError as err:
        print(f"\n{explain_grpc_error(err)}")
        return 1

    print("\n--- transcript ---")
    print(" ".join(finals) if finals else "(nothing recognized)")
    return 0 if finals else 1


if __name__ == "__main__":
    raise SystemExit(main())
