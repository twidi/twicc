"""Import the server boot module without its environment purge leaking into pytest.

``twicc.cli.run`` cleans ``os.environ`` at import, for the processes the server
launches: the provider markers, ``DJANGO_SETTINGS_MODULE`` and TwiCC's own Python
environment (see ``twicc.server_env``). In a test, that ``os.environ`` is the
pytest process's own, for the rest of the session. The compute workers'
forkserver (``tests.warm_spawn``), started later, would then lack
``DJANGO_SETTINGS_MODULE`` and fail its preload, and every later compute test
with it.
"""

import importlib
import os


def import_cli_run():
    """Return ``twicc.cli.run``, with ``os.environ`` as it was before its first import."""
    saved = dict(os.environ)
    try:
        return importlib.import_module("twicc.cli.run")
    finally:
        os.environ.clear()
        os.environ.update(saved)
