"""Batch scheduling uses bounded, lifecycle-owned command invocations."""
import asyncio
import threading

import pytest

from twicc.mcp.batch import BatchRuntime
from twicc.mcp.batch_contract import PreparedBatch, PreparedCall
from twicc.mcp.dispatch import PreparedTool
from twicc.mcp.tools import tools_by_name


def batch(count=3, *, mode="sequential", on_error="stop", id="batch"):
    spec = tools_by_name()["session"]
    return PreparedBatch(id, mode, on_error, tuple(
        PreparedCall(i, str(i), PreparedTool("session", spec, {"session_id": str(i)}))
        for i in range(count)
    ))


async def valid(grant):
    return True


def test_sequential_stop_and_continue():
    async def scenario(policy):
        seen = []
        async def execute(tool, session_id, *, on_start):
            on_start()
            seen.append(tool.arguments["session_id"])
            return {"exit_code": 4 if len(seen) == 2 else 0, "result": None, "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=valid)
        try:
            result = await runtime.run(batch(on_error=policy), session_id=None)
            assert seen == (["0", "1"] if policy == "stop" else ["0", "1", "2"])
            assert result["summary"]["failed"] == 1
            assert result["summary"]["skipped"] == (policy == "stop")
        finally:
            await runtime.close()
    for policy in ("stop", "continue"):
        asyncio.run(scenario(policy))


def test_parallel_overlap_and_input_order():
    async def scenario():
        entered = asyncio.Event()
        gates = [asyncio.Event() for _ in range(4)]
        seen = []
        async def execute(tool, session_id, *, on_start):
            on_start()
            i = int(tool.arguments["session_id"])
            seen.append(i)
            if len(seen) == 4:
                entered.set()
            await gates[i].wait()
            if i == 2:
                raise RuntimeError("secret")
            return {"exit_code": 0, "result": i, "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=valid)
        task = asyncio.create_task(runtime.run(batch(4, mode="parallel", on_error="continue"), session_id=None))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert seen == [0, 1, 2, 3]
            for gate in reversed(gates):
                gate.set()
                await asyncio.sleep(0)
            result = await task
            assert [r["id"] for r in result["results"]] == ["0", "1", "2", "3"]
            assert result["results"][2]["status"] == "tool_error"
            assert "secret" not in str(result)
        finally:
            for gate in gates:
                gate.set()
            await runtime.close()
    asyncio.run(scenario())


def test_cancelled_request_keeps_capacity_until_worker_returns():
    async def scenario():
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        def work():
            loop.call_soon_threadsafe(entered.set)
            release.wait(5)
            return {"exit_code": 0, "result": None, "error": None}
        async def execute(tool, session_id, *, on_start):
            on_start()
            return await asyncio.to_thread(work)
        runtime = BatchRuntime(execute=execute, check_grant=valid, max_batches=1)
        task = asyncio.create_task(runtime.run(batch(), session_id=None))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            busy = await runtime.run(batch(), session_id=None)
            assert busy["status"] == "rejected"
            assert busy["errors"][0]["code"] == "server_busy"
            release.set()
        finally:
            release.set()
            await runtime.close()
    asyncio.run(scenario())


def test_global_capacity_and_busy_admission():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0
        async def execute(tool, session_id, *, on_start):
            nonlocal active, peak
            on_start()
            active += 1
            peak = max(peak, active)
            if active == 8:
                entered.set()
            try:
                await release.wait()
                return {"exit_code": 0, "result": None, "error": None}
            finally:
                active -= 1
        runtime = BatchRuntime(execute=execute, check_grant=valid)
        tasks = [asyncio.create_task(runtime.run(batch(4, mode="parallel", on_error="continue", id=str(i)),
                                                session_id=None)) for i in range(4)]
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert runtime.admitted == 4
            busy = await runtime.run(batch(), session_id=None)
            assert busy["status"] == "rejected"
            release.set()
            results = await asyncio.gather(*tasks)
            assert all(r["ok"] for r in results)
            assert peak == 8
        finally:
            release.set()
            await runtime.close()
    asyncio.run(scenario())


def test_cancellation_while_waiting_for_permit_does_not_leak():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        seen = []
        async def execute(tool, session_id, *, on_start):
            on_start()
            seen.append(session_id)
            entered.set()
            await release.wait()
            return {"exit_code": 0, "result": None, "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=valid, max_children=1)
        first = asyncio.create_task(runtime.run(batch(1), session_id="first"))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(runtime.run(batch(1), session_id="second"))
            # Wait until the second coordinator is actually contending for capacity.
            for _ in range(100):
                if runtime.permits._waiters:
                    break
                await asyncio.sleep(0)
            assert runtime.permits._waiters
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            release.set()
            await first
            await runtime.close()
            assert seen == ["first"]
            assert runtime.permits._value == 1
        finally:
            release.set()
            await runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("check_kind", ["revoked", "exception", "missing"])
def test_authority_stop_preserves_completed_results(check_kind):
    from twicc.mcp.identity import ExternalCaller, ExternalGrant, external_caller, external_grant
    async def scenario():
        checks = 0
        seen = []
        async def checker(grant):
            nonlocal checks
            checks += 1
            if checks > 1:
                if check_kind == "exception":
                    raise RuntimeError("SECRET")
                return False
            return True
        async def execute(tool, session_id, *, on_start):
            on_start()
            seen.append(tool.arguments["session_id"])
            return {"exit_code": 0, "result": None, "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=checker)
        caller_token = external_caller.set(ExternalCaller("connection", "External"))
        grant_token = external_grant.set(None if check_kind == "missing" else ExternalGrant("connection", "resource", 999))
        try:
            result = await runtime.run(batch(on_error="continue"), session_id=None)
            completed = 0 if check_kind == "missing" else 1
            assert seen == ([] if completed == 0 else ["0"])
            assert result["summary"]["succeeded"] == completed
            code = "authorization_changed" if check_kind == "revoked" else "authorization_unavailable"
            assert all(r["error"]["code"] == code for r in result["results"][completed:])
            assert "SECRET" not in str(result)
        finally:
            external_grant.reset(grant_token)
            external_caller.reset(caller_token)
            await runtime.close()
    asyncio.run(scenario())


def test_coordinator_failure_keeps_worker_owned(monkeypatch):
    async def scenario():
        release = asyncio.Event()
        entered = asyncio.Event()
        async def execute(tool, session_id, *, on_start):
            on_start()
            entered.set()
            await release.wait()
            return {"exit_code": 0, "result": None, "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=valid, max_batches=1)
        launch = runtime._launch
        def faulty(state, call):
            if call.index == 1:
                raise RuntimeError("fault injection")
            launch(state, call)
        monkeypatch.setattr(runtime, "_launch", faulty)
        task = asyncio.create_task(runtime.run(batch(mode="parallel", on_error="continue"), session_id=None))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert (await runtime.run(batch(), session_id=None))["status"] == "rejected"
            assert len(runtime.children) == 1
            release.set()
            result = await task
            assert result["summary"]["succeeded"] == 1
            assert result["summary"]["skipped"] == 2
        finally:
            release.set()
            await runtime.close()
    asyncio.run(scenario())


def test_start_marker_and_omission_do_not_change_stop_policy():
    async def scenario():
        seen = []
        async def execute(tool, session_id, *, on_start):
            i = tool.arguments["session_id"]
            seen.append(i)
            if i == "1":
                raise ValueError("render failed")
            on_start()
            return {"exit_code": 0, "result": "x" * (384 * 1024), "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=valid)
        try:
            result = await runtime.run(batch(), session_id=None)
            assert seen == ["0", "1"]
            assert result["results"][0]["response_omitted"]
            assert not result["results"][1]["outcome_unknown"]
            assert result["results"][2]["status"] == "skipped"
        finally:
            await runtime.close()
    asyncio.run(scenario())


def test_shutdown_grace_does_not_join_thread():
    async def scenario():
        release = threading.Event()
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        def worker():
            loop.call_soon_threadsafe(entered.set)
            release.wait(5)
            return {"exit_code": 0, "result": None, "error": None}
        async def execute(tool, session_id, *, on_start):
            on_start()
            return await asyncio.to_thread(worker)
        runtime = BatchRuntime(execute=execute, check_grant=valid, shutdown_grace=0.01)
        task = asyncio.create_task(runtime.run(batch(), session_id=None))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(runtime.close(), 1)
            assert not release.is_set()
            assert not runtime.accepting
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_concurrent_callers_and_correlation_are_isolated():
    from twicc.cli._drop_request.whoami import forced_session_id
    from twicc.mcp.identity import ExternalCaller, ExternalGrant, external_caller, external_grant, batch_correlation
    async def scenario():
        entered = asyncio.Event()
        contexts = []
        async def execute(tool, session_id, *, on_start):
            on_start()
            context = (session_id, forced_session_id.get(), external_caller.get(), external_grant.get(),
                       batch_correlation.get().batch_id)
            contexts.append(context)
            if len(contexts) == 2:
                entered.set()
            await entered.wait()
            assert context == (session_id, forced_session_id.get(), external_caller.get(), external_grant.get(),
                               batch_correlation.get().batch_id)
            return {"exit_code": 0, "result": None, "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=valid)
        internal = asyncio.create_task(runtime.run(batch(1, id="internal"), session_id="session"))
        token = external_caller.set(ExternalCaller("connection", "name"))
        grant_token = external_grant.set(ExternalGrant("connection", "resource", 100))
        external = asyncio.create_task(runtime.run(batch(1, id="external"), session_id=None))
        external_grant.reset(grant_token)
        external_caller.reset(token)
        try:
            await asyncio.wait_for(asyncio.gather(internal, external), 2)
            contexts.sort(key=lambda x: x[-1])
            assert contexts[0][:2] == (None, None)
            assert contexts[0][2].connection_id == "connection"
            assert contexts[1][:4] == ("session", "session", None, None)
            assert external_caller.get() is None
        finally:
            entered.set()
            await runtime.close()
    asyncio.run(scenario())


def test_raw_cancellation_of_shutdown_cancels_owned_tasks():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def execute(tool, session_id, *, on_start):
            on_start()
            entered.set()
            await release.wait()
            return {"exit_code": 0, "result": None, "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=valid)
        request = asyncio.create_task(runtime.run(batch(), session_id=None))
        cleanup = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            cleanup = asyncio.create_task(runtime.close())
            await asyncio.sleep(0)
            cleanup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cleanup
            assert runtime.forcing
            assert all(task.cancelling() or task.done() for task in runtime.children)
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)
            await runtime.close()
    asyncio.run(scenario())


def test_anyio_request_cancellation_keeps_native_children_owned():
    import anyio
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def execute(tool, session_id, *, on_start):
            on_start()
            entered.set()
            await release.wait()
            return {"exit_code": 0, "result": None, "error": None}
        runtime = BatchRuntime(execute=execute, check_grant=valid, max_batches=1)
        async def request():
            await runtime.run(batch(), session_id=None)
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(request)
                await entered.wait()
                group.cancel_scope.cancel()
            assert runtime.admitted == 1
            assert len(runtime.children) == 1
            assert not next(iter(runtime.children)).cancelled()
            assert (await runtime.run(batch(), session_id=None))["status"] == "rejected"
        finally:
            release.set()
            await runtime.close()
    asyncio.run(scenario())
