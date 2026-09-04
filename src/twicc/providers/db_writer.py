"""
DB writer.

A single permanent coroutine that owns every database write coming from the
two boot-time "big jobs": the per-provider initial sync (producers run in
``asyncio.to_thread`` threads) and the background metadata compute (producers
run in ``multiprocessing`` subprocesses). Both push onto process-wide shared
queues; this DB writer drains them and applies every write inside
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
every preceding message of that run has been applied. The completion carries
the run's failure count so the producer learns the run was not clean.
- Initial sync: ``InitialSyncDoneMarker`` carries its own ``asyncio.Future``
  (the queue is intra-process, the Future travels by reference); the DB writer
  resolves it with the count of payloads that failed to apply.
- Compute: the ``done`` message comes from a subprocess and cannot carry a
  Future, so ``arm_compute_completion`` hands the orchestrator a fresh Future
  kept in ``_compute_done_events``, resolved with the failed-session count
  when the ``done`` is drained.

Import discipline: this module is never imported by the spawn subprocess.
Django model imports are still done lazily inside functions, matching the
convention of ``background_compute_task.py``.
"""

from __future__ import annotations

import asyncio
import contextvars
import itertools
import logging
import multiprocessing
import queue
import threading
from collections import defaultdict
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date as date_cls
from typing import Any, Literal, NamedTuple, TypeVar

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import transaction

from twicc.core.enums import Provider
from twicc.logging_context import provider_log_context
from twicc.startup_progress import broadcast_startup_progress
from twicc.workspaces import auto_add_project_to_workspaces

logger = logging.getLogger(__name__)

# Throttle: broadcast project_updated every N normal sessions during compute.
PROJECT_BROADCAST_INTERVAL = 5
# Batch size for activity recalculation flushes (per provider).
BATCH_ACTIVITY_COUNT = 50

# Bounded-queue capacities. Both boot-time producers (initial-sync threads,
# compute subprocesses) can parse far faster than the DB writer commits to
# SQLite. An unbounded queue would let the backlog — each initial-sync payload
# carrying a whole session's JSONL lines — grow to gigabytes of RAM. A bounded
# queue makes a full queue block the producer instead: the backlog is capped,
# and the producer is throttled to the DB writer's write rate.
INITIAL_SYNC_QUEUE_MAXSIZE = 200
COMPUTE_QUEUE_MAXSIZE = 200
# A compute result carrying more item writes than this is applied in slices
# of this size, each in its own ``sync_to_async`` call and transaction, so a
# giant session (hundreds of thousands of items) cannot hold the shared
# thread-sensitive executor — and with it every REST view, WebSocket
# connect and watcher write of the process — for minutes.
COMPUTE_APPLY_CHUNK_SIZE = 2000

# "spawn" context — the compute result queue is created here and passed to
# the spawn workers, so it must come from the same context they use.
_mp_ctx = multiprocessing.get_context("spawn")


# =============================================================================
# Queue payload vocabulary
# =============================================================================
#
# The DB writer owns this vocabulary: every type below has a handler in
# _process_thread_message. Initial-sync producers
# (providers/<p>/initial_sync.py) import these and push them; they only ever
# use the subset that applies to them. The compute side does NOT use these —
# its messages are plain orjson-encoded dicts produced by the subprocess.


class CreateSessionPayload(NamedTuple):
    """Producer parsed a JSONL file for a session not yet in DB.

    The DB writer creates the ``Project`` (idempotent — no-op if it exists),
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

    The DB writer appends the new items (``ignore_conflicts=True`` covers the
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

    The DB writer issues one ``Session.objects.filter(id__in=..., stale=False).update(stale=True)``.
    """

    provider: Provider
    session_ids: list[str]


class DeleteSessionsPayload(NamedTuple):
    """Producer identified internal rollouts that must not exist in TwiCC.

    The DB writer deletes the sessions and their cascaded data, then refreshes
    the affected project and activity aggregates. Providers use this only for
    exact, provider-owned classifications — never for missing user files,
    which continue to use :class:`MarkSessionsStalePayload`.
    """

    provider: Provider
    session_ids: list[str]


class UpdateProjectMetadataPayload(NamedTuple):
    """End-of-sync metadata for one project.

    ``new_stale`` is applied when non-``None``. Four boolean flags ask the
    DB writer to do work that reads back from the DB: ``recalc_sessions_count``
    and ``recalc_mtime`` recompute those ``Project`` fields from the project's
    sessions; ``recalc_total_cost`` and ``resolve_git_root`` invoke heavier
    helpers that themselves write to DB. Bundled so the project's end-of-sync
    writes commit in one transaction.

    ``recalc_sessions_count`` / ``recalc_mtime`` exist because neither value
    can be computed in the producer: both are cross-provider, so a value
    counted in one provider's producer could clobber — or be clobbered by — a
    fresher value another provider's compute writes; reading them from the DB
    in the producer also races the DB writer, which has not yet applied this
    run's session payloads. The DB writer recomputes them instead, inside the
    same transaction that applies the payload.
    """

    provider: Provider
    project_id: str
    recalc_sessions_count: bool
    recalc_mtime: bool
    new_stale: bool | None
    recalc_total_cost: bool
    resolve_git_root: bool
    git_root_directory: str | None


class ResolveProjectGitRootsPayload(NamedTuple):
    """End-of-sync request to resolve git_root for every project on disk.

    A provider enqueues this once, last in its run, instead of running
    ``Project.objects.filter(directory__isnull=False, stale=False)`` itself
    and pushing one payload per project: that producer-side query runs
    before the DB writer has applied this run's earlier project payloads
    (FIFO), so a project being un-staled in the same sync would still look
    stale and be skipped. The DB writer runs the query when it drains this
    marker — after every prior project update of the run has committed.
    """

    provider: Provider


class UpsertWorkflowPayload(NamedTuple):
    """Producer found a ``wf_*.json`` run file; persist it as a Workflow row.

    The DB writer issues one ``Workflow.objects.update_or_create`` keyed on
    ``run_id``. ``raw_json`` is the file content verbatim (already validated as
    JSON by the producer). No-op when the owning session row does not exist
    (an upstream session payload was rejected, or the file is an orphan).
    """

    provider: Provider
    session_id: str
    run_id: str
    raw_json: str


class InitialSyncDoneMarker(NamedTuple):
    """Sentinel pushed last by an initial-sync producer.

    Carries an ``asyncio.Future`` the DB writer resolves once it drains the
    marker — i.e. once every payload of that run has been applied. The Future
    resolves to the count of payloads that failed to apply, so the producer
    (which awaits it) learns whether the run was clean.
    """

    provider: Provider
    done_future: asyncio.Future


class ComputeApplied(NamedTuple):
    """Per-session result emitted after the DB writer handles compute output."""

    session_id: str
    outcome: Literal["applied", "superseded", "missing", "failed"]
    error: str | None = None


# =============================================================================
# Module state
# =============================================================================

# The two process-wide shared queues, created by start_db_writer().
_thread_queue: queue.Queue | None = None
_subprocess_queue: object | None = None  # _mp_ctx.Queue()

# The permanent DB writer task and its stop signal.
_db_writer_task: asyncio.Task | None = None
_db_writer_stop_event: asyncio.Event | None = None

# Intra-process queue of finalization jobs the DB writer must run itself, so
# their writes are serialized with every other DB writer write — the
# single-writer guarantee. abandon_compute_run() runs in a provider's
# shutdown() task, not in the DB writer task, and uses this to have the
# DB writer flush an abandoned compute run's batched state instead of writing
# to the DB directly and racing the DB writer.
_async_queue: asyncio.Queue | None = None

# Shared write lock — serializes every SQLite-writing critical section that
# happens in the main process. The DB writer itself acquires it via
# :func:`_run_under_db_write_lock` (the internal version, which bypasses the
# public stop-event guard so the final shutdown drain can apply messages
# queued before the stop) around each of the three queue branches in
# ``_drain_one``. External callers (the JSONL watcher, agent lifecycle hooks,
# the cron expiry monitor, the Codex title flush — every direct writer R19
# left in place) will eventually adopt either :func:`run_under_db_write_lock`
# (cancellation-safe, the recommended path for anything that awaits
# ``sync_to_async``) or, for short critical sections that do not await
# orphanable executor work, the raw lock via :func:`get_db_write_lock`.
# The point is to move the contention from SQLite's busy_timeout (no fairness,
# 30 s grace period, raises "database is locked" on loss) into asyncio's
# FIFO lock (fair, no timeout, never raises). ``None`` outside of an active
# DB writer lifetime so a stray ``get_db_write_lock`` call surfaces a clear
# RuntimeError instead of silently handing out a stale Lock.
_db_write_lock: asyncio.Lock | None = None

# Reentrance marker for the lock runners. ``asyncio.Lock`` is NOT reentrant:
# a second ``acquire()`` from the same Task self-deadlocks. The runners
# (:func:`run_under_db_write_lock` / :func:`_run_under_db_write_lock`) install
# a :class:`_LockLease` in this ``ContextVar`` inside their ``async with``
# block, then call the user's coroutine factory through
# :func:`_drive_inner_under_held_lock`. A nested ``run_under_db_write_lock``
# call (typical when an outer caller wraps a function that itself wraps
# another function under the lock) is granted the short-circuit only if
# its ``current_task()`` is registered in ``lease.drive_tasks`` — i.e. it
# is the Task that installed the lease, OR the single inner Task created
# by :func:`_drive_inner_under_held_lock` for this hold. (Nested
# ``run_under_db_write_lock`` calls short-circuit on the existing
# membership rather than entering a fresh drive, so no second inner Task
# is created during the same hold.) Anything else (most importantly:
# sub-tasks spawned via ``asyncio.create_task`` from inside a factory)
# is excluded from the short-circuit and falls through to a real
# acquire.
#
# **Why the ``drive_tasks`` set rather than a plain ``active`` flag.**
# A sub-task spawned from inside the factory via ``asyncio.create_task``
# copies the parent's :class:`contextvars.Context` at creation time and
# therefore inherits the lease object by reference. A bool-only or
# ``active``-only check would let such a sub-task see the lease, skip
# its acquire, and run concurrently with the outer factory — either in
# parallel while the outer still holds the lock, or worse, continuing
# past the outer's release and writing without any lock held at all (the
# v3 design caught only the strict "starts entirely after release" case;
# the "started during hold, continued past release" case still slipped
# through). Restricting the short-circuit to ``current_task() in
# lease.drive_tasks`` forces every inherited sub-task to take its own
# slot in the lock's FIFO, regardless of when it runs relative to the
# outer's release.
#
# ``ContextVar`` is still needed because :func:`_drive_inner_under_held_lock`
# runs the factory inside an inner Task it creates with
# ``asyncio.create_task``: the inner Task must see the lease for the
# reentrance check to find it. The drive wraps the user factory in a tiny
# coroutine that admits ``current_task()`` (i.e. the inner Task itself)
# to ``lease.drive_tasks`` BEFORE any user code runs. Under the default
# task factory the wrapped body would start at the next scheduler tick
# anyway; under :func:`asyncio.eager_task_factory` (Python 3.12+) the
# wrapped body runs synchronously at ``create_task`` time — the admit
# happens inside the inner Task's own frame, so a nested
# ``run_under_db_write_lock`` made immediately by the user's factory
# already sees ``current_task() in lease.drive_tasks`` and short-circuits
# correctly. Either mode: the inner is admitted before it can hit a
# nested call.
#
# **Consequence for fire-and-forget sub-tasks under lock.** A sub-task
# spawned naively from inside a factory will block on its own acquire
# attempt because the outer still holds the lock. If the factory does
# not ``await`` it, the sub will queue FIFO, acquire normally once the
# outer releases, and write under its own slot. If the factory ``await``s
# the returned Task handle from within the locked block, the result is
# a deadlock (sub waits for the lock; the lock-holder waits for sub).
# The deadlock cannot be broken by an outer ``asyncio.wait_for`` or a
# task cancel: :func:`_drive_inner_under_held_lock`'s shield-loop
# deliberately waits for the inner Task to finish before yielding to
# a cancellation, so once the deadlock takes hold no top-level
# timeout can release the lock. This is strictly safer than the v3
# design's silent lockless write (the misuse is loud, not invisible),
# but the caller must avoid it by construction. The safer pattern for
# a "nested step" is to ``await`` the coroutine directly (no
# ``create_task``) — it becomes a transparent reentrant call. The
# helper :func:`spawn_isolated_db_write_task` exists only to document
# "this sub-task takes its own slot in the FIFO"; its returned Task
# handle has the SAME constraint (await it only AFTER the locked
# factory returns).


