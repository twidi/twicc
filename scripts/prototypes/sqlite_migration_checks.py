"""Throwaway migration-check experiments. Never import the TwiCC application.

Run with a Python environment that already contains Django and orjson.
All databases are in memory. These classes are NOT a production backend.
"""

import sqlite3
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from statistics import median
from collections import defaultdict
from contextlib import contextmanager
from time import perf_counter

import django
import orjson
from django.conf import settings

settings.configure(
    INSTALLED_APPS=[],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    SECRET_KEY="isolated-prototype",
)
django.setup()

from django.db import DatabaseError, NotSupportedError, connection, migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.backends.sqlite3.schema import DatabaseSchemaEditor
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState


ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def identifier(value):
    # SQLite identifiers fold ASCII letters; Unicode casefold is not equivalent.
    return value.translate(ASCII_LOWER)


class EnforcedDataEditor(DatabaseSchemaEditor):
    """Probe an already-classified data-only migration with enforcement on."""

    def __enter__(self):
        if getattr(self.connection, "_prototype_editor_owner", None) is not None or self.connection.in_atomic_block:
            raise NotSupportedError("Nested prototype schema editors are unsupported")
        assert self.connection.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        return BaseDatabaseSchemaEditor.__enter__(self)

    def __exit__(self, exc_type, exc_value, traceback):
        return BaseDatabaseSchemaEditor.__exit__(self, exc_type, exc_value, traceback)


