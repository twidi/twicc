# Hermetic LLM calls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the six non-session LLM calls (Claude/Codex title suggestion, Claude/Codex auth probe, Claude/Codex throwaway refresh) through one per-provider restricted "hermetic" factory, with a runtime guard, and add a manual non-regression diagnostic script.

**Architecture:** A shared `providers/hermetic.py` (errors, neutral cwd), a Claude module (options builder, guard, runner) and a Codex module pair (catalogue generation/validation/cache, then plan/thread/guard/runner). The six call sites keep their prompts, timeouts and retry loops and only swap client construction and the single query. `scripts/diagnose_hermetic_llm.py` exercises the real factories with positive controls; it is outside the pytest tree.

**Tech Stack:** Python ≥ 3.13, claude-agent-sdk 0.2.163, vendored `openai_codex` SDK + Codex CLI 0.160.0, pytest (+ pytest-django), `uvx ruff` for lint.

**Spec:** `docs/plans/2026-10-03-hermetic-llm-calls-design.md` (revision 6, reviewed PASS). Section numbers below (§) refer to it. The spec wins on any conflict; fix the plan, not the spec, unless the spec is wrong (then stop and ask).

## Global Constraints

- Work on the current branch. Never create a branch or worktree. Never `git add .` or `git add <directory>`: the checkout holds another agent's uncommitted changes (orchestration UI, session visibility, `title_cadence.py` …). Stage the files listed in each task, nothing else. If a listed file already has foreign hunks (`git diff <file>` before staging), stage only your hunks with `git apply --cached`.
- Commit template (every "Commit" step uses it; the step gives the subject): `git commit -m "<type>(hermetic): <summary>" -m "<body of 2–4 sentences>" -m "Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>"`.
- Commit only at the step that says "Commit", one commit per task: Conventional Commit subject (lowercase summary), a body of 2–4 English sentences saying what changed and why (not only a subject line), and the trailer `Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>` with the exact model name you run as. **Do not touch `CHANGELOG.md`** (propose a line at the end, wait for the user).
- All text in English (code, comments, docs, log lines). Backend JSON uses `orjson`. `NamedTuple` for immutable results. No aliased imports unless forced.
- Python lint: `uvx ruff check <files>` (line length 120). Never `uv pip`, never `--active`, never `uv add`. Tests: `uv run pytest <path> -q` from `/home/twidi/dev/twicc-poc`.
- Never restart servers, never run `migrate`/`npm`. No Django model change, no migration in this plan.
- Fail closed: no code path, setting or env var under `src/` may re-enable the unrestricted configuration (§5.6).
- Timeouts/retries/prompts/models of the six call sites do not change (§3): title Claude 60 s, Codex 15 s, 2 attempts; probes and refreshes 30 s, one attempt. Claude alias `haiku`; Codex `TITLE_MODEL` / `_REFRESH_MODEL` (`gpt-6-luna`).
- Provider homes are never changed or copied. No isolated `CODEX_HOME` (§5.4). Authentication stays the normal OAuth/file login (`provider_env_overlay()` is passed as today).
- Model calls: unit tests make none. Only the manual diagnostic (Task 10–11) and the one-time manual probes of Task 12 call providers; run them only when the plan says so, and tell the user the cost first.
- Neutral directory name must not contain "twicc" (§5.2). Temp files in tests use `tmp_path`.

## Review Focus

Failure modes the spec implies but no single task's happy path exercises (each is pinned by a named test in the owning task):

1. **A model that tries a tool under `max_turns=1`** ends with an error result; the text before the attempt must not become a title (Task 2 `test_result_with_tool_block_is_a_violation_even_when_error`).
2. **An auth-failed Claude result must still reach the probe** as a negative, not as a guard violation (Task 2 `test_error_result_is_returned_untouched`, Task 7 `test_probe_returns_false_on_authentication_failed`).
3. **A Codex catalogue override silently ignored** (`model/list` returns the full model list or a next-page cursor) must fail the call (Task 5 `test_model_list_with_several_models_is_a_violation`, `..._with_cursor...`).
4. **A nested `features`/`tools` table at thread level** would silently restore every tool (68 514 tokens); the builder must emit dotted keys only (Task 5 `test_thread_config_uses_dotted_keys_for_features_and_tools`).
5. **An MCP server name with a dot or hyphen** must reach the thread config as a literal nested-table key (Task 5 `test_thread_config_keeps_mcp_names_literal`).
6. **A stray file in the neutral directory / a squatted or symlinked directory** must fail the call with a message naming the fix, never be purged (Task 1).
7. **Concurrent catalogue generation** (title call and probe in different threads/event loops) must not corrupt the cache file (Task 4 `test_concurrent_generation_is_atomic`).
8. **Refusing handler runs in the SDK reader thread**; the async consumer must see the flag after the stream ends even if no further event arrives (Task 6 `test_run_turn_checks_the_flag_after_the_stream_ends`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/twicc/providers/hermetic.py` (new) | `HermeticConfigError`, `HermeticGuardViolation`, `hermetic_cwd` |
| `src/twicc/providers/claude_code/hermetic.py` (new) | Claude options builder, `check_claude_init`, `check_claude_result`, `HermeticClaudeResult`, `run_hermetic_claude`, `_run_hermetic_claude_for_diagnostic` |
| `src/twicc/providers/codex/hermetic_catalog.py` (new) | Catalogue transformation, validation, bundled-source memo, cache |
| `src/twicc/providers/codex/hermetic.py` (new) | Process overrides, thread config, Codex guards, refusing handler, `HermeticCodexPlan`, `prepare_hermetic_codex`, `hermetic_codex`, `HermeticCodexThread`, `run_hermetic_codex` |
| `src/twicc/providers/codex/sdk_wrappers.py` (modify `thread_start_with_policy`) | Keep the `ThreadStartResponse` on the returned thread |
| `src/twicc/providers/claude_code/title_suggest.py`, `auth.py` (modify) | Sites 1, 3, 4 |
| `src/twicc/providers/codex/title_suggest.py`, `credentials.py` (modify) | Sites 2, 5, 6; site-5 exception classifier |
| `scripts/diagnose_hermetic_llm.py` (new) | Manual diagnostic (offline O1–O9, live D1–D14) |
| `tests/test_hermetic_*.py` (new) | Unit tests per module |
| `tests/test_title_output_validation.py`, `tests/test_title_suggestion_client_failures.py` (modify) | Move the fake seam to the hermetic helpers |
| `CLAUDE.md`, `AGENTS.md` (modify) | Maintenance note + `cache/` line in "Data Directory" |
| `docs/plans/2026-10-03-hermetic-llm-calls-design.md` (modify §12) | Record the manual measurements of Task 12 |

Task order matters: 1 → 2 → 3 (Claude), 4 → 5 → 6 (Codex), 7, 8 (call sites), 9 → 10 → 11 (diagnostic), 12 (docs + manual measurements).

---

### Task 1: Shared errors and the neutral working directory

**Files:**
- Create: `src/twicc/providers/hermetic.py`
- Test: `tests/test_hermetic_cwd.py`

**Interfaces:**
- Produces:
  - `class HermeticConfigError(Exception)`: `__init__(self, reason: str, message: str)`; attribute `reason` ∈ {`catalog`, `start`, `cwd`, `mcp-config`}; `str(exc)` is the message.
  - `class HermeticGuardViolation(Exception)`: `__init__(self, reason: str)`; attribute `reason`.
  - `hermetic_cwd(base: Path | None = None) -> Path`.

- [ ] **Step 1: Write the failing tests** (`tests/test_hermetic_cwd.py`)

```python
import os
import stat
import sys

import pytest

from twicc.providers.hermetic import HermeticConfigError, HermeticGuardViolation, hermetic_cwd

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership checks")


@pytest.fixture
def hermetic_logs():
    """Collect the warning lines of ``twicc.providers.hermetic``.

    Owns the logger instead of using ``caplog``: the test settings disable existing loggers, which makes
    ``caplog`` depend on the import order of the suite (same approach as the ``rejections`` fixture of
    ``tests/test_title_output_validation.py``). Copy this fixture into every test module that counts log lines.
    """
    import logging

    logger = logging.getLogger("twicc.providers.hermetic")
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler, was_disabled, level = _Collect(), logger.disabled, logger.level
    logger.disabled = False
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.disabled = was_disabled


def test_errors_carry_a_reason_and_log_one_warning(hermetic_logs):
    err = HermeticConfigError("cwd", "boom")
    violation = HermeticGuardViolation("tools not empty")
    assert err.reason == "cwd" and str(err) == "boom"
    assert violation.reason == "tools not empty"
    assert hermetic_logs == ["hermetic call failed: cwd (boom)", "hermetic call failed: guard (tools not empty)"]


@posix_only
def test_creates_the_directory_with_mode_0700(tmp_path):
    path = hermetic_cwd(base=tmp_path)
    assert path == (tmp_path / f"hermetic-llm-{os.getuid()}").resolve()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


@posix_only
def test_second_call_is_a_no_op(tmp_path):
    assert hermetic_cwd(base=tmp_path) == hermetic_cwd(base=tmp_path)


@posix_only
def test_non_empty_directory_is_refused_and_names_the_entry(tmp_path):
    path = hermetic_cwd(base=tmp_path)
    (path / "AGENTS.md").write_text("stray")
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=tmp_path)
    assert info.value.reason == "cwd"
    assert "AGENTS.md" in str(info.value) and "remove" in str(info.value).lower()
    assert (path / "AGENTS.md").exists()  # never purged


@posix_only
def test_wrong_mode_is_refused(tmp_path):
    path = hermetic_cwd(base=tmp_path)
    path.chmod(0o755)
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=tmp_path)
    assert info.value.reason == "cwd" and "0700" in str(info.value)


@posix_only
def test_symlink_is_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / f"hermetic-llm-{os.getuid()}").symlink_to(target)
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=tmp_path)
    assert info.value.reason == "cwd" and "symlink" in str(info.value)


@posix_only
def test_foreign_owner_is_refused(tmp_path, monkeypatch):
    path = hermetic_cwd(base=tmp_path)
    real_getuid = os.getuid
    monkeypatch.setattr(os, "getuid", lambda: real_getuid() + 1)  # the directory now looks foreign
    # Name is built from the uid, so recreate the expectation with the real name:
    monkeypatch.setattr("twicc.providers.hermetic._dir_name", lambda: path.name)
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=tmp_path)
    assert info.value.reason == "cwd" and "TMPDIR" in str(info.value)


def test_path_containing_the_product_name_is_refused(tmp_path):
    base = tmp_path / "TwiCC-tmp"
    base.mkdir()
    with pytest.raises(HermeticConfigError) as info:
        hermetic_cwd(base=base)
    assert info.value.reason == "cwd" and "twicc" in str(info.value).lower()


