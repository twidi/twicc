"""Tests for the adaptation of a text for speech (twicc.tts.adapt).

The streamed call (hermetic or OpenRouter) is faked through ``adapt._open_stream``: no test
reaches a model.
Every scenario runs inside one ``asyncio.run(...)``, bounded by ``asyncio.timeout``.
"""

import asyncio
import logging
import re

import pytest

from twicc.core.enums import Provider
from twicc.openrouter_text import OpenRouterTextError, TextModel
from twicc.providers.hermetic import HermeticConfigError, HermeticGuardViolation, HermeticStreamError
from twicc.tts import adapt
from twicc.tts.adapt import (
    ADAPT_MAX_CHARS,
    ADAPT_PROMPT,
    ADAPT_STOPPED,
    OPENROUTER_ENGINE,
    AdaptedParts,
    Engine,
    fallback_engine,
    resolve_engine,
)
from twicc.tts.openrouter import SpeechError
from twicc.tts.part_source import PartSource
from twicc.tts.parts import PartSplitter

HAIKU = Engine("haiku", Provider.CLAUDE_CODE)
LUNA = Engine("luna", Provider.CODEX)

LIMIT = 2000
FIRST = 250
ORIGINAL = "The **original** text, with `code`."
FALLBACK = ["The original text, part one.", "The original text, part two."]

# A step of a fake stream that never ends (until the call is cancelled).
HANG = object()


def _sentences(length: int) -> str:
    """Short sentences, joined by spaces, up to at least ``length`` characters."""
    sentences = []
    total = 0
    index = 0
    while total < length:
        sentence = f"Sentence number {index} is short."
        sentences.append(sentence)
        total += len(sentence) + 1
        index += 1
    return " ".join(sentences)


class FakeStream:
    """One fake streamed call: each step is a text delta, a delay in seconds, an event to wait
    for, an exception to raise, or ``HANG``."""

    def __init__(self, steps):
        self.steps = steps
        self.finished = False
        self.closed = False
        self.task = None

    async def run(self):
        self.task = asyncio.current_task()
        try:
            for step in self.steps:
                if isinstance(step, str):
                    yield step
                elif isinstance(step, (int, float)):
                    await asyncio.sleep(step)
                elif isinstance(step, asyncio.Event):
                    await step.wait()
                elif isinstance(step, BaseException):
                    raise step
                elif step is HANG:
                    await asyncio.Event().wait()
            self.finished = True
        finally:
            self.closed = True


class FakeCall:
    """A fake ``_open_stream``: the steps of every call (see :class:`FakeStream`), or one list of
    step tuples: one stream per call, in order. ``calls`` records each ``(engine, prompt)``;
    ``finished``, ``closed`` and ``task`` are those of the last stream."""

    def __init__(self, *steps):
        self.scripts = [tuple(script) for script in steps[0]] if len(steps) == 1 and isinstance(steps[0], list) else None
        self.steps = steps
        self.calls = []
        self.streams: list[FakeStream] = []

    def __call__(self, engine, prompt):
        self.calls.append((engine, prompt))
        stream = FakeStream(self.steps if self.scripts is None else self.scripts[len(self.calls) - 1])
        self.streams.append(stream)
        return stream.run()

    @property
    def finished(self):
        return bool(self.streams) and self.streams[-1].finished

    @property
    def closed(self):
        return bool(self.streams) and self.streams[-1].closed

    @property
    def task(self):
        return self.streams[-1].task if self.streams else None


@pytest.fixture
def fake_call(monkeypatch):
    def install(*steps):
        fake = FakeCall(*steps)
        monkeypatch.setattr(adapt, "_open_stream", fake)
        return fake

    return install


@pytest.fixture(autouse=True)
def short_limits(monkeypatch):
    monkeypatch.setattr(adapt, "FIRST_TEXT_SECONDS", 2.0)
    monkeypatch.setattr(adapt, "NEXT_TEXT_SECONDS", 2.0)
    monkeypatch.setattr(adapt, "OPENROUTER_FIRST_TEXT_SECONDS", 2.0)
    monkeypatch.setattr(adapt, "OPENROUTER_NEXT_TEXT_SECONDS", 2.0)


def _source(engine=HAIKU, *, text=ORIGINAL, limit=LIMIT, first=FIRST, fallback=FALLBACK, label=None, then=None):
    return AdaptedParts(
        text, engine, limit, first, fallback=fallback, label={} if label is None else label, then=then
    )


async def _rest(source: AdaptedParts, parts: list[str] | None = None) -> list[str]:
    """Every part left, until the source says there is no more, appended to ``parts`` (so that
    the parts read before an error stay there)."""
    parts = [] if parts is None else parts
    while (part := await source.next_part()) is not None:
        parts.append(part)
    return parts


