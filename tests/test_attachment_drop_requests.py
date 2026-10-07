"""Drop-request sends and creations that carry staged refs.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.5. The planner, the
plan target, the managers and the staging release are faked; the session rows, the send lanes,
the drop routing and the services are real.
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from PIL import Image

from twicc.agent import AgentState, SendDeliveryError, send_lanes
from twicc.core.models import ProcessRun, Project, Session, SessionType
from twicc.core.services import send_message as send_message_service
from twicc.core.services import session_creation
from twicc.core.services.attachments import drop as attachment_drop
from twicc.core.services.attachments import lifecycle, planner, staging
from twicc.core.services.attachments import target as target_module
from twicc.core.services.attachments.planner import AttachmentPlanError
from twicc.core.services.attachments.staging import AttachmentError
from twicc.core.services.attachments.types import (
    AttachmentPlan,
    AttachmentRef,
    PlannedEntry,
    PlanTarget,
    StagedEntry,
)
from twicc.drop_requests_watcher import _KIND_HANDLERS, execute_drop_payload

ID_1 = "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a01"
ID_2 = "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a02"
REFS = (AttachmentRef("cli-bucket", ID_1), AttachmentRef("cli-bucket", ID_2))
WIRE_REFS = [ref._asdict() for ref in REFS]
TARGET = PlanTarget("claude_code", False, False, "opus", False, "first_party")
SESSION_ID = "drop-session"
PROJECT_ID = "-tmp-drop"
# Captured before any fixture replaces it: the "real planner" tests restore it.
REAL_PLAN_ATTACHMENTS = planner.plan_attachments


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    return buffer.getvalue()


def _plan(refs=REFS, target=TARGET) -> AttachmentPlan:
    entries = []
    for n, ref in enumerate(refs, start=1):
        source = StagedEntry(ref, f"f{n}.txt", 5, Path(f"/staged/f{n}.txt"), None)
        entries.append(PlannedEntry(ref, n, f"f{n}.txt", "text", n, len(refs), "file", source, None))
    return AttachmentPlan(target, tuple(entries))


class FakeManager:
    def __init__(self):
        self.result = True
        self.error: BaseException | None = None
        self.gate: asyncio.Event | None = None
        self.calls: list = []
        self.events: list = []

    def get_live_agent(self, session_id):
        return None

    async def _run(self, kind, text, settings, kwargs, value):
        self.events.append((kind, text))
        self.calls.append(SimpleNamespace(text=text, settings=settings, kwargs=kwargs))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return value

    async def send_to_session(self, session_id, project_id, cwd, text, *, settings, **kwargs):
        return await self._run("send", text, settings, kwargs, self.result)

    async def create_session(self, session_id, project_id, cwd, text, *, settings, **kwargs):
        return await self._run("create", text, settings, kwargs, session_id)


@pytest.fixture
def drop(monkeypatch, transactional_db):
    from twicc.pending_agent_settings import _pending as pending_settings
    from twicc.pending_session_attributes import _pending as pending_attributes
    from twicc.pending_titles import _pending as pending_titles

    send_lanes._reset_for_tests()
    project = Project.objects.create(id=PROJECT_ID, directory="/tmp/drop")
    Session.objects.create(
        id=SESSION_ID, project=project, provider="claude_code", file_path="drop.jsonl", type=SessionType.SESSION,
    )
    h = SimpleNamespace(manager=FakeManager(), released=[], plan_calls=[], target_calls=[], plan_error=None,
                        pending_settings=pending_settings)

    async def release_refs(refs):
        h.released.append(tuple(refs))

    async def resolve_plan_target(**kwargs):
        h.target_calls.append(kwargs)
        return TARGET._replace(hybrid=kwargs["hybrid"], ephemeral=kwargs["ephemeral"])

    def plan_attachments(refs, target, *, text):
        h.plan_calls.append((refs, target, text))
        if h.plan_error is not None:
            raise h.plan_error
        return _plan(refs, target)

    identity = SimpleNamespace(
        resolve_agent_settings=lambda settings: settings,
        enforce_agent_settings_consistency=lambda settings: settings,
    )
    monkeypatch.setattr(lifecycle, "release_refs", release_refs)
    monkeypatch.setattr(target_module, "resolve_plan_target", resolve_plan_target)
    monkeypatch.setattr(planner, "plan_attachments", plan_attachments)
    monkeypatch.setattr(send_message_service, "ensure_provider_running", lambda provider: None)
    monkeypatch.setattr(send_message_service, "get_provider_helpers", lambda provider: identity)
    monkeypatch.setattr(session_creation, "ensure_provider_running", lambda provider: None)
    monkeypatch.setattr(
        "twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda provider: h.manager),
    )
    yield h
    send_lanes._reset_for_tests()
    for store in (pending_titles, pending_settings, pending_attributes):
        store.pop(SESSION_ID, None)


async def _settle() -> None:
    """Wait for the detached releases scheduled by ``delivery_release``."""
    for _ in range(5):
        pending = list(lifecycle._DELIVERY_RELEASE_TASKS)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


async def _until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _run(coro):
    async def scenario():
        result = await coro
        await _settle()
        return result

    return asyncio.run(scenario())


def _send(**extra):
    return _run(execute_drop_payload({"session_id": SESSION_ID, "text": "look", **extra}, "session:send_message"))


def _create(**extra):
    payload = {"session_id": SESSION_ID, "project_id": PROJECT_ID, "provider": "claude_code", "text": "hello", **extra}
    return _run(execute_drop_payload(payload, "session:create"))


# ── Drop rules ───────────────────────────────────────────────────────────────


def test_refs_to_release_keeps_valid_unique_refs_in_order():
    payload = {"attachments": [WIRE_REFS[1], "junk", WIRE_REFS[0], WIRE_REFS[1], {"bucket": "b", "id": "nope"}]}
    assert attachment_drop.refs_to_release(payload) == (REFS[1], REFS[0])
    assert attachment_drop.refs_to_release({"attachments": "x"}) == ()
    assert attachment_drop.refs_to_release({}) == ()


@pytest.mark.parametrize(("code", "expected"), [
    ("attachment_missing", True), ("attachments_with_command", True), ("attachment_commit_failed", True),
    ("agent_starting", False), ("send_failed", False), (None, False),
])
def test_attachment_codes(code, expected):
    assert attachment_drop.is_attachment_code(code) is expected


def test_the_session_kinds_route_to_the_drop_wrappers():
    assert _KIND_HANDLERS["session:send_message"][:2] == (
        "twicc.core.services.send_message", "send_message_from_drop_payload",
    )
    assert _KIND_HANDLERS["session:create"][:2] == (
        "twicc.core.services.session_creation", "create_session_from_drop_payload",
    )


# ── Send to an existing session ──────────────────────────────────────────────


def test_refs_are_planned_then_released_once_delivered(drop):
    status = _send(attachments=WIRE_REFS)
    assert status["status"] == "sent", status
    assert drop.manager.calls[0].kwargs["attachment_plan"] == _plan()
    assert drop.plan_calls == [(REFS, TARGET, "look")]
    target_call = drop.target_calls[0]
    assert (target_call["ephemeral"], target_call["live_agent"], target_call["hybrid"]) == (False, None, False)
    assert target_call["directory"] == "/tmp/drop"
    assert drop.released == [REFS]


def test_refs_alone_are_content(drop):
    assert _send(text="", attachments=WIRE_REFS)["status"] == "sent"


def test_a_send_without_refs_plans_nothing(drop):
    assert _send()["status"] == "sent"
    assert "attachment_plan" not in drop.manager.calls[0].kwargs
    assert (drop.plan_calls, drop.released) == ([], [])


def test_a_send_not_delivered_now_keeps_its_refs(drop):
    drop.manager.result = False
    status = _send(attachments=WIRE_REFS)
    assert status["errors"][0]["code"] == "send_failed"
    assert drop.released == []


@pytest.mark.parametrize(("error", "field", "code"), [
    (SendDeliveryError("disk full", code="attachment_commit_failed"), "attachments", "attachment_commit_failed"),
    (SendDeliveryError("Attachments cannot be sent with a command", code="attachments_with_command"),
     "attachments", "attachments_with_command"),
    (SendDeliveryError("The agent is starting", code="agent_starting"), "session", "agent_starting"),
    (RuntimeError("busy"), "session", "manager_busy"),
])
def test_manager_errors_are_mapped_and_release_the_refs(drop, error, field, code):
    drop.manager.error = error
    status = _send(attachments=WIRE_REFS)
    assert status["status"] == "rejected"
    assert [(e["field"], e["code"]) for e in status["errors"]] == [(field, code)]
    assert drop.released == [REFS]


def test_a_delivery_error_keeps_the_names_it_carries(drop):
    error = SendDeliveryError("Attachment not found", code="attachment_missing")
    error.names = ("shot.png",)
    drop.manager.error = error
    assert _send(attachments=WIRE_REFS)["errors"][0]["message"] == "Attachment not found: shot.png"


@pytest.mark.parametrize("error", [
    AttachmentPlanError("attachments_with_command", "Attachments cannot be sent with a command"),
    AttachmentPlanError("attachment_requires_artifacts", "Ephemeral sessions only accept native attachments",
                        names=("clip.mp4",)),
    AttachmentError("attachment_missing", "Attachment not found"),
    AttachmentError("attachment_not_ready", "Upload not complete"),
])
def test_plan_errors_reject_release_and_send_nothing(drop, error):
    drop.plan_error = error
    status = _send(attachments=WIRE_REFS)
    expected = str(error) + (": clip.mp4" if getattr(error, "names", ()) else "")
    assert status["errors"] == [{"field": "attachments", "code": error.code, "message": expected}]
    assert drop.manager.calls == []
    assert drop.released == [REFS]


def test_a_business_rejection_releases_the_refs(drop):
    status = _run(execute_drop_payload(
        {"session_id": "no-such-session", "text": "look", "attachments": WIRE_REFS}, "session:send_message",
    ))
    assert status["errors"][0]["code"] == "session_not_found"
    assert drop.released == [REFS]


def test_malformed_refs_release_the_valid_ones(drop):
    status = _send(attachments=[WIRE_REFS[0], WIRE_REFS[0], {"bucket": "b", "id": "nope"}])
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert drop.released == [(REFS[0],)]


def test_an_unexpected_exception_releases_then_fails(drop):
    drop.manager.error = ValueError("boom")
    status = _send(attachments=WIRE_REFS)
    assert status["status"] == "failed"
    assert drop.released == [REFS]


# ── Send lane (D15) ──────────────────────────────────────────────────────────


def test_two_drop_sends_to_one_session_keep_their_order(drop):
    async def scenario():
        drop.manager.gate = asyncio.Event()
        first = asyncio.create_task(execute_drop_payload({"session_id": SESSION_ID, "text": "one"},
                                                         "session:send_message"))
        await _until(lambda: drop.manager.events == [("send", "one")])
        second = asyncio.create_task(execute_drop_payload({"session_id": SESSION_ID, "text": "two"},
                                                          "session:send_message"))
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert drop.manager.events == [("send", "one")]
        drop.manager.gate.set()
        return await asyncio.gather(first, second)

    statuses = asyncio.run(scenario())
    assert [s["status"] for s in statuses] == ["sent", "sent"]
    assert drop.manager.events == [("send", "one"), ("send", "two")]


def test_a_send_waiting_in_the_lane_reads_the_settings_written_before_it(drop):
    async def scenario():
        async with send_lanes.send_lane(SESSION_ID):  # a WS send holds the lane
            task = asyncio.create_task(execute_drop_payload({"session_id": SESSION_ID, "text": "hi"},
                                                            "session:send_message"))
            for _ in range(20):
                await asyncio.sleep(0.01)
            assert drop.manager.calls == []
            await sync_to_async(lambda: Session.objects.filter(id=SESSION_ID).update(selected_model="updated-model"))()
        return await task

    assert asyncio.run(scenario())["status"] == "sent"
    assert drop.manager.calls[0].settings.selected_model == "updated-model"


def test_a_send_waiting_in_the_lane_sees_a_pending_request_raised_before_it(drop):
    async def scenario():
        async with send_lanes.send_lane(SESSION_ID):
            task = asyncio.create_task(execute_drop_payload(
                {"session_id": SESSION_ID, "text": "hi", "attachments": WIRE_REFS}, "session:send_message",
            ))
            for _ in range(20):
                await asyncio.sleep(0.01)
            now = timezone.now()
            await sync_to_async(lambda: ProcessRun.objects.create(
                provider="claude_code", session_id=SESSION_ID, twicc_pid=os.getpid(), started_at=now,
                state=AgentState.USER_TURN.value, last_state_change_at=now, awaiting_user_input=True,
            ))()
        status = await task
        await _settle()
        return status

    status = asyncio.run(scenario())
    assert status["errors"][0]["code"] == "awaiting_user_input"
    assert drop.manager.calls == []
    assert drop.released == [REFS]


def test_a_send_right_after_a_drop_creation_waits_for_it(drop):
    async def scenario():
        drop.manager.gate = asyncio.Event()
        create = asyncio.create_task(execute_drop_payload(
            {"session_id": SESSION_ID, "project_id": PROJECT_ID, "provider": "claude_code", "text": "hello"},
            "session:create",
        ))
        await _until(lambda: ("create", "hello") in drop.manager.events)
        send = asyncio.create_task(execute_drop_payload({"session_id": SESSION_ID, "text": "next"},
                                                        "session:send_message"))
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert ("send", "next") not in drop.manager.events
        drop.manager.gate.set()
        return await asyncio.gather(create, send)

    created, sent = asyncio.run(scenario())
    assert (created["status"], sent["status"]) == ("created", "sent")
    assert drop.manager.events == [("create", "hello"), ("send", "next")]


# ── The real planner, per provider ───────────────────────────────────────────


@pytest.fixture
def real_plan(drop, monkeypatch, tmp_path):
    """The phase 1 planner on real staged files; only the settings-file reads are skipped."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(data))
    monkeypatch.setattr(planner, "plan_attachments", REAL_PLAN_ATTACHMENTS)

    async def resolve_plan_target(**kwargs):
        drop.target_calls.append(kwargs)
        return PlanTarget(kwargs["provider"], kwargs["hybrid"], kwargs["ephemeral"], "opus", False, "first_party")

    monkeypatch.setattr(target_module, "resolve_plan_target", resolve_plan_target)
    return drop


