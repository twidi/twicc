"""Prepare and finish of composer attachments: retry-safe promotion without overwrite (spec §6.1.3, §7.1-§7.2).

Every link, copy and marker failure is injected; concurrent writers are simulated by creating a
candidate name just before the committer claims it.
"""

import errno
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest

from twicc import composer_attachments_cleanup_task as cleanup
from twicc.agent.exceptions import SendDeliveryError
from twicc.core.services.attachments import committer, staging
from twicc.core.services.attachments.committer import discard_prepared, finish_attachments, prepare_attachments
from twicc.core.services.attachments.staging import load_entry
from twicc.core.services.attachments.types import (
    AttachmentPlan,
    ManifestEntry,
    NativePart,
    PlannedEntry,
    PlanTarget,
    PreparedAttachments,
)
from twicc.paths import get_artifacts_dir

from tests.test_composer_attachments_staging import make_entry, root  # noqa: F401 - fixture

TARGET = PlanTarget("claude_code", False, False, "opus", False, "first_party")
OLD = time.time() - 3 * 86400


# ── Builders ──


def plan_of(*specs) -> AttachmentPlan:
    """A plan from ``(ref, kind, native)`` specs, numbered and ranked like the planner does."""
    totals: dict[str, int] = {}
    for _ref, kind, _native in specs:
        totals[kind] = totals.get(kind, 0) + 1
    ranks: dict[str, int] = {}
    entries = []
    for n, (ref, kind, native) in enumerate(specs, start=1):
        source = load_entry(ref)
        ranks[kind] = ranks.get(kind, 0) + 1
        name = source.promoted.original_name if source.promoted is not None else source.filename
        mode = "file" if native is None else "inline"
        entries.append(PlannedEntry(ref, n, name, kind, ranks[kind], totals[kind], mode, source, native))
    return AttachmentPlan(TARGET, tuple(entries))


def stage(root, name="notes.txt", content=b"notes", bucket="b1"):  # noqa: F811 - fixture name
    ref, entry, source = make_entry(root, bucket=bucket, filename=name, content=content)
    os.utime(source, (OLD, OLD))
    return ref, entry, source


def attachments_dir(session_id: str) -> Path:
    return get_artifacts_dir() / session_id / "attachments"


def precopies() -> list[Path]:
    artifacts = get_artifacts_dir()
    if not artifacts.is_dir():
        return []
    return sorted(p for p in artifacts.iterdir() if p.name.startswith(".twicc-upload-") and p.name.endswith(".tmp"))


def commit(plan: AttachmentPlan, session_id: str, *, prepare_session: object = "same", text="hello"):
    prepared = prepare_attachments(plan, session_id=session_id if prepare_session == "same" else prepare_session)
    try:
        return finish_attachments(prepared, session_id=session_id, text=text)
    finally:
        discard_prepared(prepared)


def promoted_marker(entry: Path) -> dict:
    return orjson.loads((entry / "promoted.json").read_bytes())


def oserror(code: int) -> OSError:
    return OSError(code, os.strerror(code))


# ── Promotion of a staged file ──


def test_file_entry_is_promoted_and_tombstoned(root):  # noqa: F811
    ref, entry, source = stage(root)
    content = commit(plan_of((ref, "text", None)), "s1", text="raw <twicc:context>x</twicc:context>")

    final = attachments_dir("s1") / "notes.txt"
    assert final.read_bytes() == b"notes"
    assert content.user_text == "raw <twicc:context>x</twicc:context>"
    assert content.native_parts == ()
    assert content.manifest.owner == "s1"
    assert content.manifest.directory == Path(os.path.realpath(attachments_dir("s1")))
    (manifest_entry,) = content.manifest.entries
    assert manifest_entry == ManifestEntry(1, "notes.txt", "text", 1, 1, "file", "notes.txt")
    marker = promoted_marker(entry)
    assert marker == {
        "session_id": "s1",
        "final_path": str(Path(os.path.realpath(final))),
        "final_name": "notes.txt",
        "kind": "text",
        "original_name": "notes.txt",
        "size": 5,
    }
    assert not source.exists()
    assert (entry / "ready.json").exists()
    assert precopies() == []
    reloaded = load_entry(ref)
    assert reloaded.promoted is not None and reloaded.promoted.final_name == "notes.txt"


