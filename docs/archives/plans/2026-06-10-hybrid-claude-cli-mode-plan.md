# Hybrid Claude CLI Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Claude Code session run as the interactive CLI inside a dedicated tmux session, rendered in a terminal embedded in the message composer, while TwiCC keeps its full JSONL-based session view, process states, and settings UI.

**Architecture:** A new `HybridClaudeAgent` implements the existing `BaseAgent` contract but drives a tmux-hosted `claude` TUI instead of an SDK client: launch = tmux `new-session` with the bundled binary and CLI flags; send = tmux bracketed paste; states derived from the JSONL by the watcher (user line → assistant turn, `turn_duration` → user turn, `tool_result` → pending cleared) + tmux liveness; a single `PermissionRequest` hook drops a file into a dedicated watched directory for pending approvals (no HTTP — works with or without the TwiCC password, no auth-exempt URL, no secret in `ps`). Original files for diffs come from Claude's own file-history store, forced on via `fileCheckpointingEnabled`. The JSONL pipeline (watcher, compute, costs) is untouched. The frontend adds a collapsible/maximizable terminal block above the composer textarea and a one-way "hybrid" toggle.

**Tech Stack:** Django 6 (ASGI), tmux (`-L twicc` socket), bundled Claude CLI 2.1.170 (`claude_agent_sdk/_bundled/claude`), Vue 3 + xterm.js (existing `useTerminal.js`), Pinia.

**Spec:** `docs/plans/2026-06-10-hybrid-claude-cli-mode-design.md` — read it first. All "verified" claims below were validated empirically in the design phase.

**Stop semantics:** TwiCC's existing stop affordances — Stop button, menu entry, triple-Escape shortcut — all funnel into the WS `kill_process` flow (`asgi.py:568`) and today stop the SDK process. In hybrid mode that same chain must be plugged to kill the tmux session and claude with it (Task 5 `kill()`, verified end-to-end in Task 15); the user never has to act inside the terminal to stop a session. There is no separate "interrupt" command: `HybridClaudeAgent` implements `interrupt_or_kill` as kill.

**Project rules that apply (from CLAUDE.md):**
- No tests, no linting — verification is manual, via commands and the running app.
- Everything written (code, comments, UI strings, docs) in English.
- Never run `migrate` by hand against the user's data dir; in the worktree, `devctl.py start` auto-applies migrations. Always `cd` into the worktree and set `TWICC_DATA_DIR=$PWD` for any manual Python.
- Commit precisely (`git add <files>`, never `-A`).

## Execution preamble — autonomous run

This plan is meant to be executed END-TO-END WITHOUT STOPPING for user input.
The user reviews the finished work afterwards; your job is to get as far as
possible, fully verified. Concretely:

