"""Startup trim of ``backend.log`` (``twicc.log_retention``)."""

import os
from datetime import date, datetime, timedelta

import pytest

from twicc import log_retention
from twicc.log_retention import (
    KEEP_DAYS,
    MIN_TAIL_DAYS,
    TRIGGER_SLACK_DAYS,
    Entry,
    log_trim_enabled,
    trim_log_file,
)

NOW = datetime(2026, 9, 5, 10, 0, 0)  # noqa: DTZ001 — local time, like the log headers
TODAY = NOW.date()


def day(days_ago: int) -> date:
    return TODAY - timedelta(days=days_ago)


def header(d: date, msg: str = "message") -> bytes:
    return f"[{d.isoformat()} 12:00:00,000 -   INFO -             - twicc.test] {msg}\n".encode()


def block(n_lines: int, text: str = "  File \"x.py\", line 1, in <module>") -> bytes:
    return (text + "\n").encode() * n_lines


def build(*parts: bytes) -> bytes:
    return b"".join(parts)


def one_per_day(newest_days_ago: int, oldest_days_ago: int) -> bytes:
    """One entry per day, oldest first."""
    return build(*(header(day(n)) for n in range(oldest_days_ago, newest_days_ago - 1, -1)))


@pytest.fixture
def log(tmp_path):
    return tmp_path / "backend.log"


# --- Entry lookup ------------------------------------------------------------


def entry_at(path, offset: int) -> Entry | None:
    size = path.stat().st_size
    with open(path, "rb") as f:
        return log_retention._entry_at(f, offset, size)


def test_entry_at_offset_zero_is_the_first_header(log):
    log.write_bytes(build(header(day(2)), header(day(1))))
    assert entry_at(log, 0) == Entry(0, day(2).isoformat())


def test_entry_at_returns_the_header_owning_the_offset(log):
    first = header(day(2))
    log.write_bytes(build(first, block(3), header(day(1))))
    # Anywhere inside the first entry (header or its continuation lines).
    assert entry_at(log, 5) == Entry(0, day(2).isoformat())
    assert entry_at(log, len(first) + 10) == Entry(0, day(2).isoformat())
    # The exact start of the second header, and inside it.
    second = len(first) + len(block(3))
    assert entry_at(log, second) == Entry(second, day(1).isoformat())
    assert entry_at(log, second + 20) == Entry(second, day(1).isoformat())


def test_entry_at_is_none_before_any_header(log):
    log.write_bytes(build(block(4), header(day(1))))
    assert entry_at(log, 0) is None
    assert entry_at(log, len(block(4)) - 1) is None
    assert entry_at(log, len(block(4))) == Entry(len(block(4)), day(1).isoformat())


def test_entry_at_scans_back_through_a_block_larger_than_one_chunk(log, monkeypatch):
    monkeypatch.setattr(log_retention, "_CHUNK", 256)
    big = block(500)  # ~18 KB, dozens of 256-byte chunks
    log.write_bytes(build(header(day(3)), big, header(day(1))))
    assert entry_at(log, len(header(day(3))) + len(big) - 1) == Entry(0, day(3).isoformat())


def test_entry_at_ignores_a_header_lookalike(log):
    lookalike = b"[2026-08-01 not a real header line\n"
    first = header(day(3))
    log.write_bytes(build(first, lookalike, header(day(1))))
    assert entry_at(log, len(first) + 5) == Entry(0, day(3).isoformat())


def test_entry_at_header_split_across_chunks(log, monkeypatch):
    """The ``\\n[`` pair and the header itself may straddle a chunk boundary."""
    monkeypatch.setattr(log_retention, "_CHUNK", 7)
    first = header(day(5), "a" * 13)
    log.write_bytes(build(first, header(day(4)), block(6), header(day(1))))
    second = len(first)
    assert entry_at(log, second + len(header(day(4))) + len(block(6)) - 1) == Entry(second, day(4).isoformat())


# --- No-op paths ---------------------------------------------------------------


def test_missing_file_is_a_noop(log):
    result = trim_log_file(log, now=NOW)
    assert result == (False, 0, None, None)
    assert not log.exists()


def test_empty_file_is_a_noop(log):
    log.write_bytes(b"")
    result = trim_log_file(log, now=NOW)
    assert not result.trimmed and result.error is None
    assert log.read_bytes() == b""


