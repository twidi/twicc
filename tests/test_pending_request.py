"""
Tests for PendingRequest dataclass, ProcessInfo serialization,
and ClaudeProcess pending request mechanism.
"""

import asyncio
from unittest.mock import AsyncMock

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from twicc.agent.process import ClaudeProcess
from twicc.agent.states import (
    PendingRequest,
    ProcessInfo,
    ProcessState,
    serialize_process_info,
)


def _make_process_info(**kwargs) -> ProcessInfo:
    """Create a ProcessInfo with sensible defaults, overridable via kwargs."""
    defaults = {
        "session_id": "test-session",
        "project_id": "test-project",
        "state": ProcessState.ASSISTANT_TURN,
        "previous_state": None,
        "started_at": 1000000.0,
        "state_changed_at": 1000001.0,
        "last_activity": 1000002.0,
    }
    defaults.update(kwargs)
    return ProcessInfo(**defaults)


def _make_pending_request(**kwargs) -> PendingRequest:
    """Create a PendingRequest with sensible defaults, overridable via kwargs."""
    defaults = {
        "request_id": "req-123",
        "request_type": "tool_approval",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la", "description": "List files"},
        "created_at": 1000005.0,
    }
    defaults.update(kwargs)
    return PendingRequest(**defaults)


class TestPendingRequest:
    """Tests for the PendingRequest dataclass."""

    def test_tool_approval_creation(self):
        """PendingRequest can be created for a tool approval request."""
        req = PendingRequest(
            request_id="abc-123",
            request_type="tool_approval",
            tool_name="Bash",
            tool_input={"command": "rm -rf /tmp/test", "description": "Delete test directory"},
            created_at=1234567890.0,
        )
        assert req.request_id == "abc-123"
        assert req.request_type == "tool_approval"
        assert req.tool_name == "Bash"
        assert req.tool_input == {"command": "rm -rf /tmp/test", "description": "Delete test directory"}
        assert req.created_at == 1234567890.0

    def test_ask_user_question_creation(self):
        """PendingRequest can be created for an ask_user_question request."""
        questions = [
            {
                "question": "How should I format the output?",
                "header": "Format",
                "options": [
                    {"label": "Summary", "description": "Brief overview"},
                    {"label": "Detailed", "description": "Full explanation"},
                ],
                "multiSelect": False,
            }
        ]
        req = PendingRequest(
            request_id="def-456",
            request_type="ask_user_question",
            tool_name="AskUserQuestion",
            tool_input={"questions": questions},
            created_at=1234567891.0,
        )
        assert req.request_type == "ask_user_question"
        assert req.tool_name == "AskUserQuestion"
        assert len(req.tool_input["questions"]) == 1
        assert req.tool_input["questions"][0]["question"] == "How should I format the output?"

    def test_frozen(self):
        """PendingRequest is frozen (immutable)."""
        req = _make_pending_request()
        try:
            req.request_id = "new-id"
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass


class TestProcessInfoWithPendingRequest:
    """Tests for PendingRequest integration in ProcessInfo."""

    def test_pending_request_defaults_to_none(self):
        """ProcessInfo.pending_request defaults to None."""
        info = _make_process_info()
        assert info.pending_request is None

    def test_pending_request_can_be_set(self):
        """ProcessInfo can hold a PendingRequest."""
        req = _make_pending_request()
        info = _make_process_info(pending_request=req)
        assert info.pending_request is req
        assert info.pending_request.request_id == "req-123"


