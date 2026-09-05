"""Startup trim of the backend log file.

``backend.log`` is append-only and grows for months. At startup, before the
logging file handler opens it, the server drops the entries older than the
retention window so the file stays bounded. The trim works on the entries'
own timestamps (the ``[YYYY-MM-DD HH:MM:SS,mmm - `` header every entry starts
with), not on the file's mtime.

Rules:

- the cut point is ``min(now - KEEP_DAYS, last_entry - MIN_TAIL_DAYS)``: keep
  everything younger than ``KEEP_DAYS``, and always the log's own last
  ``MIN_TAIL_DAYS`` days of activity (a user coming back after a long pause
  still finds their last days of logs);
- hysteresis: the trim runs only when the oldest entry is more than
  ``TRIGGER_SLACK_DAYS`` past the cut point, so the tail copy happens every
  ~``TRIGGER_SLACK_DAYS`` days of runtime rather than at every start;
- an entry is its header line plus the undated lines that follow it
  (tracebacks, multi-line messages) — those go wherever their header goes.

Finding the cut point never reads the whole file: a bisection on byte offsets,
each probe resolving the entry that owns the probed byte by scanning
*backwards* to the previous header (chunked ``rfind``, not line by line).
Dropping the old part never rewrites in place: the kept tail is copied to a
sibling temp file which then replaces the log atomically (``os.replace``), so
a failure at any point leaves the original untouched. The caller runs this
under the instance lock, with no handler of its own open on the file yet:
a descriptor already open on the old inode would keep appending to the
unlinked file.

The trim is opt-out per data dir: ``TWICC_NO_LOG_TRIM=1`` in the ``.env``
(``devctl`` sets it in a worktree, where a bounded log has no value).
"""

import os
import re
import shutil
import stat
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, NamedTuple

KEEP_DAYS = 30  # drop entries older than this
MIN_TAIL_DAYS = 7  # always keep the log's own last N days of activity
TRIGGER_SLACK_DAYS = 7  # trim only when the oldest entry exceeds the cut point by this

TMP_SUFFIX = ".tmp"

# A header line, matched at a line start: ``[YYYY-MM-DD HH:MM:SS,mmm - ``
# (see the ``standard`` formatter in ``twicc.settings.LOGGING``).
_HEADER_RE = re.compile(rb"\[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2},\d{3} - ")
_HEADER_LEN = 27  # bytes a header needs to be recognised
_CHUNK = 64 * 1024  # backward-scan read size


class Entry(NamedTuple):
    """A log entry: the byte offset of its header line and the header's date."""

    offset: int
    date: str  # ISO ``YYYY-MM-DD``


class TrimResult(NamedTuple):
    trimmed: bool
    dropped_bytes: int
    cut_date: str | None  # date of the first kept entry
    error: str | None


_NOOP = TrimResult(False, 0, None, None)


def log_trim_enabled(environ=os.environ) -> bool:
    """Opt-out switch: ``TWICC_NO_LOG_TRIM=1`` disables the startup trim."""
    return environ.get("TWICC_NO_LOG_TRIM", "").strip().lower() not in ("1", "true", "yes")


def trim_log_file(path: Path, now: datetime | None = None) -> TrimResult:
    """Drop the entries of ``path`` older than the retention window.

    Never raises: any failure is reported in the result and leaves the file
    as it was — a log trim must not block the server's startup.
    """
    try:
        # Local time on purpose: the log's ``asctime`` headers are local time too.
        return _trim(path, (now or datetime.now()).date())  # noqa: DTZ005
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return TrimResult(False, 0, None, f"{type(exc).__name__}: {exc}")


