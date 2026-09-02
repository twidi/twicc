from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md"


def test_twicc_peer_message_skill_contract():
    text = SKILL.read_text()
    local_line = next(
        line for line in text.splitlines()
        if line.startswith("- `origin_session` / `delivered_to_session`")
    )
    assert "The peer receives neither." in local_line

    wire_line = next(
        line for line in text.splitlines()
        if line.startswith("- **Wire boundary**")
    )
    for name in ("`message_id`", "`title`", "`reply_to`", "`origin.sent_at`", "`payload`"):
        assert name in wire_line
    assert 'A root message carries `reply_to` as `""`.' in wire_line
    assert (
        "`thread_id`, `reply_to_ref`, and `reply_target` are local serialization values, "
        "not wire fields."
    ) in wire_line