class _LockLease:
    """Per-hold marker installed in :data:`_db_write_lock_lease`.

    ``active`` is flipped to ``False`` in the runner's ``finally``.
    Combined with ``drive_tasks`` it forms the membership-test the
    runners apply on every entry to decide whether to short-circuit.

    ``drive_tasks`` is the set of asyncio Tasks that are allowed to
    reuse this hold without re-acquiring the lock:

    - The Task that installed the lease (the runner's caller), added
      synchronously by the runner before entering the drive.
    - The single inner Task the drive creates for this hold. The
      drive wraps the user factory in a tiny coroutine that admits
      ``asyncio.current_task()`` (= the inner Task) to ``drive_tasks``
      as its FIRST action, before any user code runs. This is
      compatible with both the default task factory and
      :func:`asyncio.eager_task_factory`. Nested
      ``run_under_db_write_lock`` calls reuse this admitted inner
      Task — they short-circuit on the existing membership rather
      than entering a fresh drive, so no second inner Task is
      created during the same hold.

    Sub-tasks the factory itself spawns via ``asyncio.create_task``
    inherit the lease object (by ContextVar copy) but are NOT added
    to ``drive_tasks``: their reentrance check therefore fails and
    they fall through to a real acquire.
    """

    __slots__ = ("active", "drive_tasks")

    def __init__(self) -> None:
        self.active = True
        # Identity-keyed: asyncio.Task is hashable by id. Mutation is
        # safe because asyncio is single-threaded — only the runner /
        # drive write here, and only the runners read.
        self.drive_tasks: set[asyncio.Task] = set()


_db_write_lock_lease: contextvars.ContextVar[_LockLease | None] = contextvars.ContextVar(
    "twicc.providers.db_writer._db_write_lock_lease",
    default=None,
)


def _lease_admits_current() -> bool:
    """Return True if the current Task may reuse the active lease.

    Encapsulates the membership-test the runners apply on entry:
    the lease must exist, be ``active``, and the current Task must
    be in ``lease.drive_tasks``. Used by both runners so the rule
    is defined in exactly one place.
    """
    lease = _db_write_lock_lease.get()
    if lease is None or not lease.active:
        return False
    current = asyncio.current_task()
    return current is not None and current in lease.drive_tasks

# Compute completion is keyed by a per-run id, not by provider: a cancelled
# run's worker can still have stale messages in the shared queue when a
# hot-restarted run is armed, and the run_id keeps each run's state and
# completion Future isolated. arm_compute_completion() mints the ids.
_compute_run_id_seq = itertools.count(1)

# run_id -> the completion Future the orchestrator awaits. The mp.Queue cannot
# transport a Future, so arm_compute_completion() keeps one here and the
# DB writer resolves it (with the run's failed-session count) when it drains
# that run's 'done' message.
_compute_done_events: dict[int, asyncio.Future] = {}

# run_id -> that run's accumulated compute state (broadcast throttling,
# batched activity flushes, failure tally). Initial-sync payloads are
# self-contained; the only initial-sync run state is the failure tally below.
_compute_states: dict[int, _ComputeProviderState] = {}

# Per-provider failed-payload counter for the in-flight initial-sync run.
# Incremented when an _apply_* raises; reported to the orchestrator via the
# InitialSyncDoneMarker's Future and reset when that marker is drained.
_initial_sync_failures: dict[Provider, int] = defaultdict(int)

# Per-provider count of subagents skipped because their parent row was absent
# (orphan-parent guard). A deliberate skip, distinct from a failure, but
# surfaced in the run summary so it is never silently lost.
_initial_sync_orphan_skips: dict[Provider, int] = defaultdict(int)


@dataclass
class _ComputeProviderState:
    """One compute run's accumulated state, created by arm_compute_completion().

    Keyed in ``_compute_states`` by ``run_id``; ``provider`` is kept for
    logging and so arm_compute_completion() can drop a previous run's leftover
    state for the same provider.

    ``abandoned`` is set by :func:`abandon_compute_run` when a provider shuts
    down: once set, the DB writer skips every remaining ``session_complete`` for
    the run, and the run's batched state is flushed and dropped by the
    :class:`_AbandonComputeRunJob` the DB writer runs.
    """

    provider: Provider
    run_id: int
    display_session_ids: set[str] | None
    total_display: int
    pending_activity_days: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    pending_project_ids: set[str] = field(default_factory=set)
    auto_added_project_ids: set[str] = field(default_factory=set)
    sessions_since_project_broadcast: int = 0
    sessions_since_activities_flush: int = 0
    completed_count: int = 0
    failed_count: int = 0
    abandoned: bool = False
    applied_queue: asyncio.Queue | None = None


class _AbandonComputeRunJob(NamedTuple):
    """A request for the DB writer to finalize an abandoned compute run.

    Pushed onto ``_async_queue`` by :func:`abandon_compute_run` (which runs
    in a provider's ``shutdown()`` task) and executed by the DB writer task
    itself, so the run's batched activity-recalculation flush is serialized
    with every other DB writer write. ``done`` is resolved once the DB writer
    has finalized and dropped the run, letting ``shutdown()`` move on.
    """

    run_id: int
    provider: Provider
    done: asyncio.Future


# -----------------------------------------------------------------------------
# Generic DB writer jobs (periodic tasks routed through the DB writer)
# -----------------------------------------------------------------------------
#
# These are pushed onto ``_async_queue`` by an async producer (a periodic
# task: commands sync, usage sync, model retirement, pricing) via
# :func:`submit_async_job`. The producer prepares its payload — HTTP fetch,
# filesystem scan, parsing — entirely outside the DB writer; only the actual DB
# write (and, for commands, the SELECT-current diff that precedes it) runs in
# the DB writer, in one ``transaction.atomic``. The job carries an
# ``asyncio.Future`` the DB writer settles with the result (or the exception),
# so the producer awaits the apply just like any other DB-writer-routed work.
#
# Boot-time motivation: the periodic tasks' *first* iteration fires immediately
# when their provider's orchestrator starts, in parallel with the initial-sync
# producer the DB writer is already draining. Two concurrent connections then
# race for SQLite's WAL write lock and trip "database is locked" errors. By
# routing them through the DB writer we restore the single-writer guarantee.


class _ApplyDesiredCommandsJob(NamedTuple):
    """A request to reconcile ``Command`` rows for one ``(provider, activation_char)`` scope.

    The producer (each provider's ``commands_task``) builds ``desired`` from
    its own discovery source — filesystem scan for Claude Code, ``skills/list``
    JSON-RPC for Codex — and submits this job. The DB writer reads the current
    rows from DB, computes the diff, and applies create/update/delete in one
    ``transaction.atomic``. ``future`` resolves to the same stats dict the old
    ``apply_desired_commands`` helper returned.
    """

    provider: Provider
    activation_char: str
    desired: dict[tuple[str | None, str], dict]
    future: asyncio.Future  # → dict[str, int] with keys created/updated/deleted/unchanged


class _CreateUsageSnapshotJob(NamedTuple):
    """A request to insert a new ``UsageSnapshot`` row.

    The producer (each provider's ``usage_task``) calls its ``fetch_usage``,
    parses the API response into the ``UsageSnapshot`` column fields, and
    submits this job. The DB writer issues one ``UsageSnapshot.objects.create``.
    ``future`` resolves to the created ``UsageSnapshot`` instance so the
    producer can log its values.
    """

    provider: Provider
    fields: dict
    future: asyncio.Future  # → UsageSnapshot


class _RetireSessionsJob(NamedTuple):
    """A request to apply per-session retirement-driven field updates.

    The producer (``model_retirement_task``) iterates the active agent
    managers, decides which sessions need their ``selected_model`` (and
    possibly ``effort``) upgraded, and submits the resulting
    ``{session_id: field_updates}`` map. The DB writer applies them in one
    ``transaction.atomic`` via ``Session.objects.filter(id=sid).update(**upd)``
    per session. ``future`` resolves to the number of sessions touched.
    """

    provider: Provider
    updates: dict[str, dict[str, object]]
    future: asyncio.Future  # → int (count of sessions updated)


class _PersistProviderPricesJob(NamedTuple):
    """A request to upsert OpenRouter prices for one provider.

    The producer (``pricing_task``) calls ``fetch_openrouter_models`` and
    ``extract_provider_prices`` (HTTP + parse, no DB), then submits this job.
    The DB writer applies the diff in one ``transaction.atomic``: for each row,
    SELECT the latest, INSERT a new history row only if anything actually
    changed. Invalidates the in-process price cache when at least one row was
    created. ``future`` resolves to a ``{"created": N, "unchanged": M}`` dict.
    """

    provider: Provider
    prices: list[dict]
    future: asyncio.Future  # → dict[str, int] with keys created/unchanged


class _PersistModelBenchmarksJob(NamedTuple):
    """A request to upsert DeepSWE model benchmarks (cross-provider).

    The producer (``benchmarks_task``) fetches the DeepSWE leaderboard and maps
    it to our supported models (HTTP + parse + registry lookup, no DB), then
    submits this job. The DB writer applies the upsert in one
    ``transaction.atomic``: for each ``(provider, model, reasoning_effort)``,
    update the metrics + ``updated_at`` or insert a new row; rows absent from
    the fresh data are left untouched. ``provider`` is ``None`` because one
    fetch spans every backend. ``future`` resolves to a
    ``{"created": N, "updated": M}`` dict.
    """

    benchmarks: list[dict]
    future: asyncio.Future  # → dict[str, int] with keys created/updated
    provider: Provider | None = None


class _MarkSessionsIndexedJob(NamedTuple):
    """A request to bump ``search_version`` on a batch of just-indexed sessions.

    Cross-provider job: the producer (``search_indexing_task``) sweeps every
    session needing indexing regardless of owning provider, in ``-mtime``
    order, and accumulates session ids into batches of up to 50 (or up to 2 s
    worth of slower indexing) before submitting one job for the batch. The
    DB writer applies a single
    ``Session.objects.filter(id__in=...).update(search_version=...)`` per job,
    centralizing what used to be one direct ``session.save()`` per indexed
    session — the producer was the last non-watcher, non-HTTP/WS writer left
    in the runtime loop. ``provider`` is ``None`` because the batch mixes
    sessions of every backend.
    """

    session_ids: list[str]
    future: asyncio.Future  # → int (count of sessions actually updated)
    provider: Provider | None = None


# =============================================================================
# Lifecycle + completion API (called by run_server and the orchestrators)
# =============================================================================


def start_db_writer() -> None:
    """Create the shared queues and launch the permanent DB writer task.

    Called once by ``run_server`` at boot, before any provider orchestrator
    starts. Raises if called twice (programming error).
    """
    global _thread_queue, _subprocess_queue
    global _db_writer_stop_event, _db_writer_task, _async_queue
    global _db_write_lock

    if _db_writer_task is not None:
        raise RuntimeError("DB writer already started")

    _thread_queue = queue.Queue(maxsize=INITIAL_SYNC_QUEUE_MAXSIZE)
    _subprocess_queue = _mp_ctx.Queue(maxsize=COMPUTE_QUEUE_MAXSIZE)
    _async_queue = asyncio.Queue()
    _db_writer_stop_event = asyncio.Event()
    # Created here, not at module level. ``asyncio.Lock`` itself binds to the
    # running loop lazily (on first contended acquire) in 3.11+, so any timing
    # would technically work for binding alone, but instantiating it inside
    # ``start_db_writer`` co-locates the lock's lifetime with the writer
    # task's: "DB writer running ⇔ lock available" is obvious from the
    # lifecycle code, and a stop properly clears the slot (see
    # ``stop_db_writer``).
    _db_write_lock = asyncio.Lock()
    _db_writer_task = asyncio.create_task(_db_writer_loop())
    logger.info("DB writer started")


