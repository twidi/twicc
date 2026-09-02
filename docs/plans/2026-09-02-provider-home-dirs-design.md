# Provider Home Directories Design

**Status:** approved design, not implemented
**Date:** 2026-09-02

## 1. Scope

TwiCC hardcodes `~/.claude` and `~/.codex` everywhere. Both providers honour an
environment variable that relocates their whole home directory:

- Claude Code: `CLAUDE_CONFIG_DIR` (plus `CLAUDE_SECURESTORAGE_CONFIG_DIR` for
  the credentials only);
- Codex: `CODEX_HOME`.

This design makes TwiCC read those variables from its own `.env`, resolve them
once, read provider data from the resolved locations, and hand the variables to
every process it launches (SDK agents, throwaway agents, CLI passthroughs, the
hybrid CLI, terminals, tmux).

**Invariant:** when a home is configured in the `.env`, no TwiCC code path,
process, or subprocess ever touches another provider home. Not the server, not
a CLI command, not a one-liner, not a terminal opened from the UI.

**Primary use case:** a git worktree instance that works on separate provider
data so a risky change (a migration, a compute rewrite) cannot damage the real
homes. Worktrees already get their own data dir and `.env`; this design makes
the provider homes follow.

Rules from `CLAUDE.md` apply: no migration is needed (`Session.file_path` is
relative to the provider data root), no CHANGELOG entry without an explicit
ask, `AGENTS.md` mirrors every `CLAUDE.md` change.

## 2. Provider facts (verified in the bundled binaries)

### 2.1 Claude Code (`claude-agent-sdk` bundled CLI, 2.1.258)

Config dir resolution, decompiled from the bundle:

```js
configDir = (process.env.CLAUDE_CONFIG_DIR ?? join(HOME, ".claude")).normalize("NFC")
```

Everything lives under `configDir` once the variable is set. Seen in the
bundle as direct `join(configDir, …)`: `projects/` (session JSONL,
`subagents/`, `workflows/`, per-project `memory/`), `plans/`,
`plugins/cache/`, `skills/`, `workflows/`, `settings.json`, `CLAUDE.md`,
`feedback/`, `state/`. Through the storage builder rooted at it:
`plugins/marketplaces/`, `plugins/installed_plugins.json`, `commands/`,
`paste-cache/`. `.credentials.json` follows the storage dir (below).

One known read outside the config dir: with `CLAUDE_CONFIG_DIR` set, the CLI
still **reads** `~/.claude/ide/` for IDE lock discovery (in addition to
`<configDir>/ide/`). Read-only; the section 1 invariant is about TwiCC's own
behaviour and the writes of the processes it launches.

The **global config file** `.claude.json` moves too:

```js
configPath = join(process.env.CLAUDE_CONFIG_DIR || HOME, ".claude.json")
```

So it is `~/.claude.json` without the variable and `$CLAUDE_CONFIG_DIR/.claude.json`
with it.

**Credentials storage** has its own resolution:

```js
storageDir = CLAUDE_SECURESTORAGE_CONFIG_DIR !== undefined
    ? (CLAUDE_SECURESTORAGE_CONFIG_DIR || join(HOME, ".claude")).normalize("NFC")
    : configDir
noSuffix = CLAUDE_SECURESTORAGE_CONFIG_DIR !== undefined
    ? CLAUDE_SECURESTORAGE_CONFIG_DIR === ""
    : !CLAUDE_CONFIG_DIR            // falsy: undefined or "" (TwiCC rejects "")
suffix  = noSuffix ? "" : "-" + sha256(storageDir).hex.slice(0, 8)
keychainService = "Claude Code" + OAUTH_FILE_SUFFIX + "-credentials" + suffix
credentialsFile = join(storageDir, ".credentials.json")
```

| Variables | `.credentials.json` | macOS keychain service |
|---|---|---|
| none | `~/.claude/.credentials.json` | `Claude Code-credentials` |
| `CLAUDE_CONFIG_DIR=/x` | `/x/.credentials.json` | `Claude Code-credentials-<sha256("/x")[:8]>` |
| `CLAUDE_CONFIG_DIR=/x`, `CLAUDE_SECURESTORAGE_CONFIG_DIR=` (empty) | `~/.claude/.credentials.json` | `Claude Code-credentials` |
| `CLAUDE_SECURESTORAGE_CONFIG_DIR=/y` | `/y/.credentials.json` | `Claude Code-credentials-<sha256("/y")[:8]>` |

Notes:

- The hash is over the **raw NFC string**, not a resolved path. TwiCC must hand
  the value to the CLI unchanged and hash the same string.
- `OAUTH_FILE_SUFFIX` is `""` in production builds (`-local-oauth`, `-custom-oauth`
  exist for internal/custom OAuth). The current constant `Claude Code-credentials`
  in `src/twicc/providers/claude_code/auth.py` is correct for production.
- The keychain **account** is the username, unchanged.
- Setting `CLAUDE_CONFIG_DIR` to its default value still changes the keychain
  service name. TwiCC therefore never forces a default value into the
  environment: a variable is passed only when the `.env` sets it.
- An empty `CLAUDE_CONFIG_DIR` is **not** "unset": `?? default` only applies to
  nullish values, so `""` becomes the config dir: `projects/` resolves relative
  to the cwd while `.claude.json` falls back (`||`) to `~/.claude.json`, a
  split brain; the Python SDK (`claude_agent_sdk/_internal/sessions.py`,
  `if config_dir:`) would even disagree with the CLI on `projects/`. TwiCC
  rejects it (section 3.3).
- A relative value is accepted by the CLI and resolved against the **current
  working directory** of each process (verified: `CLAUDE_CONFIG_DIR=rel claude
  auth status` creates `<cwd>/rel`). Agents run in project directories, so a
  relative value means one home per project. TwiCC rejects it (section 3.3).
  The bundle strings `is not an absolute path` / `changed after Claude Code
  started` belong to a git-dir placement check, not to startup.
- The CLI creates a missing config dir on first use (verified: `.claude.json`
  and `backups/` appear).
- Legacy fallback: when `<configDir>/.config.json` exists, the CLI uses it as
  the global config file instead of `.claude.json`
  (`legacyPath` / `configPath` pair in the bundle). `trust.py` mirrors it
  (section 6.1).

### 2.2 Codex (`codex` 0.150.1)

