"""Authorizer effects select child checks and reject unsupported mutations."""

import sqlite3
from contextlib import closing

import pytest

from twicc.db.backends.sqlite3.effects import EffectObserver
from twicc.db.backends.sqlite3.metadata import read_schema


@pytest.fixture
def connection():
    connection = sqlite3.connect(":memory:")
    connection.executescript('''
        CREATE TABLE parent (id INTEGER PRIMARY KEY, code TEXT UNIQUE, ordinary TEXT);
        CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES PARENT(ID), ordinary TEXT);
        CREATE TABLE code_child (code TEXT REFERENCES parent(CODE));
        CREATE TABLE unrelated (id INTEGER PRIMARY KEY, ordinary TEXT);
        INSERT INTO parent VALUES (1, 'one', 'value'), (2, 'two', 'value');
        INSERT INTO child VALUES (1, 1, 'value');
        INSERT INTO code_child VALUES ('one');
    ''')
    yield connection
    connection.close()


def observe_sql(connection, sql, parameters=()):
    before = read_schema(connection)
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    try:
        connection.execute(sql, parameters)
        after = read_schema(connection)
    finally:
        connection.set_authorizer(None)
    return observer, observer.decision(before, after)


@pytest.mark.parametrize(("sql", "scope", "tables"), [
    ("UPDATE child SET ordinary = 'changed'", "none", ()),
    ("UPDATE parent SET ordinary = 'changed'", "none", ()),
    ("UPDATE unrelated SET ordinary = 'changed'", "none", ()),
    ("UPDATE child SET parent_id = 999", "tables", ("child",)),
    ("INSERT INTO child VALUES (2, 999, 'value')", "tables", ("child",)),
    ("UPDATE parent SET id = 999 WHERE id = 1", "tables", ("child", "code_child")),
    ("UPDATE parent SET code = 'new' WHERE id = 1", "tables", ("child", "code_child")),
    ("DELETE FROM parent WHERE id = 1", "tables", ("child", "code_child")),
    ("INSERT INTO parent VALUES (3, 'three', 'value')", "tables", ("child", "code_child")),
    ("DELETE FROM child", "none", ()),
    ("UPDATE parent SET rowid = 999 WHERE id = 1", "tables", ("child", "code_child")),
    ("UPDATE parent SET _rowid_ = 999 WHERE id = 1", "tables", ("child", "code_child")),
    ("UPDATE parent SET oid = 999 WHERE id = 1", "tables", ("child", "code_child")),
])
def test_data_scope_uses_foreign_keys_and_unique_conflict_inputs(connection, sql, scope, tables):
    _, decision = observe_sql(connection, sql)
    assert decision.scope == scope
    assert decision.tables == frozenset(tables)


@pytest.mark.parametrize("sql", [
    "INSERT OR REPLACE INTO parent VALUES (2, 'one', 'value')",
    "UPDATE OR REPLACE parent SET code = 'one' WHERE id = 2",
])
def test_replacement_deletion_is_selected_without_delete_authorization(connection, sql):
    observer, decision = observe_sql(connection, sql)
    assert not any(effect.action == sqlite3.SQLITE_DELETE and effect.table == "parent" for effect in observer.effects)
    assert decision.tables == frozenset({"child", "code_child"})
    assert connection.execute("PRAGMA foreign_key_check(child)").fetchall()


def test_schema_conflict_replace_checks_all_incoming_children(connection):
    connection.executescript('''
        CREATE TABLE replace_parent (id INTEGER PRIMARY KEY, code TEXT UNIQUE ON CONFLICT REPLACE);
        CREATE TABLE replace_child (key INTEGER REFERENCES replace_parent(id));
        INSERT INTO replace_parent VALUES (1, 'one'), (2, 'two');
        INSERT INTO replace_child VALUES (1);
    ''')
    _, decision = observe_sql(connection, "UPDATE replace_parent SET code = 'one' WHERE id = 2")
    assert decision.tables == frozenset({"replace_child"})
    assert connection.execute("PRAGMA foreign_key_check(replace_child)").fetchall()


@pytest.mark.parametrize("index_sql", [
    "CREATE UNIQUE INDEX uncertain ON parent(code) WHERE ordinary IS NOT NULL",
    "CREATE UNIQUE INDEX uncertain ON parent(lower(ordinary))",
    "CREATE UNIQUE INDEX uncertain ON parent(generated)",
])
def test_unknown_unique_dependencies_check_all_incoming_children(connection, index_sql):
    connection.execute("ALTER TABLE parent ADD COLUMN generated TEXT GENERATED ALWAYS AS (ordinary) VIRTUAL")
    connection.execute("UPDATE parent SET ordinary = code")
    connection.execute(index_sql)
    _, decision = observe_sql(connection, "UPDATE parent SET ordinary = 'changed' WHERE id = 1")
    assert decision.scope == "tables"
    assert decision.tables == frozenset({"child", "code_child"})


