"""Claude agent-run signals: control calls, first results, run ends, interrupts.

The Claude provider writes ``AgentInteraction`` rows for ``SendMessage`` /
``TaskStop`` / ``TaskOutput`` calls and ``AgentRunEnd`` rows for
``<task-notification>`` ends and the subagent interrupt marker, through the
batch hook ``collect_agent_run_signals`` and the live hook
``apply_agent_run_signals``. Scenario tests run on both paths (the ``mode``
fixture) and assert the rows and the resulting ``agent_run_states``. Design:
``docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md`` §4.1,
§5.1, §5.2, §6.1 and §9.
"""
from datetime import UTC, datetime, timedelta
from queue import Queue

import orjson
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from twicc.core.agent_runs import agent_run_states
from twicc.core.enums import Provider
from twicc.core.models import (
    AgentInteraction,
    AgentInteractionKind,
    AgentLink,
    AgentRunEnd,
    AgentRunEndSource,
    Project,
    Session,
    SessionItem,
    SessionType,
    ToolResultLink,
)
from twicc.providers.claude_code.agent_runs import (
    ControlCall,
    RunEndNotification,
    control_calls,
    is_plain_interrupt_marker,
    run_end_notification,
    send_message_opens_run,
)
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute

T0 = datetime(2026, 9, 27, 10, tzinfo=UTC)

AGENT = "a1111111111111111"  # spawned by the root
OTHER = "a2222222222222222"  # a subagent that sends control calls
LEGACY = "a1b2c3d"  # legacy 7-character agent id
SHELL = "b12ab34cd"
INTERRUPT_TEXT = "[Request interrupted by user]"


def at(seconds):
    return T0 + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Line builders (shapes from design §4.1)
# ---------------------------------------------------------------------------


def line(role, content, seconds, **extra):
    return {"type": role, "timestamp": at(seconds).isoformat(), "message": {"role": role, "content": content},
            **extra}


def calls(seconds, *blocks):
    """One assistant line holding several tool_use blocks ``(id, name, input)``."""
    return line("assistant", [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}
                              for tool_id, name, tool_input in blocks], seconds)


def spawn(tool, seconds):
    return calls(seconds, (tool, "Agent", {"prompt": f"work for {tool}", "run_in_background": True}))


def ack(tool, agent, seconds):
    return line("user", [{"type": "tool_result", "tool_use_id": tool,
                          "content": f"Async agent launched successfully.\nagentId: {agent} (internal)"}], seconds)


def send(tool, to, seconds):
    return calls(seconds, (tool, "SendMessage", {"to": to, "summary": "s", "message": "go on"}))


def task_stop(tool, task_id, seconds):
    return calls(seconds, (tool, "TaskStop", {"task_id": task_id}))


def task_output(tool, task_id, seconds):
    return calls(seconds, (tool, "TaskOutput", {"task_id": task_id, "block": True, "timeout": 1000}))


def result(tool, content, seconds, tool_use_result=None, is_error=False, **extra):
    block = {"type": "tool_result", "tool_use_id": tool, "content": content}
    if is_error:
        block["is_error"] = True
    entry = line("user", [block], seconds, **extra)
    if tool_use_result is not None:
        entry["toolUseResult"] = tool_use_result
    return entry


def resumed_ack(tool, agent, seconds, message=None):
    """Root-side resumed shape: ``toolUseResult`` with ``resumedAgentId``."""
    payload = {"success": True, "message": message or f"Resuming agent {agent[:7]}", "resumedAgentId": agent,
               "pin": {"id": agent, "name": "n", "ref": "r"}}
    return result(tool, orjson.dumps(payload).decode(), seconds, tool_use_result=payload)


def text_resumed_ack(tool, agent, seconds):
    """Subagent-side resumed shape: JSON text only, no ``resumedAgentId``."""
    payload = {"success": True, "message": f"Resuming agent {agent[:7]}", "pin": {"id": agent, "name": "n"}}
    return result(tool, orjson.dumps(payload).decode(), seconds)


def queued_ack(tool, agent, seconds):
    payload = {"success": True, "message": f"Message queued for delivery to {agent} at its next tool round.",
               "pin": {"id": agent}}
    return result(tool, orjson.dumps(payload).decode(), seconds, tool_use_result=payload)


