"""
The minimal Codex model catalogue of the hermetic calls (design §5.4 (a)).

Codex has no "empty tool list" option, but a catalogue file (``model_catalog_json``)
overrides the model entries of a process, including the tools and the base
instructions. The file is derived from the installed binary's bundled catalogue
(``codex debug models --bundled``: offline, deterministic), transformed by a fixed
versioned list of overrides, validated, and cached.
"""
import hashlib
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import orjson

from twicc.providers.hermetic import HermeticConfigError

from .catalog_cache import catalog_cache

TRANSFORM_VERSION = 1
# Keys of the bundled entry when TRANSFORM_VERSION was last reviewed; the diagnostic (O2) warns on new ones.
KNOWN_ENTRY_KEYS = frozenset({
    "additional_speed_tiers",
    "apply_patch_tool_type",
    "availability_nux",
    "base_instructions",
    "comp_hash",
    "context_window",
    "default_reasoning_level",
    "default_reasoning_summary",
    "default_service_tier",
    "default_verbosity",
    "description",
    "display_name",
    "effective_context_window_percent",
    "experimental_supported_tools",
    "include_apps_usage_instructions",
    "include_plugin_usage_instructions",
    "include_skills_usage_instructions",
    "input_modalities",
    "max_context_window",
    "model_messages",
    "multi_agent_version",
    "node_repl_auto_review_required",
    "node_repl_disabled",
    "priority",
    "service_tiers",
    "shell_type",
    "slug",
    "support_verbosity",
    "supported_in_api",
    "supported_reasoning_levels",
    "supports_experimental_context",
    "supports_image_detail_original",
    "supports_reasoning_effort_updates",
    "supports_search_tool",
    "tool_mode",
    "truncation_policy",
    "upgrade",
    "use_responses_lite",
    "visibility",
    "web_search_tool_type",
})
SUBPROCESS_TIMEOUT_SECONDS = 20

PRODUCTION_BASE_INSTRUCTIONS = (
    "You write short answers from the text you are given. You have no tools. Answer with the result only."
)
NEUTRAL_BASE_INSTRUCTIONS = "You are a helpful assistant."

# Fields the transformation sets, other than base_instructions (value for every variant).
_OVERRIDES: dict[str, object] = {
    "model_messages": None,
    "include_apps_usage_instructions": False,
    "include_plugin_usage_instructions": False,
    "include_skills_usage_instructions": False,
    "shell_type": "disabled",
    "tool_mode": None,
    "apply_patch_tool_type": None,
    "supports_search_tool": False,
    "experimental_supported_tools": [],
    "multi_agent_version": None,
}
# A source entry may omit these when null; the output then carries null.
NULLABLE_KEYS = ("tool_mode", "multi_agent_version")

_memo: dict[str, tuple[str, dict]] = {}
_memo_lock = threading.Lock()


def _reset_memo() -> None:
    with _memo_lock:
        _memo.clear()


def _base_instructions(variant: str) -> str:
    if variant == "production":
        return PRODUCTION_BASE_INSTRUCTIONS
    if variant == "neutral":
        return NEUTRAL_BASE_INSTRUCTIONS
    raise HermeticConfigError("catalog", f"Unknown catalogue variant {variant!r}")


def transform_entry(entry: dict, *, variant: str) -> dict:
    """Return a copy of the bundled ``entry`` with the tool-removing overrides applied."""
    out = dict(entry)
    for key in _OVERRIDES:
        if key not in out and key not in NULLABLE_KEYS:
            raise HermeticConfigError("catalog", f"Bundled catalogue schema changed: no {key!r} in the entry")
    out.update({k: (list(v) if isinstance(v, list) else v) for k, v in _OVERRIDES.items()})
    out["base_instructions"] = _base_instructions(variant)
    return out


def catalog_problem(data: dict, *, model: str, variant: str) -> str | None:
    """Why ``data`` is not exactly one model, the requested one, with every override in place; ``None`` if fine.

    Does not raise (and so does not log): the cache path uses it to regenerate a damaged file silently.
    """
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list) or len(models) != 1:
        return "The hermetic catalogue must contain exactly one model"
    entry = models[0]
    if not isinstance(entry, dict):
        return "The hermetic catalogue entry is not an object"
    if entry.get("slug") != model:
        return f"The hermetic catalogue holds {entry.get('slug')!r}, not {model!r}"
    expected = {**_OVERRIDES, "base_instructions": _base_instructions(variant)}
    for key, value in expected.items():
        if key in NULLABLE_KEYS:
            actual = entry.get(key)  # absent and null are the same
        elif key in entry:
            actual = entry[key]
        else:
            return f"The hermetic catalogue has no {key!r}"
        if actual != value:
            return f"The hermetic catalogue has {key}={actual!r}, expected {value!r}"
    return None


