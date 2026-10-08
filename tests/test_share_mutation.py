import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.utils import timezone as djtz

from twicc import paths
from twicc.core.models import (
    ArtifactBookmark,
    PinMode,
    Project,
    Session,
    SessionType,
    Share,
)
from twicc.core.services import share_mutation
from twicc.providers.helpers import get_provider_helpers
from twicc.core.services.share_mutation import (
    _validate_artifact_options,
    _validate_session_options,
    snapshot_artifact_share,
)


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-shm", directory="/tmp/shm")


@pytest.fixture
def session(project):
    now = djtz.now()
    return Session.objects.create(
        id="sess-shm", project=project, provider="claude_code",
        file_path="sess-shm.jsonl", type=SessionType.SESSION, title="Session SHM",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=17,
        compute_version=get_provider_helpers("claude_code").current_compute_version,
    )


@pytest.fixture
def bookmark(session, project):
    return ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock",
        _passthrough,
    )


def _run(coro):
    return asyncio.run(coro)


def _write(artifacts_root: Path, session_id: str, name: str, payload: bytes) -> Path:
    target = artifacts_root / session_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


# ── Option validation ───────────────────────────────────────────────────────

def test_session_options_rejects_unknown_key():
    _out, errors = _validate_session_options({"bogus": 1})
    assert any(e.code == "unknown_keys" for e in errors)


def test_session_options_rejects_invalid_mode():
    _out, errors = _validate_session_options({"mode": "weird"})
    assert any(e.field == "mode" and e.code == "invalid" for e in errors)


def test_session_options_rejects_invalid_display_mode():
    _out, errors = _validate_session_options({"max_display_mode": "ultra"})
    assert any(e.field == "max_display_mode" for e in errors)


def test_session_options_display_title_trimmed():
    out, errors = _validate_session_options({"display_title": "  Hello  "})
    assert not errors
    assert out["display_title"] == "Hello"


def test_session_options_blank_display_title_dropped():
    out, _errors = _validate_session_options({"display_title": "   "})
    assert "display_title" not in out


def test_artifact_options_rejects_unknown_key():
    _out, errors = _validate_artifact_options({"mode": "live"})
    assert any(e.code == "unknown_keys" for e in errors)


def test_artifact_options_keeps_display_title_and_show_title():
    out, errors = _validate_artifact_options({"display_title": "Report"})
    assert not errors
    # show_title defaults to True (master switch, mirrors sessions).
    assert out == {"show_title": True, "display_title": "Report"}


def test_artifact_options_show_title_false_kept():
    out, errors = _validate_artifact_options({"show_title": False, "display_title": "Report"})
    assert not errors
    assert out == {"show_title": False, "display_title": "Report"}


# ── create_share ────────────────────────────────────────────────────────────

def test_create_session_share_snapshot_freezes_line(session):
    result = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "snapshot"}))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    assert share.options["mode"] == "snapshot"
    assert share.options["frozen_at_line"] == 17  # session.last_line


def test_create_session_share_live_has_no_frozen_line(session):
    result = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "live"}))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    assert "frozen_at_line" not in share.options


def test_create_session_share_rejects_unknown_option(session):
    result = _run(share_mutation.create_share(
        "session", session=session, options={"nope": True}))
    assert not result.success
    assert result.errors


# ── Snapshot size cap ───────────────────────────────────────────────────────

def test_snapshot_size_cap(session, bookmark, artifacts_root, monkeypatch):
    _write(artifacts_root, "sess-shm", "demo/index.html", b"0123456789")  # 10 bytes
    monkeypatch.setattr(share_mutation, "_MAX_SNAPSHOT_BYTES", 4)
    share = Share(id="shr_capcap", kind="artifact", token="x" * 20,
                  artifact_bookmark=bookmark)
    err = snapshot_artifact_share(share)
    assert err is not None
    assert "too large" in err