def test_the_prompt_frames_the_text():
    assert ADAPT_PROMPT.startswith("You rewrite a text so that a speech engine can read it aloud.")
    assert ADAPT_PROMPT.endswith("\n\n<text>\n{text}\n</text>")
    assert ADAPT_PROMPT.count("{text}") == 1
    assert (ADAPT_MAX_CHARS, adapt.TEST_TEXT_KEEP_CHARS) == (12_000, 4_000)
    assert ADAPT_STOPPED == "The text adaptation stopped before the end"


def test_the_prompt_asks_for_no_reasoning_first():
    # Without it, Haiku's adaptive thinking adds seconds before the first text.
    assert "start writing at once, with no reasoning first." in ADAPT_PROMPT.split("\n", 1)[0]


def test_the_prompt_keeps_code_diagrams_and_paths_short():
    assert "never tell how it works or what it does step by step." in ADAPT_PROMPT.lower()
    assert "- A path: its last name only, as written." in ADAPT_PROMPT
    assert "Never say the folder a file is in, in any language." in ADAPT_PROMPT
    assert "A diagram shows the order flow." in ADAPT_PROMPT


def test_the_prompt_formats_with_the_example_braces_and_any_text():
    prompt = ADAPT_PROMPT.format(text="a {b} c")
    assert "B --> C{Paid?}\n" in prompt
    assert prompt.endswith("<text>\na {b} c\n</text>")


def test_the_label_says_pending_until_the_first_text(fake_call):
    first_text = asyncio.Event()
    fake_call(first_text, "Hello to you. ", HANG)

    async def scenario():
        async with asyncio.timeout(5):
            label = {}
            source = _source(label=label)
            assert label == {"adapted": "pending:haiku"}
            reading = asyncio.create_task(source.next_part())
            await asyncio.sleep(0.05)  # the call runs, no text came yet
            assert label == {"adapted": "pending:haiku"}
            first_text.set()
            while label["adapted"] != "haiku":
                await asyncio.sleep(0)
            assert isinstance(label["adapt_first_ms"], int)
            await source.aclose()
            await reading

    asyncio.run(scenario())


def test_the_stall_limits(monkeypatch):
    monkeypatch.undo()  # the values of the module, not the short ones of the fixture
    assert (adapt.FIRST_TEXT_SECONDS, adapt.NEXT_TEXT_SECONDS) == (30.0, 15.0)


def test_engine_resolution(monkeypatch):
    running: set[Provider] = set()
    monkeypatch.setattr(adapt, "is_provider_running", lambda provider: provider in running)
    text = "A message."

    running = {Provider.CLAUDE_CODE, Provider.CODEX}
    assert resolve_engine(None, text) == (HAIKU, None)
    assert resolve_engine("haiku", text) == (HAIKU, None)
    assert resolve_engine("luna", text) == (LUNA, None)
    assert resolve_engine("off", text) == (None, "off")
    assert resolve_engine("bogus", text) == (HAIKU, None)  # an unknown choice takes the default order

    running = {Provider.CODEX}
    assert resolve_engine(None, text) == (LUNA, None)
    assert resolve_engine("haiku", text) == (LUNA, None)
    assert resolve_engine("luna", text) == (LUNA, None)

    running = {Provider.CLAUDE_CODE}
    assert resolve_engine(None, text) == (HAIKU, None)
    assert resolve_engine("bogus", text) == (HAIKU, None)
    assert resolve_engine("luna", text) == (HAIKU, None)

    running = set()
    assert resolve_engine(None, text) == (None, "no_provider")
    assert resolve_engine("haiku", text) == (None, "no_provider")
    assert resolve_engine("luna", text) == (None, "no_provider")
    assert resolve_engine("off", text) == (None, "off")

    running = {Provider.CLAUDE_CODE, Provider.CODEX}
    assert resolve_engine(None, "x" * ADAPT_MAX_CHARS) == (HAIKU, None)
    assert resolve_engine(None, "x" * (ADAPT_MAX_CHARS + 1)) == (None, "too_long")
    assert resolve_engine("luna", "x" * (ADAPT_MAX_CHARS + 1)) == (None, "too_long")
    assert resolve_engine("off", "x" * (ADAPT_MAX_CHARS + 1)) == (None, "off")


