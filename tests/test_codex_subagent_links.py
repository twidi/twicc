"""Codex subagent linkage, across both multi-agent protocol generations.

Codex ships two incompatible spawn protocols and both are on disk in any
long-lived install, so compute must recognise them by shape:

- **v1** (``turn_context`` has no ``multi_agent_version``): bare
  ``spawn_agent`` name, ack ``{"agent_id", "nickname"}`` carrying the
  subagent's thread id, completion announced by a
  ``<subagent_notification>`` user message keyed on that same id.
- **v2** (``multi_agent_version == "v2"``): name qualified with the
  ``collaboration`` namespace, ack ``{"task_name": "/root/<task>"}``
  carrying an agent *path* and no thread id at all. The thread id
  arrives on an ``event_msg.sub_agent_activity`` line
  (``kind == "started"``, ``event_id`` = the spawning call_id), and the
  completion signal is a ``FINAL_ANSWER`` inter-agent message keyed on
  the sender's agent path.

Both must produce the same two artefacts: one ``AgentLink`` (parent
tool_use ↔ subagent session) and two ``ToolResultLink`` rows on the
spawn call (ack + completion), the second being what stops the
frontend's "agent running" state and what
``check_agent_naturally_stopped`` counts.

Covered here for the batch (recompute) path and the live (watcher) path,
which resolve the same links through different machinery.
"""

from __future__ import annotations

import json
import queue
from datetime import UTC, datetime

import orjson
import pytest

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import AgentLink, Project, Session, SessionItem, ToolResultLink
from twicc.providers.codex.compute import get_compute

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

# A Fernet token like the one v2 puts in ``spawn_agent.message`` — opaque
# to us, and deliberately never parsed.
_ENCRYPTED = "gAAAAABmZmZmZm9vYmFy"


# ---------------------------------------------------------------------------
# JSONL line builders
# ---------------------------------------------------------------------------


def _line(type_: str, payload: dict) -> str:
    return json.dumps({"timestamp": _NOW.isoformat(), "type": type_, "payload": payload})


def _spawn_call_v1(call_id: str, prompt: str = "do the thing") -> str:
    return _line("response_item", {
        "type": "function_call",
        "name": "spawn_agent",
        "call_id": call_id,
        "arguments": json.dumps({"message": prompt, "fork_context": "none"}),
    })


def _spawn_ack_v1(call_id: str, agent_id: str, nickname: str = "Ptolemy") -> str:
    return _line("response_item", {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps({"agent_id": agent_id, "nickname": nickname}),
    })


def _subagent_notification_v1(agent_id: str, message: str = "all done") -> str:
    body = json.dumps({"agent_path": agent_id, "status": {"completed": message}})
    return _line("response_item", {
        "type": "message",
        "role": "user",
        "content": [{
            "type": "input_text",
            "text": f"<subagent_notification>\n{body}\n</subagent_notification>",
        }],
    })


def _spawn_call_v2(call_id: str, task_name: str = "display_test") -> str:
    return _line("response_item", {
        "type": "function_call",
        "name": "spawn_agent",
        "namespace": "collaboration",
        "call_id": call_id,
        "arguments": json.dumps({
            "task_name": task_name,
            "fork_turns": "none",
            "message": _ENCRYPTED,
        }),
    })


def _sub_agent_activity(call_id: str, agent_id: str, agent_path: str, kind: str = "started") -> str:
    return _line("event_msg", {
        "type": "item_completed",
        "thread_id": "parent-thread",
        "turn_id": "turn-1",
        "completed_at_ms": 1786769944144,
        "item": {
            "type": "SubAgentActivity",
            "id": call_id,
            "agent_thread_id": agent_id,
            "agent_path": agent_path,
            "kind": kind,
        },
    })


def _spawn_ack_v2(call_id: str, agent_path: str) -> str:
    return _line("response_item", {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps({"task_name": agent_path}),
    })


def _final_answer_v2(agent_path: str, answer: str = "all done") -> str:
    text = (
        "Message Type: FINAL_ANSWER\n"
        "Task name: /root\n"
        f"Sender: {agent_path}\n"
        "Payload:\n"
        f"{answer}"
    )
    return _line("response_item", {
        "type": "agent_message",
        "content": [{"type": "input_text", "text": text}],
    })


