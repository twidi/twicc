"""A title provider whose client cannot be built returns ``None``, never raises.

``generate_title`` promises ``str | None``. The WS handler leans on that promise
to fall back to the other provider (``asgi._handle_suggest_title``); an escaping
exception would skip the fallback. Both title functions therefore return ``None``
whenever the hermetic call fails (build, start or guard) — these tests pin that
contract, which a handler-level test cannot see.
"""

import logging

import pytest
from asgiref.sync import async_to_sync

from twicc.providers.claude_code import title_suggest as claude_title_suggest
from twicc.providers.codex import title_suggest as codex_title_suggest
from twicc.providers.hermetic import HermeticConfigError


@pytest.mark.parametrize("current_title", [None, "Existing title"])
def test_claude_returns_none_when_the_hermetic_call_cannot_start(monkeypatch, current_title):
    async def _explode(*_a, **_k):
        raise HermeticConfigError("cwd", "neutral directory unusable")

    monkeypatch.setattr(claude_title_suggest, "run_hermetic_claude", _explode)

    assert async_to_sync(claude_title_suggest.generate_title)(
        "hello", "Summarize: {text}", current_title=current_title,
    ) is None


@pytest.mark.parametrize("current_title", [None, "Existing title"])
def test_codex_returns_none_when_the_hermetic_plan_cannot_be_prepared(monkeypatch, current_title):
    async def _explode(model):
        raise HermeticConfigError("catalog", "x")

    monkeypatch.setattr(codex_title_suggest, "prepare_hermetic_codex", _explode)

    assert async_to_sync(codex_title_suggest.generate_title)(
        "hello", "Summarize: {text}", current_title=current_title,
    ) is None


@pytest.fixture
def title_logs():
    """Collect the title modules' log lines even when an earlier test disabled or muted those loggers."""
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Collect()
    loggers = [claude_title_suggest.logger, codex_title_suggest.logger]
    saved = [(lg.disabled, lg.level) for lg in loggers]
    for lg in loggers:
        lg.disabled = False
        lg.setLevel(logging.WARNING)
        lg.addHandler(handler)
    yield records
    for lg, (disabled, level) in zip(loggers, saved):
        lg.removeHandler(handler)
        lg.setLevel(level)
        lg.disabled = disabled


def test_claude_error_log_carries_the_reason_code(monkeypatch, title_logs):
    async def _explode(*_a, **_k):
        raise HermeticConfigError("cwd", "neutral directory unusable")

    monkeypatch.setattr(claude_title_suggest, "run_hermetic_claude", _explode)
    async_to_sync(claude_title_suggest.generate_title)("hello", "Summarize: {text}")
    assert any("(reason=cwd)" in line for line in title_logs)


def test_codex_error_log_carries_the_reason_code(monkeypatch, title_logs):
    async def _explode(model):
        raise HermeticConfigError("catalog", "x")

    monkeypatch.setattr(codex_title_suggest, "prepare_hermetic_codex", _explode)
    async_to_sync(codex_title_suggest.generate_title)("hello", "Summarize: {text}")
    assert any("(reason=catalog)" in line for line in title_logs)