def test_open_stream_dispatches_to_the_engine(monkeypatch):
    calls = []

    async def claude(prompt, *, model):
        calls.append(("claude", prompt, model))
        yield "from claude"

    async def prepare(model):
        calls.append(("prepare", model))
        return "the plan"

    async def codex(plan, prompt, *, effort):
        calls.append(("codex", plan, prompt, effort))
        yield "from codex"

    monkeypatch.setattr(adapt, "stream_hermetic_claude", claude)
    monkeypatch.setattr(adapt, "prepare_hermetic_codex", prepare)
    monkeypatch.setattr(adapt, "stream_hermetic_codex", codex)

    async def scenario():
        async with asyncio.timeout(5):
            assert [delta async for delta in adapt._open_stream(HAIKU, "the prompt")] == ["from claude"]
            stream = adapt._open_stream(LUNA, "the prompt")
            assert calls == [("claude", "the prompt", "haiku")]  # the preparation waits for the first step
            assert [delta async for delta in stream] == ["from codex"]

    asyncio.run(scenario())
    assert calls == [
        ("claude", "the prompt", "haiku"),
        ("prepare", "gpt-6-luna"),
        ("codex", "the plan", "the prompt", "low"),
    ]


def test_parts_come_as_the_adaptation_streams(fake_call):
    adapted = _sentences(600)
    more = asyncio.Event()
    fake = fake_call(adapted[:300], adapted[300:], more, " The last sentence.")
    text = "A text with {braces} and **Markdown**."

    async def scenario():
        async with asyncio.timeout(5):
            source = _source(text=text)
            assert isinstance(source, PartSource)
            assert source.split is None
            first = await source.next_part()
            assert not fake.finished
            assert source.split is True
            more.set()
            parts = [first, *await _rest(source)]
            await source.aclose()
            return source, parts

    source, parts = asyncio.run(scenario())
    splitter = PartSplitter(LIMIT, FIRST)
    expected = splitter.feed(adapted + " The last sentence.") + splitter.end()
    assert len(expected) > 1
    assert parts == expected
    assert fake.calls == [(HAIKU, ADAPT_PROMPT.format(text=text))]
    assert fake.finished and fake.closed
    assert source.adapted_by == "haiku"
    assert source.adapt_reason is None
    assert source.adapted_text == adapted + " The last sentence."


def test_a_short_adaptation_is_one_part(fake_call):
    fake_call("Hello there. ", "A short text.\n")

    async def scenario():
        async with asyncio.timeout(5):
            source = _source()
            parts = await _rest(source)
            assert await source.next_part() is None
            return source, parts

    source, parts = asyncio.run(scenario())
    assert parts == ["Hello there. A short text."]
    assert source.split is False
    assert source.adapted_by == "haiku"
    assert source.adapt_reason is None


def test_heading_marks_left_by_the_model_are_not_read(fake_call):
    adapted = "## Status\n\nAll eight tasks are done. " + _sentences(300) + "\n\n  ### Next steps\n\nC# and #tags stay.\n"
    fake_call(adapted[:20], adapted[20:])

    async def scenario():
        async with asyncio.timeout(5):
            source = _source()
            return source, await _rest(source)

    source, parts = asyncio.run(scenario())
    assert parts[0].startswith("Status")
    assert any(re.search(r"(^|\n)Next steps\n", part) for part in parts)
    assert any("C# and #tags stay." in part for part in parts)
    assert not any(re.search(r"(^|\n)\s*#", part) for part in parts)
    # The settings show what the model really wrote.
    assert source.adapted_text == adapted


def test_an_adaptation_with_only_heading_marks_reads_the_original(fake_call):
    fake_call("## \n", "#\n")

    async def scenario():
        async with asyncio.timeout(5):
            source = _source()
            return source, await _rest(source)

    source, parts = asyncio.run(scenario())
    assert parts == FALLBACK
    assert source.adapt_reason == "empty"


def test_a_failure_before_the_first_part_reads_the_original(fake_call):
    async def read(fake_steps):
        fake_call(*fake_steps)
        async with asyncio.timeout(5):
            label = {}
            source = _source(label=label)
            parts = await _rest(source)
            await source.aclose()
            return source, parts, label

    # The call fails before 250 characters came.
    source, parts, label = asyncio.run(read(("Some adapted text.", HermeticGuardViolation("a tool was used"))))
    assert parts == FALLBACK
    assert source.split is True
    assert source.adapted_by is None
    assert source.adapt_reason == "failed"
    assert label == {"adapted": "no", "adapt_reason": "failed"}

    # Parts were released, but the call failed before the first one was returned.
    source, parts, label = asyncio.run(read((_sentences(800), RuntimeError("boom"))))
    assert parts == FALLBACK
    assert source.adapt_reason == "failed"

    # A one-part fallback is not split.
    async def one_part_fallback():
        fake_call(RuntimeError("boom"))
        async with asyncio.timeout(5):
            source = _source(fallback=["The whole original."])
            parts = await _rest(source)
            return source, parts

    source, parts = asyncio.run(one_part_fallback())
    assert parts == ["The whole original."]
    assert source.split is False