def failed_send_ack(tool, seconds):
    payload = {"success": False, "message": "No agent named parent"}
    return result(tool, orjson.dumps(payload).decode(), seconds, tool_use_result=payload)


def stop_ok(tool, task_id, seconds, task_type="local_agent"):
    payload = {"message": f"Successfully stopped task: {task_id} (desc)", "task_id": task_id,
               "task_type": task_type, "command": "desc"}
    return result(tool, orjson.dumps(payload).decode(), seconds, tool_use_result=payload)


def stop_error(tool, task_id, seconds):
    return result(tool, f"<tool_use_error>Task {task_id} is not running (status: completed)</tool_use_error>",
                  seconds, is_error=True, tool_use_result="Error: not running")


def notification_xml(agent, tool, status="completed", payload=True):
    xml = f"<task-notification><task-id>{agent}</task-id>"
    if tool is not None:
        xml += f"<tool-use-id>{tool}</tool-use-id>"
    if status is not None:
        xml += f"<status>{status}</status>"
    if payload:
        xml += "<result>done</result>"
    return xml + "</task-notification>"


def notif_user(agent, tool, seconds, status="completed", payload=True, xml=None):
    return {"type": "user", "timestamp": at(seconds).isoformat(), "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": xml or notification_xml(agent, tool, status, payload)}}


def notif_attachment(agent, tool, seconds, status="completed", payload=True, xml=None):
    return {"type": "attachment", "timestamp": at(seconds).isoformat(),
            "attachment": {"type": "queued_command", "commandMode": "task-notification",
                           "prompt": xml or notification_xml(agent, tool, status, payload)}}


def notif_queue(agent, tool, seconds, status="completed", payload=True, operation="enqueue", xml=None):
    return {"type": "queue-operation", "operation": operation, "timestamp": at(seconds).isoformat(),
            "content": xml or notification_xml(agent, tool, status, payload)}


NOTIFICATION_FORMS = {"user": notif_user, "attachment": notif_attachment, "queue": notif_queue}


def interrupt(seconds, **extra):
    return line("user", INTERRUPT_TEXT, seconds, **extra)


# ---------------------------------------------------------------------------
# Harness: the same steps, live or batch
# ---------------------------------------------------------------------------


@pytest.fixture
def tree(db, provider_home):
    project = Project.objects.create(id="runs-project")
    root = Session.objects.create(id="runs-root", project=project, provider=Provider.CLAUDE_CODE,
                                  file_path="runs-project/runs-root.jsonl")
    children = {
        agent_id: Session.objects.create(
            id=agent_id, project=project, provider=Provider.CLAUDE_CODE, type=SessionType.SUBAGENT,
            parent_session=root, file_path=f"runs-project/runs-root/subagents/agent-{agent_id}.jsonl")
        for agent_id in (AGENT, OTHER)
    }
    return root, children, provider_home.claude / "projects"


@pytest.fixture(params=["live", "batch"])
def mode(request):
    return request.param


def append_file(session, home, entries):
    path = home / session.file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        for parsed in entries:
            f.write(orjson.dumps(parsed) + b"\n")
    return path


def seed(session, entries):
    start = session.items.count()
    compute = ClaudeCodeSessionCompute()
    for n, parsed in enumerate(entries, start + 1):
        SessionItem.objects.create(session=session, line_num=n, content=orjson.dumps(parsed).decode(),
                                   kind=compute.compute_item_kind(parsed))


def recompute(session, compute=None):
    compute = compute or ClaudeCodeSessionCompute()
    queue = Queue()
    compute.compute_session_metadata(session.id, queue, "claude-runs-test")
    messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
    msg = next(m for m in messages if m["type"] == "session_complete")
    assert compute.apply_session_complete(msg).outcome == "applied"
    return msg


def play(mode, tree, *steps, compute=None):
    """Feed ``(session, [entries])`` steps: live syncs each in order; batch seeds all then recomputes."""
    _, _, home = tree
    if mode == "live":
        for session, entries in steps:
            append_file(session, home, entries)
            (compute or ClaudeCodeSessionCompute()).sync_session_items_from_file(session, home / session.file_path)
        return
    touched = []
    for session, entries in steps:
        seed(session, entries)
        if session not in touched:
            touched.append(session)
    for session in touched:
        recompute(session, compute)


