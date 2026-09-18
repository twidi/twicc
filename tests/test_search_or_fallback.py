"""Tests for the AND → OR fallback in the UI-facing ``search()``.

The strict pass is per DOCUMENT, and a document is one message: "all terms"
means "all terms in the same message". A multi-word query therefore returns
nothing far more often than a reader expects — including for sessions that
plainly cover every term, just across several messages. ``MATCH_MODE_AUTO``
retries such a query disjunctively and reports which pass answered.

These are the first tests ``search()`` has ever had; the sibling
``test_search_annotation_scope.py`` only exercises ``raw_search``.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlencode

import pytest
from django.test import AsyncClient
from django.utils import timezone

from twicc import search as search_mod
from twicc.core.models import Project, Session, SessionType

PROJECT_ID = "-tmp-twicc-orfallback"
OTHER_PROJECT_ID = "-tmp-twicc-orfallback-other"


@pytest.fixture
def index(tmp_path, monkeypatch, db):
    from twicc import paths

    # ``init_search_index`` imports it from ``twicc.paths`` at call time.
    monkeypatch.setattr(paths, "get_search_dir", lambda: tmp_path / "search-index")
    search_mod.init_search_index()
    try:
        yield
    finally:
        search_mod.shutdown_search_index()


def make_session(project, session_id, messages, *, archived=False):
    """Create a session and index one document per ``(line_num, role, body)``."""
    now = timezone.now()
    Session.objects.create(
        id=session_id,
        project=project,
        provider="claude_code",
        file_path=f"{session_id}.jsonl",
        type=SessionType.SESSION,
        created_at=now,
        archived=archived,
        user_message_count=len(messages),
    )
    for line_num, role, body in messages:
        search_mod.index_document(
            session_id=session_id,
            project_id=project.id,
            line_num=line_num,
            body=body,
            from_role=role,
            timestamp=now,
            archived=archived,
        )


@pytest.fixture
def corpus(index, db):
    """Four sessions covering every shape the fallback has to distinguish.

    - ``both-in-one``  — "alpha beta" in a single message: the strict pass hits.
    - ``split``        — "alpha" and "beta" in two different messages of the
                         same session: the strict pass misses it entirely.
    - ``alpha-only`` / ``beta-only`` — one term each, in distinct sessions.
    """
    project = Project.objects.create(id=PROJECT_ID, directory="/tmp/twicc-orfallback")
    make_session(project, "both-in-one", [(1, "user", "alpha beta together here")])
    make_session(project, "split", [(1, "user", "alpha lives here"), (2, "user", "beta lives there")])
    make_session(project, "alpha-only", [(1, "user", "alpha on its own")])
    make_session(project, "beta-only", [(1, "assistant", "beta on its own")])
    search_mod.commit()
    return project


def run(query, **kwargs):
    return search_mod.search(query, **kwargs)


def session_ids(results):
    return {sr.session_id for sr in results.results}


class CountingIndex:
    """Wraps the real index to record how each query was parsed."""

    def __init__(self, inner):
        self.inner = inner
        self.conjunctions = []

    def parse_query(self, *args, **kwargs):
        self.conjunctions.append(kwargs.get("conjunction_by_default"))
        return self.inner.parse_query(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture
def counting_index(corpus):
    """Swap the module's index for a counting wrapper, for this test only.

    Restored by hand rather than with ``monkeypatch``: the ``index`` fixture
    sets ``monkeypatch`` up first, so its finalizer would run *after*
    ``shutdown_search_index()`` and write the real index back into a global
    that shutdown had just cleared — leaving ``is_initialized()`` true with no
    writer and no schema for whatever runs next.
    """
    original = search_mod._index
    counter = CountingIndex(original)
    search_mod._index = counter
    try:
        yield counter
    finally:
        if search_mod._index is counter:
            search_mod._index = original


# ---------------------------------------------------------------------------
# The two passes
# ---------------------------------------------------------------------------


def test_strict_pass_answers_when_one_message_holds_every_term(corpus):
    results = run("alpha beta")
    assert results.match_mode == search_mod.MATCH_MODE_ALL
    assert session_ids(results) == {"both-in-one"}


def test_terms_split_across_messages_of_one_session_need_the_fallback(corpus):
    """The per-message semantics, pinned: 'split' holds both terms, in two
    messages, and the strict pass cannot see it."""
    strict = run("alpha beta", match_mode=search_mod.MATCH_MODE_ALL)
    assert "split" not in session_ids(strict)

    results = run("alpha beta zeta")  # zeta is absent, so the strict pass returns nothing
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert "split" in session_ids(results)


def test_fallback_returns_every_session_matching_at_least_one_term(corpus):
    results = run("alpha zeta")
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert session_ids(results) == {"both-in-one", "split", "alpha-only"}


def test_a_multi_token_query_with_strict_hits_is_not_widened(counting_index):
    """Guards the retry condition: hits on the strict pass must stop it, and
    stop it before the second index query, not just before the relabel."""
    results = run("alpha beta")
    assert results.match_mode == search_mod.MATCH_MODE_ALL
    assert session_ids(results) == {"both-in-one"}
    assert counting_index.conjunctions == [True]


def test_pagination_walks_the_widened_result_set(corpus):
    """The frontend pins the mode across pages, so the slice has to behave the
    same on a widened pass as on a strict one."""
    first = run("alpha zeta", limit=1)
    second = run("alpha zeta", limit=1, offset=1)

    assert first.match_mode == second.match_mode == search_mod.MATCH_MODE_ANY
    assert first.total_sessions == second.total_sessions == 3
    assert len(first.results) == len(second.results) == 1
    assert first.results[0].session_id != second.results[0].session_id


# ---------------------------------------------------------------------------
# What must NOT widen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["alpha AND zeta", "+alpha +zeta", '"alpha zeta"'])
def test_explicit_operators_never_widen_silently(corpus, query):
    """``AND``, ``+`` and quoted phrases are immune to conjunction_by_default,
    so the retry runs, returns nothing, and must not relabel the result."""
    results = run(query)
    assert results.results == []
    assert results.total_sessions == 0
    assert results.match_mode == search_mod.MATCH_MODE_ALL


def test_a_bare_token_mixed_with_an_explicit_one_does_widen(corpus):
    """The accepted consequence of the above: only clauses carrying no explicit
    prefix are governed by the mode, so one bare token widens the whole query."""
    results = run("+alpha zeta")
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert session_ids(results) == {"both-in-one", "split", "alpha-only"}


def test_a_single_token_query_is_never_retried(counting_index):
    """Strict and loose parse identically for one token, so a second pass would
    burn an index query for nothing. Counted, not assumed."""
    results = run("zeta")
    assert results.results == []
    assert results.match_mode == search_mod.MATCH_MODE_ALL
    assert counting_index.conjunctions == [True]


def test_a_multi_token_query_with_no_strict_hits_is_parsed_twice(counting_index):
    """The other side of the guard: more than one token, so the retry runs."""
    results = run("alpha zeta")
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert counting_index.conjunctions == [True, False]


def test_a_punctuation_joined_single_token_is_not_retried_either(counting_index):
    """``alpha,zeta`` is one whitespace token, and the parser turns it into a
    phrase query under both settings — so the guard skips it too. Counted,
    because the mode alone would read the same with or without the retry."""
    results = run("alpha,zeta")
    assert results.results == []
    assert results.match_mode == search_mod.MATCH_MODE_ALL
    assert counting_index.conjunctions == [True]


def test_forcing_all_never_parses_twice(counting_index):
    """An explicit strict mode must not pay for a retry, even with zero hits."""
    results = run("alpha zeta", match_mode=search_mod.MATCH_MODE_ALL)
    assert results.results == []
    assert counting_index.conjunctions == [True]


# ---------------------------------------------------------------------------
# Filters apply to both passes
# ---------------------------------------------------------------------------


def test_the_fallback_still_honours_the_from_role_filter(corpus):
    """``beta-only`` holds the term in an assistant message; a user-scoped
    search must not surface it even once the query is widened."""
    results = run("alpha zeta", from_role="user")
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert "beta-only" not in session_ids(results)

    results = run("beta zeta", from_role="user")
    assert "beta-only" not in session_ids(results)


def test_the_fallback_works_with_no_filter_clause_at_all(corpus):
    """Lifting every implicit filter leaves the query with no clause to AND
    against. The widened pass must still answer in that configuration."""
    results = run("alpha zeta", include_archived=True, include_hidden=True)
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert session_ids(results) == {"both-in-one", "split", "alpha-only"}


def test_the_fallback_still_honours_the_project_filter(index, db):
    project = Project.objects.create(id=PROJECT_ID, directory="/tmp/twicc-orfallback")
    other = Project.objects.create(id=OTHER_PROJECT_ID, directory="/tmp/twicc-orfallback-other")
    make_session(project, "in-scope", [(1, "user", "alpha here")])
    make_session(other, "out-of-scope", [(1, "user", "alpha elsewhere")])
    search_mod.commit()

    results = run("alpha zeta", project_id=PROJECT_ID)
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert session_ids(results) == {"in-scope"}


# ---------------------------------------------------------------------------
# In-session search (the find bar's call shape)
# ---------------------------------------------------------------------------


@pytest.fixture
def in_session_corpus(index, db):
    """One session whose TITLE holds both terms and whose messages hold one each.

    ``session_id`` search excludes title documents, and that exclusion is the
    only ``MustNot`` clause in the list — so it is the one most likely to be
    lost when the widened pass rebuilds the query.
    """
    project = Project.objects.create(id=PROJECT_ID, directory="/tmp/twicc-orfallback")
    make_session(project, "in-session", [(1, "user", "alpha in a message")])
    search_mod.index_document(
        session_id="in-session",
        project_id=project.id,
        line_num=0,
        body="alpha zeta in the title",
        from_role="title",
        timestamp=timezone.now(),
        archived=False,
    )
    search_mod.commit()
    return project


def test_the_widened_in_session_search_still_excludes_the_title(in_session_corpus):
    results = run("alpha zeta", session_id="in-session", include_archived=True)
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert [(m.line_num, m.from_role) for m in results.results[0].matches] == [(1, "user")]


def test_an_explicit_title_role_still_reaches_the_title(in_session_corpus):
    """The exclusion is conditional on ``from_role is None``; asking for titles
    explicitly must keep working. Here the title holds both terms, so the strict
    pass answers — which is the point: the refactor must not have dropped the
    conditional."""
    results = run(
        "alpha zeta", session_id="in-session", from_role="title", include_archived=True
    )
    assert results.match_mode == search_mod.MATCH_MODE_ALL  # the title holds both terms
    assert [m.line_num for m in results.results[0].matches] == [0]


# ---------------------------------------------------------------------------
# Forced modes
# ---------------------------------------------------------------------------


def test_forcing_all_keeps_the_historical_behaviour(corpus):
    results = run("alpha zeta", match_mode=search_mod.MATCH_MODE_ALL)
    assert results.results == []
    assert results.match_mode == search_mod.MATCH_MODE_ALL


def test_forcing_any_skips_the_strict_pass(counting_index):
    """Not just "widens": the strict pass must not run at all, or pinning the
    mode while paginating would cost an extra index query per page."""
    results = run("alpha beta", match_mode=search_mod.MATCH_MODE_ANY)
    assert results.match_mode == search_mod.MATCH_MODE_ANY
    assert session_ids(results) == {"both-in-one", "split", "alpha-only", "beta-only"}
    assert counting_index.conjunctions == [False]


def test_forcing_any_reports_any_even_with_no_hits(corpus):
    """The reported mode describes the pass that ran, not the outcome."""
    results = run("zeta eta", match_mode=search_mod.MATCH_MODE_ANY)
    assert results.results == []
    assert results.match_mode == search_mod.MATCH_MODE_ANY


def test_forcing_any_on_an_unparseable_query_reports_any(corpus):
    """Asymmetric with the AUTO path, which never got to run a loose pass."""
    results = run("alpha'zeta", match_mode=search_mod.MATCH_MODE_ANY)
    assert results.results == []
    assert results.match_mode == search_mod.MATCH_MODE_ANY


def test_an_unparseable_query_on_auto_reports_all(corpus):
    results = run("alpha'zeta")
    assert results.results == []
    assert results.match_mode == search_mod.MATCH_MODE_ALL


def test_an_empty_filiation_scope_reports_the_requested_mode(corpus):
    """The short-circuit runs before any pass, so it must still not claim
    a strict pass answered when the caller asked for a loose one."""
    assert run("alpha beta", descendants=set()).match_mode == search_mod.MATCH_MODE_ALL
    assert run(
        "alpha beta", descendants=set(), match_mode=search_mod.MATCH_MODE_ANY
    ).match_mode == search_mod.MATCH_MODE_ANY


def test_an_unknown_match_mode_is_rejected(corpus):
    with pytest.raises(ValueError, match="unknown match_mode"):
        run("alpha beta", match_mode="bogus")


# ---------------------------------------------------------------------------
# Snippets
# ---------------------------------------------------------------------------


def test_fallback_snippets_highlight_the_term_that_matched(corpus):
    """A widened result must not come back as plain, unhighlighted text — the UI
    renders these snippets with ``v-html``. Asserting the exact markup on a
    session that matched only one of the two terms, so the highlight has to come
    from the term that actually hit."""
    results = run("alpha zeta")
    assert results.match_mode == search_mod.MATCH_MODE_ANY

    by_id = {sr.session_id: sr for sr in results.results}
    assert "<b>alpha</b>" in by_id["alpha-only"].matches[0].snippet
    assert all("<b>" in match.snippet for sr in results.results for match in sr.matches)


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def http_corpus(index, transactional_db):
    """Same shape as ``corpus``, on the transactional DB the async view needs."""
    project = Project.objects.create(id=PROJECT_ID, directory="/tmp/twicc-orfallback")
    make_session(project, "both-in-one", [(1, "user", "alpha beta together here")])
    make_session(project, "alpha-only", [(1, "user", "alpha on its own")])
    search_mod.commit()
    return project


def get(**params):
    return asyncio.run(AsyncClient().get("/api/search/?" + urlencode(params)))


def test_the_endpoint_rejects_an_unknown_match_mode(http_corpus):
    response = get(q="alpha", match_mode="bogus")
    assert response.status_code == 400
    assert "match_mode" in response.json()["error"]


def test_the_endpoint_echoes_the_mode_that_answered(http_corpus):
    strict = get(q="alpha beta")
    assert strict.status_code == 200
    assert strict.json()["match_mode"] == "all"

    widened = get(q="alpha zeta")
    assert widened.status_code == 200
    payload = widened.json()
    assert payload["match_mode"] == "any"
    assert {r["session_id"] for r in payload["results"]} == {"both-in-one", "alpha-only"}


def test_an_empty_match_mode_means_auto(http_corpus):
    response = get(q="alpha zeta", match_mode="")
    assert response.status_code == 200
    assert response.json()["match_mode"] == "any"


def test_the_endpoint_honours_a_pinned_all(http_corpus):
    """The mode the frontend pins while paginating must reach ``search()``.
    Auto would widen this query; pinned, it must stay empty."""
    response = get(q="alpha zeta", match_mode="all")
    assert response.status_code == 200
    payload = response.json()
    assert payload["match_mode"] == "all"
    assert payload["results"] == []


def test_the_endpoint_honours_a_pinned_any(http_corpus):
    """The other direction: auto would answer "all" here."""
    response = get(q="alpha beta", match_mode="any")
    assert response.status_code == 200
    payload = response.json()
    assert payload["match_mode"] == "any"
    assert {r["session_id"] for r in payload["results"]} == {"both-in-one", "alpha-only"}
