"""Keep TwiCC's own Django settings module out of what the server launches.

The server needs ``DJANGO_SETTINGS_MODULE=twicc.settings`` only to boot Django
(``cli/run.py``). Every agent, terminal and subprocess it launches inherits the
server's ``os.environ``; a leftover value there would override the settings
module of any *other* Django project an agent works on (its ``manage.py`` would
load twicc's settings instead of its own).

Processes that need the variable set it themselves: the ``twicc`` CLI
(``twicc/cli/__init__.py``) and the compute worker
(``providers/background_compute_task.py``).
"""

from __future__ import annotations

from collections.abc import MutableMapping

DJANGO_SETTINGS_ENV = "DJANGO_SETTINGS_MODULE"


def strip_django_settings_env(env: MutableMapping[str, str]) -> None:
    """Remove ``DJANGO_SETTINGS_MODULE`` from ``env`` in place, if present.

    Call it on ``os.environ`` right before launching anything that inherits it:
    the SDKs copy ``os.environ`` at spawn time and cannot unset an inherited key.
    """
    env.pop(DJANGO_SETTINGS_ENV, None)
