"""The provider-home resolver (``twicc.provider_homes``) and the test isolation.

Design: docs/plans/2026-09-02-provider-home-dirs-design.md (sections 2, 3, 5, 11).
"""

import ast
import hashlib
import os
import stat
from pathlib import Path

import pytest

from twicc import provider_homes
from twicc.paths import PROVIDER_HOME_KEYS
from twicc.provider_homes import ProviderHomeConfigError, ResolvedHome


def _sha8(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """A clean slate: fake ``$HOME``, no provider home variable set, cache reset.

    Restores the ``settings_test`` isolation values afterwards (cache reset
    before monkeypatch puts the environment back).
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    for key in PROVIDER_HOME_KEYS:
        monkeypatch.delenv(key, raising=False)
    provider_homes.reset_cache()
    yield fake_home
    provider_homes.reset_cache()


class TestDefaults:
    def test_nothing_configured(self, homes):
        assert provider_homes.claude_config_dir() == ResolvedHome(homes / ".claude", None, "default")
        assert provider_homes.codex_home() == ResolvedHome(homes / ".codex", None, "default")
        assert provider_homes.claude_secure_storage_dir() == ResolvedHome(homes / ".claude", None, "default")
        assert provider_homes.provider_env_overlay() == {}
        assert provider_homes.claude_keychain_service() == "Claude Code-credentials"
        assert provider_homes.claude_global_config_path() == homes / ".claude.json"
        assert provider_homes.claude_projects_dir() == homes / ".claude" / "projects"
        assert provider_homes.claude_plans_dir() == homes / ".claude" / "plans"
        assert provider_homes.codex_sessions_dir() == homes / ".codex" / "sessions"
        assert provider_homes.describe_provider_homes() == [
            f"Claude Code home: {homes / '.claude'} (default)",
            f"Codex home: {homes / '.codex'} (default)",
        ]

    def test_cached_until_reset(self, homes, monkeypatch):
        assert provider_homes.codex_home().source == "default"
        monkeypatch.setenv("CODEX_HOME", "/configured")
        assert provider_homes.codex_home().source == "default"  # cached
        provider_homes.reset_cache()
        assert provider_homes.codex_home() == ResolvedHome(Path("/configured"), "/configured", "env")


class TestConfigured:
    def test_both_homes(self, homes, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/x")
        monkeypatch.setenv("CODEX_HOME", "/y")
        assert provider_homes.claude_config_dir() == ResolvedHome(Path("/x"), "/x", "env")
        assert provider_homes.codex_home() == ResolvedHome(Path("/y"), "/y", "env")
        # Unset CLAUDE_SECURESTORAGE_CONFIG_DIR → the credentials follow the config dir
        assert provider_homes.claude_secure_storage_dir() == ResolvedHome(Path("/x"), None, "env")
        assert provider_homes.provider_env_overlay() == {"CLAUDE_CONFIG_DIR": "/x", "CODEX_HOME": "/y"}
        assert provider_homes.claude_projects_dir() == Path("/x/projects")
        assert provider_homes.claude_plans_dir() == Path("/x/plans")
        assert provider_homes.codex_sessions_dir() == Path("/y/sessions")
        assert provider_homes.claude_global_config_path() == Path("/x/.claude.json")
        assert provider_homes.describe_provider_homes() == [
            "Claude Code home: /x (CLAUDE_CONFIG_DIR from .env)",
            "Codex home: /y (CODEX_HOME from .env)",
        ]

    def test_raw_value_is_passed_unchanged(self, homes, monkeypatch, tmp_path):
        # A symlinked path stays a symlinked path: the CLI hashes the raw string.
        real = tmp_path / "real-home"
        real.mkdir()
        link = tmp_path / "link-home"
        link.symlink_to(real)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(link))
        assert provider_homes.claude_config_dir().raw == str(link)
        assert provider_homes.claude_config_dir().path == link
        assert provider_homes.claude_keychain_service() == "Claude Code-credentials-" + _sha8(str(link))

    def test_nfc_normalised_path_for_reads(self, homes, monkeypatch):
        nfd = "/tmp/café"  # e + combining acute
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", nfd)
        home = provider_homes.claude_config_dir()
        assert home.raw == nfd  # exact string for the CLI
        assert str(home.path) == "/tmp/café"  # what the CLI creates on disk
        # The keychain hash is over the NFC string, as the CLI does
        assert provider_homes.claude_keychain_service() == "Claude Code-credentials-" + _sha8("/tmp/café")


class TestValidation:
    @pytest.mark.parametrize("key", ["CLAUDE_CONFIG_DIR", "CODEX_HOME"])
    def test_empty_rejected(self, homes, monkeypatch, key):
        monkeypatch.setenv(key, "")
        with pytest.raises(ProviderHomeConfigError, match=f"{key} is empty"):
            provider_homes.validate()

    @pytest.mark.parametrize("key", ["CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CODEX_HOME"])
    @pytest.mark.parametrize("value", ["relative/home", "~/.claude-other", "."])
    def test_relative_rejected(self, homes, monkeypatch, key, value):
        monkeypatch.setenv(key, value)
        with pytest.raises(ProviderHomeConfigError, match=f"{key}=.*must be an absolute path"):
            provider_homes.validate()

    def test_validate_passes_on_absolute(self, homes, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/x")
        monkeypatch.setenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", "/y")
        monkeypatch.setenv("CODEX_HOME", "/z")
        provider_homes.validate()

    def test_error_is_a_value_error(self):
        assert issubclass(ProviderHomeConfigError, ValueError)


class TestSecureStorage:
    """The four rows of the design's section 2.1 table."""

    def test_config_dir_only(self, homes, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/x")
        assert provider_homes.claude_secure_storage_dir().path == Path("/x")
        assert provider_homes.claude_keychain_service() == "Claude Code-credentials-" + _sha8("/x")
        assert "CLAUDE_SECURESTORAGE_CONFIG_DIR" not in provider_homes.provider_env_overlay()

    def test_config_dir_with_empty_secure_storage(self, homes, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/x")
        monkeypatch.setenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", "")
        assert provider_homes.claude_secure_storage_dir() == ResolvedHome(homes / ".claude", "", "env")
        assert provider_homes.claude_keychain_service() == "Claude Code-credentials"
        # The empty value IS passed: it is what keeps the CLI on the default credentials
        assert provider_homes.provider_env_overlay() == {
            "CLAUDE_CONFIG_DIR": "/x",
            "CLAUDE_SECURESTORAGE_CONFIG_DIR": "",
        }
        assert provider_homes.describe_provider_homes() == [
            "Claude Code home: /x (CLAUDE_CONFIG_DIR from .env)",
            f"Claude Code credentials: {homes / '.claude'} (CLAUDE_SECURESTORAGE_CONFIG_DIR empty)",
            f"Codex home: {homes / '.codex'} (default)",
        ]

    def test_secure_storage_only(self, homes, monkeypatch):
        monkeypatch.setenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", "/y")
        assert provider_homes.claude_config_dir().source == "default"
        assert provider_homes.claude_secure_storage_dir() == ResolvedHome(Path("/y"), "/y", "env")
        assert provider_homes.claude_keychain_service() == "Claude Code-credentials-" + _sha8("/y")
        assert provider_homes.provider_env_overlay() == {"CLAUDE_SECURESTORAGE_CONFIG_DIR": "/y"}
        assert provider_homes.describe_provider_homes()[1] == (
            "Claude Code credentials: /y (CLAUDE_SECURESTORAGE_CONFIG_DIR from .env)"
        )

    def test_both_set_hashes_secure_storage(self, homes, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/x")
        monkeypatch.setenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", "/y")
        assert provider_homes.claude_keychain_service() == "Claude Code-credentials-" + _sha8("/y")

    def test_keychain_vectors(self, homes, monkeypatch):
        # Fixed vectors, so a regression in the hashing shows as a value change.
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/home/u/dev/wt/claude-home")
        assert provider_homes.claude_keychain_service() == (
            "Claude Code-credentials-" + hashlib.sha256(b"/home/u/dev/wt/claude-home").hexdigest()[:8]
        )
        assert _sha8("/home/u/dev/wt/claude-home") == hashlib.sha256(b"/home/u/dev/wt/claude-home").hexdigest()[:8]


class TestGlobalConfigPath:
    def test_legacy_config_json_wins(self, homes, monkeypatch, tmp_path):
        home = tmp_path / "x"
        home.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
        assert provider_homes.claude_global_config_path() == home / ".claude.json"
        (home / ".config.json").write_text("{}")
        assert provider_homes.claude_global_config_path() == home / ".config.json"

    def test_default_legacy_lives_inside_default_dir(self, homes):
        (homes / ".claude").mkdir()
        (homes / ".claude" / ".config.json").write_text("{}")
        assert provider_homes.claude_global_config_path() == homes / ".claude" / ".config.json"


class TestEnsureCodexHome:
    def test_creates_configured_home(self, homes, monkeypatch, tmp_path):
        target = tmp_path / "nested" / "codex-home"
        monkeypatch.setenv("CODEX_HOME", str(target))
        provider_homes.ensure_codex_home()
        assert target.is_dir()
        assert stat.S_IMODE(target.stat().st_mode) == 0o700
        provider_homes.ensure_codex_home()  # idempotent

    def test_never_creates_the_default(self, homes):
        provider_homes.ensure_codex_home()
        assert not (homes / ".codex").exists()


class TestMismatches:
    def test_mismatches(self, homes, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/x")
        assert provider_homes.provider_home_mismatches({"CLAUDE_CONFIG_DIR": "/x"}) == []
        assert provider_homes.provider_home_mismatches({}) == ["CLAUDE_CONFIG_DIR"]
        assert provider_homes.provider_home_mismatches(
            {"CLAUDE_CONFIG_DIR": "/x", "CODEX_HOME": "/other"}
        ) == ["CODEX_HOME"]


class TestSettingsTestIsolation:
    """``settings_test`` must keep every read away from the developer's real homes."""

    def test_every_accessor_is_under_the_temporary_root(self):
        from django.conf import settings

        from twicc.settings_test import _PROVIDER_HOMES_ROOT
        from twicc.providers.claude_code import auth as claude_auth
        from twicc.providers.claude_code import trust as claude_trust
        from twicc.providers.codex import credentials as codex_credentials
        from twicc.providers.codex import trust as codex_trust

        root = str(_PROVIDER_HOMES_ROOT)
        paths = [
            provider_homes.claude_config_dir().path,
            provider_homes.claude_secure_storage_dir().path,
            provider_homes.codex_home().path,
            provider_homes.claude_projects_dir(),
            provider_homes.claude_plans_dir(),
            provider_homes.codex_sessions_dir(),
            provider_homes.claude_global_config_path(),
            claude_auth.credentials_path(),
            claude_trust._config_path(),
            codex_credentials.credentials_path(),
            codex_trust._config_path(),
            settings.CLAUDE_CONFIG_DIR,
            settings.CLAUDE_SECURE_STORAGE_DIR,
            settings.CODEX_HOME,
        ]
        for path in paths:
            assert str(path).startswith(root), path
        # Never the real keychain entry: the credentials dir is a path, so the
        # service name carries a suffix.
        assert provider_homes.claude_keychain_service() != "Claude Code-credentials"
        assert set(provider_homes.provider_env_overlay()) == set(PROVIDER_HOME_KEYS)

    def test_watchers_resolve_at_instantiation(self, provider_home):
        from twicc.providers.claude_code.plans_watcher import ClaudeCodePlansWatcher
        from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
        from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher

        assert ClaudeCodeSessionsWatcher().projects_dir == provider_home.claude / "projects"
        assert ClaudeCodePlansWatcher().directory == provider_home.claude / "plans"
        assert CodexSessionsWatcher().projects_dir == provider_home.codex / "sessions"

    def test_sdk_session_lookup_follows_the_environment(self, provider_home):
        # ``titles.rename_session_in_jsonl`` calls the SDK, which resolves the
        # projects dir itself from ``os.environ["CLAUDE_CONFIG_DIR"]`` (no
        # ``options.env``): correct only because the loader keeps ``os.environ``
        # right. Pin the SDK contract.
        from claude_agent_sdk._internal.sessions import _get_projects_dir

        assert _get_projects_dir() == provider_homes.claude_projects_dir()
        assert os.environ["CLAUDE_CONFIG_DIR"] == str(provider_home.claude)


def test_settings_module_imports_no_provider_module():
    """``settings.py`` reads the homes through ``twicc.provider_homes`` only.

    Inspects the module's direct imports: ``sys.modules`` cannot be used
    because ``import twicc`` already pulls ``providers.*.constants`` through
    the CLI package.
    """
    import twicc.settings as settings_module

    tree = ast.parse(Path(settings_module.__file__).read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
            imported += [f"{node.module}.{alias.name}" for alias in node.names]
    assert not [name for name in imported if name.startswith("twicc.providers")], imported
