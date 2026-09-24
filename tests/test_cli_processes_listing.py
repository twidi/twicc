"""Tests for the ``twicc processes`` listing command."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.core.models import ProcessRun, Project, Session, SessionType


@pytest.fixture(autouse=True)
def _before_the_pagination_cutover(monkeypatch):
    """Pin the clock below ``LISTING_CUTOVER``.

    These tests assert the pre-cutover shape (a bare array). Past the date it
    changes, so without this they would go red on 2026-10-01 for a reason that
    has nothing to do with what they cover.
    The pinned value is naive, like the constant it replaces.
    """
    from twicc.cli import _output

    monkeypatch.setattr(_output, "LISTING_CUTOVER", datetime(2200, 1, 1))  # noqa: DTZ001


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-twicc-processes-listing",
        directory="/tmp/twicc-processes-listing",
    )


def make_session(
    project,
    session_id: str,
    *,
    spawned_by=None,
    spawn_root=None,
    annotations=None,
    hidden: bool = False,
    minutes: int = 0,
):
    now = timezone.now() + timedelta(minutes=minutes)
    return Session.objects.create(
        id=session_id,
        project=project,
        provider="codex",
        file_path=f"{session_id}.jsonl",
        type=SessionType.SESSION,
        title=session_id,
        created_at=now,
        last_new_content_at=now,
        user_message_count=1,
        spawned_by=spawned_by,
        spawn_root=spawn_root,
        annotations=annotations or {},
        hidden=hidden,
    )


def make_process(session_id: str, *, twicc_pid: int = 1234, minutes: int = 0):
    now = timezone.now() + timedelta(minutes=minutes)
    return ProcessRun.objects.create(
        provider="codex",
        session_id=session_id,
        started_at=now,
        state=AgentState.USER_TURN.value,
        last_state_change_at=now,
        twicc_pid=twicc_pid,
        agent_pid=9000 + minutes,
    )


@pytest.fixture(autouse=True)
def live_twicc(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc_or_exit",
        lambda: SimpleNamespace(pid=1234),
    )


def test_processes_applies_spawned_by_scope_before_pagination(project, capsysbinary):
    root = make_session(project, "ROOT")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    child = make_session(project, "CHILD", spawned_by=root, spawn_root=root)
    unrelated = make_session(project, "UNRELATED")
    make_process(child.id, minutes=0)
    make_process(unrelated.id, minutes=10)

    from twicc.cli import processes as cli_processes

    cli_processes.main(spawned_by=root.id, limit=1)

    rows = orjson.loads(capsysbinary.readouterr().out)
    assert [row["session_id"] for row in rows] == [child.id]


def test_processes_applies_hidden_filter_before_pagination(project, capsysbinary):
    visible = make_session(project, "VISIBLE")
    hidden = make_session(project, "HIDDEN", hidden=True)
    make_process(visible.id, minutes=0)
    make_process(hidden.id, minutes=10)

    from twicc.cli import processes as cli_processes

    cli_processes.main(limit=1)

    rows = orjson.loads(capsysbinary.readouterr().out)
    assert [row["session_id"] for row in rows] == [visible.id]


def test_processes_rejects_annotation_without_filiation_scope(project, capsys):
    from twicc.cli import processes as cli_processes

    with pytest.raises(typer.Exit) as exc:
        cli_processes.main(annotation=["role=worker"])

    assert exc.value.exit_code == 1
    assert "--annotation on processes listing requires" in capsys.readouterr().err


def test_processes_annotation_narrows_spawn_tree_scope(project, capsysbinary):
    root = make_session(project, "ROOT")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    worker = make_session(
        project,
        "WORKER",
        spawned_by=root,
        spawn_root=root,
        annotations={"role": "worker"},
    )
    unrelated = make_session(
        project,
        "UNRELATED",
        annotations={"role": "worker"},
    )
    make_process(worker.id, minutes=0)
    make_process(unrelated.id, minutes=10)

    from twicc.cli import processes as cli_processes

    cli_processes.main(spawn_tree=root.id, annotation=["role=worker"])

    rows = orjson.loads(capsysbinary.readouterr().out)
    assert [row["session_id"] for row in rows] == [worker.id]


def test_processes_stop_rejects_parent_scope(project, capsys):
    from twicc.cli.processes_stop import stop_cmd

    with pytest.raises(typer.Exit) as exc:
        stop_cmd([], timeout=1, spawned_by="parent")

    assert exc.value.exit_code == 1
    assert "does not support parent-scoped filters" in capsys.readouterr().err


def test_processes_stop_rejects_annotation_without_filiation_scope(project, capsys):
    from twicc.cli.processes_stop import stop_cmd

    with pytest.raises(typer.Exit) as exc:
        stop_cmd(["abc123"], timeout=1, annotation=["role=worker"])

    assert exc.value.exit_code == 1
    assert "--annotation on processes stop requires" in capsys.readouterr().err


def test_processes_wait_rejects_parent_scope(project, capsys):
    from twicc.cli.processes_wait import wait_cmd

    with pytest.raises(typer.Exit) as exc:
        wait_cmd(
            ["user_turn"],
            timeout=1,
            wait_all=True,
            transition=False,
            descendants="parent",
        )

    assert exc.value.exit_code == 1
    assert "does not support parent-scoped filters" in capsys.readouterr().err


def test_processes_wait_rejects_annotation_without_filiation_scope(project, capsys):
    from twicc.cli.processes_wait import wait_cmd

    with pytest.raises(typer.Exit) as exc:
        wait_cmd(
            ["abc123", "user_turn"],
            timeout=1,
            wait_all=True,
            transition=False,
            annotation=["role=worker"],
        )

    assert exc.value.exit_code == 1
    assert "--annotation on processes wait requires" in capsys.readouterr().err
