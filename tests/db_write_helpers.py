"""Run lock-guarded writes in tests without the real DB writer."""

import asyncio
import sys
from contextlib import asynccontextmanager

from twicc.providers import db_writer
from twicc.providers.compute_executor import start_compute_executor, stop_compute_executor


async def _direct_write(coro_factory, *, content_activity=False, **kwargs):
    """Stand-in for ``run_under_db_write_lock``: run the factory at once, without a lock.

    ``content_activity=True`` keeps the real behaviour: the active-session snapshot from the agent registry.
    """
    if content_activity:
        return await db_writer._run_with_content_activity(coro_factory)
    return await coro_factory()


@asynccontextmanager
async def _direct_admission():
    """Stand-in for ``storage_admission``: no fence to acquire without a writer."""
    yield


#: The writer-owned entry points :func:`run_with_direct_writes` replaces, with their stand-ins.
_DIRECT_STAND_INS = {"run_under_db_write_lock": _direct_write, "storage_admission": _direct_admission}


def _swap(name, old, new):
    """Point every ``twicc`` module attribute ``name`` that holds ``old`` at ``new``.

    Services import the function at module level (one attribute per module) or lazily
    from ``db_writer`` at call time (the ``db_writer`` attribute covers them).
    """
    for module_name, module in list(sys.modules.items()):
        if (module_name == "twicc" or module_name.startswith("twicc.")) and getattr(module, name, None) is old:
            setattr(module, name, new)


def run_with_direct_writes(factory):
    """Run ``await factory()`` with lock-guarded writes run directly: no DB writer, no statistics consumer.

    ``run_under_db_write_lock`` becomes a passthrough, ``storage_admission`` a no-op, and the
    compute executor (``run_compute_sync``) runs for the call. Only a test about the writer itself
    (lock holding, admission, priorities, lifecycle) starts the real one.
    """
    reals = {name: getattr(db_writer, name) for name in _DIRECT_STAND_INS}

    async def runner():
        start_compute_executor()
        try:
            return await factory()
        finally:
            await stop_compute_executor()

    for name, stand_in in _DIRECT_STAND_INS.items():
        _swap(name, reals[name], stand_in)
    try:
        return asyncio.run(runner())
    finally:
        # Also restores a module first imported during the call (it bound the stand-in).
        for name, stand_in in _DIRECT_STAND_INS.items():
            _swap(name, stand_in, reals[name])
