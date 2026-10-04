"""Shared arrival lookup preserves row metadata and CLI acceptance."""
import orjson
import pytest
from django.utils import timezone

from twicc.cli.session import lookup_wait_session
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session
from twicc.mcp.events.prompts import first_non_command_prompt


@pytest.fixture
def session(db):
    project = Project.objects.create(id="events-lookup", directory="/tmp/events-lookup")
    return Session.objects.create(id="events-session", project=project, provider="claude_code",
        file_path="session.jsonl", mtime=1, created_at=timezone.now(), user_message_count=0,
        history_epoch=2, last_line=19, last_offset=0)


@pytest.mark.parametrize("live", [False, True])
def test_rejected_row_preserved(session, monkeypatch, live):
    monkeypatch.setattr("twicc.cli.session._live_session_ids", lambda ids: set(ids) if live else set())
    result = lookup_wait_session(session.id)
    assert result.session.last_line == 19
    assert result.session.history_epoch == 2
    assert result.session.last_offset == 0
    assert not result.indexed
    assert result.live == live
    assert result.accepted == live
    assert lookup_wait_session(session.id, accept_history_epoch=True).indexed


def test_epoch_requires_creation(session, monkeypatch):
    session.created_at = None
    session.save()
    monkeypatch.setattr("twicc.cli.session._live_session_ids", lambda ids: set())
    assert not lookup_wait_session(session.id, accept_history_epoch=True).accepted


def test_missing_live_row(db, monkeypatch):
    monkeypatch.setattr("twicc.cli.session._live_session_ids", lambda ids: set(ids))
    result = lookup_wait_session("missing")
    assert result.session is None
    assert result.live and result.accepted


def test_first_prompt_order_and_cursor(session, django_assert_num_queries):
    for line, text, kind in [(1, "older", ItemKind.USER_MESSAGE),
        (2, "<command-name>/rename</command-name>", ItemKind.USER_MESSAGE),
        (3, "assistant", ItemKind.ASSISTANT_MESSAGE), (4, "prompt", ItemKind.USER_MESSAGE),
        (5, "later", ItemKind.USER_MESSAGE)]:
        session.items.create(line_num=line, kind=kind,
            content=orjson.dumps({"message": {"content": text}}).decode())
    with django_assert_num_queries(1):
        assert first_non_command_prompt(session.id, session.provider, 1).line_num == 4
    assert first_non_command_prompt(session.id, session.provider, 5) is None


def test_cli_rejected_live_row_starts_at_zero(session, monkeypatch):
    import typer
    from twicc.cli import session as session_cli
    from twicc.cli import _wait_reply

    monkeypatch.setattr(session_cli, "_live_session_ids", lambda ids: set(ids))
    seen = {}

    def wait(session_id, **kwargs):
        seen.update(kwargs)
        return {"outcome": "replied"}

    monkeypatch.setattr(_wait_reply, "wait_for_reply_or_degrade", wait)
    monkeypatch.setattr(session_cli, "emit_json", lambda value: None)
    with pytest.raises(typer.Exit) as error:
        session_cli.wait_reply(session.id, timeout=1, since="2026-01-01T00:00:00Z")
    assert error.value.exit_code == 0
    assert seen["since_line_num"] == 0
