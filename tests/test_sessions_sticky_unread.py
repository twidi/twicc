"""The sticky ``unread`` preload must skip muted sessions.

``_get_sessions_page(unread_only=True)`` is the SQL twin of the frontend's
``hasUnreadContent``. A muted session carries no unread state for the user, so
it must not be force-fetched into the sidebar — while its *other* sticky
reasons (pinned, live process) still apply.
"""

import asyncio

import pytest
from django.utils import timezone

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.views import _get_sessions_page


def _run(coro):
    return asyncio.run(coro)


def _make_session(session_id, *, muted=False, pinned=None):
    project, _ = Project.objects.get_or_create(
        id="-sticky-unread", defaults={"directory": "/tmp/sticky-unread"}
    )
    now = timezone.now()
    return Session.objects.create(
        id=session_id,
        # `file_path` is non-null and unique with no default; give each row its
        # own value so a test may create several sessions.
        file_path=f"/tmp/sticky-unread/{session_id}.jsonl",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        created_at=now,
        user_message_count=1,
        mtime=now.timestamp(),
        last_new_content_at=now,
        last_viewed_at=None,
        mute_on_user_turn=muted,
        pinned=pinned,
    )


@pytest.mark.django_db(transaction=True)
def test_unread_preload_returns_an_unmuted_session():
    _make_session("sticky-unmuted")
    page = _run(_get_sessions_page(None, None, unread_only=True))
    assert [s["id"] for s in page["sessions"]] == ["sticky-unmuted"]


@pytest.mark.django_db(transaction=True)
def test_unread_preload_skips_a_muted_session():
    _make_session("sticky-muted", muted=True)
    page = _run(_get_sessions_page(None, None, unread_only=True))
    assert page["sessions"] == []


@pytest.mark.django_db(transaction=True)
def test_a_muted_session_still_preloads_when_pinned():
    _make_session("sticky-muted-pinned", muted=True, pinned="all")
    page = _run(_get_sessions_page(None, None, pinned_only=True, unread_only=True))
    assert [s["id"] for s in page["sessions"]] == ["sticky-muted-pinned"]


@pytest.mark.django_db(transaction=True)
def test_a_muted_session_still_preloads_when_its_process_is_active():
    _make_session("sticky-muted-active", muted=True)
    page = _run(_get_sessions_page(
        None, None, unread_only=True, active_session_ids=["sticky-muted-active"],
    ))
    assert [s["id"] for s in page["sessions"]] == ["sticky-muted-active"]