def test_occupied_destination_is_never_overwritten(root):  # noqa: F811
    ref, entry, _source = stage(root)
    occupied_destination = attachments_dir("s1") / "notes.txt"
    occupied_destination.parent.mkdir(parents=True)
    occupied_bytes = b"someone else's notes"
    occupied_destination.write_bytes(occupied_bytes)

    content = commit(plan_of((ref, "text", None)), "s1")

    assert content.manifest.entries[0].artifact_name == "notes (1).txt"
    assert content.manifest.entries[0].name == "notes.txt"
    assert occupied_destination.read_bytes() == occupied_bytes
    assert (attachments_dir("s1") / "notes (1).txt").read_bytes() == b"notes"
    marker = promoted_marker(entry)
    assert marker["original_name"] == "notes.txt"
    assert marker["final_name"] == "notes (1).txt"


def test_concurrent_claim_moves_to_next_candidate(root, monkeypatch):  # noqa: F811
    ref, _entry, _source = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    target = attachments_dir("s1")
    real_link = os.link
    raced: list[str] = []

    def racing_link(src, dst, *args, **kwargs):
        dst_path = Path(dst)
        if dst_path.parent == Path(os.path.realpath(target)) and not raced:
            # Another writer claims the very name between our check and our link.
            dst_path.write_bytes(b"concurrent writer")
            raced.append(dst_path.name)
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    try:
        content = finish_attachments(prepared, session_id="s1", text="")
    finally:
        discard_prepared(prepared)
    assert raced == ["notes.txt"]
    assert (target / "notes.txt").read_bytes() == b"concurrent writer"
    assert content.manifest.entries[0].artifact_name == "notes (1).txt"
    assert (target / "notes (1).txt").read_bytes() == b"notes"


def test_committed_marker_written_for_every_entry_before_promotion(root, monkeypatch):  # noqa: F811
    image_ref, image_entry, _ = stage(root, "shot.png", b"png-bytes")
    file_ref, file_entry, _ = stage(root, "notes.txt", b"notes")
    native = NativePart("image", "image/png", b"normalized")
    plan = plan_of((image_ref, "image", native), (file_ref, "text", None))
    seen: list[tuple[bool, bool]] = []
    real_promote = staging.promote_entry

    def spying_promote(prepared_entry, session_id):
        seen.append(((image_entry / "committed.json").exists(), (file_entry / "committed.json").exists()))
        return real_promote(prepared_entry, session_id)

    monkeypatch.setattr(staging, "promote_entry", spying_promote)
    commit(plan, "s1")
    assert seen == [(True, True)]
    for entry in (image_entry, file_entry):
        marker = orjson.loads((entry / "committed.json").read_bytes())
        assert set(marker) == {"at"}
        assert datetime.fromisoformat(marker["at"]).tzinfo is not None


def test_native_entries_get_no_artifact_and_keep_order(root):  # noqa: F811
    first_ref, first_entry, first_source = stage(root, "a.png", b"aaa")
    file_ref, _, _ = stage(root, "clip.mp4", b"video")
    second_ref, _, _ = stage(root, "b.txt", b"text")
    first = NativePart("image", "image/png", b"A")
    second = NativePart("text", "text/plain", "B")
    plan = plan_of((first_ref, "image", first), (file_ref, "video", None), (second_ref, "text", second))

    content = commit(plan, "s1")

    assert content.native_parts == (first, second)
    assert [(e.n, e.name, e.mode, e.artifact_name) for e in content.manifest.entries] == [
        (1, "a.png", "inline", None),
        (2, "clip.mp4", "file", "clip.mp4"),
        (3, "b.txt", "inline", None),
    ]
    assert sorted(p.name for p in attachments_dir("s1").iterdir()) == ["clip.mp4"]
    # The native entry stays staged (released after delivery), with no tombstone.
    assert first_source.read_bytes() == b"aaa"
    assert not (first_entry / "promoted.json").exists()


def test_only_native_entries_have_no_directory(root):  # noqa: F811
    ref, _, _ = stage(root, "a.png", b"aaa")
    content = commit(plan_of((ref, "image", NativePart("image", "image/png", b"A"))), "s1")
    assert content.manifest.directory is None
    assert not attachments_dir("s1").exists()
    assert precopies() == []


