"""Async, bounded file downloads with explicit ownership of blocking operations."""

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from contextvars import ContextVar
from typing import BinaryIO

import aiofiles.threadpool
from django.http import StreamingHttpResponse
from django.utils.cache import add_never_cache_headers
from django.utils.http import content_disposition_header

logger = logging.getLogger(__name__)

# Keep cancellation cleanup alive after the request task releases its response.
_cleanup_tasks = set()
# Producer shutdown must not queue behind the reads that it must unblock.
_cleanup_executor = ThreadPoolExecutor(thread_name_prefix="download-cleanup")
_request_downloads = ContextVar("request_downloads", default=None)


def _retain(awaitable):
    task = asyncio.create_task(awaitable)
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)
    return task


async def _release_preparation(preparation):
    try:
        response = await asyncio.shield(preparation)
        if response is not None:
            await response._release()
    except Exception:
        # A failed factory owns its partial acquisition. Retrieve its error
        # here as well when the original caller no longer awaits it.
        logger.warning("Download preparation or cleanup failed", exc_info=True)


async def prepare_download_response(factory: Callable[[], "AsyncFileResponse | None"]) -> "AsyncFileResponse | None":
    """Prepare off-loop; retain a late result after acquisition cancellation.

    A factory owns any partially acquired resources if it raises. A returned
    response transfers that ownership to the request registry. Direct callers
    outside the wrapper own successful responses and must close them.
    """
    preparation = asyncio.create_task(asyncio.to_thread(factory))
    registry = _request_downloads.get()
    if registry is not None:
        registry.append(preparation)
    try:
        return await asyncio.shield(preparation)
    except asyncio.CancelledError:
        if registry is None:
            _retain(_release_preparation(preparation))
        raise


class DownloadCleanupMiddleware:
    """Retain download ownership for the complete ASGI request lifetime.

    The registry survives Django middleware task adaptation through ContextVar
    propagation. It also covers initial-header send failure, where Django may
    skip both body iteration and response.close().
    """

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.application(scope, receive, send)
        registry = []
        token = _request_downloads.set(registry)
        try:
            return await self.application(scope, receive, send)
        finally:
            _request_downloads.reset(token)
            if registry:
                async def release_all():
                    await asyncio.gather(*(_release_preparation(item) for item in registry))

                cleanup = _retain(release_all())
                if not asyncio.current_task().cancelling():
                    await asyncio.shield(cleanup)


class AsyncFileResponse(StreamingHttpResponse):
    """Stream an already-open binary reader in bounded blocks off the event loop.

    ``finish`` validates source completion off-loop, after all reads succeed.
    ``close`` owns both the reader and its source when provided. It can run
    while a read or finish operation is still pending. It must unblock source
    operations before safely closing the reader (for Git: stop the process,
    then close stdout). Default cleanup waits for pending reads before close.
    """

    block_size = 64 * 1024

    def __init__(
        self,
        file: BinaryIO,
        *,
        filename: str,
        content_type: str,
        size: int | None = None,
        head: bool = False,
        finish: Callable[[], None] | None = None,
        close: Callable[[], None] | None = None,
    ):
        self._file = file
        self._size = size
        self._head = head
        self._finish = finish
        self._source_close = close
        self._loop = None
        self._pending = None
        self._cleanup_lock = threading.Lock()
        self._cleanup_started = False
        self._cleanup_done = Future()
        super().__init__(self._chunks(), content_type=content_type)
        if size is not None:
            self["Content-Length"] = str(size)
        self["Content-Disposition"] = content_disposition_header(True, filename)
        self["X-Content-Type-Options"] = "nosniff"
        add_never_cache_headers(self)
        self["CDN-Cache-Control"] = "no-store"

    async def _operation(self, awaitable):
        # Cancelling the caller cannot cancel ownership of the worker read.
        operation = self._pending = asyncio.create_task(awaitable)
        try:
            return await asyncio.shield(operation)
        finally:
            if operation.done():
                self._pending = None

    async def _chunks(self):
        self._loop = asyncio.get_running_loop()
        try:
            if self._head or self._cleanup_started:
                return
            reader = aiofiles.threadpool.wrap(self._file, loop=self._loop)
            remaining = self._size
            while not self._cleanup_started and (remaining is None or remaining > 0):
                count = self.block_size if remaining is None else min(self.block_size, remaining)
                chunk = await self._operation(reader.read(count))
                if self._cleanup_started:
                    return
                if not chunk:
                    if remaining is not None:
                        raise OSError("Download source ended before its announced length")
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk
            if self._finish is not None and not self._cleanup_started:
                await self._operation(asyncio.to_thread(self._finish))
        finally:
            await self._release()

    async def __aiter__(self):
        try:
            async for chunk in self.streaming_content:
                yield chunk
        finally:
            # Django closes this iterator when sending fails or is cancelled.
            # Nested async generators alone do not propagate aclose promptly.
            await self._release()

    def _claim_cleanup(self):
        with self._cleanup_lock:
            if self._cleanup_started:
                return False
            self._cleanup_started = True
            return True

    async def _cleanup(self):
        try:
            # A source callback can stop a producer whose pending read waits
            # for EOF. Waiting for that read first would deadlock cleanup.
            try:
                if self._source_close is not None:
                    await asyncio.get_running_loop().run_in_executor(_cleanup_executor, self._source_close)
            finally:
                # Even a failed source callback cannot abandon an in-flight
                # worker or lose a read exception that arrives after cancellation.
                if self._pending is not None:
                    with suppress(Exception):
                        await asyncio.shield(self._pending)
            if self._source_close is None:
                await asyncio.get_running_loop().run_in_executor(_cleanup_executor, self._file.close)
        except BaseException as exc:
            self._cleanup_done.set_exception(exc)
            logger.warning("Download source cleanup failed", exc_info=True)
        else:
            self._cleanup_done.set_result(None)

    def _start_cleanup(self):
        self._loop = asyncio.get_running_loop()
        if self._claim_cleanup():
            _retain(self._cleanup())

    async def _release(self):
        self._start_cleanup()
        if not asyncio.current_task().cancelling():
            await asyncio.shield(asyncio.wrap_future(self._cleanup_done))

    def close(self):
        """Keep Django's synchronous fallback; never block an active event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is not None and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._release(), self._loop).result()
            elif self._claim_cleanup():
                try:
                    (self._source_close or self._file.close)()
                except BaseException as exc:
                    self._cleanup_done.set_exception(exc)
                    raise
                else:
                    self._cleanup_done.set_result(None)
            else:
                self._cleanup_done.result()
        else:
            # Claim immediately: close followed by iteration cannot read ahead
            # before the scheduled cleanup task gets its first turn.
            self._start_cleanup()
        super().close()
