# Timeline Incremental Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the batch parcours of the timeline backfill with a candidate-driven loop: one ordered id list read once outside the lock, then one session per lock acquisition, with an end-of-run retry of failed sessions.

**Architecture:** A composite index `(session_id, kind)` on `TimelineEvent` makes "does this session have a `session.created` row" a direct seek. `timeline_backfill.py` is rewritten around `candidate_ids()` (ids only, newest `mtime` first), `backfill_session_sync(session_id)` (everything about one session, inside one lock acquisition, all-or-nothing, with its own non-swallowing insert path) and an async runner that retries failed sessions at the end of the run. What the backfill writes does not change.

**Tech Stack:** Python 3.13, Django 6 (SQLite, WAL), asyncio, pytest + pytest-django.

**Spec:** `docs/plans/2026-10-14-timeline-incremental-backfill-design.md` (read it first; section numbers below refer to it). It is a delta over the system implemented at commit `cfa958c0` (`docs/plans/2026-10-13-session-timeline-design.md`, which is not amended).

## Global Constraints

- All code, comments and docs in **English** (CLAUDE.md).
- Work in the worktree `/home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline` (branch `feature/session-timeline`). **Prefix every Bash command with `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && `.** Never touch the main checkout.
- Tests: `cd <worktree> && TWICC_DATA_DIR=$PWD uv run pytest <files> -v`. Never `uv pip`, never `--active`. Python lint: `uvx ruff check <files>` (ruff is not installed in the env).
- The `Session` table is **not** modified (no column, no index). B1: the marker is the `session.created` timeline row.
- B3: the index is a plain composite index `(session_id, kind)` on `TimelineEvent`, **not** a partial index (an ORM filter binds `kind` as a parameter and SQLite does not use a partial index for a bound parameter).
- B4: the candidate id list is read **once** per run, outside the lock, ordered `mtime` descending then `id` descending, ids only.
- B5/B6: one `run_under_db_write_lock` acquisition per session; reload, re-check, reads, row building and insertion all happen inside it. No batches.
- B8 and §5: a session that was written or skipped is never attempted again in the same run; a failed session is retried only by the end-of-run retry: at most 3 more attempts, with growing pauses (outside the lock) under about 10 s in total, then an error log and the session is left for the next boot.
- §5: a session's rows are inserted all-or-nothing in one transaction through the backfill's own non-swallowing insert path (not `record_event_sync`); an already-present `dedupe_key` counts as written, not as a failure; the refusal of the lock because the writer is stopping is not a session failure: it ends the run.
- §7: what is written is unchanged: `session.created` (derived, `origin="sync"`, `settings` null, `spawned_by` from the column) and the `<flag>.started` rows (estimated), never process rows.
- Do **not** run `migrate`, restart servers or run `devctl.py` (CLAUDE.md "Operations Reserved to User"). Creating the migration file with `makemigrations` is allowed and required. Do **not** write the CHANGELOG.
- Never import application code inside a migration (the migration of this plan is auto-generated).
- `NamedTuple`, not `@dataclass`; no needless `as` aliases; backend JSON uses `orjson`.
- Commit messages: Conventional Commits, a descriptive body, and the trailer `Co-Authored-By: Claude MODEL <noreply@anthropic.com>` where `MODEL` is the exact name of the model doing the work. `git add` lists files explicitly, never a directory. Two untracked files `docs/plans/2026-07-18-*` are not part of this work (and are not in this worktree); the new spec and this plan are untracked in the worktree and are committed in Task 1, Step 0.

## Review Focus

Conditions the spec implies that a happy path would not exercise. Each has a pinning test in the task that owns the code.