def assert_batch_keeps_live_rows(*sessions):
    """A recompute after live sync finds the same agent-run rows (live/batch parity)."""
    for session in sessions:
        msg = recompute(session)
        for key in ("agent_interactions_to_create", "agent_interactions_to_update", "agent_interactions_to_delete",
                    "agent_run_ends_to_create", "agent_run_ends_to_update", "agent_run_ends_to_delete"):
            assert not msg.get(key), (session.id, key, msg.get(key))


def state(root, agent_id=AGENT):
    return agent_run_states(Session.objects.get(id=root.id), [agent_id])[agent_id]


def interaction(session, tool):
    return AgentInteraction.objects.get(session=session, tool_use_id=tool)


def ends(**filters):
    return list(AgentRunEnd.objects.filter(**filters).order_by("id").values(
        "session_id", "line_num", "source", "agent_id", "tool_use_id", "ended_at", "status"))


def spawned_and_finished(root):
    """Root lines: background spawn of AGENT, its ack, its end notification (line 3)."""
    return (root, [spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2), notif_user(AGENT, "tool_spawn", 3)])


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def test_control_calls_reads_every_control_block():
    parsed = calls(1, ("t1", "SendMessage", {"to": AGENT, "message": "m"}), ("t2", "Read", {"path": "x"}),
                   ("t3", "TaskStop", {"task_id": SHELL}), ("t4", "TaskOutput", {"task_id": LEGACY}))
    assert control_calls(parsed) == [
        ControlCall("t1", "message", AGENT), ControlCall("t3", "stop", SHELL), ControlCall("t4", "output", LEGACY),
    ]
    assert control_calls(result("t1", "x", 2)) == []
    assert control_calls(calls(1, ("t5", "SendMessage", {"message": "no target"}))) == []


@pytest.mark.parametrize(("parsed", "expected"), [
    (resumed_ack("s", AGENT, 1), True),
    (text_resumed_ack("s", AGENT, 1), True),
    (resumed_ack("s", AGENT, 1, message=f'Agent "{AGENT}" had no active task; resumed from transcript'), True),
    (resumed_ack("s", AGENT, 1, message=f"Agent {AGENT} was stopped (completed); resumed it"), True),
    (queued_ack("s", AGENT, 1), False),
    (result("s", "x", 1, tool_use_result={"success": False, "resumedAgentId": ""}), True),
    (failed_send_ack("s", 1), False),
    (result("s", "not json", 1), False),
    (result("s", "<tool_use_error>No such agent</tool_use_error>", 1, is_error=True), False),
    (result("s", [{"type": "text", "text": '{"success": true, "message": "Resuming agent a1"}'}], 1), True),
    (result("s", [{"type": "text", "text": '{"success": true, "message": "Message queued for a1"}'}], 1), False),
])
def test_send_message_opens_run(parsed, expected):
    assert send_message_opens_run(parsed, "s") is expected


def test_send_message_opens_run_reads_the_block_of_its_call():
    assert send_message_opens_run(text_resumed_ack("other", AGENT, 1), "s") is False


@pytest.mark.parametrize("form", sorted(NOTIFICATION_FORMS))
def test_run_end_notification_reads_raw_and_rewritten_forms(form):
    raw = NOTIFICATION_FORMS[form](AGENT, "tool_x", 5, status="killed")
    expected = RunEndNotification(AGENT, "tool_x", "killed")
    assert run_end_notification(raw) == expected
    rewritten = orjson.loads(orjson.dumps(raw))
    ClaudeCodeSessionCompute().transform_inline(rewritten, session_id="runs-root", line_num=1)
    if form != "queue":
        assert rewritten != raw  # the ingest rewrite really happened
    assert run_end_notification(rewritten) == expected


