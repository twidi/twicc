"""Shared title generation routing without a WebSocket adapter."""

from asgiref.sync import async_to_sync
import pytest

from twicc.core.enums import Provider
from twicc.core.services.title_suggestion import TitleSuggestionResult, suggest_title


class QwenFake:
    """The Qwen title call and the OpenRouter key, as a test sets them."""

    def __init__(self):
        self.key_set = False
        # The answer: a title, None, or an exception to raise.
        self.outcome = "Qwen title"
        self.calls = []

    async def generate(self, source, system_prompt, *, current_title=None):
        self.calls.append((source, system_prompt, current_title))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def qwen(monkeypatch):
    fake = QwenFake()
    monkeypatch.setattr("twicc.core.services.title_suggestion.generate_openrouter_title", fake.generate)
    monkeypatch.setattr("twicc.core.services.title_suggestion.openrouter_key_set", lambda: fake.key_set)
    return fake


@pytest.fixture
def routing(monkeypatch, qwen):
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
    assert result == TitleSuggestionResult(None, Provider.CODEX, None, "no_prompt", requested_model="luna", title_model=None)


def test_no_running_provider(routing):
    calls, _, running = routing
    running.clear()
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX)
    assert result == TitleSuggestionResult(
        None, Provider.CODEX, None, "no_provider_available", requested_model="luna", title_model=None,
    )
    assert calls == []


@pytest.mark.parametrize("outcome", [None, "", RuntimeError("broken")])
def test_failure_falls_back_and_passes_current_title_to_both_providers(routing, outcome):
    calls, outcomes, _ = routing
    outcomes[Provider.CODEX] = outcome
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, current_title="Current")
    assert result == TitleSuggestionResult(
        "Suggested title", Provider.CODEX, Provider.CLAUDE_CODE, None, requested_model="luna", title_model="haiku",
    )
    assert calls == [
        (Provider.CODEX, "Source", "Prompt {text}", "Current"),
        (Provider.CLAUDE_CODE, "Source", "Prompt {text}", "Current"),
    ]


@pytest.mark.parametrize("outcome", [None, "", RuntimeError("broken")])
def test_complete_failure(routing, outcome):
    calls, outcomes, _ = routing
    outcomes.update({Provider.CODEX: outcome, Provider.CLAUDE_CODE: outcome})
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX)
    assert result == TitleSuggestionResult(
        None, Provider.CODEX, None, "generation_failed", requested_model="luna", title_model=None,
    )
    assert len(calls) == 2


@pytest.mark.parametrize("model, expected, expected_model", [
    ("haiku", Provider.CLAUDE_CODE, "haiku"), ("luna", Provider.CODEX, "luna"),
    (None, Provider.CODEX, "luna"), ("unknown", Provider.CODEX, "luna"), ("provider", Provider.CODEX, "luna"),
])
def test_model_selects_generation_provider(routing, model, expected, expected_model):
    calls, _, _ = routing
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model=model)
    assert result == TitleSuggestionResult(
        "Suggested title", expected, expected, None, requested_model=expected_model, title_model=expected_model,
    )
    assert calls == [(expected, "Source", "Prompt {text}", None)]


def test_unavailable_requested_provider_uses_fallback(routing):
    calls, _, running = routing
    running.remove(Provider.CODEX)
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX)
    assert result == TitleSuggestionResult(
        "Suggested title", Provider.CODEX, Provider.CLAUDE_CODE, None, requested_model="luna", title_model="haiku",
    )
    assert calls == [(Provider.CLAUDE_CODE, "Source", "Prompt {text}", None)]


@pytest.mark.parametrize("available, error", [(True, "generation_failed"), (False, "no_provider_available")])
def test_no_fallback_tries_only_requested_provider(routing, available, error):
    calls, outcomes, running = routing
    outcomes[Provider.CODEX] = None
    if not available:
        running.remove(Provider.CODEX)
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, no_fallback=True)
    assert result == TitleSuggestionResult(None, Provider.CODEX, None, error, requested_model="luna", title_model=None)
    assert [call[0] for call in calls] == ([Provider.CODEX] if available else [])


def test_qwen_makes_the_title(routing, qwen):
    calls, _, _ = routing
    qwen.key_set = True
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="qwen")
    assert result == TitleSuggestionResult(
        "Qwen title", Provider.CODEX, None, None, requested_model="qwen", title_model="qwen",
    )
    assert qwen.calls == [("Source", "Prompt {text}", None)]
    assert calls == []