def _inter_agent_message_v2(agent_path: str) -> str:
    """A mid-flight (encrypted) message — must never look like a completion."""
    return _line("response_item", {
        "type": "agent_message",
        "content": [
            {
                "type": "input_text",
                "text": (
                    "Message Type: MESSAGE\n"
                    "Task name: /root\n"
                    f"Sender: {agent_path}\n"
                    "Payload:\n"
                ),
            },
            {"type": "encrypted_content", "encrypted_content": _ENCRYPTED},
        ],
    })


def _task_started() -> str:
    return _line("event_msg", {"type": "task_started", "turn_id": "turn-1"})


def _task_complete(message: str = "done") -> str:
    return _line("event_msg", {"type": "task_complete", "last_agent_message": message})


def _spawn_rejection(call_id: str, text: str) -> str:
    """Failed spawn: the SDK answers with freeform text instead of JSON."""
    return _line("response_item", {
        "type": "function_call_output",
        "call_id": call_id,
        "output": text,
    })


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture
def parent_session(db):
    project = Project.objects.create(id="test-project-codex-subagents")
    return Session.objects.create(
        id="test-session-codex-subagents",
        project=project,
        provider=Provider.CODEX,
    )


def _seed(session: Session, lines: list[str]) -> list[SessionItem]:
    return [
        SessionItem.objects.create(session=session, line_num=i, content=content)
        for i, content in enumerate(lines, start=1)
    ]


def _run_batch_compute(session: Session) -> None:
    """Full recompute of the session, applying the produced diff to the DB."""
    from queue import Empty

    compute = get_compute()
    result_q: queue.Queue = queue.Queue()
    compute.compute_session_metadata(session.id, result_q, run_id=0)
    while True:
        try:
            raw = result_q.get_nowait()
        except Empty:
            break
        msg = orjson.loads(raw)
        if msg.get("type") == "session_complete":
            compute.apply_session_complete(msg)


def _run_live_sync_collecting(session: Session, lines: list[str]) -> tuple[list, list]:
    """Same as :func:`_run_live_sync`, returning the broadcast payloads.

    The hooks' return values are exactly what the watcher turns into the
    ``agent_link_created`` / ``tool_state`` WS messages, which drive both
    spinners in the frontend: the synthetic process state of the subagent
    tab, and the tool card's ``isAgentRunning`` (``result_count`` vs the
    background threshold).
    """
    compute = get_compute()
    agent_updates, tool_updates = [], []
    for line_num, content in enumerate(lines, start=1):
        item = SessionItem.objects.create(
            session=session, line_num=line_num, content=content, timestamp=_NOW,
        )
        parsed = orjson.loads(content)
        if compute.is_tool_result_item(parsed):
            if link_update := compute.create_agent_link_from_tool_result(session.id, item, parsed):
                agent_updates.append(link_update)
            if update := compute.create_tool_result_link_live(session.id, item, parsed):
                tool_updates.append(update)
                compute.check_agent_naturally_stopped(session.id, update)
    return agent_updates, tool_updates


def _run_live_sync(session: Session, lines: list[str]) -> None:
    """Replay ``lines`` through the live hooks, one item at a time.

    Mirrors the watcher loop's ordering: each item is persisted, then the
    agent link is created before the tool_result link (same order as
    ``compute_base``'s incremental path), so a hook that needs a prior
    line finds it in the DB exactly like it would in production.
    """
    compute = get_compute()
    for line_num, content in enumerate(lines, start=1):
        # ``timestamp`` is set by the watcher before these hooks run; the
        # agent-stopped check reads it through ``ToolResultLink.tool_result_at``.
        item = SessionItem.objects.create(
            session=session, line_num=line_num, content=content, timestamp=_NOW,
        )
        parsed = orjson.loads(content)
        if compute.is_tool_result_item(parsed):
            compute.create_agent_link_from_tool_result(session.id, item, parsed)
            update = compute.create_tool_result_link_live(session.id, item, parsed)
            if update:
                compute.check_agent_naturally_stopped(session.id, update)


def _links(session: Session, tool_use_id: str) -> list[ToolResultLink]:
    return list(
        ToolResultLink.objects.filter(session=session, tool_use_id=tool_use_id)
        .order_by("tool_result_line_num")
    )


