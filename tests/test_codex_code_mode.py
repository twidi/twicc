"""Codex code mode (GPT-5.6 ``exec`` / ``wait``) — extraction and compute wiring.

Covers, per ``docs/plans/2026-07-10-codex-code-mode-display-design.md`` §9:

- the static script extractor (``parse_code_mode_script``) over the fixture
  list shared with the frontend mirror (``parseCodeModeScript.js``);
- the output status parser (``parse_code_mode_output``) for both wire shapes
  (plain string / array of ``input_text`` segments) and all four statuses;
- compute integration on JSONL lifted from the real 5.6 sessions
  ``019f4d27-01dd-7612-8ec4-f9659e71a7ac`` (exec/wait/patch) and
  ``019f4fcb-5ddb-71b0-ac0d-660c41ee5fd6`` (nested MCP): kinds,
  ``wait`` → ``exec`` chaining (batch + live), ``is_terminated``
  transitions, script-failure errors, orphan ``patch_apply_end`` /
  ``mcp_tool_call_end`` pairing, doc-edit events;
- a pre-5.6 regression check (``exec_command`` / ``write_stdin`` chain
  untouched by the remap refactor).
"""

from __future__ import annotations

import json
import queue
from datetime import datetime, UTC

import orjson
import pytest

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import Project, Session, SessionItem, ToolResultLink
from twicc.providers.codex.code_mode_script import (
    parse_code_mode_output,
    parse_code_mode_script,
)
from twicc.providers.codex.compute import get_compute
from twicc.providers.plan_docs import DocEditEvent


# ---------------------------------------------------------------------------
# Extractor — parse_code_mode_script
# ---------------------------------------------------------------------------


class TestParseCodeModeScript:
    def test_canonical_exec_command_wrapper(self):
        source = (
            'const r = await tools.exec_command({"cmd":"ls -l toto3.txt",'
            '"workdir":"/home/twidi/dev/twicc-poc","yield_time_ms":10000,'
            '"max_output_tokens":2000});\ntext(r.output);'
        )
        result = parse_code_mode_script(source)
        assert result.pragma is None
        assert len(result.calls) == 1
        call = result.calls[0]
        assert call.name == "exec_command"
        assert call.resolved is True
        assert call.arg == {
            "cmd": "ls -l toto3.txt",
            "workdir": "/home/twidi/dev/twicc-poc",
            "yield_time_ms": 10000,
            "max_output_tokens": 2000,
        }

    def test_unquoted_object_keys(self):
        source = 'const r = await tools.exec_command({ cmd: "echo hi", workdir: "/tmp" });'
        result = parse_code_mode_script(source)
        assert result.calls == [
            ("exec_command", {"cmd": "echo hi", "workdir": "/tmp"}, True),
        ]

    def test_pragma_line(self):
        source = '// @exec: {"yield_time_ms": 500}\nawait tools.exec_command({"cmd":"sleep 1"});'
        result = parse_code_mode_script(source)
        assert result.pragma == {"yield_time_ms": 500}
        assert result.calls == [("exec_command", {"cmd": "sleep 1"}, True)]

    def test_malformed_pragma_is_ignored(self):
        source = '// @exec: {not json}\nawait tools.exec_command({"cmd":"x"});'
        result = parse_code_mode_script(source)
        assert result.pragma is None
        assert result.calls == [("exec_command", {"cmd": "x"}, True)]

    def test_const_patch_apply_patch_wrapper(self):
        # Canonical shape observed in the real session (line 32).
        source = (
            'const patch = "*** Begin Patch\\n*** Add File: /home/twidi/dev/twicc-poc/toto3.txt\\n*** End Patch";\n'
            "const r = await tools.apply_patch(patch);\n"
            'text(typeof r === "string" ? r : JSON.stringify(r));'
        )
        result = parse_code_mode_script(source)
        assert result.calls == [(
            "apply_patch",
            "*** Begin Patch\n*** Add File: /home/twidi/dev/twicc-poc/toto3.txt\n*** End Patch",
            True,
        )]

    def test_string_concat_const(self):
        source = (
            'const a = "*** Begin Patch\\n" + "*** End Patch";\n'
            "await tools.apply_patch(a);"
        )
        result = parse_code_mode_script(source)
        assert result.calls == [("apply_patch", "*** Begin Patch\n*** End Patch", True)]

    def test_call_nested_in_expression(self):
        source = 'text(JSON.stringify(await tools.exec_command({"cmd":"pwd"})));'
        result = parse_code_mode_script(source)
        assert result.calls == [("exec_command", {"cmd": "pwd"}, True)]

    def test_polling_loop_extracts_single_call(self):
        source = 'while ((await tools.exec_command({"cmd":"cat /tmp/s"})).output !== "ready") {}'
        result = parse_code_mode_script(source)
        assert result.calls == [("exec_command", {"cmd": "cat /tmp/s"}, True)]

    def test_multiple_calls_in_source_order(self):
        source = 'await tools.exec_command({"cmd":"a"});\nawait tools.apply_patch("*** Begin Patch\\n*** End Patch");'
        result = parse_code_mode_script(source)
        assert [c.name for c in result.calls] == ["exec_command", "apply_patch"]
        assert all(c.resolved for c in result.calls)

    def test_template_interpolation_is_unresolved(self):
        source = 'const f = "x";\nawait tools.exec_command({"cmd":`cat ${f}`});'
        result = parse_code_mode_script(source)
        assert result.calls == [("exec_command", None, False)]

    def test_mcp_call(self):
        source = 'const r = await tools.mcp__twicc__sessions({"limit": 5});'
        result = parse_code_mode_script(source)
        assert result.calls == [("mcp__twicc__sessions", {"limit": 5}, True)]

    def test_view_image_call(self):
        # Canonical GPT-5.6 wrapper: the image is emitted through the code
        # cell's ``image(...)`` helper, while ``view_image`` remains the only
        # nested tool call recovered from the script.
        source = (
            'const r = await tools.view_image({path:"/tmp/preview.png",detail:"high"}); '
            'image(r.image_url,r.detail);'
        )
        result = parse_code_mode_script(source)
        assert result.calls == [(
            "view_image",
            {"path": "/tmp/preview.png", "detail": "high"},
            True,
        )]

    def test_no_calls(self):
        assert parse_code_mode_script('text("hello");').calls == []

    def test_calls_inside_strings_and_comments_are_ignored(self):
        source = (
            '// tools.apply_patch(decoy)\n'
            '/* tools.exec_command({"cmd":"decoy"}) */\n'
            'text("tools.exec_command(decoy)");\n'
            'await tools.exec_command({"cmd":"real"});'
        )
        result = parse_code_mode_script(source)
        assert result.calls == [("exec_command", {"cmd": "real"}, True)]

    def test_non_string_input_degrades_to_empty(self):
        assert parse_code_mode_script(None).calls == []
        assert parse_code_mode_script(42).calls == []
        assert parse_code_mode_script("").calls == []

    def test_literal_values(self):
        source = 'await tools.foo({"s":"x","n":1.5,"neg":-2,"t":true,"f":false,"nul":null,"arr":[1,"a",{"k":"v"}],});'
        result = parse_code_mode_script(source)
        assert result.calls == [(
            "foo",
            {"s": "x", "n": 1.5, "neg": -2, "t": True, "f": False,
             "nul": None, "arr": [1, "a", {"k": "v"}]},
            True,
        )]

    def test_non_literal_argument_is_unresolved_but_listed(self):
        source = 'await tools.exec_command(buildArgs());'
        result = parse_code_mode_script(source)
        assert result.calls == [("exec_command", None, False)]