The binary references `CODEX_HOME` throughout (`sessions/`, `auth.json`,
`config.toml`, `plugins/cache/`, `generated_images/`, `log/`, `themes/`, MCP
OAuth, remote control). Default `~/.codex`.

Keyring account for the OS keyring backend (`codex-rs/login/src/auth/storage.rs`):
`"cli|" + sha256(canonicalize(codex_home))[:16]`. Unlike Claude, the path is
**canonicalized** (symlinks resolved). `_compute_keyring_account` in
`src/twicc/providers/codex/credentials.py` already mirrors this; it must receive
the resolved home.

Behaviour verified on the binary:

- **A missing `CODEX_HOME` is fatal**: `Error loading configuration:
  CODEX_HOME points to "/x", but that path does not exist`, exit 1, nothing
  created. This applies to `codex login status`, `codex login` and every
  `app-server`. TwiCC therefore creates a configured `CODEX_HOME` itself
  (section 3.5).
- An empty `CODEX_HOME` is treated as unset by Codex. TwiCC still rejects it
  (section 3.3) so that "empty" never silently means "default".
- A relative `CODEX_HOME` resolves against the process cwd (then fails when
  absent). TwiCC rejects it.

Codex has no credentials-relocation variable. A new `CODEX_HOME` needs one
`codex login` (from a terminal of the instance, section 13.3).

## 3. Configuration contract

### 3.1 Keys

The `.env` of the data dir accepts the **official** names, no `TWICC_` aliases:

| Key | Meaning | Empty value |
|---|---|---|
| `CLAUDE_CONFIG_DIR` | Claude Code home | rejected (fail fast) |
| `CLAUDE_SECURESTORAGE_CONFIG_DIR` | Claude credentials dir | valid: "use the default location" |
| `CODEX_HOME` | Codex home | rejected (fail fast) |

Using the official names means the same variable reaches the CLI, the SDK, the
terminal user and any script unchanged. No translation layer.

### 3.2 Precedence: the `.env` wins

Today `load_dotenv()` is called with the default `override=False`
(`src/twicc/cli/run.py`, `src/twicc/settings.py`): a variable already present
in the process environment silently beats the `.env`. `devctl.py` purges
`TWICC_*` in worktrees to work around exactly that leak.

In TwiCC, inheritance is almost always accidental: every agent, terminal and
`devctl` run from an agent session inherits the backend's whole environment.
With provider homes in play, `override=False` lets a worktree started from a
main-instance agent session inherit main's `CODEX_HOME` and write into main's
home. That violates the invariant.

Decision, three rules:

1. **Every `.env` load uses `override=True`.** A key defined in the `.env`
   replaces an inherited value. Keys the `.env` does not define still come
   from the process environment: `override=True` only touches keys present in
   the file. `devctl.py` keeps injecting its worktree flags (`TWICC_DEBUG`,
   `TWICC_NO_*`, `TWICC_SESSION_COOKIE`, …) through the environment, and the
   documented `TWICC_NO_MCP=1` / `TWICC_NO_CODEX_RUNTIME_CLEANUP=1` one-liners
   keep working. `devctl.py`'s worktree `TWICC_*` purge stays: it protects keys
   the worktree `.env` does not define (`TWICC_PASSWORD_HASH`).
2. **The three provider keys are `.env`-exclusive.** After the load, any of
   `CLAUDE_CONFIG_DIR`, `CLAUDE_SECURESTORAGE_CONFIG_DIR`, `CODEX_HOME` that is
   **not defined in the `.env` file** is removed from `os.environ`, with a
   warning naming the dropped value. Rule 1 alone leaves a hole: a worktree
   whose `.env` sets no home, started from an agent session of an instance
   whose `.env` does, would inherit that home and write into it. Rule 2 closes
   it for every launcher, not only devctl.
3. **`TWICC_DATA_DIR` is environment-only.** The loader skips it when the
   `.env` defines it (with a warning). `README.md` lists it in the `.env`
   table today; with `override=True` a `.env` found through data dir A that
   says `TWICC_DATA_DIR=B` would move every later `get_data_dir()` to B while
   the configuration came from A. The README row moves to an
   "environment only" note. This is a deliberate behaviour change: the
   `.env`-as-redirect trick (unset in the shell, `TWICC_DATA_DIR` in
   `~/.twicc/.env`) stops working.

The documented model:

> The `.env` in the data dir is the instance configuration. A key defined
> there wins over the process environment. The provider home keys are read
> **only** from the `.env`: an inherited `CLAUDE_CONFIG_DIR` / `CODEX_HOME` is
> ignored (and logged). `TWICC_DATA_DIR` is the one variable read **only**
> from the environment, because it locates the `.env`.

Consequence to document: a user who exports `CLAUDE_CONFIG_DIR` in their shell
profile must also write it in the `.env`, or TwiCC uses `~/.claude`. The
startup lines of section 10 and the warning make the mismatch visible.

### 3.3 Validation (fail fast)

At resolution time, for `CLAUDE_CONFIG_DIR` and `CODEX_HOME` when set:

- empty string → error (Claude would use `""` as its home; Codex would
  silently fall back to the default);
- not absolute, including a leading `~` → error. TwiCC does **not** expand:
  the CLIs receive the raw value and resolve a relative one against each
  process's cwd, which would scatter one home per project directory.

For `CLAUDE_SECURESTORAGE_CONFIG_DIR` when set and non-empty: same absolute
rule.

The resolver raises `ProviderHomeConfigError`. Who handles it:

- every `twicc` invocation, server included: `cli/__init__.py` `main()`
  calls `provider_homes.validate()` before `app()` dispatches; on error it
  prints the message to stderr and exits 1. **Inside `main()`, never at
  module level**: `import twicc` runs `cli/__init__.py` (section 4), so a
  module-level exit would kill every Django-only entry point during package
  import. Order inside `main()`: the `--remote` forward (`maybe_forward()`)
  first, then warnings (section 10), then `validate()`, then `app()`: a
  broken local `.env` must not block a command aimed at a remote instance.
  No Django is set up yet in `main()`, so `run.py`, `twicc claude`,
  `twicc codex` and every subcommand that calls `django.setup()` all fail the
  same way, before any traceback;
- Django-only entry points (`python -m django --settings=twicc.settings`,
  one-liners, pytest): `settings.py` re-raises as
  `django.core.exceptions.ImproperlyConfigured` with the same message. The
  compute worker is spawned by an already-validated server.