def test_snapshot_ok_under_cap(session, bookmark, artifacts_root):
    _write(artifacts_root, "sess-shm", "demo/index.html", b"<html></html>")
    share = Share(id="shr_okok", kind="artifact", token="y" * 20,
                  artifact_bookmark=bookmark)
    err = snapshot_artifact_share(share)
    assert err is None
    snap = paths.get_share_snapshot_dir("shr_okok")
    assert (snap / "index.html").is_file()


# ── patch_share / propagate / from_payload (regression for review findings) ──

def test_frozen_at_line_invalid_rejected():
    _out, errors = _validate_session_options({"mode": "snapshot", "frozen_at_line": "x"})
    assert any(e.field == "frozen_at_line" for e in errors)


def test_patch_preserves_frozen_line(session):
    created = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "snapshot"}))
    share = Share.objects.select_related("session").get(id=created.share_id)
    assert share.options["frozen_at_line"] == 17
    # Re-send options without frozen_at_line → the stored freeze is preserved.
    result = _run(share_mutation.patch_share(share, {"options": {"mode": "snapshot", "show_timestamps": False}}))
    assert result.success
    share.refresh_from_db()
    assert share.options["frozen_at_line"] == 17
    assert share.options["show_timestamps"] is False


def test_patch_live_to_snapshot_freezes(session):
    created = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "live"}))
    share = Share.objects.select_related("session").get(id=created.share_id)
    assert "frozen_at_line" not in share.options
    result = _run(share_mutation.patch_share(share, {"options": {"mode": "snapshot"}}))
    assert result.success
    share.refresh_from_db()
    assert share.options["mode"] == "snapshot"
    assert share.options["frozen_at_line"] == 17  # frozen at session.last_line


def test_update_from_payload_string_expires(session):
    created = _run(share_mutation.create_share("session", session=session, options={"mode": "live"}))
    # The drop-request/CLI path sends expires_at as an ISO string (Finding 1 regression):
    # patch_share must coerce it so the broadcast's serialize_share doesn't crash.
    result = _run(share_mutation.update_share_from_payload({
        "share_id": created.share_id,
        "fields": {"label": "renamed", "expires_at": "2999-01-01T00:00:00+00:00"},
    }))
    assert result.success
    share = Share.objects.get(id=created.share_id)
    assert share.label == "renamed"
    assert share.expires_at is not None
    assert share.expires_at.year == 2999


def test_propagate_session_refreezes(session):
    created = _run(share_mutation.create_share(
        "session", session=session, options={"mode": "snapshot"}))
    session.last_line = 99
    session.save(update_fields=["last_line"])
    share = Share.objects.select_related("session").get(id=created.share_id)
    result = _run(share_mutation.propagate_share(share))
    assert result.success
    share.refresh_from_db()
    assert share.options["frozen_at_line"] == 99


def test_propagate_live_share_rejected(session):
    created = _run(share_mutation.create_share("session", session=session, options={"mode": "live"}))
    share = Share.objects.select_related("session").get(id=created.share_id)
    result = _run(share_mutation.propagate_share(share))
    assert not result.success
    assert result.errors[0].code == "not_snapshot"


def test_create_artifact_share_and_propagate_resnapshot(session, bookmark, artifacts_root):
    _write(artifacts_root, "sess-shm", "demo/index.html", b"<html>v1</html>")
    created = _run(share_mutation.create_share("artifact", bookmark=bookmark))
    assert created.success
    share = Share.objects.select_related("artifact_bookmark").get(id=created.share_id)
    snap = paths.get_share_snapshot_dir(share.id) / "index.html"
    assert snap.read_bytes() == b"<html>v1</html>"
    assert share.options.get("snapshot_at")
    # Mutate the source, propagate → re-snapshot over the existing dir (.old swap branch).
    _write(artifacts_root, "sess-shm", "demo/index.html", b"<html>v2</html>")
    result = _run(share_mutation.propagate_share(share))
    assert result.success
    share.refresh_from_db()
    assert snap.read_bytes() == b"<html>v2</html>"
    assert share.options.get("snapshot_at")


# ── Shared repairs (agent-sharing design §7.2 / §8) ─────────────────────────