# ── Pre-copies ──


def test_precopy_is_a_fresh_top_level_hard_link(root):  # noqa: F811
    ref, _entry, source = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    try:
        (prepared_entry,) = prepared.entries
        precopy = prepared_entry.precopy
        assert precopy is not None
        assert precopy.parent == Path(os.path.realpath(get_artifacts_dir()))
        assert precopy.name.startswith(".twicc-upload-") and precopy.name.endswith(".tmp")
        assert precopy.stat().st_ino == source.stat().st_ino
        assert time.time() - precopy.stat().st_mtime < 60
        # The 24 h reaper must not remove a pre-copy created just now, whatever the staged mtime was.
        assert cleanup._sweep_precopies(datetime.now(UTC)) == 0
        assert precopy.exists()
    finally:
        discard_prepared(prepared)
    assert precopies() == []
    assert source.exists()


def test_precopy_is_fsynced(root, monkeypatch):  # noqa: F811
    ref, _entry, source = stage(root)
    synced: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd):
        synced.append(os.fstat(fd).st_ino)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    try:
        assert prepared.entries[0].precopy.stat().st_ino in synced
    finally:
        discard_prepared(prepared)


@pytest.mark.parametrize("code", [errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS])
def test_precopy_falls_back_to_a_copy(root, monkeypatch, code):  # noqa: F811
    ref, _entry, source = stage(root)

    def no_link(src, dst, *args, **kwargs):
        raise oserror(code)

    monkeypatch.setattr(os, "link", no_link)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    try:
        precopy = prepared.entries[0].precopy
        assert precopy.read_bytes() == b"notes"
        assert precopy.stat().st_ino != source.stat().st_ino
        assert time.time() - precopy.stat().st_mtime < 60
    finally:
        discard_prepared(prepared)


def test_link_error_is_commit_failed_and_leaves_nothing(root, monkeypatch):  # noqa: F811
    first_ref, _, first_source = stage(root, "a.txt", b"a")
    second_ref, _, second_source = stage(root, "b.txt", b"b")
    real_link = os.link

    def failing_link(src, dst, *args, **kwargs):
        if Path(src) == second_source:
            raise oserror(errno.EIO)
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", failing_link)
    with pytest.raises(SendDeliveryError) as exc:
        prepare_attachments(plan_of((first_ref, "text", None), (second_ref, "text", None)), session_id="s1")
    assert exc.value.code == "attachment_commit_failed"
    assert precopies() == []
    assert load_entry(first_ref).path == first_source
    assert load_entry(second_ref).path == second_source


def test_copy_error_is_commit_failed_and_leaves_nothing(root, monkeypatch):  # noqa: F811
    ref, _, source = stage(root)

    def no_link(src, dst, *args, **kwargs):
        raise oserror(errno.EXDEV)

    def full_disk(*args, **kwargs):
        raise oserror(errno.ENOSPC)

    monkeypatch.setattr(os, "link", no_link)
    monkeypatch.setattr(committer.shutil, "copyfileobj", full_disk)
    with pytest.raises(SendDeliveryError) as exc:
        prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    assert exc.value.code == "attachment_commit_failed"
    assert precopies() == []
    assert load_entry(ref).path == source


def test_staged_file_gone_at_prepare_is_commit_failed(root):  # noqa: F811
    ref, _, source = stage(root)
    plan = plan_of((ref, "text", None))
    source.unlink()
    with pytest.raises(SendDeliveryError) as exc:
        prepare_attachments(plan, session_id="s1")
    assert exc.value.code == "attachment_commit_failed"
    assert precopies() == []


def test_native_entries_get_no_precopy(root):  # noqa: F811
    ref, _, _ = stage(root, "a.png", b"aaa")
    prepared = prepare_attachments(plan_of((ref, "image", NativePart("image", "image/png", b"A"))), session_id="s1")
    assert prepared.entries[0].precopy is None
    assert precopies() == []


def test_discard_prepared_is_idempotent(root):  # noqa: F811
    ref, _, _ = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    assert len(precopies()) == 1
    discard_prepared(prepared)
    discard_prepared(prepared)
    assert precopies() == []