async def stop_db_writer() -> None:
    """Signal the DB writer to stop, await it, then reset the lifecycle bundle.

    Idempotent (no-op on a second call once everything has been reset).
    Must run after every provider orchestrator has shut down, so no
    producer is still pushing.

    Multi-cancel safe: the writer task's final drain (the
    ``while True: await _drain_one()`` loop inside :func:`_db_writer_loop`
    after the stop event fires) is what guarantees no queued message is
    abandoned, so we MUST wait for the task to actually finish — even if
    our own coroutine is cancelled mid-await. A naive
    ``await _db_writer_task`` would let asyncio propagate our cancellation
    into the writer task via ``Task._fut_waiter``, interrupting the final
    drain and contradicting the "lock cleared ⇒ drain complete" contract.

    The shielded while-loop is identical in spirit to
    :func:`run_under_db_write_lock`: every outer cancel raises
    ``CancelledError`` on a fresh ``await asyncio.shield(_db_writer_task)``,
    we remember it, and re-enter — only exiting once
    ``_db_writer_task.done()`` is True. Then we clear the entire lifecycle
    bundle (lock + task + stop event + queues) and re-raise
    ``CancelledError`` if we were cancelled, so the rest of the shutdown
    path proceeds.

    Clearing **all** of the lifecycle globals (not just the lock) lets a
    same-process restart of the DB writer work, and turns any post-stop
    call to a queue API into a clean :class:`RuntimeError` rather than
    a hand-out of a stale (and now drained) queue.

    Before clearing the globals, we also drain in-flight external lock
    holders by briefly acquiring/releasing the lock. That closes the
    TOCTOU window where an external caller passed the public
    ``run_under_db_write_lock``'s pre-acquire stop check, joined the
    FIFO acquire queue, and would otherwise still be holding the lock
    when we ``= None`` it — leaving them serializing on an orphan lock
    that nothing else references.
    """
    global _db_write_lock, _db_writer_task, _db_writer_stop_event
    global _thread_queue, _subprocess_queue, _async_queue

    if _db_writer_stop_event is not None:
        _db_writer_stop_event.set()
    cancelled = False
    try:
        if _db_writer_task is not None:
            # Wait for the writer task to finish its final drain.
            cancelled |= await _shielded_shutdown_wait(
                _db_writer_task,
                external_cancel_log=lambda: logger.error(
                    "DB writer task was cancelled externally during shutdown "
                    "— final drain may be incomplete; some queued messages "
                    "may not have been applied"
                ),
                unexpected_exception_log=lambda exc: logger.error(
                    "DB writer task exited with an unexpected exception: %s",
                    exc,
                    exc_info=exc,
                ),
            )
        # Drain in-flight external lock holders. The writer task is done by
        # this point, so it has released its last apply's hold on the lock;
        # the only remaining holders/waiters are external callers that
        # passed ``run_under_db_write_lock``'s pre-check before we set
        # the stop event. ``acquire()`` blocks until every prior holder
        # in the FIFO queue has released; we immediately release so the
        # lock is quiescent. Shielded against our own cancellation so we
        # don't bail mid-drain and leave the next ``_db_write_lock = None``
        # racing an active holder.
        #
        # ONE drain Task created up front, shield-looped until ``.done()``.
        # A naive ``while True: shield(_acquire_then_release(lock))`` would
        # create a fresh coroutine on every cancel retry, queueing multiple
        # orphan acquirers in the FIFO and leaving earlier
        # ``_acquire_then_release`` Tasks unobserved.
        if _db_write_lock is not None:
            drain_task = asyncio.create_task(_acquire_then_release(_db_write_lock))
            cancelled |= await _shielded_shutdown_wait(
                drain_task,
                external_cancel_log=lambda: logger.warning(
                    "Lock holder-drain task was cancelled externally during "
                    "shutdown — in-flight external lock holders may not have "
                    "fully released before the lifecycle bundle is cleared"
                ),
                unexpected_exception_log=lambda exc: logger.error(
                    "DB write-lock drain at shutdown raised: %s",
                    exc,
                    exc_info=exc,
                ),
            )
    finally:
        # Reset the entire lifecycle bundle once the writer task is done
        # AND in-flight external lock holders have released. Same-process
        # restart (``start_db_writer`` after ``stop_db_writer``) then
        # works, and any stray post-stop queue-API call surfaces a clean
        # ``RuntimeError("DB writer not started")`` instead of returning
        # a drained queue or handing out an orphan lock.
        _db_write_lock = None
        _db_writer_task = None
        _db_writer_stop_event = None
        _thread_queue = None
        _subprocess_queue = None
        _async_queue = None
    if cancelled:
        # Our caller wanted us cancelled; honour that AFTER the writer task
        # has actually drained and we've cleared module state.
        raise asyncio.CancelledError()
    logger.info("DB writer stopped")


async def _acquire_then_release(lock: asyncio.Lock) -> None:
    """Acquire ``lock``, release it. Used by :func:`stop_db_writer` to wait
    for in-flight external lock holders to release before lifecycle teardown."""
    await lock.acquire()
    lock.release()


async def _shielded_shutdown_wait(
    task: asyncio.Task[Any],
    *,
    external_cancel_log: Callable[[], None],
    unexpected_exception_log: Callable[[Exception], None],
) -> bool:
    """Wait for a shutdown-phase Task to finish, cancellation-safe.

    Shared body of :func:`stop_db_writer`'s two shielded loops (writer-task
    final-drain wait and lock holder-drain wait). Returns ``True`` if our
    outer task was cancelled during the wait; the caller is expected to
    propagate that signal via ``raise asyncio.CancelledError()`` AFTER
    its lifecycle teardown.

    Invariants:

    1. **Outer cancels never propagate into the shielded task.**
       :func:`asyncio.shield` keeps ``task`` alive when our own task is
       cancelled. The while-loop keeps re-awaiting until ``task.done()``
       is True; an outer ``cancel()`` raises ``CancelledError`` on the
       await, we catch it, set the return flag, and continue waiting.
    2. **External cancellation of the shielded task is logged.** Two
       paths can raise ``CancelledError`` from the await: OUR task got
       ``cancel()``d (``asyncio.current_task().cancelling() > 0``), OR
       ``task`` itself was cancelled directly (``cancelling() == 0``).
       The distinction matters because direct cancellation of ``task``
       is an anomaly worth surfacing (writer's final drain may be
       incomplete; lock holders may not have released), but it should
       not contaminate the outer cancellation signal we return.
    3. **The post-loop ``result()`` block fills the gaps the in-loop
       discrimination misses.** Two missed-cancel scenarios:

       - ``task`` was already cancelled BEFORE the loop ran; the
         ``while not task.done()`` check exits without any iteration.
       - An outer cancel arrived too, so the in-loop discrimination
         classified the cancel as outer; ``external_cancel_log`` was
         skipped.

       The post-loop ``except CancelledError`` calls ``external_cancel_log``
       if the in-loop branch hasn't already.
    4. **Other exceptions from ``task`` surface via the post-loop
       ``result()`` block.** The in-loop ``except Exception: pass``
       swallows so the loop exits naturally on ``task.done()``; the
       post-loop ``except Exception`` then calls
       ``unexpected_exception_log(exc)`` once. ``BaseException``
       (excluding ``CancelledError`` / ``Exception``) is deliberately
       not suppressed — ``KeyboardInterrupt`` / ``SystemExit`` propagate.

    Callers provide the two log callbacks so the helper stays
    message-agnostic; the two existing call sites (writer-task wait and
    drain-task wait) emit different wording and different levels
    (error vs warning) — both follow the same shape.
    """
    cancelled = False
    external_cancel_logged = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 0:
                cancelled = True
                # Loop and keep waiting for the shielded task.
            else:
                # Shielded task was cancelled externally — log the anomaly.
                # Don't flip ``cancelled`` (we weren't cancelled, no need
                # to re-raise CancelledError at our caller).
                external_cancel_log()
                external_cancel_logged = True
        except Exception:
            # Shielded task raised an Exception. ``task.done()`` is now
            # True, so the loop will exit naturally on its next ``while``
            # check; the post-loop ``result()`` block handles the
            # logging once (avoids double-handling).
            pass
    # Task is done — retrieve its outcome so asyncio doesn't log "exception
    # was never retrieved". (Exception, CancelledError) are narrowed
    # rather than ``BaseException`` so KeyboardInterrupt / SystemExit
    # still propagate.
    try:
        task.result()
    except asyncio.CancelledError:
        if not external_cancel_logged:
            external_cancel_log()
    except Exception as exc:
        unexpected_exception_log(exc)
    return cancelled


def get_db_write_lock() -> asyncio.Lock:
    """Return the shared write lock for SQLite serialization.

    .. warning::

       **Most callers should use :func:`run_under_db_write_lock` instead.**
       The naive pattern ``async with get_db_write_lock(): await
       sync_to_async(fn)(...)`` is cancellation-unsafe: if the caller is
       cancelled while waiting for the executor thread, ``__aexit__``
       releases the lock the instant the await unwinds — even though the
       worker thread is still inside ``transaction.atomic`` — letting
       the next writer acquire and run a concurrent SQLite UPDATE.
       :func:`run_under_db_write_lock` wraps the apply in a shielded
       inner Task and waits across N outer cancellations, so the lock
       stays held until the worker thread has actually returned.

       Direct ``async with get_db_write_lock()`` is only safe when the
       critical section does NOT ``await`` anything that could be
       orphaned by cancellation (no ``sync_to_async``, no executor
       call, no nested ``await`` on a third-party Future). For
       everything else, prefer the runner.

    The lock is the single FIFO serialisation point shared with every
    other main-process SQLite writer — current (the DB writer's own
    apply branches inside :func:`_drain_one`) AND future (the JSONL
    watcher, agent lifecycle hooks, the cron expiry monitor, the
    Codex title flush, the ``DropRequestsWatcher`` — every path
    R19 documented as still bypassing the writer queue). The point
    is to move contention from SQLite's ``busy_timeout`` (no
    fairness, gives up after 30 s, raises ``database is locked``)
    into asyncio's FIFO ``Lock`` (fair, never raises).

    External wiring (watcher etc.) is intentionally deferred to a
    later commit so the lock infrastructure can land and stabilise
    first.

    Raises :class:`RuntimeError` if the DB writer is not started, has
    been stopped, or is in the middle of stopping (``_db_writer_stop_event``
    set). Handing out a stale ``None`` would silently let the caller's
    ``async with`` succeed on whatever the call site happens to cache,
    breaking serialisation; handing out the live lock during shutdown
    would race with the lifecycle teardown.

    .. important::

       **Never cache the returned lock object.** Call
       ``get_db_write_lock()`` right before each ``async with``,
       inside the smallest possible scope. The shutdown guard above
       only protects the moment of retrieval — once the caller holds
       a reference, ``stop_db_writer`` cannot guarantee the lock is
       still the active one when the caller's ``async with`` finally
       acquires (a same-process restart between cache and acquire
       leaves you serializing on an orphan).
       :func:`run_under_db_write_lock` is the safe alternative for
       any apply that crosses an ``await``.
    """
    if _db_write_lock is None:
        raise RuntimeError("DB writer not started")
    if _db_writer_stop_event is not None and _db_writer_stop_event.is_set():
        raise RuntimeError("DB writer is stopping")
    return _db_write_lock


def get_thread_queue() -> queue.Queue:
    """Return the shared thread queue (for an orchestrator's producer).

    Raises :class:`RuntimeError` if the DB writer is not started — the
    explicit guard survives ``python -O`` (where ``assert`` is stripped)
    and matches the same shape used by :func:`get_db_write_lock` and
    :func:`run_under_db_write_lock`.
    """
    if _thread_queue is None:
        raise RuntimeError("DB writer not started")
    return _thread_queue


async def put_thread_message(
    item: object, stop_event: threading.Event | None = None
) -> bool:
    """Push a payload (initial-sync entry or completion marker) onto the thread queue.

    For event-loop callers (the orchestrators). The shared queue is bounded;
    a full queue is handled by polling ``put_nowait()`` and awaiting a short
    sleep between attempts — entirely on the event loop, with no worker
    thread. A blocking put offloaded to a thread (``asyncio.to_thread``) is
    not cancellation-safe: if the awaiting coroutine is cancelled at shutdown,
    the thread's put keeps running and can enqueue the item *afterwards* —
    behind the drain marker ``shutdown()`` already pushed and awaited — which
    breaks the FIFO drain proof and can interleave with a hot restart. Polling
    keeps the whole wait inside the coroutine, so cancellation enqueues
    nothing: ``put_nowait()`` is atomic, and a cancel can only land on the
    ``await asyncio.sleep`` between attempts.

    With a ``stop_event``: returns ``True`` once the item is enqueued,
    ``False`` if ``stop_event`` fired first — the item is dropped, which is
    safe at shutdown since nothing awaits its completion then. ``stop_event``
    is rechecked before every attempt.

    With ``stop_event=None``: the item is never dropped — the loop retries
    until the put succeeds, then returns ``True``. Used for the drain marker
    ``shutdown()`` pushes once ``_sync_stop_event`` is already set: that marker
    proves the queue is drained and must not be dropped. The DB writer is
    permanent (it outlives every provider shutdown), so it keeps draining and
    a slot always frees up.
    """
    q = get_thread_queue()
    while stop_event is None or not stop_event.is_set():
        try:
            q.put_nowait(item)
            return True
        except queue.Full:
            await asyncio.sleep(0.05)
    return False


def get_subprocess_queue():
    """Return the shared compute result queue (passed to a compute worker).

    Raises :class:`RuntimeError` if the DB writer is not started — the
    explicit guard survives ``python -O`` (where ``assert`` is stripped)
    and matches the same shape used by :func:`get_db_write_lock` and
    :func:`run_under_db_write_lock`.
    """
    if _subprocess_queue is None:
        raise RuntimeError("DB writer not started")
    return _subprocess_queue


def arm_compute_completion(
    provider: Provider,
    display_session_ids: set[str] | None,
    total_display: int,
    applied_queue: asyncio.Queue | None = None,
) -> tuple[int, asyncio.Future]:
    """Declare a compute run for ``provider``; return ``(run_id, Future)``.

    Mints a fresh ``run_id`` and creates this run's ``_ComputeProviderState``
    and completion ``asyncio.Future``, both keyed by ``run_id``. The worker is
    spawned with this ``run_id`` and tags every message with it, so the
    DB writer routes each message to its own run — a stale message from a
    cancelled run can never touch a hot-restarted one. The Future resolves to
    the run's failed-session count when its ``done`` message is drained.

    Any leftover state for the same provider (a previous run whose worker was
    force-killed before emitting ``done``) is dropped here, with a warning —
    that state would otherwise leak for the process lifetime.
    """
    run_id = next(_compute_run_id_seq)
    for stale_run_id in [rid for rid, st in _compute_states.items() if st.provider == provider]:
        logger.warning(
            "arm_compute_completion: dropping leftover compute state for "
            "run_id=%d — its run never finalized (force-killed worker?)",
            stale_run_id,
        )
        _compute_states.pop(stale_run_id, None)
        _compute_done_events.pop(stale_run_id, None)
    _compute_states[run_id] = _ComputeProviderState(
        provider=provider,
        run_id=run_id,
        display_session_ids=display_session_ids,
        total_display=total_display,
        applied_queue=applied_queue,
    )
    future = asyncio.get_running_loop().create_future()
    _compute_done_events[run_id] = future
    return run_id, future


