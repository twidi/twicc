import pytest
from asgiref.sync import async_to_sync
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolUseBlock

from twicc.providers.claude_code import hermetic as mod
from twicc.providers.hermetic import HermeticGuardViolation

INIT = {"tools": [], "mcp_servers": [], "slash_commands": [], "skills": [], "permissionMode": "dontAsk",
        "cwd": "", "model": "claude-haiku-4-5-20251001"}


def init_message(cwd, **over):
    return SystemMessage(subtype="init", data={**INIT, "cwd": str(cwd), **over})


def assistant(text=None, error=None, blocks=()):
    content = ([TextBlock(text=text)] if text else []) + list(blocks)
    return AssistantMessage(content=content, model="claude-haiku-4-5", error=error)


def result_message(**over):
    base = {"subtype": "success", "duration_ms": 1, "duration_api_ms": 1, "is_error": False, "num_turns": 1,
            "session_id": "s", "usage": {"input_tokens": 10}}
    base.update(over)
    return ResultMessage(**base)


class FakeClient:
    script: list = []
    instances: list = []

    def __init__(self, options=None):
        self.options = options
        self.interrupted = False
        self.disconnected = False
        type(self).instances.append(self)

    async def connect(self): pass
    async def query(self, prompt): self.prompt = prompt
    async def interrupt(self): self.interrupted = True
    async def disconnect(self): self.disconnected = True

    async def receive_messages(self):
        for m in type(self).script:
            yield m(self.options.cwd) if callable(m) else m


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeClient.instances = []
    monkeypatch.setattr(mod, "_client_factory", FakeClient)
    monkeypatch.setattr(mod, "hermetic_cwd", lambda base=None: tmp_path)
    monkeypatch.setattr("twicc.provider_homes.provider_env_overlay", dict)
    return FakeClient


def run(**kw):
    return async_to_sync(mod.run_hermetic_claude)("hello", model="haiku", **kw)


@pytest.fixture
def hermetic_logs():
    import logging

    logger = logging.getLogger("twicc.providers.hermetic")
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler, was_disabled, level = _Collect(), logger.disabled, logger.level
    logger.disabled = False
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.disabled = was_disabled


def test_happy_path_collects_text_usage_and_init(fake):
    fake.script = [init_message, assistant("OK"), result_message()]
    r = run()
    assert r.text == "OK" and r.num_turns == 1 and r.usage == {"input_tokens": 10}
    assert r.init["tools"] == [] and r.violation is None
    assert fake.instances[0].disconnected


def test_a_violation_logs_exactly_one_warning(fake, hermetic_logs):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), result_message()]
    with pytest.raises(HermeticGuardViolation):
        run()
    assert [m for m in hermetic_logs if m.startswith("hermetic call failed: guard")] == [
        "hermetic call failed: guard (init reports non-empty tools: ['Bash'])"]
    hermetic_logs.clear()
    async_to_sync(mod._run_hermetic_claude_for_diagnostic)("hello", model="haiku")
    assert len(hermetic_logs) == 1


def test_tool_block_without_a_result_message_is_a_violation(fake):
    fake.script = [init_message, assistant("t", blocks=[ToolUseBlock(id="1", name="Bash", input={})])]
    with pytest.raises(HermeticGuardViolation):
        run()


def test_bad_init_raises_and_interrupts(fake):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), assistant("x"), result_message()]
    with pytest.raises(HermeticGuardViolation):
        run()
    assert fake.instances[0].interrupted and fake.instances[0].disconnected


def test_missing_init_is_a_violation(fake):
    fake.script = [assistant("OK"), result_message()]
    with pytest.raises(HermeticGuardViolation):
        run()


def test_tool_use_block_raises_even_with_an_error_result(fake):
    fake.script = [init_message, assistant("t", blocks=[ToolUseBlock(id="1", name="Bash", input={})]),
                   result_message(is_error=True, num_turns=1)]
    with pytest.raises(HermeticGuardViolation):
        run()


def test_authentication_failed_is_returned_not_raised(fake):
    fake.script = [init_message, assistant(error="authentication_failed"), result_message(is_error=True, num_turns=0)]
    r = run()
    assert r.assistant_error == "authentication_failed" and r.is_error


def test_diagnostic_seam_returns_the_violation(fake):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), result_message()]
    r = async_to_sync(mod._run_hermetic_claude_for_diagnostic)("hello", model="haiku")
    assert r.violation and "tools" in r.violation


def test_diagnostic_seam_cwd_sets_options_and_expected_cwd(fake, tmp_path):
    other = tmp_path / "fixture"; other.mkdir()
    fake.script = [init_message, assistant("OK"), result_message()]
    r = async_to_sync(mod._run_hermetic_claude_for_diagnostic)("hello", model="haiku", cwd=other)
    assert fake.instances[0].options.cwd == str(other) and r.violation is None


def test_public_claude_api_has_no_cwd_or_override_parameter():
    import inspect

    assert list(inspect.signature(mod.hermetic_client_options).parameters) == ["model", "effort"]
    assert list(inspect.signature(mod.run_hermetic_claude).parameters) == ["prompt", "model"]
    assert list(inspect.signature(mod._run_hermetic_claude_for_diagnostic).parameters) == [
        "prompt", "model", "cwd", "options_override"]