def _staged(name: str, data: bytes) -> dict:
    return staging.stage_bytes(data, name, bucket=staging.new_bucket("cli"), origin="cli")._asdict()


@pytest.mark.parametrize(("provider", "hybrid", "modes"), [
    ("claude_code", False, ["inline", "inline", "file"]),
    ("claude_code", True, ["inline", "file", "file"]),
    ("codex", False, ["inline", "file", "file"]),
])
def test_refs_are_planned_for_the_session_provider(real_plan, provider, hybrid, modes):
    Session.objects.filter(id=SESSION_ID).update(provider=provider, hybrid=hybrid)
    attachments = [_staged("shot.png", _png()), _staged("notes.txt", b"hello"), _staged("clip.mp4", b"\x00\x01")]
    status = _send(attachments=attachments)
    assert status["status"] == "sent", status
    plan = real_plan.manager.calls[0].kwargs["attachment_plan"]
    assert [entry.name for entry in plan.entries] == ["shot.png", "notes.txt", "clip.mp4"]
    # Hybrid: the text document is a file entry, never lost (phase 1 D18).
    assert [entry.mode for entry in plan.entries] == modes


@pytest.mark.parametrize(("provider", "hybrid", "text"), [
    ("claude_code", True, "/help"),
    ("claude_code", True, "!ls"),
    ("codex", False, "/compact"),
])
def test_a_command_carrying_files_is_refused(real_plan, provider, hybrid, text):
    Session.objects.filter(id=SESSION_ID).update(provider=provider, hybrid=hybrid)
    status = _send(text=text, attachments=[_staged("notes.txt", b"x")])
    assert status["errors"][0]["code"] == "attachments_with_command"
    assert status["errors"][0]["field"] == "attachments"
    assert real_plan.manager.calls == []


