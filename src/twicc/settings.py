import os
import shlex
import shutil
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from twicc import provider_homes
from twicc.paths import ensure_data_dirs, ensure_env_loaded, get_backend_log_path, get_db_path
from twicc.secret_key import load_or_create_secret_key
from twicc.version import get_version

PACKAGE_DIR = Path(__file__).resolve().parent  # src/twicc/

APP_VERSION = get_version()
DEV_MODE = PACKAGE_DIR.parent.name == "src"


def _is_uv_managed(segment: str) -> bool:
    """Whether ``sys.executable`` lives inside a uv-managed directory of
    the given kind: ``archive-v0`` for ephemeral ``uvx`` runs (cache),
    ``tools`` for persistent ``uv tool install`` venvs.

    We don't rely on environment variables: ``UV_RUN_RECURSION_DEPTH`` is
    only set by ``uv run``, never by ``uvx`` / ``uv tool run``. The
    interpreter path is the only stable signal across both modes.

    We intentionally do NOT ``resolve()`` the path: uv venvs ship a
    ``bin/python`` that's a symlink to the system Python (or to its own
    download), so resolving would strip the very ``uv/<segment>/`` parts
    we're trying to detect.
    """
    parts = Path(sys.executable).parts
    for i, p in enumerate(parts):
        if p == "uv" and i + 1 < len(parts) and parts[i + 1] == segment:
            return True
    return False


UVX_MODE = not DEV_MODE and _is_uv_managed("archive-v0")


def _resolve_twicc_launch_prefix() -> str:
    """
    Build the shell prefix that re-invokes the same TwiCC distribution as
    a command, ready to be suffixed with subcommand args (e.g.
    ``claude auth login``).

    Detection, in order:

    1. ``uvx twicc`` when launched via ephemeral ``uvx`` (cache lives in
       ``~/.cache/uv/archive-v0/<hash>/``).
    2. Bare ``twicc`` when launched from a ``uv tool install`` venv
       (``~/.local/share/uv/tools/<name>/``) AND its shim is on PATH.
    3. ``uv run --directory <dir> <script>`` when launched via ``uv run``
       in dev mode (e.g. ``uv run ./run.py`` via ``devctl``). The
       ``--directory`` makes the command work regardless of the
       terminal's current working directory.
    4. Quoted absolute path of the ``twicc`` script when ``sys.argv[0]``
       points at one (covers the ``uv tool install`` shim-not-on-PATH
       case, and any other installed-package layout).
    5. ``<sys.executable> -m twicc`` as a generic fallback (covers
       ``python -m twicc`` and any other case where the launching
       interpreter has the ``twicc`` package importable).
    """
    if UVX_MODE:
        return "uvx twicc"

    # ``uv tool install twicc`` puts the venv in ``~/.local/share/uv/tools/twicc/``
    # and creates a shim in the uv tool bin dir. Only return the bare name when
    # that shim is actually on PATH (so the command works from anywhere);
    # otherwise fall through to the absolute argv0 path below.
    if not DEV_MODE and _is_uv_managed("tools") and shutil.which("twicc"):
        return "twicc"

    # Build the shortest viable form of argv0 — both ``absolute()`` (no
    # symlink following, keeps the path the user actually typed) and
    # ``resolve()`` (follows symlinks, but normalises ``..``/``.``) are
    # candidates; we keep whichever is shorter. In practice ``absolute()``
    # almost always wins (e.g. ``~/.local/bin/twicc`` vs the longer
    # ``~/.local/share/uv/tools/twicc/bin/twicc`` target), but ``resolve()``
    # can win when argv0 carries redundant segments.
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidates = [Path(argv0).absolute()]
        try:
            candidates.append(Path(argv0).resolve())
        except OSError:
            pass
        argv0_path = min(candidates, key=lambda p: len(str(p)))
    else:
        argv0_path = None

    # uv run <script> in dev mode: re-invoke the same script through uv,
    # forcing the project dir via ``--directory`` so the prefix stays a
    # single shell command (no ``&&``). The script is named bare (no ``./``)
    # so uv resolves it against the ``--directory`` target, not the caller's
    # cwd at invocation time.
    is_uv_run = "UV_RUN_RECURSION_DEPTH" in os.environ
    if is_uv_run and DEV_MODE and argv0_path and argv0_path.is_file():
        return (
            f"uv run --directory {shlex.quote(str(argv0_path.parent))} "
            f"{argv0_path.name}"
        )

    # Installed ``twicc`` script.
    if argv0_path and argv0_path.is_file() and argv0_path.name in ("twicc", "twicc.exe"):
        return shlex.quote(str(argv0_path))

    # Sibling entry-point script in the same venv ``bin/`` as ``sys.executable``.
    # Covers any installed-package layout (including ``uv tool install`` with
    # the shim off PATH) — always preferable to the ``python -m twicc``
    # fallback because it doesn't depend on PATH and produces a single-binary
    # command.
    sibling = Path(sys.executable).parent / "twicc"
    if sibling.is_file():
        return shlex.quote(str(sibling))

    # Fallback: same interpreter, twicc as a module.
    return f"{shlex.quote(sys.executable)} -m twicc"


