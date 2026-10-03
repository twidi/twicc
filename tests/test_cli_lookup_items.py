"""Batch lookups and `peers`: the result is wrapped in `items`."""

from __future__ import annotations

import pytest

from twicc.core.models import Project, Session, SessionType
from twicc.rpc.invoker import invoke

LOOKUPS = {
    "sessions get": ["sessions", "get", "lk-s", "--full"],
    # No leading dash: Click would read "-tmp-lk" as an option. The CLI re-adds
    # the dash (twicc-projects/SKILL.md).
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
def test_items_without_pagination(name):
    result = invoke(LOOKUPS[name])
    assert result.exit_code == 0, result.error
    assert set(result.result) == {"items"}


@pytest.mark.parametrize("name", LOOKUPS)
def test_paginated_is_an_accepted_no_op(name):
    without = invoke(LOOKUPS[name])
    with_flag = invoke([*LOOKUPS[name], "--paginated"])
    assert with_flag.exit_code == 0, with_flag.error
    assert with_flag.result == without.result


def test_the_no_op_flag_is_hidden_from_the_schemas():
    from twicc.mcp.tools import iter_mcp_tools, tools_by_name

    described = {t.name: t.description for t in iter_mcp_tools()}
    specs = tools_by_name()
    for name in ("sessions_get", "projects_get", "workspaces_get", "peers"):
        assert not described[name].startswith("DEPRECATION"), name
        assert "paginated" not in specs[name].json_schema["properties"], name
