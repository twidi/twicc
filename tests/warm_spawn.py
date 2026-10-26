"""Start the compute workers from a warm interpreter instead of a fresh one.

Production starts each compute worker with ``spawn``: a new interpreter that
imports Django, the settings and the compute modules (~2 s with the agent SDKs
they pull in). The suite starts about eighty of them. Here a ``forkserver``
imports all that once (:mod:`tests.warm_spawn_preload`), then forks each worker
from it: the worker still runs in its own process, with its own memory, its own
DB connections and the same arguments, sent the same way.

Two things a ``spawn`` child takes from its parent at start, and a forkserver
child takes from the forkserver instead, are handed over explicitly: the
environment (tests drive their compute factories through variables set by
``monkeypatch``) and the working directory. The variables that
``twicc.settings_test`` sets or drops at import keep the worker's own values,
as they would after the import a ``spawn`` child runs.
"""

import functools
import multiprocessing
import os

from twicc import settings_test

PRELOAD_MODULE = "tests.warm_spawn_preload"
_SETTINGS_KEYS = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CODEX_HOME")

_forkserver = multiprocessing.get_context("forkserver")
_forkserver.set_forkserver_preload([PRELOAD_MODULE])


def _settings_managed(key):
    return key in _SETTINGS_KEYS or (key.startswith("TWICC_") and key not in settings_test._TEST_SWITCHES)


def _run_in_parent_context(environ, cwd, target, /, *args, **kwargs):
    """Runs in the worker: take the parent's environment and directory, then the real target."""
    own = {key: value for key, value in os.environ.items() if _settings_managed(key)}
    os.environ.clear()
    os.environ.update({key: value for key, value in environ.items() if not _settings_managed(key)})
    os.environ.update(own)
    os.chdir(cwd)
    return target(*args, **kwargs)


class WarmSpawnProcess(_forkserver.Process):
    """A ``spawn`` context ``Process`` started from the preloaded forkserver."""

    def start(self):
        self._target = functools.partial(_run_in_parent_context, dict(os.environ), os.getcwd(), self._target)
        super().start()
