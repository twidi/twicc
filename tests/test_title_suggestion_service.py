"""Shared title generation routing without a WebSocket adapter."""

from asgiref.sync import async_to_sync
import pytest

from twicc.core.enums import Provider
from twicc.core.services.title_suggestion import TitleSuggestionResult, suggest_title


@pytest.fixture
def routing(monkeypatch):
    calls = []
    outcomes = {}
    running = {Provider.CLAUDE_CODE, Provider.CODEX}

    class Helpers:
        def __init__(self, provider):
            self.provider = provider

        async def generate_title(self, source, system_prompt, *, current_title=None):
            calls.append((self.provider, source, system_prompt, current_title))
            outcome = outcomes.get(self.provider, "Suggested title")
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr("twicc.core.services.title_suggestion.get_provider_helpers", Helpers)
    monkeypatch.setattr("twicc.core.services.title_suggestion.is_provider_running", running.__contains__)
    return calls, outcomes, running


@pytest.mark.parametrize("source", [None, ""])
def test_no_source_skips_all_provider_work(monkeypatch, source):
    def unexpected(*args):
        pytest.fail("No source must skip availability and generation")

    monkeypatch.setattr("twicc.core.services.title_suggestion.is_provider_running", unexpected)
    monkeypatch.setattr("twicc.core.services.title_suggestion.get_provider_helpers", unexpected)
    result = async_to_sync(suggest_title)(source, "Prompt {text}", Provider.CODEX)
    assert result == TitleSuggestionResult(None, Provider.CODEX, None, "no_prompt")


def test_no_running_provider(routing):
    calls, _, running = routing
    running.clear()
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX)
    assert result == TitleSuggestionResult(None, Provider.CODEX, None, "no_provider_available")
    assert calls == []


@pytest.mark.parametrize("outcome", [None, "", RuntimeError("broken")])
def test_failure_falls_back_and_passes_current_title_to_both_providers(routing, outcome):
    calls, outcomes, _ = routing
    outcomes[Provider.CODEX] = outcome
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, current_title="Current")
    assert result == TitleSuggestionResult("Suggested title", Provider.CODEX, Provider.CLAUDE_CODE, None)
    assert calls == [
        (Provider.CODEX, "Source", "Prompt {text}", "Current"),
        (Provider.CLAUDE_CODE, "Source", "Prompt {text}", "Current"),
    ]


@pytest.mark.parametrize("outcome", [None, "", RuntimeError("broken")])
def test_complete_failure(routing, outcome):
    calls, outcomes, _ = routing
    outcomes.update({Provider.CODEX: outcome, Provider.CLAUDE_CODE: outcome})
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX)
    assert result == TitleSuggestionResult(None, Provider.CODEX, None, "generation_failed")
    assert len(calls) == 2


@pytest.mark.parametrize("model, expected", [
    ("haiku", Provider.CLAUDE_CODE), ("luna", Provider.CODEX),
    (None, Provider.CODEX), ("unknown", Provider.CODEX), ("provider", Provider.CODEX),
])
def test_model_selects_generation_provider(routing, model, expected):
    calls, _, _ = routing
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model=model)
    assert result == TitleSuggestionResult("Suggested title", expected, expected, None)
    assert calls == [(expected, "Source", "Prompt {text}", None)]


def test_unavailable_requested_provider_uses_fallback(routing):
    calls, _, running = routing
    running.remove(Provider.CODEX)
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX)
    assert result == TitleSuggestionResult("Suggested title", Provider.CODEX, Provider.CLAUDE_CODE, None)
    assert calls == [(Provider.CLAUDE_CODE, "Source", "Prompt {text}", None)]


@pytest.mark.parametrize("available, error", [(True, "generation_failed"), (False, "no_provider_available")])
def test_no_fallback_tries_only_requested_provider(routing, available, error):
    calls, outcomes, running = routing
    outcomes[Provider.CODEX] = None
    if not available:
        running.remove(Provider.CODEX)
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, no_fallback=True)
    assert result == TitleSuggestionResult(None, Provider.CODEX, None, error)
    assert [call[0] for call in calls] == ([Provider.CODEX] if available else [])