# ---------------------------------------------------------------------------
# Output parsing — parse_code_mode_output
# ---------------------------------------------------------------------------


class TestParseCodeModeOutput:
    def test_array_shape_completed(self):
        output = [
            {"type": "input_text", "text": "Script completed\nWall time 0.2 seconds\nOutput:\n"},
            {"type": "input_text", "text": "file contents\n"},
        ]
        parsed = parse_code_mode_output(output)
        assert parsed.status == "completed"
        assert parsed.cell_id is None
        assert parsed.wall_time_seconds == 0.2
        assert parsed.error_text is None
        assert parsed.body == "file contents\n"

    def test_string_shape_running(self):
        parsed = parse_code_mode_output(
            "Script running with cell ID 2\nWall time 32.2 seconds\nOutput:\n"
        )
        assert parsed.status == "running"
        assert parsed.cell_id == "2"
        assert parsed.body == ""

    def test_failed_with_error_segment(self):
        output = [
            {"type": "input_text", "text": "Script failed\nWall time 1.0 seconds\nOutput:\n"},
            {"type": "input_text", "text": "partial"},
            {"type": "input_text", "text": "Script error:\nReferenceError: x is not defined"},
        ]
        parsed = parse_code_mode_output(output)
        assert parsed.status == "failed"
        assert parsed.error_text == "ReferenceError: x is not defined"
        assert parsed.body == "partial"

    def test_terminated(self):
        parsed = parse_code_mode_output("Script terminated\nWall time 5.0 seconds\nOutput:\n")
        assert parsed.status == "terminated"
        assert parsed.error_text is None

    def test_non_code_mode_outputs_return_none(self):
        assert parse_code_mode_output("Process exited with code 0\nOutput:\nfoo") is None
        assert parse_code_mode_output("plain shell output") is None
        assert parse_code_mode_output(None) is None
        assert parse_code_mode_output(42) is None
        assert parse_code_mode_output([]) is None
        assert parse_code_mode_output([{"type": "image", "url": "x"}]) is None


# ---------------------------------------------------------------------------
# Compute integration
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _codex_line(type_: str, payload: dict) -> str:
    # Compact separators to match the real rollout wire shape (serde_json /
    # orjson write no spaces) — the live lookups' textual pre-filters
    # (``content__contains='"name":"exec"'``) rely on it.
    return json.dumps(
        {"timestamp": _NOW.isoformat(), "type": type_, "payload": payload},
        separators=(",", ":"),
    )


def _exec_call_line(call_id: str, script: str) -> str:
    return _codex_line(
        "response_item",
        {"type": "custom_tool_call", "call_id": call_id, "name": "exec", "input": script},
    )


def _custom_output_line(call_id: str, output) -> str:
    return _codex_line(
        "response_item",
        {"type": "custom_tool_call_output", "call_id": call_id, "output": output},
    )


def _wait_call_line(call_id: str, cell_id: str) -> str:
    return _codex_line(
        "response_item",
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "wait",
            "arguments": json.dumps({"cell_id": cell_id, "yield_time_ms": 10000, "max_tokens": 2000}),
        },
    )


def _function_output_line(call_id: str, output) -> str:
    return _codex_line(
        "response_item",
        {"type": "function_call_output", "call_id": call_id, "output": output},
    )


def _completed_item_line(item: dict) -> str:
    return _codex_line("event_msg", {
        "type": "item_completed",
        "thread_id": "test-session-code-mode",
        "turn_id": "turn-1",
        "item": item,
        "completed_at_ms": int(_NOW.timestamp() * 1000),
    })


def _file_change_line(
    call_id: str,
    changes: dict,
    *,
    status: str = "completed",
    stdout: str = "Success.\n",
    stderr: str = "",
) -> str:
    return _completed_item_line({
        "type": "FileChange",
        "id": call_id,
        "changes": changes,
        "status": status,
        "stdout": stdout,
        "stderr": stderr,
    })


