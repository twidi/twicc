from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync

from twicc.providers.codex import hermetic as mod
from twicc.providers.hermetic import HermeticConfigError, HermeticGuardViolation

MODEL = "gpt-6-luna"


class FakeStream:
    def __init__(self, events, after=None):
        self.events, self.after = events, after

    async def stream(self):
        for e in self.events:
            yield e
        if self.after:
            self.after()


def item_event(method, type_, text=None):
    d = {"type": type_}
    if text is not None:
        d["text"] = text
    return SimpleNamespace(method=method, payload=SimpleNamespace(item=d))


def token_event(n):
    payload = SimpleNamespace(model_dump=lambda mode=None: {"token_usage": {"last": {"input_tokens": n}}})
    return SimpleNamespace(method="thread/tokenUsage/updated", payload=payload)


class FakeCodex:
    """Stands for TwiccAsyncCodex."""
    events = []
    after = None
    model_list = SimpleNamespace(data=[SimpleNamespace(id=MODEL, model=MODEL)], next_cursor=None)
    mcp = {"mcp_servers": {"cloudflare-api": {}, "node.repl": {}}}

    def __init__(self, config=None):
        self.config = config
        self._client = SimpleNamespace(_sync=SimpleNamespace(_approval_handler=None))
        self.closed = False
        type(self).last = self

    async def _ensure_initialized(self): pass

    async def model_list_call(self): return type(self).model_list

    async def thread_start_with_policy(self, **kw):
        self.start_kwargs = kw
        thread = SimpleNamespace(
            start_response=SimpleNamespace(model_dump=lambda mode=None: {
                "model": MODEL, "cwd": str(self.config.cwd),
                "sandbox": {"type": "readOnly", "network_access": False}, "approval_policy": "never",
                "instruction_sources": []}),
            turn_with_policy=self._turn,
        )
        return thread

    async def _turn(self, *_a, **_k):
        return FakeStream(type(self).events, type(self).after)

    async def close(self): self.closed = True


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeCodex.events = []; FakeCodex.after = None
    FakeCodex.model_list = SimpleNamespace(data=[SimpleNamespace(id=MODEL, model=MODEL)], next_cursor=None)

    async def fake_config(*, cwd=None, **extra):
        return SimpleNamespace(cwd=cwd, **extra)

    monkeypatch.setattr(mod, "make_codex_config", fake_config)
    monkeypatch.setattr(mod, "hermetic_cwd", lambda base=None: tmp_path)
    monkeypatch.setattr(mod, "_client_factory", FakeCodex)
    monkeypatch.setattr(mod, "_list_models", lambda codex: codex.model_list_call())
    monkeypatch.setattr(mod, "_read_mcp_server_names", lambda codex, cwd: _names(codex))
    monkeypatch.setattr(mod, "codex_home", lambda: SimpleNamespace(path=tmp_path / "home"))
    async def _cat(model, variant):
        return tmp_path / "cat.json"

    async def _runtime():
        calls.append("runtime")

    calls: list[str] = []
    monkeypatch.setattr(mod, "_catalog_for", _cat)
    monkeypatch.setattr(mod, "ensure_codex_runtime", _runtime)
    FakeCodex.runtime_calls = calls
    return FakeCodex


async def _names(codex):
    return tuple(codex.mcp["mcp_servers"])


def run(prompt="hi"):
    async def go():
        plan = await mod.prepare_hermetic_codex(MODEL)
        return await mod.run_hermetic_codex(plan, prompt, effort="low")
    return async_to_sync(go)()


def test_happy_path_returns_text_tokens_and_start(fake):
    fake.events = [item_event("item/started", "agentMessage"), token_event(670),
                   item_event("item/completed", "agentMessage", "OK")]
    r = run()
    assert r.text == "OK" and r.input_tokens == 670 and r.terminal_error is None
    assert r.start["model"] == MODEL and fake.last.closed


def test_thread_is_started_read_only_with_dotted_config_and_disabled_mcp(fake):
    fake.events = [item_event("item/completed", "agentMessage", "OK")]
    run()
    kw = fake.last.start_kwargs
    assert kw["ephemeral"] is True and kw["model"] == MODEL
    assert kw["config"]["mcp_servers"] == {"cloudflare-api": {"enabled": False}, "node.repl": {"enabled": False}}
    assert kw["config"]["features.default_mode_request_user_input"] is False
    assert str(kw["sandbox"]).endswith("read_only") and str(kw["approval_policy"]).lower().count("never")


def test_refusing_handler_is_installed_before_the_first_request(fake):
    fake.events = [item_event("item/completed", "agentMessage", "OK")]
    run()
    assert isinstance(fake.last._client._sync._approval_handler, mod.RefusingApprovalHandler)


def test_unexpected_item_type_raises_and_closes(fake):
    fake.events = [item_event("item/started", "commandExecution")]
    with pytest.raises(HermeticGuardViolation):
        run()
    assert fake.last.closed


