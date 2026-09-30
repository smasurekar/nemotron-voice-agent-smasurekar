# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Backend tool calls -> Realtime function calls or local callables, and results back (plan 8.2, 8.3).

* Calls arriving within ``batch_window_ms`` form one batch.
* ``ArgumentNormalizer.screen`` runs first (when configured): invalid or already-failed
  lookups are answered locally and never reach the client.
* ``executor: wire`` emits the batch as one Realtime response of ``function_call`` items;
  ``function_call_output`` goes back to the gateway, and a later ``response.create`` is the
  batch's acknowledgement (:meth:`ToolRelay.take_ack`).
* ``executor: local`` runs the calls in-process (browser demo), with an optional per-call delay.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from prototypes.text_frontend_backend_agent.messages import ToolCall
from prototypes.text_frontend_backend_agent.tools import ToolRegistry, ToolSpec
from prototypes.voice_delegation_hermes_agent.engine.output_scheduler import CallsItem
from prototypes.voice_frontend_backend_agent.errors import WireProtocolError
from prototypes.voice_frontend_backend_agent.normalization.arguments import ArgumentNormalizer, FailureKey

SendResult = Callable[[str, int, str, bool], None]  # call_id, epoch, output, local
Log = Callable[..., None]

_BATCH_IDS = itertools.count(1)


@dataclass(slots=True)
class _Call:
    call_id: str
    epoch: int
    name: str
    arguments: str
    batch_id: str = ""
    key: FailureKey | None = None


@dataclass(slots=True)
class _Batch:
    batch_id: str
    call_ids: tuple[str, ...]
    outputs: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return all(call_id in self.outputs for call_id in self.call_ids)


