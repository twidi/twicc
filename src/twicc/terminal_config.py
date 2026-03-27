"""Read/write terminal configuration (custom combos and snippets).

File: <data_dir>/terminal-config.json
"""

import os
import tempfile

import orjson

from twicc.paths import get_terminal_config_path


def read_terminal_config() -> dict:
    """Read terminal-config.json. Returns empty config if file doesn't exist or is invalid."""
    path = get_terminal_config_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {"combos": [], "snippets": {}}


def write_terminal_config(config: dict) -> None:
    """Write terminal-config.json atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_terminal_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = orjson.dumps(config, option=orjson.OPT_INDENT_2)

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