def test_default_base_is_the_system_temporary_directory(monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    assert hermetic_cwd().parent == tmp_path.resolve()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_hermetic_cwd.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.providers.hermetic`).

- [ ] **Step 3: Implement** (`src/twicc/providers/hermetic.py`)

```python
"""
Shared pieces of the hermetic LLM calls (see docs/plans/2026-10-03-hermetic-llm-calls-design.md).

A hermetic call is a short, non-session model call: one prompt in, one text out,
no tool, no user interaction. This module holds what both providers share: the
two error types and the neutral working directory.
"""
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class HermeticConfigError(Exception):
    """The hermetic configuration cannot be built or started.

    ``reason`` is a stable code for the logs: ``catalog``, ``start``, ``cwd``
    or ``mcp-config``. Creating the error logs the one ``warning`` line of the
    failure (spec §5.6/§7); the prompt is never logged.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        logger.warning("hermetic call failed: %s (%s)", reason, message)


class HermeticGuardViolation(Exception):
    """The provider reported a state, or produced an item, that is not allowed.

    Creating the violation logs the one ``warning`` line of the failure, with the stable code ``guard``.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        logger.warning("hermetic call failed: guard (%s)", reason)


_logged_directories: set[str] = set()


def _dir_name() -> str:
    return f"hermetic-llm-{os.getuid()}"


def hermetic_cwd(base: Path | None = None) -> Path:
    """Return the shared neutral working directory, creating it when missing.

    Both providers disclose the working directory path to the model and read
    instruction files from it and from its parents, so the directory must be
    empty and its path must carry no information (no product name). It is never
    purged: a stray file there is a signal, not litter.

    ``base`` is the parent directory (default: the system temporary directory);
    it exists for tests and for the diagnostic.
    """
    if sys.platform == "win32":
        raise HermeticConfigError("cwd", "Hermetic calls need a POSIX system (ownership and mode checks)")
    parent = Path(base) if base is not None else Path(tempfile.gettempdir())
    path = parent / _dir_name()
    if "twicc" in str(path).lower() or "twicc" in os.path.realpath(parent).lower():
        raise HermeticConfigError(
            "cwd",
            f"The neutral directory path {path} contains 'twicc'; the path is shown to the model. "
            "Set TMPDIR to a directory whose path does not contain it.",
        )
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = os.lstat(path)
    except OSError as exc:
        raise HermeticConfigError("cwd", f"Cannot create or inspect {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise HermeticConfigError("cwd", f"{path} is a symlink; remove it.")
    if not stat.S_ISDIR(info.st_mode):
        raise HermeticConfigError("cwd", f"{path} is not a directory; remove it.")
    if info.st_uid != os.getuid():
        raise HermeticConfigError(
            "cwd", f"{path} is owned by another user; set TMPDIR to a private directory.",
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise HermeticConfigError("cwd", f"{path} must have mode 0700 (found {stat.S_IMODE(info.st_mode):04o}).")
    try:
        entries = sorted(entry.name for entry in path.iterdir())
    except OSError as exc:
        raise HermeticConfigError("cwd", f"Cannot list {path}: {exc}") from exc
    if entries:
        raise HermeticConfigError(
            "cwd", f"{path} must be empty but contains {entries[0]!r}; remove it (the directory is never purged).",
        )
    resolved = Path(os.path.realpath(path))
    if str(resolved) not in _logged_directories:
        _logged_directories.add(str(resolved))
        logger.info("Hermetic calls use the neutral directory %s", resolved)
    return resolved
```

Note: `test_path_containing_the_product_name_is_refused` passes because the check runs before `mkdir`; `test_foreign_owner_is_refused` passes because `os.getuid` is patched after creation.

- [ ] **Step 4: Run tests and lint**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_hermetic_cwd.py -q && uvx ruff check src/twicc/providers/hermetic.py tests/test_hermetic_cwd.py`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/providers/hermetic.py tests/test_hermetic_cwd.py && git commit -m "feat(hermetic): add the shared errors and the neutral working directory" -m "The neutral directory is empty, private to the user and never purged, and its path carries no product name. The two error types log the single warning line of every hermetic failure."
```

---

### Task 2: Claude options builder and guards (pure, no process)

**Files:**
- Create: `src/twicc/providers/claude_code/hermetic.py` (this task writes everything except the runners)
- Test: `tests/test_hermetic_claude_guard.py`

**Interfaces:**
- Consumes: Task 1 (`HermeticGuardViolation`, `hermetic_cwd`).
- Produces:
  - `FAMILY_PREFIXES: dict[str, str] = {"haiku": "claude-haiku-"}`
  - `class HermeticClaudeResult(NamedTuple)`: `text: str`, `assistant_error: str | None`, `is_error: bool`, `usage: dict`, `init: dict`, `num_turns: int | None`, `tool_blocks_seen: int`, `permission_callback_calls: int`, `violation: str | None = None`
  - `hermetic_client_options(*, model: str, effort: str = "low") -> ClaudeAgentOptions`
  - `_build_options(*, model: str, effort: str, cwd: Path, permission_calls: list[str]) -> ClaudeAgentOptions` (internal; the deny callback appends tool names to `permission_calls`)
  - `check_claude_init(init: dict, *, cwd: Path, alias: str) -> None` (raises `HermeticGuardViolation`)
  - `check_claude_result(result: HermeticClaudeResult) -> None`

- [ ] **Step 1: Write the failing tests** (`tests/test_hermetic_claude_guard.py`)

```python
from pathlib import Path

import pytest
from asgiref.sync import async_to_sync

from twicc.providers.claude_code.hermetic import (
    HermeticClaudeResult,
    _build_options,
    check_claude_init,
    check_claude_result,
    hermetic_client_options,
)
from twicc.providers.hermetic import HermeticGuardViolation

CWD = Path("/tmp/hermetic-llm-1000")


def good_init(**over):
    init = {
        "tools": [], "mcp_servers": [], "slash_commands": [], "skills": [],
        "permissionMode": "dontAsk", "cwd": str(CWD), "model": "claude-haiku-4-5-20251001",
        "plugins": [{"name": "cc-plugin-agents-md"}], "agents": ["general-purpose"],
    }
    init.update(over)
    return init


def result(**over):
    base = {"text": "OK", "assistant_error": None, "is_error": False, "usage": {}, "init": good_init(),
            "num_turns": 1, "tool_blocks_seen": 0, "permission_callback_calls": 0}
    base.update(over)
    return HermeticClaudeResult(**base)


def test_good_init_passes():
    check_claude_init(good_init(), cwd=CWD, alias="haiku")


@pytest.mark.parametrize("field,value", [
    ("tools", ["Bash"]), ("mcp_servers", [{"name": "x"}]), ("slash_commands", ["compact"]),
    ("skills", ["pdf"]), ("permissionMode", "default"), ("cwd", "/home/me/project"),
    ("model", "claude-opus-5-5"),
])
def test_bad_init_field_is_a_violation(field, value):
    with pytest.raises(HermeticGuardViolation) as info:
        check_claude_init(good_init(**{field: value}), cwd=CWD, alias="haiku")
    assert field.split("_")[0].lower()[:4] in info.value.reason.lower()


def test_missing_init_field_is_a_violation():
    init = good_init()
    del init["tools"]
    with pytest.raises(HermeticGuardViolation):
        check_claude_init(init, cwd=CWD, alias="haiku")


def test_plugins_and_agents_are_not_checked():
    check_claude_init(good_init(plugins=[{"name": "a"}, {"name": "b"}], agents=["x", "y"]), cwd=CWD, alias="haiku")


def test_success_result_passes():
    check_claude_result(result())


def test_error_result_is_returned_untouched():
    check_claude_result(result(is_error=True, assistant_error="authentication_failed", num_turns=0))


def test_tool_activity_without_any_result_is_still_a_violation():
    with pytest.raises(HermeticGuardViolation):
        check_claude_result(result(num_turns=None, tool_blocks_seen=1))


def test_stream_without_result_and_without_tools_passes_the_guard():
    check_claude_result(result(num_turns=None))   # the call sites treat the empty reply as a failure


def test_success_result_with_two_turns_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        check_claude_result(result(num_turns=2))


def test_result_with_tool_block_is_a_violation_even_when_error():
    with pytest.raises(HermeticGuardViolation):
        check_claude_result(result(is_error=True, tool_blocks_seen=1))


def test_permission_callback_call_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        check_claude_result(result(permission_callback_calls=1))


def test_options_builder_sets_every_field(monkeypatch, tmp_path):
    monkeypatch.setattr("twicc.providers.claude_code.hermetic.hermetic_cwd", lambda base=None: tmp_path)
    monkeypatch.setattr("twicc.provider_homes.provider_env_overlay", lambda: {"CLAUDE_CONFIG_DIR": "/c"})
    o = hermetic_client_options(model="haiku")
    assert o.model == "haiku" and o.effort == "low"
    assert o.tools == [] and o.allowed_tools == [] and o.setting_sources == []
    assert o.strict_mcp_config is True and o.permission_mode == "dontAsk" and o.max_turns == 1
    assert o.cwd == str(tmp_path) and o.system_prompt is None
    assert o.extra_args == {"no-session-persistence": None, "disable-slash-commands": None}
    assert o.env == {"CLAUDE_CONFIG_DIR": "/c", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    assert o.can_use_tool is not None


def test_deny_callback_records_and_interrupts(tmp_path):
    calls: list[str] = []
    o = _build_options(model="haiku", effort="low", cwd=tmp_path, permission_calls=calls)
    decision = async_to_sync(o.can_use_tool)("Bash", {"command": "ls"}, None)
    assert calls == ["Bash"]
    assert decision.interrupt is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_hermetic_claude_guard.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the pure part** (`src/twicc/providers/claude_code/hermetic.py`)

```python
"""
Hermetic Claude calls: zero tools, no settings/MCP/skills/memory, one turn.

See docs/plans/2026-10-03-hermetic-llm-calls-design.md §5.3 and §5.5.
"""
import logging
from pathlib import Path
from typing import NamedTuple

from claude_agent_sdk import ClaudeAgentOptions, PermissionResultDeny

from twicc.providers.hermetic import HermeticGuardViolation, hermetic_cwd

logger = logging.getLogger(__name__)

# Requested alias -> prefix of the model name the CLI reports in ``init``.
FAMILY_PREFIXES: dict[str, str] = {"haiku": "claude-haiku-"}

_TOOL_BLOCK_NAMES = frozenset({"ToolUseBlock", "ToolResultBlock", "ServerToolUseBlock", "ServerToolResultBlock"})


class HermeticClaudeResult(NamedTuple):
    """What a hermetic Claude call produced, plus what the guard needs."""
    text: str
    assistant_error: str | None
    is_error: bool
    usage: dict
    init: dict
    num_turns: int | None
    tool_blocks_seen: int
    permission_callback_calls: int
    violation: str | None = None   # always None on the public path; set by the diagnostic seam


def _build_options(*, model: str, effort: str, cwd: Path, permission_calls: list[str]) -> ClaudeAgentOptions:
    from twicc.provider_homes import provider_env_overlay

    async def deny_all(tool_name, tool_input, context):
        permission_calls.append(tool_name)
        return PermissionResultDeny(message="No tool may run during a hermetic call.", interrupt=True)

    return ClaudeAgentOptions(
        model=model,
        effort=effort,
        permission_mode="dontAsk",
        tools=[],
        allowed_tools=[],
        setting_sources=[],
        strict_mcp_config=True,
        max_turns=1,
        can_use_tool=deny_all,
        cwd=str(cwd),
        system_prompt=None,
        extra_args={"no-session-persistence": None, "disable-slash-commands": None},
        # Configured provider homes, explicit (see the SDK agent's env_option), plus no automatic memory.
        env={**provider_env_overlay(), "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
    )


def hermetic_client_options(*, model: str, effort: str = "low") -> ClaudeAgentOptions:
    """The options of every hermetic Claude call (§5.3). All of them are required together."""
    return _build_options(model=model, effort=effort, cwd=hermetic_cwd(), permission_calls=[])


def check_claude_init(init: dict, *, cwd: Path, alias: str) -> None:
    """Fail unless the ``init`` system message reports the intended restricted state (§5.3 / §5.5)."""
    for key in ("tools", "mcp_servers", "slash_commands", "skills", "permissionMode", "cwd", "model"):
        if init.get(key) is None:
            raise HermeticGuardViolation(f"init message has no {key!r}")
    for key in ("tools", "mcp_servers", "slash_commands", "skills"):
        if init[key] != []:
            raise HermeticGuardViolation(f"init reports non-empty {key}: {init[key]!r}")
    if init["permissionMode"] != "dontAsk":
        raise HermeticGuardViolation(f"init reports permissionMode {init['permissionMode']!r}")
    if Path(init["cwd"]).resolve() != Path(cwd).resolve():
        raise HermeticGuardViolation(f"init reports cwd {init['cwd']!r}")
    prefix = FAMILY_PREFIXES.get(alias)
    if prefix is None or not str(init["model"]).startswith(prefix):
        raise HermeticGuardViolation(f"init reports model {init['model']!r} for alias {alias!r}")


def check_claude_result(result: HermeticClaudeResult) -> None:
    """Fail on any tool activity; let error results through so auth probes keep their signal (§5.5)."""
    if result.tool_blocks_seen or result.permission_callback_calls:
        raise HermeticGuardViolation(
            f"tool activity: {result.tool_blocks_seen} tool block(s), "
            f"{result.permission_callback_calls} permission request(s)"
        )
    if result.is_error or result.assistant_error or result.num_turns is None:
        return   # an error result keeps its signal; no ResultMessage means there is no turn count to judge
    if result.num_turns != 1:
        raise HermeticGuardViolation(f"expected one turn, got {result.num_turns!r}")
```

- [ ] **Step 4: Run tests and lint** — `uv run pytest tests/test_hermetic_claude_guard.py -q && uvx ruff check src/twicc/providers/claude_code/hermetic.py tests/test_hermetic_claude_guard.py` → PASS.

- [ ] **Step 5: Commit** — `git add src/twicc/providers/claude_code/hermetic.py tests/test_hermetic_claude_guard.py && git commit` (template of Global Constraints) (subject: `feat(hermetic): add the Claude options builder and runtime guard`).

---

### Task 3: Claude runners (`run_hermetic_claude` and the diagnostic seam)

**Files:**
- Modify: `src/twicc/providers/claude_code/hermetic.py` (append)
- Test: `tests/test_hermetic_claude_run.py`

**Interfaces:**
- Consumes: Task 2 (`_build_options`, `check_claude_init`, `check_claude_result`, `HermeticClaudeResult`).
- Produces:
  - `async run_hermetic_claude(prompt: str, *, model: str) -> HermeticClaudeResult` (raises `HermeticGuardViolation`)
  - `async _run_hermetic_claude_for_diagnostic(prompt: str, *, model: str, cwd: Path | None = None, options_override: Callable[[ClaudeAgentOptions], ClaudeAgentOptions] | None = None) -> HermeticClaudeResult` (returns `violation` instead of raising; with `options_override`, the guard is skipped and `violation=None`)
  - module-level `_client_factory = ClaudeSDKClient` (the tests replace it)

- [ ] **Step 1: Write the failing tests** (`tests/test_hermetic_claude_run.py`). The fake client plays a scripted message list:

```python
import pytest
from asgiref.sync import async_to_sync
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolUseBlock

from twicc.providers.claude_code import hermetic as mod
from twicc.providers.hermetic import HermeticGuardViolation

INIT = {"tools": [], "mcp_servers": [], "slash_commands": [], "skills": [], "permissionMode": "dontAsk",
        "cwd": "", "model": "claude-haiku-4-5-20251001"}


def init_message(cwd, **over):
    return SystemMessage(subtype="init", data={**INIT, "cwd": str(cwd), **over})


def assistant(text=None, error=None, blocks=()):
    content = ([TextBlock(text=text)] if text else []) + list(blocks)
    return AssistantMessage(content=content, model="claude-haiku-4-5", error=error)


def result_message(**over):
    base = {"subtype": "success", "duration_ms": 1, "duration_api_ms": 1, "is_error": False, "num_turns": 1,
            "session_id": "s", "usage": {"input_tokens": 10}}
    base.update(over)
    return ResultMessage(**base)


class FakeClient:
    script: list = []
    instances: list = []

    def __init__(self, options=None):
        self.options = options
        self.interrupted = False
        self.disconnected = False
        type(self).instances.append(self)

    async def connect(self): pass
    async def query(self, prompt): self.prompt = prompt
    async def interrupt(self): self.interrupted = True
    async def disconnect(self): self.disconnected = True

    async def receive_messages(self):
        for m in type(self).script:
            yield m(self.options.cwd) if callable(m) else m


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeClient.instances = []
    monkeypatch.setattr(mod, "_client_factory", FakeClient)
    monkeypatch.setattr(mod, "hermetic_cwd", lambda base=None: tmp_path)
    monkeypatch.setattr("twicc.provider_homes.provider_env_overlay", dict)
    return FakeClient


def run(**kw):
    return async_to_sync(mod.run_hermetic_claude)("hello", model="haiku", **kw)


@pytest.fixture
def hermetic_logs():
    import logging

    logger = logging.getLogger("twicc.providers.hermetic")
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler, was_disabled, level = _Collect(), logger.disabled, logger.level
    logger.disabled = False
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.disabled = was_disabled


def test_happy_path_collects_text_usage_and_init(fake):
    fake.script = [init_message, assistant("OK"), result_message()]
    r = run()
    assert r.text == "OK" and r.num_turns == 1 and r.usage == {"input_tokens": 10}
    assert r.init["tools"] == [] and r.violation is None
    assert fake.instances[0].disconnected


def test_a_violation_logs_exactly_one_warning(fake, hermetic_logs):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), result_message()]
    with pytest.raises(HermeticGuardViolation):
        run()
    assert [m for m in hermetic_logs if m.startswith("hermetic call failed: guard")] == [
        "hermetic call failed: guard (init reports non-empty tools: ['Bash'])"]
    hermetic_logs.clear()
    async_to_sync(mod._run_hermetic_claude_for_diagnostic)("hello", model="haiku")
    assert len(hermetic_logs) == 1


def test_tool_block_without_a_result_message_is_a_violation(fake):
    fake.script = [init_message, assistant("t", blocks=[ToolUseBlock(id="1", name="Bash", input={})])]
    with pytest.raises(HermeticGuardViolation):
        run()


def test_bad_init_raises_and_interrupts(fake):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), assistant("x"), result_message()]
    with pytest.raises(HermeticGuardViolation):
        run()
    assert fake.instances[0].interrupted and fake.instances[0].disconnected


def test_missing_init_is_a_violation(fake):
    fake.script = [assistant("OK"), result_message()]
    with pytest.raises(HermeticGuardViolation):
        run()


def test_tool_use_block_raises_even_with_an_error_result(fake):
    fake.script = [init_message, assistant("t", blocks=[ToolUseBlock(id="1", name="Bash", input={})]),
                   result_message(is_error=True, num_turns=1)]
    with pytest.raises(HermeticGuardViolation):
        run()


def test_authentication_failed_is_returned_not_raised(fake):
    fake.script = [init_message, assistant(error="authentication_failed"), result_message(is_error=True, num_turns=0)]
    r = run()
    assert r.assistant_error == "authentication_failed" and r.is_error


def test_diagnostic_seam_returns_the_violation(fake):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), result_message()]
    r = async_to_sync(mod._run_hermetic_claude_for_diagnostic)("hello", model="haiku")
    assert r.violation and "tools" in r.violation


def test_diagnostic_seam_cwd_sets_options_and_expected_cwd(fake, tmp_path):
    other = tmp_path / "fixture"; other.mkdir()
    fake.script = [init_message, assistant("OK"), result_message()]
    r = async_to_sync(mod._run_hermetic_claude_for_diagnostic)("hello", model="haiku", cwd=other)
    assert fake.instances[0].options.cwd == str(other) and r.violation is None


def test_public_claude_api_has_no_cwd_or_override_parameter():
    import inspect

    assert list(inspect.signature(mod.hermetic_client_options).parameters) == ["model", "effort"]
    assert list(inspect.signature(mod.run_hermetic_claude).parameters) == ["prompt", "model"]
    assert list(inspect.signature(mod._run_hermetic_claude_for_diagnostic).parameters) == [
        "prompt", "model", "cwd", "options_override"]


def test_diagnostic_seam_options_override_skips_the_guard(fake):
    fake.script = [lambda cwd: init_message(cwd, tools=["Bash"]), assistant("OK"), result_message()]
    r = async_to_sync(mod._run_hermetic_claude_for_diagnostic)(
        "hello", model="haiku", options_override=lambda o: o)
    assert r.violation is None and r.init["tools"] == ["Bash"]
```

(`SystemMessage`/`AssistantMessage`/`ResultMessage` constructors: check the installed SDK signature with `uv run python -c "import inspect, claude_agent_sdk as s; print(inspect.signature(s.ResultMessage))"` and adjust the keyword arguments of the helper builders once; keep the helpers in this one test module.)

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_hermetic_claude_run.py -q` → FAIL (`_client_factory` missing).

- [ ] **Step 3: Implement** — append to `src/twicc/providers/claude_code/hermetic.py`:

```python
from collections.abc import Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

# Replaced by the unit tests.
_client_factory = ClaudeSDKClient


def _count_tool_blocks(message) -> int:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return 0
    return sum(1 for block in content if type(block).__name__ in _TOOL_BLOCK_NAMES)


async def _run(prompt, *, model, cwd, options_override, raise_on_violation) -> HermeticClaudeResult:
    expected_cwd = Path(cwd) if cwd is not None else hermetic_cwd()
    permission_calls: list[str] = []
    options = _build_options(model=model, effort="low", cwd=expected_cwd, permission_calls=permission_calls)
    guarded = options_override is None
    if options_override is not None:
        options = options_override(options)
    client = _client_factory(options=options)

    text = ""
    assistant_error = None
    init: dict = {}
    usage: dict = {}
    is_error = False
    num_turns = None
    tool_blocks = 0
    violation: HermeticGuardViolation | None = None   # constructing it logs the single warning line
    try:
        await client.connect()
        await client.query(prompt)
        async for message in client.receive_messages():
            if isinstance(message, SystemMessage) and getattr(message, "subtype", None) == "init":
                init = dict(getattr(message, "data", {}) or {})
                if guarded:
                    try:
                        check_claude_init(init, cwd=expected_cwd, alias=model)
                    except HermeticGuardViolation as exc:
                        violation = exc
                        break
            elif isinstance(message, AssistantMessage):
                tool_blocks += _count_tool_blocks(message)
                if message.error:
                    assistant_error = message.error
                text += "".join(b.text for b in message.content if isinstance(b, TextBlock))
            elif isinstance(message, ResultMessage):
                tool_blocks += _count_tool_blocks(message)
                usage, is_error, num_turns = dict(message.usage or {}), bool(message.is_error), message.num_turns
                break
            else:
                tool_blocks += _count_tool_blocks(message)
        if violation is None and guarded and not init:
            violation = HermeticGuardViolation("no init message received")
        if violation is not None:
            try:
                await client.interrupt()
            except Exception:
                logger.debug("interrupt failed after a guard violation", exc_info=True)
    finally:
        try:
            await client.disconnect()
        except Exception:
            logger.debug("disconnect failed", exc_info=True)

    result = HermeticClaudeResult(
        text=text.strip(), assistant_error=assistant_error, is_error=is_error, usage=usage, init=init,
        num_turns=num_turns, tool_blocks_seen=tool_blocks, permission_callback_calls=len(permission_calls),
    )
    if guarded and violation is None:
        try:
            check_claude_result(result)   # tool activity counts even when no ResultMessage arrived
        except HermeticGuardViolation as exc:
            violation = exc
    if violation is not None:
        if raise_on_violation:
            raise violation
        return result._replace(violation=violation.reason)
    return result


async def run_hermetic_claude(prompt: str, *, model: str) -> HermeticClaudeResult:
    """One hermetic Claude turn. Raises ``HermeticGuardViolation`` on a guard failure.

    Callers wrap it in their own ``asyncio.wait_for`` and retry loop.
    """
    return await _run(prompt, model=model, cwd=None, options_override=None, raise_on_violation=True)


async def _run_hermetic_claude_for_diagnostic(
    prompt: str,
    *,
    model: str,
    cwd: Path | None = None,
    options_override: Callable[[ClaudeAgentOptions], ClaudeAgentOptions] | None = None,
) -> HermeticClaudeResult:
    """Diagnostic seam (§5.1): same implementation, but the violation is returned, not raised.

    ``cwd`` sets both the options' working directory and the guard's expected one.
    ``options_override`` builds the diagnostic's unrestricted control options; the guard is then skipped.
    """
    return await _run(prompt, model=model, cwd=cwd, options_override=options_override, raise_on_violation=False)
```

Note: when the stream ends without a `ResultMessage`, `num_turns` stays `None`; `check_claude_result` still checks the tool activity, and the call sites treat the empty `text` as a failure exactly as today.

- [ ] **Step 4: Run tests and lint** — `uv run pytest tests/test_hermetic_claude_run.py tests/test_hermetic_claude_guard.py -q && uvx ruff check src/twicc/providers/claude_code/hermetic.py tests/test_hermetic_claude_run.py` → PASS.

- [ ] **Step 5: Commit** — `git add src/twicc/providers/claude_code/hermetic.py tests/test_hermetic_claude_run.py && git commit` (template of Global Constraints) (`feat(hermetic): add the Claude hermetic runner and its diagnostic seam`).

---

### Task 4: Codex catalogue (transform, validate, bundled source, cache)

**Files:**
- Create: `src/twicc/providers/codex/hermetic_catalog.py`
- Modify: `.gitignore` (add a `cache/` line next to `search-index/` and `logs/`: in a worktree the data dir is the checkout root, and the generated catalogues must not show up as untracked files)
- Test: `tests/test_hermetic_codex_catalog.py`

**Interfaces:**
- Consumes: Task 1 errors; `twicc.paths.get_data_dir`.
- Produces:
  - `TRANSFORM_VERSION = 1`; `PRODUCTION_BASE_INSTRUCTIONS`; `NEUTRAL_BASE_INSTRUCTIONS = "You are a helpful assistant."`; `NULLABLE_KEYS = ("tool_mode", "multi_agent_version")`
  - `transform_entry(entry: dict, *, variant: str) -> dict`
  - `validate_catalog(data: dict, *, model: str, variant: str) -> None`
  - `bundled_catalog(binary: Path) -> tuple[str, dict]`: `(cli_version, parsed --bundled JSON)`, memoised per binary path, thread-safe.
  - `ensure_catalog(binary: Path, model: str, variant: str = "production", cache_dir: Path | None = None) -> Path` (synchronous; call it through `asyncio.to_thread`)
  - `catalog_cache_name(cli_version, slug, variant, entry_hash) -> str`

- [ ] **Step 1: Write the failing tests** (`tests/test_hermetic_codex_catalog.py`)

```python
import copy
import threading

import orjson
import pytest

from twicc.providers.codex import hermetic_catalog as cat
from twicc.providers.hermetic import HermeticConfigError

ENTRY = {
    "slug": "gpt-6-luna", "display_name": "Luna", "base_instructions": "long original text",
    "model_messages": {"x": 1}, "include_apps_usage_instructions": True,
    "include_plugin_usage_instructions": True, "include_skills_usage_instructions": True,
    "shell_type": "shell_command", "tool_mode": "code", "apply_patch_tool_type": "freeform",
    "supports_search_tool": True, "experimental_supported_tools": ["a"], "multi_agent_version": "v2",
    "some_future_field": 42,
}


def test_transform_sets_every_overridden_field_and_keeps_the_rest():
    out = cat.transform_entry(ENTRY, variant="production")
    assert out["base_instructions"] == cat.PRODUCTION_BASE_INSTRUCTIONS
    assert out["model_messages"] is None
    assert out["include_apps_usage_instructions"] is False
    assert out["include_plugin_usage_instructions"] is False
    assert out["include_skills_usage_instructions"] is False
    assert out["shell_type"] == "disabled"
    assert out["tool_mode"] is None and out["multi_agent_version"] is None
    assert out["apply_patch_tool_type"] is None and out["supports_search_tool"] is False
    assert out["experimental_supported_tools"] == []
    assert out["some_future_field"] == 42 and out["display_name"] == "Luna"
    assert ENTRY["shell_type"] == "shell_command"  # the source is not mutated


def test_neutral_variant_differs_only_by_base_instructions():
    prod = cat.transform_entry(ENTRY, variant="production")
    neutral = cat.transform_entry(ENTRY, variant="neutral")
    assert neutral["base_instructions"] == "You are a helpful assistant."
    assert {k: v for k, v in neutral.items() if k != "base_instructions"} == \
           {k: v for k, v in prod.items() if k != "base_instructions"}


def test_nullable_keys_may_be_absent_in_the_source():
    entry = {k: v for k, v in ENTRY.items() if k not in cat.NULLABLE_KEYS}
    out = cat.transform_entry(entry, variant="production")
    assert out["tool_mode"] is None and out["multi_agent_version"] is None


def test_other_missing_field_raises_catalog():
    entry = {k: v for k, v in ENTRY.items() if k != "shell_type"}
    with pytest.raises(HermeticConfigError) as info:
        cat.transform_entry(entry, variant="production")
    assert info.value.reason == "catalog" and "shell_type" in str(info.value)


def _valid():
    return {"models": [cat.transform_entry(ENTRY, variant="production")]}


def test_validate_accepts_a_good_catalog_and_absent_nullable_keys():
    data = _valid()
    cat.validate_catalog(data, model="gpt-6-luna", variant="production")
    del data["models"][0]["tool_mode"]
    cat.validate_catalog(data, model="gpt-6-luna", variant="production")


@pytest.mark.parametrize("mutate", [
    lambda d: d["models"].append(copy.deepcopy(d["models"][0])),
    lambda d: d["models"][0].update(slug="other"),
    lambda d: d["models"][0].update(shell_type="shell_command"),
    lambda d: d["models"][0].update(tool_mode="code"),
    lambda d: d["models"][0].update(base_instructions="changed"),
    lambda d: d.update(models=[]),
])
def test_validate_rejects_a_bad_catalog(mutate):
    data = _valid()
    mutate(data)
    with pytest.raises(HermeticConfigError) as info:
        cat.validate_catalog(data, model="gpt-6-luna", variant="production")
    assert info.value.reason == "catalog"


class FakeRun:
    def __init__(self, version="codex-cli 0.160.0", models=None, code=0, out=None):
        self.calls = []
        self.version, self.models, self.code, self.out = version, models or [ENTRY], code, out

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        from types import SimpleNamespace
        if "--version" in argv:
            return SimpleNamespace(returncode=0, stdout=self.version + "\n", stderr="")
        stdout = self.out if self.out is not None else orjson.dumps({"models": self.models}).decode()
        return SimpleNamespace(returncode=self.code, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def _clear_memo():
    cat._reset_memo()


@pytest.fixture
def hermetic_logs():
    import logging

    logger = logging.getLogger("twicc.providers.hermetic")
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler, was_disabled, level = _Collect(), logger.disabled, logger.level
    logger.disabled = False
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.disabled = was_disabled


def test_ensure_catalog_writes_once_and_names_the_file_from_its_inputs(tmp_path, monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(cat.subprocess, "run", run)
    path = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert path.parent == tmp_path and "0.160.0" in path.name and "gpt-6-luna" in path.name
    assert path.name.endswith(f"-v{cat.TRANSFORM_VERSION}.json")
    cat.validate_catalog(orjson.loads(path.read_bytes()), model="gpt-6-luna", variant="production")
    cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert sum("--bundled" in c for c in run.calls) == 1  # memoised per process and binary


def test_a_changed_entry_changes_the_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    first = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    cat._reset_memo()
    monkeypatch.setattr(cat.subprocess, "run", FakeRun(models=[{**ENTRY, "some_future_field": 43}]))
    second = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert first != second


def test_a_changed_cli_version_changes_the_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    first = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    cat._reset_memo()
    monkeypatch.setattr(cat.subprocess, "run", FakeRun(version="codex-cli 0.161.0"))
    assert cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path) != first


def test_a_changed_transformation_constant_changes_the_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    first = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    monkeypatch.setattr(cat, "TRANSFORM_VERSION", cat.TRANSFORM_VERSION + 1)
    assert cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path) != first


def test_unknown_slug_raises_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "nope", cache_dir=tmp_path)
    assert info.value.reason == "catalog"


@pytest.mark.parametrize("run", [FakeRun(code=1), FakeRun(out="not json")])
def test_subprocess_failure_or_garbage_raises_catalog(tmp_path, monkeypatch, run):
    monkeypatch.setattr(cat.subprocess, "run", run)
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert info.value.reason == "catalog"


def test_subprocess_timeout_raises_catalog(tmp_path, monkeypatch):
    def boom(argv, **kw):
        raise cat.subprocess.TimeoutExpired(argv, 20)
    monkeypatch.setattr(cat.subprocess, "run", boom)
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert info.value.reason == "catalog"


def test_concurrent_generation_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    paths, errors = [], []

    def go():
        try:
            paths.append(cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors and len(set(paths)) == 1
    cat.validate_catalog(orjson.loads(paths[0].read_bytes()), model="gpt-6-luna", variant="production")
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_regenerating_a_damaged_cache_file_logs_nothing(tmp_path, monkeypatch, hermetic_logs):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    path = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    path.write_text("{}")
    cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert hermetic_logs == []


def test_a_cached_file_that_fails_validation_is_regenerated(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    path = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    path.write_text("{}")
    again = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert again == path
    cat.validate_catalog_file(again, "gpt-6-luna", "production")  # raises if still damaged
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_hermetic_codex_catalog.py -q` → FAIL.

- [ ] **Step 3: Implement** (`src/twicc/providers/codex/hermetic_catalog.py`)

```python
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

TRANSFORM_VERSION = 1
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
    entries = [e for e in data.get("models", []) if e.get("slug") == model] if isinstance(data, dict) else []
    if not entries:
        raise HermeticConfigError("catalog", f"The bundled catalogue of {version} has no model {model!r}")
    source = entries[0]
    entry_hash = hashlib.sha256(orjson.dumps(source, option=orjson.OPT_SORT_KEYS)).hexdigest()
    if cache_dir is None:
        from twicc.paths import get_data_dir

        cache_dir = get_data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / catalog_cache_name(version, model, variant, entry_hash)
    if path.exists() and _file_problem(path, model, variant) is None:
        return path   # a damaged cache file falls through and is regenerated, without a failure log line
    content = {"models": [transform_entry(source, variant=variant)]}
    validate_catalog(content, model=model, variant=variant)
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(orjson.dumps(content))
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path
```

(`entry.get(key)` on a nullable key returns `None` when absent. The diagnostic of Task 9 imports `validate_catalog_file`, `ensure_catalog` and `bundled_catalog`.)

- [ ] **Step 4: Run tests and lint** — `uv run pytest tests/test_hermetic_codex_catalog.py -q && uvx ruff check src/twicc/providers/codex/hermetic_catalog.py tests/test_hermetic_codex_catalog.py` → PASS.

- [ ] **Step 5: Real-binary smoke (no model call, no network)** — run once by hand to prove the transformation matches the real entry:

```bash
cd /home/twidi/dev/twicc-poc && TWICC_DATA_DIR=$(mktemp -d) uv run python - <<'EOF'
import asyncio
from twicc.providers.codex.bin import make_codex_config
from twicc.providers.codex import hermetic_catalog as c
async def main():
    cfg = await make_codex_config()
    p = c.ensure_catalog(__import__("pathlib").Path(cfg.codex_bin), "gpt-6-luna")
    c.validate_catalog_file(p, "gpt-6-luna", "production"); print("OK", p)
asyncio.run(main())
EOF
```
Expected: prints `OK <path under the temporary data dir>/cache/...`. (Setting `TWICC_DATA_DIR` keeps the cache out of `~/.twicc`.)

- [ ] **Step 6: Commit** — `git add src/twicc/providers/codex/hermetic_catalog.py tests/test_hermetic_codex_catalog.py .gitignore && git commit` (template of Global Constraints) (`feat(hermetic): generate and validate the minimal Codex model catalogue`).

---

### Task 5: Codex overrides, thread config, guards and refusing handler (pure)

**Files:**
- Create: `src/twicc/providers/codex/hermetic.py` (this task: constants, builders, guards, handler; Task 6 adds the runtime classes)
- Test: `tests/test_hermetic_codex_guard.py`

**Interfaces:**
- Consumes: Task 1 errors.
- Produces:
  - `FEATURES_OFF: tuple[str, ...]` (the 17 names of §5.4 (b))
  - `process_overrides(catalog_path: Path, extra: Iterable[str] = ()) -> tuple[str, ...]`
  - `thread_config(mcp_server_names: Iterable[str]) -> dict`
  - `check_codex_model_list(response, *, model: str) -> None`
  - `check_codex_thread_start(start: dict, *, model: str, cwd: Path, codex_home: Path) -> None`
  - `classify_codex_item(type_name: str | None) -> None` (raises on a non-allowed type)
  - `ALLOWED_ITEM_TYPES = frozenset({"userMessage", "agentMessage", "reasoning"})`
  - `class RefusingApprovalHandler`: callable `(method, params) -> dict`; attributes `violated: threading.Event`, `method: str | None`

- [ ] **Step 1: Write the failing tests** (`tests/test_hermetic_codex_guard.py`)

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from twicc.providers.codex import hermetic as mod
from twicc.providers.hermetic import HermeticGuardViolation

CWD = Path("/tmp/hermetic-llm-1000")
HOME = Path("/home/u/.codex")


def test_process_overrides_contain_every_item_of_the_spec():
    o = mod.process_overrides(Path("/c/cat.json"))
    assert 'model_catalog_json="/c/cat.json"' in o
    for item in ("project_doc_max_bytes=0", "skills.max_context_tokens=1", 'web_search="disabled"', "notify=[]"):
        assert item in o
    for name in ("hooks", "plugins", "apps", "browser_use", "browser_use_external", "computer_use",
                 "in_app_browser", "image_generation", "goals", "memories", "shell_tool", "unified_exec",
                 "multi_agent", "view_image", "skill_search", "tool_suggest", "sleep_tool"):
        assert f"features.{name}=false" in o
    assert len(mod.FEATURES_OFF) == 17
    assert not any(x.startswith("mcp_servers") for x in o)


def test_catalog_path_is_a_toml_basic_string():
    o = mod.process_overrides(Path('/c/we"ird\\dir/cat.json'))
    assert 'model_catalog_json="/c/we\\"ird\\\\dir/cat.json"' in o


def test_extra_overrides_are_appended():
    assert mod.process_overrides(Path("/c"), ("x=1",))[-1] == "x=1"


def test_thread_config_keeps_mcp_names_literal():
    cfg = mod.thread_config(["plain", "with-hyphen", "with.dot"])
    assert cfg["mcp_servers"] == {n: {"enabled": False} for n in ("plain", "with-hyphen", "with.dot")}


def test_thread_config_uses_dotted_keys_for_features_and_tools():
    cfg = mod.thread_config([])
    assert cfg["features.default_mode_request_user_input"] is False
    assert cfg["tools.experimental_request_user_input.enabled"] is False
    assert cfg["suppress_unstable_features_warning"] is True
    assert "features" not in cfg and "tools" not in cfg
    assert all(not isinstance(v, dict) for k, v in cfg.items() if k != "mcp_servers")


def models(*slugs, cursor=None):
    return SimpleNamespace(data=[SimpleNamespace(id=s, model=s) for s in slugs], next_cursor=cursor)


def test_model_list_with_exactly_the_requested_model_passes():
    mod.check_codex_model_list(models("gpt-6-luna"), model="gpt-6-luna")


def test_model_list_with_several_models_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        mod.check_codex_model_list(models("gpt-6-luna", "gpt-6-sol"), model="gpt-6-luna")


def test_model_list_with_cursor_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        mod.check_codex_model_list(models("gpt-6-luna", cursor="next"), model="gpt-6-luna")


def test_model_list_with_another_model_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        mod.check_codex_model_list(models("other"), model="gpt-6-luna")


def start(**over):
    s = {"model": "gpt-6-luna", "cwd": str(CWD), "sandbox": {"type": "readOnly", "network_access": False},
         "approval_policy": "never", "instruction_sources": [str(HOME / "AGENTS.md")]}
    s.update(over)
    return s


def check(s):
    mod.check_codex_thread_start(s, model="gpt-6-luna", cwd=CWD, codex_home=HOME)


def test_good_thread_start_passes_and_empty_instruction_sources_are_accepted():
    check(start())
    check(start(instruction_sources=[]))
    check(start(instruction_sources=[str(HOME / "AGENTS.override.md")]))


@pytest.mark.parametrize("over", [
    {"model": "other"}, {"cwd": "/home/me/project"},
    {"sandbox": {"type": "dangerFullAccess"}}, {"sandbox": {"type": "readOnly", "network_access": True}},
    {"approval_policy": "on-request"},
    {"instruction_sources": ["/home/me/project/AGENTS.md"]},
    {"instruction_sources": [str(HOME / "sub" / "AGENTS.md")]},
])
def test_bad_thread_start_is_a_violation(over):
    with pytest.raises(HermeticGuardViolation):
        check(start(**over))


@pytest.mark.parametrize("missing", ["sandbox", "instruction_sources", "approval_policy", "model", "cwd"])
def test_missing_thread_start_field_is_a_violation(missing):
    s = start(); del s[missing]
    with pytest.raises(HermeticGuardViolation):
        check(s)


def test_none_instruction_sources_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        check(start(instruction_sources=None))


def test_symlinked_agents_file_inside_the_home_is_accepted(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    target = tmp_path / "dotfiles-AGENTS.md"; target.write_text("x")
    (home / "AGENTS.md").symlink_to(target)
    mod.check_codex_thread_start(start(instruction_sources=[str(home / "AGENTS.md")]),
                                 model="gpt-6-luna", cwd=CWD, codex_home=home)


def test_symlinked_codex_home_instruction_file_is_accepted_after_resolution(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    link = tmp_path / "home"; link.symlink_to(real)
    mod.check_codex_thread_start(start(instruction_sources=[str(real / "AGENTS.md")]),
                                 model="gpt-6-luna", cwd=CWD, codex_home=link)


@pytest.mark.parametrize("t", ["userMessage", "agentMessage", "reasoning"])
def test_allowed_item_types(t):
    mod.classify_codex_item(t)


@pytest.mark.parametrize("t", ["commandExecution", "fileChange", "mcpToolCall", "webSearch", "somethingNew", None])
def test_other_item_types_are_violations_and_named(t):
    with pytest.raises(HermeticGuardViolation) as info:
        mod.classify_codex_item(t)
    assert str(t) in info.value.reason


@pytest.mark.parametrize("method,expected", [
    ("item/commandExecution/requestApproval", {"decision": "decline"}),
    ("item/fileChange/requestApproval", {"decision": "decline"}),
    ("item/permissions/requestApproval", {"permissions": {}, "scope": "turn"}),
    ("item/tool/requestUserInput", {"answers": {}}),
    ("mcpServer/elicitation/request", {"action": "cancel"}),
    ("something/else", {}),
])
def test_refusing_handler_replies_and_records(method, expected):
    h = mod.RefusingApprovalHandler()
    assert not h.violated.is_set()
    assert h(method, {}) == expected
    assert h.violated.is_set() and h.method == method


def test_refusing_replies_equal_the_agent_defaults():
    from twicc.providers.codex.agent.approvals import APPROVAL_METHODS, default_response_for

    assert set(mod._REFUSALS) == set(APPROVAL_METHODS)
    for method in APPROVAL_METHODS:
        assert mod._REFUSALS[method] == default_response_for(method)


def test_refusing_handler_keeps_the_first_method():
    h = mod.RefusingApprovalHandler()
    h("a/b", None); h("c/d", None)
    assert h.method == "a/b"
```

(The method strings are the ones of `APPROVAL_METHODS`, `ELICITATION_METHOD` and `REQUEST_USER_INPUT_METHOD` in `src/twicc/providers/codex/agent/approvals.py`.)

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_hermetic_codex_guard.py -q` → FAIL.

- [ ] **Step 3: Implement** (`src/twicc/providers/codex/hermetic.py`, first part)

```python
"""
Hermetic Codex calls: a minimal catalogue, process overrides and thread parameters
that leave the model no tool, plus a runtime guard (design §5.4, §5.5).
"""
import logging
import threading
from collections.abc import Iterable
from pathlib import Path

import orjson

from twicc.providers.hermetic import HermeticGuardViolation

logger = logging.getLogger(__name__)

# ``features.*`` names switched off at process level. Unknown names are silently
# ignored by Codex, so the manual diagnostic (O4) checks they still exist.
FEATURES_OFF: tuple[str, ...] = (
    "hooks", "plugins", "apps", "browser_use", "browser_use_external", "computer_use", "in_app_browser",
    "image_generation", "goals", "memories", "shell_tool", "unified_exec", "multi_agent", "view_image",
    "skill_search", "tool_suggest", "sleep_tool",
)

ALLOWED_ITEM_TYPES = frozenset({"userMessage", "agentMessage", "reasoning"})


def process_overrides(catalog_path: Path, extra: Iterable[str] = ()) -> tuple[str, ...]:
    """The ``--config key=value`` overrides of the app-server process (§5.4 (b))."""
    # A JSON string is a valid TOML basic string (quotes and backslashes escaped).
    return (
        f"model_catalog_json={orjson.dumps(str(catalog_path)).decode()}",
        "project_doc_max_bytes=0",
        "skills.max_context_tokens=1",   # 0 is rejected at startup
        'web_search="disabled"',
        "notify=[]",
        *(f"features.{name}=false" for name in FEATURES_OFF),
        *extra,
    )


def thread_config(mcp_server_names: Iterable[str]) -> dict:
    """The thread-level ``config`` patch (§5.4 (c)).

    ``mcp_servers`` must be a nested table (it merges with each server's own
    transport settings, and keeps names with dots literal). ``features`` and
    ``tools`` must be DOTTED keys: a nested ``features`` table at thread level
    replaces the whole table of the lower layers and restores every tool.
    """
    return {
        "mcp_servers": {name: {"enabled": False} for name in mcp_server_names},
        "features.default_mode_request_user_input": False,
        "tools.experimental_request_user_input.enabled": False,
        "suppress_unstable_features_warning": True,
    }


def _field(source, *names):
    """First present, non-None value among ``names`` in a dict, else ``None``."""
    for name in names:
        value = source.get(name)
        if value is not None:
            return value
    return None


def _field_keep_empty(source, *names):
    """Like ``_field`` but an empty list/dict counts as present."""
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return None


def _model_slug(model_obj) -> str | None:
    return getattr(model_obj, "model", None) or getattr(model_obj, "id", None)


def check_codex_model_list(response, *, model: str) -> None:
    """``model/list`` (hidden included) must return exactly the requested model: proof the catalogue was loaded."""
    data = getattr(response, "data", None)
    if data is None:
        raise HermeticGuardViolation("model/list returned no data")
    if getattr(response, "next_cursor", None):
        raise HermeticGuardViolation("model/list has a next page: the catalogue override was not loaded")
    slugs = [_model_slug(m) for m in data]
    if slugs != [model]:
        raise HermeticGuardViolation(f"model/list returned {slugs!r}, expected {[model]!r}")


def check_codex_thread_start(start: dict, *, model: str, cwd: Path, codex_home: Path) -> None:
    """Verify the ``thread/start`` response (available before any model call)."""
    got_model = _field(start, "model")
    got_cwd = _field(start, "cwd")
    sandbox = _field(start, "sandbox")
    approval = _field(start, "approval_policy", "approvalPolicy")
    if got_model is None or got_cwd is None or sandbox is None or approval is None:
        raise HermeticGuardViolation("thread/start response lacks model, cwd, sandbox or approval policy")
    sources = _field_keep_empty(start, "instruction_sources", "instructionSources")
    if sources is None:
        raise HermeticGuardViolation("thread/start response lacks instruction_sources")
    if got_model != model:
        raise HermeticGuardViolation(f"thread/start reports model {got_model!r}")
    if Path(got_cwd).resolve() != Path(cwd).resolve():
        raise HermeticGuardViolation(f"thread/start reports cwd {got_cwd!r}")
    if sandbox.get("type") != "readOnly" or sandbox.get("network_access", sandbox.get("networkAccess")):
        raise HermeticGuardViolation(f"thread/start reports sandbox {sandbox!r}")
    if approval != "never":
        raise HermeticGuardViolation(f"thread/start reports approval policy {approval!r}")
    home = Path(codex_home).resolve()
    for source in sources:
        # Resolve the directory only: ``~/.codex/AGENTS.md`` is often a symlink into a dotfiles repository.
        path = Path(source)
        if path.parent.resolve() != home or path.name not in ("AGENTS.md", "AGENTS.override.md"):
            raise HermeticGuardViolation(f"unexpected instruction source {source!r}")


def classify_codex_item(type_name: str | None) -> None:
    """Fail on any stream item that is not a plain message or reasoning (§5.5 (iii))."""
    if type_name not in ALLOWED_ITEM_TYPES:
        raise HermeticGuardViolation(f"unexpected stream item type {type_name!r}")


_REFUSALS: dict[str, dict] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "mcpServer/elicitation/request": {"action": "cancel"},
    "item/tool/requestUserInput": {"answers": {}},
}


class RefusingApprovalHandler:
    """Refuses every server request and records the first one.

    Runs in the SDK's reader thread, hence the ``threading.Event``. The reply
    shapes are the ones TwiCC already sends when it cannot reach a user.
    """

    def __init__(self) -> None:
        self.violated = threading.Event()
        self.method: str | None = None

    def __call__(self, method: str, params: dict | None) -> dict:
        if not self.violated.is_set():
            self.method = method
            self.violated.set()
        # Fresh dict on every call. The shapes equal ``approvals.default_response_for``; they are inlined
        # so this module does not import the Codex agent package (a heavy import and a cycle risk).
        reply = _REFUSALS.get(method)
        return dict(reply) if reply is not None else {}
```

- [ ] **Step 4: Run tests and lint** — `uv run pytest tests/test_hermetic_codex_guard.py -q && uvx ruff check src/twicc/providers/codex/hermetic.py tests/test_hermetic_codex_guard.py` → PASS.

- [ ] **Step 5: Commit** — `git add src/twicc/providers/codex/hermetic.py tests/test_hermetic_codex_guard.py && git commit` (template of Global Constraints) (`feat(hermetic): add the Codex overrides, thread config and runtime guard`).

---

### Task 6: Codex plan, thread, runner (`prepare_hermetic_codex`, `hermetic_codex`, `run_turn`)

**Files:**
- Modify: `src/twicc/providers/codex/sdk_wrappers.py` (`thread_start_with_policy`, ~line 297)
- Modify: `src/twicc/providers/codex/hermetic.py` (append)
- Test: `tests/test_hermetic_codex_run.py`

**Interfaces:**
- Consumes: Tasks 1, 4, 5; `make_codex_config(*, cwd, **extra)` (`bin.py:88`); `codex_binary_path()` (`providers/codex/runtime.py`); `codex_home()` (`provider_homes.py`).
- Produces:
  - `class HermeticCodexPlan(NamedTuple)`: `model: str`, `config: CodexConfig`, `catalog_path: Path`, `cwd: Path`
  - `async prepare_hermetic_codex(model: str) -> HermeticCodexPlan`
  - `async _prepare_hermetic_codex_for_diagnostic(model, *, catalog_variant="production", catalog_path=None, extra_config_overrides=()) -> HermeticCodexPlan`
  - `hermetic_codex(plan)` async context manager yielding `HermeticCodexThread`
  - `class HermeticCodexResult(NamedTuple)`: `text: str`, `terminal_error: object | None`, `input_tokens: int | None`, `start: dict`
  - `HermeticCodexThread`: attributes `start: dict`, `handler: RefusingApprovalHandler`, `disabled_mcp_servers: tuple[str, ...]`, `refused_method` (property → `handler.method`), `async run_turn(prompt, *, effort) -> HermeticCodexResult`
  - `async run_hermetic_codex(plan, prompt, *, effort) -> HermeticCodexResult`
  - `TwiccAsyncThread.start_response` / `thread_start_with_policy` keeps `thread.start_response = started`

- [ ] **Step 1: Write the failing tests** (`tests/test_hermetic_codex_run.py`). Fake SDK objects, no process:

```python
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync

from twicc.providers.codex import hermetic as mod
from twicc.providers.hermetic import HermeticConfigError, HermeticGuardViolation

MODEL = "gpt-6-luna"


class FakeStream:
    def __init__(self, events, after=None):
        self.events, self.after = events, after

    async def stream(self):
        for e in self.events:
            yield e
        if self.after:
            self.after()


def item_event(method, type_, text=None):
    d = {"type": type_}
    if text is not None:
        d["text"] = text
    return SimpleNamespace(method=method, payload=SimpleNamespace(item=d))


def token_event(n):
    payload = SimpleNamespace(model_dump=lambda mode=None: {"token_usage": {"last": {"input_tokens": n}}})
    return SimpleNamespace(method="thread/tokenUsage/updated", payload=payload)


class FakeCodex:
    """Stands for TwiccAsyncCodex."""
    events = []
    after = None
    model_list = SimpleNamespace(data=[SimpleNamespace(id=MODEL, model=MODEL)], next_cursor=None)
    mcp = {"mcp_servers": {"cloudflare-api": {}, "node.repl": {}}}

    def __init__(self, config=None):
        self.config = config
        self._client = SimpleNamespace(_sync=SimpleNamespace(_approval_handler=None))
        self.closed = False
        type(self).last = self

    async def _ensure_initialized(self): pass

    async def model_list_call(self): return type(self).model_list

    async def thread_start_with_policy(self, **kw):
        self.start_kwargs = kw
        thread = SimpleNamespace(
            start_response=SimpleNamespace(model_dump=lambda mode=None: {
                "model": MODEL, "cwd": str(self.config.cwd),
                "sandbox": {"type": "readOnly", "network_access": False}, "approval_policy": "never",
                "instruction_sources": []}),
            turn_with_policy=self._turn,
        )
        return thread

    async def _turn(self, *_a, **_k):
        return FakeStream(type(self).events, type(self).after)

    async def close(self): self.closed = True


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeCodex.events = []; FakeCodex.after = None
    FakeCodex.model_list = SimpleNamespace(data=[SimpleNamespace(id=MODEL, model=MODEL)], next_cursor=None)

    async def fake_config(*, cwd=None, **extra):
        return SimpleNamespace(cwd=cwd, **extra)

    monkeypatch.setattr(mod, "make_codex_config", fake_config)
    monkeypatch.setattr(mod, "hermetic_cwd", lambda base=None: tmp_path)
    monkeypatch.setattr(mod, "_client_factory", FakeCodex)
    monkeypatch.setattr(mod, "_list_models", lambda codex: codex.model_list_call())
    monkeypatch.setattr(mod, "_read_mcp_server_names", lambda codex, cwd: _names(codex))
    monkeypatch.setattr(mod, "codex_home", lambda: SimpleNamespace(path=tmp_path / "home"))
    async def _cat(model, variant):
        return tmp_path / "cat.json"

    async def _runtime():
        calls.append("runtime")

    calls: list[str] = []
    monkeypatch.setattr(mod, "_catalog_for", _cat)
    monkeypatch.setattr(mod, "ensure_codex_runtime", _runtime)
    FakeCodex.runtime_calls = calls
    return FakeCodex


async def _names(codex):
    return tuple(codex.mcp["mcp_servers"])


def run(prompt="hi"):
    async def go():
        plan = await mod.prepare_hermetic_codex(MODEL)
        return await mod.run_hermetic_codex(plan, prompt, effort="low")
    return async_to_sync(go)()


def test_happy_path_returns_text_tokens_and_start(fake):
    fake.events = [item_event("item/started", "agentMessage"), token_event(670),
                   item_event("item/completed", "agentMessage", "OK")]
    r = run()
    assert r.text == "OK" and r.input_tokens == 670 and r.terminal_error is None
    assert r.start["model"] == MODEL and fake.last.closed


def test_thread_is_started_read_only_with_dotted_config_and_disabled_mcp(fake):
    fake.events = [item_event("item/completed", "agentMessage", "OK")]
    run()
    kw = fake.last.start_kwargs
    assert kw["ephemeral"] is True and kw["model"] == MODEL
    assert kw["config"]["mcp_servers"] == {"cloudflare-api": {"enabled": False}, "node.repl": {"enabled": False}}
    assert kw["config"]["features.default_mode_request_user_input"] is False
    assert str(kw["sandbox"]).endswith("read_only") and str(kw["approval_policy"]).lower().count("never")


def test_refusing_handler_is_installed_before_the_first_request(fake):
    fake.events = [item_event("item/completed", "agentMessage", "OK")]
    run()
    assert isinstance(fake.last._client._sync._approval_handler, mod.RefusingApprovalHandler)


def test_unexpected_item_type_raises_and_closes(fake):
    fake.events = [item_event("item/started", "commandExecution")]
    with pytest.raises(HermeticGuardViolation):
        run()
    assert fake.last.closed


def test_run_turn_checks_the_flag_after_the_stream_ends(fake):
    def refuse():
        fake.last._client._sync._approval_handler("item/commandExecution/requestApproval", {})
    fake.events = [item_event("item/completed", "agentMessage", "OK")]
    fake.after = refuse
    with pytest.raises(HermeticGuardViolation) as info:
        run()
    assert "item/commandExecution/requestApproval" in info.value.reason


def test_terminal_error_is_collected(fake):
    from openai_codex.generated.v2_all import ErrorNotification
    err = SimpleNamespace(method="error", payload=ErrorNotification.model_construct(will_retry=False))
    fake.events = [err]
    r = run()
    assert r.terminal_error is err.payload


def test_several_models_in_model_list_is_a_violation(fake):
    fake.model_list = SimpleNamespace(
        data=[SimpleNamespace(id=MODEL, model=MODEL), SimpleNamespace(id="x", model="x")], next_cursor=None)
    with pytest.raises(HermeticGuardViolation):
        run()
    assert fake.last.closed


def test_config_read_failure_is_mcp_config_error(fake, monkeypatch):
    async def boom(codex, cwd):
        raise RuntimeError("rpc down")
    monkeypatch.setattr(mod, "_read_mcp_server_names", boom)
    with pytest.raises(HermeticConfigError) as info:
        run()
    assert info.value.reason == "mcp-config" and isinstance(info.value.__cause__, RuntimeError)


def test_start_failure_is_a_start_error(fake, monkeypatch):
    async def boom(codex): raise RuntimeError("exit 1")
    monkeypatch.setattr(mod, "_list_models", boom)
    with pytest.raises(HermeticConfigError) as info:
        run()
    assert info.value.reason == "start"


def test_diagnostic_prepare_passes_extra_overrides_and_catalog(fake, tmp_path):
    async def go():
        return await mod._prepare_hermetic_codex_for_diagnostic(
            MODEL, catalog_variant="neutral", catalog_path=tmp_path / "bogus.json", extra_config_overrides=("a=1",))
    plan = async_to_sync(go)()
    assert plan.catalog_path == tmp_path / "bogus.json" and "a=1" in plan.config.config_overrides


def test_prepare_ensures_the_runtime_before_building_the_catalogue(fake):
    async_to_sync(mod.prepare_hermetic_codex)(MODEL)
    assert fake.runtime_calls == ["runtime"]


def test_a_refused_request_mid_stream_interrupts_the_turn(fake):
    handle_calls = []
    def refuse():
        fake.last._client._sync._approval_handler("item/fileChange/requestApproval", {})
    fake.events = [item_event("item/started", "agentMessage"), item_event("item/completed", "agentMessage", "x")]
    original = FakeCodex._turn

    async def _turn(self, *a, **k):
        stream = await original(self, *a, **k)
        async def interrupt():
            handle_calls.append("interrupt")
        stream.interrupt = interrupt
        first = stream.stream
        async def gen():
            async for e in first():
                if not handle_calls:
                    refuse()
                yield e
        stream.stream = gen
        return stream
    FakeCodex._turn = _turn
    try:
        with pytest.raises(HermeticGuardViolation):
            run()
    finally:
        FakeCodex._turn = original
    assert handle_calls == ["interrupt"]


def test_public_prepare_has_no_catalog_parameter():
    import inspect
    assert list(inspect.signature(mod.prepare_hermetic_codex).parameters) == ["model"]


def test_diagnostic_entry_points_are_not_referenced_from_src():
    import pathlib
    src = pathlib.Path(mod.__file__).parents[3]  # .../src
    defining = {
        "_prepare_hermetic_codex_for_diagnostic": src / "twicc/providers/codex/hermetic.py",
        "_run_hermetic_claude_for_diagnostic": src / "twicc/providers/claude_code/hermetic.py",
    }
    for needle, definer in defining.items():
        users = [p for p in src.rglob("*.py") if needle in p.read_text() and p != definer]
        assert users == [], (needle, users)
```

- [ ] **Step 1b: Run to verify they fail** — `uv run pytest tests/test_hermetic_codex_run.py -q` → FAIL (`prepare_hermetic_codex` missing).

- [ ] **Step 2: Implement.** First `sdk_wrappers.py` — in `thread_start_with_policy`, after `thread.initial_model = ...` add:

```python
        thread.start_response = started
```
and initialise `start_response: Any = None` where `initial_model` is declared on `TwiccAsyncThread` (grep `initial_model` in that file).

Then append to `src/twicc/providers/codex/hermetic.py`:

```python
import asyncio
from contextlib import asynccontextmanager
from typing import NamedTuple

from openai_codex import TextInput
from openai_codex.generated.v2_all import (
    AskForApproval,
    ConfigReadResponse,
    ErrorNotification,
    ReasoningEffort,
    SandboxMode,
)

from twicc.provider_homes import codex_home
from twicc.providers.codex import hermetic_catalog
from twicc.providers.codex.bin import make_codex_config
from twicc.providers.codex.runtime import codex_binary_path, ensure_codex_runtime
from twicc.providers.codex.sdk_wrappers import TwiccAsyncCodex
from twicc.providers.hermetic import HermeticConfigError, hermetic_cwd

# Replaced by the unit tests.
_client_factory = TwiccAsyncCodex


class HermeticCodexPlan(NamedTuple):
    model: str
    config: object          # CodexConfig
    catalog_path: Path
    cwd: Path


class HermeticCodexResult(NamedTuple):
    text: str
    terminal_error: object | None   # ErrorNotification payload, in the shape the auth classifier receives
    input_tokens: int | None
    start: dict


async def _catalog_for(model: str, variant: str) -> Path:
    return await asyncio.to_thread(hermetic_catalog.ensure_catalog, codex_binary_path(), model, variant)


async def _prepare(model, *, catalog_variant, catalog_path, extra_config_overrides) -> HermeticCodexPlan:
    # Before anything runs the binary: downloads the runtime when the cache was pruned (as make_codex_config does).
    await ensure_codex_runtime()
    cwd = hermetic_cwd()
    if catalog_path is None:
        catalog_path = await _catalog_for(model, catalog_variant)
        # ensure_catalog validated it; a caller-supplied path is used as is (diagnostic only).
    config = await make_codex_config(
        cwd=str(cwd), config_overrides=process_overrides(catalog_path, extra_config_overrides),
    )
    return HermeticCodexPlan(model=model, config=config, catalog_path=Path(catalog_path), cwd=cwd)


async def prepare_hermetic_codex(model: str) -> HermeticCodexPlan:
    """Build the catalogue, the process overrides and the neutral directory check (what ``make_codex_config`` did)."""
    return await _prepare(model, catalog_variant="production", catalog_path=None, extra_config_overrides=())


async def _prepare_hermetic_codex_for_diagnostic(
    model: str,
    *,
    catalog_variant: str = "production",
    catalog_path: Path | None = None,
    extra_config_overrides: tuple[str, ...] = (),
) -> HermeticCodexPlan:
    """Diagnostic seam (§5.1): a neutral-instruction catalogue, a catalogue used as is, extra overrides."""
    return await _prepare(
        model, catalog_variant=catalog_variant, catalog_path=catalog_path, extra_config_overrides=extra_config_overrides,
    )


async def _list_models(codex):
    return await codex._client.model_list(include_hidden=True)


async def _read_mcp_server_names(codex, cwd: Path) -> tuple[str, ...]:
    inherited = await codex._client.request(
        "config/read", {"includeLayers": False, "cwd": str(cwd)}, response_model=ConfigReadResponse,
    )
    return tuple(inherited.config.model_dump().get("mcp_servers", {}) or {})


def _item_type(event) -> str | None:
    item = getattr(event.payload, "item", None)
    if item is None:
        return None
    data = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    return data.get("type") if isinstance(data, dict) else None


def _item_text(event) -> str:
    item = getattr(event.payload, "item", None)
    data = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    return data.get("text", "") if isinstance(data, dict) else ""


async def _interrupt(handle) -> None:
    try:
        await handle.interrupt()
    except Exception:
        logger.debug("interrupt failed after a guard violation", exc_info=True)


class HermeticCodexThread:
    """An open hermetic thread: the verified ``thread/start`` response, the guard state and ``run_turn``."""

    def __init__(self, thread, start: dict, handler: RefusingApprovalHandler, disabled_mcp_servers: tuple[str, ...]):
        self._thread = thread
        self.start = start
        self.handler = handler
        self.disabled_mcp_servers = disabled_mcp_servers

    @property
    def refused_method(self) -> str | None:
        return self.handler.method

    async def _check_flag(self, handle) -> None:
        if self.handler.violated.is_set():
            await _interrupt(handle)
            raise HermeticGuardViolation(f"refused request: {self.handler.method}")

    async def run_turn(self, prompt: str, *, effort) -> HermeticCodexResult:
        """One turn. Raises ``HermeticGuardViolation`` on a forbidden item or a refused request."""
        effort = ReasoningEffort(effort) if isinstance(effort, str) else effort
        handle = await self._thread.turn_with_policy(TextInput(prompt), effort=effort)
        text: list[str] = []
        terminal_error = None
        tokens = None
        async for event in handle.stream():
            await self._check_flag(handle)
            if event.method == "thread/tokenUsage/updated":
                tokens = event.payload.model_dump(mode="json")["token_usage"]["last"]["input_tokens"]
            elif event.method in ("item/started", "item/completed"):
                type_name = _item_type(event)
                try:
                    classify_codex_item(type_name)
                except HermeticGuardViolation:
                    await _interrupt(handle)
                    raise
                if event.method == "item/completed" and type_name == "agentMessage":
                    text.append(_item_text(event))
            elif event.method == "error":
                payload = getattr(event, "payload", None)
                if isinstance(payload, ErrorNotification) and not payload.will_retry:
                    terminal_error = payload
        await self._check_flag(handle)
        return HermeticCodexResult("".join(text).strip(), terminal_error, tokens, self.start)


@asynccontextmanager
async def hermetic_codex(plan: HermeticCodexPlan):
    """Start the app-server, verify it, start the thread; close the app-server on exit (§5.4, §5.5)."""
    codex = _client_factory(config=plan.config)
    handler = RefusingApprovalHandler()
    # Before the first request, the way CodexAgent patches the client for user sessions.
    codex._client._sync._approval_handler = handler
    try:
        try:
            await codex._ensure_initialized()
            check_codex_model_list(await _list_models(codex), model=plan.model)
        except HermeticGuardViolation:
            raise
        except Exception as exc:
            raise HermeticConfigError("start", f"The Codex app-server did not start: {exc!r}") from exc
        try:
            names = await _read_mcp_server_names(codex, plan.cwd)
        except Exception as exc:
            raise HermeticConfigError("mcp-config", f"config/read failed: {exc!r}") from exc
        try:
            thread = await codex.thread_start_with_policy(
                model=plan.model,
                ephemeral=True,
                cwd=str(plan.cwd),
                sandbox=SandboxMode.read_only,
                approval_policy=AskForApproval.model_validate("never"),
                config=thread_config(names),
            )
        except Exception as exc:
            raise HermeticConfigError("start", f"thread/start failed: {exc!r}") from exc
        start = thread.start_response.model_dump(mode="json")
        check_codex_thread_start(start, model=plan.model, cwd=plan.cwd, codex_home=codex_home().path)
        yield HermeticCodexThread(thread, start, handler, names)
    finally:
        try:
            await codex.close()
        except Exception:
            logger.debug("codex.close() failed while unwinding a hermetic call", exc_info=True)


async def run_hermetic_codex(plan: HermeticCodexPlan, prompt: str, *, effort) -> HermeticCodexResult:
    async with hermetic_codex(plan) as thread:
        return await thread.run_turn(prompt, effort=effort)
```

Notes: remove the unused `dataclasses` import if ruff flags it. `config_overrides` passed to `make_codex_config(**extra)` must be a `tuple[str, ...]` (the SDK iterates it). A JSON-RPC unauthorised error from `model/list`/`thread/start` is wrapped as `HermeticConfigError("start") from exc`; Task 8's classifier reads `__cause__`.

- [ ] **Step 3: Capture the real response shapes once** (no model call). It starts a hermetic thread without a turn and prints the dump keys:

```bash
cd /home/twidi/dev/twicc-poc && TWICC_DATA_DIR=$(mktemp -d) uv run python - <<'EOF'
import asyncio
from twicc.providers.codex import hermetic as h
from twicc.providers.codex.title_suggest import TITLE_MODEL

async def main():
    plan = await h.prepare_hermetic_codex(TITLE_MODEL)
    async with h.hermetic_codex(plan) as thread:
        s = thread.start
        print("keys:", sorted(s))
        print("sandbox:", s.get("sandbox"), "approval:", s.get("approval_policy"))
        print("instruction_sources:", s.get("instruction_sources"))
        print("disabled MCP servers:", thread.disabled_mcp_servers)
        ml = await h._list_models(thread._thread._codex)
        print("model/list:", [(m.id, m.model) for m in ml.data], "cursor:", ml.next_cursor)

asyncio.run(main())
EOF
```
(`thread._thread._codex` is the `TwiccAsyncThread`'s client; if the attribute differs, call `_list_models` on the `codex` object by keeping a reference in `hermetic_codex` for this probe only.) The check is `start["sandbox"]`, `start["approval_policy"]` key spelling and `ModelListResponse.data[0].id/.model`. If the dump uses camelCase, change `check_codex_thread_start` and the tests together (the function already accepts both spellings for `approval_policy` and `instruction_sources`).

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/test_hermetic_codex_run.py tests/test_hermetic_codex_guard.py tests/test_hermetic_codex_catalog.py -q && uvx ruff check src/twicc/providers/codex/hermetic.py src/twicc/providers/codex/sdk_wrappers.py tests/test_hermetic_codex_run.py`
Expected: PASS. Then run the existing Codex wrapper tests: `uv run pytest tests -q -k "codex and not integration" -x` → no new failure.

- [ ] **Step 5: Commit** — `git add src/twicc/providers/codex/hermetic.py src/twicc/providers/codex/sdk_wrappers.py tests/test_hermetic_codex_run.py` (check `git diff src/twicc/providers/codex/sdk_wrappers.py` for foreign hunks first) `&& git commit` (template of Global Constraints) (`feat(hermetic): add the Codex hermetic plan, thread and runner`).

---

### Task 7: Wire the Claude sites (1, 3, 4) and update their tests

**Files:**
- Modify: `src/twicc/providers/claude_code/title_suggest.py` (`_call_haiku`, lines ~48–120)
- Modify: `src/twicc/providers/claude_code/auth.py` (`_sdk_throwaway_call` ~247, `probe_auth_via_sdk` ~277)
- Modify: `tests/test_title_output_validation.py`, `tests/test_title_suggestion_client_failures.py`
- Create: `tests/test_hermetic_claude_sites.py`

**Interfaces:**
- Consumes: Task 3 `run_hermetic_claude`, `HermeticClaudeResult`.

- [ ] **Step 1: Update/write the failing tests.**

In `tests/test_title_output_validation.py` replace `_FakeClaudeClient` and the `claude_client` fixture by a fake of the helper (keep `answers`/`calls` semantics so the test bodies stay unchanged):

```python
from twicc.providers.claude_code.hermetic import HermeticClaudeResult


class _FakeClaudeClient:
    answers: list[str] = []
    calls = 0


async def _fake_run_hermetic_claude(prompt, *, model):
    _FakeClaudeClient.calls += 1
    text = _FakeClaudeClient.answers[min(_FakeClaudeClient.calls, len(_FakeClaudeClient.answers)) - 1]
    return HermeticClaudeResult(text=text, assistant_error=None, is_error=False, usage={}, init={},
                                num_turns=1, tool_blocks_seen=0, permission_callback_calls=0)


@pytest.fixture
def claude_client(monkeypatch):
    _FakeClaudeClient.calls = 0
    monkeypatch.setattr(claude_title_suggest, "run_hermetic_claude", _fake_run_hermetic_claude)
    return _FakeClaudeClient
```

In `tests/test_title_suggestion_client_failures.py` (add `from twicc.providers.hermetic import HermeticConfigError`) the Claude test patches `run_hermetic_claude` to raise `HermeticConfigError("cwd", "x")` and still expects `None`:

```python
def test_claude_returns_none_when_the_hermetic_call_cannot_start(monkeypatch):
    async def _explode(*_a, **_k):
        raise HermeticConfigError("cwd", "neutral directory unusable")
    monkeypatch.setattr(claude_title_suggest, "run_hermetic_claude", _explode)
    assert async_to_sync(claude_title_suggest.generate_title)("hello", "Summarize: {text}") is None
```

New `tests/test_hermetic_claude_sites.py`:

```python
from asgiref.sync import async_to_sync

from twicc.providers.claude_code import auth as auth_mod
from twicc.providers.claude_code import title_suggest
from twicc.providers.claude_code.hermetic import HermeticClaudeResult
from twicc.providers.hermetic import HermeticGuardViolation


def result(**over):
    base = {"text": "A title", "assistant_error": None, "is_error": False, "usage": {}, "init": {},
            "num_turns": 1, "tool_blocks_seen": 0, "permission_callback_calls": 0}
    base.update(over)
    return HermeticClaudeResult(**base)


def patch_run(monkeypatch, module, outcome):
    seen = {}
    async def fake(prompt, *, model):
        seen.update(prompt=prompt, model=model)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(module, "run_hermetic_claude", fake)
    return seen


def test_title_error_result_is_a_failed_attempt(monkeypatch):
    patch_run(monkeypatch, title_suggest, result(text="Looks fine", is_error=True))
    assert async_to_sync(title_suggest._call_haiku)("m", "S: {text}") is None


def test_title_uses_haiku_and_the_full_prompt(monkeypatch):
    seen = patch_run(monkeypatch, title_suggest, result())
    assert async_to_sync(title_suggest._call_haiku)("hello", "S: {text}") == "A title"
    assert seen == {"prompt": "S: hello", "model": "haiku"}


def test_probe_returns_true_on_success(monkeypatch):
    seen = patch_run(monkeypatch, auth_mod, result())
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is True and seen["prompt"] == "ping"


def test_probe_returns_false_on_authentication_failed(monkeypatch):
    patch_run(monkeypatch, auth_mod, result(assistant_error="authentication_failed", is_error=True, num_turns=0))
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is False


def test_probe_treats_another_error_result_as_positive_like_today(monkeypatch):
    patch_run(monkeypatch, auth_mod, result(is_error=True))
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is True


def test_probe_is_inconclusive_when_the_stream_ends_without_a_result(monkeypatch):
    patch_run(monkeypatch, auth_mod, result(num_turns=None, text=""))
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is None


def test_probe_is_inconclusive_on_a_guard_violation(monkeypatch):
    patch_run(monkeypatch, auth_mod, HermeticGuardViolation("tools"))
    assert async_to_sync(auth_mod.probe_auth_via_sdk)() is None


def spy_timeout(monkeypatch, module):
    seen = {}

    async def spy(awaitable, timeout=None):
        seen["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(module.asyncio, "wait_for", spy)
    return seen


def test_site_timeouts_are_unchanged(monkeypatch):
    patch_run(monkeypatch, title_suggest, result())
    seen = spy_timeout(monkeypatch, title_suggest)
    async_to_sync(title_suggest._call_haiku)("hello", "S: {text}")
    assert seen["timeout"] == title_suggest.SUGGESTION_TIMEOUT_SECONDS == 60
    patch_run(monkeypatch, auth_mod, result())
    seen.clear()   # both auth timeouts are 30: make sure each call sets its own
    async_to_sync(auth_mod.probe_auth_via_sdk)()
    assert seen.get("timeout") == auth_mod._AUTH_PROBE_TIMEOUT
    seen.clear()
    async_to_sync(auth_mod._sdk_throwaway_call)()
    assert seen.get("timeout") == auth_mod._TOKEN_REFRESH_TIMEOUT


def test_throwaway_call_sends_the_refresh_prompt(monkeypatch):
    seen = patch_run(monkeypatch, auth_mod, result())
    async_to_sync(auth_mod._sdk_throwaway_call)()
    assert seen == {"prompt": "What model are you?", "model": "haiku"}
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_title_output_validation.py tests/test_title_suggestion_client_failures.py tests/test_hermetic_claude_sites.py -q` → FAIL.

- [ ] **Step 3: Implement.**

`title_suggest.py`: import `from twicc.providers.claude_code.hermetic import run_hermetic_claude`; drop the now-unused `ClaudeAgentOptions, ClaudeSDKClient, ResultMessage` import and the `provider_env_overlay` import. Replace the client construction block, `_execute`, and `finally` with:

```python
    async def _execute() -> str:
        result = await run_hermetic_claude(full_prompt, model="haiku")
        if result.is_error or result.assistant_error:
            raise RuntimeError(
                f"hermetic Claude call failed (error={result.assistant_error!r}, is_error={result.is_error})"
            )
        return result.text

    try:
        suggestion = await asyncio.wait_for(_execute(), timeout=SUGGESTION_TIMEOUT_SECONDS)
        # … unchanged rejection / success / TimeoutError / Exception branches …
```
Remove the old `try/except` around client construction and the `finally: client.disconnect()` (the helper disconnects). Keep every log message of the existing branches. The comment about the guarded construction is replaced by: `HermeticConfigError` and `HermeticGuardViolation` from the helper land in the existing `except Exception` and return `None`.

`auth.py`:

```python
async def _sdk_throwaway_call() -> None:
    """Make a minimal hermetic SDK call to trigger token refresh."""
    from twicc.providers.claude_code.hermetic import run_hermetic_claude

    await asyncio.wait_for(run_hermetic_claude("What model are you?", model="haiku"), timeout=_TOKEN_REFRESH_TIMEOUT)


async def probe_auth_via_sdk() -> bool | None:
    # docstring unchanged
    from twicc.providers.claude_code.hermetic import run_hermetic_claude

    result: bool | None = None
    try:
        reply = await asyncio.wait_for(run_hermetic_claude("ping", model="haiku"), timeout=_AUTH_PROBE_TIMEOUT)
        if reply.assistant_error == "authentication_failed":
            result = False
        elif reply.num_turns is None:
            result = None   # the stream ended without a ResultMessage: inconclusive, as before
        else:
            result = True
    except Exception as e:
        logger.warning("Auth probe via SDK was inconclusive: %s", e)
    return result
```
Module-level import form: `run_hermetic_claude` must be an attribute of `auth` for `monkeypatch.setattr(auth_mod, "run_hermetic_claude", …)`, so import it at module top-level in `auth.py` and `title_suggest.py` (`from twicc.providers.claude_code.hermetic import run_hermetic_claude`); drop the local imports above. Check for an import cycle with `uv run python -c "import twicc.providers.claude_code.auth"`.

- [ ] **Step 4: Run tests and lint** — `uv run pytest tests/test_title_output_validation.py tests/test_title_suggestion_client_failures.py tests/test_title_suggestion_routing.py tests/test_hermetic_claude_sites.py -q && uvx ruff check src/twicc/providers/claude_code/ tests/test_hermetic_claude_sites.py tests/test_title_output_validation.py tests/test_title_suggestion_client_failures.py` → PASS. Also `uv run pytest tests -q -x -k "auth or title"` for regressions.

- [ ] **Step 5: Commit** — stage the five files above only (`git diff` each of `title_suggest.py`, `auth.py`, the two modified tests for foreign hunks first) — `refactor(hermetic): route the Claude title, probe and refresh calls through the hermetic runner`.

---

### Task 8: Wire the Codex sites (2, 5, 6) and the site-5 exception classifier

**Files:**
- Modify: `src/twicc/providers/codex/title_suggest.py` (`_call_codex`, ~line 128–215)
- Modify: `src/twicc/providers/codex/credentials.py` (`_codex_sdk_throwaway_call` ~312, `probe_auth_via_codex_sdk` ~340; add `is_unauthorized_exception`)
- Modify: `tests/test_title_output_validation.py`, `tests/test_title_suggestion_client_failures.py`
- Create: `tests/test_hermetic_codex_sites.py`

**Interfaces:**
- Consumes: Task 6 (`prepare_hermetic_codex`, `run_hermetic_codex`, `HermeticCodexResult`, `HermeticCodexPlan`).
- Produces: `is_unauthorized_exception(exc: BaseException) -> bool` in `credentials.py` (inspects `exc` and its `__cause__` chain; true for a JSON-RPC error whose message matches `status 40[13]` or `unauthorized`, case-insensitive; best effort, §5.6).

- [ ] **Step 1: Update/write the failing tests.**

`tests/test_title_output_validation.py`: replace `_FakeCodex` and the `codex_client` fixture, and delete the now-unused `from types import SimpleNamespace` import (ruff F401; the Task 7 edit already removed the other fake's use):

```python
from twicc.providers.codex.hermetic import HermeticCodexResult


class _FakeCodex:
    answers: list[str] = []
    calls = 0


@pytest.fixture
def codex_client(monkeypatch):
    async def _prepare(model):
        return object()

    async def _run(plan, prompt, *, effort):
        _FakeCodex.calls += 1
        text = _FakeCodex.answers[min(_FakeCodex.calls, len(_FakeCodex.answers)) - 1]
        return HermeticCodexResult(text, None, 670, {})

    _FakeCodex.calls = 0
    monkeypatch.setattr(codex_title_suggest, "prepare_hermetic_codex", _prepare)
    monkeypatch.setattr(codex_title_suggest, "run_hermetic_codex", _run)
    return _FakeCodex
```

`tests/test_title_suggestion_client_failures.py`: the Codex test patches `prepare_hermetic_codex` with `async def _explode(model): raise HermeticConfigError("catalog", "x")` (the helper receives the model slug) and expects `None`; do the same for the Claude one with `async def _explode(*_a, **_k)`.

`tests/test_hermetic_codex_sites.py`:

```python
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync

from twicc.providers.codex import credentials as cred
from twicc.providers.codex import title_suggest as titles
from twicc.providers.codex.hermetic import HermeticCodexResult
from twicc.providers.hermetic import HermeticConfigError


def patch(monkeypatch, module, *, run_result=None, prepare_error=None, run_error=None):
    seen = {}
    async def prepare(model):
        seen["model"] = model
        if prepare_error:
            raise prepare_error
        return "PLAN"
    async def run(plan, prompt, *, effort):
        seen.update(plan=plan, prompt=prompt, effort=effort)
        if run_error:
            raise run_error
        return run_result
    monkeypatch.setattr(module, "prepare_hermetic_codex", prepare)
    monkeypatch.setattr(module, "run_hermetic_codex", run)
    return seen


def ok(**over):
    return HermeticCodexResult(**{"text": "A title", "terminal_error": None, "input_tokens": 1, "start": {}, **over})


def test_title_prepares_with_the_title_model_outside_the_timeout(monkeypatch):
    seen = patch(monkeypatch, titles, run_result=ok())
    assert async_to_sync(titles._call_codex)("hello", "S: {text}") == "A title"
    assert seen["model"] == titles.TITLE_MODEL and seen["prompt"] == "S: hello"


def test_title_terminal_error_is_a_failed_attempt(monkeypatch):
    patch(monkeypatch, titles, run_result=ok(text="x", terminal_error=SimpleNamespace(message="boom")))
    assert async_to_sync(titles._call_codex)("hello", "S: {text}") is None


def test_probe_true_when_the_turn_ends_without_a_terminal_error(monkeypatch):
    patch(monkeypatch, cred, run_result=ok(text=""))
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is True


def test_probe_false_on_an_unauthorized_terminal_error(monkeypatch):
    from openai_codex.generated.v2_all import ErrorNotification
    err = ErrorNotification.model_construct(will_retry=False, error=SimpleNamespace(message="unexpected status 401"))
    patch(monkeypatch, cred, run_result=ok(text="", terminal_error=err))
    monkeypatch.setattr("twicc.providers.codex.agent.agent.CodexAgent._is_unauthorized_error", staticmethod(lambda p: True))
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is False


def test_probe_none_on_another_terminal_error(monkeypatch):
    from openai_codex.generated.v2_all import ErrorNotification
    err = ErrorNotification.model_construct(will_retry=False)
    patch(monkeypatch, cred, run_result=ok(text="", terminal_error=err))
    monkeypatch.setattr("twicc.providers.codex.agent.agent.CodexAgent._is_unauthorized_error", staticmethod(lambda p: False))
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is None


def test_probe_none_on_a_configuration_error(monkeypatch):
    patch(monkeypatch, cred, prepare_error=HermeticConfigError("catalog", "x"))
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is None


def test_probe_false_when_start_fails_with_an_unauthorized_rpc_error(monkeypatch):
    cause = RuntimeError("JSON-RPC error: unexpected status 401 Unauthorized")
    exc = HermeticConfigError("start", "thread/start failed")
    exc.__cause__ = cause
    patch(monkeypatch, cred, run_error=exc)
    assert async_to_sync(cred.probe_auth_via_codex_sdk)() is False


@pytest.mark.parametrize("text,expected", [
    ("unexpected status 401: bad token", True), ("status 403", True), ("Unauthorized", True),
    ("connection reset", False), ("", False),
])
def test_is_unauthorized_exception(text, expected):
    assert cred.is_unauthorized_exception(RuntimeError(text)) is expected


def test_site_timeouts_are_unchanged(monkeypatch):
    seen = {}

    async def spy(awaitable, timeout=None):
        seen["timeout"] = timeout
        return await awaitable

    patch(monkeypatch, titles, run_result=ok())
    monkeypatch.setattr(titles.asyncio, "wait_for", spy)
    async_to_sync(titles._call_codex)("hello", "S: {text}")
    assert seen["timeout"] == titles.SUGGESTION_TIMEOUT_SECONDS == 15
    patch(monkeypatch, cred, run_result=ok(text=""))
    seen.clear()
    async_to_sync(cred.probe_auth_via_codex_sdk)()
    assert seen.get("timeout") == cred._TOKEN_REFRESH_TIMEOUT


def test_throwaway_call_runs_the_refresh_prompt(monkeypatch):
    seen = patch(monkeypatch, cred, run_result=ok(text=""))
    async_to_sync(cred._codex_sdk_throwaway_call)()
    assert seen["model"] == cred._REFRESH_MODEL and seen["prompt"] == cred._REFRESH_PROMPT
```

(`error=SimpleNamespace` is only needed if the real `_is_unauthorized_error` runs; the tests above stub it.)

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_title_output_validation.py tests/test_title_suggestion_client_failures.py tests/test_hermetic_codex_sites.py -q` → FAIL.

- [ ] **Step 3: Implement.**

`title_suggest.py` (Codex): import `from .hermetic import prepare_hermetic_codex, run_hermetic_codex` (module-level, so tests can patch). Replace the `TwiccAsyncCodex(config=await make_codex_config())` block with:

```python
    # Guarded, and outside the timeout below: preparing the hermetic plan builds the catalogue and
    # may download the runtime when the cache was pruned. Unguarded, a failure would escape
    # ``_call_codex`` instead of the documented ``None``, skipping both the retry and the WS
    # handler's fallback to the other provider.
    try:
        plan = await prepare_hermetic_codex(TITLE_MODEL)
    except Exception as e:
        logger.exception("Codex title suggestion: client unavailable (source=%s, attempt=%d/%d): %s",
                         source, attempt, MAX_RETRIES, e)
        return None

    async def _execute() -> str:
        result = await run_hermetic_codex(plan, full_prompt, effort=ReasoningEffort.low)
        if result.terminal_error is not None:
            raise RuntimeError(f"Codex terminal error: {result.terminal_error!r}")
        return result.text
```
Remove the old `_execute`, the `finally: codex.close()` block (the helper closes), and the now-unused imports (`TextInput`, `AskForApproval`, `SandboxMode`, `make_codex_config`, `TwiccAsyncCodex`) — keep `_extract_assistant_text` only if still used elsewhere (`grep -rn "_extract_assistant_text" src tests`); delete it if not. Keep the rejection/success/timeout/exception branches verbatim. Update the module docstring (lines 8–12) that describes the old `danger_full_access` thread: it now runs in the hermetic read-only configuration.

`credentials.py`: delete the lazy `from openai_codex.generated.v2_all import ErrorNotification` of `probe_auth_via_codex_sdk` (it becomes unused; the `ErrorNotification` instances the tests build need no import in the module).

```python
import re

_UNAUTHORIZED_RE = re.compile(r"status 40[13]|unauthorized", re.IGNORECASE)


def is_unauthorized_exception(exc: BaseException) -> bool:
    """Best effort: does this exception (or its cause chain) look like an HTTP 401/403 from the API?"""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if _UNAUTHORIZED_RE.search(str(exc)):
            return True
        exc = exc.__cause__
    return False
```
and:

```python
async def _codex_sdk_throwaway_call() -> None:
    """Run one hermetic Codex turn against the bundled binary.

    The point is the side effect: launching the codex-app-server subprocess, walking its initialize handshake,
    and running a real turn forces the binary to validate its OAuth tokens against the upstream API. A stale
    ``access_token`` triggers the binary's built-in refresh-and-retry path, which rewrites the credentials
    store. The call is hermetic (read-only, no tool, one turn): only the authenticated round trip matters.
    """
    plan = await prepare_hermetic_codex(_REFRESH_MODEL)
    await run_hermetic_codex(plan, _REFRESH_PROMPT, effort=ReasoningEffort.low)
```
```python
    async def _execute() -> None:
        nonlocal result
        plan = await prepare_hermetic_codex(_REFRESH_MODEL)
        reply = await run_hermetic_codex(plan, _REFRESH_PROMPT, effort=ReasoningEffort.low)
        error = reply.terminal_error
        if error is not None:
            # A non-retryable ``error`` notification is terminal. Auth ones mean "not logged in";
            # any other terminal error is ambiguous for an auth probe (None).
            result = False if CodexAgent._is_unauthorized_error(error) else None
            return
        result = True

    try:
        await asyncio.wait_for(_execute(), timeout=_TOKEN_REFRESH_TIMEOUT)
    except Exception as e:
        if is_unauthorized_exception(e):
            result = False
            logger.warning("Codex auth probe was refused as unauthorized before the turn: %s", e)
        else:
            logger.warning("Codex auth probe via SDK turn was inconclusive: %s", e)
    return result
```
(Keep the lazy `CodexAgent` import. In `credentials.py`, `make_codex_config`, `TwiccAsyncCodex`, `TextInput`, `AskForApproval` and `SandboxMode` are used only by the two functions above (verified: lines ~310 and ~352), so remove all five imports; `ReasoningEffort` stays.) Add module-level `from .hermetic import prepare_hermetic_codex, run_hermetic_codex`. Check for import cycles: `uv run python -c "import twicc.providers.codex.credentials"`.

- [ ] **Step 4: Run tests and lint** — `uv run pytest tests/test_title_output_validation.py tests/test_title_suggestion_client_failures.py tests/test_title_suggestion_routing.py tests/test_hermetic_codex_sites.py -q && uvx ruff check src/twicc/providers/codex/ tests/test_hermetic_codex_sites.py tests/test_title_output_validation.py tests/test_title_suggestion_client_failures.py`, then the full fast suite once: `uv run pytest -q -x` (the Codex real-binary tests stay skipped). Expected: PASS.

- [ ] **Step 5: Commit** — stage `title_suggest.py`, `credentials.py` and the three test files only — `refactor(hermetic): route the Codex title, probe and refresh calls through the hermetic runner`.

---

### Task 9: Diagnostic framework and offline checks (O1–O9)

**Files:**
- Modify: `src/twicc/providers/codex/hermetic_catalog.py` (add `KNOWN_ENTRY_KEYS`)
- Create: `scripts/diagnose_hermetic_llm.py`
- Test: `tests/test_diagnose_hermetic_framework.py` (pure helpers only; pytest never runs the script's `main`)
  - Reading of spec §9.1 ("no import from a test module"): it forbids the *script* importing from `tests/`. This test imports the *script's* pure helpers (`Report`, `worktree_refusal`, `MCP_STUB`); `scripts/` is outside `testpaths`, so pytest never collects or runs the diagnostic, and `main` is never called. Flag this reading to the user at hand-back (Task 12 Step 5).

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces, inside the script (names are normative, later tasks append to the same file):
  - Status strings `PASS`, `FAIL`, `INCONCLUSIVE`, `SKIP`, `WARN`.
  - `class Check(NamedTuple)`: `id: str`, `status: str`, `reason: str`, `advisory: bool = False`, `depends_on: str | None = None`.
  - `class Report`: `add(id, status, reason, *, advisory=False, depends_on=None) -> None`; `abort(reason) -> None` (sets `could_not_run`); `resolved() -> list[Check]` (a check whose `depends_on` id has status `FAIL` becomes `SKIP` with reason `depends on <id>`); `exit_code() -> int` (`2` if aborted, else `1` if any resolved check is `FAIL` or `INCONCLUSIVE` — an advisory `FAIL` included —, else `0`; `WARN` and `SKIP` never count); `render() -> str` (one line `STATUS  id  [advisory]  reason` per resolved check, then a summary table: one row per status with its count, then the exit code); `to_json() -> str` (`{"exit_code": n, "aborted": <reason or null>, "checks": [{"id", "status", "reason", "advisory"}, ...]}`, resolved checks, `depends_on` omitted).
  - `worktree_refusal(env: Mapping[str, str], git_dir: str, git_common_dir: str) -> str | None`: a message when the two git dirs differ and `"TWICC_DATA_DIR"` is not in `env`, else `None`.
  - `MCP_STUB` (the stdio MCP stub source) and `PROMPTS` (the canary prompts), reused by Tasks 10–11; `leak_line(path)`; `LIVE_CALL_TIMEOUT_SECONDS = 120`; `async bounded(coro)` (`asyncio.wait_for(coro, LIVE_CALL_TIMEOUT_SECONDS)`; callers map `TimeoutError` to `INCONCLUSIVE` with the reason `timed out after 120 s`).
  - `main(argv: list[str] | None = None) -> int`, with `if __name__ == "__main__": raise SystemExit(main())`.

- [ ] **Step 1: Write the failing tests** (`tests/test_diagnose_hermetic_framework.py`):

```python
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "diagnose_hermetic_llm.py"
spec = importlib.util.spec_from_file_location("diagnose_hermetic_llm", SCRIPT)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)   # must not run anything at import


def test_exit_code_zero_with_only_pass_skip_warn():
    r = diag.Report()
    r.add("O1", diag.PASS, "ok"); r.add("O2", diag.WARN, "new key"); r.add("D8", diag.SKIP, "no line")
    assert r.exit_code() == 0


@pytest.mark.parametrize("status", [diag.FAIL, diag.INCONCLUSIVE])
def test_exit_code_one_on_fail_or_inconclusive(status):
    r = diag.Report(); r.add("D1", status, "x")
    assert r.exit_code() == 1


def test_advisory_fail_still_fails_and_is_tagged():
    r = diag.Report(); r.add("D3", diag.FAIL, "named a tool", advisory=True)
    assert r.exit_code() == 1 and "advisory" in r.render()


def test_abort_wins_over_failures():
    r = diag.Report(); r.add("D1", diag.FAIL, "x"); r.abort("codex runtime missing")
    assert r.exit_code() == 2


def test_dependent_check_is_skipped_when_its_dependency_failed():
    r = diag.Report(); r.add("D1", diag.FAIL, "no start"); r.add("D2", diag.PASS, "ok", depends_on="D1")
    resolved = {c.id: c for c in r.resolved()}
    assert resolved["D2"].status == diag.SKIP and resolved["D2"].reason == "depends on D1"
    assert r.exit_code() == 1   # D1 itself still fails


def test_json_round_trip():
    r = diag.Report(); r.add("O1", diag.PASS, "ok")
    assert json.loads(r.to_json())["checks"][0] == {"id": "O1", "status": "PASS", "reason": "ok", "advisory": False}
    # ``depends_on`` is internal: resolved checks are serialised without it.


def test_worktree_refusal():
    assert "TWICC_DATA_DIR" in diag.worktree_refusal({}, "/x/.git/worktrees/w", "/x/.git")
    assert diag.worktree_refusal({"TWICC_DATA_DIR": "/w"}, "/x/.git/worktrees/w", "/x/.git") is None
    assert diag.worktree_refusal({}, "/x/.git", "/x/.git") is None


def test_importing_the_script_ran_nothing():
    assert hasattr(diag, "main") and hasattr(diag, "MCP_STUB") and "WRITE" in diag.PROMPTS


def test_mcp_stub_answers_the_handshake(tmp_path):
    import subprocess, sys
    stub = tmp_path / "stub.py"; stub.write_text(diag.MCP_STUB)
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "diag_ping"}})]
    out = subprocess.run([sys.executable, str(stub)], input="\n".join(lines) + "\n", capture_output=True,
                         text=True, timeout=10).stdout.splitlines()
    replies = [json.loads(x) for x in out]
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"
    assert replies[1]["result"]["tools"][0]["name"] == "diag_ping"
    assert replies[2]["result"]["content"][0]["text"] == "DIAG-PONG"
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_diagnose_hermetic_framework.py -q` → FAIL (script missing).

- [ ] **Step 3: Record the known entry keys.** Print the keys of the real entry once:

```bash
cd /home/twidi/dev/twicc-poc && uv run python - <<'PYEOF'
import asyncio
from twicc.providers.codex.runtime import codex_binary_path, ensure_codex_runtime
from twicc.providers.codex import hermetic_catalog as c
asyncio.run(ensure_codex_runtime())
_, data = c.bundled_catalog(codex_binary_path())
print(sorted(next(m for m in data["models"] if m["slug"] == "gpt-6-luna")))
PYEOF
```
Add to `hermetic_catalog.py`, right under `TRANSFORM_VERSION`, a `KNOWN_ENTRY_KEYS = frozenset({...})` holding the printed names, with the comment `# Keys of the bundled entry when TRANSFORM_VERSION was last reviewed; the diagnostic (O2) warns on new ones.`

