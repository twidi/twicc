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