@pytest.mark.parametrize(("ddl", "sql", "tables"), [
    ('''CREATE TABLE generated_child (
        input INTEGER, key INTEGER GENERATED ALWAYS AS (input) REFERENCES parent(id)
    ); INSERT INTO generated_child(input) VALUES (1)''',
     "UPDATE generated_child SET input = 999", {"generated_child"}),
    ('''CREATE TABLE generated_parent (
        input TEXT, key TEXT GENERATED ALWAYS AS (input) UNIQUE
    ); CREATE TABLE generated_reference (key TEXT REFERENCES generated_parent(key));
    INSERT INTO generated_parent(input) VALUES ('one');
    INSERT INTO generated_reference VALUES ('one')''',
     "UPDATE generated_parent SET input = 'changed'", {"generated_reference"}),
])
def test_generated_foreign_keys_and_parent_keys_check_input_updates(connection, ddl, sql, tables):
    connection.executescript(ddl)
    _, decision = observe_sql(connection, sql)
    assert decision.tables == frozenset(tables)


def test_native_cursor_and_executemany_observe_each_prepared_write(connection):
    before = read_schema(connection)
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    connection.cursor().executemany("UPDATE child SET parent_id = ? WHERE id = ?", [(900, 1), (901, 1)])
    connection.set_authorizer(None)
    assert observer.decision(before, before).tables == frozenset({"child"})
    assert connection.execute("SELECT parent_id FROM child").fetchone() == (901,)


def test_authorizer_installation_invalidates_cached_statements(connection):
    sql = "UPDATE child SET parent_id = ? WHERE id = 1"
    connection.execute(sql, (1,))
    _, decision = observe_sql(connection, sql, (999,))
    assert decision.tables == frozenset({"child"})


def test_trigger_writes_remain_visible_with_source(connection):
    connection.execute('''CREATE TRIGGER invalidate AFTER UPDATE ON unrelated
        BEGIN UPDATE child SET parent_id = 999; END''')
    connection.execute("INSERT INTO unrelated VALUES (1, 'value')")
    observer, decision = observe_sql(connection, "UPDATE unrelated SET ordinary = 'changed'")
    assert decision.tables == frozenset({"child"})
    assert any(effect.table == "child" and effect.source == "invalidate" for effect in observer.effects)


def test_unknown_actions_fall_back_to_main_global_check():
    observer = EffectObserver()
    assert observer.observe(987654, None, None, None, None) == sqlite3.SQLITE_OK
    decision = observer.decision({}, {})
    assert decision.scope == "global"
    assert decision.reasons


def test_unknown_attached_action_is_not_proven_readonly():
    observer = EffectObserver()
    assert observer.observe(987654, "target", None, "other", None) == sqlite3.SQLITE_DENY
    assert observer.decision({}, {}).scope == "global"


def test_callback_errors_do_not_escape_and_force_global_scope():
    observer = EffectObserver()
    assert observer.observe(sqlite3.SQLITE_UPDATE, None, "id", "main", None) == sqlite3.SQLITE_DENY
    assert observer.decision({}, {}).scope == "global"


