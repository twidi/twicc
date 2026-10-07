"""Log lines never carry attachment bytes (phase 2 design §4.6, §4.7)."""

from twicc.log_redaction import redact_for_log


def test_short_values_are_kept():
    assert redact_for_log("x" * 512) == "x" * 512
    assert redact_for_log(["a", 1, None, True]) == ["a", 1, None, True]


def test_long_strings_are_cut_anywhere_in_a_structure():
    long = "--attach=data:image/png;base64," + "A" * 1000
    cut = long[:64] + f"…<{len(long)} chars>"
    assert redact_for_log(long) == cut
    assert redact_for_log(["send-message", long]) == ["send-message", cut]
    assert redact_for_log({"attach": [long], "n": 3, "t": (long,)}) == {"attach": [cut], "n": 3, "t": (cut,)}