The directory does not need to exist beforehand: Claude creates its home,
TwiCC creates Codex's (section 3.5), and the sync/watcher code already
handles a missing root (`scan_projects`, `scan_session_files`,
`sessions_watcher` wait loop).

### 3.4 Only when set

A variable absent from the `.env` is never materialized (section 3.2 rule 2
guarantees it is absent from `os.environ` too). TwiCC then reads `~/.claude`
/ `~/.codex` like today and passes nothing. Reason: section 2.1, keychain
suffix.

### 3.5 Creating a configured `CODEX_HOME`

Codex refuses to start when `CODEX_HOME` does not exist (section 2.2).
`provider_homes.ensure_codex_home()` does `mkdir(parents=True, exist_ok=True,
mode=0o700)` on the configured path (never on the default). Call sites:

- `run.py`, right after validation;
- `providers/codex/bin.py` `make_codex_config()` (covers every app-server;
  no `twicc` CLI command builds one);
- `providers/codex/auth.py` before `codex login status`;
- `cli/codex.py` before `execvp`.

Creating the directory is not seeding. Codex itself populates `tmp/`
(PATH-alias helper binaries) on its very first run, `codex login status`
included, so "empty" is never a usable signal for "not logged in"
(section 9). Exception: under the system temp dir Codex refuses to create
the helpers (`Refusing to create helper binaries under temporary dir`) and
leaves the home empty; a test on a pytest `tmp_path` must not assert `tmp/`.

## 4. Environment loading: one loader, once per process

New helper in `src/twicc/paths.py` (already imported by every entry point,
Django-free):

```python
PROVIDER_HOME_KEYS = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CODEX_HOME")
_ENV_LOADED = False
_ENV_WARNINGS: list[str] = []

def ensure_env_loaded() -> None:
    """Load <data_dir>/.env into os.environ once per process.

    Keys defined in the file win (override=True), except TWICC_DATA_DIR
    (environment-only, skipped with a warning). The provider home keys are
    .env-exclusive: an inherited value for a key the file does not define
    (or defines without a value, a bare ``KEY`` line) is dropped.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    from dotenv import dotenv_values
    env_path = get_env_path()
    values = dotenv_values(env_path) if env_path.is_file() else {}
    if "TWICC_DATA_DIR" in values:
        _ENV_WARNINGS.append(f"Ignoring TWICC_DATA_DIR in {env_path}: environment-only")
        del values["TWICC_DATA_DIR"]
    for key, value in values.items():
        if value is not None:               # bare ``KEY`` lines carry None
            os.environ[key] = value
    for key in PROVIDER_HOME_KEYS:
        if values.get(key) is None and key in os.environ:
            _ENV_WARNINGS.append(f"Ignoring inherited {key}={os.environ[key]!r}: not set in {env_path}")
            del os.environ[key]
    _ENV_LOADED = True

def get_env_load_warnings() -> list[str]:
    """Warnings produced by the first load; callers print or log them."""
    return list(_ENV_WARNINGS)
```

`dotenv_values` reports `KEY=` as `""`, so `CLAUDE_SECURESTORAGE_CONFIG_DIR=`
counts as defined (section 3.1); a bare `KEY` line reports `None` and counts
as not defined. Assigning the values directly (instead of `load_dotenv`) is
what lets the loader skip `TWICC_DATA_DIR`; interpolation semantics are those
of `dotenv_values` (file-first, the same as `load_dotenv(override=True)`).

`src/twicc/cli/password.py` also reads and rewrites the `.env`
(`dotenv_values`, `set_key`, `unset_key`) but never loads it into the
environment; unchanged, `set_key` preserves the other lines.

Import-order fact: `src/twicc/__init__.py` is `from twicc.cli import main`, so
the call at the top of `cli/__init__.py` runs on **any** `import twicc`. That
is the guarantee the invariant needs: whatever imports the package loads the
`.env` first. The loader is imported from `twicc.paths` among the first
lines of `cli/__init__.py` and called before any `twicc.cli.*`
command-module import: those pull provider modules through
`create_session.command` → `_drop_request/help_context` →
`providers.*.constants` (the existing top imports `_drop_request.project`,
`_output`, `version` read no environment at import). Only the load is
module-level; validation, warning output and any exit happen in `main()`
(section 3.3), which gates every invocation, `--version`, `--help`,
`password` and `token` included. `TWICC_DATA_DIR` must be set before the import (it is: devctl
exports it, worktree one-liners prefix it, pytest reads it from the shell).

A test-only `paths._reset_env_loader()` clears `_ENV_LOADED` and
`_ENV_WARNINGS` so loader tests can run it repeatedly against temporary
`.env` files.

Call sites (`run.py` and `settings.py` replace their current
`load_dotenv(get_env_path())`; the other two are new):

| Entry point | Where | Covers |
|---|---|---|
| `twicc` CLI, every subcommand | top of `src/twicc/cli/__init__.py`, before `app` is built | `twicc claude`, `twicc codex`, drop-request commands, `twicc` (server) |
| server | `src/twicc/cli/run.py` (keeps its early call; now idempotent) | boot order unchanged |
| Django settings | `src/twicc/settings.py` | `python -m django --settings=twicc.settings`, one-liners, pytest, compute worker |
| provider-home resolver | `src/twicc/provider_homes.py` at first resolution | any path that reaches a home |

The once-per-process guard matters for tests: `settings_test` sets the
provider variables **after** the first load (section 11); a later
`ensure_env_loaded()` must not clobber them with `override=True` nor drop them
as "inherited".

`run.py` (root dev script) goes through `cli.main()`, so it is covered.
`devctl.py` keeps its own `.env` parser (`load_env_file`); it never imports
`twicc`.

## 5. The resolver: `src/twicc/provider_homes.py`

Django-free module (it must serve `twicc claude` / `twicc codex`, which never
set up Django). Values are computed lazily on first access and cached for the
process. Every public function calls `ensure_env_loaded()` first.