def _mcp_end_line(call_id: str, server: str, tool: str, *, result: dict | None = None) -> str:
    item = {
        "type": "McpToolCall",
        "id": call_id,
        "server": server,
        "tool": tool,
        "arguments": {},
        "status": "completed",
        "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
    }
    if result is not None:
        if "Err" in result:
            item["status"] = "failed"
            item["error"] = {"message": result["Err"]}
            item.pop("result")
        else:
            item["result"] = result["Ok"]
            if result["Ok"].get("isError"):
                item["status"] = "failed"
    return _completed_item_line(item)


_RUNNING_CELL_2 = "Script running with cell ID 2\nWall time 32.2 seconds\nOutput:\n"
_COMPLETED_ARRAY = [
    {"type": "input_text", "text": "Script completed\nWall time 0.1 seconds\nOutput:\n"},
    {"type": "input_text", "text": "done\n"},
]


def _apply_compute_results(result_queue, provider_compute) -> None:
    while True:
        try:
            raw_msg = result_queue.get_nowait()
        except queue.Empty:
            break
        msg = orjson.loads(raw_msg)
        if msg.get("type") == "session_complete":
            provider_compute.apply_session_complete(msg)


def _run_batch_compute(session) -> None:
    compute = get_compute()
    result_q = queue.Queue()
    compute.compute_session_metadata(session.id, result_q, run_id=0)
    _apply_compute_results(result_q, compute)


@pytest.fixture
def codex_session(db):
    project = Project.objects.create(id="test-project-code-mode")
    return Session.objects.create(
        id="test-session-code-mode", project=project, provider=Provider.CODEX,
    )


def _create_items(session, lines: list[str]) -> None:
    SessionItem.objects.bulk_create([
        SessionItem(session=session, line_num=i, content=line)
        for i, line in enumerate(lines, start=1)
    ])


class TestCodeModeKinds:
    def test_exec_is_tool_use_and_wait_is_system(self):
        compute = get_compute()
        exec_line = orjson.loads(_exec_call_line("call_1", 'text("x");'))
        wait_line = orjson.loads(_wait_call_line("call_2", "2"))
        assert compute.compute_item_kind(exec_line) == ItemKind.TOOL_USE
        assert compute.compute_item_kind(wait_line) == ItemKind.SYSTEM

    def test_resolved_write_stdin_wrapper_is_an_invisible_write_stdin(self):
        compute = get_compute()
        wrapped = orjson.loads(_exec_call_line(
            "call_stdin",
            'const r = await tools.write_stdin({"session_id":78213,"chars":""});\ntext(r.output);',
        ))

        assert compute.compute_item_kind(wrapped) == ItemKind.SYSTEM
        assert compute.extract_tool_use_entries(
            wrapped, session_id="test-session",
        ) == {"call_stdin": "write_stdin"}

    def test_unresolved_write_stdin_wrapper_stays_a_visible_exec(self):
        compute = get_compute()
        wrapped = orjson.loads(_exec_call_line(
            "call_stdin_dynamic",
            "await tools.write_stdin(buildArgs());",
        ))

        assert compute.compute_item_kind(wrapped) == ItemKind.TOOL_USE
        assert compute.extract_tool_use_entries(
            wrapped, session_id="test-session",
        ) == {"call_stdin_dynamic": "exec"}


class TestCodeModeTasks:
    @staticmethod
    def _mixed_plan_script() -> str:
        # Shape observed in session 019f61f0-de78-7551-85ff-46fa6792bf98,
        # line 22: update_plan shares one exec cell with unrelated tools.
        return (
            'const p = await tools.update_plan({plan:['
            '{step:"Inspect the session",status:"completed"},'
            '{step:"Implement the fix",status:"in_progress"}'
            '],explanation:"Recovered from code mode"});\n'
            'const [s,c] = await Promise.all(['
            'tools.exec_command({cmd:"pwd"}),'
            'tools.exec_command({cmd:"rg update_plan src"})'
            ']);\ntext(JSON.stringify(p));'
        )

    def test_mixed_exec_extracts_nested_update_plan(self):
        parsed = orjson.loads(_exec_call_line("call_plan_nested", self._mixed_plan_script()))
        compute = get_compute()

        assert compute.compute_item_kind(parsed) == ItemKind.TOOL_USE
        assert compute.extract_tool_use_entries(
            parsed, session_id="test-session",
        ) == {"call_plan_nested": "exec"}
        assert compute.extract_tasks_payload(parsed) == {
            "source": "update_plan",
            "items": [
                {"content": "Inspect the session", "status": "completed"},
                {"content": "Implement the fix", "status": "in_progress"},
            ],
            "explanation": "Recovered from code mode",
        }

    def test_dynamic_or_repeated_nested_updates_are_ignored(self):
        dynamic = orjson.loads(_exec_call_line(
            "call_plan_dynamic", "await tools.update_plan(buildPlan());",
        ))
        repeated = orjson.loads(_exec_call_line(
            "call_plan_repeated",
            'await tools.update_plan({plan:[{step:"a",status:"pending"}]});\n'
            'await tools.update_plan({plan:[{step:"b",status:"in_progress"}]});',
        ))

        compute = get_compute()
        assert compute.extract_tasks_payload(dynamic) is None
        assert compute.extract_tasks_payload(repeated) is None

    def test_native_update_plan_remains_supported(self):
        parsed = orjson.loads(_codex_line("response_item", {
            "type": "function_call",
            "call_id": "call_plan_native",
            "name": "update_plan",
            "arguments": json.dumps({
                "plan": [{"step": "Keep compatibility", "status": "completed"}],
            }),
        }))

        assert get_compute().extract_tasks_payload(parsed) == {
            "source": "update_plan",
            "items": [{"content": "Keep compatibility", "status": "completed"}],
            "explanation": None,
        }

    def test_batch_recompute_persists_nested_plan_snapshot(self, codex_session):
        _create_items(codex_session, [
            _exec_call_line("call_plan_batch", self._mixed_plan_script()),
        ])

        _run_batch_compute(codex_session)

        codex_session.refresh_from_db()
        assert codex_session.tasks == {
            "provider": "codex",
            "line": 1,
            "updated_at": _NOW.isoformat(),
            "source": "update_plan",
            "items": [
                {"content": "Inspect the session", "status": "completed"},
                {"content": "Implement the fix", "status": "in_progress"},
            ],
            "explanation": "Recovered from code mode",
        }

    def test_live_sync_persists_nested_plan_snapshot(self, codex_session, tmp_path):
        rollout = tmp_path / "rollout.jsonl"
        rollout.write_text(
            _exec_call_line("call_plan_live", self._mixed_plan_script()) + "\n",
            encoding="utf-8",
        )

        get_compute().sync_session_items_from_file(codex_session, rollout)

        codex_session.refresh_from_db()
        assert codex_session.tasks["provider"] == "codex"
        assert codex_session.tasks["line"] == 1
        assert codex_session.tasks["source"] == "update_plan"
        assert codex_session.tasks["items"][1] == {
            "content": "Implement the fix",
            "status": "in_progress",
        }
        assert codex_session.tasks["explanation"] == "Recovered from code mode"


