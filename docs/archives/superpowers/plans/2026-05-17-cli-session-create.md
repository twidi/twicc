# CLI Session Create Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a top-level `twicc create-session` CLI command that spawns a session in any provider (Claude Code, Codex) from the terminal, with full agent settings, presets, attachments, and prompt-from-file, without any network or auth.

**Architecture:** The CLI imports TwiCC as a library to read the same settings/constants the server reads, validates user inputs locally, then drops a JSON request file in `<data_dir>/sessions-pending/<request_uuid>.json`. A new `PendingSessionsWatcher` on the server picks the file up and calls a shared service `create_session_from_payload()` (extracted from `_handle_send_message`). The watcher writes a status file the CLI polls; the CLI deletes both files once it has a final status. A server-side heartbeat file lets the CLI fail-fast if the server is down.

**Tech Stack:** Python 3.13 (Django ASGI + Channels), Typer (CLI), `watchfiles` (file watching), `orjson` (JSON), `uv` (package manager). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-17-cli-session-create-design.md`

**Note:** This project follows a "no tests, no linting" policy (see CLAUDE.md). Verification is manual:
- Smoke-test imports/instantiation with `uv run python -c "..."` after each backend change.
- Read `logs/backend.log` via `uv run ./devctl.py logs back` after wiring a watcher/task in `cli/run.py`.
- For the end-to-end CLI flow, drop a file by hand or invoke the CLI and observe the status file + backend log.
- Never restart the dev servers yourself — that's a user-reserved operation (see `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/feedback_never_restart_servers.md`).

**Worktree:** All work happens in `/home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create`. Every `Bash` command must `cd` into the worktree first (the editable install otherwise resolves to the main repo — see CLAUDE.md "Worktree Support"). For ad-hoc Python invocations that touch the data dir, also set `TWICC_DATA_DIR=$PWD`.

---

## File Structure

### New backend files

| File | Responsibility |
|------|----------------|
| `src/twicc/core/services/__init__.py` | Empty package init. |
| `src/twicc/core/services/session_creation.py` | `SessionCreationResult` NamedTuple + `create_session_from_payload()` shared service called by both the WS consumer and the new pending sessions watcher. |
| `src/twicc/heartbeat.py` | `heartbeat_loop()` async task that touches `<data_dir>/.server-heartbeat` every 5s. |
| `src/twicc/pending_sessions_watcher.py` | `PendingSessionsWatcher` class: watches `<data_dir>/sessions-pending/`, calls the service, writes status files, runs the boot scan based on drop/status co-presence. |
| `src/twicc/cli/create_session/__init__.py` | Empty package init. |
| `src/twicc/cli/create_session/command.py` | `@app.command("create-session")` Typer entry point; orchestrates discovery → validation → drop → poll → output. |
| `src/twicc/cli/create_session/discovery.py` | `get_data_dir()` + `check_heartbeat()` helpers. Wraps `twicc.paths.get_data_dir()` and reads the heartbeat file. |
| `src/twicc/cli/create_session/bootstrap_local.py` | `load_local_bootstrap(provider)`: in-process equivalent of `GET /api/bootstrap/`. Reads `synced_settings.json`, presets file, and provider helpers' constants. |
| `src/twicc/cli/create_session/validation.py` | Collects all validation errors (provider, agent settings, attachments, prompt, project) into a `list[ValidationError]`; raises `ValidationGroup` if non-empty. |
| `src/twicc/cli/create_session/presets.py` | `apply_preset_and_overrides(preset_name, provider_data, cli_overrides) -> AgentSettings`. Handles the `model`→`selected_model` / `thinking`→`thinking_enabled` rename. |
| `src/twicc/cli/create_session/attachments.py` | `validate_and_encode_attachments(paths, support) -> tuple[list[ImageBlock], list[DocumentBlock]]`. MIME sniffing, size/count checks, base64 encoding. |
| `src/twicc/cli/create_session/project.py` | `resolve_project(project_arg) -> Project`. Heuristic `isdir` vs ID with optional leading `-`. Calls `Project.objects.get_or_create(...)` for paths and broadcasts `project_added`. |
| `src/twicc/cli/create_session/prompt.py` | `resolve_prompt(prompt_arg) -> str`. File-vs-text heuristic. |
| `src/twicc/cli/create_session/drop_file.py` | `write_drop_file(data_dir, payload) -> Path`. Atomic write via `.tmp` + rename. Returns the drop-file path. |
| `src/twicc/cli/create_session/polling.py` | `poll_status(status_path, timeout_seconds) -> dict`. Loops every 100 ms until a final status (`created`, `rejected`, `failed`) appears or timeout. |
| `src/twicc/cli/create_session/output.py` | Text-mode and JSON-mode formatters for progress lines, final result, and validation errors. |

### Modified backend files

| File | What changes |
|------|--------------|
| `src/twicc/providers/helpers.py` | Add `BaseProviderHelpers.get_agent_settings_choices()` and `get_attachment_support()` abstract methods. Extend `get_bootstrap_data()` to include `agent_settings_choices` and `attachment_support` keys per provider. |
| `src/twicc/providers/claude_code/helpers.py` | Add `AGENT_SETTINGS_CHOICES` and `ATTACHMENT_SUPPORT` module-level dicts. Implement both new methods. |
| `src/twicc/providers/codex/helpers.py` | Same as Claude Code, with Codex-specific values (no documents support, no `thinking_enabled`/`claude_in_chrome`). |
| `src/twicc/agent/base_manager.py` | `_start_agent()` (and `create_session()`/`send_to_session()` public entry points if they swallow the return value) returns the canonical session id (`str`). |
| `src/twicc/providers/claude_code/agent/manager.py` | `create_session()` returns the agent's `session_id`. |
| `src/twicc/providers/codex/agent/manager.py` | `create_session()` returns the canonical id minted by `thread_start()`. |
| `src/twicc/asgi.py` | Replace the bulk of `_handle_send_message()`'s body with a call to `create_session_from_payload()`. Keep the WS-specific glue (parsing the payload, sending `error` frames, the `provider_disabled` short-circuit). |
| `src/twicc/cli/run.py` | After `migrate`, launch `heartbeat_loop()` and `PendingSessionsWatcher.start()` as asyncio tasks (alongside the existing orchestrators). |
| `src/twicc/cli/__init__.py` | Register `@app.command("create-session")` at top level (NOT under `session_app`). |

### Frontend cleanup (NOT in this chantier)

The spec §6.1 mentioned a follow-up cleanup of the JS duplicates. After
verifying the actual frontend shape, we **defer** that cleanup: the
front-end currently exposes `AGENT_SETTINGS_CHOICES` as a list of UI-rich
objects `[{value, label, description?, display_label?}, ...]` and
`getAttachmentSupport()` returns a camelCase shape with a `resizeImages`
flag that has no backend counterpart. Unifying the shapes would touch
many UI call sites and risks breaking selects/popovers without any
benefit for v1 (the CLI doesn't need labels — it just needs the `value`
lists).

For v1 we therefore **add** the Python constants and **expose** them via
bootstrap (Task 4) but **do not touch** the frontend. Both sources of
truth coexist; the project memory should track this as known duplication
to revisit if/when i18n or shape unification becomes a goal.

---

## Tasks

### Task 1: Managers — return the canonical session id from `create_session()`

**Why first:** The shared service extracted in Task 5 must hand the canonical id back to its caller. Today, both managers return `None`.

**Files:**
- Modify: `src/twicc/agent/base_manager.py`
- Modify: `src/twicc/providers/claude_code/agent/manager.py`
- Modify: `src/twicc/providers/codex/agent/manager.py`

- [ ] **Step 1: Locate the relevant methods**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
grep -nE "async def create_session|async def _start_agent|async def _register_and_start" \
     src/twicc/agent/base_manager.py \
     src/twicc/providers/claude_code/agent/manager.py \
     src/twicc/providers/codex/agent/manager.py
```

- [ ] **Step 2: Change `_start_agent()` in `base_manager.py` to return `agent.session_id`**

At the end of `_start_agent()` (after `_register_and_start` has returned successfully), return `agent.session_id`. Type annotation becomes `-> str`. Make sure both branches (resume + new) return.

**Precise placement** : `_start_agent` calls `_register_and_start(agent, ...)` inside a try/except block. The `return agent.session_id` must be **inside the try**, after `_register_and_start` completes successfully. Do not move the try boundaries. If `_register_and_start` re-raises, the existing cleanup runs and the exception propagates — that's unchanged.

- [ ] **Step 3: Change `ClaudeCodeAgentManager.create_session()` to return the value**

```python
# providers/claude_code/agent/manager.py — sketch
async def create_session(self, session_id: str, project_id: str, cwd: str,
                         text: str, settings: AgentSettings,
                         images: list | None = None,
                         documents: list | None = None) -> str:
    async with self._lock:
        if session_id in self._agents:
            raise RuntimeError(f"Agent already exists for session {session_id}")
        return await self._start_agent(
            session_id, project_id, cwd, text, resume=False,
            settings=settings, images=images, documents=documents,
        )
```

