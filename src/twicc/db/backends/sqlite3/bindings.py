"""Check atomic ownership inside SQLite's parameter adaptation boundary."""

import sqlite3
from inspect import getattr_static
from typing import NamedTuple


class Parameter(NamedTuple):
    connection: object
    value: object
    retained: list


def buffer_method(value, name):
    method = getattr_static(type(value), name, None)
    if method is not None and hasattr(method, "__get__"):
        return method.__get__(value, type(value))
    return method


class SimpleBuffer(NamedTuple):
    value: object

    def __buffer__(self, flags):
        # sqlite3 requests PyBUF_SIMPLE, not memoryview's richer flags.
        return buffer_method(self.value, "__buffer__")(0)

    def __release_buffer__(self, view):
        release = buffer_method(self.value, "__release_buffer__")
        if release is not None:
            release(view)


def adapt_parameter(parameter):
    connection, value, retained = parameter
    connection._check_migration_transaction()
    # Use SQLite's registry/protocol precedence and its original-value fallback.
    # Returning through a registered adapter avoids a second adaptation, and
    # preserves SQL NULL (where __conform__ returning None means no adaptation).
    adapted = sqlite3.adapt(value, sqlite3.PrepareProtocol, value)
    # Finalizers of temporary parameters/results must not run between this
    # check and sqlite3_step(). The parameter container owns these references.
    retained.append(adapted)
    if (
        not issubclass(type(adapted), (int, float, str))
        and getattr_static(type(adapted), "__buffer__", None) is not None
    ):
        # Python buffer acquisition/release can execute SQL too. Bind an exact
        # bytes snapshot only after both callbacks finish, with native flags.
        with memoryview(SimpleBuffer(adapted)) as view:
            adapted = bytes(view)
    connection._check_migration_transaction()
    return adapted


# This new private type never replaces an existing adapter or wraps values
# outside an atomic editor. Ordinary registered adapters remain untouched.
sqlite3.register_adapter(Parameter, adapt_parameter)


class Sequence:
    def __init__(self, connection, values):
        self.connection = connection
        self.values = values
        self.retained = []

    def __len__(self):
        size = len(self.values)
        self.connection._check_migration_transaction()
        return size

    def __getitem__(self, index):
        value = self.values[index]
        self.connection._check_migration_transaction()
        self.retained.append(value)
        return Parameter(self.connection, value, self.retained)


class Mapping(dict):
    def __init__(self, connection, values):
        self.connection = connection
        self.values = values
        self.retained = []

    def __getitem__(self, key):
        value = self.values[key]
        self.connection._check_migration_transaction()
        self.retained.append(value)
        return Parameter(self.connection, value, self.retained)


def guard_parameters(connection, parameters):
    connection._check_migration_transaction()
    if issubclass(type(parameters), dict):
        return Mapping(connection, parameters)
    if hasattr(type(parameters), "__getitem__"):
        return Sequence(connection, parameters)
    # Let SQLite retain its error for unsupported containers.
    return parameters


class BindingCursor(sqlite3.Cursor):
    """Last Python cursor layer, after Django/custom query transformations."""

    def execute(self, sql, parameters=()):
        if self.connection._migration_transaction_guard is not None:
            parameters = guard_parameters(self.connection, parameters)
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters):
        if self.connection._migration_transaction_guard is not None:
            parameters = (guard_parameters(self.connection, values) for values in parameters)
        return super().executemany(sql, parameters)