# Shell prefix exposed via the bootstrap API so the frontend can render
# accurate "run <X> ..." instructions and inject them into the terminal.
TWICC_LAUNCH_PREFIX = _resolve_twicc_launch_prefix()

# Surface the same prefix as TWICC_BIN so subprocesses (Claude/Codex
# agents, and the shell commands they spawn from skills) can re-invoke
# THIS TwiCC instance reliably — independent of whatever ``twicc`` may
# happen to be first in PATH on the user's system. The plugin's
# SKILL.md files all read TWICC_BIN with a ``command -v twicc`` fallback.
os.environ.setdefault("TWICC_BIN", TWICC_LAUNCH_PREFIX)

# Load .env from the data directory (~/.twicc/.env or $TWICC_DATA_DIR/.env).
# Idempotent: no-op when already loaded by the CLI package import / run.py.
# Only Django-only entry points (``python -m django``, one-liners, pytest, the
# compute worker) reach here without it.
ensure_env_loaded()

# Provider home directories (``CLAUDE_CONFIG_DIR`` / ``CLAUDE_SECURESTORAGE_CONFIG_DIR``
# / ``CODEX_HOME`` from the .env). Informational copies: code reads the paths
# from ``twicc.provider_homes`` at call time, so Django-free processes and tests
# share one source of truth. Unusable values fail fast here for Django-only entry
# points; the ``twicc`` CLI validates earlier in ``cli.main()``. This module must
# import no ``twicc.providers.*`` module (a test pins it).
try:
    CLAUDE_CONFIG_DIR = provider_homes.claude_config_dir().path
    CLAUDE_SECURE_STORAGE_DIR = provider_homes.claude_secure_storage_dir().path
    CODEX_HOME = provider_homes.codex_home().path
    PROVIDER_HOMES_DESCRIPTION = provider_homes.describe_provider_homes()
except provider_homes.ProviderHomeConfigError as exc:
    raise ImproperlyConfigured(str(exc)) from exc

# Ensure data directories exist (db/, logs/)
ensure_data_dirs()

# Per-install random key, generated on first startup and persisted to
# <data_dir>/secret-key (override: TWICC_SECRET_KEY). Rotating it forces a
# re-login and re-mints the MCP session tokens derived from it.
SECRET_KEY = load_or_create_secret_key()

# TWICC_DEBUG is set by devctl when launching the backend process.
# It controls Django's DEBUG mode and the twicc logger level.
DEBUG = os.environ.get("TWICC_DEBUG", "").strip().lower() in ("1", "true", "yes")

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "channels",
    "twicc.core.apps.CoreConfig",
]

# In debug mode, try to load django-extensions (dev dependency, not required at runtime)
if DEBUG:
    try:
        import django_extensions  # noqa: F401

        INSTALLED_APPS.insert(-1, "django_extensions")
    except ImportError:
        pass

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "twicc.auth.middleware.PasswordAuthMiddleware",
    "twicc.auth.middleware.RpcTokenAuthMiddleware",
]

# Artifact data-store PUTs (design 2026-08-05 §4) carry up to 10 MB bodies;
# Django's 2.5 MB default would reject them at request.body. Kept modestly
# above the per-file cap so the data_store's own 413 stays the visible limit.
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

# Password protection
# Set TWICC_PASSWORD_HASH in .env to enable password protection.
# Use `twicc password set` (or `uvx twicc password set`, etc.) to set it
# interactively. If not set or empty, the app is accessible without authentication.
TWICC_PASSWORD_HASH = os.environ.get("TWICC_PASSWORD_HASH", "")

