# tests/test_timeline_provider.py
"""The ``provider`` column of ``TimelineEvent`` (display design 2026-10-15, section 8)."""

import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.core.management import call_command
from django.db import connection, migrations
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.agent.timeline_hooks import (
    ProcessSnapshot, record_process_changes, record_process_ended, record_process_started,
)
from twicc.core.enums import Provider
from twicc.core.models import (
    ProcessRun, Project, Session, TimelineActor, TimelineEvent, TimelineKind,
)
from twicc.core.services import session_timeline
from twicc.core.services.session_timeline import (
    PROVIDER_MAX_LENGTH, EventSpec, record_event_sync, record_events_bulk_sync,
    record_session_created, record_title_change, resolve_provider,
)
from twicc.core.services.session_update import (
    apply_session_archived_change, apply_session_mute_on_user_turn_change, apply_session_pinned_change,
    archive_sessions_bulk, record_settings_change,
)
from twicc.core.services.timeline_backfill import write_creation_row_sync
from twicc.core.services.timeline_boot import reconcile_at_boot
from twicc.request_origin import UI_ORIGIN
from tests.timeline_helpers import mark_known, run_with_direct_writes

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-provider", directory="/tmp/tl-provider")


def make_session(project, sid="s1", provider="codex", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider=provider, **kw)


def select_session_queries(captured):
    """The SELECTs that read the ``core_session`` table, from a ``CaptureQueriesContext``."""
    return [
        q["sql"] for q in captured.captured_queries
        if q["sql"].lstrip().upper().startswith("SELECT") and '"core_session"' in q["sql"]
    ]


@pytest.fixture
def explicit_hints(monkeypatch):
    """Record the ``explicit`` hint that every writer hands to ``resolve_provider``."""
    seen = []
    original = session_timeline.resolve_provider

    def spy(session_id, *, explicit=None, data=None):
        seen.append((session_id, explicit))
        return original(session_id, explicit=explicit, data=data)

    monkeypatch.setattr(session_timeline, "resolve_provider", spy)
    return seen


def rows_without_provider():
    """The guard: rows that name a session but have no provider."""
    return list(TimelineEvent.objects.filter(session_id__isnull=False, provider__isnull=True))


# -- Model, indexes, migration ----------------------------------------------------------------------------


def test_the_column_is_nullable_and_as_wide_as_the_constant():
    field = TimelineEvent._meta.get_field("provider")
    assert field.null and field.blank and field.max_length == PROVIDER_MAX_LENGTH == 50


def test_the_two_display_indexes_exist_in_the_model_and_in_the_database():
    declared = {index.name: list(index.fields) for index in TimelineEvent._meta.indexes}
    assert declared["idx_timeline_occurred"] == ["occurred_at", "id"]
    assert declared["idx_timeline_provider"] == ["provider", "occurred_at", "id"]
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, TimelineEvent._meta.db_table)
    assert constraints["idx_timeline_occurred"]["columns"] == ["occurred_at", "id"]
    assert constraints["idx_timeline_provider"]["columns"] == ["provider", "occurred_at", "id"]
    # The earlier indexes stay.
    for name in ("idx_timeline_session", "idx_timeline_project", "idx_timeline_kind", "idx_timeline_session_kind"):
        assert name in constraints


def test_migration_0163_creates_the_provider_column_and_the_two_display_indexes():
    module = importlib.import_module("twicc.core.migrations.0163_timeline_event")
    migration = module.Migration
    assert [type(op) for op in migration.operations] == [migrations.CreateModel]
    create = migration.operations[0]
    assert create.name == "TimelineEvent"
    field = dict(create.fields)["provider"]
    # Nullable and without a default: a row written without a provider reads NULL.
    assert field.null is True and not field.has_default() and field.max_length == 50
    index_names = [index.name for index in create.options["indexes"]]
    assert "idx_timeline_occurred" in index_names
    assert "idx_timeline_provider" in index_names


def test_the_models_and_the_migrations_agree():
    # ``--check`` exits with status 1 when the models differ from the migrations.
    call_command("makemigrations", "core", check=True, dry_run=True, verbosity=0)


# Existing rows: the migration is a nullable AddField without a default (checked above), so rows written before
# it read NULL; here only the model default of a new row is checked.
def test_the_column_defaults_to_null(project):
    row = TimelineEvent.objects.create(
        session_id="old", kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
    )
    row.refresh_from_db()
    assert row.provider is None


