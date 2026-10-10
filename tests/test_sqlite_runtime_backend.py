"""The runtime SQLite backend keeps Django 6.0's boolean WHERE shape."""

from django.db import connection

from twicc.core.models import Session


def test_boolean_filters_compile_to_bare_columns():
    sql = str(Session.objects.filter(hidden=False, archived=True).query)
    assert 'NOT "core_session"."hidden"' in sql
    assert '"core_session"."archived" = ' not in sql
    assert '"core_session"."hidden" = ' not in sql


def test_partial_index_condition_matches_the_query_shape():
    # SQLite only uses a partial index when the query repeats its condition.
    index = next(i for i in Session._meta.indexes if i.name == "idx_session_visible")
    # Only renders the statement: the editor is never entered, nothing runs.
    sql = str(index.create_sql(Session, connection.schema_editor(collect_sql=True)))
    assert 'NOT "hidden"' in sql