def test_a_failure_logs_one_line_without_the_text(fake_call, caplog, monkeypatch):
    async def read():
        async with asyncio.timeout(5):
            return await _rest(_source(text="The secret original."))

    # Routed straight to caplog: settings_test's ``disable_existing_loggers: True`` can disable this
    # logger, depending on the suite's import order (as in tests/test_tts_streams.py).
    logger = logging.getLogger(adapt.__name__)
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.DEBUG, logger=adapt.__name__)
    for error in (
        HermeticConfigError("start", "thread/start failed"),
        HermeticGuardViolation("a tool was used"),
        HermeticStreamError("The call ended without a result"),
    ):
        caplog.clear()
        fake_call("The secret adapted text.", error)
        assert asyncio.run(read()) == FALLBACK
        # The hermetic error logged its own line at its creation (another logger): no second one here.
        assert caplog.records == []

    caplog.clear()
    fake_call("The secret adapted text.", RuntimeError("The secret adapted text."))
    assert asyncio.run(read()) == FALLBACK
    assert [record.getMessage() for record in caplog.records] == ["TTS text adaptation failed (haiku): RuntimeError"]


def test_a_stall_before_the_first_text_reads_the_original(fake_call, monkeypatch):
    monkeypatch.setattr(adapt, "FIRST_TEXT_SECONDS", 0.05)
    fake = fake_call(HANG)

    async def scenario():
        async with asyncio.timeout(5):
            label = {}
            source = _source(label=label)
            parts = await _rest(source)
            await source.aclose()
            return source, parts, label

    source, parts, label = asyncio.run(scenario())
    assert parts == FALLBACK
    assert source.adapted_by is None
    assert source.adapt_reason == "stalled"
    assert label == {"adapted": "no", "adapt_reason": "stalled"}
    assert fake.closed and not fake.finished

    # The first text has its own, longer limit: a first text later than the next-text limit is
    # not a stall.
    monkeypatch.setattr(adapt, "FIRST_TEXT_SECONDS", 2.0)
    monkeypatch.setattr(adapt, "NEXT_TEXT_SECONDS", 0.01)
    fake_call(0.1, "Hello there.")

    async def late_first_text():
        async with asyncio.timeout(5):
            source = _source()
            return source, await _rest(source)

    source, parts = asyncio.run(late_first_text())
    assert parts == ["Hello there."]
    assert source.adapted_by == "haiku"


def test_an_empty_answer_reads_the_original(fake_call):
    async def read(*fake_steps):
        fake_call(*fake_steps)
        async with asyncio.timeout(5):
            label = {}
            source = _source(label=label)
            parts = await _rest(source)
            return source, parts, label

    for steps in ((), ("",), ("  ", "**", " --- \n")):
        source, parts, label = asyncio.run(read(*steps))
        assert parts == FALLBACK
        assert source.adapted_by is None
        assert source.adapt_reason == "empty"
        assert label == {"adapted": "no", "adapt_reason": "empty"}


def test_a_stall_after_the_first_part_stops_the_reading(fake_call, monkeypatch):
    # The first-text limit is far beyond the scenario's own timeout: only the next-text one ends it.
    monkeypatch.setattr(adapt, "FIRST_TEXT_SECONDS", 30.0)
    monkeypatch.setattr(adapt, "NEXT_TEXT_SECONDS", 0.05)
    received = _sentences(600) + " A complete sentence. A sentence cut in the mid"

    async def read(*fake_steps):
        async with asyncio.timeout(5):
            label = {}
            source = _source(label=label)
            parts = [await source.next_part()]
            for step in fake_steps:
                step.set()
            with pytest.raises(SpeechError) as raised:
                await _rest(source, parts)
            await source.aclose()
            return source, parts, label, raised.value

    splitter = PartSplitter(LIMIT, FIRST)
    expected = splitter.feed(received)
    expected.append(splitter.tail())
    assert expected[-1].endswith("A complete sentence.")

    # A stall.
    fake = fake_call(received[:400], received[400:], HANG)
    source, parts, label, error = asyncio.run(read())
    assert parts == expected
    assert error.message == ADAPT_STOPPED
    assert error.log_message == "adaptation stalled after the first part"
    assert source.split is True
    assert source.adapted_by == "haiku"
    assert source.adapt_reason == "stalled"
    assert source.adapted_text == received
    assert label["adapted"] == "haiku" and "adapt_reason" not in label
    assert fake.closed

    # A failure.
    failing = asyncio.Event()
    fake_call(received[:400], received[400:], failing, RuntimeError("boom"))
    source, parts, label, error = asyncio.run(read(failing))
    assert parts == expected
    assert error.log_message == "adaptation failed after the first part"
    assert source.adapt_reason == "failed"

    # A tail without speech is not read.
    released = _sentences(200)
    assert len(released) <= FIRST

    async def without_tail():
        failing = asyncio.Event()
        fake_call(released, "\n\n", "-" * 300, failing, RuntimeError("boom"))
        async with asyncio.timeout(5):
            source = _source()
            parts = [await source.next_part()]
            failing.set()
            with pytest.raises(SpeechError):
                await _rest(source, parts)
            with pytest.raises(SpeechError):
                await source.next_part()
            return parts

    assert asyncio.run(without_tail()) == [released]