@pytest.mark.parametrize("session_provider, first, second, first_model", [
    (Provider.CODEX, Provider.CODEX, Provider.CLAUDE_CODE, "luna"),
    (Provider.CLAUDE_CODE, Provider.CLAUDE_CODE, Provider.CODEX, "haiku"),
])
@pytest.mark.parametrize("qwen_outcome", [None, "", RuntimeError("broken")])
def test_qwen_failure_falls_back_like_match_session_provider(
    routing, qwen, session_provider, first, second, first_model, qwen_outcome,
):
    calls, outcomes, _ = routing
    qwen.key_set = True
    qwen.outcome = qwen_outcome
    result = async_to_sync(suggest_title)(
        "Source", "Prompt {text}", session_provider, title_model="qwen", current_title="Current",
    )
    assert result == TitleSuggestionResult(
        "Suggested title", session_provider, first, None, requested_model="qwen", title_model=first_model,
    )
    assert qwen.calls == [("Source", "Prompt {text}", "Current")]
    assert calls == [(first, "Source", "Prompt {text}", "Current")]

    # The session provider fails too: the other provider comes next.
    calls.clear()
    qwen.calls.clear()
    outcomes[first] = None
    result = async_to_sync(suggest_title)(
        "Source", "Prompt {text}", session_provider, title_model="qwen", current_title="Current",
    )
    assert result.title_provider == second
    assert result.title_model == {"haiku": "luna", "luna": "haiku"}[first_model]
    assert result.requested_model == "qwen"
    assert calls == [
        (first, "Source", "Prompt {text}", "Current"),
        (second, "Source", "Prompt {text}", "Current"),
    ]


def test_qwen_without_fallback_fails_alone(routing, qwen):
    calls, _, _ = routing
    qwen.key_set = True
    qwen.outcome = None
    result = async_to_sync(suggest_title)(
        "Source", "Prompt {text}", Provider.CODEX, title_model="qwen", no_fallback=True,
    )
    assert result == TitleSuggestionResult(
        None, Provider.CODEX, None, "generation_failed", requested_model="qwen", title_model=None,
    )
    assert len(qwen.calls) == 1
    assert calls == []


def test_qwen_failed_and_no_agent_runs_is_generation_failed(routing, qwen):
    calls, _, running = routing
    running.clear()
    qwen.key_set = True
    qwen.outcome = None
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="qwen")
    assert result == TitleSuggestionResult(
        None, Provider.CODEX, None, "generation_failed", requested_model="qwen", title_model=None,
    )
    assert len(qwen.calls) == 1
    assert calls == []


def test_qwen_works_without_any_agent(routing, qwen):
    calls, _, running = routing
    running.clear()
    qwen.key_set = True
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="qwen")
    assert result == TitleSuggestionResult(
        "Qwen title", Provider.CODEX, None, None, requested_model="qwen", title_model="qwen",
    )
    assert calls == []


@pytest.mark.parametrize("session_provider, model", [(Provider.CODEX, "luna"), (Provider.CLAUDE_CODE, "haiku")])
def test_qwen_without_the_key_routes_as_provider(routing, qwen, session_provider, model):
    calls, _, _ = routing
    as_qwen = async_to_sync(suggest_title)("Source", "Prompt {text}", session_provider, title_model="qwen")
    qwen_calls = list(calls)
    calls.clear()
    as_provider = async_to_sync(suggest_title)("Source", "Prompt {text}", session_provider, title_model="provider")
    assert as_qwen == as_provider == TitleSuggestionResult(
        "Suggested title", session_provider, session_provider, None, requested_model=model, title_model=model,
    )
    assert qwen_calls == calls
    assert qwen.calls == []


def test_qwen_without_a_source_is_no_prompt(routing, qwen):
    qwen.key_set = True
    result = async_to_sync(suggest_title)(None, "Prompt {text}", Provider.CODEX, title_model="qwen")
    assert result == TitleSuggestionResult(
        None, Provider.CODEX, None, "no_prompt", requested_model="qwen", title_model=None,
    )
    assert qwen.calls == []


def test_agent_models_are_reported(routing):
    _, outcomes, _ = routing
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="haiku")
    assert (result.requested_model, result.title_model) == ("haiku", "haiku")

    outcomes[Provider.CLAUDE_CODE] = None
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="haiku")
    assert (result.requested_model, result.title_model) == ("haiku", "luna")
    assert result.title_provider == Provider.CODEX