```python
class ProviderHomeConfigError(ValueError): ...

class ResolvedHome(NamedTuple):
    path: Path          # for TwiCC's own filesystem reads
    raw: str | None     # exact value to pass to the CLI; None = not configured
    source: str         # "env" (configured) or "default"

def claude_config_dir() -> ResolvedHome           # $CLAUDE_CONFIG_DIR or ~/.claude
def claude_secure_storage_dir() -> ResolvedHome   # section 2.1 rule
def claude_keychain_service() -> str              # "Claude Code-credentials" + suffix
def claude_global_config_path() -> Path           # <config dir>/.claude.json or ~/.claude.json
def codex_home() -> ResolvedHome                  # $CODEX_HOME or ~/.codex
def provider_env_overlay() -> dict[str, str]      # only the configured raw values
def describe_provider_homes() -> list[str]        # human lines for logs / devctl
def validate() -> None                            # raises ProviderHomeConfigError (section 3.3)
def ensure_codex_home() -> None                   # section 3.5
def reset_cache() -> None                         # tests only (section 11)

# Derived data roots, evaluated at call time (section 6)
def claude_projects_dir() -> Path                 # <claude home>/projects
def claude_plans_dir() -> Path                    # <claude home>/plans
def codex_sessions_dir() -> Path                  # <codex home>/sessions
```

Rules:

- `path` is always the raw value as a `Path`, never resolved. Filesystem reads
  work through symlinks either way; `_compute_keyring_account` already
  canonicalizes internally for Codex.
- `provider_env_overlay()` contains at most `CLAUDE_CONFIG_DIR`,
  `CLAUDE_SECURESTORAGE_CONFIG_DIR` (possibly `""`), `CODEX_HOME`. Never a
  default value (section 3.4).
- `claude_keychain_service()` hashes `storageDir` exactly as section 2.1
  (`unicodedata.normalize("NFC", value)`, `sha256(...).hexdigest()[:8]`).
- `describe_provider_homes()` returns one line per provider, e.g.
  `Claude Code home: /x (CLAUDE_CONFIG_DIR from .env)`,
  `Claude Code credentials: ~/.claude (CLAUDE_SECURESTORAGE_CONFIG_DIR empty)`,
  `Codex home: ~/.codex (default)`.

Django settings (`src/twicc/settings.py`) expose the resolved values so the
rest of the backend has one canonical, logged copy:

```python
CLAUDE_CONFIG_DIR: Path            # resolved path
CLAUDE_SECURE_STORAGE_DIR: Path
CODEX_HOME: Path
PROVIDER_HOMES_DESCRIPTION: list[str]
```

They are informational; code reads paths from the resolver, not from settings,
so Django-free processes and tests share one source of truth. `settings.py`
wraps the resolution in `try/except ProviderHomeConfigError` and re-raises
`ImproperlyConfigured` (section 3.3). `settings.py` itself imports no
provider module today and must stay that way (a test inspects the module's
direct imports; `sys.modules` cannot be used because `import twicc` already
pulls `providers.*.constants` through the CLI package, section 4).

## 6. Read side: every hardcoded path goes through the resolver

**Every read is evaluated at call time**, never at import. `import twicc`
already imports `providers.claude_code.constants` (owner of `PLANS_DIR`),
`providers.codex.constants` and `providers.helpers` (through
`cli/__init__.py` → `create_session.command` →
`_drop_request/help_context.py`), i.e. before `settings_test` or any test
can change the environment; the other modules below are imported by
`django.setup()` or the first sync, still before any test can reset the
resolver. An import-time constant would freeze the real home. So the module
constants and ClassVars below become functions of the resolver (section 5)
or attributes set when an object is instantiated, and the resolver's cache is
the single thing tests reset.

Tests that monkeypatch `ClaudeCodeHelpers.PROJECTS_DIR`,
`CodexHelpers.SESSIONS_DIR` and `constants.PLANS_DIR` today
(`tests/test_claude_subagent_lifecycle.py`,
`tests/test_codex_guardian_session_filtering.py`,
`tests/test_plan_docs_providers.py`) switch to
`monkeypatch.setenv("CLAUDE_CONFIG_DIR" | "CODEX_HOME", tmp)` +
`provider_homes.reset_cache()` (a small `provider_home` fixture in
`tests/conftest.py`), and create the `projects/` / `sessions/` / `plans/`
subfolder they need.

### 6.1 Claude Code

| Location | Today | After |
|---|---|---|
| `providers/claude_code/helpers.py` `PROJECTS_DIR` ClassVar | `Path.home()/.claude/projects` | removed; the 11 code uses (`initial_sync.py` ×6, `compute.py` ~407/~783, `workflow_synthesis.py` ×3) call `claude_projects_dir()`; the two `initial_sync.py` docstrings and the `compute.py` ~778 comment naming it are updated |
| `providers/claude_code/sessions_watcher.py` `projects_dir` ClassVar | copy of `PROJECTS_DIR` | instance attribute set in `__init__` from `claude_projects_dir()` (the watcher is instantiated lazily by `get_watcher()`); same for the base-class annotation |
| `providers/claude_code/constants.py` `PLANS_DIR` | `Path.home()/.claude/plans` | removed; `helpers.py` (`resolve_plan_path`), `plans_watcher.py` (`self.directory` in `__init__`), `compute.py` ~834 call `claude_plans_dir()` |
| `providers/claude_code/agent/permissions.py` (legacy plan fallback) | `Path.home()/.claude/plans/<slug>.md` | `claude_plans_dir() / f"{slug}.md"` |
| `providers/claude_code/auth.py` `CREDENTIALS_PATH` | `Path.home()/.claude/.credentials.json` | function `credentials_path()` = `claude_secure_storage_dir().path / ".credentials.json"` |
| `providers/claude_code/auth.py` `KEYCHAIN_SERVICE` | constant | `claude_keychain_service()` |
| `providers/claude_code/trust.py` `_config_path()` | `Path.home()/.claude.json` | `claude_global_config_path()`: `<configDir>/.config.json` when that legacy file exists, else `<CLAUDE_CONFIG_DIR or ~>/.claude.json` (section 2.1) |
| `providers/claude_code/titles.py` `rename_session` (SDK call) | SDK resolves the projects dir itself: `claude_agent_sdk/_internal/session_mutations.py` `rename_session` → `_internal/sessions.py` `_get_projects_dir()`, which reads `os.environ["CLAUDE_CONFIG_DIR"]`; no `options.env` | no code change; correct only because section 3.2 keeps `os.environ` right — pinned by a test (section 11) |
| `providers/claude_code/commands.py` `read_plugin_entries` | `~/.claude/plugins/installed_plugins.json` | `claude_config_dir().path / "plugins" / "installed_plugins.json"` |
| `providers/claude_code/commands.py` `discover_global_commands` | `~/.claude/{commands,skills,workflows}` | `claude_config_dir().path / ...` |