# ── Session creation ─────────────────────────────────────────────────────────


def test_a_drop_creation_plans_its_refs_and_releases_them_after_success(drop):
    status = _create(attachments=WIRE_REFS)
    assert status["status"] == "created", status
    assert drop.manager.calls[0].kwargs["attachment_plan"] == _plan()
    assert drop.released == [REFS]


def test_a_drop_creation_plan_error_leaves_no_stash_and_releases(drop):
    drop.plan_error = AttachmentPlanError("attachments_with_command", "Attachments cannot be sent with a command")
    status = _create(attachments=WIRE_REFS)
    assert status["errors"][0]["field"] == "attachments"
    assert SESSION_ID not in drop.pending_settings
    assert drop.manager.calls == []
    assert drop.released == [REFS]


def test_a_drop_creation_commit_error_keeps_its_field(drop):
    drop.manager.error = SendDeliveryError("Cannot place 'a.txt'", code="attachment_commit_failed")
    status = _create(attachments=WIRE_REFS)
    assert status["errors"] == [
        {"field": "attachments", "code": "attachment_commit_failed", "message": "Cannot place 'a.txt'"},
    ]
    assert drop.released == [REFS]


def test_the_ws_creation_never_releases_its_refs(drop):
    drop.manager.error = SendDeliveryError("busy", code="agent_starting")
    result = _run(session_creation.create_session_from_payload(
        {"session_id": SESSION_ID, "project_id": PROJECT_ID, "provider": "claude_code", "text": "hello",
         "attachments": WIRE_REFS},
        allow_hybrid=True, allow_ephemeral=True,
    ))
    assert result.success is False
    assert drop.released == []