def test_recent_file_is_untouched(log):
    content = one_per_day(0, KEEP_DAYS - 1)
    log.write_bytes(content)
    inode = log.stat().st_ino
    result = trim_log_file(log, now=NOW)
    assert result == (False, 0, None, None)
    assert log.read_bytes() == content
    assert log.stat().st_ino == inode


def test_oldest_entry_within_the_slack_does_not_trigger(log):
    """Exactly ``cutoff - slack`` is still inside the slack."""
    content = one_per_day(0, KEEP_DAYS + TRIGGER_SLACK_DAYS)
    log.write_bytes(content)
    assert not trim_log_file(log, now=NOW).trimmed
    assert log.read_bytes() == content


def test_oldest_entry_past_the_slack_triggers(log):
    log.write_bytes(one_per_day(0, KEEP_DAYS + TRIGGER_SLACK_DAYS + 1))
    assert trim_log_file(log, now=NOW).trimmed


# --- Cut point ------------------------------------------------------------------


def test_keeps_exactly_the_retention_window(log):
    log.write_bytes(one_per_day(0, 60))
    result = trim_log_file(log, now=NOW)
    expected = one_per_day(0, KEEP_DAYS)
    assert log.read_bytes() == expected
    assert result.trimmed
    assert result.cut_date == day(KEEP_DAYS).isoformat()
    assert result.dropped_bytes == len(one_per_day(0, 60)) - len(expected)


def test_cutoff_day_itself_is_kept(log):
    """An entry dated exactly ``now - KEEP_DAYS`` is inside the window."""
    log.write_bytes(build(header(day(KEEP_DAYS + TRIGGER_SLACK_DAYS + 1)), header(day(KEEP_DAYS)), header(day(0))))
    trim_log_file(log, now=NOW)
    assert log.read_bytes() == build(header(day(KEEP_DAYS)), header(day(0)))


def test_always_keeps_the_last_days_of_activity(log):
    """Last entry 45 days ago: keep the log's own last MIN_TAIL_DAYS days."""
    log.write_bytes(one_per_day(45, 120))
    result = trim_log_file(log, now=NOW)
    assert log.read_bytes() == one_per_day(45, 45 + MIN_TAIL_DAYS)
    assert result.cut_date == day(45 + MIN_TAIL_DAYS).isoformat()


def test_min_tail_wins_when_it_reaches_further_back_than_the_window(log):
    """Last entry 28 days ago: min(now-30, last-7) = 35 days ago."""
    log.write_bytes(one_per_day(28, 120))
    trim_log_file(log, now=NOW)
    assert log.read_bytes() == one_per_day(28, 28 + MIN_TAIL_DAYS)


def test_short_tail_of_old_activity_does_not_trigger(log):
    """Old file, but its own activity spans less than MIN_TAIL_DAYS + slack: untouched."""
    content = one_per_day(100, 100 + MIN_TAIL_DAYS + TRIGGER_SLACK_DAYS - 1)
    log.write_bytes(content)
    assert not trim_log_file(log, now=NOW).trimmed
    assert log.read_bytes() == content


def test_default_now_is_the_current_time(log):
    log.write_bytes(one_per_day(0, 60))
    assert trim_log_file(log).trimmed
    assert log.read_bytes().startswith(header(date.today() - timedelta(days=KEEP_DAYS)))


# --- Undated blocks -------------------------------------------------------------


def test_block_before_the_cut_goes_with_its_old_entry(log):
    old = build(header(day(60)), block(5000))
    kept = build(header(day(KEEP_DAYS)), header(day(0)))
    log.write_bytes(build(old, kept))
    result = trim_log_file(log, now=NOW)
    assert log.read_bytes() == kept
    assert result.dropped_bytes == len(old)


def test_block_after_the_cut_stays_with_its_recent_entry(log):
    kept = build(header(day(KEEP_DAYS)), block(5000), header(day(0)))
    log.write_bytes(build(header(day(60)), kept))
    trim_log_file(log, now=NOW)
    assert log.read_bytes() == kept


def test_blocks_larger_than_the_scan_chunk_on_both_sides(log):
    big = block(3000, "x" * 200)  # ~600 KB, well past the 64 KB chunk
    kept = build(header(day(KEEP_DAYS)), big, header(day(0)), big)
    log.write_bytes(build(header(day(60)), big, header(day(40)), big, kept))
    trim_log_file(log, now=NOW)
    assert log.read_bytes() == kept