def test_diagnostic_seam_options_override_skips_the_guard(fake):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), assistant("OK"), result_message()]
    r = async_to_sync(mod._run_hermetic_claude_for_diagnostic)(
        "hello", model="haiku", options_override=lambda o: o)
    assert r.violation is None and r.init["tools"] == ["Bash"]


# --------------------------------------------------------------------------------------
# Streaming mode
# --------------------------------------------------------------------------------------

def stream_event(event):
    from claude_agent_sdk import StreamEvent

    return StreamEvent(uuid="u", session_id="s", event=event)


def message_start():
    return stream_event({"type": "message_start", "message": {}})


def delta(text):
    return stream_event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})


def stream(out: list):
    """Collect the yielded deltas into ``out`` (kept when the stream raises)."""
    import asyncio

    async def go():
        async with asyncio.timeout(5):
            async for chunk in mod.stream_hermetic_claude("hello", model="haiku"):
                out.append(chunk)

    asyncio.run(go())


def test_stream_yields_the_deltas_in_order_and_not_the_final_text(fake):
    fake.script = [init_message, message_start(), delta("One. "),
                   stream_event({"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "thinking_delta", "thinking": "hmm"}}),
                   delta("Two."), assistant("One. Two."), result_message()]
    out: list[str] = []
    stream(out)
    assert out == ["One. ", "Two."]
    client = fake.instances[0]
    assert client.options.include_partial_messages is True and client.disconnected and not client.interrupted


def test_the_title_call_does_not_ask_for_partial_messages(fake):
    fake.script = [init_message, assistant("OK"), result_message()]
    assert run().text == "OK"
    assert fake.instances[0].options.include_partial_messages is False


def test_stream_a_bad_init_raises_before_any_yield(fake):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), message_start(), delta("a"), result_message()]
    out: list[str] = []
    with pytest.raises(HermeticGuardViolation):
        stream(out)
    assert out == [] and fake.instances[0].interrupted and fake.instances[0].disconnected


def test_stream_a_delta_before_the_init_is_a_violation(fake):
    fake.script = [message_start(), delta("a"), init_message, result_message()]
    out: list[str] = []
    with pytest.raises(HermeticGuardViolation) as info:
        stream(out)
    assert out == [] and "no init" in info.value.reason


def test_stream_a_tool_block_in_the_final_message_raises_after_the_yields(fake):
    fake.script = [init_message, message_start(), delta("a"),
                   assistant("a", blocks=[ToolUseBlock(id="1", name="Bash", input={})]), result_message()]
    out: list[str] = []
    with pytest.raises(HermeticGuardViolation) as info:
        stream(out)
    assert out == ["a"] and "tool activity" in info.value.reason


@pytest.mark.parametrize("ending", [
    [assistant("a"), result_message(is_error=True)],
    [assistant(error="rate_limit"), result_message()],
    [assistant("a")],
], ids=["is_error", "assistant_error", "no_result"])
def test_a_claude_error_after_some_text_raises(fake, ending):
    from twicc.providers.hermetic import HermeticStreamError

    fake.script = [init_message, message_start(), delta("a"), *ending]
    out: list[str] = []
    with pytest.raises(HermeticStreamError) as info:
        stream(out)
    assert out == ["a"] and "hello" not in str(info.value)
    assert fake.instances[0].disconnected


def test_a_restarted_message_raises(fake):
    from twicc.providers.hermetic import HermeticStreamError

    fake.script = [init_message, message_start(), delta("a"), message_start(), delta("a"), assistant("a"),
                   result_message()]
    out: list[str] = []
    with pytest.raises(HermeticStreamError):
        stream(out)
    assert out == ["a"]   # the restarted answer is not yielded again
    assert fake.instances[0].interrupted and fake.instances[0].disconnected


def test_stream_aclose_interrupts_and_disconnects(fake):
    import asyncio

    fake.script = [init_message, message_start(), delta("a"), delta("b"), assistant("ab"), result_message()]

    async def go():
        async with asyncio.timeout(5):
            gen = mod.stream_hermetic_claude("hello", model="haiku")
            first = await gen.__anext__()
            await gen.aclose()
            return first

    assert asyncio.run(go()) == "a"
    assert fake.instances[0].interrupted and fake.instances[0].disconnected


def test_stream_cancellation_interrupts_and_disconnects(fake):
    import asyncio

    started = asyncio.Event()

    class Hanging(FakeClient):
        async def receive_messages(self):
            yield init_message(self.options.cwd)
            yield delta("a")
            started.set()
            await asyncio.Event().wait()   # never set: the consumer cancels

    fake.instances = []
    mod._client_factory = Hanging   # the fixture's monkeypatch restores the original

    async def consume():
        async for _ in mod.stream_hermetic_claude("hello", model="haiku"):
            pass

    async def go():
        async with asyncio.timeout(5):
            task = asyncio.create_task(consume())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(go())
    assert fake.instances[0].interrupted and fake.instances[0].disconnected


def test_public_stream_api_has_no_cwd_or_override_parameter():
    import inspect

    assert list(inspect.signature(mod.stream_hermetic_claude).parameters) == ["prompt", "model"]
