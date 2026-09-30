"""SQLite backend with transaction-local migration validation."""

from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper

from .schema import DatabaseSchemaEditor


class DatabaseWrapper(SQLiteDatabaseWrapper):
    SchemaEditorClass = DatabaseSchemaEditor