class ObservedEditor(DatabaseSchemaEditor):
    """Probe column-aware write observation while Django disables enforcement.

    Unknown schema operations use a full check. This deliberately does not
    classify rebuilds or attempt to infer the rows changed by an operation.
    """

    def __enter__(self):
        if getattr(self.connection, "_prototype_editor_owner", None) is not None or self.connection.in_atomic_block:
            raise NotSupportedError("Nested prototype schema editors are unsupported")
        self.writes = set()
        self.unknown = set()
        self.known_schema = 0
        self.deferred_known = set()
        self.created = set()
        self.deleted = set()
        self.unique_indexes = {}
        if self.connection.connection.execute("PRAGMA writable_schema").fetchone()[0]:
            raise NotSupportedError("Writable schema mode is unsupported by this prototype")
        self.before = self.metadata()
        result = super().__enter__()
        self.connection.connection.set_authorizer(self.observe)
        self.connection._prototype_editor_owner = self
        return result

    def metadata(self):
        result = {}
        native = self.connection.connection
        tables = native.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table,) in tables:
            quoted = self.quote_name(table)
            keys = native.execute(f"PRAGMA table_xinfo({quoted})").fetchall()
            primary = tuple(row[1] for row in sorted(keys, key=lambda row: row[5]) if row[5])
            generated = {identifier(row[1]) for row in keys if row[6] in (2, 3)}
            unique_columns = {identifier(column) for column in primary}
            unknown_unique_inputs = False
            for index in native.execute(f"PRAGMA index_list({quoted})").fetchall():
                self.unique_indexes[index[1]] = bool(index[2])
                if index[2]:
                    index_keys = native.execute(f"PRAGMA index_xinfo({self.quote_name(index[1])})").fetchall()
                    columns = {identifier(row[2]) for row in index_keys if row[5] and row[2] is not None}
                    unique_columns.update(columns)
                    unknown_unique_inputs |= bool(index[4] or columns & generated
                                                  or any(row[5] and row[1] < 0 for row in index_keys))
            result[table] = (primary, native.execute(f"PRAGMA foreign_key_list({quoted})").fetchall(),
                             generated, unique_columns, unknown_unique_inputs)
        return result

    def observe(self, action, arg1, arg2, database, trigger):
        # No SQL inside this callback. SQLite forbids reentrant execution here.
        writes = (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_UPDATE)
        schema_actions = (
            sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_CREATE_VTABLE,
            sqlite3.SQLITE_DROP_VTABLE, sqlite3.SQLITE_CREATE_TEMP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
            sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
            sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
            sqlite3.SQLITE_DROP_TEMP_VIEW,
        )
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            self.unknown.add("unsupported connection attachment change")
            return sqlite3.SQLITE_DENY
        affected_database = arg1 if action == sqlite3.SQLITE_ALTER_TABLE else database
        # Main ALTER TABLE can update SQLite's temp catalog internally even
        # with no user temp tables. Direct writable_schema mode is rejected.
        internal_temp_catalog = (database == "temp" and arg1 in ("sqlite_temp_master", "sqlite_temp_schema")
                                 and action in writes)
        if affected_database not in (None, "main") and action in writes + schema_actions and not internal_temp_catalog:
            self.unknown.add("unsupported non-main mutation")
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_UPDATE):
            if arg1 not in ("sqlite_master", "sqlite_schema", "sqlite_sequence", "sqlite_temp_master", "sqlite_temp_schema"):
                self.writes.add((action, arg1, arg2, trigger))
            elif not self.known_schema:
                self.unknown.add("unclassified schema write")
        elif action in schema_actions:
            if not self.known_schema:
                self.unknown.add("unclassified schema operation")
        elif action == sqlite3.SQLITE_PRAGMA:
            if arg1.lower() == "writable_schema" and arg2 is not None:
                self.unknown.add("unsupported writable_schema mutation")
                return sqlite3.SQLITE_DENY
            # A conservative probe policy. Read-only PRAGMAs are also unknown.
            self.unknown.add("migration PRAGMA")
        return sqlite3.SQLITE_OK

    @contextmanager
    def known(self):
        self.known_schema += 1
        before = {id(sql) for sql in self.deferred_sql}
        try:
            yield
        finally:
            self.deferred_known.update(id(sql) for sql in self.deferred_sql if id(sql) not in before)
            self.known_schema -= 1

    def create_model(self, model):
        self.created.add(model._meta.db_table)
        with self.known():
            return super().create_model(model)

    def add_index(self, model, index):
        if type(index) is not models.Index:
            self.unknown.add("custom index")
        with self.known():
            return super().add_index(model, index)

    def remove_index(self, model, index):
        if self.unique_indexes.get(index.name, True):
            self.unknown.add("unique or unknown removed index")
        with self.known():
            return super().remove_index(model, index)

    def delete_model(self, model, handle_autom2m=True):
        self.deleted.add(model._meta.db_table)
        with self.known():
            return super().delete_model(model, handle_autom2m=handle_autom2m)

    def _remake_table(self, *args, **kwargs):
        self.unknown.add("table rebuild is not classified by this prototype")
        return super()._remake_table(*args, **kwargs)

    def scope(self, after):
        incoming = defaultdict(list)
        names = {identifier(table): table for table in after}
        for child, (primary, foreign_keys, generated, unique_columns, unknown_unique_inputs) in after.items():
            for row in foreign_keys:
                # FK columns: id, seq, table, from, to, on_update, on_delete, match.
                parent = identifier(row[2])
                parent_columns = (row[4],) if row[4] else after.get(names.get(parent), ((), [], set(), set(), False))[0]
                parent_columns = tuple(identifier(column) for column in parent_columns)
                incoming[parent].append((child, parent_columns))
        checked = set()
        for parent in self.created:
            checked.update(child for child, columns in incoming[identifier(parent)])
        for parent in self.deleted:
            checked.update(child for child, columns in incoming[identifier(parent)])
        for action, table, column, trigger in self.writes:
            normalized_table = identifier(table)
            column = identifier(column) if column is not None else None
            if normalized_table not in names:
                if table in self.deleted:
                    continue
                self.unknown.add("write to missing table")
                continue
            table = names[normalized_table]
            primary, foreign_keys, generated, unique_columns, unknown_unique_inputs = after[table]
            outgoing_columns = {identifier(row[3]) for row in foreign_keys}
            if action == sqlite3.SQLITE_INSERT and foreign_keys:
                checked.add(table)
            rowid_update = column in ("rowid", "_rowid_", "oid")
            if action == sqlite3.SQLITE_UPDATE and (column in outgoing_columns or outgoing_columns & generated
                                                   or rowid_update and foreign_keys):
                checked.add(table)
            for child, parent_columns in incoming[normalized_table]:
                # INSERT OR REPLACE can remove a referenced parent implicitly.
                # The authorizer need not report that implicit DELETE.
                if (action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE) or column in parent_columns
                        or set(parent_columns) & generated or rowid_update
                        or action == sqlite3.SQLITE_UPDATE and (column in unique_columns or unknown_unique_inputs)):
                    checked.add(child)
        return sorted(checked)

    def __exit__(self, exc_type, exc_value, traceback):
        original_failure = exc_type is not None
        try:
            if not original_failure:
                for sql in self.deferred_sql:
                    if id(sql) in self.deferred_known:
                        with self.known():
                            self.execute(sql, None)
                    else:
                        self.execute(sql, None)
                self.deferred_sql = []
        except BaseException:
            exc_type, exc_value, traceback = sys.exc_info()
        finally:
            self.connection.connection.set_authorizer(None)
        try:
            if exc_type is None:
                after = self.metadata()
                tables = self.scope(after)
                if self.unknown:
                    self.connection.check_constraints()
                elif tables:
                    self.connection.check_constraints(table_names=tables)
        except BaseException:
            exc_type, exc_value, traceback = sys.exc_info()
        finally:
            try:
                BaseDatabaseSchemaEditor.__exit__(self, exc_type, exc_value, traceback)
            finally:
                try:
                    self.connection.enable_constraint_checking()
                finally:
                    self.connection._prototype_editor_owner = None
        if exc_type is not None and not original_failure:
            raise exc_value.with_traceback(traceback)


