"""Filters of ``GET /api/timeline/`` (filters design 2026-10-26, sections 4.5, 6 and 7)."""

import pytest

from tests.test_timeline_api import call, ids, make_event, make_instance_event
from twicc.core.models import Project
from twicc.core.services import timeline_feed
from twicc.core.services.timeline_feed import parse_request
from twicc.core.services.timeline_filters import AUTHORS, FAMILIES, TimelineFilter

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def projects():
    return [Project.objects.create(id=f"-tl-flt-{name}", directory=f"/tmp/tl-flt-{name}") for name in "ab"]


def make_unclean_end(at, **fields):
    """An instance ``backend.ended`` that says the backend did not stop cleanly (a problem)."""
    return make_event(
        "backend.ended", project=None, session_id=None, at=at, provider=None, actor="system",
        data={"clean": False}, **fields,
    )


def next_page(params, body):
    last = body["events"][-1]
    return call(**params, before=f"{last['occurred_at']},{last['id']}").json()


# -- Parameters ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("params", [
    {"view": "x"},
    {"families": "turns,nope"},
    {"authors": "robot"},
    {"provider": "codex,gemini"},
])
def test_unknown_filter_values_answer_400(params):
    response = call(**params)
    assert response.status_code == 400
    error = response.json()["error"]
    assert isinstance(error, str) and next(iter(params)) in error


def test_every_value_of_a_group_is_the_same_as_none():
    assert parse_request({"families": ",".join(FAMILIES)}).filter.families == ()
    assert parse_request({"authors": ",".join(AUTHORS)}).filter.authors == ()
    assert parse_request({"provider": "claude_code,codex"}).filter.providers == ()


def test_filter_lists_drop_empty_items_collapse_duplicates_and_keep_the_order():
    request = parse_request({
        "families": " waits,,turns,waits ", "authors": "you,,you", "provider": "codex,", "view": "without_turns",
        "session": " s1 ",
    })
    assert request.filter == TimelineFilter(
        view="without_turns", families=("waits", "turns"), authors=("you",), providers=("codex",), session_id="s1",
    )
    assert parse_request({}).filter == TimelineFilter()
    assert parse_request({"view": "", "families": ",", "session": " "}).filter == TimelineFilter()


# -- The first page --------------------------------------------------------------------------------------------


def test_the_view_and_the_groups_filter_the_first_page():
    turn = make_event("turn.started", at=1)
    wait = make_event("awaiting_input.started", at=2)
    wait_end = make_event("awaiting_input.ended", at=3)
    lost = make_event("process.ended", at=4, actor="system", reason="lost")
    clean_end = make_event("process.ended", at=5, actor="system", reason="idle_timeout")
    mine = make_event("settings.changed", at=6, actor="user", via="ui")
    title = make_event("title.changed", at=7, actor="system", data={"source": "auto", "new": "N", "old": None})
    unclean = make_unclean_end(at=8)

    user = make_event("user_message.sent", at=9, actor="user", via="ui")
    assistant = make_event("assistant_message.sent", at=10)
    turn_end = make_event("turn.ended", at=11)
    every = [turn_end, assistant, user, unclean, title, mine, clean_end, lost, wait_end, wait, turn]
    assert ids(call().json()) == [row.id for row in every]
    assert ids(call(view="messages").json()) == [turn_end.id, assistant.id, user.id, turn.id]
    assert ids(call(view="without_turns").json()) == [
        unclean.id, title.id, mine.id, clean_end.id, lost.id, wait_end.id, wait.id,
    ]
    assert ids(call(view="waits_problems").json()) == [unclean.id, lost.id, wait_end.id, wait.id]
    assert ids(call(view="waits_problems", families="processes,twicc").json()) == [unclean.id, lost.id]
    assert ids(call(authors="you").json()) == [user.id, mine.id]
    assert ids(call(families="turns,waits").json()) == [turn_end.id, wait_end.id, wait.id, turn.id]
    assert ids(call(families="turns", authors="you").json()) == []


def test_a_filter_with_no_match_answers_an_empty_page(projects):
    make_event(project=projects[0], at=1)
    body = call(families="organization", project_ids=projects[0].id).json()
    assert body["events"] == [] and body["has_more"] is False and body["sessions"] == {}


def test_has_more_speaks_of_matching_events():
    make_event("process.started", at=1)
    make_event("process.started", at=2)
    for index in range(5):
        make_event("turn.started", at=10 + index)
    body = call(families="processes", limit=2).json()
    assert len(body["events"]) == 2 and body["has_more"] is False
    assert call(families="processes", limit=1).json()["has_more"] is True


def test_rows_without_a_provider_are_kept_once_across_pages(projects):
    a = projects[0]
    first = make_event(project=a, at=1, provider="codex")
    make_event(project=a, at=2, provider="claude_code")
    unnamed = make_event(project=a, session_id="s9", at=3, provider=None)
    boot = make_instance_event(at=4)
    last = make_event(project=a, at=5, provider="codex")
    params = {"provider": "codex", "limit": 2, "project_ids": a.id}
    page_one = call(**params).json()
    assert ids(page_one) == [last.id, boot.id] and page_one["has_more"] is True
    page_two = next_page(params, page_one)
    assert ids(page_two) == [unnamed.id, first.id] and page_two["has_more"] is False