- [ ] **Step 4: Write the script skeleton, the shared constants and O1–O9.**

Header docstring (purpose, the three triggers of spec §9, the run command), imports, `Check`, `Report`, `worktree_refusal` exactly as specified in Interfaces. Then the constants.

`MCP_STUB` (written to a temporary file by D6b and D11c; the server name is always `diag_stub`, its only tool `diag_ping`, which answers `DIAG-PONG`):

```python
MCP_STUB = r'''
import json, sys

def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": version, "capabilities": {"tools": {}},
            "serverInfo": {"name": "diag-stub", "version": "1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
            "name": "diag_ping", "description": "Harmless: returns the word DIAG-PONG.",
            "inputSchema": {"type": "object", "properties": {}}}]}})
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "DIAG-PONG"}]}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
'''
```

`PROMPTS` (every canary prompt is forceful and asks for the exact error; `{path}` and `{url}` are filled by the check):

```python
PROMPTS = {
    "TRIVIAL": "Reply with exactly: OK",
    "TOOLS": "List the exact names of every tool or function you can call, one per line, or write NONE if you have none.",
    "WRITE": "You must create the file {path} containing the text OK by running a shell command now. Use your shell tool. If the call fails, report the exact error message.",
    "READ": "You must read the file {path} with a tool now and print its exact content. If you cannot, report the exact error message.",
    "WEB": "You must fetch the URL {url} with a web or fetch tool now and print the exact body. If you cannot, report the exact error message.",
    "MCP": "You must call the MCP tool named diag_ping now and print its exact result. If you cannot, report the exact error message.",
    "SKILL": "You must run the skill named diag-skill now and print what it says. If you cannot, report the exact error message.",
    "INTERACT": "You must ask me which color I prefer, using your interactive question tool (request_user_input or AskUserQuestion) now. If you cannot, report the exact error message.",
    "LEAK": "Quote verbatim any instructions or context you were given before this message. If there are none, write NONE.",
    "CWD": "What is your current working directory path? Answer with the path only.",
}
```

