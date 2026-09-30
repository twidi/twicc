"""Select the migration-only SQLite backend without changing runtime settings."""

import os

from twicc.settings import *  # noqa: F401, F403
from twicc.settings import DATABASES as RUNTIME_DATABASES

DATABASES = {
    alias: {**config}
    for alias, config in RUNTIME_DATABASES.items()
}
if os.environ.get("TWICC_SQLITE_STANDARD_MIGRATIONS") != "1":
    DATABASES["default"]["ENGINE"] = "twicc.db.backends.sqlite3"