Unchanged on purpose (HOME semantics, not the config dir):
`commands._walk_up_to_home`, project-level `<repo>/.claude/`, `git.py`
`.claude/worktrees`, terminal default cwd.

### 6.2 Codex

| Location | Today | After |
|---|---|---|
| `providers/codex/helpers.py` `SESSIONS_DIR` ClassVar | `Path.home()/.codex/sessions` | removed; `initial_sync.py` (×3) calls `codex_sessions_dir()`; its three docstrings naming `SESSIONS_DIR` / `~/.codex/sessions` are updated |
| `providers/codex/sessions_watcher.py` `projects_dir` ClassVar | copy of `SESSIONS_DIR` | instance attribute set in a new `__init__` (calling `super().__init__()`; the class has none today) from `codex_sessions_dir()` |
| `providers/codex/credentials.py` `CODEX_HOME`, `CREDENTIALS_PATH` constants | `Path.home()/.codex` | removed; functions `credentials_path()` = `codex_home().path / "auth.json"`; the keyring reader passes `codex_home().path` |
| `providers/codex/credentials.py` `_compute_keyring_account(...)` | raw home | unchanged (resolves internally) |
| `providers/codex/trust.py` `_config_path()` | `Path.home()/.codex/config.toml` | `codex_home().path / "config.toml"` |
| `providers/codex/usage_task.py` | imports `CREDENTIALS_PATH` | calls `credentials_path()` |

Already correct once the environment is propagated (they go through the
app-server): trust **write** (`config/batchWrite`), `plugin_install.py`,
`commands_task.py` skill catalogue, `generated_images` (path comes from the
event payload).

`src/openai_codex/client.py` `default_codex_home()` is vendored and unused.
Not touched.

## 7. Write side: the overlay reaches every launched process

`overlay = provider_env_overlay()`. Applied explicitly at each launch point,
even where `os.environ` inheritance would already carry it: explicit beats
implicit for a security invariant, and it survives any future purge widening.

| Launch point | Change |
|---|---|
| SDK agent, `providers/claude_code/agent/agent.py` `env_option` | `env_option.update(overlay)` |
| Throwaway Claude agents: `title_suggest.py`, `auth.py` `_sdk_throwaway_call`, `auth.py` `probe_auth_via_sdk` | `ClaudeAgentOptions(..., env=overlay)` |
| `claude auth status --json`, `providers/claude_code/auth.py` | `create_subprocess_exec(..., env={**os.environ, **overlay})` |
| Every Codex app-server, `providers/codex/bin.py` `_codex_env()` | `env.update(overlay)` — one point covers the agent manager, titles (×3), `title_suggest`, credentials (×2), trust, plugin install, skill catalogue |
| `codex login status`, `providers/codex/auth.py` | `create_subprocess_exec(..., env={**os.environ, **overlay})` |
| Hybrid CLI, `providers/claude_code/agent/hybrid/tmux.py` | `_HYBRID_LAUNCH_ENV` becomes `_hybrid_launch_env()` = static forced vars + overlay; the `exec env ... NAME=VALUE` assignments win over the tmux server's frozen environment |
| Terminal raw shell, `terminal.py` `spawn_pty` | after the purges, `os.environ.update(overlay)` |
| Terminal tmux, `terminal.py` `spawn_tmux_pty` | after the purges, `os.environ.update(overlay)`; **and** `new-session ... -e NAME=VALUE` per overlay entry (tmux ≥ 3.2; 3.6 installed) so a new session gets the values even on a server whose global environment was frozen by an older backend |
| Compute worker (multiprocessing spawn) | inherits `os.environ`; resolver re-reads the `.env` in the worker anyway |
| `twicc claude` / `twicc codex`, `cli/claude.py`, `cli/codex.py` | `.env` loaded by `cli/__init__.py` (section 4); `execvp` inherits `os.environ`; additionally `os.environ.update(overlay)` before exec |
| Cron restarts, MCP server, hybrid hooks | go through the agent manager / the CLI's own env; nothing to add |

### 7.1 Purges never strip the overlay, and re-apply it

Current purges are all narrow and already leave the three variables alone:

- `ClaudeCodeHelpers.purge_env_vars`: prefixes `CLAUDE_CODE`, `CLAUDECODE`;
- `CodexHelpers.purge_env_vars`: `CODEX_SANDBOX`, `CODEX_ESCALATE`, `CODEX_NETWORK_`
  (its comment already names `CODEX_HOME` as a value to preserve);
- `terminal.sanitize_terminal_env`: a named list (`test_terminal_env_sanitation.py`
  already pins that `CLAUDE_CONFIG_DIR` survives);
- `hybrid/tmux.py` `_purged_env_names`: same prefixes plus named markers;
- `devctl.purge_claude_code_vars`: same prefixes;
- `devctl` worktree `TWICC_*` purge: different namespace.

To make the invariant hold "whatever happens":

- `ProviderHelpersRegistry.purge_env_vars(env)` ends with `env.update(overlay)`.
  Every purge through the registry (server boot in `run.py`, both PTY spawners)
  restores the values. Its docstring, which says the CLI calls it "before its
  own `django.setup()`", is corrected while touched: `run.py` calls it after.
- `hybrid/tmux.py` assigns the overlay through the same `NAME=VALUE` list as
  `_HYBRID_LAUNCH_ENV`. The existing "never also `-u` a forced name" guard
  covers them; today `_purged_env_names()` can never yield them anyway.
- A test asserts that each purge function and the terminal sanitizer leave
  every overlay key in place (section 11).

## 8. tmux isolation: one socket per data dir

### 8.1 Problem

Both tmux sockets are per user: `-L twicc` for terminals (`terminal.py`
`TMUX_SOCKET_NAME`) and `-L twicc-hybrid` for hybrid CLIs
(`HYBRID_TMUX_SOCKET_NAME`). Main and every worktree share them. Session names
collide across instances:

| Context | tmux name | Collides because |
|---|---|---|
| global terminal | `twicc-global` | same literal everywhere |
| workspace `w:<id>` | `twicc-w_<id>` | worktree first-setup copies `workspaces.json` |
| project `p:<id>` | `twicc-p_<id>` | ids derive from the path |
| session `s:<id>` | `twicc-<id>` | worktree first-setup copies the DB |
| hybrid | `twicc-hybrid-<id>` | same socket, same ids |