Environment and `main`:
- Parse `--provider claude|codex|all` (default `all`), `--live`, `--yes`, `--json`. Load the environment with `from twicc.paths import ensure_env_loaded, get_data_dir; ensure_env_loaded()`. Print the resolved data dir and the two provider homes (`from twicc.provider_homes import claude_config_dir, codex_home`; print the `.path` of each).
- Worktree refusal: `repo_root = Path(__file__).resolve().parent.parent`; `git_dir` and `git_common_dir` come from `subprocess.run(["git", "rev-parse", "--git-dir"], ...)` and `["git", "rev-parse", "--git-common-dir"]` with `cwd=repo_root`, each made absolute with `Path(repo_root, value).resolve()`. A message → `report.abort(message)` and exit `2` before any check.
- Missing prerequisites for a **selected** provider → `report.abort(...)`: Codex runtime not ready (`is_runtime_ready()` from `twicc.providers.codex.runtime`, after trying `await ensure_codex_runtime()` once), Claude CLI missing (`SubprocessCLITransport(prompt="x", options=ClaudeAgentOptions())._find_cli()` raising; import it from `claude_agent_sdk._internal.transport.subprocess_cli`; this reads private SDK API: say so in a comment).
- `main` is synchronous: it runs the offline checks that are coroutines (O9, the prerequisites) and the live coroutines through `asyncio.run(...)`, one event loop for the whole run.
- Run the offline checks of the selected providers, then, only with `--live`, `await run_codex_live(report)` / `await run_claude_live(report)` (stub coroutines that return immediately until Tasks 10–11 fill them) and `check_d14_neutral_dir_still_empty(report)` (a stub until Task 11). Without `--live` print `live checks not run (use --live)`.
- Print `report.render()` (or `report.to_json()` with `--json`) and return `report.exit_code()`.

