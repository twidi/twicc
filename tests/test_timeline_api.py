"""``GET /api/timeline/`` (display design 2026-10-15, sections 7 and 14)."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test import AsyncClient
from django.test.utils import CaptureQueriesContext

from twicc.auth.middleware import _is_data_path
from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services import timeline_feed
from twicc.core.services.timeline_feed import AFTER_ID_CAP, FeedError, parse_request
from twicc.core.services.timeline_filters import TimelineFilter
from twicc.pending_titles import pop_pending_title, set_pending_title

pytestmark = pytest.mark.django_db(transaction=True)

T0 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def call(client=None, **params):
    """GET the endpoint; returns the response (``.json()`` for the body)."""
    return asyncio.run((client or AsyncClient()).get("/api/timeline/", params))


@pytest.fixture
def projects():
    return [Project.objects.create(id=f"-tl-api-{name}", directory=f"/tmp/tl-api-{name}") for name in "abc"]


def make_event(kind="turn.started", *, project=None, session_id="s1", at=0, provider="codex", **fields):
    """One event ``at`` seconds after T0 (a float keeps microseconds)."""
    fields.setdefault("actor", "agent")
    return TimelineEvent.objects.create(
        kind=kind, project=project, session_id=session_id, occurred_at=T0 + timedelta(seconds=at),
        provider=provider, **fields,
    )


def make_instance_event(at=0, kind="backend.started", **fields):
    return make_event(
        kind, project=None, session_id=None, at=at, provider=None, actor="system", data={"pid": 1}, **fields,
    )


def ids(body):
    return [event["id"] for event in body["events"]]


# -- Ordering, paging, cursor -----------------------------------------------------------------------------


def test_events_come_newest_first_and_the_id_breaks_ties():
    old = make_event(at=0)
    tie_a = make_event(at=5)
    tie_b = make_event(at=5)
    new = make_event(at=9)
    body = call().json()
    assert ids(body) == [new.id, tie_b.id, tie_a.id, old.id]


def test_the_event_shape_is_exactly_the_public_one():
    make_event(
        "title.changed", at=1.5, session_id="s1", actor="agent", via="mcp_internal", actor_ref="boss",
        actor_name=None, reason="r", precision="derived", data={"source": "agent", "new": "N", "old": None},
    )
    event = call().json()["events"][0]
    assert set(event) == {
        "id", "kind", "occurred_at", "precision", "actor", "via", "actor_ref", "actor_name", "reason", "data",
        "session_id", "project_id", "provider",
    }
    assert TIMESTAMP.match(event["occurred_at"]) and event["occurred_at"] == "2026-10-14T10:00:01.500000Z"
    assert event["precision"] == "derived" and event["provider"] == "codex" and event["data"]["new"] == "N"


def test_paging_by_before_returns_every_row_exactly_once_with_many_equal_timestamps():
    created = [make_event(at=0) for _ in range(7)] + [make_event(at=3) for _ in range(5)]
    seen, cursor, pages = [], None, 0
    # Bounded: a regression of the cursor would repeat the same rows forever and hang the suite.
    for _ in range(6):
        params = {"limit": 3}
        if cursor:
            params["before"] = cursor
        body = call(**params).json()
        seen += ids(body)
        pages += 1
        if not body["has_more"]:
            break
        last = body["events"][-1]
        cursor = f"{last['occurred_at']},{last['id']}"
    else:
        pytest.fail(f"the paging never reached the end: {seen}")
    expected = sorted((event.id for event in created), reverse=True)
    expected = [i for i in expected if i > created[6].id] + [i for i in expected if i <= created[6].id]
    assert seen == expected and len(set(seen)) == 12 and pages == 4


def test_paging_over_equal_timestamps_in_a_multi_project_scope(projects):
    # With several project ids the planner sorts in a temp b-tree: only the SQL ``id DESC`` tie-break keeps
    # the pages consistent, the index order does not.
    a, b, _ = projects
    rows = [make_event(project=(a if i % 2 else b), at=0) for i in range(8)]
    seen, cursor = [], None
    for _ in range(6):
        params = {"limit": 3, "project_ids": f"{a.id},{b.id}"}
        if cursor:
            params["before"] = cursor
        body = call(**params).json()
        seen += ids(body)
        if not body["has_more"]:
            break
        last = body["events"][-1]
        cursor = f"{last['occurred_at']},{last['id']}"
    else:
        pytest.fail(f"never reached the end: {seen}")
    assert seen == sorted((row.id for row in rows), reverse=True)


def test_the_cursor_round_trips_microseconds_for_rows_equal_to_the_millisecond():
    rows = [make_event(at=0.000100), make_event(at=0.000400), make_event(at=0.000900)]
    first = call(limit=1).json()
    assert first["events"][0]["occurred_at"] == "2026-10-14T10:00:00.000900Z"
    assert first["has_more"] is True
    cursor = f"{first['events'][-1]['occurred_at']},{first['events'][-1]['id']}"
    second = call(limit=1, before=cursor).json()
    cursor = f"{second['events'][-1]['occurred_at']},{second['events'][-1]['id']}"
    third = call(limit=1, before=cursor).json()
    assert ids(first) + ids(second) + ids(third) == [rows[2].id, rows[1].id, rows[0].id]
    assert third["has_more"] is False


def test_a_cursor_with_an_offset_instead_of_z_is_accepted():
    row = make_event(at=1)
    make_event(at=0)
    body = call(before=f"2026-10-14T12:00:01+02:00,{row.id + 1}").json()
    assert row.id in ids(body)


def test_has_more_uses_one_extra_row():
    for index in range(4):
        make_event(at=index)
    assert call(limit=3).json()["has_more"] is True
    assert call(limit=4).json()["has_more"] is False


# -- Scope --------------------------------------------------------------------------------------------------


def test_global_includes_null_project_rows_and_a_project_scope_excludes_them(projects):
    a, b, _ = projects
    in_a = make_event(project=a, at=1)
    orphan = make_event(project=None, session_id="lost", at=2)
    in_b = make_event(project=b, at=3)
    assert set(ids(call().json())) == {in_a.id, orphan.id, in_b.id}
    assert ids(call(project_ids=a.id).json()) == [in_a.id]


def test_a_project_with_worktrees_returns_both_when_the_ids_are_sent(projects):
    a, b, c = projects
    main, worktree, other = make_event(project=a, at=1), make_event(project=b, at=2), make_event(project=c, at=3)
    body = call(project_ids=f"{a.id},{b.id}").json()
    assert ids(body) == [worktree.id, main.id] and other.id not in ids(body)


def test_project_ids_drop_empty_items_and_an_empty_value_means_global(projects):
    a = projects[0]
    row = make_event(project=a, at=1)
    other = make_event(project=None, session_id="lost", at=2)
    assert ids(call(project_ids=f" {a.id} ,, ,").json()) == [row.id]
    assert set(ids(call(project_ids="").json())) == {row.id, other.id}
    assert set(ids(call(project_ids=",,").json())) == {row.id, other.id}


# -- Instance events: the four cases of the 7.4 table ------------------------------------------------------


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("provider", [None, "codex"])
def test_an_instance_event_appears_exactly_once_in_each_of_the_four_cases(projects, scoped, provider):
    a = projects[0]
    row = make_event(project=a, at=1)
    boot = make_instance_event(at=2)
    other_provider = make_event(project=a, at=3, provider="claude_code")
    params = {}
    if scoped:
        params["project_ids"] = a.id
    if provider:
        params["provider"] = provider
    body = call(**params).json()
    expected = [boot.id, row.id] if provider else [other_provider.id, boot.id, row.id]
    assert ids(body) == expected
    assert ids(body).count(boot.id) == 1


@pytest.mark.parametrize("scoped", [False, True])
def test_an_instance_event_is_merged_at_its_time_position_across_a_page_boundary(projects, scoped):
    a = projects[0]
    rows = [make_event(project=a, at=index * 10) for index in range(4)]  # at 0, 10, 20, 30
    boot = make_instance_event(at=15)
    params = {"limit": 2, "provider": "codex"}
    if scoped:
        params["project_ids"] = a.id
    first = call(**params).json()
    assert ids(first) == [rows[3].id, rows[2].id] and first["has_more"] is True
    last = first["events"][-1]
    second = call(before=f"{last['occurred_at']},{last['id']}", **params).json()
    assert ids(second) == [boot.id, rows[1].id] and second["has_more"] is True
    last = second["events"][-1]
    third = call(before=f"{last['occurred_at']},{last['id']}", **params).json()
    assert ids(third) == [rows[0].id] and third["has_more"] is False


def test_an_instance_event_and_a_session_event_with_the_same_time_are_ordered_by_id(projects):
    a = projects[0]
    first = make_event(project=a, at=5)
    boot = make_instance_event(at=5)
    assert ids(call(project_ids=a.id).json()) == [boot.id, first.id]


def test_a_row_that_both_queries_return_is_listed_once(projects):
    a = projects[0]
    both = make_event(project=a, session_id=None, at=1, actor="system", data={"pid": 1})
    body = call(project_ids=a.id, provider="codex").json()
    assert ids(body) == [both.id]


@pytest.mark.parametrize("params,queries", [
    ({}, 1),                                          # global, no provider: the main query already holds them
    ({"provider": "codex"}, 3),                       # global with a provider: instance + null-provider queries
    ({"project_ids": "-tl-api-a"}, 2),                # scoped
    ({"project_ids": "-tl-api-a", "provider": "codex"}, 3),
    ({"session": "s1"}, 1),                           # a session filter: one query, no instance row
    ({"session": "s1", "provider": "codex"}, 1),
    ({"session": "s1", "project_ids": "-tl-api-a"}, 1),
    ({"session": "s1", "project_ids": "-tl-api-a", "provider": "codex"}, 1),
])
def test_the_number_of_page_queries_per_scope_and_filter(params, queries):
    assert len(timeline_feed._page_querysets(parse_request(params))) == queries


# -- Provider, parameters, errors ---------------------------------------------------------------------------


def test_the_provider_filter_keeps_only_that_provider_and_instance_events():
    codex = make_event(at=1, provider="codex")
    make_event(at=2, provider="claude_code")
    boot = make_instance_event(at=3)
    unnamed = make_event(session_id="s9", at=4, provider=None)  # a session row no source could name
    assert ids(call(provider="codex").json()) == [unnamed.id, boot.id, codex.id]


@pytest.mark.parametrize("params", [
    {"provider": "nope"},
    {"before": "garbage"},
    {"before": "2026-10-14T10:00:00Z"},
    {"before": "2026-10-14T10:00:00Z,x"},
    {"before": "2026-10-14T10:00:00,5"},
    {"before": "2026-10-14T10:00:00 00:00,5"},
    {"after_id": "x"},
    {"after_id": "1.5"},
    {"after_id": "-1"},
    {"limit": "0"},
    {"limit": "-3"},
    {"limit": "x"},
    {"limit": "1.5"},
    {"limit": ""},
    {"limit": "1_0"},
    {"before": "2026-10-14T10:00:00Z,5", "after_id": "3"},
    {"after_id": "5", "limit": "0"},
    {"before": "2026-10-14T10:00:00Z,99999999999999999999"},   # beyond a 64-bit integer: SQLite cannot bind it
    {"before": "2026-10-14T10:00:00Z,-1"},
    {"before": "0001-01-01T00:00:00+05:00,1"},                  # the conversion to UTC overflows
    {"after_id": "99999999999999999999"},
    {"limit": "9" * 5000},                                      # beyond Python's int-from-string limit
    {"after_id": "9" * 5000},
    {"before": "2026-10-14T10:00:00Z," + "9" * 5000},
])
def test_bad_parameters_answer_400_with_an_error_body(params):
    response = call(**params)
    assert response.status_code == 400
    assert isinstance(response.json()["error"], str) and response.json()["error"]


def test_a_limit_over_100_is_capped():
    for index in range(105):
        make_event(at=index)
    body = call(limit=1000).json()
    assert len(body["events"]) == 100 and body["has_more"] is True
    assert len(call().json()["events"]) == 50


def test_the_documented_limits():
    assert (timeline_feed.DEFAULT_LIMIT, timeline_feed.MAX_LIMIT, AFTER_ID_CAP) == (50, 100, 500)


def test_parse_request_defaults_and_normalisation():
    request = parse_request({"project_ids": "a,b,a", "provider": "codex"})
    assert request == timeline_feed.FeedRequest(("a", "b"), 50, None, None, TimelineFilter(providers=("codex",)))
    assert parse_request({"limit": " 7 "}).limit == 7
    with pytest.raises(FeedError):
        parse_request({"limit": "0"})


def test_the_endpoint_only_answers_get():
    response = asyncio.run(AsyncClient().post("/api/timeline/"))
    assert response.status_code == 405


def test_the_endpoint_is_behind_the_instance_authentication(settings):
    assert _is_data_path(SimpleNamespace(path="/api/timeline/")) is True
    make_event(at=1)
    settings.TWICC_PASSWORD_HASH = "pbkdf2_sha256$1$salt$hash"  # a fresh client builds the middleware chain again
    assert call().status_code == 401
    assert call(after_id=0).status_code == 401
    settings.TWICC_PASSWORD_HASH = ""
    assert call().status_code == 200


def test_a_database_error_answers_500(monkeypatch):
    def boom(request):
        raise RuntimeError("database down")

    monkeypatch.setattr(timeline_feed, "fetch_feed", boom)
    response = asyncio.run(AsyncClient(raise_request_exception=False).get("/api/timeline/"))
    assert response.status_code == 500


def test_the_view_never_writes_the_table_and_refuses_other_methods(projects):
    make_event(project=projects[0], at=1)
    before = list(TimelineEvent.objects.values_list("id", "kind", "occurred_at", "data"))
    assert call().status_code == 200 and call(after_id=0).status_code == 200
    client = AsyncClient()
    for method in ("post", "put", "patch", "delete"):
        assert asyncio.run(getattr(client, method)("/api/timeline/")).status_code == 405
    assert list(TimelineEvent.objects.values_list("id", "kind", "occurred_at", "data")) == before


def test_the_feed_service_issues_no_write_statement(projects):
    make_event(project=projects[0], at=1)
    # The view runs in another thread: read the statements of the service it calls, in this one.
    with CaptureQueriesContext(connection) as captured:
        for params in ({}, {"after_id": "0"}, {"project_ids": projects[0].id, "provider": "codex"}):
            timeline_feed.fetch_feed(parse_request(params))
    assert captured.captured_queries
    writes = [q["sql"] for q in captured.captured_queries if q["sql"].lstrip().split()[0].upper() in
              {"INSERT", "UPDATE", "DELETE", "REPLACE"}]
    assert writes == []


# -- after_id ------------------------------------------------------------------------------------------------


def test_after_id_returns_only_newer_ids_in_ascending_order_even_when_back_dated(projects):
    a = projects[0]
    first = make_event(project=a, at=10)
    back_dated = make_event(project=a, at=1)
    newest = make_event(project=a, at=20)
    body = call(after_id=first.id).json()
    assert ids(body) == [back_dated.id, newest.id] and body["truncated"] is False
    assert call(after_id=newest.id).json()["events"] == []


@pytest.mark.parametrize("scoped,provider,expected", [
    (False, None, ["a-codex", "b-claude", "boot"]),
    (False, "codex", ["a-codex", "boot"]),
    (True, None, ["a-codex", "boot"]),
    (True, "claude_code", ["boot"]),
])
def test_after_id_applies_the_scope_and_provider_and_includes_instance_events(projects, scoped, provider, expected):
    a, b, _ = projects
    names = {}
    names["a-codex"] = make_event(project=a, at=1, provider="codex").id
    names["b-claude"] = make_event(project=b, at=2, provider="claude_code").id
    names["boot"] = make_instance_event(at=3).id
    params = {"after_id": 0}
    if scoped:
        params["project_ids"] = a.id
    if provider:
        params["provider"] = provider
    assert ids(call(**params).json()) == [names[name] for name in expected]


def test_after_id_is_truncated_past_the_cap_and_keeps_the_first_rows(projects):
    a = projects[0]
    rows = [make_event(project=a, at=index) for index in range(AFTER_ID_CAP + 3)]
    body = call(after_id=0).json()
    assert body["truncated"] is True and ids(body) == [row.id for row in rows[:AFTER_ID_CAP]]
    exact = call(after_id=rows[2].id).json()
    assert len(exact["events"]) == AFTER_ID_CAP and exact["truncated"] is False


def test_the_cap_applies_to_the_merged_set_of_rows_and_instance_events(projects):
    a = projects[0]
    for index in range(AFTER_ID_CAP):
        make_event(project=a, at=index)
    make_instance_event(at=999)
    body = call(after_id=0, project_ids=a.id).json()
    assert body["truncated"] is True and len(body["events"]) == AFTER_ID_CAP


def test_an_empty_feed_has_a_null_latest_id_and_after_id_zero_fetches_the_first_event():
    body = call().json()
    assert body == {"events": [], "has_more": False, "truncated": False, "latest_id": None, "sessions": {}, "turns_below": {}}
    assert call(after_id=0).json()["latest_id"] is None
    first = make_event(at=1)
    assert ids(call(after_id=0).json()) == [first.id]


def test_latest_id_is_the_highest_id_whatever_the_scope(projects):
    a, b, _ = projects
    make_event(project=a, at=1)
    make_event(project=b, at=2)
    last = make_instance_event(at=3)  # no project, no provider: still the highest id of the table
    assert call(project_ids=a.id).json()["latest_id"] == last.id
    assert call(provider="claude_code").json()["latest_id"] == last.id
    assert call().json()["latest_id"] == last.id


def test_latest_id_is_read_before_the_events(projects, monkeypatch):
    first = make_event(project=projects[0], at=1)
    original = timeline_feed._fetch_page
    created = []

    def racing(request):
        # A row committed between the two reads.
        created.append(make_event(project=projects[0], at=2))
        return original(request)

    monkeypatch.setattr(timeline_feed, "_fetch_page", racing)
    body = call().json()
    assert body["latest_id"] == first.id
    assert ids(body) == [created[0].id, first.id] and created[0].id > body["latest_id"]


# -- Side table -----------------------------------------------------------------------------------------------


def make_session(project, sid, **fields):
    fields.setdefault("provider", "codex")
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, **fields)


def test_the_side_table_holds_every_cited_session_by_event_by_actor_ref_and_by_spawned_by(projects):
    a = projects[0]
    make_session(a, "s1", title="Mine", archived=True)
    make_session(a, "boss", title="The boss", provider="claude_code")
    make_session(a, "parent", title=None)
    make_session(a, "unrelated", title="Unrelated")
    make_event("title.changed", project=a, session_id="s1", actor="agent", actor_ref="boss", at=1)
    make_event("session.created", project=a, session_id="s1", at=2, actor="system",
               data={"origin": "twicc", "spawned_by": "parent"})
    sessions = call().json()["sessions"]
    assert set(sessions) == {"s1", "boss", "parent"}
    assert sessions["s1"] == {"title": "Mine", "project_id": a.id, "provider": "codex", "hidden": False,
                              "archived": True, "mute_on_user_turn": False, "pinned": None}
    assert sessions["boss"]["provider"] == "claude_code"
    assert sessions["parent"]["title"] is None


def test_session_side_table_state_is_current_without_changing_historical_events(projects):
    session = make_session(projects[0], "s1")
    make_event("archived.started", project=projects[0], session_id="s1", actor="user", via="ui")
    before = call(include_muted="1").json()
    Session.objects.filter(pk=session.pk).update(archived=True, mute_on_user_turn=True, pinned="workspace")
    after = call(include_muted="1").json()
    assert before["events"] == after["events"]
    assert after["sessions"]["s1"]["archived"] is True
    assert after["sessions"]["s1"]["mute_on_user_turn"] is True
    assert after["sessions"]["s1"]["pinned"] == "workspace"


def test_a_user_actor_ref_is_not_looked_up_as_a_session(projects):
    make_session(projects[0], "tok_1", title="Not a token")
    make_event("settings.changed", project=projects[0], session_id="s1", actor="user", actor_ref="tok_1", at=1)
    assert "tok_1" not in call().json()["sessions"]


def test_the_side_table_applies_the_pending_title_override_and_flags(projects):
    make_session(projects[0], "s1", title="Stored", hidden=True)
    # The rows of a hidden session are left out: a visible row cites it (its agent did something).
    make_event(project=projects[0], session_id="s2", actor="agent", via="cli", actor_ref="s1", at=1)
    set_pending_title("s1", "Pending")
    try:
        entry = call().json()["sessions"]["s1"]
    finally:
        pop_pending_title("s1")
    assert entry["title"] == "Pending" and entry["hidden"] is True and entry["archived"] is False


def test_an_untitled_session_has_a_null_title_in_the_side_table(projects):
    make_session(projects[0], "empty", title="")
    make_session(projects[0], "none", title=None)
    make_event(project=projects[0], session_id="empty", at=1)
    make_event(project=projects[0], session_id="none", at=2)
    sessions = call().json()["sessions"]
    assert sessions["empty"]["title"] is None and sessions["none"]["title"] is None


def test_a_session_id_with_no_row_has_no_entry(projects):
    make_event(project=projects[0], session_id="purged", at=1)
    body = call().json()
    assert ids(body) and body["sessions"] == {}


def test_the_side_table_lookup_is_chunked(projects, monkeypatch):
    monkeypatch.setattr(timeline_feed, "SESSION_LOOKUP_CHUNK", 2)
    for index in range(5):
        make_session(projects[0], f"c{index}", title=f"T{index}")
        make_event(project=projects[0], session_id=f"c{index}", at=index)
    with CaptureQueriesContext(connection) as captured:
        sessions = timeline_feed.build_sessions_table({f"c{index}" for index in range(5)})
    assert set(sessions) == {f"c{index}" for index in range(5)}
    lookups = [q["sql"] for q in captured.captured_queries if 'FROM "core_session"' in q["sql"]]
    assert len(lookups) == 3
    # No single ``IN`` carries more than the chunk: a slice that forgot its upper bound would send them all.
    assert all(len(re.search(r"IN \(([^)]*)\)", sql).group(1).split(",")) <= 2 for sql in lookups)


# -- Query plans --------------------------------------------------------------------------------------------------


def plan_of(queryset):
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
        return sql, " | ".join(str(row[-1]) for row in cursor.fetchall())


@pytest.fixture
def busy(projects):
    for index in range(30):
        make_event(project=projects[index % 3], at=index, provider="codex" if index % 2 else "claude_code")
    return projects


CURSOR = f"{(T0 + timedelta(seconds=15)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')},12"


@pytest.mark.parametrize("params,index_name", [
    ({}, "idx_timeline_occurred"),
    ({"provider": "codex"}, "idx_timeline_provider"),
    ({"project_ids": "-tl-api-a"}, "idx_timeline_project"),
    ({"project_ids": "-tl-api-a,-tl-api-b"}, "idx_timeline_project"),
])
def test_the_page_query_uses_an_index_with_the_row_value_keyset_and_no_or(busy, params, index_name):
    request = parse_request({**params, "before": CURSOR})
    main = timeline_feed._page_querysets(request)[0]
    sql, plan = plan_of(main)
    assert index_name in plan and "MULTI-INDEX OR" not in plan
    assert "(occurred_at, id) < (" in sql and " OR " not in sql.upper()


def test_the_instance_query_uses_the_session_index(busy):
    request = parse_request({"project_ids": "-tl-api-a", "before": CURSOR})
    _, plan = plan_of(timeline_feed._page_querysets(request)[1])
    assert "idx_timeline_session" in plan and "USE TEMP B-TREE" not in plan


@pytest.mark.parametrize("params", [
    {}, {"project_ids": "-tl-api-a,-tl-api-b"}, {"provider": "codex"},
    {"project_ids": "-tl-api-a", "provider": "codex"},
])
def test_the_after_id_query_scans_a_primary_key_range_never_a_scope_index(busy, params):
    request = parse_request({**params, "after_id": "5"})
    # The real builder of ``_fetch_after``: a regression inside it shows up in the plan.
    _, plan = plan_of(timeline_feed._after_queryset(request))
    assert "INTEGER PRIMARY KEY" in plan
    assert "idx_timeline_project" not in plan and "idx_timeline_provider" not in plan


FILTER_PARAMS = [{"view": "messages"}, {"families": "turns"}, {"families": "sessions,processes"}, {"view": "without_turns"},
                 {"view": "waits_problems"}]


@pytest.mark.parametrize("scope", [None, "-tl-api-a"])
@pytest.mark.parametrize("params", FILTER_PARAMS)
def test_a_family_or_view_filter_keeps_the_ordered_index_and_sorts_nothing(busy, params, scope):
    scope_params = {"project_ids": scope} if scope else {}
    for before in ({}, {"before": CURSOR}):
        request = parse_request({**params, **scope_params, **before})
        _, plan = plan_of(timeline_feed._page_querysets(request)[0])
        assert "USE TEMP B-TREE" not in plan, plan
        assert "idx_timeline_kind" not in plan and "MULTI-INDEX OR" not in plan, plan
        assert ("idx_timeline_project" if scope else "idx_timeline_occurred") in plan, plan


@pytest.mark.parametrize("params", FILTER_PARAMS)
def test_a_family_or_view_filter_in_a_multi_project_scope_never_uses_the_kind_index(busy, params):
    # A multi-value ``IN`` sorts per value (harmless: SQLite stops at the limit per value).
    for before in ({}, {"before": CURSOR}):
        request = parse_request({**params, "project_ids": "-tl-api-a,-tl-api-b", **before})
        _, plan = plan_of(timeline_feed._page_querysets(request)[0])
        assert "idx_timeline_kind" not in plan and "MULTI-INDEX OR" not in plan, plan


@pytest.mark.parametrize("params", [
    {"session": "s1"},
    {"session": "s1", "provider": "codex"},
    {"session": "s1", "project_ids": "-tl-api-a"},
    {"session": "s1", "provider": "codex", "project_ids": "-tl-api-a"},
    {"session": "s1", "provider": "codex", "project_ids": "-tl-api-a,-tl-api-b", "families": "turns"},
])
def test_a_session_filter_uses_the_session_index(busy, params):
    for before in ({}, {"before": CURSOR}):
        (query,) = timeline_feed._page_querysets(parse_request({**params, **before}))
        _, plan = plan_of(query)
        assert "USING INDEX idx_timeline_session (" in plan, plan
        assert "USE TEMP B-TREE" not in plan, plan


@pytest.mark.parametrize("scope", [None, "-tl-api-a", "-tl-api-a,-tl-api-b"])
def test_the_null_provider_query_uses_the_provider_index(busy, scope):
    params = {"provider": "codex", "before": CURSOR}
    if scope:
        params["project_ids"] = scope
    _, plan = plan_of(timeline_feed._page_querysets(parse_request(params))[2])
    assert "idx_timeline_provider" in plan and "USE TEMP B-TREE" not in plan, plan


def test_a_scoped_provider_filter_keeps_the_project_index(busy):
    request = parse_request({"provider": "codex", "project_ids": "-tl-api-a", "before": CURSOR})
    _, plan = plan_of(timeline_feed._page_querysets(request)[0])
    assert "idx_timeline_project" in plan and "USE TEMP B-TREE" not in plan, plan


@pytest.mark.parametrize("params", [
    {"session": "s1"}, {"session": "s1", "provider": "codex", "project_ids": "-tl-api-a"},
    {"families": "turns", "provider": "codex"},
])
def test_the_after_id_query_with_filters_still_scans_a_primary_key_range(busy, params):
    _, plan = plan_of(timeline_feed._after_queryset(parse_request({**params, "after_id": "5"})))
    assert "INTEGER PRIMARY KEY" in plan and "idx_timeline" not in plan, plan


EXTERNAL_SCOPES = [{}, {"project_ids": "-tl-api-a"}, {"project_ids": "-tl-api-a,-tl-api-b"}, {"provider": "codex"},
                   {"project_ids": "-tl-api-a", "provider": "codex"}]


@pytest.mark.parametrize("scope", EXTERNAL_SCOPES)
def test_an_external_author_filter_walks_the_partial_external_index(busy, scope):
    # ``idx_timeline_external`` (actor = 'external' only): the scope columns carry a unary plus, so the page
    # query walks that tiny index in order instead of reading the whole scope for rows that are rare.
    for before in ({}, {"before": CURSOR}):
        request = parse_request({"authors": "external_mcp", **scope, **before})
        _, plan = plan_of(timeline_feed._page_querysets(request)[0])
        assert "idx_timeline_external" in plan, plan
        assert "USE TEMP B-TREE" not in plan and "MULTI-INDEX OR" not in plan, plan


@pytest.mark.parametrize("authors", ["you", "external_mcp,you", "unknown,external_mcp"])
def test_other_author_filters_keep_the_scope_index(busy, authors):
    for scope, index_name in (({}, "idx_timeline_occurred"), ({"project_ids": "-tl-api-a"}, "idx_timeline_project")):
        request = parse_request({"authors": authors, **scope, "before": CURSOR})
        _, plan = plan_of(timeline_feed._page_querysets(request)[0])
        assert index_name in plan and "idx_timeline_external" not in plan, plan
        assert "USE TEMP B-TREE" not in plan and "MULTI-INDEX OR" not in plan, plan


@pytest.mark.parametrize("scope", EXTERNAL_SCOPES)
def test_an_instance_family_filter_reads_the_instance_rows_only(busy, scope):
    # Only instance rows carry the ``twicc`` kinds: one query on the session index (``session_id IS NULL``).
    for before in ({}, {"before": CURSOR}):
        queries = timeline_feed._page_querysets(parse_request({"families": "twicc", **scope, **before}))
        assert len(queries) == 1
        sql, plan = plan_of(queries[0])
        assert '"session_id" IS NULL' in sql
        assert "idx_timeline_session (session_id=?" in plan and "USE TEMP B-TREE" not in plan, plan


@pytest.mark.parametrize("scope", ["project", "workspace", "global"])
@pytest.mark.parametrize("kind", ["user_message.sent", "assistant_message.sent"])
def test_message_snapshots_keep_saved_text_in_every_feed_scope(projects, tmp_path, scope, kind):
    from twicc.core.services.session_timeline import record_event_sync

    a, b, c = projects
    source = tmp_path / "saved-message.jsonl"
    source.write_text("Original source text", encoding="utf-8")
    session = make_session(a, "saved-message", title="Saved session")
    Session.objects.filter(pk=session.pk).update(file_path=str(source))
    text = "    indented code\n**Saved Markdown**  \n" + "x" * 10000
    payload = {"text": text, "source_refs": [{"kind": "codex_item", "native_id": "saved-item", "turn_id": "turn"}],
               "source_line": 1, "storage_incarnation": None, "history_epoch": None}
    saved = record_event_sync(kind, session_id=session.id, project_id=a.id, actor="agent", data=payload,
                              occurred_at=T0 + timedelta(seconds=2))
    other = record_event_sync(kind, session_id="other-scope", project_id=c.id, actor="agent", data=payload,
                              occurred_at=T0 + timedelta(seconds=1))
    turn = make_event(project=a, session_id=session.id, at=0)
    source.write_text("Replaced source text", encoding="utf-8")
    params = {"project_ids": a.id} if scope == "project" else (
        {"project_ids": f"{a.id},{b.id}"} if scope == "workspace" else {})
    expected = [saved.id] if scope != "global" else [saved.id, other.id]
    assert ids(call(**params).json()) == [*expected, turn.id]
    for extra in ({"families": "messages"}, {"families": "messages", "after_id": 0}):
        body = call(**params, **extra).json()
        assert ids(body) == expected  # after_id orders by insertion id, independently of occurrence time
        event = next(event for event in body["events"] if event["id"] == saved.id)
        assert event["kind"] == kind and event["data"] == payload
        assert event["session_id"] == session.id and event["provider"] == "codex"
        assert event["occurred_at"] == "2026-10-14T10:00:02.000000Z"
        assert body["sessions"][session.id]["title"] == "Saved session"
    assert ids(call(**params, families="messages", session=session.id).json()) == [saved.id]


# -- Turns below a page (the browser draws turns as rails from the rows of a page) -------------------------


def test_turns_below_names_the_turn_started_under_the_page_for_a_session_with_no_boundary_on_it(projects):
    make_event("turn.started", project=projects[0], session_id="a", at=0)
    make_event("assistant_message.sent", project=projects[0], session_id="a", at=10)
    make_event("assistant_message.sent", project=projects[0], session_id="a", at=20)
    body = call(limit=2).json()
    assert body["has_more"] is True
    assert body["turns_below"] == {"a": "2026-10-14T10:00:00.000000Z"}


def test_turns_below_gives_the_start_of_a_turn_whose_end_is_the_oldest_boundary_of_the_page(projects):
    make_event("turn.started", project=projects[0], session_id="a", at=0)
    make_event("assistant_message.sent", project=projects[0], session_id="a", at=10)
    make_event("turn.ended", project=projects[0], session_id="a", at=20)
    make_event("assistant_message.sent", project=projects[0], session_id="a", at=30)
    body = call(limit=2).json()
    assert [event["kind"] for event in body["events"]] == ["assistant_message.sent", "turn.ended"]
    assert body["turns_below"] == {"a": "2026-10-14T10:00:00.000000Z"}


def test_turns_below_is_empty_for_a_session_outside_a_turn_or_whose_start_is_on_the_page(projects):
    # `a`: its last boundary below the page is an end (idle). `b`: the start of its turn is on the page.
    make_event("turn.ended", project=projects[0], session_id="a", at=0)
    make_event("turn.started", project=projects[0], session_id="b", at=5)
    make_event("assistant_message.sent", project=projects[0], session_id="a", at=10)
    make_event("assistant_message.sent", project=projects[0], session_id="b", at=20)
    body = call(limit=3).json()
    assert [event["session_id"] for event in body["events"]] == ["b", "a", "b"]
    assert body["turns_below"] == {}


def test_turns_below_is_empty_on_the_last_page_and_on_live_responses(projects):
    make_event("turn.started", project=projects[0], session_id="a", at=0)
    make_event("assistant_message.sent", project=projects[0], session_id="a", at=10)
    assert call().json()["turns_below"] == {}
    first = make_event("assistant_message.sent", project=projects[0], session_id="a", at=20)
    assert "turns_below" not in call(after_id=first.id - 1).json()


def test_turns_below_looks_for_the_nearest_boundary_before_the_oldest_row_of_the_page_only(projects):
    # Two earlier turns of `a`: only the nearest boundary counts, here the end of the first one.
    make_event("turn.started", project=projects[0], session_id="a", at=0)
    make_event("turn.ended", project=projects[0], session_id="a", at=5)
    make_event("assistant_message.sent", project=projects[0], session_id="a", at=10)
    body = call(limit=1).json()
    assert body["turns_below"] == {}


def test_turns_below_costs_one_indexed_read_per_session_that_needs_it(projects):
    for name in ("a", "b", "c"):
        make_event("turn.started", project=projects[0], session_id=name, at=0)
    make_event("assistant_message.sent", project=projects[0], session_id="a", at=10)
    make_event("turn.started", project=projects[0], session_id="b", at=11)
    make_event("assistant_message.sent", project=projects[0], session_id="c", at=12)
    # The view runs in another thread: read the statements of the service it calls, in this one.
    with CaptureQueriesContext(connection) as captured:
        body = timeline_feed.fetch_feed(parse_request({"limit": "3"}))
    assert body["has_more"] is True
    reads = [query["sql"] for query in captured.captured_queries if "LIMIT 1" in query["sql"] and "core_timelineevent" in query["sql"] and "turn.ended" in query["sql"]]
    # `b` has its start on the page: no read. `a` and `c` need one each.
    assert len(reads) == 2, reads