# ── Finish without hard links ──


@pytest.mark.parametrize("code", [errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS])
def test_finish_without_hard_links_reserves_then_replaces(root, monkeypatch, code):  # noqa: F811
    ref, entry, source = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    occupied = attachments_dir("s1") / "notes.txt"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"taken")

    def no_link(src, dst, *args, **kwargs):
        raise oserror(code)

    monkeypatch.setattr(os, "link", no_link)
    try:
        content = finish_attachments(prepared, session_id="s1", text="")
    finally:
        discard_prepared(prepared)
    assert occupied.read_bytes() == b"taken"
    assert content.manifest.entries[0].artifact_name == "notes (1).txt"
    assert (attachments_dir("s1") / "notes (1).txt").read_bytes() == b"notes"
    assert promoted_marker(entry)["final_name"] == "notes (1).txt"
    assert not source.exists()
    assert precopies() == []


def test_finish_cross_device_claim_is_commit_failed(root, monkeypatch):  # noqa: F811
    ref, entry, source = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")

    def cross_device(src, dst, *args, **kwargs):
        raise oserror(errno.EXDEV)

    monkeypatch.setattr(os, "link", cross_device)
    with pytest.raises(SendDeliveryError) as exc:
        finish_attachments(prepared, session_id="s1", text="")
    assert exc.value.code == "attachment_commit_failed"
    assert precopies() == []
    assert source.read_bytes() == b"notes"
    assert not (entry / "promoted.json").exists()


# ── Crash order ──


def test_tombstone_is_durable_before_sources_are_removed(root, monkeypatch):  # noqa: F811
    ref, entry, source = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    precopy = prepared.entries[0].precopy
    events: list[tuple[str, str]] = []
    real_write_marker = staging.write_marker
    real_unlink = os.unlink

    def recording_write_marker(directory, name, payload):
        real_write_marker(directory, name, payload)
        events.append(("marker", name))

    def recording_unlink(path, *args, **kwargs):
        events.append(("unlink", str(path)))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(staging, "write_marker", recording_write_marker)
    monkeypatch.setattr(os, "unlink", recording_unlink)
    finish_attachments(prepared, session_id="s1", text="")

    tombstone = events.index(("marker", "promoted.json"))
    assert events.index(("unlink", str(source))) > tombstone
    assert events.index(("unlink", str(precopy))) > tombstone


class Crash(BaseException):
    """A process death: nothing after it runs."""


def test_crash_after_tombstone_leaves_a_promoted_entry(root, monkeypatch):  # noqa: F811
    ref, entry, source = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    real_unlink = os.unlink

    def crashing_unlink(path, *args, **kwargs):
        if Path(path) == source:
            raise Crash
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", crashing_unlink)
    with pytest.raises(Crash):
        finish_attachments(prepared, session_id="s1", text="")
    monkeypatch.setattr(os, "unlink", real_unlink)
    reloaded = load_entry(ref)
    assert reloaded.promoted is not None
    assert reloaded.promoted.final_path.read_bytes() == b"notes"


def test_tombstone_failure_keeps_ready_source_and_retry_duplicates(root, monkeypatch):  # noqa: F811
    ref, entry, source = stage(root)
    real_write_marker = staging.write_marker

    def failing_tombstone(directory, name, payload):
        if name == "promoted.json":
            raise oserror(errno.EIO)
        return real_write_marker(directory, name, payload)

    monkeypatch.setattr(staging, "write_marker", failing_tombstone)
    with pytest.raises(SendDeliveryError) as exc:
        commit(plan_of((ref, "text", None)), "s1")
    assert exc.value.code == "attachment_commit_failed"
    monkeypatch.setattr(staging, "write_marker", real_write_marker)

    assert precopies() == []
    assert load_entry(ref).path == source
    assert source.read_bytes() == b"notes"

    # Retry: the entry is still ready; the first claim survived, so the retry takes the next name.
    content = commit(plan_of((ref, "text", None)), "s1")
    assert content.manifest.entries[0].artifact_name == "notes (1).txt"
    assert promoted_marker(entry)["final_name"] == "notes (1).txt"


