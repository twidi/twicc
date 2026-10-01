"""Side-effect-free Codex history evidence using the existing provider predicates."""

from twicc.providers.history_facts import HistoryFact, HistoryFactContext, HistoryFactKind


def _identifier(value):
    return value if isinstance(value, str) and value.strip() else None


def tool_calls(parsed: dict):
    from .compute import _payload, _TOOL_CALL_PAYLOAD_TYPES

    if parsed.get("type") != "response_item":
        return
    payload = _payload(parsed)
    if payload is not None and payload.get("type") in _TOOL_CALL_PAYLOAD_TYPES:
        identifier = _identifier(payload.get("call_id"))
        if identifier is not None:
            yield identifier, payload


def code_cell(parsed: dict) -> tuple[str, str] | None:
    from .compute import _payload, parse_code_mode_output

    if parsed.get("type") != "response_item":
        return None
    payload = _payload(parsed)
    if payload is None or payload.get("type") != "custom_tool_call_output":
        return None
    identifier = _identifier(payload.get("call_id"))
    status = parse_code_mode_output(payload.get("output"))
    if identifier is not None and status is not None and _identifier(status.cell_id) is not None:
        return status.cell_id, identifier
    return None


def _process_owner(parsed: dict, line_num: int, history: HistoryFactContext):
    from .compute import (
        _payload,
        _TOOL_RESULT_PAYLOAD_TYPES,
        _tool_use_name,
        parse_code_mode_output,
        parse_exec_command_status,
        _code_mode_exec_command_id_from_output,
        _wait_cell_id_from_payload,
        _resolved_code_mode_nested_args,
    )

    if parsed.get("type") != "response_item":
        return None
    payload = _payload(parsed)
    if payload is None or payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
        return None
    call_id = _identifier(payload.get("call_id"))
    if call_id is None:
        return None
    output = payload.get("output")
    if isinstance(output, str) and parse_code_mode_output(output) is None:
        status = parse_exec_command_status(output)
        if status.exec_command_id is None or status.is_terminated:
            return None
        owner = history.lookup_tool_call(call_id, before_line=line_num)
        if owner is not None and _tool_use_name(owner[0]) == "exec_command":
            return status.exec_command_id, call_id, owner[1]
        return None
    process_id = _code_mode_exec_command_id_from_output(output)
    if process_id is None:
        return None
    owner = history.lookup_tool_call(call_id, before_line=line_num)
    if owner is not None and _tool_use_name(owner[0]) == "wait":
        cell_id = _wait_cell_id_from_payload(owner[0])
        cell = history.lookup_code_cell(cell_id, before_line=line_num) if cell_id is not None else None
        if cell is not None:
            call_id = cell[0]
            # All three queries use the process-output line. An owner ID
            # reused after its cell announcement must select that newer call.
            owner = history.lookup_tool_call(call_id, before_line=line_num)
    nested = _resolved_code_mode_nested_args(owner[0] if owner else None, "exec_command")
    if nested is not None and isinstance(nested.get("cmd"), str):
        return process_id, call_id, owner[1]
    return None


def extract_history_facts(parsed: dict, *, line_num: int, history: HistoryFactContext) -> list[HistoryFact]:
    from .compute import (
        _payload,
        _tool_use_name,
        _script_targets,
        _parse_sub_agent_activity_started,
        _turn_context_collaboration_mode,
        _goal_context_objective,
        _injected_command_text,
        _restore_private_source,
        user_message_text,
        task_started_turn_id,
    )

    facts = []

    def add(kind, key, data):
        facts.append(HistoryFact(line_num, kind, key, data))

    evidence = history.record_evidence(line_num=line_num)
    for call_id, payload in tool_calls(parsed):
        name = evidence.get("tool_name")
        if name is None:
            name = _tool_use_name(payload)
        add(
            HistoryFactKind.TOOL_CALL,
            call_id,
            {
                "source_line": line_num,
                "tool_name": name,
                "payload_type": payload["type"],
            },
        )
        if payload.get("type") == "custom_tool_call" and name == "exec":
            targets = evidence.get("code_exec_targets")
            if targets is None:
                targets = _script_targets(payload.get("input"))
            if targets.has_patch:
                add(
                    HistoryFactKind.CODE_EXEC_TARGET,
                    "patch",
                    {"call_id": call_id, "paths": sorted(targets.patch_paths)},
                )
            if targets.mcp_tools:
                add(HistoryFactKind.CODE_EXEC_TARGET, "mcp", {"call_id": call_id, "tools": sorted(targets.mcp_tools)})
    cell = code_cell(parsed)
    if cell is not None:
        add(HistoryFactKind.CODE_CELL, cell[0], {"call_id": cell[1]})
    owner = _process_owner(parsed, line_num, history)
    if owner is not None:
        add(HistoryFactKind.PROCESS_START, str(owner[0]), {"call_id": owner[1], "call_line": owner[2]})
    spawn = _parse_sub_agent_activity_started(parsed)
    if spawn is not None and all(_identifier(value) is not None for value in spawn):
        add(HistoryFactKind.AGENT_SPAWN, spawn.agent_path, {"call_id": spawn.call_id, "agent_id": spawn.agent_id})
    turn_id = task_started_turn_id(parsed)
    if _identifier(turn_id) is not None:
        add(HistoryFactKind.TURN_START, turn_id, {"source_line": line_num})
    mode = _turn_context_collaboration_mode(parsed)
    if mode is not None:
        add(HistoryFactKind.TURN_CONTEXT, "context", {"source_line": line_num, "mode": mode})
    if user_message_text(parsed) == "/plan" or _injected_command_text(_restore_private_source(parsed)) == "/plan":
        add(HistoryFactKind.PLAN_MARKER, "context", {"source_line": line_num})
    if _goal_context_objective(parsed) is not None:
        add(HistoryFactKind.GOAL_CONTEXT, "context", {"source_line": line_num})
    payload = _payload(parsed)
    if parsed.get("type") == "event_msg" and payload is not None:
        if payload.get("type") == "thread_goal_updated" and isinstance(payload.get("goal"), dict):
            add(HistoryFactKind.GOAL_UPDATE, "context", {"source_line": line_num})
        if payload.get("type") == "token_count":
            info = payload.get("info")
            total = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(total, dict):
                add(HistoryFactKind.TOKEN_USAGE, "context", {"source_line": line_num})
    return facts
