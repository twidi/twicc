"""Exercise editor decisions and failure boundaries on disposable databases."""

import sqlite3
import subprocess
import sys
from contextlib import contextmanager

import pytest
from django.db import (
    DatabaseError,
    IntegrityError,
    NotSupportedError,
    OperationalError,
    connections,
    models,
    transaction,
)
from django.db.migrations import CreateModel, Migration, RunPython, RunSQL
from django.db.migrations.operations.base import Operation
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ModelState, ProjectState
from django.test.utils import isolate_apps

from twicc.db.backends.sqlite3.base import DatabaseWrapper


@pytest.fixture
def connection(tmp_path, django_db_blocker):
    alias = "migration_scope"
    config = {**connections["default"].settings_dict, "NAME": str(tmp_path / "disposable.sqlite3")}
    db = DatabaseWrapper(config, alias)
    setattr(connections._connections, alias, db)
    django_db_blocker.unblock()
    db.ensure_connection()
    with db.cursor() as cursor:
        cursor.execute("CREATE TABLE parent (id integer PRIMARY KEY, label text, code text UNIQUE)")
        cursor.execute(
            "CREATE TABLE child (id integer PRIMARY KEY, parent_id integer REFERENCES parent(id), label text)"
        )
        cursor.execute("INSERT INTO parent VALUES (1, 'parent', 'a')")
        cursor.execute("INSERT INTO child VALUES (1, 1, 'child')")
    yield db
    db.close()
    delattr(connections._connections, alias)
    django_db_blocker.restore()


@contextmanager
def checks(db):
    statements = []
    db.connection.set_trace_callback(statements.append)
    try:
        yield statements
    finally:
        db.connection.set_trace_callback(None)


def fk_checks(statements):
    return [sql for sql in statements if sql.startswith("PRAGMA foreign_key_check")]


def assert_restored(db):
    assert db.get_autocommit()
    assert not db.in_atomic_block
    assert db.connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    # Observer ownership must be gone: temp writes work outside the context.
    db.connection.execute("CREATE TEMP TABLE cleanup_probe (id integer)")
    db.connection.execute("DROP TABLE cleanup_probe")


def migration(operation):
    result = Migration("scope_test", "scope_app")
    result.operations = [operation]
    return result


def state():
    result = ProjectState()
    result.add_model(
        ModelState(
            "scope_app",
            "Parent",
            [
                ("id", models.AutoField(primary_key=True)),
                ("label", models.TextField()),
                ("code", models.TextField(unique=True)),
            ],
            {"db_table": "parent"},
        )
    )
    result.add_model(
        ModelState(
            "scope_app",
            "Child",
            [
                ("id", models.AutoField(primary_key=True)),
                ("parent", models.ForeignKey("scope_app.Parent", on_delete=models.CASCADE)),
                ("label", models.TextField()),
            ],
            {"db_table": "child"},
        )
    )
    return result


@pytest.mark.parametrize("kind", ["sql", "python"])
@pytest.mark.parametrize("backward", [False, True])
def test_executor_ordinary_writes_skip_checks_and_record_migration(connection, kind, backward):
    if kind == "sql":
        operation = RunSQL("UPDATE child SET label = 'changed'", "UPDATE child SET label = 'reversed'")
    else:

        def write(apps, editor):
            apps.get_model("scope_app", "Child").objects.using(editor.connection.alias).update(label="changed")

        operation = RunPython(write, write)
    executor = MigrationExecutor(connection)
    executor.recorder.ensure_schema()
    item = migration(operation)
    if backward:
        executor.recorder.record_applied("scope_app", "scope_test")
    with checks(connection) as statements:
        if backward:
            executor.unapply_migration(state(), item)
        else:
            executor.apply_migration(state(), item)
    assert fk_checks(statements) == []
    assert (("scope_app", "scope_test") in executor.recorder.applied_migrations()) is not backward
    assert_restored(connection)


@pytest.mark.parametrize("sql", ["UPDATE child SET parent_id = 99", "DELETE FROM parent", "UPDATE parent SET id = 99"])
@pytest.mark.parametrize("backward", [False, True])
def test_executor_invalid_writes_rollback_before_record_change(connection, sql, backward):
    executor = MigrationExecutor(connection)
    executor.recorder.ensure_schema()
    item = migration(RunSQL(sql, sql))
    if backward:
        executor.recorder.record_applied("scope_app", "scope_test")
    with checks(connection) as statements, pytest.raises(IntegrityError):
        if backward:
            executor.unapply_migration(state(), item)
        else:
            executor.apply_migration(state(), item)
    assert fk_checks(statements) == ['PRAGMA foreign_key_check("child")']
    assert connection.connection.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert connection.connection.execute("SELECT id FROM parent").fetchall() == [(1,)]
    assert (("scope_app", "scope_test") in executor.recorder.applied_migrations()) is backward
    assert_restored(connection)


@isolate_apps()
def test_create_model_foreign_keys_and_implicit_m2m_skip_checks(connection):
    class Tag(models.Model):
        class Meta:
            app_label = "scope_app"

    class Entry(models.Model):
        tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
        tags = models.ManyToManyField(Tag, related_name="+")
        label = models.TextField(db_index=True)

        class Meta:
            app_label = "scope_app"
            indexes = [models.Index(fields=["label"], name="entry_label_idx")]
            unique_together = [("tag", "label")]

    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.create_model(Tag)
        editor.create_model(Entry)
    assert fk_checks(statements) == []
    assert "scope_app_entry_tags" in connection.introspection.table_names()
    assert sum('CREATE INDEX "entry_label_idx"' in sql for sql in statements) == 1
    assert_restored(connection)


@pytest.mark.parametrize("remove", [False, True])
def test_nonunique_index_operations_skip_checks(connection, remove):
    model = state().apps.get_model("scope_app", "Child")
    index = models.Index(fields=["label"], name="child_label_idx")
    if remove:
        connection.connection.execute("CREATE INDEX child_label_idx ON child(label)")
    with checks(connection) as statements, connection.schema_editor() as editor:
        getattr(editor, "remove_index" if remove else "add_index")(model, index)
    assert fk_checks(statements) == []
    assert_restored(connection)


