"""Storage schema contracts preserve legacy tables and exact logical bytes."""

import hashlib

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction

from twicc.core import models as core


def test_item_content_fks_are_nullable_protected_and_unindexed():
    for name in ("current_content", "original_content"):
        field = core.SessionItem._meta.get_field(name)
        assert field.null is True
        assert field.db_index is False
        assert field.remote_field.on_delete is models.PROTECT


def test_session_state_additions_have_no_db_default_or_index():
    for name in ("content_storage_version", "content_state_epoch", "plaintext_verified_epoch", "storage_incarnation"):
        field = core.Session._meta.get_field(name)
        assert field.null is True
        assert field.db_index is False
        assert field.has_db_default() is False
        assert field.has_default() is False


def test_payload_is_single_binary_field():
    content = getattr(core, "SessionItemContent", None)
    assert content is not None
    assert [f.name for f in content._meta.fields if isinstance(f, models.BinaryField)] == ["sha256", "payload"]


@pytest.mark.django_db
def test_logical_hash_is_unique_binary_32_bytes():
    content = getattr(core, "SessionItemContent", None)
    assert content is not None
    first = '{"value": "é"}'
    other = '{"value":"é"}'
    def insert(text):
        raw = text.encode("utf-8")
        return content.objects.create(sha256=hashlib.sha256(raw).digest(), payload=raw, raw_size=len(raw))
    row = insert(first)
    assert len(row.sha256) == 32
    assert row.sha256 != insert(other).sha256
    with transaction.atomic(), pytest.raises(IntegrityError):
        insert(first)
    with pytest.raises(ValidationError):
        content._meta.get_field("sha256").clean(b"short", row)


@pytest.mark.django_db(transaction=True)
def test_storage_migration_keeps_legacy_tables_and_skips_legacy_fk_scans(tmp_path):
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    from tests.test_sqlite_migration_schema import disposable_config
    from twicc.db.backends.sqlite3.base import DatabaseWrapper

    alias = "content_migration"
    db = DatabaseWrapper(disposable_config(tmp_path / "migration.sqlite"), alias)
    setattr(connections._connections, alias, db)
    try:
        executor = MigrationExecutor(db)
        previous = [("core", "0155_session_hidden_partial_index")]
        state = executor.loader.project_state(previous)
        apps = state.apps
        with db.schema_editor() as editor:
            for model in apps.get_app_config("core").get_models():
                editor.create_model(model)
        executor.recorder.ensure_schema()
        project = apps.get_model("core", "Project").objects.using(alias).create(id="test-project", directory="/example")
        session = apps.get_model("core", "Session").objects.using(alias).create(
            id="test-session", project=project, provider="codex", file_path="test.jsonl",
        )
        apps.get_model("core", "SessionItem").objects.using(alias).create(session=session, line_num=1, content="legacy é")
        before = db.connection.execute(
            "SELECT name, rootpage FROM sqlite_master WHERE name IN ('core_session', 'core_sessionitem')"
        ).fetchall()
        statements = []
        db.connection.set_trace_callback(statements.append)
        migration = executor.loader.get_migration("core", "0156_content_storage_schema")
        executor.apply_migration(state, migration)
        db.connection.set_trace_callback(None)
        assert db.connection.execute(
            "SELECT name, rootpage FROM sqlite_master WHERE name IN ('core_session', 'core_sessionitem')"
        ).fetchall() == before
        assert db.connection.execute(
            "SELECT content, current_content_id, original_content_id FROM core_sessionitem"
        ).fetchall() == [("legacy é", None, None)]
        assert db.connection.execute(
            "SELECT content_storage_version, content_state_epoch, plaintext_verified_epoch, storage_incarnation FROM core_session"
        ).fetchall() == [(None, None, None, None)]
        assert db.connection.execute("SELECT COUNT(*) FROM core_contentdictionary").fetchone() == (2,)
        # Dictionary insertion may check the new, empty dependent table only.
        assert [sql for sql in statements if sql.startswith("PRAGMA foreign_key_check")] == [
            'PRAGMA foreign_key_check("core_sessionitemcontent")',
        ]
        assert not any('new__core_session' in sql for sql in statements)
        assert not any("CREATE INDEX" in sql and 'ON "core_sessionitem"' in sql for sql in statements)
    finally:
        db.close()
        delattr(connections._connections, alias)