Today the collision is invisible: both instances have the same environment.
With different provider homes, the global terminal of the worktree UI attaches
to main's shell, whose `CODEX_HOME` is main's. A `twicc codex login` typed
there writes into main's home. `new-session -e` cannot fix it: `-A` attaches
to the existing session. Worse, `adopt_running_hybrid_sessions` in
`providers/claude_code/agent/manager.py` lists every `twicc-hybrid-*` on the
shared socket, adopts those present in its DB and **kills** the others (dead
pane, or no matching hybrid `Session` row): a worktree booted with
`--empty-db` kills main's live hybrid CLIs today.

### 8.2 Decision

Socket names derive from the data dir:

```python
def tmux_socket_suffix() -> str:
    data_dir = get_data_dir().resolve()
    if data_dir == DEFAULT_DATA_DIR.resolve():
        return ""                      # ~/.twicc keeps "twicc" / "twicc-hybrid"
    return "-" + sha256(str(data_dir).encode()).hexdigest()[:8]

TMUX_SOCKET_NAME = "twicc" + tmux_socket_suffix()
HYBRID_TMUX_SOCKET_NAME = "twicc-hybrid" + tmux_socket_suffix()
```

Both sides are resolved: `get_data_dir()` returns `DEFAULT_DATA_DIR`
**unresolved** when `TWICC_DATA_DIR` is unset (the production launch), and a
symlinked home (`/home → /var/home`, relocated macOS homes) would otherwise
make the production instance lose its socket name. A test covers a symlinked
default dir.

`DEFAULT_DATA_DIR` and `get_data_dir()` live in `paths.py`; the helper lives
there too. Consumers keep importing the two constants from `terminal.py`
(`tmux_socket_for`, `_tmux_set_global_option`'s default `socket` argument,
`tmux_cleanup_task.py`, `hybrid/tmux.py` `_tmux_base`). The constants stay
module-level values computed at import: the data dir is fixed for the
process, unlike the provider homes.

Effects:

- The main instance keeps every existing terminal and hybrid session (no
  rename for `~/.twicc`).
- Terminals, hybrid CLIs, boot adoption and the reaper are fully isolated per
  instance. `TWICC_NO_TMUX_CLEANUP` is no longer required for worktrees;
  `devctl.py` stops setting it (the reaper now only sees the worktree's own
  sessions). Texts describing a fixed or shared socket are updated:
  `settings.py` (the flag), `tmux_cleanup_task.py` ("Always the main `twicc`
  socket", and the `tmux -L twicc` example), `terminal.py` (socket
  constants), `hybrid/tmux.py` module docstring,
  `providers/claude_code/agent/manager.py` (`adopt_running_hybrid_sessions`
  docstring), `devctl.py` worktree block, `docs/tmux-probe-recipe.md`, and
  the **user-facing** string in
  `frontend/src/components/app/SettingsPopover.vue` ("TwiCC always runs tmux
  on a dedicated socket (`-L twicc`)"), which becomes "a dedicated socket per
  instance".
- `new-session -e` (section 7) remains the belt-and-braces for a server that
  outlived a `.env` change.

Lifecycle of a suffixed server: nothing kills it when a worktree is deleted;
it would outlive the checkout with its terminals and any surviving hybrid
CLI. `devctl.py` gains `kill-tmux`, which runs `tmux -L <name> kill-server` on
both suffixed sockets and refuses on the default data dir. It is **not** part
of `stop` (hybrid CLIs must survive a backend restart by design). The
worktree-deletion rule in `CLAUDE.md` ("you MUST run `stop all`") adds
`kill-tmux`.

Limits, documented in section 13: a tmux session created before a `.env`
change keeps its shell's old environment until it is closed; a tmux server
started before the change keeps its global environment (harmless once every
new session carries `-e`).

## 9. devctl

`devctl.py` never imports `twicc`; it parses the `.env` with `load_env_file()`,
a plain `KEY=VALUE` parser (no `export`, no `${VAR}` interpolation, no inline
comments). To keep devctl and the backend in agreement, the three provider
keys must be written as plain `KEY=VALUE` lines; the README says so, and
devctl scans the **raw lines** of the `.env` and warns when a line for one
of the three keys starts with `export `, contains `${`, or carries an inline
`#` comment (python-dotenv strips it, devctl would keep it in the value).

- `start` / `status` print the provider homes, one line per provider, from the
  `.env` values (default shown when absent), mirroring
  `describe_provider_homes()`.
- Worktree mode: `TWICC_NO_CODEX_PLUGIN=1` is set only when the worktree
  `.env` does **not** define `CODEX_HOME`. With its own home, the worktree
  installs its own copy of the TwiCC plugin there; that is the point.
- Worktree mode: `TWICC_NO_TMUX_CLEANUP` is dropped (section 8.2).
- The three provider keys are purged from `proc_env` (all modes, next to
  `purge_claude_code_vars`), then re-added from the `.env` when defined there.
  Same exclusive semantics as section 3.2 rule 2, one layer earlier, so the
  backend never even sees an inherited value.
- Hint at `start` when a configured home looks **unused**. Codex: none of
  `auth.json`, `config.toml`, `sessions/` exists (Codex creates `tmp/` on its
  first run, so "directory empty" is not a signal; a keyring-mode login
  implies a `config.toml` with `cli_auth_credentials_store`, so the check
  does not misfire there). Claude: none of `.credentials.json`,
  `settings.json`, `projects/` exists, skipped when
  `CLAUDE_SECURESTORAGE_CONFIG_DIR` is set (credentials are elsewhere).
  Messages: `CODEX_HOME=/x looks unused: run "twicc codex login" from this
  instance's terminal`; `CLAUDE_CONFIG_DIR=/x looks unused: log in from a
  Claude session of this instance`. A hint, printed at every `start` until
  one of the files appears; never an error.
- New `kill-tmux` command (section 8.2). `status` prints the two socket
  names. devctl mirrors `tmux_socket_suffix()` standalone, the way it already
  mirrors `get_data_dir()`: same hash input `str(DATA_DIR.resolve())`, same
  default-dir comparison. A test pins the two implementations to the same
  value for a default and a non-default data dir.

No first-setup seeding of a new home. Maintaining a list of files to copy
would track the providers' layouts forever. A new home starts empty; login is
one command; historical data is a manual `cp -r` when a test needs it.

## 10. Startup display and logs

`src/twicc/cli/run.py`, right after `Environment loaded`, logs every line of
`describe_provider_homes()` at INFO. These lines go to the startup console and
to `backend.log`. Example:

```
Environment loaded
Claude Code home: /home/u/dev/wt/claude-home (CLAUDE_CONFIG_DIR from .env)
Claude Code credentials: /home/u/.claude (CLAUDE_SECURESTORAGE_CONFIG_DIR empty)
Codex home: /home/u/dev/wt/codex-home (CODEX_HOME from .env)
```

When nothing is configured:

```
Claude Code home: /home/u/.claude (default)
Codex home: /home/u/.codex (default)
```

The load warnings (dropped inherited keys, `TWICC_DATA_DIR` in the file,
section 3.2) are produced once at `import twicc` and kept by the loader
(`get_env_load_warnings()`, section 4). `cli/__init__.py` `main()` prints
them to stderr before `app()` dispatches, so every subcommand, `twicc claude`
and `twicc codex` included, shows them. `run.py` logs them again at WARNING right before
the lines above, so they land in `backend.log`. A `ProviderHomeConfigError`
is printed to stderr and exits the process in `cli/__init__.py` (section
3.3), so it never reaches the logger.

Boot adoption of a surviving hybrid CLI (`adopt=True`) logs a WARNING when the
adopted process's environment does not match the overlay (read from
`/proc/<pid>/environ` on Linux, `ps -Eww` on macOS; best effort): the CLI
keeps its launch-time home until it exits.

