# Shared-Queue DB Writer Implementation Plan

> ⚠️ **HISTORICAL — OUT OF DATE.** Do not read the plan below as a description
> of the current code.
>
> This is the *implementation plan* as written on **2026-05-20**, before any
> code existed. The feature was built from it and then **hardened through 16
> rounds of review-driven fixes (57 commits on `feature/centralize-db-writes`)**.
> The shipped code **diverged from this plan** on several load-bearing points —
> for example:
>
> - initial-sync completion is signalled by an **`asyncio.Future`** carrying
>   the run's failure count, not a bare `asyncio.Event`;
> - compute-run state and completion are keyed by a per-run **`run_id`**, not
>   by provider (the planned `dict[Provider, Event]`);
> - a compute run interrupted by a provider shutdown is finalized through a
>   **consumer-side path** (`abandon_compute_run` → `_AbandonComputeRunJob` →
>   `_finalize_abandoned_run`) that did not exist at planning time.
>
> The plan body below is **kept verbatim, as a historical record** of the
> design intent; it is *not* maintained. For the **accurate, as-built
> description of the whole feature**, read the **"Appendix — As-built design"**
> section appended at the end of this file.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-provider register/unregister registry of the unified DB consumer with two process-wide shared queues drained by a single permanent consumer, extracted into its own `db_writer.py`.

**Architecture:** The consumer becomes a permanent task owned by `run_server` (boot → app shutdown), draining one shared `queue.Queue` (initial sync) and one shared `multiprocessing.Queue` (compute). Producers no longer register anything — each message carries its own `provider`. Completion is signalled by a sentinel pushed last by the producer: the initial-sync marker carries its own `asyncio.Event`; the compute side uses a small `dict[Provider, Event]` armed before each run (mp.Queue cannot transport an Event). Orchestrator `shutdown()` becomes blocking — it awaits the real end of the initial-sync thread, not just the wrapping coroutine.

**Tech Stack:** Python 3.13, Django 6 ORM, asyncio, `multiprocessing` (spawn), SQLite (WAL).

---

## Context

