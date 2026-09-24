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
    """One entry per input id is the contract of the shared function:
    `processes stop` emits the list as is, `sessions stop` re-keys it by id
    under `results`."""
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
    """`processes stop` emits this list as is and `sessions stop` keys it
    under `results` in the same order, so a reordering breaks both."""
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


def test_the_caller_is_skipped_before_any_submission(project, monkeypatch):
    """No drop is submitted for the caller; its entry says where to go instead."""
    make_session(project, "me")

    def no_submit(payload, *, kind):
        raise AssertionError(f"nothing may be submitted here, got {payload}")

    monkeypatch.setattr("twicc.cli._drop_request.transport.submit", no_submit)
    [entry] = _stop_batch.stop_session_ids(
        ["me"], timeout=1, force=False, twicc_pid=TWICC_PID, caller_id="me",
    )
    assert entry["status"] == "skipped_self"
    assert entry["request_uuid"] is None
    assert "`session self stop`" in entry["error"]
    assert entry["session_known"] is True


def test_processes_stop_passes_no_caller(project, monkeypatch, capsysbinary):
    """The retired command keeps stopping the caller until its removal."""
    from twicc.cli import processes_stop

    monkeypatch.setattr("twicc.cli._drop_request.transport.ensure_server_available", lambda: None)
    monkeypatch.setattr(
        "twicc.cli._twicc_info.resolve_live_twicc", lambda: type("I", (), {"pid": TWICC_PID})(),
    )
    # A real caller, so a mutant that resolved it and passed `caller_id=`
    # would be seen (with no caller it would pass `None` and survive).
    me = make_session(project, "me")
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session", lambda: me)
    seen = {}

    def fake_stop(ids, **kwargs):
        seen.update(kwargs, ids=list(ids))
        return []

    monkeypatch.setattr("twicc.cli._stop_batch.stop_session_ids", fake_stop)
    processes_stop.stop_cmd(["me"], timeout=5)
    assert seen["ids"] == ["me"], "the caller reaches the stopper"
    assert "caller_id" not in seen