def test_run_turn_checks_the_flag_after_the_stream_ends(fake):
    def refuse():
        fake.last._client._sync._approval_handler("item/commandExecution/requestApproval", {})
    fake.events = [item_event("item/completed", "agentMessage", "OK")]
    fake.after = refuse
    with pytest.raises(HermeticGuardViolation) as info:
        run()
    assert "item/commandExecution/requestApproval" in info.value.reason


def test_terminal_error_is_collected(fake):
    from openai_codex.generated.v2_all import ErrorNotification
    err = SimpleNamespace(method="error", payload=ErrorNotification.model_construct(will_retry=False))
    fake.events = [err]
    r = run()
    assert r.terminal_error is err.payload


def test_several_models_in_model_list_is_a_violation(fake):
    fake.model_list = SimpleNamespace(
        data=[SimpleNamespace(id=MODEL, model=MODEL), SimpleNamespace(id="x", model="x")], next_cursor=None)
    with pytest.raises(HermeticGuardViolation):
        run()
    assert fake.last.closed


def test_config_read_failure_is_mcp_config_error(fake, monkeypatch):
    async def boom(codex, cwd):
        raise RuntimeError("rpc down")
    monkeypatch.setattr(mod, "_read_mcp_server_names", boom)
    with pytest.raises(HermeticConfigError) as info:
        run()
    assert info.value.reason == "mcp-config" and isinstance(info.value.__cause__, RuntimeError)


def test_start_failure_is_a_start_error(fake, monkeypatch):
    async def boom(codex): raise RuntimeError("exit 1")
    monkeypatch.setattr(mod, "_list_models", boom)
    with pytest.raises(HermeticConfigError) as info:
        run()
    assert info.value.reason == "start"


def test_diagnostic_prepare_passes_extra_overrides_and_catalog(fake, tmp_path):
    async def go():
        return await mod._prepare_hermetic_codex_for_diagnostic(
            MODEL, catalog_variant="neutral", catalog_path=tmp_path / "bogus.json", extra_config_overrides=("a=1",))
    plan = async_to_sync(go)()
    assert plan.catalog_path == tmp_path / "bogus.json" and "a=1" in plan.config.config_overrides


def test_prepare_ensures_the_runtime_before_building_the_catalogue(fake):
    async_to_sync(mod.prepare_hermetic_codex)(MODEL)
    assert fake.runtime_calls == ["runtime"]


def test_a_refused_request_mid_stream_interrupts_the_turn(fake):
    handle_calls = []
    def refuse():
        fake.last._client._sync._approval_handler("item/fileChange/requestApproval", {})
    fake.events = [item_event("item/started", "agentMessage"), item_event("item/completed", "agentMessage", "x")]
    original = FakeCodex._turn

    async def _turn(self, *a, **k):
        stream = await original(self, *a, **k)
        async def interrupt():
            handle_calls.append("interrupt")
        stream.interrupt = interrupt
        first = stream.stream
        async def gen():
            async for e in first():
                if not handle_calls:
                    refuse()
                yield e
        stream.stream = gen
        return stream
    FakeCodex._turn = _turn
    try:
        with pytest.raises(HermeticGuardViolation):
            run()
    finally:
        FakeCodex._turn = original
    assert handle_calls == ["interrupt"]


def test_public_prepare_has_no_catalog_parameter():
    import inspect
    assert list(inspect.signature(mod.prepare_hermetic_codex).parameters) == ["model"]


def test_diagnostic_entry_points_are_not_referenced_from_src():
    import pathlib
    src = pathlib.Path(mod.__file__).parents[3]  # .../src
    defining = {
        "_prepare_hermetic_codex_for_diagnostic": src / "twicc/providers/codex/hermetic.py",
        "_run_hermetic_claude_for_diagnostic": src / "twicc/providers/claude_code/hermetic.py",
    }
    for needle, definer in defining.items():
        users = [p for p in src.rglob("*.py") if needle in p.read_text() and p != definer]
        assert users == [], (needle, users)


def test_token_usage_shape_drift_degrades_to_none(fake):
    drifted = SimpleNamespace(method="thread/tokenUsage/updated",
                              payload=SimpleNamespace(model_dump=lambda mode=None: {"token_usage": {}}))
    fake.events = [drifted, item_event("item/completed", "agentMessage", "OK")]
    result = run()
    assert result.input_tokens is None and result.text == "OK"


def test_the_stream_is_closed_when_the_turn_raises(fake):
    closed = []

    class Closing:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return item_event("item/started", "commandExecution")

        async def aclose(self):
            closed.append(True)

    original = FakeCodex._turn

    async def _turn(self, *a, **k):
        handle = await original(self, *a, **k)
        handle.stream = lambda: Closing()
        return handle

    FakeCodex._turn = _turn
    try:
        with pytest.raises(HermeticGuardViolation):
            run()
    finally:
        FakeCodex._turn = original
    assert closed == [True]
