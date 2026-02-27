"""
Read/write synced settings from/to settings.json in the data directory.

Synced settings are user preferences that should be shared across all devices
(e.g., default model, permission mode, title prompt). They are stored as a
simple JSON object in <data_dir>/settings.json.

The backend acts as a dumb store — it doesn't know the schema or defaults.
The frontend owns the schema and handles merging/validation.
"""

import os
import tempfile

import orjson

from twicc.paths import get_synced_settings_path


def read_synced_settings() -> dict:
    """Read synced settings from settings.json.

    Returns an empty dict if the file doesn't exist or is invalid.
    """
    path = get_synced_settings_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {}


def write_synced_settings(data: dict) -> None:
    """Write synced settings to settings.json atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_synced_settings_path()
    content = orjson.dumps(data, option=orjson.OPT_INDENT_2)

    # Write to a temp file in the same directory, then atomically replace.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
