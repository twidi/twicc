"""Keep boolean filters in the SQL shape the database was tuned for."""

from django.db.backends.sqlite3.operations import DatabaseOperations as SQLiteDatabaseOperations


class DatabaseOperations(SQLiteDatabaseOperations):
    def conditional_expression_supported_in_where_clause(self, expression):
        """Use boolean columns directly in WHERE, as Django did before 6.1.

        Django 6.1 (ticket #36492) compiles ``flag=False`` on SQLite as
        ``"flag" = False`` instead of ``NOT "flag"``. Without ANALYZE
        statistics (TwiCC never runs it), SQLite then picks the single-column
        index on ``core_session.hidden`` for per-project session counts, which
        matches almost every row: the count got up to 200x slower on a real
        database. The new form would also stop matching the WHERE clause of
        partial indexes created before 6.1 (``idx_session_visible``).
        """
        return True