1. **A live hook writes `session.created` between the id snapshot and the session's turn:** the in-lock re-check skips it, no duplicate row. Task 2.
2. **The second row of a session fails to insert:** the first row is rolled back too (all-or-nothing) and the session is still a candidate. Task 2.
3. **A session has live flag rows (for example `archived.ended`) but no `session.created`:** it gets its `session.created`, but no flag row for a subject that already has a row. Task 2.
4. **The cost of the candidate test does not depend on how many timeline events a session has:** `EXPLAIN QUERY PLAN` uses the new index, and the number of SQL statements per session is constant. Tasks 1 and 2.
5. **Order:** most recent `mtime` first, ties broken by `id` descending. Task 2.
6. **Transient failure then success inside the same run; permanent failure bounded to 1 + 3 attempts; the run ends.** Task 3.
7. **The writer is stopping:** the run ends without burning the retry rounds. Task 3.
8. **A second run has no candidates and takes no lock.** Task 3.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/twicc/core/models.py` | modify | Add the `(session_id, kind)` index to `TimelineEvent.Meta.indexes` |
| `src/twicc/core/migrations/0157_timeline_event_session_kind_index.py` | create (generated) | Creates the index |
| `src/twicc/core/services/timeline_backfill.py` | rewrite | `candidate_ids`, `backfill_session_sync`, the insert path, the async runner with the end-of-run retry |
| `tests/test_timeline_backfill.py` | rewrite | Behaviour of the new backfill |
| `tests/test_timeline_backfill_index.py` | create | Index exists; candidate query plan |
| `tests/test_timeline_process_hooks.py` | modify | One unordered query made order-explicit (the new index can change SQLite's row order) |
| `tests/test_timeline_backfill_bench.py` | create | Opt-in benchmark (skipped unless `TWICC_TIMELINE_BENCH=1`) |
| `CLAUDE.md`, `AGENTS.md` | modify | One sentence on the new backfill behaviour |
| `docs/plans/2026-10-14-timeline-incremental-backfill-design.md`, `...-plan.md` | add | The spec and this plan (committed in Task 1, Step 0) |

Migration placement (spec open point 1): a **new** migration `0157`, not an edit of `0156`. It is correct whether or not `0156_timeline_event` was already applied on someone's database, and the branch is not merged yet.

---

### Task 1: The `(session_id, kind)` index

**Files:**
- Modify: `src/twicc/core/models.py` (`TimelineEvent.Meta.indexes`, ~line 2257)
- Create: `src/twicc/core/migrations/0157_timeline_event_session_kind_index.py` (generated)
- Modify: `tests/test_timeline_process_hooks.py` (one unordered query that relied on the old row order, Step 5)
- Test: `tests/test_timeline_backfill_index.py`

**Interfaces:**
- Produces: the index `idx_timeline_session_kind` on `core_timelineevent (session_id, kind)`.

- [ ] **Step 0: Commit the spec and this plan**

They are untracked in the worktree and belong in history before the implementation starts:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add docs/plans/2026-10-14-timeline-incremental-backfill-design.md docs/plans/2026-10-14-timeline-incremental-backfill-plan.md
git commit   # subject: docs(plans): add the timeline incremental backfill design and plan
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_timeline_backfill_index.py
import pytest
from django.db import connection

from twicc.core.models import TimelineEvent

pytestmark = pytest.mark.django_db


def test_the_session_kind_index_exists_in_the_model_and_in_the_database():
    assert "idx_timeline_session_kind" in {index.name for index in TimelineEvent._meta.indexes}
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, TimelineEvent._meta.db_table)
    index = constraints["idx_timeline_session_kind"]
    assert index["index"] and index["columns"] == ["session_id", "kind"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill_index.py -v`
Expected: FAIL (`assert 'idx_timeline_session_kind' in {...}`).

- [ ] **Step 3: Add the index**

In `TimelineEvent.Meta.indexes` add, after the `idx_timeline_kind` line:

```python
            models.Index(fields=["session_id", "kind"], name="idx_timeline_session_kind"),
```

- [ ] **Step 4: Generate the migration**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$(mktemp -d) uv run python -m django makemigrations core -n timeline_event_session_kind_index --settings=twicc.settings_test`
Expected: creates `src/twicc/core/migrations/0157_timeline_event_session_kind_index.py` with one `AddIndex`, depending on `('core', '0156_timeline_event')`, importing nothing from `twicc`. Open it and confirm.

- [ ] **Step 5: Fix the one existing test that relied on the old row order**

With the new index, SQLite may return the rows of an unordered query in another order. In `tests/test_timeline_process_hooks.py`, the query in `test_waiting_for_the_user_records_the_request_and_the_tool` is `list(TimelineEvent.objects.filter(session_id="s1", kind__startswith="awaiting_input"))` with no `order_by`; add `.order_by("id")` to it. Production code is unaffected: every production query on the timeline is ordered (`latest_event_id`, `_open_started_data`, the boot code). Then grep the other timeline tests for unordered multi-row queries (`grep -n "TimelineEvent.objects.filter" tests/test_timeline_*.py`) and add `.order_by("id")` wherever a test compares a list of rows or kinds in order.

- [ ] **Step 6: Run the tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill_index.py tests/test_timeline_model.py tests/test_timeline_process_hooks.py -v`
Expected: PASS. Then `cd ... && TWICC_DATA_DIR=$(mktemp -d) uv run python -m django makemigrations --check --dry-run --settings=twicc.settings_test` prints `No changes detected`.

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/models.py src/twicc/core/migrations/0157_timeline_event_session_kind_index.py tests/test_timeline_backfill_index.py tests/test_timeline_process_hooks.py
git commit   # subject: feat(timeline): index the timeline by session and kind
```

(Add to the `git add` list any other test file you had to touch in Step 5.)

---

### Task 2: Candidates and the per-session pass

**Files:**
- Rewrite: `src/twicc/core/services/timeline_backfill.py`
- Rewrite: `tests/test_timeline_backfill.py`

**Interfaces:**
- Consumes: `TimelineEvent`, `TimelineKind`, `TimelinePrecision`, `TimelineActor`, `Session`, `SessionType` (`twicc.core.models`); `validate_data` (`twicc.core.timeline`); `BACKFILL_PREFIX` (`twicc.core.services.timeline_boot`); `run_under_db_write_lock` (`twicc.providers.db_writer`).
- Produces (all in `twicc.core.services.timeline_backfill`):
  - `candidate_ids() -> list[str]` — sync, ids of normal sessions with `created_at` set and no `session.created` row, ordered `mtime` descending then `id` descending.
  - `class BackfillStatus(StrEnum)` with `WRITTEN = "written"` and `SKIPPED = "skipped"`; `class SessionOutcome(NamedTuple)` with `status: BackfillStatus` and `rows: int = 0`.
  - `backfill_session_sync(session_id: str) -> SessionOutcome` — sync, all-or-nothing in one transaction, **raises** on any write failure. Call it under the DB write lock from a worker thread.
  - `_insert_rows(rows: list[TimelineEvent]) -> None` — the single insert path (Task 4 may change its body after measuring).
  - `async run_timeline_backfill() -> None` — Task 3 completes it with the retry; here it is a single pass.
- `backfill_batch_sync`, `BATCH_SIZE`, `_existing_kinds` and `_backfill_session` are **removed** (nothing else imports them: `cli/run.py` imports only `run_timeline_backfill`).

- [ ] **Step 1: Rewrite the test file**

```python
# tests/test_timeline_backfill.py
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services import timeline_backfill
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_backfill import (
    BackfillStatus, backfill_session_sync, candidate_ids, run_timeline_backfill,
)
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-backfill", directory="/tmp/tl-backfill")


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def run_all():
    run_with_writer(run_timeline_backfill)


