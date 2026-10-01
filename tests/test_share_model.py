import asyncio
from datetime import datetime, timedelta, UTC

import orjson
import pytest
from django.db import IntegrityError
from django.utils import timezone as djtz

from twicc.core.models import (
    ArtifactBookmark,
    PinMode,
    Project,
    Session,
    SessionType,
    Share,
)
from twicc.core.serializers import serialize_share, serialize_share_public_meta
from twicc.core.services.share_tokens import mint_token, resolve_share


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-share", directory="/tmp/share")


@pytest.fixture
def session(project):
    now = djtz.now()
    return Session.objects.create(
        id="sess-share", project=project, provider="claude_code",
        file_path="sess-share.jsonl", type=SessionType.SESSION, title="My session",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=42,
    )


@pytest.fixture
def bookmark(session, project):
    return ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


def _run(coro):
    return asyncio.run(coro)


def _creator(project, *, sid="agent-1", title="Agent one"):
    now = djtz.now()
    return Session.objects.create(
        id=sid, project=project, provider="claude_code",
        file_path=f"{sid}.jsonl", type=SessionType.SESSION, title=title,
        created_at=now, last_new_content_at=now, user_message_count=1,
        last_line=7,
    )


def _serialized(share):
    loaded = Share.objects.select_related(
        "session", "artifact_bookmark", "created_by_session",
    ).get(id=share.id)
    return serialize_share(loaded)


def test_created_by_session_column_and_serializer_shapes(project, session):
    legacy = Share.objects.create(
        kind="session", token=mint_token(), session=session,
    )
    assert _serialized(legacy)["created_by"] == {
        "kind": "human_or_legacy", "session": None,
    }

    creator = _creator(project)
    attributed = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        created_by_session=creator,
    )
    assert _serialized(attributed)["created_by"] == {
        "kind": "agent",
        "session": {
            "id": "agent-1", "title": "Agent one", "project_id": project.id,
        },
    }

    creator.hidden = True
    creator.save(update_fields=["hidden"])
    hidden = _serialized(attributed)["created_by"]
    assert hidden == {"kind": "agent", "session": None}
    assert "agent-1" not in orjson.dumps(hidden).decode()

    creator.hidden = False
    creator.title = ""
    creator.save(update_fields=["hidden", "title"])
    untitled = _serialized(attributed)["created_by"]
    assert untitled == {
        "kind": "agent",
        "session": {"id": "agent-1", "title": "", "project_id": project.id},
    }


def test_self_target_by_hidden_creator_keeps_target_fields(project):
    creator = _creator(project, sid="hidden-self", title="Published target")
    creator.hidden = True
    creator.save(update_fields=["hidden"])
    share = Share.objects.create(
        kind="session", token=mint_token(), session=creator,
        created_by_session=creator,
    )
    data = _serialized(share)
    assert data["created_by"] == {"kind": "agent", "session": None}
    assert data["session_id"] == creator.id
    assert data["target_title"] == "Published target"


def test_created_by_absent_from_public_meta(project, session):
    creator = _creator(project)
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        created_by_session=creator,
    )
    assert "created_by" not in serialize_share_public_meta(share)


def test_serialize_share_in_async_context_no_lazy_load(project, session):
    creator = _creator(project)
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        created_by_session=creator,
    )
    loaded = Share.objects.select_related(
        "session", "artifact_bookmark", "created_by_session",
    ).get(id=share.id)

    async def go():
        return serialize_share(loaded)

    assert _run(go())["created_by"]["session"]["id"] == creator.id


def test_hide_emits_no_share_event_and_next_snapshot_hides_creator(
        project, session, monkeypatch):
    from twicc.core.services import session_visibility

    creator = _creator(project)
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        created_by_session=creator,
    )
    assert _serialized(share)["created_by"]["session"]["id"] == creator.id

    sent = []

    class RecordingLayer:
        async def group_send(self, group, payload):
            assert group == "updates"
            sent.append(payload["data"]["type"])

    monkeypatch.setattr(session_visibility, "_check_hidden_invariants", lambda row: [])
    # Keep the real _apply_flip path. The recording layer then observes any
    # broadcast added anywhere in the full hide path. Stub only unrelated
    # expensive work that happens after the hidden flag is saved.
    monkeypatch.setattr(
        "twicc.projects.update_project_metadata", lambda project_id: None,
    )
    monkeypatch.setattr(
        "twicc.search.reindex_session", lambda session_id: None,
    )
    monkeypatch.setattr(session_visibility, "get_channel_layer", lambda: RecordingLayer())

    async def hide_with_writer():
        from twicc.providers import db_writer
        db_writer.start_db_writer()
        try:
            return await session_visibility.hide_session(creator)
        finally:
            await db_writer.stop_db_writer()
    result = asyncio.run(hide_with_writer())
    assert result.success
    assert sent == ["session_removed", "project_updated"]
    assert _serialized(share)["created_by"] == {"kind": "agent", "session": None}


