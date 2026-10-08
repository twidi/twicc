"""Authenticated inline document, asset, and optional saved-data routes."""

from __future__ import annotations

import asyncio
import errno
import os
import stat
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import BinaryIO
from urllib.parse import unquote

from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.utils.cache import add_never_cache_headers

from twicc.artifacts.broker_html import artifact_html_response, is_artifact_document_request
from twicc.artifacts.data_store import validate_data_write
from twicc.core.models import Session, SessionType
from twicc.inline_artifacts.files import (
    InlineArtifactUnavailable,
    _directory_at,
    _open_regular,
    _parts,
    open_source_asset,
    source_artifact_directory,
)
from twicc.inline_artifacts.publications import latest_publications
from twicc.views import _guess_raw_content_type


def inline_asset_response(
    file: BinaryIO,
    content_type: str,
    *,
    as_document: bool,
    head: bool,
    release: Callable[[], None] | None = None,
) -> HttpResponse:
    """Consume the validated file itself. Own its close and optional lease."""
    finished = False

    def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        try:
            file.close()
        finally:
            if release is not None:
                release()

    try:
        if as_document and content_type == "text/html":
            try:
                response = artifact_html_response(file.read())
            finally:
                finish()
            if head:
                response["Content-Length"] = str(len(response.content))
                response.content = b""
            return response
        response = FileResponse(file, content_type=content_type)
        # FileResponse registers file.close; replace it with one lease owner.
        response._resource_closers.remove(file.close)
        response._resource_closers.append(finish)
        response["X-Content-Type-Options"] = "nosniff"
        add_never_cache_headers(response)
        response["CDN-Cache-Control"] = "no-store"
        if head:
            result = HttpResponse(b"", headers=dict(response.headers))
            response.close()
            return result
        return response
    except OSError:
        finish()
        raise InlineArtifactUnavailable() from None
    except BaseException:
        finish()
        raise


def _data_entries(directory_fd: int, prefix: str = "") -> list[dict]:
    files = []
    for name in sorted(os.listdir(directory_fd)):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        relative = prefix + name
        if stat.S_ISDIR(metadata.st_mode):
            with _directory_at(directory_fd, [name]) as child:
                files.extend(_data_entries(child, relative + "/"))
        elif stat.S_ISREG(metadata.st_mode):
            file_fd = _open_regular(directory_fd, name)
            try:
                metadata = os.fstat(file_fd)
            finally:
                os.close(file_fd)
            files.append(
                {
                    "path": relative,
                    "size": metadata.st_size,
                    "mtime": datetime.fromtimestamp(metadata.st_mtime, tz=UTC).isoformat(),
                }
            )
        else:
            raise InlineArtifactUnavailable()
    return files


def _existing_size(parent_fd: int, name: str) -> int:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(metadata.st_mode):
        raise InlineArtifactUnavailable()
    return metadata.st_size


def _write_data(root_fd: int, parts: list[str], body: bytes) -> tuple[dict, int]:
    # Check the per-file limit before creating any saved-data directories.
    refusal = validate_data_write(len(body), 0, 0)
    if refusal is not None:
        return refusal
    with _directory_at(root_fd, ["data"], create=True) as data_fd:
        used = sum(entry["size"] for entry in _data_entries(data_fd))
        with _directory_at(data_fd, parts[1:-1], create=True) as parent:
            existing = _existing_size(parent, parts[-1])
            refusal = validate_data_write(len(body), existing, used)
            if refusal is not None:
                return refusal
            temporary = ".twicc-data-" + uuid.uuid4().hex
            temporary_fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
            )
            try:
                with os.fdopen(temporary_fd, "wb") as file:
                    file.write(body)
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
                raise
    return {"ok": True, "size": len(body)}, 200


