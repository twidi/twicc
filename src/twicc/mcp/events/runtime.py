"""Thread-owned event monitors and their ordered, generation-bound state writer."""

import asyncio
from contextlib import suppress
from datetime import datetime
import logging
import queue
import threading
from typing import NamedTuple

from django.db import close_old_connections
from django.db.models import Case, F, Value, When
from django.db.models.functions import Greatest

from twicc.cli import _twicc_info
from twicc.cli._drop_request import transport
from twicc.cli._wait_reply import POLL_INTERVAL_SECONDS, _SessionWait
from twicc.core.models import McpEventSubscription, Session
from twicc.core.serializers import session_compute_ready
from twicc.mcp.events import SYSTEM_CLOCK
from twicc.mcp.events.methods import SubscriptionSnapshot
from twicc.mcp.oauth import storage
from twicc.paths import get_data_dir

logger = logging.getLogger(__name__)


class AddCommand(NamedTuple):
    snapshot: SubscriptionSnapshot


class UpdateCommand(NamedTuple):
    snapshot: SubscriptionSnapshot


class RemoveCommand(NamedTuple):
    id: str
    created_at: datetime


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
        self.dormant = False
        self.wait = None
        self.session_snapshot = None
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
    def __init__(self, *, clock=SYSTEM_CLOCK, data_dir=None):
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

    def add(self, snapshot):
        self.commands.put(AddCommand(snapshot))

    def update(self, snapshot):
        self.commands.put(UpdateCommand(snapshot))

    def remove(self, identity, created_at):
        self.commands.put(RemoveCommand(identity, created_at))

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

    def _new_wait(self, monitor):
        info = _twicc_info.resolve_live_twicc()
        return _SessionWait(
            monitor.session_id, monitor.cursor_line, started=self.clock.monotonic(),
            twicc_pid=info.pid if info is not None else None, want_text=True,
            wait_background=monitor.arguments.get("wait_background", False),
        )

    def _read_monitor_session(self, monitor):
        snapshot = read_session_snapshot(monitor.session_id)
        monitor.session_snapshot = snapshot
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
            if not monitor.dormant and not monitor.pending_rebase:
                self._tick_monitor(monitor)

    def _tick_monitor(self, monitor):
        """Detection extension point; implemented by the detection task."""

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
