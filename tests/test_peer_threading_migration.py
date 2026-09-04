"""Migration coverage for peer-message threading."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


MIGRATE_FROM = [("core", "0133_share_created_by_session")]
MIGRATE_TO = [("core", "0134_peermessage_threading")]


@pytest.fixture
def restore_latest_migration():
    """Bring the schema back to the CURRENT leaf, whatever it is: a hardcoded
    target goes stale with the next migration and leaves every later test on
    an old schema."""
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes("core"))


@pytest.mark.django_db(transaction=True)
def test_peer_threading_backfill_makes_every_historical_row_a_root(restore_latest_migration):
    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_FROM)
    old_apps = executor.loader.project_state(MIGRATE_FROM).apps
    Peer = old_apps.get_model("core", "Peer")
    PeerMessage = old_apps.get_model("core", "PeerMessage")

    peer = Peer.objects.create(
        id="peer_old", name="Old peer", base_url="https://old.example.com", state="active",
    )
    for direction, message_id in (
        ("in", "root-in"),
        ("out", "root-out"),
        ("in", "collision"),
        ("out", "collision"),
    ):
        PeerMessage.objects.create(
            peer=peer,
            direction=direction,
            message_id=message_id,
            title="Historical",
            payload={"text": "old", "images": [], "documents": []},
            status="pending",
        )

    executor = MigrationExecutor(connection)
    executor.migrate(MIGRATE_TO)
    new_apps = executor.loader.project_state(MIGRATE_TO).apps
    PeerMessage = new_apps.get_model("core", "PeerMessage")
    rows = list(PeerMessage.objects.order_by("direction", "message_id", "pk"))

    assert len(rows) == 4
    for row in rows:
        assert row.thread_id == row.message_id
        assert row.reply_to == ""
        assert row.reply_to_message_id is None

    distinct = [row for row in rows if row.message_id != "collision"]
    assert len({(row.peer_id, row.thread_id) for row in distinct}) == 2
    collision = [row for row in rows if row.message_id == "collision"]
    assert len(collision) == 2
    assert len({(row.peer_id, row.thread_id) for row in collision}) == 1