SETUP = migrations.Migration("setup", "probe")
SETUP.operations = [
    migrations.CreateModel(
        name="Parent",
        fields=[("id", models.IntegerField(primary_key=True)), ("label", models.TextField(default=""))],
    ),
    migrations.CreateModel(
        name="Child",
        fields=[
            ("id", models.IntegerField(primary_key=True)),
            ("parent", models.ForeignKey("probe.Parent", on_delete=models.CASCADE)),
            ("label", models.TextField(default="")),
        ],
    ),
]


def reset():
    # In-memory Django close() is a no-op. Close the driver explicitly.
    if connection.connection is not None:
        connection.connection.close()
        connection.connection = None
    connection.SchemaEditorClass = DatabaseSchemaEditor
    connection.ensure_connection()
    assert connection.settings_dict["NAME"] == ":memory:"
    with connection.schema_editor() as editor:
        state = SETUP.apply(ProjectState(), editor)
    Parent = state.apps.get_model("probe", "Parent")
    Child = state.apps.get_model("probe", "Child")
    Parent.objects.create(id=1, label="parent")
    Child.objects.create(id=1, parent_id=1, label="original")
    executor = MigrationExecutor(connection)
    executor.recorder.ensure_schema()
    return state, executor, Parent, Child


def update_label(apps, editor):
    apps.get_model("probe", "Child").objects.update(label="changed")


def invalid_fk(apps, editor):
    apps.get_model("probe", "Child").objects.update(parent_id=999)


def delete_parent(apps, editor):
    # Use SQL; the ORM's deletion collector intentionally handles child rows.
    editor.connection.cursor().execute('DELETE FROM "probe_parent" WHERE id = 1')


def native_invalid_fk(apps, editor):
    editor.connection.connection.execute('UPDATE "probe_child" SET parent_id=999 WHERE id=1')


def mixed_write(apps, editor):
    update_label(apps, editor)
    invalid_fk(apps, editor)


def operation_error(apps, editor):
    update_label(apps, editor)
    raise ValueError("deliberate probe failure")


def run_case(name, editor_class, operations, *, invalid=False, expected_checks=None, prepare=None, reverse=False):
    state, executor, Parent, Child = reset()
    if prepare:
        prepare(connection)
    statements = []
    connection.connection.set_trace_callback(statements.append)
    connection.SchemaEditorClass = editor_class
    migration = migrations.Migration(name, "probe")
    migration.operations = operations
    if reverse:
        executor.apply_migration(state.clone(), migration)
        statements.clear()
    started = perf_counter()
    failure = None
    try:
        if reverse:
            executor.unapply_migration(state.clone(), migration)
        else:
            executor.apply_migration(state, migration)
    except (DatabaseError, ValueError) as error:
        failure = type(error).__name__
    duration = perf_counter() - started
    connection.connection.set_trace_callback(None)
    checks = [sql for sql in statements if "foreign_key_check" in sql.lower()]
    foreign_keys = connection.connection.execute("PRAGMA foreign_keys").fetchone()[0]
    label = Child.objects.get(id=1).label
    recorded = executor.recorder.migration_qs.filter(app="probe", name=name).exists()
    assert bool(failure) == invalid, (name, failure)
    assert foreign_keys == 1, (name, foreign_keys)
    assert not connection.in_atomic_block, name
    assert connection.get_autocommit(), name
    assert recorded == (invalid if reverse else not invalid), (name, recorded)
    if invalid:
        assert label == "original", (name, label)
        assert Child.objects.get(id=1).parent_id == 1
        assert Parent.objects.filter(id=1).exists()
        assert connection.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    if expected_checks is not None:
        assert checks == expected_checks, (name, checks)
    return {"case": name, "editor": editor_class.__name__, "checks": checks,
            "failure": failure, "seconds": round(duration, 6), "foreign_keys_restored": foreign_keys == 1}


