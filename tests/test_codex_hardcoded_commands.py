"""Codex hardcoded slash commands: parsing, the ``/plan`` agent action, and
the compute-side ``/plan <prompt>`` prefix restoration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from openai_codex.generated.v2_all import CollaborationMode, ModeKind, Settings

from twicc.agent import AgentState
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import Project, Session, SessionItem
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.hardcoded_commands import (
    HardcodedCommand,
    parse_hardcoded_command,
)
from twicc.providers.codex.canonical import agent_message_text, user_message_text
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
from twicc.providers.helpers import AgentSettings


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------


def test_parse_bare_plan() -> None:
    assert parse_hardcoded_command("/plan") == HardcodedCommand("plan", "")


def test_parse_plan_surrounding_whitespace() -> None:
    assert parse_hardcoded_command("  /plan \n") == HardcodedCommand("plan", "")


def test_parse_plan_with_prompt() -> None:
    parsed = parse_hardcoded_command("/plan Propose a migration plan for this service")
    assert parsed == HardcodedCommand("plan", "Propose a migration plan for this service")


def test_parse_plan_with_multiline_prompt() -> None:
    parsed = parse_hardcoded_command("/plan\nFirst line\nSecond line")
    assert parsed == HardcodedCommand("plan", "First line\nSecond line")


def test_parse_planning_is_ordinary_text() -> None:
    assert parse_hardcoded_command("/planning the sprint") is None


def test_parse_plan_like_path_is_ordinary_text() -> None:
    assert parse_hardcoded_command("/plan/notes.md") is None


# ----------------------------------------------------------------------
# ``/plan`` agent action
# ----------------------------------------------------------------------


def _make_agent(monkeypatch: pytest.MonkeyPatch, *, sdk_model: str | None = "gpt-5.6") -> CodexAgent:
    """Build a ``CodexAgent`` on fully mocked SDK objects.

    The thread is an ``AsyncMock`` so the RPC helpers can be asserted; the
    model resolver is stubbed so the test controls the resolved SDK model
    without touching synced settings.
    """
    monkeypatch.setattr(
        "twicc.providers.codex.agent.agent.get_provider_helpers",
        lambda provider: SimpleNamespace(resolve_sdk_model=lambda selected: sdk_model),
    )
    agent = CodexAgent(
        "session-id",
        "project-id",
        "/tmp",
        AgentSettings(selected_model="gpt-terra", effort="high", permission_mode="auto"),
        MagicMock(),
        AsyncMock(),
    )
    agent._broadcast_stream_event = AsyncMock()
    agent._schedule_turn = MagicMock()
    return agent


def _emitted_events(agent: CodexAgent) -> list[str]:
    return [call.args[0]["type"] for call in agent._broadcast_stream_event.await_args_list]


def test_bare_plan_enters_plan_mode_and_settles_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(monkeypatch)

    asyncio.run(agent.run_hardcoded_command(HardcodedCommand("plan", "")))

    agent._thread.update_settings_with_policy.assert_awaited_once_with(
        collaboration_mode=CollaborationMode(
            mode=ModeKind.plan,
            settings=Settings(
                model="gpt-5.6",
                reasoning_effort=None,
                developer_instructions=None,
            ),
        ),
    )
    # Durable transcript marker + settled idle + optimistic bubble retired.
    agent._thread.inject_user_message.assert_awaited_once_with("/plan")
    assert agent.state == AgentState.USER_TURN
    assert _emitted_events(agent) == ["plan_command_done"]
    agent._schedule_turn.assert_not_called()


def test_plan_with_prompt_schedules_a_normal_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(monkeypatch)

    asyncio.run(agent.run_plan_command("Propose a migration plan"))

    agent._thread.update_settings_with_policy.assert_awaited_once()
    # The prompt runs as an ordinary turn — never the literal "/plan" prefix —
    # and the turn's own user_message line makes the injected marker useless.
    agent._schedule_turn.assert_called_once_with("Propose a migration plan", None)
    assert agent.state == AgentState.ASSISTANT_TURN
    agent._thread.inject_user_message.assert_not_awaited()
    assert _emitted_events(agent) == []


def test_plan_rpc_failure_recovers_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(monkeypatch)
    agent._thread.update_settings_with_policy.side_effect = Exception("boom")

    with pytest.raises(RuntimeError, match="/plan failed: boom"):
        asyncio.run(agent.run_plan_command("do it"))

    # Never left stuck: settled back to USER_TURN, bubble retired, no turn.
    assert agent.state == AgentState.USER_TURN
    assert _emitted_events(agent) == ["plan_command_done"]
    agent._schedule_turn.assert_not_called()
    agent._thread.inject_user_message.assert_not_awaited()


def test_bare_plan_marker_injection_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(monkeypatch)
    agent._thread.inject_user_message.side_effect = Exception("inject failed")

    # The mode switch already succeeded — a failed marker must not raise.
    asyncio.run(agent.run_plan_command(""))

    assert agent.state == AgentState.USER_TURN
    assert _emitted_events(agent) == ["plan_command_done"]


def test_plan_without_resolved_model_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(monkeypatch, sdk_model=None)

    with pytest.raises(RuntimeError, match="model resolved"):
        asyncio.run(agent.run_plan_command(""))

    agent._thread.update_settings_with_policy.assert_not_awaited()
    assert agent.state == AgentState.USER_TURN
    assert _emitted_events(agent) == ["plan_command_done"]


# ----------------------------------------------------------------------
# Post-plan "Implement this plan?" prompt
# ----------------------------------------------------------------------


def _plan_completed_event(session_id: str = "session-id") -> SimpleNamespace:
    return SimpleNamespace(
        method="item/completed",
        payload=SimpleNamespace(
            thread_id=session_id,
            item=SimpleNamespace(root=SimpleNamespace(type="plan", id="turn-1-plan")),
        ),
    )


def test_plan_item_completed_arms_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_agent(monkeypatch)
    assert agent._plan_item_this_turn is False

    asyncio.run(agent._handle_stream_event(_plan_completed_event()))

    assert agent._plan_item_this_turn is True


@pytest.mark.parametrize("decision", ["stay", "newSession"])
def test_plan_prompt_stay_settles_in_plan_mode(
    monkeypatch: pytest.MonkeyPatch, decision: str,
) -> None:
    # ``newSession`` is agent-side identical to ``stay``: the frontend owns
    # the fresh-session creation.
    agent = _make_agent(monkeypatch)
    agent._await_pending_request = AsyncMock(return_value={"decision": decision})
    agent._run_turn = AsyncMock()

    asyncio.run(agent._prompt_plan_implementation())

    request = agent._await_pending_request.await_args.args[0]
    assert request.tool_name == "planImplementation"
    assert request.request_type == "ask_user_question"
    assert agent.state == AgentState.USER_TURN
    agent._run_turn.assert_not_awaited()
    agent._thread.update_settings_with_policy.assert_not_awaited()


def test_plan_prompt_implement_switches_default_and_runs_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_agent(monkeypatch)
    agent._await_pending_request = AsyncMock(return_value={"decision": "implement"})
    agent._run_turn = AsyncMock()

    asyncio.run(agent._prompt_plan_implementation())

    agent._thread.update_settings_with_policy.assert_awaited_once_with(
        collaboration_mode=CollaborationMode(
            mode=ModeKind.default,
            settings=Settings(
                model="gpt-5.6",
                reasoning_effort=None,
                developer_instructions=None,
            ),
        ),
    )
    agent._run_turn.assert_awaited_once_with("Implement the plan.", None)


def test_plan_prompt_mode_switch_failure_settles_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The implement turn must never run while still in Plan mode (mutations
    # are blocked there): a failed switch settles idle instead.
    agent = _make_agent(monkeypatch)
    agent._await_pending_request = AsyncMock(return_value={"decision": "implement"})
    agent._run_turn = AsyncMock()
    agent._thread.update_settings_with_policy.side_effect = Exception("boom")

    asyncio.run(agent._prompt_plan_implementation())

    assert agent.state == AgentState.USER_TURN
    agent._run_turn.assert_not_awaited()


def test_plan_implementation_ws_response_validation() -> None:
    from twicc.providers.codex.ws import CodexWSHandler

    # Bypass __init__ (needs a live consumer); the validators only touch
    # class-level constants.
    handler = object.__new__(CodexWSHandler)
    build = CodexWSHandler._build_codex_response
    assert build(handler, "planImplementation", {"decision": "implement"}) == {"decision": "implement"}
    assert build(handler, "planImplementation", {"decision": "stay"}) == {"decision": "stay"}
    assert build(handler, "planImplementation", {"decision": "newSession"}) == {"decision": "newSession"}
    assert build(handler, "planImplementation", {"decision": "accept"}) is None
    assert CodexWSHandler._safe_default_for(handler, "planImplementation") == {"decision": "stay"}


# ----------------------------------------------------------------------
# Compute: ``/plan <prompt>`` prefix restoration
# ----------------------------------------------------------------------


def _codex_line(wrapper_type: str, payload: dict) -> dict:
    return {"timestamp": "2026-07-16T15:00:00.000Z", "type": wrapper_type, "payload": payload}


def _turn_context(mode: str) -> dict:
    return _codex_line("turn_context", {
        "turn_id": "turn-1",
        "collaboration_mode": {
            "mode": mode,
            "settings": {
                "model": "gpt-5.6",
                "reasoning_effort": None,
                "developer_instructions": None,
            },
        },
    })


def _user_message(text: str) -> dict:
    return _codex_line("event_msg", {
        "type": "item_completed",
        "thread_id": _COMPUTE_SESSION,
        "turn_id": "turn-1",
        "completed_at_ms": 1784214000000,
        "item": {
            "type": "UserMessage",
            "id": "message-1",
            "content": [{"type": "text", "text": text, "text_elements": []}],
        },
    })


def _injected_plan_marker() -> dict:
    # The raw ``thread/inject_items`` shape a bare ``/plan`` writes.
    return _codex_line("response_item", {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "/plan"}],
    })


_COMPUTE_SESSION = "compute-session"


def _batch_compute() -> CodexSessionCompute:
    compute = CodexSessionCompute()
    compute.begin_session_compute(_COMPUTE_SESSION)
    return compute


def _transform_all(
    compute: CodexSessionCompute, lines: list[dict], session_id: str = _COMPUTE_SESSION,
) -> list[str | None]:
    return [
        compute.transform_inline(line, session_id=session_id, line_num=num)
        for num, line in enumerate(lines, 1)
    ]


def test_inline_prompt_user_message_is_reprefixed() -> None:
    results = _transform_all(_batch_compute(), [
        _turn_context("default"),
        _user_message("hi"),
        _turn_context("plan"),           # transition → arms the prefix
        _user_message("do the thing"),   # the /plan <prompt> inline prompt
        _user_message("follow-up"),      # steered message, already disarmed
        _turn_context("plan"),           # sticky plan→plan: no transition
        _user_message("another message"),
    ])

    rewritten_lines = [num for num, result in enumerate(results, 1) if result is not None]
    assert rewritten_lines == [4]
    parsed = orjson.loads(results[3])
    assert user_message_text(parsed) == "/plan do the thing"
    assert parsed["twiccPlanCommand"] is True


def test_inline_prompt_prefix_preserves_multiple_text_blocks() -> None:
    message = _user_message("first ")
    message["payload"]["item"]["content"].append({
        "type": "text",
        "text": "second",
        "text_elements": [],
    })

    results = _transform_all(_batch_compute(), [
        _turn_context("plan"),
        message,
    ])

    parsed = orjson.loads(results[1])
    content = parsed["payload"]["item"]["content"]
    assert [entry["text"] for entry in content] == ["/plan first ", "second"]
    assert user_message_text(parsed) == "/plan first second"


def test_inline_prompt_prefix_on_an_attachment_only_prompt() -> None:
    # An image-only prompt has no text entry: the prefix becomes a bare
    # ``/plan`` text entry in front of the attachments, and the armed state
    # is consumed (the next prompt must NOT be prefixed).
    image_only = _user_message("")
    image_only["payload"]["item"]["content"] = [
        {"type": "image", "image_url": "data:image/png;base64,AA"},
    ]
    results = _transform_all(_batch_compute(), [
        _turn_context("default"),
        _turn_context("plan"),
        image_only,
        _user_message("second prompt"),
    ])

    parsed = orjson.loads(results[2])
    content = parsed["payload"]["item"]["content"]
    assert content[0] == {"type": "text", "text": "/plan", "text_elements": []}
    assert content[1]["type"] == "image"
    assert parsed["twiccPlanCommand"] is True
    assert results[3] is None


def test_plan_transition_on_a_new_drafts_first_turn() -> None:
    # ``/plan <prompt>`` as the first input of a brand-new session: the very
    # first turn_context is already in plan mode.
    results = _transform_all(_batch_compute(), [
        _turn_context("plan"),
        _user_message("first prompt"),
    ])

    assert user_message_text(orjson.loads(results[1])) == "/plan first prompt"


def test_bare_plan_marker_suppresses_the_prefix() -> None:
    results = _transform_all(_batch_compute(), [
        _turn_context("default"),
        _injected_plan_marker(),          # bare /plan → relabelled marker
        _turn_context("plan"),            # transition announced by the marker
        _user_message("ordinary message"),
    ])

    marker = orjson.loads(results[1])
    assert marker["type"] == "event_msg"
    assert user_message_text(marker) == "/plan"
    assert results[3] is None


def test_reprefix_is_idempotent_on_recompute() -> None:
    first_pass = _transform_all(_batch_compute(), [
        _turn_context("plan"),
        _user_message("prompt"),
    ])
    stored = orjson.loads(first_pass[1])

    # A later batch re-compute replays the arming turn_context from the file
    # and then meets the already-rewritten line: the flag must stop a second
    # "/plan " prefix.
    second_pass = _transform_all(_batch_compute(), [_turn_context("plan"), stored])

    assert second_pass[1] is None


def _assistant_response_item(text: str) -> dict:
    return _codex_line("response_item", {
        "type": "message",
        "id": "msg-1",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    })


def test_proposed_plan_response_item_becomes_a_visible_agent_message() -> None:
    # A Plan-mode final answer has no ``event_msg.agent_message`` twin: the
    # ``response_item`` (normally a hidden duplicate) must be relabelled so
    # the plan shows in the transcript.
    text = "Intro.\n<proposed_plan>\n# Title\n\n- step\n</proposed_plan>"
    result = _batch_compute().transform_inline(
        _assistant_response_item(text), session_id=_COMPUTE_SESSION, line_num=1,
    )

    parsed = orjson.loads(result)
    assert parsed["type"] == "event_msg"
    assert agent_message_text(parsed) == text
    assert parsed["twiccOriginalContent"]["role"] == "assistant"


def test_injected_goal_clear_is_classified_from_the_rewritten_item() -> None:
    # ``transform_inline`` mutates the caller's dict: kind, goal event and
    # title are computed on that same dict afterwards. A rewrite that only
    # returned a copy would store a visible ``/goal clear`` yet classify the
    # raw ``response_item`` as SYSTEM and never clear the goal.
    from twicc.core.enums import ItemKind

    line = _codex_line("response_item", {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "/goal clear"}],
    })
    compute = _batch_compute()
    result = compute.transform_inline(line, session_id=_COMPUTE_SESSION, line_num=1)

    assert result is not None
    assert user_message_text(line) == "/goal clear"
    assert compute.compute_item_kind(line) == ItemKind.USER_MESSAGE
    event = compute.extract_goal_event(line)
    assert event is not None and event.cleared is True
    assert orjson.loads(result) == line


def test_injected_compact_is_a_visible_user_message_after_transform() -> None:
    from twicc.core.enums import ItemKind

    line = _codex_line("response_item", {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "/compact"}],
    })
    compute = _batch_compute()
    compute.transform_inline(line, session_id=_COMPUTE_SESSION, line_num=1)

    assert compute.compute_item_kind(line) == ItemKind.USER_MESSAGE


def test_proposed_plan_is_classified_as_assistant_message_after_transform() -> None:
    from twicc.core.enums import ItemKind

    line = _assistant_response_item("Intro.\n<proposed_plan>\n# Title\n\n- step\n</proposed_plan>")
    compute = _batch_compute()
    compute.transform_inline(line, session_id=_COMPUTE_SESSION, line_num=1)

    assert compute.compute_item_kind(line) == ItemKind.ASSISTANT_MESSAGE


def test_ordinary_assistant_response_item_stays_hidden() -> None:
    # The normal duplicate of an ``event_msg.agent_message`` must keep its
    # SYSTEM classification — no relabel without a ``<proposed_plan>`` block.
    result = _batch_compute().transform_inline(
        _assistant_response_item("A normal answer mentioning <proposed_plan> inline."),
        session_id=_COMPUTE_SESSION, line_num=1,
    )

    assert result is None


@pytest.fixture
def plan_compute_session(db):
    project = Project.objects.create(id="test-project-plan-prefix")
    return Session.objects.create(
        id="test-session-plan-prefix",
        project=project,
        provider=Provider.CODEX,
    )


def _persist_line(session: Session, line_num: int, parsed: dict) -> None:
    SessionItem.objects.create(
        session=session,
        line_num=line_num,
        content=orjson.dumps(parsed).decode(),
    )


def test_live_seed_detects_the_transition_from_db(plan_compute_session) -> None:
    # Fresh process (no in-memory state): the previous turn's mode comes from
    # the persisted items.
    session = plan_compute_session
    _persist_line(session, 1, _turn_context("default"))
    compute = CodexSessionCompute()

    assert compute.transform_inline(
        _turn_context("plan"), session_id=session.id, line_num=10,
    ) is None
    result = compute.transform_inline(
        _user_message("live prompt"), session_id=session.id, line_num=11,
    )

    assert user_message_text(orjson.loads(result)) == "/plan live prompt"


def test_live_seed_sees_a_persisted_bare_marker(plan_compute_session) -> None:
    # Bare /plan on a cold session, backend restarted before the next turn:
    # the relabelled marker persisted between the turn_contexts must suppress
    # the prefix.
    session = plan_compute_session
    _persist_line(session, 1, _turn_context("default"))
    marker_content = _batch_compute().transform_inline(
        _injected_plan_marker(), session_id=_COMPUTE_SESSION, line_num=2,
    )
    SessionItem.objects.create(session=session, line_num=2, content=marker_content)
    compute = CodexSessionCompute()

    assert compute.transform_inline(
        _turn_context("plan"), session_id=session.id, line_num=10,
    ) is None
    assert compute.transform_inline(
        _user_message("hello"), session_id=session.id, line_num=11,
    ) is None


def test_live_seed_sticky_plan_is_not_a_transition(plan_compute_session) -> None:
    session = plan_compute_session
    _persist_line(session, 1, _turn_context("plan"))
    compute = CodexSessionCompute()

    assert compute.transform_inline(
        _turn_context("plan"), session_id=session.id, line_num=10,
    ) is None
    assert compute.transform_inline(
        _user_message("hello"), session_id=session.id, line_num=11,
    ) is None


# ----------------------------------------------------------------------
# Goal continuations: transcript boundaries + completion evidence
# ----------------------------------------------------------------------


def _thread_goal_updated(status: str, objective: str = "Count to five") -> dict:
    return _codex_line("event_msg", {
        "type": "thread_goal_updated",
        "goal": {
            "threadId": _COMPUTE_SESSION,
            "objective": objective,
            "status": status,
        },
    })


def _goal_context(objective: str = "Count to five") -> dict:
    return _codex_line("response_item", {
        "type": "message",
        "role": "user",
        "content": [{
            "type": "input_text",
            "text": (
                '<codex_internal_context source="goal">\n'
                f"<objective>\n{objective}\n</objective>\n"
                "</codex_internal_context>"
            ),
        }],
    })


def _old_visible_goal_context(objective: str = "Count to five") -> dict:
    native = _goal_context(objective)
    return {
        "timestamp": native["timestamp"],
        "type": "event_msg",
        "payload": {"type": "user_message", "message": f"/goal {objective}"},
        "twiccOriginalContent": native["payload"],
    }


def _goal_tool_result(status: str = "complete") -> dict:
    result = {
        "goal": {
            "threadId": _COMPUTE_SESSION,
            "objective": "Count to five",
            "status": status,
            "tokensUsed": 123,
        },
        "remainingTokens": None,
        "completionBudgetReport": "Goal achieved.",
    }
    return _codex_line("response_item", {
        "type": "custom_tool_call_output",
        "call_id": "call-goal",
        "output": [
            {"type": "input_text", "text": "Script completed\nWall time 0.0 seconds\nOutput:\n"},
            {"type": "input_text", "text": orjson.dumps(result).decode()},
        ],
    })


def test_only_first_context_after_goal_activation_is_a_user_message() -> None:
    compute = _batch_compute()
    lines = [
        _thread_goal_updated("active"),
        _goal_context(),
        _goal_context(),
        _goal_context(),
    ]
    results = _transform_all(compute, lines)

    first = orjson.loads(results[1])
    assert user_message_text(first) == "/goal Count to five"
    assert compute.compute_item_kind(first) == ItemKind.USER_MESSAGE
    assert results[2:] == [None, None]
    assert all(compute.compute_item_kind(line) == ItemKind.SYSTEM for line in lines[2:])


def test_recompute_demotes_previously_rewritten_goal_continuations() -> None:
    compute = _batch_compute()
    lines = [
        _thread_goal_updated("active"),
        _old_visible_goal_context(),
        _old_visible_goal_context(),
    ]
    results = _transform_all(compute, lines)

    first = orjson.loads(results[1])
    assert user_message_text(first) == "/goal Count to five"
    demoted = orjson.loads(results[2])
    assert demoted["type"] == "response_item"
    assert "twiccOriginalContent" not in demoted
    assert compute.compute_item_kind(demoted) == ItemKind.SYSTEM


def test_goal_objective_update_arms_another_visible_boundary() -> None:
    compute = _batch_compute()
    lines = [
        _thread_goal_updated("active", "First objective"),
        _goal_context("First objective"),
        _goal_context("First objective"),
        _thread_goal_updated("active", "Revised objective"),
        _goal_context("Revised objective"),
    ]
    results = _transform_all(compute, lines)

    assert user_message_text(orjson.loads(results[1])) == "/goal First objective"
    assert results[2] is None
    assert user_message_text(orjson.loads(results[4])) == "/goal Revised objective"


def test_live_seed_hides_a_continuation_after_backend_restart(plan_compute_session) -> None:
    session = plan_compute_session
    _persist_line(session, 1, _thread_goal_updated("active"))
    _persist_line(session, 2, _old_visible_goal_context())
    compute = CodexSessionCompute()

    duplicate = _old_visible_goal_context()
    result = compute.transform_inline(duplicate, session_id=session.id, line_num=10)

    assert orjson.loads(result)["type"] == "response_item"


def test_structured_goal_tool_result_completes_lifecycle_and_continuation() -> None:
    compute = _batch_compute()
    result = _goal_tool_result("complete")

    event = compute.extract_goal_event(result)

    assert event is not None
    assert event.state == "completed"
    assert event.raw_state == "complete"
    assert compute.is_goal_continuation_stopped(result) is True


def test_active_goal_tool_result_does_not_stop_continuation() -> None:
    compute = _batch_compute()
    result = _goal_tool_result("active")

    assert compute.is_goal_continuation_stopped(result) is False


@pytest.mark.django_db(transaction=True)
def test_goal_tool_result_notifies_the_live_continuation(plan_compute_session) -> None:
    session = plan_compute_session
    _persist_line(session, 1, _goal_tool_result("complete"))
    manager = SimpleNamespace(notify_goal_continuation_stopped=AsyncMock())

    asyncio.run(CodexSessionsWatcher()._check_goal_continuation_end(
        manager, session.id, [1],
    ))

    manager.notify_goal_continuation_stopped.assert_awaited_once_with(session.id)


def test_goal_stop_notification_returns_parked_agent_to_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_agent(monkeypatch)
    agent._goal_continuation_active = True
    agent._set_state(AgentState.ASSISTANT_TURN)
    agent._notify_state_change = AsyncMock()

    asyncio.run(agent.notify_goal_continuation_stopped())

    assert agent.state == AgentState.USER_TURN
    assert agent.in_goal_continuation() is False
    agent._notify_state_change.assert_awaited_once()