# -- Central resolution -----------------------------------------------------------------------------------


def test_an_explicit_value_wins_over_the_payload_the_cache_and_the_row(project):
    make_session(project, provider="claude_code")
    session_timeline._remember_provider("s1", "claude_code")
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider("s1", explicit="codex", data={"provider": "claude_code"}) == "codex"
    assert select_session_queries(captured) == []


def test_the_payload_wins_over_the_cache_and_the_row(project):
    make_session(project, provider="claude_code")
    session_timeline._remember_provider("s1", "claude_code")
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider("s1", data={"provider": "codex"}) == "codex"
    assert select_session_queries(captured) == []


def test_the_cache_wins_over_the_row(project):
    make_session(project, provider="claude_code")
    session_timeline._remember_provider("s1", "codex")
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider("s1") == "codex"
    assert select_session_queries(captured) == []


def test_the_row_is_read_once_then_cached(project):
    make_session(project, provider="codex")
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider("s1") == "codex"
        assert resolve_provider("s1") == "codex"
    assert len(select_session_queries(captured)) == 1


def test_a_provider_enum_member_is_stored_by_value(project):
    assert resolve_provider("s1", explicit=Provider.CODEX) == "codex"


def test_a_hint_that_is_not_a_string_is_ignored(project):
    make_session(project, provider="codex")
    assert resolve_provider("s1", explicit=7, data={"provider": ""}) == "codex"


def test_an_instance_event_has_no_provider_and_no_query():
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider(None, explicit="codex", data={"provider": "codex"}) is None
    assert len(captured) == 0


def test_a_session_with_no_row_and_no_hint_gets_null_and_is_not_cached(project):
    assert resolve_provider("ghost") is None
    make_session(project, sid="ghost", provider="codex")
    assert resolve_provider("ghost") == "codex"


def test_the_cache_is_bounded_and_drops_the_least_recently_used(monkeypatch):
    monkeypatch.setattr(session_timeline, "_PROVIDER_CACHE_MAX", 2)
    for sid in ("a", "b"):
        session_timeline._remember_provider(sid, "codex")
    session_timeline._remember_provider("c", "codex")
    assert list(session_timeline._provider_cache) == ["b", "c"]
    assert resolve_provider("b") == "codex"
    session_timeline._remember_provider("d", "codex")
    assert list(session_timeline._provider_cache) == ["b", "d"]


def test_a_failing_lookup_gives_null_and_never_raises(project, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(session_timeline.Session.objects, "filter", boom)
    assert resolve_provider("s1") is None


def test_the_event_is_written_when_the_resolution_fails(project, monkeypatch):
    monkeypatch.setattr(session_timeline.Session.objects, "filter", lambda *a, **k: 1 / 0)
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    assert event is not None and event.provider is None


# -- Every writer -----------------------------------------------------------------------------------------


def test_the_single_path_resolves_the_provider_from_the_session_row(project):
    make_session(project)
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    event.refresh_from_db()
    assert event.provider == "codex"


def test_an_instance_event_is_stored_without_provider():
    event = record_event_sync(
        TimelineKind.BACKEND_STARTED, actor=TimelineActor.SYSTEM, data={"pid": 1},
    )
    assert event.provider is None


def test_the_bulk_path_resolves_each_spec_and_honours_a_hint(project):
    make_session(project, "a", provider="codex")
    make_session(project, "b", provider="claude_code")
    written = record_events_bulk_sync([
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="a"),
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="b"),
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="ghost", provider="codex"),
        EventSpec(TimelineKind.BACKEND_STARTED, data={"pid": 1}),
    ])
    assert written == 4
    assert dict(TimelineEvent.objects.values_list("session_id", "provider")) == {
        "a": "codex", "b": "claude_code", "ghost": "codex", None: None,
    }