def trigger(connection):
    connection.connection.execute('''CREATE TRIGGER invalidate_child AFTER UPDATE OF label ON probe_parent
        BEGIN UPDATE probe_child SET parent_id=999 WHERE id=1; END''')


def cached_update(connection):
    # Compile and execute before installing the authorizer.
    connection.connection.execute('UPDATE "probe_child" SET label=? WHERE id=1', ("original",))


def cached_fk_update(connection):
    connection.connection.execute('UPDATE "probe_child" SET parent_id=? WHERE id=1', (1,))


def repeat_cached_fk_update(apps, editor):
    editor.connection.connection.execute('UPDATE "probe_child" SET parent_id=? WHERE id=1', (999,))


def unique_reference(connection):
    connection.connection.execute('CREATE TABLE unique_parent(id INTEGER PRIMARY KEY, code TEXT UNIQUE)')
    connection.connection.execute('CREATE TABLE unique_child(id INTEGER PRIMARY KEY, code TEXT REFERENCES unique_parent(code))')
    connection.connection.execute("INSERT INTO unique_parent VALUES (1, 'old')")
    connection.connection.execute("INSERT INTO unique_child VALUES (1, 'old')")


def generated_reference(connection):
    connection.connection.execute('''CREATE TABLE generated_parent(id INTEGER PRIMARY KEY, source TEXT,
        code TEXT GENERATED ALWAYS AS (source) STORED UNIQUE)''')
    connection.connection.execute('CREATE TABLE generated_child(id INTEGER PRIMARY KEY, code TEXT REFERENCES generated_parent(code))')
    connection.connection.execute("INSERT INTO generated_parent(id, source) VALUES (1, 'old')")
    connection.connection.execute("INSERT INTO generated_child VALUES (1, 'old')")


def uppercase_reference(connection):
    connection.connection.execute("INSERT INTO probe_parent VALUES (2, 'second')")
    connection.connection.execute('CREATE TABLE uppercase_child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES PROBE_PARENT(ID))')
    connection.connection.execute('INSERT INTO uppercase_child VALUES (1, 2)')


def ordinary_unique_parent(connection):
    connection.connection.execute('CREATE UNIQUE INDEX parent_label_unique ON probe_parent(label)')
    connection.connection.execute("INSERT INTO probe_parent VALUES (2, 'second')")


def schema_conflict_parent(connection):
    connection.connection.execute('CREATE TABLE replace_parent(id INTEGER PRIMARY KEY,label TEXT UNIQUE ON CONFLICT REPLACE)')
    connection.connection.execute('CREATE TABLE replace_child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES replace_parent(id))')
    connection.connection.execute("INSERT INTO replace_parent VALUES(1,'first'),(2,'second')")
    connection.connection.execute('INSERT INTO replace_child VALUES(1,1)')


def partial_unique_parent(connection):
    connection.connection.execute('ALTER TABLE probe_parent ADD COLUMN active INTEGER NOT NULL DEFAULT 0')
    connection.connection.execute('UPDATE probe_parent SET active=1 WHERE id=1')
    connection.connection.execute("INSERT INTO probe_parent VALUES(2,'parent',0)")
    connection.connection.execute('CREATE UNIQUE INDEX parent_label_partial ON probe_parent(label) WHERE active=1')


def expression_unique_parent(connection):
    connection.connection.execute('CREATE UNIQUE INDEX parent_label_expression ON probe_parent(lower(label))')
    connection.connection.execute("INSERT INTO probe_parent VALUES(2,'second')")


def generated_unique_parent(connection):
    connection.connection.execute('CREATE TABLE generated_replace_parent(id INTEGER PRIMARY KEY,source TEXT,code TEXT GENERATED ALWAYS AS(source) STORED UNIQUE)')
    connection.connection.execute('CREATE TABLE generated_replace_child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES generated_replace_parent(id))')
    connection.connection.execute("INSERT INTO generated_replace_parent(id,source) VALUES(1,'first'),(2,'second')")
    connection.connection.execute('INSERT INTO generated_replace_child VALUES(1,1)')