class ToolRelay:
    """One session's tool round trips."""

    def __init__(
        self,
        *,
        executor: str,
        send_result: SendResult,
        emit_calls: Callable[[CallsItem], None],
        log: Log,
        normalizer: ArgumentNormalizer | None = None,
        local_tools: Sequence[ToolSpec] = (),
        batch_window_ms: int = 30,
        per_call_delay_s: float = 0.0,
    ) -> None:
        """Bind the session's sinks and settings."""
        self._executor = executor
        self._send_result = send_result
        self._emit_calls = emit_calls
        self._log = log
        self._normalizer = normalizer
        self._registry = ToolRegistry(local_tools) if executor == "local" else None
        self._window = batch_window_ms / 1000.0
        self._delay = per_call_delay_s
        self._pending: list[_Call] = []
        self._flush_handle: asyncio.TimerHandle | None = None
        self._open: dict[str, _Call] = {}
        self._cancelled: set[str] = set()
        self._batches: list[_Batch] = []
        self._failed: frozenset[FailureKey] = frozenset()
        self._invalid_counts: dict[str, int] = {}  # tool -> invalid answers so far (escalating wording)
        self._tasks: set[asyncio.Task[None]] = set()

    # -- from the gateway -------------------------------------------------------------------

    def on_call(self, data: Mapping[str, Any]) -> None:
        """A ``tool.call`` from the gateway: start or join the current batch window."""
        call = _Call(
            call_id=str(data["call_id"]),
            epoch=int(data["epoch"]),
            name=str(data["name"]),
            arguments=str(data.get("arguments") or "{}"),
        )
        self._pending.append(call)
        if self._flush_handle is None:
            self._flush_handle = asyncio.get_running_loop().call_later(self._window, self._flush)

    def on_cancel(self, call_id: str, reason: str) -> None:
        """The gateway gave up on a call (timeout, interrupt, worker died)."""
        self._pending = [call for call in self._pending if call.call_id != call_id]
        call = self._open.pop(call_id, None)
        self._cancelled.add(call_id)
        if call is not None:
            for batch in self._batches:
                if call_id in batch.call_ids and call_id not in batch.outputs:
                    batch.outputs[call_id] = ""
        self._log("tool_cancelled", call_id=call_id, reason=reason)

    # -- from the client --------------------------------------------------------------------

    def on_function_output(self, call_id: str, output: str) -> None:
        """A ``function_call_output`` item; unknown ids are a wire error, cancelled ones are dropped."""
        call = self._open.pop(call_id, None)
        if call is None:
            if call_id in self._cancelled:
                self._log("tool_result_ignored", call_id=call_id, reason="cancelled")
                return
            raise WireProtocolError(
                f"call_id {call_id!r} does not match an outstanding function call",
                code="invalid_value",
                param="item.call_id",
            )
        self._log("tool_output_in", call_id=call_id, output=output)
        if call.key is not None and self._normalizer is not None and self._normalizer.is_permanent_failure(output):
            self._failed = self._failed | {call.key}
        self._send_result(call_id, call.epoch, output, False)
        for batch in self._batches:
            if call_id in batch.call_ids:
                batch.outputs[call_id] = output

    def take_ack(self) -> bool:
        """``response.create``: consume one tool batch's acknowledgement, if a batch awaits one."""
        for index, batch in enumerate(self._batches):
            if batch.complete or batch.outputs:
                del self._batches[index]
                self._log("tool_batch_ack", batch_id=batch.batch_id, complete=batch.complete)
                return True
        if self._batches:
            batch = self._batches.pop(0)
            self._log("tool_batch_ack", batch_id=batch.batch_id, complete=False, early=True)
            return True
        return False

    @property
    def outstanding(self) -> int:
        """Calls sent to the client and not answered yet."""
        return len(self._open)

    async def close(self) -> None:
        """Stop timers and local executions."""
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # -- batching ---------------------------------------------------------------------------

    def _flush(self) -> None:
        self._flush_handle = None
        calls, self._pending = self._pending, []
        if not calls:
            return
        batch_id = f"batch_{next(_BATCH_IDS)}"
        sent = self._screen(calls)
        if not sent:
            return
        for call in sent:
            call.batch_id = batch_id
        if self._executor == "local":
            for call in sent:
                task = asyncio.create_task(self._run_local(call), name=f"local-tool-{call.call_id}")
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            return
        for call in sent:
            self._open[call.call_id] = call
        self._batches.append(_Batch(batch_id=batch_id, call_ids=tuple(call.call_id for call in sent)))
        self._log("tool_calls_out", batch_id=batch_id, calls=[call.name for call in sent])
        self._emit_calls(CallsItem(batch_id=batch_id, calls=[(c.call_id, c.name, c.arguments) for c in sent]))

    def _screen(self, calls: list[_Call]) -> list[_Call]:
        """Canonicalize arguments and answer invalid / already-failed calls locally."""
        if self._normalizer is None:
            return calls
        screening = self._normalizer.screen(
            [ToolCall(id=call.call_id, name=call.name, arguments_json=call.arguments) for call in calls],
            self._failed,
            self._invalid_counts,
        )
        by_id = {call.call_id: call for call in calls}
        for rewrite in screening.rewrites:
            self._log(
                "argument_normalized",
                call_id=rewrite.call_id,
                tool=rewrite.tool,
                argument=rewrite.argument,
                before=rewrite.before,
                after=rewrite.after,
            )
        for answer in screening.local:
            call = by_id[answer.call_id]
            if answer.reason == "invalid":
                self._invalid_counts[answer.tool] = self._invalid_counts.get(answer.tool, 0) + 1
            self._log(
                "call_answered_locally",
                call_id=answer.call_id,
                tool=answer.tool,
                reason=answer.reason,
                message_key=answer.message_key,
            )
            self._send_result(answer.call_id, call.epoch, answer.message, True)
        out: list[_Call] = []
        for canonical in screening.sent:
            call = by_id[canonical.id]
            call.arguments = canonical.arguments_json
            call.key = screening.keys.get(canonical.id)
            out.append(call)
        return out

    async def _run_local(self, call: _Call) -> None:
        assert self._registry is not None  # noqa: S101 - local executor only
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        result = await self._registry.execute(
            ToolCall(id=call.call_id, name=call.name, arguments_json=call.arguments or "{}")
        )
        self._log("tool_output_in", call_id=call.call_id, output=result.content, local_executor=True)
        self._send_result(call.call_id, call.epoch, result.content, False)