def test_check_constraint_rejects_session_kind_without_session(project, bookmark):
    with pytest.raises(IntegrityError):
        Share.objects.create(kind="session", token=mint_token(), session=None,
                             artifact_bookmark=bookmark)


def test_check_constraint_rejects_artifact_kind_with_session(session, bookmark):
    with pytest.raises(IntegrityError):
        Share.objects.create(kind="artifact", token=mint_token(), session=session,
                             artifact_bookmark=bookmark)


def test_valid_session_share_saves(session):
    share = Share.objects.create(kind="session", token=mint_token(), session=session)
    assert share.id.startswith("shr_")
    assert share.status() == "active"


def test_resolve_share_unknown_returns_none(transactional_db):
    assert resolve_share("does-not-exist") is None
    assert resolve_share("") is None


def test_resolve_share_found(session):
    share = Share.objects.create(kind="session", token=mint_token(), session=session)
    resolved = resolve_share(share.token)
    assert resolved is not None
    assert resolved.id == share.id


def test_status_transitions(session):
    now = datetime.now(tz=UTC)
    active = Share.objects.create(kind="session", token=mint_token(), session=session)
    assert active.status() == "active"
    assert active.is_active()

    expired = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        expires_at=now - timedelta(hours=1),
    )
    assert expired.status() == "expired"
    assert not expired.is_active()

    # Revoked wins over expired.
    revoked = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        expires_at=now - timedelta(hours=1), revoked_at=now,
    )
    assert revoked.status() == "revoked"


def test_serialize_share_session(session):
    share = Share.objects.create(kind="session", token=mint_token(), session=session,
                                 label="private label")
    data = serialize_share(share)
    assert data["kind"] == "session"
    assert data["token"] == share.token
    assert data["url_path"] == f"/share/{share.token}/"
    assert data["session_id"] == "sess-share"
    assert data["target_title"] == "My session"
    assert data["label"] == "private label"
    assert data["has_password"] is False


def test_public_meta_hides_title_when_show_title_false(session):
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        options={"mode": "live", "show_title": False},
    )
    meta = serialize_share_public_meta(share)
    # No real title exposed, and never the label / counters.
    assert "title" not in meta
    assert "label" not in meta
    assert meta["session_id"] == "sess-share"


def test_public_meta_show_title_is_master(session):
    # show_title off ⇒ no title, even if a display_title override is stored.
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        options={"mode": "live", "show_title": False, "display_title": "Public name"},
    )
    assert "title" not in serialize_share_public_meta(share)


def test_public_meta_display_title_used_when_shown(session):
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        options={"mode": "live", "show_title": True, "display_title": "Public name"},
    )
    assert serialize_share_public_meta(share)["title"] == "Public name"


def test_artifact_public_meta_uses_bookmark_name_by_default(bookmark):
    share = Share.objects.create(
        kind="artifact", token=mint_token(),
        artifact_bookmark=bookmark, options={"show_title": True},
    )
    assert serialize_share_public_meta(share)["title"] == "Demo"


def test_artifact_public_meta_hides_title_when_show_title_false(bookmark):
    # show_title off ⇒ no title, even with a display_title override (master switch).
    share = Share.objects.create(
        kind="artifact", token=mint_token(), artifact_bookmark=bookmark,
        options={"show_title": False, "display_title": "Public name"},
    )
    assert "title" not in serialize_share_public_meta(share)


def test_artifact_public_meta_display_title_used_when_shown(bookmark):
    share = Share.objects.create(
        kind="artifact", token=mint_token(), artifact_bookmark=bookmark,
        options={"show_title": True, "display_title": "Public name"},
    )
    assert serialize_share_public_meta(share)["title"] == "Public name"