For Claude Code the returned id is equal to the input `session_id` (the CLI passes its UUID via `--session-id`).

- [ ] **Step 4: Change `CodexAgentManager.create_session()` to return the value**

Mirror the same change. For Codex the returned id is the canonical one minted by `thread_start()`, which differs from the draft `session_id` parameter — that's the entire point of the change.

- [ ] **Step 5: Smoke-test imports**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.codex.agent.manager import CodexAgentManager
import inspect
for cls in (ClaudeCodeAgentManager, CodexAgentManager):
    sig = inspect.signature(cls.create_session)
    print(cls.__name__, '->', sig.return_annotation)
"
```

Expected: both lines show `-> str`.

- [ ] **Step 6: Confirm the WS consumer still type-checks**

`_handle_send_message` currently calls `await manager.create_session(...)` without using the return value — this remains fine. We'll wire the return value through in Task 5.

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/agent/base_manager.py \
        src/twicc/providers/claude_code/agent/manager.py \
        src/twicc/providers/codex/agent/manager.py
git commit -m "refactor(agent): return canonical session id from create_session()"
```

---

### Task 2: Helpers — declare `AGENT_SETTINGS_CHOICES` Python constants

**Why now:** Required by the CLI's validation in Task 12 and (later) by the bootstrap exposure in Task 4. Live constants today only exist in `frontend/src/providers/*/constants.js`.

**Files:**
- Modify: `src/twicc/providers/helpers.py`
- Modify: `src/twicc/providers/claude_code/helpers.py`
- Modify: `src/twicc/providers/codex/helpers.py`

- [ ] **Step 1: Add an abstract method to `BaseProviderHelpers`**

In `src/twicc/providers/helpers.py`, inside `BaseProviderHelpers`:

```python
def get_agent_settings_choices(self) -> dict[str, list]:
    """Return the valid choices per agent-settings field for this provider.

    Keys are field names (subset of `AgentSettings._fields`, never including
    `selected_model` which is covered by `model_registry`). Values are lists
    of valid raw values (strings, bools, or ints depending on the field).

    Used by both the CLI (for pre-flight validation) and the front-end
    bootstrap (to populate select widgets).
    """
    raise NotImplementedError
```

- [ ] **Step 2: Implement for Claude Code**

In `src/twicc/providers/claude_code/helpers.py`, add a module-level constant near the existing settings constants:

```python
AGENT_SETTINGS_CHOICES: dict[str, list] = {
    "effort": ["low", "medium", "high", "xhigh", "max"],
    "permission_mode": ["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"],
    "thinking_enabled": [True, False],
    "context_max": [200_000, 1_000_000],
    "claude_in_chrome": [True, False],
}
```

Then on `ClaudeCodeHelpers`:

```python
def get_agent_settings_choices(self) -> dict[str, list]:
    return AGENT_SETTINGS_CHOICES
```

- [ ] **Step 3: Implement for Codex**

In `src/twicc/providers/codex/helpers.py`:

```python
AGENT_SETTINGS_CHOICES: dict[str, list] = {
    "effort": ["low", "medium", "high", "xhigh"],
    "permission_mode": ["read_only", "strict", "auto", "autonomous", "yolo"],
    "context_max": [272_000],
}
```

Then on `CodexHelpers`:

```python
def get_agent_settings_choices(self) -> dict[str, list]:
    return AGENT_SETTINGS_CHOICES
```

Note: cross-check against `frontend/src/providers/codex/constants.js` — Codex has `LOW, MEDIUM, HIGH, X_HIGH` for effort (no `MAX`) and the five permission modes above. The list must be identical character-for-character or the front-end cleanup in Task 4 will diverge.

- [ ] **Step 4: Smoke-test**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django; django.setup()
from twicc.providers.helpers import get_provider_helpers_registry
for p, h in get_provider_helpers_registry().items():
    print(p.value)
    for k, v in h.get_agent_settings_choices().items():
        print(' ', k, '=', v)
"
```

Expected output: both providers print their choice maps. No `NotImplementedError`.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/providers/helpers.py \
        src/twicc/providers/claude_code/helpers.py \
        src/twicc/providers/codex/helpers.py
git commit -m "feat(providers): expose agent_settings_choices on helpers"
```

---

### Task 3: Helpers — declare `ATTACHMENT_SUPPORT` Python constants

**Files:**
- Modify: `src/twicc/providers/helpers.py`
- Modify: `src/twicc/providers/claude_code/helpers.py`
- Modify: `src/twicc/providers/codex/helpers.py`

- [ ] **Step 1: Add the abstract method**

In `src/twicc/providers/helpers.py`, inside `BaseProviderHelpers`:

```python
def get_attachment_support(self) -> dict:
    """Return the attachment capabilities of this provider.

    Returned dict shape:
        {
            "images": bool,
            "documents": bool,
            "accepted_mime_types": list[str],
            "max_bytes_per_file": int,
            "max_files_per_message": int,
            "max_total_bytes": int,
        }
    """
    raise NotImplementedError
```

- [ ] **Step 2: Implement for Claude Code**

In `src/twicc/providers/claude_code/helpers.py`:

```python
ATTACHMENT_SUPPORT: dict = {
    "images": True,
    "documents": True,
    "accepted_mime_types": [
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "application/pdf", "text/plain",
    ],
    "max_bytes_per_file": 5 * 1024 * 1024,
    "max_files_per_message": 100,
    "max_total_bytes": 32 * 1024 * 1024,
}
```

```python
def get_attachment_support(self) -> dict:
    return ATTACHMENT_SUPPORT
```

- [ ] **Step 3: Implement for Codex**

In `src/twicc/providers/codex/helpers.py`:

```python
ATTACHMENT_SUPPORT: dict = {
    "images": True,
    "documents": False,
    "accepted_mime_types": [
        "image/png", "image/jpeg", "image/gif", "image/webp",
    ],
    "max_bytes_per_file": 5 * 1024 * 1024,
    "max_files_per_message": 100,
    "max_total_bytes": 32 * 1024 * 1024,
}
```

```python
def get_attachment_support(self) -> dict:
    return ATTACHMENT_SUPPORT
```

- [ ] **Step 4: Smoke-test**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django; django.setup()
from twicc.providers.helpers import get_provider_helpers_registry
for p, h in get_provider_helpers_registry().items():
    s = h.get_attachment_support()
    print(p.value, 'doc=', s['documents'], 'mimes=', len(s['accepted_mime_types']))
"
```

Expected: `claude_code doc= True mimes= 6` and `codex doc= False mimes= 4`.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/providers/helpers.py \
        src/twicc/providers/claude_code/helpers.py \
        src/twicc/providers/codex/helpers.py
git commit -m "feat(providers): expose attachment_support on helpers"
```

---

### Task 4: Expose the new keys in `get_bootstrap_data()`