def test_creation_row_is_derived_from_created_at(project):
    created = timezone.now() - timedelta(days=10)
    make(project, created_at=created)
    run_all()
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "session.created" and row.occurred_at == created
    assert row.precision == "derived" and row.actor == "system" and row.project_id == project.id
    assert row.dedupe_key == "backfill:session.created"
    assert row.data == {"origin": "sync", "provider": "codex", "settings": None, "spawned_by": None}


def test_creation_row_carries_the_spawner_when_the_column_is_set(project):
    make(project, "boss", created_at=timezone.now())
    make(project, "kid", created_at=timezone.now(), spawned_by_id="boss", selected_model="opus")
    run_all()
    data = TimelineEvent.objects.get(session_id="kid", kind="session.created").data
    assert data["spawned_by"] == "boss" and data["settings"] is None


def test_flag_rows_use_last_updated_at_then_created_at(project):
    updated = timezone.now() - timedelta(days=1)
    created = updated - timedelta(days=5)
    make(project, "a", created_at=created, last_updated_at=updated, archived=True)
    make(project, "b", created_at=created, hidden=True)
    make(project, "c", created_at=created, mute_on_user_turn=True)
    run_all()
    assert TimelineEvent.objects.get(session_id="a", kind="archived.started").occurred_at == updated
    assert TimelineEvent.objects.get(session_id="b", kind="hidden.started").occurred_at == created
    assert TimelineEvent.objects.get(session_id="b", kind="hidden.started").precision == "estimated"
    assert TimelineEvent.objects.get(session_id="c", kind="muted.started").dedupe_key == "backfill:muted.started"


def test_pinned_row_carries_the_scope(project):
    make(project, created_at=timezone.now(), pinned="workspace")
    run_all()
    assert TimelineEvent.objects.get(session_id="s1", kind="pinned.started").data == {"scope": "workspace"}


def test_no_process_rows_are_ever_written(project):
    now = timezone.now()
    make(project, created_at=now, last_started_at=now, last_stopped_at=now)
    run_all()
    assert kinds("s1") == ["session.created"]


def test_running_twice_never_duplicates(project):
    make(project, created_at=timezone.now(), archived=True, last_updated_at=timezone.now())
    run_all()
    first = TimelineEvent.objects.count()
    run_all()
    assert TimelineEvent.objects.count() == first == 2


def test_a_live_flag_row_without_session_created_suppresses_only_that_subject(project):
    make(project, created_at=timezone.now(), archived=True, hidden=True, last_updated_at=timezone.now())
    record_event_sync("archived.ended", session_id="s1", actor="user")
    run_all()
    assert kinds("s1") == ["archived.ended", "session.created", "hidden.started"]


def test_candidates_exclude_uncomputed_subagents_and_processed_sessions(project):
    now = timezone.now()
    make(project, "ok", created_at=now)
    make(project, "uncomputed")
    make(project, "kid", created_at=now, type=SessionType.SUBAGENT, parent_session=Session.objects.get(id="ok"))
    make(project, "done", created_at=now)
    record_event_sync("session.created", session_id="done", actor="system", data={"origin": "twicc"})
    assert candidate_ids() == ["ok"]


def test_candidates_are_ordered_by_mtime_descending_then_id_descending(project):
    now = timezone.now()
    for sid, mtime in (("s-a", 100.0), ("s-b", 300.0), ("s-c", 300.0), ("s-d", 0.0)):
        make(project, sid, created_at=now, mtime=mtime)
    assert candidate_ids() == ["s-c", "s-b", "s-a", "s-d"]


def test_a_session_processed_meanwhile_is_skipped_without_a_duplicate(project):
    make(project, created_at=timezone.now())
    record_event_sync("session.created", session_id="s1", actor="system", data={"origin": "twicc"})
    outcome = backfill_session_sync("s1")
    assert outcome.status == BackfillStatus.SKIPPED and outcome.rows == 0
    assert TimelineEvent.objects.filter(session_id="s1").count() == 1


def test_a_vanished_or_uncomputed_session_is_skipped(project):
    make(project, "uncomputed")
    assert backfill_session_sync("uncomputed").status == BackfillStatus.SKIPPED
    assert backfill_session_sync("nope").status == BackfillStatus.SKIPPED


def test_a_failure_on_a_later_row_rolls_the_whole_session_back(project, monkeypatch):
    make(project, created_at=timezone.now(), archived=True)

    def insert_one_then_fail(rows):
        TimelineEvent.objects.bulk_create(rows[:1])
        raise RuntimeError("boom")

    monkeypatch.setattr(timeline_backfill, "_insert_rows", insert_one_then_fail)
    with pytest.raises(RuntimeError):
        backfill_session_sync("s1")
    assert TimelineEvent.objects.count() == 0
    assert candidate_ids() == ["s1"]


