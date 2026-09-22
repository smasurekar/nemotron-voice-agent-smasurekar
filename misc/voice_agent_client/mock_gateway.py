#!/usr/bin/env python3
"""A fake `WS /v1/realtime` endpoint for exercising the client offline.

This is a wire-shape stand-in, not a pipeline: it emits the event sequence the
real gateway emits, with canned answers. It exists so the client can be tested
without ASR, an LLM, TTS, or a GPU - which also makes it the fastest way to see
what a case file does before pointing it at a real deployment.

    ../../.venv/bin/python mock_gateway.py --port 7870
    ../../.venv/bin/python run_client.py --base-url http://127.0.0.1:7870 \
        --cases cases.text.jsonl

It does not validate the way the real gateway validates. A case that passes
here still has to be run against a real deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import struct
import uuid

import websockets

MODEL = "nvidia/nemotron-realtime"
RATE = 24_000
TRANSCRIPT = "The birch canoe slid on the smooth planks."
ANSWER = "This is a mock reply from the fake Realtime gateway."


def event(kind: str, **payload) -> str:
    return json.dumps({"event_id": f"event_{uuid.uuid4().hex[:16]}", "type": kind, **payload})


def base_session(session_id: str) -> dict:
    return {
        "id": session_id,
        "object": "realtime.session",
        "type": "realtime",
        "model": MODEL,
        "output_modalities": ["audio"],
        "instructions": "",
        "max_output_tokens": "inf",
        "tools": [],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": RATE},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                    "create_response": True,
                    "interrupt_response": True,
                    "idle_timeout_ms": None,
                },
                "transcription": {"model": "mock-asr"},
            },
            "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": "Magpie-Multilingual.EN-US.Sofia"},
        },
    }


def tone(seconds: float = 0.6) -> bytes:
    """A short sine so audio cases produce a playable, non-empty WAV."""
    frames = int(RATE * seconds)
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / RATE))) for n in range(frames)
    )


class Connection:
    """One mock session. Mirrors the real gateway's per-connection state."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.session = base_session(f"sess_{uuid.uuid4().hex[:16]}")
        self.manual = False
        self.pending_audio = 0
        self.speaking = False
        self.silence_frames = 0
        self.turn_open = False
        self.tool_called = False
        self.answered_tool = False

    async def send(self, kind: str, **payload) -> None:
        await self.ws.send(event(kind, **payload))

    async def run(self) -> None:
        await self.send("session.created", session=self.session)
        await self.send("conversation.created", conversation={"id": f"conv_{uuid.uuid4().hex[:16]}", "object": "realtime.conversation"})
        async for raw in self.ws:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await self.error("invalid_request_error", "Event is not valid JSON")
                continue
            await self.dispatch(message)

    async def error(self, kind: str, message: str, code: str = "invalid_value") -> None:
        await self.send("error", error={"type": kind, "code": code, "message": message})

    async def dispatch(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "session.update":
            await self.on_session_update(message.get("session") or {})
        elif kind == "input_audio_buffer.append":
            await self.on_append(message.get("audio") or "")
        elif kind == "input_audio_buffer.commit":
            await self.on_commit()
        elif kind == "input_audio_buffer.clear":
            self.pending_audio = 0
            await self.send("input_audio_buffer.cleared")
        elif kind == "conversation.item.create":
            await self.on_item_create(message.get("item") or {})
        elif kind == "response.create":
            await self.respond(message.get("response") or {})
        elif kind == "response.cancel":
            await self.send("response.done", response={"id": "resp_cancelled", "status": "cancelled", "output": []})
        else:
            await self.error("invalid_request_error", f"Unsupported event type {kind!r}", code="unsupported_capability")

    async def on_session_update(self, patch: dict) -> None:
        if patch.get("model") not in (None, MODEL):
            await self.error("invalid_request_error", "session.model must repeat the selected profile")
            return
        audio = patch.get("audio") or {}
        turn_detection = (audio.get("input") or {}).get("turn_detection", ...)
        if turn_detection is None:
            self.manual = True
        for key in ("instructions", "output_modalities", "max_output_tokens", "tools", "tool_choice", "parallel_tool_calls"):
            if key in patch:
                self.session[key] = patch[key]
        if audio:
            for side in ("input", "output"):
                if side in audio:
                    self.session["audio"][side].update({k: v for k, v in audio[side].items() if v is not ...})
        await self.send("session.updated", session=self.session)

    async def on_append(self, chunk_b64: str) -> None:
        chunk = base64.b64decode(chunk_b64) if chunk_b64 else b""
        self.pending_audio += len(chunk)
        if self.manual:
            return
        loud = max((abs(v) for v in struct.unpack(f"<{len(chunk) // 2}h", chunk[: len(chunk) // 2 * 2])), default=0) > 500
        if loud:
            self.silence_frames = 0
            if not self.speaking:
                self.speaking = True
                await self.send("input_audio_buffer.speech_started", audio_start_ms=0, item_id=f"item_{uuid.uuid4().hex[:12]}")
        elif self.speaking:
            self.silence_frames += 1
            # ~500 ms of 20 ms frames, matching the advertised silence_duration_ms.
            if self.silence_frames >= 25:
                self.speaking = False
                await self.send("input_audio_buffer.speech_stopped", audio_end_ms=0)
                await self.commit_turn(create_response=True)

    async def on_commit(self) -> None:
        if not self.pending_audio:
            await self.error("invalid_request_error", "input_audio_buffer_commit_empty", code="input_audio_buffer_commit_empty")
            return
        await self.commit_turn(create_response=False)

    async def commit_turn(self, *, create_response: bool) -> None:
        item_id = f"item_{uuid.uuid4().hex[:12]}"
        self.pending_audio = 0
        await self.send("input_audio_buffer.committed", item_id=item_id)
        item = {"id": item_id, "type": "message", "role": "user", "status": "completed", "content": [{"type": "input_audio", "transcript": None}]}
        await self.send("conversation.item.added", item=item)
        await self.send("conversation.item.done", item=item)
        await self.send("conversation.item.input_audio_transcription.completed", item_id=item_id, content_index=0, transcript=TRANSCRIPT)
        self.turn_open = True
        if create_response:
            await self.respond({})

    async def on_item_create(self, item: dict) -> None:
        item_id = item.get("id") or f"item_{uuid.uuid4().hex[:12]}"
        stored = {**item, "id": item_id, "status": "completed"}
        if item.get("type") == "function_call_output":
            self.answered_tool = True
        await self.send("conversation.item.added", item=stored)
        await self.send("conversation.item.done", item=stored)

    async def respond(self, override: dict) -> None:
        response_id = f"resp_{uuid.uuid4().hex[:16]}"
        modality = (override.get("output_modalities") or self.session.get("output_modalities") or ["audio"])[0]
        tools = override.get("tools", self.session.get("tools") or [])
        choice = override.get("tool_choice", self.session.get("tool_choice"))
        await self.send("response.created", response={"id": response_id, "status": "in_progress", "output": []})

        if tools and choice != "none" and not self.tool_called:
            await self.emit_tool_call(response_id, tools[0])
            return

        item_id = f"item_{uuid.uuid4().hex[:12]}"
        await self.send("response.output_item.added", response_id=response_id, output_index=0, item={"id": item_id, "type": "message", "role": "assistant", "status": "in_progress"})
        text = ANSWER if not self.answered_tool else "Per the tool result, it is sunny and 21 degrees."
        if modality == "text":
            for word in text.split():
                await self.send("response.output_text.delta", response_id=response_id, item_id=item_id, output_index=0, content_index=0, delta=word + " ")
                await asyncio.sleep(0.02)
            await self.send("response.output_text.done", response_id=response_id, item_id=item_id, output_index=0, content_index=0, text=text)
        else:
            await self.send("response.output_audio_transcript.delta", response_id=response_id, item_id=item_id, output_index=0, content_index=0, delta=text)
            audio = tone()
            frame = RATE // 50 * 2
            for offset in range(0, len(audio), frame):
                await self.send("response.output_audio.delta", response_id=response_id, item_id=item_id, output_index=0, content_index=0, delta=base64.b64encode(audio[offset : offset + frame]).decode())
                await asyncio.sleep(0.005)
            await self.send("response.output_audio.done", response_id=response_id, item_id=item_id, output_index=0, content_index=0)
        await self.send("response.output_item.done", response_id=response_id, output_index=0, item={"id": item_id, "type": "message", "role": "assistant", "status": "completed"})
        await self.send(
            "response.done",
            response={
                "id": response_id,
                "status": "completed",
                "output": [{"id": item_id, "type": "message", "role": "assistant"}],
                "usage": {"total_tokens": 42, "input_tokens": 20, "output_tokens": 22},
            },
        )

    async def emit_tool_call(self, response_id: str, tool: dict) -> None:
        self.tool_called = True
        item_id = f"item_{uuid.uuid4().hex[:12]}"
        call = {
            "id": item_id,
            "type": "function_call",
            "status": "completed",
            "name": tool.get("name", "unknown_tool"),
            "call_id": f"call_{uuid.uuid4().hex[:16]}",
            "arguments": json.dumps({"city": "San Jose", "order_id": "A-1729"}),
        }
        await self.send("response.output_item.added", response_id=response_id, output_index=0, item={**call, "status": "in_progress"})
        await self.send("response.output_item.done", response_id=response_id, output_index=0, item=call)
        await self.send(
            "response.done",
            response={"id": response_id, "status": "completed", "output": [call], "usage": {"total_tokens": 30}},
        )


async def serve(host: str, port: int) -> None:
    async def handler(ws) -> None:
        try:
            await Connection(ws).run()
        except websockets.ConnectionClosed:
            pass

    async with websockets.serve(handler, host, port, max_size=None):
        print(f"mock realtime gateway on ws://{host}:{port}/v1/realtime (model {MODEL})", flush=True)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7870)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
