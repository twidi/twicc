"""``apply_goal_instruction``: the ``<twicc:instruction>`` block rides ``/goal <objective>`` only.

The Claude Code CLI recognizes a clear only when the WHOLE ``/goal`` argument is
one of its clear aliases. An appended block would turn ``/goal clear`` into a
new goal whose condition is "clear", so a clear must go out untouched.
"""

from __future__ import annotations

import pytest

from twicc.context_injection import (
    GOAL_CLEAR_ARGS,
    INSTRUCTION_BLOCK_MARKER,
    apply_goal_instruction,
)


@pytest.mark.parametrize(
    "text",
    [
        "/goal count to 10",
        "  /goal count to 10",
        "/goal clear the cache before stopping",
        "/goal stop at 90",
        "/goal\ncount to 10",
    ],
)
def test_objective_gets_the_instruction(text):
    result = apply_goal_instruction(text)
    assert result.startswith(text)
    assert INSTRUCTION_BLOCK_MARKER in result


@pytest.mark.parametrize(
    "text",
    [
        *(f"/goal {alias}" for alias in sorted(GOAL_CLEAR_ARGS)),
        "/goal CLEAR",
        "/goal Stop",
        "/goal clear ",
        "/goal   clear\n",
        " /goal clear",
    ],
)
def test_clear_is_sent_untouched(text):
    assert apply_goal_instruction(text) == text


@pytest.mark.parametrize(
    "text",
    ["/goal", "/goal   ", "/goalkeeper clear", "hello /goal count", "/compact"],
)
def test_non_goal_commands_are_untouched(text):
    assert apply_goal_instruction(text) == text
