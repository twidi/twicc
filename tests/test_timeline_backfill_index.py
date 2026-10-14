import pytest
from django.db import connection
from django.db.models import Exists, OuterRef

from twicc.core.models import Session, SessionType, TimelineEvent, TimelineKind

pytestmark = pytest.mark.django_db


def test_the_session_kind_index_exists_in_the_model_and_in_the_database():
    assert "idx_timeline_session_kind" in {index.name for index in TimelineEvent._meta.indexes}
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, TimelineEvent._meta.db_table)
    index = constraints["idx_timeline_session_kind"]
    assert index["index"] and index["columns"] == ["session_id", "kind"]


def test_the_candidate_anti_join_is_served_by_the_session_kind_index():
    has_created = TimelineEvent.objects.filter(session_id=OuterRef("id"), kind=TimelineKind.SESSION_CREATED)
    queryset = (
        Session.objects.filter(type=SessionType.SESSION, created_at__isnull=False)
        .filter(~Exists(has_created))
        .order_by("-mtime", "-id")
        .values_list("id", flat=True)
    )
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
        plan = " ".join(str(row[-1]) for row in cursor.fetchall())
    assert "idx_timeline_session_kind" in plan
