"""Resolution of the provider home directories (Claude Code, Codex).

TwiCC reads provider data from, and launches every provider process against,
the homes configured in the data dir's ``.env`` — the providers' official
variables, no alias:

- ``CLAUDE_CONFIG_DIR`` — Claude Code home (default ``~/.claude``);
- ``CLAUDE_SECURESTORAGE_CONFIG_DIR`` — Claude credentials dir (its own rule,
  see :func:`claude_secure_storage_dir`);
- ``CODEX_HOME`` — Codex home (default ``~/.codex``).

Invariant: when a home is configured, no TwiCC code path, process or
subprocess touches another provider home. Hence two rules: every read of a
provider path goes through the accessors below, evaluated at call time (never
an import-time constant, which would freeze the real home before a test can
isolate it); every launch applies :func:`provider_env_overlay` explicitly.

Django-free on purpose: ``twicc claude`` / ``twicc codex`` never set up
Django, yet must resolve the same homes. Values are computed lazily on first
access and cached for the process (:func:`reset_cache` for tests). Every
public function calls :func:`twicc.paths.ensure_env_loaded` first, so the
``.env`` is honoured whatever entry point reached here.

Design: docs/plans/2026-09-02-provider-home-dirs-design.md.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from pathlib import Path
from typing import NamedTuple
from collections.abc import Mapping

from twicc.paths import PROVIDER_HOME_KEYS, ensure_env_loaded

# Base of the macOS keychain service name; the CLI appends ``-<sha8>`` when the
# credentials dir is relocated (see :func:`claude_keychain_service`). Production
# builds only (``OAUTH_FILE_SUFFIX == ""``).
CLAUDE_KEYCHAIN_SERVICE_BASE = "Claude Code-credentials"


class ProviderHomeConfigError(ValueError):
    """A provider home variable in the ``.env`` is unusable (fail fast at startup)."""


class ResolvedHome(NamedTuple):
    """One resolved home directory.

    ``path`` is for TwiCC's own filesystem reads (the configured value as a
    ``Path``, NFC-normalised like the CLIs do, never ``resolve()``d — reads
    work through symlinks either way). ``raw`` is the exact string to hand to
    the provider process; ``None`` when not configured, so nothing is passed
    and the provider applies its own default (a default value passed
    explicitly is NOT neutral: it changes Claude's keychain service name).
    """

    path: Path
    raw: str | None
    source: str  # "env" (configured in the .env) or "default"


class _Resolved(NamedTuple):
    claude_config: ResolvedHome
    claude_secure_storage: ResolvedHome
    claude_keychain_suffix: str
    codex: ResolvedHome


_cache: _Resolved | None = None


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _default_claude_home() -> Path:
    return Path.home() / ".claude"


def _default_codex_home() -> Path:
    return Path.home() / ".codex"


def _validate_absolute(key: str, value: str) -> None:
    """Reject a relative value (a leading ``~`` included).

    TwiCC does not expand: the CLIs receive the raw value and resolve a
    relative one against each process's cwd — agents run in project
    directories, so a relative home would scatter one home per project.
    """
    if not os.path.isabs(value):
        raise ProviderHomeConfigError(
            f"{key}={value!r} must be an absolute path (no '~', no relative path): "
            "the providers resolve a relative value against each process's working directory"
        )


def _resolve() -> _Resolved:
    global _cache
    if _cache is not None:
        return _cache
    ensure_env_loaded()

    claude_raw = os.environ.get("CLAUDE_CONFIG_DIR")
    if claude_raw is not None:
        if claude_raw == "":
            raise ProviderHomeConfigError(
                "CLAUDE_CONFIG_DIR is empty: Claude Code would use '' as its home "
                "(set an absolute path, or remove the line to use ~/.claude)"
            )
        _validate_absolute("CLAUDE_CONFIG_DIR", claude_raw)
        claude_config = ResolvedHome(Path(_nfc(claude_raw)), claude_raw, "env")
    else:
        claude_config = ResolvedHome(_default_claude_home(), None, "default")

    # Credentials storage (decompiled from the CLI bundle):
    #   storageDir = SECURE !== undefined ? (SECURE || ~/.claude) : configDir
    #   noSuffix   = SECURE !== undefined ? SECURE === "" : !CLAUDE_CONFIG_DIR
    secure_raw = os.environ.get("CLAUDE_SECURESTORAGE_CONFIG_DIR")
    if secure_raw is not None:
        if secure_raw == "":
            secure = ResolvedHome(_default_claude_home(), "", "env")
            no_suffix = True
            hashed = None
        else:
            _validate_absolute("CLAUDE_SECURESTORAGE_CONFIG_DIR", secure_raw)
            secure = ResolvedHome(Path(_nfc(secure_raw)), secure_raw, "env")
            no_suffix = False
            hashed = secure_raw
    else:
        secure = ResolvedHome(claude_config.path, None, claude_config.source)
        no_suffix = claude_raw is None
        hashed = claude_raw
    # The CLI hashes the raw NFC string, not a resolved path.
    suffix = "" if no_suffix else "-" + hashlib.sha256(_nfc(hashed).encode()).hexdigest()[:8]

    codex_raw = os.environ.get("CODEX_HOME")
    if codex_raw is not None:
        if codex_raw == "":
            raise ProviderHomeConfigError(
                "CODEX_HOME is empty: Codex would silently fall back to ~/.codex "
                "(set an absolute path, or remove the line to use ~/.codex)"
            )
        _validate_absolute("CODEX_HOME", codex_raw)
        codex = ResolvedHome(Path(_nfc(codex_raw)), codex_raw, "env")
    else:
        codex = ResolvedHome(_default_codex_home(), None, "default")

    _cache = _Resolved(claude_config, secure, suffix, codex)
    return _cache


def reset_cache() -> None:
    """Tests only: forget the resolved values so the next access re-reads the environment."""
    global _cache
    _cache = None


def validate() -> None:
    """Resolve every home, raising :class:`ProviderHomeConfigError` on an unusable value."""
    _resolve()


# ── Claude Code ─────────────────────────────────────────────────────────────


def claude_config_dir() -> ResolvedHome:
    """``$CLAUDE_CONFIG_DIR`` or ``~/.claude``."""
    return _resolve().claude_config


def claude_secure_storage_dir() -> ResolvedHome:
    """Where Claude's ``.credentials.json`` lives.

    ``CLAUDE_SECURESTORAGE_CONFIG_DIR`` set to a path → that path; set but
    empty → ``~/.claude`` (the way to keep the real credentials next to a
    relocated ``CLAUDE_CONFIG_DIR``, ``raw`` is ``""``); unset → the config dir.
    """
    return _resolve().claude_secure_storage


def claude_keychain_service() -> str:
    """macOS keychain service name for the credentials item.

    ``Claude Code-credentials``, plus ``-<sha256(storage dir)[:8]>`` whenever
    the credentials dir is relocated (``CLAUDE_CONFIG_DIR`` set without an
    empty ``CLAUDE_SECURESTORAGE_CONFIG_DIR``, or the latter set to a path).
    """
    return CLAUDE_KEYCHAIN_SERVICE_BASE + _resolve().claude_keychain_suffix


def claude_global_config_path() -> Path:
    """The CLI's global config file (``hasTrustDialogAccepted`` & co).

    ``<config dir>/.config.json`` when that legacy file exists, else
    ``$CLAUDE_CONFIG_DIR/.claude.json`` when configured, else ``~/.claude.json``
    (the file sits NEXT to the default config dir, not inside it).
    """
    home = _resolve().claude_config
    legacy = home.path / ".config.json"
    if legacy.is_file():
        return legacy
    if home.raw is not None:
        return home.path / ".claude.json"
    return Path.home() / ".claude.json"


def claude_projects_dir() -> Path:
    """``<claude home>/projects`` — one folder per project, one JSONL per session."""
    return claude_config_dir().path / "projects"


def claude_plans_dir() -> Path:
    """``<claude home>/plans`` — the CLI's native plan files (``<slug>.md``)."""
    return claude_config_dir().path / "plans"


# ── Codex ───────────────────────────────────────────────────────────────────


def codex_home() -> ResolvedHome:
    """``$CODEX_HOME`` or ``~/.codex``."""
    return _resolve().codex


def codex_sessions_dir() -> Path:
    """``<codex home>/sessions`` — ``YYYY/MM/DD/rollout-*.jsonl``."""
    return codex_home().path / "sessions"


def ensure_codex_home() -> None:
    """Create a CONFIGURED ``CODEX_HOME`` (never the default one).

    Codex refuses to start when ``CODEX_HOME`` points at a missing directory
    (``Error loading configuration: CODEX_HOME points to "/x", but that path
    does not exist``, exit 1, nothing created) — every app-server and
    ``codex login`` included. Creating the directory is not seeding: Codex
    populates it itself on its first run. Mode 0700 like the CLI's own default.
    """
    home = codex_home()
    if home.raw is None:
        return
    home.path.mkdir(parents=True, exist_ok=True, mode=0o700)


# ── Propagation ─────────────────────────────────────────────────────────────


def provider_env_overlay() -> dict[str, str]:
    """The configured raw values, keyed by variable name — nothing else.

    Applied explicitly at every launch point (SDK agents, subprocesses, the
    hybrid CLI, terminals, tmux, CLI passthroughs) and re-applied after every
    environment purge: explicit beats implicit for a security invariant.
    Never contains a default value (section 3.4 of the design): passing
    ``CLAUDE_CONFIG_DIR=~/.claude`` explicitly would change the keychain
    service name.
    """
    resolved = _resolve()
    overlay: dict[str, str] = {}
    if resolved.claude_config.raw is not None:
        overlay["CLAUDE_CONFIG_DIR"] = resolved.claude_config.raw
    if resolved.claude_secure_storage.raw is not None:
        overlay["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = resolved.claude_secure_storage.raw
    if resolved.codex.raw is not None:
        overlay["CODEX_HOME"] = resolved.codex.raw
    return overlay


def provider_home_mismatches(env: Mapping[str, str]) -> list[str]:
    """Provider home keys whose value in ``env`` differs from this instance's.

    For a process TwiCC did not launch under the current configuration (a
    hybrid CLI adopted at boot): a key configured here must carry the same
    value there; a key not configured here must be absent there.
    """
    overlay = provider_env_overlay()
    return [key for key in PROVIDER_HOME_KEYS if env.get(key) != overlay.get(key)]


def describe_provider_homes() -> list[str]:
    """Human lines for the startup log / devctl, one per resolved location."""
    resolved = _resolve()
    lines = [_describe("Claude Code home", resolved.claude_config, "CLAUDE_CONFIG_DIR")]
    secure = resolved.claude_secure_storage
    if secure.raw == "":
        lines.append(f"Claude Code credentials: {secure.path} (CLAUDE_SECURESTORAGE_CONFIG_DIR empty)")
    elif secure.raw is not None:
        lines.append(_describe("Claude Code credentials", secure, "CLAUDE_SECURESTORAGE_CONFIG_DIR"))
    lines.append(_describe("Codex home", resolved.codex, "CODEX_HOME"))
    return lines


def _describe(label: str, home: ResolvedHome, key: str) -> str:
    origin = f"{key} from .env" if home.source == "env" else "default"
    return f"{label}: {home.path} ({origin})"