This plan continues the `feature/centralize-db-writes` branch (9 commits, `1a8e5a6b`..HEAD). Those commits introduced a unified consumer with a `register_*`/`unregister_*` registry. A Codex review found a critical bug (#1): on shutdown / hot-toggle, the orchestrator's `finally` unregisters the initial-sync entry while the `asyncio.to_thread` producer thread is still alive and still pushing payloads onto an orphaned queue → lost writes + zombie thread.

The fix chosen during design: drop the routing registry entirely. A permanent consumer + process-wide shared queues mean a late-pushing thread pushes onto a queue still drained by a still-alive consumer — the lost-writes condition disappears mechanically. The remaining concern (a zombie thread overlapping a new run on hot-toggle) is handled by making `shutdown()` block on the real thread end.

## Decisions (resolved during design)

- **`apply_session_complete` → `@staticmethod`.** Verified it never uses `self` (only the parameter and the `self_cost` field name). The consumer calls `BaseSessionCompute.apply_session_complete(msg)` directly; no per-provider `compute` instances needed in the consumer.
- **Hot-toggle.** `arm_compute_completion(provider, ...)` overwrites the provider's state + event on every run. Correct as long as runs never overlap — guaranteed by blocking `shutdown()`. A `logger.warning` fires if a previous run's state is still present when arming (surfaces a lifecycle bug).
- **Commits.** Two: Commit A = mechanical rename of `background_task.py` → `background_compute_task.py`. Commit B = the whole refonte (atomic — the system only works once every piece is in place).

## File Structure (after this plan)

| File | Responsibility |
|---|---|
| `src/twicc/providers/db_writer.py` | **NEW.** The permanent unified consumer. Owns the two shared queues, the queue payload vocabulary (NamedTuples), the consumer loop, the `_apply_*` write handlers, and the completion API (`arm_compute_completion`, `get_initial_sync_queue`, `get_compute_result_queue`, `start_unified_consumer`, `stop_unified_consumer`). |
| `src/twicc/providers/background_compute_task.py` | **RENAMED** from `background_task.py`. After the refonte, holds only the *compute producer*: `ComputeContext`, `compute_worker_main` (subprocess), `start_compute_process`, `start_background_compute_task`, `stop_background_task`. No consumer code. |
| `src/twicc/sync_helpers.py` | Producer-side initial-sync helpers only: `read_session_items_from_file`, `check_file_has_content`, `SessionItemsToInsert`. Payloads move out to `db_writer.py`. |
| `src/twicc/providers/claude_code/initial_sync.py` | Initial-sync producer (Claude Code). Imports payloads from `db_writer.py`. |
| `src/twicc/providers/codex/initial_sync.py` | Initial-sync producer (Codex). Imports payloads from `db_writer.py`. |
| `src/twicc/providers/claude_code/orchestrator.py` | Wires the shared queue into `_initial_sync_task`; blocking `shutdown()`. |
| `src/twicc/providers/codex/orchestrator.py` | Same as Claude Code. |
| `src/twicc/providers/compute_base.py` | `apply_session_complete` becomes `@staticmethod`; `compute_session_metadata` tags its messages with `provider`. |
| `src/twicc/cli/run.py` | `start_unified_consumer()` before `start_all()`; `stop_unified_consumer()` at global shutdown. |

**Import / spawn rule (critical):** `background_compute_task.py` is re-imported by the spawn subprocess *before* `django.setup()`. It must never import Django models at module level (already the case). It will need `arm_compute_completion` / `get_compute_result_queue` from `db_writer.py` — those imports MUST be **lazy** (inside functions), never top-level, so `db_writer.py` is not dragged into the subprocess. `db_writer.py` itself is never imported by the subprocess, but keeps the lazy-model-import discipline anyway.

---

## Task 1: Mechanical rename `background_task.py` → `background_compute_task.py`

**Files:**
- Rename: `src/twicc/providers/background_task.py` → `src/twicc/providers/background_compute_task.py`
- Modify: `src/twicc/providers/claude_code/orchestrator.py` (import line)
- Modify: `src/twicc/providers/codex/orchestrator.py` (import line)

- [ ] **Step 1: Rename the file with git**

Run: `cd <worktree> && git mv src/twicc/providers/background_task.py src/twicc/providers/background_compute_task.py`

- [ ] **Step 2: Update the two importers**

Both orchestrators import from `twicc.providers.background_task`. Change to `twicc.providers.background_compute_task`. Find them:

Run: `cd <worktree> && grep -rn "providers.background_task\|providers import background_task\|background_task import" src/`

Expected: matches only in `claude_code/orchestrator.py` and `codex/orchestrator.py`. Update each `from twicc.providers.background_task import (...)` → `from twicc.providers.background_compute_task import (...)`.

- [ ] **Step 3: Sanity check — no stale reference remains**

Run: `cd <worktree> && grep -rn "background_task" src/ ; echo "---done---"`
Expected: no match (only `---done---`). If any match remains, fix it.

- [ ] **Step 4: Compile**

Run: `cd <worktree> && python -m py_compile src/twicc/providers/background_compute_task.py src/twicc/providers/claude_code/orchestrator.py src/twicc/providers/codex/orchestrator.py && echo OK`
Expected: `OK`

- [ ] **Step 5: Commit A**

```bash
cd <worktree>
git add src/twicc/providers/background_task.py src/twicc/providers/background_compute_task.py src/twicc/providers/claude_code/orchestrator.py src/twicc/providers/codex/orchestrator.py
git commit -m "$(cat <<'EOF'
refactor(compute): rename background_task.py to background_compute_task.py

The file holds the background compute producer. The unified DB consumer
that grew inside it is about to move into its own db_writer.py, so the
name is renamed up front to reflect its real (and soon only) role.

Mechanical rename + import updates, no behaviour change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

> Everything below is **Commit B** — do not commit between Task 2 and Task 10. Intermediate states are intentionally incoherent (e.g. duplicated code between Task 3 and Task 5). Each task still py-compiles the files it touches.

---

## Task 2: `apply_session_complete` → `@staticmethod`

**Files:**
- Modify: `src/twicc/providers/compute_base.py` (the `apply_session_complete` method)

- [ ] **Step 1: Convert to staticmethod**

Locate `apply_session_complete`. It currently reads:

```python
    @transaction.atomic
    def apply_session_complete(self, msg: dict) -> None:
```

Change to:

```python
    @staticmethod
    @transaction.atomic
    def apply_session_complete(msg: dict) -> None:
```

(Decorator order matters: `@staticmethod` outermost, then `@transaction.atomic`, so the transaction wraps the plain function.)

The body uses no `self` — leave it byte-for-byte identical otherwise. Existing test callers (`provider_compute.apply_session_complete(msg)`, `get_compute().apply_session_complete(msg)`) keep working: Python allows calling a staticmethod through an instance.

- [ ] **Step 2: Compile**

Run: `cd <worktree> && python -m py_compile src/twicc/providers/compute_base.py && echo OK`
Expected: `OK`

---

## Task 3: Create `db_writer.py` — payloads, state, lifecycle, completion API

**Files:**
- Create: `src/twicc/providers/db_writer.py`

- [ ] **Step 1: Write the module (part 1 — header, payloads, state, lifecycle, API)**

Create `src/twicc/providers/db_writer.py` with exactly this content (Task 4 appends the consumer loop + handlers to the same file):

```python
"""
Unified DB writer.

A single permanent coroutine that owns every database write coming from the
two boot-time "big jobs": the per-provider initial sync (producers run in
``asyncio.to_thread`` threads) and the background metadata compute (producers
run in ``multiprocessing`` subprocesses). Both push onto process-wide shared
queues; this consumer drains them and applies every write inside
``transaction.atomic`` from one place — so the SQLite write lock is never
contended between providers, nor between the initial-sync phase and the
compute phase.

Lifecycle: started once by ``run_server`` at boot (before any provider
orchestrator starts), stopped once at application shutdown (after every
orchestrator has shut down). It strictly outlives every producer. Provider
orchestrators never start/stop it and never register anything for routing —
each message carries its own ``provider``.

Completion signalling: a producer pushes a sentinel *last*, after all its
real messages. Because the queues are FIFO, draining the sentinel proves
every preceding message of that run has been applied.
- Initial sync: ``InitialSyncDoneMarker`` carries its own ``asyncio.Event``
  (the queue is intra-process, the Event travels by reference).
- Compute: the ``done`` message comes from a subprocess and cannot carry an
  Event, so ``arm_compute_completion`` hands the orchestrator a fresh Event
  kept in ``_compute_done_events`` and set when the ``done`` is drained.

Import discipline: this module is never imported by the spawn subprocess.
Django model imports are still done lazily inside functions, matching the
convention of ``background_compute_task.py``.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import queue
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date as date_cls
from typing import NamedTuple

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import transaction

from twicc.core.enums import Provider
from twicc.startup_progress import broadcast_startup_progress
from twicc.workspaces import auto_add_project_to_workspaces

logger = logging.getLogger(__name__)

# Throttle: broadcast project_updated every N normal sessions during compute.
PROJECT_BROADCAST_INTERVAL = 5
# Batch size for activity recalculation flushes (per provider).
BATCH_ACTIVITY_COUNT = 50

# "spawn" context — the compute result queue is created here and passed to
# the spawn workers, so it must come from the same context they use.
_mp_ctx = multiprocessing.get_context("spawn")


# =============================================================================
# Queue payload vocabulary
# =============================================================================
#
# The consumer owns this vocabulary: every type below has a handler in
# _process_initial_sync_message. Initial-sync producers
# (providers/<p>/initial_sync.py) import these and push them; they only ever
# use the subset that applies to them. The compute side does NOT use these —
# its messages are plain orjson-encoded dicts produced by the subprocess.


class CreateSessionPayload(NamedTuple):
    """Producer parsed a JSONL file for a session not yet in DB.

    The consumer creates the ``Project`` (idempotent — no-op if it exists),
    saves the unsaved ``Session`` instance, bulk-creates the items, then
    persists the tracking fields. ``new_project_directory`` may be ``None``
    (Claude Code stores the cwd inside the JSONL body, not available at
    initial-sync time).
    """

    provider: Provider
    project_id: str
    new_project_directory: str | None
    new_project_stale: bool
    session: object  # an unsaved twicc.core.models.Session instance
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float


class UpdateSessionPayload(NamedTuple):
    """Producer parsed a JSONL file for a session already in DB.

    The consumer appends the new items (``ignore_conflicts=True`` covers the
    rare watcher-already-inserted-them race), persists the tracking fields,
    optionally clears ``stale``, and optionally resets ``compute_version``.
    """

    provider: Provider
    session: object  # an existing twicc.core.models.Session instance
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float
    reset_compute_version: bool
    clear_stale: bool


class MarkSessionsStalePayload(NamedTuple):
    """Producer wants to flag a set of sessions stale (no longer on disk).

    The consumer issues one ``Session.objects.filter(id__in=..., stale=False).update(stale=True)``.
    """

    provider: Provider
    session_ids: list[str]


class UpdateProjectMetadataPayload(NamedTuple):
    """End-of-sync metadata for one project.

    Each non-``None`` field is applied; ``None`` means leave it alone. Two
    boolean flags ask the consumer to invoke heavier helpers that themselves
    write to DB (``update_project_total_cost``, ``ensure_project_git_root``).
    Bundled so the project's end-of-sync writes commit in one transaction.
    """

    provider: Provider
    project_id: str
    new_sessions_count: int | None
    new_mtime: float | None
    new_stale: bool | None
    recalc_total_cost: bool
    resolve_git_root: bool
    git_root_directory: str | None


class InitialSyncDoneMarker(NamedTuple):
    """Sentinel pushed last by an initial-sync producer.

    Carries the ``asyncio.Event`` the consumer sets once it drains the
    marker — i.e. once every payload of that run has been applied. The
    producer awaits this same Event.
    """

    provider: Provider
    done_event: asyncio.Event


# =============================================================================
# Module state
# =============================================================================

# The two process-wide shared queues, created by start_unified_consumer().
_initial_sync_queue: queue.Queue | None = None
_compute_result_queue: object | None = None  # _mp_ctx.Queue()

# The permanent consumer task and its stop signal.
_consumer_task: asyncio.Task | None = None
_consumer_stop_event: asyncio.Event | None = None

# Compute completion: the mp.Queue cannot transport an asyncio.Event, so the
# orchestrator arms an Event here before a run and the consumer sets it when
# it drains that provider's 'done' message.
_compute_done_events: dict[Provider, asyncio.Event] = {}

# Per-provider accumulated state for the compute side only (broadcast
# throttling, batched activity flushes). The initial-sync side has no
# accumulated state — every payload is self-contained.
_compute_states: dict[Provider, "_ComputeProviderState"] = {}


@dataclass
class _ComputeProviderState:
    """Per-provider compute-run state, created by arm_compute_completion()."""

    display_session_ids: set[str] | None
    total_display: int
    pending_activity_days: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    pending_project_ids: set[str] = field(default_factory=set)
    auto_added_project_ids: set[str] = field(default_factory=set)
    sessions_since_project_broadcast: int = 0
    sessions_since_activities_flush: int = 0
    completed_count: int = 0


# =============================================================================
# Lifecycle + completion API (called by run_server and the orchestrators)
# =============================================================================


def start_unified_consumer() -> None:
    """Create the shared queues and launch the permanent consumer task.

    Called once by ``run_server`` at boot, before any provider orchestrator
    starts. Raises if called twice (programming error).
    """
    global _initial_sync_queue, _compute_result_queue
    global _consumer_stop_event, _consumer_task

    if _consumer_task is not None:
        raise RuntimeError("unified DB writer already started")

    _initial_sync_queue = queue.Queue()
    _compute_result_queue = _mp_ctx.Queue()
    _consumer_stop_event = asyncio.Event()
    _consumer_task = asyncio.create_task(_consumer_loop())
    logger.info("Unified DB writer started")


async def stop_unified_consumer() -> None:
    """Signal the consumer to stop and await it. Called once at app shutdown.

    Idempotent. Must run after every provider orchestrator has shut down, so
    no producer is still pushing.
    """
    if _consumer_stop_event is not None:
        _consumer_stop_event.set()
    if _consumer_task is not None:
        with suppress(Exception):
            await _consumer_task
    logger.info("Unified DB writer stopped")


def get_initial_sync_queue() -> queue.Queue:
    """Return the shared initial-sync queue (for an orchestrator's producer)."""
    assert _initial_sync_queue is not None, "unified DB writer not started"
    return _initial_sync_queue


def get_compute_result_queue():
    """Return the shared compute result queue (passed to a compute worker)."""
    assert _compute_result_queue is not None, "unified DB writer not started"
    return _compute_result_queue


def arm_compute_completion(
    provider: Provider,
    display_session_ids: set[str] | None,
    total_display: int,
) -> asyncio.Event:
    """Declare a compute run for ``provider`` and return its completion Event.

    Creates a fresh ``_ComputeProviderState`` and a fresh ``asyncio.Event``.
    The consumer sets the Event when it drains the ``done`` message for this
    provider (and pops the state). Overwrites any leftover state from a
    previous run — safe because the blocking ``shutdown()`` guarantees runs
    never overlap. A warning is logged if leftover state is found, which
    would mean a previous run did not finalize (lifecycle bug).
    """
    if provider in _compute_states:
        logger.warning(
            "arm_compute_completion: provider=%s still has compute state from "
            "a previous run — runs should not overlap (lifecycle bug?)",
            provider.value,
        )
    _compute_states[provider] = _ComputeProviderState(
        display_session_ids=display_session_ids,
        total_display=total_display,
    )
    event = asyncio.Event()
    _compute_done_events[provider] = event
    return event
```

- [ ] **Step 2: Compile (the file is syntactically complete on its own so far)**

Run: `cd <worktree> && python -m py_compile src/twicc/providers/db_writer.py && echo OK`
Expected: `OK`

---

## Task 4: `db_writer.py` — consumer loop, message handlers, write appliers

**Files:**
- Modify: `src/twicc/providers/db_writer.py` (append to the file from Task 3)

- [ ] **Step 1: Append the consumer loop + handlers**

Append exactly this to the end of `src/twicc/providers/db_writer.py`:

```python
# =============================================================================
# The consumer loop
# =============================================================================


async def _consumer_loop() -> None:
    """Drain both shared queues until the application shuts down.

    Permanent: runs from start_unified_consumer() to stop_unified_consumer().
    Resilient: an unexpected exception in one tick is logged and the loop
    continues — a single bad message must never take the writer down.
    """
    assert _consumer_stop_event is not None
    assert _initial_sync_queue is not None
    assert _compute_result_queue is not None

    logger.info("Unified DB writer loop running")
    while not _consumer_stop_event.is_set():
        try:
            any_processed = False

            # ---- Compute side (mp.Queue, shared by every provider) ----
            try:
                raw = _compute_result_queue.get_nowait()
            except queue.Empty:
                pass
            else:
                any_processed = True
                try:
                    msg = orjson.loads(raw)
                except Exception:
                    logger.error(f"Failed to deserialize compute message: {raw!r:.500}")
                else:
                    await _process_compute_message(msg)

            # ---- Initial-sync side (queue.Queue, shared by every provider) ----
            try:
                payload = _initial_sync_queue.get_nowait()
            except queue.Empty:
                pass
            else:
                any_processed = True
                await _process_initial_sync_message(payload)

            # Yield: tightly when busy, back off when idle.
            await asyncio.sleep(0 if any_processed else 0.05)

        except Exception as exc:
            logger.error(f"Unified DB writer tick crashed: {exc}", exc_info=True)
            await asyncio.sleep(0.05)

    logger.info("Unified DB writer loop exited")


# =============================================================================
# Compute message handling
# =============================================================================


async def _process_compute_message(msg: dict) -> None:
    """Apply one message drained from the shared compute result queue."""
    msg_type = msg.get("type")
    provider_value = msg.get("provider")
    if provider_value is None:
        logger.error(f"Compute message without 'provider': {msg_type} => {msg!r:.300}")
        return
    try:
        provider = Provider(provider_value)
    except ValueError:
        logger.error(f"Compute message with unknown provider {provider_value!r}")
        return

    if msg_type == "done":
        await _finalize_compute_run(provider)
        return

    if msg_type == "error":
        logger.error(f"Compute error for {msg.get('session_id')}: {msg.get('error')}")
        return

    if msg_type != "session_complete":
        logger.error(f"Unexpected compute message type: {msg_type} => {msg!r:.300}")
        return

    # session_complete — the heavy path.
    state = _compute_states.get(provider)
    if state is None:
        # A session_complete arrived without arm_compute_completion() having
        # run. Abnormal, but the write itself does not need state — apply it
        # and skip the broadcast/activity bookkeeping.
        logger.warning(
            "session_complete for provider=%s with no armed compute state",
            provider.value,
        )

    try:
        from twicc.providers.compute_base import BaseSessionCompute
        await sync_to_async(BaseSessionCompute.apply_session_complete)(msg)
        await _handle_compute_done(msg["session_id"])
    except Exception as e:
        logger.error(f"Error applying session_complete: {e}", exc_info=True)
        return

    if state is None:
        return

    project_id = msg.get("project_id")
    if project_id:
        state.pending_project_ids.add(project_id)

    project_directory = msg.get("project_directory")
    if project_id and project_directory and project_id not in state.auto_added_project_ids:
        state.auto_added_project_ids.add(project_id)
        await auto_add_project_to_workspaces(project_id, project_directory)

    session_id = msg["session_id"]
    if state.display_session_ids is None or session_id in state.display_session_ids:
        state.completed_count += 1
        await broadcast_startup_progress(
            "background_compute", state.completed_count, state.total_display,
            provider=provider.value,
        )
        state.sessions_since_project_broadcast += 1
        if state.sessions_since_project_broadcast >= PROJECT_BROADCAST_INTERVAL:
            for pid in state.pending_project_ids:
                await _broadcast_project_updated(pid)
            state.pending_project_ids.clear()
            state.sessions_since_project_broadcast = 0

    affected_days = msg.get("affected_days")
    if project_id and affected_days:
        state.pending_activity_days[project_id].update(
            date_cls.fromisoformat(d) for d in affected_days
        )
        state.sessions_since_activities_flush += 1

    if state.sessions_since_activities_flush >= BATCH_ACTIVITY_COUNT:
        try:
            await _flush_pending_activities(provider, state.pending_activity_days)
        except Exception as e:
            logger.error(f"Error flushing activity recalculations: {e}", exc_info=True)
        state.pending_activity_days.clear()
        state.sessions_since_activities_flush = 0


async def _finalize_compute_run(provider: Provider) -> None:
    """Drain-time finalisation when a provider's compute worker is done.

    Flushes the provider's pending broadcasts + activities, sets its
    completion Event, and drops its state so the next run starts clean.
    """
    logger.info(f"Unified DB writer: compute 'done' for provider={provider.value}")
    state = _compute_states.pop(provider, None)
    if state is not None:
        for pid in state.pending_project_ids:
            try:
                await _broadcast_project_updated(pid)
            except Exception as e:
                logger.error(f"Error in final project broadcast for {pid}: {e}")
        if state.pending_activity_days:
            try:
                await _flush_pending_activities(provider, state.pending_activity_days)
            except Exception as e:
                logger.error(f"Error in final activity flush: {e}", exc_info=True)

    event = _compute_done_events.pop(provider, None)
    if event is not None:
        event.set()
    else:
        logger.warning(
            "compute 'done' for provider=%s with no armed completion event",
            provider.value,
        )


async def _handle_compute_done(session_id: str) -> None:
    """Broadcast session_updated for a real, user-visible session."""
    from twicc.core.models import Session, SessionType
    from twicc.core.serializers import serialize_session

    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)
        if session.user_message_count == 0 or session.type != SessionType.SESSION:
            return
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            }},
        )
    except Session.DoesNotExist:
        logger.warning(f"Session {session_id} not found for broadcast")
    except Exception as e:
        logger.error(f"Error broadcasting updates for {session_id}: {e}")


