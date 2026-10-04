"""Composer attachment sends end to end: admission, planning, errors, delivery cleanup.

Spec: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.4 (server release at
delivery), §6.2 (plan target), §6.6 (shape checks, planning off the loop), §8 (WS protocol), §11.

The planner, the target resolver, the managers, the admission and the socket are faked. The
staging release (``lifecycle.release_refs``) is replaced by a recorder, so no test touches disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from twicc import asgi
from twicc.agent import AgentState, SendDeliveryError, ephemeral, send_lanes
from twicc.asgi import WSConsumer
from twicc.core.services.attachments import lifecycle, planner
from twicc.core.services.attachments.planner import AttachmentPlanError
from twicc.core.services.attachments.staging import AttachmentError
from twicc.core.services.attachments.types import (
    AttachmentContent,
    AttachmentManifest,
    AttachmentPlan,
    AttachmentRef,
    ManifestEntry,
    NativePart,
    PlannedEntry,
    PlanTarget,
    StagedEntry,
)
from twicc.core.services.send_message import send_message_to_session_from_payload
from twicc.core.services.session_creation import create_session_from_payload
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.helpers import AgentSettings

ID_1 = "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a01"
ID_2 = "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a02"
REF_1 = AttachmentRef("draft-bucket", ID_1)
REF_2 = AttachmentRef("draft-bucket", ID_2)
REFS = (REF_1, REF_2)
WIRE_REFS = [{"bucket": "draft-bucket", "id": ID_1}, {"bucket": "draft-bucket", "id": ID_2}]
TARGET = PlanTarget("claude_code", False, False, "opus", False, "first_party")
REQUEST_ID = "req-1"
SESSION_ID = "session-id"
_REAL_RESOLVER = asgi.resolve_existing_session_plan_target


def _plan(refs=REFS, target=TARGET) -> AttachmentPlan:
    entries = []
    for n, ref in enumerate(refs, start=1):
        source = StagedEntry(ref, f"f{n}.txt", 5, Path(f"/staged/f{n}.txt"), None)
        entries.append(PlannedEntry(ref, n, f"f{n}.txt", "text", n, len(refs), "file", source, None))
    return AttachmentPlan(target, tuple(entries))


def _content() -> AttachmentContent:
    entries = (ManifestEntry(1, "notes.md", "text", 1, 1, "inline", None),)
    manifest = AttachmentManifest(SESSION_ID, None, entries)
    return AttachmentContent((NativePart("text", "text/plain", "body"),), manifest, "")


@pytest.fixture(autouse=True)
def _clean_state():
    ephemeral.clear()
    send_lanes._reset_for_tests()
    yield
    ephemeral.clear()
    send_lanes._reset_for_tests()
    asgi._DETACHED_TASKS.clear()
    asgi._PENDING_HYBRID_SWITCHES.clear()


@pytest.fixture
def released(monkeypatch):
    """Record every staging release instead of touching disk."""
    calls: list[tuple[AttachmentRef, ...]] = []

    async def release_refs(refs):
        calls.append(tuple(refs))

    monkeypatch.setattr(lifecycle, "release_refs", release_refs)
    return calls


@contextlib.contextmanager
def _logs(name: str):
    """Capture *name*'s records whatever the global logging state.

    ``caplog`` is not reliable here: ``settings_test`` disables existing loggers, so whether a
    module logger is still enabled depends on import order across the suite.
    """
    logger = logging.getLogger(name)
    was_disabled, was_level = logger.disabled, logger.level
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.disabled = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.disabled = was_disabled
        logger.setLevel(was_level)


def _text(records) -> str:
    return " ".join(r.getMessage() + (str(r.exc_info[1]) if r.exc_info else "") for r in records)


async def _drain() -> None:
    """Wait for every detached WS task, then for the release tasks they scheduled."""
    for _ in range(5):
        while asgi._DETACHED_TASKS:
            await asyncio.gather(*list(asgi._DETACHED_TASKS), return_exceptions=True)
        pending = list(lifecycle._DELIVERY_RELEASE_TASKS)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for _ in range(5):
            await asyncio.sleep(0)


# ----------------------------------------------------------------------
# Frame shape checks (validate_attachment_frame)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload", [{}, {"attachments": None}, {"attachments": []}, {"attachments": [], "images": [{"x": 1}]}],
)
def test_absent_or_empty_refs_are_no_attachment_content(payload):
    assert planner.validate_attachment_frame(payload) == ()


def test_valid_refs_keep_their_add_order():
    refs = planner.validate_attachment_frame({"attachments": list(reversed(WIRE_REFS)), "images": [], "documents": []})
    assert refs == (REF_2, REF_1)


@pytest.mark.parametrize(
    "attachments",
    [
        {"bucket": "b", "id": ID_1},
        "not-a-list",
        ["not-an-object"],
        [{"bucket": "b"}],
        [{"id": ID_1}],
        [{"bucket": "../x", "id": ID_1}],
        [{"bucket": "b", "id": "not-a-uuid"}],
        [{"bucket": "b", "id": ID_1.upper()}],
        [{"bucket": "b", "id": ID_1.replace("-", "")}],
        [{"bucket": "b", "id": ID_1}, {"bucket": "b", "id": ID_1}],
    ],
)
def test_malformed_noncanonical_or_duplicate_refs_are_invalid(attachments):
    with pytest.raises(AttachmentError) as caught:
        planner.validate_attachment_frame({"attachments": attachments})
    assert caught.value.code == "invalid_attachments"


@pytest.mark.parametrize("legacy", ["images", "documents"])
def test_refs_with_a_legacy_field_are_invalid(legacy):
    with pytest.raises(AttachmentError) as caught:
        planner.validate_attachment_frame({"attachments": WIRE_REFS, legacy: [{"type": "image"}]})
    assert caught.value.code == "invalid_attachments"


def test_same_id_in_two_buckets_is_not_a_duplicate():
    refs = planner.validate_attachment_frame(
        {"attachments": [{"bucket": "a", "id": ID_1}, {"bucket": "b", "id": ID_1}]}
    )
    assert refs == (AttachmentRef("a", ID_1), AttachmentRef("b", ID_1))


def test_inline_shape_error_answers_at_once_and_spawns_nothing():
    async def scenario():
        consumer = WSConsumer()
        consumer.send_json = AsyncMock()
        consumer._handle_send_message_admitted = AsyncMock(return_value=True)
        await consumer._handle_send_message({
            "type": "send_message", "session_id": SESSION_ID, "project_id": "p", "text": "hi",
            "request_id": REQUEST_ID, "attachments": WIRE_REFS + [WIRE_REFS[0]],
        })
        assert asgi._DETACHED_TASKS == set()
        consumer._handle_send_message_admitted.assert_not_called()
        frame = consumer.send_json.await_args.args[0]
        assert frame["type"] == "error"
        assert frame["code"] == "invalid_attachments"
        assert frame["request_id"] == REQUEST_ID
        assert frame["session_id"] == SESSION_ID
        assert ephemeral.pending_snapshot() == []

    asyncio.run(scenario())


# ----------------------------------------------------------------------
# WS existing session
# ----------------------------------------------------------------------


class FakeManager:
    def __init__(self, events: list, *, result=True, error=None, process=False, live_agent=None, gate=None):
        self.events = events
        self.result = result
        self.error = error
        self.process = process
        self.live_agent = live_agent
        self.gate = gate
        self.sent = asyncio.Event()  # set once send_to_session is entered (planning runs in a real thread)
        self.calls: list = []

    def get_agent_info(self, session_id):
        return object() if self.process else None

    def get_live_agent(self, session_id):
        return self.live_agent

    async def send_to_session(self, session_id, project_id, cwd, text, *, settings, images=None, documents=None,
                              **kwargs):
        self.events.append("send")
        self.calls.append(SimpleNamespace(text=text, settings=settings, images=images, documents=documents,
                                          kwargs=kwargs))
        self.sent.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result


class Harness(SimpleNamespace):
    pass


@pytest.fixture
def ws(monkeypatch, released):
    """A consumer whose existing-session path reaches a fake manager through faked planning."""
    events: list = []
    frames: list = []
    h = Harness(events=events, frames=frames, released=released, plan_threads=[], plan_calls=[],
                target_calls=[], plan_error=None)
    h.manager = FakeManager(events)

    helpers = SimpleNamespace(
        resolve_agent_settings=lambda s: (events.append("resolve"), s._replace(selected_model="opus"))[1],
        enforce_agent_settings_consistency=lambda s: (events.append("enforce"), s._replace(effort="high"))[1],
        validate_title=lambda t: SimpleNamespace(error=None, title=t),
    )

    async def resolve_target(**kwargs):
        events.append("target")
        h.target_calls.append(kwargs)
        return TARGET

    def plan_attachments(refs, target, *, text):
        events.append("plan")
        h.plan_threads.append(threading.get_ident())
        h.plan_calls.append((refs, target, text))
        if h.plan_error is not None:
            raise h.plan_error
        return _plan(refs, target)

    async def record_release(refs):
        events.append("release")
        released.append(tuple(refs))

    monkeypatch.setattr(lifecycle, "release_refs", record_release)
    monkeypatch.setattr(planner, "plan_attachments", plan_attachments)
    monkeypatch.setattr("twicc.asgi.resolve_existing_session_plan_target", resolve_target)
    monkeypatch.setattr("twicc.asgi.get_session_provider", AsyncMock(return_value="claude_code"))
    monkeypatch.setattr("twicc.asgi.ensure_provider_running", lambda provider: None)
    monkeypatch.setattr("twicc.asgi.get_provider_helpers", lambda provider: helpers)
    monkeypatch.setattr("twicc.asgi.get_project_directory", AsyncMock(return_value="/project"))
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda p: h.manager))
    monkeypatch.setattr("twicc.asgi.run_under_db_write_lock", AsyncMock())

    consumer = WSConsumer()

    async def send_json(frame, close=False):
        # A real socket write yields: a release scheduled before the ack would run here.
        for _ in range(5):
            await asyncio.sleep(0)
        events.append(("frame", frame.get("type")))
        frames.append(frame)

    consumer.send_json = send_json
    h.consumer = consumer

    def frame(**extra):
        return {"type": "send_message", "session_id": SESSION_ID, "project_id": "p", "text": "look",
                "request_id": REQUEST_ID, "attachments": WIRE_REFS, **extra}

    h.frame = frame

    async def send(content):
        await consumer._handle_send_message(content)
        await _drain()

    h.send = send
    return h


def _acks(frames):
    return [f for f in frames if f["type"] == "send_ack"]


def _errors(frames):
    return [f for f in frames if f["type"] == "error"]


@pytest.mark.django_db(transaction=True)
def test_delivered_send_acks_first_then_releases_its_refs(ws):
    asyncio.run(ws.send(ws.frame()))

    assert ws.frames == [{"type": "send_ack", "request_id": REQUEST_ID, "session_id": SESSION_ID}]
    assert ws.released == [REFS]
    assert ws.events.index(("frame", "send_ack")) < ws.events.index("release")
    call = ws.manager.calls[0]
    assert call.kwargs["attachment_plan"] == _plan()
    assert call.text == "look"


@pytest.mark.django_db(transaction=True)
def test_settings_are_resolved_and_enforced_before_an_off_loop_plan(ws):
    asyncio.run(ws.send(ws.frame()))

    assert ws.events[:5] == ["resolve", "enforce", "target", "plan", "send"]
    assert ws.plan_threads[0] != threading.get_ident()
    effective = AgentSettings(selected_model="opus", effort="high")
    assert ws.target_calls == [{
        "session_id": SESSION_ID, "provider": "claude_code", "effective_settings": effective,
        "directory": "/project", "ephemeral": False, "live_agent": None,
    }]
    assert ws.plan_calls == [(REFS, TARGET, "look")]
    assert ws.manager.calls[0].settings == effective


@pytest.mark.django_db(transaction=True)
def test_the_live_agent_shapes_the_target(ws):
    live = object()
    ws.manager.live_agent = live
    asyncio.run(ws.send(ws.frame()))
    assert ws.target_calls[0]["live_agent"] is live


@pytest.mark.django_db(transaction=True)
def test_refs_alone_count_as_content_without_a_process(ws):
    asyncio.run(ws.send(ws.frame(text="")))

    assert ws.events.count("send") == 1
    assert ws.plan_calls == [(REFS, TARGET, "")]
    assert ws.released == [REFS]


@pytest.mark.django_db(transaction=True)
def test_empty_refs_and_no_text_stay_a_settings_only_update(ws):
    asyncio.run(ws.send(ws.frame(text="", attachments=[])))
    assert ws.manager.calls == []
    assert ws.plan_calls == []
    assert ws.frames == []
    assert ws.released == []


@pytest.mark.django_db(transaction=True)
def test_a_legacy_send_never_plans_nor_releases(ws):
    content = ws.frame(images=[{"type": "image"}])
    del content["attachments"]
    asyncio.run(ws.send(content))

    assert ws.plan_calls == []
    assert "attachment_plan" not in ws.manager.calls[0].kwargs
    assert ws.manager.calls[0].images == [{"type": "image"}]
    assert len(_acks(ws.frames)) == 1
    assert ws.released == []


@pytest.mark.django_db(transaction=True)
def test_no_request_id_means_no_ack_but_still_a_release(ws):
    content = ws.frame()
    del content["request_id"]
    asyncio.run(ws.send(content))

    assert ws.frames == []
    assert ws.released == [REFS]


@pytest.mark.django_db(transaction=True)
def test_a_lost_ack_still_releases_the_delivered_refs(ws):
    """The socket is closed: the real ``send_json`` logs the failure, the release still runs."""
    del ws.consumer.send_json  # back to WSConsumer.send_json, which swallows the socket error

    async def closed_socket(self, content, close=False):
        raise RuntimeError("socket closed")

    with patch.object(AsyncJsonWebsocketConsumer, "send_json", closed_socket), _logs("twicc.asgi") as records:
        asyncio.run(ws.send(ws.frame()))

    released_refs_after_lost_ack = ws.released[0]
    assert released_refs_after_lost_ack == REFS
    assert "socket closed" in _text(records)


@pytest.mark.django_db(transaction=True)
def test_an_ack_that_raises_still_releases(ws):
    async def raising(frame, close=False):
        raise ConnectionResetError("gone")

    ws.consumer.send_json = raising
    asyncio.run(ws.send(ws.frame()))
    assert ws.released == [REFS]


@pytest.mark.django_db(transaction=True)
def test_commit_failure_sends_no_ack_and_releases_nothing(ws):
    ws.manager.error = SendDeliveryError("disk full", code="attachment_commit_failed")
    asyncio.run(ws.send(ws.frame()))

    ack_count_on_commit_failure = len(_acks(ws.frames))
    assert ack_count_on_commit_failure == 0
    error_frame = _errors(ws.frames)[0]
    assert error_frame["code"] == "attachment_commit_failed"
    assert error_frame["request_id"] == REQUEST_ID
    assert ws.released == []


@pytest.mark.django_db(transaction=True)
def test_a_send_not_delivered_now_releases_nothing(ws):
    ws.manager.result = False
    asyncio.run(ws.send(ws.frame()))
    assert ws.frames == []
    assert ws.released == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "error,code",
    [
        (AttachmentError("attachment_missing", "Attachment not found"), "attachment_missing"),
        (AttachmentError("attachment_not_ready", "Upload not complete"), "attachment_not_ready"),
        (AttachmentPlanError("attachments_with_command", "no"), "attachments_with_command"),
        (FileNotFoundError("vanished"), "attachment_missing"),
        (PermissionError("denied"), "attachment_missing"),
    ],
)
def test_plan_errors_become_error_frames_and_nothing_is_sent(ws, error, code):
    ws.plan_error = error
    asyncio.run(ws.send(ws.frame()))

    assert ws.manager.calls == []
    assert _acks(ws.frames) == []
    error_frame = _errors(ws.frames)[0]
    assert error_frame["code"] == code
    assert error_frame["request_id"] == REQUEST_ID
    assert error_frame["session_id"] == SESSION_ID
    assert ws.released == []


@pytest.mark.django_db(transaction=True)
def test_requires_artifacts_lists_the_file_names(ws):
    ws.plan_error = AttachmentPlanError(
        "attachment_requires_artifacts", "Ephemeral sessions only accept native attachments",
        names=("clip.mp4", "data.zip"),
    )
    asyncio.run(ws.send(ws.frame()))

    error_frame = _errors(ws.frames)[0]
    assert error_frame["code"] == "attachment_requires_artifacts"
    assert error_frame["names"] == ["clip.mp4", "data.zip"]
    assert "clip.mp4, data.zip" in error_frame["message"]
    assert error_frame["request_id"] == REQUEST_ID


@pytest.mark.django_db(transaction=True)
def test_the_hybrid_pending_switch_reaches_the_plan_target(ws, monkeypatch):
    """The real resolver: a pending switch makes the plan target the hybrid CLI."""
    from twicc.core.services.attachments import target as plan_target

    seen = []

    async def resolve_plan_target(**kwargs):
        seen.append(kwargs)
        return TARGET._replace(hybrid=kwargs["hybrid"])

    monkeypatch.setattr("twicc.asgi.resolve_existing_session_plan_target", _REAL_RESOLVER)
    monkeypatch.setattr(plan_target, "resolve_plan_target", resolve_plan_target)
    asgi._PENDING_HYBRID_SWITCHES.add(SESSION_ID)
    asyncio.run(ws.send(ws.frame()))

    assert seen[0]["hybrid"] is True
    assert ws.plan_calls[0][1].hybrid is True


@pytest.mark.django_db(transaction=True)
def test_closed_socket_never_blocks_the_error_log_nor_the_admission_finish(ws, monkeypatch):
    finished = []
    original_finish = ephemeral.finish

    async def finish(admission, *, failed=False):
        finished.append((admission, failed))
        await original_finish(admission, failed=failed)

    monkeypatch.setattr(ephemeral, "finish", finish)

    async def raising(frame, close=False):
        raise ConnectionResetError("gone")

    ws.consumer.send_json = raising
    ws.manager.error = SendDeliveryError("disk full", code="attachment_commit_failed")
    with _logs("twicc.asgi") as records:
        asyncio.run(ws.send(ws.frame()))
    assert "disk full" in _text(records)
    assert len(finished) == 1
    assert ws.released == []


@pytest.mark.django_db(transaction=True)
def test_disconnect_never_cancels_an_admitted_send_and_cleanup_still_runs(ws):
    async def scenario():
        gate = asyncio.Event()
        ws.manager.gate = gate
        await ws.consumer._handle_send_message(ws.frame())
        # Planning runs in a real worker thread: wait on the event, never on a bounded count of loop yields.
        await asyncio.wait_for(ws.manager.sent.wait(), timeout=10)
        assert ws.events.count("send") == 1
        await ws.consumer._cleanup_connection()
        tasks = list(asgi._DETACHED_TASKS)
        assert tasks and not any(t.cancelled() for t in tasks)
        gate.set()
        await _drain()

    asyncio.run(scenario())
    assert ws.released == [REFS]


# ----------------------------------------------------------------------
# WS new session
# ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_new_session_passes_its_refs_to_the_trusted_service_and_releases(ws, monkeypatch):
    from twicc.core.services.session_creation import SessionCreationResult

    ws.manager = None
    monkeypatch.setattr("twicc.asgi.get_session_provider", AsyncMock(return_value=None))
    create = AsyncMock(return_value=SessionCreationResult(True, SESSION_ID, "claude_code", "p", None))
    monkeypatch.setattr("twicc.asgi.create_session_from_payload", create)
    asyncio.run(ws.send(ws.frame(provider="claude_code")))

    assert create.await_args.kwargs["allow_attachments"] is True
    assert create.await_args.args[0]["attachments"] == WIRE_REFS
    assert _acks(ws.frames) == [{"type": "send_ack", "request_id": REQUEST_ID, "session_id": SESSION_ID}]
    assert ws.released == [REFS]


@pytest.mark.django_db(transaction=True)
def test_new_session_errors_keep_their_code_names_and_request_id(ws, monkeypatch):
    from twicc.core.services.session_creation import SessionCreationError, SessionCreationResult

    monkeypatch.setattr("twicc.asgi.get_session_provider", AsyncMock(return_value=None))
    error = SessionCreationError("attachments", "attachment_requires_artifacts", "Ephemeral sessions only accept "
                                 "native attachments: clip.mp4")
    create = AsyncMock(return_value=SessionCreationResult(False, None, None, None, [error], error_names=("clip.mp4",)))
    monkeypatch.setattr("twicc.asgi.create_session_from_payload", create)
    asyncio.run(ws.send(ws.frame(provider="claude_code", ephemeral=True)))

    error_frame = _errors(ws.frames)[0]
    assert error_frame["code"] == "attachment_requires_artifacts"
    assert error_frame["names"] == ["clip.mp4"]
    assert error_frame["request_id"] == REQUEST_ID
    assert _acks(ws.frames) == []
    assert ws.released == []


# ----------------------------------------------------------------------
# Session creation service
# ----------------------------------------------------------------------


@pytest.fixture
def creation(monkeypatch, tmp_path):
    from twicc.core.models import Project
    from twicc.pending_agent_settings import _pending as pending_settings
    from twicc.pending_session_attributes import _pending as pending_attributes
    from twicc.pending_titles import _pending as pending_titles

    Project.objects.create(id="p", directory=str(tmp_path))
    h = Harness(plan_calls=[], plan_threads=[], target_calls=[], plan_error=None, stashes_at_plan=[],
                directory=str(tmp_path))

    def stashes():
        return {
            name: store[SESSION_ID]
            for name, store in (("title", pending_titles), ("settings", pending_settings),
                                ("attributes", pending_attributes))
            if SESSION_ID in store
        }

    h.stashes = stashes

    async def resolve_plan_target(**kwargs):
        h.target_calls.append(kwargs)
        return TARGET._replace(hybrid=kwargs["hybrid"], ephemeral=kwargs["ephemeral"])

    def plan_attachments(refs, target, *, text):
        h.plan_threads.append(threading.get_ident())
        h.stashes_at_plan.append(stashes())
        h.plan_calls.append((refs, target, text))
        if h.plan_error is not None:
            raise h.plan_error
        return _plan(refs, target)

    from twicc.core.services.attachments import target as plan_target

    monkeypatch.setattr(plan_target, "resolve_plan_target", resolve_plan_target)
    monkeypatch.setattr(planner, "plan_attachments", plan_attachments)
    h.manager = SimpleNamespace(create_session=AsyncMock(return_value=SESSION_ID))
    monkeypatch.setattr("twicc.core.services.session_creation.ensure_provider_running", lambda provider: None)
    monkeypatch.setattr(
        "twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda provider: h.manager),
    )

    def payload(**extra):
        return {"session_id": SESSION_ID, "project_id": "p", "provider": "claude_code", "text": "hello",
                "title": "T", "layout": {}, "attachments": WIRE_REFS, "images": [], "documents": [], **extra}

    h.payload = payload
    yield h
    for store in (pending_titles, pending_settings, pending_attributes):
        store.pop(SESSION_ID, None)


@pytest.mark.django_db(transaction=True)
def test_creation_plans_before_every_stash_and_hands_the_plan_over(creation):
    result = asyncio.run(create_session_from_payload(creation.payload(), allow_attachments=True))

    assert result.success, result.errors
    assert creation.stashes_at_plan == [{}]
    assert creation.plan_threads[0] != threading.get_ident()
    assert creation.plan_calls == [(REFS, TARGET, "hello")]
    assert creation.manager.create_session.await_args.kwargs["attachment_plan"] == _plan()
    assert set(creation.stashes()) == {"title", "settings", "attributes"}
    target_call = creation.target_calls[0]
    assert target_call["hybrid"] is False
    assert target_call["ephemeral"] is False
    assert target_call["live_agent"] is None
    assert target_call["directory"] == creation.directory
    assert target_call["provider"] == "claude_code"


@pytest.mark.django_db(transaction=True)
def test_creation_plan_error_leaves_no_stash_and_lists_the_names(creation):
    creation.plan_error = AttachmentPlanError(
        "attachment_requires_artifacts", "Ephemeral sessions only accept native attachments", names=("clip.mp4",),
    )
    # Not ephemeral: an ephemeral failure drains every buffer anyway, which would hide a stash.
    result = asyncio.run(create_session_from_payload(creation.payload(), allow_attachments=True))

    assert result.success is False
    assert [e.code for e in result.errors] == ["attachment_requires_artifacts"]
    assert result.errors[0].message == "Ephemeral sessions only accept native attachments: clip.mp4"
    assert result.error_names == ("clip.mp4",)
    pending_stashes_after_plan_error = creation.stashes()
    assert pending_stashes_after_plan_error == {}
    creation.manager.create_session.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_creation_target_is_ephemeral_for_an_ephemeral_run(creation):
    result = asyncio.run(create_session_from_payload(
        creation.payload(ephemeral=True), allow_attachments=True, allow_ephemeral=True,
    ))
    assert result.success, result.errors
    assert creation.target_calls[0]["ephemeral"] is True
    assert creation.manager.create_session.await_args.kwargs["ephemeral"] is True


@pytest.mark.django_db(transaction=True)
def test_creation_staging_race_is_attachment_missing(creation):
    creation.plan_error = FileNotFoundError("gone")
    result = asyncio.run(create_session_from_payload(creation.payload(), allow_attachments=True))
    assert [e.code for e in result.errors] == ["attachment_missing"]
    assert creation.stashes() == {}


@pytest.mark.django_db(transaction=True)
def test_creation_still_requires_text_with_refs(creation):
    result = asyncio.run(create_session_from_payload(creation.payload(text=""), allow_attachments=True))
    assert [e.code for e in result.errors] == ["empty_text"]
    assert creation.plan_calls == []


@pytest.mark.django_db(transaction=True)
def test_creation_with_empty_refs_plans_nothing(creation):
    result = asyncio.run(create_session_from_payload(creation.payload(attachments=[]), allow_attachments=True))
    assert result.success
    assert creation.plan_calls == []
    assert "attachment_plan" not in creation.manager.create_session.await_args.kwargs


@pytest.mark.django_db(transaction=True)
def test_creation_hybrid_flag_shapes_the_target(creation, settings):
    settings.CLAUDE_HYBRID_ENABLED = True
    result = asyncio.run(create_session_from_payload(
        creation.payload(hybrid=True), allow_attachments=True, allow_hybrid=True,
    ))
    assert result.success
    assert creation.target_calls[0]["hybrid"] is True


@pytest.mark.django_db(transaction=True)
def test_creation_commit_error_keeps_its_code(creation):
    creation.manager.create_session.side_effect = SendDeliveryError("disk", code="attachment_commit_failed")
    result = asyncio.run(create_session_from_payload(creation.payload(), allow_attachments=True))
    assert [e.code for e in result.errors] == ["attachment_commit_failed"]


@pytest.mark.django_db(transaction=True)
def test_creation_rejects_malformed_refs(creation):
    result = asyncio.run(create_session_from_payload(
        creation.payload(attachments=[{"bucket": "b", "id": "nope"}]), allow_attachments=True,
    ))
    assert [e.code for e in result.errors] == ["invalid_attachments"]
    assert creation.stashes() == {}


@pytest.mark.django_db(transaction=True)
def test_drop_request_creation_rejects_refs(creation):
    result = asyncio.run(create_session_from_payload(creation.payload()))
    assert [e.code for e in result.errors] == ["invalid_attachments"]
    assert creation.plan_calls == []
    assert creation.stashes() == {}
    creation.manager.create_session.assert_not_called()


@pytest.mark.django_db
def test_drop_request_send_rejects_refs():
    result = asyncio.run(send_message_to_session_from_payload(
        {"session_id": "unknown-session", "text": "hi", "attachments": WIRE_REFS},
    ))
    assert result.success is False
    assert [e.code for e in result.errors] == ["invalid_attachments"]


# ----------------------------------------------------------------------
# delivery_release
# ----------------------------------------------------------------------


def test_delivery_release_runs_once_detached(released):
    async def scenario():
        release = lifecycle.delivery_release(REFS)
        assert release() is None
        release()
        assert released == []  # scheduled, not awaited inline
        await _drain()

    asyncio.run(scenario())
    assert released == [REFS]


def test_delivery_release_failure_is_logged_and_left_to_retention(monkeypatch):
    async def failing(refs):
        raise OSError("busy")

    monkeypatch.setattr(lifecycle, "release_refs", failing)

    async def scenario():
        lifecycle.delivery_release(REFS)()
        await _drain()

    with _logs("twicc.core.services.attachments.lifecycle") as records:
        asyncio.run(scenario())
    assert "left to retention" in _text(records)


def test_delivery_release_of_no_refs_does_nothing(released):
    async def scenario():
        lifecycle.delivery_release(())()
        await _drain()

    asyncio.run(scenario())
    assert released == []


# ----------------------------------------------------------------------
# Parked sends (Claude manager)
# ----------------------------------------------------------------------


class Lock:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        self.events.append("manager_lock")

    async def __aexit__(self, *exc):
        self.events.append("manager_unlock")
        return False


class FakeAgent:
    def __init__(self, events, *, state=AgentState.USER_TURN, hybrid=False, send_error=None):
        self.events = events
        self.state = state
        self.is_hybrid = hybrid
        self.send_error = send_error
        self.agent_settings = AgentSettings()
        self.process_run = None
        self.session_id = SESSION_ID
        self.project_id = "p"
        self.cwd = "/project"
        self.sent: list = []

    def background_shell_count(self):
        return 0

    async def apply_live_settings(self, settings):
        pass

    async def send(self, text, **kwargs):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((text, kwargs))
        return True

    async def interrupt_or_kill(self, reason):
        self.state = AgentState.DEAD


@pytest.fixture
def parked(monkeypatch, released):
    from twicc.core.services.attachments import committer

    events: list = []

    async def record_release(refs):
        events.append("release")
        released.append(tuple(refs))

    monkeypatch.setattr(lifecycle, "release_refs", record_release)
    monkeypatch.setattr(committer, "prepare_attachments", lambda plan, *, session_id: SimpleNamespace())
    monkeypatch.setattr(committer, "finish_attachments", lambda prepared, *, session_id, text: _content())
    monkeypatch.setattr(committer, "discard_prepared", lambda prepared: None)
    manager = ClaudeCodeAgentManager()
    manager._lock = Lock(events)
    manager._check_ephemeral_readonly = lambda *args: None
    return SimpleNamespace(manager=manager, events=events, released=released)


def _startup_change(manager, refs=REFS):
    return manager.send_to_session(
        SESSION_ID, "p", "/project", "", AgentSettings(effort="max"), attachment_plan=_plan(refs),
    )


def _start_replacing_with(manager, new_agent, events):
    async def start(session_id, *args, **kwargs):
        events.append(("start", kwargs.get("on_delivered") is not None))
        manager._start_kwargs = kwargs
        if new_agent is None:
            manager._agents.pop(session_id, None)
        else:
            manager._agents[session_id] = new_agent
        return session_id

    return start


@pytest.mark.parametrize(
    "new_state,expected",
    [("live", [REFS]), ("absent", []), ("dead", [])],
)
def test_startup_change_releases_only_once_a_live_sdk_agent_took_the_parked_send(parked, monkeypatch, new_state,
                                                                                   expected):
    manager, events = parked.manager, parked.events
    manager._agents[SESSION_ID] = FakeAgent(events)
    new_agent = None if new_state == "absent" else FakeAgent(
        events, state=AgentState.DEAD if new_state == "dead" else AgentState.STARTING,
    )
    monkeypatch.setattr(manager, "_session_has_crons", AsyncMock(return_value=False))
    monkeypatch.setattr(manager, "_broadcast_agent_state", AsyncMock())
    monkeypatch.setattr(manager, "_start_agent", _start_replacing_with(manager, new_agent, events))

    async def scenario():
        assert await _startup_change(manager) is False
        await _drain()

    asyncio.run(scenario())
    assert parked.released == expected
    assert manager._start_kwargs["content"] == _content()
    if expected:
        assert events.index("manager_unlock") < events.index("release")


def test_startup_change_to_a_hybrid_agent_defers_the_release_to_the_first_paste(parked, monkeypatch):
    manager, events = parked.manager, parked.events
    manager._agents[SESSION_ID] = FakeAgent(events)
    monkeypatch.setattr(manager, "_session_has_crons", AsyncMock(return_value=False))
    monkeypatch.setattr(manager, "_broadcast_agent_state", AsyncMock())
    hybrid_agent = FakeAgent(events, state=AgentState.STARTING, hybrid=True)
    monkeypatch.setattr(manager, "_start_agent", _start_replacing_with(manager, hybrid_agent, events))

    async def scenario():
        await _startup_change(manager)
        await _drain()
        assert parked.released == []
        manager._start_kwargs["on_delivered"]()  # the successful first paste
        await _drain()

    asyncio.run(scenario())
    assert parked.released == [REFS]


def _park_for_crons(parked, monkeypatch, refs=REFS):
    manager, events = parked.manager, parked.events
    manager._agents[SESSION_ID] = FakeAgent(events)
    monkeypatch.setattr(manager, "_session_has_crons", AsyncMock(return_value=True))
    monkeypatch.setattr(manager, "_broadcast_agent_state", AsyncMock())
    asyncio.run(_startup_change(manager, refs))
    return manager._pending_after_restart[SESSION_ID]


def test_parked_send_carries_content_refs_and_release(parked, monkeypatch):
    entry = _park_for_crons(parked, monkeypatch)
    assert set(entry) == {"text", "images", "documents", "content", "refs", "on_delivered"}
    assert entry["refs"] == REFS
    assert entry["content"] == _content()
    assert parked.released == []


@pytest.mark.parametrize(
    "agent_kind,expected",
    [("ok", [REFS]), ("send_error", []), ("busy", []), ("absent", [])],
)
def test_cron_restart_releases_only_a_delivered_parked_send(parked, monkeypatch, agent_kind, expected):
    from twicc.providers.claude_code import cron_restart

    _park_for_crons(parked, monkeypatch)
    manager, events = parked.manager, parked.events
    if agent_kind == "absent":
        manager._agents.pop(SESSION_ID)
    else:
        manager._agents[SESSION_ID] = FakeAgent(
            events,
            state=AgentState.ASSISTANT_TURN if agent_kind == "busy" else AgentState.USER_TURN,
            send_error=SendDeliveryError("busy", code="send_failed") if agent_kind == "send_error" else None,
        )
    monkeypatch.setattr(cron_restart, "restart_session_crons", AsyncMock())

    async def scenario():
        await manager._restart_crons_for_session(SESSION_ID)
        await _drain()

    asyncio.run(scenario())
    assert parked.released == expected
    assert SESSION_ID not in manager._pending_after_restart


def test_an_overwritten_parked_send_releases_nothing(parked, monkeypatch):
    from twicc.providers.claude_code import cron_restart

    _park_for_crons(parked, monkeypatch, refs=(REF_1,))
    _park_for_crons(parked, monkeypatch, refs=(REF_2,))
    manager = parked.manager
    manager._agents[SESSION_ID] = FakeAgent(parked.events)
    monkeypatch.setattr(cron_restart, "restart_session_crons", AsyncMock())

    async def scenario():
        await manager._restart_crons_for_session(SESSION_ID)
        await _drain()

    asyncio.run(scenario())
    assert parked.released == [(REF_2,)]


def _pending_settings(monkeypatch, manager):
    from twicc.core import models
    from twicc.providers.helpers import get_provider_helpers

    monkeypatch.setattr(
        models.Session, "objects",
        SimpleNamespace(filter=lambda **kw: SimpleNamespace(first=lambda: SimpleNamespace())),
    )
    monkeypatch.setattr(AgentSettings, "from_session", staticmethod(lambda session: AgentSettings(effort="max")))
    helpers = get_provider_helpers("claude_code")
    monkeypatch.setattr(helpers, "resolve_agent_settings", lambda settings: settings)
    monkeypatch.setattr(helpers, "enforce_agent_settings_consistency", lambda settings: settings)
    monkeypatch.setattr(manager, "_session_has_crons", AsyncMock(return_value=False))
    monkeypatch.setattr(manager, "_broadcast_agent_state", AsyncMock())


@pytest.mark.parametrize(
    "new_state,hybrid,expected",
    [("live", False, [REFS]), ("absent", False, []), ("dead", False, []), ("live", True, [])],
)
def test_pending_settings_restart_releases_after_a_live_sdk_start(parked, monkeypatch, new_state, hybrid, expected):
    manager, events = parked.manager, parked.events
    old = FakeAgent(events)
    manager._agents[SESSION_ID] = old
    manager._pending_after_restart[SESSION_ID] = {
        "text": "", "images": None, "documents": None, "content": _content(), "refs": REFS,
        "on_delivered": lifecycle.delivery_release(REFS),
    }
    _pending_settings(monkeypatch, manager)
    new_agent = None if new_state == "absent" else FakeAgent(
        events, state=AgentState.DEAD if new_state == "dead" else AgentState.STARTING, hybrid=hybrid,
    )
    monkeypatch.setattr(manager, "_start_agent", _start_replacing_with(manager, new_agent, events))

    async def scenario():
        await manager._apply_pending_settings(old)
        await _drain()

    asyncio.run(scenario())
    assert parked.released == expected
    assert manager._start_kwargs["content"] == _content()
    assert manager._start_kwargs["on_delivered"] is not None


def test_legacy_parked_send_has_no_release(parked, monkeypatch):
    manager, events = parked.manager, parked.events
    old = FakeAgent(events)
    manager._agents[SESSION_ID] = old
    manager._pending_after_restart[SESSION_ID] = {"text": "hi", "images": None, "documents": None}
    _pending_settings(monkeypatch, manager)
    monkeypatch.setattr(manager, "_start_agent", _start_replacing_with(manager, FakeAgent(events), events))

    async def scenario():
        await manager._apply_pending_settings(old)
        await _drain()

    asyncio.run(scenario())
    assert "on_delivered" not in manager._start_kwargs
    assert "content" not in manager._start_kwargs
    assert parked.released == []