def test_run_end_notification_filters():
    # Non-terminal, missing ids, remove lines: no end.
    assert run_end_notification(notif_queue(AGENT, "t", 1, status="running")) is None
    assert run_end_notification(notif_user(AGENT, "t", 1, status="running")) is None
    assert run_end_notification(notif_queue(AGENT, "t", 1, operation="remove")) is None
    assert run_end_notification(notif_queue(AGENT, "t", 1, status="pending", payload=False)) is None
    assert run_end_notification(notif_queue(AGENT, None, 1)) is None
    # No status and a payload (old completions): an end.
    assert run_end_notification(notif_queue(AGENT, "t", 1, status=None)) == RunEndNotification(AGENT, "t", None)
    # No status, no payload: still a task result (old shape predates <status>).
    assert run_end_notification(notif_queue(AGENT, "t", 1, status=None, payload=False)) is not None
    # Every terminal status.
    for status in ("completed", "failed", "stopped", "killed", "cancelled", "canceled"):
        assert run_end_notification(notif_queue(AGENT, "t", 1, status=status)).status == status


def test_run_end_notification_ignores_local_command_original():
    """``twiccOriginalContent`` is also set by the local-command rewrite: the prefix test is mandatory."""
    parsed = line("user", "<local-command-stdout>Model set to opus</local-command-stdout>", 1)
    ClaudeCodeSessionCompute().transform_inline(parsed, session_id="runs-root", line_num=1)
    assert "twiccOriginalContent" in parsed
    assert run_end_notification(parsed) is None


def test_run_end_notification_malformed_xml_recovers_status():
    broken = (f"<task-notification><task-id>{AGENT}</task-id><tool-use-id>t</tool-use-id>"
              "<status>{}</status><result>a < b & c</result></task-notification>")
    assert run_end_notification(notif_queue(AGENT, "t", 1, xml=broken.format("completed"))) == \
        RunEndNotification(AGENT, "t", "completed")
    assert run_end_notification(notif_queue(AGENT, "t", 1, xml=broken.format("running"))) is None
    assert run_end_notification(notif_user(AGENT, "t", 1, xml=broken.format("running"))) is None


@pytest.mark.parametrize(("parsed", "expected"), [
    (interrupt(1), True),
    (line("user", [{"type": "text", "text": INTERRUPT_TEXT}], 1), True),
    (line("user", "[Request interrupted by user for tool use]", 1), False),
    (line("user", f"Please quote {INTERRUPT_TEXT} here", 1), False),
    (interrupt(1, isMeta=True), False),
    (interrupt(1, isMeta=True, origin={"kind": "coordinator"}), False),
    (interrupt(1, origin={"kind": "peer", "from": OTHER}), False),
    (line("user", [{"type": "text", "text": INTERRUPT_TEXT}, {"type": "text", "text": "x"}], 1), False),
    (line("assistant", INTERRUPT_TEXT, 1), False),
])
def test_is_plain_interrupt_marker(parsed, expected):
    assert is_plain_interrupt_marker(parsed) is expected


# ---------------------------------------------------------------------------
# Scenarios, live and batch
# ---------------------------------------------------------------------------


def test_root_resume_runs_until_its_notification(tree, mode):
    root, _, _ = tree
    play(mode, tree, spawned_and_finished(root))
    assert not state(root).running
    play(mode, tree, (root, [send("tool_send", AGENT, 10), resumed_ack("tool_send", AGENT, 11)]))
    row = interaction(root, "tool_send")
    assert (row.kind, row.agent_id, row.opens_run, row.tool_use_line_num, row.event_line_num) == \
        (AgentInteractionKind.MESSAGE, AGENT, True, 4, 4)
    assert row.started_at == at(11)
    current = state(root)
    assert current.running and current.run_started_at == at(11)
    play(mode, tree, (root, [notif_user(AGENT, "tool_send", 20)]))
    assert ends(tool_use_id="tool_send") == [{
        "session_id": root.id, "line_num": 6, "source": AgentRunEndSource.TRANSCRIPT, "agent_id": AGENT,
        "tool_use_id": "tool_send", "ended_at": at(20), "status": "completed"}]
    current = state(root)
    assert not current.running and current.stopped_at == at(20)
    if mode == "live":
        assert_batch_keeps_live_rows(root)


