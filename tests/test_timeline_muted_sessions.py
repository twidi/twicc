"""Muted sessions are opt-in in the UI timeline, including live visibility changes."""

import pytest

from tests.test_timeline_api import call, ids, make_event, make_instance_event, make_session
from tests.test_timeline_hidden_sessions import every_id
from twicc.core.models import Project, Session

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-muted", directory="/tmp/tl-muted")


@pytest.mark.parametrize("params", [{}, {"project_ids": "-tl-muted"}, {"session": "muted"}, {"provider": "codex"}])
def test_muted_sessions_are_opt_in_on_all_pages(project, params):
    make_session(project, "muted", mute_on_user_turn=True)
    make_session(project, "shown")
    muted = [make_event(project=project, session_id="muted", at=index * 2) for index in range(3)]
    visible = [make_event(project=project, session_id="shown", at=index * 2 + 1) for index in range(3)]
    assert not {e.id for e in muted} & set(every_id({**params, "limit": 1}))
    expected = muted if params.get("session") else muted + visible
    assert set(every_id({**params, "include_muted": "1", "limit": 1})) == {e.id for e in expected}
    assert not {e.id for e in muted} & set(ids(call(**params, after_id=0).json()))


def test_muted_opt_in_never_reveals_hidden_sessions(project):
    make_session(project, "secret", hidden=True, mute_on_user_turn=True)
    make_event(project=project, session_id="secret")
    boot = make_instance_event(at=1)
    for params in ({}, {"after_id": 0}):
        assert ids(call(include_muted="1", **params).json()) == [boot.id]


def test_live_mute_and_unmute_reuse_visibility_updates_even_under_event_filters(project):
    session = make_session(project, "s")
    cursor = make_instance_event(at=0)
    make_event("muted.started", project=project, session_id="s", at=1, actor="user", via="ui")
    Session.objects.filter(pk=session.pk).update(mute_on_user_turn=True)
    body = call(after_id=cursor.id, families="messages").json()
    assert body["events"] == []
    assert body["hidden_sessions"] == ["s"]
    assert body["shown_sessions"] == []
    included = call(after_id=cursor.id, include_muted="1").json()
    assert included["hidden_sessions"] == []
    assert included["sessions"]["s"]["mute_on_user_turn"] is True
    show = make_event("muted.ended", project=project, session_id="s", at=2, actor="user", via="ui")
    Session.objects.filter(pk=session.pk).update(mute_on_user_turn=False)
    body = call(after_id=cursor.id, families="messages").json()
    assert body["shown_sessions"] == ["s"]
    assert body["hidden_sessions"] == []
    assert ids(call(session="s").json())[0] == show.id


def test_unhiding_a_still_muted_session_does_not_reveal_it(project):
    make_session(project, "s", mute_on_user_turn=True)
    make_event("hidden.ended", project=project, session_id="s", actor="user", via="ui")
    body = call(after_id=0).json()
    assert body["events"] == [] and body["shown_sessions"] == []


@pytest.mark.parametrize("value", ["true", "2", "garbage"])
def test_include_muted_rejects_invalid_values(value):
    assert call(include_muted=value).status_code == 400
