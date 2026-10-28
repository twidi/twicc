"""The session list endpoints accept an optional ``limit`` page size.

The sidebar loads a small first page of a scope it never fetched, then the rest
of a normal page after its cursor. Without ``limit``, a page keeps the default
size (``SESSIONS_PAGE_SIZE``).
"""

import asyncio

import orjson
import pytest
from django.test import RequestFactory
from django.utils import timezone

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.views import (
    SESSIONS_PAGE_SIZE,
    _get_sessions_page,
    _parse_sessions_limit,
    all_sessions,
    project_sessions,
)

PROJECT_ID = "-sessions-page-limit"


def _run(coro):
    return asyncio.run(coro)


def _make_sessions(count):
    project, _ = Project.objects.get_or_create(
        id=PROJECT_ID, defaults={"directory": "/tmp/sessions-page-limit"}
    )
    now = timezone.now()
    for index in range(count):
        Session.objects.create(
            id=f"page-limit-{index}",
            file_path=f"/tmp/sessions-page-limit/{index}.jsonl",
            project=project,
            provider=Provider.CODEX.value,
            type=SessionType.SESSION,
            created_at=now,
            user_message_count=1,
            # Most recent first: page-limit-0 has the highest mtime.
            mtime=now.timestamp() - index,
        )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({}, SESSIONS_PAGE_SIZE),
        ({"limit": "1"}, 1),
        ({"limit": "30"}, 30),
        ({"limit": str(SESSIONS_PAGE_SIZE)}, SESSIONS_PAGE_SIZE),
        ({"limit": "0"}, None),
        ({"limit": str(SESSIONS_PAGE_SIZE + 1)}, None),
        ({"limit": "-5"}, None),
        ({"limit": "abc"}, None),
        ({"limit": ""}, None),
    ],
)
def test_parse_sessions_limit(query, expected):
    request = RequestFactory().get("/api/sessions/", query)
    assert _parse_sessions_limit(request) == expected


@pytest.mark.django_db(transaction=True)
def test_a_limited_page_returns_the_most_recent_sessions_and_has_more():
    _make_sessions(3)
    page = _run(_get_sessions_page(PROJECT_ID, None, limit=2))
    assert [s["id"] for s in page["sessions"]] == ["page-limit-0", "page-limit-1"]
    assert page["has_more"] is True


@pytest.mark.django_db(transaction=True)
def test_a_limit_covering_every_session_has_no_more():
    _make_sessions(3)
    page = _run(_get_sessions_page(PROJECT_ID, None, limit=3))
    assert len(page["sessions"]) == 3
    assert page["has_more"] is False


@pytest.mark.django_db(transaction=True)
def test_both_endpoints_apply_the_limit():
    _make_sessions(3)
    factory = RequestFactory()
    responses = [
        _run(all_sessions(factory.get("/api/sessions/", {"limit": "1"}))),
        _run(project_sessions(factory.get(f"/api/projects/{PROJECT_ID}/sessions/", {"limit": "1"}), PROJECT_ID)),
    ]
    for response in responses:
        assert response.status_code == 200
        body = orjson.loads(response.content)
        assert [s["id"] for s in body["sessions"]] == ["page-limit-0"]
        assert body["has_more"] is True


@pytest.mark.django_db(transaction=True)
def test_both_endpoints_reject_an_invalid_limit():
    _make_sessions(1)
    factory = RequestFactory()
    responses = [
        _run(all_sessions(factory.get("/api/sessions/", {"limit": "0"}))),
        _run(project_sessions(factory.get(f"/api/projects/{PROJECT_ID}/sessions/", {"limit": "abc"}), PROJECT_ID)),
    ]
    for response in responses:
        assert response.status_code == 400
        assert "limit" in orjson.loads(response.content)["error"]