@pytest.mark.parametrize("caller_copy", [False, True])
def test_subagent_resume_closed_by_root_notification(tree, mode, caller_copy):
    root, children, _ = tree
    caller = children[OTHER]
    play(mode, tree, spawned_and_finished(root),
         (caller, [send("tool_sub", AGENT, 10), text_resumed_ack("tool_sub", AGENT, 11)]))
    row = interaction(caller, "tool_sub")
    assert row.opens_run and row.started_at == at(11)
    assert state(root).running
    steps = [(root, [notif_user(AGENT, "tool_sub", 20)])]
    if caller_copy:
        steps.append((caller, [notif_attachment(AGENT, "tool_sub", 20)]))
    play(mode, tree, *steps)
    assert ToolResultLink.objects.filter(session=caller, tool_use_id="tool_sub").count() == (2 if caller_copy else 1)
    assert not ToolResultLink.objects.filter(session=root, tool_use_id="tool_sub").exists()
    expected_sessions = {root.id, caller.id} if caller_copy else {root.id}
    assert {end["session_id"] for end in ends(tool_use_id="tool_sub")} == expected_sessions
    current = state(root)
    assert not current.running and current.stopped_at == at(20)
    if mode == "live":
        assert_batch_keeps_live_rows(root, caller)


def test_killed_notification_writes_a_run_end(tree, mode):
    root, _, _ = tree
    play(mode, tree, (root, [spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                             notif_queue(AGENT, "tool_spawn", 5, status="killed")]))
    assert ends() == [{"session_id": root.id, "line_num": 3, "source": AgentRunEndSource.TRANSCRIPT,
                       "agent_id": AGENT, "tool_use_id": "tool_spawn", "ended_at": at(5), "status": "killed"}]
    assert not state(root).running


def test_non_terminal_and_remove_notifications_write_nothing(tree, mode):
    root, _, _ = tree
    play(mode, tree, (root, [
        spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
        notif_queue(AGENT, "tool_spawn", 3, status="running"),
        notif_user(AGENT, "tool_spawn", 4, status="running"),
        notif_attachment(AGENT, "tool_spawn", 5, status="running"),
        notif_queue(AGENT, "tool_spawn", 6, operation="remove"),
        notif_queue("x-monitor", None, 7, status=None, xml=(
            "<task-notification><task-id>x-monitor</task-id><event>line</event></task-notification>")),
    ]))
    assert ends() == []


@pytest.mark.parametrize("form", sorted(NOTIFICATION_FORMS))
def test_every_notification_form_writes_the_same_run_end(tree, mode, form):
    root, _, _ = tree
    play(mode, tree, (root, [spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                             NOTIFICATION_FORMS[form](AGENT, "tool_spawn", 7, status="completed")]))
    assert ends() == [{"session_id": root.id, "line_num": 3, "source": AgentRunEndSource.TRANSCRIPT,
                       "agent_id": AGENT, "tool_use_id": "tool_spawn", "ended_at": at(7), "status": "completed"}]
    if mode == "live":
        assert_batch_keeps_live_rows(root)


def test_queued_send_message_opens_no_run(tree, mode):
    root, _, _ = tree
    play(mode, tree, (root, [spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                             send("tool_send", AGENT, 3), queued_ack("tool_send", AGENT, 4)]))
    row = interaction(root, "tool_send")
    assert not row.opens_run and row.started_at == at(3)
    current = state(root)
    assert current.running and [run.tool_use_id for run in current.runs] == ["tool_spawn"]
    play(mode, tree, (root, [notif_user(AGENT, "tool_spawn", 9)]))
    assert not state(root).running


def test_task_stop_records(tree, mode):
    root, _, _ = tree
    play(mode, tree, (root, [
        spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
        task_stop("tool_fail", AGENT, 3), stop_error("tool_fail", AGENT, 4),
    ]))
    assert interaction(root, "tool_fail").kind == AgentInteractionKind.STOP
    assert state(root).running  # a tool_use_error is no stop
    play(mode, tree, (root, [
        task_stop("tool_shell", SHELL, 5), stop_ok("tool_shell", SHELL, 6, task_type="local_bash"),
        task_stop("tool_stop", AGENT, 7), stop_ok("tool_stop", AGENT, 8),
    ]))
    assert interaction(root, "tool_shell").agent_id == SHELL
    assert not state(root, SHELL).known
    current = state(root)
    assert not current.running and current.stopped_at == at(8)
    if mode == "live":
        assert_batch_keeps_live_rows(root)


def test_send_message_to_main_is_never_a_run(tree, mode):
    root, _, _ = tree
    play(mode, tree, (root, [send("tool_main", "main", 1), resumed_ack("tool_main", "main", 2)]))
    assert interaction(root, "tool_main").agent_id == "main"
    assert not state(root, "main").known