@pytest.mark.parametrize("referencing_deleted", [False, True])
def test_delete_model_checks_only_surviving_children(connection, referencing_deleted):
    apps = state().apps
    with checks(connection) as statements:
        if referencing_deleted:
            with connection.schema_editor() as editor:
                editor.delete_model(apps.get_model("scope_app", "Child"))
                editor.delete_model(apps.get_model("scope_app", "Parent"))
        else:
            with pytest.raises(IntegrityError), connection.schema_editor() as editor:
                editor.delete_model(apps.get_model("scope_app", "Parent"))
    assert fk_checks(statements) == ([] if referencing_deleted else ['PRAGMA foreign_key_check("child")'])
    assert ("parent" in connection.introspection.table_names()) is not referencing_deleted
    assert_restored(connection)


@pytest.mark.parametrize("write_path", ["native", "trigger", "callback"])
def test_known_index_operation_preserves_unrelated_invalid_data_effects(connection, write_path):
    model = state().apps.get_model("scope_app", "Child")
    if write_path == "trigger":
        connection.connection.execute(
            "CREATE TRIGGER bad AFTER UPDATE OF label ON parent BEGIN UPDATE child SET parent_id=99; END"
        )

    class CallbackIndex(models.Index):
        def create_sql(self, model, schema_editor, using=""):
            schema_editor.connection.connection.execute("UPDATE child SET parent_id=99")
            return super().create_sql(model, schema_editor, using=using)

    index = (CallbackIndex if write_path == "callback" else models.Index)(fields=["label"], name="child_label_idx")
    with checks(connection) as statements, pytest.raises(IntegrityError), connection.schema_editor() as editor:
        editor.add_index(model, index)
        if write_path == "native":
            connection.connection.execute("UPDATE child SET parent_id=99")
        elif write_path == "trigger":
            connection.connection.execute("UPDATE parent SET label='trigger'")
    assert fk_checks(statements)
    assert connection.connection.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert_restored(connection)


@pytest.mark.parametrize("sql", ["CREATE TABLE raw (id integer)", "DROP INDEX parent_code_idx"])
def test_raw_schema_and_unique_index_removal_use_global_check(connection, sql):
    connection.connection.execute("CREATE UNIQUE INDEX parent_code_idx ON parent(code)")
    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.execute(sql)
    assert fk_checks(statements) == ["PRAGMA foreign_key_check"]


def test_standard_unique_index_removal_uses_global_check(connection):
    connection.connection.execute("CREATE UNIQUE INDEX parent_code_idx ON parent(code)")
    model = state().apps.get_model("scope_app", "Parent")
    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.remove_index(model, models.Index(fields=["code"], name="parent_code_idx"))
    assert fk_checks(statements) == ["PRAGMA foreign_key_check"]


@isolate_apps()
@pytest.mark.parametrize("tampering", ["append", "mutate", "failure"])
def test_deferred_sql_loses_trust_or_rolls_back_on_failure(connection, tampering):
    class Entry(models.Model):
        label = models.TextField(db_index=True)

        class Meta:
            app_label = "scope_app"

    with checks(connection) as statements:
        try:
            with connection.schema_editor() as editor:
                editor.create_model(Entry)
                if tampering == "append":
                    editor.deferred_sql.append("CREATE INDEX appended_idx ON scope_app_entry(label)")
                elif tampering == "mutate":
                    editor.deferred_sql[0].template = "CREATE INDEX mutated_idx ON %(table)s (%(columns)s)"
                else:
                    editor.deferred_sql.append("INSERT INTO absent VALUES (1)")
        except OperationalError:
            if tampering != "failure":
                raise
        else:
            assert tampering != "failure"
    assert fk_checks(statements) == ([] if tampering == "failure" else ["PRAGMA foreign_key_check"])
    assert ("scope_app_entry" in connection.introspection.table_names()) is not (tampering == "failure")
    assert_restored(connection)


@pytest.mark.parametrize("nested", ["editor", "atomic"])
def test_nested_entry_rejection_preserves_outer_observer(connection, nested):
    with checks(connection) as statements, pytest.raises(IntegrityError), connection.schema_editor() as editor:
        if nested == "editor":
            with pytest.raises(NotSupportedError), connection.schema_editor():
                pass
        else:
            with (
                transaction.atomic(using=connection.alias),
                pytest.raises(NotSupportedError),
                connection.schema_editor(),
            ):
                pass
        editor.execute("UPDATE child SET parent_id=99")
    assert fk_checks(statements) == ['PRAGMA foreign_key_check("child")']
    assert_restored(connection)


def test_entry_inside_atomic_rejects_before_disabling_enforcement(connection):
    with transaction.atomic(using=connection.alias):
        with pytest.raises(NotSupportedError), connection.schema_editor():
            pass
        assert connection.connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert_restored(connection)


def test_enabled_writable_schema_entry_is_rejected_without_changing_fk_state(connection):
    connection.connection.execute("PRAGMA writable_schema=ON")
    try:
        with pytest.raises(NotSupportedError), connection.schema_editor():
            pass
        assert connection.connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    finally:
        connection.connection.execute("PRAGMA writable_schema=OFF")
    assert_restored(connection)


@pytest.mark.parametrize("sql", ["CREATE TEMP TABLE unsupported (id integer)", "ATTACH ':memory:' AS unsupported"])
def test_unsupported_mutations_reject_and_cleanup(connection, sql):
    with pytest.raises(DatabaseError, match="not authorized"), connection.schema_editor() as editor:
        editor.execute(sql)
    assert_restored(connection)


def test_operation_error_rolls_back_and_restores(connection):
    with pytest.raises(ValueError, match="original"), connection.schema_editor() as editor:
        editor.execute("UPDATE child SET label='changed'")
        raise ValueError("original")
    assert connection.connection.execute("SELECT label FROM child").fetchall() == [("child",)]
    assert_restored(connection)


def test_nonatomic_validation_failure_restores_without_rollback_claim(connection):
    with pytest.raises(IntegrityError), connection.schema_editor(atomic=False) as editor:
        editor.execute("UPDATE child SET parent_id=99")
    assert_restored(connection)
    assert connection.connection.execute("SELECT parent_id FROM child").fetchall() == [(99,)]


@isolate_apps()
def test_collect_sql_has_no_database_or_enforcement_effects(connection):
    class Entry(models.Model):
        label = models.TextField(db_index=True)

        class Meta:
            app_label = "scope_app"

    with checks(connection) as statements, connection.schema_editor(collect_sql=True) as editor:
        assert connection.connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        editor.create_model(Entry)
    assert len(editor.collected_sql) == 2
    assert "scope_app_entry" not in connection.introspection.table_names()
    assert fk_checks(statements) == []
    assert_restored(connection)


