import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

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
    # The query the creation-row engine runs to know whether a session already has its creation row.
    # Captured as run, so the plan is the one of the exact SQL ``exists()`` sends.
    with CaptureQueriesContext(connection) as captured:
        timeline_backfill._creation_rows("s1").exists()
    (query,) = captured.captured_queries
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + query["sql"])
        plan = " ".join(str(row[-1]) for row in cursor.fetchall())
    assert "idx_timeline_session_kind" in plan
