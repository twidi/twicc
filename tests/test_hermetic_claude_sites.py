from asgiref.sync import async_to_sync

from twicc.providers.claude_code import auth as auth_mod
from twicc.providers.claude_code import title_suggest
from twicc.providers.claude_code.hermetic import HermeticClaudeResult
from twicc.providers.hermetic import HermeticGuardViolation


def result(**over):
    base = {"text": "A title", "assistant_error": None, "is_error": False, "usage": {}, "init": {},
            "num_turns": 1, "tool_blocks_seen": 0, "permission_callback_calls": 0}
    base.update(over)
    return HermeticClaudeResult(**base)


def patch_run(monkeypatch, module, outcome):
    seen = {}
    async def fake(prompt, *, model):
        seen.update(prompt=prompt, model=model)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(module, "run_hermetic_claude", fake)
    return seen


def test_title_error_result_is_a_failed_attempt(monkeypatch):
    patch_run(monkeypatch, title_suggest, result(text="Looks fine", is_error=True))
    assert async_to_sync(title_suggest._call_haiku)("m", "S: {text}") is None


def test_title_uses_haiku_and_the_full_prompt(monkeypatch):
    seen = patch_run(monkeypatch, title_suggest, result())
    assert async_to_sync(title_suggest._call_haiku)("hello", "S: {text}") == "A title"
    assert seen == {"prompt": "S: hello", "model": "haiku"}


def test_probe_returns_true_on_success(monkeypatch):
    seen = patch_run(monkeypatch, auth_mod, result())
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is True and seen["prompt"] == "ping"


def test_probe_returns_false_on_authentication_failed(monkeypatch):
    patch_run(monkeypatch, auth_mod, result(assistant_error="authentication_failed", is_error=True, num_turns=0))
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is False


def test_probe_treats_another_error_result_as_positive_like_today(monkeypatch):
    patch_run(monkeypatch, auth_mod, result(is_error=True))
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is True


def test_probe_is_inconclusive_when_the_stream_ends_without_a_result(monkeypatch):
    patch_run(monkeypatch, auth_mod, result(num_turns=None, text=""))
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is None


def test_probe_is_inconclusive_on_a_guard_violation(monkeypatch):
    patch_run(monkeypatch, auth_mod, HermeticGuardViolation("tools"))
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is None


def spy_timeout(monkeypatch, module):
    seen = {}

    async def spy(awaitable, timeout=None):
        seen["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(module.asyncio, "wait_for", spy)
    return seen


def test_site_timeouts_are_unchanged(monkeypatch):
    patch_run(monkeypatch, title_suggest, result())
    seen = spy_timeout(monkeypatch, title_suggest)
    async_to_sync(title_suggest._call_haiku)("hello", "S: {text}")
    assert seen["timeout"] == title_suggest.SUGGESTION_TIMEOUT_SECONDS == 60
    patch_run(monkeypatch, auth_mod, result())
    seen.clear()   # both auth timeouts are 30: make sure each call sets its own
    async_to_sync(auth_mod.probe_auth_via_sdk)()
    assert seen.get("timeout") == auth_mod._AUTH_PROBE_TIMEOUT
    seen.clear()
    async_to_sync(auth_mod._sdk_throwaway_call)()
    assert seen.get("timeout") == auth_mod._TOKEN_REFRESH_TIMEOUT


def test_throwaway_call_sends_the_refresh_prompt(monkeypatch):
    seen = patch_run(monkeypatch, auth_mod, result())
    async_to_sync(auth_mod._sdk_throwaway_call)()
    assert seen == {"prompt": "What model are you?", "model": "haiku"}