def test_aclose_cancels_the_call(fake_call):
    fake = fake_call(_sentences(600), HANG)

    async def scenario():
        async with asyncio.timeout(5):
            source = _source()
            assert await source.next_part()
            assert not fake.closed
            await source.aclose()
            assert fake.closed and not fake.finished
            assert fake.task.done()
            await source.aclose()
            assert await source.next_part() is None

    asyncio.run(scenario())
    assert len(fake.calls) == 1

    # A pump cancelled while it waits for its first part: its ``aclose`` ends the call.
    fake = fake_call(HANG)

    async def cancelled_pump():
        async with asyncio.timeout(5):
            source = _source()
            pump = asyncio.create_task(source.next_part())
            while fake.task is None:
                await asyncio.sleep(0)
            pump.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pump
            assert not fake.closed
            await source.aclose()
            assert fake.closed
            return source

    source = asyncio.run(cancelled_pump())
    assert source.adapt_reason is None  # a close is not a failure
    assert source.adapted_by == "haiku"

    # The call's task cancelled by something else (a server shutdown): the waiting reader is not
    # left waiting, it reads the original.
    fake = fake_call(HANG)

    async def cancelled_call():
        async with asyncio.timeout(5):
            source = _source()
            pump = asyncio.create_task(_rest(source))
            while fake.task is None:
                await asyncio.sleep(0)
            fake.task.cancel()
            return source, await pump

    source, parts = asyncio.run(cancelled_call())
    assert parts == FALLBACK
    assert source.adapt_reason == "failed"

    # The call's task cancelled before its first step: no call ran, the reader reads the original.
    fake = fake_call("Some adapted text.")

    async def cancelled_before_its_first_step():
        async with asyncio.timeout(5):
            source = _source()
            pump = asyncio.create_task(_rest(source))
            await asyncio.sleep(0)  # the pump creates the task, which has not run yet
            assert source._task is not None and fake.calls == []
            source._task.cancel()
            return source, await pump

    source, parts = asyncio.run(cancelled_before_its_first_step())
    assert fake.calls == []
    assert parts == FALLBACK
    assert source.adapt_reason == "failed"

    # The same after the first part: the reading stops.
    fake = fake_call(_sentences(600), HANG)

    async def cancelled_after_the_first_part():
        async with asyncio.timeout(5):
            source = _source()
            assert await source.next_part()
            source._task.cancel()
            with pytest.raises(SpeechError) as raised:
                await _rest(source)
            return raised.value

    assert asyncio.run(cancelled_after_the_first_part()).log_message == "adaptation failed after the first part"


def test_a_reading_cancelled_before_its_first_part_starts_no_call(fake_call):
    fake = fake_call("Some text.")

    async def scenario():
        async with asyncio.timeout(5):
            source = _source()
            await asyncio.sleep(0.01)
            assert fake.calls == []

            # A pump task cancelled before its first step.
            pump = asyncio.create_task(source.next_part())
            pump.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pump
            await asyncio.sleep(0.01)
            assert fake.calls == []

            await source.aclose()
            assert await source.next_part() is None
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert fake.calls == []


def test_the_label_tells_the_adaptation(fake_call):
    fake_call(0.05, "Hello there.")

    async def adapted():
        async with asyncio.timeout(5):
            label = {"config": "a voice"}
            source = _source(LUNA, label=label)
            assert label == {"config": "a voice", "adapted": "pending:luna"}
            assert source.adapted_by == "luna"
            assert source.adapt_first_ms is None
            assert await source.next_part() == "Hello there."
            return source, label

    source, label = asyncio.run(adapted())
    assert isinstance(source.adapt_first_ms, int) and source.adapt_first_ms >= 40
    assert label == {"config": "a voice", "adapted": "luna", "adapt_first_ms": source.adapt_first_ms}

    # A fallback after some text: not adapted, so no time to the first adapted text.
    fake_call(0.01, "Some text.", RuntimeError("boom"))

    async def fallen_back():
        async with asyncio.timeout(5):
            label = {}
            source = _source(label=label)
            assert await _rest(source) == FALLBACK
            return source, label

    source, label = asyncio.run(fallen_back())
    assert label == {"adapted": "no", "adapt_reason": "failed"}
    assert source.adapted_by is None
    assert source.adapt_first_ms is None
    assert source.adapted_text is None


# --- OpenRouter, then the agent ---------------------------------------------------


