"""Migration 0153: stored peer rows move to the attachment entries shape (design §4.8.5)."""

import base64
import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

MIGRATE_FROM = [("core", "0152_async_question_state")]
MIGRATE_TO = [("core", "0153_peer_message_attachments")]
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()


@pytest.fixture
def restore_latest_migration():
    """Bring the schema back to the current leaf, whatever it is."""
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes("core"))


def _models(target):
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    apps = executor.loader.project_state(target).apps
    return apps, apps.get_model("core", "Peer"), apps.get_model("core", "PeerMessage")


def _make(Peer, PeerMessage, message_id, payload, meta=(), **extra):
    peer, _ = Peer.objects.get_or_create(
        id="peer_old", defaults={"name": "Old", "base_url": "https://old.example.com", "state": "active"},
    )
    return PeerMessage.objects.create(
        peer=peer, direction="in", message_id=message_id, thread_id=message_id, title="T",
        payload=payload, attachments_meta=list(meta), status="pending", **extra,
    ).pk


@pytest.mark.django_db(transaction=True)
def test_old_rows_are_converted(restore_latest_migration):
    _apps, Peer, PeerMessage = _models(MIGRATE_FROM)
    files = _make(Peer, PeerMessage, "files", {"text": "files", "images": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
    ], "documents": [
        {"type": "document", "title": "spec.pdf",
         "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="}},
        {"type": "document", "title": "a/b\nc.md",
         "source": {"type": "text", "media_type": "text/plain", "data": "hé"}},
        {"type": "document", "source": {"type": "base64", "media_type": "not a type", "data": "AAAA"}},
    ]}, meta=[{"kind": "image", "media_type": "image/png", "bytes": 8}])
    text_only = _make(Peer, PeerMessage, "text", {"text": "hello", "images": [], "documents": []})
    purged = _make(Peer, PeerMessage, "purged", {"text": "kept", "images": [], "documents": []}, meta=[
        {"kind": "image", "media_type": "image/png", "bytes": 9},
        {"kind": "document", "media_type": "text/plain; charset=utf-8", "bytes": 4, "name": "x/y.txt"},
    ])

    _apps, _Peer, PeerMessage = _models(MIGRATE_TO)
    row = PeerMessage.objects.get(pk=files)
    assert row.payload == {"text": "files", "attachments": [
        {"name": "attachment-1.png", "media_type": "image/png", "data": PNG},
        {"name": "spec.pdf", "media_type": "application/pdf", "data": "JVBERi0="},
        {"name": "a_b_c.md", "media_type": "text/plain", "data": base64.b64encode("hé".encode()).decode()},
        {"name": "attachment-4.bin", "media_type": "application/octet-stream", "data": "AAAA"},
    ]}
    assert row.attachments_meta == [
        {"name": "attachment-1.png", "media_type": "image/png", "bytes": 8},
        {"name": "spec.pdf", "media_type": "application/pdf", "bytes": 5},
        {"name": "a_b_c.md", "media_type": "text/plain", "bytes": 3},
        {"name": "attachment-4.bin", "media_type": "application/octet-stream", "bytes": 3},
    ]
    row = PeerMessage.objects.get(pk=text_only)
    assert (row.payload, row.attachments_meta) == ({"text": "hello"}, [])
    row = PeerMessage.objects.get(pk=purged)
    assert row.payload == {"text": "kept"}
    assert row.attachments_meta == [
        {"name": "attachment-1.png", "media_type": "image/png", "bytes": 9},
        {"name": "x_y.txt", "media_type": "application/octet-stream", "bytes": 4},
    ]


@pytest.mark.django_db(transaction=True)
def test_rows_are_loaded_one_at_a_time(restore_latest_migration):
    apps, Peer, PeerMessage = _models(MIGRATE_TO)
    for index in range(3):
        _make(Peer, PeerMessage, f"m{index}", {"text": "x", "images": [], "documents": []})
    module = importlib.import_module("twicc.core.migrations.0153_peer_message_attachments")
    with CaptureQueriesContext(connection) as queries:
        module.convert_peer_messages(apps, None)
    loads = [q["sql"] for q in queries if q["sql"].startswith("SELECT") and '"payload"' in q["sql"].split("FROM")[0]]
    assert len(loads) == 3
    assert all("LIMIT 1" in sql for sql in loads)
    assert all(row.payload == {"text": "x"} for row in PeerMessage.objects.all())


@pytest.mark.django_db(transaction=True)
def test_the_reverse_is_a_noop(restore_latest_migration):
    _apps, Peer, PeerMessage = _models(MIGRATE_TO)
    pk = _make(Peer, PeerMessage, "new", {"text": "x"})
    _apps, _Peer, PeerMessage = _models(MIGRATE_FROM)
    assert PeerMessage.objects.get(pk=pk).payload == {"text": "x"}