def test_calls_targeting_the_owner_or_the_root_write_no_row(tree, mode):
    root, children, _ = tree
    caller = children[OTHER]
    play(mode, tree, (root, [send("tool_self", root.id, 1)]),
         (caller, [send("tool_to_root", root.id, 2), task_stop("tool_to_self", OTHER, 3)]))
    assert not AgentInteraction.objects.exists()


def test_interaction_before_its_target_spawn_link(tree, mode):
    root, children, _ = tree
    caller = children[OTHER]
    play(mode, tree, (caller, [send("tool_sub", AGENT, 10), text_resumed_ack("tool_sub", AGENT, 11)]))
    assert interaction(caller, "tool_sub").opens_run
    assert not state(root).known
    play(mode, tree, spawned_and_finished(root))
    assert AgentLink.objects.filter(agent_id=AGENT).exists()
    current = state(root)
    assert current.known and current.running and current.run_started_at == at(11)


def test_subagent_interrupt_marker(tree, mode):
    root, children, _ = tree
    child = children[AGENT]
    play(mode, tree, (root, [spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2)]),
         (child, [line("user", f"work, and never write {INTERRUPT_TEXT} alone", 1),
                  interrupt(3, isMeta=True, origin={"kind": "coordinator"}),
                  line("user", f"Quoted: {INTERRUPT_TEXT}", 4)]))
    assert ends() == []
    assert state(root).running
    play(mode, tree, (child, [interrupt(5)]))
    assert ends() == [{"session_id": AGENT, "line_num": 4, "source": AgentRunEndSource.TRANSCRIPT,
                       "agent_id": AGENT, "tool_use_id": "", "ended_at": at(5), "status": "interrupted"}]
    current = state(root)
    assert not current.running and current.stopped_at == at(5)
    # A later resume starts after the marker: it stays open.
    play(mode, tree, (root, [send("tool_send", AGENT, 10), resumed_ack("tool_send", AGENT, 11)]))
    assert state(root).running
    if mode == "live":
        assert_batch_keeps_live_rows(root, child)


def test_root_interrupt_marker_writes_nothing(tree, mode):
    root, _, _ = tree
    play(mode, tree, (root, [spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2), interrupt(3)]))
    assert ends() == []


@pytest.mark.parametrize(("stop_first", "running"), [(True, True), (False, False)])
def test_rule_two_tie_break_with_real_lines(tree, mode, stop_first, running):
    root, _, _ = tree
    stop_call = ("tool_stop", "TaskStop", {"task_id": AGENT})
    send_call = ("tool_send", "SendMessage", {"to": AGENT, "message": "again"})
    stop_result = stop_ok("tool_stop", AGENT, 11)
    send_result = resumed_ack("tool_send", AGENT, 11)  # same tool_result_at
    lines = [calls(10, stop_call, send_call), stop_result, send_result] if stop_first else \
        [calls(10, send_call, stop_call), send_result, stop_result]
    play(mode, tree, spawned_and_finished(root), (root, lines))
    assert state(root).running is running


def test_resume_run_starts_at_its_ack(tree, mode):
    root, _, _ = tree
    play(mode, tree, spawned_and_finished(root), (root, [
        calls(10, ("tool_stop", "TaskStop", {"task_id": AGENT}), ("tool_send", "SendMessage", {"to": AGENT})),
        stop_ok("tool_stop", AGENT, 11),
    ]))
    assert interaction(root, "tool_send").started_at == at(10)  # call time until the ack
    play(mode, tree, (root, [resumed_ack("tool_send", AGENT, 12)]))
    row = interaction(root, "tool_send")
    assert row.opens_run and row.started_at == at(12)
    assert state(root).running  # the stop at 11 is before the run's start
    play(mode, tree, (root, [notif_user(AGENT, "tool_send", 20)]))
    assert not state(root).running


class RecordingCompute(ClaudeCodeSessionCompute):
    """Records what each hook call returned, per line."""

    def __init__(self):
        super().__init__()
        self.batch = {}
        self.live = {}

    def collect_agent_run_signals(self, session_id, item, parsed, batch_state):
        signals = super().collect_agent_run_signals(session_id, item, parsed, batch_state)
        self.batch[(session_id, item.line_num)] = signals
        return signals

    def apply_agent_run_signals(self, session_id, item, parsed, **kwargs):
        signals = super().apply_agent_run_signals(session_id, item, parsed, **kwargs)
        self.live[(session_id, item.line_num)] = (signals, AgentInteraction.objects.filter(
            session_id=session_id, tool_use_id="tool_send", opens_run=True).exists())
        return signals


