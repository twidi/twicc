"""Bounded download bodies and ownership across async and synchronous close paths."""

import asyncio
import io
import threading
from contextlib import suppress

import pytest
from django.core.handlers.asgi import ASGIHandler

from twicc.http_streams import AsyncFileResponse


class Reader(io.BytesIO):
    """A standard binary reader with observable reads and resource release."""

    def __init__(self, data=b"", *, fail=False, hold=False):
        super().__init__(data)
        self.requests = []
        self.read_threads = []
        self.close_threads = []
        self.fail = fail
        self.hold = hold
        self.started = threading.Event()
        self.release = threading.Event()
        self.settled = threading.Event()
        self.closed_event = threading.Event()
        self.closed_during_read = False

    def read(self, size=-1):
        self.requests.append(size)
        self.read_threads.append(threading.get_ident())
        self.started.set()
        try:
            if self.hold:
                if not self.release.wait(3):
                    raise TimeoutError("Test did not release the read")
            if self.fail:
                raise OSError("Source read failed")
            return super().read(size)
        finally:
            self.settled.set()

    def close(self):
        self.closed_during_read = self.started.is_set() and not self.settled.is_set()
        self.close_threads.append(threading.get_ident())
        super().close()
        self.closed_event.set()


def response_for(reader, **kwargs):
    return AsyncFileResponse(reader, filename="file.bin", content_type="application/octet-stream", **kwargs)


async def wait_event(event):
    assert await asyncio.to_thread(event.wait, 2), "Resource lifecycle did not progress"


async def body(response):
    return b"".join([chunk async for chunk in response])


@pytest.mark.asyncio
@pytest.mark.parametrize("size,expected", [(0, b""), (3, b"abc"), (6, b"abcdef"), (None, b"abcdef")])
async def test_known_and_unknown_lengths_send_exact_bytes(size, expected):
    reader = Reader(b"abcdef")
    response = response_for(reader, size=size)
    assert response.is_async
    assert await body(response) == expected
    assert reader.closed
    assert response.get("Content-Length") == (str(size) if size is not None else None)
    assert all(0 < count <= 65536 for count in reader.requests)
    assert threading.get_ident() not in reader.read_threads + reader.close_threads


@pytest.mark.asyncio
async def test_newline_free_body_is_bounded_and_follows_consumption():
    reader = Reader(b"x" * (65536 * 3 + 7))
    response = response_for(reader)
    iterator = aiter(response)
    try:
        assert await anext(iterator) == b"x" * 65536
        await asyncio.sleep(0)
        assert reader.requests == [65536]
        rest = [chunk async for chunk in iterator]
        assert b"".join(rest) == b"x" * (65536 * 2 + 7)
        assert reader.requests == [65536] * 5
    finally:
        await iterator.aclose()
    assert reader.closed


@pytest.mark.asyncio
async def test_empty_unknown_source_completes():
    reader = Reader()
    assert await body(response_for(reader)) == b""
    assert reader.closed


@pytest.mark.asyncio
async def test_premature_eof_fails_instead_of_completing():
    reader = Reader(b"ab")
    response = response_for(reader, size=3)
    iterator = aiter(response)
    assert await anext(iterator) == b"ab"
    with pytest.raises(OSError):
        await anext(iterator)
    assert reader.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [b"", b"patch"])
async def test_finish_validates_source_off_loop_before_completion(data):
    reader = Reader(data)
    validated = []

    def finish():
        validated.append((reader.tell(), threading.get_ident(), reader.closed))

    assert await body(response_for(reader, finish=finish)) == data
    assert len(validated) == 1
    assert validated[0][0] == len(data)
    assert validated[0][2] is False
    assert validated[0][1] != threading.get_ident()
    assert reader.closed


@pytest.mark.asyncio
async def test_finish_failure_aborts_after_partial_output():
    reader = Reader(b"patch")

    def finish():
        raise OSError("Git failed")

    iterator = aiter(response_for(reader, finish=finish))
    assert await anext(iterator) == b"patch"
    with pytest.raises(OSError, match="Git failed"):
        await anext(iterator)
    assert reader.closed


