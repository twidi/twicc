"""Tests for the part source protocol and the static source (twicc.tts.part_source)."""

import asyncio

from twicc.tts.part_source import PartSource, StaticParts, as_part_source


def test_static_parts():
    async def scenario():
        async with asyncio.timeout(5):
            source = StaticParts(["One.", "Two."])
            assert isinstance(source, PartSource)
            assert source.split is True
            assert [await source.next_part(), await source.next_part()] == ["One.", "Two."]
            assert await source.next_part() is None
            assert await source.next_part() is None
            await source.aclose()

            single = StaticParts(["Only."])
            assert single.split is False
            assert await single.next_part() == "Only."
            assert await single.next_part() is None

            parts = ["Kept."]
            copied = StaticParts(parts)
            parts.append("Added later.")
            assert await copied.next_part() == "Kept."
            assert await copied.next_part() is None

            empty = StaticParts([])
            assert empty.split is False
            assert await empty.next_part() is None

    asyncio.run(scenario())


def test_as_part_source():
    async def scenario():
        async with asyncio.timeout(5):
            alone = as_part_source("The text.", None)
            assert [await alone.next_part(), await alone.next_part()] == ["The text.", None]
            assert alone.split is False

            listed = as_part_source("The text.", ["One.", "Two."])
            assert isinstance(listed, StaticParts)
            assert listed.split is True
            assert await listed.next_part() == "One."

            source = StaticParts(["Own."])
            assert as_part_source("The text.", source) is source

    asyncio.run(scenario())


def test_prefixed_parts_preserves_body_and_closes_it():
    from twicc.tts.part_source import PrefixedParts

    async def scenario():
        body = StaticParts(["Body one.", "Body two."])
        prefix = ["Project.", "Session."]
        source = PrefixedParts(prefix, body)
        prefix.append("Added later.")
        assert isinstance(source, PartSource)
        assert source.split is True
        assert [await source.next_part() for _ in range(5)] == [
            "Project.", "Session.", "Body one.", "Body two.", None,
        ]
        await source.aclose()

        body = StaticParts(["Never read."])
        source = PrefixedParts(["Project."], body)
        await source.aclose()
        assert await source.next_part() is None
        assert await body.next_part() is None

        for parts in (["One."], ["One.", "Two."]):
            body = StaticParts(parts)
            source = PrefixedParts([], body)
            assert source.split is body.split
            assert await source.next_part() == "One."
            await source.aclose()
            assert await body.next_part() is None

    asyncio.run(scenario())


def test_prefixed_parts_closes_body_adaptation(monkeypatch):
    from twicc.tts import adapt
    from twicc.tts.part_source import PrefixedParts

    async def scenario():
        async with asyncio.timeout(5):
            started = asyncio.Event()
            closed = asyncio.Event()

            async def open_stream(engine, prompt):
                started.set()
                try:
                    await asyncio.Event().wait()
                    yield "Unused."
                finally:
                    closed.set()

            monkeypatch.setattr(adapt, "_open_stream", open_stream)
            engine = adapt.OPENROUTER_ENGINE
            body = adapt.AdaptedParts("Body.", engine, 100, 100, fallback=["Body."], label={})
            source = PrefixedParts(["Project."], body)
            await source.aclose()
            assert await body.next_part() is None
            assert not started.is_set()

            body = adapt.AdaptedParts("Body.", engine, 100, 100, fallback=["Body."], label={})
            source = PrefixedParts([], body)
            assert source.split is None
            pending = asyncio.create_task(source.next_part())
            await started.wait()
            await source.aclose()
            assert await pending is None
            assert closed.is_set()

    asyncio.run(scenario())