def test_the_batch_fallback_carries_the_provider(project, monkeypatch):
    make_session(project)

    def failing_batch(rows):
        raise RuntimeError("batch insert failed")

    monkeypatch.setattr(session_timeline, "insert_event_rows", failing_batch)
    assert record_events_bulk_sync([EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s1")]) == 1
    assert TimelineEvent.objects.get(session_id="s1").provider == "codex"
    # A session with no row: the hint of the spec is the only source. The batch remembered it in the cache;
    # evict it, as a busy process could, so the fallback row must carry the value it was built with.
    monkeypatch.setattr(
        session_timeline, "insert_event_rows", lambda rows: (session_timeline.clear_provider_cache(), failing_batch(rows)),
    )
    spec = EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="ghost", provider="claude_code")
    assert record_events_bulk_sync([spec]) == 1
    assert TimelineEvent.objects.get(session_id="ghost").provider == "claude_code"


def test_a_provider_longer_than_the_column_is_cut_not_rejected(project):
    assert resolve_provider("s1", explicit="p" * 80) == "p" * PROVIDER_MAX_LENGTH
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s2", actor=TimelineActor.AGENT, provider="q" * 80)
    assert event is not None
    event.refresh_from_db()
    assert event.provider == "q" * PROVIDER_MAX_LENGTH


def test_the_bulk_archive_takes_the_provider_from_the_update_not_from_a_query(project):
    make_session(project, "a", provider="codex")
    make_session(project, "b", provider="claude_code")
    with CaptureQueriesContext(connection) as captured:
        assert sorted(archive_sessions_bulk({"a", "b"})) == ["a", "b"]
    assert dict(TimelineEvent.objects.values_list("session_id", "provider")) == {"a": "codex", "b": "claude_code"}
    # The content storage lifecycle reads the session rows (main's ``lifecycle_change``); none reads the provider.
    assert [sql for sql in select_session_queries(captured) if '"provider"' in sql] == []


def test_the_boot_reconciliation_uses_the_process_run_provider_even_without_a_session_row(project):
    now = timezone.now()
    ProcessRun.objects.create(
        provider="codex", session_id="lost", started_at=now - timedelta(hours=3),
        state="assistant_turn", last_state_change_at=now - timedelta(hours=1),
    )
    reconcile_at_boot(now=now, boot_id="b1")
    lost = list(TimelineEvent.objects.filter(session_id="lost").order_by("id"))
    assert [row.kind for row in lost] == ["turn.ended", "process.ended"]
    assert {row.provider for row in lost} == {"codex"}
    assert TimelineEvent.objects.get(kind=TimelineKind.BACKEND_STARTED).provider is None


def test_the_process_hooks_pass_the_agent_provider(project, explicit_hints):
    agent = SimpleNamespace(
        session_id="nosession", project_id=None, provider=Provider.CODEX, ephemeral=False, process_run=None,
        pending_requests=(), kill_reason=None, error=None, is_hybrid=False,
    )
    record_process_started(agent, resume=False)
    record_process_changes(
        agent, ProcessSnapshot("user_turn", False, None), new_state="assistant_turn", new_awaiting=False, new_work=None,
    )
    record_process_ended(agent, ProcessSnapshot("assistant_turn", False, None))
    rows = TimelineEvent.objects.filter(session_id="nosession")
    assert rows.count() == 4
    assert {row.provider for row in rows} == {"codex"}
    assert {explicit for sid, explicit in explicit_hints if sid == "nosession"} == {"codex"}


def test_session_created_and_its_flag_rows_pass_the_session_provider(project, explicit_hints):
    session = Session(
        id="fresh", file_path="fresh.jsonl", project=project, provider="claude_code", hidden=True,
        mute_on_user_turn=True,
    )
    record_session_created(session, origin="twicc")
    rows = list(TimelineEvent.objects.filter(session_id="fresh").order_by("id"))
    assert [row.kind for row in rows] == ["session.created", "hidden.started", "muted.started"]
    assert {row.provider for row in rows} == {"claude_code"}
    assert {explicit for sid, explicit in explicit_hints if sid == "fresh"} == {"claude_code"}


def test_the_title_recorder_passes_the_session_provider(project, explicit_hints):
    make_session(project)
    record_title_change("s1", "New", source="user", old="Old", origin=UI_ORIGIN)
    assert TimelineEvent.objects.get(session_id="s1").provider == "codex"
    assert [explicit for sid, explicit in explicit_hints if sid == "s1"] == ["codex"]


def test_the_settings_recorder_resolves_through_the_central_rule(project):
    make_session(project)
    record_settings_change("s1", {"effort": "low"}, {"effort": "high"})
    assert TimelineEvent.objects.get(session_id="s1").provider == "codex"


@pytest.fixture
def no_agent_side_effects(monkeypatch):
    from twicc import terminal
    from twicc.agent import registry

    monkeypatch.setattr(registry, "get_agent_manager_registry",
                        lambda: SimpleNamespace(kill_agent=AsyncMock(), get_agent_info=lambda session_id: None,
                                                get_active_agents=list))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", lambda scope: None)


def test_the_flag_hooks_pass_the_session_provider(project, explicit_hints, no_agent_side_effects):
    from twicc.core.services.session_visibility import _apply_visibility_flag

    session = make_session(project)
    run_with_direct_writes(lambda: apply_session_pinned_change(session, "project"))
    run_with_direct_writes(lambda: apply_session_mute_on_user_turn_change(session, True))
    run_with_direct_writes(lambda: apply_session_archived_change(session, True, also_unpin=True))
    _apply_visibility_flag("s1", True)
    rows = TimelineEvent.objects.filter(session_id="s1")
    assert sorted(row.kind for row in rows) == sorted([
        "pinned.started", "muted.started", "archived.started", "pinned.ended", "hidden.started",
    ])
    assert {row.provider for row in rows} == {"codex"}
    assert {explicit for sid, explicit in explicit_hints if sid == "s1"} == {"codex"}


def test_the_backfill_writes_the_session_provider(project):
    make_session(project, "a", provider="codex", created_at=timezone.now(), archived=True)
    make_session(project, "b", provider="claude_code", created_at=timezone.now())
    write_creation_row_sync("a")
    write_creation_row_sync("b")
    assert dict(TimelineEvent.objects.filter(kind="session.created").values_list("session_id", "provider")) == {
        "a": "codex", "b": "claude_code",
    }
    assert {row.provider for row in TimelineEvent.objects.filter(session_id="a")} == {"codex"}


def test_guard_no_row_with_a_session_keeps_a_null_provider_after_any_writer_ran(project, no_agent_side_effects):
    """Every writer path on an existing session, then the guard (display design, section 8)."""
    from twicc.core.services.session_visibility import _apply_visibility_flag

    session = make_session(project, created_at=timezone.now(), archived=False)
    mark_known("s1", project.id)
    make_session(project, "bulk-a")
    make_session(project, "backfilled", created_at=timezone.now(), archived=True)
    record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    record_events_bulk_sync([EventSpec(TimelineKind.MUTED_STARTED, session_id="s1")])
    archive_sessions_bulk({"bulk-a"})
    record_title_change("s1", "T", source="auto")
    record_settings_change("s1", {"effort": "low"}, {"effort": "high"})
    run_with_direct_writes(lambda: apply_session_pinned_change(session, "all"))
    _apply_visibility_flag("s1", True)
    write_creation_row_sync("backfilled")
    assert TimelineEvent.objects.filter(session_id__isnull=False).count() >= 9
    assert rows_without_provider() == []


def test_every_timeline_event_constructor_in_src_passes_the_provider():
    """Source guard (pattern of ``test_timeline_origin_guard.py``): a new writer that builds a row must pass it.

    Covers ``TimelineEvent(...)`` and ``TimelineEvent.objects.create / bulk_create / get_or_create``.
    """
    import ast
    from pathlib import Path

    import twicc

    src = Path(twicc.__file__).resolve().parent
    found, missing = 0, []
    for path in src.rglob("*.py"):
        if path == src / "core" / "models.py" or "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            direct = isinstance(func, ast.Name) and func.id == "TimelineEvent"
            manager = (
                isinstance(func, ast.Attribute) and func.attr in {"create", "bulk_create", "get_or_create"}
                and isinstance(func.value, ast.Attribute) and func.value.attr == "objects"
                and isinstance(func.value.value, ast.Name) and func.value.value.id == "TimelineEvent"
            )
            if direct or manager:
                found += 1
                if "provider" not in {keyword.arg for keyword in node.keywords}:
                    missing.append(f"{path.relative_to(src).as_posix()}:{node.lineno}")
    assert found >= 3, "the guard no longer finds the constructors it guards"
    assert missing == []


def test_the_guard_itself_detects_a_null_provider():
    TimelineEvent.objects.create(
        session_id="raw", kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
    )
    TimelineEvent.objects.create(
        kind=TimelineKind.BACKEND_STARTED, occurred_at=timezone.now(), actor=TimelineActor.SYSTEM, data={"pid": 1},
    )
    assert [row.session_id for row in rows_without_provider()] == ["raw"]
