"""The ``spawned by`` line of the addendum's ``### Context`` block.

Its title segment mirrors the sender header of an inter-session message
(``cli/_drop_request/sender_header.py``), so a child reads the same shape in
both places.
"""

import pytest

from twicc.agent.system_prompt import build_dynamic_block
from twicc.providers.helpers import AgentSettings


def _spawned_by_line(**kwargs):
    settings = AgentSettings(**{field: None for field in AgentSettings._fields})
    block = build_dynamic_block(
        provider="claude_code",
        project_id="-home-twidi-project",
        resolved_settings=settings,
        session_id="child-id",
        **kwargs,
    )
    lines = [line for line in block.splitlines() if line.startswith("- spawned by:")]
    return lines[0] if lines else None


@pytest.mark.django_db
def test_no_line_without_a_spawner():
    assert _spawned_by_line() is None


@pytest.mark.django_db
def test_title_is_quoted_between_id_and_project():
    line = _spawned_by_line(
        spawned_by_id="parent-id",
        spawned_by_title="Refactor the layout resolver",
        spawned_by_project_id="-parent-project",
    )
    assert line == (
        '- spawned by: parent-id ("Refactor the layout resolver") '
        "(project: -parent-project)"
    )


@pytest.mark.django_db
def test_untitled_parent_keeps_the_previous_shape():
    # A parent spawned moments ago has no title yet.
    for title in (None, "", "   "):
        line = _spawned_by_line(
            spawned_by_id="parent-id",
            spawned_by_title=title,
            spawned_by_project_id="-parent-project",
        )
        assert line == "- spawned by: parent-id (project: -parent-project)"


@pytest.mark.django_db
def test_title_without_a_known_parent_project():
    line = _spawned_by_line(spawned_by_id="parent-id", spawned_by_title="Some task")
    assert line == '- spawned by: parent-id ("Some task")'


@pytest.mark.django_db
def test_the_live_environment_explains_twicc_notices():
    settings = AgentSettings(**{field: None for field in AgentSettings._fields})
    block = build_dynamic_block(
        provider="claude_code", project_id="-home-twidi-project", resolved_settings=settings,
        session_id="child-id",
    )
    assert "`:: notice from TwiCC`" in block
