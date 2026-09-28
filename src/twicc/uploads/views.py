"""HTTP endpoints of the browser file uploads.

Design: docs/plans/2026-09-28-file-upload-design.md (§5.3, §5.4, §5.5).

- ``POST`` under the three file-operation prefixes creates an upload (JSON);
- ``GET api/uploads/`` lists the non-terminal uploads and the tombstones;
- ``HEAD`` / ``PATCH`` / ``DELETE api/uploads/<id>/`` are the tus core
  protocol with the ``termination`` extension.

Every view is wrapped by :func:`upload_view`, which turns an escaping
exception into a JSON ``500`` and adds ``X-Twicc-Upload: 1`` to every answer.
Every write to an upload's metadata or files runs in a guarded operation
(:func:`twicc.uploads.locks.run_guarded`) under the upload's lock.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import shutil
from datetime import UTC, datetime
from typing import NamedTuple

import orjson
from asgiref.sync import sync_to_async
from django.core.exceptions import RequestDataTooBig
from django.http import Http404, HttpResponse, JsonResponse

from twicc.file_tree import validate_path
from twicc.uploads import locks, store
from twicc.uploads.broadcast import broadcast_upload_state
from twicc.views import validate_standalone_root

logger = logging.getLogger(__name__)

UPLOAD_HEADER = "X-Twicc-Upload"
TUS_VERSION = "1.0.0"


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def upload_location(upload_id: str) -> str:
    """URL of one upload (the tus upload URL)."""
    return f"/api/uploads/{upload_id}/"


# ── View decorator (§5.3) ─────────────────────────────────────────────────────


def upload_view(*, tus: bool = False):
    """Wrap an upload view.

    - An ``Exception`` escaping the view (including one re-raised by the
      shielded guarded task) becomes a JSON ``500``, logged once: an exception
      already logged by the guarded task's done-callback is not logged again.
      ``asyncio.CancelledError`` is not an ``Exception``: it propagates (a
      client disconnect, §5.4).
    - Every answer gets ``X-Twicc-Upload: 1``; with *tus* (the ``<id>``
      routes) also ``Tus-Resumable: 1.0.0``, and ``Cache-Control: no-store``
      on a ``HEAD`` answer.
    """

    def decorator(view):
        @functools.wraps(view)
        async def wrapper(request, *args, **kwargs):
            try:
                response = await view(request, *args, **kwargs)
            except Exception as exc:
                if not locks.was_logged(exc):
                    logger.exception("Upload view %s failed", view.__name__)
                response = _error("Internal server error", 500)
            response[UPLOAD_HEADER] = "1"
            if tus:
                response["Tus-Resumable"] = TUS_VERSION
                if request.method == "HEAD":
                    response["Cache-Control"] = "no-store"
            return response

        return wrapper

    return decorator


# ── Finalization hook (§5.6) ──────────────────────────────────────────────────


class StepOutcome(NamedTuple):
    """Result of a step run under an upload's lock (finalization, recovery)."""

    meta: dict | None  # the metadata on disk after the step (None: absent)
    code: int  # the HTTP answer of the step


async def finalize_upload(upload_id: str) -> StepOutcome:
    """Finalization of a complete upload (§5.6).

    A step that runs under the upload's lock, **already held by the caller**
    (it never takes the lock). Returns the resulting metadata and the answer
    code of the step. Implemented by task 4 of the implementation plan.
    """
    raise NotImplementedError("upload finalization is not implemented yet")


# ── Recovery hook (§5.7) ──────────────────────────────────────────────────────


async def recover_upload(upload_id: str) -> StepOutcome:
    """Recovery of an upload that a crash or a stop left unsettled (§5.7).

    Implemented by task 4 of the implementation plan. Contract:

    - A step that runs under the upload's lock, **already held by the caller**
      (the ``HEAD`` guarded operation, later the janitor); it never takes the
      lock. It re-reads the metadata itself, runs its file and metadata work
      in a worker thread, runs finalization (:func:`finalize_upload`) itself
      for a *finalize* verdict, and broadcasts every record it persists.
    - Returns ``StepOutcome(meta, code)``: *meta* is the metadata on disk after
      the step (``None``: absent); *code* is ``507`` or ``500`` when recovery
      ran a finalization that failed with that code, or when a step or a write
      failed and left the state not settled (``507`` for disk full, else
      ``500``). Any other *code* means "settled": the ``HEAD`` caller then
      answers from *meta* with its lock-free table (``completed`` →
      offset = length, ``failed`` / ``cancelled`` → ``410``, ``active`` with a
      short ``.part`` → its offset).
    - The ``HEAD`` gates of §5.3 (the ``507`` free-space gate and the ``500``
      60 s throttle, decided from the metadata re-read under the lock) belong
      to task 4 too; they run before this step, in
      :func:`_head_recovery` (or inside this step).
    """
    raise NotImplementedError("upload recovery is not implemented yet")


