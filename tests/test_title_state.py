"""Stored title state and pending-title wire metadata."""

import pytest
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

from twicc.core.models import Project, Provider, Session
from twicc.core.serializers import serialize_session, slim_session
from twicc.pending_titles import pop_pending_title, set_pending_title


TITLE_MIGRATION = ("core", "0150_session_automatic_titles")
PREDECESSOR = ("core", "0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index")


@pytest.fixture
def pending_session_id():
    session_id = "title-pending"
    pop_pending_title(session_id)
    yield session_id
    pop_pending_title(session_id)


def test_new_title_state_defaults(db):
    project = Project.objects.create(id="title-state-project")
    session = Session.objects.create(
        id="title-defaults", project=project, provider=Provider.CODEX,
        file_path="title-defaults.jsonl",
    )
    session.refresh_from_db()
    assert (session.title_origin, session.title_check_count, session.title_checked_at) == ("", None, None)


def test_serializer_keeps_database_origin_when_pending(db, pending_session_id):
    project = Project.objects.create(id="title-pending-project")
    session = Session.objects.create(
        id=pending_session_id, project=project, provider=Provider.CODEX,
        file_path="title-pending.jsonl", title="Automatic", title_origin="auto",
    )
    set_pending_title(session.id, "Chosen")
    payload = serialize_session(session)
    assert payload["title"] == "Chosen"
    assert payload["title_origin"] == "auto"
    assert payload["has_pending_title"] is True
    assert "title_check_count" not in payload
    assert "title_checked_at" not in payload
    assert slim_session(payload)["title_origin"] == "auto"
    assert "has_pending_title" not in slim_session(payload)


def test_serializer_reports_no_pending_title(db):
    project = Project.objects.create(id="title-stored-project")
    session = Session.objects.create(
        id="title-stored", project=project, provider=Provider.CODEX,
        file_path="title-stored.jsonl", title="Stored",
    )
    payload = serialize_session(session)
    assert payload["title"] == "Stored"
    assert payload["title_origin"] == ""
    assert payload["has_pending_title"] is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("original_history", [False, True], ids=["squash", "original-0148-0149"])
def test_migration_preserves_legacy_titles_with_frozen_origin(original_history):
    executor = MigrationExecutor(connection)
    assert TITLE_MIGRATION in executor.loader.graph.nodes
    migration = executor.loader.get_migration(*TITLE_MIGRATION)
    assert len(migration.operations) == 3
    assert all(isinstance(operation, migrations.AddField) for operation in migration.operations)
    latest = executor.loader.graph.leaf_nodes("core")
    try:
        executor.migrate([PREDECESSOR])
        old_apps = executor.loader.project_state([PREDECESSOR]).apps
        old_project = old_apps.get_model("core", "Project").objects.create(id="title-legacy-project")
        old_apps.get_model("core", "Session").objects.create(
            id="title-legacy", project=old_project, provider="codex",
            file_path="title-legacy.jsonl", title="Legacy title",
        )
        if original_history:
            # An installation from before the squash has both original records only.
            recorder = MigrationRecorder(connection)
            recorder.record_unapplied(*PREDECESSOR)
            records = recorder.applied_migrations()
            assert ("core", "0148_live_contribution_indexes") in records
            assert ("core", "0149_remove_redundant_message_index") in records
            assert PREDECESSOR not in records
        executor = MigrationExecutor(connection)
        executor.migrate([TITLE_MIGRATION])
        new_apps = executor.loader.project_state([TITLE_MIGRATION]).apps
        session = new_apps.get_model("core", "Session").objects.get(pk="title-legacy")
        assert session.title == "Legacy title"
        assert (session.title_origin, session.title_check_count, session.title_checked_at) == ("", None, None)
    finally:
        MigrationExecutor(connection).migrate(latest)