class TestCodeModeCompute:
    def test_atomic_completed_exec(self, codex_session):
        """One-shot script: single link, terminated on arrival, no error."""
        script = 'const r = await tools.exec_command({"cmd":"ls"});\ntext(r.output);'
        _create_items(codex_session, [
            _exec_call_line("call_exec_1", script),
            _custom_output_line("call_exec_1", _COMPLETED_ARRAY),
        ])
        _run_batch_compute(codex_session)

        link = ToolResultLink.objects.get(session=codex_session, tool_use_id="call_exec_1")
        assert link.tool_name == "exec"
        assert link.error is None
        assert json.loads(link.extra) == {"is_terminated": True}

    def test_wait_chain_rebinds_to_exec(self, codex_session):
        """Background cell: the wait output lands on the exec's link chain."""
        script = 'const r = await tools.apply_patch("*** Begin Patch\\n*** End Patch");'
        _create_items(codex_session, [
            _exec_call_line("call_exec_2", script),
            _custom_output_line("call_exec_2", _RUNNING_CELL_2),
            _wait_call_line("call_wait_1", "2"),
            _function_output_line("call_wait_1", _COMPLETED_ARRAY),
        ])
        _run_batch_compute(codex_session)

        links = list(
            ToolResultLink.objects.filter(session=codex_session).order_by("tool_result_line_num")
        )
        assert [link.tool_use_id for link in links] == ["call_exec_2", "call_exec_2"]
        assert [link.tool_result_line_num for link in links] == [2, 4]
        # Spinner: the running chunk leaves extra unset; the closing wait
        # chunk flips is_terminated.
        assert links[0].extra is None
        assert json.loads(links[1].extra) == {"is_terminated": True}

    def test_wrapped_write_stdin_and_its_wait_rebind_to_wrapped_exec_command(
        self, codex_session,
    ):
        """Real GPT-5.6 shape: both wrapper layers collapse onto one card."""
        exec_script = (
            'const r = await tools.exec_command({"cmd":"npx vite build","yield_time_ms":1000});\n'
            'text(r.output);\nif (r.session_id) text(`SESSION_ID=${r.session_id}`);'
        )
        stdin_script = (
            'const r = await tools.write_stdin({"session_id":78213,"chars":"",'
            '"yield_time_ms":10000});\ntext(r.output);'
        )
        exec_output = [
            {"type": "input_text", "text": "Script completed\nWall time 1.2 seconds\nOutput:\n"},
            {"type": "input_text", "text": "transforming...\nSESSION_ID=78213\n"},
        ]
        _create_items(codex_session, [
            _exec_call_line("call_exec_parent", exec_script),
            _custom_output_line("call_exec_parent", exec_output),
            _exec_call_line("call_stdin_wrapper", stdin_script),
            _custom_output_line(
                "call_stdin_wrapper",
                "Script running with cell ID 25\nWall time 10.0 seconds\nOutput:\n",
            ),
            _wait_call_line("call_wait_stdin", "25"),
            _function_output_line("call_wait_stdin", _COMPLETED_ARRAY),
        ])
        _run_batch_compute(codex_session)

        wrapped_item = SessionItem.objects.get(session=codex_session, line_num=3)
        assert wrapped_item.kind == ItemKind.SYSTEM
        links = list(
            ToolResultLink.objects.filter(session=codex_session).order_by("tool_result_line_num")
        )
        assert [link.tool_use_id for link in links] == [
            "call_exec_parent", "call_exec_parent", "call_exec_parent",
        ]
        assert [link.tool_use_line_num for link in links] == [1, 1, 1]
        assert [link.tool_name for link in links] == ["exec", "exec", "exec"]
        assert json.loads(links[0].extra) == {"is_terminated": True}
        assert links[1].extra is None
        assert json.loads(links[2].extra) == {"is_terminated": True}

    def test_wrapped_exec_command_wait_can_announce_session_id(self, codex_session):
        """The parent code cell may need a wait before it yields session_id."""
        exec_script = (
            'const r = await tools.exec_command({"cmd":"sleep 60","yield_time_ms":30000});\n'
            'text(r.output);\nif (r.session_id) text(`SESSION_ID=${r.session_id}`);'
        )
        stdin_script = (
            'const r = await tools.write_stdin({"session_id":7,"chars":""});\ntext(r.output);'
        )
        parent_completed = [
            {"type": "input_text", "text": "Script completed\nWall time 10.1 seconds\nOutput:\n"},
            {"type": "input_text", "text": "SESSION_ID=7\n"},
        ]
        _create_items(codex_session, [
            _exec_call_line("call_exec_parent", exec_script),
            _custom_output_line("call_exec_parent", _RUNNING_CELL_2),
            _wait_call_line("call_wait_parent", "2"),
            _function_output_line("call_wait_parent", parent_completed),
            _exec_call_line("call_stdin_wrapper", stdin_script),
            _custom_output_line("call_stdin_wrapper", _COMPLETED_ARRAY),
        ])
        _run_batch_compute(codex_session)

        links = list(
            ToolResultLink.objects.filter(session=codex_session).order_by("tool_result_line_num")
        )
        assert [link.tool_use_id for link in links] == [
            "call_exec_parent", "call_exec_parent", "call_exec_parent",
        ]
        assert [link.tool_result_line_num for link in links] == [2, 4, 6]

    def test_failed_script_surfaces_error(self, codex_session):
        failed_output = [
            {"type": "input_text", "text": "Script failed\nWall time 1.0 seconds\nOutput:\n"},
            {"type": "input_text", "text": "Script error:\nReferenceError: x is not defined"},
        ]
        _create_items(codex_session, [
            _exec_call_line("call_exec_3", "x.y;"),
            _custom_output_line("call_exec_3", failed_output),
        ])
        _run_batch_compute(codex_session)

        link = ToolResultLink.objects.get(session=codex_session, tool_use_id="call_exec_3")
        assert link.error == "ReferenceError: x is not defined"
        assert json.loads(link.extra) == {"is_terminated": True}

    def test_nested_patch_apply_end_pairs_with_exec(self, codex_session):
        """The nested patch_apply_end (``exec-<uuid>`` call_id) is rebound
        to the outer exec call so the frontend gets the same rich event
        (structured changes, original_files splice) as a direct
        apply_patch — 5.5 display parity."""
        script = (
            'const patch = "*** Begin Patch\\n*** Add File: /tmp/toto3.txt\\n+x\\n*** End Patch";\n'
            "await tools.apply_patch(patch);"
        )
        _create_items(codex_session, [
            _exec_call_line("call_exec_4", script),
            _custom_output_line("call_exec_4", _COMPLETED_ARRAY),
            _file_change_line(
                "exec-b179a299-665e-423e-84e0-1bc0f2daba9f",
                {"/tmp/toto3.txt": {"type": "add", "content": "x"}},
            ),
        ])
        _run_batch_compute(codex_session)

        links = list(
            ToolResultLink.objects.filter(
                session=codex_session, tool_use_id="call_exec_4",
            ).order_by("tool_result_line_num")
        )
        assert [link.tool_result_line_num for link in links] == [2, 3]
        event_link = links[1]
        assert event_link.error is None
        # The event must not disturb the spinner logic: no extra on it,
        # the exec's own completed output carries is_terminated.
        assert event_link.extra is None
        assert json.loads(links[0].extra) == {"is_terminated": True}

    def test_nested_patch_path_match_beats_recency(self, codex_session):
        """An event whose paths match an older script binds to that script,
        not to the most recent patch-wrapping exec."""
        script_a = 'await tools.apply_patch("*** Begin Patch\\n*** Add File: docs/a.txt\\n+x\\n*** End Patch");'
        script_b = 'await tools.apply_patch("*** Begin Patch\\n*** Add File: docs/b.txt\\n+y\\n*** End Patch");'
        _create_items(codex_session, [
            _exec_call_line("call_exec_a", script_a),
            _custom_output_line("call_exec_a", _RUNNING_CELL_2),
            _exec_call_line("call_exec_b", script_b),
            _custom_output_line("call_exec_b", _COMPLETED_ARRAY),
            _file_change_line(
                "exec-11111111-2222-3333-4444-555555555555",
                {"/repo/docs/a.txt": {"type": "add", "content": "x"}},
            ),
        ])
        _run_batch_compute(codex_session)

        event_link = ToolResultLink.objects.get(
            session=codex_session, tool_result_line_num=5,
        )
        assert event_link.tool_use_id == "call_exec_a"

    def test_failed_nested_patch_surfaces_error_on_exec(self, codex_session):
        script = 'await tools.apply_patch("*** Begin Patch\\n*** Update File: /tmp/f.txt\\n*** End Patch");'
        _create_items(codex_session, [
            _exec_call_line("call_exec_6", script),
            _custom_output_line("call_exec_6", _COMPLETED_ARRAY),
            _file_change_line(
                "exec-66666666-7777-8888-9999-000000000000",
                {"/tmp/f.txt": {"type": "update", "unified_diff": ""}},
                status="failed",
                stdout="",
                stderr="apply_patch: /tmp/f.txt: No such file",
            ),
        ])
        _run_batch_compute(codex_session)

        event_link = ToolResultLink.objects.get(
            session=codex_session, tool_use_id="call_exec_6", tool_result_line_num=3,
        )
        assert event_link.error is not None

    def test_nested_mcp_ends_pair_with_exec(self, codex_session):
        """Both ``mcp_tool_call_end`` events of a two-call script are
        rebound to the outer exec, and neither disturbs the spinner
        (their links carry no extra; the exec's own output terminates)."""
        script = (
            "const status = await tools.mcp__twicc__status({});\n"
            "const info = await tools.mcp__twicc__info({});\n"
            'text(JSON.stringify({status, info}));'
        )
        _create_items(codex_session, [
            _exec_call_line("call_exec_mcp_1", script),
            _mcp_end_line("exec-56549e4c-4c82-4f8f-b67a-405fd2707e9b", "twicc", "status"),
            _mcp_end_line("exec-5539c01c-0566-42a3-ba31-a84e0dc3aeb0", "twicc", "info"),
            _custom_output_line("call_exec_mcp_1", _COMPLETED_ARRAY),
        ])
        _run_batch_compute(codex_session)

        links = list(
            ToolResultLink.objects.filter(
                session=codex_session, tool_use_id="call_exec_mcp_1",
            ).order_by("tool_result_line_num")
        )
        assert [link.tool_result_line_num for link in links] == [2, 3, 4]
        assert links[0].error is None and links[0].extra is None
        assert links[1].error is None and links[1].extra is None
        assert json.loads(links[2].extra) == {"is_terminated": True}

    def test_nested_mcp_tool_name_match_beats_recency(self, codex_session):
        """An event whose invocation matches an older script binds to that
        script, not to the most recent MCP-wrapping exec."""
        _create_items(codex_session, [
            _exec_call_line("call_exec_mcp_a", "await tools.mcp__twicc__status({});"),
            _custom_output_line("call_exec_mcp_a", _RUNNING_CELL_2),
            _exec_call_line("call_exec_mcp_b", "await tools.mcp__twicc__info({});"),
            _custom_output_line("call_exec_mcp_b", _COMPLETED_ARRAY),
            _mcp_end_line("exec-11111111-2222-3333-4444-555555555555", "twicc", "status"),
        ])
        _run_batch_compute(codex_session)

        event_link = ToolResultLink.objects.get(
            session=codex_session, tool_result_line_num=5,
        )
        assert event_link.tool_use_id == "call_exec_mcp_a"

    def test_nested_mcp_dashed_server_matches_underscored_identifier(self, codex_session):
        """The invocation's raw server name (``chrome-devtools``) must match
        the JS identifier the script uses (``mcp__chrome_devtools__…`` —
        code mode rewrites non-identifier chars to ``_``). Real shape from
        session 019f4d27 line 215/216. Recency alone can't prove the match
        here, so an unrelated MCP exec sits in between."""
        _create_items(codex_session, [
            _exec_call_line(
                "call_exec_shot",
                'const r = await tools.mcp__chrome_devtools__take_screenshot({format:"png"});',
            ),
            _custom_output_line("call_exec_shot", _RUNNING_CELL_2),
            _exec_call_line("call_exec_other", "await tools.mcp__twicc__status({});"),
            _custom_output_line("call_exec_other", _COMPLETED_ARRAY),
            _mcp_end_line(
                "exec-99999999-0000-1111-2222-333333333333", "chrome-devtools", "take_screenshot",
            ),
        ])
        _run_batch_compute(codex_session)

        event_link = ToolResultLink.objects.get(
            session=codex_session, tool_result_line_num=5,
        )
        assert event_link.tool_use_id == "call_exec_shot"

    def test_failed_nested_mcp_surfaces_error_on_exec(self, codex_session):
        _create_items(codex_session, [
            _exec_call_line("call_exec_mcp_2", "await tools.mcp__twicc__status({});"),
            _mcp_end_line(
                "exec-66666666-7777-8888-9999-000000000000", "twicc", "status",
                result={"Err": "MCP server unreachable"},
            ),
            _custom_output_line("call_exec_mcp_2", _COMPLETED_ARRAY),
        ])
        _run_batch_compute(codex_session)

        event_link = ToolResultLink.objects.get(
            session=codex_session, tool_use_id="call_exec_mcp_2", tool_result_line_num=2,
        )
        assert event_link.error == "MCP server unreachable"

    def test_direct_mcp_end_pairing_unchanged(self, codex_session):
        """Regression: a direct MCP function_call's end event keeps
        binding to its own call (same call_id, no ``exec-`` prefix — the
        orphan remap never fires)."""
        _create_items(codex_session, [
            _codex_line("response_item", {
                "type": "function_call",
                "call_id": "call_mcp_direct_1",
                "name": "status",
                "namespace": "mcp__twicc",
                "arguments": "{}",
            }),
            _function_output_line("call_mcp_direct_1", "{}"),
            _mcp_end_line("call_mcp_direct_1", "twicc", "status"),
        ])
        _run_batch_compute(codex_session)

        links = ToolResultLink.objects.filter(
            session=codex_session, tool_use_id="call_mcp_direct_1",
        )
        assert links.count() == 2

    def test_direct_patch_apply_end_pairing_unchanged(self, codex_session):
        """5.5 regression: a direct apply_patch's event keeps binding to its
        own call (same call_id — the orphan remap never fires)."""
        _create_items(codex_session, [
            _codex_line("response_item", {
                "type": "custom_tool_call",
                "call_id": "call_patch_1",
                "name": "apply_patch",
                "input": "*** Begin Patch\n*** Add File: /tmp/z.txt\n+z\n*** End Patch",
            }),
            _custom_output_line("call_patch_1", "Done"),
            _file_change_line(
                "call_patch_1",
                {"/tmp/z.txt": {"type": "add", "content": "z"}},
            ),
        ])
        _run_batch_compute(codex_session)

        links = ToolResultLink.objects.filter(
            session=codex_session, tool_use_id="call_patch_1",
        )
        assert links.count() == 2


