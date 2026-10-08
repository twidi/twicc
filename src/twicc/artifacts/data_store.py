"""Byte store for an HTML artifact's ``data/`` subtree (design 2026-08-05 §3/§4).

Pure filesystem helpers, no Django imports: the serving views (file_raw,
standalone_file_raw, artifact_serve) resolve WHO may write where — these
helpers only enforce the ``data/`` confinement, the size caps, and atomicity.
Every mutator returns ``(json_payload, http_status)`` so the views translate
uniformly.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, UTC

MAX_DATA_FILE_BYTES = 10 * 1024 * 1024  # 10 MB per file
MAX_DATA_TREE_BYTES = 100 * 1024 * 1024  # 100 MB per data/ tree


def resolve_data_target(doc_dir: str, target: str) -> str | None:
    """Resolve ``target`` and require it inside ``<doc_dir>/data/``.

    Symlinks are resolved on both sides before comparison (a link under
    ``data/`` pointing outside must not escape). The ``data/`` directory
    itself is accepted (the listing endpoint targets it). Returns the
    resolved path, or ``None`` when the target falls outside.
    """
    data_root = os.path.join(os.path.realpath(doc_dir), "data")
    resolved = os.path.realpath(target)
    if resolved == data_root or resolved.startswith(data_root + os.sep):
        return resolved
    return None


def _tree_size(data_root: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(data_root):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def validate_data_write(size: int, existing: int, used: int) -> tuple[dict, int] | None:
    """Shared quota decision for pathname and confined descriptor writers."""
    if size > MAX_DATA_FILE_BYTES:
        return {"error": "too_large", "max_bytes": MAX_DATA_FILE_BYTES, "size": size}, 413
    if used - existing + size > MAX_DATA_TREE_BYTES:
        return {
            "error": "quota_exceeded",
            "max_bytes": MAX_DATA_TREE_BYTES,
            "used_bytes": used - existing,
            "size": size,
        }, 413
    return None


def write_data_file(data_root: str, target: str, body: bytes) -> tuple[dict, int]:
    """Create or overwrite ``target`` atomically (temp file + ``os.replace``).

    Missing parent directories under ``data/`` are created. Refused with an
    explicit payload when the body exceeds the per-file cap or would push the
    tree over its quota (the replaced file's current size is reclaimed first).
    """
    refusal = validate_data_write(len(body), 0, 0)
    if refusal is not None:
        return refusal
    existing = 0
    try:
        existing = os.path.getsize(target)
    except OSError:
        pass
    used = _tree_size(data_root) if os.path.isdir(data_root) else 0
    refusal = validate_data_write(len(body), existing, used)
    if refusal is not None:
        return refusal
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".twicc-data-")
        try:
            with os.fdopen(fd, "wb") as fp:
                fp.write(body)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        return {"error": "write_failed", "detail": str(exc)}, 500
    return {"ok": True, "size": len(body)}, 200


def delete_data_file(target: str) -> tuple[dict, int]:
    """Delete one file. Directories are refused — files only (design §4)."""
    if os.path.isdir(target):
        return {"error": "is_directory"}, 400
    try:
        os.unlink(target)
    except FileNotFoundError:
        return {"error": "not_found"}, 404
    except OSError as exc:
        return {"error": "delete_failed", "detail": str(exc)}, 500
    return {"ok": True}, 200


def list_data_dir(data_root: str) -> tuple[dict, int]:
    """Recursive index of the ``data/`` tree: relative path, size, ISO mtime.

    A missing ``data/`` directory is an empty listing, not an error — the
    artifact probes its store before the first write.
    """
    files = []
    if os.path.isdir(data_root):
        for dirpath, _dirnames, filenames in os.walk(data_root):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                files.append(
                    {
                        "path": os.path.relpath(full, data_root).replace(os.sep, "/"),
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
                    }
                )
    return {"files": files}, 200
