"""Migrated database files shared by every migration test of the run."""

import shutil

import pytest


@pytest.fixture(scope="session")
def migrated_133(tmp_path_factory, django_db_blocker, migrated_145):
    """Keep the historical rollback covered once, then share closed-file copies."""
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    from tests.test_sqlite_migration_integration import M145, M146, default_alias_database

    path = tmp_path_factory.mktemp("migrated_133") / "template.sqlite3"
    shutil.copyfile(migrated_145, path)
    with default_alias_database(path, django_db_blocker, standard=True) as db:
        # The full-install test covers latest -> 0146. Keep 0146 -> 0133
        # here, without rebuilding the latest schema after each peer test.
        target = ("core", "0133_share_created_by_session")
        MigrationExecutor(db).migrate([M146])
        MigrationExecutor(db).migrate([target])
        records = MigrationRecorder(db).applied_migrations()
        assert target in records and M145 not in records and M146 not in records
        with db.cursor() as cursor:
            columns = {column.name for column in db.introspection.get_table_description(cursor, "core_peermessage")}
        assert not {"thread_id", "reply_to", "reply_to_message_id"} & columns
    return path


@pytest.fixture
def peer_migration_database(tmp_path, django_db_blocker, migrated_133):
    """Keep historical peer migrations off the suite's current-schema database.

    Restoring the common database by replaying every later migration repeats
    unrelated schema work. A private copy needs no restoration. Each test
    still migrates its historical rows through the real migration executor.
    """
    from tests.test_sqlite_migration_integration import default_alias_database

    path = tmp_path / "peers.sqlite3"
    shutil.copyfile(migrated_133, path)
    with default_alias_database(path, django_db_blocker, standard=True) as db:
        yield db


@pytest.fixture(scope="session")
def migrated_145(tmp_path_factory, django_db_blocker):
    """A database file migrated from zero to 0145, built once per run.

    Tests whose subject starts at an installed 0145 or later copy it and
    migrate the copy, instead of replaying the whole chain each. Sharing is
    safe: no test opens this file, each one gets a byte copy of it (closed,
    journal deleted). The complete fresh install keeps its own run from zero.
    """
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    from tests.test_sqlite_migration_integration import M145, M146, default_alias_database

    path = tmp_path_factory.mktemp("migrated_145") / "template.sqlite3"
    with default_alias_database(path, django_db_blocker) as db:
        MigrationExecutor(db).migrate([M145])
        records = MigrationRecorder(db).applied_migrations()
        assert M145 in records and M146 not in records
    return path