def test_openrouter_resolves_only_with_the_key(monkeypatch):
    running: set[Provider] = set()
    key_set = True
    monkeypatch.setattr(adapt, "is_provider_running", lambda provider: provider in running)
    monkeypatch.setattr(adapt, "openrouter_key_set", lambda: key_set)
    text = "A message."

    # With the key, OpenRouter needs no provider.
    for running in (set(), {Provider.CLAUDE_CODE}, {Provider.CLAUDE_CODE, Provider.CODEX}):
        assert resolve_engine("openrouter", text) == (OPENROUTER_ENGINE, None)
    assert resolve_engine("openrouter", "x" * (ADAPT_MAX_CHARS + 1)) == (None, "too_long")
    assert resolve_engine("off", text) == (None, "off")
    # No choice never takes OpenRouter.
    running = {Provider.CLAUDE_CODE}
    assert resolve_engine(None, text) == (HAIKU, None)

    # Without the key, an OpenRouter choice works as no choice.
    key_set = False
    running = {Provider.CLAUDE_CODE}
    assert resolve_engine("openrouter", text) == (HAIKU, None)
    running = {Provider.CODEX}
    assert resolve_engine("openrouter", text) == (LUNA, None)
    running = set()
    assert resolve_engine("openrouter", text) == (None, "no_provider")
    assert resolve_engine("openrouter", "x" * (ADAPT_MAX_CHARS + 1)) == (None, "too_long")


def test_fallback_engine(monkeypatch):
    running: set[Provider] = set()
    monkeypatch.setattr(adapt, "is_provider_running", lambda provider: provider in running)

    running = {Provider.CLAUDE_CODE, Provider.CODEX}
    assert fallback_engine(OPENROUTER_ENGINE) == HAIKU
    assert fallback_engine(HAIKU) is None
    assert fallback_engine(LUNA) is None
    running = {Provider.CLAUDE_CODE}
    assert fallback_engine(OPENROUTER_ENGINE) == HAIKU
    running = {Provider.CODEX}
    assert fallback_engine(OPENROUTER_ENGINE) == LUNA
    running = set()
    assert fallback_engine(OPENROUTER_ENGINE) is None
    # The agents stay the default order: OpenRouter is never in it.
    assert OPENROUTER_ENGINE not in adapt.ENGINES and OPENROUTER_ENGINE == Engine("openrouter", None)


def test_open_stream_streams_the_openrouter_text_model(monkeypatch):
    calls = []
    # The adaptation reads the model behind the choice at call time, from one place.
    model = TextModel("some/model", "Some provider", {})
    monkeypatch.setattr(adapt, "OPENROUTER_TEXT_MODEL", model)

    async def openrouter(model, prompt, *, system_prompt=None):
        calls.append((model, prompt, system_prompt))
        yield "from "
        yield "openrouter"

    monkeypatch.setattr(adapt, "stream_openrouter_text", openrouter)

    async def scenario():
        async with asyncio.timeout(5):
            return [delta async for delta in adapt._open_stream(OPENROUTER_ENGINE, "p")]

    assert asyncio.run(scenario()) == ["from ", "openrouter"]
    assert calls == [(model, "p", None)]


def test_openrouter_limits(monkeypatch, fake_call):
    monkeypatch.undo()  # the values of the module, not the short ones of the fixture
    assert (adapt.OPENROUTER_FIRST_TEXT_SECONDS, adapt.OPENROUTER_NEXT_TEXT_SECONDS) == (5.0, 5.0)

    monkeypatch.setattr(adapt, "OPENROUTER_FIRST_TEXT_SECONDS", 0.05)
    monkeypatch.setattr(adapt, "OPENROUTER_NEXT_TEXT_SECONDS", 0.05)
    monkeypatch.setattr(adapt, "FIRST_TEXT_SECONDS", 2.0)
    monkeypatch.setattr(adapt, "NEXT_TEXT_SECONDS", 2.0)

    async def read(engine):
        async with asyncio.timeout(5):
            source = _source(engine)
            parts = await _rest(source)
            await source.aclose()
            return source, parts

    # OpenRouter: a late first text, and a late next text, are stalls.
    fake_call(0.2, "Hello there.")
    source, parts = asyncio.run(read(OPENROUTER_ENGINE))
    assert (parts, source.adapt_reason) == (FALLBACK, "stalled")
    fake_call("Hello ", 0.2, "there.")
    source, parts = asyncio.run(read(OPENROUTER_ENGINE))
    assert (parts, source.adapt_reason) == (FALLBACK, "stalled")

    # An agent keeps its own limits.
    fake_call(0.2, "Hello there.")
    source, parts = asyncio.run(read(HAIKU))
    assert (parts, source.adapt_reason) == (["Hello there."], None)
    fake_call("Hello ", 0.2, "there.")
    source, parts = asyncio.run(read(HAIKU))
    assert (parts, source.adapt_reason) == (["Hello there."], None)