@pytest.mark.parametrize("resumed", [True, False])
def test_first_result_decides_in_its_own_hook_call(tree, mode, resumed):
    root, _, _ = tree
    compute = RecordingCompute()
    first = resumed_ack("tool_send", AGENT, 11) if resumed else queued_ack("tool_send", AGENT, 11)
    # Line 4: call; line 5: first result; line 6: compaction copy of line 5.
    play(mode, tree, spawned_and_finished(root), (root, [send("tool_send", AGENT, 10), first, first]),
         compute=compute)
    row = interaction(root, "tool_send")
    assert row.opens_run is resumed
    assert row.started_at == (at(11) if resumed else at(10))
    assert ToolResultLink.objects.filter(session=root, tool_use_id="tool_send").count() == 2
    if mode == "batch":
        assert compute.batch[(root.id, 5)].opens_run == ((("tool_send", at(11).isoformat()),) if resumed else ())
        assert compute.batch[(root.id, 6)].opens_run == ()
    else:
        signals, flipped = compute.live[(root.id, 5)]
        assert flipped is resumed
        assert signals.run_interactions == (((root.id, "tool_send"),) if resumed else ())
        assert signals.changed_interactions == signals.run_interactions
        assert compute.live[(root.id, 6)][0] == type(signals)()
        assert_batch_keeps_live_rows(root)


class QueryRecordingCompute(ClaudeCodeSessionCompute):
    """Records the SQL of each live hook call, per line."""

    def __init__(self):
        super().__init__()
        self.queries = {}

    def apply_agent_run_signals(self, session_id, item, parsed, **kwargs):
        with CaptureQueriesContext(connection) as ctx:
            signals = super().apply_agent_run_signals(session_id, item, parsed, **kwargs)
        self.queries[(session_id, item.line_num)] = [query["sql"] for query in ctx.captured_queries]
        return signals


def interaction_queries(sqls):
    return [sql for sql in sqls if 'FROM "core_agentinteraction"' in sql]


def test_success_shaped_result_of_another_tool_skips_the_interaction_lookup(tree):
    """A non-``SendMessage`` result with the resumed shape costs no ``AgentInteraction`` query (live)."""
    root, _, _ = tree
    compute = QueryRecordingCompute()
    payload = orjson.dumps({"success": True, "message": "Saved"}).decode()
    play("live", tree, (root, [
        calls(1, ("tool_mcp", "mcp__notes__save", {"text": "x"})),
        result("tool_mcp", [{"type": "text", "text": payload}], 2, tool_use_result=[{"type": "text", "text": payload}]),
        send("tool_send", AGENT, 3),
        resumed_ack("tool_send", AGENT, 4),
    ]), compute=compute)
    assert interaction_queries(compute.queries[(root.id, 2)]) == []
    # A SendMessage result still reads its row.
    assert len(interaction_queries(compute.queries[(root.id, 4)])) == 1
    assert interaction(root, "tool_send").opens_run


def test_only_the_first_result_decides(tree, mode):
    """A later result with another ``tool_result_at`` never re-decides ``opens_run``."""
    root, _, _ = tree
    play(mode, tree, spawned_and_finished(root), (root, [
        send("tool_send", AGENT, 10), queued_ack("tool_send", AGENT, 11), resumed_ack("tool_send", AGENT, 12)]))
    assert not interaction(root, "tool_send").opens_run


@pytest.mark.parametrize("form", ["user", "attachment"])
def test_notification_is_never_a_first_result(tree, mode, form):
    """A rewritten notification whose payload looks like a resumed result opens no run.

    Without ``<task-id>`` the rewrite sets no ``toolUseResult``, so the JSON
    text of the rewritten block is what a first-result check would read.
    """
    root, _, _ = tree
    xml = ("<task-notification><tool-use-id>tool_send</tool-use-id>"
           '<status>completed</status><result>{"success": true, "message": "done"}</result></task-notification>')
    play(mode, tree, spawned_and_finished(root),
         (root, [send("tool_send", AGENT, 10), NOTIFICATION_FORMS[form](AGENT, "tool_send", 12, xml=xml)]))
    assert ToolResultLink.objects.filter(session=root, tool_use_id="tool_send").count() == 1
    row = interaction(root, "tool_send")
    assert not row.opens_run and row.started_at == at(10)


