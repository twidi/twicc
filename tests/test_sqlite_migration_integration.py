"""Validate backend selection, command logs, and the complete upgrade graph."""

import io
import logging
import os
import subprocess
import sys
from contextlib import contextmanager

import pytest
from django.core.management import call_command
from django.db import IntegrityError, OperationalError, connections
from django.db.backends.sqlite3.base import DatabaseWrapper as StandardWrapper
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import override_settings

from twicc.db.backends.sqlite3.base import DatabaseWrapper


M146 = ("core", "0146_agent_runs")
M147 = ("core", "0147_session_history_fact")
M148 = ("core", "0148_live_contribution_indexes")
M149 = ("core", "0149_remove_redundant_message_index")
SQUASH = ("core", "0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index")


@pytest.fixture
def disposable_db(tmp_path, django_db_blocker):
    @contextmanager
    def open_db(standard=False):
        # Historical RunPython migrations use the default ORM alias.
        alias = "default"
        previous = connections[alias]
        previous_config = connections.databases[alias]
        config = {**connections["default"].settings_dict, "NAME": str(tmp_path / "disposable.sqlite3")}
        config["ENGINE"] = "django.db.backends.sqlite3" if standard else "twicc.db.backends.sqlite3"
        db = (StandardWrapper if standard else DatabaseWrapper)(config, alias)
        connections.databases[alias] = config
        setattr(connections._connections, alias, db)
        with django_db_blocker.unblock():
            db.ensure_connection()
            try:
                yield db
            finally:
                db.close()
                setattr(connections._connections, alias, previous)
                connections.databases[alias] = previous_config
    return open_db


@contextmanager
def trace(db):
    statements = []
    db.connection.set_trace_callback(statements.append)
    try:
        yield statements
    finally:
        db.connection.set_trace_callback(None)


def check_sql(statements):
    return [sql for sql in statements if sql.startswith("PRAGMA foreign_key_check")]