class TestSerializeProcessInfoPendingRequest:
    """Tests for pending request serialization in serialize_process_info()."""

    def test_no_pending_request_omits_key(self):
        """When pending_request is None, the serialized dict has no 'pending_request' key."""
        info = _make_process_info()
        data = serialize_process_info(info)
        assert "pending_request" not in data

    def test_tool_approval_serialization(self):
        """Tool approval pending request is fully serialized."""
        req = PendingRequest(
            request_id="uuid-abc",
            request_type="tool_approval",
            tool_name="Bash",
            tool_input={"command": "echo hello", "description": "Print hello"},
            created_at=1000005.0,
        )
        info = _make_process_info(pending_request=req)
        data = serialize_process_info(info)

        assert "pending_request" in data
        pr = data["pending_request"]
        assert pr["request_id"] == "uuid-abc"
        assert pr["request_type"] == "tool_approval"
        assert pr["tool_name"] == "Bash"
        assert pr["tool_input"] == {"command": "echo hello", "description": "Print hello"}
        assert pr["created_at"] == 1000005.0

    def test_ask_user_question_serialization(self):
        """Ask user question pending request is fully serialized."""
        questions = [
            {
                "question": "Which format?",
                "header": "Output",
                "options": [{"label": "JSON"}, {"label": "CSV"}],
                "multiSelect": False,
            }
        ]
        req = PendingRequest(
            request_id="uuid-def",
            request_type="ask_user_question",
            tool_name="AskUserQuestion",
            tool_input={"questions": questions},
            created_at=1000006.0,
        )
        info = _make_process_info(pending_request=req)
        data = serialize_process_info(info)

        pr = data["pending_request"]
        assert pr["request_type"] == "ask_user_question"
        assert pr["tool_name"] == "AskUserQuestion"
        assert pr["tool_input"]["questions"] == questions

    def test_serialized_pending_request_has_exactly_five_keys(self):
        """The serialized pending request dict contains exactly the five expected keys."""
        req = _make_pending_request()
        info = _make_process_info(pending_request=req)
        data = serialize_process_info(info)

        pr = data["pending_request"]
        assert set(pr.keys()) == {"request_id", "request_type", "tool_name", "tool_input", "created_at"}

    def test_other_fields_unaffected_by_pending_request(self):
        """Adding a pending_request does not change serialization of other fields."""
        info_without = _make_process_info(error="some error", kill_reason="manual")
        info_with = _make_process_info(
            error="some error",
            kill_reason="manual",
            pending_request=_make_pending_request(),
        )

        data_without = serialize_process_info(info_without)
        data_with = serialize_process_info(info_with)

        # All keys from without should be present in with
        for key in data_without:
            assert data_with[key] == data_without[key]

        # with has one extra key
        assert set(data_with.keys()) - set(data_without.keys()) == {"pending_request"}


# =============================================================================
# ClaudeProcess pending request mechanism tests
# =============================================================================


def _make_claude_process() -> ClaudeProcess:
    """Create a ClaudeProcess for testing, without starting it."""
    return ClaudeProcess(
        session_id="test-session-1",
        project_id="test-project-1",
        cwd="/tmp/test",
    )


class TestHandlePendingRequest:
    """Tests for ClaudeProcess._handle_pending_request()."""

    def test_creates_pending_request_and_blocks_on_future(self):
        """_handle_pending_request() creates a PendingRequest, sets the Future,
        notifies state change, then blocks. When the Future resolves, it clears
        state and notifies again."""
        process = _make_claude_process()
        state_change_calls = []

        async def mock_state_change(proc):
            # Capture snapshot of pending_request at each call
            state_change_calls.append(proc.pending_request)

        process._state_change_callback = mock_state_change

        async def run():
            # Schedule the callback in a task so we can resolve the Future
            task = asyncio.create_task(
                process._handle_pending_request(
                    "Bash", {"command": "ls"}, None
                )
            )
            # Let the callback run until it blocks on the Future
            await asyncio.sleep(0)

            # Verify pending request was created
            assert process._pending_request is not None
            assert process._pending_request.request_type == "tool_approval"
            assert process._pending_request.tool_name == "Bash"
            assert process._pending_request.tool_input == {"command": "ls"}
            assert process._pending_request.request_id  # non-empty UUID string

            # Verify Future exists and is not resolved
            assert process._pending_request_future is not None
            assert not process._pending_request_future.done()

            # First state change notification happened (with pending request set)
            assert len(state_change_calls) == 1
            assert state_change_calls[0] is not None
            assert state_change_calls[0].tool_name == "Bash"

            # Resolve the Future
            response = PermissionResultAllow(updated_input={"command": "ls"})
            process._pending_request_future.set_result(response)

            # Let the callback finish
            result = await task

            # After resolution: state is cleared
            assert process._pending_request is None
            assert process._pending_request_future is None

            # Second state change notification happened (with pending request cleared)
            assert len(state_change_calls) == 2
            assert state_change_calls[1] is None

            # Returns the response
            assert result is response

        asyncio.run(run())

    def test_ask_user_question_sets_correct_type(self):
        """_handle_pending_request() sets request_type to 'ask_user_question'
        when tool_name is 'AskUserQuestion'."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            questions = [{"question": "Which format?", "options": [{"label": "JSON"}]}]
            task = asyncio.create_task(
                process._handle_pending_request(
                    "AskUserQuestion", {"questions": questions}, None
                )
            )
            await asyncio.sleep(0)

            assert process._pending_request is not None
            assert process._pending_request.request_type == "ask_user_question"
            assert process._pending_request.tool_name == "AskUserQuestion"

            # Clean up: resolve the Future so the task completes
            process._pending_request_future.set_result(
                PermissionResultAllow(updated_input={"questions": questions})
            )
            await task

        asyncio.run(run())

    def test_non_ask_user_question_tools_are_tool_approval(self):
        """_handle_pending_request() sets request_type to 'tool_approval'
        for any tool other than 'AskUserQuestion'."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            for tool_name in ("Bash", "Write", "Edit", "Read"):
                task = asyncio.create_task(
                    process._handle_pending_request(
                        tool_name, {"file_path": "/test"}, None
                    )
                )
                await asyncio.sleep(0)

                assert process._pending_request.request_type == "tool_approval"
                assert process._pending_request.tool_name == tool_name

                # Resolve and clean up
                process._pending_request_future.set_result(
                    PermissionResultAllow(updated_input={})
                )
                await task

        asyncio.run(run())


