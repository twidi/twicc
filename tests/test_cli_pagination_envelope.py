"""Tests for the ``--paginated`` envelope shared by every listing command.

Two contracts are locked here. Without the flag, a listing emits exactly what
it always emitted — that is what keeps existing scripts working. With it, the
payload is ``{"items": [...], "pagination": {...}}`` carrying the same four
keys on every command, so a caller never has to probe for the next page.
"""

from __future__ import annotations

from datetime import timedelta

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.cli import projects as cli_projects
from twicc.cli import session as cli_session
from twicc.cli import sessions as cli_sessions
from twicc.cli import share as cli_share
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType, Share


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-twicc-pagination", directory="/tmp/twicc-pagination",
    )


def make_sessions(project, count):
    now = timezone.now()
    return [
        Session.objects.create(
            id=f"pg-sess-{i}",
            project=project,
            provider="claude_code",
            file_path=f"pg-sess-{i}.jsonl",
            type=SessionType.SESSION,
            created_at=now + timedelta(minutes=i),
            mtime=1000 + i,
            last_line=1,
            user_message_count=1,
        )
        for i in range(count)
    ]


def read(capsysbinary):
    return orjson.loads(capsysbinary.readouterr().out)


# --- The default shape must not move ----------------------------------------


def test_without_the_flag_the_payload_is_still_a_bare_array(project, capsysbinary):
    make_sessions(project, 3)
    cli_sessions.main(project=project.id, limit=2)
    payload = read(capsysbinary)
    assert isinstance(payload, list)
    assert len(payload) == 2


# --- The envelope ------------------------------------------------------------


