"""Committed title maps cannot replace choices with stale placeholders or echoes."""

import asyncio
import queue
from types import SimpleNamespace
from unittest.mock import Mock

import orjson
import pytest

from django.db import transaction

from twicc import title_echo
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.providers.claude_code import titles
from twicc.providers.claude_code.compute import get_compute
from twicc.providers.compute_base import BaseSessionCompute, ComputeApplyResult
from twicc.providers.live_sync import LiveSyncLimits, LiveSyncUpdates, RawLiveSlice, merge_live_updates

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def clean_guards(monkeypatch):
    monkeypatch.setattr(title_echo, "_automatic_title_echoes", {})
    monkeypatch.setattr(titles, "_protected_titles", {})


@pytest.fixture
def source():
    return Session.objects.create(id="source", project=Project.objects.create(id="title-compute-project"),
                                  provider=Provider.CLAUDE_CODE, user_message_count=1)


def user_line():
    return {"type": "user", "uuid": "user-one", "message": {"role": "user", "content": "First message"}}


def provider_line(target="source", title="Provider"):
    return {"type": "custom-title", "sessionId": target, "customTitle": title}


def full_message(source, lines):
    for number, line in enumerate(lines, 1):
        SessionItem.objects.create(session=source, line_num=number, content=orjson.dumps(line).decode())
    results = queue.Queue()
    get_compute().compute_session_metadata(source.id, results, run_id=0)
    messages = [orjson.loads(raw) for raw in list(results.queue)]
    return next(msg for msg in messages if msg["type"] == "session_complete")


