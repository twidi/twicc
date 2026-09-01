from pathlib import Path

import orjson

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src/twicc/agent/plugin/twicc/skills/twicc-share/SKILL.md"
README = ROOT / "src/twicc/agent/plugin/README.md"
PLUGIN = ROOT / "src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json"


def _resolver_block(text: str) -> str:
    start = text.index("**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.**")
    final = "it may expand to multiple words, which quoting would break."
    end = text.index(final, start) + len(final)
    return text[start:end]


def test_twicc_share_skill_contract():
    text = SKILL.read_text()
    assert text.startswith("---\nname: twicc-share\ndescription: ")
    frontmatter = text.split("---", 2)[1]
    assert (
        "Use when you or the user want to create or manage a link, "
        "including a new link for a peer message."
    ) in frontmatter
    assert "send a peer link" not in frontmatter
    headings = [
        "# Sharing sessions and artifacts",
        "## When to use",
        "## How to invoke",
        "## Usage",
        "## Errors",
        "## Output format",
        "## Examples",
        "## Related commands",
        "## How to present results",
    ]
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert _resolver_block(text) == _resolver_block(README.read_text())

    assert "--limit N" in text and "--offset N" in text
    for operation in ("update", "revoke", "unrevoke", "delete", "propagate"):
        line = next(
            row
            for row in text.splitlines()
            if row.startswith(f"$TWICC share {operation} ")
        )
        assert "[--timeout N]" in line
    assert "$TWICC share create session" in text
    assert "$TWICC share create artifact" in text
    assert "revoke|unrevoke|delete|propagate" not in text
    assert "--max-display debug" not in text
    assert "draft to adapt" not in text

    plugin = orjson.loads(PLUGIN.read_bytes())
    assert plugin["version"] == "0.72.1"
