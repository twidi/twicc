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
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import orjson
from asgiref.sync import sync_to_async
from django.core.exceptions import RequestDataTooBig
from django.http import Http404, HttpResponse, JsonResponse

from twicc.core.services.attachments import lifecycle, staging
from twicc.core.services.attachments.types import AttachmentRef
from twicc.file_tree import validate_path
from twicc.uploads import locks, store
from twicc.uploads.broadcast import broadcast_upload_state
from twicc.views import ensure_session_artifacts_root, validate_standalone_root

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


# ── Finalization (§5.6) ───────────────────────────────────────────────────────


class StepOutcome(NamedTuple):
    """Result of a step run under an upload's lock (finalization, recovery)."""

    meta: dict | None  # the metadata on disk after the step (None: absent)
    code: int  # the HTTP answer of the step


async def finalize_upload(upload_id: str) -> StepOutcome:
    """Finalization of a complete upload (§5.6).

    A step that runs under the upload's lock, **already held by the caller**
    (it never takes the lock). The coroutine side: re-read (step 1), ORM
    re-validation (step 2), the "finalizing now" set and the ``finalizing``
    write (step 3), the broadcasts. Every file and metadata step of step 4,
    and every failure rule, run in the worker thread
    (:func:`store.finalize_files`, :func:`store.finalize_precommit_failure`).

    Returns the metadata on disk after the step and its answer: ``204``
    (``completed``), ``422`` (``failed``), else ``507`` (disk full) / ``500``
    with the upload ``active`` with ``error`` or still ``finalizing``.
    """
    # 1. Re-read; a terminal state stops here.
    meta = await asyncio.to_thread(store.peek_metadata, upload_id)
    if meta is None:
        return StepOutcome(None, 404)
    if store.is_terminal(meta["state"]):
        return StepOutcome(meta, _TERMINAL_STEP_CODES[meta["state"]])

    # 2. Re-validate the stored scope and target (creation checks 3-4).
    try:
        refusal = await _revalidate_target(meta)
    except Exception:
        logger.warning("Upload %s: re-validation of the target raised", upload_id, exc_info=True)
        result = await asyncio.to_thread(
            store.finalize_precommit_failure, upload_id, meta, 500, "Cannot validate the target directory"
        )
        return await _finish_step(result)
    if refusal is not None:
        result = await asyncio.to_thread(store.finalize_precommit_failure, upload_id, meta, 422, refusal)
        return await _finish_step(result)

    # 3. "Finalizing now", then the ``finalizing`` write; the id leaves the
    # set in the ``finally`` of the context manager, after every later step.
    with locks.finalizing_now(upload_id):
        try:
            current = await asyncio.to_thread(store.update_metadata, upload_id, state=store.STATE_FINALIZING)
        except Exception as exc:
            logger.warning("Upload %s: cannot write the 'finalizing' state", upload_id, exc_info=True)
            return StepOutcome(meta, store.failure_code(exc))
        await broadcast_upload_state(current)
        # 4-5. The worker thread, then the broadcast of its result.
        result = await asyncio.to_thread(store.finalize_files, upload_id, current)
        return await _finish_step(result)


_TERMINAL_STEP_CODES = {store.STATE_COMPLETED: 204, store.STATE_FAILED: 422, store.STATE_CANCELLED: 410}


async def _finish_step(result: store.FinalizeResult) -> StepOutcome:
    if result.broadcast:
        await broadcast_upload_state(result.meta)
    return StepOutcome(result.meta, result.code)


def _error_message(response: JsonResponse) -> str:
    try:
        message = orjson.loads(response.content).get("error")
    except (orjson.JSONDecodeError, AttributeError):
        message = None
    return message if isinstance(message, str) and message else "The target directory is not valid"


