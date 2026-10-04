"""Durable subscription schema and session history generation contracts."""

from datetime import timedelta

import orjson
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from twicc.core.models import McpConnection, McpEventSubscription, McpOAuthClient, Project, Session
from twicc.mcp.oauth.storage import snapshot
from twicc.paths import get_data_dir

pytestmark = pytest.mark.django_db


@pytest.fixture
def subscription():
    client = McpOAuthClient.objects.create(id="events-client")
    principal = McpConnection.objects.create(id="events-connection", client=client, resource="https://mcp.example/mcp")
    return McpEventSubscription.objects.create(
        id="sub_storage", connection=principal, name="session.concluded",
        arguments={"session_id": "not-indexed", "since_line_num": 0}, session_id="not-indexed",
        callback_url="https://callback.example/events", secret="current-private-secret",
        previous_secret="previous-private-secret", cursor_line=0, cursor_at=1791097200.1234567,
        initial_last_line=0, turn_open=True, turn_started_at=1791097200.7654321,
        turn_opened_by="transition", turn_start_line=0, numbering=None,
        refresh_before=timezone.now() + timedelta(days=1),
    )


def test_subscription_survives_missing_session_and_connection_delete_cascades(subscription):
    assert not Session.objects.filter(pk=subscription.session_id).exists()
    subscription.refresh_from_db()
    assert subscription.numbering is None
    project = Project.objects.create(id="events-project", directory="/tmp/events")
    session = Session.objects.create(id=subscription.session_id, project=project)
    session.delete()
    assert McpEventSubscription.objects.filter(pk=subscription.pk).exists()
    subscription.connection.delete()
    assert not McpEventSubscription.objects.filter(pk=subscription.pk).exists()


def test_transition_floats_and_instance_binding_survive_reload(subscription):
    subscription.refresh_from_db()
    assert subscription.cursor_at == 1791097200.1234567
    assert subscription.turn_started_at == 1791097200.7654321
    assert subscription.data_dir == str(get_data_dir().resolve())
    assert subscription.turn_started_at != timezone.datetime.fromtimestamp(
        subscription.turn_started_at, timezone.get_current_timezone()
    ).timestamp()


def test_refresh_preserves_generation_and_replacement_renews_it(subscription):
    generation = subscription.created_at
    updated = subscription.updated_at
    subscription.refresh_before += timedelta(days=1)
    subscription.save()
    subscription.refresh_from_db()
    assert subscription.created_at == generation
    assert subscription.updated_at > updated
    fields = {field.name: getattr(subscription, field.name) for field in subscription._meta.fields
              if field.name not in {"created_at", "updated_at"}}
    subscription.delete()
    replacement = McpEventSubscription.objects.create(**fields)
    assert replacement.id == fields["id"]
    assert replacement.created_at > generation


def test_owner_snapshot_does_not_expose_subscription_secrets(subscription):
    result = orjson.dumps(snapshot())
    assert subscription.secret.encode() not in result
    assert subscription.previous_secret.encode() not in result


@pytest.mark.django_db(transaction=True)
def test_migration_sets_existing_session_epoch_zero():
    executor = MigrationExecutor(connection)
    target = [("core", "0150_mcp_event_subscriptions")]
    previous = executor.loader.graph.node_map[target[0]].parents
    source = [node.key for node in previous]
    try:
        executor.migrate(source)
        old_session = executor.loader.project_state(source).apps.get_model("core", "Session")
        old_project = executor.loader.project_state(source).apps.get_model("core", "Project")
        project = old_project.objects.create(id="pre-events-project", directory="/tmp/events")
        old_session.objects.create(id="pre-events-session", project_id=project.pk)
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        migrated_session = executor.loader.project_state(target).apps.get_model("core", "Session")
        assert migrated_session.objects.get(pk="pre-events-session").history_epoch == 0
        assert executor.loader.detect_conflicts() == {}
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes("core"))