async def _broadcast_project_updated(project_id: str) -> None:
    """Broadcast project_updated for a single project."""
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    try:
        if project := await sync_to_async(Project.objects.filter(id=project_id).first)():
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "updates",
                {"type": "broadcast", "data": {
                    "type": "project_updated",
                    "project": serialize_project(project),
                }},
            )
    except Exception as e:
        logger.error(f"Error broadcasting project_updated for {project_id}: {e}")


@sync_to_async
def _flush_pending_activities(provider: Provider, pending_activity_days: dict[str, set]) -> None:
    """Flush accumulated activity recalculations for all projects."""
    from twicc.core.models import PeriodicActivity

    for project_id, days in pending_activity_days.items():
        PeriodicActivity.recalculate_for_days(project_id, days, provider=provider, do_global=False)
    days = set.union(*pending_activity_days.values())
    PeriodicActivity.recalculate_for_days(None, days, provider=provider, do_global=True)


# =============================================================================
# Initial-sync message handling
# =============================================================================


async def _process_initial_sync_message(msg) -> None:
    """Apply one message drained from the shared initial-sync queue."""
    try:
        if isinstance(msg, CreateSessionPayload):
            await sync_to_async(_apply_create_session_payload)(msg)
        elif isinstance(msg, UpdateSessionPayload):
            await sync_to_async(_apply_update_session_payload)(msg)
        elif isinstance(msg, MarkSessionsStalePayload):
            await sync_to_async(_apply_mark_sessions_stale_payload)(msg)
        elif isinstance(msg, UpdateProjectMetadataPayload):
            await sync_to_async(_apply_update_project_metadata_payload)(msg)
        elif isinstance(msg, InitialSyncDoneMarker):
            logger.info(
                f"Unified DB writer: initial-sync 'done' for provider={msg.provider.value}"
            )
            msg.done_event.set()
        else:
            logger.error(
                f"Unexpected initial-sync message type: {type(msg).__name__} => {msg!r:.300}"
            )
    except Exception as e:
        logger.error(
            f"Error processing initial-sync message {type(msg).__name__}: {e}",
            exc_info=True,
        )