# ---------------------------------------------------------------------------
# Batch path
# ---------------------------------------------------------------------------


class TestBatchSubagentLinks:
    def test_v1_spawn_links_subagent_and_pairs_both_results(self, parent_session):
        call_id = "call_v1_001"
        agent_id = "019e2cab-be94-71a0-a790-0864c5d82d83"
        _seed(parent_session, [
            _spawn_call_v1(call_id),
            _spawn_ack_v1(call_id, agent_id),
            _subagent_notification_v1(agent_id),
        ])

        _run_batch_compute(parent_session)

        link = AgentLink.objects.get(session=parent_session)
        assert link.agent_id == agent_id
        assert link.tool_use_id == call_id
        assert link.tool_use_line_num == 1
        assert link.is_background is True

        results = _links(parent_session, call_id)
        assert [r.tool_result_line_num for r in results] == [2, 3]
        assert all(r.error is None for r in results)

    def test_v2_spawn_links_subagent_and_pairs_both_results(self, parent_session):
        call_id = "call_v2_001"
        agent_id = "01a003c9-adec-79a2-b236-131c185aeaf9"
        agent_path = "/root/display_test"
        _seed(parent_session, [
            _spawn_call_v2(call_id),
            _sub_agent_activity(call_id, agent_id, agent_path),
            _spawn_ack_v2(call_id, agent_path),
            _final_answer_v2(agent_path),
        ])

        _run_batch_compute(parent_session)

        link = AgentLink.objects.get(session=parent_session)
        assert link.agent_id == agent_id, "the thread id comes from sub_agent_activity, not the ack"
        assert link.tool_use_id == call_id
        assert link.tool_use_line_num == 1
        assert link.is_background is True

        results = _links(parent_session, call_id)
        assert [r.tool_result_line_num for r in results] == [3, 4], (
            "expected the ack and the FINAL_ANSWER rebound onto the spawn call"
        )
        assert all(r.error is None for r in results), (
            "the v2 ack {'task_name': ...} is a success, not a rejection"
        )

    def test_v2_ack_alone_creates_no_link(self, parent_session):
        """Without the activity event there is no thread id to link to."""
        call_id = "call_v2_002"
        _seed(parent_session, [
            _spawn_call_v2(call_id),
            _spawn_ack_v2(call_id, "/root/display_test"),
        ])

        _run_batch_compute(parent_session)

        assert not AgentLink.objects.filter(session=parent_session).exists()
        results = _links(parent_session, call_id)
        assert [r.tool_result_line_num for r in results] == [2]
        assert results[0].error is None

    def test_v2_non_spawn_activity_kinds_create_no_link(self, parent_session):
        """``interacted`` carries the *messaging* call_id — never a spawn."""
        send_call_id = "call_v2_send"
        _seed(parent_session, [
            _line("response_item", {
                "type": "function_call",
                "name": "send_message",
                "namespace": "collaboration",
                "call_id": send_call_id,
                "arguments": json.dumps({"target": "/root/display_test", "message": _ENCRYPTED}),
            }),
            _sub_agent_activity(
                send_call_id, "01a003c9-adec-79a2-b236-131c185aeaf9",
                "/root/display_test", kind="interacted",
            ),
        ])

        _run_batch_compute(parent_session)

        assert not AgentLink.objects.filter(session=parent_session).exists()

    def test_v2_encrypted_message_is_not_a_completion(self, parent_session):
        """Mid-flight ``MESSAGE`` envelopes must not pair with the spawn."""
        call_id = "call_v2_003"
        agent_path = "/root/display_test"
        _seed(parent_session, [
            _spawn_call_v2(call_id),
            _sub_agent_activity(call_id, "agent-thread-003", agent_path),
            _spawn_ack_v2(call_id, agent_path),
            _inter_agent_message_v2(agent_path),
        ])

        _run_batch_compute(parent_session)

        results = _links(parent_session, call_id)
        assert [r.tool_result_line_num for r in results] == [2 + 1], (
            "only the ack should pair; the encrypted message is not a completion"
        )

    def test_v2_rejected_spawn_surfaces_the_error(self, parent_session):
        """A freeform rejection still flips the link to error on v2 names."""
        call_id = "call_v2_004"
        rejection = "Full-history forked agents inherit the parent agent type, model, ..."
        _seed(parent_session, [
            _spawn_call_v2(call_id),
            _spawn_rejection(call_id, rejection),
        ])

        _run_batch_compute(parent_session)

        results = _links(parent_session, call_id)
        assert len(results) == 1
        assert results[0].error == rejection
        assert not AgentLink.objects.filter(session=parent_session).exists()


# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------


class TestLiveSubagentLinks:
    def test_v1_live_sequence(self, parent_session):
        call_id = "call_live_v1"
        agent_id = "019e2cab-be94-71a0-a790-0864c5d82d83"
        _run_live_sync(parent_session, [
            _spawn_call_v1(call_id),
            _spawn_ack_v1(call_id, agent_id),
            _subagent_notification_v1(agent_id),
        ])

        link = AgentLink.objects.get(session=parent_session)
        assert (link.agent_id, link.tool_use_id) == (agent_id, call_id)
        assert [r.tool_result_line_num for r in _links(parent_session, call_id)] == [2, 3]

    def test_v2_live_sequence(self, parent_session):
        call_id = "call_live_v2"
        agent_id = "01a003c9-adec-79a2-b236-131c185aeaf9"
        agent_path = "/root/display_test"
        subagent = Session.objects.create(
            id=agent_id,
            project=parent_session.project,
            provider=Provider.CODEX,
            type="subagent",
            parent_session=parent_session,
            file_path=f"2026/08/15/rollout-{agent_id}.jsonl",
        )
        _run_live_sync(parent_session, [
            _spawn_call_v2(call_id),
            _sub_agent_activity(call_id, agent_id, agent_path),
            _spawn_ack_v2(call_id, agent_path),
            _final_answer_v2(agent_path),
        ])

        link = AgentLink.objects.get(session=parent_session)
        assert (link.agent_id, link.tool_use_id) == (agent_id, call_id)
        assert link.is_background is True
        assert [r.tool_result_line_num for r in _links(parent_session, call_id)] == [3, 4], (
            "the FINAL_ANSWER must resolve its agent path back to the spawn call"
        )

        subagent.refresh_from_db()
        assert subagent.last_stopped_at is not None, (
            "the second result closes the background agent — without it the "
            "subagent would stay 'running' forever in the parent's tool card"
        )

    def test_v2_live_broadcasts_drive_both_spinners(self, parent_session):
        """The spawn turns both spinners on, the FINAL_ANSWER turns them off.

        Frontend contract (``useWebSocket.js``):

        - ``agent_link_created`` → ``setSyntheticProcessState(agent_id)``:
          the subagent tab shows its ``ProcessIndicator``, and the tool
          card's ``isAgentRunning`` starts counting against the
          background threshold of 2 results.
        - ``tool_state`` with ``result_count >= 2`` on a background link
          → ``removeSyntheticProcessState``: both spinners stop.
        """
        call_id = "call_live_v2_spinners"
        agent_id = "agent-thread-spinners"
        agent_path = "/root/display_test"
        agent_updates, tool_updates = _run_live_sync_collecting(parent_session, [
            _spawn_call_v2(call_id),
            _sub_agent_activity(call_id, agent_id, agent_path),
            _spawn_ack_v2(call_id, agent_path),
            _final_answer_v2(agent_path),
        ])

        assert len(agent_updates) == 1, "exactly one agent_link_created broadcast"
        spawn_broadcast = agent_updates[0]
        assert spawn_broadcast.agent_id == agent_id
        assert spawn_broadcast.tool_use_id == call_id
        assert spawn_broadcast.is_background is True
        assert spawn_broadcast.started_at == _NOW, "spinner start time = the spawn line"

        counts = [(u.tool_use_id, u.result_count) for u in tool_updates]
        assert counts == [(call_id, 1), (call_id, 2)], (
            "the ack alone must keep the agent running (1 < 2); the "
            "FINAL_ANSWER must take it to the threshold that stops both spinners"
        )

    def test_v2_live_final_answer_of_unknown_agent_stays_unpaired(self, parent_session):
        """No spawn event for that path → no rebind, and no bogus link."""
        _run_live_sync(parent_session, [
            _final_answer_v2("/root/never_spawned"),
        ])

        assert not ToolResultLink.objects.filter(session=parent_session).exists()
        assert not AgentLink.objects.filter(session=parent_session).exists()