- **Do every verification yourself.** Scriptable checks: drive the interactive
  CLI with the tmux probe technique — read `docs/tmux-probe-recipe.md` FIRST,
  it contains the full recipe (isolation, paste, dialogs, hooks-as-taps,
  timings, traps). UI checks (toggle, composer terminal block, badge,
  attachments, final sweep): do them yourself with the Chrome MCP browser
  tools against the worktree frontend (use the ports printed by
  `uv run ./devctl.py start`; run it from the worktree — NEVER touch the
  user's main instance at `~/.twicc`).
- **Test sessions cost real API tokens — keep them cheap:** launch every test
  claude (probes AND UI test sends) with `--model sonnet --effort low` /
  the sonnet model + low effort in the UI. ⚠️ the bare `haiku` alias is
  silently ignored by the CLI; if you want Haiku use the full
  `claude-haiku-4-5-20251001`.
- **Judgment points** (`FIRST_PASTE_DELAY` tuning, trust-dialog paste behavior,
  `attachment`-line rendering, UI placement details): decide alone, verify your
  choice with a probe or Chrome MCP, and record the decision + rationale in the
  commit message. Do not block on them.
- **`rg` trap:** never glue `-r` into combined short flags — `rg -rln foo`
  parses as `--replace=ln` and silently rewrites every match as `ln` in the
  output (this really happened). Write flags separately (`rg -n`, `rg -l`);
  avoid `-r` entirely.
- If something is truly impossible without the user (e.g. Chrome extension not
  connected), note it in a final report and KEEP GOING with everything else —
  finish the implementation and all scriptable checks, list the skipped UI
  checks at the end.

---

## File map

**Created:**

| File | Responsibility |
|---|---|
| `src/twicc/providers/claude_code/agent/hybrid/__init__.py` | Package marker, re-exports |
| `src/twicc/providers/claude_code/agent/hybrid/tmux.py` | All tmux subprocess primitives for hybrid sessions (create/paste/keys/liveness/kill) |
| `src/twicc/providers/claude_code/agent/hybrid/launch.py` | CLI flag builder (settings → argv), hooks `--settings` JSON (file-drop commands), addendum file |
| `src/twicc/providers/claude_code/agent/hybrid/agent.py` | `HybridClaudeAgent(BaseAgent)` — lifecycle, send, rename, settings application |
| `src/twicc/providers/claude_code/agent/hybrid/hooks_watcher.py` | Watcher on `<data_dir>/hybrid-hooks/` feeding `PermissionRequest` events (the only injected hook) to the manager |
| `src/twicc/core/migrations/0107_session_hybrid.py` | `Session.hybrid` column |
| `frontend/src/components/message/HybridTerminalBlock.vue` | Terminal block above the textarea (3-state: minimized/normal/maximized) |

**Modified:**

| File | Change |
|---|---|
| `src/twicc/core/models.py` | `Session.hybrid` field |
| `src/twicc/core/serializers.py` | serialize `hybrid` |
| `src/twicc/core/services/session_creation.py` | trusted-only `allow_hybrid` kwarg → pending attributes |
| `src/twicc/pending_session_attributes.py` | add `hybrid` to pending attributes |
| `src/twicc/providers/sessions_watcher.py` | apply pending `hybrid` on Session row creation (~lines 369–421); JSONL→state bridge for hybrid sessions (at the `touch_agent_activity` call site, ~line 619) |
| `src/twicc/paths.py` | `get_session_hybrid_dir()` + `get_hybrid_hooks_dir()` |
| `src/twicc/providers/claude_code/constants.py` | `HYBRID_AGENT_SETTINGS_CATEGORIES` |
| `src/twicc/providers/claude_code/agent/manager.py` | hybrid branch in `_create_agent`, hybrid settings classification (incl. mid-turn guard), `/rename` on title flush, boot adoption, hybrid hook handler |
| `src/twicc/providers/helpers.py` | optional `categories=` param on `classify_agent_settings_changes` (line ~460) |
| `src/twicc/providers/claude_code/compute.py` | classify CLI-only line types (`ai-title`, `queued-command`) + compute version bump |
| `src/twicc/cli/run.py` | start the hybrid-hooks watcher task (next to the drop-requests watcher) |
| `src/twicc/asgi.py` | `set_session_hybrid` WS handler; `hybrid` in the new-session payload dict (trusted path) |
| `src/twicc/terminal.py` | `h:` terminal context → attach-only to `twicc-hybrid-*` tmux sessions |
| `frontend/src/stores/data.js` | `hybrid` on draft sessions |
| `frontend/src/components/message/MessageInput.vue` | hybrid toggle button, `hybrid` in send payload (drafts) |
| `frontend/src/components/session/detail/SessionItemsList.vue` | mount `HybridTerminalBlock`, suppress `PendingRequestForm` + sending lock for hybrid |
| `frontend/src/composables/useTerminal.js` | support `h:` context key (forced tmux, no creation) |
| `CHANGELOG.md` | Unreleased entry |

---

### Task 1: `Session.hybrid` flag — model, migration, serializer, pending attributes

**Files:**
- Modify: `src/twicc/core/models.py` (Session fields block, after `system_prompt_addendum`, ~line 465)
- Create: `src/twicc/core/migrations/0107_session_hybrid.py` (check the latest migration number first — `ls src/twicc/core/migrations/ | tail`; it was `0106_project_worktree_directory.py` at design time)
- Modify: `src/twicc/core/serializers.py` (`serialize_session`, ~line 50)
- Modify: `src/twicc/pending_session_attributes.py` (`set_pending_session_attributes` / the pending NamedTuple)
- Modify: `src/twicc/providers/sessions_watcher.py` (`create_session_sync`, ~lines 369–421, where `system_prompt_addendum` is popped from pending attributes)
- Modify: `src/twicc/core/services/session_creation.py` (signature + the `set_pending_session_attributes(...)` call, ~line 302)

- [x] **Step 1: Add the model field**

In `Session`, next to the orchestration fields (after `system_prompt_addendum`):

```python
# Hybrid CLI mode: when True, this session is driven by the interactive
# Claude Code CLI running in a dedicated tmux session instead of the SDK.
# One-way: once a session has been resumed by the CLI it can never go back
# to the SDK (the SDK no longer sees CLI-era messages), so this flag is
# never reset to False. Human-only: only settable from the web UI (WS),
# never from the agent-facing CLI or drop-request paths.
hybrid = models.BooleanField(default=False)
```

- [x] **Step 2: Generate the migration**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/claude-hybrid
TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --settings=twicc.settings
```

Expected: creates `0107_session_hybrid.py`. Sanity-check the resolved DB path first if in doubt (see CLAUDE.md). Do NOT run `migrate` manually — `devctl.py start` applies it.

- [x] **Step 3: Serialize the field**

In `serialize_session()` add `"hybrid": session.hybrid,` next to `archived`/`pinned`.

- [x] **Step 4: Pending attributes plumbing — trusted-only**

`set_pending_session_attributes` carries creation-time attributes until the watcher creates the `Session` row (same mechanism as `system_prompt_addendum`). Add a `hybrid: bool = False` field to the pending-attributes NamedTuple and its setter; in `sessions_watcher.create_session_sync`, copy it onto the new `Session` row exactly where `system_prompt_addendum` is applied.

**Security (spec §2.1 — enforce here, this is load-bearing):** `hybrid` must only ever come from the human web UI. `create_session_from_payload` is ALSO the handler for `session:create` drop-request files (`src/twicc/drop_requests_watcher.py:45–49`), which any agent can write — so it must NOT read `hybrid` from the raw payload. Add a **keyword-only trusted parameter** instead:

```python
async def create_session_from_payload(payload: dict, *, allow_hybrid: bool = False) -> SessionCreationResult:
    ...
    hybrid = bool(payload.get("hybrid")) if allow_hybrid else False
```

Only the WS handler (Task 8) passes `allow_hybrid=True`. The drop-request watcher and every other caller keep the default `False`, so a crafted `"hybrid": true` drop file is silently ignored. Do not add any `hybrid` handling to `src/twicc/cli/` or `send_message_to_session_from_payload`.

Also update the pending-attributes re-key forwarding in `base_manager.py:368–377`: it forwards an explicit kwargs list (`hidden`, `spawned_by_id`, `spawn_root_id`, `annotations`, `system_prompt_addendum`) — add `hybrid` there too so the flag survives a draft→canonical id re-key (no V1 impact for Claude Code, but a silent `hybrid=False` default there is a future trap).

- [x] **Step 5: Manual check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/claude-hybrid
TWICC_DATA_DIR=$PWD uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
django.setup()
from twicc.core.models import Session
print('hybrid' in [f.name for f in Session._meta.fields])
"
```

Expected: `True`.

- [x] **Step 6: Commit**

```bash
git add src/twicc/core/models.py src/twicc/core/migrations/0107_session_hybrid.py \
        src/twicc/core/serializers.py src/twicc/core/services/session_creation.py \
        src/twicc/providers/sessions_watcher.py src/twicc/pending_session_attributes.py
git commit -m "feat(hybrid): add one-way Session.hybrid flag with trusted-only creation plumbing"
```

---

### Task 2: Per-session hybrid directory in `paths.py`

**Files:**
- Modify: `src/twicc/paths.py`

- [x] **Step 1: Add the helper** (pattern: `get_session_artifacts_dir`, line ~146)

```python
def get_session_hybrid_dir(session_id: str) -> Path:
    """Per-session runtime files for hybrid CLI mode (addendum file, attachments).

    The whole directory is passed to the CLI via --add-dir so attachment
    reads never trigger permission prompts.
    """
    path = get_data_dir() / "hybrid" / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_hybrid_hooks_dir() -> Path:
    """Watched drop directory for hybrid CLI hook event files (see Task 6).

    File-based on purpose: hook commands must reach TwiCC without HTTP, so
    the channel works with the password enabled and exposes no URL/secret.
    """
    path = get_data_dir() / "hybrid-hooks"
    path.mkdir(parents=True, exist_ok=True)
    return path
```

- [x] **Step 2: Commit**

```bash
git add src/twicc/paths.py
git commit -m "feat(hybrid): per-session hybrid runtime directory helper"
```

---

### Task 3: tmux primitives — `hybrid/tmux.py`

**Files:**
- Create: `src/twicc/providers/claude_code/agent/hybrid/__init__.py` (empty for now)
- Create: `src/twicc/providers/claude_code/agent/hybrid/tmux.py`

Reuse from `src/twicc/terminal.py`: `TMUX_SOCKET_NAME` (= `"twicc"`, line ~66), `get_tmux_path()`, and the tmux-config resolution. **Note:** `resolve_tmux_config_path(path)` takes the user-configured value as an argument — read it from synced settings key `terminalTmuxConfigPath` exactly as `terminal.py` does around lines 716–720 (copy that read), don't call it bare.

- [x] **Step 1: Write the module**

All functions are sync (callers wrap with `asyncio.to_thread`); every tmux call uses `[tmux, "-L", TMUX_SOCKET_NAME, ...]` with a short `subprocess.run(..., timeout=5)`.

```python
"""Tmux primitives for hybrid CLI sessions.

A hybrid session owns exactly one tmux session named ``twicc-hybrid-<id>``
(sanitized), on the shared ``-L twicc`` socket, whose single pane runs the
Claude CLI directly (via ``exec``) so the pane PID *is* the claude PID and
``pane_dead`` flips when claude exits (``remain-on-exit on``).
"""

import os
import shlex
import subprocess

from twicc.terminal import TMUX_SOCKET_NAME, get_tmux_path  # + the config-path read, see above

HYBRID_SESSION_PREFIX = "twicc-hybrid-"

# Prefix-based purge, same intent as ClaudeCodeHelpers.purge_env_vars
# (helpers.py:765): CLAUDE_CODE* and CLAUDECODE*. ``env -u`` needs exact
# names, so expand the prefixes against the CURRENT environment at build
# time (the tmux server env is a superset of ours for these vars in
# practice; the worst case of a leftover var in an old server is harmless
# because claude only checks the entrypoint/CLAUDECODE markers we purge).
_PURGED_ENV_PREFIXES = ("CLAUDE_CODE", "CLAUDECODE")


def _purged_env_names() -> list[str]:
    return [name for name in os.environ if name.startswith(_PURGED_ENV_PREFIXES)]


def hybrid_tmux_session_name(session_id: str) -> str:
    return HYBRID_SESSION_PREFIX + session_id.replace(".", "_").replace(":", "_")


def _tmux_base() -> list[str]:
    tmux = get_tmux_path()
    if tmux is None:
        raise RuntimeError("tmux is not installed — hybrid mode requires tmux")
    return [tmux, "-L", TMUX_SOCKET_NAME]


def _run(args: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_tmux_base(), *args],
        input=input_bytes,
        capture_output=True,
        timeout=5,
        check=False,
    )


def session_exists(session_id: str) -> bool:
    # '=' forces exact-name match (no prefix matching).
    return _run(["has-session", "-t", "=" + hybrid_tmux_session_name(session_id)]).returncode == 0


def create_session(session_id: str, cwd: str, argv: list[str]) -> None:
    """Create the tmux session running ``argv`` directly as the pane command.

    ``exec env -u VAR…`` ensures the sh -c wrapper is replaced and the
    purged variables never reach claude, regardless of the tmux server env.
    """
    name = hybrid_tmux_session_name(session_id)
    unsets: list[str] = []
    for var in _purged_env_names():
        unsets += ["-u", var]
    command = "exec env " + shlex.join(unsets + argv) if unsets else "exec " + shlex.join(argv)
    args = ["new-session", "-d", "-s", name, "-c", cwd]
    base = _tmux_base()
    config = _read_tmux_config_path()  # synced-settings read, as in terminal.py
    if config:
        base = base[:1] + ["-f", config] + base[1:]
    result = subprocess.run(
        [*base, *args, command], capture_output=True, timeout=10, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux new-session failed: {result.stderr.decode(errors='replace')}")
    _run(["set-option", "-t", "=" + name, "remain-on-exit", "on"])
    _run(["set-option", "-t", "=" + name, "mouse", "off"])


def pane_status(session_id: str) -> tuple[int | None, bool]:
    """Return ``(pane_pid, pane_dead)``; ``(None, True)`` if the session is gone."""
    result = _run(
        ["list-panes", "-t", "=" + hybrid_tmux_session_name(session_id), "-F", "#{pane_pid} #{pane_dead}"]
    )
    if result.returncode != 0:
        return None, True
    parts = result.stdout.decode().split()
    if len(parts) < 2:
        return None, True
    return int(parts[0]), parts[1] == "1"


def paste_text(session_id: str, text: str, *, submit: bool = True) -> None:
    """Bracketed-paste ``text`` into the TUI composer, then press Enter.

    Verified: multiline pastes don't auto-submit, ``@`` mentions don't open
    the picker, pasted slash commands are interpreted on submit.
    """
    name = "=" + hybrid_tmux_session_name(session_id)
    buf = "twicc-hybrid"
    r = _run(["load-buffer", "-b", buf, "-"], input_bytes=text.encode())
    if r.returncode != 0:
        raise RuntimeError(f"tmux load-buffer failed: {r.stderr.decode(errors='replace')}")
    r = _run(["paste-buffer", "-p", "-d", "-b", buf, "-t", name])
    if r.returncode != 0:
        raise RuntimeError(f"tmux paste-buffer failed: {r.stderr.decode(errors='replace')}")
    if submit:
        _run(["send-keys", "-t", name, "Enter"])


def kill_session(session_id: str) -> None:
    _run(["kill-session", "-t", "=" + hybrid_tmux_session_name(session_id)])


def list_hybrid_sessions() -> list[str]:
    """Return raw tmux session names with the hybrid prefix (boot adoption)."""
    result = _run(["list-sessions", "-F", "#{session_name}"])
    if result.returncode != 0:
        return []
    return [n for n in result.stdout.decode().splitlines() if n.startswith(HYBRID_SESSION_PREFIX)]
```

(Check `terminal.py`'s exact import names before writing; the tmux-config read helper `_read_tmux_config_path` is a thin copy of the synced-settings lookup at terminal.py:716–720.)

- [x] **Step 2: Manual smoke test**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/claude-hybrid
TWICC_DATA_DIR=$PWD uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
django.setup()
from twicc.providers.claude_code.agent.hybrid import tmux as t
t.create_session('plan-smoke', '/tmp', ['sleep', '300'])
print('exists:', t.session_exists('plan-smoke'))
print('pane:', t.pane_status('plan-smoke'))
t.kill_session('plan-smoke')
print('after kill:', t.session_exists('plan-smoke'))
"
```

Expected: `exists: True`, `pane: (<pid>, False)`, `after kill: False`. Confirm the pid is the `sleep` process (`ps -p <pid> -o comm=` while it runs, if checking by hand).

- [x] **Step 3: Commit**

```bash
git add src/twicc/providers/claude_code/agent/hybrid/__init__.py \
        src/twicc/providers/claude_code/agent/hybrid/tmux.py
git commit -m "feat(hybrid): tmux primitives for hybrid CLI sessions"
```

---

### Task 4: Launch builder — `hybrid/launch.py`

**Files:**
- Create: `src/twicc/providers/claude_code/agent/hybrid/launch.py`
- Reference: `src/twicc/providers/claude_code/agent/agent.py:756-906` (the SDK options builder — mirror its decisions), `src/twicc/providers/claude_code/bin.py` (`resolve_bundled_binary`), `src/twicc/agent/plugin/__init__.py` (`get_plugin_dir`), `src/twicc/core/services/trust.py` (`clamp_permission_mode_for_untrusted`)

- [x] **Step 1: Hooks settings JSON — the single `PermissionRequest` hook + forced checkpointing**

States, turn transitions, pending RESOLUTION, and AskUserQuestion content all
come from the JSONL (Task 6's bridge) — verified empirically. The only signal
the JSONL cannot provide is a pending approval's APPEARANCE (verified: nothing
in the JSONL while the prompt is up). So exactly ONE hook is injected:
`PermissionRequest`, whose payload carries `tool_name`, the full `tool_input`
and `permission_suggestions` (verified — enough for a rich badge).

The hook command does NOT call TwiCC over HTTP: it drops one event file into
the dedicated watched directory (`get_hybrid_hooks_dir()`, Task 2; consumed by
the watcher in Task 6). Authentication is the filesystem itself (same-user
write access). Works identically with `TWICC_PASSWORD_HASH` set, survives
restarts (stable path), routes per data dir, and fires at most once per
approval prompt — performance is a non-issue.

Filename protocol: `<session_id>__<event>__<nonce>.json`, written atomically
(`.tmp` then `mv`, same convention as the drop-requests watcher,
`drop_requests_watcher.py:36`). The file CONTENT is the hook's stdin JSON.

```python
import shlex

import orjson


def build_hooks_settings(session_id: str, fast_mode: bool) -> str:
    """Inline --settings JSON: fastMode + forced file checkpointing + the
    single PermissionRequest hook.

    Schema validated empirically on CLI 2.1.170 (2026-06-11 design probe).
    """
    hooks_dir = shlex.quote(str(get_hybrid_hooks_dir()))
    # $$ + nanoseconds make the name unique; cat captures the hook's stdin
    # JSON; the .tmp→mv rename makes the drop atomic.
    name = f"{session_id}__PermissionRequest__$$-$(date +%s%N)"
    command = f'f={hooks_dir}/{name}.json; cat > "$f.tmp" && mv "$f.tmp" "$f" || true'
    settings = {
        "fastMode": fast_mode,
        # Forced ON: off by default in SDK mode and user-disablable in user
        # settings; the original-file capture (Task 16) depends on it. The
        # env purge already drops an inherited
        # CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING.
        "fileCheckpointingEnabled": True,
        "hooks": {
            "PermissionRequest": [{"hooks": [{"type": "command", "command": command}]}],
        },
    }
    return orjson.dumps(settings).decode()
```

- [x] **Step 3: The argv builder**

Mirror `agent.py`'s decisions one-for-one (model resolution via `helpers.resolve_sdk_model(selected_model, context_max)`, thinking mapping, untrusted clamp + setting sources, chrome flags, question_widget → disallowedTools). All flags verified on CLI 2.1.170 (design doc §3.2).

```python
def build_argv(
    *,
    session_id: str,
    cwd: str,
    settings: AgentSettings,
    resume: bool,
    temp_title: str,
    addendum_path: Path | None,
    attachments_dir: Path,
    untrusted: bool,
) -> list[str]:
    binary = str(resolve_bundled_binary())
    argv = [binary]
    if resume:
        argv += ["--resume", session_id]
    else:
        argv += ["--session-id", session_id]
    sdk_model = helpers.resolve_sdk_model(settings.selected_model, settings.context_max)
    if sdk_model:
        argv += ["--model", sdk_model]
    if settings.effort:
        argv += ["--effort", settings.effort]
    if settings.thinking_enabled is True:
        argv += ["--thinking", "adaptive"]
    elif settings.thinking_enabled is False:
        argv += ["--thinking", "disabled"]
    permission_mode = settings.permission_mode
    if untrusted:
        permission_mode = clamp_permission_mode_for_untrusted(Provider.CLAUDE_CODE, permission_mode)
        argv += ["--setting-sources", "user"]
    else:
        argv += ["--allow-dangerously-skip-permissions"]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    if settings.claude_in_chrome is True:
        argv += ["--chrome"]
    elif settings.claude_in_chrome is False:
        argv += ["--no-chrome"]
    if settings.question_widget is False:
        argv += ["--disallowedTools", "AskUserQuestion"]
    argv += ["--settings", build_hooks_settings(session_id, bool(settings.fast_mode))]
    argv += ["--plugin-dir", str(get_plugin_dir())]
    argv += ["--add-dir", str(attachments_dir)]
    if addendum_path is not None:
        argv += ["--append-system-prompt-file", str(addendum_path)]
    if temp_title:
        argv += ["-n", temp_title[:100]]
    return argv
```

Check against `agent.py:800-806` whether `--allow-dangerously-skip-permissions` is gated on trust the same way (it is: trusted only) and copy the exact trust lookup used there (`self._untrusted` derivation) rather than reinventing it.

- [x] **Step 4: Addendum file writer**

```python
def write_addendum_file(session_id: str, addendum: str | None) -> Path | None:
    if not addendum:
        return None
    path = get_session_hybrid_dir(session_id) / "addendum.md"
    path.write_text(addendum, encoding="utf-8")
    return path
```

- [x] **Step 5: Manual check — full dry-run of the command line**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/claude-hybrid
TWICC_DATA_DIR=$PWD uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
django.setup()
from twicc.providers.claude_code.agent.hybrid.launch import build_argv, build_hooks_settings
# build with a representative AgentSettings and print
" # (adapt: construct AgentSettings, print shlex.join(argv))
```

The hooks schema and the checkpointing forcing were already validated during design (2026-06-11 probe — design doc §5.1). Still sanity-check the BUILT artifacts once: run the built argv inside a throwaway tmux session in `/tmp`, trigger an Edit that needs approval, and confirm (a) a `…__PermissionRequest__….json` file appears in `<data_dir>/hybrid-hooks/` while the prompt is up, and (b) after accepting, `~/.claude/file-history/<sid>/` is populated and the JSONL has a non-empty `trackedFileBackups` snapshot line.

- [x] **Step 6: Commit**

```bash
git add src/twicc/providers/claude_code/agent/hybrid/launch.py
git commit -m "feat(hybrid): CLI launch builder with hook-driven state reporting"
```

---

### Task 5: `HybridClaudeAgent` — `hybrid/agent.py`

**Files:**
- Create: `src/twicc/providers/claude_code/agent/hybrid/agent.py`
- Reference: `src/twicc/agent/base_agent.py` (contract: `_set_state`, `_transition_to_dead`, `_notify_state_change` — **note: these are async**, `get_info`, abstract `interrupt_or_kill`, the `provider` class attribute requirement at line ~60, `_pending_requests` is a dict keyed by `request_id`), `src/twicc/agent/states.py` (`AgentState`, `PendingRequest`)

- [x] **Step 1: Implement the agent**

Key behaviors (all driven externally — there is no message loop). The sketch below is intent, not paste-ready: align every signature with `base_agent.py` (notably `start()` must match what `_register_and_start` calls, `base_manager.py:395`, and the async-ness of the notify/transition helpers).

```python
class HybridClaudeAgent(BaseAgent):
    """Claude Code session driven by the interactive CLI in tmux.

    State transitions come from CLI hooks (HTTP) and a light liveness
    monitor, not from an SDK message loop.
    """

    provider = Provider.CLAUDE_CODE   # required by BaseAgent (fails fast without it)
    is_hybrid = True

    FIRST_PASTE_DELAY = 8.0  # let the TUI come up (and swallow the trust dialog case); tune empirically

    def __init__(self, session_id, project_id, cwd, agent_settings, *, untrusted: bool):
        super().__init__(session_id, project_id, cwd, agent_settings)
        self._untrusted = untrusted
        self.agent_pid: int | None = None

    async def start(self, text, state_change_callback, *, resume: bool, images=None, documents=None) -> None:
        # IMPORTANT: start() is awaited under the MANAGER-WIDE lock
        # (_register_and_start, base_manager.py:395) — it must return fast.
        # Only tmux creation happens inline; the first paste (with its TUI
        # warm-up delay) runs in a background task, the same way the SDK
        # agent defers its message loop. A blocking sleep here would stall
        # every other Claude Code operation (sends, kills, hook handling)
        # for FIRST_PASTE_DELAY seconds.
        self._state_change_callback = state_change_callback
        addendum = await self._read_addendum()          # same DB+pending read as agent.py:838-850
        temp_title = await self._resolve_temp_title(text)  # Session.title or first 100 chars of text
        argv = build_argv(...)                          # Task 4
        await asyncio.to_thread(hybrid_tmux.create_session, self.session_id, self.cwd, argv)
        self.agent_pid, _ = await asyncio.to_thread(hybrid_tmux.pane_status, self.session_id)
        self._first_paste_task = asyncio.create_task(self._first_paste(text, images, documents))
        self._start_liveness_monitor()

    async def _first_paste(self, text, images, documents) -> None:
        # Fire-and-forget task: wrap the body in try/except — if tmux died
        # during the warm-up, log and transition to DEAD instead of leaving
        # the agent stuck in STARTING until the liveness monitor notices.
        full_text = await self._materialize_attachments(text, images, documents)
        await asyncio.sleep(self.FIRST_PASTE_DELAY)
        await asyncio.to_thread(hybrid_tmux.paste_text, self.session_id, full_text)
        self._set_state(AgentState.ASSISTANT_TURN)      # corrected by the JSONL bridge if wrong
        await self._notify_state_change()

    async def send(self, text, *, images=None, documents=None) -> None:
        full_text = await self._materialize_attachments(text, images, documents)
        await asyncio.to_thread(hybrid_tmux.paste_text, self.session_id, full_text)

    async def _materialize_attachments(self, text, images, documents) -> str:
        # Stub until Task 12: pass text through, ignore attachments.
        return text

    # --- signal-driven transitions ---
    # One source is the single injected hook (PermissionRequest), delivered by
    # the hooks watcher; everything else comes from the JSONL bridge in the
    # sessions watcher (Task 6). Both go through the manager.

    async def on_permission_request(self, payload: dict) -> None:
        # payload carries tool_name + full tool_input + permission_suggestions
        # (verified) — kept on the synthetic PendingRequest for a rich badge.
        self._mark_pending_in_terminal(payload)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def on_jsonl_user_message(self) -> None:
        self._clear_pending_marker()
        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def on_jsonl_turn_end(self) -> None:
        self._clear_pending_marker()
        self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def on_jsonl_progress(self) -> None:
        # Called when new tool_result lines land. The PermissionRequest payload
        # carries no tool_use_id (verified), so clearing is unconditional: any
        # tool_result written AFTER the marker was set proves the prompt was
        # answered (approve → result, deny → error result).
        self.last_activity = time.time()
        if self._has_pending_marker():
            self._clear_pending_marker()
            await self._notify_state_change()

    async def kill(self, reason: str = "manual") -> None:
        self.kill_reason = reason
        await asyncio.to_thread(hybrid_tmux.kill_session, self.session_id)
        await self._transition_to_dead()

    async def interrupt_or_kill(self, reason: str) -> None:
        # Timeouts and manual stops both kill: the tmux session must go away
        # to free claude's memory. Mid-turn interruption is done by the user
        # pressing Escape inside the embedded terminal (V1 decision).
        await self.kill(reason)

    async def rename(self, title: str) -> None:
        await asyncio.to_thread(hybrid_tmux.paste_text, self.session_id, f"/rename {title}")

    async def apply_live_settings(self, settings) -> None:
        # IDLE: model/context only (hybrid categories — Task 7).
        sdk_model = helpers.resolve_sdk_model(settings.selected_model, settings.context_max)
        if sdk_model:
            await asyncio.to_thread(hybrid_tmux.paste_text, self.session_id, f"/model {sdk_model}")
        self.agent_settings = settings
```

**Pending-in-terminal marker:** reuse the existing `_pending_requests` dict with a single synthetic `PendingRequest` under a fixed key (e.g. `"hybrid-terminal"`), `request_type="hybrid_terminal"`, carrying the hook payload (tool_name, tool_input, suggestions) — the frontend renders a rich badge for it (Task 11) and the existing `awaiting_user_input` ProcessRun logic works unchanged. `_clear_pending_marker()` pops that key (and only that key).

**Liveness monitor:** a 5s asyncio loop checking `pane_status()`; on `pane_dead or pid is None` → `kill_reason="cli-exit"`, `await self._transition_to_dead()`. This is the ONLY death-detection path (no SessionEnd hook in V1's minimal hook set). Stop the loop on DEAD.

- [x] **Step 2: Commit**

```bash
git add src/twicc/providers/claude_code/agent/hybrid/agent.py
git commit -m "feat(hybrid): HybridClaudeAgent driven by CLI hooks and tmux liveness"
```

---

### Task 6: Hybrid signals — `PermissionRequest` drop watcher + JSONL state bridge

**Files:**
- Create: `src/twicc/providers/claude_code/agent/hybrid/hooks_watcher.py`
- Modify: `src/twicc/cli/run.py` (start the watcher task next to the drop-requests watcher)
- Modify: `src/twicc/providers/claude_code/agent/manager.py` (`handle_hybrid_hook`, `handle_hybrid_jsonl_signals`)
- Modify: `src/twicc/providers/sessions_watcher.py` (JSONL bridge at the `touch_agent_activity` call site, ~line 619)

Two signal paths converge on the agent's methods from Task 5: (a) the file-drop watcher for the single injected hook, (b) the JSONL bridge for everything else. No HTTP and no auth code: write access to the data dir IS the authentication, so the channel works identically with `TWICC_PASSWORD_HASH` set, exposes no URL, and needs no token. Model the watcher on `DropRequestsWatcher` (`src/twicc/drop_requests_watcher.py:141`): start `watchfiles.awatch` BEFORE the boot scan (so nothing dropped during boot is missed), ignore `.tmp` files (atomic-write convention, `drop_requests_watcher.py:36`), delete files after processing.

- [x] **Step 1: Watcher**

```python
"""Watcher for hybrid CLI hook event files.

Hook commands (see hybrid/launch.py) drop ``<session_id>__<event>__<nonce>.json``
files into ``get_hybrid_hooks_dir()``; this watcher feeds them to the Claude
Code manager and deletes them. File-based on purpose: no HTTP endpoint to
exempt from the password middleware, no token to leak through ``ps``.
"""
```

- Parse `path.stem.split("__")` → `(session_id, event, nonce)`; malformed names → `unlink` + log warning.
- Within a change burst, process files in filename order (the nanosecond nonce makes that chronological enough across a session's own events).
- Read the body with orjson (empty/invalid → `{}`), call `await manager.handle_hybrid_hook(session_id, event, payload)`, then `path.unlink(missing_ok=True)`. A `False` return (no live hybrid agent) still deletes the file — stale events are dropped, not retried.
- Only `PermissionRequest` is injected in V1, but parse/route any event name (forward-compat with the V2 ideas in design §7); unknown events → log + delete.
- Boot scan: process leftover files at startup. **Ordering with Task 14:** run boot adoption BEFORE the boot scan, so a leftover `PermissionRequest` of a still-pending prompt reaches the adopted agent, while events for long-gone sessions fall through harmlessly (`handled=False` → deleted).

- [x] **Step 2: Start the watcher**

In `src/twicc/cli/run.py`, create the asyncio task right next to the drop-requests watcher task (`rg -n "drop_watcher_task" src/twicc/cli/run.py`), with the same lifecycle/cancellation handling.

- [x] **Step 3: Manager-side handlers** (in `manager.py`)

```python
async def handle_hybrid_hook(self, session_id: str, event: str, payload: dict) -> bool:
    async with self._lock:
        agent = self._agents.get(session_id)
    if agent is None or not getattr(agent, "is_hybrid", False):
        return False
    if event == "PermissionRequest":
        await agent.on_permission_request(payload)
        return True
    return False  # unknown event for V1 — logged by the caller


async def handle_hybrid_jsonl_signals(self, session_id: str, signals: "HybridJsonlSignals") -> None:
    # signals: NamedTuple(user_message: bool, turn_end: bool, tool_results: bool)
    async with self._lock:
        agent = self._agents.get(session_id)
    if agent is None or not getattr(agent, "is_hybrid", False):
        return
    if signals.user_message:
        await agent.on_jsonl_user_message()
    if signals.tool_results:
        await agent.on_jsonl_progress()
    if signals.turn_end:
        await agent.on_jsonl_turn_end()
```

- [x] **Step 4: JSONL state bridge in the sessions watcher**

In `src/twicc/providers/sessions_watcher.py`, at the spot where freshly ingested
lines already trigger `touch_agent_activity()` (~line 619): when the session is
hybrid (`Session.hybrid` — fetch alongside the data already loaded there, do not
add a per-line query) and the provider is Claude Code, derive a small
`HybridJsonlSignals` NamedTuple from the just-computed items of this batch:
- `user_message`: a new item of kind user message (real human prompt — exclude
  meta/tool_result user lines; reuse the computed `ItemKind`),
- `turn_end`: a new `system` line with `subtype == "turn_duration"`,
- `tool_results`: any new tool_result content in the batch.

Then `await manager.handle_hybrid_jsonl_signals(session_id, signals)` (fire and
forget — never block the ingest path on agent locks; `asyncio.create_task` is
fine). Latency is inotify-level (ms), same as the live UI updates today.

- [x] **Step 5: Manual check**

With the worktree backend running (`uv run ./devctl.py start back` from the worktree):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/claude-hybrid
echo '{}' > hybrid-hooks/manual-test__PermissionRequest__1.json.tmp   # .tmp → must stay untouched
mv hybrid-hooks/manual-test__PermissionRequest__1.json.tmp hybrid-hooks/manual-test__PermissionRequest__1.json
sleep 1; ls hybrid-hooks/
```

Expected: the `.json` file disappears within ~a second (consumed, `handled=False` logged since no live agent), and a file left with the `.tmp` suffix is never touched. A garbage-named file (`echo x > hybrid-hooks/garbage.json`) is deleted with a warning in `logs/backend.log`. The JSONL bridge itself is exercised end-to-end at Task 7 Step 5.

- [x] **Step 6: Commit**

```bash
git add src/twicc/providers/claude_code/agent/hybrid/hooks_watcher.py \
        src/twicc/cli/run.py src/twicc/providers/claude_code/agent/manager.py \
        src/twicc/providers/sessions_watcher.py
git commit -m "feat(hybrid): PermissionRequest drop watcher and JSONL state bridge"
```

---

### Task 7: Manager integration — creation, send, hybrid settings categories

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/manager.py`
- Modify: `src/twicc/providers/claude_code/constants.py`
- Modify: `src/twicc/providers/helpers.py` (`classify_agent_settings_changes`, ~line 460)

- [x] **Step 1: Hybrid settings categories** (`constants.py`, below `AGENT_SETTINGS_CATEGORIES`)

```python
# Hybrid CLI mode classification. Differs from the SDK's: the TUI has no
# reliable way to set permission_mode externally (Shift+Tab cycling is
# stateful), so it becomes STARTUP. Model stays IDLE via a pasted /model
# command. effort/thinking/fast COULD become live later via /effort, Tab,
# /fast — kept STARTUP in V1 (design doc §2.5).
HYBRID_AGENT_SETTINGS_CATEGORIES: dict[AgentSettingCategory, list[str]] = {
    AgentSettingCategory.LIVE: [],
    AgentSettingCategory.IDLE: ["selected_model", "context_max"],
    AgentSettingCategory.STARTUP: [
        "permission_mode",
        "effort",
        "thinking_enabled",
        "claude_in_chrome",
        "fast_mode",
        "question_widget",
    ],
}
```

- [x] **Step 2: Branch agent creation**

In `_create_agent` (manager.py:328): fetch the session's `hybrid` flag (DB read; for brand-new sessions read the pending attributes — same dual lookup as the addendum). If hybrid → return `HybridClaudeAgent(...)` (with the `untrusted` flag computed the same way as for the SDK agent). Otherwise unchanged.

- [x] **Step 3: Branch settings classification — including the mid-turn permission branch**

Everywhere `send_to_session` / `_apply_pending_settings` call `classify_agent_settings_changes(...)` (manager.py ~141–222 and ~882–947), pass `categories=HYBRID_AGENT_SETTINGS_CATEGORIES` when `getattr(agent, "is_hybrid", False)` (add an optional `categories=` parameter to `classify_agent_settings_changes`, defaulting to `self.AGENT_SETTINGS_CATEGORIES`).

**Load-bearing guard:** the ASSISTANT_TURN branch of `send_to_session` *bypasses* classification and calls `await agent.set_permission_mode(settings.permission_mode)` directly (manager.py:199–201). `HybridClaudeAgent` has no `set_permission_mode` and `permission_mode` is STARTUP in hybrid — guard this call with `if not getattr(agent, "is_hybrid", False)`; for hybrid, the change is picked up by `_apply_pending_settings` on the next USER_TURN (startup → restart), which is the intended behavior.

The remaining flows (startup → kill+restart with `_pending_after_restart`; idle on USER_TURN → `apply_live_settings`) are reused as-is — that's the point of implementing the `BaseAgent` contract.

- [x] **Step 4: Hybrid resume must never go through the SDK**

Audit every path in `manager.py` that constructs the SDK `ClaudeCodeAgent` — the hybrid check in `_create_agent` covers them all if they go through the factory. Verify cron-restart paths use `_create_agent` too; if a session is hybrid and a cron fires, V1 behavior: refuse + log (crons on hybrid sessions are out of scope; document in the code comment).

- [x] **Step 5: Manual end-to-end (first real run)**

Start worktree servers (`uv run ./devctl.py start` from the worktree; read the ports it prints). In the UI: create a new Claude Code draft, send a message normally (sanity), then in a Django shell flip `Session.objects.filter(id=...).update(hybrid=True)` on a NEW draft-created session and send — expect: tmux session `twicc-hybrid-<id>` exists, claude TUI running, message pasted and submitted, JSONL items appear in the UI, process state goes assistant_turn → user_turn via the JSONL bridge (watch `logs/backend.log`).

- [x] **Step 6: Commit**

```bash
git add src/twicc/providers/claude_code/agent/manager.py \
        src/twicc/providers/claude_code/constants.py src/twicc/providers/helpers.py
git commit -m "feat(hybrid): manager integration with hybrid-specific settings categories"
```

---

### Task 8: WS surface — `set_session_hybrid` + draft `hybrid` passthrough

**Files:**
- Modify: `src/twicc/asgi.py`

- [x] **Step 1: `set_session_hybrid` handler** (pattern: neighboring session-update handlers in `WSConsumer`)

Payload `{type: "set_session_hybrid", session_id}`. Behavior:
1. Load session; error reply if missing; no-op `{ok: true}` if already hybrid.
2. If an SDK agent is live for this session → `await manager.kill_agent(session_id, reason="switch-hybrid")`.
3. `Session.objects.filter(id=...).aupdate(hybrid=True)`; broadcast `session_updated` (reuse the existing serialize+group_send pattern, asgi.py:815-859 neighborhood).
4. Never accept `hybrid: false` — there is no payload field for it; the handler only sets True (one-way).

- [x] **Step 2: Draft passthrough (explicit edit, not just a check)**

In `_handle_send_message` (asgi.py:662), the new-session branch builds an **explicit payload dict** (asgi.py:866–876) before calling `create_session_from_payload` — add `"hybrid": bool(content.get("hybrid"))` to that dict, and pass `allow_hybrid=True` to `create_session_from_payload` **only here** (this is the trusted human path; Task 1 made every other caller default to False).

- [x] **Step 3: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat(hybrid): one-way set_session_hybrid WS command"
```

---

### Task 9: Terminal backend — attach-only `h:` context

**Files:**
- Modify: `src/twicc/terminal.py` (context parsing ~lines 671–696, `tmux_session_name` ~line 197, spawn path)
- Modify: `frontend/src/composables/useTerminal.js` (`getWsUrl()` lines 311–339, `shouldUseTmux()` lines 296–303)

- [x] **Step 1: Backend**

- `tmux_session_name`: map context `h:<session_id>` → `twicc-hybrid-<sanitized>` (import `hybrid_tmux_session_name` from the hybrid module to avoid drift).
- In the connection handler: a context starting with `h:` is **attach-only**: if the tmux session does not exist, send the exit message the frontend already understands (the one that sets `ptyExited` — check `useTerminal.js` for the exact `type`, it is `pty_exited`-adjacent) and close; never create a shell or a tmux session for it. Attach with `pty.fork()` + `exec tmux -L twicc attach-session -t =<name>` (variant of `spawn_tmux_pty` that uses `attach-session` instead of `new-session -A`).
- `h:` context is reached through the global URL pattern (`/ws/terminal/<index>/?name=h:<session_id>&tmux=1`), which already parses `?name=` — verify the `?name=` branch doesn't reject unknown prefixes.

- [x] **Step 2: Frontend `useTerminal`**

Make `shouldUseTmux()` itself return `true` when `contextKey` starts with `h:` — hybrid terminals are tmux by definition, overriding both the user's `isTerminalUseTmux` setting and the draft/archived guard. Patching the single predicate (rather than just `getWsUrl()`) keeps the ~8 other `shouldUseTmux()` call sites in `useTerminal.js` (pane-state monitor, alternate-screen and exit handling, lines ~355/575/842/1050/1522) consistent with the backend. In `getWsUrl()`: when `contextKey` starts with `h:`, take the global-path branch (no sessionId/projectId in the URL path).

- [x] **Step 3: Manual check**

With a hybrid session running (Task 7 step 5), open a raw WS to `/ws/terminal/0/?name=h:<id>&tmux=1` (or wait for Task 11 and test in the UI) — expect to see the live TUI. After killing the tmux session, a new connection must close cleanly without creating anything (`tmux -L twicc list-sessions` unchanged).

- [x] **Step 4: Commit**

```bash
git add src/twicc/terminal.py frontend/src/composables/useTerminal.js
git commit -m "feat(hybrid): attach-only terminal context for hybrid tmux sessions"
```

---

### Task 10: Frontend — hybrid flag on drafts + toggle UI

**Files:**
- Modify: `frontend/src/stores/data.js` (`createDraftSession` ~line 1105)
- Modify: `frontend/src/components/message/MessageInput.vue` (toolbar near Send/Reset, lines ~1754–1791; payload ~1213–1229)

- [x] **Step 1: Draft flag**

`createDraftSession`: include `hybrid: false` in the draft object (persisted via the existing `saveDraftSession` pass-through). Add a small action `setDraftHybrid(sessionId, value)` that patches the draft + persists.

- [x] **Step 2: Toggle UI**

In the `message-input-actions` toolbar, before the Send button, add a terminal-style toggle button (`wa-button` + `wa-icon name="terminal"`), shown only when: `provider === 'claude_code'` AND the session is not hidden (`!session.hidden` — spec §2.1: not for hidden/orchestrated sessions):
- Draft: toggles `draft.hybrid` freely (visual pressed state).
- Existing non-hybrid session: click opens a `wa-dialog` confirm — "Switch this session to hybrid CLI mode? This cannot be undone." On confirm → `sendWsMessage({type: 'set_session_hybrid', session_id})`.
- Existing hybrid session: button shown pressed + disabled (tooltip "Hybrid CLI mode (permanent)").

- [x] **Step 3: Payload**

In `handleSend()` payload: add `hybrid: session.value?.hybrid === true` only for drafts (`isDraft`).

- [x] **Step 4: Manual check**

In the UI: create a draft, flip the toggle, send. Expect the session to come up hybrid (tmux session exists). Then on a normal session, use the toggle + confirm; check `hybrid` flips in the store (Vue devtools or the WS frame). Confirm the toggle is absent on a Codex session and on a hidden session.

- [x] **Step 5: Commit**

```bash
git add frontend/src/stores/data.js frontend/src/components/message/MessageInput.vue
git commit -m "feat(hybrid): hybrid mode toggle in the composer"
```

---

### Task 11: Frontend — `HybridTerminalBlock`

**Files:**
- Create: `frontend/src/components/message/HybridTerminalBlock.vue`
- Modify: `frontend/src/components/session/detail/SessionItemsList.vue` (footer block, lines ~1517–1534)

- [x] **Step 1: Component**

Follow `PendingRequestForm.vue` for the 3-state pattern (`viewState: 'normal'|'minimized'|'maximized'`, `CollapsedBar` when minimized, `position:absolute; inset:0; z-index:2` when maximized — lines 59–99 and 255–285 are the reference). Content:

- A `TerminalInstance` (`components/terminal/TerminalInstance.vue`) with `contextKey: 'h:' + sessionId`, `active` bound to "hybrid process exists" (see below), default height ~`40dvh` in normal state.
- Connect/show the terminal ONLY when a process state exists for the session (`store.getProcessState(sessionId)` non-null) — before the first send, render a placeholder line: "Claude CLI starts when you send your first message."
- When the process dies (state entry removed), keep the last screen but show the existing disconnect overlay (TerminalInstance has one) — the next send relaunches.
- Badge: when `store.getPendingRequests(sessionId)` contains an entry with `request_type === 'hybrid_terminal'`, show a pulsing badge in the block header / CollapsedBar trailing slot: "Answer in the terminal".
- Mutual-collapse: emit `@expand` and accept a `minimize()` expose, wired with `MessageInput` exactly like `PendingRequestForm` does (SessionItemsList lines 1517–1524).

- [x] **Step 2: Mount + suppression + no sending lock**

In `SessionItemsList.vue` footer:
- Render `HybridTerminalBlock` when `session?.hybrid` (above `MessageInput`).
- Suppress `PendingRequestForm` for hybrid sessions (`v-if="hasPendingRequest && !session?.hybrid"`).
- **Do not lock the composer for hybrid:** `MessageInput` receives `:sending-locked="hasPendingRequest"` (line ~1534) — change to `:sending-locked="hasPendingRequest && !session?.hybrid"`. Spec §2.2: in hybrid mode a send must be possible at any moment (it steers or queues in the TUI), including while a permission prompt is open.

- [x] **Step 3: Manual check (full loop in the browser)**

Hybrid session: terminal block appears, shows the TUI after first send, typing directly in the TUI works, composer send pastes into it, minimize/maximize work. Badge: trigger a permission prompt (ask claude to run a command in `default` mode) → badge appears (with the tool name from the hook payload), composer still sendable; answer the prompt in the TUI → badge clears when the tool_result lands in the JSONL.

- [x] **Step 4: Commit**

```bash
git add frontend/src/components/message/HybridTerminalBlock.vue \
        frontend/src/components/session/detail/SessionItemsList.vue
git commit -m "feat(hybrid): composer terminal block with pending badge"
```

---

### Task 12: Attachments — files + `@` mentions

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/hybrid/agent.py` (replace the `_materialize_attachments` stub)

- [x] **Step 1: Implement**

Input: the SDK-format `images`/`documents` lists from the WS payload (base64 + media type — same shape the SDK path consumes, see `agent.py:636-685`). For each: decode, write to `get_session_hybrid_dir(session_id) / f"att_{secrets.token_hex(6)}.{ext}"` (ext from media type), then prepend mentions to the text:

```
@/path/att_a1b2c3.png
@/path/att_d4e5f6.pdf
<original text>
```

Randomized names are deliberate (verified pitfall: the model can answer from a meaningful filename without reading the content). The dir is covered by `--add-dir` (Task 4) → no permission prompt even in `dontAsk` (verified).

- [x] **Step 2: Manual check**

Attach an image in the composer of a hybrid session, send "describe this image"; expect a correct content-based description and no permission prompt in the TUI.

- [x] **Step 3: Commit**

```bash
git add src/twicc/providers/claude_code/agent/hybrid/agent.py
git commit -m "feat(hybrid): path-based attachments via add-dir and @ mentions"
```

---

### Task 13: Title integration — temp `-n` + `/rename`

**Files:**
- Modify: `src/twicc/providers/claude_code/helpers.py` (`rename_session`, ~line 743)
- Modify: `src/twicc/providers/claude_code/agent/manager.py` (new `rename_hybrid_if_live` helper)
- Reference: `src/twicc/agent/base_manager.py` (`_try_flush_pending_title`, ~line 548), `src/twicc/providers/claude_code/compute.py` (`apply_session_title`, ~line 1580)

- [x] **Step 1: Launch-side** — already done in Task 5 (`-n` = `Session.title` if set, else first 100 chars of the outgoing text). Verified effect: a custom title at launch permanently suppresses the CLI's own ai-title generation.

- [x] **Step 2: Rename on title write**

Both places where TwiCC persists a title funnel into `ClaudeCodeHelpers.rename_session` (`_try_flush_pending_title` → `rename_session`; check whether `compute.apply_session_title` also calls it — if not, add the same branch there). For hybrid sessions with a LIVE hybrid agent, paste `/rename <title>` instead of writing the `custom-title` JSONL line directly (a live claude rewrites state lines on exit and would clobber it). Concretely: add a manager helper `rename_hybrid_if_live(session_id, title) -> bool`; call it first from `rename_session` and fall through to the existing JSONL write when it returns False (dead/non-hybrid — the CLI reads the JSONL line on next `--resume`). Strip newlines from the title before pasting.

- [x] **Step 3: Manual check**

New hybrid session without a custom title: after the first turn, TwiCC's generated title lands and the TUI prompt-box title updates (visible in the terminal status area); the JSONL contains `custom-title` lines with the TwiCC title and **no `ai-title` lines**.

- [x] **Step 4: Commit**

```bash
git add src/twicc/providers/claude_code/helpers.py src/twicc/providers/claude_code/compute.py \
        src/twicc/providers/claude_code/agent/manager.py
git commit -m "feat(hybrid): TwiCC titles via temp -n and pasted /rename"
```

---

### Task 14: Boot adoption of surviving hybrid sessions

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/manager.py`
- Reference: `src/twicc/agent/process_run_cleanup.py` (`cleanup_stale_process_runs`, line 170) and the provider startup hook that restarts cron sessions (find where the Claude Code manager is started at boot — `rg -n "cleanup_stale_process_runs|restart" src/twicc/asgi.py src/twicc/providers/claude_code/`)

- [x] **Step 1: `adopt_running_hybrid_sessions()`**

At manager startup (after stale-ProcessRun cleanup): `hybrid_tmux.list_hybrid_sessions()` → for each name, extract the session id, check `pane_status` alive, check the `Session` row exists and `hybrid=True` (else kill the orphan tmux session and log). For each adoptee: construct a `HybridClaudeAgent` WITHOUT launching (a small `adopt()` classmethod or an `attached=True` start variant that skips `create_session` + paste), register it through the same path `_register_and_start` uses (ProcessRun row + broadcast), state = `USER_TURN` (the JSONL bridge corrects it on the next ingested lines), `agent_pid` from the pane, start the liveness monitor.

- [x] **Step 2: Manual check**

With a live hybrid session, restart the worktree backend (`uv run ./devctl.py restart back` — worktree servers only, never the user's main instance). After boot: the session shows a live process state again, the terminal block reattaches, sending a message pastes into the SAME claude (no relaunch), and hook events still flow (the hooks directory path baked into the running claude's settings is stable across restarts).

- [x] **Step 3: Commit**

```bash
git add src/twicc/providers/claude_code/agent/manager.py
git commit -m "feat(hybrid): adopt surviving hybrid tmux sessions at boot"
```

---

### Task 15: Settings popover polish + Stop-button wiring

**Files:**
- Modify: `frontend/src/providers/claude_code/helpers.js` (the Claude Code override of `baseHelpers.js` — `getFieldHelpText`, line ~595 of `baseHelpers.js`; confirm the exact override file with `rg -l "getFieldHelpText|baseHelpers" frontend/src/providers/claude_code/`)
- Modify: `frontend/src/components/message/AgentSettingsPopover.vue` (only if a context prop is missing)

- [x] **Step 1:** For hybrid sessions, `getFieldHelpText` returns a short note on settings rows: "Applied on next message; some changes restart the CLI." Also surface the design-doc advisory once (e.g. a dismissible hint near the popover or in the terminal block): "Change settings here rather than inside the TUI — TwiCC does not read back TUI-side changes."

- [x] **Step 2: Plug the existing stop affordances into the hybrid kill.** Today the Stop button, the menu entry, and the triple-Escape shortcut stop the SDK process via WS `kill_process` (`asgi.py:568`, frontend `useWebSocket.js:154`) → `manager.kill_agent` → `agent.interrupt_or_kill(reason)`. For hybrid sessions that chain must end in tmux+claude being killed: `HybridClaudeAgent.interrupt_or_kill` → `kill()` → `tmux kill-session` + DEAD broadcast (Task 5). Verify each of the three affordances end-to-end on a live hybrid session (works in any state, including mid-turn); fix the chain if any of them short-circuits on an SDK-only assumption.

- [x] **Step 3: Commit**

```bash
# plus any backend/frontend files touched by chain fixes from Step 2
git add frontend/src/providers/claude_code/helpers.js frontend/src/components/message/AgentSettingsPopover.vue
git commit -m "feat(hybrid): settings guidance and stop wiring"
```

---

### Task 16: Compute — CLI-only line types + original-file capture from file-history

**Files:**
- Modify: `src/twicc/providers/claude_code/compute.py` (line-type classification ~lines 1027–1031; tool-result processing for the capture)
- Modify: `src/twicc/settings.py` (`CLAUDE_CODE_COMPUTE_VERSION`, ~line 272)

This is spec §3.5 (original files) + §3.6 / remaining-verification §5.4.

- [x] **Step 1: Audit**

The classifier already handles `last-prompt`, `attachment`, `permission-mode`, `custom-title`, `mode`, `queue-operation` (compute.py:1027–1031). Two types fall through to the fallback and WILL occur in hybrid usage:
- `ai-title` — written by CLI sessions without a custom title (not ours, but pre-existing standalone CLI sessions have them).
- `queued-command` — written when the user queues input mid-turn in the TUI (normal hybrid usage).

Decide classification for each (most likely `DEBUG_ONLY`, matching the other state-line types — check how `custom-title`/`mode` are classified and mirror). Also verify an `attachment` line carrying a base64 image (`attachment.type == "file"`, `content.type == "image"`) renders something sensible in the session view — if it is hidden as DEBUG_ONLY today, that is acceptable for V1 (the image is also visible in the TUI), but note it in the commit message.

- [x] **Step 2: Original-file capture via file-history**

For hybrid sessions (the SDK path keeps its in-process PreToolUse capture), restore `originalFile` parity from Claude's own checkpoint store, forced on at launch by Task 4's `fileCheckpointingEnabled: true`:

- While computing, track the latest `file-history-snapshot` mapping per session: `trackedFileBackups = { "<cwd-relative path>": {backupFileName, version, backupTime} }` (verified shape).
- When processing the `tool_result` of an Edit/Write/MultiEdit/NotebookEdit `tool_use` in a hybrid session: make the tool's `file_path` relative to the session cwd, look it up in the latest mapping seen so far, read `~/.claude/file-history/<session_id>/<backupFileName>`, and persist the content through the SAME path the SDK-captured original uses today (`rg -n "originalFile|original_file" src/twicc/` to locate it — it is DB-persisted).
- **Copy at ingest, never reference**: claude owns the store and its retention.
- Version subtlety: snapshot lines carry a `messageId`; V1 heuristic = the most recent mapping ingested before the tool line. Verify on a session that edits the SAME file twice (`@v1`/`@v2` bumping) — each diff must show its own "before".

- [x] **Step 3: Bump the compute version**

Any classification/capture change requires bumping `CLAUDE_CODE_COMPUTE_VERSION` — the constant lives in `src/twicc/settings.py:272` (compute.py only carries the reminder comment around line 1016) — so existing sessions get re-computed at next startup.

- [x] **Step 4: Manual check**

Open the hybrid test session in the UI with debug display mode on: no "unknown line type" fallbacks for `queued-command`/`ai-title` lines (create a queued-command by sending a second message in the TUI while a turn runs). Then ask claude (hybrid) to edit the same file twice and check both diffs show the correct pre-edit content.

- [x] **Step 5: Commit**

```bash
git add src/twicc/providers/claude_code/compute.py src/twicc/settings.py
git commit -m "feat(hybrid): classify CLI-only JSONL line types (compute version bump)"
```

---

### Task 17: CHANGELOG + final verification sweep

**Files:**
- Modify: `CHANGELOG.md` (Unreleased section)

- [x] **Step 1:** Add the feature entry under `[Unreleased]`.

- [x] **Step 2: Full manual pass** (worktree servers, real browser):

1. Draft → toggle hybrid → send: tmux session created, TUI visible in the composer block, message answered, items render in the session view, costs update.
2. Send while assistant is mid-turn (steering): paste lands in the TUI input; composer never locked.
3. Slash command from the composer (`/compact`): interpreted by the TUI.
4. Image attachment: described correctly, no permission prompt.
5. Settings: change model **including a context_max change to 1M** (idle — `/model <name>[1m]` pasted on next user-turn; this is remaining-verification §5.2), change permission_mode (startup — CLI restarted with `--resume`, conversation continues).
6. States: assistant_turn/user_turn transitions visible in the session list (JSONL bridge); permission prompt in TUI → badge (PermissionRequest drop); answer → badge clears when the tool_result lands.
7. `/exit` in the TUI → process state dead; next send relaunches with `--resume` and the full history.
8. Idle timeout: temporarily lower `PROCESS_TIMEOUT_USER_TURN` in `src/twicc/settings.py` (~line 285 — it is a hardcoded constant, not env-driven; revert after testing), confirm the tmux session is killed and relaunch-on-send works.
9. Backend restart with live claude → adoption (Task 14 check).
10. Normal SDK sessions: zero regression (create, send, settings, pending requests widget, sending lock).
11. Untrusted project: hybrid launch flags contain the clamped permission mode + `--setting-sources user`, no `--allow-dangerously-skip-permissions`.
12. Drop-request with `"hybrid": true` in a `session:create` file → session created NON-hybrid (Task 1 security check).
13. First hybrid launch in a never-trusted directory: the TUI trust dialog appears in the embedded terminal, and the first pasted message still lands correctly after answering it (design §5.5 — tune `FIRST_PASTE_DELAY` or add a dialog-aware wait if the paste gets swallowed).
14. Original files: hybrid edits show correct "before" content in the diff view, including two successive edits of the same file (file-history capture, Task 16).

- [x] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for hybrid Claude CLI mode"
```

---

## Out of scope (V1) — do not implement

- TUI→TwiCC settings/title back-sync (JSONL `mode`/`permission-mode` lines are ingested but not mirrored to `Session` settings).
- Crons on hybrid sessions (refuse + log).
- Codex hybrid mode.
- Any CLI/skill surface for `hybrid` (human-only, UI-only — security decision).
- Clipboard image paste into the TUI.
- A dedicated mid-turn interrupt command distinct from stop (the stop affordances kill tmux+claude; interrupting without killing stays possible by pressing Escape in the embedded terminal, but no TwiCC affordance is added for it).
- The two V2 ideas noted in design §7: live "Claude is editing…" synthetic indicators via tool hooks, and GUI answering of approvals/AskUserQuestion via bidirectional hooks (drop + status-file wait). Do not implement any part of them.
