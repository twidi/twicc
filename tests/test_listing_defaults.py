"""The listing defaults: the pagination envelope, a page of 20, no notices."""

from __future__ import annotations

from datetime import timedelta

import orjson
import pytest
from django.utils import timezone

from twicc.cli import _output
from twicc.cli import session as cli_session
from twicc.cli import sessions as cli_sessions
from twicc.cli import share as cli_share
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-twicc-listing", directory="/tmp/twicc-listing",
    )


def make_sessions(project, count):
    now = timezone.now()
    return [
        Session.objects.create(
            id=f"ls-sess-{i}", project=project, provider="claude_code",
            file_path=f"ls-sess-{i}.jsonl", type=SessionType.SESSION,
            created_at=now + timedelta(minutes=i), mtime=1000 + i,
            last_line=1, user_message_count=1,
        )
        for i in range(count)
    ]


def read(capsysbinary):
    out, err = capsysbinary.readouterr()
    return orjson.loads(out), err.decode()


def test_the_envelope_is_the_default(project, capsysbinary):
    make_sessions(project, 60)
    cli_sessions.main(project=project.id)
    payload, err = read(capsysbinary)
    assert set(payload) == {"items", "pagination"}
    assert payload["pagination"]["limit"] == 20
    assert len(payload["items"]) == 20
    assert err == "", "nothing is announced"


def test_an_explicit_limit_wins(project, capsysbinary):
    make_sessions(project, 60)
    cli_sessions.main(project=project.id, limit=3)
    payload, _ = read(capsysbinary)
    assert payload["pagination"]["limit"] == 3


def test_a_bare_content_call_returns_a_page(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    for i in range(60):
        SessionItem.objects.create(
            session=session, line_num=i + 1, kind=ItemKind.USER_MESSAGE,
            content=orjson.dumps({"type": "user", "n": i}).decode(),
        )
    cli_session.content(session.id)
    payload, _ = read(capsysbinary)
    assert len(payload["items"]) == 20
    assert payload["pagination"]["total"] == 60


def test_a_bare_messages_call_pages_at_twenty(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    for i in range(60):
        SessionItem.objects.create(
            session=session, line_num=i + 1, kind=ItemKind.USER_MESSAGE,
            content=orjson.dumps({"type": "user", "message": {"content": f"m{i}"}}).decode(),
        )
    cli_session.messages(session.id)
    payload, _ = read(capsysbinary)
    assert payload["pagination"]["limit"] == 20


def test_share_pages_at_twenty(project, capsysbinary):
    from twicc.core.models import Share

    session = make_sessions(project, 1)[0]
    now = timezone.now()
    for i in range(25):
        Share.objects.create(
            kind="session", session=session, token=f"pg20-token-{i}",
            created_at=now + timedelta(minutes=i),
        )
    cli_share.list_main()
    payload, _ = read(capsysbinary)
    assert len(payload["items"]) == 20


def test_every_listing_default_is_twenty():
    assert _output.PAGINATED_DEFAULT_LIMIT == 20
    assert _output.limit_help("sessions") == "Max number of sessions to return (default: 20)."


def test_the_paginated_flag_is_an_accepted_no_op(project):
    from twicc.rpc.invoker import invoke

    make_sessions(project, 5)
    without = invoke(["sessions", "--project", project.id, "--limit", "2"])
    with_flag = invoke(["sessions", "--project", project.id, "--limit", "2", "--paginated"])
    assert without.exit_code == with_flag.exit_code == 0
    assert without.result == with_flag.result
    assert set(without.result) == {"items", "pagination"}


def test_the_rpc_envelope_carries_no_warnings(project):
    from twicc.rpc.invoker import invoke

    make_sessions(project, 1)
    result = invoke(["sessions", "--project", project.id])
    assert not hasattr(result, "warnings")


def test_no_mcp_description_announces_a_deprecation():
    from twicc.mcp.tools import iter_mcp_tools

    assert [t.name for t in iter_mcp_tools() if "DEPRECATION" in t.description] == []


def test_the_retired_process_commands_are_gone():
    from twicc.mcp.tools import iter_mcp_tools
    from twicc.rpc.generator import build_registry
    from twicc.rpc.invoker import invoke

    assert not [p for p in build_registry() if p.split("/")[0] in {"process", "processes"}]
    assert not [t.name for t in iter_mcp_tools() if t.name.split("_")[0] in {"process", "processes"}]
    for argv in (["process", "abc"], ["processes"], ["processes", "get", "abc"]):
        result = invoke(argv)
        assert result.exit_code != 0
        assert "No such command" in (result.error or "")