# Local-only safety net: when no password is configured there is nothing to
# authenticate against, so by default TwiCC refuses any non-loopback request
# (a forgotten password must not leave the instance silently reachable over the
# network — see twicc.auth.local_access). An operator who deliberately wants
# unauthenticated remote access can set this to bypass the refusal. Intentionally
# not surfaced in the UI or the access-blocked screen.
TWICC_ALLOW_INSECURE_REMOTE = os.environ.get("TWICC_ALLOW_INSECURE_REMOTE", "").strip().lower() in ("1", "true", "yes")

# Session settings
# Prefixed default (not Django's bare "sessionid"): TwiCC and a user's own dev
# app often both run on localhost, and cookies ignore the port — a shared
# "sessionid" slot on host localhost would clobber between the two (typically
# another Django app, previewed in the Browser tab). The prefix keeps them in
# separate cookie slots. Still overridable via TWICC_SESSION_COOKIE.
SESSION_COOKIE_NAME = os.environ.get("TWICC_SESSION_COOKIE", "twicc_sessionid")
SESSION_ENGINE = "django.contrib.sessions.backends.db"
# Effectively no server-side expiry: TwiCC is self-hosted, the real protection
# is the password (PBKDF2) plus the fingerprint that invalidates every session
# the moment TWICC_PASSWORD_HASH changes (see auth.session_auth). Sliding expiry
# adds no meaningful security here. Browsers cap Set-Cookie Max-Age at ~400 days
# regardless, so users may need to re-login about once a year — that's the
# floor we can't push past from the server side.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365 * 100  # 100 years (browsers cap ~400d)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# SessionMiddleware would otherwise emit an UPDATE django_session on every
# authenticated request to refresh expire_date — concurrent with our DB writer
# / watcher writes since it runs outside run_under_db_write_lock. With
# SESSION_COOKIE_AGE at 100 years there is nothing useful to refresh anyway.
SESSION_SAVE_EVERY_REQUEST = False

# Dev mode: make TwiCC's own session cookie usable when TwiCC is itself embedded
# cross-site in another TwiCC's Browser tab (e.g. two worktree instances, each
# exposed over its own HTTPS tunnel). A Lax cookie is withheld in a cross-site
# iframe; SameSite=None lets it flow, and browsers require Secure alongside None.
# Secure is fine over the usual dev origins — HTTPS tunnels and http://localhost
# (a secure context) — but NOT over a plain-http LAN IP, where the cookie won't
# be set at all (use localhost or a tunnel there). Dev-only: we never widen the
# cross-site cookie surface for real deployments.
if DEBUG:
    SESSION_COOKIE_SAMESITE = "None"
    SESSION_COOKIE_SECURE = True

ROOT_URLCONF = "twicc.urls"

# Django template engine — used for the few server-rendered pages (currently the
# standalone artifact password page in twicc.artifacts/templates). The SPA itself
# is a static index.html (not a template), so we only register the dirs that
# actually hold templates rather than enabling APP_DIRS.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [PACKAGE_DIR / "artifacts" / "templates", PACKAGE_DIR / "share" / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {"context_processors": []},
    },
]

ASGI_APPLICATION = "twicc.asgi.application"

# ``CONFIG`` is handed to the backend as keyword arguments
# (``channels.layers.ChannelLayerManager._make_backend``), so these are the
# ``InMemoryChannelLayer.__init__`` parameters. The library defaults
# (capacity 100, expiry 60) target the generic case — many clients, small
# messages. TwiCC is the opposite: a handful of browsers receiving large
# payloads (``session_items_added`` carries full item content) plus one
# ``stream_block_delta`` per streamed token, from every live agent at once.
#
# Both values guard the same per-consumer queue, and both discard silently:
#
# - ``capacity`` — queue depth. ``group_send`` swallows ``ChannelFull``, so a
#   client that cannot drain fast enough loses individual frames with no
#   error anywhere. At the default 100 that is barely a second of buffer
#   under parallel agent streaming; a lost ``session_removed`` leaves a
#   hidden session visible until the page is reloaded.
# - ``expiry`` — per-message TTL. A message older than this is dropped AND
#   ``_clean_expired`` calls ``_remove_from_groups``, which unsubscribes the
#   channel from *every* group. The client then receives nothing at all,
#   socket still open, until it reconnects. Raising ``capacity`` alone makes
#   that worse: a deeper queue holds messages longer, so more of them reach
#   the TTL. The two must move together.
#
# Memory stays bounded: the queue is per client and holds a deepcopy of each
# message, so the cost is roughly clients x capacity x message size — a few MB
# at the observed ~4 KB average.
#
# - ``group_expiry`` — how long a channel stays in a group after joining it.
#   The join timestamp is written once by ``group_add`` and never refreshed, so
#   this is not an idle timeout: at the library default a browser tab whose
#   socket has been open for 24 hours is dropped from the group and stops
#   receiving anything, however healthy it is. The mechanism collects consumers
#   that vanish without notice, which cannot happen in-process — our consumers
#   call ``group_discard`` on disconnect — so it is pushed out of reach rather
#   than relied upon.
#
# Memory stays bounded: the queue is per client and holds a deepcopy of each
# message, so the cost is roughly clients x capacity x message size — a few MB
# at the observed ~4 KB average.
#
# Do NOT add ``channel_capacity`` here: ``InMemoryChannelLayer`` stores it raw
# and ``get_capacity`` iterates it as ``(pattern, capacity)`` pairs, while
# ``compile_capacities`` is never called — a plain dict breaks every send.
#
# The backend is TwiCC's own subclass: it turns a discarded frame into a
# ``resync_required`` signal instead of a silent gap. See
# :mod:`twicc.channel_layer`.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "twicc.channel_layer.TwiccChannelLayer",
        "CONFIG": {
            "capacity": 1000,
            "expiry": 300,
            "group_expiry": 365 * 24 * 3600,
        },
    }
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": get_db_path(),
        "OPTIONS": {
            "timeout": 30,
            "init_command": """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA busy_timeout=30000;
                PRAGMA mmap_size=134217728;
                PRAGMA journal_size_limit=27103364;
                PRAGMA cache_size=2000;
            """,
        },
    }
}

