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
