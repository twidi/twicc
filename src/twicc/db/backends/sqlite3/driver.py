"""Guard the editor's transaction at execution time, including cached SQL."""

import sqlite3
from functools import lru_cache

from .bindings import BindingCursor


class GuardedCursor:
    """Mixin placed before a native cursor class, including Django's cursor."""

    def execute(self, sql, parameters=()):
        self.connection._check_migration_transaction()
        result = super().execute(sql, parameters)
        self.connection._check_migration_transaction()
        return result

    def executemany(self, sql, parameters):
        self.connection._check_migration_transaction()
        if self.connection._migration_transaction_guard is not None:
            parameters = self._guard_parameters(parameters)
        result = super().executemany(sql, parameters)
        self.connection._check_migration_transaction()
        return result

    def _guard_parameters(self, parameters):
        # A generator can execute SQL and catch a rollback between rows.
        for values in parameters:
            self.connection._check_migration_transaction()
            yield values

    def fetchone(self):
        self.connection._check_migration_transaction()
        result = super().fetchone()
        self.connection._check_migration_transaction()
        return result

    def fetchmany(self, *args, **kwargs):
        self.connection._check_migration_transaction()
        result = super().fetchmany(*args, **kwargs)
        self.connection._check_migration_transaction()
        return result

    def fetchall(self):
        self.connection._check_migration_transaction()
        result = super().fetchall()
        self.connection._check_migration_transaction()
        return result

    def __next__(self):
        self.connection._check_migration_transaction()
        result = super().__next__()
        self.connection._check_migration_transaction()
        return result

    def executescript(self, sql_script):
        self.connection._check_migration_transaction()
        result = super().executescript(sql_script)
        self.connection._check_migration_transaction()
        return result


@lru_cache(maxsize=32)
def guarded_cursor_class(factory):
    if issubclass(factory, GuardedCursor):
        return factory
    if factory is sqlite3.Cursor:
        bases = (GuardedCursor, BindingCursor)
    elif issubclass(factory, BindingCursor):
        bases = (GuardedCursor, factory)
    else:
        bases = (GuardedCursor, factory, BindingCursor)
    return type(f"Guarded{factory.__name__}", bases, {})


class Connection(sqlite3.Connection):
    """Normal sqlite3 connection unless an atomic schema editor owns it.

    Connection shortcuts must use guarded cursors: sqlite3's C shortcuts
    otherwise construct cursors without calling the Python cursor override.
    """

    _migration_transaction_guard = None
    _migration_unguarded_cursor = False

    def _check_migration_transaction(self):
        guard = self._migration_transaction_guard
        if guard is not None:
            guard()

    def cursor(self, factory=sqlite3.Cursor):
        if not isinstance(factory, type) or not issubclass(factory, sqlite3.Cursor):
            if self._migration_transaction_guard is not None:
                raise sqlite3.NotSupportedError("Atomic SQLite editing requires a class-based cursor factory.")
            cursor = super().cursor(factory=factory)
            # An opaque callable can return an unguarded cursor retained by its
            # caller. Preserve normal behavior, but reject later atomic editing.
            if not isinstance(cursor, GuardedCursor):
                self._migration_unguarded_cursor = True
            return cursor
        return super().cursor(factory=guarded_cursor_class(factory))

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql_script):
        return self.cursor().executescript(sql_script)
