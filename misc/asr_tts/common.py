"""Shared helpers for the standalone ASR/TTS scripts.

Mirrors how the repo authenticates against NVIDIA speech endpoints:

  src/examples/generic/pipeline.py   -> builds NvidiaSTTService / NvidiaTTSService
  src/utils.py::nvidia_api_key       -> reads NVIDIA_API_KEY
  src/utils.py::is_nvcf              -> "nvcf.nvidia.com" in server => use SSL
  pipecat/services/nvidia/{stt,tts}.py -> riva.client.Auth(None, use_ssl, server, metadata)

The gRPC metadata is the whole auth story for NVCF:

    metadata = [["function-id", <uuid>], ["authorization", f"Bearer {api_key}"]]

Run with the repo venv so `riva.client` is importable:

    ../../.venv/bin/python tts_synthesize.py --text "hello"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import grpc
    import riva.client
    import yaml
    from dotenv import load_dotenv
except ModuleNotFoundError as exc:  # wrong interpreter is the usual cause
    _root = Path(__file__).resolve().parents[2]
    _venv = _root / ".venv" / "bin" / "python"
    try:
        _script = Path(sys.argv[0]).resolve().relative_to(_root)
    except ValueError:
        _script = Path(sys.argv[0]).resolve()
    _args = " ".join(sys.argv[1:])
    sys.exit(
        f"Missing module '{exc.name}'.\n\n"
        "These scripts need the repo's virtualenv, which has riva-client, grpc,\n"
        "PyYAML and python-dotenv installed. Re-run with:\n\n"
        f"    {_venv} {_script} {_args}\n\n"
        "or activate it first:  source .venv/bin/activate\n"
        "If .venv does not exist, create it from the repo root: uv sync --group dev"
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = REPO_ROOT / "src" / "examples" / "generic" / "services.cloud.yaml"


def load_env() -> None:
    """Load the repo-root .env, exactly like Docker Compose does for the app."""
    load_dotenv(REPO_ROOT / ".env")


def nvidia_api_key(default: str = "not-needed") -> str:
    """Return NVIDIA_API_KEY.

    Copy of src/utils.py::nvidia_api_key. The default lets you point these
    scripts at an unauthenticated local NIM / NeMo-Speech.cpp sidecar.
    """
    return os.getenv("NVIDIA_API_KEY") or default


def is_nvcf(server: str) -> bool:
    """Copy of src/utils.py::is_nvcf — NVCF endpoints require TLS."""
    return "nvcf.nvidia.com" in server


def load_catalog_entry(category: str, key: str, catalog: Path | None = None) -> dict:
    """Read one entry out of a services.cloud.yaml catalog.

    category: "asr" or "tts"; key: e.g. "magpie-multilingual-tts".
    """
    path = catalog or DEFAULT_CATALOG
    data = yaml.safe_load(path.read_text()) or {}
    section = data.get(category) or {}
    if key not in section:
        available = ", ".join(section) or "(none)"
        sys.exit(f"'{key}' not found in {category}: of {path}\nAvailable: {available}")
    entry = dict(section[key])
    entry.setdefault("server", "grpc.nvcf.nvidia.com:443")
    return entry


def make_auth(server: str, function_id: str, api_key: str) -> riva.client.Auth:
    """Build the Riva Auth the way pipecat's NVIDIA services do.

    See pipecat/services/nvidia/tts.py::_initialize_client (and the STT twin):
    the function-id selects the NVCF function, the bearer token authenticates it.
    Self-hosted sidecars have no function_id and usually no key, so both
    metadata entries are omitted when empty.
    """
    metadata: list[list[str]] = []
    if function_id:
        metadata.append(["function-id", function_id])
    if api_key and api_key != "not-needed":
        metadata.append(["authorization", f"Bearer {api_key}"])
    return riva.client.Auth(None, is_nvcf(server), server, metadata)


def explain_grpc_error(err: "grpc.RpcError") -> str:
    """Turn a raw gRPC failure into the likely cause, in repo terms."""
    code = err.code() if hasattr(err, "code") else None
    name = getattr(code, "name", str(code))
    hints = {
        "PERMISSION_DENIED": (
            "The bearer token was rejected. NVIDIA_API_KEY is set but not valid "
            "for this function. NVCF speech wants an `nvapi-...` key from "
            "build.nvidia.com; an Inference Hub `sk-...` key authenticates the "
            "LLM, not these gRPC speech endpoints. Note that UNSETTING the key "
            "sends no auth header at all, which these functions currently accept."
        ),
        "UNAUTHENTICATED": "The endpoint requires a key. Set NVIDIA_API_KEY in the repo-root .env.",
        "NOT_FOUND": "The function-id does not match a deployed NVCF function. Check the catalog entry.",
        "UNAVAILABLE": "Could not reach the server. Check the address, TLS, and network egress.",
        "INVALID_ARGUMENT": "The server rejected the request config (sample rate, language, or voice).",
    }
    return f"gRPC {name}: {hints.get(name, err.details() if hasattr(err, 'details') else str(err))}"


def describe(server: str, function_id: str, api_key: str) -> str:
    """One-line summary of what we are about to call, without leaking the key."""
    shown = f"{api_key[:7]}…{api_key[-4:]}" if len(api_key) > 12 else api_key
    return (
        f"server={server} ssl={is_nvcf(server)} "
        f"function_id={function_id or '(none)'} api_key={shown}"
    )
