"""Task tool_use replay vs compaction-duplicated JSONL lines.

Claude Code's compaction re-appends the retained history lines verbatim to
the session JSONL (same ``uuid``, same tool_use ``id``). The task-replay
state machinery (``_enrich_task_tool_uses`` / ``_rebuild_state_if_missing``)
used to treat those duplicates as fresh TaskCreate/TaskUpdate calls: every
compaction re-created the whole task list (11 real tasks shown as 33 after
two compactions), and the corrupted ``twiccTasksData`` snapshots were then
persisted into ``SessionItem.content`` and ``Session.tasks``.

Covered here:
  * live path — a duplicated TaskCreate/TaskUpdate block (already-seen
    tool_use id) never advances the state;
  * watcher restart — ``_rebuild_state_if_missing`` registers the tool_use
    ids of every task block already in DB, so a duplicate arriving after the
    restart is recognised;
  * batch recompute — a session corrupted by the old code is repaired:
    duplicated blocks are stripped of their enrichment, post-duplicate
    snapshots are re-derived from the tool inputs, ``Session.tasks`` shrinks
    back to the real list;
  * batch recompute — a healthy session is left byte-identical (the
    immutability rule still holds when no duplicate exists).
"""

from __future__ import annotations

import queue

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute


pytestmark = pytest.mark.usefixtures("metadata_rows")


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------

_TS = "2026-01-01T12:00:00.000Z"


def _task_create_block(tool_use_id: str, subject: str, **enrichment) -> dict:
    block = {
        "type": "tool_use",
        "id": tool_use_id,
        "name": "TaskCreate",
        "input": {"subject": subject, "activeForm": f"doing {subject}"},
    }
    block.update(enrichment)
    return block


def _task_update_block(tool_use_id: str, task_id: str, status: str, **enrichment) -> dict:
    block = {
        "type": "tool_use",
        "id": tool_use_id,
        "name": "TaskUpdate",
        "input": {"taskId": task_id, "status": status},
    }
    block.update(enrichment)
    return block


def _assistant_line(blocks: list[dict], uuid: str) -> str:
    # orjson (compact separators) — the DB pre-filters of
    # _rebuild_state_if_missing match the compact '"name":"TaskCreate"'
    # form that real stored content uses.
    return orjson.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "timestamp": _TS,
            "message": {"role": "assistant", "content": blocks},
        }
    ).decode()


def _task_data(task_id: str, subject: str, status: str = "pending") -> dict:
    return {
        "subject": subject,
        "activeForm": f"doing {subject}",
        "status": status,
        "id": task_id,
    }


def _apply_compute_results(result_queue, provider_compute) -> None:
    """Drain the queue and apply every ``session_complete`` message."""
    from queue import Empty

    while True:
        try:
            raw_msg = result_queue.get_nowait()
        except Empty:
            break
        msg = orjson.loads(raw_msg)
        if msg.get("type") == "session_complete":
            provider_compute.apply_session_complete(msg)


@pytest.fixture
def claude_session(db):
    project = Project.objects.create(id="test-project-claude-tasks")
    return Session.objects.create(
        id="test-session-claude-tasks",
        project=project,
        provider=Provider.CLAUDE_CODE,
    )


def _blocks_of(item: SessionItem) -> list[dict]:
    from tests.content_storage_helpers import read_current

    return orjson.loads(read_current(item))["message"]["content"]


# ---------------------------------------------------------------------------
# Live path (in-memory state already warm)
# ---------------------------------------------------------------------------


