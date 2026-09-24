"""Batch lookups and `peers`: `items` from the date, `--paginated` before it."""

from __future__ import annotations

from datetime import datetime

import pytest

from twicc.cli import _output
from twicc.core.models import Project, Session, SessionType
from twicc.rpc.invoker import invoke

PAST = datetime(2000, 1, 1)     # noqa: DTZ001
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001

LOOKUPS = {
    "sessions get": ["sessions", "get", "lk-s", "--full"],
    # No leading dash: Click would read "-tmp-lk" as an option. The CLI re-adds
    # the dash (twicc-projects/SKILL.md), and no `--` either: `--paginated` is
    # appended after the id and would become a positional past `--`.
    "projects get": ["projects", "get", "tmp-lk"],
    "workspaces get": ["workspaces", "get", "nope-ws"],
    "peers": ["peers"],
}


@pytest.fixture(autouse=True)
def data(db, monkeypatch):
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)
    monkeypatch.setattr("twicc.cli.sessions_get._PLACEHOLDER_TEMPLATE", None)
    project = Project.objects.create(id="-tmp-lk", directory="/tmp/lk")
    Session.objects.create(
        id="lk-s", project=project, provider="claude_code", file_path="s.jsonl",
        type=SessionType.SESSION,
    )


@pytest.mark.parametrize("name", LOOKUPS)
def test_before_the_current_shape_and_one_notice(monkeypatch, name):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    result = invoke(LOOKUPS[name])
    assert result.exit_code == 0, result.error
    assert len(result.warnings) == 1
    if name == "peers":
        assert set(result.result) == {"peers"}
        assert "under `items` instead of `peers`" in result.warnings[0]
    else:
        assert isinstance(result.result, list)
        assert f"`{name}` returns" in result.warnings[0]
    assert "pages at" not in result.warnings[0]


@pytest.mark.parametrize("name", LOOKUPS)
@pytest.mark.parametrize("pinned", [FUTURE, PAST])
def test_paginated_is_the_new_shape_on_both_sides(monkeypatch, name, pinned):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", pinned)
    result = invoke([*LOOKUPS[name], "--paginated"])
    assert result.exit_code == 0, result.error
    assert set(result.result) == {"items"}
    assert result.warnings == ()


@pytest.mark.parametrize("name", LOOKUPS)
def test_after_items_without_pagination(monkeypatch, name):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", PAST)
    result = invoke(LOOKUPS[name])
    assert set(result.result) == {"items"}
    assert result.warnings == ()


def test_a_flagless_sessions_get_gets_two_notices_in_order(monkeypatch):
    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    warnings = invoke(["sessions", "get", "lk-s"]).warnings
    assert len(warnings) == 2
    assert "instead of a bare array" in warnings[0]
    assert "reduced session projection" in warnings[1]


@pytest.mark.parametrize("name", LOOKUPS)
def test_mcp_is_never_notified(monkeypatch, name):
    from twicc.mcp.identity import mcp_call

    monkeypatch.setattr(_output, "LISTING_CUTOVER", FUTURE)
    token = mcp_call.set(True)
    try:
        assert invoke(LOOKUPS[name]).warnings == ()
    finally:
        mcp_call.reset(token)


def test_the_help_flips_with_the_effective_constant():
    """Import-time: two-sided on the effective constant (real clock or override)."""
    from twicc.mcp.tools import iter_mcp_tools, tools_by_name

    # tools_by_name() holds CommandSpec objects (no `description`); the MCP
    # descriptions come from iter_mcp_tools().
    described = {t.name: t.description for t in iter_mcp_tools()}
    specs = tools_by_name()
    for name in ("sessions_get", "projects_get", "workspaces_get", "peers"):
        assert described[name].startswith("DEPRECATION") != _output.listing_cutover_passed(), name
        assert "paginated" in specs[name].json_schema["properties"], name