@pytest.mark.asyncio
async def test_read_failure_skips_finish_and_releases_source():
    reader = Reader(fail=True)
    validated = []
    with pytest.raises(OSError, match="Source read failed"):
        await body(response_for(reader, finish=lambda: validated.append(True)))
    assert not validated
    assert reader.closed


@pytest.mark.asyncio
async def test_head_skips_body_and_finish_and_releases_source():
    reader = Reader(b"abcdef")
    validated = []
    response = response_for(reader, size=6, head=True, finish=lambda: validated.append(True))
    assert await body(response) == b""
    assert response["Content-Length"] == "6"
    assert not reader.requests
    assert not validated
    assert reader.closed


@pytest.mark.asyncio
async def test_headers_preserve_unicode_attachment_and_cache_policy():
    response = AsyncFileResponse(Reader(), filename="résumé.html", content_type="text/html", size=0)
    assert response["Content-Disposition"] == "attachment; filename*=utf-8''r%C3%A9sum%C3%A9.html"
    assert response["Content-Type"] == "text/html"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert set(response["Cache-Control"].split(", ")) == {"max-age=0", "no-cache", "no-store", "must-revalidate", "private"}
    assert response["CDN-Cache-Control"] == "no-store"
    assert "Expires" in response
    await body(response)


@pytest.mark.django_db
def test_synchronous_close_before_iteration_is_idempotent():
    reader = Reader(b"unused")
    response = response_for(reader)
    response.close()
    response.close()
    assert reader.closed
    assert len(reader.close_threads) == 1
    assert not reader.requests
    assert response.closed


@pytest.mark.asyncio
async def test_close_before_iteration_on_loop_runs_cleanup_off_loop():
    reader = Reader(b"unused")
    response = response_for(reader)
    response.close()
    response.close()
    await wait_event(reader.closed_event)
    assert len(reader.close_threads) == 1
    assert reader.close_threads[0] != threading.get_ident()
    assert await body(response) == b""
    assert not reader.requests


@pytest.mark.asyncio
async def test_success_and_repeated_response_close_release_custom_source_once():
    reader = Reader(b"data")
    closes = []

    def close():
        closes.append(threading.get_ident())
        reader.close()

    response = response_for(reader, close=close)
    assert await body(response) == b"data"
    response.close()
    await asyncio.to_thread(response.close)
    assert len(closes) == 1
    assert closes[0] != threading.get_ident()
    assert len(reader.close_threads) == 1