@pytest.mark.parametrize("sql", [
    "CREATE TABLE other.new_table (id INTEGER)",
    "CREATE TEMP TABLE new_table (id INTEGER)",
    "CREATE INDEX other.new_index ON target(id)",
    "CREATE INDEX temp.new_index ON target(id)",
    "CREATE VIEW other.new_view AS SELECT * FROM target",
    "CREATE TEMP VIEW new_view AS SELECT * FROM target",
    "CREATE TRIGGER other.new_trigger AFTER INSERT ON target BEGIN SELECT 1; END",
    "CREATE TEMP TRIGGER new_trigger AFTER INSERT ON target BEGIN SELECT 1; END",
    "ALTER TABLE other.target ADD COLUMN extra INTEGER",
    "ALTER TABLE temp.target ADD COLUMN extra INTEGER",
    "INSERT INTO other.target VALUES (2)",
    "INSERT INTO temp.target VALUES (2)",
    "UPDATE other.target SET id = 2",
    "UPDATE temp.target SET id = 2",
    "DELETE FROM other.target",
    "DELETE FROM temp.target",
    "DROP TABLE other.target",
    "DROP TABLE temp.target",
    "DROP INDEX other.target_index",
    "DROP INDEX temp.target_index",
    "DROP VIEW other.target_view",
    "DROP VIEW temp.target_view",
    "DROP TRIGGER other.target_trigger",
    "DROP TRIGGER temp.target_trigger",
    "ATTACH ':memory:' AS new_database",
    "DETACH other",
    "PRAGMA other.user_version = 2",
    "PRAGMA temp.user_version = 2",
])
def test_unsupported_mutations_fail_before_side_effects(connection, sql):
    connection.executescript('''
        ATTACH ':memory:' AS other;
        CREATE TABLE other.target (id INTEGER);
        CREATE TEMP TABLE target (id INTEGER);
        INSERT INTO other.target VALUES (1);
        INSERT INTO temp.target VALUES (1);
        CREATE INDEX other.target_index ON target(id);
        CREATE INDEX temp.target_index ON target(id);
        CREATE VIEW other.target_view AS SELECT * FROM target;
        CREATE TEMP VIEW target_view AS SELECT * FROM target;
        CREATE TRIGGER other.target_trigger AFTER INSERT ON target BEGIN SELECT 1; END;
        CREATE TEMP TRIGGER target_trigger AFTER INSERT ON target BEGIN SELECT 1; END;
    ''')
    before = {
        schema: connection.execute(f"SELECT type, name, sql FROM {schema}.sqlite_schema ORDER BY name").fetchall()
        for schema in ("main", "temp", "other")
    }
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute(sql)
    connection.set_authorizer(None)
    for schema in ("main", "temp", "other"):
        assert connection.execute(f"SELECT type, name, sql FROM {schema}.sqlite_schema ORDER BY name").fetchall() == before[schema]
    assert connection.execute("SELECT * FROM other.target").fetchall() == [(1,)]
    assert connection.execute("SELECT * FROM temp.target").fetchall() == [(1,)]
    assert connection.execute("PRAGMA other.user_version").fetchone() == (0,)
    assert connection.execute("PRAGMA temp.user_version").fetchone() == (0,)
    assert [row[1] for row in connection.execute("PRAGMA database_list")] == ["main", "temp", "other"]


def test_read_only_temp_and_attached_access_is_allowed(connection):
    connection.executescript('''
        ATTACH ':memory:' AS other;
        CREATE TABLE other.target (id INTEGER);
        CREATE TEMP TABLE target (id INTEGER);
    ''')
    before = read_schema(connection)
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    connection.execute("SELECT * FROM other.target JOIN temp.target USING (id)").fetchall()
    connection.execute("PRAGMA other.table_xinfo(target)").fetchall()
    connection.execute("PRAGMA temp.foreign_key_check(target)").fetchall()
    connection.set_authorizer(None)
    assert observer.decision(before, before).scope == "none"


def test_main_alter_allows_internal_temp_catalog_updates(connection):
    observer, decision = observe_sql(connection, "ALTER TABLE parent RENAME COLUMN ordinary TO renamed")
    assert decision.scope == "global"
    assert observer.schema_effects
    assert "renamed" in [row[1] for row in connection.execute("PRAGMA table_xinfo(parent)")]


@pytest.mark.parametrize("value", ["ON", "OFF", "1", "0", "RESET"])
def test_writable_schema_setters_are_rejected(connection, value):
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute(f"PRAGMA writable_schema = {value}")
    connection.set_authorizer(None)
    assert connection.execute("PRAGMA writable_schema").fetchone() == (0,)


def test_readonly_writable_schema_query_is_allowed(connection):
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    assert connection.execute("PRAGMA writable_schema").fetchone() == (0,)
    assert observer.decision({}, {}).scope == "none"


def test_unclassified_main_schema_effects_force_global_check(connection):
    observer, decision = observe_sql(connection, "CREATE TABLE new_table (id INTEGER)")
    assert decision.scope == "global"
    assert any(effect.action == sqlite3.SQLITE_CREATE_TABLE and effect.arg1 == "new_table"
               and effect.database == "main" for effect in observer.schema_effects)


def test_writes_to_surviving_unknown_tables_force_global_check():
    observer = EffectObserver()
    observer.observe(sqlite3.SQLITE_UPDATE, "unclassified", "column", "main", None)
    assert observer.decision({}, {}).scope == "global"


def test_deleted_child_tables_are_not_selected(connection):
    before = read_schema(connection)
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    connection.execute("DELETE FROM parent")
    connection.set_authorizer(None)
    connection.execute("DROP TABLE child")
    after = read_schema(connection)
    assert observer.decision(before, after).tables == frozenset({"code_child"})


