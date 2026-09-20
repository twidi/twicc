"""The selection ``sessions stop`` and ``sessions wait-reply`` share.

Extracted from ``sessions stop`` when the wait copied it, because the two had
already drifted apart. The extraction moved code neither command's tests
covered: the ``self`` / ``parent`` resolution and the order ids come back in
were claimed by a docstring and a help string, and pinned by nothing. One
unpinned resolver behind two commands is worse than two — ``sessions stop
self`` stopping the *parent* is the version of that bug nobody survives.
"""

from __future__ import annotations

import pytest
import typer

from twicc.cli._session_selection import reject_conflicting_scopes, resolve_explicit_ids


class _Current:
    id = "me"
    spawned_by_id = "my-parent"


@pytest.fixture
def inside_a_session(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: _Current(),
    )


@pytest.fixture
def outside_any_session(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None,
    )


def test_the_order_named_is_the_order_returned():
    """A batch result is read against the input, so the first id named stays
    the first reported. Reversing it survived both commands' suites."""
    assert resolve_explicit_ids(["b", "a", "c"]) == ["b", "a", "c"]


def test_a_repeated_id_is_kept_once_at_its_first_position():
    assert resolve_explicit_ids(["b", "a", "b"]) == ["b", "a"]


def test_self_is_the_current_session(inside_a_session):
    """And not its parent. Swapping the two survived the whole suite, which
    would make `sessions stop self` stop the session that spawned you."""
    assert resolve_explicit_ids(["self"]) == ["me"]


def test_parent_is_the_session_that_spawned_it(inside_a_session):
    assert resolve_explicit_ids(["parent"]) == ["my-parent"]


def test_the_keywords_mix_with_plain_ids_and_keep_their_place(inside_a_session):
    assert resolve_explicit_ids(["x", "self", "y"]) == ["x", "me", "y"]


@pytest.mark.parametrize("keyword", ["self", "parent"])
def test_a_keyword_outside_a_session_is_refused(outside_any_session, capsysbinary, keyword):
    with pytest.raises(typer.Exit) as exc:
        resolve_explicit_ids([keyword])

    assert exc.value.exit_code == 1
    assert b"TwiCC session in the process" in capsysbinary.readouterr().err


def test_parent_on_a_session_nobody_spawned_is_refused(monkeypatch, capsysbinary):
    class _Orphan:
        id = "me"
        spawned_by_id = None

    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: _Orphan(),
    )

    with pytest.raises(typer.Exit) as exc:
        resolve_explicit_ids(["parent"])

    assert exc.value.exit_code == 1
    assert b"no spawner" in capsysbinary.readouterr().err


def test_nothing_named_is_nothing_resolved():
    assert resolve_explicit_ids([]) == []
    assert resolve_explicit_ids(None) == []


@pytest.mark.parametrize("kwargs, expected", [
    ({"spawned_by": "a", "descendants": "b"}, b"--spawned-by and --descendants"),
    ({"spawn_tree": "a", "siblings": "b"}, b"--spawn-tree and --siblings"),
    ({"spawned_by": "a", "spawn_tree": "b", "siblings": "c"},
     b"--spawned-by and --spawn-tree and --siblings"),
])
def test_conflicting_scopes_are_named_in_the_error(capsysbinary, kwargs, expected):
    """Which two, not "some two": a caller passing three has to be told all of
    them, and the message is the only place that says so."""
    with pytest.raises(typer.Exit) as exc:
        reject_conflicting_scopes(
            kwargs.get("spawned_by"), kwargs.get("spawn_tree"),
            kwargs.get("descendants"), kwargs.get("siblings"),
        )

    assert exc.value.exit_code == 1
    assert expected in capsysbinary.readouterr().err


def test_one_scope_alone_passes():
    reject_conflicting_scopes("a", None, None, None)


def test_project_and_workspace_are_mutually_exclusive(capsysbinary):
    with pytest.raises(typer.Exit) as exc:
        reject_conflicting_scopes(None, None, None, None, project="p", workspace="w")

    assert exc.value.exit_code == 1
    assert b"--project and --workspace" in capsysbinary.readouterr().err
