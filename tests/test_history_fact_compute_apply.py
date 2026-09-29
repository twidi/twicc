"""Normal compute publishes complete history facts with its session version."""

import asyncio
import queue
import threading

import orjson
import pytest
from django.core.exceptions import FieldDoesNotExist

from twicc.core.enums import Provider
from twicc.core.models import HistoryFactKind, Project, Session, SessionHistoryFact, SessionItem
from twicc.providers import db_writer
from twicc.providers.codex.compute import get_compute
from twicc.providers.codex.rollout_migration import ReplaceCodexHistoryJob, _apply_replace_codex_history_job
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.helpers import get_provider_helpers
from twicc.providers.history_facts import HistoryFact, append_history_facts, history_facts_are_current


@pytest.fixture
def session(db):
    project = Project.objects.create(id="fact-apply-project")
    return Session.objects.create(
        id="fact-apply-session", project=project, provider=Provider.CODEX,
        file_path="history.jsonl", last_offset=100,
    )


def _fact(line_num=1, key="call-new"):
    return HistoryFact(line_num, HistoryFactKind.TOOL_CALL, key, {"name": "exec_command"})


def _message(session, facts, *, offset=None):
    return {
        "session_id": session.id,
        "observed_last_offset": session.last_offset if offset is None else offset,
        "history_facts": [fact._asdict() for fact in facts],
        "session_fields": {
            "compute_version": get_provider_helpers(session.provider).current_compute_version,
            "title": "Computed title",
        },
    }


def test_final_apply_replaces_facts_and_publishes_metadata_with_version(session):
    append_history_facts(session.id, [_fact(key="call-old")])

    result = BaseSessionCompute.apply_session_complete(_message(session, [_fact(2)]))

    session.refresh_from_db()
    assert result.outcome == "applied"
    assert session.title == "Computed title"
    assert history_facts_are_current(session)
    assert list(SessionHistoryFact.objects.filter(session=session).values_list("line_num", "key")) == [(2, "call-new")]


@pytest.mark.parametrize("missing", [False, True])
def test_superseded_or_missing_apply_publishes_nothing(session, missing):
    append_history_facts(session.id, [_fact(key="call-old")])
    msg = _message(session, [_fact(2)], offset=99)
    if missing:
        session.delete()

    result = BaseSessionCompute.apply_session_complete(msg)

    assert result.outcome == ("missing" if missing else "superseded")
    assert not SessionHistoryFact.objects.filter(key="call-new").exists()
    if not missing:
        session.refresh_from_db()
        assert session.title != "Computed title"
        assert not history_facts_are_current(session)
        assert list(SessionHistoryFact.objects.filter(session=session).values_list("key", flat=True)) == ["call-old"]


def test_failure_after_fact_replacement_rolls_back_facts_and_version(session):
    append_history_facts(session.id, [_fact(key="call-old")])
    msg = _message(session, [_fact(2)])
    msg["session_fields"]["missing_column"] = "failure after fact replacement"

    with pytest.raises(FieldDoesNotExist):
        BaseSessionCompute.apply_session_complete(msg)

    session.refresh_from_db()
    assert not history_facts_are_current(session)
    assert list(SessionHistoryFact.objects.filter(session=session).values_list("key", flat=True)) == ["call-old"]


def test_missing_complete_fact_payload_cannot_publish_current_version(session):
    SessionItem.objects.create(session=session, line_num=1, content="{}")
    msg = _message(session, [_fact()])
    del msg["history_facts"]

    with pytest.raises(ValueError, match="history_facts"):
        BaseSessionCompute.apply_session_complete(msg)

    session.refresh_from_db()
    assert not history_facts_are_current(session)
    assert not SessionHistoryFact.objects.filter(session=session).exists()


def test_item_preapply_does_not_publish_facts_or_version(session):
    item = SessionItem.objects.create(session=session, line_num=1, content="{}")

    outcome = BaseSessionCompute.apply_session_items_chunk(
        session.id, session.last_offset, ["kind"], [{"id": item.id, "kind": "user_message"}], [],
    )

    session.refresh_from_db()
    item.refresh_from_db()
    assert outcome == "ok"
    assert item.kind == "user_message"
    assert not history_facts_are_current(session)
    assert not SessionHistoryFact.objects.filter(session=session).exists()


