"""REST endpoints of the composer staging store, under ``/api/composer-attachments/``.

Password-protected like every ``/api/`` route (``PasswordAuthMiddleware``).

- ``GET <bucket>/<id>/content``: stream the entry's bytes (no trailing slash).
- ``POST status/`` ``{refs}``: the state of each entry, in the requested order.
- ``DELETE <bucket>/<id>/``: release one entry (idempotent).
- ``POST touch/`` ``{refs, holder}``: heartbeat of the entries a browser still holds.

Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.2, §6.1.4, §12.
"""

from __future__ import annotations

import asyncio
import functools
import io
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

import orjson
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header

from twicc.core.services.attachments import lifecycle, staging
from twicc.core.services.attachments.types import AttachmentRef
from twicc.uploads import locks

logger = logging.getLogger(__name__)

# Each read of a streamed file, run off the event loop.
STREAM_CHUNK_SIZE = 64 * 1024


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _json_view(*methods: str):
    """Allow only *methods*; an unexpected exception becomes a JSON ``500``, logged."""

    def decorator(view):
        @functools.wraps(view)
        async def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                response = _error("Method not allowed", 405)
                response["Allow"] = ", ".join(methods)
                return response
            try:
                return await view(request, *args, **kwargs)
            except Exception as exc:
                if not locks.was_logged(exc):
                    logger.exception("Composer attachments view %s failed", view.__name__)
                return _error("Internal server error", 500)

        return wrapper

    return decorator


def _parse_body(request) -> dict | None:
    try:
        body = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _parse_refs(raw: object) -> tuple[AttachmentRef, ...] | None:
    if not isinstance(raw, list):
        return None
    try:
        return tuple(staging.validate_ref(item) for item in raw)
    except staging.AttachmentError:
        return None


def _path_ref(bucket: str, attachment_id: str) -> AttachmentRef | None:
    try:
        return staging.validate_ref({"bucket": bucket, "id": attachment_id})
    except staging.AttachmentError:
        return None


# ── Content ──


def _open_content(path: Path) -> io.RawIOBase:
    """Open the bytes to stream (unbuffered: each read is one bounded ``read`` call)."""
    return open(path, "rb", buffering=0)


class FileStream:
    """Async iterator over at most *size* bytes of an open file, read in 64 KiB blocks off the loop.

    The file is closed in a ``finally`` when the iteration ends, fails or is cancelled (a client
    disconnect), and by :meth:`close`, which ``StreamingHttpResponse.close`` calls.
    """

    def __init__(self, file: io.RawIOBase, size: int):
        self._file = file
        self._size = size

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[bytes]:
        remaining = self._size
        try:
            while remaining > 0:
                chunk = await asyncio.to_thread(self._file.read, min(STREAM_CHUNK_SIZE, remaining))
                if not chunk:
                    break  # the file shrank meanwhile
                remaining -= len(chunk)
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()


def _prepare_content(ref: AttachmentRef) -> tuple[io.RawIOBase, int, str, bool, str] | None:
    """Open the entry's bytes (blocking): ``(file, size, media type, inline, name)``, ``None`` → 404."""
    try:
        entry = staging.load_entry(ref)
        path = staging.content_location(entry)
        media_type, inline = staging.content_media_type(entry)
    except (staging.AttachmentError, OSError):
        return None
    file = _open_content(path)
    try:
        size = os.fstat(file.fileno()).st_size
    except BaseException:
        file.close()
        raise
    return file, size, media_type, inline, entry.filename


async def _content(ref: AttachmentRef) -> HttpResponse:
    prepared = await asyncio.to_thread(_prepare_content, ref)
    if prepared is None:
        return _error("Attachment not found", 404)
    file, size, media_type, inline, name = prepared
    response = StreamingHttpResponse(FileStream(file, size), content_type=media_type)
    response["Content-Length"] = str(size)
    response["Content-Disposition"] = content_disposition_header(not inline, name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store"
    return response


@_json_view("GET")
async def attachment_content(request, bucket: str, attachment_id: str):
    """``GET <bucket>/<id>/content``: the entry's staged file, or its promoted file."""
    ref = _path_ref(bucket, attachment_id)
    if ref is None:
        return _error("Invalid attachment reference", 400)
    return await _content(ref)


# ── Release ──


@_json_view("DELETE")
async def attachment_detail(request, bucket: str, attachment_id: str):
    """``DELETE <bucket>/<id>/``: release one entry; ``204`` even when it is absent."""
    ref = _path_ref(bucket, attachment_id)
    if ref is None:
        return _error("Invalid attachment reference", 400)
    try:
        # Guarded: a client disconnect never interrupts a release half-way.
        await locks.run_guarded(lifecycle.release_refs((ref,)), label=f"release({bucket}/{attachment_id})")
    except lifecycle.SettleError:
        return _error("Cannot cancel the upload of this attachment", 500)
    except OSError:
        return _error("Cannot remove the attachment", 500)
    return HttpResponse(status=204)


# ── Status and heartbeat ──


@_json_view("POST")
async def attachments_status(request):
    """``POST status/`` ``{refs: [{bucket, id}]}`` → ``{statuses: [{bucket, id, state, client_id?, offset?}]}``."""
    body = _parse_body(request)
    refs = _parse_refs(body.get("refs")) if body is not None else None
    if refs is None:
        return _error("Expected {refs: [{bucket, id}]}", 400)
    statuses = await asyncio.to_thread(lifecycle.status_refs, refs)
    return JsonResponse({"statuses": statuses})


@_json_view("POST")
async def attachments_touch(request):
    """``POST touch/`` ``{refs: [{bucket, id}], holder: "draft" | "snapshot"}`` → ``204``."""
    body = _parse_body(request)
    refs = _parse_refs(body.get("refs")) if body is not None else None
    if refs is None or body.get("holder") not in lifecycle.HOLDERS:
        return _error("Expected {refs: [{bucket, id}], holder: 'draft' | 'snapshot'}", 400)
    await asyncio.to_thread(lifecycle.touch_refs, refs, holder=body["holder"])
    return HttpResponse(status=204)