def test_empty_editor_skips_checks(connection):
    with checks(connection) as statements, connection.schema_editor():
        pass
    assert fk_checks(statements) == []
    assert_restored(connection)


@isolate_apps()
def test_new_parent_validates_existing_incoming_children(connection):
    connection.disable_constraint_checking()
    connection.connection.execute(
        "CREATE TABLE orphan (id integer PRIMARY KEY, parent_id integer REFERENCES future_parent(id))"
    )
    connection.connection.execute("INSERT INTO orphan VALUES (1, 99)")
    connection.enable_constraint_checking()

    class FutureParent(models.Model):
        class Meta:
            app_label = "scope_app"
            db_table = "future_parent"

    with checks(connection) as statements, pytest.raises(IntegrityError), connection.schema_editor() as editor:
        editor.create_model(FutureParent)
    assert fk_checks(statements) == ['PRAGMA foreign_key_check("orphan")']
    assert "future_parent" not in connection.introspection.table_names()
    assert_restored(connection)


@isolate_apps()
def test_deferred_indexes_execute_before_table_check_and_commit(connection):
    class Entry(models.Model):
        label = models.TextField(db_index=True)

        class Meta:
            app_label = "scope_app"

    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.create_model(Entry)
        editor.execute("UPDATE child SET parent_id=1")
    checks_run = fk_checks(statements)
    assert checks_run == ['PRAGMA foreign_key_check("child")']
    index_positions = [i for i, sql in enumerate(statements) if sql.startswith("CREATE INDEX")]
    assert len(index_positions) == 1
    assert index_positions[0] < statements.index(checks_run[0]) < statements.index("COMMIT")
    assert_restored(connection)


def test_table_rebuild_retains_global_fallback(connection):
    apps = state().apps
    model = apps.get_model("scope_app", "Child")
    field = models.IntegerField(default=0)
    field.set_attributes_from_name("added")
    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.add_field(model, field)
    assert fk_checks(statements) == ["PRAGMA foreign_key_check"]
    assert connection.connection.execute("SELECT added FROM child").fetchall() == [(0,)]
    assert_restored(connection)


def test_custom_index_schema_callback_is_not_trusted_by_nested_standard_call(connection):
    model = state().apps.get_model("scope_app", "Child")

    class CallbackIndex(models.Index):
        def create_sql(self, model, schema_editor, using=""):
            schema_editor.connection.connection.execute("CREATE TABLE unrelated (id integer)")
            return super().create_sql(model, schema_editor, using=using)

    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.add_index(model, CallbackIndex(fields=["label"], name="callback_idx"))
        editor.add_index(model, models.Index(fields=["label"], name="standard_idx"))
    assert fk_checks(statements) == ["PRAGMA foreign_key_check"]
    assert "unrelated" in connection.introspection.table_names()
    assert_restored(connection)


def test_custom_create_callback_cannot_hide_schema_or_invalid_writes(connection):
    class CallbackField(models.TextField):
        def db_parameters(self, connection):
            if (
                connection.connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name='callback_table'"
                ).fetchone()[0]
                == 0
            ):
                connection.connection.execute("CREATE TABLE callback_table (id integer)")
                connection.connection.execute("UPDATE child SET parent_id=99")
            return super().db_parameters(connection)

    with isolate_apps():

        class Entry(models.Model):
            label = CallbackField()

            class Meta:
                app_label = "scope_app"

        with checks(connection) as statements, pytest.raises(IntegrityError), connection.schema_editor() as editor:
            editor.create_model(Entry)
    assert fk_checks(statements) == ["PRAGMA foreign_key_check"]
    assert "callback_table" not in connection.introspection.table_names()
    assert connection.connection.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert_restored(connection)


@pytest.mark.parametrize("error_source", ["operation", "check"])
def test_original_error_survives_enforcement_cleanup_error(connection, monkeypatch, error_source):
    enable = connection.enable_constraint_checking

    def failing_enable():
        enable()
        raise RuntimeError("cleanup error")

    monkeypatch.setattr(connection, "enable_constraint_checking", failing_enable)
    original = ValueError if error_source == "operation" else IntegrityError
    with pytest.raises(original), connection.schema_editor() as editor:
        editor.execute("UPDATE child SET parent_id=99")
        if error_source == "operation":
            raise ValueError("original error")
    assert connection.connection.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert_restored(connection)


def test_success_cleanup_error_is_reported(connection, monkeypatch):
    enable = connection.enable_constraint_checking

    def failing_enable():
        enable()
        raise RuntimeError("cleanup error")

    monkeypatch.setattr(connection, "enable_constraint_checking", failing_enable)
    with pytest.raises(RuntimeError, match="cleanup error"), connection.schema_editor():
        pass
    assert_restored(connection)


def test_observer_cleanup_failure_does_not_commit_data(connection, monkeypatch):
    with pytest.raises(RuntimeError, match="observer cleanup error"), connection.schema_editor() as editor:
        stop = editor._stop_observing

        def failing_stop():
            stop()
            raise RuntimeError("observer cleanup error")

        monkeypatch.setattr(editor, "_stop_observing", failing_stop)
        editor.execute("UPDATE child SET label='changed'")
    assert connection.connection.execute("SELECT label FROM child").fetchall() == [("child",)]
    assert_restored(connection)


def test_readonly_attached_and_temp_access_is_supported(connection):
    connection.connection.execute("ATTACH ':memory:' AS existing")
    connection.connection.execute("CREATE TABLE existing.saved (id integer)")
    connection.connection.execute("CREATE TEMP TABLE saved_temp (id integer)")
    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.execute("SELECT * FROM existing.saved")
        editor.execute("SELECT * FROM saved_temp")
    assert fk_checks(statements) == []
    assert_restored(connection)


@pytest.mark.parametrize("schema", ["existing", "temp"])
def test_unsupported_native_writes_leave_other_schemas_unchanged(connection, schema):
    if schema == "existing":
        connection.connection.execute("ATTACH ':memory:' AS existing")
    connection.connection.execute(f"CREATE TABLE {schema}.saved (id integer)")
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"), connection.schema_editor():
        connection.connection.execute(f"INSERT INTO {schema}.saved VALUES (1)")
    assert connection.connection.execute(f"SELECT * FROM {schema}.saved").fetchall() == []
    assert_restored(connection)