class TestResolvePendingRequest:
    """Tests for ClaudeProcess.resolve_pending_request()."""

    def test_returns_true_and_resolves_active_future(self):
        """resolve_pending_request() returns True and sets the Future result
        when a pending Future exists."""
        process = _make_claude_process()

        async def run():
            loop = asyncio.get_event_loop()
            process._pending_request = _make_pending_request()
            process._pending_request_future = loop.create_future()

            response = PermissionResultAllow(updated_input={"command": "ls"})
            result = process.resolve_pending_request(response)

            assert result is True
            assert process._pending_request_future.done()
            assert process._pending_request_future.result() is response

        asyncio.run(run())

    def test_returns_false_when_no_pending_request(self):
        """resolve_pending_request() returns False when _pending_request_future is None."""
        process = _make_claude_process()

        response = PermissionResultDeny(message="denied")
        result = process.resolve_pending_request(response)

        assert result is False

    def test_returns_false_when_future_already_resolved(self):
        """resolve_pending_request() returns False when the Future is already done."""
        process = _make_claude_process()

        async def run():
            loop = asyncio.get_event_loop()
            process._pending_request = _make_pending_request()
            process._pending_request_future = loop.create_future()
            process._pending_request_future.set_result(
                PermissionResultAllow(updated_input={})
            )

            response = PermissionResultDeny(message="too late")
            result = process.resolve_pending_request(response)

            assert result is False

        asyncio.run(run())

    def test_returns_false_when_future_already_cancelled(self):
        """resolve_pending_request() returns False when the Future is cancelled."""
        process = _make_claude_process()

        async def run():
            loop = asyncio.get_event_loop()
            process._pending_request = _make_pending_request()
            process._pending_request_future = loop.create_future()
            process._pending_request_future.cancel()

            response = PermissionResultAllow(updated_input={})
            result = process.resolve_pending_request(response)

            assert result is False

        asyncio.run(run())


class TestCancelPendingRequestFuture:
    """Tests for ClaudeProcess._cancel_pending_request_future()."""

    def test_cancels_active_future_and_clears_state(self):
        """_cancel_pending_request_future() cancels the Future and sets both
        _pending_request and _pending_request_future to None."""
        process = _make_claude_process()

        async def run():
            loop = asyncio.get_event_loop()
            process._pending_request = _make_pending_request()
            process._pending_request_future = loop.create_future()

            process._cancel_pending_request_future()

            assert process._pending_request is None
            assert process._pending_request_future is None

        asyncio.run(run())

    def test_handles_already_done_future(self):
        """_cancel_pending_request_future() does not raise when the Future is already done."""
        process = _make_claude_process()

        async def run():
            loop = asyncio.get_event_loop()
            process._pending_request = _make_pending_request()
            process._pending_request_future = loop.create_future()
            process._pending_request_future.set_result(
                PermissionResultAllow(updated_input={})
            )

            # Should not raise
            process._cancel_pending_request_future()

            assert process._pending_request is None
            assert process._pending_request_future is None

        asyncio.run(run())

    def test_handles_no_future(self):
        """_cancel_pending_request_future() handles None Future gracefully."""
        process = _make_claude_process()

        # Set pending_request but no Future
        process._pending_request = _make_pending_request()
        process._pending_request_future = None

        # Should not raise
        process._cancel_pending_request_future()

        assert process._pending_request is None
        assert process._pending_request_future is None

    def test_cancelled_future_is_actually_cancelled(self):
        """The Future is marked as cancelled after _cancel_pending_request_future()."""
        process = _make_claude_process()

        async def run():
            loop = asyncio.get_event_loop()
            future = loop.create_future()
            process._pending_request = _make_pending_request()
            process._pending_request_future = future

            process._cancel_pending_request_future()

            assert future.cancelled()

        asyncio.run(run())