def attached_schema(connection):
    connection.connection.execute("ATTACH ':memory:' AS aux")
    connection.connection.execute('CREATE TABLE aux.parent(id INTEGER PRIMARY KEY)')
    connection.connection.execute('CREATE TABLE aux.child(id INTEGER PRIMARY KEY,parent_id REFERENCES parent(id))')
    connection.connection.execute('INSERT INTO aux.parent VALUES(1)')
    connection.connection.execute('INSERT INTO aux.child VALUES(1,1)')


def temp_schema(connection):
    connection.connection.execute('CREATE TEMP TABLE parent(id INTEGER PRIMARY KEY)')
    connection.connection.execute('CREATE TEMP TABLE child(id INTEGER PRIMARY KEY,parent_id REFERENCES parent(id))')
    connection.connection.execute('INSERT INTO temp.parent VALUES(1)')
    connection.connection.execute('INSERT INTO temp.child VALUES(1,1)')


def nested_editor(apps, editor):
    assert editor.connection._prototype_editor_owner is editor
    try:
        with editor.connection.schema_editor():
            raise AssertionError("Nested editor must be rejected on entry")
    except NotSupportedError:
        pass
    assert editor.connection._prototype_editor_owner is editor
    assert editor.connection.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    # The read-only PRAGMA conservatively requests a global check in this probe.
    invalid_fk(apps, editor)


def native_many_invalid(apps, editor):
    editor.connection.connection.executemany('UPDATE "probe_child" SET parent_id=? WHERE id=1', [(1,), (999,)])


def repeat_cached_update(apps, editor):
    editor.connection.connection.execute('UPDATE "probe_child" SET label=? WHERE id=1', ("changed",))


