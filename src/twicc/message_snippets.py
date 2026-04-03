"""Read/write message snippets configuration.

File: <data_dir>/message-snippets.json
"""

import os
import tempfile

import orjson

from twicc.paths import get_message_snippets_config_path


def read_message_snippets_config() -> dict:
    """Read message-snippets.json. Returns empty config if file doesn't exist or is invalid."""
    path = get_message_snippets_config_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {"snippets": {}}


def write_message_snippets_config(config: dict) -> None:
    """Write message-snippets.json atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_message_snippets_config_path()
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
