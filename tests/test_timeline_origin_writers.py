"""Request-caused writers take the origin of the request (design section 6, writers list; verification 6, 8b)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.timeline_helpers import columns, run_with_writer
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services.session_update import (
    apply_session_archived_change, apply_session_mute_on_user_turn_change, apply_session_pinned_change,
    archive_sessions_bulk, persist_session_settings,
)
from twicc.drop_requests_watcher import execute_drop_payload
from twicc.request_origin import (
    ORIGIN_PAYLOAD_KEY, agent_cli_origin, human_cli_origin, mcp_session_origin, origin_scope, origin_to_payload,
)

pytestmark = pytest.mark.django_db(transaction=True)

AGENT = mcp_session_origin("sess-A")
AGENT_COLUMNS = ("agent", "mcp_internal", "sess-A", None)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-origin-writers", directory="/tmp/tl-origin-writers")


@pytest.fixture(autouse=True)
def no_agent_side_effects(monkeypatch):
    from twicc import terminal
    from twicc.agent import registry

    monkeypatch.setattr(registry, "get_agent_manager_registry",
                        lambda: SimpleNamespace(kill_agent=AsyncMock(), get_agent_info=lambda session_id: None,
                                                get_active_agents=list))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", lambda scope: None)


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def under(origin, factory):
    """Run ``factory()`` with the DB writer started, inside an origin scope."""

    async def runner():
        with origin_scope(origin):
            return await factory()

    return run_with_writer(runner)


def rows(session_id="s1"):
    return list(TimelineEvent.objects.filter(session_id=session_id).order_by("id"))


def test_archive_and_unarchive_take_the_request_origin(project):
    session = make(project)
    under(AGENT, lambda: apply_session_archived_change(session, True))
    under(agent_cli_origin("sess-B"), lambda: apply_session_archived_change(session, False))
    started, ended = rows()
    assert (started.kind, columns(started)) == ("archived.started", AGENT_COLUMNS)
    assert (ended.kind, columns(ended)) == ("archived.ended", ("agent", "cli", "sess-B", None))


def test_archive_with_also_unpin_gives_both_rows_the_same_origin(project):
    session = make(project)
    under(AGENT, lambda: apply_session_pinned_change(session, "project"))
    under(agent_cli_origin("sess-B"), lambda: apply_session_archived_change(session, True, also_unpin=True))
    pinned, archived, unpinned = rows()
    assert columns(pinned) == AGENT_COLUMNS
    assert columns(archived) == columns(unpinned) == ("agent", "cli", "sess-B", None)


def test_a_pin_scope_change_writes_both_rows_with_one_origin(project):
    session = make(project)
    under(AGENT, lambda: apply_session_pinned_change(session, "project"))
    under(agent_cli_origin("sess-B"), lambda: apply_session_pinned_change(session, "all"))
    assert [columns(r) for r in rows()] == [AGENT_COLUMNS] + [("agent", "cli", "sess-B", None)] * 2


def test_mute_takes_the_request_origin(project):
    session = make(project)
    under(AGENT, lambda: apply_session_mute_on_user_turn_change(session, True))
    assert columns(rows()[0]) == AGENT_COLUMNS


def test_with_no_origin_set_every_writer_gives_the_default(project):
    session = make(project)
    run_with_writer(lambda: apply_session_mute_on_user_turn_change(session, True))
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    assert {columns(r) for r in rows()} == {("user", "ui", None, None)}


def _hide_unhide(session, hide, origin):
    from twicc.core.services import session_visibility

    async def go():
        with origin_scope(origin):
            return await (session_visibility.hide_session if hide else session_visibility.unhide_session)(session)

    with patch.object(session_visibility, "_push_hidden_to_live_agent", lambda *a: None), \
            patch.object(session_visibility, "_broadcast_session_removed", AsyncMock()), \
            patch.object(session_visibility, "_broadcast_session_updated", AsyncMock()), \
            patch.object(session_visibility, "_broadcast_project_updated", AsyncMock()):
        return run_with_writer(go)


def test_hide_through_the_compute_executor_keeps_the_origin(project, monkeypatch):
    # The flip's best-effort search reindex would log a swallowed "index writer is not initialized" error;
    # ``_recompute`` does ``from twicc.search import reindex_session``, so patching the package attribute works.
    monkeypatch.setattr("twicc.search.reindex_session", lambda session_id: None)
    # The flag is written inside ``run_compute_sync`` (asgiref ``sync_to_async`` on a dedicated worker
    # thread): the carrier must survive that hop, or an agent's hide would read as the default.
    session = make(project, permission_mode="yolo", question_widget=False)
    assert _hide_unhide(session, True, AGENT).success
    assert _hide_unhide(session, False, agent_cli_origin("sess-B")).success
    hidden, unhidden = rows()
    assert (hidden.kind, columns(hidden)) == ("hidden.started", AGENT_COLUMNS)
    assert (unhidden.kind, columns(unhidden)) == ("hidden.ended", ("agent", "cli", "sess-B", None))


def test_settings_default_to_the_request_origin(project):
    make(project, selected_model="sonnet")
    with origin_scope(AGENT):
        persist_session_settings("s1", {"selected_model": "opus"})
    assert columns(rows()[0]) == AGENT_COLUMNS


def test_settings_with_an_explicit_system_actor_stay_system_with_null_fields(project):
    make(project, selected_model="sonnet")
    with origin_scope(AGENT):
        persist_session_settings("s1", {"selected_model": "opus"}, actor="system", reason="model_retired")
    assert columns(rows()[0]) == ("system", None, None, None)


def test_the_retire_job_stays_system_with_null_fields(project):
    from twicc.providers import db_writer

    make(project, selected_model="old-model")
    job = db_writer._RetireSessionsJob(
        provider=Provider.CODEX, updates={"s1": {"selected_model": "new-model"}}, future=None,
    )
    with origin_scope(AGENT):
        db_writer._apply_retire_sessions_job(job)
    assert columns(rows()[0]) == ("system", None, None, None)


def test_the_permission_mode_suggestion_takes_the_request_origin(project):
    from twicc.providers.claude_code.ws import update_session_permission_mode

    make(project, permission_mode="default", hidden=True)  # hidden: no broadcast needed
    under(AGENT, lambda: update_session_permission_mode("s1", "acceptEdits"))
    row = rows()[0]
    assert row.reason == "set_mode" and columns(row) == AGENT_COLUMNS


def test_the_bulk_archive_writes_one_origin_on_every_row(project):
    for sid in ("a", "b", "c"):
        make(project, sid)
    with origin_scope(AGENT):
        flipped = archive_sessions_bulk({"a", "b", "c"})
    assert sorted(flipped) == ["a", "b", "c"]
    got = TimelineEvent.objects.filter(kind="archived.started")
    assert got.count() == 3 and {columns(r) for r in got} == {AGENT_COLUMNS}


def test_the_bulk_archive_without_a_carrier_writes_the_default_on_every_row(project):
    for sid in ("a", "b"):
        make(project, sid)
    archive_sessions_bulk({"a", "b"})
    assert {columns(r) for r in TimelineEvent.objects.filter(kind="archived.started")} == {("user", "ui", None, None)}


async def _passthrough_db_write(coro_factory):
    return await coro_factory()


@pytest.mark.parametrize(("origin_key", "expected"), [
    (agent_cli_origin("boss"), ("agent", "cli", "boss", None)),
    (human_cli_origin(), ("user", "cli", None, None)),
    (None, ("user", "ui", None, None)),  # a drop file from an old CLI: the default
])
def test_a_drop_payload_writes_its_origin_into_the_event(project, origin_key, expected):
    session = Session.objects.create(
        id="drop-session", project=project, provider=Provider.CODEX.value, type=SessionType.SESSION,
    )
    payload = {"session_id": session.id, "mute_on_user_turn": True}
    if origin_key is not None:
        payload[ORIGIN_PAYLOAD_KEY] = origin_to_payload(origin_key)
    layer = SimpleNamespace(group_send=AsyncMock())
    with patch("twicc.core.services.session_update.get_channel_layer", return_value=layer), \
            patch("twicc.core.services.session_update.ensure_provider_running"), \
            patch("twicc.core.services.session_update.run_under_db_write_lock", side_effect=_passthrough_db_write):
        status = asyncio.run(execute_drop_payload(payload, "session:update_mute_on_user_turn"))
    assert status["status"] == "updated"
    assert columns(TimelineEvent.objects.get(session_id=session.id, kind="muted.started")) == expected