def test_failure_on_a_later_entry_keeps_earlier_promotions_retryable(root, monkeypatch):  # noqa: F811
    first_ref, first_entry, _ = stage(root, "a.txt", b"a")
    second_ref, second_entry, second_source = stage(root, "b.txt", b"b")
    real_write_marker = staging.write_marker

    def failing_second(directory, name, payload):
        if name == "promoted.json" and directory == second_entry:
            raise oserror(errno.EIO)
        return real_write_marker(directory, name, payload)

    monkeypatch.setattr(staging, "write_marker", failing_second)
    with pytest.raises(SendDeliveryError):
        commit(plan_of((first_ref, "text", None), (second_ref, "text", None)), "s1")
    monkeypatch.setattr(staging, "write_marker", real_write_marker)
    assert precopies() == []
    assert load_entry(first_ref).promoted.final_name == "a.txt"
    assert load_entry(second_ref).path == second_source

    content = commit(plan_of((first_ref, "text", None), (second_ref, "text", None)), "s1")
    assert [e.artifact_name for e in content.manifest.entries] == ["a.txt", "b (1).txt"]
    assert sorted(p.name for p in attachments_dir("s1").iterdir()) == ["a.txt", "b (1).txt", "b.txt"]


def test_released_entry_at_finish_is_missing(root):  # noqa: F811
    import shutil

    ref, entry, _ = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    shutil.rmtree(entry)
    with pytest.raises(SendDeliveryError) as exc:
        finish_attachments(prepared, session_id="s1", text="")
    assert exc.value.code == "attachment_missing"
    assert precopies() == []
    assert not entry.exists()


# ── Tombstone reuse ──


def test_same_session_tombstone_reuses_the_file(root):  # noqa: F811
    ref, entry, _ = stage(root)
    commit(plan_of((ref, "text", None)), "s1")
    original = attachments_dir("s1") / "notes.txt"
    inode = original.stat().st_ino

    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    assert prepared.entries[0].precopy is None
    content = finish_attachments(prepared, session_id="s1", text="")
    assert content.manifest.entries[0].name == "notes.txt"
    assert content.manifest.entries[0].artifact_name == "notes.txt"
    assert sorted(p.name for p in attachments_dir("s1").iterdir()) == ["notes.txt"]
    assert original.stat().st_ino == inode
    assert promoted_marker(entry)["session_id"] == "s1"


def test_same_session_tombstone_uses_final_name_not_original(root):  # noqa: F811
    ref, entry, _ = stage(root)
    occupied = attachments_dir("s1") / "notes.txt"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"other")
    commit(plan_of((ref, "text", None)), "s1")

    plan = plan_of((ref, "text", None))
    assert plan.entries[0].name == "notes.txt"
    content = commit(plan, "s1")
    assert content.manifest.entries[0].artifact_name == "notes (1).txt"


def test_same_session_tombstone_vanished_before_finish_is_missing(root):  # noqa: F811
    ref, _, _ = stage(root)
    commit(plan_of((ref, "text", None)), "s1")
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")
    (attachments_dir("s1") / "notes.txt").unlink()
    with pytest.raises(SendDeliveryError) as exc:
        finish_attachments(prepared, session_id="s1", text="")
    assert exc.value.code == "attachment_missing"


@pytest.mark.parametrize("prepare_session", ["s2", None])
def test_other_session_tombstone_gets_independent_bytes(root, prepare_session):  # noqa: F811
    ref, entry, _ = stage(root)
    commit(plan_of((ref, "text", None)), "s1")
    original_session_file = attachments_dir("s1") / "notes.txt"

    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id=prepare_session)
    try:
        precopy = prepared.entries[0].precopy
        assert precopy is not None
        assert precopy.stat().st_ino != original_session_file.stat().st_ino
        content = finish_attachments(prepared, session_id="s2", text="")
    finally:
        discard_prepared(prepared)

    other_session_file = attachments_dir("s2") / "notes.txt"
    assert other_session_file.read_bytes() == b"notes"
    assert other_session_file.stat().st_ino != original_session_file.stat().st_ino
    assert original_session_file.read_bytes() == b"notes"
    assert content.manifest.owner == "s2"
    assert content.manifest.entries[0].artifact_name == "notes.txt"
    marker = promoted_marker(entry)
    assert marker["session_id"] == "s2"
    assert marker["final_path"] == str(Path(os.path.realpath(other_session_file)))
    assert marker["original_name"] == "notes.txt"
    assert precopies() == []


