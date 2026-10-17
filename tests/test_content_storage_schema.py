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
        state = executor.apply_migration(state, migration)
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

        # Run the remaining branch chain with legacy content still present.
        for name in (
            '0157_compute_aggregate_repair', '0158_statistics_queue', '0159_remove_compute_aggregate_repair',
            '0160_statistics_queue_force_cost_sessions', '0161_session_compute_root_index',
        ):
            state = executor.apply_migration(state, executor.loader.get_migration('core', name))
        before_index = state.clone()
        migration = executor.loader.get_migration('core', '0162_content_plain_partial_index')
        counts_at_create = []

        def observe_index(execute, sql, params, many, context):
            if str(sql).startswith('CREATE INDEX "idx_content_plain_id"'):
                counts_at_create.append(db.connection.execute('SELECT COUNT(*) FROM core_sessionitemcontent').fetchone()[0])
            return execute(sql, params, many, context)

        statements.clear()
        db.connection.set_trace_callback(statements.append)
        with db.execute_wrapper(observe_index):
            state = executor.apply_migration(state, migration)
        db.connection.set_trace_callback(None)
        assert counts_at_create == [0]
        assert not any(sql.startswith('PRAGMA foreign_key_check') for sql in statements)
        assert not any('new__core_sessionitem' in sql for sql in statements)
        assert db.connection.execute(
            "SELECT name, rootpage FROM sqlite_master WHERE name IN ('core_session', 'core_sessionitem')"
        ).fetchall() == before
        assert db.connection.execute('SELECT content FROM core_sessionitem').fetchall() == [('legacy é',)]

        content = state.apps.get_model('core', 'SessionItemContent')
        first = content.objects.using(alias).create(sha256=b'a' * 32, payload=b'first', raw_size=5)
        second = content.objects.using(alias).create(sha256=b'b' * 32, payload=b'second', raw_size=6, codec='zstd')

        def members():
            return db.connection.execute(
                "SELECT id FROM core_sessionitemcontent INDEXED BY idx_content_plain_id WHERE codec = 'plain' ORDER BY id"
            ).fetchall()

        assert members() == [(first.id,)]
        content.objects.using(alias).filter(pk=first.id).update(codec='zstd')
        assert members() == []
        content.objects.using(alias).filter(pk=second.id).update(codec='plain')
        assert members() == [(second.id,)]
        executor.unapply_migration(before_index, migration)
        assert db.connection.execute("SELECT name FROM sqlite_master WHERE name = 'idx_content_plain_id'").fetchall() == []
        state = executor.apply_migration(before_index, migration)
        assert members() == [(second.id,)]
    finally:
        db.close()
        delattr(connections._connections, alias)


@pytest.mark.django_db
def test_plain_partial_index_definition():
    from django.db import connection

    indexes = {index.name: index for index in core.SessionItemContent._meta.indexes}
    assert 'idx_content_plain_id' in indexes
    index = indexes['idx_content_plain_id']
    assert index.fields == ['id']
    assert index.condition == models.Q(codec='plain')
    with connection.cursor() as cursor:
        cursor.execute("SELECT sql FROM sqlite_master WHERE name = %s", ['idx_content_plain_id'])
        ddl = cursor.fetchone()[0]
        assert 'ON "core_sessionitemcontent" ("id")' in ddl
        assert ddl.endswith("WHERE \"codec\" = 'plain'")
