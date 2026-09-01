"""``@@`` include-marker expansion in the CLI prompt resolution.

Covers the scanner grammar (escape, delimited form, bare form), the
recursive expansion (depth cap, byte budget), the per-case resolution
rules (missing file, empty file, directory, non-UTF-8, unreadable), the
``forward`` mode used by the ``--remote`` forwarder (client-side
expansion + literal escaping + ``remote:`` rewriting), and the
``resolve_prompt`` integration (``expand=`` switch, 500 KB final cap).
"""

import os

import pytest

from twicc.cli._drop_request.prompt import (
    MAX_INCLUDE_DEPTH,
    MAX_PROMPT_BYTES,
    PromptError,
    expand_prompt_includes,
    resolve_prompt,
)


# ---------------------------------------------------------------------------
# Scanner grammar — what is (and is not) a marker
# ---------------------------------------------------------------------------


def test_bare_marker_absolute_path_is_replaced(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("included content")
    assert expand_prompt_includes(f"before @@{f} after") == "before included content after"


def test_bare_marker_ends_at_whitespace(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("X")
    assert expand_prompt_includes(f"@@{f}\ttail") == "X\ttail"
    assert expand_prompt_includes(f"@@{f}") == "X"


def test_double_at_without_path_prefix_is_literal():
    assert expand_prompt_includes("a @@foo b") == "a @@foo b"
    assert expand_prompt_includes("diff hunk @@ -1,3 +1,3 @@ kept") == "diff hunk @@ -1,3 +1,3 @@ kept"


def test_escape_produces_literal_double_at():
    assert expand_prompt_includes("@@@@/not/included") == "@@/not/included"
    assert expand_prompt_includes("@@@@{/not/included}") == "@@{/not/included}"


def test_triple_at_is_literal():
    # `@@` not followed by a path start is literal (both chars consumed),
    # so the third `@` never pairs into a marker.
    assert expand_prompt_includes("@@@/x y") == "@@@/x y"


def test_delimited_marker_allows_spaces(tmp_path):
    f = tmp_path / "with space.md"
    f.write_text("spaced")
    assert expand_prompt_includes(f"a @@{{{f}}} b") == "a spaced b"


def test_delimited_marker_relative_path_is_literal():
    assert expand_prompt_includes("a @@{relative/path} b") == "a @@{relative/path} b"


def test_delimited_marker_unclosed_is_literal():
    assert expand_prompt_includes("a @@{/tmp/x\nb") == "a @@{/tmp/x\nb"


def test_tilde_path_is_expanded_against_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "home.md"
    f.write_text("from home")
    assert expand_prompt_includes("@@~/home.md") == "from home"


# ---------------------------------------------------------------------------
# Resolution rules
# ---------------------------------------------------------------------------


def test_missing_file_mid_line_is_replaced_by_nothing():
    assert expand_prompt_includes("see @@/no/such/file here") == "see  here"


def test_missing_file_alone_on_line_drops_the_line():
    assert expand_prompt_includes("a\n@@/no/such/file\nb") == "a\nb"


def test_missing_file_alone_on_line_with_whitespace_drops_the_line():
    assert expand_prompt_includes("a\n  @@/no/such/file  \nb") == "a\nb"


def test_empty_file_alone_on_line_drops_the_line(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text("")
    assert expand_prompt_includes(f"a\n@@{f}\nb") == "a\nb"


def test_included_content_trailing_newlines_are_stripped(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("content\n\n")
    assert expand_prompt_includes(f"a\n@@{f}\nb") == "a\ncontent\nb"


def test_directory_is_an_error(tmp_path):
    with pytest.raises(PromptError, match="directory"):
        expand_prompt_includes(f"@@{tmp_path}")


def test_non_utf8_file_is_an_error(tmp_path):
    f = tmp_path / "bin.dat"
    f.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(PromptError, match="UTF-8"):
        expand_prompt_includes(f"@@{f}")


def test_unreadable_file_is_an_error(tmp_path):
    f = tmp_path / "locked.md"
    f.write_text("secret")
    os.chmod(f, 0o000)
    try:
        with pytest.raises(PromptError, match="not readable"):
            expand_prompt_includes(f"@@{f}")
    finally:
        os.chmod(f, 0o644)


def test_remote_marker_is_an_error_in_local_mode():
    with pytest.raises(PromptError, match="--remote"):
        expand_prompt_includes("@@remote:/abs/path")


# ---------------------------------------------------------------------------
# Relative markers — resolved against the file that carries them
# ---------------------------------------------------------------------------


def test_relative_marker_resolves_against_the_including_file(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.md").write_text("inner")
    outer = tmp_path / "sub" / "outer.md"
    outer.write_text("outer(@@{./inner.md})")
    assert expand_prompt_includes(f"@@{outer}") == "outer(inner)"


def test_each_level_resolves_against_its_own_file(tmp_path):
    # a.md (root) → ./sub/b.md → ./c.md, which is sub/c.md, not root/c.md.
    (tmp_path / "sub").mkdir()
    (tmp_path / "c.md").write_text("WRONG")
    (tmp_path / "sub" / "c.md").write_text("right")
    (tmp_path / "sub" / "b.md").write_text("b(@@{./c.md})")
    a = tmp_path / "a.md"
    a.write_text("a(@@{./sub/b.md})")
    assert expand_prompt_includes(f"@@{a}") == "a(b(right))"


def test_parent_relative_marker_climbs_out_of_the_file_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.md").write_text("top")
    inner = tmp_path / "sub" / "inner.md"
    inner.write_text("@@{../top.md}")
    assert expand_prompt_includes(f"@@{inner}") == "top"


def test_bare_relative_marker_is_replaced(tmp_path):
    (tmp_path / "inner.md").write_text("inner")
    outer = tmp_path / "outer.md"
    outer.write_text("before @@./inner.md after")
    assert expand_prompt_includes(f"@@{outer}") == "before inner after"


def test_relative_marker_ignores_the_process_cwd(tmp_path, monkeypatch):
    (tmp_path / "decoy").mkdir()
    (tmp_path / "decoy" / "inner.md").write_text("WRONG")
    (tmp_path / "inner.md").write_text("right")
    outer = tmp_path / "outer.md"
    outer.write_text("@@./inner.md")
    monkeypatch.chdir(tmp_path / "decoy")
    assert expand_prompt_includes(f"@@{outer}") == "right"


def test_missing_relative_include_stays_optional(tmp_path):
    outer = tmp_path / "outer.md"
    outer.write_text("a\n@@./missing.md\nb")
    assert expand_prompt_includes(f"@@{outer}") == "a\nb"


def test_relative_marker_without_a_base_is_an_error():
    with pytest.raises(PromptError, match="relative"):
        expand_prompt_includes("inline @@./nope.md text")


def test_relative_marker_without_dot_prefix_stays_literal(tmp_path):
    # Only `./` and `../` open a relative marker; a bare word never does.
    (tmp_path / "inner.md").write_text("inner")
    outer = tmp_path / "outer.md"
    outer.write_text("@@inner.md")
    assert expand_prompt_includes(f"@@{outer}") == "@@inner.md"


def test_relative_depth_error_names_resolved_paths(tmp_path):
    # A cycle written with relative markers must be readable in the error.
    (tmp_path / "a.md").write_text("@@./b.md")
    (tmp_path / "b.md").write_text("@@./a.md")
    with pytest.raises(PromptError) as excinfo:
        expand_prompt_includes(f"@@{tmp_path / 'a.md'}")
    assert str(tmp_path / "b.md") in str(excinfo.value)


# ---------------------------------------------------------------------------
# Recursion
# ---------------------------------------------------------------------------


def test_included_content_is_rescanned(tmp_path):
    inner = tmp_path / "inner.md"
    inner.write_text("deep")
    outer = tmp_path / "outer.md"
    # Delimited form: a bare marker would swallow the closing parenthesis
    # (tokens end at whitespace only, no punctuation stripping).
    outer.write_text(f"outer(@@{{{inner}}})")
    assert expand_prompt_includes(f"@@{outer}") == "outer(deep)"


def test_depth_cap_allows_max_depth_chain(tmp_path):
    # A chain of MAX_INCLUDE_DEPTH nested files resolves fine.
    prev = tmp_path / "leaf.md"
    prev.write_text("leaf")
    for i in range(MAX_INCLUDE_DEPTH - 1):
        f = tmp_path / f"chain{i}.md"
        f.write_text(f"@@{prev}")
        prev = f
    assert expand_prompt_includes(f"@@{prev}") == "leaf"


def test_depth_cap_rejects_deeper_chain(tmp_path):
    prev = tmp_path / "leaf.md"
    prev.write_text("leaf")
    for i in range(MAX_INCLUDE_DEPTH):
        f = tmp_path / f"chain{i}.md"
        f.write_text(f"@@{prev}")
        prev = f
    with pytest.raises(PromptError, match="depth"):
        expand_prompt_includes(f"@@{prev}")


def test_self_including_file_terminates_with_depth_error(tmp_path):
    f = tmp_path / "loop.md"
    f.write_text("")  # placeholder so the path exists before writing the marker
    f.write_text(f"@@{f}")
    with pytest.raises(PromptError, match="depth"):
        expand_prompt_includes(f"@@{f}")


# ---------------------------------------------------------------------------
# Byte budget
# ---------------------------------------------------------------------------


def test_running_budget_aborts_oversized_expansion(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * (300 * 1024))
    with pytest.raises(PromptError, match="500"):
        expand_prompt_includes(f"@@{big} @@{big}")


def test_budget_allows_content_under_the_cap(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("x" * (200 * 1024))
    out = expand_prompt_includes(f"@@{big} @@{big}")
    assert len(out) == 2 * 200 * 1024 + 1


# ---------------------------------------------------------------------------
# Forward mode (client side of --remote)
# ---------------------------------------------------------------------------


def test_forward_mode_expands_local_markers_and_escapes_content(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("has @@@@/kept text")
    # Local mode: the escape renders as a literal `@@/kept`.
    assert expand_prompt_includes(f"@@{f}") == "has @@/kept text"
    # Forward mode: the literal is re-escaped so the server pass keeps it.
    assert expand_prompt_includes(f"@@{f}", forward=True) == "has @@@@/kept text"


def test_forward_mode_rewrites_remote_marker_to_bare(tmp_path):
    out = expand_prompt_includes("read @@remote:/srv/file.md", forward=True)
    assert out == "read @@/srv/file.md"


def test_forward_mode_rewrites_remote_tilde_marker():
    out = expand_prompt_includes("@@remote:~/notes.md", forward=True)
    assert out == "@@~/notes.md"


def test_forward_mode_relative_remote_marker_is_an_error():
    with pytest.raises(PromptError, match="absolute"):
        expand_prompt_includes("@@remote:rel/path", forward=True)


def test_forward_mode_dot_relative_remote_marker_is_an_error():
    # The base lives on the server; the client cannot resolve it.
    with pytest.raises(PromptError, match="absolute"):
        expand_prompt_includes("@@remote:./rel/path", forward=True)


def test_forward_mode_expands_relative_markers_client_side(tmp_path):
    (tmp_path / "inner.md").write_text("inner")
    outer = tmp_path / "outer.md"
    outer.write_text("outer(@@{./inner.md})")
    assert expand_prompt_includes(f"@@{outer}", forward=True) == "outer(inner)"


def test_forward_mode_escapes_user_escapes_for_the_server_pass():
    # `@@@@/x` (literal `@@/x`) must survive the server's own pass.
    assert expand_prompt_includes("@@@@/x", forward=True) == "@@@@/x"


def test_forward_output_round_trips_through_server_pass(tmp_path):
    # The server runs a plain local pass on the forwarded text; the result
    # must equal what a direct local expansion produces.
    f = tmp_path / "a.md"
    f.write_text("content with @@marker-ish and @@@@ escape")
    text = f"@@{f} plain @@foo @@@@/lit"
    forwarded = expand_prompt_includes(text, forward=True)
    assert expand_prompt_includes(forwarded) == expand_prompt_includes(text)


def test_forward_mode_delimited_remote_marker(tmp_path):
    out = expand_prompt_includes("@@{remote:/srv/with space.md}", forward=True)
    assert out == "@@{/srv/with space.md}"


# ---------------------------------------------------------------------------
# resolve_prompt integration
# ---------------------------------------------------------------------------


def test_resolve_prompt_expands_inline_markers(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("inlined")
    assert resolve_prompt(f"do @@{f} now") == "do inlined now"


def test_resolve_prompt_expands_markers_inside_prompt_file(tmp_path):
    inc = tmp_path / "inc.md"
    inc.write_text("from include")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(f"start @@{inc} end")
    assert resolve_prompt(str(prompt_file)) == "start from include end"


def test_resolve_prompt_expands_relative_markers_inside_prompt_file(tmp_path):
    (tmp_path / "inc.md").write_text("from include")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("start @@{./inc.md} end")
    assert resolve_prompt(str(prompt_file)) == "start from include end"


def test_resolve_prompt_relative_base_is_the_file_not_the_cwd(tmp_path, monkeypatch):
    (tmp_path / "decoy").mkdir()
    (tmp_path / "decoy" / "inc.md").write_text("WRONG")
    (tmp_path / "inc.md").write_text("right")
    (tmp_path / "prompt.md").write_text("@@./inc.md")
    monkeypatch.chdir(tmp_path / "decoy")
    # The prompt argument itself is cwd-relative; its markers are not.
    assert resolve_prompt("../prompt.md") == "right"


def test_resolve_prompt_relative_marker_in_inline_text_is_an_error():
    with pytest.raises(PromptError, match="relative"):
        resolve_prompt("do @@./inc.md now")


def test_resolve_prompt_expand_false_leaves_markers(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("inlined")
    assert resolve_prompt(f"do @@{f} now", expand=False) == f"do @@{f} now"


def test_resolve_prompt_empty_after_expansion_is_an_error():
    with pytest.raises(PromptError, match="empty"):
        resolve_prompt("@@/no/such/file")


def test_resolve_prompt_final_cap_applies_without_markers():
    with pytest.raises(PromptError, match="500"):
        resolve_prompt("x" * (MAX_PROMPT_BYTES + 1))


def test_resolve_prompt_final_cap_applies_with_expand_false():
    with pytest.raises(PromptError, match="500"):
        resolve_prompt("x" * (MAX_PROMPT_BYTES + 1), expand=False)


def test_resolve_prompt_cap_is_measured_in_utf8_bytes():
    # 200k three-byte chars: fine as a char count, over 500 KB as bytes.
    with pytest.raises(PromptError, match="500"):
        resolve_prompt("€" * (200 * 1024))


def test_resolve_prompt_under_cap_passes():
    text = "x" * (MAX_PROMPT_BYTES - 1)
    assert resolve_prompt(text) == text


# ---------------------------------------------------------------------------
# Remote forwarder (client side of --remote)
# ---------------------------------------------------------------------------


def _forward_argv(argv):
    from twicc.cli._remote import inline_prompt, resolve_command

    return inline_prompt(list(argv), resolve_command(list(argv)))


def test_forwarder_expands_inline_prompt_markers(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("included")
    out = _forward_argv(["create-session", f"do @@{f} now"])
    assert out[-1] == "do included now"


def test_forwarder_expands_send_message_prompt(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("included")
    out = _forward_argv(["send-message", "sid", f"do @@{f} now"])
    assert out[-1] == "do included now"


def test_forwarder_rewrites_remote_markers_in_inline_prompt():
    out = _forward_argv(["create-session", "read @@remote:/srv/x.md"])
    assert out[-1] == "read @@/srv/x.md"


def test_forwarder_escapes_literals_for_the_server_pass():
    out = _forward_argv(["create-session", "keep @@@@/lit"])
    assert out[-1] == "keep @@@@/lit"


def test_forwarder_expands_file_borne_prompt_markers(tmp_path):
    inc = tmp_path / "inc.md"
    inc.write_text("from include")
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text(f"start @@{inc} end")
    out = _forward_argv(["create-session", str(prompt_file)])
    assert out[-1] == "start from include end"


def test_forwarder_expands_relative_markers_from_prompt_file(tmp_path):
    (tmp_path / "inc.md").write_text("from include")
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("start @@{./inc.md} end")
    out = _forward_argv(["create-session", str(prompt_file)])
    assert out[-1] == "start from include end"


def test_forwarder_expands_message_option(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("included")
    out = _forward_argv(["send-messages", "--message", f"do @@{f}", "sid"])
    assert any("do included" in token for token in out)


def test_forwarder_no_expand_flag_disables_expansion(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("included")
    argv = ["create-session", "--no-expand", f"do @@{f} now"]
    out = _forward_argv(argv)
    assert out[-1] == f"do @@{f} now"


def test_forwarder_leaves_peer_send_untouched(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("included")
    argv = ["peer-send", "mypeer", "Title", f"do @@{f} now"]
    assert _forward_argv(argv) == argv


def test_forwarder_still_inlines_peer_send_file_prompt(tmp_path):
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("body @@/kept/verbatim")
    out = _forward_argv(["peer-send", "mypeer", "Title", str(prompt_file)])
    # File inlining still happens; markers travel untouched (no expansion,
    # no escaping — the server side of peer-send does not expand either).
    assert out[-1] == "body @@/kept/verbatim"


def test_forwarder_maps_expansion_errors_to_usage_errors(tmp_path):
    from twicc.cli._remote import RemoteUsageError

    with pytest.raises(RemoteUsageError):
        _forward_argv(["create-session", f"@@{tmp_path}"])  # a directory