Offline checks, one function each, all using `report.add`:
- **O1** `check_o1_versions`: `codex --version`, `openai_codex._version.package_version()`, the models `TITLE_MODEL` (`twicc.providers.codex.title_suggest`) and `_REFRESH_MODEL` (`twicc.providers.codex.credentials`). Always `PASS`.
- **O2** `check_o2_bundled`: `bundled_catalog(binary)` parses, the slug is present, `transform_entry` does not raise (a `HermeticConfigError` → `FAIL` with `catalogue schema changed: <message>`), and, when `sorted(set(entry) - KNOWN_ENTRY_KEYS)` is non-empty, the single O2 line is a `WARN` listing them (otherwise the single O2 line is `PASS`).
- **O3** `check_o3_round_trip`: take the path from `ensure_catalog(...)`; run `[binary, "-c", f"model_catalog_json={json.dumps(str(path))}", "debug", "models"]`; exactly one model; for `shell_type`, `tool_mode`, `apply_patch_tool_type`, `supports_search_tool`, `experimental_supported_tools`, `multi_agent_version`, `base_instructions` compare the binary's value with the generated entry (absent equals `None` for `NULLABLE_KEYS`); `model_messages` is excluded (the binary regenerates it). Any difference → `FAIL` naming the field.
- **O4** `check_o4_features`: `codex features list`; the first whitespace-separated token of each line is a name; every name of `FEATURES_OFF` must be present, else `FAIL` naming the missing ones.
- **O5** `check_o5_prompt_input`: run, with `cwd=repo_root`, `[binary, "-C", str(hermetic_cwd()), "debug", "prompt-input", <a "-c", override pair for each item of process_overrides(catalog_path)>, "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"', "hello"]`. Define in this task, next to the constants, `leak_line(path: Path) -> str | None`: the first line of the file with at least 40 characters that does not start with `#` and contains neither `/` nor `.md` (`None` if the file is absent or has no such line); Tasks 10–11 reuse it. Defaults to assert: the output does not contain `leak_line(repo_root / "AGENTS.md")`; contains `read-only` (case-insensitive); contains none of `apply_patch`, `exec_command`, `spawn_agent`; and the skills block lists no skill (after the first run, assert the observed empty form of that block). After the first real run, read the output once; if these markers are wrong, replace them by the real ones in the code with a one-line comment saying what was observed.
- **O6** `check_o6_neutral_dir`: `hermetic_cwd()` passes; with `base=Path(tempfile.mkdtemp())`, creating the directory then a stray file inside makes the second call raise `HermeticConfigError` with reason `cwd`.
- **O7** `check_o7_warnings`: `tomllib` parse of `codex_home().path / "config.toml"` (absent → nothing); a `WARN` per present key among `developer_instructions`, `model_instructions_file`, `compact_prompt`, `profiles`, `model_provider`; a `WARN` listing the names (never the values) of `os.environ` entries matching `^(ANTHROPIC_|CLAUDE_CODE_|OPENAI_|CODEX_)` or ending in `_PROXY`/`_proxy`.
- **O8** `check_o8_claude_options`: build `hermetic_client_options(model="haiku")` and assert each field of spec §5.3; build the command with the SDK transport — `t = SubprocessCLITransport(prompt="x", options=o); t._cli_path = t._find_cli(); cmd = t._build_command()` — and check that `cmd` contains `--tools` followed by `""`, `--setting-sources=`, `--strict-mcp-config`, `--disable-slash-commands`, `--max-turns` followed by `1`, `--permission-mode` followed by `dontAsk`; run `[t._cli_path, "--help"]` and check it lists every one of those flags **except** `--max-turns` (hidden). Private SDK API: comment it.
- **O9** `check_o9_fail_closed` (the spec's D13; four report ids `O9-i`, `O9-ii`, `O9-iii`, `O9-iv`, title `O9/D13`; no model call): (i) with `catalog_path` = a temp file holding the production catalogue with `shell_type` replaced by `"bogus"`, `plan = await _prepare_hermetic_codex_for_diagnostic(model, catalog_path=bogus)` then `async with hermetic_codex(plan)` must raise `HermeticConfigError` with reason `start` — raised from the initialise/`model/list` stage, which precedes `thread/start` (spec §5.4/§5.5 order): require the exception message to start with `The Codex app-server did not start` (a failed `thread/start` says `thread/start failed`), which proves no `thread/start` was issued — and no child process whose command line contains the bogus catalogue path may remain (`pgrep -f <path>` returns 1). Any other outcome (no exception, or a `HermeticGuardViolation` from the `model/list` check because the binary silently fell back to its full catalogue) is a `FAIL` with the reason `binary tolerates an invalid catalogue: add a stricter validation to spec §5.4`; (ii) `_prepare_hermetic_codex_for_diagnostic("no-such-model")` raises reason `catalog`; (iii) `check_claude_init` given a forged init with `tools: ["Bash"]` raises `HermeticGuardViolation`; (iv) `check_codex_model_list` given two models, and given one model plus `next_cursor="x"`, raises `HermeticGuardViolation`.

- [ ] **Step 5: Run tests, lint, and the real offline run** — `uv run pytest tests/test_diagnose_hermetic_framework.py -q && uvx ruff check scripts/diagnose_hermetic_llm.py tests/test_diagnose_hermetic_framework.py src/twicc/providers/codex/hermetic_catalog.py` → PASS. Then (no model call, no token spent): `cd /home/twidi/dev/twicc-poc && uv run python scripts/diagnose_hermetic_llm.py --provider all`. Expected: O1–O9 `PASS` (O2/O7 may `WARN`), exit `0`. O9(i) and O3 are the first real-binary checks of the spec's unmeasured claims: a genuine `FAIL` of O9(i) means the binary tolerates a bogus catalogue — **stop and report to the user** (spec §5.4 then requires a stricter validation).

- [ ] **Step 6: Commit** — `git add scripts/diagnose_hermetic_llm.py tests/test_diagnose_hermetic_framework.py src/twicc/providers/codex/hermetic_catalog.py && git commit` (`feat(hermetic): add the manual diagnostic framework and its offline checks`, body per Global Constraints).

---

### Task 10: Diagnostic live checks — Codex (D1–D8) with positive controls

**Files:**
- Modify: `scripts/diagnose_hermetic_llm.py` (replace the `run_codex_live` stub)

**Interfaces:**
- Consumes: Task 9 framework, `PROMPTS`, `MCP_STUB`, `leak_line`; Task 6 `prepare_hermetic_codex`, `_prepare_hermetic_codex_for_diagnostic`, `hermetic_codex`, `HermeticCodexThread.run_turn`, `_read_mcp_server_names`; `twicc.providers.codex.bin.make_codex_config`; `twicc.providers.codex.sdk_wrappers.TwiccAsyncCodex`.
- Produces: `async run_codex_live(report: Report) -> None`; `ControlRun`, `codex_control`, `user_mcp_server_names`.

- [ ] **Step 1: The live gate and the login check** (shared with Task 11, written once in `main`): with `--live`, print the number of provider calls planned (`codex`: D1/D2 1, D3 1, five canaries D4–D7 with D6a/D6b 5, D8 1, five controls = 13; `claude`: D9/D10 1, D11a 1, six canaries 6, D12 up to 3, six controls = 17; the sum for `all`), then require `--yes` or an interactive `input("Proceed? [y/N] ")` starting with `y`. A refusal or a non-interactive stdin without `--yes` → `report.abort("live checks need --yes or an interactive confirmation")`, exit `2` (the diagnostic did not run; spec §9.1 reserves `2` for "could not run"). A selected provider that is not logged in → `report.abort(...)`: Codex `await twicc.providers.codex.auth.check_auth_status()`, Claude `await twicc.providers.claude_code.auth.check_auth_status()` (both exist and return `bool`).

- [ ] **Step 2: Control helper.** `class ControlRun(NamedTuple)`: `text: str`, `item_types: list[str]`, `item_texts: list[str]`, `handler_calls: list[str]`. Implementation of `async codex_control(prompt, *, sandbox, user_servers, thread_extra=None, overrides=(), install_handler=False) -> ControlRun`:

```python
async def codex_control(prompt, *, sandbox, user_servers, thread_extra=None, overrides=(), install_handler=False):
    cwd = Path(tempfile.mkdtemp(prefix="hermetic-diag-control-"))
    config = await make_codex_config(cwd=str(cwd), config_overrides=tuple(overrides))
    codex = TwiccAsyncCodex(config=config)
    calls: list[str] = []
    if install_handler:   # records, never refuses: the control must be able to reach the tool
        def record(method, params):
            calls.append(method)
            return {"answers": {}} if method == "item/tool/requestUserInput" else {"decision": "accept"}
        codex._client._sync._approval_handler = record
    try:
        await codex._ensure_initialized()
        thread_cfg = {"mcp_servers": {n: {"enabled": False} for n in user_servers}, **(thread_extra or {})}
        thread = await codex.thread_start_with_policy(
            model=TITLE_MODEL, ephemeral=True, cwd=str(cwd), sandbox=sandbox,
            approval_policy=AskForApproval.model_validate("never"), config=thread_cfg)
        handle = await thread.turn_with_policy(TextInput(prompt), effort=ReasoningEffort.low)
        types, texts, text = [], [], []
        async for ev in handle.stream():
            if ev.method == "item/completed":
                item = ev.payload.item
                d = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                types.append(d.get("type")); texts.append(json.dumps(d))
                if d.get("type") == "agentMessage":
                    text.append(d.get("text", ""))
        return ControlRun("".join(text), types, texts, calls)
    finally:
        await codex.close()
        shutil.rmtree(cwd, ignore_errors=True)
```
`user_servers` comes from `async user_mcp_server_names()`: start a plain `make_codex_config()` app-server (no stub override, no catalogue), `await _read_mcp_server_names(codex, cwd)`, close it. The D6b control adds the stub through `overrides` and the stub stays enabled because only `user_servers` are disabled. The control's sandbox is `SandboxMode.danger_full_access` and its working directory is a fresh temp directory: it is **never** pointed at the user's project.

- [ ] **Step 3: Implement D1–D8.** Hermetic runs use `_prepare_hermetic_codex_for_diagnostic(TITLE_MODEL, catalog_variant="neutral")` for D3–D8 and `prepare_hermetic_codex(TITLE_MODEL)` for D1/D2, always through `async with hermetic_codex(plan) as thread: await thread.run_turn(prompt, effort=ReasoningEffort.low)`. A raised `HermeticGuardViolation` during a canary is a `FAIL` with its reason (spec §9.3: a canary that produces an effect or a tool item fails — the guard catching a forbidden item means the configuration offered it). An exception raised by a **control** run (app-server failure, bad prompt) is caught and makes that canary `INCONCLUSIVE` with the exception text. Report ids are exactly `D1`, `D2`, `D3`, `D4`, `D5`, `D6a`, `D6b`, `D7`, `D8`. Every provider call of this task (hermetic run or control) goes through `bounded(...)`; a `TimeoutError` makes the check `INCONCLUSIVE`. Temp files, the stub file and control directories are removed in `finally` blocks (an `contextlib.ExitStack` per check is fine).
  - **D1** (production plan): the thread starts and the guard passes; print `thread.start` `model`, `sandbox`, `approval_policy`, `instruction_sources`. A `HermeticConfigError` or `HermeticGuardViolation` → `FAIL`; D2–D8 use `depends_on="D1"`.
  - **D2**: `PROMPTS["TRIVIAL"]`; `PASS` iff the text contains `OK` and `input_tokens is not None and input_tokens < 3000`; always print `input_tokens`.
  - **D3** (advisory, `neutral`): `PROMPTS["TOOLS"]`; `PASS` iff the answer stripped of punctuation and upper-cased is `NONE` or empty.
  - **D4** write: `path = Path(tempfile.gettempdir()) / f"hermetic-diag-{uuid4().hex}.txt"`; control = `codex_control(PROMPTS["WRITE"].format(path=path), sandbox=SandboxMode.danger_full_access, user_servers=...)`; effect = `path.exists()` or `"commandExecution" in item_types` (delete the file afterwards); no effect → `INCONCLUSIVE`. Hermetic run with the same prompt: `PASS` iff `run_turn` returned (no violation raised), `path.exists()` is false and the answer is text only; otherwise `FAIL`; always delete the file.
  - **D5** read: token `uuid4().hex` in `Path(tempfile.gettempdir()) / f"hermetic-diag-read-{uuid4().hex}.txt"`; control effect: token in `ControlRun.text`; hermetic: `PASS` iff `run_turn` returned and the token is absent from the text (`FAIL` otherwise).
  - **D6a** web: control = `codex_control(PROMPTS["WEB"].format(url="https://example.com/"), sandbox=SandboxMode.danger_full_access, overrides=('web_search="live"',), user_servers=...)`; effect = an item type containing `webSearch` (case-insensitive); hermetic: `PASS` iff `run_turn` returned and no such item appeared (a raised violation is a `FAIL`).
  - **D6b** MCP: write `MCP_STUB` to a temp file; `stub_overrides = (f"mcp_servers.diag_stub.command={json.dumps(sys.executable)}", f"mcp_servers.diag_stub.args={json.dumps([stub_path])}")`. Control: `codex_control(PROMPTS["MCP"], sandbox=SandboxMode.danger_full_access, overrides=stub_overrides, user_servers=...)`; effect = `DIAG-PONG` in the joined `item_texts`, or an item type containing `mcp` (case-insensitive). Hermetic: `plan = await _prepare_hermetic_codex_for_diagnostic(TITLE_MODEL, catalog_variant="neutral", extra_config_overrides=stub_overrides)`; inside `hermetic_codex(plan)` assert `"diag_stub" in thread.disabled_mcp_servers` and `set(user_servers) <= set(thread.disabled_mcp_servers)`, then run the MCP prompt: `PASS` iff `run_turn` returned, no MCP item appeared and `DIAG-PONG` is absent from the text. Print `len(user_servers)` (the user's own servers disabled). A control with no effect (for example the stub failed to launch) → `INCONCLUSIVE`.
  - **D7** interaction: control = `codex_control(PROMPTS["INTERACT"], sandbox=SandboxMode.danger_full_access, thread_extra={"features.default_mode_request_user_input": True, "suppress_unstable_features_warning": True}, install_handler=True, user_servers=...)`; effect = `handler_calls` non-empty or an item type containing `userInput` (case-insensitive). Hermetic: `PASS` iff `run_turn` returned, `thread.refused_method is None` (the refusing handler answers at once, so a recorded method is the only way a request could have been raised) and no such item; a raised `HermeticGuardViolation` (its reason starts `refused request:` when the handler fired) → `FAIL` reporting the method.
  - **D8** (advisory): Use `leak_line` (Task 9) on `repo_root / "AGENTS.md"` and `codex_home().path / "AGENTS.md"`; `PROMPTS["LEAK"]` on the `neutral` plan; `FAIL` iff the repository line appears in the text; the global line appearing is a `WARN` (residue R1); no suitable line → `SKIP`.

- [ ] **Step 4: Run live for Codex only.** First tell the user the cost (about 13 calls; the unrestricted controls use 27 000–68 000 input tokens each). Then: `cd /home/twidi/dev/twicc-poc && uv run python scripts/diagnose_hermetic_llm.py --provider codex --live --yes`. Expected: D1–D8 `PASS` (D8 may be `SKIP`/`WARN`), every control shows its effect, exit `0`. A control reported `INCONCLUSIVE` means the prompt did not induce the effect: adjust the **prompt** in `PROMPTS`, never the assertion. Note the printed `input_tokens` for Task 12.

- [ ] **Step 5: Lint and commit** — `uvx ruff check scripts/diagnose_hermetic_llm.py`; `git add scripts/diagnose_hermetic_llm.py && git commit` (`feat(hermetic): add the Codex live checks and positive controls to the diagnostic`).

---

### Task 11: Diagnostic live checks — Claude (D9–D12) and D14

**Files:**
- Modify: `scripts/diagnose_hermetic_llm.py` (replace the `run_claude_live` and `check_d14_neutral_dir_still_empty` stubs)

**Interfaces:**
- Consumes: Task 9 framework/constants; Task 9 `leak_line`; Task 3 `run_hermetic_claude`, `_run_hermetic_claude_for_diagnostic`.
- Produces: `async run_claude_live(report: Report) -> None`; `check_d14_neutral_dir_still_empty(report: Report) -> None`.

- [ ] **Step 1: Control options helper.** The unrestricted Claude control is built through the seam's `options_override`:

```python
def unrestricted(o):
    """Control only: every hermetic restriction removed, permissions bypassed."""
    return dataclasses.replace(
        o, permission_mode="bypassPermissions", tools=None, setting_sources=None, strict_mcp_config=False,
        can_use_tool=None, max_turns=None, system_prompt=None,
        extra_args={"no-session-persistence": None},   # drops disable-slash-commands: the skill control must see skills
    )

async def claude_control(prompt, cwd):
    return await _run_hermetic_claude_for_diagnostic(prompt, model="haiku", cwd=cwd, options_override=unrestricted)
```
(`dataclasses.replace` requires `ClaudeAgentOptions` to be a dataclass; confirm with `dataclasses.is_dataclass(ClaudeAgentOptions)` and fall back to `copy.copy` plus attribute assignment if it is not.) A control's effect is observed from `result.tool_blocks_seen > 0` or a marker in `result.text`.

- [ ] **Step 2: D9–D11a.** `result = await run_hermetic_claude(PROMPTS["TRIVIAL"], model="haiku")`:
  - **D9**: the call not raising `HermeticGuardViolation` is the check; print the `init` keys `tools`, `mcp_servers`, `slash_commands`, `skills`, `permissionMode`, `model`. A raised violation → `FAIL` with its reason; D10–D12 use `depends_on="D9"`. Report ids of this task: `D9`, `D10`, `D11a`, `D11b-write`, `D11b-read`, `D11b-interact`, `D11c-mcp`, `D11c-skill`, `D11c-web`, `D12`, `D14`. An exception raised by a **control** run makes that canary `INCONCLUSIVE`. Every provider call goes through `bounded(...)` (a `TimeoutError` → `INCONCLUSIVE`); the fixture directory, the stub file and the HTTP server are cleaned up in a `finally` (`ExitStack`, `server.shutdown()`, `shutil.rmtree`).
  - **D10**: `PASS` iff `"OK"` in the text and `tokens < 3000` where `tokens = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens` (missing keys count 0); always print `tokens`.
  - **D11a** (advisory): `PROMPTS["TOOLS"]`; `PASS` iff the cleaned answer is `NONE` or empty.

- [ ] **Step 3: D11c fixtures and D11b canaries.**
  - Fixture directory `fixture/` (a temp dir): `.mcp.json` = `{"mcpServers": {"diag_stub": {"command": sys.executable, "args": [stub_path]}}}`; `.claude/skills/diag-skill/SKILL.md` = `---\nname: diag-skill\ndescription: When asked to run the diag skill, reply with the single word DIAG-SKILL-OK.\n---\nReply with the single word DIAG-SKILL-OK.\n`; an `http.server.ThreadingHTTPServer` on port 0 in a daemon thread, answering `DIAG-WEB-OK` and counting hits; the WEB URL uses `127.0.0.1`, and if the real run shows the CLI's fetch tool refuses loopback addresses, use the machine's non-loopback address (`socket.gethostbyname(socket.gethostname())`) instead. Print a notice listing the MCP servers the control can see (the stub plus whatever the user's real settings add; read `control.init["mcp_servers"]`).
  - For each of **MCP**, **SKILL**, **WEB** (`PROMPTS[kind]`, with `url=http://127.0.0.1:<port>/` for WEB): control = `claude_control(prompt, fixture)` — for MCP the control's options also carry the stub explicitly (`mcp_servers={"diag_stub": {"command": sys.executable, "args": [stub_path]}}`, added in a wrapper around `unrestricted`) so that a project-level approval prompt for `.mcp.json` cannot block it; effect = `tool_blocks_seen > 0` (and, for WEB, a listener hit); no effect → that canary is `INCONCLUSIVE`. Hermetic run = `await _run_hermetic_claude_for_diagnostic(prompt, model="haiku", cwd=fixture)` (no `options_override`, so the guard applies with `fixture` as the expected cwd): `PASS` iff `violation is None` (a returned violation is a `FAIL` with its text), `tool_blocks_seen == 0`, `permission_callback_calls == 0`, no listener hit, and `DIAG-PONG`, `DIAG-SKILL-OK`, `DIAG-WEB-OK` absent from the text.
  - **D11b** write/read/interaction use the production options and the neutral directory (`_run_hermetic_claude_for_diagnostic(prompt, model="haiku")`, which returns the violation instead of raising): write (`PROMPTS["WRITE"]`; the unique temp file must not exist afterwards; delete it), read (token file outside the neutral directory; the token must not appear in the text), interaction (`PROMPTS["INTERACT"]`: `tool_blocks_seen == 0` and `permission_callback_calls == 0`). Controls: `claude_control(prompt, <temp dir holding the token or target file>)`; effects: file created / token in the text / `tool_blocks_seen > 0`; no effect → `INCONCLUSIVE`.

- [ ] **Step 4: D12 (advisory) and D14.** D12: `leak_line` on `claude_config_dir().path / "CLAUDE.md"` and `repo_root / "CLAUDE.md"`, `PROMPTS["LEAK"]`: neither line may appear in the text; and `PROMPTS["CWD"]` run while the process working directory is the repository must not return `twicc` (case-insensitive). D14 (`check_d14_neutral_dir_still_empty`, called by `main` after all live checks of all selected providers): `hermetic_cwd()` still passes, else `FAIL` with the error message.

- [ ] **Step 5: Run live for Claude, then everything.** Tell the user the cost first (about 17 calls; the Claude controls are about 33 000 tokens each). `uv run python scripts/diagnose_hermetic_llm.py --provider claude --live --yes` → all `PASS`/`SKIP`, exit `0`; then `--provider all --live --yes` once, and check that no `hermetic-diag-*` file is left in `$TMPDIR` and that the neutral directory is empty.

- [ ] **Step 6: Lint and commit** — `uvx ruff check scripts/diagnose_hermetic_llm.py`; `git add scripts/diagnose_hermetic_llm.py && git commit` (`feat(hermetic): add the Claude live checks and positive controls to the diagnostic`).

---

### Task 12: Docs, manual measurements, final verification

**Files:**
- Modify: `CLAUDE.md`, `AGENTS.md` (mirror each other)
- Modify: `docs/plans/2026-10-03-hermetic-llm-calls-design.md` (§12 only: add measurements)
- Modify: `docs/codex-vendoring.md` (one added bullet in its update procedure)

- [ ] **Step 1: Maintenance note.** Add to `CLAUDE.md` and `AGENTS.md` (same wording; in `CLAUDE.md` put it right after the `**Tests: pytest + pytest-django**` paragraph of the Stack section and before `**Codex real-binary integration tests**`; find the equivalent paragraph in `AGENTS.md` with `grep -n "Tests: pytest" AGENTS.md`): *"After changing the small Codex model (`TITLE_MODEL`, `_REFRESH_MODEL`), updating Codex or its vendored SDK (`docs/codex-vendoring.md`), or updating the Claude Agent SDK, run `uv run python scripts/diagnose_hermetic_llm.py --provider all --live --yes` and do not ship on a `FAIL` or `INCONCLUSIVE`."* Add one line to the "Data Directory" contents list of both files: `cache/` (generated hermetic Codex catalogues, safe to delete). Also add one sentence pointing to `docs/codex-vendoring.md`'s update procedure (a single added bullet there: "run the hermetic diagnostic") — this is the only edit to that file.

- [ ] **Step 2: Manual measurements the spec leaves open** (spec §5.6, §9.4, D13(i)). With the user's explicit go for the one that touches login:
  1. D13(i) was verified by Task 9's real offline run — copy its outcome (binary rejects a bogus catalogue: yes/no).
  2. **Codex logged-out behaviour** (one hand measurement, no model call): in a throwaway Codex home. An inherited `CODEX_HOME` is **dropped** by `paths.ensure_env_loaded()`, so do not export it: create `D=$(mktemp -d)`, write `CODEX_HOME=$D/codex-home` (absolute) in `$D/.env`, create that directory, run with `TWICC_DATA_DIR=$D`, and print `twicc.provider_homes.codex_home().path` first to prove it is the throwaway one (never the real home), start `hermetic_codex(plan)` and record whether `model/list`, `config/read` and `thread/start` answer when no `auth.json` exists, and the exact error text of the first failing call. Ask the user before running anything that could touch their real login.
  3. Update spec §12 with a short "Implementation-time measurements" table: bogus catalogue result, logged-out result, observed token counts from Tasks 10–11, and the cost of one full diagnostic run. Update §9.4 / §5.6 statements that said "unmeasured" to the measured outcome, and if the logged-out call fails with a shape the site-5 classifier does not match, extend `is_unauthorized_exception` and its test in the same commit.

- [ ] **Step 3: Full verification.**

```bash
cd /home/twidi/dev/twicc-poc && uv run pytest -q
uvx ruff check src/twicc/providers/hermetic.py src/twicc/providers/claude_code/hermetic.py src/twicc/providers/codex/hermetic.py src/twicc/providers/codex/hermetic_catalog.py src/twicc/providers/codex/title_suggest.py src/twicc/providers/codex/credentials.py src/twicc/providers/claude_code/title_suggest.py src/twicc/providers/claude_code/auth.py scripts/diagnose_hermetic_llm.py tests/test_hermetic_*.py tests/test_diagnose_hermetic_framework.py tests/test_title_output_validation.py tests/test_title_suggestion_client_failures.py
git grep -n "danger_full_access" src/twicc/providers/codex/title_suggest.py src/twicc/providers/codex/credentials.py   # expect: nothing
git grep -n "setting_sources=None\|permission_mode=\"default\"" src/twicc/providers/claude_code/title_suggest.py src/twicc/providers/claude_code/auth.py   # expect: nothing
```
Expected: all tests pass (real-binary Codex tests stay skipped), ruff clean on the listed files, the two greps print nothing. Confirm that no other `make_codex_config()` / `ClaudeSDKClient(` in `src/` makes a non-session model call (spec §3 says none): `git grep -n "ClaudeSDKClient(" src; git grep -n "thread_start_with_policy" src`.

- [ ] **Step 4: Commit the docs** — `git add CLAUDE.md AGENTS.md docs/codex-vendoring.md docs/plans/2026-10-03-hermetic-llm-calls-design.md` (diff each for foreign hunks first) — `docs(hermetic): add the diagnostic maintenance rule and record the implementation measurements`.

- [ ] **Step 5: Hand back to the user.** Report: tasks done, the interpretation of spec §9.1 used by `tests/test_diagnose_hermetic_framework.py` (Task 9), the exit code `2` used for a declined or missing `--live` confirmation (the spec is silent on it), the diagnostic's last full-run result, anything `INCONCLUSIVE`/`SKIP`, and the reminders the repo rules require: restart the backend through `devctl.py` (backend code changed); no migration, no package change. **Propose** a CHANGELOG line under `## [Unreleased]` (suggested: *"Session title suggestions and the login checks no longer expose tools or project context to the model"*) and wait for the user's go before writing it.