def test_the_ws_creation_still_passes_legacy_blocks(drop):
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    result = _run(session_creation.create_session_from_payload(
        {"session_id": SESSION_ID, "project_id": PROJECT_ID, "provider": "claude_code", "text": "hello",
         "images": [image]},
        allow_hybrid=True, allow_ephemeral=True,
    ))
    assert result.success, result.errors
    assert drop.manager.calls[0].kwargs["images"] == [image]


# ── Legacy fields (an older CLI) ─────────────────────────────────────────────


@pytest.mark.parametrize("legacy", [{"images": [{"type": "image"}]}, {"documents": [{"type": "document"}]}])
def test_a_drop_send_with_legacy_blocks_is_refused_and_releases(drop, legacy):
    status = _send(attachments=WIRE_REFS[:1], **legacy)
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert "older than the server" in status["errors"][0]["message"]
    assert drop.manager.calls == []
    assert drop.released == [(REFS[0],)]


@pytest.mark.parametrize("legacy", [{"images": [{"type": "image"}]}, {"documents": [{"type": "document"}]}])
def test_a_drop_creation_with_legacy_blocks_is_refused_and_releases(drop, legacy):
    status = _create(attachments=WIRE_REFS, **legacy)
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert "older than the server" in status["errors"][0]["message"]
    assert drop.manager.calls == []
    assert drop.plan_calls == []
    assert drop.released == [REFS]


