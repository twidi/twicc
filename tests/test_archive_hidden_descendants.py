"""Tests for the backend path that archives the eligible hidden descendants."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from channels.layers import get_channel_layer
from django.test import AsyncClient
from django.utils import timezone

from tests.db_write_helpers import run_with_direct_writes
from twicc.agent.states import AgentState
from twicc.core.models import ProcessRun, Project, Session, SessionCron, SessionType
from twicc.core.services.archive_descendants import archive_hidden_descendants, select_hidden_descendants

pytestmark = pytest.mark.django_db(transaction=True)

SERVICE = "twicc.core.services.archive_descendants"
ROUTE = "/api/projects/{project_id}/sessions/{session_id}/archive-hidden-descendants/"


@pytest.fixture
def project():
    return Project.objects.create(id="-tmp-archive-hidden", directory="/tmp/archive-hidden")


@pytest.fixture
def target(project):
    return make_session(project, "target")


def make_session(project, session_id, *, spawned_by=None, **overrides):
    now = timezone.now()
    values = {
        "id": session_id,
        "project": project,
        "provider": "claude_code",
        "file_path": f"{session_id}.jsonl",
        "type": SessionType.SESSION,
        "title": session_id,
        "created_at": now,
        "last_new_content_at": now,
        "user_message_count": 1,
        "spawned_by": spawned_by,
    }
    values.update(overrides)
    return Session.objects.create(**values)


def hidden(project, session_id, spawned_by, **overrides):
    return make_session(project, session_id, spawned_by=spawned_by, hidden=True, **overrides)


def make_process(session, *, state=AgentState.USER_TURN.value, twicc_pid=None, **overrides):
    now = timezone.now()
    return ProcessRun.objects.create(
        provider=session.provider,
        session_id=session.id,
        started_at=now,
        last_state_change_at=now,
        state=state,
        twicc_pid=os.getpid() if twicc_pid is None else twicc_pid,
        **overrides,
    )


def make_cron(session, *, created_at=None):
    created_at = created_at or timezone.now()
    return SessionCron.objects.create(
        provider="claude_code",
        cron_id=f"cron-{session.id}",
        session_id=session.id,
        cron_expr="*/5 * * * *",
        recurring=True,
        prompt="tick",
        created_at=created_at,
        next_fire=created_at + timedelta(minutes=5),
    )


def post(project_id, session_id, body):
    content = body if isinstance(body, bytes) else orjson.dumps(body)
    return asyncio.run(AsyncClient().post(
        ROUTE.format(project_id=project_id, session_id=session_id),
        data=content,
        content_type="application/json",
    ))


# --- One test per line of the walk table.


def test_detached_child_skips_its_whole_subtree(project, target):
    detached = hidden(project, "detached", target, tree_detached=True)
    hidden(project, "below-detached", detached)
    assert select_hidden_descendants(target.id, set()) == []


def test_detached_child_is_skipped_even_when_sent(project, target):
    detached = make_session(project, "detached", spawned_by=target, tree_detached=True)
    hidden(project, "below-detached", detached)
    assert select_hidden_descendants(target.id, {"detached"}) == []


def test_child_sent_as_archived_is_not_selected_and_makes_its_children_eligible(project, target):
    listed = make_session(project, "listed", spawned_by=target)
    sent = make_session(project, "sent", spawned_by=listed)
    hidden(project, "below-sent", sent)
    assert select_hidden_descendants(target.id, {"sent"}) == ["below-sent"]


def test_hidden_child_sent_as_archived_is_not_selected(project, target):
    hidden(project, "sent-hidden", target)
    assert select_hidden_descendants(target.id, {"sent-hidden"}) == []


def test_already_archived_child_keeps_an_eligible_walk(project, target):
    done = make_session(project, "done", spawned_by=target, archived=True)
    hidden(project, "below-done", done)
    assert select_hidden_descendants(target.id, set()) == ["below-done"]


def test_already_archived_child_keeps_a_blocked_walk(project, target):
    listed = make_session(project, "listed", spawned_by=target)
    done = make_session(project, "done", spawned_by=listed, archived=True)
    hidden(project, "below-done", done)
    assert select_hidden_descendants(target.id, set()) == []


def test_hidden_already_archived_child_is_not_selected(project, target):
    hidden(project, "done-hidden", target, archived=True)
    assert select_hidden_descendants(target.id, set()) == []


def test_hidden_barrier_is_not_selected_and_blocks_its_children(project, target):
    barrier = hidden(project, "barrier", target, pinned="project")
    hidden(project, "below-barrier", barrier)
    assert select_hidden_descendants(target.id, set()) == []


def test_hidden_child_is_selected_when_eligible_and_keeps_the_walk_eligible(project, target):
    middle = hidden(project, "middle", target)
    hidden(project, "leaf", middle)
    assert select_hidden_descendants(target.id, set()) == ["middle", "leaf"]


def test_hidden_child_is_not_selected_when_not_eligible(project, target):
    listed = make_session(project, "listed", spawned_by=target)
    middle = hidden(project, "middle", listed)
    hidden(project, "leaf", middle)
    assert select_hidden_descendants(target.id, set()) == []


def test_listed_child_not_sent_blocks_its_children(project, target):
    listed = make_session(project, "listed", spawned_by=target)
    hidden(project, "below-listed", listed)
    assert select_hidden_descendants(target.id, set()) == []


# --- Named scenarios of the brief.


def test_listed_child_already_archived_in_db_and_sent_stays_eligible(project, target):
    listed = make_session(project, "listed", spawned_by=target)
    done = make_session(project, "done", spawned_by=listed, archived=True)
    hidden(project, "below-done", done)
    assert select_hidden_descendants(target.id, {"done"}) == ["below-done"]


def test_hidden_cron_is_barrier(project, target):
    with_cron = hidden(project, "with-cron", target)
    make_cron(with_cron)
    hidden(project, "below-cron", with_cron)
    assert select_hidden_descendants(target.id, set()) == []


def test_hidden_below_hidden_barrier_is_not_selected(project, target):
    barrier = hidden(project, "barrier", target)
    make_process(barrier, state=AgentState.ASSISTANT_TURN.value)
    middle = hidden(project, "middle", barrier)
    hidden(project, "leaf", middle)
    assert select_hidden_descendants(target.id, set()) == []


# --- Barrier definition.


@pytest.mark.parametrize(
    "process",
    [
        {"state": AgentState.STARTING.value},
        {"state": AgentState.ASSISTANT_TURN.value},
        {"state": AgentState.ASSISTANT_TURN.value, "awaiting_user_input": True},
        {"state": AgentState.USER_TURN.value, "background_work_in_progress": {"shells": 1}},
    ],
)
def test_work_in_progress_is_barrier(project, target, process):
    make_process(hidden(project, "busy", target), **process)
    assert select_hidden_descendants(target.id, set()) == []


@pytest.mark.parametrize("state", [AgentState.USER_TURN.value, AgentState.DEAD.value])
def test_idle_process_is_not_barrier(project, target, state):
    make_process(hidden(project, "idle", target), state=state)
    assert select_hidden_descendants(target.id, set()) == ["idle"]


def test_process_row_of_another_backend_is_not_barrier(project, target):
    process = hidden(project, "stale", target)
    make_process(process, state=AgentState.ASSISTANT_TURN.value, twicc_pid=os.getpid() + 1)
    assert select_hidden_descendants(target.id, set()) == ["stale"]


def test_recurring_cron_past_the_cli_window_is_barrier(project, target):
    with_cron = hidden(project, "old-cron", target)
    make_cron(with_cron, created_at=timezone.now() - SessionCron.CLAUDE_RECURRING_MAX_AGE - timedelta(days=1))
    assert select_hidden_descendants(target.id, set()) == []


# --- Walk robustness.


def test_a_spawn_cycle_ends_the_walk(project, target):
    first = hidden(project, "first", target)
    second = hidden(project, "second", first)
    Session.objects.filter(id="first").update(spawned_by=second)
    Session.objects.filter(id="second").update(spawned_by=first)
    assert select_hidden_descendants("first", set()) == ["second"]


def test_only_the_descendants_of_the_target_are_walked(project, target):
    other = make_session(project, "other")
    hidden(project, "foreign", other)
    assert select_hidden_descendants(target.id, set()) == []


# --- The archive itself.


def test_archive_calls_the_archive_flow_once_per_selected_session(project, target, monkeypatch):
    hidden(project, "one", target)
    hidden(project, "two", target)
    make_session(project, "listed", spawned_by=target)
    apply_change = AsyncMock()
    monkeypatch.setattr(f"{SERVICE}.apply_session_archived_change", apply_change)

    result = asyncio.run(archive_hidden_descendants(target.id, set()))

    assert result == (["one", "two"], [])
    assert [(call.args[0].id, call.args[1]) for call in apply_change.await_args_list] == [("one", True), ("two", True)]


def test_a_failure_is_reported_and_does_not_stop_the_others(project, target, monkeypatch):
    hidden(project, "broken", target)
    hidden(project, "working", target)

    async def apply_change(session, archived):
        if session.id == "broken":
            raise RuntimeError("boom")

    monkeypatch.setattr(f"{SERVICE}.apply_session_archived_change", apply_change)

    assert asyncio.run(archive_hidden_descendants(target.id, set())) == (["working"], ["broken"])


def test_archive_persists_the_flag_and_does_not_broadcast(project, target, monkeypatch):
    from twicc import terminal, title_auto_task
    from twicc.agent import registry

    selected = hidden(project, "selected", target)
    barrier = hidden(project, "barrier", target, pinned="project")
    monkeypatch.setattr(registry, "get_agent_manager_registry", lambda: SimpleNamespace(
        kill_agent=AsyncMock(), get_active_agents=list,
    ))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", lambda scope: None)
    monkeypatch.setattr(title_auto_task, "request_title_check", lambda *args, **kwargs: None)
    group_send = AsyncMock()
    monkeypatch.setattr(get_channel_layer(), "group_send", group_send)

    assert run_with_direct_writes(lambda: archive_hidden_descendants(target.id, set())) == (["selected"], [])

    selected.refresh_from_db()
    barrier.refresh_from_db()
    target.refresh_from_db()
    assert selected.archived is True
    assert barrier.archived is False
    assert target.archived is False
    group_send.assert_not_awaited()


# --- The endpoint.


def test_endpoint_archives_and_returns_both_lists(project, target, monkeypatch):
    listed = make_session(project, "listed", spawned_by=target)
    hidden(project, "below-listed", listed)
    hidden(project, "below-target", target)
    hidden(project, "broken", target)

    async def apply_change(session, archived):
        if session.id == "broken":
            raise RuntimeError("boom")

    monkeypatch.setattr(f"{SERVICE}.apply_session_archived_change", apply_change)

    response = post(project.id, target.id, {"archived_ids": ["listed"]})

    assert response.status_code == 200
    assert orjson.loads(response.content) == {"archived": ["below-target", "below-listed"], "failed": ["broken"]}


@pytest.mark.parametrize(
    "body",
    [
        {"archived_ids": "listed"},
        {"archived_ids": [1]},
        {"archived_ids": ["listed", None]},
        {"archived_ids": None},
        {},
        ["listed"],
        b"not json",
    ],
)
def test_endpoint_rejects_non_list_body(project, target, body):
    response = post(project.id, target.id, body)

    assert response.status_code == 400
    assert orjson.loads(response.content)["ui_message"]["key"] == "backend.sessions.archived_ids_invalid"


def test_endpoint_returns_404_for_unknown_target(project):
    assert post(project.id, "missing", {"archived_ids": []}).status_code == 404


def test_endpoint_returns_404_for_a_session_of_another_project(project, target):
    other = Project.objects.create(id="-tmp-archive-other", directory="/tmp/archive-other")
    assert post(other.id, target.id, {"archived_ids": []}).status_code == 404


def test_endpoint_returns_404_for_a_hidden_target(project, target):
    hidden_target = hidden(project, "hidden-target", target)
    assert post(project.id, hidden_target.id, {"archived_ids": []}).status_code == 404


def test_endpoint_only_accepts_post(project, target):
    response = asyncio.run(AsyncClient().get(ROUTE.format(project_id=project.id, session_id=target.id)))
    assert response.status_code == 405