class TestCodeModeLiveRemap:
    def test_wait_output_remaps_to_exec_live(self, codex_session):
        script = 'await tools.exec_command({"cmd":"sleep 60"});'
        _create_items(codex_session, [
            _exec_call_line("call_exec_5", script),
            _custom_output_line("call_exec_5", _RUNNING_CELL_2),
            _wait_call_line("call_wait_2", "2"),
        ])
        wait_output_item = SessionItem.objects.create(
            session=codex_session, line_num=4,
            content=_function_output_line("call_wait_2", _COMPLETED_ARRAY),
        )
        compute = get_compute()
        remapped = compute.remap_tool_result_id_live(
            orjson.loads(wait_output_item.content),
            "call_wait_2",
            session_id=codex_session.id,
            item=wait_output_item,
        )
        assert remapped == "call_exec_5"

    def test_cell_id_prefix_does_not_cross_match(self, codex_session):
        """Cell "2" must not latch onto the exec owning cell "23"."""
        _create_items(codex_session, [
            _exec_call_line("call_exec_23", 'await tools.exec_command({"cmd":"a"});'),
            _custom_output_line(
                "call_exec_23",
                "Script running with cell ID 23\nWall time 1.0 seconds\nOutput:\n",
            ),
            _wait_call_line("call_wait_3", "2"),
        ])
        wait_output_item = SessionItem.objects.create(
            session=codex_session, line_num=4,
            content=_function_output_line("call_wait_3", _COMPLETED_ARRAY),
        )
        compute = get_compute()
        remapped = compute.remap_tool_result_id_live(
            orjson.loads(wait_output_item.content),
            "call_wait_3",
            session_id=codex_session.id,
            item=wait_output_item,
        )
        assert remapped == "call_wait_3"  # identity fallback, no cross-match

    def test_nested_patch_apply_end_remaps_to_exec_live(self, codex_session):
        script = (
            'const patch = "*** Begin Patch\\n*** Add File: toto3.txt\\n+x\\n*** End Patch";\n'
            "await tools.apply_patch(patch);"
        )
        _create_items(codex_session, [
            _exec_call_line("call_exec_7", script),
            _custom_output_line("call_exec_7", _RUNNING_CELL_2),
        ])
        event_item = SessionItem.objects.create(
            session=codex_session, line_num=3,
            content=_file_change_line(
                "exec-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                {"/repo/toto3.txt": {"type": "add", "content": "x"}},
            ),
        )
        compute = get_compute()
        remapped = compute.remap_tool_result_id_live(
            orjson.loads(event_item.content),
            "exec-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            session_id=codex_session.id,
            item=event_item,
        )
        assert remapped == "call_exec_7"

    def test_nested_mcp_end_remaps_to_exec_live(self, codex_session):
        _create_items(codex_session, [
            _exec_call_line("call_exec_mcp_live", "await tools.mcp__twicc__status({});"),
        ])
        event_item = SessionItem.objects.create(
            session=codex_session, line_num=2,
            content=_mcp_end_line(
                "exec-aaaaaaaa-bbbb-cccc-dddd-ffffffffffff", "twicc", "status",
            ),
        )
        compute = get_compute()
        remapped = compute.remap_tool_result_id_live(
            orjson.loads(event_item.content),
            "exec-aaaaaaaa-bbbb-cccc-dddd-ffffffffffff",
            session_id=codex_session.id,
            item=event_item,
        )
        assert remapped == "call_exec_mcp_live"

    def test_write_stdin_live_remap_still_works(self, codex_session):
        """Pre-5.6 regression: the generalized function_call lookup keeps
        resolving write_stdin chains."""
        _create_items(codex_session, [
            _codex_line("response_item", {
                "type": "function_call", "call_id": "call_ec_1",
                "name": "exec_command", "arguments": json.dumps({"cmd": "sleep 60"}),
            }),
            _function_output_line(
                "call_ec_1",
                "Process running with session ID 7\nWall time 1.0 seconds\nOutput:\n",
            ),
            _codex_line("response_item", {
                "type": "function_call", "call_id": "call_ws_1",
                "name": "write_stdin", "arguments": json.dumps({"session_id": 7, "chars": ""}),
            }),
        ])
        stdin_output_item = SessionItem.objects.create(
            session=codex_session, line_num=4,
            content=_function_output_line(
                "call_ws_1", "Process exited with code 0\nWall time 2.0 seconds\nOutput:\n",
            ),
        )
        compute = get_compute()
        remapped = compute.remap_tool_result_id_live(
            orjson.loads(stdin_output_item.content),
            "call_ws_1",
            session_id=codex_session.id,
            item=stdin_output_item,
        )
        assert remapped == "call_ec_1"

    def test_wrapped_write_stdin_output_remaps_to_wrapped_exec_command_live(
        self, codex_session,
    ):
        exec_script = (
            'const r = await tools.exec_command({"cmd":"sleep 60"});\n'
            'text(r.output);\nif (r.session_id) text(`SESSION_ID=${r.session_id}`);'
        )
        stdin_script = (
            'const r = await tools.write_stdin({"session_id":7,"chars":""});\ntext(r.output);'
        )
        _create_items(codex_session, [
            _exec_call_line("call_exec_wrapped", exec_script),
            _custom_output_line(
                "call_exec_wrapped",
                [
                    {"type": "input_text", "text": "Script completed\nWall time 1.0 seconds\nOutput:\n"},
                    {"type": "input_text", "text": "SESSION_ID=7\n"},
                ],
            ),
            _exec_call_line("call_stdin_wrapped", stdin_script),
        ])
        stdin_output_item = SessionItem.objects.create(
            session=codex_session,
            line_num=4,
            content=_custom_output_line("call_stdin_wrapped", _COMPLETED_ARRAY),
        )

        remapped = get_compute().remap_tool_result_id_live(
            orjson.loads(stdin_output_item.content),
            "call_stdin_wrapped",
            session_id=codex_session.id,
            item=stdin_output_item,
        )

        assert remapped == "call_exec_wrapped"

    def test_wrapped_write_stdin_finds_session_id_from_parent_wait_live(
        self, codex_session,
    ):
        exec_script = (
            'const r = await tools.exec_command({"cmd":"sleep 60","yield_time_ms":30000});\n'
            'text(r.output);\nif (r.session_id) text(`SESSION_ID=${r.session_id}`);'
        )
        stdin_script = (
            'const r = await tools.write_stdin({"session_id":7,"chars":""});\ntext(r.output);'
        )
        _create_items(codex_session, [
            _exec_call_line("call_exec_wrapped", exec_script),
            _custom_output_line("call_exec_wrapped", _RUNNING_CELL_2),
            _wait_call_line("call_wait_parent", "2"),
            _function_output_line(
                "call_wait_parent",
                [
                    {"type": "input_text", "text": "Script completed\nWall time 10.1 seconds\nOutput:\n"},
                    {"type": "input_text", "text": "SESSION_ID=7\n"},
                ],
            ),
            _exec_call_line("call_stdin_wrapped", stdin_script),
        ])
        stdin_output_item = SessionItem.objects.create(
            session=codex_session,
            line_num=6,
            content=_custom_output_line("call_stdin_wrapped", _COMPLETED_ARRAY),
        )

        remapped = get_compute().remap_tool_result_id_live(
            orjson.loads(stdin_output_item.content),
            "call_stdin_wrapped",
            session_id=codex_session.id,
            item=stdin_output_item,
        )

        assert remapped == "call_exec_wrapped"

    def test_wait_for_wrapped_write_stdin_remaps_transitively_live(self, codex_session):
        exec_script = (
            'const r = await tools.exec_command({"cmd":"sleep 60"});\n'
            'text(r.output);\nif (r.session_id) text(`SESSION_ID=${r.session_id}`);'
        )
        stdin_script = (
            'const r = await tools.write_stdin({"session_id":7,"chars":""});\ntext(r.output);'
        )
        _create_items(codex_session, [
            _exec_call_line("call_exec_wrapped", exec_script),
            _custom_output_line(
                "call_exec_wrapped",
                [
                    {"type": "input_text", "text": "Script completed\nWall time 1.0 seconds\nOutput:\n"},
                    {"type": "input_text", "text": "SESSION_ID=7\n"},
                ],
            ),
            _exec_call_line("call_stdin_wrapped", stdin_script),
            _custom_output_line(
                "call_stdin_wrapped",
                "Script running with cell ID 25\nWall time 10.0 seconds\nOutput:\n",
            ),
            _wait_call_line("call_wait_wrapped_stdin", "25"),
        ])
        wait_output_item = SessionItem.objects.create(
            session=codex_session,
            line_num=6,
            content=_function_output_line("call_wait_wrapped_stdin", _COMPLETED_ARRAY),
        )

        remapped = get_compute().remap_tool_result_id_live(
            orjson.loads(wait_output_item.content),
            "call_wait_wrapped_stdin",
            session_id=codex_session.id,
            item=wait_output_item,
        )

        assert remapped == "call_exec_wrapped"


