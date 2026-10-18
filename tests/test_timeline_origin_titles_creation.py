"""Titles and created sessions keep the origin captured with them (design 6.5, 6.6; verification 7, 7a, 7b, 7c, 8f, 8i)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from django.utils import timezone

from tests.timeline_helpers import columns, kinds, mark_known, run_with_writer
from twicc.agent.base_manager import BaseAgentManager
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, Session, SessionType, TimelineEvent
from twicc.core.services.session_creation import create_session_from_payload
from twicc.core.services.session_timeline import record_session_created, record_title_change
from twicc.core.services.session_update import update_session_title_from_payload
from twicc.pending_agent_settings import pop_pending_agent_settings
from twicc.pending_session_attributes import (
    PendingSessionAttributes, get_pending_session_attributes, pop_pending_session_attributes,
    rekey_pending_session_attributes, set_pending_session_attributes,
)
from twicc.pending_titles import (
    get_pending_title, get_pending_title_origin, pop_pending_title, rekey_pending_title, set_pending_title,
)
from twicc.providers.helpers import AgentSettings
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile
from twicc.request_origin import (
    SYSTEM_ORIGIN, UI_ORIGIN, agent_cli_origin, mcp_session_origin, origin_scope,
)

pytestmark = pytest.mark.django_db(transaction=True)

AGENT = mcp_session_origin("boss")
AGENT_COLUMNS = ("agent", "mcp_internal", "boss", None)


class _Compute:
    provider = Provider.CODEX
    compute_version = 1


class _Watcher(BaseSessionsWatcher):
    def get_compute(self):
        return _Compute()


class _Manager(BaseAgentManager):
    provider = Provider.CODEX

    async def _create_agent(self, session_id, project_id, cwd, **kwargs):
        return SimpleNamespace(session_id="canonical-id", interrupt_or_kill=AsyncMock())


@pytest.fixture
def project(tmp_path):
    return Project.objects.create(id="-tl-origin-tc", directory=str(tmp_path))


@pytest.fixture(autouse=True)
def clean_pending_stores():
    yield
    for sid in ("s1", "draft-id", "canonical-id", "kid", "flagged", "created-id"):
        pop_pending_title(sid)
        pop_pending_session_attributes(sid)


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def parsed(project, sid):
    return ParsedSessionFile(project.id, sid, SessionType.SESSION, f"{sid}.jsonl")


# --- record_title_change ------------------------------------------------------------------------------------


def test_a_user_title_takes_the_carrier(project):
    make(project)
    with origin_scope(AGENT):
        record_title_change("s1", "New", source="user", old="Old")
    assert columns(TimelineEvent.objects.get(session_id="s1")) == AGENT_COLUMNS


def test_a_user_title_with_a_captured_origin_ignores_the_carrier(project):
    make(project)
    with origin_scope(SYSTEM_ORIGIN):
        record_title_change("s1", "New", source="user", old="Old", origin=AGENT)
    assert columns(TimelineEvent.objects.get(session_id="s1")) == AGENT_COLUMNS


@pytest.mark.parametrize("source", ["auto", "agent"])
def test_a_non_user_title_keeps_actor_agent_and_null_origin_fields(project, source):
    make(project)
    mark_known("s1", project.id)
    with origin_scope(AGENT):
        record_title_change("s1", "New", source=source, old=None)
    assert columns(TimelineEvent.objects.get(session_id="s1", kind="title.changed")) == ("agent", None, None, None)


def test_the_title_update_service_records_the_drop_origin(project):
    make(project, title="Old")
    with patch("twicc.core.services.session_update.ensure_provider_running"), origin_scope(agent_cli_origin("sess-B")):
        run_with_writer(lambda: update_session_title_from_payload({"session_id": "s1", "title": "New"}))
    assert columns(TimelineEvent.objects.get(session_id="s1")) == ("agent", "cli", "sess-B", None)


# --- The pending title and its origin -----------------------------------------------------------------------


def test_the_pending_title_keeps_its_origin_until_popped():
    set_pending_title("s1", "T", origin=AGENT)
    assert get_pending_title("s1") == "T" and get_pending_title_origin("s1") == AGENT
    assert pop_pending_title("s1") == "T"
    assert get_pending_title_origin("s1") is None


def test_setting_a_title_without_an_origin_forgets_an_older_origin():
    set_pending_title("s1", "T", origin=AGENT)
    set_pending_title("s1", "T2")
    assert get_pending_title_origin("s1") is None


def test_rekey_moves_the_title_and_its_origin_together():
    set_pending_title("draft-id", "T", origin=AGENT)
    assert rekey_pending_title("draft-id", "canonical-id") is True
    assert (get_pending_title("draft-id"), get_pending_title_origin("draft-id")) == (None, None)
    assert (get_pending_title("canonical-id"), get_pending_title_origin("canonical-id")) == ("T", AGENT)
    assert rekey_pending_title("nothing", "x") is False


class _FlushAgent:
    session_id = "s1"
    provider = Provider.CODEX


def _flush(carrier):
    manager = _Manager()
    manager._broadcast_session_updated = AsyncMock()

    async def go():
        with origin_scope(carrier):
            return await manager._try_flush_pending_title(_FlushAgent(), "Title")

    helpers = SimpleNamespace(rename_session=AsyncMock())
    with patch("twicc.providers.helpers.get_provider_helpers", return_value=helpers):
        return run_with_writer(go)


def test_a_flushed_title_records_the_captured_origin_even_inside_the_system_marker(project):
    make(project)
    set_pending_title("s1", "Title", origin=AGENT)
    assert _flush(SYSTEM_ORIGIN) is True  # the state-change funnel runs the flush under the system marker
    assert columns(TimelineEvent.objects.get(session_id="s1", kind="title.changed")) == AGENT_COLUMNS


def test_a_flushed_title_without_a_captured_origin_is_the_default_never_system(project):
    make(project)
    set_pending_title("s1", "Title")
    assert _flush(SYSTEM_ORIGIN) is True
    assert columns(TimelineEvent.objects.get(session_id="s1", kind="title.changed")) == ("user", "ui", None, None)


def test_a_flushed_title_ignores_a_different_stale_request_origin(project):
    make(project)
    set_pending_title("s1", "Title", origin=AGENT)
    assert _flush(agent_cli_origin("someone-else")) is True
    assert columns(TimelineEvent.objects.get(session_id="s1", kind="title.changed")) == AGENT_COLUMNS


# --- The Codex draft -> canonical re-key ----------------------------------------------------------------------


def test_rekey_moves_every_field_of_the_pending_attributes():
    set_pending_session_attributes(
        "draft-id", hidden=True, mute_on_user_turn=True, spawned_by_id="boss", spawn_root_id="root",
        annotations={"a": 1}, system_prompt_addendum="addendum", hybrid=True, layout={"k": 1}, ephemeral=True,
        origin=AGENT,
    )
    before = get_pending_session_attributes("draft-id")
    # Every field holds a non-default value, so a field the re-key forgot would show up below.
    assert all(getattr(before, name) not in (None, False, {}, "") for name in PendingSessionAttributes._fields)
    assert rekey_pending_session_attributes("draft-id", "canonical-id") is True
    assert get_pending_session_attributes("draft-id") is None
    assert get_pending_session_attributes("canonical-id") == before
    assert rekey_pending_session_attributes("nothing", "x") is False


def test_a_codex_start_keeps_the_captured_origins_of_the_title_and_the_creation_attributes():
    set_pending_title("draft-id", "Title", origin=AGENT)
    set_pending_session_attributes("draft-id", hidden=True, origin=AGENT)
    manager = _Manager()
    manager.notify_session_bound = AsyncMock()
    manager._register_and_start = AsyncMock()
    canonical = asyncio.run(manager._start_agent(
        "draft-id", "-tl-origin-tc", "/tmp/tl-origin-tc", "Work", False, settings=AgentSettings(),
    ))
    assert canonical == "canonical-id"
    assert get_pending_title_origin("canonical-id") == AGENT
    assert get_pending_session_attributes("canonical-id").origin == AGENT
    assert get_pending_title_origin("draft-id") is None and get_pending_session_attributes("draft-id") is None


def test_an_agent_created_codex_session_keeps_its_origin_after_the_rekey(project):
    set_pending_session_attributes("draft-id", hidden=True, origin=AGENT)
    manager = _Manager()
    manager.notify_session_bound = AsyncMock()
    manager._register_and_start = AsyncMock()
    asyncio.run(manager._start_agent(
        "draft-id", project.id, "/tmp/x", "Work", False, settings=AgentSettings(),
    ))
    now = timezone.now()
    ProcessRun.objects.create(provider="codex", session_id="canonical-id", started_at=now, last_state_change_at=now)
    _Watcher().create_session_sync(parsed(project, "canonical-id"), project)
    assert {columns(r) for r in TimelineEvent.objects.filter(session_id="canonical-id")} == {AGENT_COLUMNS}
    assert kinds("canonical-id") == ["session.created", "hidden.started"]


# --- session.created and its flag rows ------------------------------------------------------------------------


def _twicc_process(sid):
    now = timezone.now()
    ProcessRun.objects.create(provider="codex", session_id=sid, started_at=now, last_state_change_at=now)


def test_a_ui_creation_gives_user_ui_and_no_flag_rows(project):
    _twicc_process("created-id")
    set_pending_session_attributes("created-id", origin=UI_ORIGIN)
    _Watcher().create_session_sync(parsed(project, "created-id"), project)
    [row] = TimelineEvent.objects.filter(session_id="created-id")
    assert row.kind == "session.created" and columns(row) == ("user", "ui", None, None)


@pytest.mark.parametrize(("origin", "expected"), [
    (AGENT, AGENT_COLUMNS),
    (agent_cli_origin("boss"), ("agent", "cli", "boss", None)),
])
def test_a_hidden_and_muted_session_created_by_an_agent_carries_that_agent_on_every_row(project, origin, expected):
    _twicc_process("kid")
    set_pending_session_attributes("kid", hidden=True, mute_on_user_turn=True, origin=origin)
    _Watcher().create_session_sync(parsed(project, "kid"), project)
    rows = list(TimelineEvent.objects.filter(session_id="kid").order_by("id"))
    assert [r.kind for r in rows] == ["session.created", "hidden.started", "muted.started"]
    assert {columns(r) for r in rows} == {expected}
    assert rows[1].caused_by_id == rows[0].id and rows[2].caused_by_id == rows[0].id


def test_a_twicc_session_without_a_captured_origin_gets_the_default_on_every_row(project):
    _twicc_process("flagged")
    set_pending_session_attributes("flagged", hidden=True, mute_on_user_turn=True)  # no origin captured
    # The watcher runs long after the request: a stale carrier must not be read (deferred writer).
    with origin_scope(agent_cli_origin("stale")):
        _Watcher().create_session_sync(parsed(project, "flagged"), project)
    rows = TimelineEvent.objects.filter(session_id="flagged")
    assert rows.count() == 3 and {columns(r) for r in rows} == {("user", "ui", None, None)}


@pytest.mark.parametrize("data_origin", ["external", "sync"])
def test_a_session_found_without_a_twicc_request_is_system_even_with_a_creator_and_a_carrier(project, data_origin):
    session = make(project, hidden=True, mute_on_user_turn=True)
    with origin_scope(AGENT):
        record_session_created(session, origin=data_origin, creator=agent_cli_origin("boss"))
    rows = TimelineEvent.objects.filter(session_id="s1")
    assert rows.count() == 3 and {columns(r) for r in rows} == {("system", None, None, None)}


def test_an_externally_discovered_session_is_system_through_the_watcher(project):
    set_pending_session_attributes("flagged", hidden=True, origin=AGENT)  # attributes without a TwiCC process
    _Watcher().create_session_sync(parsed(project, "flagged"), project)  # no ProcessRun: data.origin "external"
    assert {columns(r) for r in TimelineEvent.objects.filter(session_id="flagged")} == {("system", None, None, None)}


def test_the_creation_service_captures_the_creating_origin(tmp_path):
    # The capture sites in ``create_session_from_payload`` (title and creation attributes) are the only
    # place the creating request is still known; the other tests stash the pending entries by hand.
    project = Project.objects.create(id="-tl-create-cap", directory=str(tmp_path))
    manager = SimpleNamespace(create_session=AsyncMock(return_value="draft-id"))
    registry = SimpleNamespace(get=lambda provider: manager)
    payload = {"session_id": "draft-id", "project_id": project.id, "provider": Provider.CODEX.value,
               "text": "Work", "title": "T", "layout": {}}

    async def go():
        with origin_scope(AGENT):
            return await create_session_from_payload(payload)

    try:
        with patch("twicc.core.services.session_creation.ensure_provider_running"), \
                patch("twicc.agent.registry.get_agent_manager_registry", return_value=registry):
            assert asyncio.run(go()).success is True
        assert get_pending_session_attributes("draft-id").origin == AGENT
        assert get_pending_title_origin("draft-id") == AGENT
    finally:
        pop_pending_agent_settings("draft-id")
        pop_pending_session_attributes("draft-id")
        pop_pending_title("draft-id")
