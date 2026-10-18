from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services import timeline_backfill, timeline_signal
from twicc.core.services.session_timeline import _conflict_clause, insert_event_rows, record_event_sync
from twicc.core.services.timeline_backfill import write_creation_row_sync
from tests.timeline_helpers import kinds, log_records_of, mark_known

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-backfill", directory="/tmp/tl-backfill")


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def write(sid="s1", **kw):
    return write_creation_row_sync(sid, **kw)


def test_creation_row_is_derived_from_created_at(project):
    created = timezone.now() - timedelta(days=10)
    make(project, created_at=created)
    assert write() == 1
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "session.created" and row.occurred_at == created
    assert row.precision == "derived" and row.actor == "system" and row.project_id == project.id
    assert row.dedupe_key == "backfill:session.created"
    assert row.data == {"origin": "sync", "provider": "codex", "settings": None, "spawned_by": None}
    assert (row.via, row.actor_ref, row.actor_name, row.provider) == (None, None, None, "codex")


def test_creation_row_carries_the_spawner_when_the_column_is_set(project):
    make(project, "boss", created_at=timezone.now())
    make(project, "kid", created_at=timezone.now(), spawned_by_id="boss", selected_model="opus")
    assert write("kid") == 1
    data = TimelineEvent.objects.get(session_id="kid", kind="session.created").data
    assert data["spawned_by"] == "boss" and data["settings"] is None


def test_no_flag_row_is_written_even_when_the_session_is_archived_hidden_pinned_muted(project):
    # A flag set before TwiCC observed it has no known date: the timeline records no invented row.
    now = timezone.now()
    make(
        project, created_at=now - timedelta(days=5), last_updated_at=now, archived=True, hidden=True,
        mute_on_user_turn=True, pinned="workspace",
    )
    assert write() == 1
    assert kinds("s1") == ["session.created"]
    assert not TimelineEvent.objects.filter(kind__endswith=".started").exists()


def test_no_process_rows_are_ever_written(project):
    now = timezone.now()
    make(project, created_at=now, last_started_at=now, last_stopped_at=now)
    assert write() == 1
    assert kinds("s1") == ["session.created"]


def test_a_known_session_gets_nothing(project):
    make(project, created_at=timezone.now())
    assert write() == 1
    assert write() == 0
    assert kinds("s1") == ["session.created"]


def test_a_watcher_row_makes_the_session_known(project):
    make(project, created_at=timezone.now())
    mark_known("s1", project.id)
    assert write() == 0
    assert kinds("s1") == ["session.created"]


def test_a_live_flag_row_without_session_created_does_not_make_the_session_known(project):
    make(project, created_at=timezone.now(), archived=True, hidden=True, last_updated_at=timezone.now())
    record_event_sync("archived.ended", session_id="s1", actor="user")
    assert write() == 1
    assert kinds("s1") == ["archived.ended", "session.created"]


def test_a_subagent_a_missing_session_or_an_empty_created_at_gets_nothing(project):
    make(project, "boss", created_at=timezone.now())
    make(project, "kid", created_at=timezone.now(), type=SessionType.SUBAGENT, parent_session_id="boss")
    make(project, "blank")
    assert write("kid") == 0 and write("blank") == 0 and write("nope") == 0
    assert TimelineEvent.objects.count() == 0


def test_require_computed_skips_a_session_with_an_empty_compute_version(project):
    make(project, "fresh", created_at=timezone.now())
    make(project, "done", created_at=timezone.now(), compute_version=3)
    assert write("fresh", require_computed=True) == 0
    assert write("done", require_computed=True) == 1


