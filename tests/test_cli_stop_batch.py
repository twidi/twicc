"""The shared stop mechanism, behind ``processes stop`` and ``sessions stop``.

It had **no** test of its own: `processes stop`'s two tests assert pre-flight
guards that return before reaching any of this, and `sessions stop`'s suite
mocks the whole function to test selection. Extracting it into a shared module
made that gap worse — one defect here would now be two broken commands — so
ten mutants survived the full suite until this file existed.

What matters here is the contract every caller reads: one entry per input id,
in input order, with a status that says what actually happened.

Scope, stated plainly: these cover the **pre-check** half — the half that
decides whether a drop is emitted at all. The submit/poll half still has no
test of its own; it needs a live transport or a mock heavy enough to be worth
its own file.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import _stop_batch
from twicc.core.models import ProcessRun, Project, Session


TWICC_PID = 4242


@pytest.fixture
def project(db):
    return Project.objects.create(id="batch-project", directory="/tmp/batch")


def make_session(project, sid, **kwargs):
    return Session.objects.create(
        id=sid, project=project, provider="claude_code", file_path=f"{sid}.jsonl",
        created_at="2026-09-18T10:00:00Z", user_message_count=1, **kwargs,
    )


def make_run(sid):
    return ProcessRun.objects.create(
        session_id=sid, provider="claude_code", twicc_pid=TWICC_PID,
        state=AgentState.ASSISTANT_TURN.value, started_at=timezone.now(),
        last_state_change_at=timezone.now(),
    )


def test_an_unknown_id_is_reported_not_dropped(project, db):
    """One entry per input id is the contract both commands publish: a caller
    zips its ids against the output."""
    result = _stop_batch.stop_session_ids(
        ["ghost"], timeout=1, force=False, twicc_pid=TWICC_PID,
    )

    assert [e["session_id"] for e in result] == ["ghost"]
    assert result[0]["status"] == "skipped_unknown"
    assert result[0]["session_known"] is False
    assert result[0]["error"]


def test_a_subagent_is_refused_with_its_own_status(project, db):
    """The lookup codes are mapped to distinct statuses on purpose — collapsing
    them to `skipped_unknown` would tell a caller its id was a typo."""
    parent = make_session(project, "parent")
    Session.objects.create(
        id="sub", project=project, provider="claude_code", file_path="sub.jsonl",
        created_at="2026-09-18T10:00:00Z", user_message_count=1,
        type="subagent", parent_session=parent,
    )

    result = _stop_batch.stop_session_ids(
        ["sub"], timeout=1, force=False, twicc_pid=TWICC_PID,
    )

    assert result[0]["status"] == "skipped_subagent"


def test_a_stale_session_is_refused_with_its_own_status(project, db):
    make_session(project, "stale", stale=True)

    result = _stop_batch.stop_session_ids(
        ["stale"], timeout=1, force=False, twicc_pid=TWICC_PID,
    )

    assert result[0]["status"] == "skipped_stale"


def test_the_output_follows_the_input_order(project, db):
    """A caller aligns the array with the ids it passed; reordering breaks
    every `zip(ids, output)` silently."""
    result = _stop_batch.stop_session_ids(
        ["c", "a", "b"], timeout=1, force=False, twicc_pid=TWICC_PID,
    )

    assert [e["session_id"] for e in result] == ["c", "a", "b"]


def test_a_known_session_is_flagged_known_even_when_skipped(project, db):
    """`session_known` separates a typo from a session that genuinely cannot
    be stopped — the whole reason the field exists."""
    make_session(project, "stale", stale=True)

    result = _stop_batch.stop_session_ids(
        ["stale", "ghost"], timeout=1, force=False, twicc_pid=TWICC_PID,
    )

    assert [e["session_known"] for e in result] == [True, False]


def test_a_live_row_alone_counts_as_known(project, db):
    """A brand-new session the JSONL watcher has not seen yet: no `Session`
    row, but TwiCC does have a trace of it."""
    make_run("brand-new")

    result = _stop_batch.stop_session_ids(
        ["brand-new"], timeout=1, force=False, twicc_pid=TWICC_PID,
    )

    assert result[0]["session_known"] is True
    assert result[0]["status"] == "skipped_unknown"


def test_a_row_from_another_instance_does_not_count_as_known(project, db):
    """Scoped to this TwiCC: a previous instance's row says nothing about what
    this one knows."""
    ProcessRun.objects.create(
        session_id="elsewhere", provider="claude_code", twicc_pid=TWICC_PID + 1,
        state=AgentState.ASSISTANT_TURN.value, started_at=timezone.now(),
        last_state_change_at=timezone.now(),
    )

    result = _stop_batch.stop_session_ids(
        ["elsewhere"], timeout=1, force=False, twicc_pid=TWICC_PID,
    )

    assert result[0]["session_known"] is False