def main():
    data = lambda callback: [migrations.RunPython(callback, migrations.RunPython.noop)]
    child_check = ['PRAGMA foreign_key_check("probe_child")']
    results = [
        run_case("observed_no_operation", ObservedEditor, [], expected_checks=[]),
        run_case("standard_data_update", DatabaseSchemaEditor, data(update_label),
                 expected_checks=["PRAGMA foreign_key_check"]),
        run_case("enforced_data_update", EnforcedDataEditor, data(update_label), expected_checks=[]),
        run_case("enforced_invalid_fk", EnforcedDataEditor, data(invalid_fk), invalid=True, expected_checks=[]),
        run_case("observed_data_update", ObservedEditor, data(update_label), expected_checks=[]),
        run_case("observed_runsql_update", ObservedEditor,
                 [migrations.RunSQL('UPDATE "probe_child" SET label=\'changed\' WHERE id=1')], expected_checks=[]),
        run_case("observed_invalid_fk", ObservedEditor, data(invalid_fk), invalid=True, expected_checks=child_check),
        run_case("observed_native_invalid_fk", ObservedEditor, data(native_invalid_fk),
                 invalid=True, expected_checks=child_check),
        run_case("observed_delete_parent", ObservedEditor, data(delete_parent), invalid=True, expected_checks=child_check),
        run_case("observed_operation_error", ObservedEditor, data(operation_error), invalid=True, expected_checks=[]),
        run_case("observed_trigger", ObservedEditor,
                 [migrations.RunSQL('UPDATE "probe_parent" SET label=\'changed\' WHERE id=1')],
                 prepare=trigger, invalid=True, expected_checks=child_check),
        run_case("observed_cached_statement", ObservedEditor, data(repeat_cached_update),
                 prepare=cached_update, expected_checks=[]),
        run_case("observed_cached_invalid_statement", ObservedEditor, data(repeat_cached_fk_update),
                 prepare=cached_fk_update, invalid=True, expected_checks=child_check),
        run_case("observed_native_executemany", ObservedEditor, data(native_many_invalid),
                 invalid=True, expected_checks=child_check),
        run_case("observed_unique_replace", ObservedEditor,
                 [migrations.RunSQL("INSERT OR REPLACE INTO unique_parent VALUES (1, 'new')")],
                 prepare=unique_reference, invalid=True,
                 expected_checks=['PRAGMA foreign_key_check("unique_child")']),
        run_case("observed_update_replace_unique", ObservedEditor,
                 [migrations.RunSQL("UPDATE OR REPLACE probe_parent SET label='parent' WHERE id=2")],
                 prepare=ordinary_unique_parent, invalid=True, expected_checks=child_check),
        run_case("observed_schema_conflict_replace", ObservedEditor,
                 [migrations.RunSQL("UPDATE replace_parent SET label='first' WHERE id=2")],
                 prepare=schema_conflict_parent, invalid=True,
                 expected_checks=['PRAGMA foreign_key_check("replace_child")']),
        run_case("observed_partial_unique_replace", ObservedEditor,
                 [migrations.RunSQL("UPDATE OR REPLACE probe_parent SET active=1 WHERE id=2")],
                 prepare=partial_unique_parent, invalid=True, expected_checks=child_check),
        run_case("observed_expression_unique_replace", ObservedEditor,
                 [migrations.RunSQL("UPDATE OR REPLACE probe_parent SET label='PARENT' WHERE id=2")],
                 prepare=expression_unique_parent, invalid=True, expected_checks=child_check),
        run_case("observed_generated_unique_replace", ObservedEditor,
                 [migrations.RunSQL("UPDATE OR REPLACE generated_replace_parent SET source='first' WHERE id=2")],
                 prepare=generated_unique_parent, invalid=True,
                 expected_checks=['PRAGMA foreign_key_check("generated_replace_child")']),
        run_case("observed_attached_write_rejected", ObservedEditor,
                 [migrations.RunSQL('UPDATE aux.child SET parent_id=999')],
                 prepare=attached_schema, invalid=True, expected_checks=[]),
        run_case("observed_temp_write_rejected", ObservedEditor,
                 [migrations.RunSQL('UPDATE temp.child SET parent_id=999')],
                 prepare=temp_schema, invalid=True, expected_checks=[]),
        run_case("observed_attach_rejected", ObservedEditor,
                 [migrations.RunSQL("ATTACH ':memory:' AS another")], invalid=True, expected_checks=[]),
        run_case("observed_temp_ddl_rejected", ObservedEditor,
                 [migrations.RunSQL('CREATE TEMP TABLE additional(id INTEGER PRIMARY KEY)')],
                 invalid=True, expected_checks=[]),
        run_case("observed_nested_editor_preserves_outer", ObservedEditor,
                 data(nested_editor), invalid=True, expected_checks=['PRAGMA foreign_key_check']),
        run_case("observed_generated_parent_key", ObservedEditor,
                 [migrations.RunSQL("UPDATE generated_parent SET source='new' WHERE id=1")],
                 prepare=generated_reference, invalid=True,
                 expected_checks=['PRAGMA foreign_key_check("generated_child")']),
        run_case("observed_uppercase_parent_reference", ObservedEditor,
                 [migrations.RunSQL("DELETE FROM probe_parent WHERE id=2")],
                 prepare=uppercase_reference, invalid=True,
                 expected_checks=['PRAGMA foreign_key_check("probe_child")', 'PRAGMA foreign_key_check("uppercase_child")']),
        run_case("observed_mixed_data", ObservedEditor, data(mixed_write), invalid=True, expected_checks=child_check),
        run_case("observed_backward_label", ObservedEditor,
                 [migrations.RunPython(migrations.RunPython.noop, update_label)], reverse=True, expected_checks=[]),
        run_case("observed_backward_invalid_fk", ObservedEditor,
                 [migrations.RunPython(migrations.RunPython.noop, invalid_fk)],
                 reverse=True, invalid=True, expected_checks=child_check),
        run_case("observed_empty_table", ObservedEditor,
                 [migrations.CreateModel(name="Extra", fields=[("id", models.IntegerField(primary_key=True))])],
                 expected_checks=[]),
        run_case("observed_empty_fk_table", ObservedEditor,
                 [migrations.CreateModel(name="Extra", fields=[
                     ("id", models.IntegerField(primary_key=True)),
                     ("parent", models.ForeignKey("probe.Parent", on_delete=models.CASCADE)),
                 ])], expected_checks=[]),
        run_case("observed_schema_then_invalid_data", ObservedEditor,
                 [migrations.CreateModel(name="Extra", fields=[("id", models.IntegerField(primary_key=True))]),
                  migrations.RunPython(invalid_fk, migrations.RunPython.noop)],
                 invalid=True, expected_checks=child_check),
        run_case("observed_index", ObservedEditor,
                 [migrations.AddIndex(model_name="child", index=models.Index(fields=["label"], name="label_index"))],
                 expected_checks=[]),
        run_case("enforced_parent_rebuild", EnforcedDataEditor,
                 [migrations.AddField(model_name="parent", name="extra", field=models.TextField(default=""))],
                 invalid=True, expected_checks=[]),
        run_case("observed_parent_rebuild", ObservedEditor,
                 [migrations.AddField(model_name="parent", name="extra", field=models.TextField(default=""))],
                 expected_checks=["PRAGMA foreign_key_check"]),
        run_case("observed_unknown_schema", ObservedEditor,
                 [migrations.RunSQL('CREATE TABLE raw_extra(id INTEGER PRIMARY KEY)')],
                 expected_checks=["PRAGMA foreign_key_check"]),
    ]
    print(orjson.dumps({"django": django.get_version(), "sqlite": sqlite3.sqlite_version,
                       "results": results, "upgrade_replay": replay_upgrade(),
                       "synthetic_benchmark": benchmark()}, option=orjson.OPT_INDENT_2).decode())


