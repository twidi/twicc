"""Grouped configurations preserve membership and order through file persistence."""

import pytest

from twicc import message_snippets, terminal_config


@pytest.mark.parametrize("kind", ["message", "terminal"])
def test_grouped_config_roundtrip(tmp_path, monkeypatch, kind):
    snippets = [
        {"label": "Outside", "text": "outside"},
        {"type": "group", "id": "commands", "label": "Commands", "items": [
            {"label": "Second", "text": "second"}, {"label": "First", "text": "first"},
        ]},
        {"type": "group", "id": "empty", "label": "Empty", "items": []},
    ]
    config = {"snippets": {"global": snippets, "workspace:one": [snippets[2]]}}
    if kind == "message":
        monkeypatch.setattr(message_snippets, "get_message_snippets_config_path", lambda: tmp_path / "message.json")
        write = message_snippets.write_message_snippets_config
        read = message_snippets.read_message_snippets_config
    else:
        config["combos"] = [{"type": "group", "id": "keys", "label": "Keys", "items": [
            {"label": "Interrupt", "steps": [{"type": "key", "key": "ctrl-c"}]},
        ]}]
        monkeypatch.setattr(terminal_config, "get_terminal_config_path", lambda: tmp_path / "terminal.json")
        write = terminal_config.write_terminal_config
        read = terminal_config.read_terminal_config
    write(config)
    assert read() == config
    config["snippets"]["global"] = list(reversed(snippets))
    write(config)
    assert read() == config