**Note**: this task does NOT modify the frontend. The shapes between Python (added in Tasks 2-3) and the existing JS constants diverge intentionally (the JS ones carry UI labels/descriptions, the Python ones don't). Unifying them would touch many UI call sites with no v1 benefit; the cleanup is left as a future chantier.

**Files:**
- Modify: `src/twicc/providers/helpers.py` (the `get_bootstrap_data()` method on `BaseProviderHelpers`)

- [ ] **Step 1: Add the two new keys to `get_bootstrap_data()`**

`get_bootstrap_data` is defined on `BaseProviderHelpers` at `src/twicc/providers/helpers.py:527-588`. Find the returned dict (around line 565 already has `model_registry`) and add:

```python
"agent_settings_choices": self.get_agent_settings_choices(),
"attachment_support": self.get_attachment_support(),
```

- [ ] **Step 2: Smoke-test the bootstrap shape**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django; django.setup()
from twicc.providers.helpers import get_provider_helpers_registry
for p, h in get_provider_helpers_registry().items():
    data = h.get_bootstrap_data()
    print(p.value, 'has_choices=', 'agent_settings_choices' in data,
          'has_attach=', 'attachment_support' in data)
"
```

Expected: both providers print `has_choices= True has_attach= True`.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/providers/helpers.py
git commit -m "feat(bootstrap): expose agent_settings_choices and attachment_support"
```

---

### Task 5: Service — extract `create_session_from_payload()`

**Files:**
- Create: `src/twicc/core/services/__init__.py`
- Create: `src/twicc/core/services/session_creation.py`

- [ ] **Step 1: Create the package**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
mkdir -p src/twicc/core/services
```

Then write `src/twicc/core/services/__init__.py`:

```python
"""Shared business-logic services callable from multiple entry points
(WebSocket consumer, pending-sessions watcher, REST handlers if any later).
"""
```

- [ ] **Step 2: Define the result type and the service**

Create `src/twicc/core/services/session_creation.py`:

```python
"""Create a new agent session from a generic payload.

Called by both ``WSConsumer._handle_send_message`` (when the front-end sends
``send_message``) and ``PendingSessionsWatcher`` (when the CLI drops a
request file). Centralises validation, project resolution, pending-settings
stashing, and agent-manager invocation so both entry points stay in sync.

The function does NOT raise for business-rule errors (missing project,
disabled provider, etc.); it returns a :class:`SessionCreationResult` with
``success=False`` and a list of structured error dicts. Unexpected
exceptions propagate normally and are the caller's responsibility to
translate (e.g. to ``status: failed`` in the watcher).
"""

from __future__ import annotations

from typing import NamedTuple

from twicc.core.enums import Provider
from twicc.pending_agent_settings import set_pending_agent_settings
from twicc.pending_titles import set_pending_title
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.providers.state import (
    ProviderDisabledError,
    ensure_provider_running,
)


class SessionCreationError(NamedTuple):
    field: str
    code: str
    message: str


class SessionCreationResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[SessionCreationError] | None


async def create_session_from_payload(payload: dict) -> SessionCreationResult:
    """Create a new session from a normalised payload.

    Expected keys in ``payload``:
    - ``session_id``: client-supplied UUID (used as Claude Code session id;
      Codex mints its own and the canonical id is returned).
    - ``project_id``: must exist in DB with ``directory`` set.
    - ``provider``: string value of ``Provider`` enum.
    - ``text``: non-empty for new sessions.
    - ``title``: optional, max 200 chars.
    - ``images``, ``documents``: lists of SDK block dicts (already validated
      by the caller — the service does not re-validate attachments).
    - Plus all six ``AgentSettings`` fields (``None`` = use synced default).
    """
    # --- payload extraction (defensive, no schema validation) ----
    session_id = payload.get("session_id")
    project_id = payload.get("project_id")
    provider_str = payload.get("provider")
    text = (payload.get("text") or "").strip()
    title = payload.get("title")
    images = payload.get("images") or []
    documents = payload.get("documents") or []

    errors: list[SessionCreationError] = []
    if not session_id:
        errors.append(SessionCreationError("session_id", "missing", "session_id is required"))
    if not project_id:
        errors.append(SessionCreationError("project_id", "missing", "project_id is required"))
    if not provider_str:
        errors.append(SessionCreationError("provider", "missing", "provider is required"))
    if not text:
        errors.append(SessionCreationError("text", "empty_text", "text is required for a new session"))
    if errors:
        return SessionCreationResult(False, None, None, None, errors)

    # --- provider resolution ---------------------------------------
    try:
        provider = Provider(provider_str)
    except ValueError:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("provider", "unknown_provider", f"Unknown provider: {provider_str}")
        ])

    # --- runtime gate ----------------------------------------------
    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("provider", "provider_disabled", str(e))
        ])

    # --- project directory ----------------------------------------
    from twicc.core.models import Project
    from asgiref.sync import sync_to_async
    try:
        project = await sync_to_async(Project.objects.get)(id=project_id)
    except Project.DoesNotExist:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("project_id", "project_not_found",
                                  f"Project {project_id!r} not found")
        ])
    cwd = project.directory
    if not cwd:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("project_id", "project_no_directory",
                                  f"Project {project_id!r} has no directory set")
        ])

    # --- build agent settings from the closed bundle --------------
    agent_settings = AgentSettings(**{
        field: payload.get(field) for field in AgentSettings._fields
    })

    # --- title --------------------------------------------------------
    if title is not None:
        title_s = title.strip()
        if not title_s or len(title_s) > 200:
            return SessionCreationResult(False, None, None, None, [
                SessionCreationError("title", "invalid_title",
                                      "Title must be non-empty and at most 200 chars")
            ])
        set_pending_title(session_id, title_s)

    # --- stash agent settings (consumed by the watcher when it creates
    #     the Session row from the JSONL) ---------------------------
    set_pending_agent_settings(session_id, agent_settings)

    # --- resolve to effective settings (None -> synced default) --
    helpers = get_provider_helpers(provider)
    effective = helpers.resolve_agent_settings(agent_settings)
    # enforce_agent_settings_consistency RETURNS an AgentSettings (may be
    # the same instance if no demotion was needed, or a fresh one via
    # _replace). Capture it.
    effective = helpers.enforce_agent_settings_consistency(effective)

    # --- invoke the agent manager --------------------------------
    from twicc.agent.registry import get_agent_manager_registry
    manager = get_agent_manager_registry().get(provider)
    try:
        canonical_id = await manager.create_session(
            session_id, project_id, cwd, text,
            settings=effective, images=images, documents=documents,
        )
    except RuntimeError as e:
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("session", "manager_busy", str(e))
        ])

    return SessionCreationResult(
        success=True,
        session_id=canonical_id,
        provider=provider.value,
        project_id=project_id,
        errors=None,
    )
```

Carefully cross-reference each block against the current `_handle_send_message` body in `src/twicc/asgi.py` (lines ~576–824 per the spec). The goal is "behavioural equivalence" for the new-session branch. Resume-session paths stay in the WS consumer for now (this service only covers new sessions for v1).

- [ ] **Step 3: Smoke-test the module import**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django; django.setup()
from twicc.core.services.session_creation import (
    create_session_from_payload, SessionCreationResult, SessionCreationError,
)
print('ok')
"
```

Expected: `ok`. Any `ImportError` means a function the service uses (`ensure_provider_running`, `set_pending_agent_settings`, etc.) was renamed since the spec was written — go fix the import.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/core/services/
git commit -m "feat(services): add create_session_from_payload shared service"
```

---

### Task 6: Refactor `_handle_send_message` to call the service

**Files:**
- Modify: `src/twicc/asgi.py`

- [ ] **Step 1: Locate `_handle_send_message`**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
grep -n "_handle_send_message" src/twicc/asgi.py
```

- [ ] **Step 2: Identify the two branches and scope the refactor**

Read the function. It has two clear branches inside the `_handle_send_message` body:

- **`if exists:` (resume path)** — looks up the Session row, updates its agent settings, calls `manager.send_to_session()`, broadcasts `session_updated`. **DO NOT TOUCH.** This branch handles existing sessions and is out of scope for v1.
- **`else:` (new-session path)** — extracts the payload, sets pending settings/title, calls `manager.create_session()`. **THIS** is what gets refactored to call the new service.

Identify the exact `else:` block boundaries before editing. Everything related to the new session creation lives inside this branch.

- [ ] **Step 3: Replace ONLY the new-session branch body with a call to the service**

Inside the `else:` (new-session) branch, replace its body with:

```python
# else: # new session
payload = {
    "session_id": session_id,
    "project_id": project_id,
    "provider": content.get("provider"),
    "text": content.get("text") or "",
    "title": content.get("title"),
    "images": content.get("images") or [],
    "documents": content.get("documents") or [],
    **{field: content.get(field) for field in AgentSettings._fields},
}

result = await create_session_from_payload(payload)
if not result.success:
    # Translate the first error to the WS-specific error frame shape.
    # The frontend already understands the error codes the service emits
    # (provider_disabled, project_not_found, etc.).
    first = result.errors[0]
    await self.send_json({
        "type": "error",
        "code": first.code,
        "message": first.message,
    })
    return
# Success: nothing to send back. The agent manager + watcher will emit
# the usual broadcasts and the front-end picks them up.
```

The `if exists:` branch above is **byte-for-byte untouched**. Everything related to resume sessions (Session update, send_to_session, session_updated broadcast) stays as-is.

Make sure the early validation that runs **before** the if/else (session_id/project_id presence checks emitting `error` frames with the legacy WS shape) is also preserved as-is — the service expects those keys to be present.

- [ ] **Step 4: Smoke-test the WS consumer can still be imported**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django; django.setup()
from twicc.asgi import WSConsumer
print('WSConsumer ok, _handle_send_message exists:', hasattr(WSConsumer, '_handle_send_message'))
"
```

- [ ] **Step 5: Manual sanity check (user-reserved restart)**

Tell the user: "Please restart the backend (`uv run ./devctl.py restart back` from the worktree). Then open the UI and try to create a brand-new session — it should behave exactly as before. If it doesn't, the service translation lost a behavior."

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/asgi.py
git commit -m "refactor(asgi): wire _handle_send_message through create_session_from_payload"
```

---

### Task 7: Heartbeat task

**Files:**
- Create: `src/twicc/heartbeat.py`

- [ ] **Step 1: Write the module**

```python
"""Heartbeat file written by the live server.

The CLI reads ``<data_dir>/.server-heartbeat`` to fail-fast when no server
is running (or the server is still starting up before the heartbeat task
has launched). The file is empty; only its mtime matters.

Period: 5 seconds. The CLI's staleness threshold is 15 seconds (3× the
period) to absorb GC pauses and load spikes.
"""

from __future__ import annotations

import asyncio
import logging
import os

from twicc.paths import get_data_dir

logger = logging.getLogger(__name__)

HEARTBEAT_PERIOD_SECONDS = 5
HEARTBEAT_FILENAME = ".server-heartbeat"


async def heartbeat_loop() -> None:
    """Touch ``<data_dir>/.server-heartbeat`` forever.

    Designed to be launched as an asyncio background task once the boot
    sequence (in particular ``migrate``) has completed.
    """
    path = get_data_dir() / HEARTBEAT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            path.touch(exist_ok=True)
            os.chmod(path, 0o600)  # idempotent
        except Exception:
            logger.exception("heartbeat: failed to update %s", path)
        await asyncio.sleep(HEARTBEAT_PERIOD_SECONDS)
```

