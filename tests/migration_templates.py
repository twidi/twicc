"""Migrated database files shared by every migration test of the run."""

import pytest


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
