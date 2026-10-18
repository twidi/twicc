import pytest
from django.db import connection

from twicc.core.models import TimelineEvent
from twicc.core.services import timeline_backfill

pytestmark = pytest.mark.django_db


def test_the_session_kind_index_exists_in_the_model_and_in_the_database():
    assert "idx_timeline_session_kind" in {index.name for index in TimelineEvent._meta.indexes}
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, TimelineEvent._meta.db_table)
    index = constraints["idx_timeline_session_kind"]
    assert index["index"] and index["columns"] == ["session_id", "kind"]


def test_the_known_check_is_served_by_the_session_kind_index():
    # The query the creation-row engine runs to know what a session already has.
    queryset = (
        TimelineEvent.objects.filter(session_id="s1", kind__in=timeline_backfill._RELEVANT_KINDS)
        .values_list("kind", flat=True).distinct()
    )
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
        plan = " ".join(str(row[-1]) for row in cursor.fetchall())
    assert "idx_timeline_session_kind" in plan