async def abandon_compute_run(run_id: int, provider: Provider) -> None:
    """Abandon a compute run at provider shutdown and flush its batched state.

    Awaited by a provider orchestrator's ``shutdown()`` once it has decided to
    stop the compute worker. Two things happen:

    1. The run is flagged ``abandoned`` synchronously, before any ``await``.
       From here on the DB writer skips every ``session_complete`` still queued
       (or still incoming) for this ``run_id`` — a shut-down provider's partial
       compute results must never apply after the provider has stopped, where
       they could clobber a hot-restart's fresher state (and leave freshly
       synced lines with ``compute_version`` already current). The run's
       sessions are recomputed from scratch on the next start.

    2. Finalization is handed to the DB writer task via an
       :class:`_AbandonComputeRunJob` on ``_async_queue``. The DB writer
       processes one message at a time, so by the time it runs the job any
       in-flight ``session_complete`` apply — and its post-apply bookkeeping
       that accumulates the session's affected activity days — has fully
       finished. The job flushes the run's batched project broadcasts and
       activity recalculations for the sessions applied before the abandon,
       then drops the run state. The flush runs *in the DB writer*, never as a
       direct DB write from this ``shutdown()`` task, so the single-writer
       guarantee holds. This coroutine awaits the job, so the provider does
       not reach STOPPED — and a hot restart cannot begin — until the
       finalization has committed.

    No-op when ``run_id`` is untracked or its tracked state belongs to another
    provider — e.g. the ``ComputeContext`` default ``run_id=0`` when the worker
    was torn down before :func:`arm_compute_completion` ran, or a run whose
    worker already emitted ``done`` and was finalized by
    :func:`_finalize_compute_run`.
    """
    state = _compute_states.get(run_id)
    if state is None or state.provider != provider:
        return
    # Flag synchronously, before any await: every session_complete the
    # DB writer dequeues for this run from now on is skipped.
    state.abandoned = True
    if _async_queue is None:
        raise RuntimeError("DB writer not started")
    done = asyncio.get_running_loop().create_future()
    _async_queue.put_nowait(
        _AbandonComputeRunJob(run_id=run_id, provider=provider, done=done)
    )
    # Await the DB writer-side finalization so shutdown does not reach STOPPED
    # before the run's batched state has been flushed and committed.
    with suppress(Exception):
        await done


def enqueue_async_job(job) -> None:
    """Queue an async-queue job for the DB writer without awaiting its result.

    Synchronous shape of :func:`submit_async_job`: the caller keeps the
    ``job.future`` reference and decides when (if ever) to await it. Useful
    for producers that batch many small jobs and want to keep submitting
    while previous batches are still being applied — most notably the
    cross-provider search-indexing sweep, which would otherwise serialize
    one batch's apply between successive indexed-session loops (the size
    of the batch becomes a latency multiplier, defeating the point of
    batching).

    Same lifecycle guards as :func:`submit_async_job`: raises ``RuntimeError``
    if the writer is not started or has been signalled to stop. The caller
    is responsible for settling ``job.future`` (with an exception, typically)
    if this raises — the DB writer will never see the job in that case, and
    nothing else would otherwise complete the Future.
    """
    if _db_writer_task is None or _db_writer_stop_event is None or _async_queue is None:
        raise RuntimeError("DB writer not started")
    if _db_writer_stop_event.is_set():
        raise RuntimeError("DB writer is stopping")
    _async_queue.put_nowait(job)


async def submit_async_job(job) -> object:
    """Push a periodic-task job onto the DB writer queue and await its result.

    Used by the periodic-task producers (commands sync, usage sync, model
    retirement, pricing) to route their DB writes through the DB writer.
    The caller pre-creates ``job.future`` (typically with
    ``loop.create_future()``) so this function can stay tiny and the job's
    NamedTuple is fully immutable. The DB writer settles ``job.future`` with
    the apply's return value, or with the apply's exception so the producer
    sees the failure rather than silently dropping it.

    Raises ``RuntimeError`` if the writer is not started or has been signalled
    to stop. A producer that has just received its task-level stop event
    should check it before calling this — the periodic loops do so via their
    own ``stop_event.is_set()`` check, and the DB writer outlives them all
    (the global shutdown stops every orchestrator before the writer, so a
    live periodic task always has a live writer to talk to).
    """
    enqueue_async_job(job)
    # ``asyncio.shield`` guards ``job.future`` against producer-task cancellation.
    # asyncio normally cancels the Future a cancelled Task is awaiting (via
    # ``Task._fut_waiter``); without the shield, ``job.future.cancelled()`` would
    # then be True by the time the DB writer reaches ``_settle_async_job``, and
    # its ``if not job.future.done(): set_result/set_exception`` guard would
    # silently drop the apply's outcome. With the shield, a CancelledError still
    # propagates out of this ``await`` to the caller (so a hot-toggle / shutdown
    # cancel still tears down promptly), but the future itself stays settleable
    # — the DB writer's apply is always observed, even if no producer is left
    # awaiting it.
    return await asyncio.shield(job.future)


# =============================================================================
# The DB writer loop
# =============================================================================


_T = TypeVar("_T")


def spawn_isolated_db_write_task(coro: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
    """Create an :class:`asyncio.Task` that documents "take a separate
    FIFO slot in the DB write lock".

    **Under the current membership-based reentrance design this helper
    is semantically equivalent to a naive
    ``asyncio.create_task(coro)``**: both produce a sub-task whose
    ``current_task()`` is NOT in the outer lease's ``drive_tasks`` set,
    so the sub-task's first :func:`run_under_db_write_lock` call falls
    through to a real acquire (blocks until the outer releases, then
    runs FIFO behind every other waiter). The helper exists for
    documentation-of-intent and as a defence-in-depth: it forces the
    inherited lease binding back to ``None`` in the sub-task's
    context, so even if a future maintainer ever removed the
    membership check the sub-task would still acquire normally.

    Each call creates a **fresh** :class:`contextvars.Context`; the
    Context is never returned, so it cannot be accidentally reused
    across multiple ``create_task`` calls (a sibling-leak hazard the
    earlier ``make_db_write_isolated_context()`` helper had and the
    reason this helper replaced it).

    Usage:

    .. code-block:: python

        async def outer_writer():
            async def with_subtask():
                # Make it explicit that ``sub_writer_coro`` takes its
                # own slot in the lock's FIFO.
                sub = spawn_isolated_db_write_task(sub_writer_coro())
                # … do the outer write’s remaining steps here …
                # DO NOT ``await sub`` here — the sub is queued behind
                # us in the lock's FIFO and cannot run until we release
                # by returning from this factory. Awaiting the returned
                # Task inside the locked factory deadlocks (sub blocks
                # on acquire; we block on sub; the shield-loop in the
                # drive prevents an outer ``asyncio.wait_for`` from
                # breaking it). Hand the handle back to the caller and
                # let them await it.
                return sub

            sub = await run_under_db_write_lock(lambda: with_subtask())
            # The lock has been released — the sub can now acquire and run.
            await sub

    Most callers should **not** need this helper. The default reentrance
    behaviour — ``await``ing other coroutines, or calling
    :func:`run_under_db_write_lock` again from inside the factory —
    transparently reuses the held lock and is the intended pattern.
    """
    ctx = contextvars.copy_context()
    ctx.run(_db_write_lock_lease.set, None)
    return asyncio.create_task(coro, context=ctx)


async def _drive_inner_under_held_lock(
    coro_factory: Callable[[], Coroutine[Any, Any, _T]],
) -> _T:
    """Body of the lock runner — assumes :data:`_db_write_lock` is HELD.

    Factored out of the public and internal runners so the cancellation-
    safety + factory-after-acquire + outer-vs-inner-cancel logic lives
    in one place. The caller is responsible for ``async with _db_write_lock:``
    around this call (otherwise serialization is lost).

    The caller is ALSO responsible for installing a :class:`_LockLease`
    in :data:`_db_write_lock_lease` BEFORE entering us. We wrap the
    user's ``coro_factory`` in a tiny coroutine that admits its own
    ``current_task()`` (i.e. the inner Task) to ``lease.drive_tasks``
    as its FIRST statement, before any user code. This works under
    both the default task factory (where the new Task does not run
    until the next scheduler tick, so the admit happens just before
    the user code starts) AND under :func:`asyncio.eager_task_factory`
    (where ``create_task`` runs the coroutine synchronously — the
    admit then happens immediately inside the new Task's own frame,
    before any user-code await or nested call).

    See :func:`run_under_db_write_lock` for the user-facing contract.
    """
    # Capture the lease NOW (snapshot of the caller's ContextVar binding)
    # so the wrapped factory and the post-loop discard agree on which
    # lease they admit / clear, even if the ContextVar binding is later
    # reset.
    lease = _db_write_lock_lease.get()

    # Wrap the factory so the inner Task admits itself to ``drive_tasks``
    # as its FIRST action, BEFORE any user code runs. Under the default
    # task factory the wrapper body runs at the next scheduler tick,
    # immediately before the user's coro_factory call — so the inner is
    # admitted before any nested ``run_under_db_write_lock`` can fire.
    # Under an **eager** task factory (``asyncio.eager_task_factory``,
    # Python 3.12+), ``asyncio.create_task(coro)`` runs the coroutine
    # eagerly until its first suspension point — without the wrapper,
    # a factory body that immediately called
    # ``run_under_db_write_lock`` would hit the membership check
    # (current_task() = inner, not yet in drive_tasks), fall through
    # to a real acquire, block on the lock the caller already holds,
    # and deadlock the drive. The wrapper makes the admission happen
    # inside the inner Task's own frame before any await, so eager-mode
    # runs admit themselves synchronously and the nested call sees
    # ``current_task() in drive_tasks == True``.
    async def admitted_factory() -> _T:
        if lease is not None:
            lease.drive_tasks.add(asyncio.current_task())
        return await coro_factory()

    # Create the coroutine AFTER the caller has acquired the lock. If a
    # cancellation arrives while the caller was waiting for ``async with``,
    # no coroutine has been created yet — no "coroutine was never awaited"
    # warning, no lost dequeued work.
    inner = asyncio.create_task(admitted_factory())
    try:
        return await _drive_inner_loop(inner)
    finally:
        # Balanced cleanup: remove the one inner Task we admitted at
        # the top of this drive call. The runner's own ``finally``
        # also does ``drive_tasks.clear()`` so the lease state is
        # always reset on hold exit, but the per-drive discard keeps
        # the set tidy and makes the bookkeeping explicit. No-op if
        # the lease is None.
        if lease is not None:
            lease.drive_tasks.discard(inner)


async def _drive_inner_loop(inner: asyncio.Task[_T]) -> _T:
    """The shield-loop + branching logic of the drive, factored out so
    the surrounding ``try/finally`` (which cleans up
    ``lease.drive_tasks``) stays compact and obvious. Assumes ``inner``
    has just been created and is owned by the caller.
    """
    outer_cancelled = False
    inner_self_cancelled = False
    while not inner.done():
        try:
            # Always shielded — outer cancels never reach the inner Task.
            await asyncio.shield(inner)
        except asyncio.CancelledError:
            # The await raised CancelledError. Two distinct sources:
            #   * OUR task got ``cancel()``d, which asyncio injects at
            #     the next await point. ``asyncio.current_task().
            #     cancelling()`` is ``> 0`` in that case (since the
            #     cancel counter was incremented and we haven't called
            #     ``uncancel()``).
            #   * The INNER Task itself raised ``CancelledError`` (rare
            #     — only if the apply code deliberately does
            #     ``raise CancelledError`` or something calls
            #     ``inner.cancel()``). The shield prevents the outer
            #     cancel from propagating to inner, but it doesn't
            #     prevent inner from cancelling itself.
            # Without this distinction we'd re-raise ``CancelledError``
            # below in either case. That's the right thing for the
            # outer-cancel branch (shutdown propagates) but wrong for
            # the inner-self-cancel branch — the DB writer task's
            # callers only ``except Exception``, so a stray
            # ``CancelledError`` from us would kill the writer task.
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 0:
                outer_cancelled = True
                # Loop and keep waiting. ``inner.done()`` may still be
                # False if the inner is still running on its executor
                # thread; the next iteration's shielded await blocks
                # until inner finishes.
            else:
                # Inner self-cancelled. Loop will exit on next check
                # because ``inner.done()`` is now True; surface it as
                # a regular Exception below so the caller's
                # ``except Exception`` catches it.
                inner_self_cancelled = True
        except Exception:
            # Inner raised. ``inner.done()`` is now True, so the loop
            # will exit naturally on its next ``while`` check. We do
            # NOT propagate from here, because we still want the
            # ``outer_cancelled`` branch below to take precedence if
            # the outer was cancelled too — cancellation wins. The
            # exception will be drained from ``inner.result()`` in
            # the right branch.
            pass
    # ``inner.done()`` is True. Branch on what happened.
    if outer_cancelled:
        # Drain the inner so asyncio doesn't log "Task exception was
        # never retrieved", AND so an apply that failed during the
        # cancel doesn't vanish: log it explicitly before re-raising
        # ``CancelledError``. ``BaseException`` is deliberately NOT
        # suppressed (``KeyboardInterrupt`` / ``SystemExit`` propagate).
        try:
            inner.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                "DB write apply failed during a cancelled outer: %s",
                exc,
                exc_info=True,
            )
        raise asyncio.CancelledError()
    if inner_self_cancelled:
        # The inner raised CancelledError on its own — without us being
        # cancelled. Surface it as a ``RuntimeError`` so the caller's
        # ``except Exception`` catches it and the surrounding loop (the
        # DB writer task) survives. A real CancelledError would slip
        # past ``except Exception`` and kill whatever is running this
        # apply.
        raise RuntimeError(
            "DB write apply task self-cancelled (no outer cancel) — "
            "likely a handler bug; surfacing as RuntimeError to keep "
            "the writer alive"
        )
    # Happy path: forward the inner's return value so callers can
    # use the runner as a transparent wrapper around their coroutine.
    # ``inner.result()`` raises if the inner raised; that propagates
    # to the caller's own ``try/except`` (no log here — the caller
    # owns happy-path error reporting).
    return inner.result()


