"""Real bundled app-server + controlled local Responses server; no account or remote model.

Opt in with TWICC_CODEX_INTEGRATION=1. CODEX_HOME and HOME are temporary;
no user sessions, credentials, or running TwiCC servers are used.
"""

import asyncio
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

import orjson
import pytest
from openai_codex import CodexConfig

from twicc.agent import AgentState
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.runtime import codex_binary_path, is_runtime_ready
from twicc.providers.codex.sdk_wrappers import TwiccAsyncCodex
from twicc.providers.helpers import AgentSettings

pytestmark = pytest.mark.skipif(
    os.environ.get("TWICC_CODEX_INTEGRATION") != "1" or not is_runtime_ready(),
    reason="requires TWICC_CODEX_INTEGRATION=1 and the downloaded Codex runtime",
)


def test_real_goal_followup_reaches_model_across_physical_turns(tmp_path):
    requests = queue.Queue()
    gates = []
    auto_release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            gate = threading.Event()
            gates.append(gate)
            requests.put((body, gate))
            if auto_release.is_set():
                gate.set()
            if not gate.wait(15):
                return
            events = [
                {"type": "response.created", "response": {"id": "response-test"}},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "id": f"message-{len(gates)}",
                        "content": [{"type": "output_text", "text": "Progress recorded."}],
                    },
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "response-test",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        },
                    },
                },
            ]
            payload = b"".join(b"data: " + orjson.dumps(e) + b"\n\n" for e in events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    home = tmp_path / "home"
    home.mkdir()
    codex_home = home / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(f"""
model = "gpt-5.4"
model_provider = "localtest"
[features]
goals = true
[model_providers.localtest]
name = "Local test"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
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
            thread = await codex.thread_start_with_policy(cwd=str(tmp_path))
            agent = CodexAgent(thread.id, "test", str(tmp_path), AgentSettings(permission_mode="auto"), codex, thread)
            agent._broadcast_stream_event = AsyncMock()
            agent._notify_state_change = AsyncMock()
            agent._try_arm_subagent_hold = AsyncMock(return_value=False)
            await agent.run_goal_command("Keep checking progress until the user clears the goal")
            _, first_gate = await asyncio.to_thread(requests.get, True, 10)
            first_turn = agent._goal_monitor.state.current_turn()
            assert first_turn
            first_gate.set()
            _, second_gate = await asyncio.to_thread(requests.get, True, 10)
            second_turn = agent._goal_monitor.state.current_turn()
            assert second_turn and second_turn != first_turn
            assert await agent.send("FOLLOWUP_MARKER_739: focus on verification") is True
            second_gate.set()
            body, third_gate = await asyncio.to_thread(requests.get, True, 10)
            assert body.count(b"FOLLOWUP_MARKER_739") == 1
            # clear does not interrupt an in-flight model request.
            await agent.run_goal_command("clear")
            assert agent.state == AgentState.ASSISTANT_TURN
            auto_release.set()
            third_gate.set()
            await asyncio.wait_for(agent._turn_task, 10)
            assert agent.state == AgentState.USER_TURN
        finally:
            for gate in gates:
                gate.set()
            await codex.close()
            if agent and agent._turn_task:
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        for gate in gates:
            gate.set()
        server.shutdown()
        server.server_close()
        server_thread.join()