def test_an_already_present_dedupe_key_counts_as_written_not_as_a_failure(project):
    make(project, created_at=timezone.now())
    TimelineEvent.objects.create(
        session_id="s1", kind="turn.started", occurred_at=timezone.now(), actor="system",
        dedupe_key="backfill:session.created",
    )
    outcome = backfill_session_sync("s1")
    assert outcome.status == BackfillStatus.WRITTEN and outcome.rows == 0


def test_the_statements_per_session_do_not_depend_on_its_timeline_size(project):
    now = timezone.now()
    make(project, "empty", created_at=now, archived=True)
    make(project, "busy", created_at=now, archived=True)
    TimelineEvent.objects.bulk_create([
        TimelineEvent(session_id="busy", kind="turn.started", occurred_at=now, actor="agent") for _ in range(300)
    ])
    with CaptureQueriesContext(connection) as empty:
        backfill_session_sync("empty")
    with CaptureQueriesContext(connection) as busy:
        backfill_session_sync("busy")
    assert len(busy) == len(empty) <= 12
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill.py -v`
Expected: FAIL with `ImportError: cannot import name 'BackfillStatus'`.

- [ ] **Step 3: Rewrite the module**

```python
# src/twicc/core/services/timeline_backfill.py
"""Conservative, incremental backfill of the timeline (design 2026-10-14).

A session is "processed" when it has a ``session.created`` timeline row. The
backfill lists the candidate sessions once (ids only, newest ``mtime`` first),
then handles them one at a time, each inside its own lock acquisition. It
writes only ``session.created`` and the flag intervals, never a process row:
``Session.last_started_at`` / ``last_stopped_at`` are recomputed from the JSONL
file and cannot prove that TwiCC ran a process.
"""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import NamedTuple

from django.db import transaction
from django.db.models import Exists, OuterRef

from twicc.core.models import (
    Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services.timeline_boot import BACKFILL_PREFIX
from twicc.core.timeline import validate_data
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)

#: (flag attribute, started kind, subject prefix) — a flag is "set" when truthy / not null.
_FLAGS = (
    ("archived", TimelineKind.ARCHIVED_STARTED, "archived."),
    ("hidden", TimelineKind.HIDDEN_STARTED, "hidden."),
    ("mute_on_user_turn", TimelineKind.MUTED_STARTED, "muted."),
    ("pinned", TimelineKind.PINNED_STARTED, "pinned."),
)

#: The only kinds read to know what a session already has: its creation and, per flag subject,
#: both directions. Bounded by a handful of rows whatever the session's activity.
_RELEVANT_KINDS = (
    TimelineKind.SESSION_CREATED,
    TimelineKind.ARCHIVED_STARTED, TimelineKind.ARCHIVED_ENDED,
    TimelineKind.HIDDEN_STARTED, TimelineKind.HIDDEN_ENDED,
    TimelineKind.MUTED_STARTED, TimelineKind.MUTED_ENDED,
    TimelineKind.PINNED_STARTED, TimelineKind.PINNED_ENDED,
)


class BackfillStatus(StrEnum):
    WRITTEN = "written"
    SKIPPED = "skipped"


class SessionOutcome(NamedTuple):
    status: BackfillStatus
    rows: int = 0


def candidate_ids() -> list[str]:
    """Ids of the sessions to process, newest ``mtime`` first (ties: id descending).

    Sync, outside the lock, called once per run. A candidate is a normal session with
    ``created_at`` set and no ``session.created`` row: the anti-join is a direct seek on the
    ``(session_id, kind)`` index, whatever the number of timeline events.
    """
    has_created = TimelineEvent.objects.filter(session_id=OuterRef("id"), kind=TimelineKind.SESSION_CREATED)
    return list(
        Session.objects.filter(type=SessionType.SESSION, created_at__isnull=False)
        .filter(~Exists(has_created))
        .order_by("-mtime", "-id")
        .values_list("id", flat=True)
    )


def _row(session: Session, kind: str, occurred_at, precision: str, data: dict | None) -> TimelineEvent:
    return TimelineEvent(
        session_id=session.id, project_id=session.project_id, kind=kind, occurred_at=occurred_at,
        precision=precision, actor=TimelineActor.SYSTEM, data=validate_data(kind, data),
        dedupe_key=f"{BACKFILL_PREFIX}{kind}",
    )


def _build_rows(session: Session, existing: set[str]) -> list[TimelineEvent]:
    """The rows of one session. ``created_at`` is never empty here (candidates require it)."""
    rows = [_row(
        session, TimelineKind.SESSION_CREATED, session.created_at, TimelinePrecision.DERIVED,
        {"origin": "sync", "provider": session.provider, "settings": None, "spawned_by": session.spawned_by_id},
    )]
    flag_time = session.last_updated_at or session.created_at
    for attribute, kind, prefix in _FLAGS:
        value = getattr(session, attribute)
        if not value or any(known.startswith(prefix) for known in existing):
            continue
        rows.append(_row(
            session, kind, flag_time, TimelinePrecision.ESTIMATED,
            {"scope": value} if attribute == "pinned" else None,
        ))
    return rows


