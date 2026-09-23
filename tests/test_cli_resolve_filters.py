"""Tests for the CLI filter resolvers in ``twicc.cli._drop_request.whoami``.

Also covers the downstream ``--spawn-tree`` filter applied by the CLI
``sessions`` / ``processes`` / ``search`` subcommands — the resolver and the
filter together must return the whole spawn tree, including standalone
sessions queried by their own id (the same single-node fallback that
``twicc topology`` already implements).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import orjson
import pytest
from django.utils import timezone

from twicc.cli._drop_request.whoami import resolve_spawn_tree_filter
from twicc.cli._session_scope import merge_session_scope_ids
from twicc.core.models import Project, Session, SessionType


@pytest.fixture(autouse=True)
def _before_the_pagination_cutover(monkeypatch):
    """Pin the clock below ``LISTING_CUTOVER``.

    These tests assert the pre-cutover shape (a bare array, and the per-command
    default page size). Past the date both change, so without this they would go
    red on 2026-10-01 for a reason that has nothing to do with what they cover.
    The pinned value is naive, like the constant it replaces.
    """
    from twicc.cli import _output

    monkeypatch.setattr(_output, "LISTING_CUTOVER", datetime(2200, 1, 1))  # noqa: DTZ001


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-twicc-resolve-filters",
        directory="/tmp/twicc-resolve-filters",
    )


def make_session(project, session_id, *, spawned_by=None, spawn_root=None, minutes=0):
    now = timezone.now() + timedelta(minutes=minutes)
    return Session.objects.create(
        id=session_id,
        project=project,
        provider="codex",
        file_path=f"{session_id}.jsonl",
        type=SessionType.SESSION,
        title=session_id,
        created_at=now,
        last_new_content_at=now,
        user_message_count=1,
        spawned_by=spawned_by,
        spawn_root=spawn_root,
    )


def test_resolve_spawn_tree_filter_returns_root_id_for_non_root_session(project):
    """Regression: a non-root session id must resolve to its tree's root id.

    Every session in a tree carries ``spawn_root_id`` pointing to the actual
    root (backfilled on the root itself when it first spawns a child). The
    downstream filter ``qs.filter(spawn_root_id=X)`` therefore only returns
    the full tree when ``X`` is the real root id. So the resolver must
    translate any session id passed by the user to the id of its tree's root
    — not just for ``"self"``, but for arbitrary ids too.
    """
    root = make_session(project, "A", minutes=0)
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    middle = make_session(project, "B", spawned_by=root, spawn_root=root, minutes=1)
    make_session(project, "C", spawned_by=middle, spawn_root=root, minutes=2)

    assert resolve_spawn_tree_filter("B") == "A"


def test_resolve_spawn_tree_filter_rejects_parent():
    with pytest.raises(RuntimeError, match="--spawn-tree parent is not supported"):
        resolve_spawn_tree_filter("parent")


def test_merge_session_scope_ids_keeps_explicit_ids_first_and_appends_scope(project):
    root = make_session(project, "A", minutes=0)
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    b = make_session(project, "B", spawned_by=root, spawn_root=root, minutes=1)
    c = make_session(project, "C", spawned_by=root, spawn_root=root, minutes=2)

    ids = merge_session_scope_ids([b.id, "UNKNOWN"], spawned_by=root.id)

    assert ids == [b.id, "UNKNOWN", c.id]


def test_sessions_cli_returns_standalone_session_filtered_by_its_own_spawn_tree(
    project, capsysbinary,
):
    """Regression: ``twicc sessions --spawn-tree <standalone-id>`` must return
    the session itself.

    A session that has never spawned a child carries ``spawn_root_id=NULL`` —
    the backfill in ``session_creation.py`` only sets it on the root once the
    root first spawns. The resolver correctly resolves the value to that
    session's own id, but the downstream ``qs.filter(spawn_root_id=X)`` then
    excludes the very row whose id is ``X`` (since its column is NULL). The
    fix mirrors ``twicc topology`` and uses ``Q(spawn_root_id=X) | Q(pk=X)``.
    """
    make_session(project, "STANDALONE", minutes=0)
    sanity = Session.objects.get(pk="STANDALONE")
    assert sanity.spawn_root_id is None

    from twicc.cli import sessions as cli_sessions

    cli_sessions.main(spawn_tree="STANDALONE")

    out = capsysbinary.readouterr().out
    rows = orjson.loads(out)
    assert {r["id"] for r in rows} == {"STANDALONE"}
