"""Regression tests for Claude command frontmatter discovery."""

import pytest

from twicc.providers.claude_code.commands import _parse_frontmatter, _scan_commands_dir, _scan_skills_dir


@pytest.mark.parametrize(
    ("marker", "description"),
    [("|", "Read emails.\nManage contacts.\n"), (">", "Read emails. Manage contacts.\n")],
)
@pytest.mark.parametrize("source", ["skill", "command"])
def test_discovery_reads_multiline_description(tmp_path, marker, description, source):
    content = f"---\nname: hey\ndescription: {marker}\n  Read emails.\n  Manage contacts.\n---\n# HEY\n"
    if source == "skill":
        directory = tmp_path / "hey"
        directory.mkdir()
        (directory / "SKILL.md").write_text(content)
        commands = _scan_skills_dir(tmp_path)
    else:
        (tmp_path / "hey.md").write_text(content)
        commands = _scan_commands_dir(tmp_path)

    assert len(commands) == 1
    assert commands[0].name == "hey"
    assert commands[0].description == description


def test_frontmatter_reads_existing_fields():
    metadata, body = _parse_frontmatter(
        '---\nname: hey\ndescription: "Read emails"\nargument-hint: "[command]"\n'
        "user-invocable: false\nallowed-tools:\n  - Read\n  - Bash\n---\n# HEY\n"
    )
    assert metadata == {
        "name": "hey",
        "description": "Read emails",
        "argument-hint": "[command]",
        "user-invocable": False,
        "allowed-tools": ["Read", "Bash"],
    }
    assert body == "# HEY\n"


@pytest.mark.parametrize("metadata", ["description: [invalid", "- list", "plain scalar", "", "null"])
def test_invalid_or_empty_frontmatter_uses_body_description(tmp_path, metadata):
    (tmp_path / "hey.md").write_text(f"---\n{metadata}\n---\n# Read emails\n")
    commands = _scan_commands_dir(tmp_path)
    assert len(commands) == 1
    assert commands[0].description == "Read emails"