def _apply_create_session_payload(payload: CreateSessionPayload) -> None:
    """Persist a new session (and project if missing) plus its items, atomically."""
    from twicc.core.models import SessionItem
    from twicc.projects import register_project_sync

    with transaction.atomic():
        register_project_sync(
            payload.project_id,
            directory=payload.new_project_directory,
            stale=payload.new_project_stale,
        )
        payload.session.save()
        if payload.items:
            SessionItem.objects.bulk_create(
                [SessionItem(session=payload.session, line_num=ln, content=ct)
                 for ln, ct in payload.items],
                ignore_conflicts=True, batch_size=50,
            )
        payload.session.last_offset = payload.last_offset
        payload.session.last_line = payload.last_line
        payload.session.mtime = payload.mtime
        payload.session.save(update_fields=["last_offset", "last_line", "mtime"])


def _apply_update_session_payload(payload: UpdateSessionPayload) -> None:
    """Append items to an existing session and update tracking fields, atomically."""
    from twicc.core.models import SessionItem

    with transaction.atomic():
        if payload.items:
            SessionItem.objects.bulk_create(
                [SessionItem(session=payload.session, line_num=ln, content=ct)
                 for ln, ct in payload.items],
                ignore_conflicts=True, batch_size=50,
            )
        update_fields = ["last_offset", "last_line", "mtime"]
        payload.session.last_offset = payload.last_offset
        payload.session.last_line = payload.last_line
        payload.session.mtime = payload.mtime
        if payload.clear_stale and payload.session.stale:
            payload.session.stale = False
            update_fields.append("stale")
        if payload.reset_compute_version and payload.session.compute_version is not None:
            payload.session.compute_version = None
            update_fields.append("compute_version")
        payload.session.save(update_fields=update_fields)


def _apply_mark_sessions_stale_payload(payload: MarkSessionsStalePayload) -> None:
    """Mark a batch of sessions stale via a single bulk UPDATE."""
    from twicc.core.models import Session

    if not payload.session_ids:
        return
    with transaction.atomic():
        Session.objects.filter(id__in=payload.session_ids, stale=False).update(stale=True)


def _apply_update_project_metadata_payload(payload: UpdateProjectMetadataPayload) -> None:
    """Apply end-of-sync metadata updates for one project, atomically."""
    from twicc.core.models import Project
    from twicc.projects import ensure_project_git_root, update_project_total_cost

    with transaction.atomic():
        if (payload.new_sessions_count is not None
                or payload.new_mtime is not None
                or payload.new_stale is not None):
            try:
                project = Project.objects.get(id=payload.project_id)
            except Project.DoesNotExist:
                logger.warning(
                    f"UpdateProjectMetadataPayload: project {payload.project_id} not found"
                )
                project = None
            if project is not None:
                update_fields: list[str] = []
                if payload.new_sessions_count is not None:
                    project.sessions_count = payload.new_sessions_count
                    update_fields.append("sessions_count")
                if payload.new_mtime is not None:
                    project.mtime = payload.new_mtime
                    update_fields.append("mtime")
                if payload.new_stale is not None:
                    project.stale = payload.new_stale
                    update_fields.append("stale")
                if update_fields:
                    project.save(update_fields=update_fields)

        if payload.recalc_total_cost:
            update_project_total_cost(payload.project_id)
        if payload.resolve_git_root and payload.git_root_directory:
            ensure_project_git_root(payload.project_id, payload.git_root_directory)
```

- [ ] **Step 2: Compile**

Run: `cd <worktree> && python -m py_compile src/twicc/providers/db_writer.py && echo OK`
Expected: `OK`

> Note: this `db_writer.py` reproduces the broadcast/activity logic that currently lives in `background_compute_task.py`'s `_process_compute_message` / `_finalize_entry` / `_handle_compute_done` / `_broadcast_project_updated` / `_flush_pending_activities`. When Task 5 strips that file, those become the source of truth here. Keep them byte-equivalent in behaviour.

---

## Task 5: Strip `background_compute_task.py` to the compute producer only

**Files:**
- Modify: `src/twicc/providers/background_compute_task.py`

- [ ] **Step 1: Remove the consumer code now living in `db_writer.py`**

Delete from `background_compute_task.py`:
- `_ConsumerEntry`, `_InitialSyncEntry` dataclasses
- `_consumer_entries`, `_initial_sync_entries`, `_unified_consumer_task` module state
- `register_compute_consumer`, `unregister_compute_consumer`
- `register_initial_sync_entry`, `unregister_initial_sync_entry`
- `_ensure_unified_consumer_running`
- `_unified_consumer_loop`
- `_process_compute_message`, `_process_initial_sync_message`
- `_apply_create_session_payload`, `_apply_update_session_payload`, `_apply_mark_sessions_stale_payload`, `_apply_update_project_metadata_payload`
- `_finalize_entry`, `_finalize_initial_sync_entry`
- `_handle_compute_done`, `_broadcast_project_updated`, `_flush_pending_activities`
- The constants `PROJECT_BROADCAST_INTERVAL`, `BATCH_ACTIVITY_COUNT` (moved to `db_writer.py`)

Keep: `ComputeContext`, `_resolve_factory`, `stop_background_task`, `_set_pdeathsig_linux`, `compute_worker_main`, `start_compute_process`, `start_background_compute_task`.

Drop the imports used **only** by the removed consumer code: `from collections import defaultdict`, `from datetime import date as date_cls`, `from channels.layers import get_channel_layer`, `from twicc.workspaces import auto_add_project_to_workspaces`, and `from django.db import transaction` (every `_apply_*` function moves to `db_writer.py`; no retained function uses `transaction`).

**KEEP** these — the retained code still uses them: `from asgiref.sync import sync_to_async` and `from twicc.startup_progress import broadcast_startup_progress` (both used heavily by the rewritten `start_background_compute_task` in Step 6), `import orjson` (used by `compute_worker_main`), `from contextlib import suppress` (used by `stop_background_task`), plus `asyncio`, `importlib`, `logging`, `multiprocessing`, `queue`, `dataclass`/`field`, `TYPE_CHECKING`, `Provider`, `current_provider`. Verify with the compile.

- [ ] **Step 2: `ComputeContext` — drop the per-context `result_queue`**

`ComputeContext` currently has `result_queue: _mp_ctx.Queue = field(default_factory=_mp_ctx.Queue)`. Remove that field — the compute result queue is now the process-wide shared one. Keep `command_queue` (still per-worker, one-way main→worker), `worker_stop_event`, `process`, `provider`, `compute_version`, `compute_factory`.

- [ ] **Step 3: `start_compute_process` — pass the shared result queue to the worker**

`start_compute_process(ctx)` builds the `_mp_ctx.Process(target=compute_worker_main, args=(ctx.command_queue, ctx.result_queue, ...))`. Replace `ctx.result_queue` with the shared queue obtained lazily:

```python
def start_compute_process(ctx: ComputeContext) -> None:
    if ctx.process is None or not ctx.process.is_alive():
        from twicc.providers.db_writer import get_compute_result_queue  # lazy import
        result_queue = get_compute_result_queue()
        ctx.worker_stop_event = _mp_ctx.Event()
        ctx.process = _mp_ctx.Process(
            target=compute_worker_main,
            args=(ctx.command_queue, result_queue, ctx.worker_stop_event, ctx.compute_factory),
            daemon=True,
            name="compute-worker",
        )
        ctx.process.start()
        logger.info(f"Started compute worker process (PID: {ctx.process.pid})")