class TestGetToolResults:
    def test_rebound_nested_end_events_survive_the_call_id_check(self, codex_session):
        """The API helper behind ``/tool-results/`` must return the nested
        end events the link table rebound to the exec — their payload
        ``call_id`` is the synthesized ``exec-<uuid>``, not the exec's
        (regression: they were filtered out, so the front never switched
        the Result to the structured MCP payload / patch event)."""
        from twicc.providers.helpers import get_provider_helpers

        items = [
            SessionItem(
                session=codex_session, line_num=2,
                content=_mcp_end_line("exec-8256675a-50d5-41b7-aed0-621f3e4354eb", "chrome-devtools", "take_screenshot"),
            ),
            SessionItem(
                session=codex_session, line_num=3,
                content=_custom_output_line("call_exec_api_1", _COMPLETED_ARRAY),
            ),
        ]
        helpers = get_provider_helpers(Provider.CODEX)
        results = helpers.get_tool_results(items, "call_exec_api_1")
        assert [r.get("type") for r in results] == ["McpToolCall", "custom_tool_call_output"]

    def test_direct_end_event_call_id_check_still_enforced(self, codex_session):
        """Defensive equality stays for direct calls: an event carrying a
        foreign non-``exec-`` call_id is still dropped."""
        from twicc.providers.helpers import get_provider_helpers

        items = [
            SessionItem(
                session=codex_session, line_num=2,
                content=_mcp_end_line("call_other", "twicc", "status"),
            ),
        ]
        helpers = get_provider_helpers(Provider.CODEX)
        assert helpers.get_tool_results(items, "call_mcp_direct_2") == []