def _drop_present(session_id: str, rows: list[TimelineEvent]) -> list[TimelineEvent]:
    """Drop the rows whose ``dedupe_key`` already exists: that counts as written, not as a failure."""
    present = set(
        TimelineEvent.objects.filter(session_id=session_id, dedupe_key__in=[r.dedupe_key for r in rows])
        .values_list("dedupe_key", flat=True)
    )
    return [row for row in rows if row.dedupe_key not in present]


def _insert_rows(rows: list[TimelineEvent]) -> None:
    """The backfill's own insert path: it validates before (see ``_row``), shares the duplicate
    check with ``record_event_sync`` (``_drop_present``) and, unlike it, lets every error propagate
    so the session's transaction is rolled back as a whole."""
    TimelineEvent.objects.bulk_create(rows)


def backfill_session_sync(session_id: str) -> SessionOutcome:
    """Process one session, all-or-nothing, in one transaction. Raises on any write failure.

    Sync: call under the DB write lock from a worker thread, one acquisition per session.
    Everything is read fresh here, so a hook that wrote ``session.created`` since the id list was
    read is seen and the session is skipped.
    """
    with transaction.atomic():
        session = Session.objects.filter(
            id=session_id, type=SessionType.SESSION, created_at__isnull=False,
        ).first()
        if session is None:
            return SessionOutcome(BackfillStatus.SKIPPED)
        existing = set(
            TimelineEvent.objects.filter(session_id=session_id, kind__in=_RELEVANT_KINDS)
            .values_list("kind", flat=True).distinct()
        )
        if TimelineKind.SESSION_CREATED in existing:
            return SessionOutcome(BackfillStatus.SKIPPED)
        rows = _drop_present(session_id, _build_rows(session, existing))
        if rows:
            _insert_rows(rows)
        return SessionOutcome(BackfillStatus.WRITTEN, len(rows))


async def run_timeline_backfill() -> None:
    """Process every candidate session, one lock acquisition each, newest first (single pass)."""
    session_ids = await asyncio.to_thread(candidate_ids)
    written = rows = 0
    for session_id in session_ids:
        outcome = await run_under_db_write_lock(
            lambda current=session_id: asyncio.to_thread(backfill_session_sync, current)
        )
        if outcome.status == BackfillStatus.WRITTEN:
            written += 1
            rows += outcome.rows
        await asyncio.sleep(0)
    if written:
        logger.info("Timeline backfill wrote %d row(s) for %d session(s)", rows, written)
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill.py tests/test_timeline_boot.py tests/test_timeline_backfill_index.py -v`
Expected: all PASS. Add to the index test file the query-plan check of Step 5 first if you prefer to run it together.

- [ ] **Step 5: Pin the query plan of the candidate query**

Append to `tests/test_timeline_backfill_index.py`:

```python
from twicc.core.models import Session, SessionType, TimelineKind
from django.db.models import Exists, OuterRef


def test_the_candidate_anti_join_is_served_by_the_session_kind_index():
    has_created = TimelineEvent.objects.filter(session_id=OuterRef("id"), kind=TimelineKind.SESSION_CREATED)
    queryset = (
        Session.objects.filter(type=SessionType.SESSION, created_at__isnull=False)
        .filter(~Exists(has_created))
        .order_by("-mtime", "-id")
        .values_list("id", flat=True)
    )
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
        plan = " ".join(str(row[-1]) for row in cursor.fetchall())
    assert "idx_timeline_session_kind" in plan
```

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill_index.py -v`
Expected: PASS. If the plan does not name the index (the planner picked `idx_timeline_session` instead), print the plan, and make the query shape match the index: this check is the acceptance criterion of spec B3 and must pass before moving on.

- [ ] **Step 6: Lint**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check src/twicc/core/services/timeline_backfill.py tests/test_timeline_backfill.py tests/test_timeline_backfill_index.py`
Expected: no `F` or `B` violation (style-only codes such as C408 are not fixed: the project has no lint baseline).

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/services/timeline_backfill.py tests/test_timeline_backfill.py tests/test_timeline_backfill_index.py
git commit   # subject: feat(timeline): backfill one candidate session per lock
```

---

### Task 3: The runner with the end-of-run retry

**Files:**
- Modify: `src/twicc/core/services/timeline_backfill.py` (`run_timeline_backfill` and new helpers)
- Modify: `tests/test_timeline_backfill.py` (append tests)

**Interfaces:**
- Consumes: `candidate_ids`, `backfill_session_sync`, `BackfillStatus`, `SessionOutcome` (Task 2).
- Produces: `RETRY_PAUSES: tuple[float, ...]` (module constant, seconds, outside the lock); `_writer_is_stopping(exc) -> bool`; the final `async run_timeline_backfill() -> None` (signature unchanged: `cli/run.py` calls it through `_guarded_timeline_backfill`).

- [ ] **Step 1: Append the failing tests**