def test_codex_rollout_replacement_removes_old_facts_with_old_items(session):
    SessionItem.objects.create(session=session, line_num=1, content="{}")
    append_history_facts(session.id, [_fact(key="call-old")])
    session.compute_version = get_provider_helpers(session.provider).current_compute_version
    session.save(update_fields=["compute_version"])
    loop = asyncio.new_event_loop()
    try:
        job = ReplaceCodexHistoryJob(Provider.CODEX, session.id, [(1, "new")], 200, 1, 1.0, loop.create_future())
        _apply_replace_codex_history_job(job)
    finally:
        loop.close()

    session.refresh_from_db()
    assert list(session.items.values_list("content", flat=True)) == ["new"]
    assert not SessionHistoryFact.objects.filter(session=session).exists()
    assert not history_facts_are_current(session)


def test_initial_sync_raw_rows_stay_outdated_until_normal_compute(session):
    SessionItem.objects.create(session=session, line_num=1, content=orjson.dumps({
        "type": "response_item", "payload": {
            "type": "function_call", "call_id": "call-raw", "name": "exec_command", "arguments": "{}",
        },
    }).decode())
    session.refresh_from_db()
    assert not history_facts_are_current(session)
    assert not SessionHistoryFact.objects.filter(session=session).exists()

    results = queue.Queue()
    get_compute().compute_session_metadata(session.id, results, run_id=7)
    messages = [orjson.loads(results.get()) for _ in range(results.qsize())]
    complete = next(msg for msg in messages if msg["type"] == "session_complete")
    assert BaseSessionCompute.apply_session_complete(complete).outcome == "applied"

    session.refresh_from_db()
    assert history_facts_are_current(session)
    assert list(SessionHistoryFact.objects.filter(session=session).values_list("key", flat=True)) == ["call-raw"]


def test_live_created_session_has_facts_for_committed_rows_before_current_lookup(session, tmp_path):
    session.last_offset = 0
    session.save(update_fields=["last_offset"])
    session.refresh_from_db()
    assert not history_facts_are_current(session)
    assert not SessionHistoryFact.objects.filter(session=session).exists()

    path = tmp_path / "live.jsonl"
    path.write_bytes(b"\n".join(orjson.dumps({
        "type": "response_item", "payload": {
            "type": "function_call", "call_id": call_id, "name": "exec_command", "arguments": "{}",
        },
    }) for call_id in ("call-live-1", "call-live-2")) + b"\n")
    get_compute().sync_session_items_from_file(session, path)

    session.refresh_from_db()
    assert list(SessionItem.objects.filter(session=session).values_list("line_num", flat=True)) == [1, 2]
    assert set(SessionHistoryFact.objects.filter(session=session).values_list("key", flat=True)) == {
        "call-live-1", "call-live-2",
    }
    assert not history_facts_are_current(session)


@pytest.mark.django_db(transaction=True)
def test_cancelled_caller_waits_for_atomic_fact_and_version_publication(monkeypatch):
    project = Project.objects.create(id="fact-cancellation-project")
    session = Session.objects.create(
        id="fact-cancellation-session", project=project, provider=Provider.CODEX,
        file_path="history.jsonl", last_offset=100,
    )
    started = threading.Event()
    release = threading.Event()
    from twicc.providers import compute_base

    replace = compute_base.replace_history_facts

    def blocked_replace(session_id, facts):
        replace(session_id, facts)
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(compute_base, "replace_history_facts", blocked_replace)

    async def scenario():
        db_writer.start_db_writer()
        try:
            caller = asyncio.create_task(db_writer.run_under_db_write_lock(
                lambda: db_writer.run_compute_sync(BaseSessionCompute.apply_session_complete, _message(session, [_fact()]))
            ))
            assert await asyncio.to_thread(started.wait, 2)
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await caller
        finally:
            release.set()
            await db_writer.stop_db_writer()

    asyncio.run(scenario())
    session.refresh_from_db()
    assert history_facts_are_current(session)
    assert session.title == "Computed title"
    assert list(SessionHistoryFact.objects.filter(session=session).values_list("key", flat=True)) == ["call-new"]