```

- [ ] **Step 4: `stop_background_task` — do not close the shared result queue**

`stop_background_task` currently calls `ctx.result_queue.cancel_join_thread()` / `.close()`. The result queue is now shared and permanent — it must NOT be closed when one provider stops. Remove the two lines that cancel/close `result_queue`. Keep the `command_queue` cancel/close (that one is still per-context). Keep all the process join/terminate/kill logic.

- [ ] **Step 5: `compute_worker_main` — tag every emitted message with `provider`**

The worker emits messages onto the (now shared) result queue. Every message MUST carry `'provider'` so the consumer can route. The worker has `compute` in scope, hence `compute.provider.value`.

- The `error` message: add `'provider': compute.provider.value`.
- The `done` message: change `result_queue.put(orjson.dumps({'type': 'done'}))` to `result_queue.put(orjson.dumps({'type': 'done', 'provider': compute.provider.value}))`.
- The `session_complete` messages are emitted inside `compute.compute_session_metadata(session_id, result_queue)` — Task 8 handles that in `compute_base.py`. (`compute_worker_main` itself only emits `error` and `done`.)

- [ ] **Step 6: `start_background_compute_task` — use `arm_compute_completion`**

Currently it does `register_compute_consumer(ctx, worker_done_event, display_session_ids=..., total_display=...)`, then `try: ... await worker_done_event.wait() ... finally: unregister_compute_consumer(...) ; stop_background_task(ctx)`.

Replace with: arm the completion Event via the lazy-imported `arm_compute_completion`, no register/unregister:

```python
async def start_background_compute_task(ctx: ComputeContext) -> None:
    from twicc.providers.db_writer import arm_compute_completion  # lazy import
    from twicc.projects import load_project_directories, load_project_git_roots
    from twicc.core.models import Session, SessionType

    provider_value = ctx.provider.value

    total_to_compute = await sync_to_async(Session.objects.filter(
        provider=ctx.provider,
    ).exclude(compute_version=ctx.compute_version).count)()

    if total_to_compute == 0:
        logger.info("Background compute: no sessions to process")
        total_display = await sync_to_async(
            Session.objects.filter(provider=ctx.provider, type=SessionType.SESSION).count
        )()
        await broadcast_startup_progress(
            "background_compute", total_display, total_display,
            provider=provider_value, completed=True,
        )
        return

    sessions_to_display = await sync_to_async(lambda: set(
        Session.objects.filter(provider=ctx.provider, type=SessionType.SESSION)
        .exclude(compute_version=ctx.compute_version)
        .values_list("id", flat=True)
    ))()
    total_display = len(sessions_to_display)

    await broadcast_startup_progress(
        "background_compute", 0, total_display, provider=provider_value
    )
    await sync_to_async(load_project_directories)()
    await sync_to_async(load_project_git_roots)()

    start_compute_process(ctx)

    # Arm the completion Event with the unified DB writer. The writer sets it
    # when it drains this provider's 'done' message.
    done_event = arm_compute_completion(ctx.provider, sessions_to_display, total_display)

    logger.info(f"Background compute task started ({total_to_compute} sessions to process)")
    try:
        session_ids_to_compute = await sync_to_async(lambda: list(
            Session.objects.filter(provider=ctx.provider)
            .exclude(compute_version=ctx.compute_version)
            .order_by("-mtime")
            .values_list("id", flat=True)
        ))()
        for session_id in session_ids_to_compute:
            if ctx.stop_event.is_set():
                break
            ctx.command_queue.put({"session_id": session_id})
        logger.info(f"Background compute: all {len(session_ids_to_compute)} sessions sent to worker")
        ctx.command_queue.put(None)

        await done_event.wait()

        await broadcast_startup_progress(
            "background_compute", total_display, total_display,
            provider=provider_value, completed=True,
        )
    finally:
        stop_background_task(ctx)

    logger.info("Background compute task completed")
```

Keep `broadcast_startup_progress` imported at module top (still used here).

- [ ] **Step 7: Compile**

Run: `cd <worktree> && python -m py_compile src/twicc/providers/background_compute_task.py && echo OK`
Expected: `OK`

---

## Task 6: Move payloads out of `sync_helpers.py`

**Files:**
- Modify: `src/twicc/sync_helpers.py`
- Modify: `src/twicc/providers/claude_code/initial_sync.py` (import line)
- Modify: `src/twicc/providers/codex/initial_sync.py` (import line)

- [ ] **Step 1: Delete the payload NamedTuples from `sync_helpers.py`**

Remove `CreateSessionPayload`, `UpdateSessionPayload`, `MarkSessionsStalePayload`, `UpdateProjectMetadataPayload`, `InitialSyncDoneMarker` (they now live in `db_writer.py`). Keep `SessionItemsToInsert`, `check_file_has_content`, `read_session_items_from_file`. Drop the now-unused `from twicc.core.enums import Provider` if nothing else in the file uses it (check — `read_session_items_from_file` does not; `SessionItemsToInsert` does not).

- [ ] **Step 2: Repoint the two initial_sync.py imports**

Both `claude_code/initial_sync.py` and `codex/initial_sync.py` currently import the payloads from `twicc.sync_helpers`. Split the import: `check_file_has_content` + `read_session_items_from_file` stay from `twicc.sync_helpers`; the payloads come from `twicc.providers.db_writer`:

```python
from twicc.sync_helpers import check_file_has_content, read_session_items_from_file
from twicc.providers.db_writer import (
    CreateSessionPayload,
    MarkSessionsStalePayload,
    UpdateProjectMetadataPayload,
    UpdateSessionPayload,
)
```

`InitialSyncDoneMarker` is imported by the *orchestrators* (Tasks 8/9), not the `initial_sync.py` producers — leave it out of these import lists.

- [ ] **Step 3: Compile**

Run: `cd <worktree> && python -m py_compile src/twicc/sync_helpers.py src/twicc/providers/claude_code/initial_sync.py src/twicc/providers/codex/initial_sync.py && echo OK`
Expected: `OK`

---

## Task 7: Wire the permanent consumer into `run_server`

**Files:**
- Modify: `src/twicc/cli/run.py`

- [ ] **Step 1: Start the consumer before the orchestrators**

In `run_server`, locate `await orchestrators.start_all(...)`. Immediately before it, add:

```python
    from twicc.providers.db_writer import start_unified_consumer, stop_unified_consumer
    start_unified_consumer()
```

(`start_unified_consumer` must run on the event loop, before any orchestrator — and therefore any producer — starts.)

- [ ] **Step 2: Stop the consumer at global shutdown**

Find where `run_server` shuts the orchestrators down (the shutdown path after `server.serve()` returns / after the shutdown event). After every orchestrator has been shut down, add:

```python
    await stop_unified_consumer()