# ---------------------------------------------------------------------------
# Subagent idleness — the parent chain is not enough
# ---------------------------------------------------------------------------


class TestSubagentIdleness:
    """A v2 subagent can finish its turn without ever completing the spawn.

    It answers its parent through ``send_message`` and stays alive (Codex
    reports it ``running`` until it is closed), so the ``FINAL_ANSWER`` that
    would pair as the spawn's second result never comes. Its own transcript
    is then the only thing that says "idle", and every "is this agent still
    working?" surface reads it off ``Session.last_stopped_at``.
    """

    def test_turn_events_map_to_the_running_state(self):
        compute = get_compute()
        assert compute.subagent_turn_boundary(orjson.loads(_task_complete())) is True
        assert compute.subagent_turn_boundary(orjson.loads(_task_started())) is False
        assert compute.subagent_turn_boundary(orjson.loads(_spawn_ack_v2("c", "/root/x"))) is None

    def _subagent(self, parent: Session, suffix: str = "1") -> Session:
        return Session.objects.create(
            id=f"subagent-idle-{suffix}",
            project=parent.project,
            provider=Provider.CODEX,
            type="subagent",
            parent_session=parent,
            file_path=f"2026/08/15/rollout-idle-{suffix}.jsonl",
        )

    def _sync(self, session: Session, lines: list[str], tmp_path) -> None:
        """Append ``lines`` to the session's rollout and sync from ``last_offset``.

        One file per session, appended to — the live path reads from the
        stored offset, so a fresh file per call would look already-consumed.
        """
        rollout = tmp_path / f"rollout-{session.id}.jsonl"
        with rollout.open("a", encoding="utf-8") as handle:
            handle.writelines(f"{line}\n" for line in lines)
        get_compute().sync_session_items_from_file(session, rollout)

    def test_turn_end_marks_the_subagent_stopped(self, parent_session, tmp_path):
        subagent = self._subagent(parent_session)

        self._sync(subagent, [_task_started(), _task_complete()], tmp_path)

        subagent.refresh_from_db()
        assert subagent.last_stopped_at is not None

    def test_a_new_turn_clears_it_again(self, parent_session, tmp_path):
        """A follow-up message restarts the subagent — it is working again."""
        subagent = self._subagent(parent_session, "2")
        self._sync(subagent, [_task_started(), _task_complete()], tmp_path)

        self._sync(subagent, [_task_started()], tmp_path)

        subagent.refresh_from_db()
        assert subagent.last_stopped_at is None

    def test_a_batch_without_a_boundary_leaves_it_alone(self, parent_session, tmp_path):
        subagent = self._subagent(parent_session, "3")
        self._sync(subagent, [_task_started(), _task_complete()], tmp_path)
        subagent.refresh_from_db()
        stopped_at = subagent.last_stopped_at

        self._sync(subagent, [_spawn_ack_v2("call-x", "/root/x")], tmp_path)

        subagent.refresh_from_db()
        assert subagent.last_stopped_at == stopped_at

    def test_main_sessions_are_untouched(self, parent_session, tmp_path):
        """Only subagents: a top-level session's lifecycle belongs to its process."""
        self._sync(parent_session, [_task_started(), _task_complete()], tmp_path)

        parent_session.refresh_from_db()
        assert parent_session.last_stopped_at is None

    def test_links_payload_carries_the_idle_timestamp(self, parent_session, tmp_path):
        """A page reload must not resurrect a finished subagent's spinner."""
        from twicc.core.session_queries import serialize_agent_links

        subagent = self._subagent(parent_session, "4")
        self._sync(subagent, [_task_started(), _task_complete()], tmp_path)
        link = AgentLink.objects.create(
            session=parent_session,
            tool_use_line_num=1,
            tool_use_id="call_payload",
            agent_id=subagent.id,
            is_background=True,
            started_at=_NOW,
        )

        payload = serialize_agent_links([link])

        subagent.refresh_from_db()
        assert payload[0]["agent_stopped_at"] == subagent.last_stopped_at.isoformat()

    def test_links_payload_stays_null_while_running(self, parent_session):
        from twicc.core.session_queries import serialize_agent_links

        subagent = self._subagent(parent_session, "5")
        link = AgentLink.objects.create(
            session=parent_session,
            tool_use_line_num=1,
            tool_use_id="call_payload_running",
            agent_id=subagent.id,
            is_background=True,
            started_at=_NOW,
        )

        payload = serialize_agent_links([link])

        assert payload[0]["agent_stopped_at"] is None
        assert payload[0]["agent_slug"] == subagent.slug