@pytest.mark.parametrize("opt_out", ["", "1", "0"])
def test_backend_selected_before_connection_opens(opt_out):
    script = """
import twicc.settings_migration as settings
from django.db.utils import load_backend
config = settings.DATABASES['default']
db = load_backend(config['ENGINE']).DatabaseWrapper(config)
print(config['ENGINE'])
print(db.connection is None)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True,
        env={**os.environ, "TWICC_SQLITE_STANDARD_MIGRATIONS": opt_out},
    )
    expected = "django.db.backends.sqlite3" if opt_out == "1" else "twicc.db.backends.sqlite3"
    assert result.stdout.splitlines() == [expected, "True"]


def test_test_settings_select_standard_backend():
    assert connections["default"].settings_dict["ENGINE"] == "django.db.backends.sqlite3"


@pytest.fixture
def command_migrations(tmp_path, monkeypatch, caplog):
    package = tmp_path / "command_migrations"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "0001_initial.py").write_text('''
from django.db import migrations, models
class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [migrations.CreateModel(name="LogProbe", fields=[
        ("id", models.AutoField(primary_key=True)), ("label", models.TextField())])]
''')
    (package / "0002_write.py").write_text('''
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunSQL("UPDATE core_logprobe SET label = 'after'",
                                   "UPDATE core_logprobe SET label = 'before'")]
''')
    (package / "0003_failure.py").write_text('''
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = [("core", "0002_write")]
    operations = [migrations.RunSQL("UPDATE missing_table SET label = 'bad'")]
''')
    (package / "0004_unknown.py").write_text('''
from django.db import migrations
from django.db.migrations.operations.base import Operation
class UnknownOperation(Operation):
    reversible = True
    def state_forwards(self, app_label, state):
        pass
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        schema_editor.execute("CREATE TABLE custom_probe (id integer)")
    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        schema_editor.execute("DROP TABLE custom_probe")
class Migration(migrations.Migration):
    dependencies = [("core", "0003_failure")]
    operations = [UnknownOperation()]
''')
    monkeypatch.syspath_prepend(str(tmp_path))
    logger = logging.getLogger("twicc.db.migrations")
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", True)
    caplog.set_level(logging.INFO, logger=logger.name)
    with override_settings(MIGRATION_MODULES={"core": "command_migrations"}):
        yield
    for name in list(sys.modules):
        if name == "command_migrations" or name.startswith("command_migrations."):
            del sys.modules[name]


def run_command(db, target, **options):
    output = io.StringIO()
    call_command("migrate", "core", target, database=db.alias, verbosity=0,
                 skip_checks=True, stdout=output, **options)
    assert output.getvalue() == ""


def events(caplog, event):
    return [record for record in caplog.records if getattr(record, "migration_event", None) == event]


def test_command_logs_forward_backward_and_fk_duration(disposable_db, command_migrations, caplog):
    with disposable_db() as db:
        run_command(db, "0002")
        run_command(db, "0001")
    complete = events(caplog, "success")
    assert [(r.migration, r.direction, r.fake) for r in complete] == [
        ("core.0001_initial", "forward", False),
        ("core.0002_write", "forward", False),
        ("core.0002_write", "backward", False),
    ]
    assert all(r.duration_seconds >= 0 for r in complete)
    checks = [r for r in events(caplog, "fk_check") if r.migration == "core.0002_write"]
    assert {r.direction for r in checks} == {"forward", "backward"}
    assert all(r.scope == "none" and r.fk_check_seconds == 0 and r.reason for r in checks)


@pytest.mark.parametrize("fake_initial", [False, True])
def test_command_logs_fake_and_fake_initial_final_outcome(disposable_db, command_migrations, caplog, fake_initial):
    with disposable_db() as db:
        if fake_initial:
            db.connection.execute("CREATE TABLE core_logprobe (id integer PRIMARY KEY, label text)")
        run_command(db, "0001", fake=not fake_initial, fake_initial=fake_initial)
        assert ("core", "0001_initial") in MigrationRecorder(db).applied_migrations()
    start = events(caplog, "start")[-1]
    success = events(caplog, "success")[-1]
    assert start.fake is (not fake_initial)
    assert success.fake is True
    assert not [r for r in events(caplog, "fk_check") if r.migration == "core.0001_initial"]


def test_failure_logs_active_migration_and_restores_context(disposable_db, command_migrations, caplog):
    from twicc.db.migration_logging import current_migration

    outer = ("outer.migration", "backward")
    token = current_migration.set(outer)
    try:
        with disposable_db() as db:
            with pytest.raises(OperationalError, match="missing_table"):
                run_command(db, "0003")
            assert ("core", "0003_failure") not in MigrationRecorder(db).applied_migrations()
            assert db.connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
            assert current_migration.get() == outer
            failed = events(caplog, "failure")[-1]
            assert (failed.migration, failed.direction) == ("core.0003_failure", "forward")
            assert failed.duration_seconds >= 0
            run_command(db, "0001")
            assert current_migration.get() == outer
    finally:
        current_migration.reset(token)


def index_names(db, table):
    return {row[1] for row in db.connection.execute(f'PRAGMA index_list("{table}")')}


def populate_items(db, state):
    Session = state.apps.get_model("core", "Session")
    Item = state.apps.get_model("core", "SessionItem")
    Project = state.apps.get_model("core", "Project")
    Project.objects.using(db.alias).create(id="replay-project", directory="/disposable/replay")
    Session.objects.using(db.alias).create(id="replay-session", project_id="replay-project")
    Item.objects.using(db.alias).bulk_create([
        Item(session_id="replay-session", line_num=n, content='{"text":"historical"}')
        for n in range(1, 11)
    ])


def assert_latest(db):
    assert "core_sessionhistoryfact" in db.introspection.table_names()
    assert "idx_session_parent_cost" in index_names(db, "core_session")
    assert "idx_item_message_line" not in index_names(db, "core_sessionitem")
    records = MigrationRecorder(db).applied_migrations()
    assert {M147, M148, M149, SQUASH} <= records.keys()


def test_complete_fresh_install_rollback_146_and_populated_replay(disposable_db):
    with disposable_db() as db:
        executor = MigrationExecutor(db)
        executor.migrate(executor.loader.graph.leaf_nodes())
        assert_latest(db)
        with trace(db) as statements:
            MigrationExecutor(db).migrate([M146])
        assert check_sql(statements) == []
        assert "core_sessionhistoryfact" not in db.introspection.table_names()
        assert "idx_session_parent_cost" not in index_names(db, "core_session")
        records = MigrationRecorder(db).applied_migrations()
        assert not {M147, M148, M149, SQUASH} & records.keys()
        executor = MigrationExecutor(db)
        populate_items(db, executor.loader.project_state([M146]))
        with trace(db) as statements:
            executor.migrate(executor.loader.graph.leaf_nodes("core"))
        assert check_sql(statements) == []
        assert_latest(db)
        assert db.connection.execute("SELECT count(*) FROM core_sessionitem").fetchone() == (10,)


def test_mixed_148_state_applies_original_149_and_replacement_bookkeeping(disposable_db, monkeypatch):
    with disposable_db() as db:
        # Simulate the release where the new replacement is not yet shipped.
        # Keep every older replacement and every original migration unchanged.
        load_disk = MigrationLoader.load_disk

        def before_new_squash(loader):
            load_disk(loader)
            loader.disk_migrations.pop(SQUASH)

        with monkeypatch.context() as patch:
            patch.setattr(MigrationLoader, "load_disk", before_new_squash)
            executor = MigrationExecutor(db)
            executor.migrate([M148])
        records = MigrationRecorder(db).applied_migrations()
        assert M148 in records and M149 not in records and SQUASH not in records
        assert "idx_item_message_line" in index_names(db, "core_sessionitem")
        executor = MigrationExecutor(db)
        assert SQUASH not in executor.loader.graph.nodes
        state = executor.loader.project_state([M148])
        populate_items(db, state)
        plan = executor.migration_plan([M149])
        assert [(migration.name, backward) for migration, backward in plan] == [(M149[1], False)]
        original = plan[0][0]
        with trace(db) as statements:
            executor.apply_migration(state.clone(), original)
        assert check_sql(statements) == []
        records = MigrationRecorder(db).applied_migrations()
        assert M148 in records and M149 in records and SQUASH not in records
        assert "idx_item_message_line" not in index_names(db, "core_sessionitem")
        # Reverse the original route before replacement bookkeeping runs.
        with trace(db) as statements:
            executor.unapply_migration(state.clone(), original)
        assert check_sql(statements) == []
        assert "idx_item_message_line" in index_names(db, "core_sessionitem")
        records = MigrationRecorder(db).applied_migrations()
        assert M148 in records and M149 not in records and SQUASH not in records
        with trace(db) as statements:
            MigrationExecutor(db).migrate([M149])
        assert check_sql(statements) == []
        assert_latest(db)
        assert SQUASH in MigrationExecutor(db).loader.graph.nodes
        with trace(db) as statements:
            MigrationExecutor(db).migrate([M146])
        assert check_sql(statements) == []
        assert "idx_session_parent_cost" not in index_names(db, "core_session")
        assert "idx_item_message_line" not in index_names(db, "core_sessionitem")
        assert "core_sessionhistoryfact" not in db.introspection.table_names()
        assert not {M147, M148, M149, SQUASH} & MigrationRecorder(db).applied_migrations().keys()


def test_sqlmigrate_collects_sql_without_writes(disposable_db):
    with disposable_db() as db:
        before = db.introspection.table_names()
        with trace(db) as statements:
            sql = call_command("sqlmigrate", "core", M147[1], database=db.alias, stdout=io.StringIO())
        assert "CREATE TABLE \"core_sessionhistoryfact\"" in sql
        assert db.introspection.table_names() == before
        assert check_sql(statements) == []
        assert db.connection.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_standard_backend_retains_global_checks(disposable_db, command_migrations):
    with disposable_db(standard=True) as db:
        with trace(db) as statements:
            run_command(db, "0002")
        # Recorder table creation and both migrations keep standard checks.
        assert check_sql(statements) == ["PRAGMA foreign_key_check"] * 3


def test_global_fallback_logs_reason_and_check_duration(disposable_db, command_migrations, caplog):
    with disposable_db() as db:
        with db.schema_editor() as editor:
            editor.execute("CREATE TABLE unknown_operation (id integer)")
        record = events(caplog, "fk_check")[-1]
        assert record.scope == "global"
        assert "unproved main-schema effects" in record.reason
        assert record.fk_check_seconds >= 0


@pytest.mark.parametrize("invalid", [False, True])
def test_selected_check_logs_scope_and_validation_failure(disposable_db, command_migrations, caplog, invalid):
    with disposable_db() as db:
        db.connection.execute("CREATE TABLE parent (id integer PRIMARY KEY)")
        db.connection.execute("CREATE TABLE child (id integer PRIMARY KEY, parent_id integer REFERENCES parent(id))")
        db.connection.execute("INSERT INTO parent VALUES (1)")
        db.connection.execute("INSERT INTO child VALUES (1, 1)")

        def change_relation():
            with db.schema_editor():
                db.connection.execute("UPDATE child SET parent_id = ?", (2 if invalid else 1,))

        if invalid:
            with pytest.raises(IntegrityError):
                change_relation()
        else:
            change_relation()
        record = events(caplog, "fk_check")[-1]
        assert record.scope == "tables"
        assert record.tables == ("child",)
        assert record.reason == "observed relation effects"
        assert record.success is (not invalid)
        assert record.fk_check_seconds >= 0
        assert db.connection.execute("SELECT parent_id FROM child").fetchone() == (1,)


def test_fake_backward_logs_and_normal_cli_output_remains(disposable_db, command_migrations, caplog):
    with disposable_db() as db:
        output = io.StringIO()
        call_command("migrate", "core", "0001", database=db.alias, verbosity=1,
                     skip_checks=True, stdout=output)
        assert "Applying core.0001_initial... OK" in output.getvalue()
        run_command(db, "zero", fake=True)
        record = events(caplog, "success")[-1]
        assert (record.direction, record.fake) == ("backward", True)
        assert "core_logprobe" in db.introspection.table_names()
        assert ("core", "0001_initial") not in MigrationRecorder(db).applied_migrations()


def test_unknown_operation_uses_global_check_in_both_directions(disposable_db, command_migrations, caplog):
    with disposable_db() as db:
        run_command(db, "0002")
        run_command(db, "0003", fake=True)
        run_command(db, "0004")
        assert "custom_probe" in db.introspection.table_names()
        run_command(db, "0003")
        assert "custom_probe" not in db.introspection.table_names()
    checks = [r for r in events(caplog, "fk_check") if r.migration == "core.0004_unknown"]
    assert [(r.direction, r.scope) for r in checks] == [("forward", "global"), ("backward", "global")]
    assert all("unproved main-schema effects" in r.reason for r in checks)
