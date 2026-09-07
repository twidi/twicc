"""Real Claude SDK control paths against an isolated, local Anthropic endpoint."""

import asyncio
import base64
import os
import struct
import threading
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

import orjson
import pytest
from claude_agent_sdk import PermissionResultAllow

from twicc.agent import AgentState
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.helpers import AgentSettings

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("TWICC_CLAUDE_INTEGRATION") != "1", reason="requires local Claude runtime opt-in"
    ),
    pytest.mark.django_db(transaction=True),
]


@pytest.mark.parametrize("mode", ["image", "approval", "stop", "error"])
def test_claude_ephemeral_sdk_scenario(tmp_path, monkeypatch, caplog, mode):
    home = tmp_path / "home"
    home.mkdir()
    cwd = home / "project"
    cwd.mkdir()
    output_path = home / "approved.txt"
    requests = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = orjson.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "count_tokens" in self.path:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"input_tokens":10}')
                return
            requests.append(body)
            if mode == "stop":
                release.wait(15)
            if mode == "error":
                error = {
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": "LOCAL_CLAUDE_FAILURE_MARKER"},
                }
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(orjson.dumps(error))
                return
            messages = body.get("messages", [])
            tool_done = any(
                isinstance(m.get("content"), list) and any(c.get("type") == "tool_result" for c in m["content"])
                for m in messages
            )
            if mode == "approval" and not tool_done:
                content = [
                    {
                        "type": "tool_use",
                        "id": "toolu_approval",
                        "name": "Write",
                        "input": {"file_path": str(output_path), "content": "APPROVED_FILE_MARKER"},
                    }
                ]
                stop = "tool_use"
            else:
                content = [{"type": "text", "text": "CLAUDE_CONTROL_FINAL"}]
                stop = "end_turn"
            result = {
                "id": "msg_" + uuid.uuid4().hex,
                "type": "message",
                "role": "assistant",
                "model": body.get("model", "claude-sonnet-4-6"),
                "content": content,
                "stop_reason": stop,
                "stop_sequence": None,
                "usage": {"input_tokens": 20, "output_tokens": 10},
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
            self.end_headers()
            try:
                if not body.get("stream"):
                    self.wfile.write(orjson.dumps(result))
                    return

                def event(kind, data):
                    self.wfile.write(b"event: " + kind.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n")

                event("message_start", {"type": "message_start", "message": dict(result, content=[], stop_reason=None)})
                for index, item in enumerate(content):
                    block = dict(item, input={}) if item["type"] == "tool_use" else {"type": "text", "text": ""}
                    event(
                        "content_block_start", {"type": "content_block_start", "index": index, "content_block": block}
                    )
                    delta = (
                        {"type": "input_json_delta", "partial_json": orjson.dumps(item["input"]).decode()}
                        if item["type"] == "tool_use"
                        else {"type": "text_delta", "text": item["text"]}
                    )
                    event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
                    event("content_block_stop", {"type": "content_block_stop", "index": index})
                event(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": stop, "stop_sequence": None},
                        "usage": {"output_tokens": 10},
                    },
                )
                event("message_stop", {"type": "message_stop"})
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    from twicc import provider_homes
    from twicc.core.services import trust
    from twicc.providers.claude_code import sessions_watcher

    monkeypatch.setattr(
        provider_homes,
        "provider_env_overlay",
        lambda: {
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(home / "config"),
            "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(home / "secure"),
            "ANTHROPIC_API_KEY": "local-test-key",
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
            "CLAUDECODE": "",
        },
    )
    monkeypatch.setattr(trust, "project_is_untrusted", lambda project_id: False)
    monkeypatch.setattr(
        sessions_watcher, "get_watcher", lambda: type("Watcher", (), {"request_fast_poll": lambda self: None})()
    )

    async def run():
        agent = ClaudeCodeAgent(
            str(uuid.uuid4()),
            "local-test",
            str(cwd),
            AgentSettings(permission_mode="default"),
            AsyncMock(),
            AsyncMock(),
            AsyncMock(),
            ephemeral=True,
        )
        agent._reconcile_context = AsyncMock()
        agent._seed_context_baseline = AsyncMock()
        agent._broadcast_stream_event = AsyncMock()
        agent._broadcast_process_tools = AsyncMock()
        settled = asyncio.Event()

        async def state_change(current):
            if current.state in (AgentState.USER_TURN, AgentState.DEAD):
                settled.set()

        images = None
        if mode == "image":

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
        try:
            await agent.start("Run the controlled local scenario.", state_change, resume=False, images=images)
            if mode == "approval":
                while not agent._pending_requests:
                    assert not settled.is_set(), agent.error or requests
                    await asyncio.sleep(0.02)
                request_id = next(iter(agent._pending_requests))
                assert agent._pending_requests[request_id].tool_name == "Write"
                assert agent.resolve_pending_request(request_id, PermissionResultAllow())
            if mode == "stop":
                while not requests:
                    assert not settled.is_set(), agent.error
                    await asyncio.sleep(0.02)
                await agent.interrupt_or_kill("manual")
                release.set()
                assert agent.state == AgentState.DEAD
                assert agent.kill_reason == "manual"
            else:
                await settled.wait()
                if mode == "error":
                    assert agent.state == AgentState.DEAD
                    assert "LOCAL_CLAUDE_FAILURE_MARKER" in agent.error
                    assert "LOCAL_CLAUDE_FAILURE_MARKER" not in caplog.text
                else:
                    assert agent.error is None
                    assert agent.ephemeral_final_text == "CLAUDE_CONTROL_FINAL"
                    assert agent.ephemeral_usage["duration_ms"] is not None
            if mode == "approval":
                assert output_path.read_text() == "APPROVED_FILE_MARKER"
            if mode == "image":
                assert any(
                    c.get("type") == "image"
                    for r in requests
                    for m in r.get("messages", [])
                    if isinstance(m.get("content"), list)
                    for c in m["content"]
                )
        finally:
            release.set()
            if agent.state != AgentState.DEAD:
                await agent.interrupt_or_kill("manual")
            await agent._shutdown_sdk_client()
        assert not list(home.rglob("*.jsonl"))

    try:
        asyncio.run(asyncio.wait_for(run(), 35))
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        server_thread.join()