async def _revalidate_target(meta: dict) -> str | None:
    """§5.6 step 2: creation checks 3-4 on the stored scope and ``target_dir``.

    Returns the refusal message of a validation verdict (→ ``422``), or
    ``None``. An exception (e.g. a SQLite ``OperationalError``) propagates:
    the caller treats it as an unexpected pre-commit failure.
    """
    target_dir = meta["target_dir"]
    if not os.path.isabs(target_dir):
        return "'target_dir' must be an absolute path"
    scope = meta["scope"]
    if scope["kind"] == lifecycle.SCOPE_KIND_COMPOSER:
        return await asyncio.to_thread(_check_composer_target, meta)
    if scope["kind"] == "project":
        _scope, error = await _check_scope(
            target_dir, project_id=scope["project_id"], session_id=scope.get("session_id"), root=None
        )
    else:
        _scope, error = await _check_scope(target_dir, project_id=None, session_id=None, root=scope.get("root"))
    if error is None:
        error = await asyncio.to_thread(_check_target_writable, target_dir)
    return None if error is None else _error_message(error)


def _check_composer_target(meta: dict) -> str | None:
    """Re-validation of a composer upload: its target is still its entry's ``file/`` directory.

    Refuses an invalid entry key, a target that is not ``<staging>/<bucket>/<id>/file``, a
    released entry and a removed (or symlinked) ``file/``, then applies check 4.
    """
    ref = lifecycle.composer_ref(meta)
    if ref is None:
        return "Invalid attachment reference"
    target_dir = meta["target_dir"]
    if os.path.normpath(target_dir) != str(lifecycle.upload_target_dir(ref)):
        return "The target directory is not the attachment's directory"
    if lifecycle.is_released(ref) or os.path.realpath(target_dir) != target_dir or not os.path.isdir(target_dir):
        return "The attachment was removed"
    error = _check_target_writable(target_dir)
    return None if error is None else _error_message(error)


# ── Recovery (§5.7) ───────────────────────────────────────────────────────────


async def recover_upload(upload_id: str) -> StepOutcome:
    """Recovery of an upload that a crash or a stop left unsettled (§5.7).

    - A step that runs under the upload's lock, **already held by the caller**
      (the ``HEAD`` guarded operation, later the janitor); it never takes the
      lock. Its file and metadata work runs in the worker thread
      (:func:`store.recover_files`), which returns a verdict; the coroutine
      broadcasts the record it persisted, and runs finalization
      (:func:`finalize_upload`) itself for a *finalize* verdict.
    - Returns ``StepOutcome(meta, code)``: *meta* is the metadata on disk after
      the step (``None``: absent); *code* is ``507`` or ``500`` when recovery
      ran a finalization that failed with that code, or when a step or a write
      failed and left the state not settled (``507`` for disk full, else
      ``500``). Any other *code* means "settled": the ``HEAD`` caller then
      answers from *meta* with its lock-free table.
    - The ``HEAD`` gates of §5.3 run before this step, in :func:`_head_recovery`.
    """
    verdict = await asyncio.to_thread(store.recover_files, upload_id)
    if verdict.broadcast:
        await broadcast_upload_state(verdict.meta)
    if verdict.kind == store.RECOVERY_FINALIZE:
        return await finalize_upload(upload_id)
    return StepOutcome(verdict.meta, verdict.code)


# ── Creation (§5.3 POST) ──────────────────────────────────────────────────────