- [ ] **Step 2: Smoke-test that it can be imported and the path resolves correctly**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django; django.setup()
from twicc.heartbeat import heartbeat_loop, HEARTBEAT_FILENAME
from twicc.paths import get_data_dir
print('data_dir:', get_data_dir())
print('heartbeat path will be:', get_data_dir() / HEARTBEAT_FILENAME)
"
```

Expected: prints a path inside the current worktree (not `~/.twicc/`).

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/heartbeat.py
git commit -m "feat(heartbeat): add server heartbeat file for CLI liveness checks"
```

---

### Task 8: `PendingSessionsWatcher`

**Files:**
- Create: `src/twicc/pending_sessions_watcher.py`

- [ ] **Step 1: Write the module**

Most of the structure is given verbatim in spec §6.4 — implement it.

```python
"""Watcher for CLI-dropped session-creation requests.

Watches ``<data_dir>/sessions-pending/`` for new ``<request_uuid>.json``
files dropped by ``twicc create-session``. Calls
:func:`create_session_from_payload` and writes a ``<request_uuid>.status.json``
file the CLI polls. Cleanup is the CLI's responsibility in the nominal
case; this watcher only handles dead-letter cleanup at boot (see
spec §5.5).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from datetime import datetime, timezone

import orjson
from watchfiles import Change, awatch

from twicc.core.services.session_creation import create_session_from_payload
from twicc.paths import get_data_dir


def __iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

logger = logging.getLogger(__name__)

DIRECTORY_NAME = "sessions-pending"
DROP_SUFFIX = ".json"
STATUS_SUFFIX = ".status.json"
TMP_SUFFIX = ".tmp"


class PendingSessionsWatcher:
    def __init__(self) -> None:
        self.directory = get_data_dir() / DIRECTORY_NAME
        self._in_flight: set[str] = set()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Start awatch FIRST to avoid missing files dropped during the boot scan
        watch_task = asyncio.ensure_future(self._watch_loop())
        await self._scan_existing()
        await self._cleanup_orphan_status_files()
        try:
            await watch_task
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Watch loop
    # ------------------------------------------------------------------
    async def _watch_loop(self) -> None:
        async for changes in awatch(self.directory, stop_event=self._stop):
            for change_type, raw_path in changes:
                p = Path(raw_path)
                if change_type != Change.added:
                    continue
                if p.name.endswith(STATUS_SUFFIX) or p.name.endswith(TMP_SUFFIX):
                    continue
                if p.suffix != DROP_SUFFIX:
                    continue
                if p.stem in self._in_flight:
                    continue
                asyncio.ensure_future(self._process_file(p))

    # ------------------------------------------------------------------
    # Boot scan
    # ------------------------------------------------------------------
    async def _scan_existing(self) -> None:
        for p in sorted(self.directory.glob(f"*{DROP_SUFFIX}")):
            if p.name.endswith(STATUS_SUFFIX) or p.name.endswith(TMP_SUFFIX):
                continue
            if p.stem in self._in_flight:
                continue
            status_path = self.directory / f"{p.stem}{STATUS_SUFFIX}"
            if status_path.exists():
                # CLI crashed before deleting both files. Session already created
                # / rejected / failed — just clean up.
                logger.info("[PendingSessionsWatcher] boot cleanup drop+status %s", p.stem)
                p.unlink(missing_ok=True)
                status_path.unlink(missing_ok=True)
            else:
                # Drop file orphaned by a server restart — process normally, no
                # timing check (cf. spec §5.5).
                logger.info("[PendingSessionsWatcher] boot processes drop %s", p.stem)
                asyncio.ensure_future(self._process_file(p))

    async def _cleanup_orphan_status_files(self) -> None:
        for p in sorted(self.directory.glob(f"*{STATUS_SUFFIX}")):
            request_uuid = p.name[:-len(STATUS_SUFFIX)]
            drop_path = self.directory / f"{request_uuid}{DROP_SUFFIX}"
            if not drop_path.exists():
                logger.info("[PendingSessionsWatcher] boot cleanup orphan status %s", request_uuid)
                p.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Per-file processing
    # ------------------------------------------------------------------
    async def _process_file(self, path: Path) -> None:
        request_uuid = path.stem
        self._in_flight.add(request_uuid)
        try:
            try:
                content = await asyncio.to_thread(path.read_bytes)
                data = await asyncio.to_thread(orjson.loads, content)
            except Exception as e:
                logger.exception("[PendingSessionsWatcher] parse failed for %s", request_uuid)
                await self._write_status(request_uuid, {
                    "status": "failed",
                    "error": f"Could not parse drop-file: {e}",
                })
                return

            await self._write_status(request_uuid, {"status": "received"})
            logger.info("[PendingSessionsWatcher] received %s", request_uuid)

            try:
                payload = data.get("payload") or {}
                result = await create_session_from_payload(payload)
            except Exception as e:
                logger.exception("[PendingSessionsWatcher] service raised for %s", request_uuid)
                await self._write_status(request_uuid, {
                    "status": "failed",
                    "error": f"{type(e).__name__}: {e}",
                })
                return

            if result.success:
                logger.info("[PendingSessionsWatcher] created %s -> %s",
                            request_uuid, result.session_id)
                await self._write_status(request_uuid, {
                    "status": "created",
                    "session_id": result.session_id,
                    "provider": result.provider,
                    "project_id": result.project_id,
                })
            else:
                logger.warning("[PendingSessionsWatcher] rejected %s: %s",
                               request_uuid, result.errors)
                await self._write_status(request_uuid, {
                    "status": "rejected",
                    "errors": [e._asdict() for e in (result.errors or [])],
                })
            # Cleanup is the CLI's job in the nominal case (cf. spec §5.5).
        finally:
            self._in_flight.discard(request_uuid)

    # ------------------------------------------------------------------
    # Status file writer (atomic via tmp + rename)
    # ------------------------------------------------------------------
    async def _write_status(self, request_uuid: str, data: dict) -> None:
        # Merge timestamps. The CLI relies on them for the wording.
        data.setdefault("request_uuid", request_uuid)
        if data["status"] == "received":
            data.setdefault("received_at", _iso_now())
        elif data["status"] == "created":
            data.setdefault("created_at", _iso_now())
        elif data["status"] == "rejected":
            data.setdefault("rejected_at", _iso_now())
        elif data["status"] == "failed":
            data.setdefault("failed_at", _iso_now())

        path = self.directory / f"{request_uuid}{STATUS_SUFFIX}"
        tmp = path.with_suffix(path.suffix + TMP_SUFFIX)
        await asyncio.to_thread(tmp.write_bytes, orjson.dumps(data))
        await asyncio.to_thread(os.replace, tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


_watcher_instance: PendingSessionsWatcher | None = None


def get_pending_sessions_watcher() -> PendingSessionsWatcher:
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = PendingSessionsWatcher()
    return _watcher_instance
```

- [ ] **Step 2: Smoke-test imports**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django; django.setup()
from twicc.pending_sessions_watcher import (
    PendingSessionsWatcher, get_pending_sessions_watcher,
)
w = get_pending_sessions_watcher()
print('directory:', w.directory)
"
```

Expected: prints a path inside the worktree.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/pending_sessions_watcher.py
git commit -m "feat(watcher): add PendingSessionsWatcher for CLI-dropped session requests"
```

---

### Task 9: Wire heartbeat + watcher into the boot sequence

**Important code-shape note** : `cli/run.py` is structured as a sync `main()` (which calls `call_command("migrate", ...)` on line 246 and then `asyncio.run(run_server(port_int))` on line 270) plus an async `run_server(port: int)` (line 129) that starts the orchestrators, the price/version periodic tasks, configures `uvicorn.Config` + `uvicorn.Server`, and finally `await server.serve()` (line 194).

Per spec §6.3, "rien ne tourne avant `migrate`". `migrate` is already in the sync `main()` *before* `asyncio.run(run_server(...))`, so by the time `run_server` starts, the migrate has completed. The heartbeat and the watcher are async tasks → they live inside `run_server`, **not** in `main`. They should be created as `asyncio.create_task(...)` alongside the existing `price_sync_task` / `version_check_task` (line 167-168).

**Files:**
- Modify: `src/twicc/cli/run.py`

