"""Remove async question tools from the bundled Codex catalogue.

Only interactive agents opt into this process-level override. The catalogue
keeps every other model field and is regenerated from each binary version.
It replaces remote catalogue refresh for that process; no provider config or
provider cache is changed.
"""

import hashlib
import os
import tempfile
from pathlib import Path

import orjson

from .hermetic_catalog import bundled_catalog
from .catalog_cache import catalog_cache

TRANSFORM_VERSION = 1
ASYNC_QUESTION_TOOLS = frozenset({"request_user_input_async", "send_user_message_async"})


def transform_catalog(source: dict) -> dict:
    """Keep the complete catalogue, excluding both names of the async question tool."""
    models = source.get("models") if isinstance(source, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("The bundled Codex catalogue has no model list")
    filtered = []
    for entry in models:
        tools = entry.get("experimental_supported_tools") if isinstance(entry, dict) else None
        if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
            raise ValueError("The bundled Codex catalogue has invalid experimental_supported_tools")
        filtered.append({
            **entry,
            "experimental_supported_tools": [tool for tool in tools if tool not in ASYNC_QUESTION_TOOLS],
        })
    return {**source, "models": filtered}


def ensure_catalog(binary: Path, *, cache_dir: Path | None = None) -> Path:
    """Generate an atomic, content-addressed catalogue or reuse a valid cached file.

    Run off the event loop. The shared bundled extractor memoizes subprocesses
    per binary path; pinned runtimes use version-specific paths.
    """
    version, source = bundled_catalog(binary)
    content = orjson.dumps(transform_catalog(source), option=orjson.OPT_SORT_KEYS)
    digest = hashlib.sha256(content).hexdigest()
    safe_version = "".join(
        char if char.isalnum() or char in ".-" else "_"
        for char in version.removeprefix("codex-cli").strip()
    )
    if cache_dir is None:
        from twicc.paths import get_data_dir

        cache_dir = get_data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"interactive-codex-catalog-{safe_version}-{digest[:16]}-v{TRANSFORM_VERSION}.json"
    with catalog_cache(path, prefix="interactive-codex-catalog"):
        _write_catalog(path, content)
    return path


def _write_catalog(path: Path, content: bytes) -> None:
    try:
        if path.read_bytes() == content:
            return
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
