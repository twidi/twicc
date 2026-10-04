"""A model answer that is not a title is rejected, logged with its reason, and retried.

The WS handler falls back to the other provider on ``None``
(``asgi._handle_suggest_title``), so a rejection must look exactly like any other
failed attempt: ``None``, one attempt consumed, the next one still made.
"""

import logging

import pytest
from asgiref.sync import async_to_sync

from twicc.providers.claude_code import title_suggest as claude_title_suggest
from twicc.providers.claude_code.hermetic import HermeticClaudeResult
from twicc.providers.codex import title_suggest as codex_title_suggest
from twicc.providers.codex.hermetic import HermeticCodexResult

REPLY = "I will review the final task 7 changes. Plan and report paths are not available in this workspace"


class _FakeClaudeClient:
    answers: list[str] = []
    calls = 0


async def _fake_run_hermetic_claude(prompt, *, model):
    _FakeClaudeClient.calls += 1
    text = _FakeClaudeClient.answers[min(_FakeClaudeClient.calls, len(_FakeClaudeClient.answers)) - 1]
    return HermeticClaudeResult(text=text, assistant_error=None, is_error=False, usage={}, init={},
                                num_turns=1, tool_blocks_seen=0, permission_callback_calls=0)


class _FakeCodex:
    answers: list[str] = []
    calls = 0


@pytest.fixture
def rejections(monkeypatch):
    """Messages logged for a rejected title.

    Handlers go straight on the two module loggers: the ``twicc`` logger does not
    propagate (``caplog`` cannot see it) and ``settings_test`` disables existing
    loggers, which other tests may have triggered before this one runs.
    """
    messages = []

    class _Collect(logging.Handler):
        def emit(self, record):
            if record.levelno == logging.WARNING and "rejected" in record.getMessage():
                messages.append(record.getMessage())

    handler = _Collect()
    for module in (claude_title_suggest, codex_title_suggest):
        monkeypatch.setattr(module.logger, "disabled", False)
        module.logger.addHandler(handler)
    yield messages
    for module in (claude_title_suggest, codex_title_suggest):
        module.logger.removeHandler(handler)


@pytest.fixture
def claude_client(monkeypatch):
    _FakeClaudeClient.calls = 0
    monkeypatch.setattr(claude_title_suggest, "run_hermetic_claude", _fake_run_hermetic_claude)
    return _FakeClaudeClient


@pytest.fixture
def codex_client(monkeypatch):
    async def _prepare(model):
        return object()

    async def _run(plan, prompt, *, effort):
        _FakeCodex.calls += 1
        text = _FakeCodex.answers[min(_FakeCodex.calls, len(_FakeCodex.answers)) - 1]
        return HermeticCodexResult(text, None, 670, {})

    _FakeCodex.calls = 0
    monkeypatch.setattr(codex_title_suggest, "prepare_hermetic_codex", _prepare)
    monkeypatch.setattr(codex_title_suggest, "run_hermetic_codex", _run)
    return _FakeCodex


def _generate(module):
    return async_to_sync(module.generate_title)("hello", "Label: {text}")


def test_claude_rejects_a_reply_logs_the_reason_and_retries(claude_client, rejections):
    claude_client.answers = [REPLY, "Clavier mobile, débordement Browser"]
    assert _generate(claude_title_suggest) == "Clavier mobile, débordement Browser"
    assert claude_client.calls == 2
    assert len(rejections) == 1
    assert "several sentences" in rejections[0] and "words (max 15)" in rejections[0]
    assert "I will review" in rejections[0]


def test_claude_gives_up_after_every_attempt_is_rejected(claude_client):
    claude_client.answers = ["Titre\nExplication"]
    assert _generate(claude_title_suggest) is None
    assert claude_client.calls == claude_title_suggest.MAX_RETRIES


def test_claude_keeps_a_question_title(claude_client):
    claude_client.answers = ["Pourquoi le cache expire ?"]
    assert _generate(claude_title_suggest) == "Pourquoi le cache expire ?"
    assert claude_client.calls == 1


def test_codex_rejects_a_reply_logs_the_reason_and_retries(codex_client, rejections):
    codex_client.answers = [REPLY, "Clavier mobile, débordement Browser"]
    assert _generate(codex_title_suggest) == "Clavier mobile, débordement Browser"
    assert codex_client.calls == 2
    assert len(rejections) == 1
    assert "several sentences" in rejections[0] and "Codex" in rejections[0]


def test_codex_gives_up_after_every_attempt_is_rejected(codex_client):
    codex_client.answers = ["x" * 101]
    assert _generate(codex_title_suggest) is None
    assert codex_client.calls == codex_title_suggest.MAX_RETRIES
