"""Staging identity, filename normalization and durable markers of composer attachments."""

import uuid
from pathlib import Path

import orjson
import pytest

from twicc.core.services.attachments import staging
from twicc.core.services.attachments.staging import (
    AttachmentError,
    get_composer_attachments_dir,
    load_entry,
    normalize_filename,
    on_upload_completed,
    validate_ref,
)
from twicc.core.services.attachments.types import AttachmentRef


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("TWICC_DATA_DIR", str(tmp_path))
    return get_composer_attachments_dir()


def make_entry(root: Path, bucket="b1", filename="doc.txt", content=b"hello", ready=True, size=None):
    att_id = str(uuid.uuid4())
    entry = root / bucket / att_id
    (entry / "file").mkdir(parents=True)
    source = entry / "file" / filename
    source.write_bytes(content)
    if ready:
        (entry / "ready.json").write_bytes(
            orjson.dumps({"filename": filename, "size": len(content) if size is None else size})
        )
    return AttachmentRef(bucket, att_id), entry, source


def meta_for(ref, size):
    return {"origin": {"panel": "composer", "key": f"{ref.bucket}/{ref.id}"}, "size": size}


# ── normalize_filename ──


def test_normalize_examples():
    assert normalize_filename("../a\n@.txt", 255) == ".._a_@.txt"
    assert normalize_filename(" . ", 255) == "attachment"
    assert normalize_filename(".twicc-upload-x", 255) == "_.twicc-upload-x"


@pytest.mark.parametrize(
    "char",
    ["/", "\\", "\x00", "\x7f", "\x85", " ", " ", *[chr(c) for c in range(0x20)]],
)
def test_normalize_replaces_forbidden_chars(char):
    assert normalize_filename(f"a{char}b.txt", 255) == "a_b.txt"


@pytest.mark.parametrize("name", ["", " ", "\t\n", ".", "..", " .. "])
def test_normalize_empty_dot_names(name):
    assert normalize_filename(name, 255) == "attachment"


def test_normalize_truncates_before_extension_on_utf8_bytes():
    name = "é" * 200 + ".pdf"
    result = normalize_filename(name, 100)
    assert result.endswith(".pdf")
    assert len(result.encode()) <= 100
    assert result[: -len(".pdf")] == "é" * ((100 - 4) // 2)


def test_normalize_truncation_never_splits_a_character():
    result = normalize_filename("a" + "😀" * 50, 10)
    result.encode()
    assert len(result.encode()) <= 10


def test_normalize_short_name_untouched():
    assert normalize_filename("report v2.pdf", 255) == "report v2.pdf"


def test_normalize_oversize_extension_is_truncated_as_a_whole():
    result = normalize_filename("a." + "x" * 50, 10)
    assert len(result.encode()) <= 10
    assert result


# ── validate_ref ──


def test_validate_ref_ok():
    att_id = str(uuid.uuid4())
    assert validate_ref({"bucket": "sess-1", "id": att_id}) == AttachmentRef("sess-1", att_id)


@pytest.mark.parametrize("bucket", ["", ".", "..", "a/b", "a\\b", "a\x00b", 5, None])
def test_validate_ref_rejects_bad_bucket(bucket):
    with pytest.raises(AttachmentError):
        validate_ref({"bucket": bucket, "id": str(uuid.uuid4())})


@pytest.mark.parametrize(
    "att_id",
    [
        "",
        ".released",
        "not-a-uuid",
        str(uuid.uuid4()).upper(),
        uuid.uuid4().hex,
        "{" + str(uuid.uuid4()) + "}",
        5,
        None,
    ],
)
def test_validate_ref_rejects_noncanonical_id(att_id):
    with pytest.raises(AttachmentError):
        validate_ref({"bucket": "b", "id": att_id})


@pytest.mark.parametrize("raw", [None, "x", [], {"bucket": "b"}, {"id": str(uuid.uuid4())}])
def test_validate_ref_rejects_bad_shape(raw):
    with pytest.raises(AttachmentError):
        validate_ref(raw)


# ── load_entry ──


def test_load_entry_ready(root):
    ref, entry, source = make_entry(root)
    loaded = load_entry(ref)
    assert loaded.size == source.stat().st_size
    assert loaded.filename == "doc.txt"
    assert loaded.path == entry / "file" / "doc.txt"
    assert loaded.promoted is None
    assert loaded.ref == ref


def test_load_entry_absent_is_missing(root):
    with pytest.raises(AttachmentError) as exc:
        load_entry(AttachmentRef("b", str(uuid.uuid4())))
    assert exc.value.code == "attachment_missing"


def test_load_entry_without_ready_marker_is_not_ready(root):
    ref, _, _ = make_entry(root, ready=False)
    with pytest.raises(AttachmentError) as exc:
        load_entry(ref)
    assert exc.value.code == "attachment_not_ready"


def test_load_entry_size_mismatch_is_not_ready(root):
    ref, _, _ = make_entry(root, size=999)
    with pytest.raises(AttachmentError) as exc:
        load_entry(ref)
    assert exc.value.code == "attachment_not_ready"


def test_load_entry_ignores_temporary_and_placeholder_files(root):
    ref, entry, _ = make_entry(root)
    (entry / "file" / ".twicc-upload-abc.tmp").write_bytes(b"partial")
    (entry / "file" / "other (1).txt").write_bytes(b"")
    assert load_entry(ref).filename == "doc.txt"


def test_load_entry_ready_pointing_at_missing_file_is_not_ready(root):
    ref, _, source = make_entry(root)
    source.unlink()
    with pytest.raises(AttachmentError) as exc:
        load_entry(ref)
    assert exc.value.code == "attachment_not_ready"


@pytest.mark.parametrize("filename", ["../x", "a/b", "", ".", ".."])
def test_load_entry_refuses_unsafe_ready_filename(root, filename):
    ref, entry, _ = make_entry(root)
    (entry / "ready.json").write_bytes(orjson.dumps({"filename": filename, "size": 5}))
    with pytest.raises(AttachmentError):
        load_entry(ref)


def test_load_entry_refuses_symlink_escaping_staging(root, tmp_path):
    ref, entry, source = make_entry(root)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"hello")
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(AttachmentError):
        load_entry(ref)


