"""Validate observed relation effects before the migration transaction commits."""

import sqlite3
import sys
from contextlib import contextmanager
from typing import NamedTuple
from time import perf_counter

from django.db import NotSupportedError
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.backends.ddl_references import Statement
from django.db.backends.sqlite3.schema import DatabaseSchemaEditor as SQLiteSchemaEditor
from django.db.backends.utils import strip_quotes
from django.db.models import Index, UniqueConstraint

from twicc.db.migration_logging import log_fk_check

from .driver import Connection
from .effects import CheckDecision, EffectObserver
from .metadata import canonical_identifier, read_schema


class StatementProof(NamedTuple):
    statement: object
    rendered: str
    params: tuple
    kind: str
    table: str
    index: str | None


class EmptyRebuild(NamedTuple):
    table: str
    temporary: str
    writes: list
    renames: list


class DatabaseSchemaEditor(SQLiteSchemaEditor):
    """Own one observer, immutable statement proofs, and one atomic boundary.

    Proofs remove only matched schema authorizations from one execution.
    Independent data effects and unknown reasons always remain observable.
    Only newly created, empty table rebuilds have an additional zero-row proof.
    Other rebuilds and unproved schema statements keep the global fallback.
    """

    def __enter__(self):
        connection = self.connection
        # Check ownership before touching PRAGMAs or the outer authorizer.
        if getattr(connection, "_migration_effect_editor", None) is not None or connection.in_atomic_block:
            raise NotSupportedError("Nested SQLite schema editors and existing atomic blocks are unsupported.")
        connection.ensure_connection()
        if self.atomic_migration and not self.collect_sql:
            if not isinstance(connection.connection, Connection):
                raise NotSupportedError("Atomic SQLite schema editing requires the guarded connection factory.")
            if connection.connection._migration_unguarded_cursor:
                raise NotSupportedError("Atomic SQLite schema editing cannot use an unguarded cursor factory.")
        if connection.connection.in_transaction:
            raise NotSupportedError("SQLite schema editing cannot enter an existing native transaction.")
        if connection.connection.execute("PRAGMA writable_schema").fetchone()[0]:
            raise NotSupportedError("SQLite schema editing requires writable_schema to be disabled.")
        self._operations = []
        self._proofs = []
        self._created_tables = set()
        self._empty_rebuild = None
        self.observer = EffectObserver()
        self.decision = CheckDecision("none", frozenset(), ())
        self.deferred_sql = []
        self._observing = False
        self._entered_atomic = False
        if self.collect_sql:
            self.atomic_migration = False
            connection._migration_effect_editor = self
            return self
        self._before = read_schema(connection.connection)
        self._foreign_keys = bool(connection.connection.execute("PRAGMA foreign_keys").fetchone()[0])
        connection._migration_effect_editor = self
        try:
            if not connection.disable_constraint_checking():
                raise NotSupportedError("SQLite foreign key enforcement cannot be disabled in this transaction.")
            BaseDatabaseSchemaEditor.__enter__(self)
            self._entered_atomic = self.atomic_migration
            if self.atomic_migration:
                connection.connection._migration_transaction_guard = self._check_atomic_transaction
            connection.connection.set_authorizer(self._authorize)
            self._observing = True
            return self
        except BaseException:
            error = sys.exc_info()
            self._finish(error)
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        if self.collect_sql:
            try:
                BaseDatabaseSchemaEditor.__exit__(self, exc_type, exc_value, traceback)
            finally:
                self._release_ownership()
            return
        error = (exc_type, exc_value, traceback)
        try:
            if exc_type is None:
                self._check_atomic_transaction()
                # Django normally drains these after its SQLite check. Drain
                # them here instead, while observation and rollback still work.
                for statement in self.deferred_sql:
                    self.execute(statement, None)
                self.deferred_sql = []
                self._stop_observing()
                after = read_schema(self.connection.connection)
                self.decision = self.observer.decision(self._before, after)
                # A new empty parent can make old unresolved references real.
                # New child tables remain empty unless observed writes fill them.
                incoming = {
                    key
                    for key, table in after.items()
                    if key in self._before and any(fk.parent_table in self._created_tables for fk in table.foreign_keys)
                }
                if self.decision.scope != "global" and incoming:
                    self.decision = CheckDecision("tables", self.decision.tables | incoming, self.decision.reasons)
                started = perf_counter()
                success = False
                try:
                    if self.decision.scope == "global":
                        self.connection.check_constraints()
                    elif self.decision.scope == "tables":
                        self.connection.check_constraints([after[key].name for key in sorted(self.decision.tables)])
                    success = True
                finally:
                    duration = perf_counter() - started if self.decision.scope != "none" else 0
                    log_fk_check(self.decision, duration, success)
        except BaseException:
            error = sys.exc_info()
            raise
        finally:
            self._finish(error)

    def _check_atomic_transaction(self):
        if self._entered_atomic and not self.connection.connection.in_transaction:
            raise sqlite3.OperationalError("The SQLite migration transaction ended before validation.")

    def _authorize(self, action, arg1, arg2, database, source):
        # The editor starts its transaction before observation and finishes
        # it after observation. User controls must not end that transaction.
        if self.atomic_migration and action == sqlite3.SQLITE_TRANSACTION:
            return sqlite3.SQLITE_DENY
        return self.observer.observe(action, arg1, arg2, database, source)

    def _stop_observing(self):
        if self._observing:
            self.connection.connection.set_authorizer(None)
            self._observing = False

    def _finish(self, error):
        """Run every cleanup step and preserve the original operation error."""
        cleanup_error = None
        if isinstance(self.connection.connection, Connection):
            self.connection.connection._migration_transaction_guard = None
        try:
            self._stop_observing()
        except BaseException:
            cleanup_error = sys.exc_info()
        try:
            if self._entered_atomic:
                self._entered_atomic = False
                self.atomic.__exit__(*error)
        except BaseException:
            if cleanup_error is None:
                cleanup_error = sys.exc_info()
        finally:
            try:
                if self._foreign_keys:
                    self.connection.enable_constraint_checking()
            except BaseException:
                if cleanup_error is None:
                    cleanup_error = sys.exc_info()
            finally:
                self._release_ownership()
        if error[0] is None and cleanup_error is not None:
            raise cleanup_error[1].with_traceback(cleanup_error[2])

    def _release_ownership(self):
        if getattr(self.connection, "_migration_effect_editor", None) is self:
            del self.connection._migration_effect_editor

    @contextmanager
    def _operation(self, kind, table):
        self._operations.append((kind, table))
        try:
            yield
        finally:
            self._operations.pop()

    def _register(self, statement, params, kind, table, index=None):
        if statement is not None:
            self._proofs.append(StatementProof(statement, str(statement), tuple(params or ()), kind, table, index))
        return statement

    def create_model(self, model):
        with self._operation("create", model._meta.db_table):
            return super().create_model(model)

    def table_sql(self, model):
        sql, params = super().table_sql(model)
        if self._operations and self._operations[-1] == ("create", model._meta.db_table):
            self._register(sql, params, "create", model._meta.db_table)
        return sql, params

    def delete_model(self, model, handle_autom2m=True):
        with self._operation("delete", model._meta.db_table):
            return super().delete_model(model, handle_autom2m=handle_autom2m)

    def _table_empty(self, table):
        return self.connection.connection.execute(
            f"SELECT 1 FROM {self.quote_name(table)} LIMIT 1"
        ).fetchone() is None

    def _remake_table(self, model, **kwargs):
        table = model._meta.db_table
        key = canonical_identifier(table)
        temporary = "new__%s" % strip_quotes(table)
        native = self.connection.connection
        if (
            self.collect_sql
            or not self.atomic_migration
            or self._empty_rebuild is not None
            or key in self._before
            or key not in self._created_tables
            or canonical_identifier(temporary) in self._before
            or canonical_identifier(temporary) in read_schema(native)
            or not self._table_empty(table)
        ):
            return super()._remake_table(model, **kwargs)
        proof = EmptyRebuild(table, temporary, [], [])
        changes = native.total_changes
        self._empty_rebuild = proof
        try:
            result = super()._remake_table(model, **kwargs)
            after = read_schema(native)
            if (
                native.total_changes == changes
                and canonical_identifier(temporary) not in after
                and key in after
                and self._table_empty(table)
                and len(proof.renames) == 1
            ):
                # Remove only the exact zero-row INSERT and rename events.
                # Callback writes, unknown reasons and other schema events stay.
                write_ids = {id(effect) for effect in proof.writes}
                rename_ids = {id(effect) for effect in proof.renames[0]}
                self.observer.effects[:] = [e for e in self.observer.effects if id(e) not in write_ids]
                self.observer.schema_effects[:] = [
                    e for e in self.observer.schema_effects if id(e) not in rename_ids
                ]
            return result
        finally:
            self._empty_rebuild = None

    def alter_db_table(self, model, old_db_table, new_db_table):
        rebuild = self._empty_rebuild
        if rebuild is None or (old_db_table, new_db_table) != (rebuild.temporary, rebuild.table):
            return super().alter_db_table(model, old_db_table, new_db_table)
        # Django mutates deferred Statement references after the rename.
        # Only still-intact standard index proofs can follow that mutation.
        proofs = [
            p for p in self._proofs
            if p.kind == "index" and p.table == old_db_table
            and type(p.statement) is Statement and p.rendered == str(p.statement)
        ]
        with self._operation("empty_rename", old_db_table):
            result = super().alter_db_table(model, old_db_table, new_db_table)
        for proof in proofs:
            self._proofs.append(proof._replace(rendered=str(proof.statement), table=new_db_table))
        return result

    def add_index(self, model, index):
        # Custom Index callbacks have no standard-operation provenance.
        kind = "index" if type(index) is Index else "unknown"
        with self._operation(kind, model._meta.db_table):
            return super().add_index(model, index)

    def add_constraint(self, model, constraint):
        table = model._meta.db_table
        key = canonical_identifier(table)
        if (
            not self.collect_sql and type(constraint) is UniqueConstraint
            and key not in self._before and key in self._created_tables and self._table_empty(table)
        ):
            with self._operation("empty_constraint", table):
                return super().add_constraint(model, constraint)
        return super().add_constraint(model, constraint)

    def remove_index(self, model, index):
        kind = "drop_index" if type(index) is Index else "unknown"
        with self._operation(kind, model._meta.db_table):
            return super().remove_index(model, index)

    def _model_indexes_sql(self, model):
        if not model._meta.managed or model._meta.proxy or model._meta.swapped:
            return []
        statements = []
        for field in model._meta.local_fields:
            with self._operation("field_index", model._meta.db_table):
                statements.extend(self._field_indexes_sql(model, field))
        for index in model._meta.indexes:
            if not index.contains_expressions or self.connection.features.supports_expression_indexes:
                kind = "index" if type(index) is Index else "unknown"
                with self._operation(kind, model._meta.db_table):
                    statements.append(index.create_sql(model, self))
        return statements

    def _create_index_sql(self, model, **kwargs):
        statement = super()._create_index_sql(model, **kwargs)
        if (
            self._operations
            and self._operations[-1]
            in (
                ("field_index", model._meta.db_table),
                ("index", model._meta.db_table),
            )
            and kwargs.get("sql") in (None, self.sql_create_index)
        ):
            # Render once to freeze lazy index names as well as the SQL text.
            str(statement)
            name = str(statement.parts["name"]).strip('"').replace('""', '"')
            self._register(statement, (), "index", model._meta.db_table, name)
        return statement

    def _create_unique_sql(self, model, fields, *args, **kwargs):
        statement = super()._create_unique_sql(model, fields, *args, **kwargs)
        if self._operations and self._operations[-1] in (
            ("create", model._meta.db_table), ("empty_constraint", model._meta.db_table)
        ) and statement is not None:
            name = str(statement.parts["name"]).strip('"').replace('""', '"')
            self._register(statement, (), "index", model._meta.db_table, name)
        return statement

    def _delete_index_sql(self, model, name, sql=None):
        statement = super()._delete_index_sql(model, name, sql=sql)
        if self._operations and self._operations[-1] == ("drop_index", model._meta.db_table) and sql is None:
            table = read_schema(self.connection.connection).get(canonical_identifier(model._meta.db_table))
            physical = (
                [
                    index
                    for index in table.indexes
                    if index.name and canonical_identifier(index.name) == canonical_identifier(name)
                ]
                if table
                else []
            )
            if len(physical) == 1 and not physical[0].unique:
                self._register(statement, (), "drop_index", model._meta.db_table, name)
        return statement

    def execute(self, sql, params=()):
        if self.collect_sql:
            return super().execute(sql, params)
        rendered = str(sql)
        proof = next(
            (
                proof
                for proof in self._proofs
                if proof.statement is sql and proof.rendered == rendered and proof.params == tuple(params or ())
            ),
            None,
        )
        if proof is None and self._operations:
            kind, table = self._operations[-1]
            if kind == "delete" and rendered == self.sql_delete_table % {"table": self.quote_name(table)}:
                proof = StatementProof(sql, rendered, tuple(params or ()), "delete", table, None)
        rebuild = self._empty_rebuild
        rename = rebuild is not None and self._operations and self._operations[-1] == (
            "empty_rename", rebuild.temporary
        ) and rendered == self.sql_rename_table % {
            "old_table": self.quote_name(rebuild.temporary), "new_table": self.quote_name(rebuild.table)
        } and not params
        before_rename = read_schema(self.connection.connection) if rename else None
        start = len(self.observer.schema_effects)
        write_start = len(self.observer.effects)
        changes = self.connection.connection.total_changes
        result = super().execute(sql, params)
        effects = self.observer.schema_effects[start:]
        if proof and self._matches(proof, effects):
            del self.observer.schema_effects[start:]
            if proof.kind == "create":
                self._created_tables.add(canonical_identifier(proof.table))
        if rebuild is not None:
            writes = self.observer.effects[write_start:]
            if (
                len(writes) == 1
                and writes[0].action == sqlite3.SQLITE_INSERT
                and writes[0].table == canonical_identifier(rebuild.temporary)
                and writes[0].source is None
                and self.connection.connection.total_changes == changes
                and not effects
                and self._table_empty(rebuild.table)
                and self._table_empty(rebuild.temporary)
            ):
                rebuild.writes.extend(writes)
            if rename and self._matches_empty_rename(rebuild, effects, before_rename):
                rebuild.renames.append(effects)
        return result

    def _matches_empty_rename(self, rebuild, effects, before):
        temporary = canonical_identifier(rebuild.temporary)
        target = canonical_identifier(rebuild.table)
        after = read_schema(self.connection.connection)
        if temporary not in before or target in before or temporary in after or target not in after:
            return False
        if {k: v for k, v in before.items() if k != temporary} != {
            k: v for k, v in after.items() if k != target
        }:
            return False
        alters = 0
        for effect in effects:
            if effect.source is not None:
                return False
            if effect.action == sqlite3.SQLITE_ALTER_TABLE:
                if effect.arg1 != "main" or canonical_identifier(effect.arg2 or "") != temporary:
                    return False
                alters += 1
            elif effect.action == sqlite3.SQLITE_UPDATE and (
                (effect.database == "main" and effect.arg1 in ("sqlite_master", "sqlite_sequence"))
                or (effect.database == "temp" and effect.arg1 == "sqlite_temp_master")
            ):
                if effect.arg2 not in ("sql", "tbl_name", "name"):
                    return False
            else:
                return False
        return alters == 1

    def _matches(self, proof, effects):
        """Prove expected main objects and their mechanical catalog effects."""
        table = canonical_identifier(proof.table)
        index = canonical_identifier(proof.index) if proof.index is not None else None
        expected_action = {
            "create": sqlite3.SQLITE_CREATE_TABLE,
            "delete": sqlite3.SQLITE_DROP_TABLE,
            "index": sqlite3.SQLITE_CREATE_INDEX,
            "drop_index": sqlite3.SQLITE_DROP_INDEX,
        }[proof.kind]
        expected_name = index if index is not None else table
        objects = [
            effect
            for effect in effects
            if effect.action == expected_action and canonical_identifier(effect.arg1 or "") == expected_name
        ]
        if len(objects) != 1:
            return False
        for effect in effects:
            if effect.database != "main" or effect.source is not None:
                return False
            name = canonical_identifier(effect.arg1 or "")
            if effect.action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_UPDATE):
                if name == "sqlite_master" and (
                    effect.action != sqlite3.SQLITE_UPDATE
                    or effect.arg2 in ("type", "name", "tbl_name", "rootpage", "sql")
                ):
                    continue
                if proof.kind == "delete" and name == "sqlite_sequence" and effect.action == sqlite3.SQLITE_DELETE:
                    continue
                return False
            if effect in objects:
                if index is not None and canonical_identifier(effect.arg2 or "") != table:
                    return False
                continue
            if proof.kind == "create":
                if effect.action == sqlite3.SQLITE_CREATE_TABLE and name == "sqlite_sequence":
                    continue
                if effect.action == sqlite3.SQLITE_CREATE_INDEX and canonical_identifier(effect.arg2 or "") == table:
                    schema = read_schema(self.connection.connection).get(table)
                    if schema and any(
                        canonical_identifier(item.name) == name and item.origin in ("u", "pk")
                        for item in schema.indexes
                        if item.name
                    ):
                        continue
            if proof.kind == "index" and effect.action == sqlite3.SQLITE_REINDEX and name == index:
                continue
            return False
        return True
