"""Read main-schema dependencies without reading application rows."""

from typing import NamedTuple


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def canonical_identifier(value: str) -> str:
    """Match SQLite's ASCII-only identifier case normalization."""
    return value.translate(_ASCII_LOWER)


class ForeignKey(NamedTuple):
    child_table: str
    parent_table: str
    child_columns: tuple[str, ...]
    parent_columns: tuple[str, ...]


class IndexSchema(NamedTuple):
    name: str
    unique: bool
    origin: str
    columns: tuple[str, ...]
    partial: bool
    has_expressions: bool
    uses_generated_columns: bool


class TableSchema(NamedTuple):
    name: str
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...]
    generated_columns: frozenset[str]
    indexes: tuple[IndexSchema, ...]


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read_schema(connection) -> dict[str, TableSchema]:
    """Keep original object names for SQL and canonical names for dependencies.

    Missing implicit parent keys remain empty. Decision code treats unresolved
    dependencies conservatively. A rowid primary key has no SQLite index;
    represent its unique conflict input with an unnamed synthetic index.
    """
    tables = connection.execute(
        "SELECT name FROM main.sqlite_schema WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
    ).fetchall()
    columns = {}
    primary_keys = {}
    for (name,) in tables:
        key = canonical_identifier(name)
        columns[key] = connection.execute(f"PRAGMA main.table_xinfo({_quote_identifier(name)})").fetchall()
        primary_keys[key] = tuple(
            canonical_identifier(row[1]) for row in sorted(columns[key], key=lambda row: row[5]) if row[5]
        )

    result = {}
    for (name,) in tables:
        key = canonical_identifier(name)
        generated = frozenset(canonical_identifier(row[1]) for row in columns[key] if row[6] in (2, 3))
        grouped_keys = {}
        for row in connection.execute(f"PRAGMA main.foreign_key_list({_quote_identifier(name)})"):
            grouped_keys.setdefault(row[0], []).append(row)
        foreign_keys = []
        for rows in grouped_keys.values():
            rows.sort(key=lambda row: row[1])
            parent = canonical_identifier(rows[0][2])
            parent_columns = (
                primary_keys.get(parent, ())
                if any(row[4] is None for row in rows)
                else tuple(canonical_identifier(row[4]) for row in rows)
            )
            foreign_keys.append(ForeignKey(
                key, parent, tuple(canonical_identifier(row[3]) for row in rows), parent_columns,
            ))

        indexes = []
        for row in connection.execute(f"PRAGMA main.index_list({_quote_identifier(name)})"):
            index_columns = connection.execute(f"PRAGMA main.index_xinfo({_quote_identifier(row[1])})").fetchall()
            key_columns = sorted((column for column in index_columns if column[5]), key=lambda column: column[0])
            named_columns = tuple(canonical_identifier(column[2]) for column in key_columns if column[1] >= 0)
            indexes.append(IndexSchema(
                row[1], bool(row[2]), row[3], named_columns, bool(row[4]),
                any(column[1] < 0 for column in key_columns), bool(generated.intersection(named_columns)),
            ))
        if primary_keys[key] and not any(index.origin == "pk" for index in indexes):
            indexes.append(IndexSchema("", True, "pk", primary_keys[key], False, False, False))
        result[key] = TableSchema(name, primary_keys[key], tuple(foreign_keys), generated, tuple(indexes))
    return result