async def _run_under_db_write_lock(
    coro_factory: Callable[[], Coroutine[Any, Any, _T]],
) -> _T:
    """Internal core — no stop-event guard.

    The DB writer's own final drain (the
    ``while True: await _drain_one()`` loop inside :func:`_db_writer_loop`
    that runs after ``_db_writer_stop_event`` is set) needs to keep
    acquiring the lock during shutdown to apply messages that were
    queued before the stop. The public :func:`run_under_db_write_lock`
    rejects new acquisitions once shutdown begins — so we expose this
    unchecked internal version, used exclusively by :func:`_drain_one`.

    Reentrance: if the current Task is admitted by the active lease
    (see :func:`_lease_admits_current`), skip the acquire and call
    the factory directly — ``asyncio.Lock`` is NOT reentrant and a
    second acquire by the same Task would self-deadlock. The admit
    set covers the Task that installed the lease and the drive's
    inner Task; nested calls reuse that existing membership rather
    than entering a fresh drive. Sub-tasks the factory spawns via
    ``asyncio.create_task`` inherit the lease but are NOT admitted,
    so they fall through to a real acquire (FIFO behind the outer).

    See :func:`run_under_db_write_lock` for the user-facing contract.
    """
    if _lease_admits_current():
        # Nested call inside a context that still holds the lock and
        # whose current Task is part of the lease's drive: the outer
        # call's drive (and its shielded loop, cancel discrimination,
        # stop-event checks) already covers us. Just run the factory
        # transparently.
        return await coro_factory()

    lock = _db_write_lock
    if lock is None:
        # Explicit guard rather than ``assert`` so the contract matches
        # :func:`get_db_write_lock` (RuntimeError, not AssertionError) and
        # survives ``python -O``.
        raise RuntimeError("DB writer not started")
    async with lock:
        # Install a fresh lease and admit the current Task to its
        # drive-task set BEFORE the drive. The drive will wrap the user
        # factory in a coroutine that self-admits its inner Task as its
        # first action (compatible with both default and eager task
        # factories). ``finally`` invalidates the lease so any sub-task
        # that inherited it observes ``active == False`` after our
        # release and acquires normally
        # — preventing the silent lock-less write a stale-True bool
        # would have allowed. ``ContextVar.reset(token)`` restores the
        # prior value in this Task's context only — other Tasks that
        # copied the context keep their own bindings.
        new_lease = _LockLease()
        current = asyncio.current_task()
        if current is not None:
            new_lease.drive_tasks.add(current)
        token = _db_write_lock_lease.set(new_lease)
        try:
            return await _drive_inner_under_held_lock(coro_factory)
        finally:
            new_lease.active = False
            new_lease.drive_tasks.clear()
            _db_write_lock_lease.reset(token)


async def run_under_db_write_lock(
    coro_factory: Callable[[], Coroutine[Any, Any, _T]],
) -> _T:
    """Run a coroutine under the shared DB write lock, cancellation-safe.

    Public, recommended API for every caller that needs to wrap a
    ``transaction.atomic`` block in the DB writer's serialization. Use
    it instead of ``async with get_db_write_lock(): ...`` whenever the
    critical section ``await``s something that dispatches to an executor
    thread (typically ``sync_to_async`` around Django ORM code) — the
    raw-lock pattern releases the lock as soon as the await is cancelled,
    even though the worker thread is still inside its transaction.

    ``coro_factory`` is a **zero-arg callable** that returns a fresh
    coroutine — typically ``lambda: my_async_fn(arg1, arg2)`` or a
    nested ``async def``. The factory is invoked AFTER the lock is
    acquired, NOT before: passing an already-created coroutine would
    leave it unawaited (with the runtime warning + lost work) if our
    task were cancelled while waiting for the lock.

    Returns whatever the inner coroutine returns on the happy path. On
    an outer-cancelled path raises :class:`asyncio.CancelledError` after
    the inner finishes — the inner's return value or non-cancel
    exception is suppressed in that case (the caller is being torn down
    and is not going to read it; we log inner Exceptions before
    swallowing so they're not invisible). On an inner-self-cancelled
    path (rare — apply code that deliberately raises
    ``CancelledError`` for some reason) raises :class:`RuntimeError`
    rather than ``CancelledError`` so the caller's ``except Exception``
    catches it; a stray ``CancelledError`` would otherwise slip past
    the caller and kill the writer task.

    Rejects new acquisitions once :data:`_db_writer_stop_event` is set:
    a future external caller can not start a fresh apply during the
    DB writer's shutdown drain (which would race the lifecycle
    teardown). The check happens at BOTH entry time AND after we
    have acquired the lock — the post-acquire re-check closes the
    TOCTOU window where ``stop_db_writer`` fires between the entry
    check and our turn in the FIFO acquire queue. The DB writer's
    own final drain uses the internal :func:`_run_under_db_write_lock`
    which has no such guard.

    **Lock granularity** (intentional design decision, documented for
    future external callers): the lock is held for the entire duration
    of ``coro_factory()``, not just its ``transaction.atomic`` part.
    Inside the DB writer that means post-commit broadcasts and any
    other side effects that the apply path emits run under the lock
    too. The trade-off is intentional — it's a "lock around the whole
    critical section, can't forget anything" safety net, at the cost
    of making future external waiters queue behind those side effects.
    In practice the post-apply work is fast (in-process Channels, FS
    via ``sync_to_async``); if profiling later shows the cost is
    unacceptable, callers can split their own critical sections by
    running the post-apply portion in a separate, lock-free coroutine.

    Cancellation-safety details:

    - The lock is acquired via ``async with``. If we are cancelled while
      waiting to acquire, no coroutine has been created yet, so nothing
      is leaked.
    - Once acquired, we instantiate the inner Task and shield it.
      ``await asyncio.shield(inner)`` raises ``CancelledError`` on our
      side if we are cancelled, but the shield keeps the inner alive.
      We catch the cancel, distinguish via
      ``current_task().cancelling()``, and loop until ``inner.done()``.
    - Inner exceptions on the happy path are caught inside the loop
      (``except Exception: pass``), drained at the end via
      ``inner.result()``, and re-raised to the caller. Cancellation
      takes precedence: if we were also cancelled, the Exception is
      logged and ``CancelledError`` is raised instead.
    - Inner self-cancel (no outer cancel) is surfaced as
      ``RuntimeError`` so it can't escape ``except Exception`` and
      tear down the writer.

    The companion :func:`get_db_write_lock` returns the raw lock for
    advanced callers whose critical section does not await on
    orphanable work (no executor calls); see its docstring for the
    warning.

    **Reentrance.** ``asyncio.Lock`` is **not** reentrant: a same-Task
    re-``acquire`` would self-deadlock. To make composition safe — an
    outer caller wraps a function under the lock, that function calls
    a helper that ALSO wraps itself under the lock — the runner installs
    a :class:`_LockLease` in a :class:`contextvars.ContextVar`
    (:data:`_db_write_lock_lease`) at entry, and admits its own Task
    plus the drive's inner Task to ``lease.drive_tasks``. A nested
    call whose ``current_task()`` is in ``lease.drive_tasks`` skips
    the acquire and runs the factory directly, reusing the lock the
    outer call already holds. The outer's drive (shield-loop, cancel
    discrimination, stop-event checks) already protects the entire
    nested execution, so the inner does not re-establish them.

    The reentrance check also short-circuits the stop-event guard for
    nested calls: an in-flight outer ``run_under_db_write_lock`` is
    by definition already past the guard, so refusing the nested call
    would be both a no-op (the outer would still complete) and a bug
    (the inner would raise ``RuntimeError("DB writer is stopping")``
    out of an in-flight write, breaking the outer's contract).

    The lease's ``active`` flag is flipped to ``False`` in the runner's
    ``finally`` after release. A sub-task that inherited an active lease
    via ``asyncio.create_task`` is NOT in ``drive_tasks`` (only the
    runner's own Task and the drive's inner Task are), so its
    reentrance check fails anyway — but the ``active`` flip is kept
    as a defence-in-depth so any future code path that mistakenly
    reads the lease without going through the membership test still
    falls through after release.

    **Sub-task behaviour.** Because ``asyncio.create_task`` propagates
    the parent context, a Task spawned **from inside** a factory
    inherits the lease, but its ``current_task()`` is not in
    ``drive_tasks`` — so its first ``run_under_db_write_lock`` call
    falls through to a real acquire. If the outer still holds the
    lock, the sub-task BLOCKS on the acquire until the outer releases.
    This serialises the sub-task FIFO behind the outer (safe behaviour:
    no parallel write under one lock, no lock-less write past the
    outer's release). The trade-off: if the factory itself ``await``s
    the sub-task inside the locked block, the result is a deadlock —
    sub waits for the lock, factory waits for sub.

    **The deadlock is fatal.** ``asyncio.wait_for`` / outer task
    cancellation cannot break it: the shield-loop inside
    :func:`_drive_inner_under_held_lock` deliberately waits for the
    inner Task to finish before yielding to a cancellation, so the
    cancel never reaches the blocked sub-task and the lock is never
    released. The misuse is loud (the process hangs forever instead
    of writing silently lock-less past release like the v3 design
    would have allowed), but the only recovery is a process kill.
    Callers MUST avoid the pattern by construction: the safer
    alternative for a "nested step" is to ``await`` the coroutine
    directly (no ``create_task``) — it becomes a transparent
    reentrant call. Callers who want the sub-task pattern explicit
    can use :func:`spawn_isolated_db_write_task`, which is
    semantically equivalent under this design but documents the
    intent; the same deadlock applies if the returned Task is
    ``await``ed from within the locked factory.

    The intended default pattern is sequential ``await``s or nested
    ``run_under_db_write_lock`` calls; neither needs special handling.
    """
    if _lease_admits_current():
        # Nested call. See the reentrance section of the docstring.
        # The outer call's pre/post-acquire stop-event checks already
        # ran; an in-flight outer means we are by definition past the
        # guard, so we skip both the check and the acquire and just
        # run the factory transparently.
        return await coro_factory()

    if _db_writer_stop_event is not None and _db_writer_stop_event.is_set():
        # Pre-acquire check: cheap RuntimeError for the common case where
        # the caller is invoked after shutdown has started.
        raise RuntimeError("DB writer is stopping")
    lock = _db_write_lock
    if lock is None:
        raise RuntimeError("DB writer not started")
    async with lock:
        # Post-acquire re-check. Closes the TOCTOU window where
        # ``stop_db_writer`` fires between our pre-check and our turn in
        # the FIFO acquire queue. Without this, a caller that passed the
        # pre-check would happily start an apply while ``stop_db_writer``
        # is mid-flight clearing the lifecycle bundle.
        if _db_writer_stop_event is None or _db_writer_stop_event.is_set():
            raise RuntimeError("DB writer is stopping")
        # Install a fresh lease and admit the current Task to its
        # drive-task set BEFORE the drive. The drive will wrap the user
        # factory in a coroutine that self-admits its inner Task as its
        # first action (compatible with both default and eager task
        # factories). ``finally`` invalidates the lease (defence-in-
        # depth — the membership check already excludes non-drive
        # sub-tasks) so an inherited lease never short-circuits past
        # our release.
        new_lease = _LockLease()
        current = asyncio.current_task()
        if current is not None:
            new_lease.drive_tasks.add(current)
        token = _db_write_lock_lease.set(new_lease)
        try:
            return await _drive_inner_under_held_lock(coro_factory)
        finally:
            new_lease.active = False
            new_lease.drive_tasks.clear()
            _db_write_lock_lease.reset(token)