async def _read_with_then(*, then=HAIKU, label=None):
    async with asyncio.timeout(5):
        label = {} if label is None else label
        source = _source(OPENROUTER_ENGINE, label=label, then=then)
        assert label == {"adapted": "pending:openrouter"}
        parts = await _rest(source)
        await source.aclose()
        return source, parts, label


@pytest.mark.parametrize(
    "openrouter_steps, reason",
    [
        ((0.05, OpenRouterTextError("http", 500)), "failed"),
        ((0.05, OpenRouterTextError("declined")), "failed"),
        ((HANG,), "stalled"),  # past the patched first-text limit
        ((0.05,), "empty"),
        ((0.05, "  ", "**"), "empty"),  # nothing to read aloud
    ],
)
def test_an_openrouter_failure_before_the_first_part_switches_to_the_agent(fake_call, monkeypatch, openrouter_steps, reason):
    monkeypatch.setattr(adapt, "OPENROUTER_FIRST_TEXT_SECONDS", 0.2)
    adapted = _sentences(600)
    fake = fake_call([openrouter_steps, (adapted,)])

    source, parts, label = asyncio.run(_read_with_then())
    splitter = PartSplitter(LIMIT, FIRST)
    assert parts == splitter.feed(adapted) + splitter.end()
    prompt = ADAPT_PROMPT.format(text=ORIGINAL)
    assert fake.calls == [(OPENROUTER_ENGINE, prompt), (HAIKU, prompt)]
    assert fake.streams[0].closed and fake.streams[1].finished
    assert source.adapted_by == "haiku"
    assert source.adapt_engine == "haiku"
    assert source.fallback_from == "openrouter"
    assert source.fallback_reason == reason
    assert source.adapt_reason is None
    assert source.adapted_text == adapted
    assert source.split is True
    # From the creation of the source: the wait for OpenRouter counts.
    assert isinstance(source.adapt_first_ms, int) and source.adapt_first_ms >= 40
    assert label == {
        "adapted": "haiku",
        "fallback_from": "openrouter",
        "fallback_reason": reason,
        "adapt_first_ms": source.adapt_first_ms,
    }


def test_text_received_from_a_failed_openrouter_call_is_dropped(fake_call, monkeypatch):
    # OpenRouter streams less than a first part, then stalls.
    monkeypatch.setattr(adapt, "OPENROUTER_NEXT_TEXT_SECONDS", 0.05)
    fake_call([("OpenRouter said this. ", HANG), (0.1, "Haiku said that.")])
    seen = []

    async def scenario():
        async with asyncio.timeout(5):
            label = {}
            source = _source(OPENROUTER_ENGINE, label=label, then=HAIKU)
            reading = asyncio.create_task(_rest(source))
            while "adapt_first_ms" not in label:
                await asyncio.sleep(0)
            seen.append((label["adapted"], label["adapt_first_ms"]))
            parts = await reading
            return source, parts, label

    source, parts, label = asyncio.run(scenario())
    [(adapted, openrouter_first_ms)] = seen
    assert adapted == "openrouter"
    assert parts == ["Haiku said that."]
    assert source.adapted_text == "Haiku said that."
    assert source.adapt_reason is None
    assert (source.fallback_from, source.fallback_reason) == ("openrouter", "stalled")
    # The agent's first text came after the OpenRouter stall and the agent's own delay.
    assert source.adapt_first_ms >= 140 and source.adapt_first_ms > openrouter_first_ms
    assert label == {
        "adapted": "haiku",
        "adapt_first_ms": source.adapt_first_ms,
        "fallback_from": "openrouter",
        "fallback_reason": "stalled",
    }


@pytest.mark.parametrize(
    "error",
    [
        OpenRouterTextError("http", 401),
        OpenRouterTextError("http", 402),
        OpenRouterTextError("http", 403),
        OpenRouterTextError("no_key"),
    ],
)
def test_a_refused_openrouter_call_is_reported_as_refused(fake_call, error):
    fake_call([(error,), ("Hello there.",)])
    source, parts, label = asyncio.run(_read_with_then())
    assert parts == ["Hello there."]
    assert (source.adapted_by, source.fallback_from, source.fallback_reason) == ("haiku", "openrouter", "refused")
    assert label["fallback_reason"] == "refused"


def test_openrouter_then_agent_both_fail_reads_the_original(fake_call):
    fake = fake_call([(OpenRouterTextError("http", 401),), ("Some text.", RuntimeError("boom"))])
    source, parts, label = asyncio.run(_read_with_then())
    assert parts == FALLBACK
    assert [engine for engine, _prompt in fake.calls] == [OPENROUTER_ENGINE, HAIKU]
    assert source.adapted_by is None
    # The engine of the last attempt is kept: the agent that failed after OpenRouter.
    assert source.adapt_engine == "haiku"
    assert source.adapt_reason == "failed"
    assert source.adapt_first_ms is None
    assert source.adapted_text is None
    assert (source.fallback_from, source.fallback_reason) == ("openrouter", "refused")
    assert label == {"adapted": "no", "adapt_reason": "failed", "fallback_from": "openrouter", "fallback_reason": "refused"}


