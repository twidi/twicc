"""Schema discovery uses SQLite identifiers and never reads table data."""

import sqlite3

import pytest

from twicc.db.backends.sqlite3.metadata import canonical_identifier, read_schema


@pytest.fixture
def connection():
    with sqlite3.connect(":memory:") as connection:
        yield connection


def test_ascii_normalization_preserves_non_ascii_identifiers():
    assert canonical_identifier("ÄBCİΣ") == "ÄbcİΣ"


def test_composite_foreign_key_order_and_implicit_parent_primary_key(connection):
    connection.executescript('''
        CREATE TABLE "Parent" (second TEXT, first TEXT, PRIMARY KEY (first, second));
        CREATE TABLE "Child" (b TEXT, a TEXT, FOREIGN KEY (a, b) REFERENCES "PARENT");
    ''')
    schema = read_schema(connection)
    assert schema["parent"].name == "Parent"
    assert schema["parent"].primary_key == ("first", "second")
    foreign_key, = schema["child"].foreign_keys
    assert foreign_key.child_table == "child"
    assert foreign_key.parent_table == "parent"
    assert foreign_key.child_columns == ("a", "b")
    assert foreign_key.parent_columns == ("first", "second")


def test_multiple_foreign_keys_stay_separate_and_explicit_columns_are_canonical(connection):
    connection.executescript('''
        CREATE TABLE Parent (ID INTEGER PRIMARY KEY, Code TEXT UNIQUE);
        CREATE TABLE Child (a INTEGER REFERENCES PARENT(ID), b TEXT REFERENCES Parent(CODE));
    ''')
    keys = read_schema(connection)["child"].foreign_keys
    assert {(key.child_columns, key.parent_columns) for key in keys} == {
        (("a",), ("id",)), (("b",), ("code",)),
    }


def test_missing_parent_preserves_unresolved_reference(connection):
    connection.execute("CREATE TABLE child (key INTEGER REFERENCES missing)")
    key, = read_schema(connection)["child"].foreign_keys
    assert key.parent_table == "missing"
    assert key.parent_columns == ()


def test_generated_columns_include_virtual_and_stored_columns(connection):
    connection.execute('''CREATE TABLE parent (
        id INTEGER PRIMARY KEY, input TEXT,
        virtual_key TEXT GENERATED ALWAYS AS (upper(input)) VIRTUAL,
        stored_key TEXT GENERATED ALWAYS AS (lower(input)) STORED
    )''')
    assert read_schema(connection)["parent"].generated_columns == frozenset({"virtual_key", "stored_key"})


def test_non_ascii_table_names_remain_distinct_and_quotes_are_safe(connection):
    connection.executescript('''
        CREATE TABLE "Ä" (id INTEGER PRIMARY KEY);
        CREATE TABLE "ä" (id INTEGER PRIMARY KEY);
        CREATE TABLE "a'b" (id INTEGER PRIMARY KEY);
        CREATE TABLE "a\"\"b" (id INTEGER PRIMARY KEY);
        CREATE TABLE child (id INTEGER REFERENCES "Ä"(id));
    ''')
    schema = read_schema(connection)
    assert {"Ä", "ä", "a'b", 'a"b', "child"} == set(schema)
    assert schema["child"].foreign_keys[0].parent_table == "Ä"


def test_index_metadata_captures_unique_inputs_and_dependency_flags(connection):
    connection.executescript('''
        CREATE TABLE Parent (
            ID INTEGER PRIMARY KEY, Code TEXT UNIQUE, Input TEXT,
            Generated TEXT GENERATED ALWAYS AS (upper(Input)) STORED
        );
        CREATE INDEX ordinary ON Parent(Input);
        CREATE UNIQUE INDEX partial_key ON Parent(Code) WHERE Input IS NOT NULL;
        CREATE UNIQUE INDEX expression_key ON Parent(lower(Input));
        CREATE UNIQUE INDEX generated_key ON Parent(Generated);
    ''')
    indexes = read_schema(connection)["parent"].indexes
    by_name = {index.name: index for index in indexes}
    assert by_name["ordinary"].columns == ("input",)
    assert not by_name["ordinary"].unique
    assert by_name["partial_key"].partial
    assert by_name["partial_key"].columns == ("code",)
    assert by_name["expression_key"].has_expressions
    assert by_name["expression_key"].columns == ()
    assert by_name["generated_key"].uses_generated_columns
    assert by_name["generated_key"].columns == ("generated",)
    assert any(index.unique and index.columns == ("id",) and index.origin == "pk" for index in indexes)
    assert any(index.unique and index.columns == ("code",) and index.origin == "u" for index in indexes)


def test_composite_primary_key_is_not_duplicated_in_indexes(connection):
    connection.execute("CREATE TABLE parent (a TEXT, b TEXT, PRIMARY KEY (a, b)) WITHOUT ROWID")
    indexes = read_schema(connection)["parent"].indexes
    assert [(index.columns, index.origin) for index in indexes] == [(("a", "b"), "pk")]


def test_metadata_reads_main_schema_only_and_never_reads_user_rows(connection):
    connection.executescript('''
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TEMP TABLE ignored (id INTEGER);
        ATTACH ':memory:' AS other;
        CREATE TABLE other.ignored (id INTEGER);
    ''')
    reads = []

    def observe(action, table, column, database, source):
        if action == sqlite3.SQLITE_READ:
            reads.append((database, table))
        return sqlite3.SQLITE_OK

    connection.set_authorizer(observe)
    assert set(read_schema(connection)) == {"parent"}
    assert ("main", "parent") not in reads


def test_sqlite_prefix_without_underscore_is_a_user_table(connection):
    connection.execute("CREATE TABLE sqliteChild (id INTEGER PRIMARY KEY)")
    assert read_schema(connection)["sqlitechild"].name == "sqliteChild"
