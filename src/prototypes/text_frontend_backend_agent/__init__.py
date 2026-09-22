# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generic text-only Frontend/Backend Agent prototype.

A domain-agnostic, text-in/text-out reimplementation of the voice
Frontend/Backend Agent example, built so a scaffold evaluation harness (tau2-bench)
can drive it: explicit session state, structured tool calls, and exactly one
user-visible payload per step.
"""

from prototypes.text_frontend_backend_agent.agent import FrontendBackendAgent, assemble_agent, build_agent
from prototypes.text_frontend_backend_agent.config import Config, load_config
from prototypes.text_frontend_backend_agent.errors import (
    ConfigError,
    FrontendBackendAgentError,
    FrontendContractError,
    StateReplayError,
    ToolProtocolError,
    ToolResultSerializationError,
)
from prototypes.text_frontend_backend_agent.events import CollectingSink, EventSink, JsonlSink, LoggingSink, NullSink
from prototypes.text_frontend_backend_agent.messages import AgentTurn, Message, ToolCall, ToolResult, UsageTotals
from prototypes.text_frontend_backend_agent.session import PendingTurn, SessionState
from prototypes.text_frontend_backend_agent.tools import ToolSpec

__all__ = [
    "AgentTurn",
    "CollectingSink",
    "Config",
    "ConfigError",
    "EventSink",
    "FrontendBackendAgent",
    "FrontendBackendAgentError",
    "FrontendContractError",
    "JsonlSink",
    "LoggingSink",
    "Message",
    "NullSink",
    "PendingTurn",
    "SessionState",
    "StateReplayError",
    "ToolCall",
    "ToolProtocolError",
    "ToolResult",
    "ToolResultSerializationError",
    "ToolSpec",
    "UsageTotals",
    "assemble_agent",
    "build_agent",
    "load_config",
]
