"""
Centralized path resolution for TwiCC data directories.

All data (database, logs, config) lives in a single "data directory":
- Default: ~/.twicc/
- Override: TWICC_DATA_DIR environment variable

Structure:
    <data_dir>/
    ├── .env              # Infrastructure config (ports, password hash, etc.)
    ├── settings.json     # User preferences synced across devices
    ├── db/
    │   └── data.sqlite (+shm, +wal)
    ├── logs/
    │   ├── backend.log              # Backend application logs
    │   ├── frontend.log             # Frontend (Vite) process output
    │   └── sdk/
    │       ├── claude_code/
    │       │   └── {session_id}.jsonl   # Raw Claude Code SDK wire messages
    │       └── codex/
    │           └── {session_id}.jsonl   # Raw Codex SDK stream events + approvals
    └── search-index/
        └── (tantivy index files)

In development with worktrees, devctl.py sets TWICC_DATA_DIR to the
worktree root so each worktree gets its own DB, logs, and .env.

The ``.env`` of the data dir is the instance configuration: it is loaded into
``os.environ`` once per process by :func:`ensure_env_loaded` (a key defined
there wins over the inherited environment). The provider home keys
(:data:`PROVIDER_HOME_KEYS`) are read ONLY from that file; ``TWICC_DATA_DIR``
is read ONLY from the environment because it locates the file. Design:
docs/plans/2026-09-02-provider-home-dirs-design.md.
"""

import hashlib
import os
import re
from pathlib import Path

# Environment variable name to override the data directory
TWICC_DATA_DIR_ENV = "TWICC_DATA_DIR"

# Default data directory (same pattern as the providers' ``~/.claude/`` / ``~/.codex/``)
DEFAULT_DATA_DIR = Path.home() / ".twicc"

# The provider home variables TwiCC honours — the providers' OFFICIAL names, no
# ``TWICC_`` alias, so the very same variable reaches the CLI, the SDK, a
# terminal user and any script unchanged. ``.env``-exclusive: an inherited value
# for a key the file does not define is dropped by :func:`ensure_env_loaded`.
# Resolution lives in :mod:`twicc.provider_homes`.
PROVIDER_HOME_KEYS: tuple[str, ...] = (
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_SECURESTORAGE_CONFIG_DIR",
    "CODEX_HOME",
)

_ENV_LOADED = False
_ENV_WARNINGS: list[str] = []


def get_data_dir() -> Path:
    """Return the resolved data directory.

    Priority:
    1. TWICC_DATA_DIR environment variable (if set and non-empty)
    2. ~/.twicc/ (default)
    """
    env_value = os.environ.get(TWICC_DATA_DIR_ENV, "").strip()
    if env_value:
        return Path(env_value).resolve()
    return DEFAULT_DATA_DIR


