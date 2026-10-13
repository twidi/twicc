import pytest

from twicc.core.models import Project, Session
from twicc.core.services.session_update import archive_sessions_bulk
from tests.timeline_helpers import kinds

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-bulk", directory="/tmp/tl-bulk")


def test_bulk_archive_flips_all_and_records_one_event_per_flipped_session(project):
    for sid in ("a", "b"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex")
    flipped = archive_sessions_bulk({"a", "b"})
    assert sorted(flipped) == ["a", "b"]
    assert Session.objects.filter(archived=True).count() == 2
    assert kinds("a") == kinds("b") == ["archived.started"]


def test_a_session_archived_meanwhile_records_nothing(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex")
    Session.objects.create(id="b", file_path="b.jsonl", project=project, provider="codex", archived=True)
    assert archive_sessions_bulk({"a", "b"}) == ["a"]
    assert kinds("a") == ["archived.started"] and kinds("b") == []


def test_search_version_is_reset_for_every_requested_session(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex", search_version=7)
    archive_sessions_bulk({"a"})
    assert Session.objects.get(id="a").search_version == 0


def test_empty_set_is_a_noop():
    assert archive_sessions_bulk(set()) == []
