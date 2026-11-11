"""Durable decision precedence and admitted transaction boundaries.

Each test protects a publication invariant, without starting semantic ingress.
"""

import importlib
import logging
from datetime import timedelta
from uuid import uuid4

import orjson
import pytest
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.migrations import CreateModel
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from twicc.agent.completion_types import CompletionSnapshot, NativeMessageRef
from twicc.agent.semantic_types import Classification, DecisionRequest, SourceScope
from twicc.core.models import LogicalTurn, Project, Session, TimelineDecision, TimelineEvent, TimelineKind
from twicc.core.services.logical_turns import open_logical_turn, store_turn_snapshot
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_decisions import (
    accept_fallback, admit_decision, bind_decision_source, cancel_decision,
    force_message, register_decision_wakeup, resolve_decision,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def decision_fixture():
    project = Project.objects.create(id="semantic-project", directory="/tmp/semantic")
    session = Session.objects.create(
        id="semantic-session", project=project, provider="claude_code",
        storage_incarnation=str(uuid4()), history_epoch=2,
    )
    now = timezone.now()
    scope = SourceScope(session.provider, session.storage_incarnation, session.history_epoch)
    request = DecisionRequest(
        str(uuid4()), "message", session.id, scope,
        {"message_refs": [{"kind": "claude_message", "native_id": "exact-answer", "turn_id": None}],
         "text": "PRIVATE candidate text"},
        now + timedelta(seconds=45), "provider",
    )
    yield session, now, request
    register_decision_wakeup(None)


def admit(request, now, *, identity="native:exact-answer"):
    return admit_decision(request, identity_key=identity, occurred_at=now, not_before=now + timedelta(seconds=1))


def ending_request(decision_fixture, *, revision=3):
    session, now, request = decision_fixture
    turn_id, run_id = str(uuid4()), str(uuid4())
    open_logical_turn(
        turn_id=turn_id, session_id=session.id, provider=session.provider, run_id=run_id,
        process_run_id=None, ordinal=1, source_floor=0,
        storage_incarnation=session.storage_incarnation, history_epoch=session.history_epoch, opened_at=now,
    )
    ref = NativeMessageRef("claude_message", "captured-final")
    snapshot = CompletionSnapshot(turn_id, run_id, 1, revision, ref, (ref,), None)
    payload = {
        "run_id": run_id, "turn_id": turn_id,
        "candidate": {"turn_id": turn_id, "revision": revision, "deadline": 10.0},
        "snapshot": {**snapshot._asdict(), "first_ref": ref._asdict(), "final_refs": [ref._asdict()]},
        "message_refs": [ref._asdict()],
        "shell_refs": [{"key": "shell-1", "owner_ref": ref._asdict(), "started_at": "2026-11-10T12:00:00Z",
                        "generation": 4, "command": "PRIVATE shell command", "output_path": "PRIVATE path"}],
    }
    return request._replace(id=str(uuid4()), kind="ending", payload=payload), snapshot


def records(caplog):
    return [orjson.loads(record.getMessage().split("semantic_decision ", 1)[1])
            for record in caplog.records if "semantic_decision " in record.getMessage()]


def test_pending_is_not_public(decision_fixture, monkeypatch):
    from twicc.core.services import timeline_signal

    _, now, request = decision_fixture
    signals = []
    monkeypatch.setattr(timeline_signal, "poke_on_commit", lambda: signals.append(True))
    decision_id = admit(request, now)
    row = TimelineDecision.objects.get(pk=decision_id)
    assert row.status == "pending"
    assert row.applied_at is None
    assert row.published_event_id is None
    assert not TimelineEvent.objects.exists()
    assert not signals
    assert resolve_decision(decision_id=decision_id, classification=Classification("NO", "routine"),
                            effective_route="haiku", now=now)
    assert not TimelineEvent.objects.exists()
    assert not signals


@pytest.mark.parametrize("unknown_scope", [False, True])
def test_scoped_identity_is_unique(decision_fixture, unknown_scope):
    _, now, request = decision_fixture
    if unknown_scope:
        request = request._replace(scope=SourceScope("claude_code", None, None))
    first_id = admit(request, now)
    assert admit(request._replace(id=str(uuid4())), now) == first_id
    row = TimelineDecision.objects.get(pk=first_id)
    with pytest.raises(IntegrityError), transaction.atomic():
        TimelineDecision.objects.create(
            id=uuid4(), session_id=row.session_id, provider=row.provider,
            storage_incarnation=row.storage_incarnation, history_epoch=row.history_epoch,
            kind=row.kind, identity_key=row.identity_key, occurred_at=now,
            not_before=now, expires_at=request.expires_at,
        )
    other = request._replace(id=str(uuid4()), scope=SourceScope("claude_code", str(uuid4()), 3))
    assert admit(other, now) != first_id


def test_force_beats_rejection(decision_fixture):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    assert resolve_decision(decision_id=decision_id, classification=Classification("NO", "routine"),
                            effective_route="haiku", now=now)
    force_message(decision_id=decision_id, reason="confirmed_final")
    row = TimelineDecision.objects.get(pk=decision_id)
    assert (row.status, row.forced) == ("accepted", True)
    assert row.applied_at is None
    assert not resolve_decision(decision_id=decision_id, classification=Classification("NO", "late"),
                                effective_route="haiku", now=now)
    assert TimelineDecision.objects.get(pk=decision_id).forced


@pytest.mark.parametrize("action", ["fallback", "cancel"])
def test_late_result_cannot_reverse_fallback_or_cancel(decision_fixture, action):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    if action == "fallback":
        assert accept_fallback(decision_id=decision_id, reason="timeout", now=now)
        status = "accepted"
    else:
        assert cancel_decision(decision_id=decision_id, reason="root_activity")
        status = "canceled"
    assert not resolve_decision(decision_id=decision_id, classification=Classification("NO", "late"),
                                effective_route="haiku", now=now)
    assert TimelineDecision.objects.get(pk=decision_id).status == status


def test_force_cannot_reverse_cancellation_or_force_ending(decision_fixture):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    cancel_decision(decision_id=decision_id, reason="root_activity")
    force_message(decision_id=decision_id, reason="confirmed_final")
    assert TimelineDecision.objects.get(pk=decision_id).status == "canceled"
    ending, _ = ending_request(decision_fixture)
    ending_id = admit(ending, now, identity="ending")
    resolve_decision(decision_id=ending_id, classification=Classification("NO", "blocking"),
                     effective_route="haiku", now=now)
    force_message(decision_id=ending_id, reason="confirmed_final")
    row = TimelineDecision.objects.get(pk=ending_id)
    assert (row.status, row.forced, row.force_pending) == ("rejected", False, False)


def test_expired_model_result_uses_timeout_fallback(decision_fixture):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    assert resolve_decision(decision_id=decision_id, classification=Classification("NO", "late model"),
                            effective_route="haiku", now=request.expires_at)
    row = TimelineDecision.objects.get(pk=decision_id)
    assert (row.status, row.reason, row.effective_route) == ("accepted", "timeout", None)


def test_resolution_retains_retry_obligation_and_rollback(decision_fixture, django_capture_on_commit_callbacks):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    wakes = []
    register_decision_wakeup(lambda: wakes.append(True))
    with pytest.raises(RuntimeError), django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        accept_fallback(decision_id=decision_id, reason="timeout", now=now)
        raise RuntimeError("publication transaction fails")
    row = TimelineDecision.objects.get(pk=decision_id)
    assert (row.status, row.applied_at) == ("pending", None)
    assert not wakes
    with django_capture_on_commit_callbacks(execute=True):
        assert accept_fallback(decision_id=decision_id, reason="timeout", now=now)
    assert wakes == [True]
    row.refresh_from_db()
    assert (row.status, row.applied_at, row.published_event_id) == ("accepted", None, None)


def test_pending_ending_persists_exact_open_revision(decision_fixture):
    _, now, _ = decision_fixture
    request, snapshot = ending_request(decision_fixture)
    decision_id = admit(request, now, identity="ending")
    turn = LogicalTurn.objects.get(pk=snapshot.turn_id)
    assert turn.revision == 3
    assert turn.outcome is None
    assert turn.closed_at is None
    assert turn.final_refs == [{"kind": "claude_message", "native_id": "captured-final", "turn_id": None}]
    row = TimelineDecision.objects.get(pk=decision_id)
    assert str(row.run_id) == snapshot.run_id
    assert str(row.turn_id) == snapshot.turn_id
    assert not row.force_pending
    assert accept_fallback(decision_id=decision_id, reason="timeout", now=now)
    assert TimelineDecision.objects.get(pk=decision_id).force_pending


def test_pending_ending_cancels_against_newer_revision(decision_fixture):
    _, now, _ = decision_fixture
    request, snapshot = ending_request(decision_fixture)
    assert store_turn_snapshot(snapshot._replace(revision=4), closed_at=None)
    decision_id = admit(request, now, identity="ending")
    assert TimelineDecision.objects.get(pk=decision_id).status == "canceled"
    assert LogicalTurn.objects.get(pk=snapshot.turn_id).revision == 4


def test_admission_rollback_keeps_journal_and_decision_atomic(decision_fixture):
    _, now, _ = decision_fixture
    request, snapshot = ending_request(decision_fixture)
    with pytest.raises(RuntimeError), transaction.atomic():
        admit(request, now, identity="ending")
        raise RuntimeError("admission fails")
    assert not TimelineDecision.objects.exists()
    assert LogicalTurn.objects.get(pk=snapshot.turn_id).revision == 0


@pytest.mark.parametrize("status", ["pending", "running"])
def test_both_unresolved_states_accept_model_verdict(decision_fixture, status):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    TimelineDecision.objects.filter(pk=decision_id).update(status=status)
    assert resolve_decision(decision_id=decision_id, classification=Classification("YES", "useful answer"),
                            effective_route="haiku", now=now)
    row = TimelineDecision.objects.get(pk=decision_id)
    assert (row.status, row.effective_route, row.applied_at) == ("accepted", "haiku", None)


def test_publication_rollback_retains_accepted_obligation(decision_fixture, django_capture_on_commit_callbacks, monkeypatch):
    from twicc.core.services import timeline_signal

    session, now, request = decision_fixture
    decision_id = admit(request, now)
    assert accept_fallback(decision_id=decision_id, reason="timeout", now=now)
    signals = []
    monkeypatch.setattr(timeline_signal, "poke", lambda: signals.append(True))
    with pytest.raises(RuntimeError), django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        event = record_event_sync(
            TimelineKind.ASSISTANT_MESSAGE_SENT, session_id=session.id, project_id=session.project_id,
            provider=session.provider, actor="agent", occurred_at=now, dedupe_key="msg:rollback",
            data={"text": request.payload["text"], "source_refs": request.payload["message_refs"],
                  "source_line": 1, "storage_incarnation": session.storage_incarnation, "history_epoch": 2},
        )
        assert event is not None
        TimelineDecision.objects.filter(pk=decision_id).update(published_event_id=event.pk, applied_at=now)
        raise RuntimeError("publication fails")
    row = TimelineDecision.objects.get(pk=decision_id)
    assert (row.status, row.published_event_id, row.applied_at) == ("accepted", None, None)
    assert not TimelineEvent.objects.exists()
    assert not signals


def test_database_failure_is_not_fallback(decision_fixture, caplog, django_capture_on_commit_callbacks):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    caplog.set_level(logging.DEBUG, logger="twicc.core.services.timeline_decisions")

    def unavailable_database(execute, sql, params, many, context):
        if sql.startswith('UPDATE "core_timelinedecision"'):
            raise OperationalError("simulated unavailable database")
        return execute(sql, params, many, context)

    with (
        pytest.raises(OperationalError),
        django_capture_on_commit_callbacks(execute=True),
        connection.execute_wrapper(unavailable_database),
    ):
        resolve_decision(decision_id=decision_id, classification=Classification("YES", "useful"),
                         effective_route="haiku", now=now)
    assert TimelineDecision.objects.get(pk=decision_id).status == "pending"
    assert not records(caplog)


@pytest.mark.parametrize("conflict", ["closed", "different_run", "same_revision_different_refs"])
def test_ending_admission_cancels_conflicting_journal(decision_fixture, conflict):
    _, now, _ = decision_fixture
    request, snapshot = ending_request(decision_fixture)
    if conflict == "closed":
        assert store_turn_snapshot(snapshot._replace(outcome="ended"), closed_at=now)
    elif conflict == "different_run":
        LogicalTurn.objects.filter(pk=snapshot.turn_id).update(run_id=uuid4())
    else:
        assert store_turn_snapshot(snapshot._replace(final_refs=()), closed_at=None)
    decision_id = admit(request, now, identity="ending")
    assert TimelineDecision.objects.get(pk=decision_id).status == "canceled"


def test_payload_copy_cannot_change_exact_captured_refs(decision_fixture):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    request.payload["message_refs"][0]["native_id"] = "later-answer"
    row = TimelineDecision.objects.get(pk=decision_id)
    assert row.payload["message_refs"][0]["native_id"] == "exact-answer"


def test_committed_decision_logs_scoped_references(decision_fixture, caplog, django_capture_on_commit_callbacks, monkeypatch):
    _, now, _ = decision_fixture
    request, _ = ending_request(decision_fixture)
    decision_id = admit(request, now, identity="ending")
    # ASGI collection can reapply settings_test's disable_existing_loggers.
    monkeypatch.setattr(logging.getLogger("twicc.core.services.timeline_decisions"), "disabled", False)
    caplog.set_level(logging.DEBUG, logger="twicc.core.services.timeline_decisions")
    with django_capture_on_commit_callbacks(execute=True):
        assert resolve_decision(decision_id=decision_id, classification=Classification("NO", "line 1\n" + "x" * 600),
                                effective_route="haiku", now=now)
        assert not records(caplog)
    events = records(caplog)
    assert len(events) == 1
    event = events[0]
    assert (event["event"], event["decision_id"], event["kind"]) == ("resolved", decision_id, "ending")
    assert event["source_scope"] == request.scope._asdict()
    assert event["message_refs"] == request.payload["message_refs"]
    assert event["shell_refs"] == [{"key": "shell-1", "owner_ref": request.payload["message_refs"][0],
                                    "started_at": "2026-11-10T12:00:00Z", "generation": 4}]
    assert (event["requested_route"], event["effective_route"], event["verdict"], event["outcome"]) == (
        "provider", "haiku", "NO", "rejected")
    assert "\n" not in event["reason"] and len(event["reason"]) == 500
    assert "PRIVATE" not in caplog.text
    with django_capture_on_commit_callbacks(execute=True):
        assert not resolve_decision(decision_id=decision_id, classification=Classification("YES", "retry"),
                                    effective_route="luna", now=now)
    assert len(records(caplog)) == 1


def test_rollback_does_not_log_applied_outcome(decision_fixture, caplog, django_capture_on_commit_callbacks):
    _, now, request = decision_fixture
    decision_id = admit(request, now)
    caplog.set_level(logging.DEBUG, logger="twicc.core.services.timeline_decisions")
    with pytest.raises(RuntimeError), django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        resolve_decision(decision_id=decision_id, classification=Classification("YES", "useful"),
                         effective_route="haiku", now=now)
        raise RuntimeError("write fails")
    assert not records(caplog)
    assert TimelineDecision.objects.get(pk=decision_id).status == "pending"


def test_unknown_scope_binds_only_matching_exact_native_proof(decision_fixture):
    session, now, request = decision_fixture
    decision_id = admit(request._replace(scope=SourceScope("claude_code", None, None)), now)
    args = {"decision_id": decision_id, "scope": request.scope}
    assert not bind_decision_source(**args, observed_ref=NativeMessageRef("claude_message", "other"))
    assert not bind_decision_source(**args, observed_ref=NativeMessageRef("unresolved", "exact-answer"))
    assert bind_decision_source(**args, observed_ref=NativeMessageRef("claude_message", "exact-answer"))
    Session.objects.filter(pk=session.pk).update(storage_incarnation=str(uuid4()))
    assert not bind_decision_source(decision_id=decision_id,
                                    scope=SourceScope("claude_code", str(uuid4()), 2),
                                    observed_ref=NativeMessageRef("claude_message", "exact-answer"))
    row = TimelineDecision.objects.get(pk=decision_id)
    assert (row.storage_incarnation, row.history_epoch) == (request.scope.storage_incarnation, 2)


@pytest.mark.parametrize("kind", ["message", "ending"])
def test_scope_binding_collision_preserves_both_obligations(decision_fixture, kind):
    _, now, request = decision_fixture
    if kind == "ending":
        request, snapshot = ending_request(decision_fixture)
        LogicalTurn.objects.filter(pk=snapshot.turn_id).update(storage_incarnation=None, history_epoch=None)
    unknown_id = admit(request._replace(scope=SourceScope("claude_code", None, None)), now)
    if kind == "message":
        force_message(decision_id=unknown_id, reason="confirmed_final")
    else:
        assert accept_fallback(decision_id=unknown_id, reason="timeout", now=now)
    known_id = admit(request._replace(id=str(uuid4())), now)
    with transaction.atomic():
        with pytest.raises(ValueError, match="scope binding collision"):
            bind_decision_source(decision_id=unknown_id, scope=request.scope,
                                 observed_ref=NativeMessageRef(**request.payload["message_refs"][0]))
        unknown = TimelineDecision.objects.get(pk=unknown_id)
        known = TimelineDecision.objects.get(pk=known_id)
        assert (unknown.storage_incarnation, unknown.history_epoch, unknown.status, unknown.forced,
                unknown.force_pending, unknown.applied_at, unknown.published_event_id) == (
                    None, None, "accepted", kind == "message", kind == "ending", None, None)
        assert (known.status, known.forced, known.applied_at, known.published_event_id) == ("pending", False, None, None)


def test_migration_adds_only_decision_table_with_scheduling_indexes():
    migration = importlib.import_module("twicc.core.migrations.0173_timeline_semantic_decisions").Migration
    assert len(migration.operations) == 1
    assert isinstance(migration.operations[0], CreateModel)
    assert migration.operations[0].name == "TimelineDecision"
    with connection.cursor() as cursor:
        indexes = connection.introspection.get_constraints(cursor, TimelineDecision._meta.db_table)
    indexed_columns = {tuple(index["columns"]) for index in indexes.values() if index["index"]}
    assert {("status", "not_before"), ("status", "expires_at"), ("session_id", "turn_id")} <= indexed_columns


@pytest.mark.parametrize("historical_migration_database", [("core", "0172_session_compressed_size")], indirect=True)
def test_real_migration_preserves_existing_tables(historical_migration_database):
    database = historical_migration_database
    before = set(database.introspection.table_names())
    with database.cursor() as cursor:
        previous_columns = database.introspection.get_table_description(cursor, "core_logicalturn")
    MigrationExecutor(database).migrate([("core", "0173_timeline_semantic_decisions")])
    assert set(database.introspection.table_names()) - before == {"core_timelinedecision"}
    with database.cursor() as cursor:
        assert database.introspection.get_table_description(cursor, "core_logicalturn") == previous_columns
        indexes = database.introspection.get_constraints(cursor, "core_timelinedecision")
    assert indexes["idx_decision_ready"]["columns"] == ["status", "not_before"]
    assert indexes["idx_decision_expiry"]["columns"] == ["status", "expires_at"]
    assert indexes["idx_decision_session_turn"]["columns"] == ["session_id", "turn_id"]


def test_session_deletion_cascades_decisions(decision_fixture):
    session, now, request = decision_fixture
    admit(request, now)
    session.delete()
    assert not TimelineDecision.objects.exists()