def load_migration(filename):
    path = Path(__file__).resolve().parents[2] / "src/twicc/core/migrations" / filename
    spec = spec_from_file_location("probe_" + path.stem, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Migration(path.stem, "core")


def replay_upgrade():
    reset()
    # A minimal historical schema is sufficient for these two real migrations.
    setup = migrations.Migration("minimal_core", "core")
    setup.operations = [
        migrations.CreateModel(name="Session", fields=[
            ("id", models.CharField(primary_key=True, max_length=255)),
            ("parent_session", models.ForeignKey("core.Session", null=True, on_delete=models.SET_NULL)),
            ("self_cost", models.DecimalField(null=True, max_digits=20, decimal_places=10)),
        ]),
        migrations.CreateModel(name="SessionItem", fields=[
            ("id", models.IntegerField(primary_key=True)),
            ("session", models.ForeignKey("core.Session", on_delete=models.CASCADE)),
            ("content", models.TextField()),
        ]),
    ]
    with connection.schema_editor() as editor:
        base = setup.apply(ProjectState(), editor)
    first = load_migration("0147_session_history_fact.py")
    squash = load_migration("0148_live_contribution_indexes_squashed_0149_remove_redundant_message_index.py")
    executor = MigrationExecutor(connection)
    connection.SchemaEditorClass = ObservedEditor
    statements = []
    connection.connection.set_trace_callback(statements.append)
    after_first = executor.apply_migration(base.clone(), first)
    after_squash = executor.apply_migration(after_first.clone(), squash)
    executor.unapply_migration(after_first.clone(), squash)
    executor.unapply_migration(base.clone(), first)
    connection.connection.set_trace_callback(None)
    checks = [sql for sql in statements if "foreign_key_check" in sql.lower()]
    assert checks == [], checks
    assert not executor.recorder.migration_qs.filter(app="core").exists()
    assert not connection.connection.execute("SELECT 1 FROM sqlite_master WHERE name='core_sessionhistoryfact'").fetchall()
    assert connection.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    return {"migrations": [first.name, squash.name], "forward_and_backward_checks": checks,
            "migration_records_removed": True, "schema_restored": True}


def benchmark():
    results = []
    for rows, payload_bytes in [(10_000, 256), (100_000, 256), (100_000, 4096)]:
        def seed(connection):
            payload = "x" * payload_bytes
            connection.connection.executemany('INSERT INTO probe_child(id,parent_id,label) VALUES (?,?,?)',
                                              ((row + 2, 1, payload) for row in range(rows - 1)))

        def update_one(apps, editor):
            apps.get_model("probe", "Child").objects.filter(id=1).update(label="changed")

        samples = {}
        for editor_class in (DatabaseSchemaEditor, ObservedEditor):
            durations = []
            for repetition in range(3):
                result = run_case("benchmark", editor_class,
                                  [migrations.RunPython(update_one, migrations.RunPython.noop)], prepare=seed,
                                  expected_checks=["PRAGMA foreign_key_check"] if editor_class is DatabaseSchemaEditor else [])
                durations.append(result["seconds"])
            samples[editor_class.__name__] = {"seconds": durations, "median_seconds": median(durations)}
        results.append({"child_rows": rows, "payload_bytes_per_extra_row": payload_bytes, "samples": samples})
    return {"storage": "in-memory", "repetitions": 3, "seed_time_excluded": True,
            "note": "Not a cold-disk or production-startup benchmark", "cases": results}


if __name__ == "__main__":
    main()