# Frontend build directory
# Built frontend assets live inside the package: src/twicc/static/frontend/
# This path works both in dev (after npm run build) and when installed via pip/uvx.
# Static file serving is handled by BlackNoise at the ASGI level (see asgi.py).
FRONTEND_DIST_DIR = PACKAGE_DIR / "static" / "frontend"

# Logging configuration
# All logs go to file (<data_dir>/logs/backend.log)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "provider": {
            "()": "twicc.logging_context.ProviderFilter",
        },
    },
    "formatters": {
        "standard": {
            "format": "[{asctime} - {levelname:>6} - {provider:>11} - {name}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "file": {
            "class": "logging.FileHandler",
            "filename": str(get_backend_log_path()),
            "formatter": "standard",
            "encoding": "utf-8",
            "filters": ["provider"],
            # Open the file at the first record, not at ``django.setup()``: the
            # server trims backend.log at startup (``twicc.log_retention``) and
            # must do so before any descriptor is open on it — and a CLI
            # command that never logs should not touch the file at all.
            "delay": True,
        },
    },
    "loggers": {
        "twicc": {
            "handlers": ["file"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "uvicorn": {
            "handlers": ["file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Display levels computation — one constant per provider, accessed by the
# provider helpers via ``BaseProviderHelpers.current_compute_version``.
# Bump the relevant constant when the corresponding provider's parsing/compute
# rules change to trigger recomputation. ``None`` declares "no compute pipeline
# yet" — sessions of that provider are reported up-to-date as-is.
CLAUDE_CODE_COMPUTE_VERSION = 108  # 108: dedup task tool_use replay against compaction-duplicated JSONL lines + repair corrupted twiccTasksData snapshots (107 = repair agent links wrongly flipped to background by SendMessage-continuation notifications, 106 = plan_paths from subagent writes too, 105 = Session.plan_paths backfill)
CODEX_COMPUTE_VERSION = 48  # 48: canonical paginated history with automatic legacy rollout migration (47: multi-agent v2 NEW_TASK opening message, 46: multi-agent v2 subagent linkage)

# Search index version
# Bumped when the schema or document layout changes — forces a full
# rebuild of the on-disk index at next startup.
# v2 -> v3: added `hidden` and `spawned_by` fields (hidden-sessions feature).
# v3 -> v4: added `spawn_root` field for full-tree filiation queries.
CURRENT_SEARCH_VERSION = 4

# Process auto-stop timeouts (in seconds)
# Processes are automatically stopped if they remain in a state for too long
PROCESS_TIMEOUT_STARTING = 60  # 1 minute - process stuck during startup
PROCESS_TIMEOUT_USER_TURN = 3900  # 65 min - idle, waiting for user input. Kept >= the prompt-cache TTL (1h) so a return within the cache window reuses the live process instead of forcing a resume (which regenerates Claude Code's env/gitStatus/date/memory prefix and busts the conversation cache).
PROCESS_TIMEOUT_ASSISTANT_TURN = 3 * 60 * 60  # 3 hours - no activity from agent
PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE = 10 * 60 * 60  # 10 hours - max total duration for a turn

# Cron auto-restart
# Set TWICC_NO_CRON_RESTART=1 to disable automatic restart of cron jobs,
# both at startup (for sessions with persisted crons) and when recurring crons expire.
CRON_AUTO_RESTART = os.environ.get("TWICC_NO_CRON_RESTART", "").strip().lower() not in ("1", "true", "yes")

# Codex plugin install
# Set TWICC_NO_CODEX_PLUGIN=1 to skip ``ensure_twicc_plugin_installed`` at Codex
# orchestrator start. ``<codex home>/config.toml`` is global to a home and already
# managed by the main install — devctl sets this flag for a worktree that shares
# the default ``~/.codex`` so it doesn't race on it (a worktree with its own
# ``CODEX_HOME`` installs its own copy of the plugin there).
CODEX_PLUGIN_INSTALL_ENABLED = os.environ.get("TWICC_NO_CODEX_PLUGIN", "").strip().lower() not in ("1", "true", "yes")

# Daily cleanup of empty per-session artifacts/scratch directories
# Set TWICC_NO_SESSION_DIRS_CLEANUP=1 to disable the janitor that prunes empty
# ``<data_dir>/artifacts/<id>/`` and ``<data_dir>/scratch/<id>/`` dirs 30 days
# after a session's last activity (cf. twicc.session_dirs_cleanup_task).
# Worktrees set this: artifacts/ and scratch/ are symlinks shared with the main
# instance, which owns the cleanup.
SESSION_DIRS_CLEANUP_ENABLED = os.environ.get("TWICC_NO_SESSION_DIRS_CLEANUP", "").strip().lower() not in ("1", "true", "yes")

# Daily reaper of never-used, orphaned twicc terminal tmux sessions
# Set TWICC_NO_TMUX_CLEANUP=1 to disable the reaper that kills ``twicc-*`` tmux
# sessions on this instance's terminal socket which were opened but never
# received any input (cf. twicc.tmux_cleanup_task). The socket is per data dir
# (``paths.tmux_socket_suffix``), so a worktree only ever reaps its own sessions;
# a manual opt-out, devctl no longer sets it.
TMUX_CLEANUP_ENABLED = os.environ.get("TWICC_NO_TMUX_CLEANUP", "").strip().lower() not in ("1", "true", "yes")

# Anonymous telemetry (design docs/plans/2026-07-18-telemetry-design.md).
# Env kill switch; the synced setting telemetryEnabled is checked at runtime.
TELEMETRY_ENABLED = os.environ.get("TWICC_NO_TELEMETRY", "").strip().lower() not in ("1", "true", "yes")

# Where telemetry payloads are POSTed. The hardcoded production URL is the
# authoritative value shipped with the project; the env override exists for
# dev/E2E only (e.g. a local `wrangler dev` collector).
TELEMETRY_ENDPOINT = (
    os.environ.get("TWICC_TELEMETRY_URL", "").strip() or "https://twicc-telemetry.twidi.com/v1/telemetry"
)

# Auto-enable every registered provider at first boot
# Set TWICC_AUTO_ENABLE_PROVIDERS=1 to bypass the initial provider activation
# dialog: at backend startup, if ``disabledProviders`` is absent from
# settings.json, ``apply_auto_enable_providers_bootstrap()`` writes an empty
# list (equivalent to validating the dialog with every provider checked).
# Idempotent once the key exists, so user toggles from Settings are preserved.
# Used by devctl in worktree mode so dev servers come up without prompting.
AUTO_ENABLE_PROVIDERS = os.environ.get("TWICC_AUTO_ENABLE_PROVIDERS", "").strip().lower() in ("1", "true", "yes")

# Hybrid Claude CLI mode (feature flag, default OFF)
# Set TWICC_CLAUDE_HYBRID_ENABLED=1 to un-gate the whole hybrid mode feature:
# the startup announcement, the per-session toggle, the Claude settings block,
# the hybrid-only shortcuts/command-palette entries, the boot adoption of
# surviving hybrid tmux sessions, and the hybrid-hooks watcher. While off, the
# backend also refuses to create or resume any hybrid session, and the frontend
# renders no hybrid surface at all. Kept dormant until Anthropic's programmatic
# billing change actually lands (it was postponed past its announced date).
CLAUDE_HYBRID_ENABLED = os.environ.get("TWICC_CLAUDE_HYBRID_ENABLED", "").strip().lower() in ("1", "true", "yes")
