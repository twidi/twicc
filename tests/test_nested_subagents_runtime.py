"""Real Claude nested spawning and SDK stop control against a local model."""

import asyncio
import dataclasses
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import orjson
import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


pytestmark = pytest.mark.skipif(
    os.environ.get("TWICC_CLAUDE_INTEGRATION") != "1",
    reason="Set TWICC_CLAUDE_INTEGRATION=1 for isolated bundled Claude runtime acceptance",
)


@pytest.mark.parametrize("stop_child", [False, True], ids=["complete", "sdk-stop"])
def test_real_nested_claude_tasks(tmp_path, stop_child):
    home = tmp_path / "home"
    cwd = home / "project"
    cwd.mkdir(parents=True)
    (home / "tmp").mkdir()
    requests = []
    frames = []
    release_async = threading.Event()
    async_entered = threading.Event()

    def tool(tool_id, prompt, background=False):
        return {"type": "tool_use", "id": tool_id, "name": "Agent", "input": {
            "description": prompt, "prompt": prompt, "subagent_type": "general-purpose",
            "run_in_background": background,
        }}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = orjson.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "count_tokens" in self.path:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"input_tokens":100}')
                return
            requests.append(body)
            messages = body.get("messages", [])
            # Agent prompts are the initial user message in each new context.
            first_user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            prompt = orjson.dumps(first_user).decode()
            tool_done = any(
                isinstance(m.get("content"), list)
                and any(c.get("type") == "tool_result" for c in m["content"])
                for m in messages
            )
            if "NESTED_ASYNC_MARKER" in prompt:
                async_entered.set()
                release_async.wait(30)
                content = [{"type": "text", "text": "NESTED_ASYNC_DONE"}]
            elif "NESTED_SYNC_MARKER" in prompt:
                content = [{"type": "text", "text": "NESTED_SYNC_DONE"}]
            elif "LAUNCHER_MARKER" in prompt and not tool_done:
                content = [
                    tool("toolu_nested_async", "NESTED_ASYNC_MARKER", True),
                    tool("toolu_nested_sync", "NESTED_SYNC_MARKER"),
                ]
            elif "ROOT_MARKER" in prompt and not tool_done:
                content = [tool("toolu_launcher", "LAUNCHER_MARKER")]
            else:
                content = [{"type": "text", "text": "LAUNCHER_DONE" if "LAUNCHER_MARKER" in prompt else "ROOT_DONE"}]
            stop = "tool_use" if content[0]["type"] == "tool_use" else "end_turn"
            result = {
                "id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
                "model": body.get("model", "claude-sonnet-4-6"), "content": content,
                "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 20},
            }
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
                self.end_headers()
                if not body.get("stream"):
                    self.wfile.write(orjson.dumps(result))
                    return

                def event(kind, data):
                    self.wfile.write(b"event: " + kind.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n")

                event("message_start", {"type": "message_start", "message": dict(result, content=[], stop_reason=None)})
                for index, item in enumerate(content):
                    block = dict(item, input={}) if item["type"] == "tool_use" else {"type": "text", "text": ""}
                    event("content_block_start", {"type": "content_block_start", "index": index, "content_block": block})
                    delta = (
                        {"type": "input_json_delta", "partial_json": orjson.dumps(item["input"]).decode()}
                        if item["type"] == "tool_use" else {"type": "text_delta", "text": item["text"]}
                    )
                    event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
                    event("content_block_stop", {"type": "content_block_stop", "index": index})
                event("message_delta", {"type": "message_delta", "delta": {
                    "stop_reason": stop, "stop_sequence": None,
                }, "usage": {"output_tokens": 20}})
                event("message_stop", {"type": "message_stop"})
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    options = ClaudeAgentOptions(
        cwd=cwd, tools=["Agent"], allowed_tools=["Agent"], permission_mode="bypassPermissions",
        setting_sources=[], strict_mcp_config=True, max_turns=8,
        env={
            "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / "config"),
            "TMPDIR": str(home / "tmp"),
            "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(home / "secure"),
            "ANTHROPIC_API_KEY": "local-test-key", "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENABLE_TELEMETRY": "0", "CLAUDECODE": "",
        },
    )

    async def run():
        async with ClaudeSDKClient(options=options) as client:
            async def consume():
                async for message in client.receive_messages():
                    frames.append(dataclasses.asdict(message))

            reader = asyncio.create_task(consume())
            try:
                await client.query("ROOT_MARKER: run the controlled nested scenario")
                while not async_entered.is_set():
                    if reader.done():
                        reader.result()
                    await asyncio.sleep(0.02)
                async_id = None
                while async_id is None:
                    for path in home.rglob("agent-*.meta.json"):
                        meta = orjson.loads(path.read_bytes())
                        if meta.get("toolUseId") == "toolu_nested_async":
                            assert meta.get("parentAgentId"), meta
                            assert meta.get("spawnDepth") == 2, meta
                            async_id = path.name.removeprefix("agent-").removesuffix(".meta.json")
                    await asyncio.sleep(0.02)
                # The launcher completes its own turn while its async child
                # remains blocked in the real model request.
                for tool_id in ("toolu_nested_sync", "toolu_launcher"):
                    while not any(
                        frame.get("subtype") == "task_notification"
                        and frame.get("data", {}).get("tool_use_id") == tool_id
                        and frame.get("data", {}).get("status") == "completed"
                        for frame in frames
                    ):
                        await asyncio.sleep(0.02)
                assert not any(
                    frame.get("subtype") == "task_notification"
                    and frame.get("data", {}).get("task_id") == async_id
                    for frame in frames
                )
                if stop_child:
                    await client.stop_task(async_id)
                else:
                    release_async.set()
                expected = "stopped" if stop_child else "completed"
                while not any(
                    frame.get("subtype") == "task_notification"
                    and frame.get("data", {}).get("task_id") == async_id
                    and frame.get("data", {}).get("status") == expected
                    for frame in frames
                ):
                    if reader.done():
                        reader.result()
                    await asyncio.sleep(0.02)
                metas = [orjson.loads(path.read_bytes()) for path in home.rglob("agent-*.meta.json")]
                while not any(meta.get("toolUseId") == "toolu_nested_sync" for meta in metas):
                    await asyncio.sleep(0.02)
                    metas = [orjson.loads(path.read_bytes()) for path in home.rglob("agent-*.meta.json")]
                assert any(meta.get("spawnDepth") == 2 and meta.get("toolUseId") == "toolu_nested_sync" for meta in metas)
                print(f"Nested Claude verdict: task={async_id}, SDK stop={stop_child}, status={expected}")
            finally:
                release_async.set()
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)

    try:
        asyncio.run(asyncio.wait_for(run(), 25))
    finally:
        release_async.set()
        (tmp_path / "requests.json").write_bytes(orjson.dumps(requests, option=orjson.OPT_INDENT_2))
        (tmp_path / "frames.json").write_bytes(orjson.dumps(frames, option=orjson.OPT_INDENT_2))
        server.shutdown()
        server.server_close()
        server_thread.join()
