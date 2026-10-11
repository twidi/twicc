"""Query plans of the session visibility filters (Django 6.1 boolean SQL).

Django 6.1 compiles `hidden=False` as `"hidden" = False`. TwiCC never runs
ANALYZE, so SQLite plans without statistics: a full index on `hidden` would
look selective and be picked for the visible-session filters. Only a partial
index on the hidden rows may exist, and the visible filters must keep using
the project indexes.
"""

import pytest
from django.db import connection

from twicc.core.models import Session, SessionType

pytestmark = pytest.mark.django_db


def plan(queryset):
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
        return " / ".join(row[3] for row in cursor.fetchall())


def visible_sessions(**filters):
    return Session.objects.filter(
        type=SessionType.SESSION, created_at__isnull=False, user_message_count__gt=0, hidden=False, **filters
    )


@pytest.mark.parametrize("queryset", [
    lambda: visible_sessions(project_id="p"),
    lambda: visible_sessions(project_id="p").order_by("-mtime")[:50],
    lambda: visible_sessions().order_by("-mtime")[:50],
], ids=["project count", "project list", "all projects list"])
def test_visible_session_filters_never_use_a_hidden_index(queryset):
    assert "hidden" not in plan(queryset())


def test_hidden_sessions_use_the_partial_index():
    assert "idx_session_hidden" in plan(Session.objects.filter(hidden=True).values_list("id", flat=True))
