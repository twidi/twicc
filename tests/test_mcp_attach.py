"""MCP tools: attach keeps its schema, inline data reaches the services, logs stay small (§4.7)."""

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest
from mcp import types as mcp_types

from twicc.cli._drop_request import whoami
from twicc.core.models import Project, Session
from twicc.core.services import send_message as send_message_service
from twicc.core.services.attachments import inline, staging
from twicc.core.services.send_message import SendMessageResult
from twicc.mcp import server as mcp_server
from twicc.mcp.identity import ExternalCaller, external_caller
from twicc.mcp.tools import iter_mcp_tools

URI = "data:text/plain;name=n.txt;base64,aGk="


@contextlib.contextmanager
def _logs(name: str):
    logger = logging.getLogger(name)
    was_disabled, was_level = logger.disabled, logger.level
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.disabled = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.disabled = was_disabled
        logger.setLevel(was_level)


def test_the_body_cap_is_the_inline_request_cap():
    assert mcp_server.MAX_REQUEST_BODY_BYTES == inline.INLINE_MAX_REQUEST_BYTES == 72 * 1024 * 1024


@pytest.mark.parametrize("name", ["send_message", "send_messages", "create_session"])
def test_attach_stays_an_array_of_strings_with_the_limit_in_its_description(name):
    tool = next(tool for tool in iter_mcp_tools() if tool.name == name)
    attach = tool.input_schema["properties"]["attach"]
    assert (attach["type"], attach["items"]) == ("array", {"type": "string"})
    assert "50 MB" in attach["description"]
    assert "images" not in tool.input_schema["properties"]
    assert "documents" not in tool.input_schema["properties"]


def test_the_instructions_name_the_forms_and_the_limit():
    assert "data URI" in mcp_server.INSTRUCTIONS
    assert "50 MB" in mcp_server.INSTRUCTIONS
    assert "name=" in mcp_server.EXTERNAL_INSTRUCTIONS
    assert "50 MB" in mcp_server.EXTERNAL_INSTRUCTIONS
    assert "file storage service" in mcp_server.EXTERNAL_INSTRUCTIONS


# Claude Code keeps only the first 2048 characters of a server's instructions. The internal
# instructions are already longer (2378 characters at f6e4b342): the batch lines from
# "A timeout/cancellation ..." on are cut today. The attach addition must not push out the
# batch lines that still fit, nor fall outside the cut itself.
CLIENT_INSTRUCTIONS_CUT = 2048
BATCH_LINES_IN_THE_CUT = 6  # "Batch tools are MCP-only wrappers." ... "No rollback, nested batches, ..."


def test_the_attach_line_does_not_push_the_batch_rules_out_of_the_client_cut():
    kept = mcp_server.INSTRUCTIONS[:CLIENT_INSTRUCTIONS_CUT]
    assert "data URI" in kept and "50 MB" in kept
    batch_lines = mcp_server.BATCH_INSTRUCTIONS.strip().splitlines()
    assert batch_lines[BATCH_LINES_IN_THE_CUT - 1].startswith("No rollback")
    for line in batch_lines[:BATCH_LINES_IN_THE_CUT]:
        assert line in kept, line
    # The attach bullet adds at most 80 characters to today's 2378.
    assert len(mcp_server.INSTRUCTIONS) <= 2378 + 80


def test_the_external_instructions_fit_in_the_client_cut():
    external = mcp_server.EXTERNAL_INSTRUCTIONS + mcp_server.BATCH_INSTRUCTIONS
    assert len(external) <= CLIENT_INSTRUCTIONS_CUT


@pytest.mark.parametrize("name", ["send_message", "send_messages", "create_session"])
def test_the_attach_description_carries_the_hint_the_instructions_point_to(name):
    tool = next(tool for tool in iter_mcp_tools() if tool.name == name)
    assert inline.INLINE_TOO_LARGE_HINT in tool.input_schema["properties"]["attach"]["description"]


@pytest.fixture
def mcp_session(transactional_db, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(data))
    project = Project.objects.create(id="mcp-project", directory=str(tmp_path))
    Session.objects.create(id="mcp-session", project=project, provider="claude_code", file_path="m.jsonl")
    seen: list[dict] = []

    async def fake_service(payload, *, release_refs_on_outcome=False):
        seen.append(payload)
        return SendMessageResult(True, "mcp-session", "claude_code", "mcp-project", None, {"last_line": 0})

    monkeypatch.setattr(send_message_service, "send_message_to_session_from_payload", fake_service)
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    return seen


def test_an_internal_call_with_a_data_uri_stages_and_reaches_the_service(mcp_session):
    result = asyncio.run(mcp_server.dispatch_tool(
        "send_message", {"session_id": "mcp-session", "prompt": "hi", "attach": [URI]}, session_id=None,
    ))
    assert result["exit_code"] == 0, result
    [ref] = [staging.validate_ref(item) for item in mcp_session[0]["attachments"]]
    assert ref.bucket.startswith("api-")
    assert staging.load_entry(ref).filename == "n.txt"


def test_an_external_call_with_a_data_uri_audits_no_argument(mcp_session, monkeypatch):
    audits: list = []

    async def write(fn):
        audits.append(fn)

    monkeypatch.setattr("twicc.mcp.oauth.storage.write", write)

    async def scenario():
        token = external_caller.set(ExternalCaller("connection", "Client"))
        try:
            return await mcp_server.dispatch_tool(
                "send_message", {"session_id": "mcp-session", "prompt": "hi", "attach": [URI]}, session_id=None,
            )
        finally:
            external_caller.reset(token)

    result = asyncio.run(scenario())
    assert result["exit_code"] == 0, result
    assert len(audits) == 1
    assert mcp_session[0]["attachments"]


def test_the_log_line_of_a_failing_call_has_no_base64(monkeypatch):
    async def explode(prepared, *, session_id, on_start=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_server, "execute_prepared", explode)
    data = "A" * 4000
    params = mcp_types.CallToolRequestParams(
        name="send_message",
        arguments={"session_id": "s", "prompt": "hi", "attach": [f"data:image/png;base64,{data}"]},
    )
    with _logs("twicc.mcp.server") as records:
        result = asyncio.run(mcp_server._call_tool(SimpleNamespace(request=None), params))
    assert result.is_error
    text = " ".join(record.getMessage() for record in records)
    assert data not in text
    assert "chars>" in text