async def _drain_one() -> bool:
    """Process at most one message from each queue.

    Covers the two shared producer queues (compute, initial-sync) and the
    intra-process async queue. Returns True if anything was processed
    this call.

    Every per-message apply runs inside :func:`provider_log_context` so
    log records emitted during the apply (including those bubbling up
    through ``sync_to_async`` and the helpers it transitively calls) get
    the right ``provider`` tag in the log column — the DB writer task
    itself runs in the global context, but each individual message
    belongs to one provider.

    Each of the three branches dispatches its apply through
    :func:`_run_under_db_write_lock` (the internal version, so the final
    shutdown drain can bypass the public stop guard and apply messages
    that were queued before the stop). The helper acquires
    :data:`_db_write_lock` for the duration of the apply AND holds it
    across asyncio cancellation (see its docstring for the cancel-vs-
    executor-thread rationale). The lock is the single FIFO
    serialisation point shared with every future external writer (the
    watcher, agent lifecycle, etc.). Acquiring per-branch rather than
    once around the whole function gives the lock 3 release points per
    tick — a future external waiter never has to wait longer than one
    apply, even when all three queues happen to fire on the same tick.
    The pulls themselves (``get_nowait``) run outside the lock: they
    are synchronous and never block, and we only need the lock once we
    have actual work to apply. The coroutine factory passed to the
    helper is a ``lambda`` capturing the dequeued message, so the apply
    coroutine is only created after the lock has been acquired — a
    cancellation while waiting for the lock cannot leave an unawaited
    coroutine (or, equivalently, a silently dropped dequeued message).

    **Granularity trade-off (intentional)**: ``_run_under_db_write_lock``
    holds the lock around the **whole** ``_process_*`` / ``_dispatch_*``
    handler, not just the ``transaction.atomic`` part. Post-commit
    broadcasts (WS via ``channel_layer.group_send``), workspace-JSON
    file updates, and any read-only ORM calls the handler makes are
    therefore under the lock too. The strict-architectural model says
    "lock around ``transaction.atomic`` only" and would not include
    those side effects, but the current shape is a deliberate safety
    net: every dequeued message goes through ``_drain_one`` and every
    branch dispatches through the helper, so no code path can forget
    to take the lock. The cost is that future external waiters queue
    behind those non-SQL side effects (Channels InMemoryLayer, FS via
    ``sync_to_async``, fast reads); in practice each handler completes
    in tens of milliseconds. If profiling later shows the wait is
    unacceptable, individual handlers can be split into a locked
    apply portion and an unlocked post-apply portion without changing
    the helper itself.

    Never raises an ordinary exception: a message is removed from its queue
    before processing, and every per-message failure (deserialization, apply,
    finalization) is caught and logged. So one bad message can neither crash a
    steady-state DB writer tick nor abort the shutdown drain — which would
    strand the messages queued behind it.
    """
    any_processed = False

    # ---- Compute side (mp.Queue, shared by every provider) ----
    try:
        raw = _subprocess_queue.get_nowait()
    except queue.Empty:
        pass
    except Exception as exc:
        # The mp.Queue is never closed, so get_nowait() should only ever raise
        # Empty; guard anyway so an unexpected failure cannot abort the drain.
        logger.error(f"Compute result queue read failed: {exc}", exc_info=True)
    else:
        any_processed = True
        try:
            msg = orjson.loads(raw)
        except Exception:
            logger.error(f"Failed to deserialize compute message: {raw!r:.500}")
        else:
            with provider_log_context(_provider_from_compute_message(msg)):
                try:
                    await _run_under_db_write_lock(lambda: _process_compute_message(msg))
                except Exception as exc:
                    logger.error(f"Error processing compute message: {exc}", exc_info=True)

    # ---- Thread queue (initial-sync, shared by every provider) ----
    try:
        payload = _thread_queue.get_nowait()
    except queue.Empty:
        pass
    else:
        any_processed = True
        with provider_log_context(getattr(payload, "provider", None)):
            try:
                await _run_under_db_write_lock(lambda: _process_thread_message(payload))
            except Exception as exc:
                logger.error(f"Error processing thread message: {exc}", exc_info=True)

    # ---- DB writer-side jobs (intra-process) ----
    try:
        job = _async_queue.get_nowait()
    except asyncio.QueueEmpty:
        pass
    else:
        any_processed = True
        with provider_log_context(getattr(job, "provider", None)):
            try:
                await _run_under_db_write_lock(lambda: _dispatch_async_job(job))
            except Exception as exc:
                # _dispatch_async_job has its own internal guards
                # (convention check, isinstance dispatch, registry fallback,
                # _settle_async_job per-apply try/except), so an Exception
                # surfacing here is unexpected. But the message is already
                # dequeued from ``_async_queue`` — if we don't settle the
                # job's future, the producer's
                # ``await asyncio.shield(job.future)`` would hang forever.
                # Mirror what the compute / thread branches log, and
                # settle the future with the exception so the producer
                # observes the failure instead of stranding.
                logger.error(
                    f"Error processing async job: {exc}", exc_info=True,
                )
                _settle_unhandled_async_job(job, exc)

    return any_processed


def _provider_from_compute_message(msg: dict) -> Provider | None:
    """Extract the ``Provider`` from a compute-side raw JSON message, if any.

    The subprocess tags every message with ``provider`` (a ``Provider.value``
    string) but ``orjson.loads`` returns it as a plain str — translate
    back to the enum for :func:`provider_log_context`. Unknown / missing
    values fall back to ``None`` (logged as ``"global"``); the dispatch
    in :func:`_process_compute_message` will surface its own error.
    """
    raw_provider = msg.get("provider")
    if raw_provider is None:
        return None
    try:
        return Provider(raw_provider)
    except ValueError:
        return None


def _settle_unhandled_async_job(job, exc: Exception) -> None:
    """Settle a rejected async-queue job so its producer never hangs.

    Two completion shapes coexist on the async queue: the periodic-task
    jobs (and any provider-specific job following the same convention)
    carry a ``future`` settled with a result or exception, while
    :class:`_AbandonComputeRunJob` carries a ``done`` Future that the
    producer (shutdown) only awaits as a "DB writer reached this point"
    signal — that one resolves with ``None`` even on failure so shutdown
    can never hang.
    """
    future = getattr(job, "future", None)
    if future is not None and not future.done():
        future.set_exception(exc)
        return
    done = getattr(job, "done", None)
    if done is not None and not done.done():
        done.set_result(None)


async def _dispatch_async_job(job) -> None:
    """Apply one DB writer job and settle its caller-visible Future.

    Two job families are dispatched here:

    - **Generic, cross-provider jobs** owned by this module:
      :class:`_AbandonComputeRunJob` (a finalization request from a
      provider's ``shutdown()``; the producer awaits ``job.done`` and only
      cares that the DB writer reached this point, so we always resolve it
      with ``None`` — a hang would block shutdown) and the four periodic-
      task jobs (:class:`_ApplyDesiredCommandsJob`,
      :class:`_CreateUsageSnapshotJob`, :class:`_RetireSessionsJob`,
      :class:`_PersistProviderPricesJob`) carry the actual apply result;
      we settle their ``job.future`` with the result on success, or with
      the raised exception so the producer can log / surface it instead
      of silently dropping the failure.
    - **Provider-specific jobs** owned by a provider's helper module
      (e.g. cron-restart preparation, which is Claude Code-only). When no
      generic ``isinstance`` matches, we fall back to the helpers registry
      and ask each provider in turn via
      :meth:`BaseProviderHelpers.try_handle_async_job`; the first one
      that returns ``True`` wins. Lets a provider keep its job type +
      apply handler co-located in its own subpackage without polluting
      this module's vocabulary.

    Every per-job apply is wrapped here so one bad message can neither crash
    the DB writer tick nor strand the messages queued behind it. Two
    safety nets specifically protect the producer from hanging on
    ``job.future``:

    - **Convention check up front**: every async job MUST declare a
      ``provider`` field — either a :class:`Provider` value (per-provider
      jobs) or ``None`` (cross-provider / system jobs such as
      :class:`_MarkSessionsIndexedJob`, which sweeps sessions of every
      provider in one shot). The field is read by :func:`_drain_one` to
      bind :func:`provider_log_context` around the apply, so log records
      emitted from inside the apply carry the right provider tag (``None``
      falls back to the ``"global"`` tag). A job missing the field outright,
      or carrying a non-Provider non-None value, is rejected before dispatch
      and its future settled with a ``TypeError``.
    - **Unmatched job at the end**: if no generic ``isinstance`` matched
      AND no provider helper claimed the job, we settle the future with
      a ``RuntimeError`` rather than ``return``-ing silently.
    """
    if not hasattr(job, "provider"):
        exc = TypeError(
            f"Async-queue job {type(job).__name__} is missing a "
            f"`provider` field; every async-queue job must declare one "
            f"(a Provider value, or None for cross-provider jobs) per "
            f"the BaseProviderHelpers.try_handle_async_job contract"
        )
        logger.error(str(exc))
        _settle_unhandled_async_job(job, exc)
        return
    provider = job.provider
    if provider is not None and not isinstance(provider, Provider):
        exc = TypeError(
            f"Async-queue job {type(job).__name__} has an invalid "
            f"`provider` value {provider!r}; expected a Provider enum "
            f"member or None for cross-provider jobs"
        )
        logger.error(str(exc))
        _settle_unhandled_async_job(job, exc)
        return

    if isinstance(job, _AbandonComputeRunJob):
        try:
            await _finalize_abandoned_run(job.run_id, job.provider)
        except Exception as exc:
            logger.error(
                f"Error finalizing abandoned compute run {job.run_id}: {exc}",
                exc_info=True,
            )
        finally:
            if not job.done.done():
                job.done.set_result(None)
        return

    if isinstance(job, _ApplyDesiredCommandsJob):
        await _settle_async_job(job, _apply_desired_commands_job, "commands sync")
        return

    if isinstance(job, _CreateUsageSnapshotJob):
        await _settle_async_job(job, _apply_create_usage_snapshot_job, "usage sync")
        return

    if isinstance(job, _RetireSessionsJob):
        await _settle_async_job(job, _apply_retire_sessions_job, "model retirement")
        return

    if isinstance(job, _PersistProviderPricesJob):
        await _settle_async_job(job, _apply_persist_provider_prices_job, "price sync")
        return

    if isinstance(job, _PersistModelBenchmarksJob):
        await _settle_async_job(job, _apply_persist_model_benchmarks_job, "model benchmark sync")
        return

    if isinstance(job, _MarkSessionsIndexedJob):
        await _settle_async_job(job, _apply_mark_sessions_indexed_job, "search-index mark")
        return

    # Peer-message attachment purge (peer_purge_task). Lazy import for the
    # same one-way dependency reason as the ProcessRun cleanup below.
    from twicc.peer_purge_task import (
        _apply_purge_peer_attachments_job,
        _PurgePeerAttachmentsJob,
    )
    if isinstance(job, _PurgePeerAttachmentsJob):
        await _settle_async_job(job, _apply_purge_peer_attachments_job, "peer attachment purge")
        return

    # Cross-provider boot cleanup of stale ProcessRun rows. Lazy import
    # because ``twicc.agent`` imports from this module (``run_under_db_write_lock``)
    # and we want a one-way dependency edge from agent → db_writer at module
    # load time; the apply function itself is only resolved at dispatch time.
    from twicc.agent.process_run_cleanup import (
        CleanupStaleProcessRunsJob,
        _apply_cleanup_stale_process_runs_job,
    )
    if isinstance(job, CleanupStaleProcessRunsJob):
        await _settle_async_job(
            job, _apply_cleanup_stale_process_runs_job, "process run cleanup",
        )
        return

    # Fallback: ask every provider's helper. Lazy import to avoid a cycle —
    # ``twicc.providers.helpers`` instantiates each provider's helpers,
    # which transitively imports Django models / this module's payloads.
    from twicc.providers.helpers import get_provider_helpers_registry

    for helpers in get_provider_helpers_registry().values():
        try:
            handled = await helpers.try_handle_async_job(job, _settle_async_job)
        except Exception as exc:
            logger.error(
                "Provider helper %s.try_handle_async_job raised on job %s: %s",
                type(helpers).__name__, type(job).__name__, exc, exc_info=True,
            )
            handled = False
        if handled:
            return

    # No handler claimed the job — settle the future so the producer's
    # ``await submit_async_job(...)`` doesn't hang forever. The producer
    # sees a RuntimeError, which is the right shape: this is a
    # programming error (forgot to register a handler, helper override
    # has a bug), not a runtime condition the producer can retry.
    exc = RuntimeError(
        f"No DB writer handler matched async-queue job "
        f"{type(job).__name__} — neither the generic dispatch nor any "
        f"provider helper claimed it"
    )
    logger.error(f"{exc} => {job!r:.300}")
    _settle_unhandled_async_job(job, exc)


async def _settle_async_job(job, apply_fn, label: str) -> None:
    """Run a periodic-task job's sync apply, then settle its Future.

    ``apply_fn`` is a synchronous function (it runs in ``transaction.atomic``
    on a worker thread via ``sync_to_async``) that takes the job and returns
    the value the caller awaits. Any exception is logged and forwarded to the
    Future as an exception result, so the producer sees a real failure rather
    than a stranded ``await``.

    The ``provider`` attribute carried by every job (either a
    :class:`twicc.core.enums.Provider` for per-provider jobs or ``None``
    for cross-provider / system jobs like :class:`_MarkSessionsIndexedJob`)
    is not read here — :func:`_drain_one` already wraps the dispatch in
    :func:`provider_log_context` so log records emitted from the apply
    carry the right tag. This function's own logging uses ``label`` so the
    failure line identifies which periodic task failed without needing the
    provider value.
    """
    try:
        result = await sync_to_async(apply_fn)(job)
    except Exception as exc:
        logger.error(
            f"Error applying {label} job: {exc}",
            exc_info=True,
        )
        if not job.future.done():
            job.future.set_exception(exc)
        return
    if not job.future.done():
        job.future.set_result(result)


