"""Tests for annotation-filtered search.

The filter lives on ``Session.annotations``, which the index knows nothing
about. It is resolved against the database first and injected as an index
clause, so ``total_hits`` counts the hits that really match — the previous
post-filter loop could only report the count taken *before* filtering.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from twicc import search as search_mod
from twicc.cli._annotation_filters import parse_annotation_filter
from twicc.core.models import Project, Session, SessionType


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


@pytest.fixture
def corpus(index, db):
    """Ten sessions, three of them annotated, one indexed message each."""
    project = Project.objects.create(
        id="-tmp-twicc-annscope", directory="/tmp/twicc-annscope",
    )
    now = timezone.now()
    for i in range(10):
        session = Session.objects.create(
            id=f"ann-{i}",
            project=project,
            provider="claude_code",
            file_path=f"ann-{i}.jsonl",
            type=SessionType.SESSION,
            created_at=now,
            user_message_count=1,
            annotations={"role": "worker"} if i < 3 else {},
        )
        search_mod.index_document(
            session_id=session.id,
            project_id=project.id,
            line_num=1,
            body="pagination envelope",
            from_role="user",
            timestamp=now,
            archived=False,
        )
    search_mod.commit()
    return project


def run(spec, **kwargs):
    filters = [parse_annotation_filter(spec)] if spec else None
    return search_mod.raw_search(
        "pagination", to_json=False, annotation_filters=filters, **kwargs,
    )


def test_unfiltered_search_sees_the_whole_corpus(corpus):
    assert run(None)["total_hits"] == 10


def test_total_hits_counts_only_the_annotated_sessions(corpus):
    result = run("role=worker")
    assert result["total_hits"] == 3
    assert len(result["hits"]) == 3
    assert {h["session_id"] for h in result["hits"]} == {"ann-0", "ann-1", "ann-2"}


def test_a_negative_operator_returns_the_complement(corpus):
    result = run("role:not-exists")
    assert result["total_hits"] == 7
    assert {h["session_id"] for h in result["hits"]} == {f"ann-{i}" for i in range(3, 10)}


def test_a_filter_matching_nothing_returns_nothing(corpus):
    result = run("role=nobody")
    assert result["total_hits"] == 0
    assert result["hits"] == []
    assert result["annotation_filtered"] is True


def test_total_hits_is_independent_of_the_page_size(corpus):
    """The regression this fixes: a small page used to report the pre-filter count."""
    assert run("role=worker", limit=1)["total_hits"] == 3
    assert len(run("role=worker", limit=1)["hits"]) == 1


def test_exhausted_tracks_whether_the_page_reaches_the_end(corpus):
    assert run("role=worker", limit=1)["exhausted"] is False
    assert run("role=worker", limit=3)["exhausted"] is True
    # ``partial`` is kept for shape compatibility and never trips any more.
    assert run("role=worker", limit=1)["partial"] is False


def test_the_scope_resolver_picks_the_smaller_side(corpus):
    """Positive filters inject the match set; negative ones inject its complement,
    which keeps the boolean query small when nearly every session matches."""
    positive = search_mod.resolve_annotation_scope([parse_annotation_filter("role=worker")])
    assert positive.include is not None and len(positive.include) == 3
    assert positive.exclude is None

    negative = search_mod.resolve_annotation_scope([parse_annotation_filter("role:not-exists")])
    assert negative.exclude is not None and len(negative.exclude) == 3
    assert negative.include is None