def test_other_session_tombstone_claims_a_free_name(root):  # noqa: F811
    ref, entry, _ = stage(root)
    commit(plan_of((ref, "text", None)), "s1")
    occupied = attachments_dir("s2") / "notes.txt"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"s2 notes")

    content = commit(plan_of((ref, "text", None)), "s2")
    assert occupied.read_bytes() == b"s2 notes"
    assert content.manifest.entries[0].artifact_name == "notes (1).txt"
    assert promoted_marker(entry)["final_name"] == "notes (1).txt"


def test_other_session_source_gone_at_prepare_is_missing(root):  # noqa: F811
    ref, _, _ = stage(root)
    commit(plan_of((ref, "text", None)), "s1")
    plan = plan_of((ref, "text", None))
    (attachments_dir("s1") / "notes.txt").unlink()
    with pytest.raises(SendDeliveryError) as exc:
        prepare_attachments(plan, session_id="s2")
    assert exc.value.code == "attachment_missing"
    assert precopies() == []


def test_tombstone_outside_its_attachments_dir_is_never_trusted(root, tmp_path):  # noqa: F811
    ref, entry, source = stage(root)
    outside = tmp_path / "elsewhere" / "notes.txt"
    outside.parent.mkdir()
    outside.write_bytes(b"secret")
    source.unlink()
    (entry / "promoted.json").write_bytes(
        orjson.dumps(
            {
                "session_id": "s1",
                "final_path": str(outside),
                "final_name": "notes.txt",
                "kind": "text",
                "original_name": "notes.txt",
                "size": 6,
            }
        )
    )
    plan = plan_of((ref, "text", None))
    for session_id in ("s1", "s2"):
        with pytest.raises(SendDeliveryError) as exc:
            commit(plan, session_id)
        assert exc.value.code == "attachment_missing"
    assert precopies() == []
    assert not attachments_dir("s2").exists()


def test_invalid_session_id_is_refused(root):  # noqa: F811
    ref, _, source = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id=None)
    with pytest.raises(SendDeliveryError) as exc:
        finish_attachments(prepared, session_id="../escape", text="")
    assert exc.value.code == "attachment_commit_failed"
    assert precopies() == []
    assert source.exists()


def test_empty_plan_is_a_noop(root):  # noqa: F811
    prepared = prepare_attachments(AttachmentPlan(TARGET, ()), session_id="s1")
    assert prepared == PreparedAttachments(AttachmentPlan(TARGET, ()), ())
    content = finish_attachments(prepared, session_id="s1", text="hi")
    assert content.native_parts == ()
    assert content.manifest.entries == ()
    assert content.user_text == "hi"


def test_unknown_session_resolving_to_the_tombstone_session_reuses_the_file(root):  # noqa: F811
    ref, _, _ = stage(root)
    commit(plan_of((ref, "text", None)), "s1")
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id=None)
    assert prepared.entries[0].precopy is not None
    content = finish_attachments(prepared, session_id="s1", text="")
    assert content.manifest.entries[0].artifact_name == "notes.txt"
    assert sorted(p.name for p in attachments_dir("s1").iterdir()) == ["notes.txt"]
    assert precopies() == []


def test_failed_replace_after_reservation_leaves_no_placeholder(root, monkeypatch):  # noqa: F811
    ref, entry, source = stage(root)
    prepared = prepare_attachments(plan_of((ref, "text", None)), session_id="s1")

    def no_link(src, dst, *args, **kwargs):
        raise oserror(errno.ENOTSUP)

    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        if Path(dst).parent.name == "attachments":
            raise oserror(errno.EIO)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", no_link)
    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(SendDeliveryError) as exc:
        finish_attachments(prepared, session_id="s1", text="")
    assert exc.value.code == "attachment_commit_failed"
    assert list(attachments_dir("s1").iterdir()) == []
    assert precopies() == []
    assert source.read_bytes() == b"notes"
    assert not (entry / "promoted.json").exists()
