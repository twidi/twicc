"""Event-driven durable statistics calculation on one owned reader thread."""
import asyncio
import logging
from asyncio import sleep
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import partial

from django.db import connections, transaction

from twicc.providers import statistics_prepare as prepare
from twicc.providers import statistics_publication as publication
from twicc.providers.statistics_queue import QueueBatch, acknowledge_queue, capture_queue, install_queue_wakeup

COLLECTION_SECONDS = 2.0
RETRY_SECONDS = 1.0
MAX_RETRY_SECONDS = 60.0
logger = logging.getLogger(__name__)


def _close_connections():
    connections.close_all()


def _workflow_owners(workflow_ids):
    from twicc.core.models import Workflow
    return tuple(Workflow.objects.filter(pk__in=workflow_ids).values_list('session_id', flat=True).distinct())


def _acknowledge(max_id):
    with transaction.atomic():
        acknowledge_queue(max_id)


async def _drain(future):
    """Retain thread ownership through repeated caller cancellation."""
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            # Inspect the completed future below. Pending caller cancellation
            # takes precedence over a worker failure during drainage.
            break
    if cancelled:
        # Retrieve exceptions even when cancellation takes precedence.
        if not future.cancelled():
            future.exception()
        raise asyncio.CancelledError
    return future.result()


class StatisticsConsumer:
    def __init__(self, wakeup: asyncio.Event | None = None):
        self.wakeup = wakeup if wakeup is not None else asyncio.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='statistics-reader')
        self._task = None
        self._closing = None

    def start(self) -> None:
        if self._task is not None or self._closing is not None:
            raise RuntimeError('Statistics consumer already started or closed')
        self._task = asyncio.create_task(self._run())
        install_queue_wakeup(self.wake)

    def wake(self) -> None:
        self.wakeup.set()

    async def _read(self, function, /, *args):
        future = asyncio.get_running_loop().run_in_executor(self._executor, partial(function, *args))
        return await _drain(future)

    async def _run(self):
        retry = RETRY_SECONDS
        while True:
            try:
                self.wakeup.clear()
                if await self._read(capture_queue) is None:
                    await self.wakeup.wait()
                    continue
                await sleep(COLLECTION_SECONDS)
                batch = await self._read(capture_queue)
                if batch is not None:
                    await self.run_batch(batch)
                retry = RETRY_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Statistics calculation failed; retaining queue for retry')
                await sleep(retry)
                retry = min(retry * 2, MAX_RETRY_SECONDS)

    async def run_batch(self, batch: QueueBatch) -> None:
        roots = tuple(dict.fromkeys(batch.root_ids))
        tree = await self._read(prepare.prepare_tree_costs, roots, batch.force_cost_session_ids)
        await publication.publish_tree_costs(tree)
        for project in dict.fromkeys(tree.project_ids):
            value = await self._read(prepare.prepare_project_cost, project)
            await publication.publish_project_cost(project, value)
        for workflow in dict.fromkeys(tree.workflow_ids):
            cost, phases = await self._read(prepare.prepare_workflow_cost, workflow)
            await publication.publish_workflow_cost(workflow, cost, phases)
        days = tuple(dict.fromkeys(await self._read(prepare.prepare_affected_days, roots)))
        for day in days:
            value = await self._read(prepare.prepare_daily, day)
            await publication.publish_daily(day, value)
        weeks = dict.fromkeys(day._replace(day=day.day - timedelta(days=day.day.weekday())) for day in days)
        for week in weeks:
            value = await self._read(prepare.prepare_weekly, week)
            await publication.publish_weekly(week, value)
        owners = await self._read(_workflow_owners, tree.workflow_ids)
        await publication.notify_statistics_published(roots, tree.project_ids, owners)
        from twicc.providers.db_writer import publish_statistics_sync
        await publish_statistics_sync(_acknowledge, batch.max_id)

    async def _close(self):
        install_queue_wakeup(None)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        try:
            await self._read(_close_connections)
        finally:
            await asyncio.to_thread(self._executor.shutdown, wait=True)

    async def close(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await _drain(self._closing)
