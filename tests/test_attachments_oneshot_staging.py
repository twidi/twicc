"""One-shot staging entries of the CLI, the RPC and the MCP.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.3.
"""

import errno
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import orjson
import pytest

from twicc.core.services.attachments import staging
from twicc.core.services.attachments.staging import AttachmentError


@pytest.fixture
def root(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(data))
    return staging.get_composer_attachments_dir()


def test_new_bucket_carries_its_origin_and_passes_the_key_rules(root):
    for origin in ("cli", "api"):
        bucket = staging.new_bucket(origin)
        assert bucket.startswith(f"{origin}-")
        assert staging.is_valid_bucket(bucket)
        uuid.UUID(bucket.removeprefix(f"{origin}-"))
    with pytest.raises(ValueError):
        staging.new_bucket("browser")


def test_stage_path_makes_a_ready_real_copy(root, tmp_path):
    source = tmp_path / "photo.png"
    source.write_bytes(b"\x89PNG data")
    ref = staging.stage_path(source, bucket=staging.new_bucket("cli"), origin="cli")
    entry = staging.load_entry(ref)
    assert (entry.filename, entry.size) == ("photo.png", 9)  # len(b"\x89PNG data") == 9
    assert entry.path.read_bytes() == b"\x89PNG data"
    assert os.stat(entry.path).st_ino != os.stat(source).st_ino
    marker = orjson.loads((staging.entry_dir(ref) / "oneshot.json").read_bytes())
    assert marker["origin"] == "cli"
    assert marker["at"]


def test_stage_path_name_overrides_the_base_name(root, tmp_path):
    source = tmp_path / "x.bin"
    source.write_bytes(b"1")
    ref = staging.stage_path(source, bucket=staging.new_bucket("api"), origin="api", name="report.pdf")
    assert staging.load_entry(ref).filename == "report.pdf"


@pytest.mark.parametrize(("name", "expected"), [
    ("a\nb.txt", "a_b.txt"),
    ("../x.txt", ".._x.txt"),
    (".", "attachment"),
    ("", "attachment"),
    (".twicc-upload-x", "_.twicc-upload-x"),
])
def test_stage_bytes_sanitizes_the_name(root, name, expected):
    ref = staging.stage_bytes(b"data", name, bucket=staging.new_bucket("api"), origin="api")
    assert staging.load_entry(ref).filename == expected


def test_a_zero_byte_file_is_ready(root):
    ref = staging.stage_bytes(b"", "empty.txt", bucket=staging.new_bucket("cli"), origin="cli")
    assert staging.load_entry(ref).size == 0


def test_a_long_name_is_truncated_before_its_extension(root):
    ref = staging.stage_bytes(b"x", "a" * 400 + ".pdf", bucket=staging.new_bucket("cli"), origin="cli")
    name = staging.load_entry(ref).filename
    assert name.endswith(".pdf")
    assert len(name.encode()) <= staging.name_max_bytes()


def test_an_undecodable_file_name_is_made_valid_utf8(root, tmp_path):
    source = tmp_path / os.fsdecode(b"bad\xffname.txt")
    source.write_bytes(b"x")
    ref = staging.stage_path(source, bucket=staging.new_bucket("cli"), origin="cli")
    assert staging.load_entry(ref).filename == "bad�name.txt"