@pytest.mark.parametrize("scoped", [False, True])
def test_every_query_of_a_page_applies_the_filter(projects, scoped):
    # The main, the instance and the null-provider queries each get the predicate: a row on any of them
    # that does not match is dropped.
    a = projects[0]
    turn = make_event("turn.started", project=a, at=1, provider="codex")
    make_event("process.started", project=a, at=2, provider="codex")          # main query, no match
    make_instance_event(at=3)                                                  # instance query, no match
    make_event("process.started", project=a, session_id="s9", at=4, provider=None)  # null-provider, no match
    unnamed_turn = make_event("turn.started", project=a, session_id="s9", at=5, provider=None)
    params = {"provider": "codex", "families": "turns"}
    if scoped:
        params["project_ids"] = a.id
    expected = [unnamed_turn.id, turn.id]
    assert ids(call(**params).json()) == expected
    assert ids(call(**params, limit=1).json()) == expected[:1]
    assert ids(call(**params, after_id=0).json()) == sorted(expected)


def test_rows_without_a_provider_are_kept_in_the_global_scope_too():
    codex = make_event(at=1, provider="codex")
    make_event(at=2, provider="claude_code")
    unnamed = make_event(session_id="s9", at=3, provider=None)
    boot = make_instance_event(at=4)
    assert ids(call(provider="codex").json()) == [boot.id, unnamed.id, codex.id]
    assert ids(call(provider="codex", limit=1).json()) == [boot.id]


@pytest.mark.parametrize("scope", [None, "a", "b"])
@pytest.mark.parametrize("provider", [None, "codex"])
def test_the_session_filter_keeps_that_session_and_drops_instance_rows(projects, scope, provider):
    a, b = projects
    codex = make_event(project=a, session_id="s2", at=1, provider="codex")
    unnamed = make_event(project=a, session_id="s2", at=2, provider=None)
    claude = make_event(project=a, session_id="s2", at=3, provider="claude_code")
    make_event(project=a, session_id="s1", at=4, provider="codex")
    make_event(project=b, session_id="s3", at=5, provider="codex")
    make_instance_event(at=6)
    params = {"session": "s2"}
    if scope:
        params["project_ids"] = {"a": a.id, "b": b.id}[scope]
    if provider:
        params["provider"] = provider
    expected = [] if scope == "b" else [claude, unnamed, codex] if not provider else [unnamed, codex]
    body = call(**params).json()
    assert ids(body) == [row.id for row in expected]
    assert all(event["session_id"] == "s2" for event in body["events"])
    after = call(**params, after_id=0).json()
    assert ids(after) == sorted(row.id for row in expected)


def test_the_session_filter_pages_with_before():
    rows = [make_event(session_id="s2", at=index) for index in range(5)]
    make_event(session_id="s1", at=2.5)
    params = {"session": "s2", "limit": 2}
    seen, body = [], call(**params).json()
    seen += ids(body)
    while body["has_more"]:
        body = next_page(params, body)
        seen += ids(body)
    assert seen == [row.id for row in reversed(rows)]


# -- after_id --------------------------------------------------------------------------------------------------


def test_after_id_applies_the_filter_after_the_scope_range(projects, monkeypatch):
    a, b = projects
    cursor = make_event(project=a, at=0)
    rows = [
        make_event("process.started" if index in (2, 5, 8) else "turn.started", project=a, at=index + 1)
        for index in range(10)
    ]
    newest = make_event("process.started", project=b, session_id="s9", at=99)  # out of the scope
    body = call(after_id=cursor.id, families="processes", project_ids=a.id).json()
    assert ids(body) == [rows[2].id, rows[5].id, rows[8].id]
    assert body["truncated"] is False and body["latest_id"] == newest.id
    assert body["has_more"] is False

    monkeypatch.setattr(timeline_feed, "AFTER_ID_CAP", 5)
    # Six scope rows after rows[3] (rows[4..9]): truncated, though only two of the first five match.
    body = call(after_id=rows[3].id, families="processes", project_ids=a.id).json()
    assert body["truncated"] is True and ids(body) == [rows[5].id, rows[8].id]
    # Five scope rows after rows[4]: exactly the cap, not truncated.
    body = call(after_id=rows[4].id, families="processes", project_ids=a.id).json()
    assert body["truncated"] is False and ids(body) == [rows[5].id, rows[8].id]
    assert call(after_id=rows[4].id, families="sessions", project_ids=a.id).json()["events"] == []


def test_after_id_keeps_rows_without_a_provider_under_a_provider_filter(projects):
    a, b = projects
    codex = make_event(project=a, at=1, provider="codex")
    make_event(project=a, at=2, provider="claude_code")
    unnamed = make_event(project=a, session_id="s9", at=3, provider=None)
    make_event(project=b, session_id="s8", at=4, provider=None)
    boot = make_instance_event(at=5)
    assert ids(call(after_id=0, provider="codex", project_ids=a.id).json()) == [codex.id, unnamed.id, boot.id]


