"""Fresh and main databases apply async questions after MCP event subscriptions."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

BASE = ("core", "0150_session_automatic_titles")
QUESTIONS = ("core", "0152_async_question_state")
EVENTS = ("core", "0151_mcp_event_subscriptions")


@pytest.mark.parametrize("existing", [[], [EVENTS]], ids=["fresh", "main-only"])
def test_fresh_and_main_migration_states_preserve_data_and_reach_one_leaf(existing, tmp_path, django_db_blocker):
    alias = "default"
    original = connections[alias]
    config = deepcopy(original.settings_dict)
    config["NAME"] = str(tmp_path / "matrix.sqlite3")
    # Historical RunPython migrations use the default alias. Replace only
    # its connection object; the caller's existing database stays untouched.
    database = DatabaseWrapper(config, alias)
    connections[alias] = database
    try:
        with django_db_blocker.unblock():
            executor = MigrationExecutor(database)
            assert executor.loader.graph.leaf_nodes("core") == [QUESTIONS]
            if existing:
                executor.migrate([BASE])
                executor = MigrationExecutor(database)
                executor.migrate(existing)
                apps = executor.loader.project_state(existing).apps
                Project = apps.get_model("core", "Project")
                Session = apps.get_model("core", "Session")
                project = Project.objects.using(alias).create(id="matrix-project")
                session = Session.objects.using(alias).create(id="matrix-session", project=project, provider="codex")
                if EVENTS in existing:
                    Session.objects.using(alias).filter(pk=session.pk).update(history_epoch=7)
                    client = apps.get_model("core", "McpOAuthClient").objects.using(alias).create(id="matrix-client")
                    oauth = apps.get_model("core", "McpConnection").objects.using(alias).create(
                        id="matrix-connection", client=client, resource="https://mcp.example/mcp",
                    )
                    apps.get_model("core", "McpEventSubscription").objects.using(alias).create(
                        id="matrix-subscription", connection=oauth, name="session.concluded",
                        arguments={"session_id": session.pk}, session_id=session.pk,
                        callback_url="https://callback.example/events", secret="preserved-secret",
                        cursor_line=3, cursor_at=1000, initial_last_line=3, numbering=0,
                        turn_open=False, turn_started_at=None, turn_start_line=3,
                        refresh_before=datetime.now(UTC),
                    )
            executor = MigrationExecutor(database)
            remaining = [migration.name for migration, backwards in executor.migration_plan([QUESTIONS])
                         if migration.app_label == "core" and migration.name.startswith("015")]
            expected = ([BASE[1]] if not existing else []) + [
                target[1] for target in (QUESTIONS, EVENTS) if target not in existing
            ]
            assert sorted(remaining) == sorted(expected)
            executor.migrate([QUESTIONS])
            applied = MigrationRecorder(database).applied_migrations()
            assert {QUESTIONS, EVENTS}.issubset(applied)
            tables = database.introspection.table_names()
            assert "core_asyncquestionstate" in tables
            assert "core_mcpeventsubscription" in tables
            apps = executor.loader.project_state([QUESTIONS]).apps
            Session = apps.get_model("core", "Session")
            if existing:
                assert Session.objects.using(alias).get(pk="matrix-session").history_epoch == (7 if EVENTS in existing else 0)
            if EVENTS in existing:
                subscription = apps.get_model("core", "McpEventSubscription").objects.using(alias).get(pk="matrix-subscription")
                assert (subscription.secret, subscription.cursor_line) == ("preserved-secret", 3)
            assert apps.get_model("core", "AsyncQuestionState").objects.using(alias).count() == 0
    finally:
        database.close()
        connections[alias] = original