def test_native_transaction_entry_is_rejected_before_pragma_or_observer_changes(connection):
    connection.disable_constraint_checking()
    connection.connection.execute("BEGIN")
    try:
        with pytest.raises(NotSupportedError), connection.schema_editor():
            pass
        assert connection.connection.in_transaction
        assert connection.connection.execute("PRAGMA foreign_keys").fetchone() == (0,)
        connection.connection.execute("CREATE TEMP TABLE native_cleanup (id integer)")
    finally:
        connection.connection.rollback()
        connection.enable_constraint_checking()
    assert_restored(connection)


def test_executor_create_model_and_implicit_m2m_apply_and_unapply_without_checks(connection):
    item = migration(
        CreateModel(
            "Entry",
            [
                ("id", models.AutoField(primary_key=True)),
                ("parent", models.ForeignKey("scope_app.Parent", on_delete=models.CASCADE)),
                ("tags", models.ManyToManyField("scope_app.Parent", related_name="+")),
                ("label", models.TextField(db_index=True)),
            ],
            options={"indexes": [models.Index(fields=["label"], name="entry_executor_idx")]},
        )
    )
    executor = MigrationExecutor(connection)
    executor.recorder.ensure_schema()
    before = state()
    with checks(connection) as statements:
        executor.apply_migration(before.clone(), item)
    assert fk_checks(statements) == []
    assert ("scope_app", "scope_test") in executor.recorder.applied_migrations()
    assert "scope_app_entry_tags" in connection.introspection.table_names()
    with checks(connection) as statements:
        executor.unapply_migration(before, item)
    assert fk_checks(statements) == []
    assert ("scope_app", "scope_test") not in executor.recorder.applied_migrations()
    assert "scope_app_entry" not in connection.introspection.table_names()
    assert_restored(connection)


def test_executor_custom_operation_keeps_unknown_schema_effects_global(connection):
    class CustomOperation(Operation):
        reversible = True

        def state_forwards(self, app_label, state):
            pass

        def database_forwards(self, app_label, editor, from_state, to_state):
            editor.connection.connection.execute("CREATE TABLE custom_effect (id integer)")
            editor.add_index(
                from_state.apps.get_model("scope_app", "Child"), models.Index(fields=["label"], name="custom_index")
            )

        def database_backwards(self, app_label, editor, from_state, to_state):
            editor.execute("DROP TABLE custom_effect")
            editor.remove_index(
                from_state.apps.get_model("scope_app", "Child"), models.Index(fields=["label"], name="custom_index")
            )

    executor = MigrationExecutor(connection)
    executor.recorder.ensure_schema()
    item = migration(CustomOperation())
    for backward in [False, True]:
        with checks(connection) as statements:
            if backward:
                executor.unapply_migration(state(), item)
            else:
                executor.apply_migration(state(), item)
        assert fk_checks(statements) == ["PRAGMA foreign_key_check"]
    assert "custom_effect" not in connection.introspection.table_names()
    assert_restored(connection)


def test_unique_sql_preserves_django_positional_argument_api(connection):
    model = state().apps.get_model("scope_app", "Parent")
    with connection.schema_editor() as editor:
        statement = editor._create_unique_sql(model, [model._meta.get_field("code")], "positional_unique")
        editor.execute(statement)
    assert connection.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='positional_unique'"
    ).fetchall() == [("positional_unique",)]
    assert_restored(connection)


@pytest.mark.parametrize("inner_collect", [False, True])
def test_collect_sql_context_rejects_nested_editors_and_releases_ownership(connection, inner_collect):
    with (
        connection.schema_editor(collect_sql=True),
        pytest.raises(NotSupportedError),
        connection.schema_editor(collect_sql=inner_collect),
    ):
        pass
    with connection.schema_editor():
        pass
    assert_restored(connection)


@pytest.mark.parametrize(
    "path",
    ["sql_commit", "sql_rollback", "sql_begin", "native_sql", "native_commit", "native_rollback", "native_script"],
)
@pytest.mark.parametrize("backward", [False, True])
def test_executor_transaction_controls_reject_before_atomic_boundary_changes(connection, path, backward):
    if path.startswith("sql_"):
        statements = ["UPDATE child SET parent_id=99", path.removeprefix("sql_").upper()]
        operation = RunSQL(statements, statements)
    else:

        def write(apps, editor):
            native = editor.connection.connection
            native.execute("UPDATE child SET parent_id=99")
            if path == "native_sql":
                native.cursor().execute("COMMIT")
            elif path == "native_commit":
                native.commit()
            elif path == "native_rollback":
                native.rollback()
            else:
                native.executescript("UPDATE child SET label='escaped';")

        operation = RunPython(write, write)
    executor = MigrationExecutor(connection)
    executor.recorder.ensure_schema()
    item = migration(operation)
    if backward:
        executor.recorder.record_applied("scope_app", "scope_test")
    expected_error = DatabaseError if path.startswith("sql_") else sqlite3.DatabaseError
    with checks(connection) as statements, pytest.raises(expected_error, match="not authorized"):
        if backward:
            executor.unapply_migration(state(), item)
        else:
            executor.apply_migration(state(), item)
    assert "COMMIT" not in statements
    assert connection.connection.execute("SELECT parent_id, label FROM child").fetchall() == [(1, "child")]
    assert (("scope_app", "scope_test") in executor.recorder.applied_migrations()) is backward
    assert_restored(connection)


@isolate_apps()
def test_custom_meta_index_cannot_inherit_trusted_deferred_provenance(connection):
    class CustomIndex(models.Index):
        pass

    class Entry(models.Model):
        label = models.TextField(db_index=True)

        class Meta:
            app_label = "scope_app"
            indexes = [
                CustomIndex(fields=["label"], name="custom_meta_idx"),
                models.Index(fields=["label"], name="standard_meta_idx"),
            ]

    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.create_model(Entry)
    assert fk_checks(statements) == ["PRAGMA foreign_key_check"]
    assert sum(sql.startswith('CREATE INDEX "custom_meta_idx"') for sql in statements) == 1
    assert sum(sql.startswith('CREATE INDEX "standard_meta_idx"') for sql in statements) == 1
    assert_restored(connection)


def test_deferred_commit_is_rejected_before_persisting_invalid_data(connection):
    with pytest.raises(DatabaseError, match="not authorized"), connection.schema_editor() as editor:
        editor.execute("UPDATE child SET parent_id=99")
        editor.deferred_sql.append("COMMIT")
    assert connection.connection.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert_restored(connection)


def test_caught_transaction_denial_keeps_later_outer_writes_observed(connection):
    with checks(connection) as statements, pytest.raises(IntegrityError), connection.schema_editor() as editor:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.connection.commit()
        assert connection.connection.in_transaction
        editor.execute("UPDATE child SET parent_id=99")
    assert fk_checks(statements) == ['PRAGMA foreign_key_check("child")']
    assert connection.connection.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert_restored(connection)


