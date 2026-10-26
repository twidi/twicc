"""Test settings for pytest-django.

No test may touch a live instance: the data dir, the provider homes and the
instance configuration all come from a throwaway directory, whatever the
environment the suite is launched from (the main repo, a worktree, an agent
session that inherits the backend's ``TWICC_DATA_DIR``).
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from twicc import paths

# One throwaway root per pytest process, removed when it exits — otherwise
# every run would leave one more directory behind in the system temp dir.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="twicc-test-")).resolve()
atexit.register(shutil.rmtree, _TEST_ROOT, True)

# --- Data dir isolation -------------------------------------------------------
# Everything below runs BEFORE the ``twicc.settings`` star-import, which creates
# the data dir tree and the secret key at import. Only ``twicc/__init__`` ran
# before this module (through ``twicc.cli``): it loaded the ``.env`` of the data
# dir the suite inherited, and wrote nothing.
#
# 1. Forbid the real data dirs: ``get_data_dir()`` raises for them and for
#    everything below them, so a test that drops or overrides the isolation
#    fails loudly instead of writing into a live instance (on 2026-10-17 a run
#    replaced the developer's ``~/.twicc/settings.json`` with defaults). A dir
#    that contains the test root (``TWICC_DATA_DIR=/tmp``) cannot be forbidden.
paths._forbid_data_dirs(*(
    data_dir for data_dir in (paths.DEFAULT_DATA_DIR, paths.get_data_dir())
    if not _TEST_ROOT.is_relative_to(data_dir.resolve())
))
# 2. Undo the ``.env`` load: an instance's configuration must not change what
#    the tests observe. The loader stays marked as done, so it never runs again.
paths._undo_env_load()
# 3. Drop the TwiCC configuration inherited from the environment too: an agent
#    session inherits the backend's whole environment, ``.env`` values included
#    (``TWICC_PASSWORD_HASH``, ``TWICC_DEBUG``, ``TWICC_BIN`` pointing at the main
#    repo, ``TWICC_REMOTE_URL``…). Kept: the switches that opt a run into extra
#    tests; a test that needs any other variable sets it itself.
_TEST_SWITCHES = frozenset({
    "TWICC_CLAUDE_INTEGRATION", "TWICC_CODEX_INTEGRATION", "TWICC_UPDATE_FIXTURES", "TWICC_TIMELINE_BENCH",
})
for _key in [key for key in os.environ if key.startswith("TWICC_") and key not in _TEST_SWITCHES]:
    del os.environ[_key]
# 4. Point the data dir at the throwaway root. ``DEFAULT_DATA_DIR`` keeps the
#    real path on purpose: a test that unsets the variable hits the guard of
#    step 1, and the tmux socket names stay suffixed (the bare ``twicc`` names
#    belong to the production instance).
TEST_DATA_DIR = _TEST_ROOT / "data"
TEST_DATA_DIR.mkdir()
os.environ[paths.TWICC_DATA_DIR_ENV] = str(TEST_DATA_DIR)

from twicc.settings import *  # noqa: E402, F401, F403

from twicc import provider_homes  # noqa: E402

# Isolate the provider homes: no test may read or write the developer's real
# ``~/.claude`` / ``~/.codex`` (nor reach the real keychain entry — hence a
# credentials dir set to a PATH, never empty). Order matters: by the time the
# star-import above returned, the provider ``constants`` modules were imported
# and ``twicc.settings`` had resolved and cached the homes. Nothing on the read
# side is evaluated at import (every path is computed from ``provider_homes`` at
# call time), so setting the variables now and resetting the resolver's cache
# is enough. ``ensure_env_loaded()`` is a no-op (see step 2 above), so it
# neither overrides nor drops these values.
os.environ["CLAUDE_CONFIG_DIR"] = str(_TEST_ROOT / "claude")
os.environ["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = str(_TEST_ROOT / "claude-credentials")
os.environ["CODEX_HOME"] = str(_TEST_ROOT / "codex")
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

# Same reasoning for the dev-worktree bypass: a suite run from a worktree
# inherits devctl's variable, and a test asserting a refusal would silently
# pass through instead. Tests that want it opt in explicitly.
TWICC_DEV_LOCAL_BYPASS = False

# No statistics consumer started with the DB writer: on the shared-cache memory
# DB, the consumer's concurrent queue read makes a writer's append fail at once
# ("database table is locked"). Tests that need the real consumer with the
# writer opt in through the ``settings`` fixture.
TWICC_STATISTICS_CONSUMER_AUTOSTART = False

# Disable logging during tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {},
    "loggers": {},
}

# Test compute version
CLAUDE_CODE_COMPUTE_VERSION = 99