```python
# appended to tests/test_timeline_backfill.py
@pytest.fixture
def no_pauses(monkeypatch):
    monkeypatch.setattr(timeline_backfill, "RETRY_PAUSES", (0.0, 0.0, 0.0))


def fail_inserts(monkeypatch, *, times, only_session=None):
    """Make ``_insert_rows`` raise ``times`` times (None = always), then work. Returns the call list."""
    calls = []
    real = timeline_backfill._insert_rows

    def flaky(rows):
        calls.append(rows[0].session_id)
        if (only_session is None or rows[0].session_id == only_session) and (times is None or len(calls) <= times):
            raise RuntimeError("database is locked")
        real(rows)

    monkeypatch.setattr(timeline_backfill, "_insert_rows", flaky)
    return calls


def test_a_transient_failure_is_retried_in_the_same_run(project, monkeypatch, no_pauses):
    make(project, created_at=timezone.now())
    calls = fail_inserts(monkeypatch, times=2)
    run_all()
    assert kinds("s1") == ["session.created"]
    assert len(calls) == 3


def test_a_permanent_failure_is_bounded_and_the_run_ends(project, monkeypatch, no_pauses):
    make(project, created_at=timezone.now())
    calls = fail_inserts(monkeypatch, times=None)
    run_all()
    assert kinds("s1") == []
    assert len(calls) == 1 + len(timeline_backfill.RETRY_PAUSES)
    assert candidate_ids() == ["s1"]


def test_a_failing_session_does_not_stop_the_others(project, monkeypatch, no_pauses):
    now = timezone.now()
    make(project, "bad", created_at=now, mtime=10.0)
    make(project, "good", created_at=now, mtime=5.0)
    fail_inserts(monkeypatch, times=None, only_session="bad")
    run_all()
    assert kinds("good") == ["session.created"] and kinds("bad") == []


def test_the_writer_stopping_ends_the_run_without_retries(project, monkeypatch, no_pauses):
    now = timezone.now()
    make(project, "a", created_at=now, mtime=2.0)
    make(project, "b", created_at=now, mtime=1.0)
    calls = []

    def stopping(session_id):
        calls.append(session_id)
        raise RuntimeError("DB writer is stopping")

    monkeypatch.setattr(timeline_backfill, "backfill_session_sync", stopping)
    run_all()
    assert calls == ["a"]


def test_only_the_stopping_error_of_the_writer_is_recognised():
    assert timeline_backfill._writer_is_stopping(RuntimeError("DB writer is stopping"))
    assert not timeline_backfill._writer_is_stopping(RuntimeError("boom"))
    assert not timeline_backfill._writer_is_stopping(ValueError("DB writer is stopping"))


def test_a_second_run_has_no_candidate_and_takes_no_lock(project, monkeypatch):
    make(project, created_at=timezone.now())
    run_all()
    calls = []
    real = timeline_backfill.backfill_session_sync
    monkeypatch.setattr(timeline_backfill, "backfill_session_sync", lambda sid: calls.append(sid) or real(sid))
    run_all()
    assert calls == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill.py -v`
Expected: FAIL (`AttributeError: ... RETRY_PAUSES`).

- [ ] **Step 3: Replace the runner**

In `timeline_backfill.py`: add `import time` and `from collections import Counter` to the imports, then replace the whole `run_timeline_backfill` of Task 2 with:

```python
#: Pauses (seconds) before each retry round of the sessions that failed: three rounds, growing,
#: under about 10 s in total. They happen outside the lock.
RETRY_PAUSES = (1.0, 3.0, 5.0)

_WRITER_STOPPING = "DB writer is stopping"


class _WriterStopping(Exception):
    """The DB writer refuses new work: the run ends. It is not a session failure."""


def _writer_is_stopping(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and _WRITER_STOPPING in str(exc)


async def _run_pass(session_ids: list[str], stats: Counter) -> dict[str, str]:
    """One pass over ``session_ids``, one lock acquisition each. Returns ``{failed id: cause}``."""
    failed: dict[str, str] = {}
    for session_id in session_ids:
        try:
            outcome = await run_under_db_write_lock(
                lambda current=session_id: asyncio.to_thread(backfill_session_sync, current)
            )
        except Exception as exc:  # noqa: BLE001 - a failed session must never stop the run
            if _writer_is_stopping(exc):
                raise _WriterStopping from exc
            logger.warning("Timeline backfill: session %s failed: %s", session_id, exc, exc_info=True)
            failed[session_id] = repr(exc)
            continue
        stats[outcome.status] += 1
        stats["rows"] += outcome.rows
        await asyncio.sleep(0)
    return failed


async def run_timeline_backfill() -> None:
    """Process every candidate session, one lock acquisition each, newest first.

    Sessions that failed are retried after the list is exhausted, at most ``len(RETRY_PAUSES)``
    more times with a growing pause; the ones still failing are logged at error level and left
    for the next boot. ``CancelledError`` propagates (shutdown).
    """
    started = time.monotonic()
    stats: Counter = Counter()
    session_ids = await asyncio.to_thread(candidate_ids)
    failed: dict[str, str] = {}
    try:
        failed = await _run_pass(session_ids, stats)
        for pause in RETRY_PAUSES:
            if not failed:
                break
            await asyncio.sleep(pause)
            failed = await _run_pass(list(failed), stats)
    except _WriterStopping:
        logger.info("Timeline backfill stopped: the DB writer is stopping")
        return
    for session_id, cause in failed.items():
        logger.error(
            "Timeline backfill gave up on session %s after %d retries: %s", session_id, len(RETRY_PAUSES), cause,
        )
    if session_ids:
        logger.info(
            "Timeline backfill: %d session(s) written (%d row(s)), %d skipped, %d failed in %.1f s",
            stats[BackfillStatus.WRITTEN], stats["rows"], stats[BackfillStatus.SKIPPED], len(failed),
            time.monotonic() - started,
        )
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill.py tests/test_timeline_backfill_index.py tests/test_timeline_boot.py -v`
Expected: all PASS, no warning noise beyond the expected logged failures.

