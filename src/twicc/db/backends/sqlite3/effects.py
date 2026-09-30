"""Observe compiled SQLite effects without executing SQL in the callback."""

import sqlite3
from typing import NamedTuple

from .metadata import TableSchema, canonical_identifier


class WriteEffect(NamedTuple):
    action: int
    table: str
    column: str | None
    source: str | None


class AuthorizationEffect(NamedTuple):
    """Keep raw schema authorization arguments for exact editor proof matching."""

    action: int
    arg1: str | None
    arg2: str | None
    database: str | None
    source: str | None


class CheckDecision(NamedTuple):
    scope: str
    tables: frozenset[str]
    reasons: tuple[str, ...]


_WRITES = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_UPDATE})
_SCHEMA_ACTIONS = frozenset({
    sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_VIEW, sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_ANALYZE, sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
})
_READ_ACTIONS = frozenset({
    sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
    sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
})
_READ_PRAGMAS_WITH_ARGUMENT = frozenset({
    "table_info", "table_xinfo", "table_list", "index_info", "index_xinfo", "index_list",
    "foreign_key_list", "foreign_key_check", "integrity_check", "quick_check",
})
_MAIN_CATALOGS = frozenset({"sqlite_master", "sqlite_schema", "sqlite_sequence"})
_TEMP_CATALOGS = frozenset({"sqlite_temp_master", "sqlite_temp_schema"})
_ROWID_ALIASES = frozenset({"rowid", "_rowid_", "oid"})


class EffectObserver:
    """Own one editor's data effects and unproved schema effects.

    The editor may remove exactly proved entries from schema_effects. It must
    never remove data effects when recognizing a schema operation. Unknown
    effects remain global for the observer's whole lifetime.
    """

    def __init__(self):
        self.effects: list[WriteEffect] = []
        self.schema_effects: list[AuthorizationEffect] = []
        self.unknown_reasons: list[str] = []
        self._main_alter_seen = False

    def observe(self, action, arg1, arg2, database, source) -> int:
        """Fail closed on malformed effects; exceptions never cross into SQLite."""
        try:
            return self._observe(action, arg1, arg2, database, source)
        except Exception as error:
            self.unknown_reasons.append(f"authorizer error: {type(error).__name__}")
            return sqlite3.SQLITE_DENY

    def _observe(self, action, arg1, arg2, database, source) -> int:
        effect = AuthorizationEffect(action, arg1, arg2, database, source)
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            self.unknown_reasons.append("ATTACH and DETACH are unsupported during schema editing")
            return sqlite3.SQLITE_DENY

        if action == sqlite3.SQLITE_PRAGMA:
            pragma = canonical_identifier(arg1)
            if pragma == "writable_schema" and arg2 is not None:
                self.unknown_reasons.append("writable_schema setters are unsupported")
                return sqlite3.SQLITE_DENY
            if arg2 is None or pragma in _READ_PRAGMAS_WITH_ARGUMENT:
                return sqlite3.SQLITE_OK
            if database not in (None, "main"):
                self.unknown_reasons.append("temp and attached PRAGMA mutations are unsupported")
                return sqlite3.SQLITE_DENY
            self.unknown_reasons.append(f"unclassified PRAGMA setter: {pragma}")
            return sqlite3.SQLITE_OK

        if action in _WRITES or action in _SCHEMA_ACTIONS:
            affected_database = arg1 if action == sqlite3.SQLITE_ALTER_TABLE else database
            internal_temp_catalog = (
                action in _WRITES and database == "temp" and arg1 in _TEMP_CATALOGS and self._main_alter_seen
            )
            if affected_database != "main" and not internal_temp_catalog:
                self.unknown_reasons.append("only main-schema mutations are supported")
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_ALTER_TABLE:
                self._main_alter_seen = True
            if action in _SCHEMA_ACTIONS or arg1 in _MAIN_CATALOGS or internal_temp_catalog:
                self.schema_effects.append(effect)
            else:
                table = canonical_identifier(arg1)
                column = canonical_identifier(arg2) if action == sqlite3.SQLITE_UPDATE else None
                self.effects.append(WriteEffect(action, table, column, source))
            return sqlite3.SQLITE_OK

        if action not in _READ_ACTIONS:
            self.unknown_reasons.append(f"unknown SQLite authorization action: {action}")
            if database not in (None, "main"):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def decision(self, before: dict[str, TableSchema], after: dict[str, TableSchema]) -> CheckDecision:
        """Select surviving children using dependencies on both sides of editing."""
        reasons = list(self.unknown_reasons)
        if self.schema_effects:
            reasons.append("unproved main-schema effects")
        if reasons:
            return CheckDecision("global", frozenset(), tuple(dict.fromkeys(reasons)))

        selected = set()
        for effect in self.effects:
            versions = [schema[effect.table] for schema in (before, after) if effect.table in schema]
            if not versions:
                reasons.append(f"unknown written table: {effect.table}")
                continue
            for table in versions:
                outgoing_columns = {column for key in table.foreign_keys for column in key.child_columns}
                if effect.table in after and (
                    effect.action == sqlite3.SQLITE_INSERT and table.foreign_keys
                    or effect.action == sqlite3.SQLITE_UPDATE and (
                        effect.column in outgoing_columns or outgoing_columns.intersection(table.generated_columns)
                    )
                ):
                    selected.add(effect.table)
                unique_indexes = [index for index in table.indexes if index.unique]
                unique_inputs = {column for index in unique_indexes for column in index.columns}
                unknown_unique_inputs = any(
                    index.partial or index.has_expressions or index.uses_generated_columns for index in unique_indexes
                )
                for schema in (before, after):
                    for child_key, child in schema.items():
                        if child_key not in after:
                            continue
                        for key in child.foreign_keys:
                            if key.parent_table != effect.table:
                                continue
                            if effect.action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE) or (
                                effect.action == sqlite3.SQLITE_UPDATE and (
                                    effect.column in key.parent_columns
                                    or effect.column in _ROWID_ALIASES
                                    or table.generated_columns.intersection(key.parent_columns)
                                    or not key.parent_columns
                                    or effect.column in unique_inputs
                                    or unknown_unique_inputs
                                )
                            ):
                                selected.add(child_key)
        if reasons:
            return CheckDecision("global", frozenset(), tuple(dict.fromkeys(reasons)))
        return CheckDecision("tables" if selected else "none", frozenset(selected), ())
