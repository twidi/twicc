import pytest
from django.db import connection

from twicc.core.models import TimelineEvent

pytestmark = pytest.mark.django_db


def test_the_session_kind_index_exists_in_the_model_and_in_the_database():
    assert "idx_timeline_session_kind" in {index.name for index in TimelineEvent._meta.indexes}
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, TimelineEvent._meta.db_table)
    index = constraints["idx_timeline_session_kind"]
    assert index["index"] and index["columns"] == ["session_id", "kind"]