def validate_catalog(data: dict, *, model: str, variant: str) -> None:
    """Fail unless ``data`` is a valid hermetic catalogue (``HermeticConfigError(catalog)``)."""
    problem = catalog_problem(data, model=model, variant=variant)
    if problem is not None:
        raise HermeticConfigError("catalog", problem)


def _file_problem(path: Path, model: str, variant: str) -> str | None:
    try:
        data = orjson.loads(Path(path).read_bytes())
    except (OSError, orjson.JSONDecodeError) as exc:
        return f"Cannot read the hermetic catalogue {path}: {exc}"
    return catalog_problem(data, model=model, variant=variant)


def validate_catalog_file(path: Path, model: str, variant: str) -> None:
    """Validate a catalogue file; ``HermeticConfigError(catalog)`` if unreadable or invalid."""
    problem = _file_problem(path, model, variant)
    if problem is not None:
        raise HermeticConfigError("catalog", problem)


def _run(argv: list[str]) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HermeticConfigError("catalog", f"{' '.join(argv[1:])} failed: {exc!r}") from exc
    if proc.returncode != 0:
        raise HermeticConfigError("catalog", f"{' '.join(argv[1:])} exited with {proc.returncode}")
    return proc.stdout


def bundled_catalog(binary: Path) -> tuple[str, dict]:
    """``(cli_version, parsed bundled catalogue)``, run once per process and binary path."""
    key = str(binary)
    with _memo_lock:
        if key in _memo:
            return _memo[key]
    version = _run([key, "--version"]).strip()
    try:
        data = orjson.loads(_run([key, "debug", "models", "--bundled"]))
    except orjson.JSONDecodeError as exc:
        raise HermeticConfigError("catalog", "codex debug models --bundled printed invalid JSON") from exc
    with _memo_lock:
        _memo[key] = (version, data)
    return version, data


def catalog_cache_name(cli_version: str, slug: str, variant: str, entry_hash: str) -> str:
    safe_version = "".join(c if c.isalnum() or c in ".-" else "_" for c in cli_version.removeprefix("codex-cli").strip())
    return f"hermetic-codex-catalog-{safe_version}-{slug}-{variant}-{entry_hash[:16]}-v{TRANSFORM_VERSION}.json"


def ensure_catalog(binary: Path, model: str, variant: str = "production", cache_dir: Path | None = None) -> Path:
    """Return the path of the validated catalogue file for ``model``, generating it when absent.

    Synchronous (a few milliseconds after the first call of a process); call it
    through ``asyncio.to_thread`` from async code. Safe across threads, event
    loops and processes: the file is written through a unique temporary file and
    ``os.replace``, and its content is a pure function of its name.
    """
    version, data = bundled_catalog(binary)
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise HermeticConfigError("catalog", f"The bundled catalogue of {version} has no model list")
    entries = [e for e in models if isinstance(e, dict) and e.get("slug") == model]
    if not entries:
        raise HermeticConfigError("catalog", f"The bundled catalogue of {version} has no model {model!r}")
    source = entries[0]
    entry_hash = hashlib.sha256(orjson.dumps(source, option=orjson.OPT_SORT_KEYS)).hexdigest()
    if cache_dir is None:
        from twicc.paths import get_data_dir

        cache_dir = get_data_dir() / "cache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HermeticConfigError("catalog", f"Cannot create the catalogue cache directory {cache_dir}: {exc}") from exc
    path = cache_dir / catalog_cache_name(version, model, variant, entry_hash)
    try:
        with catalog_cache(path, prefix="hermetic-codex-catalog"):
            _ensure_catalog_file(path, source, model, variant)
    except OSError as exc:
        raise HermeticConfigError("catalog", f"Cannot prepare the catalogue cache {path}: {exc}") from exc
    return path


def _ensure_catalog_file(path: Path, source: dict, model: str, variant: str) -> None:
    """Validate or atomically write the file while holding its family directory lock."""
    if path.exists() and _file_problem(path, model, variant) is None:
        return   # a damaged cache file falls through and is regenerated, without a failure log line
    content = {"models": [transform_entry(source, variant=variant)]}
    validate_catalog(content, model=model, variant=variant)
    try:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    except OSError as exc:
        raise HermeticConfigError("catalog", f"Cannot write the catalogue file in {path.parent}: {exc}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(orjson.dumps(content))
        os.replace(tmp_name, path)
    except BaseException as exc:
        Path(tmp_name).unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise HermeticConfigError("catalog", f"Cannot write the catalogue file {path}: {exc}") from exc
        raise
