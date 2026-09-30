"""Exercise editor decisions and failure boundaries on disposable databases."""

import sqlite3
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