- [ ] **Step 5: Lint**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check src/twicc/core/services/timeline_backfill.py tests/test_timeline_backfill.py`
Expected: no `F` or `B` violation. (If your ruff version rejects the `noqa: BLE001` code as unknown, remove that comment.)

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/services/timeline_backfill.py tests/test_timeline_backfill.py
git commit   # subject: feat(timeline): retry the failed backfill sessions at the end of the run
```

---

### Task 4: Measure, then fix the insert method

**Files:**
- Create: `tests/test_timeline_backfill_bench.py`
- Modify (only if the measurement says so): `src/twicc/core/services/timeline_backfill.py` (`_insert_rows` body)

**Interfaces:**
- Consumes: `timeline_backfill.backfill_session_sync`, `run_timeline_backfill`, `_insert_rows` (Tasks 2-3).
- Produces: an opt-in benchmark and a recorded decision on the insert method (spec §10).

- [ ] **Step 1: Write the benchmark (skipped unless `TWICC_TIMELINE_BENCH=1`)**

```python
# tests/test_timeline_backfill_bench.py
"""Opt-in benchmark of the incremental backfill. Run with:
TWICC_TIMELINE_BENCH=1 TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill_bench.py -s -v
It prints timings and asserts only structural facts: it is not a pass/fail performance gate."""

import os
import time

import orjson
import pytest
from django.db import connection, transaction
from django.utils import timezone

from tests.timeline_helpers import run_with_writer
from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services import timeline_backfill

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(os.environ.get("TWICC_TIMELINE_BENCH") != "1", reason="set TWICC_TIMELINE_BENCH=1"),
]

SESSIONS = 2000


def _project():
    return Project.objects.create(id="-tl-bench", directory="/tmp/tl-bench")


def test_per_session_time_and_total_duration(monkeypatch):
    project = _project()
    now = timezone.now()
    Session.objects.bulk_create([
        Session(id=f"s{i:05d}", file_path=f"s{i:05d}.jsonl", project=project, provider="codex",
                created_at=now, last_updated_at=now, mtime=float(i), archived=(i % 5 == 0))
        for i in range(SESSIONS)
    ])
    spans = []
    real = timeline_backfill.backfill_session_sync

    def timed(session_id):
        started = time.perf_counter()
        try:
            return real(session_id)
        finally:
            spans.append(time.perf_counter() - started)

    monkeypatch.setattr(timeline_backfill, "backfill_session_sync", timed)
    begin = time.perf_counter()
    run_with_writer(timeline_backfill.run_timeline_backfill)
    first = time.perf_counter() - begin
    millis = sorted(span * 1000 for span in spans)
    print(f"\nfirst run: {len(millis)} sessions in {first:.2f}s; per session ms: "
          f"median {millis[len(millis) // 2]:.2f}, p95 {millis[int(len(millis) * 0.95)]:.2f}, max {millis[-1]:.2f}")
    assert len(millis) == SESSIONS
    spans.clear()
    begin = time.perf_counter()
    run_with_writer(timeline_backfill.run_timeline_backfill)
    print(f"following run: {time.perf_counter() - begin:.3f}s, sessions processed: {len(spans)}")
    assert not spans


def _rows(project_id, session_id):
    now = timezone.now()
    return [
        TimelineEvent(session_id=session_id, project_id=project_id, kind=kind, occurred_at=now,
                      precision="derived", actor="system", data={}, dedupe_key=f"backfill:{kind}")
        for kind in ("session.created", "archived.started", "hidden.started")
    ]


def _per_row(rows):
    for row in rows:
        row.save(force_insert=True)


def _bulk(rows):
    TimelineEvent.objects.bulk_create(rows)


def _executemany(rows):
    ops = connection.ops
    params = [
        (r.session_id, r.project_id, r.kind, ops.adapt_datetimefield_value(r.occurred_at),
         ops.adapt_datetimefield_value(timezone.now()), r.precision, r.actor, "", None,
         orjson.dumps(r.data).decode(), r.dedupe_key)
        for r in rows
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            'INSERT INTO "core_timelineevent" ("session_id","project_id","kind","occurred_at","recorded_at",'
            '"precision","actor","reason","caused_by_id","data","dedupe_key") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            params,
        )


def test_insert_variants_one_transaction_per_session():
    project = _project()
    timings = {}
    for name, insert in (("per_row", _per_row), ("bulk_create", _bulk), ("executemany", _executemany)):
        TimelineEvent.objects.all().delete()
        begin = time.perf_counter()
        for i in range(SESSIONS):
            with transaction.atomic():
                insert(_rows(project.id, f"{name}-{i}"))
        timings[name] = time.perf_counter() - begin
        assert TimelineEvent.objects.count() == SESSIONS * 3
    print("\ninsert variants, total seconds for", SESSIONS, "sessions x 3 rows:", {k: round(v, 3) for k, v in timings.items()})
```