```

It must run AFTER the orchestrators' `shutdown()` so no producer is still pushing. Read the surrounding shutdown code and place it on the correct path (there may be a `finally`/cleanup block — match the existing structure).

- [ ] **Step 3: Compile**

Run: `cd <worktree> && python -m py_compile src/twicc/cli/run.py && echo OK`
Expected: `OK`

---

## Task 8: Claude Code — wire shared queue, blocking shutdown; `compute_session_metadata` provider tag

**Files:**
- Modify: `src/twicc/providers/claude_code/orchestrator.py`
- Modify: `src/twicc/providers/compute_base.py` (`compute_session_metadata`)

- [ ] **Step 1: `compute_session_metadata` — tag `session_complete` with `provider`**

In `compute_base.py`, `compute_session_metadata` pushes messages onto `result_queue`. There are **two** `result_queue.put(...)` sites in that method, and **both** dicts must carry `'provider': self.provider.value` (`compute_session_metadata` is a method, `self.provider` is available):

1. The `session_complete` payload (around line 1941).
2. The `error` payload — `{'type': 'error', 'session_id': ..., 'error': 'Session not found'}` (around line 1587).

If the `error` dict is **not** tagged, `db_writer._process_compute_message` hits `if provider_value is None: return` and silently swallows the error instead of logging it. Grep inside the method for `result_queue.put` to confirm you tagged both.

- [ ] **Step 2: `_initial_sync_task` — use the shared queue, keep the thread future**

In `claude_code/orchestrator.py`, the current `_initial_sync_task` creates its own `queue.Queue()`, calls `register_initial_sync_entry`, runs `await asyncio.to_thread(sync_all, sync_queue, ...)`, pushes `InitialSyncDoneMarker`, awaits `worker_done_event`, and `unregister`s in a `finally`.

Replace with: take the shared queue from `db_writer`, keep an explicit reference to the `to_thread` future on `self`, and let `shutdown()` (Step 3) do the blocking wait. No register/unregister.

```python
    async def _initial_sync_task(self) -> None:
        """Run sync_all() in a thread, pushing payloads onto the shared queue."""
        from twicc.providers.db_writer import get_initial_sync_queue, InitialSyncDoneMarker

        loop = asyncio.get_running_loop()
        provider_value = self.provider.value

        total_sessions = await asyncio.to_thread(_count_total_sessions)
        await broadcast_startup_progress(
            "initial_sync", 0, total_sessions, provider=provider_value
        )

        progress = {"current": 0}

        def on_session_progress(session_id: str, idx: int, total: int):
            progress["current"] += 1
            asyncio.run_coroutine_threadsafe(
                broadcast_startup_progress(
                    "initial_sync", progress["current"], total_sessions, provider=provider_value
                ),
                loop,
            )

        sync_queue = get_initial_sync_queue()
        logger.info("Starting data synchronization...")

        # Keep an explicit reference to the producer thread future so
        # shutdown() can wait for the *real* thread end, not just this
        # coroutine being cancelled.
        self._sync_thread_future = asyncio.ensure_future(
            asyncio.to_thread(
                sync_all, sync_queue,
                on_session_progress=on_session_progress,
                stop_event=self._sync_stop_event,
            )
        )
        await self._sync_thread_future

        # Producer thread finished pushing — close the run with the marker.
        done_event = asyncio.Event()
        sync_queue.put(InitialSyncDoneMarker(provider=self.provider, done_event=done_event))
        await done_event.wait()

        await broadcast_startup_progress(
            "initial_sync", total_sessions, total_sessions,
            provider=provider_value, completed=True,
        )
        # ... keep the rest of the original method (projects_count log,
        #     self.initial_sync_done.set(), etc.) unchanged ...
```

Add `self._sync_thread_future: asyncio.Future | None = None` to `__init__`.

> Why no `try/finally` here: with the permanent consumer, a thread that keeps pushing after a cancel pushes onto a still-drained queue — no lost writes. The zombie-thread / overlap concern is handled by `shutdown()` (Step 3) blocking on `_sync_thread_future`.

- [ ] **Step 3: `shutdown()` — block on the real thread end**

In `shutdown()`, after `self._sync_stop_event.set()` and before/around the existing `_cancel_task(self._sync_task, ...)`, make the shutdown wait for the producer thread itself:

```python
        self._sync_stop_event.set()
        ...
        # Cancel the coroutine, then wait for the underlying producer THREAD
        # to actually finish (asyncio.to_thread does not kill the thread on
        # cancel — only awaiting its future proves it stopped).
        if self._sync_task is not None:
            await _cancel_task(self._sync_task, "Initial sync task")
        if self._sync_thread_future is not None and not self._sync_thread_future.done():
            with suppress(Exception):
                await asyncio.shield(self._sync_thread_future)
            self._sync_thread_future = None
```

This is what makes `shutdown()` honest: it does not return (the orchestrator does not reach the `stopped` phase) until the initial-sync thread has actually exited. Combined with the front-end blocking start/stop outside the stable phases, no new run can overlap an old one.

`import` needed: `from contextlib import suppress` (add if absent).

- [ ] **Step 4: Drop the register/unregister imports**

`claude_code/orchestrator.py` imports `register_initial_sync_entry` / `unregister_initial_sync_entry` from `background_compute_task`. Those no longer exist — remove them from the import. Keep `ComputeContext`, `start_background_compute_task`, `stop_background_task`.

- [ ] **Step 5: Compile**

Run: `cd <worktree> && python -m py_compile src/twicc/providers/claude_code/orchestrator.py src/twicc/providers/compute_base.py && echo OK`
Expected: `OK`

---

## Task 9: Codex — same wiring as Claude Code

**Files:**
- Modify: `src/twicc/providers/codex/orchestrator.py`

- [ ] **Step 1: `_initial_sync_task` — shared queue + thread future**

Apply the same transformation as Task 8 Step 2 to `codex/orchestrator.py`'s `_initial_sync_task` (the Codex version has the same shape — `asyncio.to_thread(sync_all, sync_queue, ...)`, marker, done wait). Use `provider=self.provider` (Codex). Add `self._sync_thread_future` to `__init__`.

- [ ] **Step 2: `shutdown()` — block on the real thread end**

Apply the same transformation as Task 8 Step 3 to `codex/orchestrator.py`'s `shutdown()`. As in Task 8 Step 3 this needs `from contextlib import suppress` — `codex/orchestrator.py` does not import it today, so add it.

- [ ] **Step 3: Drop register/unregister imports**

Remove `register_initial_sync_entry` / `unregister_initial_sync_entry` from the `background_compute_task` import in `codex/orchestrator.py`.

- [ ] **Step 4: Compile**

Run: `cd <worktree> && python -m py_compile src/twicc/providers/codex/orchestrator.py && echo OK`
Expected: `OK`

---

## Task 10: Global sanity check + Commit B

**Files:** none (verification only)

- [ ] **Step 1: No stale symbol references anywhere**

Run: `cd <worktree> && grep -rn "register_compute_consumer\|unregister_compute_consumer\|register_initial_sync_entry\|unregister_initial_sync_entry\|_ConsumerEntry\|_InitialSyncEntry\|_unified_consumer_loop\|_ensure_unified_consumer_running" src/ ; echo "---done---"`
Expected: only `---done---` (no match). Any match is a leftover — fix it.

- [ ] **Step 2: No stale `background_task` references**

Run: `cd <worktree> && grep -rn "background_task" src/ ; echo "---done---"`
Expected: only `---done---`.

- [ ] **Step 3: Compile the whole touched set**

Run:
```bash
cd <worktree> && python -m py_compile \
  src/twicc/providers/db_writer.py \
  src/twicc/providers/background_compute_task.py \
  src/twicc/providers/compute_base.py \
  src/twicc/sync_helpers.py \
  src/twicc/providers/claude_code/initial_sync.py \
  src/twicc/providers/claude_code/orchestrator.py \
  src/twicc/providers/codex/initial_sync.py \
  src/twicc/providers/codex/orchestrator.py \
  src/twicc/cli/run.py && echo OK
```
Expected: `OK`

- [ ] **Step 4: Import smoke test**

Run: `cd <worktree> && TWICC_DATA_DIR=$PWD uv run python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','twicc.settings'); django.setup(); from twicc.providers import db_writer, background_compute_task; from twicc.providers.claude_code import initial_sync as ccs, orchestrator as cco; from twicc.providers.codex import initial_sync as cxs, orchestrator as cxo; print('imports OK')"`
Expected: `imports OK` (this proves no circular import and no `AppRegistryNotReady`).

- [ ] **Step 5: Commit B**

