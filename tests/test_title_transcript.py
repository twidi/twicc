from twicc.title_transcript import (
    EDGE_MESSAGES,
    MAX_MESSAGE_CHARS,
    TRUNCATED_MARKER,
    build_title_source,
    clip_message,
    has_title_content,
)


def test_no_message_gives_none():
    assert build_title_source([]) is None


def test_single_message_is_returned_alone():
    assert build_title_source(["fix the bug"]) == "fix the bug"


def test_several_messages_are_numbered_in_order():
    assert build_title_source(["a", "b"]) == "[Message 1]\na\n\n[Message 2]\nb"


def test_conversation_at_the_limit_is_kept_whole():
    messages = [f"m{i}" for i in range(1, 2 * EDGE_MESSAGES + 1)]
    source = build_title_source(messages)
    assert "omitted" not in source
    assert "[Message 1]\nm1" in source
    assert f"[Message {2 * EDGE_MESSAGES}]\nm{2 * EDGE_MESSAGES}" in source


def test_long_conversation_keeps_both_ends_and_says_what_is_missing():
    total = 2 * EDGE_MESSAGES + 7
    messages = [f"m{i}" for i in range(1, total + 1)]
    source = build_title_source(messages)
    assert source.startswith("Beginning of the conversation:")
    assert "[… 7 messages omitted …]" in source
    assert "End of the conversation:" in source
    assert f"[Message {EDGE_MESSAGES}]\nm{EDGE_MESSAGES}" in source
    assert f"[Message {EDGE_MESSAGES + 1}]" not in source
    assert f"[Message {total - EDGE_MESSAGES + 1}]\nm{total - EDGE_MESSAGES + 1}" in source
    assert source.endswith(f"[Message {total}]\nm{total}")


def test_long_message_keeps_both_ends_around_a_marker():
    half = MAX_MESSAGE_CHARS // 2
    text = "a" * half + "m" * 500 + "z" * half
    clipped = clip_message(text)
    assert clipped == "a" * half + TRUNCATED_MARKER + "z" * half
    assert "m" not in clipped
    assert build_title_source([text, "b"]).count(TRUNCATED_MARKER) == 1


def test_message_at_the_limit_is_kept_whole():
    text = "x" * MAX_MESSAGE_CHARS
    assert clip_message(text) == text
    assert clip_message("short") == "short"
    assert clip_message(text + "x") != text + "x"


def test_superseded_default_prompts_are_dropped_but_a_custom_one_is_kept():
    from twicc import synced_settings as ss

    current = ss.SYNCED_SETTINGS_DEFAULTS["titleSystemPrompt"]
    assert len(ss._SUPERSEDED_TITLE_SYSTEM_PROMPTS) == 1
    for old_default in ss._SUPERSEDED_TITLE_SYSTEM_PROMPTS:
        assert old_default != current and "{text}" in old_default
        stored = {"titleSystemPrompt": old_default}
        assert ss._migrate_legacy_settings(stored) is True
        assert "titleSystemPrompt" not in stored

        custom = {"titleSystemPrompt": old_default + "\n\nAlways put the key words first."}
        assert ss._migrate_legacy_settings(custom) is False
        assert custom["titleSystemPrompt"].endswith("key words first.")

    # The current default is a value like any other: it is left as stored.
    untouched = {"titleSystemPrompt": current}
    assert ss._migrate_legacy_settings(untouched) is False
    assert untouched["titleSystemPrompt"] == current



def test_bound_is_per_message_not_global():
    messages = ["y" * (MAX_MESSAGE_CHARS * 2)] * (2 * EDGE_MESSAGES + 5)
    source = build_title_source(messages)
    assert source.count(TRUNCATED_MARKER) == 2 * EDGE_MESSAGES
    assert source.count("y") == 2 * EDGE_MESSAGES * MAX_MESSAGE_CHARS
    assert len(source) > 60_000


def test_messages_without_content_are_left_out_and_never_sent():
    assert has_title_content("fix 42") and not has_title_content("  ...  \n— ?!")
    assert build_title_source(["...", "  "]) is None
    assert build_title_source(["...", "real subject"]) == "real subject"


def test_normal_titles_are_accepted():
    from twicc.title_transcript import title_rejection_reasons

    for title in (
        "Clavier mobile, débordement Browser",
        "Peer threading lot 1.1 plan review, round 2",
        "sessions-index.json watcher, invalidation et logs de debug",
        "Pourquoi le cache expire ?",           # a question is a legitimate title
        "Modèle utilisé par Codex ?",
        "Upload lent. barre figée",             # no capital letter after the dot
        " Titre avec espaces autour \n",
    ):
        assert title_rejection_reasons(title) == [], title


def test_limits_are_inclusive():
    from twicc.title_transcript import MAX_TITLE_CHARS, MAX_TITLE_WORDS, title_rejection_reasons

    assert title_rejection_reasons(" ".join(["mot"] * MAX_TITLE_WORDS)) == []
    assert title_rejection_reasons("x" * MAX_TITLE_CHARS) == []
    assert title_rejection_reasons(" ".join(["mot"] * (MAX_TITLE_WORDS + 1))) == [
        f"{MAX_TITLE_WORDS + 1} words (max {MAX_TITLE_WORDS})",
    ]
    assert title_rejection_reasons("x" * (MAX_TITLE_CHARS + 1)) == [
        f"{MAX_TITLE_CHARS + 1} characters (max {MAX_TITLE_CHARS})",
    ]


def test_each_rejection_reason_is_reported():
    from twicc.title_transcript import title_rejection_reasons

    assert title_rejection_reasons("") == ["empty response"]
    assert title_rejection_reasons("   \n ") == ["empty response"]
    assert title_rejection_reasons("Titre\nExplication") == ["line break"]
    assert title_rejection_reasons("Fini. Voici autre chose") == ["several sentences"]
    assert title_rejection_reasons("Fini! Voici") == ["several sentences"]
    reply = (
        "I will review the final task 7 changes and write the requested review report. "
        "Plan and report paths are not available in this workspace, so I cannot verify the round 9 changes."
    )
    reasons = title_rejection_reasons(reply)
    assert len(reasons) == 3
    assert "words (max 15)" in reasons[0] and "characters (max 100)" in reasons[1]
    assert reasons[2] == "several sentences"