class CreationRequest(NamedTuple):
    """The body of a creation ``POST`` after check 1 (types and bounds)."""

    filename: str  # not stripped yet (check 2)
    size: int
    target_dir: str  # as sent (check 3 normalises it); empty for the composer origin
    root: str | None  # standalone prefix only
    origin: dict
    fingerprint: str
    client_id: str
    attachment_ref: AttachmentRef | None = None  # composer origin only: the target entry


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
    raw_origin = data.get("origin")
    composer = isinstance(raw_origin, dict) and raw_origin.get("panel") == store.ORIGIN_PANEL_COMPOSER
    target_dir = data.get("target_dir")
    if composer:
        # The server computes the target of a composer upload (spec 2026-10-03 §6.1.1).
        if not standalone:
            return _error("Composer uploads are created with POST /api/uploads/ only", 400)
        if target_dir is not None or data.get("root") is not None:
            return _error("'target_dir' and 'root' must be absent for the composer origin", 400)
        target_dir = ""
    elif not isinstance(target_dir, str):
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
    if standalone and not composer:
        root = data.get("root")
        if root is not None and not isinstance(root, str):
            return _error("'root' must be a string or null", 400)
    origin = raw_origin
    if not isinstance(origin, dict):
        return _error("'origin' must be an object", 400)
    panel = origin.get("panel")
    key = origin.get("key")
    if panel not in store.ORIGIN_PANELS:
        return _error("'origin.panel' must be 'files', 'artifacts' or 'composer'", 400)
    if not isinstance(key, str) or len(key) > store.ORIGIN_KEY_MAX_LENGTH:
        return _error(f"'origin.key' must be a string of at most {store.ORIGIN_KEY_MAX_LENGTH} characters", 400)
    attachment_ref = None
    if composer:
        try:
            attachment_ref = lifecycle.ref_from_origin_key(key)
        except staging.AttachmentError:
            return _error("'origin.key' must be '<bucket>/<attachment id>' for the composer origin", 400)

    return CreationRequest(
        filename=filename,
        size=size,
        target_dir=target_dir,
        root=root,
        origin={"panel": panel, "key": key},
        fingerprint=fingerprint,
        client_id=client_id,
        attachment_ref=attachment_ref,
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
_NAME_SUFFIX_ROOM = staging.NAME_SUFFIX_ROOM


def _check_target_writable(target_dir: str) -> JsonResponse | None:
    """Check 4: *target_dir* is writable and is not the staging dir or inside it (``403``)."""
    if not os.access(target_dir, os.W_OK):
        return _error("The target directory is not writable", 403)
    if store.is_in_staging_dir(target_dir):
        return _error("The target directory is not allowed", 403)
    return None


def _check_target_sync(target_dir: str, filename: str, size: int) -> JsonResponse | None:
    """Blocking part of the checks after check 3: ``PC_NAME_MAX``, check 4, check 5."""
    try:
        name_max = os.pathconf(target_dir, "PC_NAME_MAX")
    except (OSError, ValueError):
        name_max = None
    if name_max is not None and 0 < name_max < 255:
        if len(filename.encode("utf-8")) + _NAME_SUFFIX_ROOM > name_max:
            return _error("The file name is too long for the target directory", 400)

    if (error := _check_target_writable(target_dir)) is not None:
        return error

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
    if store.st_dev(target_dir) != store.st_dev(staging) and shutil.disk_usage(target_dir).free < size:
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
        if error is None:
            await asyncio.to_thread(ensure_session_artifacts_root, target_dir, root)
        if error is None and not await asyncio.to_thread(os.path.isdir, target_dir):
            error = _error("Directory not found", 404)
        return scope, error

    scope = {"kind": "project", "project_id": project_id, "session_id": session_id}
    try:
        _session, _dir_path, error = await sync_to_async(validate_path)(project_id, target_dir, session_id)
    except Http404 as exc:
        return scope, _error(str(exc) or "Not found", 404)
    return scope, error


class ComposerTarget(NamedTuple):
    """The target of a composer upload, prepared by :func:`_prepare_composer_target`."""

    filename: str  # normalized
    target_dir: str  # ``<staging>/<bucket>/<attachment_id>/file``
    scope: dict


async def _prepare_composer_target(body: CreationRequest) -> ComposerTarget | JsonResponse:
    """Steps 2-5 of a composer creation (spec 2026-10-03 §6.1.1), under the creation lock.

    Release tombstone (``410``), file name normalization, settle of every other attempt of the
    entry (``500`` when one cannot be settled: nothing is reset), then the reset of the entry. The
    target checks that need the directory and the upload creation follow in :func:`_create`.
    """
    ref = body.attachment_ref
    # 2. Release tombstone: a released entry is never re-created.
    if await asyncio.to_thread(lifecycle.is_released, ref):
        return _error("The attachment was removed", 410)
    # 3. File name: any name is accepted, normalized.
    filename = staging.normalize_filename(body.filename, await asyncio.to_thread(staging.name_max_bytes))
    # 4. Settle every other attempt for the entry.
    try:
        await lifecycle.settle_entry_uploads(ref)
    except lifecycle.SettleError:
        logger.warning("Composer upload creation: cannot settle the previous uploads of %s", ref, exc_info=True)
        return _error("Cannot cancel the previous upload of this attachment", 500)
    # 5. Reset the entry and create ``file/``.
    try:
        target_dir = await asyncio.to_thread(lifecycle.reset_entry, ref)
    except OSError as exc:
        logger.warning("Composer upload creation: cannot reset the entry %s", ref, exc_info=True)
        code = store.failure_code(exc)
        return _error("Not enough disk space" if code == 507 else "Cannot prepare the attachment", code)
    return ComposerTarget(filename, str(target_dir), {"kind": lifecycle.SCOPE_KIND_COMPOSER})


async def _create(body: CreationRequest, *, project_id: str | None, session_id: str | None) -> JsonResponse:
    """The whole creation (checks 1b-5, files, zero-byte finalization).

    Runs as one guarded task that holds the creation lock for its whole
    duration (§5.3, §5.4). A composer upload replaces checks 2-3 with
    :func:`_prepare_composer_target` (server-computed target, settle, reset).
    """
    async with lifecycle.composer_creation_guard():
        # 1b. Idempotency lookup, before every other check.
        existing = await asyncio.to_thread(_find_by_client_id, body.client_id)
        if existing is not None:
            return _creation_answer(existing, 200)

        if body.attachment_ref is not None:
            prepared = await _prepare_composer_target(body)
            if isinstance(prepared, JsonResponse):
                return prepared
            filename, target_dir, scope = prepared
        else:
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
        if body.attachment_ref is not None:
            await asyncio.to_thread(lifecycle.touch_entry, body.attachment_ref)

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


def _list_records(now: datetime) -> list[dict]:
    records = []
    for meta in store.list_metadata():
        if store.is_terminal(meta["state"]):
            updated_at = store.parse_iso(meta["updated_at"])
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


# A finalization that failed with an unexpected error is not run again by
# ``HEAD`` before this delay (bounds the copies made by client retries, §5.3).
FINALIZE_RETRY_THROTTLE = timedelta(seconds=60)


def _within_throttle(failed_at: object) -> bool:
    """True when *failed_at* (``finalize_failed_at``) is less than 60 s old."""
    parsed = store.parse_iso(failed_at)
    if parsed is None:
        return False
    return timedelta(0) <= datetime.now(UTC) - parsed < FINALIZE_RETRY_THROTTLE


async def _head_recovery(meta: dict) -> HttpResponse:
    """``HEAD`` after the lock-free table chose the recovery path (§5.3).

    Runs under the held lock. Every decision comes from *meta*, the metadata
    re-read under it: the table rows (terminal state, "finalizing now", a
    short ``.part``), then the gates of an ``active`` upload with a complete
    ``.part`` whose last finalization failed (``507``: free-space gate;
    ``500``: 60 s throttle), then recovery (§5.7).
    """
    upload_id = meta["id"]
    size = meta["size"]
    part = await asyncio.to_thread(store.part_size, upload_id)
    answer = _head_table(meta, part)
    if answer is not None:
        return answer

    if meta["state"] == store.STATE_ACTIVE and part is not None and part >= size:
        failed_code = meta.get("finalize_error_code")
        if failed_code == 507:
            # A Retry right after freeing space works at once; still not
            # enough → 507 without copying. A target that cannot be stat'ed
            # skips the gate: recovery ends it in 422 → failed.
            if not await asyncio.to_thread(store.finalize_space_available, meta):
                return _status(507)
        elif failed_code == 500 and _within_throttle(meta.get("finalize_failed_at")):
            return _status(500)

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
        if result.part_size is not None and result.part_size > part:
            # Accepted progress keeps a composer staging entry fresh for the retention reaper.
            await asyncio.to_thread(lifecycle.touch_upload_entry, meta)
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
