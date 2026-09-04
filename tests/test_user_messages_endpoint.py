"""The composer's history-picker feed (``/api/…/sessions/<id>/user-messages/``).

It lists what the human typed, so messages another session sent to this one --
recognisable by their sender header -- are dropped. Exercised end-to-end through
Django's ``AsyncClient`` on both providers, since each one parses its own
message shape.
"""

from __future__ import annotations

import asyncio

import orjson
import pytest
from django.test import AsyncClient
from django.utils import timezone

from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType

pytestmark = pytest.mark.django_db(transaction=True)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "proj"
    directory.mkdir()
    return Project.objects.create(id="-tmp-user-messages", directory=str(directory))


def _session(project, provider):
    now = timezone.now()
    return Session.objects.create(
        id=f"sess-user-messages-{provider}", project=project, provider=provider,
        file_path=f"sess-user-messages-{provider}.jsonl", type=SessionType.SESSION,
        title="orchestrator", created_at=now, last_new_content_at=now,
    )


def _claude_line(text):
    return orjson.dumps({"type": "user", "message": {"role": "user", "content": text}}).decode()


def _codex_line(text):
    return orjson.dumps(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "completed_at_ms": 0,
                "item": {
                    "type": "UserMessage",
                    "id": "message-1",
                    "content": [{"type": "text", "text": text, "text_elements": []}],
                },
            },
        }
    ).decode()


def _add_items(session, build_line, texts):
    SessionItem.objects.bulk_create([
        SessionItem(
            session=session, line_num=line_num, content=build_line(text),
            kind=ItemKind.USER_MESSAGE, timestamp=timezone.now(),
        )
        for line_num, text in enumerate(texts, start=1)
    ])


def _fetch(client, session):
    resp = _run(client.get(
        f"/api/projects/{session.project_id}/sessions/{session.id}/user-messages/"
    ))
    assert resp.status_code == 200
    return orjson.loads(resp.content)["messages"]


@pytest.mark.parametrize("provider,build_line", [
    ("claude_code", _claude_line),
    ("codex", _codex_line),
])
def test_inter_session_messages_are_left_out(client, project, provider, build_line):
    session = _session(project, provider)
    _add_items(session, build_line, [
        "run the tests",
        ':: message from your spawned session `child-id` ("**Review 1**")\n\nFamily processed.',
        ":: message from your parent session `parent-id`\n\nreprends",
        "and now commit",
    ])

    texts = [msg["text"] for msg in _fetch(client, session)]

    assert texts == ["run the tests", "and now commit"]


def test_line_num_and_order_are_preserved(client, project):
    session = _session(project, "claude_code")
    _add_items(session, _claude_line, [
        "first",
        ":: message from another session `x`\n\nnoise",
        "second",
    ])

    messages = _fetch(client, session)

    assert [(m["line_num"], m["text"]) for m in messages] == [(1, "first"), (3, "second")]


def test_a_session_with_only_inter_session_messages_returns_nothing(client, project):
    session = _session(project, "claude_code")
    _add_items(session, _claude_line, [
        ":: message from a sibling session `a`\n\none",
        ":: message from a sibling session `b`\n\ntwo",
    ])

    assert _fetch(client, session) == []


def test_a_human_message_quoting_the_marker_is_kept(client, project):
    # The marker only counts when it opens the message.
    session = _session(project, "claude_code")
    _add_items(session, _claude_line, [
        "the header reads\n:: message from another session `a`",
    ])

    assert len(_fetch(client, session)) == 1
