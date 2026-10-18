# Timeline Creation Rows From the Background Compute — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The creation rows of the sessions that TwiCC did not see being created (`session.created` + initial flag rows) are written by the background compute, with the `created_at` it has just computed, progressively, root by root. The boot backfill task and the initial-sync hook disappear.

**Architecture:** `core/services/timeline_backfill.py` loses its boot loop and keeps only the row building, behind two never-raising entry points: `write_creation_rows_sync` (called inside the compute's final per-session transaction, after its title step) and `write_unavailable_root_rows_sync` (called by the Codex "mark unavailable" apply, for the root of the marked session). `record_title_change` stops writing `auto`/`agent` titles for a session that has no `session.created` row. The compute versions go to 117 / 58 so every existing session is computed again.

**Tech Stack:** Python 3.13, Django 6 ORM (savepoints, `transaction.on_commit`), SQLite, pytest + pytest-django. No frontend change. No migration. No new dependency.

**Spec:** `docs/plans/2026-10-18-timeline-compute-integration-design.md` (reviewed PASS over 5 rounds; decisions C1..C9 are binding). Earlier designs it changes, not to be edited: `docs/plans/2026-10-13-session-timeline-design.md`, `docs/plans/2026-10-14-timeline-incremental-backfill-design.md`, `docs/plans/2026-10-15-timeline-display-design.md`. Do not commit `docs/plans/2026-10-14-session-timeline-handoff.md` (untracked on purpose).

---

## Working conventions (every task)

- **Worktree.** All work happens in `/home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline` (branch `feature/session-timeline`). Prefix EVERY Bash command with `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && `. Never `cd` to the main repo `/home/twidi/dev/twicc-poc`.
- **Backend tests.** `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest <paths> -q`. Never add `TWICC_DATA_DIR=` (the test settings ignore it), never plain `pytest`, never `uv run --active`, never `uv pip`.
- **Lint.** `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check <changed .py files>`. Zero findings on the lines you write, including `C408` (use `{}` / `[]` literals) and `SIM117` (merge nested `with`). When you delete code, also delete the imports it leaves unused (`F401`), even on a line you did not otherwise touch.
- **Forbidden.** No `migrate`, no server start or restart (`devctl.py`), no `npm install`/`npm ci`, no CHANGELOG edit, no `git stash` (use explicit WIP commits if needed), no `git add .` or a directory: list every file.
- **Commits.** One commit per task, Conventional Commit subject (`feat(timeline): ...`, `test(timeline): ...`, `docs(timeline): ...`), a body that explains the change, and the trailer `Co-Authored-By: Claude <exact model name you run as> <noreply@anthropic.com>`.
- **Style.** Match the surrounding code: comment density, naming, `NamedTuple` over dataclasses, lazy imports where the neighbours use them. English everywhere.

## Global Constraints

- Compute versions: `CLAUDE_CODE_COMPUTE_VERSION = 117`, `CODEX_COMPUTE_VERSION = 58` (spec C2).
- The rows written are exactly those of the spec §5.2: `session.created` dated `created_at`, `precision=derived`, `origin=sync`, `provider`, `spawned_by` from the column, `settings` null, actor `system`; flag rows `<flag>.started` dated `last_updated_at` (fallback `created_at`), `precision=estimated`, `scope` for `pinned`; dedupe keys `backfill:<kind>`. The existing `_row` / `_build_rows` already produce this: reuse them unchanged.
- Nothing is written for a session that already has a `session.created` row ("known", C6), for a subagent, or for a session whose `created_at` is empty.
- The compute step and the C9 step never raise; a failure is logged with `logger.exception` and only the timeline rows are discarded (savepoint).
- The signal is requested only through `timeline_signal.poke_on_commit()`, only when rows were written (C8).
- `auto` / `agent` `title.changed` is written only for a known session; `user` is always written (C7).

## Review Focus

1. **A recompute of a known session** (every session of the owner's instance after the bump has a row only once the first pass ran; a second bump finds the row): the compute must write nothing and request no signal. Pinned in Task 2 (`test_a_known_session_gets_nothing_and_no_signal`).
2. **A tree with subagents**: only the root gets rows, once, when its own final apply succeeds; a subagent's final apply writes nothing even though it is published first. Pinned in Task 2 (`test_only_the_root_of_a_tree_gets_rows`).
3. **A failure after the timeline step** (any later step of the final transaction raises): the timeline rows roll back with the compute and no signal fires, so the retry writes them once. Pinned in Task 2 (`test_a_rolled_back_publication_leaves_no_row_and_no_signal`).
4. **A watcher-created Codex session later marked unavailable**: it is known (watcher row), so the C9 step must add nothing. Pinned in Task 3 (`test_a_known_root_gets_nothing`).
5. **Title order inside the first compute**: the title step runs before the creation step in the same transaction, so a historical session never gets a `title.changed` from its first compute, but a second compute that changes the title records it. Pinned in Task 4 (`test_the_first_compute_records_no_title_and_a_later_change_is_recorded`).

---

### Task 1: Creation-row engine, boot backfill removed

Replace the boot loop of `timeline_backfill.py` with the never-raising entry point used by the compute, and remove the task that started it.

**Files:**
- Modify: `src/twicc/core/services/timeline_backfill.py` (whole module)
- Modify: `src/twicc/cli/run.py:130-138` (delete `_guarded_timeline_backfill`), `:305-308` (keep `start_timeline_signal(...)`, delete the task creation and fix the comment), `:445` and `:571-572` (delete the two cancels)
- Modify: `tests/test_timeline_backfill.py`, `tests/test_timeline_backfill_index.py`, `tests/test_timeline_signal.py`, `tests/test_timeline_provider.py`, `tests/test_timeline_origin_columns.py`
- Delete: `tests/test_timeline_backfill_bench.py`
- Modify: `tests/timeline_helpers.py` (add one helper)
- Keep unchanged: `tests/test_timeline_enums_sync.py` (it reads `_FLAGS` and `_RELEVANT_KINDS`, which stay)

**Interfaces:**
- Produces: `write_creation_rows_sync(session_id: str, *, require_computed: bool = False) -> int` in `twicc.core.services.timeline_backfill`. Returns the number of rows inserted (0 when skipped or on failure). Never raises. Sync; the caller already holds the DB write lock (compute final transaction, DB writer job).
- Produces (tests): `log_records_of(logger: logging.Logger)` in `tests/timeline_helpers.py`: a context manager that re-enables `logger`, attaches a collecting handler, yields the list of `LogRecord`s, then restores both. Tasks 2 and 3 use it.
- Produces (tests): `mark_known(session_id: str, project_id: str | None = None) -> None` in `tests/timeline_helpers.py`: inserts one watcher-shaped `session.created` row (no `dedupe_key`, actor `system`, `occurred_at=timezone.now()`, `provider` read from the `Session` row (`Session.objects.filter(id=session_id).values_list("provider", flat=True).first()`, so the provider guard of `test_timeline_provider.py` stays green), `data=validate_data(TimelineKind.SESSION_CREATED, {"origin": "external", "provider": <that provider>, "settings": None, "spawned_by": None})`) with `TimelineEvent.objects.create(...)`. Tasks 2, 3 and 4 use it to make a session "known".
- Keeps (unchanged, used by Task 3 and the enum tests): `_FLAGS`, `_RELEVANT_KINDS`, `_subject_prefix`, `_row`, `_build_rows`.
- Removes: `BackfillStatus`, `SessionOutcome`, `candidate_ids`, `_insert_rows`, `backfill_session_sync`, `RETRY_PAUSES`, `_WRITER_STOPPING`, `_WRITER_NOT_STARTED`, `_WriterStopping`, `_writer_is_stopping`, `_writer_is_not_started`, `_run_pass`, `run_timeline_backfill`. The module no longer imports `twicc.providers.db_writer` (so `compute_base` can import it lazily without a cycle).

- [ ] **Step 1: Rewrite `tests/test_timeline_backfill.py` for the new entry point**

Keep the file name (it tests the same row-building module). Keep every test from `_parity_rows` (line ~291) to the end that exercises `insert_event_rows` / `_conflict_clause` directly (raw insert parity, SQL from metadata, conflict clause, duplicate keys, NULL keys, keyed row without session, other constraint violation), with one change: `test_raw_insert_stores_exactly_what_the_orm_stores` (~310) and `test_raw_insert_sql_is_built_from_the_model_metadata` (~326) call `timeline_backfill._insert_rows`, which is removed: call `insert_event_rows` (already imported) instead. **Port** `test_a_rerun_after_deleting_the_creation_row_rewrites_only_that_row` (~393, it calls `backfill_session_sync`): `write() == 2`, delete the creation row, `write() == 1`, two rows in total, `write() == 0`. Delete the two last tests that go through the boot run (`test_conflicting_rows_reach_the_backfill_insert_...`, `test_a_non_conflict_error_rolls_the_backfill_session_back_as_a_whole`), `test_running_twice_never_duplicates` (~79, replaced by `test_a_known_session_gets_nothing` below) and every test of the run loop, candidates, retries and writer state (lines ~94-290), with two precisions for that range: `test_the_statements_per_session_do_not_depend_on_its_timeline_size` (~149) is kept, renamed and ported (last test of the block below); `test_an_already_present_dedupe_key_counts_as_written_not_as_a_failure` (~139) is dropped on purpose (the kept raw-insert tests pin the `ON CONFLICT` behaviour, and the "known" check returns before any insert). **Move** the `log_records` fixture (~163-178: a handler on the module logger that also re-enables it, because `settings_test.py:103` sets `disable_existing_loggers` and another test may have applied that config) to `tests/timeline_helpers.py` as `log_records_of(logger)`, a context manager yielding the list of records; use it, never `caplog`, in every "error is logged" assertion of this plan (Tasks 1, 2, 3). Replace `run_all()` and the row-shape tests with calls to the new function:

```python
from twicc.core.services.timeline_backfill import write_creation_rows_sync


def write(sid="s1", **kw):
    return write_creation_rows_sync(sid, **kw)


def test_creation_row_is_derived_from_created_at(project):
    created = timezone.now() - timedelta(days=10)
    make(project, created_at=created)
    assert write() == 1
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "session.created" and row.occurred_at == created
    assert row.precision == "derived" and row.actor == "system" and row.project_id == project.id
    assert row.dedupe_key == "backfill:session.created"
    assert row.data == {"origin": "sync", "provider": "codex", "settings": None, "spawned_by": None}
    assert (row.via, row.actor_ref, row.actor_name, row.provider) == (None, None, None, "codex")
```

Port the existing shape tests the same way (spawner, flag dates `last_updated_at` then `created_at`, pinned scope, no process rows, a live flag row suppresses only its subject, a deleted creation row is rewritten alone), each calling `write(...)` instead of `run_all()`. Add:

```python
def test_a_known_session_gets_nothing(project):
    make(project, created_at=timezone.now())
    assert write() == 1
    assert write() == 0
    assert kinds("s1") == ["session.created"]


def test_a_subagent_a_missing_session_or_an_empty_created_at_gets_nothing(project):
    make(project, "boss", created_at=timezone.now())
    make(project, "kid", created_at=timezone.now(), type=SessionType.SUBAGENT, parent_session_id="boss")
    make(project, "blank")
    assert write("kid") == 0 and write("blank") == 0 and write("nope") == 0
    assert TimelineEvent.objects.count() == 0


def test_require_computed_skips_a_session_with_an_empty_compute_version(project):
    make(project, "fresh", created_at=timezone.now())
    make(project, "done", created_at=timezone.now(), compute_version=3)
    assert write("fresh", require_computed=True) == 0
    assert write("done", require_computed=True) == 1


def test_a_failure_is_logged_never_raised_and_leaves_no_partial_row(project, monkeypatch):
    make(project, created_at=timezone.now(), archived=True)
    real = timeline_backfill.insert_event_rows

    def insert_then_fail(rows):
        real(rows[:1])
        raise RuntimeError("disk full")

    monkeypatch.setattr(timeline_backfill, "insert_event_rows", insert_then_fail)
    with log_records_of(timeline_backfill.logger) as records, transaction.atomic():  # the compute's transaction
        assert write() == 0
        Session.objects.filter(id="s1").update(title="still writable")  # the outer transaction is usable
    assert TimelineEvent.objects.count() == 0
    assert Session.objects.get(id="s1").title == "still writable"
    assert any("disk full" in str(r.exc_info[1]) for r in records if r.exc_info)


def test_the_statements_do_not_depend_on_the_timeline_size(project):
    # keep the existing body of this test (CaptureQueriesContext around one session with and without
    # many unrelated timeline rows), calling write() instead of backfill_session_sync().
```

Imports at the top: drop `asyncio`, `logging`, `BackfillStatus`, `backfill_session_sync`, `candidate_ids`, `run_timeline_backfill`, `run_with_writer` when no test uses them any more; keep `kinds`, `transaction`, `timeline_backfill`; add `from tests.timeline_helpers import kinds, log_records_of, mark_known`.

- [ ] **Step 2: Adapt the other test files**

- `tests/test_timeline_backfill_index.py`: keep the first test. Replace the second by `test_the_known_check_is_served_by_the_session_kind_index`: build `TimelineEvent.objects.filter(session_id="s1", kind__in=timeline_backfill._RELEVANT_KINDS).values_list("kind", flat=True).distinct()` (the query the engine runs) and assert `"idx_timeline_session_kind"` is in its `EXPLAIN QUERY PLAN`.
- `tests/test_timeline_signal.py`: remove the `run_timeline_backfill` import and the two backfill tests (~171-186), and the imports they leave unused (`from datetime import timedelta`, `run_with_writer`; ruff F401 tells). Add:

```python
def test_the_creation_rows_request_one_signal_after_commit_and_none_when_nothing_is_written(project, pokes):
    from twicc.core.services.timeline_backfill import write_creation_rows_sync

    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex",
                           created_at=timezone.now(), archived=True)
    with transaction.atomic():
        assert write_creation_rows_sync("a") == 2
        assert pokes == []
    assert pokes == [1]
    assert write_creation_rows_sync("a") == 0
    assert pokes == [1]


def test_a_rolled_back_transaction_requests_no_signal(project, pokes):
    from twicc.core.services.timeline_backfill import write_creation_rows_sync

    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex", created_at=timezone.now())
    with pytest.raises(RuntimeError), transaction.atomic():
        write_creation_rows_sync("a")
        raise RuntimeError("compute failed later")
    assert pokes == [] and TimelineEvent.objects.count() == 0
```

  Rewrite the wiring test (`test_the_server_source_starts_the_signal_before_the_backfill_and_stops_it_at_shutdown`) as `test_the_server_source_starts_the_signal_before_the_orchestrators_and_stops_it_at_shutdown`: `start_timeline_signal(asyncio.get_running_loop())` appears before `await orchestrators.start_all(`; in the normal shutdown, `stop_timeline_signal()` appears after `await _cancel_task(title_auto_task, "Automatic title task")` and before `"Stop cross-provider tasks first"`; the outer-`finally` assertion stays as it is; and the source no longer contains `timeline_backfill` nor `_guarded_timeline_backfill`.
- `tests/test_timeline_provider.py`: in `test_the_backfill_writes_the_session_provider` (~356) replace `run_with_writer(run_timeline_backfill)` with `write_creation_rows_sync("a")` and `write_creation_rows_sync("b")`; in `test_guard_no_row_with_a_session_keeps_a_null_provider_after_any_writer_ran` (~366) replace it with `write_creation_rows_sync("backfilled")` (the `s1` session already has rows from the other writers, so the creation writer must target the session it was created for), and add `mark_known("s1", project.id)` right after `make_session(project, created_at=...)` so that the `record_title_change(..., source="auto")` call still writes its row after Task 4. Fix the imports; the assertions stay.
- `tests/test_timeline_origin_columns.py` (~193-197): replace `run_with_writer(run_timeline_backfill)` with one `write_creation_rows_sync(<id>)` call per session the test creates; fix the imports; the assertions stay.
- `tests/test_timeline_backfill_bench.py` is deleted by the `git rm` of Step 6 (it measures the removed boot loop; the compute step is measured by the hold diagnostics, Task 2). Do not remove it earlier.
- `tests/timeline_helpers.py`: add `mark_known` (Interfaces above), and in `tests/test_timeline_backfill.py` add `test_a_watcher_row_makes_the_session_known`: `make(project, created_at=timezone.now())`, `mark_known("s1", project.id)`, `write() == 0`, `kinds("s1") == ["session.created"]`.

- [ ] **Step 3: Run the tests to see them fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_backfill.py tests/test_timeline_backfill_index.py tests/test_timeline_signal.py tests/test_timeline_provider.py tests/test_timeline_origin_columns.py -q`
Expected: FAIL at import (`cannot import name 'write_creation_rows_sync'`).

- [ ] **Step 4: Implement `write_creation_rows_sync` and remove the loop**

In `timeline_backfill.py`: new module docstring (the rows of the sessions TwiCC did not see being created, written by the compute's final transaction and by the Codex unavailable marking; spec 2026-10-18). Body of `write_creation_rows_sync`: `try:` + `with transaction.atomic():` (a savepoint inside the caller's transaction); read the row filtered on `id`, `type=SessionType.SESSION`, `created_at__isnull=False`, plus `compute_version__isnull=False` when `require_computed`; return 0 if missing; read the distinct relevant kinds (the existing query of `backfill_session_sync`); return 0 if `session.created` is among them; `count = insert_event_rows(_build_rows(session, existing))`; after the savepoint, `timeline_signal.poke_on_commit()` when `count`; `except Exception: logger.exception("Timeline creation rows could not be written for session %s", session_id); return 0`. Delete everything listed under "Removes".

In `cli/run.py`: delete `_guarded_timeline_backfill`, the task creation and both cancels; keep `start_timeline_signal(asyncio.get_running_loop())` where it is, with the comment `# The signal needs the loop before any writer (compute, watcher, hooks) can request one.`

- [ ] **Step 5: Run the tests to see them pass, and lint**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_backfill.py tests/test_timeline_backfill_index.py tests/test_timeline_signal.py tests/test_timeline_provider.py tests/test_timeline_origin_columns.py tests/test_timeline_enums_sync.py -q && uvx ruff check src/twicc/core/services/timeline_backfill.py src/twicc/cli/run.py tests/timeline_helpers.py tests/test_timeline_backfill.py tests/test_timeline_backfill_index.py tests/test_timeline_signal.py tests/test_timeline_provider.py tests/test_timeline_origin_columns.py`
Expected: all PASS, `All checks passed!`. Then `grep -rn "run_timeline_backfill\|backfill_session_sync\|candidate_ids\|_guarded_timeline_backfill" src tests` prints nothing except three kinds of expected hits: `candidate_ids` in `workflow_synthesis.py`, `codex/background_compute.py` and `background_compute_task.py` (unrelated local names); the wiring test of `tests/test_timeline_signal.py` that asserts `_guarded_timeline_backfill` is absent from `run.py`; and `tests/test_timeline_backfill_bench.py` (still present at this step, deleted by Step 6).

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/services/timeline_backfill.py src/twicc/cli/run.py tests/timeline_helpers.py tests/test_timeline_backfill.py tests/test_timeline_backfill_index.py tests/test_timeline_signal.py tests/test_timeline_provider.py tests/test_timeline_origin_columns.py && git rm -q tests/test_timeline_backfill_bench.py && git commit
```
Subject: `feat(timeline): replace the boot backfill with a never-raising creation-row writer`.

---

### Task 2: The compute writes the creation rows; initial-sync hook removed; versions 117 / 58

**Files:**
- Modify: `src/twicc/providers/compute_base.py` — `_apply_session_complete_final`, right after the `with timed_sync_phase('compute_final_titles'):` block (~4203-4213) and before `compute_final_project_directory`
- Modify: `src/twicc/providers/db_writer.py:3069` (drop the `record_session_created` import) and `:3095-3097` (drop the hook and its comment)
- Modify: `src/twicc/settings.py:395-396`
- Create: `tests/test_timeline_compute_creation.py`
- Modify: `tests/test_timeline_session_created.py` (`test_initial_sync_payload_records_origin_sync`, ~83), `tests/test_content_upgrade.py:27-28`

**Interfaces:**
- Consumes: `write_creation_rows_sync(session_id, *, require_computed=False) -> int` (Task 1).
- Produces: nothing new for later tasks. Task 4 relies on the ORDER fixed here: the title step runs before the creation step in the same final transaction.

- [ ] **Step 1: Write the failing tests** in `tests/test_timeline_compute_creation.py`

```python
"""Creation rows written by the compute's final transaction (spec 2026-10-18, C1/C3/C8)."""

import pytest

from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services import timeline_backfill, timeline_signal
from twicc.providers.compute_base import BaseSessionCompute
from tests.timeline_helpers import kinds, log_records_of, mark_known

pytestmark = pytest.mark.django_db(transaction=True)

CREATED = "2026-01-02T03:04:05+00:00"
UPDATED = "2026-03-04T05:06:07+00:00"
PROVISIONAL = "2026-02-01T00:00:00+00:00"


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-compute", directory="/tmp/tl-compute")


@pytest.fixture
def pokes(monkeypatch):
    calls = []
    monkeypatch.setattr(timeline_signal, "poke", lambda: calls.append(1))
    return calls


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="claude_code", **kw)


def publish(sid, version=99, fields=None, **extra):
    """A minimal final publication (the shape of tests/test_root_compute_completion.py). ``fields``
    overrides session fields; ``extra`` adds message keys (``placeholder_titles``, ``provider_titles``)."""
    return BaseSessionCompute.apply_session_complete({
        "session_id": sid, "history_facts": [],
        "session_fields": {"compute_version": version, "created_at": CREATED, "last_updated_at": UPDATED,
                           **(fields or {})},
        **extra,
    })
```

(`created_at` / `last_updated_at` travel as ISO strings, as the worker sends them: `compute_base.py` ~3677.)

Tests (one per behaviour of spec §10 that this task owns):

- `test_the_first_compute_writes_the_creation_row_at_the_computed_created_at`: `make(project, created_at=PROVISIONAL, hidden=True)` (a live slice's provisional value); `publish("s1").outcome == "applied"`; the `session.created` row has `occurred_at.isoformat() == CREATED`, `precision == "derived"`, `data["origin"] == "sync"`; `hidden.started` exists, dated `UPDATED`, `precision == "estimated"`; `kinds("s1") == ["session.created", "hidden.started"]`.
- `test_a_known_session_gets_nothing_and_no_signal`: `make(project)`, `mark_known("s1", project.id)` (Task 1); `publish` → applied; still one `session.created`; `pokes == []`.
- `test_only_the_root_of_a_tree_gets_rows` (takes `pokes`): root `r` and child `c` (`type=SessionType.SUBAGENT`, `parent_session=r`); `publish("r").outcome == "superseded"`, no row, `pokes == []`; `publish("c").outcome == "applied"`, no row for `c` nor `r`, `pokes == []`; `publish("r").outcome == "applied"`, `kinds("r") == ["session.created"]`, `pokes == [1]`.
- `test_an_empty_created_at_gets_nothing`: `publish("s1", fields={"created_at": None})` → applied, `TimelineEvent.objects.count() == 0`.
- `test_a_failing_timeline_step_leaves_the_compute_applied`: monkeypatch `timeline_backfill.insert_event_rows` to raise `RuntimeError("boom")`; inside `with log_records_of(timeline_backfill.logger) as records:` run `publish(...)`; `.outcome == "applied"`; `Session.objects.get(id="s1").compute_version == 99`; no timeline row; a record carries the `boom` exception.
- `test_a_rolled_back_publication_leaves_no_row_and_no_signal` (Review Focus 3): monkeypatch `twicc.core.content_storage.extraction.finish_storage_result` (the last step, imported inside the function) to raise; `with pytest.raises(...)`: `publish("s1")`; `TimelineEvent.objects.count() == 0`; `pokes == []`; `Session.objects.get(id="s1").compute_version is None`.
- `test_the_signal_is_requested_once_per_published_root`: two roots published one after the other → `pokes == [1, 1]`.
- `test_the_step_is_timed_in_the_hold_diagnostics`: monkeypatch `twicc.providers.compute_base.timed_sync_phase` (imported at module level, line ~81) with a recorder (a `contextlib.contextmanager` taking `(name, **kwargs)` that appends `name` and yields) and assert `"compute_final_timeline"` comes right after `"compute_final_titles"` in the recorded order.

In `tests/test_timeline_session_created.py`, rename `test_initial_sync_payload_records_origin_sync` to `test_the_initial_sync_insert_writes_no_timeline_row` and assert `kinds(<sid>) == []` after the same payload apply. In `tests/test_content_upgrade.py`, `test_combined_release_targets` asserts `117` and `58` (leave the parametrized inline-upgrade tests as they are).

- [ ] **Step 2: Run the tests to see them fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_compute_creation.py tests/test_timeline_session_created.py tests/test_content_upgrade.py -q`
Expected: FAIL (no row written by the compute; the initial sync still writes one; versions 116/57).

- [ ] **Step 3: Implement**

- `compute_base.py`: after the titles block, add a `with timed_sync_phase('compute_final_timeline'):` block that, when `session.type == SessionType.SESSION`, lazily imports and calls `write_creation_rows_sync(session_id)`. Comment: the step runs after the titles so that the first compute of a historical session records no title change (spec C7), and inside the transaction so that a rolled-back publication discards its rows (spec §5.1). (`session` is the row loaded at ~3904; its `type` never changes.)
- `db_writer.py`: delete the import and the three hook lines, and drop `SessionType` from the local `from twicc.core.models import ...` line of the same function (~3064): the hook was its only user there; replace the hook comment by `# The creation row of an initial-sync session is written by its first compute (spec 2026-10-18).`
- `settings.py`: `CLAUDE_CODE_COMPUTE_VERSION = 117  # 117: timeline creation rows from the compute; 116: ...` (keep the existing history after it), same for `CODEX_COMPUTE_VERSION = 58  # 58: timeline creation rows from the compute; 57: ...`.

- [ ] **Step 4: Run the tests to see them pass, then the compute suites, and lint**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_compute_creation.py tests/test_timeline_session_created.py tests/test_content_upgrade.py tests/test_root_compute_completion.py tests/test_content_lifecycle.py tests/test_codex_recompute_persistence.py -q && uvx ruff check src/twicc/providers/compute_base.py src/twicc/providers/db_writer.py src/twicc/settings.py tests/test_timeline_compute_creation.py tests/test_timeline_session_created.py tests/test_content_upgrade.py`
Expected: all PASS; ruff reports nothing on the lines you added. `compute_base.py` and `db_writer.py` may carry older findings: compare each reported line number with `git diff -U0` and fix only yours. Also `grep -rn "CLAUDE_CODE_COMPUTE_VERSION == 116\|CODEX_COMPUTE_VERSION == 57" tests` prints nothing.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/providers/compute_base.py src/twicc/providers/db_writer.py src/twicc/settings.py tests/test_timeline_compute_creation.py tests/test_timeline_session_created.py tests/test_content_upgrade.py && git commit
```
Subject: `feat(timeline): write creation rows in the compute's final transaction`. The body names the bump (117 / 58) and its reason.

---

### Task 3: Codex sessions marked unavailable (C9)

**Files:**
- Modify: `src/twicc/core/services/timeline_backfill.py` (add one function)
- Modify: `src/twicc/providers/codex/rollout_migration.py:449-450` (`_apply_mark_session_unavailable_job`)
- Create: `tests/test_timeline_codex_unavailable.py`

**Interfaces:**
- Consumes: `write_creation_rows_sync(session_id, *, require_computed=False) -> int` (Task 1); `resolve_native_root(session_id: str) -> str` from `twicc.providers.compute_tree` (existing; raises `NativeTreeError` on a cycle and `Session.DoesNotExist` on a missing row).
- Produces: `write_unavailable_root_rows_sync(session_id: str) -> int` in `twicc.core.services.timeline_backfill`. Never raises. Resolves the native root of `session_id` (the session itself when it is a root) and calls `write_creation_rows_sync(root, require_computed=True)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_timeline_codex_unavailable.py`

```python
"""Creation rows of Codex sessions marked unavailable (spec 2026-10-18, C9 and section 5.4)."""

import asyncio

import pytest
from django.utils import timezone

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services import timeline_backfill, timeline_signal
from twicc.providers.codex.rollout_migration import MarkSessionUnavailableJob, _apply_mark_session_unavailable_job
from tests.timeline_helpers import kinds, log_records_of, mark_known

pytestmark = pytest.mark.django_db(transaction=True)


def mark(sid, reason="rollout_missing"):
    return _apply_mark_session_unavailable_job(
        MarkSessionUnavailableJob(Provider.CODEX, sid, reason, asyncio.new_event_loop().create_future()),
    )
```

Fixtures `project` (`id="-tl-unavailable"`) and `pokes` as in Task 2; `make(project, sid, **kw)` creating `provider="codex"` sessions. Tests:

- `test_a_computed_root_marked_unavailable_gets_its_rows_from_the_stored_created_at`: root with `created_at=<aware datetime>`, `compute_version=57`, `pinned="project"`; `mark("r") == 1`; `unavailable_reason == "rollout_missing"`; `kinds("r") == ["session.created", "pinned.started"]`; `session.created` dated the stored value; `pokes == [1]`.
- `test_a_subagent_marked_unavailable_gives_its_root_the_rows`: root `r` (computed, `created_at` set) and subagent `c` (`type=SessionType.SUBAGENT`, `parent_session_id="r"`); `mark("c")`; `kinds("r") == ["session.created"]`; `kinds("c") == []`.
- `test_a_root_with_an_empty_compute_version_gets_nothing`: root with `created_at` set and `compute_version=None` → `mark` → no row, `pokes == []`.
- `test_a_known_root_gets_nothing` (Review Focus 4): computed root, `mark_known("r", project.id)` → `mark("r")` → still one row, `pokes == []`.
- `test_a_second_marking_and_a_clearing_write_nothing`: computed root; `mark("r")`, `mark("r")`, `mark("r", reason=None)` → exactly the rows of the first marking; `unavailable_reason is None` at the end.
- `test_a_failure_of_the_rows_never_prevents_the_marking`: monkeypatch `timeline_backfill.insert_event_rows` to raise `RuntimeError("boom")`; inside `with log_records_of(timeline_backfill.logger) as records:` → `mark("r") == 1`; the reason is stored, no timeline row, a record carries the `boom` exception.
- `test_a_missing_or_cyclic_tree_never_raises`: `mark("ghost") == 0` (no row, no exception); and a two-session parent cycle (`a.parent_session_id = "b"`, `b.parent_session_id = "a"`, set with `Session.objects.filter(...).update(...)`) → `mark("a") == 1`, no timeline row.

- [ ] **Step 2: Run them to see them fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_codex_unavailable.py -q`
Expected: FAIL (no row written).

- [ ] **Step 3: Implement**

- `write_unavailable_root_rows_sync(session_id)`: `try:` resolve the root with a lazy `from twicc.providers.compute_tree import resolve_native_root`, then `return write_creation_rows_sync(root, require_computed=True)`; `except Exception:` log with `logger.exception` and return 0.
- `_apply_mark_session_unavailable_job`: decorate with `@transaction.atomic` (the flag and the rows commit together, spec §5.4); keep the `update(...)` and its return value; when `job.reason is not None` and the update matched a row, lazily import and call `write_unavailable_root_rows_sync(job.session_id)`. A clearing (`reason=None`) writes nothing.

- [ ] **Step 4: Run the new tests and the Codex job suites, and lint**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_codex_unavailable.py tests/test_codex_migration_jobs.py tests/test_codex_recompute_persistence.py -q && uvx ruff check src/twicc/core/services/timeline_backfill.py src/twicc/providers/codex/rollout_migration.py tests/test_timeline_codex_unavailable.py`
Expected: all PASS, no finding on your lines.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/services/timeline_backfill.py src/twicc/providers/codex/rollout_migration.py tests/test_timeline_codex_unavailable.py && git commit
```
Subject: `feat(timeline): write the creation rows of codex sessions marked unavailable`.

---

### Task 4: No automatic or agent title change for a session not yet known (C7)

**Files:**
- Modify: `src/twicc/core/services/session_timeline.py` — `record_title_change` (~425-449)
- Modify: `tests/test_timeline_title_settings.py`, `tests/test_timeline_origin_titles_creation.py`, `tests/test_timeline_service.py` (only the tests that expect an `auto`/`agent` row on a session without a `session.created` row)
- Modify: `tests/test_timeline_compute_creation.py` (one test)

**Interfaces:**
- Consumes: the order fixed in Task 2 (titles, then creation rows, in the final transaction); `mark_known(session_id, project_id=None)` from `tests/timeline_helpers.py` (Task 1).
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing tests**

In `tests/test_timeline_title_settings.py` add:

```python
def test_an_automatic_or_agent_title_of_an_unknown_session_is_not_recorded(project):
    from twicc.core.services.session_timeline import record_title_change

    make(project)
    record_title_change("s1", "Auto", source="auto")
    record_title_change("s1", "Agent", source="agent", old="Auto")
    assert kinds("s1") == []


def test_a_user_title_of_an_unknown_session_is_recorded(project):
    from twicc.core.services.session_timeline import record_title_change

    make(project)
    record_title_change("s1", "Mine", source="user", old=None)
    assert kinds("s1") == ["title.changed"]


def test_the_codex_import_records_nothing_for_an_unknown_session(project):
    from twicc.providers.codex.titles import SyncSessionTitlesJob, _apply_sync_session_titles_job

    make(project, title=None)
    _apply_sync_session_titles_job(SyncSessionTitlesJob({"s1": "Imported"}, None))
    assert Session.objects.get(id="s1").title == "Imported"
    assert kinds("s1") == []


def test_live_slice_titles_of_an_unknown_session_are_not_recorded(project):
    """The live slice applies its titles through these two writers (compute_base ~4722)."""
    from twicc.providers.compute_base import BaseSessionCompute

    make(project)
    assert BaseSessionCompute.apply_placeholder_title("s1", "First words")
    assert BaseSessionCompute._write_provider_title("s1", "From provider")
    assert Session.objects.get(id="s1").title == "From provider"
    assert kinds("s1") == []


def test_the_automatic_title_task_records_nothing_for_an_unknown_session(env, session):  # noqa: F811
    """Same call as test_automatic_title_check_records_source_auto, without mark_known."""
    from asgiref.sync import async_to_sync

    from tests.test_title_automation import NOW
    from twicc.title_cadence import TitleCheckState

    async_to_sync(env.module._apply_check)(
        session, TitleCheckState(7, NOW), title="New title", now=NOW, closing=False, succeeded=True)
    assert Session.objects.get(id=session.id).title == "New title"
    assert not TimelineEvent.objects.filter(session_id=session.id, kind="title.changed").exists()
```

In `tests/test_timeline_compute_creation.py` add (Review Focus 5):

```python
def test_the_first_compute_records_no_title_and_a_later_change_is_recorded(project):
    make(project)
    assert publish("s1", placeholder_titles={"s1": "First words"}).outcome == "applied"
    assert kinds("s1") == ["session.created"]
    assert publish("s1", version=100, provider_titles={"s1": "Renamed"}).outcome == "applied"
    assert kinds("s1") == ["session.created", "title.changed"]
    assert TimelineEvent.objects.get(kind="title.changed").data == {
        "source": "agent", "new": "Renamed", "old": "First words",
    }
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_title_settings.py tests/test_timeline_compute_creation.py -q`
Expected: the new tests FAIL (a `title.changed` row is written for the unknown session), except `test_a_user_title_of_an_unknown_session_is_recorded`, which already passes: it pins the unchanged `user` behaviour.

- [ ] **Step 3: Implement the rule in `record_title_change`**

Inside the existing `try`, after the subagent check: when `source != "user"` and no `TimelineEvent` row with this `session_id` and `kind=TimelineKind.SESSION_CREATED` exists (`.exists()`, served by `idx_timeline_session_kind`), return without writing. Update the docstring: an `auto`/`agent` title of a session not yet known to the timeline is its initial title, not a change (spec 2026-10-18, C7).

- [ ] **Step 4: Fix the existing tests that relied on the old behaviour**

Run the whole timeline and title suites to find them: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_*.py tests/test_title_*.py -q`. Known ones (`test_timeline_provider.py`'s guard test is already handled by Task 1): `test_placeholder_title_records_source_auto`, `test_provider_title_overwrite_records_source_agent`, `test_automatic_title_check_records_source_auto`, `test_codex_title_import_records_auto_for_a_null_title_and_agent_otherwise` (`test_timeline_title_settings.py`), the parametrized `auto`/`agent` test at ~92 of `test_timeline_origin_titles_creation.py`, and the test around line 214 of `test_timeline_service.py`. Fix each by calling `mark_known(<session id>, <project id>)` right after the session is created, by adapting any `TimelineEvent.objects.get(session_id=...)` that now sees two rows to filter on `kind="title.changed"`, and by prefixing any expected `kinds(...)` list of a marked session with `"session.created"` (e.g. `kinds("p") == ["session.created", "title.changed"]` in `test_timeline_service.py`). Never weaken an assertion: the title row's data and columns stay asserted as before.

- [ ] **Step 5: Run the suites and lint**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest tests/test_timeline_*.py tests/test_title_automation.py -q && uvx ruff check src/twicc/core/services/session_timeline.py tests/timeline_helpers.py tests/test_timeline_title_settings.py tests/test_timeline_origin_titles_creation.py tests/test_timeline_service.py tests/test_timeline_compute_creation.py`
Expected: all PASS, no finding on your lines.

- [ ] **Step 6: Commit**

List every test file you changed explicitly in `git add`, then `git commit`. Subject: `feat(timeline): record automatic titles only for sessions the timeline knows`.

---

### Task 5: Documentation and full verification

**Files:**
- Modify: `CLAUDE.md` and `AGENTS.md` (the `TimelineEvent` paragraph of each)
- Modify: `src/twicc/providers/sessions_watcher.py:730-735` (comment only)
- Modify: `src/twicc/core/services/session_timeline.py` — `record_session_created` docstring (~378-392)

- [ ] **Step 1: Update the two `TimelineEvent` paragraphs**

In `CLAUDE.md`, replace the clause `the backfill (`timeline_backfill.py`) lists the sessions without a `session.created` row once (newest `mtime` first) and handles them one per lock acquisition, writing only `session.created` and flag states, never process rows.` with:

`the creation rows of the sessions TwiCC did not see being created (`session.created` + flag states, never process rows; `timeline_backfill.py`) are written by the background compute in each root's final transaction, after its title step, from the `created_at` it has just computed, and by the Codex "mark unavailable" apply for the root of the marked session; the watcher hook writes the creation row of a session born while TwiCC runs. An `auto`/`agent` `title.changed` is written only for a session that has a `session.created` row.`

and replace `requested after commit by `record_event_sync` and the bulk path, one per backfill run)` with `requested after commit by every writer, the compute's creation step included)`.

In `AGENTS.md`, replace the clause `the backfill (`timeline_backfill.py`) lists the sessions without a `session.created` row once (newest `mtime` first) and handles them one per lock acquisition, writing only `session.created` and flag states.` with the same new text as in `CLAUDE.md` (AGENTS.md has no signal clause to change; check with `grep -n "backfill run" AGENTS.md`). Keep both paragraphs' surrounding text and design links; add `docs/plans/2026-10-18-timeline-compute-integration-design.md` to the list of designs at the end of each paragraph.

- [ ] **Step 2: Comments and docstrings**

- `sessions_watcher.py`, above `record_session_created(...)`: `# The only live writer of a creation row: sessions found by an initial sync get theirs from the compute.` (keep the existing "The creating request travels..." line).
- `record_session_created` docstring: remove the mention of the `origin="sync"` caller if any; state that it is called for rows created by the watcher's creation function only.

- [ ] **Step 3: Full verification**

Run, one after the other:
- `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uv run pytest -q -x` (the whole backend suite). Expected: all pass (the count is the rebased baseline 11714 minus the deleted backfill tests plus the new ones).
- `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check $(git diff --name-only --diff-filter=d 2304bdb53 -- '*.py')`. Expected: no finding on lines this plan changed.
- `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && grep -rn "timeline_backfill_task\|run_timeline_backfill\|one per backfill run" src tests CLAUDE.md AGENTS.md` prints nothing.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add CLAUDE.md AGENTS.md src/twicc/providers/sessions_watcher.py src/twicc/core/services/session_timeline.py && git commit
```
Subject: `docs(timeline): describe the creation rows written by the compute`.

Report to the owner at the end (do not do it): no migration is added; a restart via `devctl.py` triggers a full recompute (versions 117 / 58), during which the creation rows appear root by root.
