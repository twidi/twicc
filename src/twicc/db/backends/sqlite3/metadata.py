"""Read main-schema dependencies without reading application rows."""

import re
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
    collations: tuple[str, ...] = ()


class TableSchema(NamedTuple):
    name: str
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...]
    generated_columns: frozenset[str]
    indexes: tuple[IndexSchema, ...]
    declarations: tuple = ()
    constraints: tuple = ()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read_schema(connection) -> dict[str, TableSchema]:
    """Keep original object names for SQL and canonical names for dependencies.

    Missing implicit parent keys remain empty. Decision code treats unresolved
    dependencies conservatively. A rowid primary key has no SQLite index;
    represent its unique conflict input with an unnamed synthetic index.
    """
    tables = connection.execute(
        "SELECT name, sql FROM main.sqlite_schema WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
    ).fetchall()
    columns = {}
    primary_keys = {}
    for name, sql in tables:
        key = canonical_identifier(name)
        columns[key] = connection.execute(f"PRAGMA main.table_xinfo({_quote_identifier(name)})").fetchall()
        primary_keys[key] = tuple(
            canonical_identifier(row[1]) for row in sorted(columns[key], key=lambda row: row[5]) if row[5]
        )

    result = {}
    for name, sql in tables:
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
                tuple(canonical_identifier(column[4]) for column in key_columns),
            ))
        if primary_keys[key] and not any(index.origin == "pk" for index in indexes):
            indexes.append(IndexSchema("", True, "pk", primary_keys[key], False, False, False, ("binary",)))
        column_declarations, constraints = declarations(sql)
        result[key] = TableSchema(
            name, primary_keys[key], tuple(foreign_keys), generated, tuple(indexes), column_declarations, constraints,
        )
    return result


def sql_tokens(sql):
    """Tokenize bounded SQLite declarations; preserve literals and quoted names.

    Unsupported comments and malformed quoting leave the proof unavailable.
    This is a lexical scanner, not an SQL expression parser.
    """
    pattern = r'''\s+|"(?:[^"]|"")*"|'(?:[^']|'')*'|`(?:[^`]|``)*`|\[[^\]]*\]|[A-Za-z_][A-Za-z_0-9$]*|\d+(?:\.\d+)?|[^\s]'''
    tokens = []
    for match in re.finditer(pattern, sql):
        token = match.group()
        if token.isspace():
            continue
        if token[0] in '"`[':
            end = ']' if token[0] == '[' else token[0]
            if len(token) < 2 or token[-1] != end:
                return ()
            token = token[1:-1].replace(end * 2, end)
            tokens.append(('identifier', canonical_identifier(token)))
        elif token[0] == "'":
            if len(token) < 2 or token[-1] != "'":
                return ()
            tokens.append(('literal', token))
        else:
            tokens.append(('word', canonical_identifier(token)))
    if any(tokens[i:i + 2] in ([('word', '-'), ('word', '-')], [('word', '/'), ('word', '*')])
           for i in range(len(tokens) - 1)):
        return ()
    return tuple(tokens)


def split_clauses(tokens):
    """Split commas only at depth zero, rejecting unbalanced parentheses."""
    clauses, current, depth = [], [], 0
    for token in tokens:
        if token == ('word', '('):
            depth += 1
        elif token == ('word', ')'):
            depth -= 1
            if depth < 0:
                return ()
        if token == ('word', ',') and depth == 0:
            clauses.append(tuple(current))
            current = []
        else:
            current.append(token)
    if depth:
        return ()
    return tuple(clauses + [tuple(current)])


def declarations(sql):
    tokens = sql_tokens(sql)
    try:
        start = tokens.index(('word', '('))
    except ValueError:
        return (), ()
    # Django emits an ordinary CREATE TABLE, without a trailing table option.
    if not tokens or tokens[-1] != ('word', ')'):
        return (), ()
    columns, constraints = [], []
    for clause in split_clauses(tokens[start + 1:-1]):
        if not clause:
            return (), ()
        if clause[0][0] == 'word' and clause[0][1] in ('constraint', 'primary', 'unique', 'check', 'foreign'):
            constraints.append(clause)
        else:
            columns.append((clause[0][1], clause[1:]))
    return tuple(columns), tuple(constraints)