def test_nonatomic_editor_preserves_explicit_native_transaction_behavior(connection):
    with checks(connection) as statements, connection.schema_editor(atomic=False):
        connection.connection.execute("BEGIN")
        connection.connection.execute("UPDATE child SET label='nonatomic'")
        connection.connection.commit()
    assert fk_checks(statements) == []
    assert connection.connection.execute("SELECT label FROM child").fetchall() == [("nonatomic",)]
    assert_restored(connection)


def test_nested_django_and_native_savepoint_release_cannot_commit_outer_editor(connection):
    with checks(connection) as statements, pytest.raises(IntegrityError), connection.schema_editor() as editor:
        with transaction.atomic(using=connection.alias):
            editor.execute("UPDATE child SET label='savepoint'")
        assert connection.connection.in_transaction
        native = connection.connection
        native.execute("SAVEPOINT explicit_savepoint")
        native.execute("UPDATE child SET parent_id=99")
        native.execute("RELEASE explicit_savepoint")
        assert native.in_transaction
        assert connection.in_atomic_block
    assert fk_checks(statements) == ['PRAGMA foreign_key_check("child")']
    assert connection.connection.execute("SELECT parent_id, label FROM child").fetchall() == [(1, "child")]
    assert_restored(connection)


@pytest.mark.parametrize("rollback_kind", ["statement", "schema", "trigger"])
@pytest.mark.parametrize(
    "path",
    ["connection", "cursor", "many_connection", "many_cursor", "script_connection", "script_cursor", "recorder"],
)
@pytest.mark.parametrize("backward", [False, True])
def test_executor_implicit_rollback_blocks_cached_writes_and_record_changes(connection, rollback_kind, path, backward):
    native = connection.connection
    if rollback_kind == "schema":
        native.execute("CREATE TABLE conflict (id INTEGER UNIQUE ON CONFLICT ROLLBACK)")
        native.execute("INSERT INTO conflict VALUES (1)")
        conflict = "INSERT INTO conflict VALUES (1)"
    elif rollback_kind == "trigger":
        native.execute("CREATE TRIGGER rollback_insert BEFORE INSERT ON parent BEGIN SELECT RAISE(ROLLBACK, 'stop'); END")
        conflict = "INSERT INTO parent(id) VALUES (2)"
    else:
        conflict = "INSERT OR ROLLBACK INTO parent(id) VALUES (1)"
    # A cursor acquired before editor entry must still receive the execution guard.
    cursor = native.cursor()
    update = "UPDATE child SET parent_id=? WHERE id=1"

    def write(apps, editor):
        cursor.execute(update, (1,))  # Populate SQLite's statement cache under observation.
        with pytest.raises(sqlite3.IntegrityError):
            native.execute(conflict)
        assert not native.in_transaction
        target = cursor if path.endswith("cursor") or path == "cursor" else native
        if path.startswith("many_"):
            target.executemany(update, [(99,)])
        elif path.startswith("script_"):
            target.executescript("UPDATE child SET parent_id=99;")
        elif path != "recorder":
            target.execute(update, (99,))

    executor = MigrationExecutor(connection)
    executor.recorder.ensure_schema()
    item = migration(RunPython(write, write))
    if backward:
        executor.recorder.record_applied("scope_app", "scope_test")
    with pytest.raises((sqlite3.OperationalError, OperationalError), match="migration transaction"):
        if backward:
            executor.unapply_migration(state(), item)
        else:
            executor.apply_migration(state(), item)
    assert native.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert native.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (("scope_app", "scope_test") in executor.recorder.applied_migrations()) is backward
    assert_restored(connection)