@pytest.mark.parametrize("session_id", [123, ["sid"], {"id": "sid"}, 0, False])
def test_a_drop_with_a_non_string_session_id_is_refused_before_the_lane(drop, session_id):
    """A non-string id would bypass the send lane: both drop wrappers refuse it and release the refs."""
    for status in (_send(session_id=session_id, attachments=WIRE_REFS), _create(session_id=session_id,
                                                                                attachments=WIRE_REFS)):
        assert [(e["field"], e["code"]) for e in status["errors"]] == [("session_id", "invalid")]
    assert drop.manager.calls == []
    assert drop.plan_calls == []
    assert drop.released == [REFS, REFS]


@pytest.mark.parametrize("session_id", [None, ""])
def test_a_drop_without_session_id_is_still_missing(drop, session_id):
    for status in (_send(session_id=session_id, attachments=WIRE_REFS), _create(session_id=session_id,
                                                                                attachments=WIRE_REFS)):
        assert ("session_id", "missing") in [(e["field"], e["code"]) for e in status["errors"]]
    assert drop.manager.calls == []
    assert drop.released == [REFS, REFS]


def test_empty_legacy_lists_are_accepted(drop):
    assert _send(images=[], documents=[])["status"] == "sent"
    assert _create(images=[], documents=[])["status"] == "created"


def test_the_send_service_no_longer_passes_legacy_blocks(drop):
    _send()
    assert "images" not in drop.manager.calls[0].kwargs
    assert "documents" not in drop.manager.calls[0].kwargs
