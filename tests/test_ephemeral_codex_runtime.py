"""Local model endpoint verifies the bundled runtime's ephemeral transcript boundary."""

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

import orjson
import pytest
from openai_codex import CodexConfig

from twicc.providers.codex.runtime import codex_binary_path, is_runtime_ready
from twicc.providers.codex.sdk_wrappers import TwiccAsyncCodex
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.helpers import AgentSettings

pytestmark = pytest.mark.skipif(
    os.environ.get("TWICC_CODEX_INTEGRATION") != "1" or not is_runtime_ready(),
    reason="requires downloaded Codex runtime and explicit local integration opt-in",
)


@pytest.mark.parametrize("spawn_child", [False, True])
def test_runtime_ephemeral_transcript_and_final_answer(tmp_path, spawn_child):
    requests = []
    release_child = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = orjson.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if requests[0].get("client_metadata", {}).get("session_id") != body.get("client_metadata", {}).get(
                "session_id"
            ):
                release_child.wait(5)
            if spawn_child and len(requests) == 1:
                items = [
                    {
                        "type": "function_call",
                        "id": "call-spawn",
                        "call_id": "call-spawn",
                        "name": "spawn_agent",
                        "namespace": "collaboration",
                        "arguments": orjson.dumps(
                            {"fork_turns": "none", "task_name": "check", "message": "Return child result."}
                        ).decode(),
                    }
                ]
            else:
                items = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "id": f"m-{len(requests)}",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": "Verified final answer."}],
                    }
                ]
            events = [{"type": "response.created", "response": {"id": "r-test"}}]
            events.extend({"type": "response.output_item.done", "item": item} for item in items)
            events.append(
                {
                    "type": "response.completed",
                    "response": {"id": "r-test", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
                }
            )
            payload = b"".join(b"data: " + orjson.dumps(e) + b"\n\n" for e in events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / "home"
    home.mkdir()
    codex_home = home / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(f"""model="gpt-5.4"
model_provider="localtest"
[features]
plugins=false
multi_agent=true
multi_agent_v2=true
[model_providers.localtest]
name="Local test"
base_url="http://127.0.0.1:{server.server_port}/v1"
wire_api="responses"
requires_openai_auth=false
supports_websockets=false
""")

    async def run():
        codex = TwiccAsyncCodex(
            CodexConfig(
                codex_bin=str(codex_binary_path()),
                cwd=str(tmp_path),
                env={"HOME": str(home), "CODEX_HOME": str(codex_home)},
            )
        )
        try:
            session = await codex.thread_start_with_policy(cwd=str(tmp_path), ephemeral=True)
            agent = CodexAgent(
                session.id, "test", str(tmp_path), AgentSettings(), codex, session, ephemeral=True, work_dirs=[]
            )
            agent._broadcast_stream_event = AsyncMock()
            agent._broadcast_process_label = AsyncMock()
            agent._notify_state_change = AsyncMock()
            turn = await session.turn("Return the final answer after your work.")
            events = []
            async for event in turn.stream():
                events.append(event)
                await agent._handle_stream_event(event)
            assert agent.ephemeral_final_text == "Verified final answer."
            if spawn_child:
                assert agent._live_subagents, "No actual provider-created subagent"
                assert await agent._try_arm_subagent_hold() is True
                child_ids = list(agent._live_subagents)
                release_child.set()
                await asyncio.wait_for(agent._ephemeral_subagent_task, 5)
                from twicc.agent import AgentState

                assert agent.state == AgentState.USER_TURN
                assert len(requests) >= 3
                for child_id in child_ids:
                    child = await codex._client.thread_read(child_id)
                    assert child.thread.ephemeral is True
                    assert child.thread.path is None
            assert not list(codex_home.rglob("rollout-*.jsonl"))
            assert not list((codex_home / "sessions").rglob("*.jsonl"))
        finally:
            await codex.close()

    try:
        asyncio.run(asyncio.wait_for(run(), 30))
    finally:
        release_child.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("mode", ["attachment", "approval", "stop", "failure"])
def test_ephemeral_agent_runtime_controls(tmp_path, mode, caplog):
    """Use the real agent loop, real approval bridge, and local-only inference."""
    from twicc.agent import AgentState

    requests = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = orjson.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if mode == "stop":
                release.wait(10)
            if mode == "failure":
                payload = orjson.dumps({"error": {"message": "LOCAL_FAILURE_MARKER", "type": "invalid_request_error"}})
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if mode == "approval" and len(requests) == 1:
                item = {
                    "type": "function_call",
                    "id": "call-approval",
                    "call_id": "call-approval",
                    "name": "exec_command",
                    "arguments": orjson.dumps(
                        {
                            "cmd": "echo APPROVED_MARKER",
                            "sandbox_permissions": "require_escalated",
                            "justification": "Exercise the local approval bridge.",
                        }
                    ).decode(),
                }
            else:
                item = {
                    "type": "message",
                    "role": "assistant",
                    "id": "answer",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "CONTROL_FINAL_MARKER"}],
                }
            events = [
                {"type": "response.created", "response": {"id": "r-control"}},
                {"type": "response.output_item.done", "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r-control",
                        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    },
                },
            ]
            payload = b"".join(b"data: " + orjson.dumps(e) + b"\n\n" for e in events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    home = tmp_path / "home"
    home.mkdir()
    codex_home = home / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(f"""model="gpt-5.4"
model_provider="localtest"
[features]
plugins=false
[model_providers.localtest]
name="Local test"
base_url="http://127.0.0.1:{server.server_port}/v1"
wire_api="responses"
requires_openai_auth=false
supports_websockets=false
""")

    async def run():
        codex = TwiccAsyncCodex(
            CodexConfig(
                codex_bin=str(codex_binary_path()),
                cwd=str(tmp_path),
                env={"HOME": str(home), "CODEX_HOME": str(codex_home)},
            )
        )
        agent = None
        try:
            session = await codex.thread_start_with_policy(cwd=str(tmp_path), ephemeral=True)
            agent = CodexAgent(
                session.id,
                "test",
                str(tmp_path),
                AgentSettings(permission_mode="auto"),
                codex,
                session,
                ephemeral=True,
                work_dirs=[],
            )
            agent._notify_state_change = AsyncMock()
            agent._broadcast_stream_event = AsyncMock()
            agent._reconcile_context = AsyncMock()
            images = None
            if mode == "attachment":
                import base64, struct, zlib

                def chunk(kind, data):
                    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

                png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                png += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
                images = [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()},
                    }
                ]
            await agent.start("Exercise the requested local control.", AsyncMock(), resume=False, images=images)
            if mode == "approval":
                while not agent._pending_requests:
                    assert not agent._turn_task.done(), agent.error or requests
                    await asyncio.sleep(0.02)
                request_id = next(iter(agent._pending_requests))
                assert agent.resolve_pending_request(request_id, {"decision": "accept"}) is True
            if mode == "stop":
                while not requests:
                    await asyncio.sleep(0.02)
                await agent.interrupt_or_kill("manual")
                release.set()
                assert agent.state == AgentState.DEAD
                assert agent.kill_reason == "manual"
            else:
                await agent._turn_task
                if mode == "failure":
                    assert agent.state == AgentState.DEAD
                    assert "LOCAL_FAILURE_MARKER" in agent.error
                    assert "LOCAL_FAILURE_MARKER" not in caplog.text
                else:
                    assert agent.state == AgentState.USER_TURN
                    assert agent.ephemeral_final_text == "CONTROL_FINAL_MARKER"
            if mode == "approval":
                outputs = [
                    item.get("output", "")
                    for r in requests
                    for item in r.get("input", [])
                    if item.get("type") == "function_call_output"
                ]
                assert any("APPROVED_MARKER" in output for output in outputs)
                assert not agent._pending_requests
            if mode == "attachment":
                assert b"data:image/png;base64," in orjson.dumps(requests)
            assert not list(codex_home.rglob("rollout-*.jsonl"))
        finally:
            release.set()
            await codex.close()
            if agent and agent._turn_task and not agent._turn_task.done():
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)

    try:
        asyncio.run(asyncio.wait_for(run(), 20))
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        server_thread.join()