def _trim(path: Path, today: date) -> TrimResult:
    real = path.resolve()  # a symlinked log: rewrite the target, keep the link
    tmp = real.with_name(real.name + TMP_SUFFIX)
    tmp.unlink(missing_ok=True)  # leftover of an interrupted trim
    if not real.is_file():
        return _NOOP
    size = real.stat().st_size
    if size == 0:
        return _NOOP

    with open(real, "rb") as src:
        last = _entry_at(src, size - 1, size)
        if last is None:
            return _NOOP  # no header anywhere: not a log we know how to trim
        cutoff = min(today - timedelta(days=KEEP_DAYS), date.fromisoformat(last.date) - timedelta(days=MIN_TAIL_DAYS))
        first = _entry_at(src, 0, size)  # the header at offset 0, if the file starts with one
        if first is not None and date.fromisoformat(first.date) >= cutoff - timedelta(days=TRIGGER_SLACK_DAYS):
            return _NOOP
        cut = _find_cut(src, size, cutoff.isoformat(), last)
        if cut == 0:
            return _NOOP
        cut_date = _entry_at(src, cut, size).date

        try:
            with open(tmp, "wb") as dst:
                os.fchmod(dst.fileno(), stat.S_IMODE(os.fstat(src.fileno()).st_mode))
                _copy_tail(src, dst, cut, size)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(tmp, real)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return TrimResult(True, cut, cut_date, None)


def _find_cut(f: BinaryIO, size: int, cutoff: str, known: Entry) -> int:
    """Offset of the first entry dated ``>= cutoff``; ``known`` is one such entry.

    Bisection on byte offsets. Invariant: the entry owning ``lo`` is older
    than ``cutoff`` (or there is none), ``hi`` is the header offset of an
    entry dated ``>= cutoff``. Dates are assumed non-decreasing along the
    file; a locally out-of-order header can shift the cut by an entry, never
    break it (the result is always a header offset).
    """
    first = _entry_at(f, 0, size)
    if first is not None and first.date >= cutoff:
        return 0
    lo, hi = 0, known.offset
    while hi - lo > 1:
        mid = (lo + hi) // 2
        entry = _entry_at(f, mid, size)
        if entry is not None and entry.date >= cutoff:
            hi = entry.offset
        else:
            lo = mid
    return hi


def _entry_at(f: BinaryIO, offset: int, size: int) -> Entry | None:
    """The entry owning byte ``offset``: the closest header line starting at or before it.

    Scans backwards from ``offset`` in ``_CHUNK`` steps. ``None`` when no
    header precedes the offset (undated lines at the head of the file).
    """
    if offset < 0 or offset >= size:
        return None
    # ``buf`` mirrors the file from ``buf_start``; it always extends at least
    # ``_HEADER_LEN`` bytes past every candidate so a header can be matched
    # in-buffer. Candidates are ``[`` bytes at a relative index < ``limit``
    # preceded by a newline (or at offset 0 of the file).
    f.seek(offset)
    buf = f.read(min(_HEADER_LEN, size - offset))
    buf_start = offset
    limit = 0
    while True:
        end = limit
        while True:
            nl = buf.rfind(b"\n[", 0, end)
            if nl == -1:
                break
            match = _HEADER_RE.match(buf, nl + 1)
            if match:
                return Entry(buf_start + nl + 1, match.group(1).decode())
            end = nl + 1
        if buf_start == 0:
            match = _HEADER_RE.match(buf, 0)
            return Entry(0, match.group(1).decode()) if match else None
        new_start = max(0, buf_start - _CHUNK)
        f.seek(new_start)
        chunk = f.read(buf_start - new_start)
        # Keep only what the new candidates can need: the chunk plus the
        # header-length lookahead into the previous buffer.
        buf = (chunk + buf)[: len(chunk) + _HEADER_LEN + 1]
        limit = len(chunk) + 1  # the old ``buf_start`` byte is now testable (its ``\n`` is in ``chunk``)
        buf_start = new_start


def _copy_tail(src: BinaryIO, dst: BinaryIO, start: int, size: int) -> None:
    """Copy ``src[start:size]`` to ``dst`` — zero-copy when the platform allows."""
    sendfile = getattr(os, "sendfile", None)
    if sendfile is not None:
        offset = start
        try:
            while offset < size:
                sent = sendfile(dst.fileno(), src.fileno(), offset, size - offset)
                if sent == 0:
                    break
                offset += sent
            return
        except OSError:
            if offset != start:
                raise  # partial copy: the temp file is garbage, let the caller drop it
            # file-to-file sendfile unsupported here (e.g. macOS): plain copy
    src.seek(start)
    shutil.copyfileobj(src, dst, 1024 * 1024)