def test_artifact_create_preserves_title_options(session, bookmark, artifacts_root):
    _write(artifacts_root, session.id, "demo/index.html", b"<html/>")
    result = _run(share_mutation.create_share(
        "artifact", bookmark=bookmark,
        options={"show_title": False, "display_title": "Custom"},
    ))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    assert share.options["show_title"] is False
    assert share.options["display_title"] == "Custom"
    assert "snapshot_at" in share.options
    # Served by the public serializer: show_title off ⇒ no title at all.
    from twicc.core.serializers import serialize_share_public_meta
    assert "title" not in serialize_share_public_meta(share)


def test_artifact_create_serves_custom_title(session, bookmark, artifacts_root):
    _write(artifacts_root, session.id, "demo/index.html", b"<html/>")
    result = _run(share_mutation.create_share(
        "artifact", bookmark=bookmark,
        options={"show_title": True, "display_title": "Custom"},
    ))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    from twicc.core.serializers import serialize_share_public_meta
    assert serialize_share_public_meta(share)["title"] == "Custom"


def test_artifact_propagate_preserves_title_options(session, bookmark, artifacts_root):
    _write(artifacts_root, session.id, "demo/index.html", b"<html/>")
    result = _run(share_mutation.create_share(
        "artifact", bookmark=bookmark,
        options={"show_title": True, "display_title": "Kept"},
    ))
    share = Share.objects.get(id=result.share_id)
    first_snapshot_at = share.options["snapshot_at"]
    result2 = _run(share_mutation.propagate_share(share))
    assert result2.success
    share.refresh_from_db()
    assert share.options["show_title"] is True
    assert share.options["display_title"] == "Kept"
    assert share.options["snapshot_at"] >= first_snapshot_at
    from twicc.core.serializers import serialize_share_public_meta
    assert serialize_share_public_meta(share)["title"] == "Kept"


def test_create_invalid_expiry_is_rejected_not_silent(session):
    """§7.2 expiry defect fix: a typo must NOT create a never-expiring link."""
    result = _run(share_mutation.create_share_from_payload({
        "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None,
        "expires_at": "not-a-date",
    }))
    assert not result.success
    assert result.errors[0].field == "expires_at"
    assert result.errors[0].code == "invalid"
    assert Share.objects.count() == 0


def test_update_invalid_expiry_preserves_existing(session):
    result = _run(share_mutation.create_share_from_payload({
        "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None,
        "expires_at": "2030-01-01T00:00:00+00:00",
    }))
    share = Share.objects.get(id=result.share_id)
    upd = _run(share_mutation.update_share_from_payload({
        "share_id": share.id, "fields": {"expires_at": "garbage"},
    }))
    assert not upd.success
    assert upd.errors[0].code == "invalid"
    share.refresh_from_db()
    assert share.expires_at == datetime(2030, 1, 1, tzinfo=UTC)


def test_valid_and_empty_expiry_unchanged(session):
    ok = _run(share_mutation.create_share_from_payload({
        "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None,
        "expires_at": "2030-06-01T12:00:00+00:00",
    }))
    assert ok.success
    none1 = _run(share_mutation.create_share_from_payload({
        "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None, "expires_at": "",
    }))
    assert none1.success
    assert Share.objects.get(id=none1.share_id).expires_at is None


def test_disabled_create_does_not_copy_unavailable_inline_sources(session, artifacts_root):
    session.inline_artifacts = {'schema': 1, 'publications': [{
        'artifact_id': 'missing', 'line_num': 10, 'text_block_index': 0, 'tag_offset': 0,
        'src': 'inline-artifacts/missing/index.html', 'title': 'Missing', 'height': 360,
    }]}
    session.compute_version = 0
    session.save(update_fields=['inline_artifacts', 'compute_version'])
    result = _run(share_mutation.create_share('session', session=session, options={'include_inline_artifacts': False}))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    assert share.inline_artifact_exports == {}
    assert not (paths.get_share_snapshot_dir(share.id) / 'inline-artifacts').exists()