- [ ] **Step 2: Run the benchmark and read the numbers**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_TIMELINE_BENCH=1 TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill_bench.py -s -v`
Expected: both tests PASS and print the timings. Without the environment variable the file is skipped: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill_bench.py -v` shows 2 skipped.

- [ ] **Step 3: Decide the insert method**

Rule: keep `bulk_create` (the current `_insert_rows` body) **unless** `per_row` or `executemany` is at least 20 % faster than `bulk_create` in total. If one is, replace the body of `_insert_rows` with that variant (for `executemany`, move the `_executemany` function body into `_insert_rows` and add the imports it needs to `timeline_backfill.py`: `import orjson`, `from django.db import connection` (next to the existing `transaction` import) and `from django.utils import timezone`; the body calls `timezone.now()` for `recorded_at`), keeping the all-or-nothing behaviour (errors propagate) and the validated rows built by `_row`. Then rerun `tests/test_timeline_backfill.py` (Tasks 2-3 tests, including the rollback test, which patches `_insert_rows`) and `uvx ruff check src/twicc/core/services/timeline_backfill.py`, which must report no `F` or `B` violation (a missing import would otherwise show up as a `NameError` that the retry logic treats as a session failure). Whatever you decide, write the measured numbers and the decision in the commit body. Also record in the commit body the "first run" and "following run" timings and the per-session median/p95/max, which answer spec §10 (target, not a contract: no single acquisition above about 50 ms).

- [ ] **Step 4: Run the tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_backfill.py tests/test_timeline_backfill_bench.py -v`
Expected: PASS (the benchmark file is skipped).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add tests/test_timeline_backfill_bench.py src/twicc/core/services/timeline_backfill.py
git commit   # subject: test(timeline): benchmark the incremental backfill and fix the insert method
```

(If `_insert_rows` was not changed, `src/.../timeline_backfill.py` is simply not staged: adapt the `git add` list to the files that changed.)

---

### Task 5: Documentation and verification

**Files:**
- Modify: `CLAUDE.md` (the `TimelineEvent` bullet in "Database Models")
- Modify: `AGENTS.md` (the mirrored bullet)

- [ ] **Step 1: Update the bullets**

In `CLAUDE.md`, in the `TimelineEvent` bullet, replace the clause `the backfill (`timeline_backfill.py`) writes only `session.created` and flag states, never process rows` with `the backfill (`timeline_backfill.py`) lists the sessions without a `session.created` row once (newest `mtime` first) and handles them one per lock acquisition, writing only `session.created` and flag states, never process rows`. Run `grep -n "backfill" AGENTS.md` and apply the same change, in AGENTS.md's terser style, to its mirrored bullet. Keep the rest of both bullets as is.

- [ ] **Step 2: Full verification**

Run, from the worktree:
- `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests -k "timeline" -v` then the whole suite `TWICC_DATA_DIR=$PWD uv run pytest -q --no-header -p no:cacheprovider` (about five minutes: run it as a background command and poll). `tests/test_migration_process.py` has timing-sensitive tests: re-run that file alone before counting a failure there as a regression.
- `uvx ruff check src/twicc/core/services/timeline_backfill.py tests/test_timeline_backfill.py tests/test_timeline_backfill_index.py tests/test_timeline_backfill_bench.py` (no `F`/`B` violation).
- `TWICC_DATA_DIR=$(mktemp -d) uv run python -m django makemigrations --check --dry-run --settings=twicc.settings_test` prints `No changes detected`.
Expected: all green. A failure in an existing test is a regression: fix the code, not the test, unless the cause is a test that depended on an unspecified row order (as in Task 1, Step 5), in which case make the test's query ordered.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add CLAUDE.md AGENTS.md
git commit   # subject: docs(timeline): describe the incremental backfill
```

- [ ] **Step 4: Report to the user**

State, without running them: the user must `migrate` their instance (new migration `0157_timeline_event_session_kind_index`, in addition to `0156_timeline_event`) and restart via `devctl.py`. Do not run either.

---

## Self-Review

**Spec coverage**

| Spec item | Task |
|---|---|
| B1 marker is the `session.created` row; B2 candidate definition | 2 |
| B3 composite index, plan verified by `EXPLAIN QUERY PLAN` | 1, 2 |
| B4 one id list, once, outside the lock, `mtime` desc then id desc | 2 |
| B5/B6 one session per lock, everything inside | 2, 3 |
| B7 first run and following runs share one code path | 2, 3 |
| B8 and §5 retry: failed sessions, 3 rounds, pauses outside the lock, error log | 3 |
| §5 all-or-nothing, own non-swallowing insert path, dedupe hit counts as written | 2 |
| §5 refusal of the lock because the writer is stopping ends the run | 3 |
| §7 what is written is unchanged | 2 |
| §8 following runs cost one seek per session and take no lock when empty | 2, 3 |
| §10 measurement and insert method | 4 |
| §13 migration placement, insert method, log output | 1 (new migration), 4, 3 |

**Placeholder scan:** none; the only decision left to execution time is the insert method, with its measured rule in Task 4 Step 3.

**Type consistency:** `candidate_ids`, `backfill_session_sync`, `BackfillStatus`, `SessionOutcome`, `_insert_rows`, `RETRY_PAUSES`, `_writer_is_stopping` are defined once (Tasks 2-3) and used with the same names in the tests.
