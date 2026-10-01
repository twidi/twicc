"""Tests for the inter-session sender header (``cli/_drop_request/sender_header.py``).

The helper is a pure function over caller/recipient attributes — the caller is
any object exposing ``id`` / ``spawned_by_id`` / ``title``, so a plain
namespace stands in for the ``Session`` row and no DB is needed.
"""

from types import SimpleNamespace

from twicc.cli._drop_request.sender_header import (
    TITLE_MAX_CHARS,
    has_sender_header,
    prefix_sender_header,
)


def _caller(id="caller-id", spawned_by_id=None, title=None):
    return SimpleNamespace(id=id, spawned_by_id=spawned_by_id, title=title)


def test_no_caller_returns_text_unchanged():
    assert prefix_sender_header(
        "hello", None, recipient_id="r", recipient_spawned_by_id=None,
    ) == "hello"


def test_self_send_returns_text_unchanged():
    caller = _caller(id="same-id")
    assert prefix_sender_header(
        "hello", caller, recipient_id="same-id", recipient_spawned_by_id="x",
    ) == "hello"


def test_child_to_parent_uses_spawned_session_wording():
    caller = _caller(id="child", spawned_by_id="parent")
    result = prefix_sender_header(
        "report", caller, recipient_id="parent", recipient_spawned_by_id=None,
    )
    assert result == ":: message from your spawned session `child`\n\nreport"


def test_parent_to_child_uses_parent_session_wording():
    caller = _caller(id="parent")
    result = prefix_sender_header(
        "steer", caller, recipient_id="child", recipient_spawned_by_id="parent",
    )
    assert result == ":: message from your parent session `parent`\n\nsteer"


def test_siblings_use_sibling_session_wording():
    caller = _caller(id="worker-a", spawned_by_id="leader")
    result = prefix_sender_header(
        "heads-up", caller, recipient_id="worker-b", recipient_spawned_by_id="leader",
    )
    assert result == ":: message from a sibling session `worker-a`\n\nheads-up"


def test_unrelated_sessions_use_another_session_wording():
    caller = _caller(id="a", spawned_by_id="x")
    result = prefix_sender_header(
        "ping", caller, recipient_id="b", recipient_spawned_by_id="y",
    )
    assert result == ":: message from another session `a`\n\nping"


def test_two_root_sessions_are_unrelated_not_siblings():
    # Both spawned_by None must NOT match the sibling branch.
    caller = _caller(id="a", spawned_by_id=None)
    result = prefix_sender_header(
        "ping", caller, recipient_id="b", recipient_spawned_by_id=None,
    )
    assert result.startswith(":: message from another session `a`")


def test_title_is_appended_in_quotes():
    caller = _caller(id="child", spawned_by_id="parent", title="Fix the tests")
    result = prefix_sender_header(
        "done", caller, recipient_id="parent", recipient_spawned_by_id=None,
    )
    assert result == (
        ':: message from your spawned session `child` ("**Fix the tests**")\n\ndone'
    )


def test_empty_or_whitespace_title_is_omitted():
    for title in (None, "", "   "):
        caller = _caller(id="a", title=title)
        result = prefix_sender_header(
            "x", caller, recipient_id="b", recipient_spawned_by_id=None,
        )
        assert result == ":: message from another session `a`\n\nx"


def test_long_title_is_truncated_with_ellipsis():
    caller = _caller(id="a", title="t" * 200)
    result = prefix_sender_header(
        "x", caller, recipient_id="b", recipient_spawned_by_id=None,
    )
    header = result.split("\n", 1)[0]
    title_part = header.split('("**', 1)[1].removesuffix('**")')
    assert title_part == "t" * (TITLE_MAX_CHARS - 1) + "…"
    # The cap measures the real title, before escaping.
    assert len(title_part) == TITLE_MAX_CHARS


def test_title_newlines_are_flattened():
    # The header is one line: a newline would push the rest of the title into
    # the message below.
    caller = _caller(id="a", title="fix\nthe tests")
    result = prefix_sender_header(
        "x", caller, recipient_id="b", recipient_spawned_by_id=None,
    )
    assert result.split("\n", 1)[0] == ':: message from another session `a` ("**fix the tests**")'


def test_title_markdown_specials_are_escaped():
    # The title sits inside a bold span; unescaped markers would break out of it.
    caller = _caller(id="a", title="fix *all* the `tests` [now]")
    result = prefix_sender_header(
        "x", caller, recipient_id="b", recipient_spawned_by_id=None,
    )
    assert result.split("\n", 1)[0] == (
        ':: message from another session `a` '
        '("**fix \\*all\\* the \\`tests\\` \\[now\\]**")'
    )


def test_a_colon_run_in_the_message_is_left_alone():
    # Nothing closes the header, so the message needs no escaping.
    caller = _caller(id="a")
    result = prefix_sender_header(
        "before\n:::\nafter", caller, recipient_id="b", recipient_spawned_by_id=None,
    )
    assert result == ":: message from another session `a`\n\nbefore\n:::\nafter"


def test_the_header_is_a_single_line_above_the_message():
    caller = _caller(id="a", title="T")
    result = prefix_sender_header(
        "line one\nline two", caller, recipient_id="b", recipient_spawned_by_id=None,
    )
    header, blank, *body = result.split("\n")
    assert header == ':: message from another session `a` ("**T**")'
    assert blank == ""
    assert body == ["line one", "line two"]


# ── has_sender_header ────────────────────────────────────────────────────────


def test_has_sender_header_recognises_every_relation():
    for spawned_by, recipient, recipient_spawned_by in (
        ("parent", "parent", None),      # your spawned session
        (None, "child", "caller-id"),    # your parent session
        ("gp", "sibling", "gp"),         # a sibling session
        (None, "other", None),           # another session
    ):
        caller = _caller(spawned_by_id=spawned_by, title="T")
        text = prefix_sender_header(
            "body", caller,
            recipient_id=recipient, recipient_spawned_by_id=recipient_spawned_by,
        )
        assert has_sender_header(text)


def test_has_sender_header_covers_an_attachments_only_message():
    # No body: the header is the whole text, with no trailing blank line.
    caller = _caller()
    text = prefix_sender_header(
        "", caller, recipient_id="b", recipient_spawned_by_id=None,
    )
    assert has_sender_header(text)


def test_has_sender_header_is_false_for_a_human_message():
    assert not has_sender_header("run the tests")
    # The marker mid-message names no sender.
    assert not has_sender_header("look at\n:: message from another session `a`")


def test_has_sender_header_tolerates_leading_whitespace():
    assert has_sender_header("\n  :: message from another session `a`\n\nbody")


def test_has_sender_header_matches_a_twicc_notice():
    from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER
    assert SHELL_NOTICE_HEADER == ":: notice from TwiCC"
    assert has_sender_header(":: notice from TwiCC: background shell(s) still running\n\nbody")
    assert not has_sender_header(":: notice something else")