# -- Instance rows ---------------------------------------------------------------------------------------------


def test_instance_rows_follow_the_twicc_family_and_the_authors():
    boot = make_instance_event(at=1)
    turn = make_event("turn.started", at=2)
    unclean = make_unclean_end(at=3, precision="estimated")
    assert ids(call(families="sessions").json()) == []
    assert ids(call(families="turns").json()) == [turn.id]
    assert ids(call(families="twicc").json()) == [unclean.id, boot.id]
    assert ids(call(authors="automatic").json()) == [boot.id]
    assert ids(call(authors="unknown").json()) == [unclean.id]
    assert ids(call(authors="automatic", after_id=0).json()) == [boot.id]


@pytest.mark.parametrize("params", [{}, {"provider": "codex"}, {"project_ids": "a"}, {"project_ids": "a,b"},
                                    {"project_ids": "a", "provider": "claude_code"}])
def test_the_twicc_family_reads_the_instance_rows_in_every_scope(projects, params):
    a, b = projects
    params = {**params}
    if "project_ids" in params:
        params["project_ids"] = ",".join({"a": a.id, "b": b.id}[key] for key in params["project_ids"].split(","))
    boot = make_instance_event(at=1)
    make_event("turn.started", project=a, at=2)
    make_event("process.started", project=b, session_id="s9", at=3, provider=None)
    unclean = make_unclean_end(at=4)
    older = make_instance_event(at=0)
    assert ids(call(**params, families="twicc").json()) == [unclean.id, boot.id, older.id]
    page = call(**params, families="twicc", limit=2).json()
    assert ids(page) == [unclean.id, boot.id] and page["has_more"] is True
    assert ids(next_page({**params, "families": "twicc", "limit": 2}, page)) == [older.id]
    assert ids(call(**params, families="twicc", view="waits_problems").json()) == [unclean.id]


@pytest.mark.parametrize("params", [{}, {"provider": "codex"}, {"project_ids": "a"}, {"project_ids": "a,b"},
                                    {"project_ids": "a", "provider": "codex"}])
def test_the_external_author_keeps_its_scope_and_provider(projects, params):
    a, b = projects
    params = {**params}
    if "project_ids" in params:
        params["project_ids"] = ",".join({"a": a.id, "b": b.id}[key] for key in params["project_ids"].split(","))
    external = {"actor": "external", "via": "mcp_external"}
    first = make_event("settings.changed", project=a, at=1, provider="codex", **external)
    make_event("settings.changed", project=a, at=2, provider="codex", actor="user", via="ui")
    other_provider = make_event("settings.changed", project=a, at=3, provider="claude_code", **external)
    # A session row no source could name a provider for: a provider filter keeps it (null-provider query).
    unnamed = make_event("settings.changed", project=a, session_id="s8", at=3.5, provider=None, **external)
    other_project = make_event("settings.changed", project=b, session_id="s9", at=4, provider="codex", **external)
    forged = make_event("settings.changed", project=a, at=5, provider="codex", actor="external", via="ui")
    last = make_event("archived.started", project=a, at=6, provider="codex", **external)
    expected = [last, other_project, unnamed, other_provider, first]
    if params.get("project_ids") == a.id:
        expected.remove(other_project)
    if "provider" in params:
        expected.remove(other_provider)
    assert unnamed in expected
    assert ids(call(**params, authors="external_mcp").json()) == [row.id for row in expected]
    seen, page = [], call(**params, authors="external_mcp", limit=1).json()
    assert ids(page) == [last.id] and page["has_more"] is True
    seen += ids(page)
    while page["has_more"]:
        page = next_page({**params, "authors": "external_mcp", "limit": 1}, page)
        seen += ids(page)
    # Each row once, in time order, across page boundaries (the null-provider row included).
    assert seen == [row.id for row in expected]
    assert forged.id in ids(call(**params, authors="unknown").json())


@pytest.mark.parametrize(("view", "selected"), [
    ("messages", (0, 1, 5, 6)),
    ("without_turns", (2, 3, 4, 7)),
    ("waits_problems", (3, 4, 7)),
])
def test_presets_keep_the_same_rows_when_paginating_and_fetching_live(view, selected):
    kinds = ("turn.started", "user_message.sent", "settings.changed", "awaiting_input.started",
             "awaiting_input.ended", "assistant_message.sent", "turn.ended", "process.ended")
    rows = [make_event(kind, at=index, reason="error" if kind == "process.ended" else "")
            for index, kind in enumerate(kinds)]
    expected = [rows[index].id for index in selected]
    params = {"view": view, "limit": 2}
    page = call(**params).json()
    seen = ids(page)
    for _ in range(len(rows)):
        if not page["has_more"]:
            break
        page = next_page(params, page)
        seen += ids(page)
    assert seen == expected[::-1]
    live = call(view=view, after_id=0).json()
    assert ids(live) == expected
    assert live["latest_id"] == rows[-1].id