def test_a_failure_is_logged_never_raised_and_leaves_no_partial_row(project, monkeypatch):
    make(project, created_at=timezone.now(), archived=True)
    real = timeline_backfill.insert_event_rows

    def insert_then_fail(rows):
        real(rows[:1])
        raise RuntimeError("disk full")

    monkeypatch.setattr(timeline_backfill, "insert_event_rows", insert_then_fail)
    pokes = []
    monkeypatch.setattr(timeline_signal, "poke", lambda: pokes.append(1))
    with log_records_of(timeline_backfill.logger) as records, transaction.atomic():  # the compute's transaction
        assert write() == 0
        Session.objects.filter(id="s1").update(title="still writable")  # the outer transaction is usable
    assert TimelineEvent.objects.count() == 0
    assert Session.objects.get(id="s1").title == "still writable"
    assert pokes == []  # the outer transaction committed, but nothing was written: no signal
    failures = [r for r in records if r.exc_info and "disk full" in str(r.exc_info[1])]
    assert failures and all("s1" in r.getMessage() for r in failures)


def test_the_statements_do_not_depend_on_the_timeline_size(project):
    now = timezone.now()
    make(project, "empty", created_at=now, archived=True)
    make(project, "busy", created_at=now, archived=True)
    TimelineEvent.objects.bulk_create([
        TimelineEvent(session_id="busy", kind="turn.started", occurred_at=now, actor="agent") for _ in range(300)
    ])
    with CaptureQueriesContext(connection) as empty:
        write("empty")
    with CaptureQueriesContext(connection) as busy:
        write("busy")
    assert len(busy) == len(empty) <= 12


_TRANSACTION_STATEMENTS = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK", "BEGIN", "COMMIT")


def _statements(captured):
    """The captured SQL without the transaction-control statements of the writer's savepoint."""
    return [q["sql"] for q in captured.captured_queries if not q["sql"].lstrip().upper().startswith(
        _TRANSACTION_STATEMENTS,
    )]


def _session_reads(statements):
    return [sql for sql in statements if '"core_session"' in sql]


def test_a_known_session_costs_one_timeline_query_and_no_session_read(project):
    make(project, created_at=timezone.now(), archived=True)
    mark_known("s1", project.id)
    with CaptureQueriesContext(connection) as captured:
        assert write() == 0
    statements = _statements(captured)
    assert len(statements) == 1, statements
    assert _session_reads(statements) == []


def test_an_unknown_session_is_read_once_with_only_the_columns_the_rows_use(project):
    make(project, created_at=timezone.now(), archived=True, hidden=True, mute_on_user_turn=True, pinned="all")
    with CaptureQueriesContext(connection) as captured:
        assert write() == 1
    reads = _session_reads(_statements(captured))
    assert len(reads) == 1, reads
    for heavy in ("inline_artifacts", "plan_paths", "goals", "tasks", "layout", "annotations"):
        assert f'"{heavy}"' not in reads[0], heavy


def _parity_rows(session_id, project, when=None):
    """Rows covering every kind of value: datetime, nested dict, empty reason, null cause, project."""
    when = when or timezone.now() - timedelta(days=3)
    return [
        TimelineEvent(
            session_id=session_id, project_id=project.id, kind="session.created", occurred_at=when,
            precision="derived", actor="system", dedupe_key="backfill:session.created",
            data={"origin": "sync", "settings": None, "nested": {"list": [1, "é", None], "flag": True}},
        ),
        TimelineEvent(
            session_id=session_id, project_id=project.id, kind="pinned.started", occurred_at=when,
            precision="estimated", actor="system", dedupe_key="backfill:pinned.started", data={"scope": "all"},
        ),
    ]


def test_raw_insert_stores_exactly_what_the_orm_stores(project):
    before = timezone.now()
    when = timezone.now() - timedelta(days=3)
    insert_event_rows(_parity_rows("raw", project, when))
    TimelineEvent.objects.bulk_create(_parity_rows("orm", project, when))
    fields = ("occurred_at", "precision", "actor", "reason", "caused_by_id", "data", "dedupe_key", "project_id", "kind")
    for kind in ("session.created", "pinned.started"):
        raw = TimelineEvent.objects.get(session_id="raw", kind=kind)
        orm = TimelineEvent.objects.get(session_id="orm", kind=kind)
        for name in fields:
            assert getattr(raw, name) == getattr(orm, name), name
        assert raw.reason == "" and raw.caused_by_id is None
        assert raw.recorded_at is not None
        assert before - timedelta(seconds=1) <= raw.recorded_at <= timezone.now() + timedelta(seconds=5)


