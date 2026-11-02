"""Shared title generation routing without a WebSocket adapter."""

import logging

from asgiref.sync import async_to_sync
import pytest

from twicc.core.enums import Provider
from twicc.core.services import title_suggestion
from twicc.core.services.title_suggestion import TitleSuggestionResult, suggest_title


class OpenRouterFake:
    """The OpenRouter title call and the OpenRouter key, as a test sets them."""

    def __init__(self):
        self.key_set = False
        # The answer: a title, None, or an exception to raise.
        self.outcome = "OpenRouter title"
        self.calls = []

    async def generate(self, source, system_prompt, *, current_title=None):
        self.calls.append((source, system_prompt, current_title))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def openrouter(monkeypatch):
    fake = OpenRouterFake()
    monkeypatch.setattr("twicc.core.services.title_suggestion.generate_openrouter_title", fake.generate)
    monkeypatch.setattr("twicc.core.services.title_suggestion.openrouter_key_set", lambda: fake.key_set)
    return fake


@pytest.fixture
def routing(monkeypatch, openrouter):
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


def test_openrouter_makes_the_title(routing, openrouter):
    calls, _, _ = routing
    openrouter.key_set = True
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="openrouter")
    assert result == TitleSuggestionResult(
        "OpenRouter title", Provider.CODEX, None, None, requested_model="openrouter", title_model="openrouter",
    )
    assert openrouter.calls == [("Source", "Prompt {text}", None)]
    assert calls == []


@pytest.mark.parametrize("session_provider, first, second, first_model", [
    (Provider.CODEX, Provider.CODEX, Provider.CLAUDE_CODE, "luna"),
    (Provider.CLAUDE_CODE, Provider.CLAUDE_CODE, Provider.CODEX, "haiku"),
])
@pytest.mark.parametrize("openrouter_outcome", [None, "", RuntimeError("broken")])
def test_openrouter_failure_falls_back_like_match_session_provider(
    routing, openrouter, session_provider, first, second, first_model, openrouter_outcome,
):
    calls, outcomes, _ = routing
    openrouter.key_set = True
    openrouter.outcome = openrouter_outcome
    result = async_to_sync(suggest_title)(
        "Source", "Prompt {text}", session_provider, title_model="openrouter", current_title="Current",
    )
    assert result == TitleSuggestionResult(
        "Suggested title", session_provider, first, None, requested_model="openrouter", title_model=first_model,
    )
    assert openrouter.calls == [("Source", "Prompt {text}", "Current")]
    assert calls == [(first, "Source", "Prompt {text}", "Current")]

    # The session provider fails too: the other provider comes next.
    calls.clear()
    openrouter.calls.clear()
    outcomes[first] = None
    result = async_to_sync(suggest_title)(
        "Source", "Prompt {text}", session_provider, title_model="openrouter", current_title="Current",
    )
    assert result.title_provider == second
    assert result.title_model == {"haiku": "luna", "luna": "haiku"}[first_model]
    assert result.requested_model == "openrouter"
    assert calls == [
        (first, "Source", "Prompt {text}", "Current"),
        (second, "Source", "Prompt {text}", "Current"),
    ]


def test_openrouter_without_fallback_fails_alone(routing, openrouter):
    calls, _, _ = routing
    openrouter.key_set = True
    openrouter.outcome = None
    result = async_to_sync(suggest_title)(
        "Source", "Prompt {text}", Provider.CODEX, title_model="openrouter", no_fallback=True,
    )
    assert result == TitleSuggestionResult(
        None, Provider.CODEX, None, "generation_failed", requested_model="openrouter", title_model=None,
    )
    assert len(openrouter.calls) == 1
    assert calls == []


def test_openrouter_failed_and_no_agent_runs_is_generation_failed(routing, openrouter):
    calls, _, running = routing
    running.clear()
    openrouter.key_set = True
    openrouter.outcome = None
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="openrouter")
    assert result == TitleSuggestionResult(
        None, Provider.CODEX, None, "generation_failed", requested_model="openrouter", title_model=None,
    )
    assert len(openrouter.calls) == 1
    assert calls == []


def test_openrouter_works_without_any_agent(routing, openrouter):
    calls, _, running = routing
    running.clear()
    openrouter.key_set = True
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="openrouter")
    assert result == TitleSuggestionResult(
        "OpenRouter title", Provider.CODEX, None, None, requested_model="openrouter", title_model="openrouter",
    )
    assert calls == []


@pytest.mark.parametrize("session_provider, model", [(Provider.CODEX, "luna"), (Provider.CLAUDE_CODE, "haiku")])
def test_openrouter_without_the_key_routes_as_provider(routing, openrouter, session_provider, model):
    calls, _, _ = routing
    as_openrouter = async_to_sync(suggest_title)("Source", "Prompt {text}", session_provider, title_model="openrouter")
    openrouter_calls = list(calls)
    calls.clear()
    as_provider = async_to_sync(suggest_title)("Source", "Prompt {text}", session_provider, title_model="provider")
    assert as_openrouter == as_provider == TitleSuggestionResult(
        "Suggested title", session_provider, session_provider, None, requested_model=model, title_model=model,
    )
    assert openrouter_calls == calls
    assert openrouter.calls == []


def test_openrouter_without_a_source_is_no_prompt(routing, openrouter):
    openrouter.key_set = True
    result = async_to_sync(suggest_title)(None, "Prompt {text}", Provider.CODEX, title_model="openrouter")
    assert result == TitleSuggestionResult(
        None, Provider.CODEX, None, "no_prompt", requested_model="openrouter", title_model=None,
    )
    assert openrouter.calls == []


def test_agent_models_are_reported(routing):
    _, outcomes, _ = routing
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="haiku")
    assert (result.requested_model, result.title_model) == ("haiku", "haiku")

    outcomes[Provider.CLAUDE_CODE] = None
    result = async_to_sync(suggest_title)("Source", "Prompt {text}", Provider.CODEX, title_model="haiku")
    assert (result.requested_model, result.title_model) == ("haiku", "luna")
    assert result.title_provider == Provider.CODEX


@pytest.fixture
def logs(caplog, monkeypatch):
    # Routed straight to caplog: settings_test's ``disable_existing_loggers: True`` can disable this
    # logger, depending on the suite's import order.
    logger = logging.getLogger(title_suggestion.__name__)
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.DEBUG, logger=title_suggestion.__name__)
    return caplog


@pytest.mark.parametrize("session_provider", [Provider.CODEX, Provider.CLAUDE_CODE])
def test_a_fallback_from_openrouter_is_logged(routing, openrouter, logs, session_provider):
    openrouter.key_set = True
    openrouter.outcome = None
    async_to_sync(suggest_title)("Source", "Prompt {text}", session_provider, title_model="openrouter")
    assert [record.getMessage() for record in logs.records] == [
        f"suggest_title: fell back from openrouter to {session_provider.value}",
    ]
