"""Keep TwiCC's own runtime environment out of what the server launches.

Every agent, terminal and subprocess the server launches inherits its
``os.environ``. Two parts of that environment belong to TwiCC alone and must
not reach them: its Django settings module, and its own Python environment.

Django settings module
----------------------

The server needs ``DJANGO_SETTINGS_MODULE=twicc.settings`` only to boot Django
(``cli/run.py``). Every agent, terminal and subprocess it launches inherits the
server's ``os.environ``; a leftover value there would override the settings
module of any *other* Django project an agent works on (its ``manage.py`` would
load twicc's settings instead of its own).

Processes that need the variable set it themselves: the ``twicc`` CLI
(``twicc/cli/__init__.py``) and the compute worker
(``providers/background_compute_task.py``).

Python environment
------------------

``uv run`` (the dev launch) sets ``VIRTUAL_ENV`` and puts the venv ``bin/``
first on ``PATH``; ``uvx`` puts its ephemeral environment's ``bin/`` first on
``PATH``. Left there, an agent working on *another* project runs TwiCC's
``python``, ``pytest``, ``mypy``, ``django-admin``… instead of its own, and
tools that honour ``VIRTUAL_ENV`` (Poetry, Hatch…) use or modify TwiCC's venv.

The server itself needs neither: its interpreter fixes its environment at
startup, and its Python subprocesses use the absolute ``sys.executable``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping

DJANGO_SETTINGS_ENV = "DJANGO_SETTINGS_MODULE"
VIRTUAL_ENV_ENV = "VIRTUAL_ENV"
# Set by ``uv run`` to count its nesting; meaningless outside of it.
UV_RUN_RECURSION_DEPTH_ENV = "UV_RUN_RECURSION_DEPTH"


def strip_django_settings_env(env: MutableMapping[str, str]) -> None:
    """Remove ``DJANGO_SETTINGS_MODULE`` from ``env`` in place, if present.

    Call it on ``os.environ`` right before launching anything that inherits it:
    the SDKs copy ``os.environ`` at spawn time and cannot unset an inherited key.
    """
    env.pop(DJANGO_SETTINGS_ENV, None)


def _same_path(a: str, b: str) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


def strip_own_python_env(
    env: MutableMapping[str, str],
    *,
    prefix: str = sys.prefix,
    base_prefix: str = sys.base_prefix,
) -> None:
    """Remove TwiCC's own Python environment from ``env`` in place.

    Only when TwiCC runs in a virtual environment (``prefix != base_prefix``),
    and only what designates *that* environment:

    - the ``PATH`` entries equal to ``<prefix>/bin`` (every occurrence; never
      a parent or sibling directory: without a venv, ``prefix`` is ``/usr``);
    - ``VIRTUAL_ENV`` when it points to ``prefix`` (a user's own venv stays).

    ``UV_RUN_RECURSION_DEPTH`` goes in every case.

    Call it on ``os.environ`` once at boot (``cli/run.py``), after Django
    settings are loaded: ``twicc.settings`` reads ``UV_RUN_RECURSION_DEPTH`` to
    build ``TWICC_BIN``.
    """
    env.pop(UV_RUN_RECURSION_DEPTH_ENV, None)
    if _same_path(prefix, base_prefix):
        return

    bin_dir = os.path.join(prefix, "bin")
    path = env.get("PATH")
    if path is not None:
        entries = path.split(os.pathsep)
        kept = [entry for entry in entries if not (entry and _same_path(entry, bin_dir))]
        if len(kept) != len(entries):
            env["PATH"] = os.pathsep.join(kept)

    virtual_env = env.get(VIRTUAL_ENV_ENV)
    if virtual_env and _same_path(virtual_env, prefix):
        del env[VIRTUAL_ENV_ENV]