## 11. Tests

`src/twicc/settings_test.py` isolates the homes, in this exact order:

1. `from twicc.settings import *` (today's line 3). By then `import twicc`
   has already run `ensure_env_loaded()` (section 4), imported the provider
   `constants` modules (section 6) and `settings.py` has resolved and cached
   the real homes.
2. Set `CLAUDE_CONFIG_DIR`, `CODEX_HOME` to fresh directories under a
   per-process temporary root and `CLAUDE_SECURESTORAGE_CONFIG_DIR` to a
   third one (never empty: a test must not reach the real keychain entry).
3. `provider_homes.reset_cache()`, then reassign the informational settings
   (`CLAUDE_CONFIG_DIR`, `CLAUDE_SECURE_STORAGE_DIR`, `CODEX_HOME`,
   `PROVIDER_HOMES_DESCRIPTION`) from the resolver.

This works because nothing on the read side is evaluated at import (section
6): every path is computed from the resolver at call time, and the resolver's
cache was just reset. `ensure_env_loaded()` is a no-op by then (once per
process), so it neither overrides nor drops the test values. A test asserts
that every section 6 accessor returns a path under the temporary root. The
three existing tests that patched class attributes switch to the
`provider_home` fixture (section 6).

New tests:

- resolver: unset → default + empty overlay; set → path/raw/source; `""` for
  `CLAUDE_CONFIG_DIR` / `CODEX_HOME` → error; relative or `~` → error;
  `CLAUDE_SECURESTORAGE_CONFIG_DIR=""` → storage dir `~/.claude`, no suffix;
  set → suffix; keychain service string for each row of the section 2.1 table
  (fixed vectors);
- `claude_global_config_path()` for the three cases (default, configured,
  legacy `.config.json` present);
- `ensure_env_loaded()` (through `paths._reset_env_loader()` and temporary
  `.env` files): loads once; a key in the `.env` wins over a pre-set
  variable; a provider key absent from the `.env` is dropped with a warning;
  a bare `KEY` line counts as absent; `KEY=` (empty) counts as defined; a
  non-provider key absent from the `.env` is kept; `TWICC_DATA_DIR` in the
  file is skipped with a warning; `get_env_load_warnings()` returns the
  warnings after the load;
- `ensure_codex_home()` creates the configured dir, never the default;
- `titles.rename_session` path: the SDK's session lookup sees the configured
  `CLAUDE_CONFIG_DIR` through `os.environ` (patch `os.environ`, assert the
  path the SDK builds);
- `settings.py` imports no `twicc.providers.*` module;
- each purge (`ClaudeCodeHelpers`, `CodexHelpers`, registry,
  `sanitize_terminal_env`, `hybrid/tmux._purged_env_names`, `devctl.purge_claude_code_vars`)
  leaves the overlay keys and the registry re-applies them;
- `_codex_env()` contains the overlay;
- hybrid `create_session` command string contains `NAME=VALUE` for each overlay
  entry and no `-u NAME` for them (build the command through a seam, no tmux);
- `spawn_tmux_pty` argv contains `-e NAME=VALUE` per entry (argv builder
  extracted as a pure function);
- socket names: default data dir → `twicc` / `twicc-hybrid`, also when
  `TWICC_DATA_DIR` points at the default dir through a symlink; other →
  suffixed, stable across calls;
- `devctl.purge` keeps no inherited provider key and re-adds the `.env` ones;
  `kill-tmux` refuses on the default data dir;
- `cli/__init__.py` loads the `.env` before dispatching (`twicc claude` seam:
  assert `os.environ` at exec time).

## 12. Documentation updates

- `README.md` Configuration table: the three keys, one line each, with the
  precedence sentence of section 3.2 and a FAQ entry "Can I point TwiCC at
  another Claude Code / Codex home?".
- `CLAUDE.md` and `AGENTS.md` (mirror): Data Directory section lists the three
  `.env` keys and the rule "the `.env` wins over the environment"; the
  worktree subsection notes that a worktree `.env` may define its own homes
  and that tmux sockets are per data dir.
- `SKILLS-AND-CLI.md`, `twicc claude` / `twicc codex` line: they honour the
  instance's `.env` (provider homes included).
- `CLAUDE.md` / `AGENTS.md` devctl section: `kill-tmux` in the command list
  and in the worktree-deletion rule.
- Comments: `settings.py` (`TWICC_NO_CODEX_PLUGIN`, `TWICC_NO_TMUX_CLEANUP`),
  `tmux_cleanup_task.py`, `terminal.py`, `hybrid/tmux.py`, `devctl.py`
  (section 8.2); docstrings naming `~/.claude.json` / `~/.codex/config.toml`
  (`src/twicc/trust.py`) and the `Claude Code-credentials` keychain item
  (`providers/claude_code/usage_task.py`).
- Every remaining comment or docstring that spells `~/.claude/…` or
  `~/.codex/…` as the location gets a "(or the configured home)" qualifier or
  a reference to `provider_homes`. Known list (from `grep -rn '~/\.claude\|~/\.codex\|\.claude/projects\|\.codex/sessions'`):
  `core/models.py`, `core/serializers.py`, `views.py`, `paths.py`,
  `settings.py`, `agent/plugin/__init__.py`, `providers/plan_docs.py`,
  `providers/compute_base.py`, `providers/sessions_watcher.py`,
  `providers/claude_code/{sessions_watcher,workflow_meta,plans_watcher,helpers,compute,auth,commands}.py`
  (`initial_sync.py` is covered by section 6.1),
  `providers/claude_code/agent/{permissions,agent}.py`,
  `providers/claude_code/agent/hybrid/agent.py`,
  `providers/claude_code/trust.py`,
  `providers/codex/{sessions_watcher,usage,usage_task,plugin_install,compute,initial_sync,trust,credentials}.py`,
  `frontend/src/views/SessionView.vue`,
  `frontend/src/components/session/detail/items/codex/ImageGeneration.vue`.
  Migrations are frozen history and are never edited
  (`core/migrations/0085_session_file_path_nullable.py` keeps its docstring).
- `README.md` also states that sessions are read from `~/.claude/projects/`
  and `~/.codex/sessions/` (Requirements and "How it works" paragraphs): add
  "by default" and point to the Configuration table.
- CHANGELOG: only on explicit request.

## 13. Behaviour notes

### 13.1 Switching a home on an existing data dir

`Session.file_path` is relative to the provider data root. Pointing the root
elsewhere makes every known file disappear; the initial sync marks those
sessions stale. Expected. A worktree started with `--empty-db` avoids the
noise.

### 13.2 A new home is empty

No sessions, no `settings.json`, no global `CLAUDE.md`, no skills, no
`config.toml`. Claude creates its directory on first use; TwiCC creates
Codex's, which Codex then fills with `tmp/` helpers on its first run
(section 3.5). Agents still run. Anything needed is copied by hand.

### 13.3 Authentication

- Claude: set `CLAUDE_SECURESTORAGE_CONFIG_DIR=` (empty) next to
  `CLAUDE_CONFIG_DIR` to keep the real credentials and the single token
  refresh path. Otherwise log in once inside the new home.
- Codex: `codex login` once, from a terminal of the instance (it carries the
  right `CODEX_HOME`). Copying `auth.json` also works; whether two homes
  refreshing the same refresh token invalidate each other is unknown, so login
  is the documented path.

### 13.4 Shell profile

The terminal spawns a login shell, which re-sources the user's profile. A
profile that exports one of the three variables overrides the instance value
in that shell. User-owned; documented, not fought.

### 13.5 Long-lived processes

- A hybrid CLI adopted at boot keeps its launch-time home (section 10 warns).
- A tmux session created before a `.env` change keeps its old environment
  until closed.

### 13.6 Plugins

- Claude caches the per-session local plugin under
  `$CLAUDE_CONFIG_DIR/plugins/cache/...`: with separate homes, main and a
  worktree no longer compete for the same cache entry at the same version.
- Codex installs the plugin into `$CODEX_HOME` (section 9).

## 14. Non-goals

- No UI surface: the homes are infrastructure config, like the port.
- No seeding, no copy, no symlink of provider files.
- No expansion of `~` or relative paths (section 3.3).
- No renaming of existing tmux sessions on the default data dir.
- Other Claude variables (`CLAUDE_CODE_OAUTH_CLIENT_ID`, custom OAuth
  suffixes) are out of scope. The keychain service name **and** the global
  config filename (`.claude${suffix}.json`) assume production builds
  (`OAUTH_FILE_SUFFIX == ""`); `-local-oauth` / `-staging-oauth` /
  `-custom-oauth` builds are not supported. A `CLAUDE_CODE_CUSTOM_OAUTH_URL`
  exported by a shell profile (section 13.4) switches a production CLI to the
  `-custom-oauth` suffix; TwiCC's own launches never carry it (the
  `CLAUDE_CODE*` purge drops it), so TwiCC's credential reads and such a
  terminal-launched CLI would disagree. Out of scope.

