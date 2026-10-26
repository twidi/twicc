"""Imported once by the compute workers' forkserver (see :mod:`tests.warm_spawn`).

Everything a compute worker imports before its first command: Django and the
settings (``django.setup()``, which the worker calls again as a no-op), the
worker loop and both providers' compute modules, with the agent SDKs they
import. The test factories import nothing heavier than these.
"""

import django

django.setup()

import pytest  # noqa: E402, F401

import twicc.providers.background_compute_task  # noqa: E402, F401
import twicc.providers.claude_code.compute  # noqa: E402, F401
import twicc.providers.codex.background_compute  # noqa: E402, F401
import twicc.providers.codex.compute  # noqa: E402, F401