def test_raw_insert_sql_is_built_from_the_model_metadata(project):
    columns = [f.column for f in TimelineEvent._meta.concrete_fields if not f.primary_key]
    with CaptureQueriesContext(connection) as captured:
        insert_event_rows(_parity_rows("meta", project))
    sql = next(q["sql"] for q in captured.captured_queries if "INSERT" in q["sql"])
    assert TimelineEvent._meta.db_table in sql
    for column in columns:
        assert f'"{column}"' in sql
    assert '"id"' not in sql


def _raw(session_id, project, key, kind="turn.started"):
    return TimelineEvent(
        session_id=session_id, project_id=project.id, kind=kind, occurred_at=timezone.now(), actor="agent",
        dedupe_key=key,
    )


def test_the_conflict_clause_matches_the_real_partial_unique_index(project):
    index_sql = " ".join(connection.cursor().execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'uniq_timeline_dedupe'"
    ).fetchone()[0].split())
    columns_part, where_part = index_sql.rsplit(" WHERE ", 1)
    index_columns = columns_part[columns_part.index("(") + 1:columns_part.rindex(")")]
    clause = _conflict_clause()
    assert f"ON CONFLICT ({index_columns}) " in clause, (
        f"the clause columns differ from the index columns {index_columns!r}: {clause!r}"
    )
    assert f"WHERE {where_part} DO NOTHING" in clause, (
        f"the clause condition differs from the index condition {where_part!r}: {clause!r}"
    )
    with CaptureQueriesContext(connection) as captured:
        insert_event_rows(_parity_rows("meta2", project))
    sql = next(q["sql"] for q in captured.captured_queries if "INSERT" in q["sql"])
    assert sql.endswith(clause)


def test_raw_insert_skips_duplicate_keys_and_counts_only_inserted_rows(project):
    insert_event_rows([_raw("d", project, "k1")])
    rows = [_raw("d", project, "k1"), _raw("d", project, "k2"), _raw("d", project, "k2"), _raw("e", project, "k1")]
    assert insert_event_rows(rows) == 2
    assert sorted(TimelineEvent.objects.values_list("session_id", "dedupe_key")) == [
        ("d", "k1"), ("d", "k2"), ("e", "k1"),
    ]
    assert insert_event_rows([_raw("d", project, "k1")]) == 0
    assert insert_event_rows([]) == 0


def test_raw_insert_keeps_every_row_with_a_null_key(project):
    """Documents that NULL keys are never deduplicated (true with or without ``ON CONFLICT``)."""
    assert insert_event_rows([_raw("n", project, None) for _ in range(3)]) == 3
    assert TimelineEvent.objects.filter(session_id="n", dedupe_key__isnull=True).count() == 3


def test_raw_insert_does_not_deduplicate_a_keyed_row_without_a_session(project):
    # KNOWN LIMIT: SQLite treats NULLs as distinct in a unique index, so (NULL, key) rows are never
    # skipped by the batch insert. No caller does this; change this test only deliberately.
    rows = [_raw(None, project, "k"), _raw(None, project, "k")]
    assert insert_event_rows(rows) == 2
    assert TimelineEvent.objects.filter(session_id__isnull=True, dedupe_key="k").count() == 2


def test_raw_insert_still_raises_on_another_constraint_violation(project):
    bad = _raw("x", project, "k")
    bad.kind = None  # NOT NULL violation: not covered by the ON CONFLICT clause
    with pytest.raises(IntegrityError), transaction.atomic():
        insert_event_rows([_raw("x", project, "ok"), bad])
    assert TimelineEvent.objects.filter(session_id="x").count() == 0


def test_a_rerun_after_deleting_the_creation_row_rewrites_that_row(project):
    make(project, created_at=timezone.now(), archived=True)
    assert write() == 1
    TimelineEvent.objects.filter(session_id="s1", kind="session.created").delete()  # let the session be re-processed
    assert write() == 1
    assert kinds("s1") == ["session.created"]
    assert write() == 0