class TestKillCancelsPendingRequest:
    """Tests that kill() cancels the pending request Future."""

    def test_kill_cancels_pending_future(self):
        """kill() cancels the pending request Future so no asyncio warnings occur."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()
        process.state = ProcessState.ASSISTANT_TURN

        async def run():
            loop = asyncio.get_event_loop()
            future = loop.create_future()
            process._pending_request = _make_pending_request()
            process._pending_request_future = future

            await process.kill(reason="test")

            # Future is cancelled (not left unawaited)
            assert future.cancelled()
            # Pending request state is cleared
            assert process._pending_request is None
            assert process._pending_request_future is None
            # Process transitioned to DEAD
            assert process.state == ProcessState.DEAD
            assert process.kill_reason == "test"

        asyncio.run(run())

    def test_kill_without_pending_request_works(self):
        """kill() works correctly when there is no pending request."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()
        process.state = ProcessState.ASSISTANT_TURN

        async def run():
            await process.kill(reason="shutdown")

            assert process.state == ProcessState.DEAD
            assert process._pending_request is None
            assert process._pending_request_future is None

        asyncio.run(run())


class TestHandleErrorCancelsPendingRequest:
    """Tests that _handle_error() cancels the pending request Future."""

    def test_handle_error_cancels_pending_future(self):
        """_handle_error() cancels the pending request Future so no asyncio warnings occur."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            loop = asyncio.get_event_loop()
            future = loop.create_future()
            process._pending_request = _make_pending_request()
            process._pending_request_future = future

            await process._handle_error("something broke")

            # Future is cancelled (not left unawaited)
            assert future.cancelled()
            # Pending request state is cleared
            assert process._pending_request is None
            assert process._pending_request_future is None
            # Process transitioned to DEAD
            assert process.state == ProcessState.DEAD
            assert process.error == "something broke"

        asyncio.run(run())

    def test_handle_error_without_pending_request_works(self):
        """_handle_error() works correctly when there is no pending request."""
        process = _make_claude_process()
        process._state_change_callback = AsyncMock()

        async def run():
            await process._handle_error("some error")

            assert process.state == ProcessState.DEAD
            assert process.error == "some error"
            assert process._pending_request is None
            assert process._pending_request_future is None

        asyncio.run(run())


class TestGetInfoIncludesPendingRequest:
    """Tests that get_info() includes the pending request in ProcessInfo."""

    def test_get_info_with_pending_request(self):
        """get_info() includes the pending request in the returned ProcessInfo."""
        process = _make_claude_process()
        process.state = ProcessState.DEAD  # Avoid memory query on non-existent PID

        req = _make_pending_request()
        process._pending_request = req

        info = process.get_info()

        assert info.pending_request is req
        assert info.pending_request.request_id == "req-123"
        assert info.pending_request.tool_name == "Bash"

    def test_get_info_without_pending_request(self):
        """get_info() returns None for pending_request when there is none."""
        process = _make_claude_process()
        process.state = ProcessState.DEAD  # Avoid memory query on non-existent PID

        info = process.get_info()

        assert info.pending_request is None


class TestPendingRequestProperty:
    """Tests for the ClaudeProcess.pending_request property."""

    def test_returns_pending_request_value(self):
        """The pending_request property returns _pending_request."""
        process = _make_claude_process()

        # Initially None
        assert process.pending_request is None

        # Set a pending request
        req = _make_pending_request()
        process._pending_request = req
        assert process.pending_request is req

        # Clear it
        process._pending_request = None
        assert process.pending_request is None


# =============================================================================
# _dummy_hook tests
# =============================================================================


class TestDummyHook:
    """Tests for the module-level _dummy_hook function."""

    def test_returns_continue_true(self):
        """_dummy_hook() returns {"continue_": True}."""
        from twicc.agent.process import _dummy_hook

        async def run():
            result = await _dummy_hook({"command": "ls"}, "tool-use-123", None)
            assert result == {"continue_": True}

        asyncio.run(run())


# =============================================================================
# _build_query_prompt tests (always async generator)
# =============================================================================


class TestBuildQueryPrompt:
    """Tests for ClaudeProcess._build_query_prompt() always returning an async generator."""

    def test_text_only_returns_async_generator(self):
        """_build_query_prompt() returns an async generator even for text-only messages."""
        process = _make_claude_process()

        async def run():
            result = process._build_query_prompt("hello", None, None)
            # Should be an async iterator, not a plain string
            assert hasattr(result, "__aiter__")
            assert hasattr(result, "__anext__")

            messages = [msg async for msg in result]
            assert len(messages) == 1
            msg = messages[0]
            assert msg["type"] == "user"
            assert msg["message"]["role"] == "user"
            assert msg["parent_tool_use_id"] is None
            # Content should be a single text block
            assert msg["message"]["content"] == [{"type": "text", "text": "hello"}]

        asyncio.run(run())

    def test_with_images(self):
        """_build_query_prompt() includes images before text in content blocks."""
        process = _make_claude_process()
        images = [{"type": "image", "source": {"data": "base64data"}}]

        async def run():
            result = process._build_query_prompt("describe this", images, None)
            messages = [msg async for msg in result]
            assert len(messages) == 1
            content = messages[0]["message"]["content"]
            assert len(content) == 2
            assert content[0] == images[0]
            assert content[1] == {"type": "text", "text": "describe this"}

        asyncio.run(run())

    def test_with_documents(self):
        """_build_query_prompt() includes documents before text in content blocks."""
        process = _make_claude_process()
        documents = [{"type": "document", "source": {"data": "pdfdata"}}]

        async def run():
            result = process._build_query_prompt("summarize", None, documents)
            messages = [msg async for msg in result]
            assert len(messages) == 1
            content = messages[0]["message"]["content"]
            assert len(content) == 2
            assert content[0] == documents[0]
            assert content[1] == {"type": "text", "text": "summarize"}

        asyncio.run(run())

    def test_with_images_and_documents(self):
        """_build_query_prompt() includes images first, then documents, then text."""
        process = _make_claude_process()
        images = [{"type": "image", "source": {"data": "img1"}}]
        documents = [{"type": "document", "source": {"data": "doc1"}}]

        async def run():
            result = process._build_query_prompt("analyze", images, documents)
            messages = [msg async for msg in result]
            assert len(messages) == 1
            content = messages[0]["message"]["content"]
            assert len(content) == 3
            assert content[0] == images[0]
            assert content[1] == documents[0]
            assert content[2] == {"type": "text", "text": "analyze"}

        asyncio.run(run())

    def test_empty_images_list_treated_as_no_images(self):
        """_build_query_prompt() with an empty images list only produces the text block."""
        process = _make_claude_process()

        async def run():
            result = process._build_query_prompt("hello", [], None)
            messages = [msg async for msg in result]
            content = messages[0]["message"]["content"]
            assert len(content) == 1
            assert content[0] == {"type": "text", "text": "hello"}

        asyncio.run(run())


# =============================================================================
# ProcessManager pending request tests (Task 4)
# =============================================================================


from twicc.agent.manager import ProcessManager


def _make_manager_with_process(
    session_id: str = "session-1",
    state: ProcessState = ProcessState.ASSISTANT_TURN,
    pending_request: PendingRequest | None = None,
    last_activity: float | None = None,
    state_changed_at: float | None = None,
) -> tuple[ProcessManager, ClaudeProcess]:
    """Create a ProcessManager with a single mock process injected directly.

    Returns the manager and the process for further test manipulation.
    """
    manager = ProcessManager()
    process = ClaudeProcess(session_id, "test-project", "/tmp/test")
    process.state = state
    process._state_change_callback = AsyncMock()
    if pending_request is not None:
        process._pending_request = pending_request
    if last_activity is not None:
        process.last_activity = last_activity
    if state_changed_at is not None:
        process.state_changed_at = state_changed_at
    manager._processes[session_id] = process
    return manager, process


class TestManagerResolvePendingRequest:
    """Tests for ProcessManager.resolve_pending_request()."""

    def test_routes_to_correct_process(self):
        """resolve_pending_request() finds the process and calls its resolve method."""

        async def run():
            manager, process = _make_manager_with_process(
                pending_request=_make_pending_request(),
            )
            loop = asyncio.get_event_loop()
            process._pending_request_future = loop.create_future()

            response = PermissionResultAllow(updated_input={"command": "ls"})
            result = await manager.resolve_pending_request("session-1", response)

            assert result is True
            assert process._pending_request_future.done()
            assert process._pending_request_future.result() is response

        asyncio.run(run())

    def test_routes_deny_response(self):
        """resolve_pending_request() correctly routes a deny response."""

        async def run():
            manager, process = _make_manager_with_process(
                pending_request=_make_pending_request(),
            )
            loop = asyncio.get_event_loop()
            process._pending_request_future = loop.create_future()

            response = PermissionResultDeny(message="not allowed")
            result = await manager.resolve_pending_request("session-1", response)

            assert result is True
            assert process._pending_request_future.result() is response

        asyncio.run(run())

    def test_returns_false_for_unknown_session(self):
        """resolve_pending_request() returns False for a session_id not in _processes."""

        async def run():
            manager = ProcessManager()
            response = PermissionResultAllow(updated_input={})
            result = await manager.resolve_pending_request("nonexistent", response)

            assert result is False

        asyncio.run(run())

    def test_returns_false_when_process_has_no_pending_request(self):
        """resolve_pending_request() returns False when the process has no pending Future."""

        async def run():
            manager, _process = _make_manager_with_process()
            # No pending request set

            response = PermissionResultAllow(updated_input={})
            result = await manager.resolve_pending_request("session-1", response)

            assert result is False

        asyncio.run(run())

    def test_routes_to_correct_process_among_multiple(self):
        """resolve_pending_request() routes to the correct process when multiple exist."""

        async def run():
            manager = ProcessManager()

            # Process 1: no pending request
            process1 = ClaudeProcess("session-1", "project-1", "/tmp/test")
            process1.state = ProcessState.ASSISTANT_TURN
            process1._state_change_callback = AsyncMock()
            manager._processes["session-1"] = process1

            # Process 2: has pending request
            process2 = ClaudeProcess("session-2", "project-1", "/tmp/test")
            process2.state = ProcessState.ASSISTANT_TURN
            process2._state_change_callback = AsyncMock()
            process2._pending_request = _make_pending_request()
            loop = asyncio.get_event_loop()
            process2._pending_request_future = loop.create_future()
            manager._processes["session-2"] = process2

            response = PermissionResultAllow(updated_input={"command": "echo ok"})
            result = await manager.resolve_pending_request("session-2", response)

            assert result is True
            assert process2._pending_request_future.done()
            assert process2._pending_request_future.result() is response
            # Process 1 should be unaffected
            assert process1._pending_request is None

        asyncio.run(run())


class TestTimeoutExemptionForPendingRequest:
    """Tests that check_and_stop_timed_out_processes() skips processes with pending requests."""

    def test_process_with_pending_request_not_killed_in_assistant_turn(self):
        """A process in ASSISTANT_TURN with a pending request is not killed by timeout."""

        async def run():
            far_past = 1000.0  # Well past any timeout
            manager, process = _make_manager_with_process(
                state=ProcessState.ASSISTANT_TURN,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            killed = await manager.check_and_stop_timed_out_processes()

            assert killed == []
            assert process.state == ProcessState.ASSISTANT_TURN

        asyncio.run(run())

    def test_process_with_pending_request_not_killed_in_user_turn(self):
        """A process in USER_TURN with a pending request is not killed by timeout.

        This is an unlikely scenario (pending requests happen during ASSISTANT_TURN)
        but the exemption is based on the pending_request field, not the state.
        """

        async def run():
            far_past = 1000.0
            manager, process = _make_manager_with_process(
                state=ProcessState.USER_TURN,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            killed = await manager.check_and_stop_timed_out_processes()

            assert killed == []
            assert process.state == ProcessState.USER_TURN

        asyncio.run(run())

    def test_process_without_pending_request_is_killed_normally(self):
        """A process in ASSISTANT_TURN without a pending request is killed after timeout."""

        async def run():
            far_past = 1000.0
            manager, process = _make_manager_with_process(
                state=ProcessState.ASSISTANT_TURN,
                last_activity=far_past,
                state_changed_at=far_past,
            )

            killed = await manager.check_and_stop_timed_out_processes()

            assert killed == ["session-1"]
            assert process.state == ProcessState.DEAD

        asyncio.run(run())

    def test_mixed_processes_only_non_pending_killed(self):
        """Only processes without pending requests are killed; those with are spared."""

        async def run():
            far_past = 1000.0
            manager = ProcessManager()

            # Process 1: has pending request, should survive
            process1 = ClaudeProcess("session-1", "project-1", "/tmp/test")
            process1.state = ProcessState.ASSISTANT_TURN
            process1._state_change_callback = AsyncMock()
            process1._pending_request = _make_pending_request()
            process1.last_activity = far_past
            process1.state_changed_at = far_past
            manager._processes["session-1"] = process1

            # Process 2: no pending request, should be killed
            process2 = ClaudeProcess("session-2", "project-1", "/tmp/test")
            process2.state = ProcessState.ASSISTANT_TURN
            process2._state_change_callback = AsyncMock()
            process2.last_activity = far_past
            process2.state_changed_at = far_past
            manager._processes["session-2"] = process2

            killed = await manager.check_and_stop_timed_out_processes()

            assert "session-2" in killed
            assert "session-1" not in killed
            assert process1.state == ProcessState.ASSISTANT_TURN
            assert process2.state == ProcessState.DEAD

        asyncio.run(run())

    def test_starting_process_with_pending_request_not_killed(self):
        """A process in STARTING state with a pending request is not killed."""

        async def run():
            far_past = 1000.0
            manager, process = _make_manager_with_process(
                state=ProcessState.STARTING,
                pending_request=_make_pending_request(),
                last_activity=far_past,
                state_changed_at=far_past,
            )

            killed = await manager.check_and_stop_timed_out_processes()

            assert killed == []
            assert process.state == ProcessState.STARTING

        asyncio.run(run())


# =============================================================================
# WebSocket handler _handle_pending_request_response tests (Task 5)
# =============================================================================


from unittest.mock import patch


class _FakeConsumer:
    """Minimal stand-in for UpdatesConsumer, sufficient to call _handle_pending_request_response.

    Avoids instantiating the real Django Channels consumer (which needs
    a channel layer, scope, etc.) while exercising the actual handler method.
    """

    def __init__(self):
        from twicc.asgi import UpdatesConsumer
        self._handle_pending_request_response = (
            UpdatesConsumer._handle_pending_request_response.__get__(self, type(self))
        )


class TestHandlePendingRequestResponseToolApproval:
    """Tests for _handle_pending_request_response with tool_approval request type."""

    def test_allow_resolves_pending_request(self):
        """An 'allow' decision resolves the Future with PermissionResultAllow."""

        async def run():
            manager, process = _make_manager_with_process(
                session_id="session-A",
                pending_request=_make_pending_request(
                    tool_name="Bash",
                    tool_input={"command": "echo hello"},
                ),
            )
            loop = asyncio.get_event_loop()
            process._pending_request_future = loop.create_future()

            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-A",
                    "request_id": "req-123",
                    "request_type": "tool_approval",
                    "decision": "allow",
                    "updated_input": {"command": "echo hello"},
                })

            assert process._pending_request_future.done()
            result = process._pending_request_future.result()
            assert isinstance(result, PermissionResultAllow)
            assert result.updated_input == {"command": "echo hello"}

        asyncio.run(run())

    def test_allow_without_updated_input(self):
        """An 'allow' decision without updated_input passes None."""

        async def run():
            manager, process = _make_manager_with_process(
                session_id="session-A",
                pending_request=_make_pending_request(),
            )
            loop = asyncio.get_event_loop()
            process._pending_request_future = loop.create_future()

            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-A",
                    "request_id": "req-123",
                    "request_type": "tool_approval",
                    "decision": "allow",
                })

            result = process._pending_request_future.result()
            assert isinstance(result, PermissionResultAllow)
            assert result.updated_input is None

        asyncio.run(run())

    def test_deny_resolves_with_permission_result_deny(self):
        """A 'deny' decision resolves the Future with PermissionResultDeny."""

        async def run():
            manager, process = _make_manager_with_process(
                session_id="session-B",
                pending_request=_make_pending_request(),
            )
            loop = asyncio.get_event_loop()
            process._pending_request_future = loop.create_future()

            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-B",
                    "request_id": "req-123",
                    "request_type": "tool_approval",
                    "decision": "deny",
                    "message": "Too dangerous",
                })

            result = process._pending_request_future.result()
            assert isinstance(result, PermissionResultDeny)
            assert result.message == "Too dangerous"

        asyncio.run(run())

    def test_deny_uses_default_message(self):
        """A 'deny' decision without a message uses the default reason."""

        async def run():
            manager, process = _make_manager_with_process(
                session_id="session-B",
                pending_request=_make_pending_request(),
            )
            loop = asyncio.get_event_loop()
            process._pending_request_future = loop.create_future()

            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-B",
                    "request_id": "req-123",
                    "request_type": "tool_approval",
                    "decision": "deny",
                })

            result = process._pending_request_future.result()
            assert isinstance(result, PermissionResultDeny)
            assert result.message == "User denied this action"

        asyncio.run(run())


class TestHandlePendingRequestResponseAskUserQuestion:
    """Tests for _handle_pending_request_response with ask_user_question request type."""

    def test_answers_resolve_with_original_questions(self):
        """ask_user_question responses include the original questions alongside answers."""
        questions = [
            {
                "question": "How should I format?",
                "header": "Format",
                "options": [{"label": "JSON"}, {"label": "CSV"}],
                "multiSelect": False,
            }
        ]

        async def run():
            manager, process = _make_manager_with_process(
                session_id="session-C",
                pending_request=_make_pending_request(
                    request_type="ask_user_question",
                    tool_name="AskUserQuestion",
                    tool_input={"questions": questions},
                ),
            )
            loop = asyncio.get_event_loop()
            process._pending_request_future = loop.create_future()

            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-C",
                    "request_id": "req-456",
                    "request_type": "ask_user_question",
                    "answers": {"How should I format?": "JSON"},
                })

            result = process._pending_request_future.result()
            assert isinstance(result, PermissionResultAllow)
            assert result.updated_input["questions"] == questions
            assert result.updated_input["answers"] == {"How should I format?": "JSON"}

        asyncio.run(run())

    def test_multiple_questions_and_answers(self):
        """Multiple questions map to multiple answers in the response."""
        questions = [
            {
                "question": "Output format?",
                "header": "Format",
                "options": [{"label": "JSON"}, {"label": "CSV"}],
                "multiSelect": False,
            },
            {
                "question": "Include headers?",
                "header": "Headers",
                "options": [{"label": "Yes"}, {"label": "No"}],
                "multiSelect": False,
            },
        ]

        async def run():
            manager, process = _make_manager_with_process(
                session_id="session-D",
                pending_request=_make_pending_request(
                    request_type="ask_user_question",
                    tool_name="AskUserQuestion",
                    tool_input={"questions": questions},
                ),
            )
            loop = asyncio.get_event_loop()
            process._pending_request_future = loop.create_future()

            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-D",
                    "request_id": "req-789",
                    "request_type": "ask_user_question",
                    "answers": {
                        "Output format?": "CSV",
                        "Include headers?": "Yes",
                    },
                })

            result = process._pending_request_future.result()
            assert result.updated_input["questions"] == questions
            assert result.updated_input["answers"] == {
                "Output format?": "CSV",
                "Include headers?": "Yes",
            }

        asyncio.run(run())

    def test_no_pending_request_does_not_resolve(self):
        """ask_user_question with no pending request on the process does nothing."""

        async def run():
            manager, process = _make_manager_with_process(
                session_id="session-E",
                # No pending request set
            )

            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                # Should not raise
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-E",
                    "request_id": "req-000",
                    "request_type": "ask_user_question",
                    "answers": {"question": "answer"},
                })

            # Process should have no Future set
            assert process._pending_request_future is None

        asyncio.run(run())


class TestHandlePendingRequestResponseEdgeCases:
    """Tests for edge cases in _handle_pending_request_response."""

    def test_missing_session_id_returns_early(self):
        """Missing session_id causes the handler to return early without errors."""

        async def run():
            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager") as mock_manager:
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "request_type": "tool_approval",
                    "decision": "allow",
                })
                # Manager should not be called at all
                mock_manager.assert_not_called()

        asyncio.run(run())

    def test_missing_request_type_returns_early(self):
        """Missing request_type causes the handler to return early without errors."""

        async def run():
            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager") as mock_manager:
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-X",
                    "decision": "allow",
                })
                mock_manager.assert_not_called()

        asyncio.run(run())

    def test_unknown_request_type_returns_early(self):
        """Unknown request_type causes the handler to return early."""

        async def run():
            manager = ProcessManager()
            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                # Should not raise
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-X",
                    "request_id": "req-X",
                    "request_type": "unknown_type",
                })

        asyncio.run(run())

    def test_unknown_session_does_not_raise(self):
        """Resolving for a non-existent session does not raise."""

        async def run():
            manager = ProcessManager()
            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                # Should not raise
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "nonexistent-session",
                    "request_id": "req-X",
                    "request_type": "tool_approval",
                    "decision": "allow",
                })

        asyncio.run(run())

    def test_already_resolved_future_does_not_raise(self):
        """Sending a response when the Future is already resolved does not raise."""

        async def run():
            manager, process = _make_manager_with_process(
                session_id="session-F",
                pending_request=_make_pending_request(),
            )
            loop = asyncio.get_event_loop()
            future = loop.create_future()
            future.set_result(PermissionResultAllow(updated_input={}))
            process._pending_request_future = future

            consumer = _FakeConsumer()

            with patch("twicc.asgi.get_process_manager", return_value=manager):
                # Should not raise
                await consumer._handle_pending_request_response({
                    "type": "pending_request_response",
                    "session_id": "session-F",
                    "request_id": "req-F",
                    "request_type": "tool_approval",
                    "decision": "deny",
                    "message": "Too late",
                })

            # Original result should be unchanged
            assert isinstance(future.result(), PermissionResultAllow)

        asyncio.run(run())
