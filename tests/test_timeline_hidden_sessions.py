"""``GET /api/timeline/`` leaves out the events of a session hidden at read time.

The rule speaks of ``Session.hidden`` NOW, not at the time of the event. Every read applies it: the first page,
the ``before`` pages and the ``after_id`` fetch. The ``after_id`` response also names the sessions hidden or shown
inside its range (``hidden_sessions`` / ``shown_sessions``), so an open list drops or merges them in place.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.test_timeline_api import call, ids, make_event, make_instance_event, make_session, plan_of
from twicc.core.models import Project, Session
from twicc.core.services import timeline_feed
from twicc.core.services.timeline_feed import parse_request

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def projects():
    return [Project.objects.create(id=f"-tl-hid-{name}", directory=f"/tmp/tl-hid-{name}") for name in "ab"]


def next_page(params, body):
    last = body["events"][-1]
    return call(**params, before=f"{last['occurred_at']},{last['id']}").json()


def every_id(params):
    """Every id of the paged feed, page by page (bounded: a broken cursor would loop forever)."""
    seen, body = [], call(**params).json()
    seen += ids(body)
    for _ in range(20):
        if not body["has_more"]:
            return seen
        body = next_page(params, body)
        seen += ids(body)
    pytest.fail(f"the paging never reached the end: {seen}")


# -- Pages ---------------------------------------------------------------------------------------------------


def test_the_rows_of_a_hidden_session_are_left_out_of_the_first_page(projects):
    a = projects[0]
    make_session(a, "shown")
    make_session(a, "secret", hidden=True)
    kept = make_event(project=a, session_id="shown", at=1)
    make_event(project=a, session_id="secret", at=2)
    make_event("hidden.started", project=a, session_id="secret", at=3, actor="user", via="ui")
    unknown = make_event(project=a, session_id="purged", at=4)  # no Session row: nothing says it is hidden
    boot = make_instance_event(at=5)
    body = call().json()
    assert ids(body) == [boot.id, unknown.id, kept.id]
    assert "secret" not in body["sessions"]


def test_the_state_at_read_time_wins_not_the_state_at_the_time_of_the_event(projects):
    a = projects[0]
    session = make_session(a, "s1")
    row = make_event(project=a, session_id="s1", at=1)
    assert ids(call().json()) == [row.id]
    Session.objects.filter(id=session.id).update(hidden=True)
    assert ids(call().json()) == []
    Session.objects.filter(id=session.id).update(hidden=False)
    assert ids(call().json()) == [row.id]


def test_instance_rows_survive_the_exclusion_when_hidden_sessions_exist(projects):
    # ``NULL NOT IN (non-empty set)`` is NULL in SQL: a naive exclusion would drop every instance row.
    a = projects[0]
    make_session(a, "secret", hidden=True)
    make_event(project=a, session_id="secret", at=1)
    boot = make_instance_event(at=2)
    end = make_instance_event(at=3, kind="backend.ended")
    for params in ({}, {"provider": "codex"}, {"project_ids": a.id}, {"families": "twicc"}, {"authors": "automatic"}):
        assert ids(call(**params).json()) == [end.id, boot.id], params
        assert ids(call(**params, after_id=0).json()) == [boot.id, end.id], params


def test_a_visible_row_that_cites_a_hidden_session_stays(projects):
    a = projects[0]
    make_session(a, "child")
    make_session(a, "boss", hidden=True)
    titled = make_event("title.changed", project=a, session_id="child", actor="agent", via="mcp_internal",
                        actor_ref="boss", at=1, data={"source": "agent", "new": "N", "old": None})
    created = make_event("session.created", project=a, session_id="child", actor="system", at=2,
                         data={"origin": "twicc", "spawned_by": "boss"})
    body = call().json()
    assert ids(body) == [created.id, titled.id]
    # The side table still describes the cited session: the row's mention of it is plain text when hidden.
    assert body["sessions"]["boss"]["hidden"] is True
    assert ids(call(after_id=0).json()) == [titled.id, created.id]


def test_a_session_filter_on_a_hidden_session_returns_nothing(projects):
    a = projects[0]
    make_session(a, "secret", hidden=True)
    for index in range(3):
        make_event(project=a, session_id="secret", at=index)
    make_instance_event(at=9)
    body = call(session="secret").json()
    assert body["events"] == [] and body["has_more"] is False
    assert call(session="secret", after_id=0).json()["events"] == []


def test_has_more_and_the_cursor_ignore_hidden_rows_between_pages(projects):
    a = projects[0]
    make_session(a, "shown")
    make_session(a, "secret", hidden=True)
    visible = []
    for index in range(6):
        visible.append(make_event(project=a, session_id="shown", at=index * 10))
        make_event(project=a, session_id="secret", at=index * 10 + 5)
    params = {"limit": 2}
    first = call(**params).json()
    assert ids(first) == [visible[5].id, visible[4].id] and first["has_more"] is True
    assert every_id(params) == [row.id for row in reversed(visible)]


def test_hidden_rows_older_than_the_page_do_not_make_has_more_true(projects):
    a = projects[0]
    make_session(a, "shown")
    make_session(a, "secret", hidden=True)
    make_event(project=a, session_id="secret", at=0)
    make_event(project=a, session_id="secret", at=1)
    newer = [make_event(project=a, session_id="shown", at=2), make_event(project=a, session_id="shown", at=3)]
    body = call(limit=2).json()
    assert ids(body) == [newer[1].id, newer[0].id] and body["has_more"] is False


SHAPES = [
    {}, {"project_ids": "a"}, {"project_ids": "a,b"}, {"provider": "codex"}, {"project_ids": "a", "provider": "codex"},
    {"session": "secret"}, {"families": "turns"}, {"view": "without_turns"}, {"authors": "external_mcp"},
    {"authors": "external_mcp", "project_ids": "a", "provider": "codex"}, {"families": "organization"},
]


@pytest.mark.parametrize("params", SHAPES)
def test_every_query_shape_leaves_the_hidden_session_out(projects, params):
    # One hidden row per query of a page: the main query, the instance query and the null-provider query, an
    # external-author row for the external shape, a turn for the families, a ``hidden.started`` for organization.
    a, b = projects
    params = dict(params)
    if "project_ids" in params:
        params["project_ids"] = ",".join({"a": a.id, "b": b.id}[key] for key in params["project_ids"].split(","))
    make_session(a, "secret", hidden=True)
    make_session(a, "shown")
    external = {"actor": "external", "via": "mcp_external"}
    hidden_rows = [
        make_event(project=a, session_id="secret", at=1, provider="codex"),
        make_event(project=a, session_id="secret", at=2, provider=None),
        make_event("settings.changed", project=a, session_id="secret", at=3, provider="codex", **external),
        make_event("hidden.started", project=a, session_id="secret", at=4, provider="codex", actor="user", via="ui"),
    ]
    visible = [
        make_event(project=a, session_id="shown", at=5, provider="codex"),
        make_event("settings.changed", project=a, session_id="shown", at=6, provider="codex", **external),
        make_event("archived.started", project=a, session_id="shown", at=7, provider="codex", actor="user", via="ui"),
    ]
    hidden_ids = {row.id for row in hidden_rows}
    for limit in (1, 50):
        seen = every_id({**params, "limit": limit})
        assert not hidden_ids & set(seen), (params, limit)
    after = ids(call(**params, after_id=0).json())
    assert not hidden_ids & set(after), params
    # Something visible survives in every shape but the hidden session's own filter.
    assert set(seen) <= {row.id for row in visible}
    assert bool(seen) is (params.get("session") != "secret"), (params, seen)


# -- after_id ------------------------------------------------------------------------------------------------


def test_after_id_leaves_out_hidden_rows_and_reports_the_hidden_session(projects):
    a = projects[0]
    make_session(a, "shown")
    make_session(a, "secret", hidden=True)
    cursor = make_event(project=a, session_id="shown", at=0)
    row = make_event(project=a, session_id="secret", at=1)
    hide = make_event("hidden.started", project=a, session_id="secret", at=2, actor="user", via="ui")
    newer = make_event(project=a, session_id="shown", at=3)
    body = call(after_id=cursor.id).json()
    assert ids(body) == [newer.id]
    assert body["hidden_sessions"] == ["secret"] and body["shown_sessions"] == []
    assert row.id not in ids(body) and hide.id not in ids(body)
    # The ``hidden.started`` row is before the range: nothing to report any more.
    later = call(after_id=hide.id).json()
    assert later["hidden_sessions"] == [] and later["shown_sessions"] == [] and ids(later) == [newer.id]


def test_after_id_reports_a_shown_session_and_returns_its_new_rows(projects):
    a = projects[0]
    make_session(a, "back")
    cursor = make_instance_event(at=0)
    show = make_event("hidden.ended", project=a, session_id="back", at=1, actor="user", via="ui")
    body = call(after_id=cursor.id).json()
    assert body["shown_sessions"] == ["back"] and body["hidden_sessions"] == []
    assert ids(body) == [show.id]


def test_the_current_state_wins_inside_one_range(projects):
    a = projects[0]
    make_session(a, "flip_back")   # hidden then shown again: visible now
    make_session(a, "flip_away", hidden=True)  # shown then hidden again: hidden now
    cursor = make_instance_event(at=0)
    for session_id, kinds in (("flip_back", ("hidden.started", "hidden.ended")),
                              ("flip_away", ("hidden.ended", "hidden.started"))):
        for offset, kind in enumerate(kinds):
            make_event(kind, project=a, session_id=session_id, at=1 + offset, actor="user", via="ui")
    body = call(after_id=cursor.id).json()
    assert body["hidden_sessions"] == ["flip_away"]
    assert body["shown_sessions"] == ["flip_back"]
    assert {event["session_id"] for event in body["events"]} == {"flip_back"}


def test_a_toggle_row_whose_state_was_undone_reports_nothing(projects):
    a = projects[0]
    make_session(a, "undone")  # visible now, though the range only holds its ``hidden.started``
    make_session(a, "redone", hidden=True)  # hidden now, though the range only holds its ``hidden.ended``
    cursor = make_instance_event(at=0)
    started = make_event("hidden.started", project=a, session_id="undone", at=1, actor="user", via="ui")
    make_event("hidden.ended", project=a, session_id="redone", at=2, actor="user", via="ui")
    body = call(after_id=cursor.id).json()
    assert body["hidden_sessions"] == [] and body["shown_sessions"] == []
    assert ids(body) == [started.id]


def test_hidden_and_shown_sessions_follow_the_scope_not_the_filters(projects):
    a, b = projects
    make_session(a, "here", hidden=True)
    make_session(b, "there", hidden=True)
    make_session(a, "back_here")
    make_session(b, "back_there")
    cursor = make_instance_event(at=0)
    make_event("hidden.started", project=a, session_id="here", at=1, actor="user", via="ui")
    make_event("hidden.started", project=b, session_id="there", at=2, actor="user", via="ui")
    make_event("hidden.ended", project=a, session_id="back_here", at=3, actor="user", via="ui")
    make_event("hidden.ended", project=b, session_id="back_there", at=4, actor="user", via="ui")
    every = call(after_id=cursor.id).json()
    assert every["hidden_sessions"] == ["here", "there"] and every["shown_sessions"] == ["back_here", "back_there"]
    scoped = call(after_id=cursor.id, project_ids=a.id).json()
    assert scoped["hidden_sessions"] == ["here"] and scoped["shown_sessions"] == ["back_here"]
    # The filters (view, families, authors) do not apply: a turns-only list must still drop and merge.
    filtered = call(after_id=cursor.id, project_ids=a.id, families="turns", authors="session_agent").json()
    assert filtered["events"] == []
    assert filtered["hidden_sessions"] == ["here"] and filtered["shown_sessions"] == ["back_here"]
    # The session filter is part of the scope.
    one = call(after_id=cursor.id, session="back_there").json()
    assert one["hidden_sessions"] == [] and one["shown_sessions"] == ["back_there"]


def test_after_id_with_a_filter_still_leaves_out_hidden_rows(projects):
    a = projects[0]
    make_session(a, "secret", hidden=True)
    make_session(a, "shown")
    make_event("turn.started", project=a, session_id="secret", at=1)
    turn = make_event("turn.started", project=a, session_id="shown", at=2)
    make_event("process.started", project=a, session_id="shown", at=3)
    body = call(after_id=0, families="turns").json()
    assert ids(body) == [turn.id]


def test_truncated_still_speaks_of_the_scope_range(projects, monkeypatch):
    a = projects[0]
    make_session(a, "secret", hidden=True)
    monkeypatch.setattr(timeline_feed, "AFTER_ID_CAP", 3)
    cursor = make_instance_event(at=0)
    rows = [make_event(project=a, session_id="secret", at=index + 1) for index in range(3)]
    visible = make_event(project=a, session_id="shown", at=9)
    exact = call(after_id=cursor.id).json()
    assert exact["truncated"] is True and exact["events"] == []
    after = call(after_id=rows[0].id).json()
    assert after["truncated"] is False and ids(after) == [visible.id]


def test_the_new_keys_are_sorted_lists_only_on_an_after_id_response(projects):
    a = projects[0]
    for name in ("zeta", "alpha", "mid"):
        make_session(a, name, hidden=True)
        make_event("hidden.started", project=a, session_id=name, at=1, actor="user", via="ui")
    body = call(after_id=0).json()
    assert body["hidden_sessions"] == ["alpha", "mid", "zeta"]
    page = call().json()
    assert "hidden_sessions" not in page and "shown_sessions" not in page


# -- Query plans ---------------------------------------------------------------------------------------------


@pytest.fixture
def busy(projects):
    for index in range(30):
        make_event(project=projects[index % 2], at=index, provider="codex" if index % 2 else "claude_code")
    make_session(projects[0], "secret", hidden=True)
    return projects


EXCLUSION = (
    'NOT ("core_timelineevent"."session_id" IN (SELECT "U0"."id" AS "id" FROM "core_session" "U0" WHERE '
    '"U0"."hidden" = %s) AND "core_timelineevent"."session_id" IS NOT NULL)'
)


@pytest.mark.parametrize("params", [
    {}, {"provider": "codex"}, {"project_ids": "-tl-hid-a"}, {"project_ids": "-tl-hid-a,-tl-hid-b", "provider": "codex"},
    {"session": "s1"}, {"families": "twicc"}, {"authors": "external_mcp"},
])
def test_every_page_query_carries_the_exclusion_as_one_uncorrelated_subquery(busy, params):
    for before in ({}, {"before": "2026-10-14T10:00:15.000000Z,12"}):
        for queryset in timeline_feed._page_querysets(parse_request({**params, **before})):
            sql, plan = plan_of(queryset)
            # An AND under a NOT: no OR of its own (the page queries refuse one), and never an index driver. The LAST
            # term of the WHERE: the other terms reject a row before its subquery probe.
            where = sql.split(" WHERE ", 1)[1].rsplit(" ORDER BY ", 1)[0]
            assert where.endswith((EXCLUSION, EXCLUSION + ")")), sql
            # The partial index of the hidden sessions serves the subquery, read once (not per row).
            assert "LIST SUBQUERY" in plan and "CORRELATED" not in plan, plan
            assert "SEARCH U0 USING INDEX idx_session_hidden (hidden=?)" in plan, plan


# -- Filters that can never match ------------------------------------------------------------------------------


IMPOSSIBLE = [{"view": "without_turns", "families": "turns"}, {"view": "waits_problems", "families": "sessions,settings"},
              {"view": "waits_problems", "families": "turns", "authors": "you", "provider": "codex"}]


def event_reads(captured):
    """The statements that read events, ``latest_id`` (the highest id, ``ORDER BY 1 DESC LIMIT 1``) aside."""
    latest = 'SELECT "core_timelineevent"."id" AS "id" FROM "core_timelineevent" ORDER BY 1 DESC LIMIT 1'
    return [q["sql"] for q in captured.captured_queries if 'FROM "core_timelineevent"' in q["sql"] and q["sql"] != latest]


@pytest.mark.parametrize("params", IMPOSSIBLE)
def test_a_filter_that_can_never_match_answers_the_empty_page_without_reading_events(projects, params):
    a = projects[0]
    make_event(project=a, at=1)
    make_event("process.ended", project=a, at=2, reason="error")
    last = make_instance_event(at=3)
    for before in ({}, {"before": "2026-10-14T10:00:09.000000Z,99"}):
        with CaptureQueriesContext(connection) as captured:
            body = timeline_feed.fetch_feed(parse_request({**params, **before}))
        assert body == {"events": [], "has_more": False, "truncated": False, "latest_id": last.id, "sessions": {}}
        assert event_reads(captured) == []
    assert call(**params).json()["events"] == []


@pytest.mark.parametrize("params", IMPOSSIBLE)
def test_a_filter_that_can_never_match_still_reads_the_after_id_range(projects, params, monkeypatch):
    a = projects[0]
    make_session(a, "secret", hidden=True)
    make_session(a, "back")
    cursor = make_instance_event(at=0)
    make_event("hidden.started", project=a, session_id="secret", at=1, actor="user", via="ui")
    make_event("hidden.ended", project=a, session_id="back", at=2, actor="user", via="ui")
    last = make_event("process.ended", project=a, session_id="back", at=3, reason="error")
    with CaptureQueriesContext(connection) as captured:
        body = timeline_feed.fetch_feed(parse_request({"after_id": str(cursor.id), **params}))
    # One read of the events: the range. The filter query on its ids is not sent.
    assert len(event_reads(captured)) == 1, event_reads(captured)
    assert body["events"] == [] and body["truncated"] is False and body["latest_id"] == last.id
    assert body["hidden_sessions"] == ["secret"] and body["shown_sessions"] == ["back"]
    # The range still decides ``truncated``: the client must reload when it overflows.
    monkeypatch.setattr(timeline_feed, "AFTER_ID_CAP", 2)
    assert call(after_id=cursor.id, **params).json()["truncated"] is True
