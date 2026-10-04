"""Thread-owned event monitors and their ordered, generation-bound state writer."""

import asyncio
from collections import deque
from contextlib import suppress
from datetime import datetime
import logging
import queue
import threading
from typing import NamedTuple

from django.db import close_old_connections
from django.db.models import Case, F, Value, When
from django.db.models.functions import Greatest

from twicc.agent.states import AgentState
from twicc.cli import _twicc_info
from twicc.cli._drop_request import transport
from twicc.cli._wait_reply import POLL_INTERVAL_SECONDS, _SessionWait
from twicc.core.models import McpEventSubscription, Session, SessionItem
from twicc.core.serializers import session_compute_ready
from twicc.mcp.events import SYSTEM_CLOCK
from twicc.mcp.events.delivery import build_occurrence, fit_body
from twicc.mcp.events.methods import SubscriptionSnapshot
from twicc.mcp.events.prompts import first_non_command_prompt
from twicc.mcp.oauth import storage
from twicc.paths import get_data_dir

logger = logging.getLogger(__name__)

EPOCH_BACKSTOP_SECONDS = 5


class AddCommand(NamedTuple):
    snapshot: SubscriptionSnapshot


class UpdateCommand(NamedTuple):
    snapshot: SubscriptionSnapshot


class RemoveCommand(NamedTuple):
    id: str
    created_at: datetime


class RebaseCommand(NamedTuple):
    session_id: str


class StopCommand(NamedTuple):
    pass


class SessionSnapshot(NamedTuple):
    history_epoch: int
    provider: str
    compute_version: int
    last_line: int
    title: str | None

    @property
    def ready(self):
        return session_compute_ready(self)


def read_session_snapshot(session_id):
    """Read epoch, readiness, last line and title in one database snapshot."""
    row = Session.objects.filter(pk=session_id).values_list(*SessionSnapshot._fields).first()
    return SessionSnapshot(*row) if row is not None else None


class Monitor:
    """Mutable state owned exclusively by the runtime's worker thread."""

    def __init__(self, snapshot):
        # The initial row is authoritative only when this generation is loaded.
        for field in snapshot._fields:
            setattr(self, field, getattr(snapshot, field))
        self.first = self.cursor_line < self.initial_last_line
        self.reported_request_ids = set()
        self.reported_request_order = deque()
        self.dormant = False
        self.wait = None
        self.session_snapshot = None
        self.has_session_snapshot = False
        self.session_read_at = None
        self.pending_rebase = self.numbering is None

    @property
    def generation(self):
        return self.id, self.created_at


class CursorWrite(NamedTuple):
    id: str
    created_at: datetime
    numbering: int | None
    cursor_line: int
    cursor_at: float | None


class TurnWrite(NamedTuple):
    id: str
    created_at: datetime
    turn_open: bool
    turn_started_at: float | None
    turn_opened_by: str
    turn_start_line: int


class Emission(NamedTuple):
    """Frozen delivery input; its cursor is persisted only after delivery ends."""

    id: str
    created_at: datetime
    event_id: str
    body: bytes
    cursor: CursorWrite | None


class RebaseWrite(NamedTuple):
    id: str
    created_at: datetime
    old_numbering: int | None
    numbering: int
    cursor_line: int
    initial_last_line: int
    turn_open: bool
    turn_started_at: float | None
    turn_opened_by: str
    turn_start_line: int


class WriteBarrier(NamedTuple):
    future: asyncio.Future


