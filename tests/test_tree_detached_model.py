import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType, TimelineEvent, TimelineKind
from twicc.core.timeline import validate_data


@pytest.mark.django_db
def test_session_defaults_to_attached():
    project = Project.objects.create(id="-tree-detached", directory="/tmp/tree-detached")
    session = Session.objects.create(
        id="tree-detached-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
    )
    assert session.tree_detached is False


def test_timeline_kind_values():
    assert TimelineKind.TREE_DETACHED_STARTED == "tree_detached.started"
    assert TimelineKind.TREE_DETACHED_ENDED == "tree_detached.ended"
    max_length = TimelineEvent._meta.get_field("kind").max_length
    assert max_length >= 40
    for kind in (TimelineKind.TREE_DETACHED_STARTED, TimelineKind.TREE_DETACHED_ENDED):
        assert len(kind.value) <= max_length


def test_kinds_take_no_payload():
    with pytest.raises(ValueError):
        validate_data("tree_detached.started", {"x": 1})
    assert validate_data("tree_detached.started", None) == {}
