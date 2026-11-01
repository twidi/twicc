"""Tests for the part source protocol and the static source (twicc.tts.part_source)."""

import asyncio

from twicc.tts.part_source import PartSource, StaticParts


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