def test_bisection_landing_inside_a_giant_block_every_step(log):
    """Two entries only; almost every byte of the file belongs to the old one."""
    giant = block(20000, "y" * 100)
    kept = header(day(0))
    log.write_bytes(build(header(day(60)), giant, kept))
    trim_log_file(log, now=NOW)
    assert log.read_bytes() == kept


def test_undated_head_is_dropped_with_the_old_entries(log):
    kept = build(header(day(KEEP_DAYS)), header(day(0)))
    log.write_bytes(build(block(10), header(day(60)), kept))
    trim_log_file(log, now=NOW)
    assert log.read_bytes() == kept


def test_undated_head_before_recent_entries_only_is_dropped(log):
    kept = one_per_day(0, 3)
    log.write_bytes(build(block(10), kept))
    result = trim_log_file(log, now=NOW)
    assert result.trimmed
    assert log.read_bytes() == kept


def test_header_lookalike_inside_a_block_is_treated_as_a_continuation(log):
    old = build(header(day(60)), b"[2026-09-05 looks dated but is not a header\n", block(3))
    kept = build(header(day(KEEP_DAYS)), header(day(0)))
    log.write_bytes(build(old, kept))
    trim_log_file(log, now=NOW)
    assert log.read_bytes() == kept


# --- Safety ----------------------------------------------------------------------


def test_stale_tmp_file_is_removed(log):
    tmp = log.with_name(log.name + ".tmp")
    tmp.write_bytes(b"leftover")
    content = one_per_day(0, 3)
    log.write_bytes(content)
    trim_log_file(log, now=NOW)
    assert not tmp.exists()
    assert log.read_bytes() == content


def test_no_tmp_file_left_after_a_trim(log):
    log.write_bytes(one_per_day(0, 60))
    trim_log_file(log, now=NOW)
    assert not log.with_name(log.name + ".tmp").exists()


def test_failed_replace_leaves_the_original_intact(log, monkeypatch):
    content = one_per_day(0, 60)
    log.write_bytes(content)
    inode = log.stat().st_ino

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    result = trim_log_file(log, now=NOW)
    assert not result.trimmed
    assert "No space left" in result.error
    assert log.read_bytes() == content
    assert log.stat().st_ino == inode
    assert not log.with_name(log.name + ".tmp").exists()


def test_failed_copy_leaves_the_original_intact(log, monkeypatch):
    content = one_per_day(0, 60)
    log.write_bytes(content)

    def boom(*args, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(log_retention, "_copy_tail", boom)
    result = trim_log_file(log, now=NOW)
    assert not result.trimmed and "Input/output" in result.error
    assert log.read_bytes() == content
    assert not log.with_name(log.name + ".tmp").exists()


def test_copy_fallback_without_sendfile(log, monkeypatch):
    monkeypatch.setattr(os, "sendfile", None)
    log.write_bytes(one_per_day(0, 60))
    assert trim_log_file(log, now=NOW).trimmed
    assert log.read_bytes() == one_per_day(0, KEEP_DAYS)


def test_symlinked_log_rewrites_the_target(tmp_path):
    target = tmp_path / "real.log"
    target.write_bytes(one_per_day(0, 60))
    link = tmp_path / "backend.log"
    link.symlink_to(target)
    assert trim_log_file(link, now=NOW).trimmed
    assert link.is_symlink()
    assert target.read_bytes() == one_per_day(0, KEEP_DAYS)
    assert not (tmp_path / "real.log.tmp").exists()


def test_file_mode_is_preserved(log):
    log.write_bytes(one_per_day(0, 60))
    log.chmod(0o600)
    trim_log_file(log, now=NOW)
    assert log.stat().st_mode & 0o777 == 0o600


# --- Opt-out flag -------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "YES", " 1 "])
def test_log_trim_disabled_by_flag(value):
    assert log_trim_enabled({"TWICC_NO_LOG_TRIM": value}) is False


@pytest.mark.parametrize("environ", [{}, {"TWICC_NO_LOG_TRIM": ""}, {"TWICC_NO_LOG_TRIM": "0"}])
def test_log_trim_enabled_otherwise(environ):
    assert log_trim_enabled(environ) is True
