from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync

from twicc.providers.codex import credentials as cred
from twicc.providers.codex import title_suggest as titles
from twicc.providers.codex.hermetic import HermeticCodexResult
from twicc.providers.hermetic import HermeticConfigError


def patch(monkeypatch, module, *, run_result=None, prepare_error=None, run_error=None):
    seen = {}
    async def prepare(model):
        seen["model"] = model
        if prepare_error:
            raise prepare_error
        return "PLAN"
    async def run(plan, prompt, *, effort):
        seen.update(plan=plan, prompt=prompt, effort=effort)
        if run_error:
            raise run_error
        return run_result
    monkeypatch.setattr(module, "prepare_hermetic_codex", prepare)
    monkeypatch.setattr(module, "run_hermetic_codex", run)
    return seen


def ok(**over):
    return HermeticCodexResult(**{"text": "A title", "terminal_error": None, "input_tokens": 1, "start": {}, **over})


def test_title_prepares_with_the_title_model_outside_the_timeout(monkeypatch):
    seen = patch(monkeypatch, titles, run_result=ok())
    assert async_to_sync(titles._call_codex)("hello", "S: {text}") == "A title"
    assert seen["model"] == titles.TITLE_MODEL and seen["prompt"] == "S: hello"


def test_title_terminal_error_is_a_failed_attempt(monkeypatch):
    patch(monkeypatch, titles, run_result=ok(text="x", terminal_error=SimpleNamespace(message="boom")))
    assert async_to_sync(titles._call_codex)("hello", "S: {text}") is None


def test_probe_true_when_the_turn_ends_without_a_terminal_error(monkeypatch):
    patch(monkeypatch, cred, run_result=ok(text=""))
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is True


def test_probe_false_on_an_unauthorized_terminal_error(monkeypatch):
    from openai_codex.generated.v2_all import ErrorNotification
    err = ErrorNotification.model_construct(will_retry=False, error=SimpleNamespace(message="unexpected status 401"))
    patch(monkeypatch, cred, run_result=ok(text="", terminal_error=err))
    monkeypatch.setattr("twicc.providers.codex.agent.agent.CodexAgent._is_unauthorized_error", staticmethod(lambda p: True))
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is False


def test_probe_none_on_another_terminal_error(monkeypatch):
    from openai_codex.generated.v2_all import ErrorNotification
    err = ErrorNotification.model_construct(will_retry=False)
    patch(monkeypatch, cred, run_result=ok(text="", terminal_error=err))
    monkeypatch.setattr("twicc.providers.codex.agent.agent.CodexAgent._is_unauthorized_error", staticmethod(lambda p: False))
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is None


def test_probe_none_on_a_configuration_error(monkeypatch):
    patch(monkeypatch, cred, prepare_error=HermeticConfigError("catalog", "x"))
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is None


def test_probe_false_when_start_fails_with_an_unauthorized_rpc_error(monkeypatch):
    cause = RuntimeError("JSON-RPC error: unexpected status 401 Unauthorized")
    exc = HermeticConfigError("start", "thread/start failed")
    exc.__cause__ = cause
    patch(monkeypatch, cred, run_error=exc)
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is False


@pytest.mark.parametrize("text,expected", [
    ("unexpected status 401: bad token", True), ("status 403", True), ("Unauthorized", True),
    ("connection reset", False), ("", False),
])
def test_is_unauthorized_exception(text, expected):
    assert cred.is_unauthorized_exception(RuntimeError(text)) is expected


def test_site_timeouts_are_unchanged(monkeypatch):
    seen = {}

    async def spy(awaitable, timeout=None):
        seen["timeout"] = timeout
        return await awaitable

    patch(monkeypatch, titles, run_result=ok())
    monkeypatch.setattr(titles.asyncio, "wait_for", spy)
    async_to_sync(titles._call_codex)("hello", "S: {text}")
    assert seen["timeout"] == titles.SUGGESTION_TIMEOUT_SECONDS == 15
    patch(monkeypatch, cred, run_result=ok(text=""))
    seen.clear()
    async_to_sync(cred.probe_auth_via_codex_sdk)()
    assert seen.get("timeout") == cred._TOKEN_REFRESH_TIMEOUT


def test_throwaway_call_runs_the_refresh_prompt(monkeypatch):
    seen = patch(monkeypatch, cred, run_result=ok(text=""))
    async_to_sync(cred._codex_sdk_throwaway_call)()
    assert seen["model"] == cred._REFRESH_MODEL and seen["prompt"] == cred._REFRESH_PROMPT
