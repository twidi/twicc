"""Gemini CLI provider: record reduction, classification, cost, file discovery, helpers.

Record shapes are copied from a real Gemini CLI 0.68 session file
(``~/.gemini/tmp/<slug>/chats/session-*.jsonl``), trimmed.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from twicc.core.enums import ItemKind, Provider
from twicc.providers.gemini import records
from twicc.providers.gemini.compute import GeminiSessionCompute, to_token_usage
from twicc.providers.gemini.helpers import extract_model_info
from twicc.providers.gemini.sessions_watcher import read_project_root, read_session_file_meta
from twicc.providers.helpers import get_provider_helpers

HEADER = {"sessionId": "8143b1d5-1c29-40b8-bcee-c563d6a03599", "projectHash": "bed6", "startTime": "2026-11-16T01:02:24.504Z",
          "lastUpdated": "2026-11-16T01:02:24.504Z", "kind": "main"}
CONTEXT = {"id": "d049", "timestamp": "2026-11-16T01:02:24.609Z", "type": "user",
           "content": [{"text": "<session_context>\nThis is the Gemini CLI.\n</session_context>"}]}
SET = {"$set": {"lastUpdated": "2026-11-16T01:02:24.611Z"}}
PROMPT = {"id": "f76c", "timestamp": "2026-11-16T01:02:27.945Z", "type": "user",
          "content": [{"text": "Read note.txt then create out.txt"}]}
TOKENS = {"input": 10049, "output": 20, "cached": 8000, "thoughts": 548, "tool": 0, "total": 10617}
THOUGHTS = [{"subject": "Defining", "description": "I read the file first.", "timestamp": "2026-11-16T01:04:38.685Z"},
            {"subject": "Clarifying", "description": "Then I write.", "timestamp": "2026-11-16T01:04:38.685Z"}]
THINKING = {"id": "d10c", "timestamp": "2026-11-16T01:04:38.685Z", "type": "gemini", "content": "",
            "thoughts": THOUGHTS, "tokens": TOKENS, "model": "gemini-3.8-flash"}
READ_CALL = {"id": "read_file__call_1", "name": "read_file", "args": {"file_path": "note.txt"},
             "result": [{"functionResponse": {"id": "read_file__call_1", "name": "read_file",
                                              "response": {"output": "hello\n"}}}],
             "status": "success", "timestamp": "2026-11-16T01:04:38.700Z", "displayName": "ReadFile"}
THINKING_WITH_CALL = {**THINKING, "toolCalls": [READ_CALL]}
ECHO = {"id": "c66e", "timestamp": "2026-11-16T01:04:38.712Z", "type": "user",
        "content": [{"functionResponse": {"id": "read_file__call_1", "name": "read_file", "response": {"output": "hello\n"}}}]}
INVOKE_CALL = {"id": "invoke_agent__call_1", "name": "invoke_agent",
               "args": {"agent_name": "codebase_investigator", "prompt": "Explain what calc.py does."},
               "result": [{"functionResponse": {"id": "invoke_agent__call_1", "name": "invoke_agent",
                                                "response": {"output": "Subagent 'codebase_investigator' finished."}}}],
               "status": "success", "timestamp": "2026-11-16T05:29:22.000Z"}
SUBAGENT_PROMPT = {"id": "84e4", "timestamp": "2026-11-16T05:29:08.369Z", "type": "user", "content": [{"text": (
    "Your task is to do a deep investigation of the codebase for the following user objective:\n"
    "<objective>\nExplain what calc.py does.\n</objective>\nReport back."
)}]}
ANSWER = {"id": "21aa", "timestamp": "2026-11-16T01:06:20.000Z", "type": "gemini", "content": "Done: out.txt says bye.",
          "thoughts": [], "tokens": {"input": 10632, "output": 27, "cached": 0, "thoughts": 0, "tool": 0, "total": 10659},
          "model": "gemini-3.8-flash"}


def _reduce_all(lines: list[dict]) -> list[dict]:
    tracker = records.RecordDeltaTracker()
    out = []
    for line in lines:
        line = copy.deepcopy(line)
        tracker.reduce(line)
        out.append(line)
    return out


# --- Record reduction --------------------------------------------------------


def test_repeated_record_keeps_only_what_it_adds():
    first, repeat = _reduce_all([THINKING, THINKING_WITH_CALL])
    assert first == THINKING
    assert repeat[records.REPEAT_KEY] is True
    assert "thoughts" not in repeat  # already shown by the first copy
    assert "tokens" not in repeat  # already counted
    assert [call["id"] for call in records.tool_calls(repeat)] == ["read_file__call_1"]


def test_running_tool_calls_wait_for_their_terminal_status():
    running = {**READ_CALL, "status": "executing", "result": None}
    first, done = _reduce_all([{**THINKING, "toolCalls": [running]}, THINKING_WITH_CALL])
    assert first["toolCalls"] == []
    assert [call["status"] for call in done["toolCalls"]] == ["success"]


def test_reduction_is_idempotent_on_stored_copies():
    lines = [HEADER, CONTEXT, SET, PROMPT, THINKING, THINKING_WITH_CALL, ECHO, ANSWER]
    once = _reduce_all(lines)
    twice = _reduce_all(once)
    assert twice == once


def test_empty_repeat_is_detected():
    first, repeat = _reduce_all([ANSWER, ANSWER])
    assert not records.is_empty_repeat(first)
    assert records.is_empty_repeat(repeat)


def test_user_text_ignores_context_and_tool_results():
    assert records.user_text(PROMPT) == "Read note.txt then create out.txt"
    assert records.user_text(CONTEXT) is None
    assert records.user_text(ECHO) is None
    assert records.is_tool_result_echo(ECHO)


# --- Compute -----------------------------------------------------------------


@pytest.fixture
def compute():
    instance = GeminiSessionCompute()
    instance.begin_session_compute("s")
    return instance


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (HEADER, ItemKind.SYSTEM),
        (SET, ItemKind.SYSTEM),
        (CONTEXT, ItemKind.SYSTEM),
        (PROMPT, ItemKind.USER_MESSAGE),
        (ECHO, ItemKind.SYSTEM),
        (THINKING, ItemKind.REASONING),
        ({**THINKING_WITH_CALL, "thoughts": []}, ItemKind.TOOL_USE),
        (ANSWER, ItemKind.ASSISTANT_MESSAGE),
        ({"id": "e1", "timestamp": "2026-11-16T01:00:00Z", "type": "error", "content": "Quota exceeded"},
         ItemKind.API_ERROR),
        ({"id": "i1", "timestamp": "2026-11-16T01:00:00Z", "type": "info", "content": "Switched model"},
         ItemKind.SYSTEM),
    ],
)
def test_compute_item_kind(compute, line, kind):
    assert compute.compute_item_kind(line) == kind


def test_repeat_of_a_thought_record_becomes_a_tool_use(compute):
    compute.transform_inline(copy.deepcopy(THINKING), session_id="s", line_num=5)
    repeat = copy.deepcopy(THINKING_WITH_CALL)
    assert compute.transform_inline(repeat, session_id="s", line_num=7) is not None
    assert compute.compute_item_kind(repeat) == ItemKind.TOOL_USE


def test_tool_links(compute):
    assert compute.extract_tool_use_entries(THINKING_WITH_CALL, session_id="s") == {"read_file__call_1": "read_file"}
    assert compute.is_tool_result_item(ECHO)
    info = compute.extract_tool_result_info(ECHO, session_id="s")
    assert (info.tool_use_id, info.is_error) == ("read_file__call_1", False)
    failed = copy.deepcopy(ECHO)
    failed["content"][0]["functionResponse"]["response"] = {"error": "File not found"}
    info = compute.extract_tool_result_info(failed, session_id="s")
    assert (info.is_error, info.error_text) == (True, "File not found")


def test_runtime_fields_and_timestamps(compute):
    assert compute.extract_runtime_fields(ANSWER) == {"model": "gemini-3.8-flash"}
    assert compute.extract_runtime_fields(PROMPT) == {}
    assert compute.is_session_start_marker(HEADER)
    assert compute.extract_item_timestamp(PROMPT).year == 2026
    assert compute.extract_item_timestamp(SET) is None


def test_token_usage_conversion():
    usage = to_token_usage(TOKENS)
    assert usage.input_tokens == 2049  # cached tokens are part of ``input``
    assert usage.cache_read_input_tokens == 8000
    assert usage.output_tokens == 568  # thinking is billed as output


@pytest.mark.django_db
def test_cost_is_counted_once_per_record(compute):
    seen: set[str] = set()
    first = SimpleNamespace(cost=None, context_usage=None, message_id=None)
    compute.compute_item_cost_and_usage(first, ANSWER, seen, None)
    assert first.message_id == "21aa"
    assert first.context_usage == 10632 + 27
    assert isinstance(first.cost, Decimal) and first.cost > 0
    again = SimpleNamespace(cost=None, context_usage=None, message_id=None)
    compute.compute_item_cost_and_usage(again, ANSWER, seen, None)
    assert again.cost is None


# --- Session files -----------------------------------------------------------


def _write_session(root: Path, slug: str, lines: list[dict], *, project: str = "/work/demo") -> Path:
    slug_dir = root / slug
    (slug_dir / "chats").mkdir(parents=True)
    (slug_dir / ".project_root").write_text(project)
    path = slug_dir / "chats" / "session-2026-11-16T01-02-8143b1d5.jsonl"
    path.write_bytes(b"".join(orjson.dumps(line) + b"\n" for line in lines))
    return path


def test_session_file_meta(tmp_path):
    path = _write_session(tmp_path, "demo", [HEADER, CONTEXT, SET, PROMPT])
    meta = read_session_file_meta(path)
    assert meta.session_id == HEADER["sessionId"]
    assert meta.cwd == "/work/demo"


def test_session_file_without_prompt_is_skipped(tmp_path):
    # The CLI writes such a file for every ACP client that connects.
    assert read_session_file_meta(_write_session(tmp_path, "demo", [HEADER, CONTEXT, SET])) is None


def test_unknown_session_kind_is_skipped(tmp_path):
    path = _write_session(tmp_path, "demo", [{**HEADER, "kind": "other"}, PROMPT])
    assert read_session_file_meta(path) is None


def test_subagent_session_file(tmp_path):
    slug_dir = tmp_path / "demo"
    (slug_dir / "chats" / "parent-id").mkdir(parents=True)
    (slug_dir / ".project_root").write_text("/work/demo")
    path = slug_dir / "chats" / "parent-id" / "child-id.jsonl"
    path.write_bytes(b"".join(orjson.dumps(line) + b"\n" for line in [
        {**HEADER, "sessionId": "child-id", "kind": "subagent"}, SUBAGENT_PROMPT,
    ]))
    meta = read_session_file_meta(path)
    assert (meta.session_id, meta.cwd, meta.kind) == ("child-id", "/work/demo", "subagent")


def test_project_root_falls_back_to_projects_json(tmp_path):
    slug_dir = tmp_path / "tmp" / "demo"
    slug_dir.mkdir(parents=True)
    (tmp_path / "projects.json").write_bytes(orjson.dumps({"projects": {"/work/demo": "demo"}}))
    assert read_project_root(slug_dir) == "/work/demo"


# --- Helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gemini-3.8-flash", ("gemini-flash", "3.8")),
        ("gemini-3.5-flash-lite", ("gemini-flash-lite", "3.5")),
        ("gemini-3.1-pro-preview-customtools", ("gemini-pro", "3.1")),
        ("gemini-3-flash-preview", ("gemini-flash", "3")),
        ("auto", None),
    ],
)
def test_extract_model_info(raw, expected):
    info = extract_model_info(raw)
    assert (tuple(info) if info else None) == expected


def test_helpers_contract():
    helpers = get_provider_helpers(Provider.GEMINI)
    assert helpers.LABEL == "Gemini"
    assert helpers.find_model("gemini-pro").full_name == "gemini-3.1-pro-preview"
    assert helpers.find_model("gemini-pro-2.5").full_name == "gemini-2.5-pro"
    assert helpers.extract_family_and_version("google/gemini-3.8-flash") == ("gemini-flash", "3.8")
    assert helpers.is_final_assistant_message(ANSWER) is True
    assert helpers.is_final_assistant_message({**ANSWER, "toolCalls": [READ_CALL]}) is False
    assert helpers.is_final_assistant_message(PROMPT) is None
    assert helpers.extract_indexable_text_from_parsed(PROMPT) == "Read note.txt then create out.txt"
    assert helpers.extract_indexable_text_from_parsed(CONTEXT) == ""
    assert "yolo" not in helpers.UNTRUSTED_PERMISSION_MODES
    for value in helpers.SYNCED_SETTINGS_DEFAULTS.values():
        assert value is not None
    # ``Session.selected_model`` holds at most 30 characters.
    for mv in helpers.MODEL_VERSIONS:
        assert len(helpers.selected_model_value(mv)) <= 30
    bootstrap = helpers.get_bootstrap_data()
    assert bootstrap["agent_settings_choices"]["permission_mode"] == ["default", "auto_edit", "plan", "yolo"]
    assert bootstrap["agent_settings_choices"]["effort"] == ["medium"]


def test_every_provider_is_registered():
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.asgi import WSConsumer
    from twicc.cli._drop_request.help_context import _PER_PROVIDER
    from twicc.cli._drop_request.help_strings import _PROVIDER_LABELS
    from twicc.orchestrator import OrchestratorRegistry
    from twicc.providers.helpers import get_provider_helpers_registry

    for provider in Provider:
        assert get_provider_helpers_registry().get(provider)
        assert get_agent_manager_registry().get(provider)
        assert provider in WSConsumer.PROVIDER_HANDLERS
        assert provider in OrchestratorRegistry().PROVIDER_ORCHESTRATORS
        assert provider.value in _PER_PROVIDER
        assert provider.value in _PROVIDER_LABELS


@pytest.mark.django_db
def test_repeating_decimal_prices_sync_once():
    # OpenRouter publishes some Google cache prices per token with a repeating
    # decimal; a second sync of the same catalogue must not insert a duplicate.
    from twicc.pricing import extract_provider_prices, persist_provider_prices

    models = [{"id": "google/gemini-3.5-flash-lite", "pricing": {
        "prompt": "0.0000001", "completion": "0.0000004", "input_cache_read": "0.00000001",
        "input_cache_write": "0.0000000833333333333333",
    }}]
    first = persist_provider_prices(Provider.GEMINI, extract_provider_prices(models, Provider.GEMINI))
    second = persist_provider_prices(Provider.GEMINI, extract_provider_prices(models, Provider.GEMINI))
    assert first == {"created": 1, "unchanged": 0}
    assert second == {"created": 0, "unchanged": 1}


# --- Subagents ---------------------------------------------------------------


def test_invoke_agent_prompt_matches_the_subagent_task(compute):
    launcher = {**THINKING, "toolCalls": [INVOKE_CALL]}
    assert compute.extract_task_tool_uses(launcher) == [("invoke_agent__call_1", False)]
    prompts = compute.extract_task_tool_use_prompts(launcher)
    assert prompts == [("invoke_agent__call_1", "Explain what calc.py does.", False)]
    # compute_base links a subagent when its first message equals the prompt.
    assert compute.extract_user_message_text(SUBAGENT_PROMPT) == prompts[0][1]
    assert compute.extract_user_message_text(PROMPT) == "Read note.txt then create out.txt"


# --- Attachments -------------------------------------------------------------


def test_attachments_manifest_is_extracted_from_the_prompt(compute):
    from twicc.core.services.attachments.manifest import build_manifest
    from twicc.core.services.attachments.types import AttachmentManifest, ManifestEntry

    manifest = AttachmentManifest(
        "s", Path("/data/artifacts/s/attachments"),
        (ManifestEntry(1, "notes.md", "text", 1, 1, "file", "notes.md"),),
    )
    record = {"id": "u1", "timestamp": "2026-11-16T01:00:00Z", "type": "user",
              "content": [{"text": build_manifest(manifest)}, {"text": "Summarize the attached notes."}]}
    assert compute.transform_inline(record, session_id="s", line_num=4) is not None
    assert record["content"] == [{"text": "Summarize the attached notes."}]
    assert record["twicc_attachments"]["entries"][0]["name"] == "notes.md"
    attachment_only = {"id": "u2", "timestamp": "2026-11-16T01:00:00Z", "type": "user",
                       "content": [{"text": build_manifest(manifest)}]}
    compute.transform_inline(attachment_only, session_id="s", line_num=5)
    assert compute.compute_item_kind(attachment_only) == ItemKind.USER_MESSAGE


def test_instruction_and_context_blocks_are_stripped(compute):
    record = {"id": "u1", "timestamp": "2026-11-16T01:00:00Z", "type": "user", "content": [{"text": (
        "<twicc:context>\nsession_id: abc\n</twicc:context>\n\n"
        "<twicc:instruction>\nYou are running inside TwiCC.\n</twicc:instruction>\n\nHello"
    )}]}
    compute.transform_inline(record, session_id="s", line_num=4)
    assert records.user_text(record) == "Hello"


# --- Slash commands ----------------------------------------------------------


def test_acp_commands_map_to_command_rows():
    from twicc.providers.gemini.commands import _commands_from_update

    desired = _commands_from_update([
        {"name": "memory show", "description": "Shows the current memory contents."},
        {"name": "review", "description": "Review code", "input": {"hint": "[path]"}},
        {"description": "no name"},
    ])
    assert set(desired) == {"memory show", "review"}
    assert desired["review"]["argument_hint"] == "[path]"
    assert desired["memory show"]["is_builtin"] is False


def test_twicc_mcp_server_entry_and_auto_approval():
    from twicc.providers.gemini.agent.agent import is_twicc_mcp_call, twicc_mcp_servers

    [server] = twicc_mcp_servers("session-1")
    assert server["type"] == "http" and server["name"] == "twicc"
    assert server["headers"][0]["name"] == "Authorization"
    assert server["headers"][0]["value"].startswith("Bearer twicc_mcp_session-1.")
    assert is_twicc_mcp_call("twicc_whoami__call_1")
    assert is_twicc_mcp_call("mcp_twicc_whoami__call_1")
    assert not is_twicc_mcp_call("write_file__call_1")


def test_tool_call_history_facts_mark_agent_calls(compute):
    from twicc.providers.history_facts import HistoryFactKind

    facts = compute.extract_history_facts({**THINKING, "toolCalls": [READ_CALL, INVOKE_CALL]}, line_num=7, history=None)
    assert [(fact.kind, fact.key, fact.data["is_agent"]) for fact in facts] == [
        (HistoryFactKind.TOOL_CALL, "read_file__call_1", False),
        (HistoryFactKind.TOOL_CALL, "invoke_agent__call_1", True),
    ]
    assert compute.rebuild_agent_prompt_links is True
