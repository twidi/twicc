"""Real Codex tool exposure through a local Responses endpoint, without an account."""

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import orjson
import pytest
from openai_codex import CodexConfig

from twicc.providers.codex.agent.manager import _apply_request_user_input
from twicc.providers.codex.hermetic_catalog import bundled_catalog
from twicc.providers.codex.interactive_catalog import ensure_catalog
from twicc.providers.codex.runtime import codex_binary_path, is_runtime_ready
from twicc.providers.codex.sdk_wrappers import TwiccAsyncCodex

pytestmark = pytest.mark.skipif(
    os.environ.get("TWICC_CODEX_INTEGRATION") != "1" or not is_runtime_ready(),
    reason="requires TWICC_CODEX_INTEGRATION=1 and the downloaded Codex runtime",
)


def _catalog_models():
    if os.environ.get("TWICC_CODEX_INTEGRATION") != "1" or not is_runtime_ready():
        return ["integration-disabled"]
    _, source = bundled_catalog(codex_binary_path())
    return [entry["slug"] for entry in source["models"] if any(
        name in entry["experimental_supported_tools"]
        for name in ("request_user_input_async", "send_user_message_async")
    )]


@pytest.mark.parametrize("model", _catalog_models())
@pytest.mark.parametrize(("filtered", "scenario"), [
    (False, "start"), (True, "start"), (True, "resume"), (True, "subagent"),
])
def test_real_runtime_keeps_sync_questions_and_filters_only_async_questions(tmp_path, model, filtered, scenario):
    requests = []
    root_id = None
    child_requested = threading.Event()

    def request_thread_id(request):
        # Subagents share their parent's session_id for cache affinity; their
        # thread_id identifies the actual child. Roots can omit thread_id.
        metadata = request.get("client_metadata", {})
        return metadata.get("thread_id", metadata.get("session_id"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = orjson.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if request_thread_id(body) != root_id:
                child_requested.set()
            if scenario == "subagent" and len(requests) == 1:
                item = {
                    "type": "function_call", "id": "spawn", "call_id": "spawn",
                    "name": "spawn_agent", "namespace": "collaboration",
                    "arguments": orjson.dumps({
                        "task_name": "catalog_check", "fork_turns": "none", "message": "Return Done.",
                    }).decode(),
                }
            else:
                item = {
                    "type": "message", "role": "assistant", "id": f"answer-{len(requests)}",
                    "phase": "final_answer", "content": [{"type": "output_text", "text": "Done."}],
                }
            events = [
                {"type": "response.created", "response": {"id": "response-test"}},
                {"type": "response.output_item.done", "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "response-test",
                        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    },
                },
            ]
            payload = b"".join(b"data: " + orjson.dumps(event) + b"\n\n" for event in events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    binary = codex_binary_path()
    _, source = bundled_catalog(binary)
    catalog_path = ensure_catalog(binary, cache_dir=tmp_path / "cache")
    original_path = tmp_path / "original.json"
    original_path.write_bytes(orjson.dumps(source))
    home = tmp_path / "home"
    home.mkdir()
    codex_home = home / ".codex"
    codex_home.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    (codex_home / "config.toml").write_text(f"""
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
        nonlocal root_id
        path = catalog_path if filtered else original_path
        config = CodexConfig(
            codex_bin=str(binary), cwd=str(tmp_path),
            env={"HOME": str(home), "CODEX_HOME": str(codex_home)},
            config_overrides=(f"model_catalog_json={orjson.dumps(str(path)).decode()}",),
        )
        codex = TwiccAsyncCodex(config=config)
        try:
            thread_config = {}
            _apply_request_user_input(thread_config, enabled=True)
            thread_config["features"].update(multi_agent=True, multi_agent_v2=True)
            thread = await codex.thread_start_with_policy(
                model=model, cwd=str(tmp_path), ephemeral=scenario != "resume", config=thread_config,
            )
            root_id = thread.id
            turn = await thread.turn("Return Done.")
            async for event in turn.stream():
                assert event.method != "error", event.payload
            if scenario == "resume":
                # Restart the app-server, then read the durable rollout through
                # the same resume path used by TwiCC. No in-memory thread survives.
                await codex.close()
                codex = TwiccAsyncCodex(config=config)
                thread = await codex.thread_resume_with_policy(
                    root_id, cwd=str(tmp_path), config=thread_config,
                )
                turn = await thread.turn("Return Done again.")
                async for event in turn.stream():
                    assert event.method != "error", event.payload
            elif scenario == "subagent":
                assert await asyncio.to_thread(child_requested.wait, 10), [
                    {
                        "metadata": request.get("client_metadata"),
                        "outputs": [item for item in request.get("input", [])
                                    if item.get("type") in ("function_call_output", "custom_tool_call_output")],
                    }
                    for request in requests
                ]
        finally:
            await codex.close()

    try:
        asyncio.run(asyncio.wait_for(run(), 45))
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert requests

    def names(value):
        if isinstance(value, dict):
            if isinstance(value.get("name"), str):
                yield value["name"]
            for child in value.values():
                yield from names(child)
        elif isinstance(value, list):
            for child in value:
                yield from names(child)

    # Responses Lite puts AdditionalTools items in input; other models use
    # the top-level tools field. Read both wire formats without changing the model.
    for request in requests:
        exposed = set(names(request.get("tools", [])))
        for item in request.get("input", []):
            if "tools" in item:
                exposed.update(names(item["tools"]))
        if request_thread_id(request) == root_id:
            assert "request_user_input" in exposed
        assert ("request_user_input_async" in exposed) is (not filtered)
    if scenario == "resume":
        assert len(requests) == 2
    elif scenario == "subagent":
        assert any(request_thread_id(request) != root_id for request in requests)
    assert any(request_thread_id(request) == root_id for request in requests)