def test_the_marker_exists_before_the_copy(root, tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")
    seen = []
    real_copy = shutil.copyfileobj

    def spy(reader, writer, length=0):
        seen.append((Path(writer.name).parent.parent / "oneshot.json").exists())
        return real_copy(reader, writer, length)

    monkeypatch.setattr(staging.shutil, "copyfileobj", spy)
    staging.stage_path(source, bucket=staging.new_bucket("cli"), origin="cli")
    assert seen == [True]


def test_an_interruption_during_the_copy_removes_the_entry(root, tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")

    def interrupted(reader, writer, length=0):
        raise KeyboardInterrupt

    monkeypatch.setattr(staging.shutil, "copyfileobj", interrupted)
    bucket = staging.new_bucket("cli")
    with pytest.raises(KeyboardInterrupt):
        staging.stage_path(source, bucket=bucket, origin="cli")
    assert list((root / bucket).iterdir()) == []


def test_a_disk_error_removes_the_entry_and_is_attachment_stage_failed(root, tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")

    def disk_full(reader, writer, length=0):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(staging.shutil, "copyfileobj", disk_full)
    bucket = staging.new_bucket("cli")
    with pytest.raises(AttachmentError) as exc:
        staging.stage_path(source, bucket=bucket, origin="cli")
    assert exc.value.code == "attachment_stage_failed"
    assert "No space left on device" in str(exc.value)
    assert list((root / bucket).iterdir()) == []


def test_an_unreadable_source_is_attachment_stage_failed(root, tmp_path):
    with pytest.raises(AttachmentError) as exc:
        staging.stage_path(tmp_path / "missing.txt", bucket=staging.new_bucket("cli"), origin="cli")
    assert exc.value.code == "attachment_stage_failed"


def test_the_stage_error_names_the_normalized_file_never_the_raw_name(root, monkeypatch):
    def disk_full(out):
        raise OSError(errno.ENOSPC, "No space left on device")

    raw_name = "a\nb" + "x" * 5000 + ".txt"  # e.g. an unbounded name= of a data URI over the RPC
    with pytest.raises(AttachmentError) as exc:
        staging._stage(disk_full, raw_name, bucket=staging.new_bucket("api"), origin="api")
    message = str(exc.value)
    assert "\n" not in message
    assert "x" * 300 not in message
    assert len(message.encode()) < staging.name_max_bytes() + 100


@pytest.mark.parametrize("failing", ["marker", "file_dir"])
def test_a_failure_while_creating_the_entry_leaves_no_entry(root, monkeypatch, failing):
    if failing == "marker":
        real_write_marker = staging.write_marker

        def write_marker(entry, name, payload):
            if name == staging.ONESHOT_MARKER:
                raise OSError(errno.ENOSPC, "No space left on device")
            real_write_marker(entry, name, payload)

        monkeypatch.setattr(staging, "write_marker", write_marker)
    else:
        real_mkdir = Path.mkdir

        def mkdir(self, *args, **kwargs):
            if self.name == staging.FILE_DIR:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    bucket = staging.new_bucket("cli")
    with pytest.raises(AttachmentError) as exc:
        staging.stage_bytes(b"x", "a.txt", bucket=bucket, origin="cli")
    assert exc.value.code == "attachment_stage_failed"
    assert list((root / bucket).iterdir()) == []


def test_a_hard_kill_before_the_ready_marker_leaves_a_not_ready_oneshot_entry(root):
    bucket = staging.new_bucket("cli")
    script = textwrap.dedent("""
        import os
        import sys

        from twicc.core.services.attachments import staging

        real_write_marker = staging.write_marker

        def write_marker(entry, name, payload):
            if name == staging.READY_MARKER:
                os._exit(9)
            real_write_marker(entry, name, payload)

        staging.write_marker = write_marker
        staging.stage_bytes(b"data", "a.txt", bucket=sys.argv[1], origin="cli")
    """)
    result = subprocess.run([sys.executable, "-c", script, bucket], env=dict(os.environ), check=False)
    assert result.returncode == 9
    [entry] = list((root / bucket).iterdir())
    assert (entry / "oneshot.json").exists()
    with pytest.raises(AttachmentError) as exc:
        staging.load_entry(staging.validate_ref({"bucket": bucket, "id": entry.name}))
    assert exc.value.code == "attachment_not_ready"


def test_a_bucket_removed_between_the_two_mkdir_calls_is_recreated(root, monkeypatch):
    calls = []
    real_mkdir_entry = staging._mkdir_entry

    def racing(entry):
        calls.append(entry)
        if len(calls) == 1:
            shutil.rmtree(entry.parent)  # the reaper removes the empty bucket
        real_mkdir_entry(entry)

    monkeypatch.setattr(staging, "_mkdir_entry", racing)
    ref = staging.stage_bytes(b"x", "a.txt", bucket=staging.new_bucket("api"), origin="api")
    assert len(calls) == 2
    assert staging.load_entry(ref).size == 1


def test_discard_staged_removes_only_the_given_entries_then_the_empty_bucket(root):
    bucket = staging.new_bucket("cli")
    first = staging.stage_bytes(b"1", "a.txt", bucket=bucket, origin="cli")
    second = staging.stage_bytes(b"2", "b.txt", bucket=bucket, origin="cli")
    staging.discard_staged([first])
    with pytest.raises(AttachmentError):
        staging.load_entry(first)
    assert staging.load_entry(second).size == 1
    staging.discard_staged([second])
    assert not (root / bucket).exists()


def test_discard_staged_never_touches_artifacts(root, tmp_path):
    artifact = Path(os.environ["TWICC_DATA_DIR"]) / "artifacts" / "s" / "attachments" / "kept.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("kept")
    staging.discard_staged([staging.validate_ref({"bucket": "s", "id": str(uuid.uuid4())})])
    assert artifact.read_text() == "kept"


def test_name_max_bytes_lives_in_staging():
    from twicc.uploads import views

    assert not hasattr(views, "_composer_name_max_bytes")
    assert 1 <= staging.name_max_bytes() <= 240
