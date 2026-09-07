"""Real bundled Claude CLI, local model endpoint, isolated homes; no account required."""

import os
import subprocess
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import orjson
import uuid

import pytest
import claude_agent_sdk

pytestmark = pytest.mark.skipif(
    os.environ.get("TWICC_CLAUDE_INTEGRATION") != "1",
    reason="Set TWICC_CLAUDE_INTEGRATION=1 to run the bundled CLI",
)


def test_parent_and_child_transcripts_are_suppressed(tmp_path, request):
    binary = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    assert binary.exists()
    root = tmp_path / "runtime"
    root.mkdir(exist_ok=True)
    requests = []

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
            all_text = orjson.dumps(messages).decode()
            has_tool_result = any(
                isinstance(m.get("content"), list) and any(c.get("type") == "tool_result" for c in m["content"])
                for m in messages
            )
            is_child = "CHILD_MARKER" in all_text and not has_tool_result
            # First parent request dispatches an actual provider-created Agent.
            if "PARENT_MARKER" in all_text and not has_tool_result and not is_child:
                tool = next((t["name"] for t in body.get("tools", []) if t["name"] in ("Agent", "Task")), None)
                content = (
                    [
                        {
                            "type": "tool_use",
                            "id": "toolu_local_child",
                            "name": tool,
                            "input": {
                                "description": "Verify child output",
                                "prompt": "CHILD_MARKER: reply with child result",
                                "subagent_type": "general-purpose",
                            },
                        }
                    ]
                    if tool
                    else [{"type": "text", "text": "NO_AGENT_TOOL"}]
                )
                stop = "tool_use" if tool else "end_turn"
            else:
                content = [{"type": "text", "text": "CHILD_RESULT" if is_child else "PARENT_FINAL_RESULT"}]
                stop = "end_turn"
            result = {
                "id": "msg_" + uuid.uuid4().hex,
                "type": "message",
                "role": "assistant",
                "model": body.get("model", "claude-sonnet-4-6"),
                "content": content,
                "stop_reason": stop,
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
            self.end_headers()
            if not body.get("stream"):
                self.wfile.write(orjson.dumps(result))
                return

            def event(kind, data):
                self.wfile.write(b"event: " + kind.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n")

            event("message_start", {"type": "message_start", "message": dict(result, content=[], stop_reason=None)})
            for i, c in enumerate(content):
                if c["type"] == "text":
                    event(
                        "content_block_start",
                        {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                    )
                    event(
                        "content_block_delta",
                        {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": c["text"]}},
                    )
                else:
                    event(
                        "content_block_start",
                        {"type": "content_block_start", "index": i, "content_block": dict(c, input={})},
                    )
                    event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": i,
                            "delta": {"type": "input_json_delta", "partial_json": orjson.dumps(c["input"]).decode()},
                        },
                    )
                event("content_block_stop", {"type": "content_block_stop", "index": i})
            event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop, "stop_sequence": None},
                    "usage": {"output_tokens": 20},
                },
            )
            event("message_stop", {"type": "message_stop"})
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    request.addfinalizer(server.server_close)
    request.addfinalizer(server.shutdown)
    for ephemeral in (True, False):
        home = root / ("ephemeral" if ephemeral else "persistent")
        home.mkdir(exist_ok=True)
        cwd = home / "project"
        cwd.mkdir(exist_ok=True)
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(home / "config"),
            "ANTHROPIC_API_KEY": "local-test-key",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
        }
        cmd = [
            str(binary),
            "-p",
            "PARENT_MARKER: use an Agent, then give final result",
            "--output-format",
            "stream-json",
            "--verbose",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            "Agent",
            "--allowedTools",
            "Agent",
            "--permission-mode",
            "bypassPermissions",
            "--max-turns",
            "4",
        ]
        if ephemeral:
            cmd += ["--no-session-persistence"]
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=30)
        assert proc.returncode == 0, proc.stderr.decode()
        frames = [orjson.loads(line) for line in proc.stdout.splitlines() if line.startswith(b"{")]
        results = [frame for frame in frames if frame.get("type") == "result"]
        assert results and all(not result["is_error"] for result in results)
        assert results[-1]["result"] == "PARENT_FINAL_RESULT"
        assert results[-1]["subagent_stats"]["spawned"] == 1
        assert results[-1]["subagent_stats"]["completed"] == 1
        transcripts = list(home.rglob("*.jsonl"))
        if ephemeral:
            assert transcripts == []
        else:
            assert any("subagents" in path.parts for path in transcripts)
            assert any(path.parent.name != "subagents" for path in transcripts)