def test_load_entry_refuses_entry_directory_symlink(root, tmp_path):
    ref, entry, _ = make_entry(root)
    real = tmp_path / "elsewhere"
    entry.rename(real)
    entry.symlink_to(real)
    with pytest.raises(AttachmentError):
        load_entry(ref)


def write_promoted(entry, final_path, **over):
    data = {
        "session_id": "s1",
        "final_path": str(final_path),
        "final_name": final_path.name,
        "kind": "PDF",
        "original_name": "doc.txt",
        "size": 5,
    }
    data.update(over)
    (entry / "promoted.json").write_bytes(orjson.dumps(data))


def test_load_entry_promoted(root, tmp_path):
    ref, entry, source = make_entry(root)
    final = tmp_path / "artifacts" / "s1" / "attachments" / "doc.txt"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"hello")
    source.unlink()
    write_promoted(entry, final)
    loaded = load_entry(ref)
    assert loaded.path is None
    assert loaded.promoted is not None
    assert loaded.promoted.final_path == final
    assert loaded.promoted.session_id == "s1"
    assert loaded.promoted.size == 5
    assert loaded.promoted.kind == "PDF"
    assert loaded.size == 5


def test_load_entry_promoted_target_missing_is_attachment_missing(root, tmp_path):
    ref, entry, source = make_entry(root)
    source.unlink()
    write_promoted(entry, tmp_path / "gone" / "doc.txt")
    with pytest.raises(AttachmentError) as exc:
        load_entry(ref)
    assert exc.value.code == "attachment_missing"


def test_load_entry_invalid_ref_never_touches_disk(root):
    with pytest.raises(AttachmentError):
        load_entry(AttachmentRef("../x", str(uuid.uuid4())))


# ── on_upload_completed ──


def completed_entry(root, content=b"hello", name="doc.txt"):
    ref, entry, source = make_entry(root, filename=name, content=content, ready=False)
    return ref, entry, source


def test_completion_writes_ready_marker(root):
    ref, entry, source = completed_entry(root)
    on_upload_completed(meta_for(ref, 5), source)
    assert orjson.loads((entry / "ready.json").read_bytes()) == {"filename": "doc.txt", "size": 5}
    assert load_entry(ref).filename == "doc.txt"


def test_completion_uses_final_renamed_basename(root):
    ref, entry, source = completed_entry(root, name="doc (1).txt")
    on_upload_completed(meta_for(ref, 5), str(source))
    assert orjson.loads((entry / "ready.json").read_bytes())["filename"] == "doc (1).txt"


def test_completion_is_idempotent(root):
    ref, entry, source = completed_entry(root)
    on_upload_completed(meta_for(ref, 5), source)
    first = (entry / "ready.json").read_bytes()
    on_upload_completed(meta_for(ref, 5), source)
    assert (entry / "ready.json").read_bytes() == first


def test_completion_never_creates_a_removed_entry(root):
    ref = AttachmentRef("b1", str(uuid.uuid4()))
    final = root / "b1" / ref.id / "file" / "doc.txt"
    on_upload_completed(meta_for(ref, 5), final)
    assert not (root / "b1" / ref.id).exists()


def test_completion_ignores_file_outside_entry_file_dir(root, tmp_path):
    ref, entry, _ = completed_entry(root)
    other = tmp_path / "other.txt"
    other.write_bytes(b"hello")
    on_upload_completed(meta_for(ref, 5), other)
    assert not (entry / "ready.json").exists()


def test_completion_ignores_size_mismatch(root):
    ref, entry, source = completed_entry(root)
    on_upload_completed(meta_for(ref, 6), source)
    assert not (entry / "ready.json").exists()


def test_completion_ignores_other_origins(root):
    ref, entry, source = completed_entry(root)
    on_upload_completed({"origin": {"panel": "files", "key": "x"}, "size": 5}, source)
    assert not (entry / "ready.json").exists()


def test_completion_writes_marker_durably(root, monkeypatch):
    ref, entry, source = completed_entry(root)
    synced = []
    real_fsync = staging.os.fsync
    monkeypatch.setattr(staging.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    on_upload_completed(meta_for(ref, 5), source)
    assert len(synced) >= 2  # the marker file and its directory
    assert not list(entry.glob("*.tmp"))