def test_schema_proof_cannot_hide_arbitrary_data_writes(connection):
    before = read_schema(connection)
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    connection.execute("CREATE TABLE empty_table (id INTEGER)")
    connection.execute("UPDATE child SET parent_id = 999")
    connection.set_authorizer(None)
    assert observer.decision(before, read_schema(connection)).scope == "global"
    observer.schema_effects.clear()
    assert observer.decision(before, read_schema(connection)).tables == frozenset({"child"})


def test_parent_delete_checks_user_table_with_sqlite_prefix(connection):
    connection.execute("CREATE TABLE sqliteChild (key INTEGER REFERENCES parent(id))")
    connection.execute("INSERT INTO sqliteChild VALUES (1)")
    _, decision = observe_sql(connection, "DELETE FROM parent WHERE id = 1")
    assert decision.tables == frozenset({"child", "code_child", "sqlitechild"})


def test_direct_temp_catalog_write_is_denied_without_main_alter():
    observer = EffectObserver()
    assert observer.observe(sqlite3.SQLITE_UPDATE, "sqlite_temp_master", "sql", "temp", None) == sqlite3.SQLITE_DENY


def test_dependencies_from_before_and_after_select_both_children(connection):
    before = read_schema(connection)
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    connection.execute("UPDATE parent SET id = 999 WHERE id = 1")
    connection.set_authorizer(None)
    connection.execute("CREATE TABLE later_child (key INTEGER REFERENCES parent(id))")
    assert observer.decision(before, read_schema(connection)).tables == frozenset({"child", "code_child", "later_child"})


@pytest.mark.parametrize("alias", ["rowid", "_rowid_", "oid"])
def test_child_primary_key_foreign_key_updates_through_rowid_aliases(connection, alias):
    connection.executescript('''
        CREATE TABLE primary_key_child (id INTEGER PRIMARY KEY REFERENCES parent(id));
        INSERT INTO primary_key_child VALUES (1);
    ''')
    observer, decision = observe_sql(connection, f"UPDATE primary_key_child SET {alias} = 999")
    assert any(effect.table == "primary_key_child" and effect.column == "rowid" for effect in observer.effects)
    assert decision.scope == "tables"
    assert decision.tables == frozenset({"primary_key_child"})
    assert connection.execute("PRAGMA foreign_key_check(primary_key_child)").fetchall() == [
        ("primary_key_child", 999, "parent", 0),
    ]


def test_attached_argument_free_incremental_vacuum_is_denied_before_page_changes(connection, tmp_path):
    path = tmp_path / "attached.sqlite"
    with closing(sqlite3.connect(path)) as attached:
        attached.execute("PRAGMA auto_vacuum = INCREMENTAL")
        attached.execute("CREATE TABLE target (data BLOB)")
        attached.execute("INSERT INTO target VALUES (zeroblob(100000))")
        attached.execute("DELETE FROM target")
        attached.commit()
    connection.execute("ATTACH DATABASE ? AS other", (str(path),))
    before = (
        connection.execute("PRAGMA other.page_count").fetchone(),
        connection.execute("PRAGMA other.freelist_count").fetchone(),
    )
    assert before[1][0] > 0
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("PRAGMA other.incremental_vacuum")
    finally:
        connection.set_authorizer(None)
    assert (
        connection.execute("PRAGMA other.page_count").fetchone(),
        connection.execute("PRAGMA other.freelist_count").fetchone(),
    ) == before
    assert observer.decision({}, {}).scope == "global"


@pytest.mark.parametrize("pragma", ["incremental_vacuum", "optimize", "unknown_pragma"])
def test_unproved_argument_free_main_pragmas_require_global_fallback(connection, pragma):
    _, decision = observe_sql(connection, f"PRAGMA main.{pragma}")
    assert decision.scope == "global"
    assert decision.reasons


@pytest.mark.parametrize("schema", ["other", "temp"])
def test_unknown_argument_free_non_main_pragmas_are_rejected(connection, schema):
    connection.execute("ATTACH ':memory:' AS other")
    observer = EffectObserver()
    connection.set_authorizer(observer.observe)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(f"PRAGMA {schema}.unknown_pragma")
    finally:
        connection.set_authorizer(None)


@pytest.mark.parametrize("pragma", [
    "page_count", "freelist_count", "auto_vacuum", "user_version", "schema_version", "journal_mode",
    "foreign_keys", "defer_foreign_keys", "writable_schema", "database_list", "compile_options",
])
@pytest.mark.parametrize("schema", ["main", "other", "temp"])
def test_proven_argument_free_readonly_pragmas_keep_scope_none(connection, pragma, schema):
    connection.execute("ATTACH ':memory:' AS other")
    _, decision = observe_sql(connection, f"PRAGMA {schema}.{pragma}")
    assert decision.scope == "none"
