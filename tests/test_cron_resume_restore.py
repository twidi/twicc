"""A resumed Claude Code session must come back with its crons, and only once.

Since CLI 2.1.110 a resume replays the transcript and re-arms every unexpired
cron with its original id and ``created_at``. Three rules follow:

- :meth:`SessionCron.is_restored_on_resume` — the predicate the CLI applies.
- :func:`_split_restorable_crons` — who brings each cron back: the CLI on its
  own (``restored``), or a fresh ``CronCreate`` from Claude (``to_recreate``,
  recurring crons past the 7-day window). A resume must never ask for a cron
  the CLI already re-armed: the new job fires *alongside* the old one.
- :func:`reattach_crons_and_purge_old_runs` — the rows of the re-armed crons
  move to the new run instead of cascading away with the old one, so the expiry
  monitor keeps renewing them.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.utils import timezone as djtz

from twicc.agent.exceptions import AgentStartError
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Session, SessionCron, SessionType
from twicc.providers.claude_code import cron_restart
from twicc.providers.claude_code.cron_restart import (
    _build_restart_message,
    _prepare_restarts,
    _split_restorable_crons,
    reattach_crons_and_purge_old_runs,
)

SESSION_ID = "sess-cron-resume"
OVER_7_DAYS = SessionCron.CLAUDE_RECURRING_MAX_AGE + timedelta(hours=1)


@pytest.fixture
def project(transactional_db):
    from twicc.core.models import Project

    return Project.objects.create(id="-tmp-cron-resume", directory="/tmp/cron-resume")


@pytest.fixture
def session(transactional_db, project):
    now = djtz.now()
    return Session.objects.create(
        id=SESSION_ID, project=project, provider=Provider.CLAUDE_CODE.value,
        file_path=f"{SESSION_ID}.jsonl", type=SessionType.SESSION,
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=1,
    )


def _run(state=AgentState.DEAD, started_at=None):
    now = started_at or djtz.now()
    return ProcessRun.objects.create(
        provider=Provider.CLAUDE_CODE.value,
        session_id=SESSION_ID,
        started_at=now,
        state=state.value,
        last_state_change_at=now,
    )


def _cron(run, cron_id, *, recurring=True, age=timedelta(0), next_fire=None):
    created_at = djtz.now() - age
    return SessionCron.objects.create(
        provider=Provider.CLAUDE_CODE.value,
        cron_id=cron_id,
        session_id=SESSION_ID,
        process_run=run,
        cron_expr="* * * * *" if recurring else "30 14 15 3 *",
        recurring=recurring,
        prompt=f"prompt {cron_id}",
        created_at=created_at,
        next_fire=next_fire if next_fire is not None else created_at + timedelta(minutes=1),
    )


# --- is_restored_on_resume --------------------------------------------------

def test_fresh_recurring_cron_is_restored_by_the_cli(transactional_db):
    assert _cron(_run(), "fresh").is_restored_on_resume() is True


def test_recurring_cron_past_seven_days_is_not(transactional_db):
    assert _cron(_run(), "old", age=OVER_7_DAYS).is_restored_on_resume() is False


def test_pending_one_shot_is_restored(transactional_db):
    cron = _cron(_run(), "soon", recurring=False, next_fire=djtz.now() + timedelta(hours=1))
    assert cron.is_restored_on_resume() is True


def test_fired_one_shot_is_not(transactional_db):
    cron = _cron(_run(), "past", recurring=False, next_fire=djtz.now() - timedelta(hours=1))
    assert cron.is_restored_on_resume() is False


# --- _split_restorable_crons ------------------------------------------------

def test_split_routes_each_cron_to_its_owner(transactional_db):
    """Age decides who brings a cron back; a fired one-shot never comes back."""
    run = _run()
    _cron(run, "fresh")
    _cron(run, "old", age=OVER_7_DAYS)
    _cron(run, "pending", recurring=False, next_fire=djtz.now() + timedelta(hours=1))
    _cron(run, "fired", recurring=False, next_fire=djtz.now() - timedelta(hours=1))

    crons = _split_restorable_crons(SESSION_ID)

    assert [c.cron_id for c in crons.restored] == ["fresh", "pending"]
    assert [c.cron_id for c in crons.to_recreate] == ["old"]
    assert crons.has_any is True


def test_split_ignores_another_session(transactional_db):
    run = _run()
    _cron(run, "mine")
    SessionCron.objects.create(
        provider=Provider.CLAUDE_CODE.value, cron_id="theirs", session_id="other",
        process_run=run, cron_expr="* * * * *", recurring=True, prompt="x",
        created_at=djtz.now(), next_fire=djtz.now(),
    )

    assert [c.cron_id for c in _split_restorable_crons(SESSION_ID).restored] == ["mine"]


def test_split_reports_nothing_for_a_fired_one_shot_only(transactional_db):
    _cron(_run(), "fired", recurring=False, next_fire=djtz.now() - timedelta(hours=1))

    assert _split_restorable_crons(SESSION_ID).has_any is False


# --- the message ------------------------------------------------------------

def test_message_forbids_recreating_a_restored_cron():
    message = _build_restart_message(
        [{"cron_id": "a768a000", "cron_expr": "* * * * *", "recurring": True, "prompt": "p"}],
        [],
    )

    assert "a768a000" in message
    assert "Do NOT call CronCreate" in message
    assert "Recreate" not in message


def test_message_asks_to_recreate_an_expired_cron():
    message = _build_restart_message(
        [],
        [{"cron_id": "b1c2d3e4", "cron_expr": "7 * * * *", "recurring": True, "prompt": "p"}],
    )

    assert "Recreate it using CronCreate" in message
    # The dead job's id is meaningless to the CLI — never show it as reusable.
    assert "b1c2d3e4" not in message


def test_message_carries_both_sections():
    message = _build_restart_message(
        [{"cron_id": "kept", "cron_expr": "* * * * *", "recurring": True, "prompt": "p"}],
        [{"cron_id": "gone", "cron_expr": "7 * * * *", "recurring": True, "prompt": "q"}],
    )

    assert "Do NOT call CronCreate" in message
    assert "Recreate it using CronCreate" in message


# --- _prepare_restarts ------------------------------------------------------

def test_prepare_restarts_keeps_a_session_past_seven_days(session):
    """The whole point: age must not decide whether the session comes back."""
    run = _run()
    _cron(run, "old", age=OVER_7_DAYS)

    assert _prepare_restarts() == [SESSION_ID]
    assert ProcessRun.objects.filter(pk=run.pk).exists()


def test_prepare_restarts_drops_a_session_with_nothing_left(session):
    """A fired one-shot brings nothing back — the row would leak forever."""
    run = _run()
    _cron(run, "fired", recurring=False, next_fire=djtz.now() - timedelta(hours=1))

    assert _prepare_restarts() == []
    assert not ProcessRun.objects.filter(pk=run.pk).exists()


# --- reattach_crons_and_purge_old_runs --------------------------------------

def test_reattach_moves_the_restored_crons_to_the_current_run(transactional_db):
    old = _run(started_at=djtz.now() - timedelta(minutes=5))
    current = _run(state=AgentState.USER_TURN)
    _cron(old, "kept")
    _cron(old, "expired", age=OVER_7_DAYS)
    _cron(current, "recreated")

    reattached, deleted = reattach_crons_and_purge_old_runs(SESSION_ID, current.pk)

    assert (reattached, deleted) == (1, 1)
    assert not ProcessRun.objects.filter(pk=old.pk).exists()
    assert set(
        SessionCron.objects.filter(session_id=SESSION_ID).values_list("cron_id", flat=True)
    ) == {"kept", "recreated"}
    assert SessionCron.objects.get(cron_id="kept").process_run_id == current.pk


def test_reattach_keeps_the_original_created_at(transactional_db):
    """The CLI re-arms with the original age, so the expiry monitor must see it."""
    old = _run(started_at=djtz.now() - timedelta(minutes=5))
    current = _run(state=AgentState.USER_TURN)
    created_at = _cron(old, "kept", age=timedelta(days=3)).created_at

    reattach_crons_and_purge_old_runs(SESSION_ID, current.pk)

    assert SessionCron.objects.get(cron_id="kept").created_at == created_at


def test_reattach_is_a_noop_without_old_runs(transactional_db):
    current = _run(state=AgentState.USER_TURN)
    _cron(current, "only")

    assert reattach_crons_and_purge_old_runs(SESSION_ID, current.pk) == (0, 0)
    assert SessionCron.objects.get(cron_id="only").process_run_id == current.pk


def test_reattach_leaves_another_session_alone(transactional_db):
    other = ProcessRun.objects.create(
        provider=Provider.CLAUDE_CODE.value, session_id="other",
        started_at=djtz.now(), state=AgentState.DEAD.value,
        last_state_change_at=djtz.now(),
    )
    current = _run(state=AgentState.USER_TURN)

    assert reattach_crons_and_purge_old_runs(SESSION_ID, current.pk) == (0, 0)
    assert ProcessRun.objects.filter(pk=other.pk).exists()


def test_a_start_failure_retries_with_a_warning_and_no_unexpected_error(monkeypatch):
    manager = SimpleNamespace(
        _agents={},
        _session_is_hybrid=AsyncMock(return_value=False),
        send_to_session=AsyncMock(side_effect=AgentStartError("did not start", reason="start_failed")),
    )
    monkeypatch.setattr(
        "twicc.providers.claude_code.agent.manager.get_claude_code_agent_manager", lambda: manager,
    )
    monkeypatch.setattr(cron_restart, "_retry_delays", lambda initial_delay: iter([0, 0]))
    restart_data = {"session_id": SESSION_ID, "restored_crons": [], "crons_to_recreate": []}
    # The second attempt finds nothing left to restart, which ends the loop.
    monkeypatch.setattr(cron_restart, "_collect_restart_data", MagicMock(side_effect=[restart_data, None]))
    monkeypatch.setattr(cron_restart, "_build_restart_message", lambda restored, to_recreate: "restart")

    with patch.object(cron_restart, "logger") as logger:
        asyncio.run(cron_restart.restart_session_crons(SESSION_ID, stop_event=asyncio.Event()))

    manager.send_to_session.assert_awaited_once()
    logger.error.assert_not_called()
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[1:3] == (SESSION_ID, 1)