```bash
cd <worktree>
git add src/twicc/providers/db_writer.py \
        src/twicc/providers/background_compute_task.py \
        src/twicc/providers/compute_base.py \
        src/twicc/sync_helpers.py \
        src/twicc/providers/claude_code/initial_sync.py \
        src/twicc/providers/claude_code/orchestrator.py \
        src/twicc/providers/codex/initial_sync.py \
        src/twicc/providers/codex/orchestrator.py \
        src/twicc/cli/run.py
git commit -m "$(cat <<'EOF'
refactor(db): replace consumer registry with shared queues + permanent writer

Drop the per-provider register/unregister registry of the unified
consumer. The consumer becomes a permanent task (db_writer.py) owned by
run_server: started before the orchestrators, stopped after them. It
drains two process-wide shared queues — one queue.Queue for initial
sync, one multiprocessing.Queue for compute — created by the writer and
handed to producers. No routing registry: every message carries its own
provider.

Completion is signalled by a sentinel the producer pushes last (FIFO
guarantees every earlier message was applied first):
- initial sync: InitialSyncDoneMarker carries its own asyncio.Event;
- compute: arm_compute_completion() hands the orchestrator an Event the
  writer sets on the provider's 'done' message (mp.Queue cannot carry
  an Event).

This kills the critical shutdown bug: a producer thread that keeps
pushing after a cancel now pushes onto a queue still drained by a
still-alive consumer — no lost writes. To stop a zombie thread from
overlapping a new run, orchestrator shutdown() now blocks on the real
end of the asyncio.to_thread producer (it keeps the thread future and
awaits it), not just on the wrapping coroutine being cancelled.

apply_session_complete is now a @staticmethod (it never used self) so
the writer applies compute writes without per-provider compute
instances. The payload NamedTuples move to db_writer.py — the consumer
owns the queue vocabulary; producers import it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Risks & notes for the implementer

- **Codex review findings #2–#7 are NOT in scope here.** This plan only addresses #1 (shutdown / lost writes) by way of the architecture change. `Project.mtime` regression (#2), swallowed errors (#3), `_sync_titles_at_boot` outside the consumer (#4), queue backpressure (#5), `exists()→open()` race (#6), `register_project_sync` inside atomic (#7) remain open and should be tackled in follow-up commits.
- **`stop_unified_consumer` ordering is load-bearing.** It must run after every orchestrator `shutdown()` has returned. Since `shutdown()` is now blocking on the producer threads, "after shutdown" genuinely means "no producer alive". Get Task 7 Step 2 placement right.
- **The shared `mp.Queue` survives provider stop/restart.** `stop_background_task` must not close it (Task 5 Step 4). Only `command_queue` is per-context and still closed.
- **`_compute_done_events` / `_compute_states` are keyed by provider with no run-id.** Correct only while runs never overlap. The blocking `shutdown()` is what guarantees that — if the `arm_compute_completion` warning ever fires in logs, a lifecycle assumption broke.
- **`asyncio.shield` in `shutdown()`**: shielding the thread future means a second cancel of `shutdown()` itself won't abandon the wait. If `sync_all` ignored `stop_event` forever the shutdown would hang — acceptable, because `sync_all` checks `stop_event` between every project and every session, so the wait is bounded.
- **No tests:** this project ships without a test suite (per CLAUDE.md). Verification is py_compile + the import smoke test + manual restart by the user. Do not add a test scaffold.

---

## Appendix — As-built design (2026-05-21)

> Added after the feature was completed, to resolve the only finding of the
> final code review: *the plan above is stale*. This appendix describes the
> centralisation **as it was actually shipped** on `feature/centralize-db-writes`
> — the net result of the initial refonte plus 16 rounds of review-driven
> hardening (the authoritative round-by-round record is `git log main..HEAD`).
> It supersedes the plan body wherever the two disagree. It is written for a
> reader who needs to understand **how the code works**, not the history of how
> it got there.

### The problem

TwiCC runs on **SQLite in WAL mode**. WAL allows many concurrent readers but
**exactly one writer**. At boot, two providers (Claude Code and Codex) each run
two write-heavy jobs:

1. **Initial sync** — scan the provider's data directory and bulk-insert raw
   `Session` / `SessionItem` rows (thousands of rows at boot). Producer: one
   thread per provider (`asyncio.to_thread`).
2. **Background compute** — compute per-session metadata (`kind`,
   `display_level`, costs, git info, collapsible groups). Producer: one
   `multiprocessing` (spawn) subprocess per provider.

These jobs ran as **parallel DB writers**. The second writer to ask for the
lock gets `SQLITE_BUSY`, and Django retries within `busy_timeout` (30 s). But
SQLite has **no fair waiter queue**: a writer running a tight loop of small
transactions repeatedly re-acquires the lock before a starved writer — asleep
in its `busy_timeout` — is ever woken. At boot, with 1000+ sessions, the
starved writer exhausts its 30 s timeout and the app crashes with **`database
is locked`**. WAL does not fix this; the only robust fix is a **single logical
writer**.

### The solution, in one sentence

A **single permanent consumer** (`src/twicc/providers/db_writer.py`) becomes
the **sole DB writer** for those two big boot jobs. The producers become
**read-only**: they read the DB and parse JSONL, then push **typed payloads**
onto two process-wide shared queues. The consumer drains the queues and
performs **every write from one place**, inside `transaction.atomic`. With one
writer there is no lock to contend for and no starvation.

**Deliberately out of scope:** the live-file **watcher** and the **periodic
tasks** (price sync, usage quotas) keep writing to the DB directly. Their write
volume is marginal — a few rows per JSONL event — so routing them through the
consumer would add complexity for no measurable gain.

### Components and files

| File | Role in the as-built design |
|---|---|
| `src/twicc/providers/db_writer.py` | **The permanent consumer.** Owns the two shared queues, the payload vocabulary (NamedTuples), the consumer loop (`_consumer_loop` / `_drain_one`), every `_apply_*` write handler, and the lifecycle + completion API. |
| `src/twicc/providers/background_compute_task.py` | The **compute producer** only (renamed from `background_task.py`). `ComputeContext`, `compute_worker_main` (subprocess entry), process start/stop. Reads the DB; never writes it. |
| `src/twicc/providers/compute_base.py` | `BaseSessionCompute`: `compute_session_metadata` (worker side, **read**), `apply_session_complete` (now a `@staticmethod @transaction.atomic`, run **by the consumer**, **write**). |
| `src/twicc/providers/{claude_code,codex}/initial_sync.py` | The **initial-sync producers**. Read the DB and parse JSONL; build and push payloads. No DB writes. |
| `src/twicc/providers/{claude_code,codex}/orchestrator.py` | Per-provider task lifecycle. Wire the shared queue into the initial-sync task; arm compute completion; **blocking `shutdown()`**. |
| `src/twicc/orchestrator.py` | `OrchestratorRegistry`: `start_all` / `shutdown_all`, provider hot-toggle, the post-compute search-index kick. |
| `src/twicc/cli/run.py` | `run_server`: starts the consumer **before** the orchestrators, stops it **after** them. |
| `src/twicc/sync_helpers.py` | Producer-side initial-sync helpers (`read_session_items_from_file`, `BackpressureSyncQueue`). |
| `src/twicc/projects.py` | Cross-provider project helpers + process caches (`ensure_project_*`, `register_project*`, `update_project_metadata`). |

### The producer/consumer contract

**Two process-wide shared queues**, both created by `start_unified_consumer()`:

- `_initial_sync_queue` — a `queue.Queue` (intra-process; the initial-sync
  producers are threads). Bounded, `maxsize=200`.
- `_compute_result_queue` — a `multiprocessing.Queue` from the **spawn** context
  (the compute producers are subprocesses). Bounded, `maxsize=200`.

Bounding the queues is what gives **backpressure**: a producer that parses
faster than the consumer commits eventually fills the queue and blocks on
`put` — the backlog is capped instead of growing to gigabytes of RAM (each
initial-sync payload carries a whole session's JSONL lines).

**There is no routing registry.** Every message carries its own `provider`
field; the consumer dispatches on the message *type*, not on a registration.
(The pre-refonte design had a `register`/`unregister` registry — its removal is
what mechanically eliminated the lost-writes-on-shutdown bug.)

The **payload vocabulary** — NamedTuples defined in `db_writer.py` and pushed
onto the initial-sync queue:

| Payload | Meaning |
|---|---|
| `CreateSessionPayload` | A session not yet in DB: create the project (idempotent), save the session, bulk-create its items. |
| `UpdateSessionPayload` | A session already in DB: append new items, advance the tracking offsets, optionally clear `stale` / reset `compute_version`. |
| `MarkSessionsStalePayload` | Flag a set of sessions whose JSONL is gone from disk. |
| `UpdateProjectMetadataPayload` | End-of-sync, one project: apply `new_stale`, recompute `sessions_count` / `mtime` / `total_cost`, resolve `git_root`. |
| `ResolveProjectGitRootsPayload` | End-of-sync: resolve `git_root` for every on-disk project — run **by the consumer** so it sees this run's already-applied updates. |
| `InitialSyncDoneMarker` | The FIFO completion sentinel for an initial-sync run (see below). |
| `SyncSessionTitlesPayload` | Boot-time bulk title import for one provider. |

The **compute side does not use these**: a compute worker emits plain
`orjson`-encoded dicts (a `session_complete` per session, a `done` at the end),
because a `multiprocessing.Queue` would have to pickle anything richer.

The contract is strict: **producers only read and parse; the consumer performs
every write.** Producers never call `.save()` / `bulk_create` — they hand the
data to the consumer in a payload, and the consumer issues every INSERT/UPDATE.

### Lifecycle and the central invariant

**The central invariant: the consumer strictly outlives every producer.**

`run_server` (`cli/run.py`) enforces it by ordering:

1. `start_unified_consumer()` — create the queues, launch the consumer task.
2. `orchestrators.start_all(...)` — only now do the providers (and their
   producers) start. The shared queues already exist for them to wire in.
3. … the application runs …
4. `orchestrators.shutdown_all()` — every provider tears down; each
   `shutdown()` **blocks** until its own producers (initial-sync thread,
   compute subprocess) have really ended.
5. `stop_unified_consumer()` — only now, with no producer left alive, signal
   the consumer to drain whatever remains and stop.

The consumer is never started or stopped by a provider orchestrator. The
invariant holds **across a provider hot-toggle** (a provider disabled, then
re-enabled, at runtime): the toggled provider's producers come and go, but the
consumer they push onto is the same permanent task, alive the whole time.

### Completion: proven by FIFO sentinels

A producer cannot simply "return" — its writes are applied asynchronously by
the consumer. Completion is proven by a **sentinel pushed last**: because each
queue is FIFO, the moment the consumer drains a run's sentinel, **every earlier
message of that run has already been applied.**

**Initial sync.** The producer pushes an `InitialSyncDoneMarker` last. The
marker carries an **`asyncio.Future`** (the initial-sync queue is intra-process,
so the Future travels by reference). The consumer resolves that Future when it
drains the marker, **with the count of payloads that failed to apply** — so the
orchestrator awaiting it learns whether the run was clean and logs a summary if
not. *(The plan specified a bare `asyncio.Event`; the Future-with-failure-count
is the shipped form.)*

**Compute.** The `done` message comes from a subprocess and cannot carry a
Future. Instead, before each run the orchestrator calls
`arm_compute_completion(provider, …)`, which **mints a fresh `run_id`**
(`itertools.count(1)`, so `0` is never a real run) and returns
`(run_id, asyncio.Future)`. The worker is spawned with that `run_id` and **tags
every message with it**. The consumer keys all compute run state by `run_id`:

- `_compute_states[run_id]` — that run's accumulated state (broadcast
  throttling, batched activity-day recomputation, failure tally);
- `_compute_done_events[run_id]` — the completion Future, resolved with the
  run's failed-session count when its `done` message is drained.

Keying by `run_id` rather than by provider is what makes a **hot restart** safe:
a cancelled run's worker can still have stale messages sitting in the shared
queue when the next run is armed; those messages carry the **old** `run_id`,
find no live state, and are ignored — they cannot corrupt the new run.

**Title sync.** `SyncSessionTitlesPayload` carries an `asyncio.Event` the
orchestrator awaits, so the boot-time bulk title import also goes through the
single writer instead of racing the consumer.

### Transaction discipline

The consumer wraps **each payload's writes in one `transaction.atomic` block**.
This coalesces what would be many tiny implicit transactions (one per `save()`)
into a single lock-acquire + `fsync` — a large speedup, **but correct only
because there is a single writer.** With concurrent writers, a longer-held
transaction would *worsen* contention; `atomic` and the single consumer are
inseparable.

**Side effects never fire from inside an uncommitted transaction.** WebSocket
broadcasts, workspace auto-add, and process-cache mutations are deliberately
kept **outside** the atomic block (or deferred with `transaction.on_commit`),
so nothing observes — or caches — a state that a rollback would undo.

### Shutdown, hot-toggle, and abandon-on-shutdown

A provider's `shutdown()` is **blocking and ordered**:

- It **cancels the dependency orchestrator first**, before any `await`, so no
  new compute / watcher / title-sync phase can start mid-teardown.
- It **drains its initial-sync run**: it pushes its own `InitialSyncDoneMarker`
  and awaits it, so every already-queued payload of the run is applied before
  the provider reaches `STOPPED`. This holds even when the run was **cancelled**
  or **crashed** — the exception is captured, the marker is still pushed and
  awaited, and only then is the error re-raised.
- It **abandons the in-flight compute run** rather than applying its partial
  results late.

**`abandon_compute_run`** is a consumer-side finalization path. It (1) flags the
run `abandoned` *synchronously*, before any `await` — from then on the consumer
skips every remaining `session_complete` for that `run_id` — and (2) hands an
`_AbandonComputeRunJob` to the consumer via the intra-process `_consumer_jobs`
queue. The **consumer itself** runs the job (`_finalize_abandoned_run`): it
flushes the run's batched project broadcasts and activity recomputations for the
sessions applied *before* the abandon, then drops the run state. Running the
flush in the consumer keeps the single-writer guarantee; `shutdown()` awaits the
job, so the provider does not reach `STOPPED` — and a hot restart cannot
begin — until that flush has committed. The abandoned run's sessions are simply
recomputed on the next boot.

**The final drain is resilient.** Once `stop_unified_consumer()` signals it, the
consumer keeps draining until both queues are empty; `_drain_one` is *total* —
an exception applying one message is logged and swallowed, never propagated — so
one bad message can never strand the messages queued behind it.

### Cross-provider fields are recomputed by the consumer

`Project.mtime` and `Project.sessions_count` are **shared by both providers**.
A producer must never carry them as a precomputed value: a number computed
early in one provider's producer could clobber — or be clobbered by — a fresher
value from the other provider, and reading the current value in a producer
races the consumer (which has not yet applied this run's session payloads).

So the consumer **recomputes them from the DB**, inside the same transaction
that applies the payload (the `recalc_sessions_count` / `recalc_mtime` flags on
`UpdateProjectMetadataPayload`). `mtime` aggregates only **visible, non-`stale`**
sessions; `sessions_count` includes `stale` ones. The same filter rule is
applied everywhere those two values are written — the consumer paths *and* the
watcher/compute path (`update_project_metadata` in `projects.py`) — so the two
never drift.

### Key divergences from the plan above

| The plan said | The shipped code does | Why |
|---|---|---|
| Initial-sync completion via a bare `asyncio.Event`. | `InitialSyncDoneMarker` carries an `asyncio.Future` resolved with the run's **failure count**. | A failed run must not signal "completed" silently; the orchestrator needs to log a summary. |
| Compute completion via `dict[Provider, Event]`, armed per run. | State and completion keyed by a per-run **`run_id`** (`dict[int, …]`). | A cancelled run's stale messages must not touch a hot-restarted run for the same provider. |
| No mechanism named for an interrupted compute run. | `abandon_compute_run` + `_AbandonComputeRunJob` + `_finalize_abandoned_run` — a consumer-side finalization path. | A shut-down provider's partial compute results must be flushed (not lost, not applied late) by the single writer. |
| `shutdown()` awaits the producer thread; then `stop_unified_consumer` runs. | Same — **plus** each `shutdown()` pushes and awaits its own drain marker (cancel and crash paths included). | Awaiting the thread is not enough: the run's already-queued payloads must also be *drained* before `STOPPED`. |
| Findings #2–#7 of the first review were "follow-up, out of scope". | All addressed, plus 14 further review rounds. | The branch was hardened all the way to a clean review verdict. |

`git log --oneline main..HEAD` is the authoritative, commit-by-commit record of
how the design reached this state.

### Verification and status

- The existing test suite — **339 tests** — passes against the branch
  (`uv run --extra test pytest`; `settings_test.py` forces in-memory SQLite).
- The branch went through **14 round-by-round Codex reviews** plus **3 holistic
  "fresh reviewer" reviews**. The final holistic verdict was
  **"architecturally sound and mergeable"**, with **no code-level finding** —
  the only item raised was that *this plan document* had gone stale, which the
  banner at the top and this appendix resolve.
- Remaining before merge (deliberate steps, not code fixes): rebase on local
  `main`, a real runtime boot / shutdown / hot-toggle test, then merge.
