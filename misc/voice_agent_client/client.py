"""A minimal OpenAI Realtime client for the Nemotron voice-agent gateway.

This is a testing client, not a product. It speaks the same wire contract as
`voice-agent-evaluation`'s static-lane adapter
(`src/voice_agent_eval/products/openai_realtime/client.py`) so a case that
passes here is the same exchange the evaluator will run: one isolated session
per case, a strict initial `session.update` built from what `session.created`
advertised, real-time-paced audio, and a response-idle budget re-armed by any
observable progress.

What it adds over the evaluator adapter is the parts a test client needs and a
benchmark harness does not: text turns, manual (`turn_detection: null`) turns,
client-owned function tools answered from canned outputs, and a readable event
log.

Deliberately close to the evaluator:

* one session per case, never reused - state leaks between cases otherwise
* wait for `session.updated` before the first append; streaming audio into an
  unconfigured session makes VAD collapse the whole buffer into one event
* `timeout_s` is an idle budget, not a wall clock; long answers are not failures
* credentials come from an environment variable, never from a case file

See `docs/how-to/use-realtime-gateway.md` for the endpoint contract.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import ssl
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# These scripts are a flat folder, not an installed package, so make the
# sibling modules importable no matter where the interpreter was started.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audio_io import load_wav, silence, write_wav  # noqa: E402,F401
from profiles import EndpointProfile, PROFILES, resolve_profile, websocket_url  # noqa: E402,F401

# Audio deltas arrive ~50/s and their payload is already captured in
# `output_pcm16`; logging them as events would bury everything else.
_UNLOGGED_EVENTS = frozenset({"response.output_audio.delta"})

DEFAULT_API_KEY_ENV = "REALTIME_API_KEY"


class RealtimeError(RuntimeError):
    """Base class for client failures."""


class TransportError(RealtimeError):
    """Connection, handshake, or socket failure. Retryable."""


class ProtocolError(RealtimeError):
    """The endpoint violated the Realtime contract. Not retryable."""


# --------------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class Case:
    """One turn to run against the gateway, as read from a JSONL case file."""

    case_id: str
    instructions: str = ""
    text: str = ""
    audio: str | None = None
    output_modalities: tuple[str, ...] = ("audio",)
    max_output_tokens: int | str = 256
    tools: tuple[Mapping[str, Any], ...] = ()
    tool_choice: str | Mapping[str, Any] = "auto"
    #: Canned client-tool results, keyed by exact function name. The gateway
    #: publishes a client-owned call but never executes it, so the client must
    #: return a `function_call_output` for its own `call_id`.
    tool_outputs: Mapping[str, Any] = field(default_factory=dict)
    #: "auto" uses the profile's advertised VAD; "manual" sends
    #: `turn_detection: null` and commits the buffer explicitly.
    turn_mode: str = "auto"
    voice: str | None = None
    notes: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, index: int) -> "Case":
        case_id = str(data.get("case_id") or f"case-{index:03d}")
        unknown = set(data) - {f for f in cls.__slots__}
        if unknown:
            raise ValueError(f"{case_id}: unknown case fields {sorted(unknown)}")

        text = str(data.get("text") or "")
        audio = data.get("audio")
        if not text and not audio:
            raise ValueError(f"{case_id}: a case needs either 'text' or 'audio'")
        if text and audio:
            raise ValueError(f"{case_id}: set 'text' or 'audio', not both")

        modalities = data.get("output_modalities") or ("audio",)
        if isinstance(modalities, str):
            modalities = (modalities,)
        modalities = tuple(str(value) for value in modalities)
        if modalities not in (("audio",), ("text",)):
            raise ValueError(
                f"{case_id}: output_modalities must be exactly ['audio'] or ['text']"
            )

        turn_mode = str(data.get("turn_mode") or "auto")
        if turn_mode not in {"auto", "manual"}:
            raise ValueError(f"{case_id}: turn_mode must be 'auto' or 'manual'")

        return cls(
            case_id=case_id,
            instructions=str(data.get("instructions") or ""),
            text=text,
            audio=str(audio) if audio else None,
            output_modalities=modalities,
            max_output_tokens=data.get("max_output_tokens", 256),
            tools=tuple(data.get("tools") or ()),
            tool_choice=data.get("tool_choice", "auto"),
            tool_outputs=dict(data.get("tool_outputs") or {}),
            turn_mode=turn_mode,
            voice=data.get("voice"),
            notes=str(data.get("notes") or ""),
        )


def load_cases(path: str) -> list[Case]:
    """Read a JSONL case file, reporting the offending line rather than a traceback."""
    cases: list[Case] = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON ({exc})") from None
            if not isinstance(data, dict):
                raise ValueError(f"{path}:{number}: each line must be a JSON object")
            try:
                cases.append(Case.from_mapping(data, index=len(cases) + 1))
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from None
    if not cases:
        raise ValueError(f"{path} holds no cases")
    return cases


# -------------------------------------------------------------------- outputs


@dataclass(frozen=True, slots=True)
class LoggedEvent:
    """One received server event, minus its bulk payload."""

    sequence: int
    type: str
    received_at_s: float
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A client-owned function the model asked for, and what we answered."""

    call_id: str
    name: str
    arguments: str
    output: str | None = None
    answered: bool = False


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Everything one isolated session produced. JSON-safe except the PCM."""

    case_id: str
    model: str
    session_id: str
    #: The instructions and the turn itself, so a result is readable without
    #: cross-referencing the case file that produced it.
    instructions: str
    input_text: str
    input_audio: str
    text: str
    user_transcript: str
    output_pcm16: bytes
    output_sample_rate_hz: int
    events: tuple[LoggedEvent, ...]
    timings: Mapping[str, float]
    errors: tuple[str, ...]
    tool_calls: tuple[ToolCall, ...]
    usage: Mapping[str, Any]
    completed: bool
    termination_reason: str

    @property
    def audio_duration_s(self) -> float:
        return len(self.output_pcm16) / 2 / max(self.output_sample_rate_hz, 1)

    def to_json(self) -> dict[str, Any]:
        """The artifact form: audio is written beside this, not inlined."""
        return {
            "case_id": self.case_id,
            "model": self.model,
            "session_id": self.session_id,
            "instructions": self.instructions,
            "input_text": self.input_text,
            "input_audio": self.input_audio,
            "text": self.text,
            "user_transcript": self.user_transcript,
            "output_audio_bytes": len(self.output_pcm16),
            "output_audio_duration_s": round(self.audio_duration_s, 3),
            "output_sample_rate_hz": self.output_sample_rate_hz,
            "timings": dict(self.timings),
            "errors": list(self.errors),
            "tool_calls": [
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "output": call.output,
                    "answered": call.answered,
                }
                for call in self.tool_calls
            ],
            "usage": dict(self.usage),
            "completed": self.completed,
            "termination_reason": self.termination_reason,
            "events": [
                {
                    "sequence": event.sequence,
                    "type": event.type,
                    "received_at_s": event.received_at_s,
                    "data": event.data,
                }
                for event in self.events
            ],
        }


# --------------------------------------------------------------------- client


class RealtimeClient:
    """One isolated Realtime session per case."""

    def __init__(
        self,
        *,
        base_url: str,
        profile: str | EndpointProfile = "nemotron-local",
        model: str | None = None,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        tls_verify: bool = True,
        connect_timeout_s: float | None = None,
        trailing_silence_ms: int = 1500,
        chunk_duration_ms: int = 20,
        realtime_pacing: bool = True,
        max_tool_rounds: int = 3,
        on_event: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.profile = profile if isinstance(profile, EndpointProfile) else resolve_profile(profile)
        self.model = model or self.profile.model
        self.api_key_env = api_key_env
        self.tls_verify = tls_verify
        self.connect_timeout_s = connect_timeout_s or self.profile.open_timeout_s
        self.trailing_silence_ms = trailing_silence_ms
        self.chunk_duration_ms = chunk_duration_ms
        self.realtime_pacing = realtime_pacing
        self.max_tool_rounds = max_tool_rounds
        self._on_event = on_event
        self.ws_url = websocket_url(self.base_url, self.profile, self.model)

    # ---------------------------------------------------------------- connect

    def _headers(self) -> dict[str, str]:
        """Bearer credential, when one is configured.

        The gateway runs unauthenticated only when `REALTIME_API_KEY` is unset
        server-side, which is the local-development case, so an unset variable
        here is not an error. A `ek_` client secret works in the same header.
        """
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            return {}
        return {"Authorization": f"{self.profile.auth_scheme} {key}"}

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self.ws_url.startswith("wss://"):
            return None
        context = ssl.create_default_context()
        if not self.tls_verify:
            # The server's default TLS mode is a self-signed local certificate.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _connect(self):
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RealtimeError(
                "this client needs the 'websockets' package; run it with the repo "
                "virtualenv (.venv/bin/python) or pip install -r requirements.txt"
            ) from exc
        return websockets.connect(
            self.ws_url,
            additional_headers=self._headers(),
            ssl=self._ssl_context(),
            open_timeout=self.connect_timeout_s,
            ping_interval=20 if self.profile.answers_websocket_pings else None,
            max_size=None,
        )

    # -------------------------------------------------------------- handshake

    async def _handshake(self, ws, *, timeout_s: float) -> dict[str, Any]:
        """Consume `session.created` + `conversation.created`, return the session.

        The gateway sends exactly these two and no welcome response, so anything
        else arriving here means the connection is not what we think it is.
        """
        created = await self._expect(ws, "session.created", timeout_s=timeout_s)
        await self._expect(ws, "conversation.created", timeout_s=timeout_s)
        session = created.get("session")
        if not isinstance(session, dict) or not session.get("model"):
            raise ProtocolError("session.created did not advertise a model id")
        return session

    async def _expect(self, ws, expected: str, *, timeout_s: float) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise TransportError(f"no {expected} within {timeout_s:.0f}s") from exc
        event = json.loads(raw)
        etype = event.get("type")
        if etype == "error":
            raise ProtocolError(f"endpoint returned an error instead of {expected}: {json.dumps(event)[:400]}")
        if etype != expected:
            raise ProtocolError(f"expected {expected}, got {etype}")
        return event

    def _session_patch(self, advertised: Mapping[str, Any], case: Case) -> dict[str, Any]:
        """Build a strict initial update out of what the deployment advertised.

        Echoing the advertised model, formats, voice and turn detection keeps
        the update portable across profiles: nothing is hardcoded that the
        deployment is entitled to choose.
        """
        audio = advertised.get("audio") or {}
        advertised_in = audio.get("input") or {}
        advertised_out = audio.get("output") or {}

        input_patch: dict[str, Any] = {"format": advertised_in.get("format")}
        if case.turn_mode == "manual":
            if not self.profile.supports_manual_turns:
                raise ProtocolError(
                    f"{case.case_id}: profile {self.profile.name} does not support "
                    "manual turns; Direct Omni pipelines are automatic-only"
                )
            # null selects manual mode: append buffers, commit submits one turn.
            input_patch["turn_detection"] = None
        else:
            input_patch["turn_detection"] = advertised_in.get("turn_detection")
        transcription = advertised_in.get("transcription")
        if self.profile.request_input_transcription and transcription is not None:
            input_patch["transcription"] = transcription

        output_patch: dict[str, Any] = {"format": advertised_out.get("format")}
        voice = case.voice or advertised_out.get("voice")
        if voice:
            output_patch["voice"] = voice

        session: dict[str, Any] = {
            "type": "realtime",
            # A live update may only repeat the profile chosen in the URL, and
            # an empty model is rejected outright.
            "model": advertised.get("model"),
            "output_modalities": list(case.output_modalities),
            "max_output_tokens": case.max_output_tokens,
            # An empty tool list is meaningful: it stops the deployment's own
            # trusted tools from answering a case that is meant to be tool-free.
            "tools": list(case.tools),
            "tool_choice": case.tool_choice if case.tools else "none",
            "parallel_tool_calls": True,
            "audio": {"input": input_patch, "output": output_patch},
        }
        if case.instructions:
            session["instructions"] = case.instructions
        return {"type": "session.update", "session": session}

    # -------------------------------------------------------------- preflight

    async def preflight(self) -> dict[str, Any]:
        """Open one throwaway session to read the deployment's identity.

        Run this before a batch: it turns a wrong URL, a missing credential, or
        the wrong model profile into one clear failure instead of a set of
        plausible-looking answers from the wrong deployment.
        """
        try:
            async with self._connect() as ws:
                session = await self._handshake(ws, timeout_s=self.connect_timeout_s)
        except RealtimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - the transport surface is broad
            raise TransportError(f"preflight failed for {self.ws_url}: {exc}") from exc

        audio = session.get("audio") or {}
        audio_in = audio.get("input") or {}
        audio_out = audio.get("output") or {}
        turn_detection = audio_in.get("turn_detection") or {}
        return {
            "websocket_url": self.ws_url,
            "session_id": session.get("id"),
            "model": session.get("model"),
            "authenticated": bool(self._headers()),
            "input_format": audio_in.get("format"),
            "output_format": audio_out.get("format"),
            "voice": audio_out.get("voice"),
            "turn_detection": turn_detection.get("type"),
            "transcription": (audio_in.get("transcription") or {}).get("model"),
            "session": session,
        }

    def check_model(self, preflight: Mapping[str, Any], expected: str | None) -> None:
        """Fail closed when the deployment is not the one the run names.

        The evaluator's `expected_pipeline_id` check, kept here for the same
        reason: several Nemotron-ish stacks often run on one box, and a mistyped
        port otherwise yields believable output from the wrong pipeline.
        """
        if not expected:
            return
        reported = str(preflight.get("model") or "")
        if reported != expected:
            raise ProtocolError(
                f"endpoint advertises model {reported!r}, expected {expected!r}; "
                "check the base URL, port, and --model"
            )

    # --------------------------------------------------------------- run_case

    async def run_case(self, case: Case, *, timeout_s: float = 120.0, audio_root: str = ".") -> CaseResult:
        """Run one case in its own session and return every observable result."""
        state = _SessionState(
            started=time.monotonic(),
            instructions=case.instructions,
            input_text=case.text,
        )
        wire_audio = None
        if case.audio:
            wire_audio = load_wav(_resolve_audio(case.audio, audio_root), target_rate_hz=self.profile.input_sample_rate_hz)
            state.input_audio = wire_audio.describe()

        try:
            async with self._connect() as ws:
                state.mark("connect_s")
                advertised = await self._handshake(ws, timeout_s=self.connect_timeout_s)
                state.session_id = str(advertised.get("id") or "")
                state.model = str(advertised.get("model") or "")
                state.output_rate = int(
                    ((advertised.get("audio") or {}).get("output") or {}).get("format", {}).get("rate")
                    or self.profile.output_sample_rate_hz
                )

                await ws.send(json.dumps(self._session_patch(advertised, case)))
                await self._await_session_updated(ws, state, timeout_s=self.connect_timeout_s)
                state.mark("session_ready_s")

                if wire_audio is not None:
                    await self._run_audio_turn(ws, case, wire_audio, state, timeout_s=timeout_s)
                else:
                    await self._run_text_turn(ws, case, state, timeout_s=timeout_s)

                await self._answer_tool_calls(ws, case, state, timeout_s=timeout_s)
        except RealtimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - the transport surface is broad
            raise TransportError(f"session failed for {case.case_id}: {exc}") from exc

        return state.finish(case.case_id)

    async def _await_session_updated(self, ws, state: "_SessionState", *, timeout_s: float) -> None:
        """Block until the session is configured.

        Mandatory, not an optimization: appending audio to an unconfigured
        session makes the endpoint fold the whole buffer into a single VAD
        event, and a rejected update would otherwise run the case against the
        previous configuration.
        """
        while True:
            event = await self._recv(ws, state, timeout_s=timeout_s)
            etype = event.get("type")
            if etype == "session.updated":
                return
            if etype == "error":
                raise ProtocolError(f"the endpoint rejected session.update: {json.dumps(event)[:400]}")

    # ------------------------------------------------------------- text turns

    async def _run_text_turn(self, ws, case: Case, state: "_SessionState", *, timeout_s: float) -> None:
        await ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": case.text}],
                    },
                }
            )
        )
        # Wait for the item's terminal acknowledgement before asking for a
        # response, so a rejected item surfaces as an error rather than as an
        # answer to the previous context.
        while True:
            event = await self._recv(ws, state, timeout_s=timeout_s)
            etype = str(event.get("type") or "")
            if etype == "conversation.item.done":
                break
            if etype == "error":
                state.errors.append(json.dumps(event)[:500])
                state.termination = "endpoint_error"
                return
        state.mark("input_sent_s")
        await ws.send(json.dumps({"type": "response.create"}))
        await self._collect_response(ws, state, timeout_s=timeout_s)

    # ------------------------------------------------------------ audio turns

    async def _run_audio_turn(self, ws, case: Case, wire_audio, state: "_SessionState", *, timeout_s: float) -> None:
        frame_bytes = max(2, int(wire_audio.sample_rate_hz * self.chunk_duration_ms / 1000) * 2)

        async def append_all(payload: bytes) -> None:
            for offset in range(0, len(payload), frame_bytes):
                await ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(payload[offset : offset + frame_bytes]).decode(),
                        }
                    )
                )
                if self.realtime_pacing:
                    await asyncio.sleep(self.chunk_duration_ms / 1000)

        if case.turn_mode == "manual":
            await append_all(wire_audio.pcm16)
            state.mark("input_sent_s")
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            # The gateway queues response.create until the matching ASR turn
            # reaches a terminal state, so these can go back to back.
            await ws.send(json.dumps({"type": "response.create"}))
            await self._collect_response(ws, state, timeout_s=timeout_s)
            return

        # Automatic mode: VAD owns the commit and creates the response, so the
        # sender keeps feeding silence until the response is terminal. Server
        # VAD needs continued signal to detect end-of-speech; stopping the
        # stream at the last speech sample leaves the turn open.
        tail = silence(sample_rate_hz=wire_audio.sample_rate_hz, duration_ms=self.trailing_silence_ms)
        idle_frame = base64.b64encode(b"\x00" * frame_bytes).decode()

        async def sender() -> None:
            await append_all(wire_audio.pcm16 + tail)
            state.mark("input_sent_s")
            while not state.response_done.is_set():
                await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": idle_frame}))
                await asyncio.sleep(self.chunk_duration_ms / 1000)

        task = asyncio.create_task(sender())
        try:
            await self._collect_response(ws, state, timeout_s=timeout_s)
        finally:
            state.response_done.set()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ tools

    async def _answer_tool_calls(self, ws, case: Case, state: "_SessionState", *, timeout_s: float) -> None:
        """Return canned outputs for client-owned calls and collect Response B.

        The gateway publishes a client-owned function call but never executes
        it. The client answers the exact `call_id` and asks for another
        response; Response A and Response B carry different response IDs.
        """
        for _ in range(self.max_tool_rounds):
            pending = [call for call in state.tool_calls if not call.answered]
            if not pending:
                return
            answered_any = False
            for index, call in enumerate(state.tool_calls):
                if call.answered or call.name not in case.tool_outputs:
                    continue
                output = case.tool_outputs[call.name]
                payload = output if isinstance(output, str) else json.dumps(output)
                await ws.send(
                    json.dumps(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call.call_id,
                                "output": payload,
                            },
                        }
                    )
                )
                state.tool_calls[index] = ToolCall(
                    call_id=call.call_id,
                    name=call.name,
                    arguments=call.arguments,
                    output=payload,
                    answered=True,
                )
                answered_any = True

            if not answered_any:
                unknown = ", ".join(sorted({call.name for call in pending}))
                state.errors.append(f"no tool_outputs entry for client function(s): {unknown}")
                state.termination = "unanswered_tool_call"
                return

            state.response_done = asyncio.Event()
            # `tool_choice: none` on the follow-up: a session that forced a tool
            # would otherwise force the same call again instead of answering
            # from its result.
            await ws.send(json.dumps({"type": "response.create", "response": {"tool_choice": "none"}}))
            await self._collect_response(ws, state, timeout_s=timeout_s)

    # -------------------------------------------------------- response stream

    async def _collect_response(self, ws, state: "_SessionState", *, timeout_s: float) -> None:
        """Read until `response.done`.

        `timeout_s` is an idle budget: every received event re-arms it, so a
        slow-but-progressing answer is not cut off, while a silent endpoint
        still fails within the budget.
        """
        while True:
            try:
                event = await self._recv(ws, state, timeout_s=timeout_s)
            except TransportError:
                state.errors.append(f"no endpoint progress for {timeout_s:.0f}s")
                state.termination = "response_idle_timeout"
                state.response_done.set()
                return

            etype = str(event.get("type") or "")

            if etype == "response.output_audio.delta":
                chunk = event.get("delta") or ""
                if chunk:
                    state.mark_once("first_audio_s")
                    state.audio.extend(base64.b64decode(chunk))
            elif etype == "response.output_text.delta":
                state.mark_once("first_text_s")
                state.text.append(str(event.get("delta") or ""))
            elif etype.endswith("output_audio_transcript.delta"):
                state.mark_once("first_text_s")
                state.text.append(str(event.get("delta") or ""))
            elif etype == "input_audio_buffer.committed":
                # Automatic VAD can commit mid-clip, before the sender finishes.
                # This, not input_sent_s, is when the endpoint owns the turn.
                state.mark_once("input_committed_s")
            elif etype == "conversation.item.input_audio_transcription.completed":
                state.user_transcript.append(str(event.get("transcript") or ""))
            elif etype == "conversation.item.input_audio_transcription.failed":
                state.errors.append(f"input transcription failed: {json.dumps(event)[:300]}")
            elif etype == "response.output_item.done":
                self._record_tool_call(event.get("item"), state)
            elif etype == "error":
                state.errors.append(json.dumps(event)[:500])
                # A recoverable request error keeps the socket open, but for a
                # single-turn test client there is nothing left to wait for.
                state.termination = "endpoint_error"
                state.response_done.set()
                return
            elif etype == "response.done":
                response = event.get("response") or {}
                status = str(response.get("status") or "unknown")
                state.mark("response_done_s")
                state.usage = response.get("usage") or {}
                state.completed = status == "completed"
                state.termination = f"response_{status}"
                if status != "completed":
                    detail = (response.get("status_details") or {})
                    state.errors.append(f"response {status}: {json.dumps(detail)[:300]}")
                state.response_done.set()
                return

    @staticmethod
    def _record_tool_call(item: Any, state: "_SessionState") -> None:
        if not isinstance(item, Mapping) or item.get("type") != "function_call":
            return
        call_id = str(item.get("call_id") or "")
        if not call_id or any(call.call_id == call_id for call in state.tool_calls):
            return
        state.tool_calls.append(
            ToolCall(
                call_id=call_id,
                name=str(item.get("name") or ""),
                arguments=str(item.get("arguments") or ""),
            )
        )

    async def _recv(self, ws, state: "_SessionState", *, timeout_s: float) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise TransportError(f"no event within {timeout_s:.0f}s") from exc
        event = json.loads(raw)
        state.log(event)
        if self._on_event is not None:
            self._on_event(event)
        return event


def _resolve_audio(reference: str, audio_root: str) -> str:
    """Look an audio path up next to the case file before the working directory."""
    candidate = Path(reference)
    if candidate.is_absolute() or candidate.exists():
        return str(candidate)
    beside = Path(audio_root) / candidate
    if beside.exists():
        return str(beside)
    raise FileNotFoundError(f"audio file {reference!r} not found (looked in {audio_root})")


@dataclass
class _SessionState:
    """Mutable evidence accumulated across one session's turns."""

    started: float
    session_id: str = ""
    model: str = ""
    instructions: str = ""
    input_text: str = ""
    input_audio: str = ""
    output_rate: int = 24_000
    text: list[str] = field(default_factory=list)
    user_transcript: list[str] = field(default_factory=list)
    audio: bytearray = field(default_factory=bytearray)
    events: list[LoggedEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    usage: Mapping[str, Any] = field(default_factory=dict)
    completed: bool = False
    termination: str = "response_incomplete"
    response_done: asyncio.Event = field(default_factory=asyncio.Event)

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 6)

    def mark(self, key: str) -> None:
        self.timings[key] = self.elapsed()

    def mark_once(self, key: str) -> None:
        self.timings.setdefault(key, self.elapsed())

    def log(self, event: Mapping[str, Any]) -> None:
        etype = str(event.get("type") or "")
        if etype in _UNLOGGED_EVENTS:
            return
        self.events.append(
            LoggedEvent(
                sequence=len(self.events),
                type=etype,
                received_at_s=self.elapsed(),
                data={key: value for key, value in event.items() if key not in {"delta", "audio"}},
            )
        )

    def finish(self, case_id: str) -> CaseResult:
        return CaseResult(
            case_id=case_id,
            model=self.model,
            session_id=self.session_id,
            instructions=self.instructions,
            input_text=self.input_text,
            input_audio=self.input_audio,
            text="".join(self.text).strip(),
            user_transcript="".join(self.user_transcript).strip(),
            output_pcm16=bytes(self.audio),
            output_sample_rate_hz=self.output_rate,
            events=tuple(self.events),
            timings=dict(self.timings),
            errors=tuple(self.errors),
            tool_calls=tuple(self.tool_calls),
            usage=dict(self.usage),
            completed=self.completed,
            termination_reason=self.termination,
        )
