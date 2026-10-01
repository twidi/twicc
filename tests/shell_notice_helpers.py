"""Helpers for the background shell notice tests.

``pytest-asyncio`` is not installed: an async test body runs through
:func:`run_async`, which keeps the body's signature so pytest still injects
fixtures (``monkeypatch``, ...).
"""

import asyncio
import contextlib
import inspect


def run_async(fn):
    """Turn ``async def test_x(...)`` into a sync test that runs it with ``asyncio.run``."""
    def wrapper(**kwargs):
        asyncio.run(fn(**kwargs))

    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    wrapper.__module__ = fn.__module__
    wrapper.__signature__ = inspect.signature(fn)
    return wrapper


def first_step(coro) -> None:
    """Run a coroutine up to its first suspension (or error), then close it.

    Used to check what a method does in its very first statements without
    driving the rest of it (which needs a live SDK).
    """
    with contextlib.suppress(BaseException):
        coro.send(None)
    with contextlib.suppress(BaseException):
        coro.close()
