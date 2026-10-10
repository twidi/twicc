"""SQLite backend with transaction-local migration validation."""

from twicc.db.backends.sqlite_runtime.base import DatabaseWrapper as RuntimeDatabaseWrapper

from .driver import Connection
from .schema import DatabaseSchemaEditor


class DatabaseWrapper(RuntimeDatabaseWrapper):
    SchemaEditorClass = DatabaseSchemaEditor

    def get_connection_params(self):
        params = super().get_connection_params()
        params.setdefault("factory", Connection)
        return params
