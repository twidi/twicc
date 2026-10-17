from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.session_update import (
    apply_session_archived_change, apply_session_mute_on_user_turn_change, apply_session_pinned_change,
)
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-flags", directory="/tmp/tl-flags")


@pytest.fixture(autouse=True)
def no_agent_side_effects(monkeypatch):
    from twicc import terminal
    from twicc.agent import registry

    monkeypatch.setattr(registry, "get_agent_manager_registry",
                        lambda: SimpleNamespace(kill_agent=AsyncMock(), get_agent_info=lambda session_id: None,
                                                get_active_agents=list))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", lambda scope: None)


def make(project, **kw):
    return Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex", **kw)


@pytest.mark.parametrize("before,after,expected", [
    (False, True, ["archived.started"]),
    (True, False, ["archived.ended"]),
    (True, True, []),
    (False, False, []),
])
def test_archive_writes_only_on_a_real_flip(project, before, after, expected):
    session = make(project, archived=before)
    run_with_writer(lambda: apply_session_archived_change(session, after))
    assert kinds("s1") == expected


def test_archive_with_also_unpin_writes_pinned_ended_caused_by_the_archive(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    run_with_writer(lambda: apply_session_archived_change(session, True, also_unpin=True))
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [r.kind for r in rows] == ["pinned.started", "archived.started", "pinned.ended"]
    assert rows[2].caused_by_id == rows[1].id
    assert rows[2].data == {"scope": "project"}  # inherited, not passed by the hook
    assert rows[1].actor == "user" and rows[1].project_id == project.id


def test_archive_with_also_unpin_on_an_already_archived_pinned_session_only_ends_the_pin(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    Session.objects.filter(id="s1").update(archived=True)
    session = Session.objects.get(id="s1")
    run_with_writer(lambda: apply_session_archived_change(session, True, also_unpin=True))
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [r.kind for r in rows] == ["pinned.started", "pinned.ended"]  # no archive row: no flip
    assert rows[1].caused_by_id is None and rows[1].data == {"scope": "project"}
    assert Session.objects.get(id="s1").pinned is None
    # A second call finds nothing pinned any more: no duplicate row.
    session = Session.objects.get(id="s1")
    run_with_writer(lambda: apply_session_archived_change(session, True, also_unpin=True))
    assert kinds("s1") == ["pinned.started", "pinned.ended"]


def test_archive_with_also_unpin_on_an_unpinned_session_writes_no_pinned_event(project):
    session = make(project)
    run_with_writer(lambda: apply_session_archived_change(session, True, also_unpin=True))
    assert kinds("s1") == ["archived.started"]


def test_pinning_writes_started_with_the_scope(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "pinned.started" and row.data == {"scope": "project"} and row.caused_by_id is None


def test_unpinning_writes_ended_with_the_inherited_scope(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "all"))
    run_with_writer(lambda: apply_session_pinned_change(session, None))
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [(r.kind, r.data) for r in rows] == [
        ("pinned.started", {"scope": "all"}), ("pinned.ended", {"scope": "all"}),
    ]


def test_changing_the_scope_ends_the_old_pin_then_starts_the_new_one(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    run_with_writer(lambda: apply_session_pinned_change(session, "all"))
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [(r.kind, r.data["scope"]) for r in rows] == [
        ("pinned.started", "project"), ("pinned.ended", "project"), ("pinned.started", "all"),
    ]
    assert rows[2].caused_by_id == rows[1].id and rows[1].caused_by_id is None
    assert rows[1].occurred_at == rows[2].occurred_at


@pytest.mark.parametrize("value", [None, "workspace"])
def test_pin_to_the_same_value_writes_nothing(project, value):
    session = make(project, pinned=value)
    run_with_writer(lambda: apply_session_pinned_change(session, value))
    assert kinds("s1") == []


@pytest.mark.parametrize("before,after,expected", [
    (False, True, ["muted.started"]), (True, False, ["muted.ended"]), (True, True, []), (False, False, []),
])
def test_mute_writes_only_on_a_real_flip(project, before, after, expected):
    session = make(project, mute_on_user_turn=before)
    run_with_writer(lambda: apply_session_mute_on_user_turn_change(session, after))
    assert kinds("s1") == expected


def hide_unhide(session, hide):
    from twicc.core.services import session_visibility

    async def go():
        fn = session_visibility.hide_session if hide else session_visibility.unhide_session
        return await fn(session)

    with patch.object(session_visibility, "_push_hidden_to_live_agent", lambda *a: None), \
            patch.object(session_visibility, "_broadcast_session_removed", AsyncMock()), \
            patch.object(session_visibility, "_broadcast_session_updated", AsyncMock()), \
            patch.object(session_visibility, "_broadcast_project_updated", AsyncMock()):
        return run_with_writer(go)


def test_hide_and_unhide_write_one_event_each(project):
    session = make(project, permission_mode="yolo", question_widget=False)
    assert hide_unhide(session, True).success
    assert kinds("s1") == ["hidden.started"]
    assert hide_unhide(session, False).success
    assert kinds("s1") == ["hidden.started", "hidden.ended"]


def test_hiding_an_already_hidden_session_writes_nothing(project):
    session = make(project, hidden=True, permission_mode="yolo", question_widget=False)
    assert hide_unhide(session, True).success
    assert kinds("s1") == []
