"""Per-endpoint deviations from the OpenAI Realtime GA schema.

A profile is *data*, not a subclass: every field below is something one
endpoint does differently from the published schema. Supporting a new endpoint
should be a new entry here rather than a code change.

This mirrors `voice-agent-evaluation`'s
`src/voice_agent_eval/products/openai_realtime/profiles.py` so a case that runs
here runs there too. Like that module, this one is **stdlib only** — keep it
importable without installing anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class EndpointProfile:
    """How one endpoint's Realtime implementation differs from the GA schema."""

    name: str

    # -- connection ---------------------------------------------------------
    #: Authorization header scheme. OpenAI and this gateway use "Bearer";
    #: Baseten rejects it and requires "Api-Key".
    auth_scheme: str = "Bearer"
    #: Appended to the base URL path when Realtime lives on a sub-path.
    ws_path_suffix: str = "/v1/realtime"
    #: Send the selected model as a `?model=` query parameter. The gateway
    #: picks its server-owned pipeline profile from this.
    include_model_query_param: bool = True
    #: Handshake budget. An endpoint that scales to zero needs minutes.
    open_timeout_s: float = 60.0
    #: Some endpoints never answer WebSocket pings, and the library keepalive
    #: then kills an idle session with "1011 keepalive ping timeout".
    answers_websocket_pings: bool = True

    # -- audio --------------------------------------------------------------
    #: Wire rate used when the endpoint does not advertise one. The gateway
    #: defaults to PCM16 at 24 kHz and also accepts 8 and 16 kHz.
    input_sample_rate_hz: int = 24_000
    output_sample_rate_hz: int = 24_000

    # -- session ------------------------------------------------------------
    #: Model id to echo in session.update. The gateway selects the profile from
    #: the connect URL and then rejects an empty `session.model`, so a live
    #: update may only repeat what session.created advertised.
    model: str | None = None
    #: Ask for public input transcription events. Cascaded profiles emit
    #: `conversation.item.input_audio_transcription.*`; Omni profiles may not.
    request_input_transcription: bool = True
    #: Whether `audio.input.turn_detection: null` (manual commit) is accepted.
    #: Generic, Multilingual and Frontend/Backend cascaded profiles support it;
    #: Direct Omni and Omni Subagents do not.
    supports_manual_turns: bool = True


PROFILES: Mapping[str, EndpointProfile] = {
    # The local Nemotron Voice Agent gateway, generic cascaded pipeline with
    # trusted server tools. `src/server.py` or `src/realtime_server.py`.
    "nemotron-local": EndpointProfile(
        name="nemotron-local",
        model="nvidia/nemotron-realtime",
    ),
    # Same gateway, the profile intended for client-owned functions. Use this
    # one for the tool cases: its prompt has no trusted server tools, so the
    # model reaches for what the client declared.
    "nemotron-local-client-tools": EndpointProfile(
        name="nemotron-local-client-tools",
        model="nvidia/nemotron-realtime-client-tools",
    ),
    # Direct Omni (speech-to-speech). No cascaded ASR, so no manual turns and
    # no fixed transcription producer to ask for.
    "nemotron-local-omni": EndpointProfile(
        name="nemotron-local-omni",
        model="nvidia/nemotron-realtime-omni",
        request_input_transcription=False,
        supports_manual_turns=False,
    ),
    # Hosted OpenAI, for checking a case against the reference implementation.
    "openai": EndpointProfile(
        name="openai",
        model="gpt-realtime",
        supports_manual_turns=True,
    ),
    # Any other server following the GA schema. Start here for a new endpoint
    # and add fields as its quirks surface.
    "generic": EndpointProfile(name="generic"),
}


def resolve_profile(name: str) -> EndpointProfile:
    """Look up a profile by name, listing the known ones when it is missing."""
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(
            f"unknown profile {name!r}; known profiles: {', '.join(sorted(PROFILES))}"
        ) from None


def websocket_url(base_url: str, profile: EndpointProfile, model: str | None = None) -> str:
    """Derive the socket URL from an http(s) base URL.

    The base URL is written as http(s) — the evaluator pins the committed
    scheme that way — and the ws(s) scheme plus any sub-path is derived here
    rather than spelled out by the caller.
    """
    parsed = urlsplit(base_url)
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path.rstrip("/") + profile.ws_path_suffix
    query = parsed.query
    chosen = model or profile.model
    if profile.include_model_query_param and chosen and "model=" not in query:
        # The model id contains a slash; percent-encode it as the gateway
        # documentation does rather than relying on lenient query parsing.
        joined = f"model={quote(chosen, safe='')}"
        query = f"{query}&{joined}" if query else joined
    return urlunsplit((scheme, parsed.netloc, path, query, ""))
