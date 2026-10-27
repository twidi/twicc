"""Migrated database files shared by every migration test of the run."""

import shutil
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from unittest.mock import patch

import pytest


@pytest.fixture(scope="session")
def historical_migration_templates(tmp_path_factory, django_db_blocker, migrated_145):
    """Build each business migration's empty predecessor once.

    The full custom-backend install/rollback test covers latest -> 0146.
    Business migration tests need historical rows and their real upgrade,
    rather than another rollback of every unrelated later migration.
    """
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    from tests.test_sqlite_migration_integration import default_alias_database

    templates = {}

    def predecessor(target):
        if target not in templates:
            path = tmp_path_factory.mktemp(target[1]) / "template.sqlite3"
            shutil.copyfile(migrated_145, path)
            with default_alias_database(path, django_db_blocker, standard=True) as database:
                MigrationExecutor(database).migrate([target])
                assert target in MigrationRecorder(database).applied_migrations()
            templates[target] = path
        return templates[target]

    return predecessor


@pytest.fixture
def historical_migration_database(request, tmp_path, transactional_db, django_db_blocker,
                                  historical_migration_templates):
    """Give each test a private closed-file copy of its explicit predecessor."""
    from tests.test_sqlite_migration_integration import default_alias_database

    path = tmp_path / "migration.sqlite3"
    shutil.copyfile(historical_migration_templates(request.param), path)
    with default_alias_database(path, django_db_blocker, standard=True) as database:
        yield database


@contextmanager
def memoized_migration_parsing():
    """Reuse immutable lexical results while migrating a private fixture DB.

    The custom backend reads the real schema after every DDL operation.
    Most unchanged CREATE TABLE declarations recur at each step. Only pure
    string parsing is cached; database metadata and decisions stay fresh.
    Each fixture connection owns these bounded caches. Non-exact strings
    retain their original protocol behavior.
    """
    from twicc.db.backends.sqlite3 import effects, metadata, schema

    def cached(function):
        memo = lru_cache(maxsize=2048)(function)

        def call(value):
            return memo(value) if type(value) is str else function(value)

        return call

    with ExitStack() as stack:
        for name in ("canonical_identifier", "sql_tokens", "declarations"):
            original = getattr(metadata, name)
            replacement = cached(original)
            for module in (metadata, effects, schema):
                if getattr(module, name, None) is original:
                    stack.enter_context(patch.object(module, name, replacement))
        yield


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
    # Only the upgrade from this fixture is under test. The independent
    # fresh-install test still applies the whole chain with the custom backend.
    with default_alias_database(path, django_db_blocker, standard=True) as db:
        MigrationExecutor(db).migrate([M145])
        records = MigrationRecorder(db).applied_migrations()
        assert M145 in records and M146 not in records
    return path