def live_apply(source, lines, tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_bytes(b"".join(orjson.dumps(line) + b"\n" for line in lines))
    return get_compute().sync_session_slice(source.id, path, limits=LiveSyncLimits()).updates


def test_full_compute_ships_separate_maps(source):
    msg = full_message(source, [user_line(), provider_line()])
    assert msg["placeholder_titles"] == {source.id: "First message"}
    assert msg["provider_titles"] == {source.id: "Provider"}
    assert "titles" not in msg


@pytest.mark.parametrize("path", ["full", "live"])
def test_stale_placeholder_cannot_replace_user_title(source, tmp_path, path):
    if path == "full":
        msg = full_message(source, [user_line()])
        Session.objects.filter(id=source.id).update(title="User choice", title_origin="user")
        result = BaseSessionCompute.apply_session_complete(msg)
    else:
        # Keep the compute snapshot from before the explicit writer commits.
        Session.objects.filter(id=source.id).update(title="User choice", title_origin="user")
        data = orjson.dumps(user_line()) + b"\n"
        file = tmp_path / "source.jsonl"
        file.write_bytes(data)
        with transaction.atomic():
            result = get_compute()._sync_session_slice(source, file, RawLiveSlice([data], len(data), False, len(data)))
    source.refresh_from_db()
    assert (source.title, source.title_origin) == ("User choice", "user")
    assert result.title_updated_session_ids == ()


@pytest.mark.parametrize("path", ["base", "claude", "full"])
def test_automatic_echo_consumes_guard_before_protection(source, monkeypatch, path):
    Session.objects.filter(id=source.id).update(title="User choice", title_origin="user")
    title_echo.record_automatic_title_push(source.id, "Automatic")
    protection = Mock(wraps=titles.check_protected_title)
    correction = Mock()
    monkeypatch.setattr(titles, "check_protected_title", protection)
    monkeypatch.setattr(titles, "rename_session_in_jsonl", correction)
    titles.protect_title(source.id, "User choice")

    def apply():
        if path == "full":
            return BaseSessionCompute.apply_session_complete({"session_id": source.id, "history_facts": [],
                                                              "provider_titles": {source.id: "Automatic"}})
        if path == "base":
            return BaseSessionCompute.apply_session_title(get_compute(), source.id, "Automatic")
        return get_compute().apply_session_title(source.id, "Automatic")

    result = apply()
    source.refresh_from_db()
    assert (source.title, source.title_origin) == ("User choice", "user")
    if path == "full":
        assert result.title_updated_session_ids == ()
    else:
        assert result is False
    protection.assert_not_called()
    correction.assert_not_called()
    # A later external rename applies once the first matching echo has been consumed.
    titles._protected_titles.clear()
    apply()
    source.refresh_from_db()
    assert (source.title, source.title_origin) == ("Automatic", "user")


@pytest.mark.parametrize("path", ["live", "full"])
@pytest.mark.parametrize("origin", ["", "auto", "user"])
@pytest.mark.parametrize("old_title", [None, "Before", "Provider"])
def test_changed_target_ids_only_include_committed_changes(source, tmp_path, path, origin, old_title):
    target = Session.objects.create(
        id="target", project=source.project, file_path="target.jsonl", title=old_title, title_origin=origin,
    )
    lines = [provider_line(target.id), provider_line("missing")]
    if path == "live":
        result = live_apply(source, lines, tmp_path)
    else:
        result = BaseSessionCompute.apply_session_complete(full_message(source, lines))
    target.refresh_from_db()
    assert (target.title, target.title_origin) == ("Provider", "auto" if old_title is None else origin)
    assert result.title_updated_session_ids == (() if old_title == "Provider" else (target.id,))


@pytest.mark.parametrize("path", ["live", "full"])
def test_placeholder_then_provider_has_provider_precedence(source, tmp_path, path):
    lines = [provider_line(), user_line()]
    if path == "live":
        result = live_apply(source, lines, tmp_path)
    else:
        result = BaseSessionCompute.apply_session_complete(full_message(source, lines))
    source.refresh_from_db()
    assert (source.title, source.title_origin) == ("Provider", "auto")
    assert result.title_updated_session_ids == (source.id,)


def test_merged_slices_deduplicate_changed_targets():
    empty = LiveSyncUpdates.empty()
    assert empty.title_updated_session_ids == ()
    left = empty._replace(title_updated_session_ids=("b", "a"))
    right = empty._replace(title_updated_session_ids=("b", "c"))
    assert merge_live_updates(left, right).title_updated_session_ids == ("b", "a", "c")


@pytest.mark.parametrize("path", ["live", "full"])
@pytest.mark.parametrize("source_hidden", [False, True])
@pytest.mark.parametrize("target_state", ["changed", "unchanged", "echo", "hidden", "empty"])
def test_other_target_broadcasts_follow_target_visibility(
    source, tmp_path, monkeypatch, path, source_hidden, target_state,
):
    from watchfiles import Change
    from twicc import search
    from twicc.providers import db_writer
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.sessions_watcher import ParsedSessionFile

    Session.objects.filter(id=source.id).update(hidden=source_hidden, title="Source", title_origin="user")
    source.refresh_from_db()
    target = Session.objects.create(
        id="target", project=source.project, provider=Provider.CLAUDE_CODE, file_path="target.jsonl",
        title="Provider" if target_state == "unchanged" else "Before", title_origin="user",
        user_message_count=0 if target_state == "empty" else 1, hidden=target_state == "hidden",
    )
    if target_state == "echo":
        title_echo.record_automatic_title_push(target.id, "Provider")
    lines = [user_line(), provider_line(target.id), provider_line(target.id), provider_line(source.id, "Source")]
    events = []

    async def capture(group, event):
        if path == "full":
            assert not db_writer._db_write_lock.locked()
        events.append(event["data"])

    channel = SimpleNamespace(group_send=capture)
    monkeypatch.setattr(db_writer, "get_channel_layer", lambda: channel)
    monkeypatch.setattr(search, "is_initialized", lambda: False)
    if path == "full":
        message = full_message(source, lines)
    else:
        file = tmp_path / "source.jsonl"
        file.write_bytes(b"".join(orjson.dumps(line) + b"\n" for line in lines))

    async def scenario():
        db_writer.start_db_writer()
        run_id = None
        try:
            if path == "full":
                run_id, _ = db_writer.arm_compute_completion(
                    Provider.CLAUDE_CODE, display_session_ids=set(), total_display=0,
                )
                await db_writer._process_compute_message({**message, "run_id": run_id})
            else:
                watcher = ClaudeCodeSessionsWatcher()
                parsed = ParsedSessionFile(source.project_id, source.id, SessionType.SESSION, str(file))
                await watcher._process_parsed_session_change(file, parsed, Change.modified, channel)
        finally:
            if run_id is not None:
                db_writer._compute_states.pop(run_id, None)
                db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_db_writer()

    asyncio.run(scenario())
    target.refresh_from_db()
    assert (target.title, target.title_origin) == (
        "Before" if target_state == "echo" else "Provider", "user",
    )
    updates = [event["session"] for event in events if event["type"] == "session_updated"]
    assert [s["id"] for s in updates].count(source.id) == (0 if source_hidden else 1)
    target_updates = [s for s in updates if s["id"] == target.id]
    assert len(target_updates) == (1 if target_state == "changed" else 0)
    if target_updates:
        assert (target_updates[0]["title"], target_updates[0]["title_origin"]) == ("Provider", "user")


def test_protection_rejection_still_consumes_automatic_echo(source, monkeypatch):
    Session.objects.filter(id=source.id).update(title="Protected", title_origin="auto")
    title_echo.record_automatic_title_push(source.id, "Automatic")
    titles.protect_title(source.id, "Protected")
    correction = Mock()
    monkeypatch.setattr(titles, "rename_session_in_jsonl", correction)
    assert get_compute().apply_session_title(source.id, "Automatic") is False
    source.refresh_from_db()
    assert (source.title, source.title_origin) == ("Protected", "auto")
    correction.assert_called_once_with(source.id, "Protected")
    assert not title_echo.should_skip_automatic_title_echo(
        source.id, "Automatic", title="Protected", title_origin="user",
    )


def test_full_broadcast_deduplicates_source_ancestor_and_targets(monkeypatch):
    from twicc.providers import db_writer

    sent = []

    async def capture(session_id):
        assert not db_writer._db_write_lock.locked()
        sent.append(session_id)

    monkeypatch.setattr(db_writer, "broadcast_session_updated", capture)
    monkeypatch.setattr(BaseSessionCompute, "apply_session_complete", staticmethod(
        lambda _: ComputeApplyResult("applied", "ancestor", ("source", "ancestor", "target", "target")),
    ))

    async def scenario():
        db_writer.start_db_writer()
        run_id, _ = db_writer.arm_compute_completion(Provider.CLAUDE_CODE, display_session_ids=set(), total_display=0)
        try:
            await db_writer._process_compute_message({
                "type": "session_complete", "provider": Provider.CLAUDE_CODE, "run_id": run_id,
                "session_id": "source",
            })
        finally:
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_db_writer()

    asyncio.run(scenario())
    assert sent == ["source", "ancestor", "target"]


def test_live_merged_slices_broadcast_changed_target_once(source, tmp_path, monkeypatch):
    from watchfiles import Change
    from twicc import search
    from twicc.providers import db_writer, sessions_watcher
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.sessions_watcher import ParsedSessionFile

    target = Session.objects.create(id="target", project=source.project, provider=Provider.CLAUDE_CODE,
                                    file_path="target.jsonl", title="Before", title_origin="user", user_message_count=1)
    file = tmp_path / "source.jsonl"
    file.write_bytes(b"".join(orjson.dumps(line) + b"\n" for line in [
        provider_line(target.id, "Intermediate"), provider_line(target.id),
    ]))
    sent = []

    async def capture(group, event):
        sent.append(event["data"])

    def merged_sync(compute, session_id, path, limits):
        first = compute.sync_session_slice(session_id, path, limits=LiveSyncLimits(max_lines=1))
        second = compute.sync_session_slice(session_id, path, limits=LiveSyncLimits(max_lines=1))
        assert first.updates.title_updated_session_ids == (target.id,)
        assert second.updates.title_updated_session_ids == (target.id,)
        return second._replace(updates=merge_live_updates(first.updates, second.updates))

    monkeypatch.setattr(sessions_watcher, "_sync_live_session_items", merged_sync)
    monkeypatch.setattr(search, "is_initialized", lambda: False)

    async def scenario():
        db_writer.start_db_writer()
        try:
            watcher = ClaudeCodeSessionsWatcher()
            parsed = ParsedSessionFile(source.project_id, source.id, SessionType.SESSION, str(file))
            await watcher._process_parsed_session_change(file, parsed, Change.modified,
                                                        SimpleNamespace(group_send=capture))
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(scenario())
    updates = [event["session"] for event in sent if event["type"] == "session_updated"]
    target_updates = [session for session in updates if session["id"] == target.id]
    assert len(target_updates) == 1
    assert (target_updates[0]["title"], target_updates[0]["title_origin"]) == ("Provider", "user")