def test_caught_implicit_rollback_fails_editor_exit_without_more_sql(connection):
    with (
        pytest.raises(sqlite3.OperationalError, match="migration transaction"),
        connection.schema_editor(),
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.connection.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
    assert_restored(connection)


@pytest.mark.parametrize("cursor", [False, True])
def test_executemany_checks_transaction_after_parameter_iterator_callbacks(connection, cursor):
    native = connection.connection

    def parameters():
        yield (1,)
        with pytest.raises(sqlite3.IntegrityError):
            native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
        yield (99,)

    target = native.cursor() if cursor else native
    with pytest.raises(sqlite3.OperationalError, match="migration transaction"), connection.schema_editor():
        target.executemany("UPDATE child SET parent_id=?", parameters())
    assert native.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert_restored(connection)


@pytest.mark.parametrize("method", ["fetchone", "fetchmany", "fetchall", "__next__"])
def test_implicit_rollback_blocks_pending_native_cursor_steps(connection, method):
    native = connection.connection
    with pytest.raises(sqlite3.OperationalError, match="migration transaction"), connection.schema_editor():
        cursor = native.execute("SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3")
        with pytest.raises(sqlite3.IntegrityError):
            native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
        with pytest.raises(sqlite3.OperationalError, match="migration transaction"):
            getattr(cursor, method)()
    assert_restored(connection)


def test_callable_cursor_factory_keeps_normal_behavior_and_rejects_atomic_editing(connection):
    native = connection.connection
    cursor = native.cursor(factory=lambda db: sqlite3.Cursor(db))
    assert cursor.execute("SELECT 1").fetchone() == (1,)
    with pytest.raises(NotSupportedError, match="cursor factory"), connection.schema_editor():
        pass
    assert_restored(connection)


def test_callable_cursor_factory_rejects_before_creating_unguarded_cursor_during_atomic_editing(connection):
    called = []

    def factory(db):
        called.append(True)
        return sqlite3.Cursor(db)

    with connection.schema_editor(), pytest.raises(sqlite3.NotSupportedError, match="cursor factory"):
        connection.connection.cursor(factory=factory)
    assert called == []
    assert_restored(connection)


@pytest.mark.parametrize("mode", ["normal", "nonatomic", "collect"])
def test_implicit_rollback_keeps_standard_behavior_without_owned_atomic_boundary(connection, mode):
    native = connection.connection

    def write():
        native.execute("BEGIN")
        with pytest.raises(sqlite3.IntegrityError):
            native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
        native.execute("UPDATE child SET label='autocommitted'")

    if mode == "normal":
        write()
    else:
        with connection.schema_editor(atomic=False, collect_sql=mode == "collect"):
            write()
    assert native.execute("SELECT label FROM child").fetchone() == ("autocommitted",)
    assert_restored(connection)


def test_uncaught_implicit_rollback_preserves_original_error_and_cleans_up(connection):
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint"), connection.schema_editor():
        connection.connection.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
    assert_restored(connection)


def test_custom_cursor_subclass_keeps_behavior_and_receives_atomic_guard(connection):
    class CustomCursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            return super().execute(sql, parameters)

    native = connection.connection
    cursor = native.cursor(factory=CustomCursor)
    assert isinstance(cursor, CustomCursor)
    with pytest.raises(sqlite3.OperationalError, match="migration transaction"), connection.schema_editor():
        cursor.execute("UPDATE child SET parent_id=?", (1,))
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
        cursor.execute("UPDATE child SET parent_id=?", (99,))
    assert native.execute("SELECT parent_id FROM child").fetchone() == (1,)
    assert_restored(connection)


@pytest.mark.parametrize("mode", ["atomic", "nonatomic", "collect"])
def test_custom_connection_factory_preserved_with_explicit_atomic_contract(connection, mode):
    config = {
        **connection.settings_dict,
        "NAME": ":memory:",
        "OPTIONS": {"factory": sqlite3.Connection},
    }
    db = DatabaseWrapper(config, "custom_factory")
    try:
        db.ensure_connection()
        assert type(db.connection) is sqlite3.Connection
        if mode == "atomic":
            with pytest.raises(NotSupportedError, match="connection factory"), db.schema_editor():
                pass
        else:
            with db.schema_editor(atomic=False, collect_sql=mode == "collect") as editor:
                editor.execute("CREATE TABLE custom_table(id integer)")
            exists = db.connection.execute("SELECT name FROM sqlite_master WHERE name='custom_table'").fetchall()
            assert exists == ([] if mode == "collect" else [("custom_table",)])
        assert_restored(db)
    finally:
        db.close()


def test_successful_native_callback_detects_implicit_rollback_before_return(connection):
    native = connection.connection

    def rollback_callback():
        with pytest.raises(sqlite3.IntegrityError):
            native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
        return 1

    native.create_function("rollback_callback", 0, rollback_callback)
    with (
        pytest.raises(sqlite3.OperationalError, match="migration transaction"),
        connection.schema_editor(),
        pytest.raises(sqlite3.OperationalError, match="migration transaction"),
    ):
        native.execute("SELECT rollback_callback()")
    assert_restored(connection)


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("path", ["connection", "cursor", "many", "custom_cursor", "django"])
@pytest.mark.parametrize("backward", [False, True])
def test_executor_parameter_adaptation_rollback_cannot_autocommit(connection, monkeypatch, registered, path, backward):
    native = connection.connection

    class CustomCursor(sqlite3.Cursor):
        pass

    cursor = native.cursor(factory=CustomCursor) if path == "custom_cursor" else native.cursor()
    sql = "UPDATE child SET parent_id=?"

    class Parameter:
        def __conform__(self, protocol):
            return adapt(self)

    def adapt(value):
        with pytest.raises(sqlite3.IntegrityError):
            native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
        return 99

    if registered:
        monkeypatch.setitem(sqlite3.adapters, (Parameter, sqlite3.PrepareProtocol), adapt)

    def write(apps, editor):
        native.execute(sql, (1,))  # Reuse the cached statement during binding.
        if path == "many":
            native.executemany(sql, [(Parameter(),)])
        elif path == "django":
            with editor.connection.cursor() as django_cursor:
                django_cursor.execute("UPDATE child SET parent_id=%s", (Parameter(),))
        else:
            (native if path == "connection" else cursor).execute(sql, (Parameter(),))

    executor = MigrationExecutor(connection)
    executor.recorder.ensure_schema()
    item = migration(RunPython(write, write))
    if backward:
        executor.recorder.record_applied("scope_app", "scope_test")
    with pytest.raises((sqlite3.OperationalError, OperationalError)):
        if backward:
            executor.unapply_migration(state(), item)
        else:
            executor.apply_migration(state(), item)
    assert native.execute("SELECT parent_id FROM child").fetchone() == (1,)
    assert native.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (("scope_app", "scope_test") in executor.recorder.applied_migrations()) is backward
    assert_restored(connection)


@pytest.mark.parametrize("stage", ["acquire", "release"])
def test_buffer_binding_callbacks_cannot_write_after_implicit_rollback(connection, stage):
    native = connection.connection
    events = []

    def rollback():
        with pytest.raises(sqlite3.IntegrityError):
            native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")

    class Buffer:
        def __buffer__(self, flags):
            events.append(("acquire", flags))
            if stage == "acquire":
                rollback()
            return memoryview(b"invalid")

        def __release_buffer__(self, view):
            events.append(("release", bytes(view)))
            if stage == "release":
                rollback()

    with pytest.raises(sqlite3.OperationalError), connection.schema_editor():
        native.execute("UPDATE child SET parent_id=?", (Buffer(),))
    assert native.execute("SELECT parent_id FROM child").fetchone() == (1,)
    assert events == [("acquire", 0), ("release", b"invalid")]
    assert_restored(connection)


@pytest.mark.parametrize("shape", ["sequence_length", "sequence_item", "mapping_item"])
def test_binding_container_callbacks_cannot_write_after_implicit_rollback(connection, shape):
    native = connection.connection

    def rollback():
        with pytest.raises(sqlite3.IntegrityError):
            native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")

    class Sequence:
        def __len__(self):
            if shape == "sequence_length":
                rollback()
            return 1

        def __getitem__(self, key):
            if shape == "sequence_item":
                rollback()
            return 99

    class Mapping(dict):
        def __getitem__(self, key):
            rollback()
            return 99

    sql = "UPDATE child SET parent_id=:parent" if shape == "mapping_item" else "UPDATE child SET parent_id=?"
    params = Mapping() if shape == "mapping_item" else Sequence()
    with pytest.raises(sqlite3.OperationalError), connection.schema_editor():
        native.execute(sql, params)
    assert native.execute("SELECT parent_id FROM child").fetchone() == (1,)
    assert_restored(connection)


@pytest.mark.parametrize("kind", ["null_adapter", "null_conform", "adapter_precedence", "native_adapter", "buffers", "buffer_hook", "buffer_adapter", "buffer_descriptor"])
def test_binding_matches_native_adaptation_semantics(connection, monkeypatch, kind):
    events = []

    class Value:
        def __conform__(self, protocol):
            events.append("conform")
            return None if kind == "null_conform" else 7

    class Buffer:
        def __buffer__(self, flags):
            events.append(("acquire", flags))
            return memoryview(b"buffer")

        def __release_buffer__(self, view):
            events.append(("release", bytes(view)))

    if kind == "buffer_descriptor":
        class BufferMethod:
            def __get__(self, instance, owner):
                events.append("buffer_descriptor")
                return lambda flags: memoryview(b"descriptor")

        Buffer.__buffer__ = BufferMethod()

    def adapter(value):
        events.append("adapter")
        if kind == "buffer_adapter":
            return memoryview(b"adapted buffer")
        return None if kind == "null_adapter" else 12

    if kind in ("null_adapter", "adapter_precedence", "buffer_adapter"):
        monkeypatch.setitem(sqlite3.adapters, (Value, sqlite3.PrepareProtocol), adapter)
    if kind in ("null_adapter", "adapter_precedence", "native_adapter"):
        # Registered native adapters must not run again on an adapter's result.
        for native_type in (int, type(None)):
            monkeypatch.setitem(sqlite3.adapters, (native_type, sqlite3.PrepareProtocol), lambda value: 42)
            sqlite3.register_adapter(native_type, sqlite3.adapters[native_type, sqlite3.PrepareProtocol])
    if kind == "buffer_adapter":
        for native_type in (bytes, bytearray, memoryview):
            monkeypatch.setitem(sqlite3.adapters, (native_type, sqlite3.PrepareProtocol), lambda value: b"readapted")
    if kind == "native_adapter":
        params = (4, None)
    elif kind == "buffers":
        params = (b"bytes", bytearray(b"mutable"), memoryview(b"view"))
    elif kind in ("buffer_hook", "buffer_descriptor"):
        params = (Buffer(),)
    else:
        params = (Value(),)
    sql = "SELECT " + ", ".join("?" for _ in params)

    def result(native):
        events.clear()
        try:
            value = native.execute(sql, params).fetchone()
            return ("value", value, list(events))
        except sqlite3.Error as error:
            return (type(error), str(error), list(events))

    with sqlite3.connect(":memory:") as standard:
        expected = result(standard)
    with connection.schema_editor():
        assert result(connection.connection) == expected
    assert_restored(connection)


def test_named_binding_skips_unused_values_and_preserves_lookup_order(connection):
    calls = []

    class Value:
        def __conform__(self, protocol):
            calls.append("adapt")
            return 1

    class Unused:
        def __conform__(self, protocol):
            raise AssertionError("unused parameter must not be adapted")

    class Params(dict):
        def __getitem__(self, key):
            calls.append(key)
            return super().__getitem__(key)

    params = Params(value=Value(), unused=Unused())
    with connection.schema_editor():
        assert connection.connection.execute("SELECT :value, :value", params).fetchone() == (1, 1)
    assert calls == ["value", "adapt"]
    assert_restored(connection)


@pytest.mark.parametrize(
    "sql, params",
    [
        ("SELECT ?", ()),
        ("SELECT ?", (1, 2)),
        ("SELECT :missing", {}),
        ("SELECT ?", {"value": 1}),
        ("SELECT ?", None),
        ("SELECT ?", (object(),)),
        ("SELECT ?", (memoryview(b"abcdef")[::2],)),
        ("broken sql ?", (object(),)),
    ],
)
def test_binding_errors_keep_native_type_and_message(connection, sql, params):
    def failure(native):
        with pytest.raises(Exception) as error:
            native.execute(sql, params)
        return type(error.value), str(error.value)

    with sqlite3.connect(":memory:") as standard:
        expected = failure(standard)
    with connection.schema_editor():
        assert failure(connection.connection) == expected
    assert_restored(connection)


def test_binding_adapter_error_takes_precedence_over_caught_rollback(connection):
    native = connection.connection

    class Parameter:
        def __conform__(self, protocol):
            with pytest.raises(sqlite3.IntegrityError):
                native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
            raise ValueError("adapter original error")

    with pytest.raises(ValueError, match="adapter original error"), connection.schema_editor():
        native.execute("UPDATE child SET parent_id=?", (Parameter(),))
    assert native.execute("SELECT parent_id FROM child").fetchone() == (1,)
    assert_restored(connection)


def test_custom_cursor_receives_original_parameters_before_guarded_binding(connection):
    received = []

    class CustomCursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            received.append(parameters)
            return super().execute(sql, parameters)

    params = (1,)
    cursor = connection.connection.cursor(factory=CustomCursor)
    with connection.schema_editor():
        assert cursor.execute("SELECT ?", params).fetchone() == (1,)
    assert received == [params]
    assert received[0] is params
    assert_restored(connection)


@pytest.mark.parametrize("stage", ["parameter", "adapted"])
def test_binding_temporaries_cannot_rollback_between_guard_and_step(connection, stage):
    native = connection.connection

    def rollback():
        try:
            native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
        except sqlite3.IntegrityError:
            pass

    class Adapted(int):
        def __del__(self):
            rollback()

    class Value:
        def __conform__(self, protocol):
            return Adapted(99) if stage == "adapted" else 99

        def __del__(self):
            if stage == "parameter":
                rollback()

    class Parameters:
        def __len__(self):
            return 1

        def __getitem__(self, index):
            return Value()

    with pytest.raises(sqlite3.OperationalError), connection.schema_editor():
        native.execute("UPDATE child SET parent_id=?", Parameters())
    assert native.execute("SELECT parent_id FROM child").fetchone() == (1,)
    assert_restored(connection)


def test_django_mapping_conversion_cannot_autocommit_a_statement_without_bindings(connection):
    native = connection.connection

    class Params(dict):
        def __iter__(self):
            with pytest.raises(sqlite3.IntegrityError):
                native.execute("INSERT OR ROLLBACK INTO parent(id) VALUES (1)")
            return iter(())

    with pytest.raises(OperationalError), connection.schema_editor(), connection.cursor() as cursor:
        cursor.execute("UPDATE child SET parent_id=99", Params())
    assert native.execute("SELECT parent_id FROM child").fetchone() == (1,)
    assert_restored(connection)


def test_binding_guard_registers_only_its_private_adapter():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sqlite3
class Existing:
    pass
def existing_adapter(value):
    return 1
sqlite3.register_adapter(Existing, existing_adapter)
before = dict(sqlite3.adapters)
from twicc.db.backends.sqlite3.bindings import Parameter, adapt_parameter
assert all(sqlite3.adapters[key] is value for key, value in before.items())
assert sqlite3.adapters.keys() - before.keys() == {(Parameter, sqlite3.PrepareProtocol)}
assert sqlite3.adapters[Parameter, sqlite3.PrepareProtocol] is adapt_parameter
print('one private registration; existing adapters unchanged')
""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "one private registration; existing adapters unchanged"


@isolate_apps()
@pytest.mark.parametrize("populated", [False, True])
def test_new_table_repeated_rebuilds(connection, populated):
    class Entry(models.Model):
        label = models.TextField()

        class Meta:
            app_label = "scope_app"

    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.create_model(Entry)
        if populated:
            editor.execute("INSERT INTO scope_app_entry (label) VALUES ('keep')")
        for _ in range(3):
            editor._remake_table(Entry)
    assert fk_checks(statements) == (["PRAGMA foreign_key_check"] if populated else [])
    assert connection.connection.execute("SELECT label FROM scope_app_entry").fetchall() == (
        [("keep",)] if populated else []
    )
    assert_restored(connection)


@isolate_apps()
@pytest.mark.parametrize("effect", ["write", "trigger", "schema", "pragma", "transient_reference"])
def test_empty_rebuild_preserves_callback_effects(connection, effect):
    if effect == "trigger":
        connection.connection.execute(
            "CREATE TRIGGER bad AFTER UPDATE OF label ON parent BEGIN UPDATE child SET parent_id=99; END"
        )
    if effect == "transient_reference":
        connection.connection.execute(
            "CREATE TABLE incoming (id integer REFERENCES new__scope_app_entry(id))"
        )
    active = False

    class CallbackField(models.TextField):
        def db_parameters(self, connection):
            nonlocal active
            if active:
                active = False
                sql = {
                    "write": "UPDATE child SET parent_id=99",
                    "trigger": "UPDATE parent SET label='fire'",
                    "schema": "CREATE TABLE unrelated (id integer)",
                    "pragma": "PRAGMA user_version=17",
                    "transient_reference": "SELECT 1",
                }[effect]
                connection.connection.execute(sql)
            return super().db_parameters(connection)

    class Entry(models.Model):
        label = CallbackField()

        class Meta:
            app_label = "scope_app"

    with checks(connection) as statements:
        try:
            with connection.schema_editor() as editor:
                editor.create_model(Entry)
                active = True
                editor._remake_table(Entry)
        except IntegrityError:
            assert effect in ("write", "trigger")
        else:
            assert effect not in ("write", "trigger")
    assert fk_checks(statements)
    assert connection.connection.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    if effect in ("schema", "pragma", "transient_reference"):
        assert fk_checks(statements) == ["PRAGMA foreign_key_check"]
    assert_restored(connection)


@isolate_apps()
def test_empty_rebuild_checks_incoming_orphans_and_rolls_back(connection):
    connection.disable_constraint_checking()
    connection.connection.execute("CREATE TABLE orphan (id integer PRIMARY KEY, parent_id integer REFERENCES scope_app_entry(id))")
    connection.connection.execute("INSERT INTO orphan VALUES (1, 99)")
    connection.enable_constraint_checking()

    class Entry(models.Model):
        class Meta:
            app_label = "scope_app"

    with checks(connection) as statements, pytest.raises(IntegrityError), connection.schema_editor() as editor:
        editor.create_model(Entry)
        editor._remake_table(Entry)
    assert fk_checks(statements) == ['PRAGMA foreign_key_check("orphan")']
    assert "scope_app_entry" not in connection.introspection.table_names()
    assert_restored(connection)


@isolate_apps()
@pytest.mark.parametrize("custom", [False, True])
def test_empty_rebuild_conditional_unique_constraint_provenance(connection, custom):
    class Entry(models.Model):
        label = models.TextField()

        class Meta:
            app_label = "scope_app"

    class CustomConstraint(models.UniqueConstraint):
        pass

    constraint = (CustomConstraint if custom else models.UniqueConstraint)(
        fields=["label"], condition=models.Q(label__isnull=False), name="entry_unique_label"
    )
    with checks(connection) as statements, connection.schema_editor() as editor:
        editor.create_model(Entry)
        editor._remake_table(Entry)
        editor.add_constraint(Entry, constraint)
    assert fk_checks(statements) == (["PRAGMA foreign_key_check"] if custom else [])


@isolate_apps()
@pytest.mark.parametrize("raw_create", [False, True])
def test_empty_rebuild_requires_new_standard_creation(connection, raw_create):
    class Entry(models.Model):
        label = models.TextField()

        class Meta:
            app_label = "scope_app"

    if not raw_create:
        with connection.schema_editor() as editor:
            editor.create_model(Entry)
    with checks(connection) as statements, connection.schema_editor() as editor:
        if raw_create:
            editor.execute("CREATE TABLE scope_app_entry (id integer PRIMARY KEY, label text)")
        editor._remake_table(Entry)
    assert fk_checks(statements) == ["PRAGMA foreign_key_check"]
    if not raw_create:
        assert not any(sql.startswith('SELECT 1 FROM "scope_app_entry"') for sql in statements)


@isolate_apps()
def test_empty_rebuild_populated_invalid_child_rolls_back(connection):
    class Parent(models.Model):
        class Meta:
            app_label = "scope_app"
            db_table = "parent"

    class Entry(models.Model):
        parent = models.ForeignKey(Parent, on_delete=models.CASCADE)

        class Meta:
            app_label = "scope_app"

    with checks(connection) as statements, pytest.raises(IntegrityError), connection.schema_editor() as editor:
        editor.create_model(Entry)
        editor.execute("INSERT INTO scope_app_entry (parent_id) VALUES (99)")
        editor._remake_table(Entry)
    assert fk_checks(statements) == ["PRAGMA foreign_key_check"]
    assert "scope_app_entry" not in connection.introspection.table_names()
    assert_restored(connection)


@isolate_apps()
def test_empty_rebuild_generated_name_collision_rolls_back(connection):
    connection.connection.execute("CREATE TABLE new__scope_app_entry (id integer PRIMARY KEY)")
    connection.connection.execute("INSERT INTO new__scope_app_entry VALUES (7)")

    class Entry(models.Model):
        class Meta:
            app_label = "scope_app"

    with pytest.raises(OperationalError, match="already exists"), connection.schema_editor() as editor:
        editor.create_model(Entry)
        editor._remake_table(Entry)
    assert connection.connection.execute("SELECT id FROM new__scope_app_entry").fetchall() == [(7,)]
    assert "scope_app_entry" not in connection.introspection.table_names()
    assert_restored(connection)
