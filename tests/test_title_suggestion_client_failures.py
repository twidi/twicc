"""A title provider whose client cannot be built returns ``None``, never raises.

``generate_title`` promises ``str | None``. The WS handler leans on that promise
to fall back to the other provider (``asgi._handle_suggest_title``); an escaping
exception would skip the fallback. Both title functions therefore return ``None``
whenever the hermetic call fails (build, start or guard) — these tests pin that
contract, which a handler-level test cannot see.
"""

from asgiref.sync import async_to_sync

from twicc.providers.claude_code import title_suggest as claude_title_suggest
from twicc.providers.codex import title_suggest as codex_title_suggest
from twicc.providers.hermetic import HermeticConfigError


def test_claude_returns_none_when_the_hermetic_call_cannot_start(monkeypatch):
    async def _explode(*_a, **_k):
        raise HermeticConfigError("cwd", "neutral directory unusable")

    monkeypatch.setattr(claude_title_suggest, "run_hermetic_claude", _explode)

    assert async_to_sync(claude_title_suggest.generate_title)("hello", "Summarize: {text}") is None


def test_codex_returns_none_when_the_hermetic_plan_cannot_be_prepared(monkeypatch):
    async def _explode(model):
        raise HermeticConfigError("catalog", "x")

    monkeypatch.setattr(codex_title_suggest, "prepare_hermetic_codex", _explode)

    assert async_to_sync(codex_title_suggest.generate_title)("hello", "Summarize: {text}") is None
