"""Run the real statistics consumer with direct isolated test writer admission."""
import pytest

def drain_statistics(monkeypatch=None):
    if monkeypatch is None:
        with pytest.MonkeyPatch.context() as patch:
            return drain_statistics(patch)
    import asyncio
    from twicc.providers import db_writer
    from twicc.providers.statistics_queue import capture_queue
    from twicc.providers.statistics_runtime import StatisticsConsumer

    batch = capture_queue()
    if batch is None:
        return

    async def admitted(function, *args):
        return await asyncio.to_thread(function, *args)

    monkeypatch.setattr(db_writer, 'publish_statistics_sync', admitted)

    async def run():
        consumer = StatisticsConsumer()
        try:
            await consumer.run_batch(batch)
        finally:
            await consumer.close()
    asyncio.run(run())