def ensure_env_loaded() -> None:
    """Load ``<data_dir>/.env`` into ``os.environ`` once per process.

    Keys defined in the file win (the equivalent of ``load_dotenv(override=True)``),
    except ``TWICC_DATA_DIR``, which is environment-only (it locates the file:
    honouring it from the file would move every later :func:`get_data_dir` away
    from the directory the configuration came from) and is skipped with a warning.

    The provider home keys (:data:`PROVIDER_HOME_KEYS`) are ``.env``-exclusive:
    an inherited value for a key the file does not define — or defines without
    a value, a bare ``KEY`` line — is dropped, with a warning. Every process TwiCC
    launches inherits the backend's whole environment, so without this rule a
    worktree started from an agent session of another instance would silently
    write into that instance's provider homes. ``KEY=`` (empty) counts as defined.

    Idempotent: only the first call loads; later calls are no-ops, so values a
    test harness sets afterwards are neither overridden nor dropped. Warnings
    are kept for :func:`get_env_load_warnings`.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    from dotenv import dotenv_values

    env_path = get_env_path()
    values = dict(dotenv_values(env_path)) if env_path.is_file() else {}
    if TWICC_DATA_DIR_ENV in values:
        _ENV_WARNINGS.append(f"Ignoring {TWICC_DATA_DIR_ENV} in {env_path}: environment-only")
        del values[TWICC_DATA_DIR_ENV]
    for key, value in values.items():
        if value is not None:  # bare ``KEY`` lines carry None
            os.environ[key] = value
    for key in PROVIDER_HOME_KEYS:
        if values.get(key) is None and key in os.environ:
            _ENV_WARNINGS.append(
                f"Ignoring inherited {key}={os.environ[key]!r}: not set in {env_path}"
            )
            del os.environ[key]
    _ENV_LOADED = True


def get_env_load_warnings() -> list[str]:
    """Warnings produced by the first :func:`ensure_env_loaded`; callers print or log them."""
    return list(_ENV_WARNINGS)


def _reset_env_loader() -> None:
    """Tests only: forget the load so :func:`ensure_env_loaded` runs again."""
    global _ENV_LOADED
    _ENV_LOADED = False
    _ENV_WARNINGS.clear()


def tmux_socket_suffix() -> str:
    """Per-data-dir suffix for the tmux socket names (``""`` on the default data dir).

    tmux sockets are per user, so without this every instance (main + each
    worktree) would share the same ``twicc`` / ``twicc-hybrid`` servers and the
    same session names — a worktree's global terminal would attach to main's
    shell, and boot adoption would kill the other instance's hybrid CLIs. A
    non-default data dir gets ``-<sha256(str(data_dir))[:8]>``; ``~/.twicc`` keeps
    the bare names so the production instance keeps its existing sessions.

    Both sides are resolved: :func:`get_data_dir` returns :data:`DEFAULT_DATA_DIR`
    unresolved when ``TWICC_DATA_DIR`` is unset, and a symlinked home would
    otherwise make the production instance lose its socket name. ``devctl.py``
    mirrors this function standalone (same hash input, same comparison).
    """
    data_dir = get_data_dir().resolve()
    if data_dir == DEFAULT_DATA_DIR.resolve():
        return ""
    return "-" + hashlib.sha256(str(data_dir).encode()).hexdigest()[:8]


def get_db_dir() -> Path:
    """Return the database directory (<data_dir>/db/)."""
    return get_data_dir() / "db"


def get_db_path() -> Path:
    """Return the database file path (<data_dir>/db/data.sqlite)."""
    return get_db_dir() / "data.sqlite"


def get_logs_dir() -> Path:
    """Return the logs directory (<data_dir>/logs/)."""
    return get_data_dir() / "logs"


def get_sdk_logs_dir(provider: str | None = None) -> Path:
    """Return the SDK logs directory.

    Without ``provider``, returns the parent ``<data_dir>/logs/sdk/``
    (used by ``ensure_data_dirs`` and any cross-provider tooling).
    With a provider value (e.g. ``Provider.CLAUDE_CODE.value`` /
    ``Provider.CODEX.value``), returns the per-provider subdirectory
    where each provider's logger writes ``{session_id}.jsonl``.

    A string is taken (rather than the ``Provider`` enum) to keep
    ``paths.py`` free of any ``twicc.core`` import — this module is
    intentionally low-level and gets imported from many places.
    """
    base = get_logs_dir() / "sdk"
    if provider is None:
        return base
    return base / provider


def get_backend_log_path() -> Path:
    """Return the backend log file path (<data_dir>/logs/backend.log)."""
    return get_logs_dir() / "backend.log"


def get_frontend_log_path() -> Path:
    """Return the frontend log file path (<data_dir>/logs/frontend.log)."""
    return get_logs_dir() / "frontend.log"


def get_env_path() -> Path:
    """Return the .env file path (<data_dir>/.env)."""
    return get_data_dir() / ".env"


def get_api_tokens_path() -> Path:
    """Path to the RPC API token store (``<data_dir>/api-tokens.json``)."""
    return get_data_dir() / "api-tokens.json"


def get_secret_key_path() -> Path:
    """Per-install Django SECRET_KEY file (chmod 600).

    Generated once by :func:`twicc.secret_key.load_or_create_secret_key`;
    also the root secret behind derived signatures (MCP session tokens).
    """
    return get_data_dir() / "secret-key"


def get_synced_settings_path() -> Path:
    """Return the synced settings file path (<data_dir>/settings.json)."""
    return get_data_dir() / "settings.json"


def get_search_dir() -> Path:
    """Return the search index directory (<data_dir>/search-index/)."""
    return get_data_dir() / "search-index"


def get_terminal_config_path() -> Path:
    return get_data_dir() / "terminal-config.json"


def get_message_snippets_config_path() -> Path:
    return get_data_dir() / "message-snippets.json"


def get_workspaces_path() -> Path:
    """Path to the workspaces definition file."""
    return get_data_dir() / "workspaces.json"


def get_layouts_path() -> Path:
    """Path to the named dockable-layouts catalog file."""
    return get_data_dir() / "layouts.json"


def get_drop_requests_dir() -> Path:
    """Directory holding ``<request_uuid>.json`` drop-files written by the CLI
    and picked up by :class:`twicc.drop_requests_watcher.DropRequestsWatcher`.
    """
    return get_data_dir() / "drop-requests"


def get_artifacts_dir() -> Path:
    """Return the artifacts root directory (``<data_dir>/artifacts/``).

    Session-scoped artifacts (typically screenshots saved by agents) live
    under ``<data_dir>/artifacts/<session_id>/<artifact_file_name>``.
    """
    return get_data_dir() / "artifacts"


def get_session_artifacts_dir(session_id: str) -> Path:
    """Return the artifact directory for a given session (path only).

    The directory itself is pre-created (``mkdir -p``) at agent start/resume by
    ``twicc.agent.work_dirs.resolve_and_create_work_dirs`` so the
    agent can write into it prompt-free; this helper only computes the path.
    """
    return get_artifacts_dir() / session_id


def get_shares_dir() -> Path:
    """Root of per-share artifact snapshots (``<data_dir>/shares/``)."""
    return get_data_dir() / "shares"


def get_share_snapshot_dir(share_id: str) -> Path:
    """Snapshot copy dir for one artifact share (``<data_dir>/shares/<share_id>/``).
    Path only — the service creates/removes it."""
    return get_shares_dir() / share_id


def get_project_icons_dir() -> Path:
    """Return the project-icons root directory (``<data_dir>/project-icons/``).

    Holds copied+normalized project icon images, keyed by an opaque hash
    bucket (``repo-<hash>`` for a git repository's shared icon, ``proj-<hash>``
    for a per-project override). Each bucket carries the image file(s) plus,
    for repo buckets, a ``manifest.json`` recording the icon lifecycle
    (auto/manual/cleared). See docs/plans/2026-07-17-project-icons-design.md.
    Populated on demand by ``twicc.project_icons``; not pre-created here.
    """
    return get_data_dir() / "project-icons"


def get_scratch_dir() -> Path:
    """Return the scratch root directory (``<data_dir>/scratch/``).

    Scratch holds throwaway working files. A session's own scratch lives
    under ``<data_dir>/scratch/<session_id>/``; an orchestration tree may
    instead share one folder, passed to its members via the ``scratch_dir``
    annotation.
    """
    return get_data_dir() / "scratch"


def get_session_scratch_dir(session_id: str) -> Path:
    """Return the per-session scratch directory (path only).

    The directory itself is pre-created (``mkdir -p``) at agent start/resume by
    ``twicc.agent.work_dirs.resolve_and_create_work_dirs`` so the
    agent can write into it prompt-free; this helper only computes the path.
    """
    return get_scratch_dir() / session_id


def get_session_hybrid_dir(session_id: str) -> Path:
    """Per-session runtime files for hybrid CLI mode (addendum file, attachments).

    The whole directory is passed to the CLI via ``--add-dir`` so attachment
    reads never trigger permission prompts.
    """
    path = get_data_dir() / "hybrid" / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_hybrid_hooks_dir() -> Path:
    """Watched drop directory for hybrid CLI hook event files.

    Hook commands injected at hybrid launch drop one
    ``<session_id>__<event>__<nonce>.json`` file per event here; the hybrid
    hooks watcher consumes and deletes them. File-based on purpose: hook
    commands must reach TwiCC without HTTP, so the channel works with the
    password enabled and exposes no URL/secret.
    """
    path = get_data_dir() / "hybrid-hooks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_seen_tips_path() -> Path:
    """Path to the synced seen-tips state file."""
    return get_data_dir() / "seen-tips.json"


def get_providers_status_path() -> Path:
    """Path to the persisted per-provider upstream status file (incidents + acknowledgments)."""
    return get_data_dir() / "providers-status.json"


def get_tips_assets_dir() -> Path:
    """Directory holding tip .md files and their image assets.

    In dev (``settings.DEV_MODE``), this points to ``frontend/public/tips/``
    in the repo so Vite is the source of truth for live editing.

    In an installed wheel, the tips folder is bundled inside
    ``FRONTEND_DIST_DIR / "tips"`` by ``hatch_build.py`` (which copies the
    whole ``frontend/public/`` tree).
    """
    from django.conf import settings
    if settings.DEV_MODE:
        return settings.PACKAGE_DIR.parent.parent / "frontend" / "public" / "tips"
    return settings.FRONTEND_DIST_DIR / "tips"


def get_seen_help_path() -> Path:
    """Path to the synced seen-help state file."""
    return get_data_dir() / "seen-help.json"


def get_help_assets_dir() -> Path:
    """Directory holding help .md files and their image assets.

    Mirrors :func:`get_tips_assets_dir`: in dev this points to
    ``frontend/public/help/`` in the repo; in an installed wheel the help
    folder is bundled inside ``FRONTEND_DIST_DIR / "help"`` by
    ``hatch_build.py`` (which copies the whole ``frontend/public/`` tree).
    """
    from django.conf import settings
    if settings.DEV_MODE:
        return settings.PACKAGE_DIR.parent.parent / "frontend" / "public" / "help"
    return settings.FRONTEND_DIST_DIR / "help"


def path_to_project_id(path: str) -> str:
    """Convert a filesystem path to a TwiCC project ID.

    A project in TwiCC is a working directory; its ID is derived from the
    directory path by replacing every non-alphanumeric character with a
    dash. The convention is inherited from Claude Code (which names its
    own subfolders of ``<claude home>/projects/`` the same way), but TwiCC
    reuses it as the cross-provider project key — multiple providers can
    run inside the same project, sharing the same ID.

    Pure function: the caller is responsible for resolving and
    normalizing the path beforehand (e.g. via ``os.path.realpath``) if a
    canonical ID is required.
    """
    return re.sub(r'[^a-zA-Z0-9]', '-', path)


def _migrate_legacy_data_files() -> None:
    """Rename legacy data files left over from older TwiCC versions.

    The agent settings presets file used to live at
    ``<data_dir>/claude-settings-presets.json`` (no provider prefix). The
    cross-provider naming scheme is now ``<data_dir>/<provider>-settings-presets.json``,
    owned by ``BaseProviderHelpers.get_settings_presets_path``. This
    migration is Claude Code-specific by design: it predates the
    multi-provider split and only ever targeted the Claude Code file.
    """
    legacy_presets = get_data_dir() / "claude-settings-presets.json"
    new_presets = get_data_dir() / "claude_code-settings-presets.json"
    if legacy_presets.exists() and not new_presets.exists():
        legacy_presets.rename(new_presets)


def ensure_data_dirs() -> None:
    """Create the data directory structure if it doesn't exist."""
    get_db_dir().mkdir(parents=True, exist_ok=True)
    get_sdk_logs_dir().mkdir(parents=True, exist_ok=True)
    get_search_dir().mkdir(parents=True, exist_ok=True)
    _migrate_legacy_data_files()