- [ ] **Step 1: Locate the cross-provider periodic-tasks section**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
grep -n "price_sync_task\|version_check_task\|server.serve" src/twicc/cli/run.py
```

You should see `price_sync_task` on line 167 and `version_check_task` on line 168, both as `asyncio.create_task(...)` calls. The two new tasks go right next to them.

- [ ] **Step 2: Add the heartbeat + watcher tasks in `run_server()`**

Insert immediately after `version_check_task = asyncio.create_task(start_version_check_task())` (around line 168):

```python
# CLI session-create plumbing (cf. docs/superpowers/specs/2026-05-17-cli-session-create-design.md)
from twicc.heartbeat import heartbeat_loop
from twicc.pending_sessions_watcher import get_pending_sessions_watcher
heartbeat_task = asyncio.create_task(heartbeat_loop())
pending_watcher_task = asyncio.create_task(get_pending_sessions_watcher().start())
```

- [ ] **Step 3: Handle shutdown for the two new tasks**

In the `finally:` block (line 195 onward), find where `price_sync_task` / `version_check_task` are cancelled / awaited and add the same treatment for `heartbeat_task` and `pending_watcher_task`. Specifically:

```python
# In the finally block, alongside the existing cancels:
heartbeat_task.cancel()
pending_watcher_task.cancel()
# Then awaited together in the gather() call (or its equivalent) that the
# finally block uses — mirror the pattern used for price_sync_task.
```

Read the existing pattern (the `finally` block has its own logic) and reproduce it. Don't invent shutdown semantics.

- [ ] **Step 4: Smoke-test the import**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
import django; django.setup()
from twicc.cli.run import run_server
print('run_server imports cleanly')
"
```

- [ ] **Step 5: Manual smoke test (user-reserved restart)**

After the user restarts the backend:
- Check that `<worktree>/.server-heartbeat` exists and its `mtime` advances every 5 seconds:
  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
  stat -c "%Y %n" .server-heartbeat
  sleep 6
  stat -c "%Y %n" .server-heartbeat
  ```
  The two timestamps should differ by ~5–6s.
- Check that `<worktree>/sessions-pending/` was created (mode 0700, owner-only):
  ```bash
  ls -ld sessions-pending/
  ```
- Tail `logs/backend.log` for any error involving `heartbeat` or `PendingSessionsWatcher`.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/cli/run.py
git commit -m "feat(boot): launch heartbeat + PendingSessionsWatcher in run_server"
```

---

### Task 10: CLI skeleton — discovery + bootstrap + Typer command

**Files:**
- Create: `src/twicc/cli/create_session/__init__.py`
- Create: `src/twicc/cli/create_session/command.py`
- Create: `src/twicc/cli/create_session/discovery.py`
- Create: `src/twicc/cli/create_session/bootstrap_local.py`
- Modify: `src/twicc/cli/__init__.py`

This task adds the empty skeleton + a stub command that does heartbeat check + bootstrap load + prints. No real validation, drop, or polling yet — those come in Tasks 11–13.

- [ ] **Step 1: Create the package init**

`src/twicc/cli/create_session/__init__.py`:

```python
"""CLI subpackage implementing ``twicc create-session``."""
```

- [ ] **Step 2: Write `discovery.py`**

```python
"""Discovery helpers for the CLI: data dir + heartbeat check."""

from __future__ import annotations

import time
from pathlib import Path

HEARTBEAT_FILENAME = ".server-heartbeat"
HEARTBEAT_STALE_AFTER_SECONDS = 15


class ServerDownError(Exception):
    """Raised when the heartbeat is missing or stale."""


def get_data_dir() -> Path:
    """Return the TwiCC data directory.

    Thin wrapper around :func:`twicc.paths.get_data_dir` so the CLI
    package doesn't reach into the wider twicc code apart from the
    well-defined helpers.
    """
    from twicc.paths import get_data_dir as _get
    return _get()


def check_heartbeat(data_dir: Path | None = None) -> float:
    """Verify the server's heartbeat file is fresh.

    Returns the age in seconds (for telemetry / output). Raises
    :class:`ServerDownError` if the file is missing or stale.
    """
    if data_dir is None:
        data_dir = get_data_dir()
    path = data_dir / HEARTBEAT_FILENAME
    if not path.exists():
        raise ServerDownError(
            "TwiCC server does not appear to be running "
            "(or is still starting up). Run `twicc` in another terminal "
            "and wait until it is ready."
        )
    age = time.time() - path.stat().st_mtime
    if age > HEARTBEAT_STALE_AFTER_SECONDS:
        raise ServerDownError(
            f"TwiCC server is unresponsive (last heartbeat {int(age)}s ago). "
            f"Make sure it is still running."
        )
    return age
```

- [ ] **Step 3: Write `bootstrap_local.py`**

```python
"""Load the same data ``/api/bootstrap/`` returns, but in-process.

Used by the CLI to validate user inputs without making any HTTP call.
"""

from __future__ import annotations

from typing import NamedTuple

from twicc.agent_settings_presets import read_agent_settings_presets
from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.synced_settings import read_synced_settings


class ProviderBootstrap(NamedTuple):
    provider: Provider
    is_disabled: bool
    agent_settings_categories: dict
    agent_settings_choices: dict
    model_registry: list
    attachment_support: dict
    presets: list


class LocalBootstrap(NamedTuple):
    disabled_providers_present: bool
    disabled_providers: list[str]
    providers: dict[str, ProviderBootstrap]


def load_local_bootstrap() -> LocalBootstrap:
    """Build the bootstrap snapshot from on-disk + in-code sources."""
    synced = read_synced_settings()
    disabled_present = "disabledProviders" in synced
    disabled = synced.get("disabledProviders") or []

    providers: dict[str, ProviderBootstrap] = {}
    for provider, helpers in get_provider_helpers_registry().items():
        provider_data = helpers.get_bootstrap_data() or {}
        presets = read_agent_settings_presets(provider).get("presets", [])
        providers[provider.value] = ProviderBootstrap(
            provider=provider,
            is_disabled=provider.value in disabled,
            agent_settings_categories=provider_data.get("agent_settings_categories", {}),
            agent_settings_choices=provider_data.get("agent_settings_choices", {}),
            model_registry=provider_data.get("model_registry", []),
            attachment_support=provider_data.get("attachment_support", {}),
            presets=presets,
        )

    return LocalBootstrap(
        disabled_providers_present=disabled_present,
        disabled_providers=disabled,
        providers=providers,
    )
```

- [ ] **Step 4: Write the stub `command.py`**

```python
"""Top-level ``twicc create-session`` command (stub)."""

from __future__ import annotations

import typer


def create_session_cmd(
    prompt: str = typer.Argument(..., help="Prompt text, or path to a file whose content is the prompt."),
    project: str | None = typer.Option(None, "--project", help="Project id or directory (path absolute or relative). Defaults to cwd."),
    provider: str = typer.Option(..., "--provider", help="claude_code or codex."),
    preset: str | None = typer.Option(None, "--preset"),
    model: str | None = typer.Option(None, "--model"),
    effort: str | None = typer.Option(None, "--effort"),
    permission_mode: str | None = typer.Option(None, "--permission-mode"),
    thinking: bool | None = typer.Option(None, "--thinking/--no-thinking"),
    claude_in_chrome: bool | None = typer.Option(None, "--claude-in-chrome/--no-claude-in-chrome"),
    context_max: int | None = typer.Option(None, "--context-max"),
    title: str | None = typer.Option(None, "--title"),
    attach: list[str] = typer.Option([], "--attach", help="Path to a file to attach."),
    timeout: int = typer.Option(30, "--timeout", help="Polling timeout in seconds."),
    no_color: bool = typer.Option(False, "--no-color"),
    json_output: bool = typer.Option(False, "--json", help="Emit a single JSON object instead of pretty text."),
) -> None:
    """Create a new session by dropping a request file the server will pick up."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli.create_session.discovery import check_heartbeat, ServerDownError
    from twicc.cli.create_session.bootstrap_local import load_local_bootstrap

    try:
        age = check_heartbeat()
    except ServerDownError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)

    bootstrap = load_local_bootstrap()
    typer.echo(f"✓ Heartbeat OK (last seen {age:.1f}s ago)")
    typer.echo(f"✓ Bootstrap loaded ({len(bootstrap.providers)} providers, "
               f"{sum(len(p.presets) for p in bootstrap.providers.values())} presets total)")
    typer.echo("(stub — validation, drop, polling not implemented yet)")
```

- [ ] **Step 5: Register the command at top level in `cli/__init__.py`**

Find where other top-level commands are registered (e.g. `@app.command()` for `usage`, `search`, etc.). Add:

```python
from twicc.cli.create_session.command import create_session_cmd
app.command(name="create-session")(create_session_cmd)
```

Place this import LAZILY (inside `main()` if necessary) to avoid paying for the Typer subcommand assembly during simple `twicc --help` calls — match the existing patterns in `cli/__init__.py`.

If `cli/__init__.py` registers all commands at module top-level, follow the same pattern (the `create_session_cmd` function itself is light and only does Django setup inside its body).

- [ ] **Step 6: Smoke-test the help**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run twicc create-session --help
```

Expected: help text lists all options, no traceback.

- [ ] **Step 7: Smoke-test the stub (requires the backend to be running for the heartbeat check)**

Tell the user: "Please make sure the backend is running in the worktree (`uv run ./devctl.py status`). If yes, I'll run the stub."

Then:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code "hello"
```

