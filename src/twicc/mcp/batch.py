"""Bounded MCP aggregation with command ownership independent of request cancellation."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Protocol

from twicc.cli._drop_request.whoami import forced_session_id
from twicc.mcp.batch_contract import (
    PreparedBatch, PreparedCall, command_record, completed_batch, failed_record,
    rejected_batch, skipped_record,
)
from twicc.mcp.dispatch import PreparedTool
from twicc.mcp.identity import (
    BatchCorrelation, ExternalGrant, batch_correlation, external_caller, external_grant,
)

logger = logging.getLogger(__name__)


class Execute(Protocol):
    def __call__(self, tool: PreparedTool, session_id: str | None, *,
                 on_start: Callable[[], None]) -> Awaitable[dict]: ...


class _Run:
    def __init__(self, batch: PreparedBatch, session_id: str | None):
        self.batch = batch
        self.session_id = session_id
        self.caller = external_caller.get()
        self.grant = external_grant.get()
        self.stop = asyncio.Event()
        self.children: set[asyncio.Task] = set()
        self.records: dict[int, dict] = {}
        self.stop_code: str | None = None
        self.caused_by: str | None = None


def _consume(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class BatchRuntime:
    def __init__(self, *, execute: Execute, check_grant: Callable[[ExternalGrant], Awaitable[bool]],
                 max_batches: int = 4, max_children: int = 8, per_batch: int = 4,
                 shutdown_grace: float = 5.0):
        self.loop = asyncio.get_running_loop()
        self.execute = execute
        self.check_grant = check_grant
        self.max_batches = max_batches
        self.per_batch = per_batch
        self.shutdown_grace = shutdown_grace
        self.accepting = True
        self.forcing = False
        self.admitted = 0
        self.permits = asyncio.Semaphore(max_children)
        self.coordinators: dict[asyncio.Task, _Run] = {}
        self.children: set[asyncio.Task] = set()

    async def run(self, batch: PreparedBatch, *, session_id: str | None) -> dict:
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("Batch runtime belongs to a different event loop.")
        if not self.accepting or self.admitted >= self.max_batches:
            logger.info("MCP batch rejected batch_id=%s code=server_busy", batch.batch_id)
            return rejected_batch(batch.batch_id, "server_busy")
        # No await between checking capacity and claiming ownership.
        state = _Run(batch, session_id)
        self.admitted += 1
        task = asyncio.create_task(self._coordinate(state))
        self.coordinators[task] = state
        task.add_done_callback(self._settled)
        logger.info("MCP batch admitted batch_id=%s caller=%s total=%s", batch.batch_id,
                    state.caller.connection_id if state.caller else session_id, len(batch.calls))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            state.stop.set()
            logger.info("MCP batch cancelled batch_id=%s", batch.batch_id)
            raise

    def _settled(self, task: asyncio.Task) -> None:
        self.coordinators.pop(task, None)
        self.admitted -= 1
        _consume(task)

    async def _acquire(self, state: _Run) -> bool:
        acquire = asyncio.create_task(self.permits.acquire())
        stopped = asyncio.create_task(state.stop.wait())
        transferred = False
        started = monotonic()
        try:
            await asyncio.wait({acquire, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if state.stop.is_set() or not self.accepting:
                return False
            await acquire
            transferred = True
            return True
        finally:
            # Cancel waiting tasks, not a running command. If both won, return the permit.
            for task in (acquire, stopped):
                if not task.done():
                    task.cancel()
            await asyncio.gather(acquire, stopped, return_exceptions=True)
            if not transferred and acquire.done() and not acquire.cancelled() and acquire.exception() is None:
                self.permits.release()
            logger.info("MCP batch capacity batch_id=%s wait_ms=%.3f", state.batch.batch_id,
                        (monotonic() - started) * 1000)

    async def _authorized(self, state: _Run) -> bool:
        if state.caller is None:
            return True
        if state.grant is None or state.grant.connection_id != state.caller.connection_id:
            state.stop_code = "authorization_unavailable"
        else:
            try:
                if await self.check_grant(state.grant):
                    return True
                state.stop_code = "authorization_changed"
            except Exception:
                state.stop_code = "authorization_unavailable"
        logger.info("MCP batch authorization stopped batch_id=%s code=%s", state.batch.batch_id, state.stop_code)
        state.stop.set()
        return False

    def _launch(self, state: _Run, call: PreparedCall) -> None:
        child = asyncio.create_task(self._child(state, call))
        state.children.add(child)
        self.children.add(child)
        child.add_done_callback(state.children.discard)
        child.add_done_callback(self.children.discard)
        child.add_done_callback(_consume)

    async def _child(self, state: _Run, call: PreparedCall) -> None:
        started = False
        start_time = monotonic()
        tokens = [
            (forced_session_id, forced_session_id.set(state.session_id)),
            (external_caller, external_caller.set(state.caller)),
            (external_grant, external_grant.set(state.grant)),
            (batch_correlation, batch_correlation.set(BatchCorrelation(state.batch.batch_id, call.id, call.index))),
        ]
        def mark_started():
            nonlocal started
            started = True
        try:
            if state.stop.is_set():
                state.records[call.index] = skipped_record(call, code=state.stop_code or "execution_error",
                                                          caused_by=state.caused_by)
                return
            try:
                envelope = await self.execute(call.tool, state.session_id, on_start=mark_started)
                record = command_record(call, envelope)
            except Exception:
                record = failed_record(call, started=started)
            state.records[call.index] = record
            if (state.batch.mode == "sequential" and state.batch.on_error == "stop"
                    and (record["status"] != "success" or record["outcome_unknown"])):
                state.stop_code = "previous_call_failed"
                state.caused_by = call.id
                state.stop.set()
            logger.info("MCP batch child batch_id=%s index=%s tool=%s status=%s exit_code=%s elapsed_ms=%.3f",
                        state.batch.batch_id, call.index, call.tool.name, record["status"],
                        envelope.get("exit_code") if record["status"] in {"success", "command_error"} else None,
                        (monotonic() - start_time) * 1000)
        finally:
            for var, token in reversed(tokens):
                var.reset(token)
            self.permits.release()

    async def _coordinate(self, state: _Run) -> dict:
        start_time = monotonic()
        width = self.per_batch if state.batch.mode == "parallel" else 1
        try:
            for call in state.batch.calls:
                if state.stop.is_set():
                    break
                while len(state.children) >= width:
                    await asyncio.wait(state.children, return_when=asyncio.FIRST_COMPLETED)
                    # Callbacks remove settled tasks; allow them to run before checking width again.
                    if state.stop.is_set():
                        break
                if state.stop.is_set() or not await self._acquire(state):
                    break
                transferred = False
                try:
                    if not state.stop.is_set() and await self._authorized(state) and not state.stop.is_set():
                        self._launch(state, call)
                        transferred = True
                finally:
                    if not transferred:
                        self.permits.release()
                if not transferred:
                    break
        except Exception:
            # Do not let an orchestration error orphan already-submitted workers.
            state.stop_code = "execution_error"
            state.stop.set()
            logger.error("MCP batch coordinator failed batch_id=%s", state.batch.batch_id)
        finally:
            if not self.forcing and state.children:
                await asyncio.gather(*state.children, return_exceptions=True)
        records = [state.records.get(call.index) or skipped_record(
            call, code=state.stop_code or "execution_error", caused_by=state.caused_by,
        ) for call in state.batch.calls]
        result = completed_batch(state.batch.batch_id, records)
        logger.info("MCP batch finished batch_id=%s ok=%s elapsed_ms=%.3f", state.batch.batch_id,
                    result["ok"], (monotonic() - start_time) * 1000)
        return result

    async def close(self) -> None:
        self.accepting = False
        for state in self.coordinators.values():
            state.stop.set()
        try:
            tasks = set(self.coordinators) | self.children
            if tasks:
                await asyncio.wait(tasks, timeout=self.shutdown_grace)
        finally:
            # Backend shutdown uses raw Task.cancel(), which an AnyIO shield does
            # not intercept. Interrupted draining must still account for all owners.
            pending = {task for task in set(self.coordinators) | self.children if not task.done()}
            if pending:
                self.forcing = True
                logger.warning("MCP batch shutdown abandoned_invocations=%s", len(self.children))
                for task in pending:
                    task.cancel()
                    task.add_done_callback(_consume)
        # No joining threads or unbounded cancellation acknowledgement here.
