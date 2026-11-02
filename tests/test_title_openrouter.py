"""Title suggestion through Qwen on OpenRouter: one attempt, the agents' prompt and checks."""

import logging

from asgiref.sync import async_to_sync
import pytest

from twicc import title_openrouter
from twicc.openrouter_text import QWEN_MODEL, OpenRouterTextError
from twicc.title_openrouter import QWEN_TITLE_TIMEOUT_SECONDS, generate_openrouter_title
from twicc.title_transcript import build_title_prompt, unwrap_title_suggestion


@pytest.fixture
def fake_call(monkeypatch):
    """Replace the OpenRouter call; ``outcome`` is the answer text, or an exception to raise."""
    state = {"calls": [], "outcome": "A plain title"}

    async def complete(model, prompt, *, system_prompt=None, timeout_seconds):
        state["calls"].append((model, prompt, system_prompt, timeout_seconds))
        if isinstance(state["outcome"], BaseException):
            raise state["outcome"]
        return state["outcome"]

    monkeypatch.setattr(title_openrouter, "complete_openrouter_text", complete)
    return state


@pytest.fixture
def logs(caplog, monkeypatch):
    # Routed straight to caplog: settings_test's ``disable_existing_loggers: True`` can disable this
    # logger, depending on the suite's import order.
    logger = logging.getLogger(title_openrouter.__name__)
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.DEBUG, logger=title_openrouter.__name__)
    return caplog


@pytest.mark.parametrize("answer", ["<current_title>Fix the parser</current_title>", "Fix the parser"])
def test_builds_the_agents_prompt_and_unwraps_the_answer(fake_call, answer):
    fake_call["outcome"] = answer

    title = async_to_sync(generate_openrouter_title)("The message", "Title: {text}", current_title="Old title")

    assert title == unwrap_title_suggestion(answer) == "Fix the parser"
    assert fake_call["calls"] == [
        (QWEN_MODEL, build_title_prompt("Title: {text}", "The message", "Old title"), None, 10),
    ]
    assert QWEN_TITLE_TIMEOUT_SECONDS == 10


def test_a_rejected_title_is_none(fake_call, logs):
    fake_call["outcome"] = "Fix the parser. Then ship it."

    assert async_to_sync(generate_openrouter_title)("The message", "Title: {text}") is None

    warnings = [record for record in logs.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "rejected" in warnings[0].getMessage()
    assert "source=openrouter" in warnings[0].getMessage()


@pytest.mark.parametrize("error", [OpenRouterTextError("timeout"), OpenRouterTextError("http", 500), RuntimeError("boom")])
def test_a_failed_call_is_none(fake_call, logs, error):
    fake_call["outcome"] = error

    assert async_to_sync(generate_openrouter_title)("The message", "Title: {text}") is None

    assert len(fake_call["calls"]) == 1
    [record] = logs.records
    assert "source=openrouter" in record.getMessage()
    assert "The message" not in record.getMessage()