async def _db_writer_loop() -> None:
    """Drain the producer queues and DB writer jobs until the app shuts down.

    Permanent: runs from start_db_writer() to stop_db_writer().
    Resilient: an unexpected exception in one tick is logged and the loop
    continues — a single bad message must never take the writer down.

    On stop, before exiting, every queue is fully drained.
    stop_db_writer() runs only after every producer has shut down, so
    the queues can no longer grow — draining them guarantees no boot-time
    write still queued at shutdown is silently abandoned.
    """
    assert _db_writer_stop_event is not None
    assert _thread_queue is not None
    assert _subprocess_queue is not None
    assert _async_queue is not None
    assert _db_write_lock is not None

    logger.info("DB writer loop running")
    while not _db_writer_stop_event.is_set():
        try:
            any_processed = await _drain_one()
            # Yield: tightly when busy, back off when idle.
            await asyncio.sleep(0 if any_processed else 0.05)
        except Exception as exc:
            logger.error(f"DB writer tick crashed: {exc}", exc_info=True)
            await asyncio.sleep(0.05)

    # Final drain: producers are all stopped by the time stop is signalled, so
    # the queues only shrink — apply everything still queued before exiting.
    # On a crashed tick, log and continue (mirroring the steady-state loop):
    # a bad message is removed from its queue before processing, so the next
    # pass still makes progress. A break here would strand every message
    # queued behind the bad one. _drain_one() is written not to raise, so the
    # except is a last-resort guard.
    drained_passes = 0
    while True:
        try:
            if not await _drain_one():
                break
        except Exception as exc:
            logger.error(f"DB writer shutdown-drain tick crashed: {exc}", exc_info=True)
            continue
        drained_passes += 1
    if drained_passes:
        logger.info(
            "DB writer drained %d queued message pass(es) at shutdown",
            drained_passes,
        )
    logger.info("DB writer loop exited")


# =============================================================================
# Compute message handling
# =============================================================================


async def _apply_compute_items_in_chunks(msg: dict) -> tuple[dict, str]:
    """Pre-apply a large result's item writes in slices; return the trimmed message.

    Small results are returned untouched (``apply_session_complete`` writes
    their items itself). For large ones every slice runs as its own
    ``sync_to_async`` call, so other thread-sensitive callers interleave,
    and re-checks the revision guard; the first non-``ok`` outcome stops the
    apply and is returned so the caller reports it exactly as
    ``apply_session_complete`` would have.
    """
    from twicc.providers.compute_base import BaseSessionCompute

    item_updates = msg.get('item_updates') or []
    content_overrides = msg.get('content_overrides') or []
    if len(item_updates) + len(content_overrides) <= COMPUTE_APPLY_CHUNK_SIZE:
        return msg, "ok"

    session_id = msg['session_id']
    observed_last_offset = msg.get('observed_last_offset')
    item_fields = msg.get('item_fields') or []
    apply_chunk = sync_to_async(BaseSessionCompute.apply_session_items_chunk)
    for start in range(0, len(item_updates), COMPUTE_APPLY_CHUNK_SIZE):
        outcome = await apply_chunk(
            session_id, observed_last_offset, item_fields,
            item_updates[start:start + COMPUTE_APPLY_CHUNK_SIZE], [],
        )
        if outcome != "ok":
            return msg, outcome
    for start in range(0, len(content_overrides), COMPUTE_APPLY_CHUNK_SIZE):
        outcome = await apply_chunk(
            session_id, observed_last_offset, [], [],
            content_overrides[start:start + COMPUTE_APPLY_CHUNK_SIZE],
        )
        if outcome != "ok":
            return msg, outcome
    return {**msg, 'item_updates': [], 'content_overrides': []}, "ok"


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

    # Every message is tagged with the run_id arm_compute_completion() minted;
    # the DB writer routes state/completion by run_id so a stale message from a
    # cancelled run can never touch a hot-restarted one.
    run_id = msg.get("run_id")

    if msg_type == "done":
        await _finalize_compute_run(run_id, provider)
        return

    if msg_type == "error":
        error = str(msg.get("error") or "Unknown compute worker error")
        logger.error(f"Compute error for {msg.get('session_id')}: {error}")
        state = _compute_states.get(run_id)
        if state is not None:
            state.failed_count += 1
            session_id = msg.get("session_id")
            if state.applied_queue is not None and isinstance(session_id, str):
                state.applied_queue.put_nowait(ComputeApplied(session_id, "failed", error))
        return

    if msg_type != "session_complete":
        logger.error(f"Unexpected compute message type: {msg_type} => {msg!r:.300}")
        return

    # session_complete — the heavy path.
    state = _compute_states.get(run_id)
    if state is None or state.abandoned:
        # No live state for this run_id: either the run was never tracked / its
        # state already dropped, or it was abandoned at provider shutdown
        # (abandon_compute_run flags it; the DB writer finalizes it via an
        # _AbandonComputeRunJob). Ignore the message — do NOT apply it. The
        # metadata was computed against a possibly-outdated view of the session
        # and could clobber fresher data written since (by a hot-restarted run,
        # or by the watcher). The session's compute_version was never advanced
        # by this skipped message, so a live run (or the watcher) recomputes it.
        logger.info(
            "Ignoring session_complete for an untracked or abandoned compute "
            "run (run_id=%s, session_id=%s) — stale run",
            run_id, msg.get("session_id"),
        )
        return

    try:
        from twicc.providers.compute_base import BaseSessionCompute, ComputeApplyResult

        msg, chunk_outcome = await _apply_compute_items_in_chunks(msg)
        if chunk_outcome != "ok":
            result = ComputeApplyResult(chunk_outcome)
        else:
            result = await sync_to_async(BaseSessionCompute.apply_session_complete)(msg)
    except Exception as e:
        logger.exception("Error applying session_complete")
        state.failed_count += 1
        if state.applied_queue is not None:
            state.applied_queue.put_nowait(ComputeApplied(msg["session_id"], "failed", str(e)))
        return

    if state.applied_queue is not None:
        state.applied_queue.put_nowait(ComputeApplied(msg["session_id"], result.outcome))
    if result.outcome != "applied":
        return

    try:
        await _handle_compute_done(msg["session_id"])
        # A subagent's compute may have folded plan-doc entries into its
        # top-level ancestor — _handle_compute_done above skipped it (the
        # completed session is the subagent), so push the ancestor too.
        if result.folded_ancestor_id:
            await _handle_compute_done(result.folded_ancestor_id)
    except Exception:
        logger.exception("Error broadcasting applied session_complete")
        state.failed_count += 1
        return

    project_id = msg.get("project_id")
    if project_id:
        state.pending_project_ids.add(project_id)

    project_directory = msg.get("project_directory")
    if project_id and project_directory and project_id not in state.auto_added_project_ids:
        state.auto_added_project_ids.add(project_id)
        await auto_add_project_to_workspaces(project_id, project_directory)
        # Link to its main repo if this project is a git worktree (local
        # import: twicc.projects ↔ db_writer is a cycle at module load).
        from twicc.projects import ensure_project_color, ensure_worktree_link
        await ensure_worktree_link(project_id, project_directory)
        await ensure_project_color(project_id, project_directory)

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


async def _finalize_compute_run(run_id: int | None, provider: Provider) -> None:
    """Drain-time finalisation when a compute run's worker is done.

    Flushes the run's pending broadcasts + activities, resolves its completion
    Future with the run's failed-session count, and drops its state so the
    next run starts clean. A 'done' for an untracked ``run_id`` (a stale
    message from a cancelled run) is ignored — it must not touch a live run.
    """
    state = _compute_states.pop(run_id, None)
    future = _compute_done_events.pop(run_id, None)
    if state is None and future is None:
        logger.info(
            "compute 'done' for an untracked run (run_id=%s) — "
            "ignoring (stale message from a cancelled run)",
            run_id,
        )
        return

    logger.info(f"DB writer: compute 'done' (run_id={run_id})")
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

    # Resolve the completion Future with the run's failed-session count; the
    # compute orchestrator (start_background_compute_task) logs the summary.
    if future is not None and not future.done():
        future.set_result(state.failed_count if state is not None else 0)


async def _finalize_abandoned_run(run_id: int, provider: Provider) -> None:
    """DB writer-side finalization of a compute run abandoned at shutdown.

    Runs inside the DB writer task (dispatched via ``_async_queue`` by
    :func:`abandon_compute_run`), so the activity-recalculation flush is
    serialized with every other DB writer write — the single-writer guarantee
    holds. The DB writer handles one message at a time, so any in-flight
    ``session_complete`` for the run, and its post-apply bookkeeping, has
    already finished by the time this runs: ``state.pending_activity_days``
    and ``state.pending_project_ids`` are final.

    Flushes the run's batched project broadcasts and activity recalculations
    for the sessions applied before the abandon — without this, those
    already-applied sessions would keep their ``compute_version`` current (so
    the next start does not recompute them) yet leave their ``PeriodicActivity``
    rows stale — then drops the run state. Unlike :func:`_finalize_compute_run`
    it does not resolve the completion Future: the orchestrator that armed the
    run is shutting the provider down and cancels the compute task that
    awaited it.
    """
    state = _compute_states.pop(run_id, None)
    _compute_done_events.pop(run_id, None)
    if state is None:
        # Already finalized by _finalize_compute_run — the worker emitted
        # 'done' between abandon_compute_run flagging the run and this job
        # running, so the flush already happened. Nothing left to do.
        return

    logger.info(
        "Finalizing abandoned compute run_id=%d "
        "(%d session(s) applied, %d failed) — remaining queued results "
        "are ignored",
        run_id, state.completed_count, state.failed_count,
    )
    for pid in state.pending_project_ids:
        try:
            await _broadcast_project_updated(pid)
        except Exception as e:
            logger.error(f"Error in abandoned-run project broadcast for {pid}: {e}")
    if state.pending_activity_days:
        try:
            await _flush_pending_activities(provider, state.pending_activity_days)
        except Exception as e:
            logger.error(f"Error in abandoned-run activity flush: {e}", exc_info=True)


async def _handle_compute_done(session_id: str) -> None:
    """Broadcast session_updated for a real, user-visible session."""
    from twicc.core.models import Session, SessionType
    from twicc.core.serializers import serialize_session

    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)
        if session.user_message_count == 0 or session.type != SessionType.SESSION:
            return
        if session.hidden:
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


async def _broadcast_project_added(project) -> None:
    """Broadcast project_added for a newly-created project.

    Called by the initial-sync DB writer *after* the create-session
    transaction has committed — never from inside it.
    """
    from twicc.core.serializers import serialize_project

    try:
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "project_added",
                "project": serialize_project(project),
            }},
        )
    except Exception as e:
        logger.error(f"Error broadcasting project_added for {project.id}: {e}")


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

    # One atomic batch for the whole flush, like every other DB writer write.
    with transaction.atomic():
        for project_id, days in pending_activity_days.items():
            PeriodicActivity.recalculate_for_days(project_id, days, provider=provider, do_global=False)
        days = set.union(*pending_activity_days.values())
        PeriodicActivity.recalculate_for_days(None, days, provider=provider, do_global=True)


# =============================================================================
# Initial-sync message handling
# =============================================================================


async def _process_thread_message(msg) -> None:
    """Apply one message drained from the shared thread queue."""
    if isinstance(msg, InitialSyncDoneMarker):
        # Drained after every real payload of the run (FIFO). Resolve the
        # marker's Future with the run's failure count so the producer learns
        # the run was not clean. Handled outside the try below so the Future
        # is always resolved.
        failures = _initial_sync_failures.pop(msg.provider, 0)
        orphan_skips = _initial_sync_orphan_skips.pop(msg.provider, 0)
        logger.info("DB writer: initial-sync 'done'")
        if orphan_skips:
            logger.warning(
                "Initial sync: %d subagent(s) skipped — parent "
                "row absent (orphan, or an upstream payload failed)",
                orphan_skips,
            )
        if not msg.done_future.done():
            msg.done_future.set_result(failures)
        return

    try:
        if isinstance(msg, CreateSessionPayload):
            project, created, adopted = await sync_to_async(_apply_create_session_payload)(msg)
            if project is not None:
                # Post-commit side effects, kept out of transaction.atomic so
                # a project is never announced before it commits. Workspace
                # auto-add runs only when the project was just created or just
                # adopted a directory — not for every session of an existing
                # project (its workspace membership cannot change per-session).
                if created:
                    await _broadcast_project_added(project)
                if (created or adopted) and project.directory:
                    await auto_add_project_to_workspaces(project.id, project.directory)
                    # Link to its main repo if this project is a git worktree
                    # (local import: twicc.projects ↔ db_writer cycle).
                    from twicc.projects import ensure_project_color, ensure_worktree_link
                    await ensure_worktree_link(project.id, project.directory)
                    new_color = await ensure_project_color(project.id, project.directory)
                    if new_color:
                        project.color = new_color
            else:
                # _apply_create_session_payload skipped an orphan subagent
                # (parent row absent). A deliberate skip, not an apply error —
                # counted separately, but still surfaced in the run summary.
                _initial_sync_orphan_skips[msg.provider] += 1
        elif isinstance(msg, UpdateSessionPayload):
            await sync_to_async(_apply_update_session_payload)(msg)
        elif isinstance(msg, MarkSessionsStalePayload):
            await sync_to_async(_apply_mark_sessions_stale_payload)(msg)
        elif isinstance(msg, DeleteSessionsPayload):
            deleted_ids = await sync_to_async(_apply_delete_sessions_payload)(msg)
            if deleted_ids:
                from twicc import search

                if search.is_initialized():
                    for session_id in deleted_ids:
                        await asyncio.to_thread(search.delete_session_documents, session_id)
                    await asyncio.to_thread(search.commit)
        elif isinstance(msg, UpdateProjectMetadataPayload):
            await sync_to_async(_apply_update_project_metadata_payload)(msg)
        elif isinstance(msg, ResolveProjectGitRootsPayload):
            await sync_to_async(_apply_resolve_git_roots_payload)(msg)
        elif isinstance(msg, UpsertWorkflowPayload):
            await sync_to_async(_apply_upsert_workflow_payload)(msg)
        else:
            logger.error(
                f"Unexpected initial-sync message type: {type(msg).__name__} => {msg!r:.300}"
            )
    except Exception as e:
        logger.error(
            f"Error processing initial-sync message {type(msg).__name__}: {e}",
            exc_info=True,
        )
        provider = getattr(msg, "provider", None)
        if provider is not None:
            _initial_sync_failures[provider] += 1