## 15. Verification in a worktree

1. Create the worktree; write its `.env`:
   ```
   CLAUDE_CONFIG_DIR=<worktree>/provider-homes/claude
   CLAUDE_SECURESTORAGE_CONFIG_DIR=
   CODEX_HOME=<worktree>/provider-homes/codex
   ```
2. `uv run ./devctl.py start --empty-db`; check the three lines in the start
   output and in `backend.log`.
3. Record `find ~/.claude ~/.codex -newer <marker>` baselines; nothing under
   them may change during the tests below (except `~/.claude/.credentials.json`
   refreshes, which is the point of 13.3).
4. From the worktree UI: open the global terminal; `tmux -L twicc-<hash> ls`
   shows it, `tmux -L twicc ls` does not; `echo $CODEX_HOME` prints the
   worktree home; `twicc codex login`.
5. Start a Claude SDK session and a Codex session: JSONL files appear under
   the worktree homes; `cat /proc/<pid>/environ | tr '\0' '\n' | grep -E 'CLAUDE_CONFIG_DIR|CODEX_HOME'`
   on both agent processes shows the values.
6. Accept the trust dialog on a project: `<claude home>/.claude.json` and
   `<codex home>/config.toml` change; the real files do not.
7. Start a hybrid Claude session; check the pane's `claude` process
   environment the same way; restart the backend; the adopted session logs no
   mismatch warning.
8. From the worktree, `cd <worktree> && TWICC_DATA_DIR=$PWD uv run twicc
   claude auth status --json` and `... twicc codex login status`: the Claude
   run creates `<claude home>/.claude.json` if absent, the Codex run succeeds
   against `<codex home>/auth.json` (created by step 4); the real homes are
   untouched. Repeat with `CLAUDE_CONFIG_DIR=/elsewhere` exported in the
   shell: the command prints the "Ignoring inherited" warning and still uses
   the `.env` value.
9. Title suggestion and "Check again" (auth probe) for both providers: no
   file created under the real homes.
10. `uv run pytest` in the worktree: green, and no test touches the real homes
    (`settings_test` isolation).