class TestLiveDedup:
    def test_duplicate_task_create_does_not_advance_state(self, claude_session):
        compute = ClaudeCodeSessionCompute()
        sid = claude_session.id

        create_a = _task_create_block("toolu_A", "task A")
        assert compute._enrich_task_tool_uses([create_a], sid, 10) is True
        assert create_a["twiccTaskData"]["id"] == "1"

        create_b = _task_create_block("toolu_B", "task B")
        compute._enrich_task_tool_uses([create_b], sid, 11)
        assert create_b["twiccTaskData"]["id"] == "2"

        # Compaction re-appends both creates verbatim (raw, same ids).
        dup_a = _task_create_block("toolu_A", "task A")
        dup_b = _task_create_block("toolu_B", "task B")
        assert compute._enrich_task_tool_uses([dup_a], sid, 20) is False
        assert compute._enrich_task_tool_uses([dup_b], sid, 21) is False
        assert "twiccTaskData" not in dup_a
        assert "twiccTasksData" not in dup_b

        # A genuinely new task gets id 3, not 5, and the snapshot holds 3 tasks.
        create_c = _task_create_block("toolu_C", "task C")
        compute._enrich_task_tool_uses([create_c], sid, 30)
        assert create_c["twiccTaskData"]["id"] == "3"
        assert len(create_c["twiccTasksData"]) == 3

    def test_duplicate_task_update_does_not_regress_status(self, claude_session):
        compute = ClaudeCodeSessionCompute()
        sid = claude_session.id

        create = _task_create_block("toolu_A", "task A")
        compute._enrich_task_tool_uses([create], sid, 10)
        upd_progress = _task_update_block("toolu_U1", "1", "in_progress")
        compute._enrich_task_tool_uses([upd_progress], sid, 11)
        upd_done = _task_update_block("toolu_U2", "1", "completed")
        compute._enrich_task_tool_uses([upd_done], sid, 12)

        # Re-appended copy of the older in_progress update.
        dup = _task_update_block("toolu_U1", "1", "in_progress")
        compute._enrich_task_tool_uses([dup], sid, 20)
        assert "twiccTaskData" not in dup

        probe = _task_create_block("toolu_B", "task B")
        compute._enrich_task_tool_uses([probe], sid, 30)
        by_id = {t["id"]: t for t in probe["twiccTasksData"]}
        assert by_id["1"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Watcher restart (state rebuilt from DB)
# ---------------------------------------------------------------------------


class TestRebuildDedup:
    def test_rebuild_registers_ids_of_persisted_blocks(self, claude_session):
        # Two enriched TaskCreate lines already persisted (live path output).
        t1 = _task_data("1", "task A")
        t2 = _task_data("2", "task B")
        SessionItem.objects.create(
            session=claude_session,
            line_num=1,
            content=_assistant_line(
                [_task_create_block("toolu_A", "task A", twiccTaskData=t1, twiccTasksData=[t1])],
                uuid="u1",
            ),
        )
        SessionItem.objects.create(
            session=claude_session,
            line_num=2,
            content=_assistant_line(
                [_task_create_block("toolu_B", "task B", twiccTaskData=t2, twiccTasksData=[t1, t2])],
                uuid="u2",
            ),
        )

        # Fresh process: the in-memory state is empty and must be rebuilt.
        compute = ClaudeCodeSessionCompute()
        dup = _task_create_block("toolu_A", "task A")
        assert compute._enrich_task_tool_uses([dup], claude_session.id, 10) is False
        assert "twiccTaskData" not in dup

        create_c = _task_create_block("toolu_C", "task C")
        compute._enrich_task_tool_uses([create_c], claude_session.id, 11)
        assert create_c["twiccTaskData"]["id"] == "3"
        assert len(create_c["twiccTasksData"]) == 3


# ---------------------------------------------------------------------------
# Batch recompute (repair + no-churn)
# ---------------------------------------------------------------------------


class TestBatchRecompute:
    def _seed_corrupted_session(self, session) -> None:
        """Reproduce what the pre-dedup code persisted after one compaction.

        Lines 1-2: originals, correctly enriched (ids 1-2).
        Lines 3-4: compaction duplicates, wrongly enriched as ids 3-4.
        Line 5: original TaskUpdate enriched against the polluted state
        (4-task snapshot).
        """
        t1 = _task_data("1", "task A")
        t2 = _task_data("2", "task B")
        t3 = _task_data("3", "task A")
        t4 = _task_data("4", "task B")
        t1_done = _task_data("1", "task A", status="completed")

        SessionItem.objects.create(
            session=session,
            line_num=1,
            content=_assistant_line(
                [_task_create_block("toolu_A", "task A", twiccTaskData=t1, twiccTasksData=[t1])],
                uuid="u1",
            ),
        )
        SessionItem.objects.create(
            session=session,
            line_num=2,
            content=_assistant_line(
                [_task_create_block("toolu_B", "task B", twiccTaskData=t2, twiccTasksData=[t1, t2])],
                uuid="u2",
            ),
        )
        SessionItem.objects.create(
            session=session,
            line_num=3,
            content=_assistant_line(
                [_task_create_block("toolu_A", "task A", twiccTaskData=t3, twiccTasksData=[t1, t2, t3])],
                uuid="u1",
            ),
        )
        SessionItem.objects.create(
            session=session,
            line_num=4,
            content=_assistant_line(
                [_task_create_block("toolu_B", "task B", twiccTaskData=t4, twiccTasksData=[t1, t2, t3, t4])],
                uuid="u2",
            ),
        )
        SessionItem.objects.create(
            session=session,
            line_num=5,
            content=_assistant_line(
                [
                    _task_update_block(
                        "toolu_U1",
                        "1",
                        "completed",
                        twiccTaskData=t1_done,
                        twiccTasksData=[t1_done, t2, t3, t4],
                        twiccTasksTotal=4,
                    )
                ],
                uuid="u3",
            ),
        )

    def test_recompute_repairs_corrupted_session(self, claude_session):
        self._seed_corrupted_session(claude_session)

        compute = ClaudeCodeSessionCompute()
        result_q = queue.Queue()
        compute.compute_session_metadata(claude_session.id, result_q, run_id=0)
        _apply_compute_results(result_q, compute)

        items = {i.line_num: i for i in SessionItem.objects.filter(session=claude_session)}

        # Originals before the first duplicate: untouched (immutability).
        assert _blocks_of(items[1])[0]["twiccTaskData"]["id"] == "1"
        assert len(_blocks_of(items[2])[0]["twiccTasksData"]) == 2

        # Duplicates: enrichment stripped, raw block preserved.
        for line in (3, 4):
            block = _blocks_of(items[line])[0]
            assert "twiccTaskData" not in block
            assert "twiccTasksData" not in block
            assert block["input"]["subject"] in ("task A", "task B")

        # Post-duplicate original: snapshot re-derived against the clean state.
        upd = _blocks_of(items[5])[0]
        assert upd["twiccTaskData"]["id"] == "1"
        assert upd["twiccTaskData"]["status"] == "completed"
        assert len(upd["twiccTasksData"]) == 2
        assert upd["twiccTasksTotal"] == 2

        # Session.tasks (the Tasks tab source) shrinks back to the real list.
        claude_session.refresh_from_db()
        assert len(claude_session.tasks["items"]) == 2
        statuses = {t["content"]: t["status"] for t in claude_session.tasks["items"]}
        assert statuses == {"task A": "completed", "task B": "pending"}

    def test_recompute_leaves_healthy_session_untouched(self, claude_session):
        t1 = _task_data("1", "task A")
        t1_done = _task_data("1", "task A", status="completed")
        SessionItem.objects.create(
            session=claude_session,
            line_num=1,
            content=_assistant_line(
                [_task_create_block("toolu_A", "task A", twiccTaskData=t1, twiccTasksData=[t1])],
                uuid="u1",
            ),
        )
        SessionItem.objects.create(
            session=claude_session,
            line_num=2,
            content=_assistant_line(
                [
                    _task_update_block(
                        "toolu_U1",
                        "1",
                        "completed",
                        twiccTaskData=t1_done,
                        twiccTasksData=[t1_done],
                        twiccTasksTotal=1,
                    )
                ],
                uuid="u2",
            ),
        )
        from tests.content_storage_helpers import read_current

        before = {i.line_num: read_current(i) for i in SessionItem.objects.filter(session=claude_session)}

        compute = ClaudeCodeSessionCompute()
        result_q = queue.Queue()
        compute.compute_session_metadata(claude_session.id, result_q, run_id=0)
        _apply_compute_results(result_q, compute)

        after = {i.line_num: read_current(i) for i in SessionItem.objects.filter(session=claude_session)}
        assert after == before