@pytest.mark.asyncio
async def test_cancelled_read_stays_owned_until_it_settles():
    reader = Reader(b"data", hold=True)
    validated = []
    response = response_for(reader, finish=lambda: validated.append(True))
    task = asyncio.create_task(body(response))
    try:
        await wait_event(reader.started)
        progressed = asyncio.Event()
        asyncio.get_running_loop().call_soon(progressed.set)
        await asyncio.wait_for(progressed.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        response.close()
        assert not reader.closed
        reader.release.set()
        await wait_event(reader.closed_event)
        assert not reader.closed_during_read
        assert not validated
        assert len(reader.close_threads) == 1
        assert reader.close_threads[0] != threading.get_ident()
    finally:
        reader.release.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_custom_close_unblocks_pending_source_read_after_cancellation():
    reader = Reader(b"data", hold=True)
    closes = []

    def close():
        closes.append(threading.get_ident())
        reader.release.set()  # Git cleanup terminates its process before closing stdout.
        assert reader.settled.wait(2)
        reader.close()

    response = response_for(reader, close=close)
    task = asyncio.create_task(body(response))
    try:
        await wait_event(reader.started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wait_event(reader.closed_event)
        assert len(closes) == 1
        assert closes[0] != threading.get_ident()
        assert not reader.closed_during_read
    finally:
        reader.release.set()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_early_iterator_close_releases_source_without_reading_ahead():
    reader = Reader(b"x" * 131072)
    iterator = aiter(response_for(reader))
    assert await anext(iterator) == b"x" * 65536
    await iterator.aclose()
    assert reader.closed
    assert reader.requests == [65536]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_asgi_sender_failure_or_cancellation_releases_paused_iterator(cancel):
    reader = Reader(b"x" * 131072)
    response = response_for(reader)
    sending = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            sending.set()
            if cancel:
                await asyncio.Future()
            raise OSError("Sender failed")

    task = asyncio.create_task(ASGIHandler().send_response(response, send))
    await sending.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else OSError):
        await task
    await wait_event(reader.closed_event)
    assert reader.requests == [65536]
    assert len(reader.close_threads) == 1


@pytest.mark.asyncio
async def test_close_then_immediate_iteration_does_not_start_a_read():
    reader = Reader(b"unused")
    response = response_for(reader)
    response.close()
    assert await body(response) == b""
    assert not reader.requests
    assert reader.closed


@pytest.mark.asyncio
async def test_close_after_yield_does_not_schedule_another_read():
    reader = Reader(b"x" * 131072)
    response = response_for(reader)
    iterator = aiter(response)
    assert await anext(iterator) == b"x" * 65536
    response.close()
    assert [chunk async for chunk in iterator] == []
    assert reader.requests == [65536]
    assert reader.closed


@pytest.mark.asyncio
async def test_custom_cleanup_starts_when_default_read_executor_is_saturated():
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    old_executor = loop._default_executor
    executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    reader = Reader(b"data", hold=True)
    started = asyncio.Event()
    loop_thread = threading.get_ident()
    closed = asyncio.Event()
    original_read = reader.read

    def read(size):
        loop.call_soon_threadsafe(started.set)
        return original_read(size)

    reader.read = read

    def close():
        assert threading.get_ident() != loop_thread
        reader.release.set()
        assert reader.settled.wait(2)
        reader.close()
        loop.call_soon_threadsafe(closed.set)

    task = asyncio.create_task(body(response_for(reader, close=close)))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Observe from the event loop: cleanup must not queue behind the held read.
        await asyncio.wait_for(closed.wait(), 1)
        assert not reader.closed_during_read
    finally:
        reader.release.set()
        loop._default_executor = old_executor
        await asyncio.to_thread(executor.shutdown, wait=True)


@pytest.mark.asyncio
async def test_cancellation_during_finish_unblocks_source_and_retains_cleanup():
    reader = Reader(b"data")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    close_started = threading.Event()

    def finish():
        started.set()
        assert release.wait(3)
        finished.set()

    def close():
        close_started.set()
        release.set()
        assert finished.wait(2)
        reader.close()

    response = response_for(reader, finish=finish, close=close)
    task = asyncio.create_task(body(response))
    try:
        await wait_event(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wait_event(close_started)
        response.close()
        await wait_event(reader.closed_event)
        assert finished.is_set()
        assert len(reader.close_threads) == 1
    finally:
        release.set()


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_cancel_cleanup():
    reader = Reader(b"data", hold=True)
    close_started = threading.Event()
    close_release = threading.Event()

    def close():
        close_started.set()
        assert close_release.wait(3)
        reader.release.set()
        assert reader.settled.wait(2)
        reader.close()

    response = response_for(reader, close=close)
    task = asyncio.create_task(body(response))
    try:
        await wait_event(reader.started)
        task.cancel()
        await wait_event(close_started)
        task.cancel()
        response.close()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not reader.closed
        close_release.set()
        await wait_event(reader.closed_event)
        assert len(reader.close_threads) == 1
    finally:
        close_release.set()
        reader.release.set()


@pytest.mark.asyncio
async def test_failing_custom_cleanup_still_owns_late_read_failure():
    reader = Reader(hold=True, fail=True)
    observed_errors = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: observed_errors.append(context))

    def close():
        reader.release.set()
        assert reader.settled.wait(2)
        reader.close()
        raise OSError("Cleanup failed after closing")

    task = asyncio.create_task(body(response_for(reader, close=close)))
    try:
        await wait_event(reader.started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wait_event(reader.closed_event)
        await asyncio.sleep(0)
        assert not observed_errors
    finally:
        reader.release.set()
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_preparation_returns_none_or_response_without_a_registry():
    from twicc.http_streams import prepare_download_response

    thread_ids = []

    def absent():
        thread_ids.append(threading.get_ident())

    assert await prepare_download_response(absent) is None
    assert thread_ids[0] != threading.get_ident()
    reader = Reader(b"data")
    response = await prepare_download_response(lambda: response_for(reader))
    assert not reader.closed
    assert await body(response) == b"data"


@pytest.mark.asyncio
async def test_cancelled_preparation_without_registry_closes_late_response():
    from twicc.http_streams import prepare_download_response

    started = threading.Event()
    release = threading.Event()
    reader = Reader(b"data")

    def factory():
        started.set()
        assert release.wait(3)
        return response_for(reader)

    task = asyncio.create_task(prepare_download_response(factory))
    try:
        await wait_event(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not reader.closed
        release.set()
        await wait_event(reader.closed_event)
        assert not reader.requests
    finally:
        release.set()


@pytest.mark.asyncio
async def test_initial_header_send_failure_cleans_prepared_response_through_real_handler(monkeypatch):
    from twicc.http_streams import DownloadCleanupMiddleware, prepare_download_response

    reader = Reader(b"unused")
    responses = []
    closed_by_django = []
    handler = ASGIHandler()

    async def view(request):
        # Simulate middleware adaptation into another task. The registry must survive it.
        async def adapted():
            response = await prepare_download_response(lambda: response_for(reader))
            responses.append(response)
            response.close = lambda: closed_by_django.append(True)
            return response

        return await asyncio.create_task(adapted())

    monkeypatch.setattr(handler, "_middleware_chain", view)
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Future()

    async def send(message):
        assert message["type"] == "http.response.start"
        raise OSError("Headers failed")

    with pytest.raises(OSError, match="Headers failed"):
        await DownloadCleanupMiddleware(handler)(
            {"type": "http", "method": "GET", "path": "/download", "headers": [], "query_string": b""},
            receive,
            send,
        )
    assert reader.closed
    assert not reader.requests
    assert not closed_by_django
    assert len(responses) == 1


@pytest.mark.asyncio
async def test_request_registry_owns_preparation_that_completes_after_disconnect():
    from twicc.http_streams import DownloadCleanupMiddleware, prepare_download_response

    started = threading.Event()
    release = threading.Event()
    reader = Reader(b"unused")

    def factory():
        started.set()
        assert release.wait(3)
        return response_for(reader)

    async def application(scope, receive, send):
        await prepare_download_response(factory)

    task = asyncio.create_task(DownloadCleanupMiddleware(application)({"type": "http"}, None, None))
    try:
        await wait_event(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await wait_event(reader.closed_event)
        assert not reader.requests
    finally:
        release.set()


@pytest.mark.asyncio
async def test_concurrent_request_cleanup_registries_are_isolated():
    from twicc.http_streams import DownloadCleanupMiddleware, prepare_download_response

    first_reader = Reader(b"first")
    second_reader = Reader(b"second")
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    first_release = asyncio.Event()
    second_release = asyncio.Event()

    async def application(scope, receive, send):
        reader = scope["reader"]
        await prepare_download_response(lambda: response_for(reader))
        scope["started"].set()
        await scope["release"].wait()

    middleware = DownloadCleanupMiddleware(application)
    first = asyncio.create_task(middleware({"type": "http", "reader": first_reader, "started": first_started, "release": first_release}, None, None))
    second = asyncio.create_task(middleware({"type": "http", "reader": second_reader, "started": second_started, "release": second_release}, None, None))
    try:
        await first_started.wait()
        await second_started.wait()
        first_release.set()
        await first
        assert first_reader.closed
        assert not second_reader.closed
        second_release.set()
        await second
        assert second_reader.closed
    finally:
        first_release.set()
        second_release.set()
        await asyncio.gather(first, second)