class TestCodeModeDocEdits:
    def test_nested_exec_command_feeds_doc_edit_heuristic(self):
        compute = get_compute()
        script = (
            'const r = await tools.exec_command({"cmd":"cat > docs/plans/2026-07-10-foo-design.md <<\'EOF\'\\nx\\nEOF",'
            '"workdir":"/repo"});\ntext(r.output);'
        )
        parsed = orjson.loads(_exec_call_line("call_doc_1", script))
        events = compute.extract_doc_edit_events(parsed, cwd="/elsewhere")
        assert events == [DocEditEvent("/repo/docs/plans/2026-07-10-foo-design.md", "write")]

    def test_unresolved_script_produces_no_events(self):
        compute = get_compute()
        parsed = orjson.loads(_exec_call_line("call_doc_2", "await tools.exec_command(buildArgs());"))
        assert compute.extract_doc_edit_events(parsed, cwd="/repo") == []


class TestPre56Regression:
    def test_direct_exec_command_pair_unchanged(self, codex_session):
        """A plain 5.5-style exec_command call/output computes exactly as
        before: TOOL_USE kind, single link, exit-code error handling."""
        _create_items(codex_session, [
            _codex_line("response_item", {
                "type": "function_call", "call_id": "call_ec_2",
                "name": "exec_command", "arguments": json.dumps({"cmd": "false"}),
            }),
            _function_output_line(
                "call_ec_2",
                "Process exited with code 1\nWall time 0.1 seconds\nOutput:\n",
            ),
        ])
        _run_batch_compute(codex_session)

        link = ToolResultLink.objects.get(session=codex_session, tool_use_id="call_ec_2")
        assert link.tool_name == "exec_command"
        assert link.error == "Exit code 1"
        assert json.loads(link.extra) == {"is_terminated": True}
