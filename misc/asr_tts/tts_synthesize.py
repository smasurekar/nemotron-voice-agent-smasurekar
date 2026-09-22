#!/usr/bin/env python
"""Synthesize speech with NVIDIA TTS (Magpie / Chatterbox) and write a WAV.

Standalone equivalent of the TTS half of src/examples/generic/pipeline.py.
Defaults come from src/examples/generic/services.cloud.yaml.

    ../../.venv/bin/python tts_synthesize.py --text "Hello from Magpie."
    ../../.venv/bin/python tts_synthesize.py --voice-catalog chatterbox-multilingual-tts
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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--text", default="Hello. This is Magpie text to speech running on NVIDIA Cloud Functions.")
    p.add_argument("--catalog-key", default="magpie-multilingual-tts", help="entry under tts: in services.cloud.yaml")
    p.add_argument("--catalog", type=Path, default=None, help="override the services.cloud.yaml path")
    p.add_argument("--out", type=Path, default=Path("out.wav"))
    p.add_argument("--language", default="en-US")
    p.add_argument("--sample-rate", type=int, default=22050)
    p.add_argument("--server", default="", help="override the catalog server, e.g. localhost:50051")
    p.add_argument("--voice", default="", help="override the catalog voice_id")
    args = p.parse_args()

    load_env()
    entry = load_catalog_entry("tts", args.catalog_key, args.catalog)

    server = args.server or entry["server"]
    voice = args.voice or entry.get("voice_id", "")
    function_id = "" if args.server else entry.get("function_id", "")
    api_key = nvidia_api_key()

    print(f"TTS: {describe(server, function_id, api_key)}")
    print(f"     model={entry.get('model', '(default)')} voice={voice} rate={args.sample_rate}")

    auth = make_auth(server, function_id, api_key)
    service = riva.client.SpeechSynthesisService(auth)

    # synthesize_online streams audio chunks back as they are generated, which
    # is what the pipeline uses to start speaking before the full reply exists.
    responses = service.synthesize_online(
        args.text,
        voice_name=voice,
        language_code=args.language,
        encoding=riva.client.AudioEncoding.LINEAR_PCM,
        sample_rate_hz=args.sample_rate,
    )

    chunks: list[bytes] = []
    try:
        for resp in responses:
            if resp.audio:
                chunks.append(resp.audio)
                print(f"  chunk {len(chunks):>3}: {len(resp.audio):>7} bytes", end="\r")
    except grpc.RpcError as err:
        print(f"\n{explain_grpc_error(err)}")
        return 1

    if not chunks:
        print("\nNo audio returned.")
        return 1

    audio = b"".join(chunks)
    with wave.open(str(args.out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # LINEAR_PCM = 16-bit
        wav.setframerate(args.sample_rate)
        wav.writeframes(audio)

    secs = len(audio) / (args.sample_rate * 2)
    print(f"\nWrote {args.out} — {len(audio)} bytes, {secs:.2f}s, {len(chunks)} chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
