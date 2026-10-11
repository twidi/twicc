"""A deliberate stop must not resurrect a Claude Code session through its crons.

Covers the two rules that decide whether a dead session comes back:

- :meth:`ClaudeCodeHelpers.should_keep_dead_process_run` — keeping the DEAD
  :class:`ProcessRun` row is what both restart paths (runtime and boot) select
  on, so a deliberate stop must drop it.
- :func:`_prepare_restarts` — the boot-time selection, which must ignore (and
  clean up) archived sessions.

Plus the archive-side cleanup for a session that was ALREADY dead when the
user archived it.
"""

import asyncio
from types import SimpleNamespace

import pytest
from django.utils import timezone as djtz

from twicc.agent import DELIBERATE_STOP_REASONS
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Session, SessionCron, SessionType
from twicc.core.services.session_update import _drop_dead_process_runs
from twicc.providers.claude_code.cron_restart import _prepare_restarts
from twicc.providers.claude_code.helpers import ClaudeCodeHelpers

SESSION_ID = "sess-cron-stop"


@pytest.fixture
def process_run(transactional_db):
    """A DEAD Claude Code process run carrying one active cron."""
    now = djtz.now()
    run = ProcessRun.objects.create(
        provider=Provider.CLAUDE_CODE.value,
        session_id=SESSION_ID,
        started_at=now,
        state=AgentState.DEAD.value,
        last_state_change_at=now,
    )
    SessionCron.objects.create(
        provider=Provider.CLAUDE_CODE.value,
        cron_id="cron-1",
        session_id=SESSION_ID,
        process_run=run,
        cron_expr="* * * * *",
        recurring=True,
        prompt="watch something",
        created_at=now,
        next_fire=now,
    )
    return run


@pytest.fixture
def session(transactional_db, project):
    now = djtz.now()
    return Session.objects.create(
        id=SESSION_ID, project=project, provider=Provider.CLAUDE_CODE.value,
        file_path=f"{SESSION_ID}.jsonl", type=SessionType.SESSION,
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=1,
    )


@pytest.fixture
def project(transactional_db):
    from twicc.core.models import Project

    return Project.objects.create(id="-tmp-cron-stop", directory="/tmp/cron-stop")


@pytest.fixture
def passthrough_db_write_lock(monkeypatch):
    """The global DB writer only runs at app boot; run the write factory directly."""
    async def _passthrough(coro_factory, **kwargs):
        return await coro_factory()

    monkeypatch.setattr(
        "twicc.core.services.session_update.run_under_db_write_lock",
        _passthrough,
    )


def _agent(kill_reason):
    """A live-agent stand-in carrying only what the rule reads."""
    return SimpleNamespace(kill_reason=kill_reason, _first_user_turn_reached=True)


@pytest.mark.parametrize("reason", sorted(DELIBERATE_STOP_REASONS))
def test_deliberate_stop_drops_the_row(process_run, reason):
    """Stop, hard kill and archive all discard the row, crons or not."""
    helpers = ClaudeCodeHelpers()
    assert helpers.should_keep_dead_process_run(process_run, agent=_agent(reason)) is False


@pytest.mark.parametrize("reason", ["shutdown", "apply-settings", "switch-hybrid", "error", None])
def test_unsolicited_death_keeps_the_row(process_run, reason):
    """Deaths nobody asked for keep their crons for the restart paths."""
    helpers = ClaudeCodeHelpers()
    assert helpers.should_keep_dead_process_run(process_run, agent=_agent(reason)) is True


def test_death_before_first_user_turn_drops_the_row(process_run):
    helpers = ClaudeCodeHelpers()
    agent = SimpleNamespace(kill_reason="error", _first_user_turn_reached=False)
    assert helpers.should_keep_dead_process_run(process_run, agent=agent) is False


def test_boot_cleanup_ignores_the_agent_context(process_run):
    """Called without an agent (boot), only cron existence decides."""
    helpers = ClaudeCodeHelpers()
    assert helpers.should_keep_dead_process_run(process_run) is True
    process_run.crons.all().delete()
    assert helpers.should_keep_dead_process_run(process_run) is False


def test_prepare_restarts_selects_a_live_session(process_run, session):
    assert _prepare_restarts() == [SESSION_ID]
    assert ProcessRun.objects.filter(pk=process_run.pk).exists()


def test_prepare_restarts_drops_an_archived_session(process_run, session):
    """Never resurrect a session the user put away — and clean up its crons."""
    Session.objects.filter(id=SESSION_ID).update(archived=True)

    assert _prepare_restarts() == []
    assert not ProcessRun.objects.filter(pk=process_run.pk).exists()
    assert not SessionCron.objects.filter(session_id=SESSION_ID).exists()


def test_prepare_restarts_drops_a_session_missing_from_db(process_run):
    """No Session row (JSONL deleted between instances) — pre-existing rule."""
    assert _prepare_restarts() == []
    assert not ProcessRun.objects.filter(pk=process_run.pk).exists()


def test_archiving_drops_dead_process_runs(process_run, passthrough_db_write_lock):
    """Archiving an already-dead session still kills its pending crons."""
    asyncio.run(_drop_dead_process_runs(SESSION_ID))

    assert not ProcessRun.objects.filter(pk=process_run.pk).exists()
    assert not SessionCron.objects.filter(session_id=SESSION_ID).exists()


def test_archiving_leaves_a_running_process_run(transactional_db, passthrough_db_write_lock):
    """Only DEAD rows are dropped; a live row settles itself on death."""
    now = djtz.now()
    run = ProcessRun.objects.create(
        provider=Provider.CLAUDE_CODE.value,
        session_id=SESSION_ID,
        started_at=now,
        state=AgentState.USER_TURN.value,
        last_state_change_at=now,
    )

    asyncio.run(_drop_dead_process_runs(SESSION_ID))

    assert ProcessRun.objects.filter(pk=run.pk).exists()
