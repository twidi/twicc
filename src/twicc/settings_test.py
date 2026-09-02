"""Test settings for pytest-django."""

import os
import tempfile
from pathlib import Path

from twicc.settings import *  # noqa: F401, F403

from twicc import provider_homes

# Isolate the provider homes: no test may read or write the developer's real
# ``~/.claude`` / ``~/.codex`` (nor reach the real keychain entry — hence a
# credentials dir set to a PATH, never empty). Order matters: by the time the
# star-import above returned, ``import twicc`` had loaded the .env, the
# provider ``constants`` modules were imported and ``twicc.settings`` had
# resolved and cached the real homes. Nothing on the read side is evaluated at
# import (every path is computed from ``provider_homes`` at call time), so
# setting the variables now and resetting the resolver's cache is enough.
# ``ensure_env_loaded()`` is a no-op by now (once per process), so it neither
# overrides nor drops these values.
_PROVIDER_HOMES_ROOT = Path(tempfile.mkdtemp(prefix="twicc-test-provider-homes-"))
os.environ["CLAUDE_CONFIG_DIR"] = str(_PROVIDER_HOMES_ROOT / "claude")
os.environ["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = str(_PROVIDER_HOMES_ROOT / "claude-credentials")
os.environ["CODEX_HOME"] = str(_PROVIDER_HOMES_ROOT / "codex")
provider_homes.reset_cache()
CLAUDE_CONFIG_DIR = provider_homes.claude_config_dir().path
CLAUDE_SECURE_STORAGE_DIR = provider_homes.claude_secure_storage_dir().path
CODEX_HOME = provider_homes.codex_home().path
PROVIDER_HOMES_DESCRIPTION = provider_homes.describe_provider_homes()

# Use in-memory SQLite for tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Fixed key: tests must not depend on the developer's real <data_dir>/secret-key
# (twicc.settings loads or creates it at import).
SECRET_KEY = "test-secret-key"

# Disable password protection in tests. The setting is otherwise sourced from
# the developer's local ``.env`` via :mod:`twicc.settings`, which would make
# every test that hits the HTTP stack require an authenticated session.
TWICC_PASSWORD_HASH = ""

# Disable logging during tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {},
    "loggers": {},
}

# Test compute version
CLAUDE_CODE_COMPUTE_VERSION = 99