Expected: three ✓ lines. If `ServerDownError`, the user needs to start the backend first.

- [ ] **Step 8: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/cli/create_session/ src/twicc/cli/__init__.py
git commit -m "feat(cli): add create-session command skeleton (heartbeat + bootstrap stub)"
```

---

### Task 11: CLI — prompt + project + preset resolution

**Files:**
- Create: `src/twicc/cli/create_session/prompt.py`
- Create: `src/twicc/cli/create_session/project.py`
- Create: `src/twicc/cli/create_session/presets.py`
- Modify: `src/twicc/cli/create_session/command.py`

- [ ] **Step 1: Write `prompt.py`**

```python
"""Resolve the positional ``PROMPT`` argument.

If the value points to an existing file (absolute or relative), read its
UTF-8 content. Otherwise treat the value as the prompt text.
"""

from __future__ import annotations

import os


class PromptError(Exception):
    pass


def resolve_prompt(prompt_arg: str) -> str:
    if os.path.isfile(prompt_arg):
        try:
            with open(prompt_arg, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError as e:
            raise PromptError(f"prompt: file {prompt_arg!r} is not valid UTF-8: {e}")
        if not text.strip():
            raise PromptError(f"prompt: file {prompt_arg!r} is empty")
        return text
    if not prompt_arg.strip():
        raise PromptError("prompt is empty")
    return prompt_arg
```

- [ ] **Step 2: Write `project.py`**

```python
"""Resolve the ``--project`` argument to a ``Project`` row.

Heuristic:
- ``os.path.isdir(value)`` → path. Resolve realpath, ``get_or_create``.
- otherwise → project id. Try ``value`` first, then ``"-" + value``
  (sucre syntaxique for the common case where the id starts with a
  dash from a leading ``/`` in the original path).
- ``--project`` absent → default to ``os.getcwd()``.
"""

from __future__ import annotations

import os
from typing import NamedTuple


class ProjectError(Exception):
    pass


class ResolvedProject(NamedTuple):
    project_id: str
    directory: str
    created: bool


def resolve_project(project_arg: str | None) -> ResolvedProject:
    from twicc.core.models import Project
    from twicc.paths import path_to_project_id

    if project_arg is None or project_arg == "":
        project_arg = os.getcwd()

    if os.path.isdir(project_arg):
        resolved_dir = os.path.realpath(project_arg)
        project_id = path_to_project_id(resolved_dir)
        project, created = Project.objects.get_or_create(
            id=project_id,
            defaults={"directory": resolved_dir},
        )
        if not project.directory:
            project.directory = resolved_dir
            project.save(update_fields=["directory"])
        return ResolvedProject(project_id=project.id,
                               directory=project.directory,
                               created=created)

    # Treat as canonical id. Try the value as-is, then with a leading "-".
    for candidate in (project_arg, "-" + project_arg):
        try:
            project = Project.objects.get(id=candidate)
        except Project.DoesNotExist:
            continue
        if not project.directory:
            raise ProjectError(
                f"--project: project {project.id!r} exists but has no directory set"
            )
        return ResolvedProject(project_id=project.id,
                               directory=project.directory,
                               created=False)

    raise ProjectError(
        f"--project: {project_arg!r} is neither an existing directory "
        f"nor a known project_id (tried also with leading '-')."
    )
```

Note: the broadcast of `project_added` for new projects is omitted here to keep the function pure. The CLI is best-effort about this — the front-end will see the project on its next refresh. If the broadcast becomes important, add a follow-up commit calling `broadcast_to_updates({"type": "project_added", ...})` from `command.py` after a successful create.

- [ ] **Step 3: Write `presets.py`**

```python
"""Resolve ``--preset`` lookup and the merge with CLI overrides.

The preset file uses the historical keys ``model`` and ``thinking`` which
map to ``selected_model`` and ``thinking_enabled`` on ``AgentSettings``.
"""

from __future__ import annotations

from twicc.providers.helpers import AgentSettings

PRESET_KEY_MAP = {
    "model": "selected_model",
    "thinking": "thinking_enabled",
}


class PresetError(Exception):
    pass


def find_preset(presets: list[dict], name: str) -> dict | None:
    for p in presets:
        if p.get("name") == name:
            return p
    return None


def apply_preset_and_overrides(
    preset_name: str | None,
    presets: list[dict],
    overrides: dict[str, object | None],
) -> AgentSettings:
    """Build the final ``AgentSettings`` from preset + per-flag overrides.

    Order:
      1. Start with all-None.
      2. If a preset is named, merge its values (after key remapping).
      3. Each non-None override replaces the corresponding field.

    A field that is neither in the preset nor in the overrides stays
    ``None`` and the back will fall back to the synced default.
    """
    fields = {name: None for name in AgentSettings._fields}

    if preset_name is not None:
        preset = find_preset(presets, preset_name)
        if preset is None:
            names = ", ".join(p.get("name", "<unnamed>") for p in presets) or "<empty>"
            raise PresetError(
                f"Preset {preset_name!r} not found. Available: {names}"
            )
        for raw_key, raw_value in preset.items():
            if raw_key == "name":
                continue
            field = PRESET_KEY_MAP.get(raw_key, raw_key)
            if field in fields:
                fields[field] = raw_value

    for field, value in overrides.items():
        if value is None:
            continue
        if field in fields:
            fields[field] = value

    return AgentSettings(**fields)
```

- [ ] **Step 4: Wire the three modules into `command.py`**

Replace the stub body with:

```python
from twicc.cli.create_session.prompt import resolve_prompt, PromptError
from twicc.cli.create_session.project import resolve_project, ProjectError
from twicc.cli.create_session.presets import apply_preset_and_overrides, PresetError

# ... after heartbeat + bootstrap:

try:
    text = resolve_prompt(prompt)
    resolved_project = resolve_project(project)
    overrides = {
        "selected_model": model,
        "effort": effort,
        "permission_mode": permission_mode,
        "thinking_enabled": thinking,
        "claude_in_chrome": claude_in_chrome,
        "context_max": context_max,
    }
    preset_list = bootstrap.providers[provider].presets if provider in bootstrap.providers else []
    settings = apply_preset_and_overrides(preset, preset_list, overrides)
except (PromptError, ProjectError, PresetError) as e:
    typer.echo(f"✗ {e}", err=True)
    raise typer.Exit(1)

typer.echo(f"✓ Prompt resolved ({len(text)} chars)")
typer.echo(f"✓ Project {resolved_project.project_id!r} "
           f"({'created' if resolved_project.created else 'existing'})")
typer.echo(f"✓ Settings: {settings._asdict()}")
```

- [ ] **Step 5: Smoke-test**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code "hello"
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code --model opus "hello"
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code --project /tmp "hello"
```

Each invocation prints all four ✓ lines and exits 0.

```bash
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code --preset does-not-exist "hello"
```

Exits non-zero with `Preset 'does-not-exist' not found.`.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/cli/create_session/
git commit -m "feat(cli): resolve prompt, project, preset for create-session"
```

---

### Task 12: CLI — agent settings validation + attachments

**Files:**
- Create: `src/twicc/cli/create_session/validation.py`
- Create: `src/twicc/cli/create_session/attachments.py`
- Modify: `src/twicc/cli/create_session/command.py`

- [ ] **Step 1: Write `validation.py`**

```python
"""Pre-flight validation for the CLI ``create-session`` command.

Aggregates errors so the user sees every problem at once, lint-style.
"""

from __future__ import annotations

from typing import NamedTuple


class ValidationError(NamedTuple):
    field: str
    code: str
    message: str


class ValidationGroup(Exception):
    def __init__(self, errors: list[ValidationError]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s)")


def validate_provider(provider: str, bootstrap) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if provider not in bootstrap.providers:
        names = ", ".join(bootstrap.providers.keys())
        errors.append(ValidationError(
            "provider", "unknown_provider",
            f"Unknown provider {provider!r}. Available: {names}.",
        ))
        return errors
    if not bootstrap.disabled_providers_present:
        errors.append(ValidationError(
            "provider", "no_provider_configured",
            "TwiCC has never been started. Run `twicc` once to activate providers.",
        ))
        return errors
    if bootstrap.providers[provider].is_disabled:
        errors.append(ValidationError(
            "provider", "provider_disabled",
            f"Provider {provider} is disabled. Enable it from the UI or settings.",
        ))
    return errors


def validate_settings(provider: str, settings, bootstrap) -> list[ValidationError]:
    """Check each non-None field against the provider's choices."""
    errors: list[ValidationError] = []
    pb = bootstrap.providers[provider]
    categories = pb.agent_settings_categories or {}
    all_supported = set()
    for fields in categories.values():
        all_supported.update(fields)
    choices = pb.agent_settings_choices or {}
    model_ids = {m.get("selected_model") for m in pb.model_registry or []}

    for field, value in settings._asdict().items():
        if value is None:
            continue
        if field not in all_supported:
            errors.append(ValidationError(
                f"--{field.replace('_', '-')}", "unsupported_field",
                f"{field} is not supported by {provider}. Supported: {sorted(all_supported)}.",
            ))
            continue
        if field == "selected_model":
            if value not in model_ids:
                ids = sorted(m for m in model_ids if m)
                errors.append(ValidationError(
                    "--model", "invalid_choice",
                    f"invalid value {value!r} for {provider}. Expected: {ids}.",
                ))
        elif field in choices:
            if value not in choices[field]:
                errors.append(ValidationError(
                    f"--{field.replace('_', '-')}", "invalid_choice",
                    f"invalid value {value!r} for {provider}. Expected: {choices[field]}.",
                ))
    return errors
```

- [ ] **Step 2: Write `attachments.py`**

```python
"""Validate and encode CLI attachments.

Validates by MIME (magic bytes), size, count, total size, and provider
support. Produces the SDK-format dicts the back-end expects in
``send_message`` payloads.
"""

from __future__ import annotations

import base64
import os
from typing import NamedTuple


# Magic byte signatures for each accepted binary type.
_MAGIC = [
    ("image/png",       b"\x89PNG\r\n\x1a\n"),
    ("image/jpeg",      b"\xff\xd8\xff"),
    ("image/gif",       b"GIF87a"),
    ("image/gif",       b"GIF89a"),
    ("image/webp",      b"RIFF"),       # plus "WEBP" at offset 8
    ("application/pdf", b"%PDF-"),
]


class AttachmentError(NamedTuple):
    file: str
    code: str
    message: str


class AttachmentResult(NamedTuple):
    images: list[dict]
    documents: list[dict]
    errors: list[AttachmentError]


def _sniff_mime(data: bytes) -> str | None:
    for mime, magic in _MAGIC:
        if mime == "image/webp":
            if data.startswith(magic) and data[8:12] == b"WEBP":
                return mime
        elif data.startswith(magic):
            return mime
    # Fallback: if decodable as UTF-8, treat as text/plain.
    try:
        data.decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return None


def validate_and_encode(paths: list[str], support: dict) -> AttachmentResult:
    images: list[dict] = []
    documents: list[dict] = []
    errors: list[AttachmentError] = []

    accepted = set(support.get("accepted_mime_types", []))
    max_per_file = support.get("max_bytes_per_file") or 0
    max_total = support.get("max_total_bytes") or 0
    max_count = support.get("max_files_per_message") or 0

    if len(paths) > max_count:
        errors.append(AttachmentError(
            "<all>", "too_many", f"{len(paths)} attachments, max {max_count}",
        ))
        return AttachmentResult([], [], errors)

    total = 0
    for path in paths:
        if not os.path.isfile(path):
            errors.append(AttachmentError(path, "not_a_file",
                                           f"file {path!r} does not exist"))
            continue
        size = os.path.getsize(path)
        if size > max_per_file:
            errors.append(AttachmentError(
                path, "size_exceeded",
                f"size {size / 1024 / 1024:.1f} MB exceeds "
                f"{max_per_file / 1024 / 1024:.0f} MB limit",
            ))
            continue
        total += size
        if total > max_total:
            errors.append(AttachmentError(
                path, "total_size_exceeded",
                f"total size exceeds {max_total / 1024 / 1024:.0f} MB",
            ))
            continue

        with open(path, "rb") as f:
            data = f.read()
        mime = _sniff_mime(data)
        if mime is None or mime not in accepted:
            accepted_list = ", ".join(sorted(accepted))
            errors.append(AttachmentError(
                path, "unsupported_mime",
                f"type {mime or 'unknown'} not supported "
                f"(accepted: {accepted_list})",
            ))
            continue

        # Build the SDK block in the same format the front-end uses.
        if mime.startswith("image/"):
            images.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                },
            })
        elif mime == "application/pdf":
            documents.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(data).decode("ascii"),
                },
            })
        elif mime == "text/plain":
            documents.append({
                "type": "document",
                "source": {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": data.decode("utf-8"),
                },
            })

    return AttachmentResult(images, documents, errors)
