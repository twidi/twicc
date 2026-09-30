"""SQLite backend with transaction-local migration validation."""

from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper

from .driver import Connection
from .schema import DatabaseSchemaEditor


class DatabaseWrapper(SQLiteDatabaseWrapper):
    SchemaEditorClass = DatabaseSchemaEditor

    def get_connection_params(self):
        params = super().get_connection_params()
        params.setdefault("factory", Connection)
        return params
