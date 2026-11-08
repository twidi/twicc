"""Read-only rollout preparation with real Zstandard streams."""

import os
import stat
import threading
from pathlib import Path

import orjson
import pytest
import zstandard

from twicc.providers.codex import rollout_sources


META = {
    "type": "session_meta",
    "payload": {
        "id": "thread-1", "cwd": "/tmp/project", "history_mode": "paginated",
        "source": {"subagent": {"other": "guardian"}},
    },
}
CONTENT = orjson.dumps(META) + b'\n{"type":"event_msg","payload":{"text":"hello"}}\n'


@pytest.fixture(autouse=True)
def private_temp_directory(tmp_path, monkeypatch):
    import tempfile

    directory = tmp_path / "prepared"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    yield directory
    assert list(directory.iterdir()) == [], "Prepared files must be cleaned up"


def compressed_file(tmp_path, content=CONTENT, **options):
    path = tmp_path / "rollout-thread-1.jsonl.zst"
    path.write_bytes(zstandard.ZstdCompressor(level=3, **options).compress(content))
    return path


def test_prepared_rollout_preserves_bytes_size_and_mtime(tmp_path):
    path = compressed_file(tmp_path)
    os.utime(path, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
    original = path.read_bytes()
    source_stat = path.stat()
    with rollout_sources.prepare_rollout(path) as prepared:
        assert prepared.physical_path == path
        assert prepared.read_path != path
        assert prepared.read_path.read_bytes() == CONTENT
        assert prepared.logical_size == len(CONTENT)
        assert prepared.mtime == source_stat.st_mtime
        assert prepared.read_path.stat().st_mtime_ns == source_stat.st_mtime_ns
        assert stat.S_IMODE(prepared.read_path.stat().st_mode) == 0o600
        assert prepared.fingerprint == rollout_sources.SourceFingerprint(
            path, source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
            source_stat.st_mtime_ns, source_stat.st_ctime_ns,
        )
        read_path = prepared.read_path
    prepared.close()
    assert not read_path.exists()
    assert path.read_bytes() == original
    assert not path.with_suffix("").exists()


def test_plain_preparation_owns_no_copy(tmp_path):
    path = tmp_path / "rollout-thread-1.jsonl"
    path.write_bytes(CONTENT)
    with rollout_sources.prepare_rollout(path) as prepared:
        assert prepared.read_path == prepared.physical_path == path
        assert prepared.logical_size == len(CONTENT)
    assert path.read_bytes() == CONTENT


@pytest.mark.parametrize("unknown_size", [False, True])
@pytest.mark.parametrize("concatenated", [False, True])
def test_prepares_unknown_size_and_concatenated_frames(tmp_path, unknown_size, concatenated):
    path = compressed_file(tmp_path, write_content_size=not unknown_size)
    if concatenated:
        with path.open("ab") as stream:
            stream.write(zstandard.ZstdCompressor(write_content_size=not unknown_size).compress(b'{}\n'))
    with rollout_sources.prepare_rollout(path) as prepared:
        assert prepared.read_path.read_bytes() == CONTENT + (b'{}\n' if concatenated else b'')
        assert prepared.logical_size == len(CONTENT) + (3 if concatenated else 0)


def test_truncated_frame_never_returns_prepared_rollout(tmp_path):
    path = compressed_file(tmp_path, CONTENT * 20000, write_checksum=True)
    truncated = path.read_bytes()[:-1]
    decoder = zstandard.ZstdDecompressor().decompressobj()
    assert b'\n' in decoder.decompress(truncated)
    assert not decoder.eof
    path.write_bytes(truncated)
    with pytest.raises(OSError):
        rollout_sources.prepare_rollout(path)


@pytest.mark.parametrize("damage", ["empty", "checksum", "garbage", "partial_next", "dictionary"])
def test_rejects_invalid_streams_and_cleans_tempfiles(tmp_path, damage):
    path = compressed_file(tmp_path, write_checksum=True)
    frame = path.read_bytes()
    if damage == "empty":
        frame = b""
    elif damage == "checksum":
        frame = frame[:-1] + bytes([frame[-1] ^ 1])
    elif damage == "garbage":
        frame += b"not a zstandard frame"
    elif damage == "partial_next":
        frame += zstandard.ZstdCompressor().compress(CONTENT)[:7]
    else:
        dictionary = zstandard.train_dictionary(1024, [CONTENT + str(index).encode() for index in range(100)])
        frame = zstandard.ZstdCompressor(dict_data=dictionary).compress(CONTENT)
    path.write_bytes(frame)
    with pytest.raises(OSError):
        rollout_sources.prepare_rollout(path)
    assert path.read_bytes() == frame


def test_valid_empty_frame_is_not_empty_compressed_input(tmp_path):
    path = compressed_file(tmp_path, b"")
    with rollout_sources.prepare_rollout(path) as prepared:
        assert prepared.logical_size == 0
        assert prepared.read_path.read_bytes() == b""


def test_codex_level3_window_above_64kib(tmp_path):
    content = CONTENT * 5000
    path = compressed_file(tmp_path, content)
    assert zstandard.get_frame_parameters(path.read_bytes()).window_size > 64 * 1024
    with rollout_sources.prepare_rollout(path) as prepared:
        assert prepared.read_path.read_bytes() == content


@pytest.mark.parametrize("suffix, valid", [
    (".jsonl", True), (".jsonl.zst", True), (".jsonl.zst.zst", False),
    (".zst", False), (".json", False),
])
def test_logical_siblings_and_rollout_names(tmp_path, suffix, valid):
    path = tmp_path / ("rollout-thread-1" + suffix)
    assert rollout_sources.is_rollout_path(path) is valid
    expected = path.with_suffix("") if suffix.endswith(".zst") else path
    assert rollout_sources.logical_rollout_path(path) == expected
    assert not rollout_sources.is_rollout_path(tmp_path / ("other" + suffix))


def test_plain_takes_precedence_for_both_requested_siblings(tmp_path):
    compressed = compressed_file(tmp_path)
    plain = compressed.with_suffix("")
    plain.write_bytes(b"plain\n")
    for requested in (plain, compressed):
        assert rollout_sources.resolve_rollout(requested) == plain
        with rollout_sources.prepare_rollout(requested) as prepared:
            assert prepared.read_path == plain
            assert prepared.read_path.read_bytes() == b"plain\n"
    plain.unlink()
    assert rollout_sources.resolve_rollout(plain) == compressed


def test_resolution_rechecks_plain_after_both_missing_lookups(tmp_path, monkeypatch):
    plain = tmp_path / "rollout-thread-1.jsonl"
    compressed = plain.with_suffix(".jsonl.zst")
    original_stat = Path.stat

    def restoring_stat(path, *args, **kwargs):
        if path == compressed:
            plain.write_bytes(CONTENT)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", restoring_stat)
    assert rollout_sources.resolve_rollout(plain) == plain


def test_missing_siblings_raise_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        rollout_sources.resolve_rollout(tmp_path / "rollout-missing.jsonl")


def test_unreadable_plain_never_falls_back(tmp_path, monkeypatch):
    compressed = compressed_file(tmp_path)
    plain = compressed.with_suffix("")
    plain.write_bytes(CONTENT)
    original_open = Path.open

    def denied_open(path, *args, **kwargs):
        if path == plain:
            raise PermissionError("plain source denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)
    with pytest.raises(PermissionError):
        rollout_sources.prepare_rollout(compressed)


def test_resolution_does_not_swallow_nonabsence_errors(tmp_path, monkeypatch):
    path = compressed_file(tmp_path)
    original_stat = Path.stat

    def denied_stat(candidate, *args, **kwargs):
        if candidate == path.with_suffix(""):
            raise PermissionError("plain lookup denied")
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)
    with pytest.raises(PermissionError):
        rollout_sources.resolve_rollout(path)


@pytest.mark.parametrize("change", ["replace", "rewrite", "restore_plain", "remove"])
def test_fingerprint_detects_source_changes(tmp_path, change):
    path = compressed_file(tmp_path)
    with rollout_sources.prepare_rollout(path) as prepared:
        assert rollout_sources.source_is_current(prepared.fingerprint)
        if change == "replace":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        elif change == "rewrite":
            source_stat = path.stat()
            path.write_bytes(path.read_bytes())
            os.utime(path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        elif change == "restore_plain":
            path.with_suffix("").write_bytes(CONTENT)
        else:
            path.unlink()
        assert not rollout_sources.source_is_current(prepared.fingerprint)


def test_symlink_source_keeps_original_path(tmp_path):
    target = compressed_file(tmp_path)
    link = tmp_path / "rollout-link.jsonl.zst"
    link.symlink_to(target)
    with rollout_sources.prepare_rollout(link) as prepared:
        assert prepared.physical_path == link
        assert prepared.fingerprint.physical_path == link
        assert rollout_sources.source_is_current(prepared.fingerprint)


@pytest.mark.parametrize("already_stopped", [False, True])
def test_cancellation_cleans_tempfile(tmp_path, monkeypatch, already_stopped):
    path = compressed_file(tmp_path, os.urandom(100000))
    stop = threading.Event()
    if already_stopped:
        stop.set()
    else:
        original_is_set = stop.is_set
        checks = 0

        def stop_after_chunks():
            nonlocal checks
            checks += 1
            if checks == 4:
                stop.set()
            return original_is_set()

        monkeypatch.setattr(stop, "is_set", stop_after_chunks)
    with pytest.raises(OSError):
        rollout_sources.prepare_rollout(path, stop=stop)


def test_source_change_during_decode_is_rejected(tmp_path, monkeypatch):
    path = compressed_file(tmp_path, CONTENT * 1000)
    stop = threading.Event()
    calls = 0

    def change_source():
        nonlocal calls
        calls += 1
        if calls == 2:
            with path.open("ab") as stream:
                stream.write(zstandard.ZstdCompressor().compress(b'{}\n'))
        return False

    monkeypatch.setattr(stop, "is_set", change_source)
    with pytest.raises(OSError):
        rollout_sources.prepare_rollout(path, stop=stop)


@pytest.mark.parametrize("compressed", [False, True])
def test_metadata_returns_first_record_without_classification_changes(tmp_path, compressed):
    path = compressed_file(tmp_path) if compressed else tmp_path / "rollout-thread-1.jsonl"
    if not compressed:
        path.write_bytes(CONTENT)
    assert rollout_sources.read_rollout_meta(path) == META


def test_metadata_does_not_decode_or_validate_later_frames(tmp_path):
    path = compressed_file(tmp_path, orjson.dumps(META) + b'\n')
    with path.open("ab") as stream:
        stream.write(b"corrupt later frame")
    assert rollout_sources.read_rollout_meta(path) == META
    with pytest.raises(OSError):
        rollout_sources.prepare_rollout(path)


def test_metadata_accepts_first_record_before_truncated_frame_end(tmp_path):
    path = compressed_file(tmp_path, CONTENT * 20000, write_checksum=True)
    path.write_bytes(path.read_bytes()[:-1])
    assert rollout_sources.read_rollout_meta(path) == META


@pytest.mark.parametrize("content", [b"", b"invalid\n", b"[]\n", b"null\n", b"\n"])
def test_metadata_rejects_invalid_first_records(tmp_path, content):
    assert rollout_sources.read_rollout_meta(compressed_file(tmp_path, content)) is None


def test_metadata_missing_source_returns_none(tmp_path):
    assert rollout_sources.read_rollout_meta(tmp_path / "rollout-missing.jsonl") is None


@pytest.mark.parametrize("offset, expected", [(0, True), (len(CONTENT) - 1, True), (len(CONTENT), None), (10**9, None)])
def test_backlog_hint_never_certifies_eof(tmp_path, offset, expected):
    path = compressed_file(tmp_path)
    assert rollout_sources.compressed_backlog_hint(path, offset) is expected


def test_backlog_hint_unknown_size_is_unknown(tmp_path):
    path = compressed_file(tmp_path, write_content_size=False)
    assert rollout_sources.compressed_backlog_hint(path, 0) is None


def test_backlog_hint_does_not_decode_or_validate_stream(tmp_path):
    path = compressed_file(tmp_path, write_checksum=True)
    path.write_bytes(path.read_bytes()[:-1])
    assert rollout_sources.compressed_backlog_hint(path, 0) is True
    with path.open("ab") as stream:
        stream.write(zstandard.ZstdCompressor().compress(CONTENT))
    assert rollout_sources.compressed_backlog_hint(path, len(CONTENT)) is None


def test_context_error_cleans_prepared_file(tmp_path):
    path = compressed_file(tmp_path)
    with pytest.raises(RuntimeError):
        with rollout_sources.prepare_rollout(path):
            raise RuntimeError("consumer failed")


def test_metadata_reads_only_bounded_prefix_of_large_source(tmp_path, monkeypatch):
    path = compressed_file(tmp_path, orjson.dumps(META) + b'\n' + os.urandom(1_000_000))
    original_open = Path.open
    consumed = 0

    class PrefixReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size):
            nonlocal consumed
            assert 0 < size <= 256
            chunk = self.stream.read(size)
            consumed += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def close(self):
            self.stream.close()

    def tracked_open(candidate, *args, **kwargs):
        stream = original_open(candidate, *args, **kwargs)
        return PrefixReader(stream) if candidate == path else stream

    monkeypatch.setattr(Path, "open", tracked_open)
    assert rollout_sources.read_rollout_meta(path) == META
    assert consumed < 1000


def test_metadata_record_can_span_concatenated_frames(tmp_path):
    encoded = orjson.dumps(META) + b'\n'
    path = compressed_file(tmp_path, encoded[:20])
    with path.open("ab") as stream:
        stream.write(zstandard.ZstdCompressor().compress(encoded[20:]))
    assert rollout_sources.read_rollout_meta(path) == META


def test_replacement_during_decode_is_rejected(tmp_path, monkeypatch):
    path = compressed_file(tmp_path, CONTENT * 1000)
    stop = threading.Event()
    calls = 0

    def replace_source():
        nonlocal calls
        calls += 1
        if calls == 2:
            replacement = tmp_path / "replacement"
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        return False

    monkeypatch.setattr(stop, "is_set", replace_source)
    with pytest.raises(OSError):
        rollout_sources.prepare_rollout(path, stop=stop)


@pytest.mark.parametrize("content", [b"", b"invalid", b"\x28\xb5\x2f"])
def test_backlog_invalid_header_is_unknown(tmp_path, content):
    path = tmp_path / "rollout-thread-1.jsonl.zst"
    path.write_bytes(content)
    assert rollout_sources.compressed_backlog_hint(path, 0) is None