# ── Creation (§5.3 POST) ──────────────────────────────────────────────────────


class CreationRequest(NamedTuple):
    """The body of a creation ``POST`` after check 1 (types and bounds)."""

    filename: str  # not stripped yet (check 2)
    size: int
    target_dir: str  # as sent (check 3 normalises it)
    root: str | None  # standalone prefix only
    origin: dict
    fingerprint: str
    client_id: str


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_empty_str(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _parse_creation_body(request, *, standalone: bool) -> CreationRequest | JsonResponse:
    """Check 1: types and bounds → a :class:`CreationRequest` or a ``400``."""
    try:
        raw = request.body
    except RequestDataTooBig:
        return _error("Request body too large", 400)
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return _error("Invalid JSON", 400)
    if not isinstance(data, dict):
        return _error("The body must be a JSON object", 400)

    size = data.get("size")
    if not _is_int(size) or size < 0:
        return _error("'size' must be a non-negative integer", 400)
    filename = data.get("filename")
    if not isinstance(filename, str):
        return _error("'filename' must be a string", 400)
    target_dir = data.get("target_dir")
    if not isinstance(target_dir, str):
        return _error("'target_dir' must be a string", 400)
    client_id = data.get("client_id")
    if not _is_non_empty_str(client_id) or len(client_id) > store.CLIENT_ID_MAX_LENGTH:
        return _error(f"'client_id' must be a non-empty string of at most {store.CLIENT_ID_MAX_LENGTH} characters", 400)
    fingerprint = data.get("fingerprint")
    if not _is_non_empty_str(fingerprint) or len(fingerprint) > store.FINGERPRINT_MAX_LENGTH:
        return _error(
            f"'fingerprint' must be a non-empty string of at most {store.FINGERPRINT_MAX_LENGTH} characters", 400
        )
    root = None
    if standalone:
        root = data.get("root")
        if root is not None and not isinstance(root, str):
            return _error("'root' must be a string or null", 400)
    origin = data.get("origin")
    if not isinstance(origin, dict):
        return _error("'origin' must be an object", 400)
    panel = origin.get("panel")
    key = origin.get("key")
    if panel not in store.ORIGIN_PANELS:
        return _error("'origin.panel' must be 'files' or 'artifacts'", 400)
    if not isinstance(key, str) or len(key) > store.ORIGIN_KEY_MAX_LENGTH:
        return _error(f"'origin.key' must be a string of at most {store.ORIGIN_KEY_MAX_LENGTH} characters", 400)

    return CreationRequest(
        filename=filename,
        size=size,
        target_dir=target_dir,
        root=root,
        origin={"panel": panel, "key": key},
        fingerprint=fingerprint,
        client_id=client_id,
    )


def _check_filename(filename: str) -> JsonResponse | None:
    """Check 2 (on the stripped name), without the ``PC_NAME_MAX`` part."""
    if not filename:
        return _error("The file name is empty", 400)
    if any(char in filename for char in ("/", "\\", "\0")):
        return _error("The file name contains a forbidden character", 400)
    if filename in (".", ".."):
        return _error("Invalid file name", 400)
    if filename.startswith(store.TEMP_FILE_PREFIX):
        return _error(f"File names starting with '{store.TEMP_FILE_PREFIX}' are reserved", 400)
    if len(filename.encode("utf-8")) > store.FILENAME_MAX_BYTES:
        return _error(f"The file name is longer than {store.FILENAME_MAX_BYTES} bytes", 400)
    return None


def _find_by_client_id(client_id: str) -> dict | None:
    """Check 1b: the upload (non-terminal or tombstone) created with *client_id*."""
    for meta in store.list_metadata():
        if meta["client_id"] == client_id:
            return meta
    return None


# Room kept for a " (n)" suffix in the final name (§5.3 check 2).
_NAME_SUFFIX_ROOM = 8


def _st_dev(path: str | os.PathLike) -> int:
    return os.stat(path).st_dev


def _check_target_sync(target_dir: str, filename: str, size: int) -> JsonResponse | None:
    """Blocking part of the checks after check 3: ``PC_NAME_MAX``, check 4, check 5."""
    try:
        name_max = os.pathconf(target_dir, "PC_NAME_MAX")
    except (OSError, ValueError):
        name_max = None
    if name_max is not None and 0 < name_max < 255:
        if len(filename.encode("utf-8")) + _NAME_SUFFIX_ROOM > name_max:
            return _error("The file name is too long for the target directory", 400)

    if not os.access(target_dir, os.W_OK):
        return _error("The target directory is not writable", 403)
    if store.is_in_staging_dir(target_dir):
        return _error("The target directory is not allowed", 403)

    # Check 5, best effort: other uploads and programs consume space too.
    staging = store.get_staging_dir()
    expected_by_others = 0
    for meta in store.list_metadata():
        if store.is_terminal(meta["state"]):
            continue
        received = store.part_size(meta["id"])
        if received is not None:
            expected_by_others += max(meta["size"] - received, 0)
    if shutil.disk_usage(staging).free - expected_by_others < size:
        return _error("Not enough disk space", 507)
    if _st_dev(target_dir) != _st_dev(staging) and shutil.disk_usage(target_dir).free < size:
        return _error("Not enough disk space in the target directory", 507)
    return None


def _creation_answer(meta: dict, status: int) -> JsonResponse:
    response = JsonResponse(store.build_record(meta), status=status)
    response["Location"] = upload_location(meta["id"])
    return response


async def _check_scope(
    target_dir: str, *, project_id: str | None, session_id: str | None, root: str | None
) -> tuple[dict, JsonResponse | None]:
    """Check 3 (scope part) → ``(scope, error)``; *target_dir* is absolute and normalised."""
    if project_id is None:
        root = (root or "").strip() or None
        scope = {"kind": "standalone", "root": root}
        error = validate_standalone_root(target_dir, root)
        if error is None and not await asyncio.to_thread(os.path.isdir, target_dir):
            error = _error("Directory not found", 404)
        return scope, error

    scope = {"kind": "project", "project_id": project_id, "session_id": session_id}
    try:
        _session, _dir_path, error = await sync_to_async(validate_path)(project_id, target_dir, session_id)
    except Http404 as exc:
        return scope, _error(str(exc) or "Not found", 404)
    return scope, error


async def _create(body: CreationRequest, *, project_id: str | None, session_id: str | None) -> JsonResponse:
    """The whole creation (checks 1b-5, files, zero-byte finalization).

    Runs as one guarded task that holds the creation lock for its whole
    duration (§5.3, §5.4).
    """
    async with locks.get_creation_lock():
        # 1b. Idempotency lookup, before every other check.
        existing = await asyncio.to_thread(_find_by_client_id, body.client_id)
        if existing is not None:
            return _creation_answer(existing, 200)

        # 2. File name.
        filename = body.filename.strip()
        if (error := _check_filename(filename)) is not None:
            return error

        # 3. Absolute target, then the scope of the prefix.
        target_dir = os.path.normpath(body.target_dir)
        if not os.path.isabs(target_dir):
            return _error("'target_dir' must be an absolute path", 400)
        scope, error = await _check_scope(target_dir, project_id=project_id, session_id=session_id, root=body.root)
        if error is not None:
            return error

        # 2 (PC_NAME_MAX), 4 and 5.
        if (error := await asyncio.to_thread(_check_target_sync, target_dir, filename, body.size)) is not None:
            return error

        upload_id = store.new_upload_id()
        try:
            meta = await asyncio.to_thread(
                store.create_upload,
                upload_id,
                client_id=body.client_id,
                size=body.size,
                filename=filename,
                target_dir=target_dir,
                scope=scope,
                origin=body.origin,
                fingerprint=body.fingerprint,
            )
        except Exception as exc:
            logger.warning("Upload creation failed for %s", target_dir, exc_info=True)
            code = store.failure_code(exc)
            return _error("Not enough disk space" if code == 507 else "Cannot create the upload", code)
        await broadcast_upload_state(meta)

        if body.size == 0:
            lock = locks.get_upload_lock(upload_id)
            if lock is None:  # pragma: no cover - the file was just written
                raise RuntimeError(f"upload {upload_id} vanished right after its creation")
            async with lock:
                outcome = await finalize_upload(upload_id)
            if outcome.meta is not None:
                meta = outcome.meta

        return _creation_answer(meta, 201)


async def _create_view(request, *, project_id: str | None = None, session_id: str | None = None):
    body = _parse_creation_body(request, standalone=project_id is None)
    if isinstance(body, JsonResponse):
        return body
    return await locks.run_guarded(
        _create(body, project_id=project_id, session_id=session_id),
        label=f"create({body.client_id})",
    )


# ── List (§5.3 GET) ───────────────────────────────────────────────────────────


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _list_records(now: datetime) -> list[dict]:
    records = []
    for meta in store.list_metadata():
        if store.is_terminal(meta["state"]):
            updated_at = _parse_iso(meta["updated_at"])
            if updated_at is None or now - updated_at >= store.TOMBSTONE_LIFETIME:
                continue
        records.append(store.build_record(meta))
    records.sort(key=lambda record: (str(record["created_at"]), record["id"]))
    return records


async def _list_view(request):
    now = datetime.now(UTC)
    records = await asyncio.to_thread(_list_records, now)
    return JsonResponse({"uploads": records, "now": now.isoformat()})


# ── tus routes: HEAD, PATCH, DELETE (§5.3) ───────────────────────────────────

TUS_METHODS = ("HEAD", "PATCH", "DELETE")
PATCH_CONTENT_TYPE = "application/offset+octet-stream"
# A non-negative integer header value. Bounded: ``int()`` refuses a digit
# string longer than 4300 digits, and 20 digits cover every file size.
_OFFSET_RE = re.compile(r"[0-9]{1,20}")


def _status(code: int) -> HttpResponse:
    """An empty answer (tus answers carry their data in headers)."""
    return HttpResponse(status=code)


def _offset_answer(offset: int, size: int) -> HttpResponse:
    response = _status(200)
    response["Upload-Offset"] = str(offset)
    response["Upload-Length"] = str(size)
    return response


def _head_table(meta: dict, part: int | None) -> HttpResponse | None:
    """The ``HEAD`` lock-free table (§5.3); ``None``: recovery must run."""
    state = meta["state"]
    size = meta["size"]
    if state == store.STATE_COMPLETED:
        return _offset_answer(size, size)
    if state in (store.STATE_FAILED, store.STATE_CANCELLED):
        return _status(410)
    if locks.is_finalizing_now(meta["id"]):
        return _offset_answer(size, size)
    if state == store.STATE_ACTIVE and part is not None and part < size:
        return _offset_answer(part, size)
    return None


async def _run_locked(upload_id: str, operation, *, label: str) -> HttpResponse:
    """Run *operation(meta)* as a guarded operation of one upload (§5.4).

    The shielded task takes the upload's lock, re-reads the metadata (absent
    or unparsable → ``404``) and calls ``await operation(meta)``, which runs
    under the held lock.
    """
    lock = locks.get_upload_lock(upload_id)
    if lock is None:
        return _status(404)

    async def guarded() -> HttpResponse:
        async with lock:
            meta = await asyncio.to_thread(store.peek_metadata, upload_id)
            if meta is None:
                return _status(404)
            return await operation(meta)

    return await locks.run_guarded(guarded(), label=f"{label}({upload_id})")


async def _head_recovery(meta: dict) -> HttpResponse:
    """``HEAD`` after the lock-free table chose the recovery path (§5.3).

    Runs under the held lock, on the metadata re-read under it.
    """
    upload_id = meta["id"]
    if store.is_terminal(meta["state"]):
        # A terminal state: the operation does nothing, answers from that state.
        return _head_table(meta, None)
    outcome = await recover_upload(upload_id)
    if outcome.code in (500, 507):
        return _status(outcome.code)
    if outcome.meta is None:
        return _status(404)
    part = await asyncio.to_thread(store.part_size, upload_id)
    answer = _head_table(outcome.meta, part)
    # Settled by contract; an answer with offset = length would end the
    # client with success for an upload that did not finish.
    return answer if answer is not None else _status(500)


async def _head(upload_id: str, meta: dict) -> HttpResponse:
    part = await asyncio.to_thread(store.part_size, upload_id)
    answer = _head_table(meta, part)
    if answer is not None:
        return answer
    return await _run_locked(upload_id, _head_recovery, label="head")


def _patch_after_finalization(outcome: StepOutcome) -> HttpResponse:
    """The ``PATCH`` answer after the finalization it ran (§5.3)."""
    meta = outcome.meta
    if meta is None:
        return _status(404)
    if meta["state"] == store.STATE_COMPLETED:
        response = _status(204)
        response["Upload-Offset"] = str(meta["size"])
        return response
    if meta["state"] == store.STATE_FAILED:
        return _status(422)
    if meta["state"] == store.STATE_CANCELLED:
        return _status(410)
    # ``active`` with ``error``, or still ``finalizing``.
    return _status(507 if outcome.code == 507 else 500)


_APPEND_FAILURE_CODES = {
    store.APPEND_EXCESS: 400,
    store.APPEND_READ_ERROR: 500,
    store.APPEND_DISK_FULL: 507,
    store.APPEND_WRITE_ERROR: 500,
}


def _declared_length(request) -> int | None:
    value = request.META.get("CONTENT_LENGTH")
    if isinstance(value, str) and _OFFSET_RE.fullmatch(value):
        return int(value)
    return None


async def _patch(request, upload_id: str) -> HttpResponse:
    if request.content_type != PATCH_CONTENT_TYPE:
        return _error(f"Content-Type must be {PATCH_CONTENT_TYPE}", 415)
    raw_offset = request.headers.get("Upload-Offset")
    if raw_offset is None or not _OFFSET_RE.fullmatch(raw_offset):
        return _error("Upload-Offset must be a non-negative integer", 400)
    client_offset = int(raw_offset)
    declared_length = _declared_length(request)

    async def operation(meta: dict) -> HttpResponse:
        state = meta["state"]
        if store.is_terminal(state):
            return _status(410)
        if state == store.STATE_FINALIZING:
            return _status(409)
        size = meta["size"]
        part = await asyncio.to_thread(store.part_size, upload_id)
        if part is None:
            lost = await asyncio.to_thread(store.mark_staging_lost, upload_id)
            if lost.meta is not None:
                await broadcast_upload_state(lost.meta)
            return _status(lost.code)
        if part >= size:
            # Nothing to append: the client resynchronises with HEAD, which
            # applies its gates before any new finalization.
            return _status(409)
        if client_offset != part:
            return _status(409)

        result = await asyncio.to_thread(
            store.append_chunk,
            upload_id,
            request.read,
            start_offset=part,
            size=size,
            meta_offset=meta["offset"],
            declared_length=declared_length,
        )
        if result.meta is not None:
            await broadcast_upload_state(result.meta)
        if result.part_size is not None and result.part_size >= size:
            return _patch_after_finalization(await finalize_upload(upload_id))
        if result.outcome != store.APPEND_OK:
            code = _APPEND_FAILURE_CODES[result.outcome]
            if code == 400:
                return _error(f"The body holds more than the {size - part} bytes left", 400)
            return _status(code)
        if result.part_size is None:  # pragma: no cover - the lock excludes every remover
            return _status(500)
        response = _status(204)
        response["Upload-Offset"] = str(result.part_size)
        return response

    return await _run_locked(upload_id, operation, label="patch")


async def _delete(upload_id: str) -> HttpResponse:
    if locks.is_finalizing_now(upload_id):
        return _status(409)

    async def operation(meta: dict) -> HttpResponse:
        state = meta["state"]
        if store.is_terminal(state):
            return _status(204)
        if state == store.STATE_FINALIZING:
            return _status(409)
        outcome = await asyncio.to_thread(store.cancel_upload, upload_id)
        if outcome.meta is not None:
            await broadcast_upload_state(outcome.meta)
        return _status(outcome.code)

    return await _run_locked(upload_id, operation, label="delete")


# ── Routes ────────────────────────────────────────────────────────────────────


@upload_view()
async def uploads_root(request):
    """``api/uploads/``: ``GET`` lists the uploads, ``POST`` creates one (standalone scope)."""
    if request.method == "GET":
        return await _list_view(request)
    if request.method == "POST":
        return await _create_view(request)
    return _error("Method not allowed", 405)


@upload_view()
async def upload_create(request, project_id, session_id=None):
    """``POST api/projects/<id>/[sessions/<sid>/]uploads/``: create an upload (project or session scope)."""
    if request.method != "POST":
        return _error("Method not allowed", 405)
    return await _create_view(request, project_id=project_id, session_id=session_id)


@upload_view(tus=True)
async def upload_detail(request, upload_id):
    """``api/uploads/<id>/``: tus ``HEAD`` (offset), ``PATCH`` (append), ``DELETE`` (cancel)."""
    if request.method not in TUS_METHODS:
        response = _error("Method not allowed", 405)
        response["Allow"] = ", ".join(TUS_METHODS)
        return response
    if request.headers.get("Tus-Resumable") != TUS_VERSION:
        response = _error(f"Tus-Resumable: {TUS_VERSION} is required", 412)
        response["Tus-Version"] = TUS_VERSION
        return response
    # Invalid id, unknown or unparsable ``<id>.json`` → 404 before any lock or path work.
    meta = await asyncio.to_thread(store.peek_metadata, upload_id)
    if meta is None:
        return _status(404)
    if request.method == "HEAD":
        return await _head(upload_id, meta)
    if request.method == "PATCH":
        return await _patch(request, upload_id)
    return await _delete(upload_id)
