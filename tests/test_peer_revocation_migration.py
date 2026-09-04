"""Migration coverage for retained Peer history and local-origin binding."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


MIGRATE_FROM = [("core", "0134_peermessage_threading")]
MIGRATE_TO = [("core", "0135_peer_revocation_reconnection")]


@pytest.fixture
def restore_latest_migration():
    """Bring the schema back to the CURRENT leaf, whatever it is: a hardcoded
    target goes stale with the next migration and leaves every later test on
    an old schema."""
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes("core"))


@pytest.mark.django_db(transaction=True)
def test_existing_active_peer_becomes_broken_without_losing_history(restore_latest_migration):
    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_FROM)
    old_apps = executor.loader.project_state(MIGRATE_FROM).apps
    Peer = old_apps.get_model("core", "Peer")
    PeerMessage = old_apps.get_model("core", "PeerMessage")

    peer = Peer.objects.create(
        id="peer_old",
        name="Old peer",
        base_url="https://old.example.com",
        state="active",
        token_ours="ours",
        token_theirs="theirs",
        verification_code="123456",
    )
    message = PeerMessage.objects.create(
        peer=peer,
        direction="out",
        message_id="old-message",
        thread_id="old-message",
        title="Historical",
        payload={"text": "keep", "images": [], "documents": []},
        status="pending",
    )

    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_TO)
    new_apps = executor.loader.project_state(MIGRATE_TO).apps
    Peer = new_apps.get_model("core", "Peer")
    PeerMessage = new_apps.get_model("core", "PeerMessage")

    migrated = Peer.objects.get(pk=peer.pk)
    assert migrated.state == "broken"
    assert migrated.broken_reason == "local_address_changed"
    assert migrated.token_ours is None
    assert migrated.token_theirs is None
    assert migrated.verification_code == ""
    assert PeerMessage.objects.filter(pk=message.pk, peer_id=migrated.pk).exists()