# ---------------------------------------------------------------------------
# The subagent's opening prompt
# ---------------------------------------------------------------------------


def _new_task_v2(task_path: str = "/root/tweak_display_test", sender: str = "/root", *, clear: str = "") -> str:
    """The task envelope Codex writes in the *receiving* thread."""
    text = (
        "Message Type: NEW_TASK\n"
        f"Task name: {task_path}\n"
        f"Sender: {sender}\n"
        "Payload:\n"
        f"{clear}"
    )
    content: list[dict] = [{"type": "input_text", "text": text}]
    if not clear:
        content.append({"type": "encrypted_content", "encrypted_content": _ENCRYPTED})
    return _line("response_item", {"type": "agent_message", "content": content})


class TestSubagentOpeningPrompt:
    """A v2 subagent has no ``user_message`` — its task arrives as an
    inter-agent envelope. In its own thread that IS the prompt, so it
    renders (and counts) as the opening user message."""

    def _subagent(self, parent: Session, suffix: str) -> Session:
        return Session.objects.create(
            id=f"subagent-prompt-{suffix}",
            project=parent.project,
            provider=Provider.CODEX,
            type="subagent",
            parent_session=parent,
            file_path=f"2026/08/15/rollout-prompt-{suffix}.jsonl",
        )

    def test_new_task_becomes_the_opening_user_message(self, parent_session):
        compute = get_compute()
        parsed = orjson.loads(_new_task_v2())

        assert compute.compute_item_kind(parsed) == ItemKind.USER_MESSAGE

    def test_other_envelopes_stay_out_of_the_transcript(self, parent_session):
        """Mid-flight messages already show through the send_message cards."""
        compute = get_compute()

        for line in (_inter_agent_message_v2("/root/x"), _final_answer_v2("/root/x")):
            assert compute.compute_item_kind(orjson.loads(line)) != ItemKind.USER_MESSAGE

    def test_a_nested_subagent_is_treated_the_same(self, parent_session):
        """Filtering is on the message type — never on ``Sender: /root``."""
        compute = get_compute()
        nested = _new_task_v2("/root/impl/review", sender="/root/impl")

        assert compute.compute_item_kind(orjson.loads(nested)) == ItemKind.USER_MESSAGE

    def test_title_falls_back_to_the_task_name(self, parent_session):
        """The body is encrypted, so the only readable description wins."""
        compute = get_compute()
        parsed = orjson.loads(_new_task_v2())

        assert compute.extract_title_from_user_message(parsed) == "Tweak display test"

    def test_a_readable_payload_wins_over_the_task_name(self, parent_session):
        compute = get_compute()
        parsed = orjson.loads(_new_task_v2(clear="Do the thing, carefully."))

        assert compute.extract_title_from_user_message(parsed) == "Do the thing, carefully."

    def test_live_sync_counts_it_and_titles_the_session(self, parent_session, tmp_path):
        subagent = self._subagent(parent_session, "live")
        rollout = tmp_path / "rollout-prompt-live.jsonl"
        rollout.write_text(f"{_task_started()}\n{_new_task_v2()}\n", encoding="utf-8")

        get_compute().sync_session_items_from_file(subagent, rollout)

        subagent.refresh_from_db()
        assert subagent.user_message_count == 1
        assert subagent.title == "Tweak display test"

    def test_followup_tasks_count_too(self, parent_session, tmp_path):
        """``followup_task`` hands a live agent a new instruction."""
        subagent = self._subagent(parent_session, "followup")
        rollout = tmp_path / "rollout-prompt-followup.jsonl"
        rollout.write_text(
            f"{_new_task_v2()}\n{_new_task_v2('/root/second_round')}\n", encoding="utf-8",
        )

        get_compute().sync_session_items_from_file(subagent, rollout)

        subagent.refresh_from_db()
        assert subagent.user_message_count == 2
        assert subagent.title == "Tweak display test", "the first task names the session"
