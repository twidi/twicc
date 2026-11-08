"""Timeline rows of Claude Code ``Workflow`` runs (``workflow.started`` / ``workflow.ended``).

The live sync reads them from the session's transcript; a process end and the boot reconciliation
close the attempts left open.
"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import orjson
import pytest
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.core.models import ProcessRun, Project, Session, TimelineEvent, TimelineKind
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_boot import reconcile_at_boot
from twicc.core.services.timeline_workflows import close_open_workflows, open_workflow_attempts
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.live_sync import LiveSyncLimits

pytestmark = pytest.mark.django_db


def _workflow_rows(session_id="wf-session"):
    return [
        (row.kind, row.data.get("task_id"), row.data.get("cause"), row.data.get("status"), row.reason, row.precision)
        for row in TimelineEvent.objects.filter(
            session_id=session_id, kind__in=[TimelineKind.WORKFLOW_STARTED, TimelineKind.WORKFLOW_ENDED],
        ).order_by("id")
    ]


@pytest.fixture
def transcript(tmp_path):
    """A live Claude session and its transcript; ``append`` writes records and syncs them."""
    session = Session.objects.create(
        id="wf-session", project=Project.objects.create(id="wf-project"), provider="claude_code",
        file_path="wf.jsonl",
    )
    path = tmp_path / "wf.jsonl"
    path.touch()
    compute = ClaudeCodeSessionCompute()
    boundary = timezone.now() - timedelta(seconds=1)
    now = timezone.now().isoformat()

    def append(*records, since=boundary):
        with path.open("ab") as stream:
            for record in records:
                stream.write(orjson.dumps(record) + b"\n")
        while compute.sync_session_slice(session.id, path, limits=LiveSyncLimits(500), timeline_since=since).has_more:
            pass

    def launch(tool_use_id, task_id, *, run_id="wf_1", resume=False, timestamp=now):
        tool_input = {"scriptPath": "/x/demo.js", "resumeFromRunId": run_id} if resume else {"script": "meta"}
        call = {
            "type": "assistant", "uuid": str(uuid4()), "timestamp": timestamp,
            "message": {"id": f"msg-{tool_use_id}", "role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": tool_use_id, "name": "Workflow", "input": tool_input}]},
        }
        ack = {
            "type": "user", "uuid": str(uuid4()), "timestamp": timestamp,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": f"Workflow launched. Task ID: {task_id}"},
            ]},
            "toolUseResult": {"status": "async_launched", "taskId": task_id, "taskType": "local_workflow",
                              "workflowName": "demo", "runId": run_id, "summary": "Demo"},
        }
        return call, ack

    def notification(tool_use_id, task_id, status="completed", timestamp=now):
        text = (f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>{tool_use_id}</tool-use-id>\n"
                f"<status>{status}</status>\n<summary>Dynamic workflow \"Demo\" {status}</summary>\n"
                "<result>{}</result>\n</task-notification>")
        queued = {"type": "queue-operation", "operation": "enqueue", "timestamp": timestamp, "content": text}
        user = {"type": "user", "uuid": str(uuid4()), "timestamp": timestamp,
                "origin": {"kind": "task-notification"}, "message": {"role": "user", "content": text}}
        return queued, user

    return SimpleNamespace(session=session, append=append, launch=launch, notification=notification)


def test_a_run_writes_its_start_then_its_end_once(transcript):
    transcript.append(*transcript.launch("toolu_a", "w1"))
    transcript.append(*transcript.notification("toolu_a", "w1"))  # the queued and the user form
    assert _workflow_rows() == [
        ("workflow.started", "w1", "new", None, "", "exact"),
        ("workflow.ended", "w1", None, "completed", "", "exact"),
    ]
    started = TimelineEvent.objects.get(kind=TimelineKind.WORKFLOW_STARTED)
    assert started.data == {"run_id": "wf_1", "task_id": "w1", "name": "demo", "cause": "new"}
    assert started.actor == "agent"
    ended = TimelineEvent.objects.get(kind=TimelineKind.WORKFLOW_ENDED)
    assert ended.data == {"run_id": "wf_1", "task_id": "w1", "name": "demo", "status": "completed"}


@pytest.mark.parametrize("status", ["failed", "killed", "stopped"])
def test_the_cli_status_is_kept(transcript, status):
    transcript.append(*transcript.launch("toolu_a", "w1"))
    transcript.append(*transcript.notification("toolu_a", "w1", status))
    assert _workflow_rows()[-1][3] == status


def test_a_resume_is_a_new_attempt_and_ends_the_paused_one(transcript):
    transcript.append(*transcript.launch("toolu_a", "w1"))
    transcript.append(*transcript.launch("toolu_b", "w2", resume=True))
    transcript.append(*transcript.notification("toolu_b", "w2"))
    assert _workflow_rows() == [
        ("workflow.started", "w1", "new", None, "", "exact"),
        ("workflow.ended", "w1", None, "paused", "", "estimated"),
        ("workflow.started", "w2", "resume", None, "", "exact"),
        ("workflow.ended", "w2", None, "completed", "", "exact"),
    ]


def test_a_resume_after_an_end_writes_no_pause(transcript):
    transcript.append(*transcript.launch("toolu_a", "w1"))
    transcript.append(*transcript.notification("toolu_a", "w1", "failed"))
    transcript.append(*transcript.launch("toolu_b", "w2", resume=True))
    assert [row[3] for row in _workflow_rows()] == [None, "failed", None]


def test_overlapping_runs_pair_by_task(transcript):
    transcript.append(*transcript.launch("toolu_a", "w1", run_id="wf_1"))
    transcript.append(*transcript.launch("toolu_b", "w2", run_id="wf_2"))
    transcript.append(*transcript.notification("toolu_a", "w1", "killed"))
    assert [row.data["task_id"] for row in open_workflow_attempts("wf-session")] == ["w2"]
    assert _workflow_rows()[-1][1:4] == ("w1", None, "killed")


def test_history_and_other_tasks_write_nothing(transcript):
    # Lines dated before the watcher started are history.
    old = (timezone.now() - timedelta(days=1)).isoformat()
    transcript.append(*transcript.launch("toolu_old", "w0", timestamp=old))
    # An agent's notification: no workflow started under its task id.
    transcript.append(*transcript.notification("toolu_agent", "a1"))
    # The end of a run the timeline never saw start.
    transcript.append(*transcript.notification("toolu_old", "w0"))
    assert _workflow_rows() == []


def test_without_a_watcher_start_nothing_is_recorded(transcript):
    transcript.append(*transcript.launch("toolu_a", "w1"), since=None)
    assert _workflow_rows() == []


def _started(session_id, task_id, **data):
    return record_event_sync(
        TimelineKind.WORKFLOW_STARTED, session_id=session_id, actor="agent",
        data={"run_id": "wf_1", "task_id": task_id, **data}, dedupe_key=f"workflow:{task_id}:started",
    )


@pytest.mark.django_db(transaction=True)
def test_a_process_end_interrupts_its_open_runs():
    from tests.test_timeline_process_hooks import make_agent, transition

    project = Project.objects.create(id="-tl-wf", directory="/tmp/tl-wf")
    agent = make_agent(project, kill_reason="manual")
    _started("s1", "w1", name="demo")
    _started("s1", "w2")
    record_event_sync(TimelineKind.WORKFLOW_ENDED, session_id="s1", actor="system",
                      data={"task_id": "w2", "status": "completed"}, dedupe_key="workflow:w2:ended")
    transition(agent, AgentState.DEAD)
    ended = TimelineEvent.objects.filter(session_id="s1", kind=TimelineKind.WORKFLOW_ENDED).order_by("id")
    assert [(row.data["task_id"], row.data["status"], row.reason) for row in ended] == [
        ("w2", "completed", ""), ("w1", "interrupted", "manual"),
    ]
    assert ended.last().data["name"] == "demo"
    kinds = list(TimelineEvent.objects.filter(session_id="s1").order_by("id").values_list("kind", flat=True))
    assert kinds.index("workflow.ended") < kinds.index("process.ended")  # inner intervals close first


@pytest.mark.django_db(transaction=True)
def test_a_process_found_dead_at_boot_loses_its_open_runs():
    project = Project.objects.create(id="-tl-wf-boot", directory="/tmp/tl-wf-boot")
    now = timezone.now()
    Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="claude_code")
    ProcessRun.objects.create(provider="claude_code", session_id="s1", started_at=now - timedelta(hours=1),
                              state="assistant_turn", last_state_change_at=now - timedelta(minutes=5))
    _started("s1", "w1")
    reconcile_at_boot(now=now, boot_id="b1")
    row = TimelineEvent.objects.get(session_id="s1", kind=TimelineKind.WORKFLOW_ENDED)
    assert (row.data["status"], row.reason, row.precision) == ("interrupted", "lost", "estimated")
    lost = TimelineEvent.objects.get(session_id="s1", kind=TimelineKind.PROCESS_ENDED)
    assert row.occurred_at == lost.occurred_at and row.id < lost.id


def test_closing_twice_writes_one_end():
    _started("s9", "w1")
    close_open_workflows("s9", occurred_at=timezone.now(), reason="shutdown")
    close_open_workflows("s9", occurred_at=timezone.now(), reason="shutdown")
    assert TimelineEvent.objects.filter(session_id="s9", kind=TimelineKind.WORKFLOW_ENDED).count() == 1


def test_the_user_form_alone_ends_the_run(transcript):
    transcript.append(*transcript.launch("toolu_a", "w1"))
    _queued, user = transcript.notification("toolu_a", "w1", "failed")
    transcript.append(user)
    assert _workflow_rows()[-1][1:4] == ("w1", None, "failed")


def test_an_end_is_written_whatever_its_date_once_its_start_is_known(transcript):
    transcript.append(*transcript.launch("toolu_a", "w1"))
    # The watcher started again in the same process: the end is dated before its new start.
    old = (timezone.now() - timedelta(hours=1)).isoformat()
    transcript.append(*transcript.notification("toolu_a", "w1", timestamp=old), since=timezone.now())
    assert _workflow_rows()[-1][1:4] == ("w1", None, "completed")


def test_a_launch_synced_after_its_process_ended_is_interrupted_at_once(transcript):
    launched = timezone.now() - timedelta(seconds=5)
    record_event_sync(TimelineKind.PROCESS_STARTED, session_id="wf-session", actor="system",
                      occurred_at=launched - timedelta(minutes=1), data={"provider": "claude_code"})
    record_event_sync(TimelineKind.PROCESS_ENDED, session_id="wf-session", actor="system",
                      occurred_at=launched + timedelta(seconds=1), reason="manual")
    transcript.append(*transcript.launch("toolu_a", "w1", timestamp=launched.isoformat()),
                      since=launched - timedelta(seconds=1))
    assert _workflow_rows() == [
        ("workflow.started", "w1", "new", None, "", "exact"),
        ("workflow.ended", "w1", None, "interrupted", "manual", "exact"),
    ]


def test_a_late_launch_after_a_restart_is_closed_by_its_own_process_end(transcript):
    launched = timezone.now() - timedelta(seconds=5)
    for kind, at, reason in ((TimelineKind.PROCESS_STARTED, -60, ""), (TimelineKind.PROCESS_ENDED, 1, "apply-settings"),
                             (TimelineKind.PROCESS_STARTED, 2, ""), (TimelineKind.PROCESS_ENDED, 3, "manual")):
        record_event_sync(kind, session_id="wf-session", actor="system", reason=reason,
                          occurred_at=launched + timedelta(seconds=at),
                          data={"provider": "claude_code"} if kind == TimelineKind.PROCESS_STARTED else None)
    transcript.append(*transcript.launch("toolu_a", "w1", timestamp=launched.isoformat()),
                      since=launched - timedelta(seconds=1))
    assert _workflow_rows()[-1][3:5] == ("interrupted", "apply-settings")


def test_a_run_synced_whole_after_its_process_ended_keeps_its_own_end(transcript):
    launched = timezone.now() - timedelta(seconds=5)
    record_event_sync(TimelineKind.PROCESS_ENDED, session_id="wf-session", actor="system",
                      occurred_at=launched + timedelta(seconds=2), reason="manual")
    stamp = launched.isoformat()
    transcript.append(*transcript.launch("toolu_a", "w1", timestamp=stamp),
                      *transcript.notification("toolu_a", "w1", timestamp=stamp),
                      since=launched - timedelta(seconds=1))
    assert [row[3] for row in _workflow_rows()] == [None, "completed"]


def test_a_launch_during_a_live_process_stays_open(transcript):
    record_event_sync(TimelineKind.PROCESS_STARTED, session_id="wf-session", actor="system",
                      data={"provider": "claude_code"})
    transcript.append(*transcript.launch("toolu_a", "w1"))
    assert [row[0] for row in _workflow_rows()] == ["workflow.started"]


@pytest.mark.django_db(transaction=True)
def test_a_process_end_keeps_open_the_runs_its_agent_saw_end():
    """The agent saw ``w2`` end: its notification is still on its way through the watcher."""
    from tests.test_timeline_process_hooks import make_agent, transition

    project = Project.objects.create(id="-tl-wf-live", directory="/tmp/tl-wf-live")
    agent = make_agent(project, kill_reason="shutdown", ended_workflow_task_ids=lambda: frozenset({"w2"}))
    _started("s1", "w1")
    _started("s1", "w2")
    transition(agent, AgentState.DEAD)
    assert [row.data["task_id"] for row in open_workflow_attempts("s1")] == ["w2"]
    ended = TimelineEvent.objects.get(session_id="s1", kind=TimelineKind.WORKFLOW_ENDED)
    assert (ended.data["task_id"], ended.data["status"], ended.reason) == ("w1", "interrupted", "shutdown")