def test_envelope_reports_the_window_and_the_total(project, capsysbinary):
    make_sessions(project, 5)
    cli_sessions.main(project=project.id, limit=2, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 2
    assert payload["pagination"] == {
        "limit": 2, "offset": 0, "total": 5, "has_more": True,
    }


def test_has_more_is_false_on_the_last_page(project, capsysbinary):
    make_sessions(project, 5)
    cli_sessions.main(project=project.id, limit=2, offset=4, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 1
    assert payload["pagination"]["has_more"] is False


def test_an_offset_past_the_end_yields_an_empty_page_not_an_error(project, capsysbinary):
    make_sessions(project, 3)
    cli_sessions.main(project=project.id, limit=2, offset=99, paginated=True)
    payload = read(capsysbinary)
    assert payload["items"] == []
    assert payload["pagination"] == {
        "limit": 2, "offset": 99, "total": 3, "has_more": False,
    }


def test_every_listing_carries_the_same_four_pagination_keys(project, capsysbinary):
    make_sessions(project, 2)
    expected = {"limit", "offset", "total", "has_more"}
    for call in (
        lambda: cli_sessions.main(project=project.id, limit=1, paginated=True),
        lambda: cli_projects.main(limit=1, paginated=True),
    ):
        call()
        assert set(read(capsysbinary)["pagination"]) == expected


# --- session messages: the two branches window different things --------------


def make_messages(session, count, *, empty_from=None):
    """``count`` user messages; those from ``empty_from`` on extract to nothing."""
    for i in range(count):
        blank = empty_from is not None and i >= empty_from
        content = {
            "type": "user",
            "message": {"role": "user", "content": [] if blank else [
                {"type": "text", "text": f"message {i} django"},
            ]},
        }
        SessionItem.objects.create(
            session=session,
            line_num=i + 1,
            kind=ItemKind.USER_MESSAGE,
            content=orjson.dumps(content).decode(),
        )


def test_messages_without_a_limit_still_returns_everything_unpaginated(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_messages(session, 4)
    cli_session.messages(session.id)
    assert len(read(capsysbinary)) == 4


def test_messages_tail_reports_its_window_and_points_backwards(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_messages(session, 10)
    cli_session.messages(session.id, tail=3, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 3
    # The window covers the last three; ``has_more`` means "seven remain before".
    assert payload["pagination"] == {
        "limit": 3, "offset": 7, "total": 10, "has_more": True,
    }


def test_messages_tail_covering_everything_has_nothing_before_it(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_messages(session, 2)
    cli_session.messages(session.id, tail=5, paginated=True)
    assert read(capsysbinary)["pagination"]["has_more"] is False


def test_messages_contains_counts_extracted_messages_exactly(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_messages(session, 5, empty_from=3)
    cli_session.messages(session.id, contains=["django"], limit=2, paginated=True)
    payload = read(capsysbinary)
    # The window sits on the extracted messages: the two dropped ones are not
    # counted, and a full page is really full.
    assert len(payload["items"]) == 2
    assert payload["pagination"]["total"] == 3
    assert payload["pagination"]["has_more"] is True


def test_messages_without_contains_counts_raw_items(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_messages(session, 5, empty_from=3)
    cli_session.messages(session.id, limit=4, paginated=True)
    payload = read(capsysbinary)
    # The window sits on raw items, so the page shrinks on extraction and the
    # total counts the two items that yield no text. Documented, and only ever
    # over-reporting: no message can hide behind a ``has_more: false``.
    assert len(payload["items"]) == 3
    assert payload["pagination"]["total"] == 5
    assert payload["pagination"]["has_more"] is True


# --- share list: the revoked filter belongs to the query ---------------------


def make_shares(session, count, *, revoked_from=None):
    now = timezone.now()
    for i in range(count):
        Share.objects.create(
            kind="session",
            session=session,
            token=f"pg-token-{i}",
            created_at=now + timedelta(minutes=i),
            revoked_at=(
                now if revoked_from is not None and i >= revoked_from else None
            ),
        )


def test_share_pages_are_full_even_when_rows_are_revoked(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_shares(session, 6, revoked_from=2)
    cli_share.list_main(limit=2, paginated=True)
    payload = read(capsysbinary)
    # Four rows are revoked; only the two live ones exist, and the page says so
    # rather than silently shrinking after the window was applied.
    assert len(payload["items"]) == 2
    assert payload["pagination"]["total"] == 2
    assert payload["pagination"]["has_more"] is False


def test_share_include_revoked_widens_the_total(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_shares(session, 6, revoked_from=2)
    cli_share.list_main(limit=2, include_revoked=True, paginated=True)
    payload = read(capsysbinary)
    assert payload["pagination"]["total"] == 6
    assert payload["pagination"]["has_more"] is True


# --- session content: a window on top of the range ---------------------------


def make_items(session, count, *, needle="django"):
    """``count`` raw items; the even ones carry ``needle``."""
    for i in range(count):
        payload = {"type": "user", "note": f"item {i}" + (f" {needle}" if i % 2 == 0 else "")}
        SessionItem.objects.create(
            session=session,
            line_num=i + 1,
            kind=ItemKind.USER_MESSAGE,
            content=orjson.dumps(payload).decode(),
        )


def test_content_still_refuses_a_bare_call(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 4)
    with pytest.raises(typer.Exit):
        cli_session.content(session.id)


def test_content_accepts_a_window_as_the_only_selector(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 10)
    cli_session.content(session.id, limit=3)
    assert len(read(capsysbinary)) == 3


def test_content_range_and_window_stack(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 20)
    cli_session.content(session.id, range_str="5-14", limit=3, offset=2)
    rows = read(capsysbinary)
    # The range picks lines 5..14, then the window takes the 3rd, 4th and 5th.
    assert [r["line_num"] for r in rows] == [7, 8, 9]


def test_content_window_ranks_matches_not_lines(project, capsysbinary):
    """The range is an address in the JSONL; the window is a rank in the matches."""
    session = make_sessions(project, 1)[0]
    make_items(session, 20)
    cli_session.content(session.id, contains=["django"], limit=3)
    rows = read(capsysbinary)
    # Every other item matches, so three matches span six lines.
    assert [r["line_num"] for r in rows] == [1, 3, 5]


def test_content_no_match_is_an_empty_list_not_an_error(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 4)
    cli_session.content(session.id, contains=["nothing-matches-this"])
    assert read(capsysbinary) == []


def test_content_envelope_counts_the_matches(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 20)
    cli_session.content(session.id, contains=["django"], limit=4, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 4
    assert payload["pagination"] == {
        "limit": 4, "offset": 0, "total": 10, "has_more": True,
    }


def test_content_envelope_past_the_end_is_an_empty_page(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 6)
    cli_session.content(session.id, limit=2, offset=99, paginated=True)
    payload = read(capsysbinary)
    assert payload["items"] == []
    assert payload["pagination"]["has_more"] is False


# --- the paginated mode supplies its own page size ---------------------------


def test_the_flag_forces_a_page_size_where_there_was_none(project, capsysbinary):
    """``messages`` and ``content`` return everything by default; a page that is
    not bounded would make ``has_more`` a vacuous ``false``."""
    session = make_sessions(project, 1)[0]
    make_messages(session, 60)
    cli_session.messages(session.id, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 50
    assert payload["pagination"] == {
        "limit": 50, "offset": 0, "total": 60, "has_more": True,
    }


def test_the_flag_overrides_a_command_s_own_default(project, capsysbinary):
    """``sessions`` defaults to 20 without the flag and 50 with it."""
    make_sessions(project, 60)
    cli_sessions.main(project=project.id)
    assert len(read(capsysbinary)) == 20

    cli_sessions.main(project=project.id, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 50
    assert payload["pagination"]["limit"] == 50


def test_an_explicit_limit_always_wins(project, capsysbinary):
    make_sessions(project, 60)
    cli_sessions.main(project=project.id, limit=3, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 3
    assert payload["pagination"]["limit"] == 3


def test_tail_keeps_its_own_window(project, capsysbinary):
    """``--tail`` sizes the window itself; the paginated default must not replace it."""
    session = make_sessions(project, 1)[0]
    make_messages(session, 60)
    cli_session.messages(session.id, tail=3, paginated=True)
    assert read(capsysbinary)["pagination"]["limit"] == 3


def test_tail_and_limit_stay_mutually_exclusive_under_the_flag(project):
    session = make_sessions(project, 1)[0]
    make_messages(session, 10)
    with pytest.raises(typer.Exit):
        cli_session.messages(session.id, tail=3, limit=5, paginated=True)


def test_content_accepts_the_flag_as_its_only_selector(project, capsysbinary):
    """The bare-call guard exists to prevent a full dump; a bounded page cannot."""
    session = make_sessions(project, 1)[0]
    make_items(session, 60)
    cli_session.content(session.id, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 50
    assert payload["pagination"]["total"] == 60


# --- session content: --tail reaches the end without knowing where it is -----


def test_content_tail_returns_the_last_matches(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 20)
    cli_session.content(session.id, tail=3)
    assert [r["line_num"] for r in read(capsysbinary)] == [18, 19, 20]


def test_content_tail_works_on_a_filtered_result(project, capsysbinary):
    """The point of --tail: the end of a filtered result has no line address."""
    session = make_sessions(project, 1)[0]
    make_items(session, 20)
    cli_session.content(session.id, contains=["django"], tail=3, paginated=True)
    payload = read(capsysbinary)
    # Every other item matches, so the last three matches are lines 15, 17, 19.
    assert [r["line_num"] for r in payload["items"]] == [15, 17, 19]
    assert payload["pagination"] == {
        "limit": 3, "offset": 7, "total": 10, "has_more": True,
    }


def test_content_tail_covering_everything_has_nothing_before_it(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 4)
    cli_session.content(session.id, tail=50, paginated=True)
    payload = read(capsysbinary)
    assert len(payload["items"]) == 4
    assert payload["pagination"]["offset"] == 0
    assert payload["pagination"]["has_more"] is False


def test_content_tail_is_exclusive_with_the_window(project):
    session = make_sessions(project, 1)[0]
    make_items(session, 10)
    with pytest.raises(typer.Exit):
        cli_session.content(session.id, tail=3, limit=5)
    with pytest.raises(typer.Exit):
        cli_session.content(session.id, tail=3, offset=2)


def test_content_tail_must_be_positive(project):
    session = make_sessions(project, 1)[0]
    make_items(session, 10)
    with pytest.raises(typer.Exit):
        cli_session.content(session.id, tail=0)


def test_content_tail_is_a_selector_on_its_own(project, capsysbinary):
    session = make_sessions(project, 1)[0]
    make_items(session, 10)
    cli_session.content(session.id, tail=2)
    assert len(read(capsysbinary)) == 2