def _apply_create_session_payload(payload: CreateSessionPayload) -> tuple[object, bool, bool]:
    """Persist a new session (and project if missing) plus its items, atomically.

    Returns ``(project, project_was_created, adopted_directory)`` so the async
    caller can run the post-commit side effects — the ``project_added``
    broadcast and workspace auto-add — *outside* this transaction.
    ``register_project_db_only`` is the DB-only half of project registration
    precisely so the broadcast never fires from inside ``transaction.atomic``.

    Returns ``(None, False, False)`` when the payload is a subagent whose
    parent row is absent — an upstream CreateSessionPayload was rejected, or
    the parent is an orphan. The session is skipped cleanly with one warning
    rather than cascading into an opaque IntegrityError on the parent_session
    FK.
    """
    from twicc.core.models import Session, SessionItem
    from twicc.projects import register_project_db_only

    parent_id = payload.session.parent_session_id
    if parent_id is not None and not Session.objects.filter(id=parent_id).exists():
        logger.warning(
            "Skipping session %s: parent %s does not exist "
            "(upstream payload rejected, or orphan subagent)",
            payload.session.id, parent_id,
        )
        return None, False, False

    with transaction.atomic():
        project, created, adopted = register_project_db_only(
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
    return project, created, adopted


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


def _compute_project_mtime(project_id: str) -> float:
    """Max ``mtime`` over a project's visible, non-stale SESSION rows (0 if none).

    Shared by ``recalc_mtime`` in :func:`_apply_update_project_metadata_payload`
    and the per-project refresh in :func:`_apply_mark_sessions_stale_payload`.
    Same filter as the ``mtime`` aggregate in ``update_project_metadata()``:
    the visible-session filter plus ``stale=False`` so a session whose JSONL
    is gone from disk does not keep the project's mtime up.
    """
    from django.db.models import Max

    from twicc.core.models import Session, SessionType

    return Session.objects.filter(
        project_id=project_id,
        type=SessionType.SESSION,
        created_at__isnull=False,
        user_message_count__gt=0,
        stale=False,
    ).aggregate(value=Max("mtime"))["value"] or 0


def _apply_mark_sessions_stale_payload(payload: MarkSessionsStalePayload) -> None:
    """Mark a batch of sessions stale, then refresh their projects' mtime.

    Marking sessions stale removes them from the project-mtime aggregate
    (:func:`_compute_project_mtime` excludes ``stale=True``), so a session
    whose file is gone from disk no longer keeps its project's ``mtime`` — and
    its sort position — high. Self-contained on purpose: every producer that
    marks sessions stale gets the refresh without enqueueing a follow-up
    ``UpdateProjectMetadataPayload`` of its own.
    """
    from twicc.core.models import Project, Session

    if not payload.session_ids:
        return
    with transaction.atomic():
        affected_project_ids = set(
            Session.objects.filter(id__in=payload.session_ids)
            .values_list("project_id", flat=True)
        )
        Session.objects.filter(id__in=payload.session_ids, stale=False).update(stale=True)
        for project_id in affected_project_ids:
            Project.objects.filter(id=project_id).update(
                mtime=_compute_project_mtime(project_id)
            )


def _apply_delete_sessions_payload(payload: DeleteSessionsPayload) -> list[str]:
    """Delete ignored internal sessions and repair their derived aggregates."""
    from collections import defaultdict

    from twicc.core.models import DailyActivity, Session, SessionItem
    from twicc.projects import update_project_metadata

    if not payload.session_ids:
        return []

    rows = list(
        Session.objects.filter(id__in=payload.session_ids, provider=payload.provider)
        .values("id", "project_id", "created_at")
    )
    if not rows:
        return []

    deleted_ids = [row["id"] for row in rows]
    project_days: dict[str, set[date_cls]] = defaultdict(set)
    project_by_session = {row["id"]: row["project_id"] for row in rows}
    for row in rows:
        if row["created_at"] is not None:
            project_days[row["project_id"]].add(row["created_at"].date())
    for session_id, timestamp in SessionItem.objects.filter(
        session_id__in=deleted_ids,
        timestamp__isnull=False,
    ).values_list("session_id", "timestamp"):
        project_days[project_by_session[session_id]].add(timestamp.date())

    with transaction.atomic():
        Session.objects.filter(id__in=deleted_ids, provider=payload.provider).delete()
        for project_id in {row["project_id"] for row in rows}:
            update_project_metadata(project_id)

        all_days: set[date_cls] = set()
        for project_id, days in project_days.items():
            DailyActivity.recalculate_for_days(
                project_id, days, payload.provider, do_global=False,
            )
            all_days.update(days)
        DailyActivity.recalculate_for_days(
            None, all_days, payload.provider, do_global=False,
        )

    return deleted_ids


def _apply_update_project_metadata_payload(payload: UpdateProjectMetadataPayload) -> None:
    """Apply end-of-sync metadata updates for one project, atomically."""
    from twicc.core.models import Project, Session, SessionType
    from twicc.projects import ensure_project_git_root, update_project_total_cost

    with transaction.atomic():
        if (payload.recalc_sessions_count
                or payload.recalc_mtime
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
                if payload.recalc_sessions_count:
                    # Recompute from the DB (not a producer-supplied count):
                    # sessions_count is cross-provider, so a value counted in
                    # the producer could clobber a fresher one another
                    # provider's compute wrote. Same filter as
                    # update_project_metadata().
                    project.sessions_count = Session.objects.filter(
                        project_id=payload.project_id,
                        type=SessionType.SESSION,
                        created_at__isnull=False,
                        user_message_count__gt=0,
                        hidden=False,
                    ).count()
                    update_fields.append("sessions_count")
                if payload.recalc_mtime:
                    # Recompute from the DB now that every session payload
                    # this provider pushed for the project has landed (FIFO).
                    project.mtime = _compute_project_mtime(payload.project_id)
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


def _apply_resolve_git_roots_payload(payload: ResolveProjectGitRootsPayload) -> None:
    """Resolve git_root for every non-stale project that has a directory.

    Run by the DB writer when it drains a ResolveProjectGitRootsPayload —
    after every prior FIFO project payload of the same sync run has
    committed — so the ``stale=False`` filter reflects this run's fresh
    state (a project being un-staled earlier in the same run is no longer
    wrongly excluded). ``ensure_project_git_root`` is idempotent and writes
    only when the resolved root actually changes.
    """
    from twicc.core.models import Project
    from twicc.projects import ensure_project_git_root

    # One atomic batch for the whole payload, like every other DB writer write.
    with transaction.atomic():
        for project in Project.objects.filter(directory__isnull=False, stale=False):
            ensure_project_git_root(project.id, project.directory)


def _apply_upsert_workflow_payload(payload: UpsertWorkflowPayload) -> None:
    """Persist a workflow run envelope (upsert by ``run_id``).

    Skips cleanly when the owning session row is absent (an upstream session
    payload was rejected, or the run file is an orphan) — same guard as
    subagent creation, to avoid an opaque IntegrityError on the FK.
    """
    from twicc.core.models import Session, Workflow

    project_id = (
        Session.objects.filter(id=payload.session_id).values_list("project_id", flat=True).first()
    )
    if project_id is None:
        logger.warning(
            "Skipping workflow %s: session %s does not exist",
            payload.run_id, payload.session_id,
        )
        return

    with transaction.atomic():
        # Boot backfill of a run TwiCC missed live = STATE 2. Like the live
        # ``_save_workflow_run`` path, swap the engine's truncated prompt/result
        # previews for the full values before storing — durable past Claude deleting
        # the run's files — and KEEP any existing synthesis (templates) so a resume
        # can re-synthesize a live STATE 1. ``enrich_previews`` is Claude-Code-
        # specific, but so are workflows, so the lazy import is fine.
        from twicc.providers.claude_code.workflow_synthesis import (
            enrich_previews,
            stamp_phase_states,
        )

        try:
            envelope = orjson.loads(payload.raw_json)
        except orjson.JSONDecodeError:
            envelope = {}
        enrich_previews(envelope, project_id, payload.session_id, payload.run_id)
        stamp_phase_states(envelope)
        cost, phases_cost = Workflow.compute_costs(payload.run_id, envelope)
        Workflow.objects.update_or_create(
            run_id=payload.run_id,
            defaults={
                "session_id": payload.session_id,
                "raw_json": orjson.dumps(envelope).decode(),
                "cost": cost,
                "phases_cost": phases_cost,
            },
        )


# =============================================================================
# Periodic-task job handlers (commands, usage, model retirement, pricing)
# =============================================================================
#
# Each handler runs synchronously on a worker thread (via ``sync_to_async`` in
# :func:`_settle_async_job`) inside its own ``transaction.atomic``, like
# every other DB writer write. The producer side prepared everything that does
# not touch DB (HTTP fetches, filesystem scans, parsing); only the actual
# diff/write happens here.


def _apply_desired_commands_job(job: _ApplyDesiredCommandsJob) -> dict[str, int]:
    """Reconcile ``Command`` rows for one ``(provider, activation_char)`` scope.

    Delegates to the existing shared helper :func:`apply_desired_commands`
    so the diff/apply logic stays in one place — wrapped here in
    ``transaction.atomic`` so the SELECT current + delete/create/update all
    commit (or roll back) together, and so the helper is never invoked from a
    different writer.
    """
    from twicc.providers.commands_sync import apply_desired_commands

    with transaction.atomic():
        return apply_desired_commands(
            provider=job.provider.value,
            activation_char=job.activation_char,
            desired=job.desired,
        )


def _apply_create_usage_snapshot_job(job: _CreateUsageSnapshotJob) -> object:
    """Insert one ``UsageSnapshot`` row from the producer-prepared ``fields``.

    The producer (each provider's ``usage_task``) parsed the API response
    into ``fields`` before submitting, so this is a single ``create()`` — no
    upstream parsing happens in the DB writer thread.
    """
    from twicc.core.models import UsageSnapshot

    with transaction.atomic():
        return UsageSnapshot.objects.create(**job.fields)


def _apply_retire_sessions_job(job: _RetireSessionsJob) -> int:
    """Apply per-session field updates for retirement-driven upgrades.

    ``updates`` maps each session id to the field/value dict the producer
    wants applied (``selected_model``, possibly ``effort``). Sessions that no
    longer exist (e.g. the row was deleted between the producer's iteration
    and the DB writer's apply) contribute 0 to the count — a transient miss,
    not an error. Returns the number of rows actually updated.
    """
    from twicc.core.models import Session

    if not job.updates:
        return 0
    count = 0
    with transaction.atomic():
        for session_id, fields in job.updates.items():
            count += Session.objects.filter(id=session_id).update(**fields)
    return count


def _apply_persist_provider_prices_job(job: _PersistProviderPricesJob) -> dict[str, int]:
    """Persist OpenRouter prices for one provider.

    Delegates to the existing :func:`persist_provider_prices` helper so the
    SELECT-latest / INSERT-on-change logic stays in one place — wrapped here
    in ``transaction.atomic`` so the per-row SELECT and INSERT pairs commit
    (or roll back) together, and so the helper is never invoked from a
    different writer.
    """
    from twicc.pricing import persist_provider_prices

    with transaction.atomic():
        return persist_provider_prices(job.provider, job.prices)


def _apply_persist_model_benchmarks_job(job: _PersistModelBenchmarksJob) -> dict[str, int]:
    """Persist DeepSWE model benchmarks (cross-provider).

    Delegates to :func:`persist_benchmarks` so the upsert logic stays in one
    place — wrapped here in ``transaction.atomic`` so every per-row upsert
    commits (or rolls back) together and the helper is never invoked from a
    different writer.
    """
    from twicc.benchmarks import persist_benchmarks

    with transaction.atomic():
        return persist_benchmarks(job.benchmarks)


def _apply_mark_sessions_indexed_job(job: _MarkSessionsIndexedJob) -> int:
    """Bump ``search_version`` on a batch of sessions that just hit Tantivy.

    One ``UPDATE Session SET search_version = ? WHERE id IN (...)`` per job,
    wrapped in ``transaction.atomic`` for consistency with every other apply
    on this writer. Sessions that no longer exist (e.g. the row was deleted
    between the indexer's ``_index_session`` and the DB writer's apply)
    contribute 0 to the count — a transient miss, not an error. Returns the
    number of rows actually updated so the producer can sanity-check the
    batch volumetry in its logs if needed.
    """
    from django.conf import settings

    from twicc.core.models import Session

    if not job.session_ids:
        return 0
    with transaction.atomic():
        return Session.objects.filter(id__in=job.session_ids).update(
            search_version=settings.CURRENT_SEARCH_VERSION
        )