```

- [ ] **Step 3: Wire validation + attachments into `command.py`**

After the section added in Task 11 (settings resolved), add:

```python
from twicc.cli.create_session.validation import (
    ValidationError, ValidationGroup,
    validate_provider, validate_settings,
)
from twicc.cli.create_session.attachments import validate_and_encode

errors: list[ValidationError] = []
errors.extend(validate_provider(provider, bootstrap))
if not errors:  # only validate settings if the provider is OK
    errors.extend(validate_settings(provider, settings, bootstrap))

support = bootstrap.providers[provider].attachment_support if provider in bootstrap.providers else {}
attach_result = validate_and_encode(attach or [], support)
for err in attach_result.errors:
    errors.append(ValidationError(f"--attach {err.file}", err.code, err.message))

if errors:
    typer.echo("✗ Validation error:", err=True)
    for e in errors:
        typer.echo(f"  - {e.field}: {e.message}", err=True)
    raise typer.Exit(1)

typer.echo(f"✓ Settings validated")
typer.echo(f"✓ Attachments validated "
           f"({len(attach_result.images)} images, "
           f"{len(attach_result.documents)} documents)")
```

- [ ] **Step 4: Smoke-test**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
# Happy path — no attachments
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code "hello"

# Invalid effort
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code --effort ultra "hello"
# Expected: ✗ Validation error: ... --effort: invalid value 'ultra' ...

# Unsupported field for Codex
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider codex --thinking "hello"
# Expected: --thinking: thinking_enabled is not supported by codex.

# Attachment too big
truncate -s 10M /tmp/big.png
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code --attach /tmp/big.png "hello"
# Expected: --attach /tmp/big.png: size 10.0 MB exceeds 5 MB limit
rm /tmp/big.png
```

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/cli/create_session/
git commit -m "feat(cli): validate provider/settings/attachments before drop"
```

---

### Task 13: CLI — drop file + polling + final output

**Files:**
- Create: `src/twicc/cli/create_session/drop_file.py`
- Create: `src/twicc/cli/create_session/polling.py`
- Create: `src/twicc/cli/create_session/output.py`
- Modify: `src/twicc/cli/create_session/command.py`

- [ ] **Step 1: Write `drop_file.py`**

```python
"""Atomic drop-file writer."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import orjson


class DropFile(NamedTuple):
    path: Path
    request_uuid: str


def write_drop_file(
    data_dir: Path,
    payload: dict,
) -> DropFile:
    """Atomically write the request file. Returns the path and uuid."""
    directory = data_dir / "sessions-pending"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    request_uuid = str(uuid.uuid4())
    final_path = directory / f"{request_uuid}.json"
    tmp_path = directory / f"{request_uuid}.json.tmp"

    envelope = {
        "version": 1,
        "request_uuid": request_uuid,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "submitter": {
            "user": os.environ.get("USER", "?"),
            "hostname": os.uname().nodename if hasattr(os, "uname") else "?",
            "pid": os.getpid(),
        },
        "payload": {
            **payload,
            "session_id": request_uuid,  # Claude Code uses this as --session-id
        },
    }

    tmp_path.write_bytes(orjson.dumps(envelope))
    os.replace(tmp_path, final_path)
    try:
        os.chmod(final_path, 0o600)
    except Exception:
        pass

    return DropFile(path=final_path, request_uuid=request_uuid)
```

- [ ] **Step 2: Write `polling.py`**

```python
"""Polling loop for the status file."""

from __future__ import annotations

import time
from pathlib import Path
from typing import NamedTuple

import orjson


POLL_INTERVAL_SECONDS = 0.1


class PollOutcome(NamedTuple):
    status: str | None        # None => timeout
    data: dict | None
    received_seen: bool       # True if at any point the status was "received"


def poll_status(status_path: Path, timeout_seconds: int) -> PollOutcome:
    """Loop reading the status file until a final status appears or timeout."""
    deadline = time.time() + timeout_seconds
    received_seen = False
    last_data: dict | None = None

    while time.time() < deadline:
        if status_path.exists():
            try:
                data = orjson.loads(status_path.read_bytes())
            except (orjson.JSONDecodeError, OSError):
                # Status file mid-rename — retry next tick.
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            status = data.get("status")
            last_data = data
            if status == "received":
                received_seen = True
            elif status in ("created", "rejected", "failed"):
                return PollOutcome(status=status, data=data,
                                   received_seen=received_seen)
        time.sleep(POLL_INTERVAL_SECONDS)

    return PollOutcome(status=None, data=last_data,
                       received_seen=received_seen)
```

- [ ] **Step 3: Write `output.py`**

```python
"""Text and JSON formatting of progress and final result."""

from __future__ import annotations

import sys

import orjson
import typer


def emit_progress(line: str, *, json_output: bool) -> None:
    if not json_output:
        typer.echo(line)


def emit_validation_errors(errors, *, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(orjson.dumps({
            "status": "validation_error",
            "errors": [e._asdict() for e in errors],
        }).decode())
        sys.stdout.write("\n")
    else:
        typer.echo("✗ Validation error:", err=True)
        for e in errors:
            typer.echo(f"  - {e.field}: {e.message}", err=True)


def emit_final(outcome, *, request_uuid: str, json_output: bool, timeout: int) -> None:
    if outcome.status == "created":
        d = outcome.data
        if json_output:
            sys.stdout.write(orjson.dumps({
                "status": "created",
                "session_id": d.get("session_id"),
                "provider": d.get("provider"),
                "project_id": d.get("project_id"),
                "request_uuid": request_uuid,
            }).decode() + "\n")
        else:
            typer.echo(f"✓ Session created: {d.get('session_id')}")
    elif outcome.status == "rejected":
        d = outcome.data
        if json_output:
            sys.stdout.write(orjson.dumps({
                "status": "rejected",
                "errors": d.get("errors", []),
                "request_uuid": request_uuid,
            }).decode() + "\n")
        else:
            typer.echo("✗ Rejected by server:", err=True)
            for e in d.get("errors", []):
                typer.echo(f"  - {e.get('code')}: {e.get('message')}", err=True)
    elif outcome.status == "failed":
        d = outcome.data
        if json_output:
            sys.stdout.write(orjson.dumps({
                "status": "failed",
                "error": d.get("error"),
                "request_uuid": request_uuid,
            }).decode() + "\n")
        else:
            typer.echo(f"✗ Unexpected server error: {d.get('error')}", err=True)
    else:
        # timeout
        if outcome.received_seen:
            msg = (f"Request was received but server did not respond within "
                   f"{timeout}s. Check server logs.")
        else:
            msg = f"No confirmation from server after {timeout}s."
        if json_output:
            sys.stdout.write(orjson.dumps({
                "status": "timeout",
                "received_seen": outcome.received_seen,
                "message": msg,
                "request_uuid": request_uuid,
            }).decode() + "\n")
        else:
            typer.echo(f"✗ {msg}", err=True)
```

- [ ] **Step 4: Wire everything in `command.py`**

After validation succeeds:

```python
from twicc.cli.create_session.drop_file import write_drop_file
from twicc.cli.create_session.polling import poll_status
from twicc.cli.create_session.output import emit_final
from twicc.cli.create_session.discovery import get_data_dir

# Build the WS-compatible payload.
payload = {
    "project_id": resolved_project.project_id,
    "provider": provider,
    "text": text,
    "title": title,
    "images": attach_result.images,
    "documents": attach_result.documents,
    **settings._asdict(),
}

drop = write_drop_file(get_data_dir(), payload)
emit_progress(f"→ Request submitted (request_uuid: {drop.request_uuid[:8]}...)",
              json_output=json_output)

status_path = drop.path.with_name(f"{drop.request_uuid}.status.json")
outcome = poll_status(status_path, timeout_seconds=timeout)

# Cleanup our own files (cf. spec §5.5).
drop.path.unlink(missing_ok=True)
status_path.unlink(missing_ok=True)

emit_final(outcome, request_uuid=drop.request_uuid,
           json_output=json_output, timeout=timeout)

# Exit code mapping (spec §2.5)
if outcome.status == "created":
    raise typer.Exit(0)
if outcome.status == "rejected":
    raise typer.Exit(3)
if outcome.status == "failed":
    raise typer.Exit(4)
raise typer.Exit(5)  # timeout
```

Also replace the earlier `typer.echo()` calls with `emit_progress()` so JSON mode stays clean.

- [ ] **Step 5: End-to-end smoke test (requires backend running)**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code "hello from CLI"
```

Expected (with backend running, claude_code enabled and ready):
- The eight progress lines all show ✓
- "Request submitted" appears
- After ~1s, "✓ Session created: ..." appears
- Exit code is 0

Inspect the new session in the UI to confirm it shows the prompt "hello from CLI".

```bash
# JSON mode
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code --json "json test"
```

Expected: a single JSON line with `"status": "created"` and a `session_id`.

- [ ] **Step 6: Verify cleanup**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
ls -la sessions-pending/
```

Expected: empty (CLI deleted both drop and status files).

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add src/twicc/cli/create_session/
git commit -m "feat(cli): drop request file, poll status, emit final result"
```

---

### Task 14: End-to-end verification + edge cases

No new code — exercise everything written so far and document any rough edges before considering the work done.

- [ ] **Step 1: Happy path Claude Code with all options**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run twicc create-session \
  --provider claude_code \
  --model opus \
  --effort high \
  --permission-mode default \
  --title "CLI test" \
  "smoke test from CLI"
```

Expected: session created, all settings reflected in the new Session row (check via `twicc session <id>` once it appears).

- [ ] **Step 2: Happy path Codex**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
TWICC_DATA_DIR=$PWD uv run twicc create-session \
  --provider codex \
  --model gpt \
  --effort medium \
  --permission-mode auto \
  "smoke test codex"
```

Expected: session created, the canonical_id printed is the one minted by Codex (different from the request_uuid in the drop-file — invisible from the CLI viewpoint, but check `logs/backend.log` for `[PendingSessionsWatcher] created`).

- [ ] **Step 3: Preset + override**

Pick an existing preset name from `~/.twicc/claude_code-settings-presets.json` (or create one via the UI first). Run:

```bash
TWICC_DATA_DIR=$PWD uv run twicc create-session \
  --provider claude_code \
  --preset "<existing-preset>" \
  --effort low \
  "preset + override"
```

Expected: session created; effort is `low`, the other fields come from the preset.

- [ ] **Step 4: Prompt from file (absolute and relative)**

```bash
echo "Prompt from file" > /tmp/prompt.txt
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code /tmp/prompt.txt
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code ./pyproject.toml
rm /tmp/prompt.txt
```

Expected: sessions created with the file content as prompt.

- [ ] **Step 5: Project auto-create from a new directory**

```bash
mkdir -p /tmp/new-project-cli-test
TWICC_DATA_DIR=$PWD uv run twicc create-session \
  --provider claude_code \
  --project /tmp/new-project-cli-test \
  "new project"
```

Expected: session created. Open the UI: the new project appears (front auto-refreshes via the JSONL watcher broadcast — may need a manual reload depending on timing).

- [ ] **Step 6: Project by id without leading dash**

Find a known project_id (e.g. via `twicc projects`). Strip the leading `-`. Pass it:

```bash
TWICC_DATA_DIR=$PWD uv run twicc create-session \
  --provider claude_code \
  --project home-twidi-dev-twicc-poc \
  "no-dash id"
```

Expected: session created. The resolution tried `home-twidi-dev-twicc-poc`, didn't find it, then tried `-home-twidi-dev-twicc-poc` and found it.

- [ ] **Step 7: Provider not yet running**

This requires the user to set up the condition: temporarily disable `codex` from the UI, then:

```bash
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider codex "should fail"
```

Expected: CLI exits with code 1 (validation error) and message `Provider codex is disabled.`. The watcher is never invoked.

Re-enable `codex` afterwards.

- [ ] **Step 8: Server down**

Tell the user: "Please stop the backend (`uv run ./devctl.py stop back` from the worktree). Then I'll re-run the CLI."

```bash
TWICC_DATA_DIR=$PWD uv run twicc create-session --provider claude_code "server down test"
```

Expected: CLI exits with code 2 and message `TwiCC server does not appear to be running...`.

Ask the user to restart afterwards.

- [ ] **Step 9: CLI crash simulation (status file orphan)**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create

# Drop a fake request by hand, watch get processed, kill the CLI midway
echo '{"version":1,"request_uuid":"deadbeef-test","payload":{"session_id":"deadbeef-test","project_id":"YOUR_PROJECT_ID","provider":"claude_code","text":"orphan test"}}' > sessions-pending/deadbeef-test.json
```

(Replace `YOUR_PROJECT_ID` with a valid project id.)

After ~2s:

```bash
ls -la sessions-pending/
```

Expected: both `deadbeef-test.json` and `deadbeef-test.status.json` are present (no CLI to clean up). Then ask the user to restart the backend (user-reserved). On next boot the watcher detects drop+status co-presence and removes both. Verify by `ls sessions-pending/`.

- [ ] **Step 10: Final commit (verification trace)**

If the previous steps surface any documentation issue (wording, example mismatch), fix and commit:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-cli-session-create
git add -- '*.md' src/twicc/cli/create_session/
git commit -m "docs(cli): polish create-session wording after end-to-end verification"
```

Otherwise no commit. Done.

---

## After completion

- **Spec updates**: do NOT amend the spec retroactively from the implementation (see project memory: "Ne pas modifier les documents historiques"). If the implementation diverged from the spec on a meaningful point, add a brief note at the bottom of the spec file referencing the commit hash of the divergence.
- **User reminders** (since these are user-reserved):
  - Restart the backend after the wiring of the heartbeat + watcher (Task 9).
  - No new package dependencies — no `uv add` needed.
  - No new migrations.
- **Merge strategy**: merge `feature/cli-session-create` into `main` via fast-forward when the user gives the go-ahead. The worktree can be removed afterwards (`git worktree remove`).