def test_duplicate_tool_use_line_keeps_the_first(tree, mode):
    root, _, _ = tree
    play(mode, tree, (root, [send("tool_send", AGENT, 10), line("user", "hello", 11), send("tool_send", AGENT, 10)]))
    rows = AgentInteraction.objects.filter(tool_use_id="tool_send")
    assert [(row.tool_use_line_num, row.event_line_num) for row in rows] == [(1, 1)]
    if mode == "live":
        assert_batch_keeps_live_rows(root)


def test_failed_send_to_parent_is_no_run(tree, mode):
    root, children, _ = tree
    caller = children[OTHER]
    play(mode, tree, (caller, [send("tool_parent", "parent", 1), failed_send_ack("tool_parent", 2)]))
    row = interaction(caller, "tool_parent")
    assert row.agent_id == "parent" and not row.opens_run
    assert not state(root, "parent").known


@pytest.mark.parametrize("wording", [
    f'Agent "{AGENT}" had no active task; resumed from transcript and delivered the message',
    f"Agent {AGENT} was stopped (completed); resumed it with the message",
])
def test_old_resumed_wordings_open_a_run(tree, mode, wording):
    root, _, _ = tree
    play(mode, tree, spawned_and_finished(root),
         (root, [send("tool_send", AGENT, 10), resumed_ack("tool_send", AGENT, 11, message=wording)]))
    assert interaction(root, "tool_send").opens_run
    assert state(root).running


def test_malformed_notification_written_only_when_terminal(tree, mode):
    root, _, _ = tree
    broken = (f"<task-notification><task-id>{AGENT}</task-id><tool-use-id>tool_spawn</tool-use-id>"
              "<status>{}</status><result>a < b & c</result></task-notification>")
    play(mode, tree, (root, [spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                             notif_queue(AGENT, "tool_spawn", 3, xml=broken.format("running")),
                             notif_queue(AGENT, "tool_spawn", 4, xml=broken.format("completed"))]))
    assert [(end["line_num"], end["status"]) for end in ends()] == [(4, "completed")]


def test_legacy_agent_ids_follow_the_tree_rule(tree, mode):
    root, _, _ = tree
    play(mode, tree, (root, [spawn("tool_legacy", 1), ack("tool_legacy", LEGACY, 2),
                             task_output("tool_out", LEGACY, 3), task_output("tool_out2", "a9f9f9f", 4)]))
    assert interaction(root, "tool_out").kind == AgentInteractionKind.OUTPUT
    assert interaction(root, "tool_out2").agent_id == "a9f9f9f"
    assert state(root, LEGACY).known and state(root, LEGACY).running
    assert not state(root, "a9f9f9f").known


def test_live_signals_describe_written_rows(tree):
    root, children, _ = tree
    compute = RecordingCompute()
    play("live", tree, (root, [spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                               send("tool_send", AGENT, 3), notif_queue(AGENT, "tool_spawn", 4),
                               task_stop("tool_stop", AGENT, 5)]),
         (children[AGENT], [interrupt(6)]), compute=compute)
    call_signals = compute.live[(root.id, 3)][0]
    assert call_signals.changed_interactions == ((root.id, "tool_send"),)
    assert call_signals.affected_agent_ids == (AGENT,)
    assert call_signals.run_interactions == () and call_signals.stop_records == ()
    end_signals = compute.live[(root.id, 4)][0]
    assert end_signals.run_end_ids == (AgentRunEnd.objects.get(session=root).id,)
    assert end_signals.affected_agent_ids == (AGENT,)
    # A TaskStop row is created before its result: never a stop record here.
    assert compute.live[(root.id, 5)][0].stop_records == ()
    interrupt_signals = compute.live[(AGENT, 1)][0]
    assert interrupt_signals.run_end_ids == (AgentRunEnd.objects.get(session_id=AGENT).id,)
    assert all(signals.agents_resumed == () for signals, _ in compute.live.values())