def _delete_data(root_fd: int, parts: list[str]) -> tuple[dict, int]:
    try:
        with _directory_at(root_fd, parts[:-1]) as parent:
            metadata = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                return {"error": "is_directory"}, 400
            if not stat.S_ISREG(metadata.st_mode):
                raise InlineArtifactUnavailable()
            # unlink never follows a final component swapped to a symlink.
            os.unlink(parts[-1], dir_fd=parent)
    except FileNotFoundError:
        return {"error": "not_found"}, 404
    return {"ok": True}, 200


def _data_mutation(operation, error_code: str, *args) -> tuple[dict, int]:
    try:
        return operation(*args)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise InlineArtifactUnavailable() from None
        return {"error": error_code, "detail": str(exc)}, 500


def _data_response(session_id: str, artifact_id: str, asset_path: str, method: str, body: bytes):
    try:
        parts = _parts(asset_path, directory=True)
        if parts[0] != "data":
            return JsonResponse({"error": "outside_data"}, status=403) if method in ("PUT", "DELETE") else None
        with source_artifact_directory(session_id, artifact_id) as root_fd:
            if method == "PUT":
                if len(parts) == 1:
                    payload, status = {"error": "is_directory"}, 400
                else:
                    payload, status = _data_mutation(_write_data, "write_failed", root_fd, parts, body)
            elif method == "DELETE":
                payload, status = _data_mutation(_delete_data, "delete_failed", root_fd, parts)
            else:
                try:
                    with _directory_at(root_fd, parts) as directory:
                        payload, status = {"files": _data_entries(directory)}, 200
                except FileNotFoundError:
                    if parts != ["data"]:
                        return None
                    payload, status = {"files": []}, 200
                except NotADirectoryError:
                    return None
            response = JsonResponse(payload, status=status)
            if method == "HEAD":
                response["Content-Length"] = str(len(response.content))
                response.content = b""
            return response
    except (OSError, ValueError):
        raise InlineArtifactUnavailable() from None


def _document_header_matches(request, prefix: str, entry: str) -> bool:
    raw = request.headers.get("X-Twicc-Artifact-Doc")
    return raw is not None and unquote(raw) == prefix + entry


async def inline_artifact_asset(request, session_id: str, artifact_id: str, asset_path: str):
    """Serve only the owning regular session's current published artifact."""
    if request.method not in ("GET", "HEAD", "PUT", "DELETE"):
        return HttpResponseNotAllowed(["GET", "HEAD", "PUT", "DELETE"])
    try:
        session = await Session.objects.aget(id=session_id, type=SessionType.SESSION)
    except Session.DoesNotExist:
        raise Http404("Inline artifact unavailable") from None
    publication = latest_publications(session.inline_artifacts).get(artifact_id)
    if publication is None:
        raise Http404("Inline artifact unavailable")
    entry = publication["src"].rsplit("/", 1)[-1]
    prefix = f"/api/sessions/{session_id}/inline-artifacts/{artifact_id}/"
    data_authorized = _document_header_matches(request, prefix, entry)
    is_write = request.method in ("PUT", "DELETE")
    if is_write and not data_authorized:
        return HttpResponseNotAllowed(["GET", "HEAD"])
    try:
        if is_write:
            try:
                _parts(asset_path, directory=True)
            except InlineArtifactUnavailable:
                return JsonResponse({"error": "outside_data"}, status=403)
        if data_authorized:
            handled = await asyncio.to_thread(
                _data_response, session_id, artifact_id, asset_path, request.method, request.body if is_write else b""
            )
            if handled is not None:
                return handled
        file = await asyncio.to_thread(open_source_asset, session_id, artifact_id, asset_path)
        return await asyncio.to_thread(
            inline_asset_response,
            file,
            _guess_raw_content_type(asset_path),
            as_document=is_artifact_document_request(request.headers.get("Sec-Fetch-Dest")),
            head=request.method == "HEAD",
        )
    except InlineArtifactUnavailable:
        raise Http404("Inline artifact unavailable") from None