class EventsRuntime:
    def __init__(self, *, clock=SYSTEM_CLOCK, data_dir=None, post_emission=None):
        self.clock = clock
        self.data_dir = str(get_data_dir().resolve()) if data_dir is None else str(data_dir)
        # These queues survive consumer restarts. Only the loop consumes writes.
        self.commands = queue.Queue()
        self.writes = asyncio.Queue()
        self.loop = None
        self.thread = None
        self.writer_task = None
        self.stop_requested = threading.Event()
        # Only _worker and its synchronous helpers access this table.
        self.monitors = {}
        self.emission_sink = post_emission

    def add(self, snapshot):
        self.commands.put(AddCommand(snapshot))

    def update(self, snapshot):
        self.commands.put(UpdateCommand(snapshot))

    def remove(self, identity, created_at):
        self.commands.put(RemoveCommand(identity, created_at))

    def rebase(self, session_id):
        self.commands.put(RebaseCommand(session_id))

    async def start(self):
        """Start the consumers. Supervisor and delivery lifecycle extend this seam."""
        self.loop = asyncio.get_running_loop()
        self.writer_task = asyncio.create_task(self.run_writer(), name="mcp-events-writer")
        self.thread = threading.Thread(target=self._worker, name="mcp-events-worker", daemon=True)
        self.thread.start()

    def request_stop(self):
        if not self.stop_requested.is_set():
            self.stop_requested.set()
            self.commands.put(StopCommand())

    async def close(self):
        """Join without blocking the loop, then drain posted state writes."""
        self.request_stop()
        if self.thread is not None:
            await asyncio.to_thread(self.thread.join, 5)
        if self.writer_task is None:
            return
        if self.writer_task.done():
            self.writer_task = asyncio.create_task(self.run_writer(), name="mcp-events-writer")
        barrier = self.loop.create_future()
        # Land behind call_soon_threadsafe writes already posted by the worker.
        self.loop.call_soon(self.writes.put_nowait, WriteBarrier(barrier))
        try:
            await asyncio.wait_for(barrier, 2)
        except TimeoutError:
            logger.warning("Timed out draining event state writes")
        finally:
            self.writer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.writer_task

    def _worker(self):
        # Plain Thread does not inherit the backend-loop ContextVar.
        transport.backend_loop.set(self.loop)
        try:
            close_old_connections()
            self._load_monitors()
            while self._drain_commands():
                self._tick()
                self.stop_requested.wait(POLL_INTERVAL_SECONDS)
        finally:
            close_old_connections()

    def _load_monitors(self):
        self.monitors = {}
        rows = McpEventSubscription.objects.filter(
            data_dir=self.data_dir, refresh_before__gt=self.clock.utcnow(), connection__revoked_at__isnull=True,
        )
        for row in rows:
            self._add_monitor(SubscriptionSnapshot.from_row(row))

    def _new_wait(self, monitor, *, cursor_line=None):
        info = _twicc_info.resolve_live_twicc()
        return _SessionWait(
            monitor.session_id, monitor.cursor_line if cursor_line is None else cursor_line,
            started=self.clock.monotonic(),
            twicc_pid=info.pid if info is not None else None, want_text=True,
            wait_background=monitor.arguments.get("wait_background", False),
        )

    def _read_monitor_session(self, monitor):
        snapshot = read_session_snapshot(monitor.session_id)
        monitor.session_snapshot = snapshot
        monitor.has_session_snapshot = True
        monitor.session_read_at = self.clock.monotonic()
        epoch = snapshot.history_epoch if snapshot is not None else 0
        # A missing row cannot clear a rebase already waiting for its session.
        monitor.pending_rebase = monitor.pending_rebase or monitor.numbering != epoch
        return snapshot

    def _add_monitor(self, snapshot):
        monitor = Monitor(snapshot)
        monitor.wait = self._new_wait(monitor)
        self._read_monitor_session(monitor)
        self.monitors[snapshot.id] = monitor

    def _update_monitor(self, monitor, snapshot):
        for field in ("refresh_before", "secret", "previous_secret", "previous_secret_until", "arguments"):
            setattr(monitor, field, getattr(snapshot, field))
        if monitor.dormant and monitor.refresh_before > self.clock.utcnow():
            monitor.wait = self._new_wait(monitor)
            self._read_monitor_session(monitor)
            monitor.dormant = False

    def _drain_commands(self):
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return True
            try:
                if isinstance(command, StopCommand):
                    return False
                if isinstance(command, RemoveCommand):
                    monitor = self.monitors.get(command.id)
                    if monitor is not None and monitor.generation == (command.id, command.created_at):
                        del self.monitors[command.id]
                elif isinstance(command, RebaseCommand):
                    for monitor in self.monitors.values():
                        if monitor.session_id == command.session_id:
                            self._read_monitor_session(monitor)
                elif isinstance(command, (AddCommand, UpdateCommand)):
                    snapshot = command.snapshot
                    monitor = self.monitors.get(snapshot.id)
                    if monitor is not None and monitor.generation == snapshot.generation:
                        self._update_monitor(monitor, snapshot)
                    elif isinstance(command, AddCommand):
                        self._add_monitor(snapshot)
                else:
                    raise TypeError(f"Unknown event command: {type(command).__name__}")
            finally:
                self.commands.task_done()

    def _tick(self):
        now = self.clock.utcnow()
        for monitor in self.monitors.values():
            if monitor.refresh_before <= now:
                monitor.dormant = True
            if monitor.dormant:
                continue
            snapshot = monitor.session_snapshot
            if (monitor.pending_rebase or monitor.session_read_at is None
                    or self.clock.monotonic() - monitor.session_read_at >= EPOCH_BACKSTOP_SECONDS):
                snapshot = self._read_monitor_session(monitor)
            if monitor.pending_rebase:
                self._apply_rebase(monitor, snapshot)
                continue
            self._tick_monitor(monitor)

    def _apply_rebase(self, monitor, snapshot):
        """Prepare a new numbering from one ready snapshot, then post its CAS."""
        if snapshot is None or not snapshot.ready:
            return
        from twicc.agent.registry import get_agent_manager_registry

        info = get_agent_manager_registry().get_agent_info(monitor.session_id)
        working = info is not None and info.state in (AgentState.STARTING, AgentState.ASSISTANT_TURN)
        write = RebaseWrite(
            *monitor.generation, monitor.numbering, snapshot.history_epoch, snapshot.last_line, snapshot.last_line,
            working, info.state_changed_at if working else None, "initial" if working else "", snapshot.last_line,
        )
        wait = self._new_wait(monitor, cursor_line=snapshot.last_line)
        self.post_write(write)
        self._apply_turn(monitor, write)
        monitor.cursor_line = monitor.initial_last_line = snapshot.last_line
        monitor.numbering = snapshot.history_epoch
        monitor.first = False
        monitor.wait = wait
        monitor.pending_rebase = False

    @staticmethod
    def _ready_end(previous, snapshot, had_previous):
        # A read with no row is ready in epoch zero. No previous read is not.
        def readiness(value):
            return (value.ready, value.history_epoch, value.last_line) if value is not None else (True, 0, 0)

        before, after = readiness(previous), readiness(snapshot)
        return had_previous and before[0] and before == after

    def _tick_monitor(self, monitor):
        """Detect one conclusion with the CLI wait and Rules A/B."""
        from twicc.agent.registry import get_agent_manager_registry

        tick_started_at = self.clock.utcnow()
        info = get_agent_manager_registry().get_agent_info(monitor.session_id)
        self._detect_new_turn(monitor, info)
        previous, had_previous = monitor.session_snapshot, monitor.has_session_snapshot
        reply = monitor.wait.step()
        if reply is None:
            return
        outcome = reply["outcome"]
        request = None
        guard_drop = False
        if outcome == "ended":
            if not monitor.turn_open:
                return
            if monitor.turn_opened_by == "transition" and reply["line_num"] is None and info is not None:
                # The wait loads the provider before it can conclude a row's end.
                provider = monitor.wait.session.provider if monitor.wait.session is not None else info.provider
                guard_drop = first_non_command_prompt(
                    monitor.session_id, provider, monitor.turn_start_line,
                ) is None
        elif outcome == "awaiting_user_input":
            request = next((request for request in (info.pending_requests if info is not None else ())
                            if request.request_id not in monitor.reported_request_ids), None)
            if request is None:
                return

        item_timestamp = None
        if outcome in ("replied", "provider_error"):
            item_timestamp = SessionItem.objects.filter(
                session_id=monitor.session_id, line_num=reply["line_num"],
            ).values_list("timestamp", flat=True).first()
        # The emission phase's final database read supplies a consistent epoch,
        # title and transcript end, including when the guard drops the end.
        snapshot = self._read_monitor_session(monitor)
        if monitor.pending_rebase:
            return
        if outcome == "ended" and not self._ready_end(previous, snapshot, had_previous):
            return
        if guard_drop:
            turn = TurnWrite(*monitor.generation, False, monitor.turn_started_at, monitor.turn_opened_by,
                             snapshot.last_line if snapshot is not None else monitor.turn_start_line)
            self.post_write(turn)
            self._apply_turn(monitor, turn)
            return

        occurrence = build_occurrence(
            monitor.id, monitor.session_id, snapshot.title if snapshot is not None else None, reply,
            numbering=snapshot.history_epoch if snapshot is not None else 0,
            last_line=snapshot.last_line if snapshot is not None else 0,
            tick_started_at=tick_started_at, item_timestamp=item_timestamp, pending_request=request,
        )
        body = fit_body(occurrence)
        conclusion_time = (item_timestamp or tick_started_at).timestamp()
        turn_open, started, opened_by, start_line = (
            monitor.turn_open, monitor.turn_started_at, monitor.turn_opened_by, monitor.turn_start_line,
        )
        cursor = None
        wait = monitor.wait
        request_ids, request_order = monitor.reported_request_ids, monitor.reported_request_order
        if outcome == "awaiting_user_input":
            if not turn_open:
                turn_open, started, start_line = True, request.created_at, monitor.cursor_line
            opened_by = "awaiting"
            # Prepare bounded memory before posting, just like the fresh wait.
            request_ids, request_order = set(request_ids), deque(request_order)
            request_ids.add(request.request_id)
            request_order.append(request.request_id)
            while len(request_order) > 256:
                request_ids.discard(request_order.popleft())
        else:
            if outcome == "ended":
                turn_open = False
                if snapshot is not None:
                    start_line = snapshot.last_line
                next_cursor = wait.scanned_up_to
            else:
                next_cursor = reply["line_num"]
                if opened_by != "history" and turn_open and started is not None and conclusion_time < started:
                    start_line = max(start_line, next_cursor)
                    if opened_by != "awaiting":
                        opened_by = "transition"
                else:
                    turn_open = False
            if monitor.first:
                next_cursor = max(next_cursor, monitor.initial_last_line)
            cursor = CursorWrite(*monitor.generation, monitor.numbering, next_cursor,
                                 max(monitor.cursor_at, conclusion_time))
            wait = self._new_wait(monitor, cursor_line=next_cursor)
        turn = TurnWrite(*monitor.generation, turn_open, started, opened_by, start_line)
        emission = Emission(*monitor.generation, occurrence["eventId"], body, cursor) if body is not None else None

        # All queries, formatting and state preparation finish before posting.
        # A shutdown drop leaves the conclusion's state untouched for restart.
        if self.stop_requested.is_set():
            return
        if body is not None and not self.post_emission(emission):
            return
        self.post_write(turn)
        if body is None and cursor is not None:
            self.post_write(cursor)
        self._apply_turn(monitor, turn)
        monitor.wait = wait
        monitor.reported_request_ids, monitor.reported_request_order = request_ids, request_order
        if cursor is not None:
            monitor.cursor_line, monitor.cursor_at = cursor.cursor_line, cursor.cursor_at
            monitor.first = False

    def _detect_new_turn(self, monitor, info):
        if (info is None or info.state not in (AgentState.STARTING, AgentState.ASSISTANT_TURN)
                or info.previous_state == info.state or info.state_changed_at <= monitor.cursor_at
                or (monitor.turn_started_at is not None and info.state_changed_at <= monitor.turn_started_at)):
            return
        old_cursor = monitor.cursor_line
        next_cursor = max(old_cursor, monitor.wait.scanned_up_to)
        ignored = monitor.wait.last_ignored
        opened_by, start_line = monitor.turn_opened_by, monitor.turn_start_line
        if not monitor.turn_open:
            opened_by = "transition"
            start_line = max(old_cursor, start_line, ignored.line_num if ignored is not None else 0)
        elif ignored is not None:
            opened_by, start_line = "transition", next_cursor
        turn = TurnWrite(*monitor.generation, True, info.state_changed_at, opened_by, start_line)
        wait = self._new_wait(monitor, cursor_line=next_cursor)
        cursor = CursorWrite(*monitor.generation, monitor.numbering, next_cursor, None)
        self.post_write(turn)
        if next_cursor > old_cursor:
            self.post_write(cursor)
        self._apply_turn(monitor, turn)
        monitor.cursor_line, monitor.wait = next_cursor, wait

    @staticmethod
    def _apply_turn(monitor, turn):
        monitor.turn_open = turn.turn_open
        monitor.turn_started_at = turn.turn_started_at
        monitor.turn_opened_by = turn.turn_opened_by
        monitor.turn_start_line = turn.turn_start_line

    def post_emission(self, emission):
        """Injected posting seam; delivery and lifecycle tasks attach the sink."""
        if self.stop_requested.is_set():
            return False
        if self.emission_sink is None:
            raise RuntimeError("Event delivery is not configured")
        self.emission_sink(emission)
        return True

    def post_write(self, item):
        """Keep worker posting order without touching asyncio.Queue from a thread."""
        self.loop.call_soon_threadsafe(self.writes.put_nowait, item)

    async def run_writer(self):
        """Apply FIFO writes under the backend write lock; isolate item failures."""
        while True:
            item = await self.writes.get()
            try:
                if isinstance(item, WriteBarrier):
                    if not item.future.done():
                        item.future.set_result(None)
                else:
                    await storage.write(lambda: self._apply_write(item))
            except Exception:
                logger.exception("Event state write failed for subscription %s", getattr(item, "id", "unknown"))
            finally:
                self.writes.task_done()

    def _apply_write(self, item):
        """Only run through storage.write, never directly from the worker."""
        rows = McpEventSubscription.objects.filter(id=item.id, created_at=item.created_at)
        if isinstance(item, CursorWrite):
            values = {
                "cursor_line": Case(
                    When(numbering=item.numbering, then=Greatest(F("cursor_line"), Value(item.cursor_line))),
                    default=F("cursor_line"),
                ),
            }
            if item.cursor_at is not None:
                values["cursor_at"] = Greatest(F("cursor_at"), Value(item.cursor_at))
        elif isinstance(item, TurnWrite):
            values = {name: getattr(item, name) for name in (
                "turn_open", "turn_started_at", "turn_opened_by", "turn_start_line",
            )}
        elif isinstance(item, RebaseWrite):
            rows = rows.filter(numbering=item.old_numbering)
            values = {name: getattr(item, name) for name in (
                "cursor_line", "initial_last_line", "numbering", "turn_open", "turn_started_at",
                "turn_opened_by", "turn_start_line",
            )}
        else:
            raise TypeError(f"Unknown event state write: {type(item).__name__}")
        return rows.update(**values, updated_at=self.clock.utcnow())