def test_openrouter_without_an_agent_reads_the_original(fake_call):
    fake = fake_call([(OpenRouterTextError("http", 500),), ("never read",)])
    source, parts, label = asyncio.run(_read_with_then(then=None))
    assert parts == FALLBACK
    assert [engine for engine, _prompt in fake.calls] == [OPENROUTER_ENGINE]
    assert source.adapted_by is None
    assert source.adapt_engine == "openrouter"
    assert source.adapt_reason == "failed"
    assert (source.fallback_from, source.fallback_reason) == (None, None)
    assert label == {"adapted": "no", "adapt_reason": "failed"}


def test_an_openrouter_failure_after_the_first_part_stops_the_reading(fake_call):
    received = _sentences(600) + " A complete sentence. A sentence cut in the mid"
    failing = asyncio.Event()
    fake = fake_call([(received[:400], received[400:], failing, OpenRouterTextError("stream_error")), ("never",)])

    async def scenario():
        async with asyncio.timeout(5):
            source = _source(OPENROUTER_ENGINE, then=HAIKU)
            parts = [await source.next_part()]
            failing.set()
            with pytest.raises(SpeechError) as raised:
                await _rest(source, parts)
            await source.aclose()
            return source, parts, raised.value

    source, parts, error = asyncio.run(scenario())
    splitter = PartSplitter(LIMIT, FIRST)
    expected = splitter.feed(received)
    expected.append(splitter.tail())
    assert parts == expected
    assert error.message == ADAPT_STOPPED
    assert error.log_message == "adaptation failed after the first part"
    assert [engine for engine, _prompt in fake.calls] == [OPENROUTER_ENGINE]
    assert (source.adapted_by, source.adapt_reason) == ("openrouter", "failed")
    assert (source.fallback_from, source.fallback_reason) == (None, None)


def test_aclose_during_openrouter_starts_no_agent_call(fake_call):
    fake = fake_call([(HANG,), ("Agent text.",)])

    async def scenario():
        async with asyncio.timeout(5):
            source = _source(OPENROUTER_ENGINE, then=HAIKU)
            reading = asyncio.create_task(source.next_part())
            while fake.task is None:
                await asyncio.sleep(0)
            await source.aclose()
            assert fake.streams[0].closed
            assert await reading is None
            await asyncio.sleep(0.01)
            return source

    source = asyncio.run(scenario())
    assert [engine for engine, _prompt in fake.calls] == [OPENROUTER_ENGINE]
    assert source.adapt_reason is None and source.fallback_reason is None


def test_aclose_after_the_switch_closes_the_agent_call(fake_call):
    fake = fake_call([(OpenRouterTextError("http", 500),), (HANG,)])

    async def scenario():
        async with asyncio.timeout(5):
            label = {}
            source = _source(OPENROUTER_ENGINE, label=label, then=HAIKU)
            reading = asyncio.create_task(source.next_part())
            while len(fake.calls) < 2:
                await asyncio.sleep(0)
            assert label == {"adapted": "pending:haiku", "fallback_from": "openrouter", "fallback_reason": "failed"}
            assert not fake.streams[1].closed
            await source.aclose()
            assert fake.streams[1].closed and not fake.streams[1].finished
            assert await reading is None
            return source

    source = asyncio.run(scenario())
    assert len(fake.calls) == 2
    assert source.adapted_by == "haiku" and source.adapt_reason is None


def test_the_agent_after_openrouter_keeps_its_own_limits(fake_call, monkeypatch):
    monkeypatch.setattr(adapt, "OPENROUTER_FIRST_TEXT_SECONDS", 0.05)
    monkeypatch.setattr(adapt, "OPENROUTER_NEXT_TEXT_SECONDS", 0.05)
    monkeypatch.setattr(adapt, "FIRST_TEXT_SECONDS", 1.0)
    monkeypatch.setattr(adapt, "NEXT_TEXT_SECONDS", 1.0)
    # OpenRouter fails at once; the agent's texts come later than OpenRouter's limits, within its own.
    fake = fake_call([(OpenRouterTextError("http", 500),), (0.3, "Hello ", 0.3, "there.")])
    source, parts, label = asyncio.run(_read_with_then())
    assert parts == ["Hello there."]
    assert [engine for engine, _prompt in fake.calls] == [OPENROUTER_ENGINE, HAIKU]
    assert source.adapted_by == "haiku"
    assert source.adapt_reason is None
    assert (source.fallback_from, source.fallback_reason) == ("openrouter", "failed")
    assert source.adapted_text == "Hello there."
