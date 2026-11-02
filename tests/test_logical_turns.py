"""The completion journal outlives disposable processes and exact input boundaries."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from twicc.agent.completion_types import CompletionCandidate, CompletionSnapshot, NativeMessageRef
from twicc.core.enums import Provider
from twicc.core.models import LogicalTurn, LogicalTurnInput, McpEventSubscription, ProcessRun, Project, Session
from twicc.core.services.logical_turns import (
    bind_turn_input, bind_turn_source, input_for_token, open_logical_turn,
    settle_abandoned_turns, store_turn_snapshot,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def journal_fixture():
    now = timezone.now()
    project = Project.objects.create(id="journal-project", directory="/tmp/journal")
    session = Session.objects.create(
        id="journal-session", project=project, provider="codex",
        storage_incarnation=str(uuid4()), history_epoch=3,
    )
    process_run = ProcessRun.objects.create(
        provider="codex", session_id=session.id, started_at=now, last_state_change_at=now,
    )
    turn_id, run_id = str(uuid4()), str(uuid4())
    args = dict(
        turn_id=turn_id, session_id=session.id, provider="codex", run_id=run_id,
        process_run_id=process_run.pk, ordinal=1, source_floor=0,
        storage_incarnation=session.storage_incarnation, history_epoch=session.history_epoch, opened_at=now,
    )
    open_logical_turn(**args)
    final_ref = NativeMessageRef("codex_item", "answer", "native-turn")
    snapshot = CompletionSnapshot(turn_id, run_id, 1, 2, final_ref, (final_ref,), "normal")
    return SimpleNamespace(**args, args=args, session=session, process_run=process_run, snapshot=snapshot)


def test_closed_journal_survives_process_deletion(journal_fixture):
    f = journal_fixture
    assert store_turn_snapshot(f.snapshot, closed_at=f.opened_at) is True
    assert store_turn_snapshot(f.snapshot._replace(revision=1, outcome=None), closed_at=None) is False
    f.process_run.delete()
    row = LogicalTurn.objects.get(pk=f.turn_id)
    assert row.outcome == "normal"
    assert row.process_run_id == f.process_run_id
    assert row.final_refs == [f.snapshot.final_refs[0]._asdict()]


def test_input_token_keeps_session_and_cursor(journal_fixture):
    f = journal_fixture
    token = str(uuid4())
    bind_turn_input(token=token, turn_id=f.turn_id, admission_cursor=0, status="accepted")
    item = input_for_token(session_id=f.session.id, token=token)
    assert str(item.turn_id) == f.turn_id
    assert item.admission_cursor == 0
    assert input_for_token(session_id="another-session", token=token) is None
    assert input_for_token(session_id=f.session.id, token="invalid") is None


def test_journal_can_open_before_session_exists(journal_fixture):
    args = {**journal_fixture.args, "turn_id": str(uuid4()), "session_id": "not-indexed", "ordinal": 2,
            "storage_incarnation": None, "history_epoch": None}
    open_logical_turn(**args)
    assert LogicalTurn.objects.get(pk=args["turn_id"]).storage_incarnation is None
    assert not Session.objects.filter(pk="not-indexed").exists()


def test_input_token_cannot_move_to_another_interval(journal_fixture):
    f = journal_fixture
    other_id, token = str(uuid4()), str(uuid4())
    open_logical_turn(**{**f.args, "turn_id": other_id, "ordinal": 2})
    bind_turn_input(token=token, turn_id=f.turn_id, admission_cursor=0, status="accepted")
    with pytest.raises(ValueError):
        bind_turn_input(token=token, turn_id=other_id, admission_cursor=0, status="accepted")
    assert str(LogicalTurnInput.objects.get(pk=token).turn_id) == f.turn_id


def test_input_cursor_stays_at_admission_during_deferred_delivery(journal_fixture):
    f = journal_fixture
    token = str(uuid4())
    bind_turn_input(token=token, turn_id=f.turn_id, admission_cursor=0, status="pending")
    bind_turn_input(token=token, turn_id=f.turn_id, admission_cursor=0, status="accepted")
    with pytest.raises(ValueError):
        bind_turn_input(token=token, turn_id=f.turn_id, admission_cursor=40, status="accepted")
    assert LogicalTurnInput.objects.get(pk=token).admission_cursor == 0


def test_positions_increase_across_run_replacement(journal_fixture):
    f = journal_fixture
    other_id = str(uuid4())
    open_logical_turn(**{**f.args, "turn_id": other_id, "run_id": str(uuid4()), "opened_at": f.opened_at - timedelta(days=1)})
    assert LogicalTurn.objects.get(pk=f.turn_id).position == 1
    assert LogicalTurn.objects.get(pk=other_id).position == 2


def test_closed_outcome_and_final_identities_are_immutable(journal_fixture):
    f = journal_fixture
    assert store_turn_snapshot(f.snapshot, closed_at=f.opened_at)
    for change in ({"outcome": "ended"}, {"final_refs": ()}, {"first_ref": None}):
        assert not store_turn_snapshot(f.snapshot._replace(revision=3, **change), closed_at=f.opened_at)
    assert LogicalTurn.objects.get(pk=f.turn_id).revision == 2


def test_snapshot_rejects_stale_or_wrong_run(journal_fixture):
    f = journal_fixture
    snapshot = f.snapshot._replace(outcome=None)
    assert store_turn_snapshot(snapshot, closed_at=None)
    assert not store_turn_snapshot(snapshot, closed_at=None)
    assert not store_turn_snapshot(snapshot._replace(revision=3, run_id=str(uuid4())), closed_at=None)
    assert not store_turn_snapshot(snapshot._replace(revision=3, ordinal=2), closed_at=None)


def test_abandoned_settlement_preserves_completed_outcomes(journal_fixture):
    f = journal_fixture
    assert store_turn_snapshot(f.snapshot, closed_at=f.opened_at)
    open_id = str(uuid4())
    open_logical_turn(**{**f.args, "turn_id": open_id, "ordinal": 2})
    at = f.opened_at + timedelta(seconds=1)
    assert settle_abandoned_turns(at=at) == 1
    assert settle_abandoned_turns(at=at) == 0
    assert LogicalTurn.objects.get(pk=f.turn_id).outcome == "normal"
    row = LogicalTurn.objects.get(pk=open_id)
    assert row.outcome == "ended" and row.closed_at == at
    assert row.revision == 1


def test_boot_cleanup_settles_before_process_deletion(journal_fixture):
    from twicc.agent.process_run_cleanup import CleanupStaleProcessRunsJob, _apply_cleanup_stale_process_runs_job

    f = journal_fixture
    assert store_turn_snapshot(f.snapshot._replace(outcome=None), closed_at=None)
    completed_id = str(uuid4())
    open_logical_turn(**{**f.args, "turn_id": completed_id, "ordinal": 2})
    assert store_turn_snapshot(f.snapshot._replace(turn_id=completed_id, ordinal=2), closed_at=f.opened_at)
    _apply_cleanup_stale_process_runs_job(CleanupStaleProcessRunsJob(future=None))
    assert not ProcessRun.objects.filter(pk=f.process_run_id).exists()
    row = LogicalTurn.objects.get(pk=f.turn_id)
    assert row.outcome == "ended" and row.final_refs[0]["native_id"] == "answer"
    assert LogicalTurn.objects.get(pk=completed_id).outcome == "normal"
    assert not store_turn_snapshot(f.snapshot._replace(revision=100), closed_at=f.opened_at)


@pytest.mark.parametrize("missing_session", [False, True])
def test_explicit_session_deletion_purges_journal_and_inputs(journal_fixture, missing_session):
    from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload

    f = journal_fixture
    token = str(uuid4())
    bind_turn_input(token=token, turn_id=f.turn_id, admission_cursor=0, status="accepted")
    if missing_session:
        f.session.delete()
    _apply_delete_sessions_payload(DeleteSessionsPayload(provider=Provider.CODEX, session_ids=[f.session_id]))
    assert not LogicalTurn.objects.filter(pk=f.turn_id).exists()
    assert not LogicalTurnInput.objects.filter(pk=token).exists()


def test_closed_source_scope_cannot_rebind(journal_fixture):
    f = journal_fixture
    assert store_turn_snapshot(f.snapshot, closed_at=f.opened_at)
    for changes in ({"storage_incarnation": str(uuid4())}, {"history_epoch": 4}):
        f.session.__class__.objects.filter(pk=f.session.id).update(**changes)
        f.session.refresh_from_db()
        assert not bind_turn_source(
            turn_id=f.turn_id, session_id=f.session.id, provider="codex",
            storage_incarnation=f.session.storage_incarnation, history_epoch=f.session.history_epoch,
            observed_ref=f.snapshot.first_ref,
        )
    row = LogicalTurn.objects.get(pk=f.turn_id)
    assert row.storage_incarnation == f.storage_incarnation and row.history_epoch == f.history_epoch
    with pytest.raises(ValueError):
        open_logical_turn(**{**f.args, "storage_incarnation": f.session.storage_incarnation})


def test_missing_scope_requires_matching_native_observation(journal_fixture):
    f = journal_fixture
    turn_id = str(uuid4())
    open_logical_turn(**{**f.args, "turn_id": turn_id, "ordinal": 2,
                         "storage_incarnation": None, "history_epoch": None})
    snapshot = f.snapshot._replace(turn_id=turn_id, ordinal=2, outcome=None)
    assert store_turn_snapshot(snapshot, closed_at=None)
    kwargs = dict(turn_id=turn_id, session_id=f.session.id, provider="codex",
                  storage_incarnation=f.storage_incarnation, history_epoch=f.history_epoch)
    assert not bind_turn_source(**kwargs, observed_ref=NativeMessageRef("codex_item", "unrelated"))
    assert not bind_turn_source(**{**kwargs, "session_id": "other"}, observed_ref=snapshot.first_ref)
    assert not bind_turn_source(**{**kwargs, "history_epoch": 9}, observed_ref=snapshot.first_ref)
    assert not bind_turn_source(**{**kwargs, "provider": "claude_code"}, observed_ref=snapshot.first_ref)
    assert not bind_turn_source(**{**kwargs, "storage_incarnation": str(uuid4())}, observed_ref=snapshot.first_ref)
    assert not bind_turn_source(**kwargs, observed_ref=NativeMessageRef("unresolved", "answer"))
    assert bind_turn_source(**kwargs, observed_ref=snapshot.first_ref)
    row = LogicalTurn.objects.get(pk=turn_id)
    assert row.storage_incarnation == f.storage_incarnation and row.history_epoch == f.history_epoch


def test_immutable_snapshot_types_and_nullable_subscription_cursor():
    candidate = CompletionCandidate("turn", 1, 2.0)
    with pytest.raises(AttributeError):
        candidate.revision = 2
    field = McpEventSubscription._meta.get_field("last_logical_turn_id")
    assert field.null and field.get_default() is None and not field.is_relation


def test_parent_deletion_purges_journal_of_descendant_from_another_provider(journal_fixture):
    from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload

    f = journal_fixture
    child = Session.objects.create(
        id="journal-child", file_path="/tmp/journal-child.jsonl", project=f.session.project,
        provider="claude_code", parent_session=f.session,
    )
    child_turn_id = str(uuid4())
    open_logical_turn(**{
        **f.args, "turn_id": child_turn_id, "session_id": child.id, "provider": "claude_code",
        "run_id": str(uuid4()), "storage_incarnation": None, "history_epoch": None,
    })
    _apply_delete_sessions_payload(DeleteSessionsPayload(provider=Provider.CODEX, session_ids=[f.session_id]))
    assert not Session.objects.filter(pk=child.id).exists()
    assert not LogicalTurn.objects.filter(pk=child_turn_id).exists()
