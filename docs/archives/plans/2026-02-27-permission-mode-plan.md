# Permission Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the SDK `permission_mode` configurable per session via a dropdown in the message input, persisted in the database, and restorable on session resume.

**Architecture:** Add a `permission_mode` field to the Session model, thread it through the process creation chain (WS consumer → ProcessManager → ClaudeProcess → SDK options), expose it via the serializer, and add UI controls in both Settings (default mode) and MessageInput (per-session dropdown). For new sessions, use a pending-value pattern (like titles) since the DB row is created by the file watcher, not the WS handler.

**Tech Stack:** Django model + migration, Python backend (asgi.py, process.py, manager.py, sessions_watcher.py), Vue.js 3 frontend (settings.js, SettingsPopover.vue, MessageInput.vue, constants.js), Web Awesome components (wa-select).

**Design doc:** `docs/plans/2026-02-27-permission-mode-design.md`

---

### Task 1: Add constants and pending permission mode module

Add the `PERMISSION_MODES` constant to `constants.js` and create a minimal pending permission mode module in the backend (mirroring the `set_pending_title` pattern from `titles.py`).

**Files:**
- Modify: `frontend/src/constants.js`
- Create: `src/twicc/pending_permission_mode.py`

**Step 1: Add PERMISSION_MODE constant to frontend constants**

In `frontend/src/constants.js`, add at the end (before closing):

```javascript
/**
 * Permission mode values (matches SDK PermissionMode).
 * Controls how Claude Code handles tool permission prompts.
 */
export const PERMISSION_MODE = {
    DEFAULT: 'default',
    ACCEPT_EDITS: 'acceptEdits',
    PLAN: 'plan',
    DONT_ASK: 'dontAsk',
    BYPASS: 'bypassPermissions',
}

export const DEFAULT_PERMISSION_MODE = PERMISSION_MODE.DEFAULT

/**
 * Human-friendly labels for each permission mode.
 */
export const PERMISSION_MODE_LABELS = {
    [PERMISSION_MODE.DEFAULT]: 'Default',
    [PERMISSION_MODE.ACCEPT_EDITS]: 'Accept Edits',
    [PERMISSION_MODE.PLAN]: 'Plan',
    [PERMISSION_MODE.DONT_ASK]: "Don't Ask",
    [PERMISSION_MODE.BYPASS]: 'Bypass',
}

/**
 * Short descriptions for each permission mode (for tooltips/settings).
 */
export const PERMISSION_MODE_DESCRIPTIONS = {
    [PERMISSION_MODE.DEFAULT]: 'Prompts for permission on first use of each tool',
    [PERMISSION_MODE.ACCEPT_EDITS]: 'Auto-accepts file edit permissions',
    [PERMISSION_MODE.PLAN]: 'Read-only: Claude can analyze but not modify files',
    [PERMISSION_MODE.DONT_ASK]: 'Auto-denies tools unless pre-approved via permission rules',
    [PERMISSION_MODE.BYPASS]: 'Skips all permission prompts',
}
```

**Step 2: Create the pending permission mode module**

Create `src/twicc/pending_permission_mode.py`:

```python
"""
Pending permission mode storage for new sessions.

When creating a new session, the permission_mode must be stored before the
file watcher creates the Session row in the database. This module provides
a simple in-memory store (like titles.py's pending title pattern) that the
watcher reads when creating the session.
"""

import logging

logger = logging.getLogger(__name__)

# Global dict: session_id -> permission_mode string
_pending_modes: dict[str, str] = {}


def set_pending_permission_mode(session_id: str, mode: str) -> None:
    """Store a permission mode to be applied when the session is created by the watcher."""
    _pending_modes[session_id] = mode
    logger.debug("Set pending permission_mode for session %s: %s", session_id, mode)


def pop_pending_permission_mode(session_id: str) -> str | None:
    """Get and remove a pending permission mode for a session."""
    return _pending_modes.pop(session_id, None)
```

**Step 3: Commit**

```
feat: add permission mode constants and pending mode module
```

---

### Task 2: Add permission_mode to Session model + migration

**Files:**
- Modify: `src/twicc/core/models.py` (Session class, around line 281)
- Create: `src/twicc/core/migrations/0039_session_permission_mode.py` (via makemigrations)

**Step 1: Add the field to the Session model**

In `src/twicc/core/models.py`, in the `Session` class, after the `pinned` field (line 282), add:

```python
    # Permission mode for the Claude SDK (e.g., "default", "acceptEdits", "plan", "bypassPermissions")
    permission_mode = models.CharField(max_length=30, default="default")
```

**Step 2: Create the migration**

Run: `cd /home/twidi/dev/twicc-poc && uv run python -m django makemigrations core --name session_permission_mode`

**Step 3: Commit**

```
feat: add permission_mode field to Session model
```

---

### Task 3: Add permission_mode to session serializer

**Files:**
- Modify: `src/twicc/core/serializers.py` (in `serialize_session`, around line 75)

**Step 1: Add permission_mode to the serialized output**

In `serialize_session()`, after the `"pinned"` line (line 75), add:

```python
        # Permission mode
        "permission_mode": session.permission_mode,
```

**Step 2: Commit**

```
feat: serialize permission_mode in session output
```

---

### Task 4: Thread permission_mode through process creation chain

Pass `permission_mode` from `ClaudeProcess.__init__` → `start()` options, and through `ProcessManager._start_process()` → `create_session()` / `send_to_session()`.

**Files:**
- Modify: `src/twicc/agent/process.py` (lines 58-68, 410-412)
- Modify: `src/twicc/agent/manager.py` (lines 122-177, 226-270)

**Step 1: Add permission_mode to ClaudeProcess**

In `src/twicc/agent/process.py`, modify `__init__` to accept `permission_mode`:

Change the signature (line 58-63) from:
```python
    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        get_last_session_slug: Callable[[str], Coroutine[Any, Any, str | None]],
    ) -> None:
```
to:
```python
    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        permission_mode: str,
        get_last_session_slug: Callable[[str], Coroutine[Any, Any, str | None]],
    ) -> None:
```

Add `self.permission_mode = permission_mode` after `self.cwd = cwd` (line 77).

Update the logger.debug call (line 93-98) to include permission_mode:
```python
        logger.debug(
            "ClaudeProcess created for session %s, project %s, cwd=%s, permission_mode=%s",
            session_id,
            project_id,
            cwd,
            permission_mode,
        )
```

**Step 2: Use self.permission_mode in start()**

In `start()` (line 412), replace:
```python
                permission_mode="bypassPermissions",
```
with:
```python
                permission_mode=self.permission_mode,
```

**Step 3: Thread through ProcessManager**

In `src/twicc/agent/manager.py`:

Add `permission_mode: str = "default"` parameter to `send_to_session()` (after `text: str`):
```python
    async def send_to_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        permission_mode: str = "default",
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
```

In `send_to_session()`, pass `permission_mode` to `_start_process` call (line 174):
```python
            await self._start_process(
                session_id, project_id, cwd, text, resume=True,
                permission_mode=permission_mode,
                images=images, documents=documents
            )
```

Add `permission_mode: str = "default"` parameter to `create_session()` (after `text: str`):
```python
    async def create_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        permission_mode: str = "default",
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
```

In `create_session()`, pass `permission_mode` to `_start_process` call (line 221):
```python
            await self._start_process(
                session_id, project_id, cwd, text, resume=False,
                permission_mode=permission_mode,
                images=images, documents=documents
            )
```

Add `permission_mode: str = "default"` parameter to `_start_process()` (after `resume: bool`):
```python
    async def _start_process(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        resume: bool,
        permission_mode: str = "default",
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
```

In `_start_process()`, pass `permission_mode` to `ClaudeProcess` constructor (line 259):
```python
        process = ClaudeProcess(session_id, project_id, cwd, permission_mode, get_last_session_slug=get_last_session_slug)
```

**Step 4: Commit**

```
feat: thread permission_mode through process creation chain
```

---

### Task 5: Handle permission_mode in WebSocket consumer

Extract `permission_mode` from the WS payload in `_handle_send_message`, store it as pending for new sessions, update it in DB for existing sessions, and pass it to the ProcessManager.

**Files:**
- Modify: `src/twicc/asgi.py` (in `_handle_send_message`, around lines 333-454)

**Step 1: Extract permission_mode and handle it**

In `_handle_send_message()`, after extracting `documents` (line 362), add:
```python
        permission_mode = content.get("permission_mode", "default")
```

After the `if exists:` block, modify the two branches:

For existing sessions (the `if exists:` branch, around line 421-425), **before** calling `manager.send_to_session`, add a DB update:
```python
            if exists:
                # Update permission_mode in DB for existing sessions
                await update_session_permission_mode(session_id, permission_mode)
                # Session exists: send message to it
                await manager.send_to_session(
                    session_id, project_id, cwd, text,
                    permission_mode=permission_mode,
                    images=images, documents=documents
                )
```

For new sessions (the `else:` branch, around line 427-435), **before** calling `manager.create_session`, add pending permission mode:
```python
            else:
                # Session doesn't exist: create new with client-provided ID
                # Store title as pending if provided (will be written when process is safe)
                if title:
                    from twicc.titles import set_pending_title

                    set_pending_title(session_id, title)

                # Store permission_mode as pending (will be applied when watcher creates the session row)
                from twicc.pending_permission_mode import set_pending_permission_mode

                set_pending_permission_mode(session_id, permission_mode)

                await manager.create_session(
                    session_id, project_id, cwd, text,
                    permission_mode=permission_mode,
                    images=images, documents=documents
                )
```

**Step 2: Add the update_session_permission_mode helper**

Add this near the other `@sync_to_async` helper functions (e.g., after `session_exists` around line 92):

```python
@sync_to_async
def update_session_permission_mode(session_id: str, permission_mode: str) -> None:
    """Update the permission_mode for an existing session in the database."""
    from twicc.core.models import Session

    Session.objects.filter(id=session_id).update(permission_mode=permission_mode)
```

**Step 3: Commit**

```
feat: handle permission_mode in WebSocket send_message handler
```

---

### Task 6: Apply pending permission mode in the file watcher

When the watcher creates a new session row, apply any pending permission mode.

**Files:**
- Modify: `src/twicc/sessions_watcher.py` (around lines 136-163 and 348-359)

**Step 1: Modify create_session to accept permission_mode**

In `src/twicc/sessions_watcher.py`, modify the `create_session` function (line 137-163) to accept an optional `permission_mode` parameter:

```python
@sync_to_async
def create_session(
    parsed: ParsedPath,
    project: Project,
    parent_session: Session | None = None,
    permission_mode: str | None = None,
) -> Session:
    """Create a session or subagent in the database.

    For subagents, parent_session must be provided.
    If permission_mode is provided, it overrides the default.
    Returns the created session.
    """
    if parsed.type == SessionType.SUBAGENT:
        if parent_session is None:
            raise ValueError("parent_session is required for subagents")
        return Session.objects.create(
            id=parsed.session_id,
            project=project,
            type=SessionType.SUBAGENT,
            parent_session=parent_session,
            agent_id=parsed.session_id,
            compute_version=settings.CURRENT_COMPUTE_VERSION,
        )
    else:
        kwargs = dict(
            id=parsed.session_id,
            project=project,
            compute_version=settings.CURRENT_COMPUTE_VERSION,
        )
        if permission_mode is not None:
            kwargs["permission_mode"] = permission_mode
        return Session.objects.create(**kwargs)
```

**Step 2: Pop and apply pending permission mode when creating sessions**

In the watcher code where `create_session` is called (around line 348), pop the pending mode and pass it:

Replace:
```python
        # Create session (regular or subagent)
        session = await create_session(parsed, project, parent_session)
```

With:
```python
        # Create session (regular or subagent)
        # Pop any pending permission_mode set by the WS handler for new sessions
        from twicc.pending_permission_mode import pop_pending_permission_mode

        pending_mode = pop_pending_permission_mode(parsed.session_id)
        session = await create_session(parsed, project, parent_session, permission_mode=pending_mode)
```

**Step 3: Commit**

```
feat: apply pending permission_mode when watcher creates session
```

---

### Task 7: Handle setMode suggestion in pending request response

When a user accepts a `setMode` permission suggestion, persist the new mode in the database.

**Files:**
- Modify: `src/twicc/asgi.py` (in `_handle_pending_request_response`, around lines 512-605)

**Step 1: Detect and persist setMode suggestions**

In `_handle_pending_request_response()`, in the `decision == "allow"` branch (around lines 557-569), after building the `PermissionResultAllow` response, add logic to persist any `setMode` from `updated_permissions`:

After the `response = PermissionResultAllow(...)` block (line 566-569), and before the `resolved = await manager.resolve_pending_request(...)` call (line 599), add:

```python
        # Persist setMode suggestions in DB so future resumes use the correct mode
        if request_type == "tool_approval" and content.get("decision") == "allow":
            raw_permissions = content.get("updated_permissions")
            if raw_permissions:
                for perm in raw_permissions:
                    if perm.get("type") == "setMode" and perm.get("mode"):
                        await update_session_permission_mode(session_id, perm["mode"])
                        logger.info(
                            "Permission mode updated to %r for session %s (from setMode suggestion)",
                            perm["mode"],
                            session_id,
                        )
                        break  # Only one setMode should be applied
```

This must be placed **before** the `resolved = await manager.resolve_pending_request(...)` call and **after** the `response` is built. The exact insertion point is right before the final `resolved = ...` line at the end of the method. Since the method branches on `request_type`, the cleanest place is right before the `resolved =` call, checking the condition there.

**Step 2: Commit**

```
feat: persist setMode permission suggestion in session database
```

---

### Task 8: Add permission mode to Settings store

**Files:**
- Modify: `frontend/src/stores/settings.js`

**Step 1: Add defaultPermissionMode to schema, validator, and watcher**

In `frontend/src/stores/settings.js`:

Add import at the top (line 7), add to the existing import from `'../constants'`:
```javascript
import { ..., DEFAULT_PERMISSION_MODE, PERMISSION_MODE } from '../constants'
```

In `SETTINGS_SCHEMA` (after `compactSessionList: false,` around line 32), add:
```javascript
    defaultPermissionMode: DEFAULT_PERMISSION_MODE,
```

In `SETTINGS_VALIDATORS` (after `compactSessionList` validator around line 63), add:
```javascript
    defaultPermissionMode: (v) => Object.values(PERMISSION_MODE).includes(v),
```

In the getters section, add:
```javascript
        getDefaultPermissionMode: (state) => state.defaultPermissionMode,
```

In the actions section, add:
```javascript
        /**
         * Set the default permission mode for new sessions.
         * @param {string} mode - One of PERMISSION_MODE values
         */
        setDefaultPermissionMode(mode) {
            if (SETTINGS_VALIDATORS.defaultPermissionMode(mode)) {
                this.defaultPermissionMode = mode
            }
        },
```

In the `initSettings()` function, add `defaultPermissionMode` to the watcher's watched properties (inside the `watch(() => ({...}), ...)` block, after `compactSessionList:`):
```javascript
            defaultPermissionMode: store.defaultPermissionMode,
```

**Step 2: Commit**

```
feat: add defaultPermissionMode to settings store
```

---

### Task 9: Add permission mode dropdown to SettingsPopover

**Files:**
- Modify: `frontend/src/components/SettingsPopover.vue`

**Step 1: Add imports, refs, computed, and handler**

In `<script setup>`, add to the constants import (line 8):
```javascript
import { ..., PERMISSION_MODE, PERMISSION_MODE_LABELS, PERMISSION_MODE_DESCRIPTIONS } from '../constants'
```

Add the options array (after `displayModeOptions`, around line 84):
```javascript
// Permission mode options for the select
const permissionModeOptions = Object.values(PERMISSION_MODE).map(value => ({
    value,
    label: PERMISSION_MODE_LABELS[value],
    description: PERMISSION_MODE_DESCRIPTIONS[value],
}))
```

Add ref (after other refs, around line 57):
```javascript
const permissionModeSelect = ref(null)
```

Add computed (after `compactSessionList`, around line 73):
```javascript
const defaultPermissionMode = computed(() => store.getDefaultPermissionMode)
```

In `syncSwitchState()`, add (after the `editorWordWrap` sync block, around line 131):
```javascript
        if (permissionModeSelect.value && permissionModeSelect.value.value !== defaultPermissionMode.value) {
            permissionModeSelect.value.value = defaultPermissionMode.value
        }
```

Add `defaultPermissionMode` to the watch array (line 136):
```javascript
watch([..., defaultPermissionMode], syncSwitchState, { immediate: true })
```

Add handler:
```javascript
/**
 * Handle default permission mode change.
 */
function onDefaultPermissionModeChange(event) {
    store.setDefaultPermissionMode(event.target.value)
}
```

**Step 2: Add the UI in the template**

In the Sessions section (after the "Compact session list" setting group, around line 376), add:

```html
                    <div class="setting-group">
                        <label class="setting-group-label">Default permission mode</label>
                        <wa-select
                            ref="permissionModeSelect"
                            :value.prop="defaultPermissionMode"
                            @change="onDefaultPermissionModeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in permissionModeOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
```

**Step 3: Commit**

```
feat: add default permission mode dropdown to Settings
```

---

### Task 10: Add permission mode dropdown to MessageInput

**Files:**
- Modify: `frontend/src/components/MessageInput.vue`

**Step 1: Add imports and state**

In `<script setup>`, add imports:
```javascript
import { useSettingsStore } from '../stores/settings'
import { PERMISSION_MODE, PERMISSION_MODE_LABELS } from '../constants'
```

Add store:
```javascript
const settingsStore = useSettingsStore()
```

Add permission mode options (static, computed once):
```javascript
// Permission mode options for the dropdown
const permissionModeOptions = Object.values(PERMISSION_MODE).map(value => ({
    value,
    label: PERMISSION_MODE_LABELS[value],
}))
```

Add local state for the selected mode:
```javascript
// Selected permission mode for the current session
const selectedPermissionMode = ref('default')
```

**Step 2: Add computed for disabled state and initial value logic**

```javascript
// Whether the permission mode dropdown should be disabled
// Disabled when a process is actively running on this session
const isPermissionModeDisabled = computed(() => {
    const state = processState.value?.state
    return state === 'starting' || state === 'assistant_turn' || state === 'user_turn'
})
```

**Step 3: Sync permission mode when session changes**

Add a watcher that sets the initial value based on session type (draft vs existing). Add this after the existing `watch(() => props.sessionId, ...)` block (around line 96):

```javascript
// Sync permission mode when session changes
watch(() => props.sessionId, (newId) => {
    const sess = store.getSession(newId)
    if (sess?.draft) {
        // New session: use default from settings
        selectedPermissionMode.value = settingsStore.getDefaultPermissionMode
    } else if (sess?.permission_mode) {
        // Existing session: use stored mode
        selectedPermissionMode.value = sess.permission_mode
    } else {
        selectedPermissionMode.value = 'default'
    }
}, { immediate: true })

// Also react when session data arrives from backend (e.g., after watcher creates the row)
watch(
    () => store.getSession(props.sessionId)?.permission_mode,
    (newMode) => {
        if (newMode && !isPermissionModeDisabled.value) {
            selectedPermissionMode.value = newMode
        }
    }
)
```

**Step 4: Include permission_mode in the send payload**

In `handleSend()`, add `permission_mode` to the payload (after `text: text`, around line 303):

```javascript
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        text: text,
        permission_mode: selectedPermissionMode.value,
    }
```

**Step 5: Add the dropdown in the template**

In the template, in the `.message-input-actions` div (line 488), add the dropdown **before** the Cancel/Clear and Send buttons:

```html
            <div class="message-input-actions">
                <!-- Permission mode selector -->
                <wa-select
                    :value.prop="selectedPermissionMode"
                    @change="selectedPermissionMode = $event.target.value"
                    size="small"
                    class="permission-mode-select"
                    :disabled="isPermissionModeDisabled"
                >
                    <wa-option
                        v-for="option in permissionModeOptions"
                        :key="option.value"
                        :value="option.value"
                    >{{ option.label }}</wa-option>
                </wa-select>

                <!-- Cancel button for draft sessions -->
                <wa-button ...>
```

**Step 6: Add styles**

Add in the `<style scoped>` section:

```css
.permission-mode-select {
    min-width: 8rem;
    max-width: 10rem;
}

/* On small screens, shrink the permission mode select */
@media (width < 400px) {
    .permission-mode-select {
        min-width: 6rem;
        max-width: 8rem;
    }
}
```

**Step 7: Commit**

```
feat: add permission mode dropdown to MessageInput
```

---

### Task 11: Manual verification

Verify the full flow works:

**Test 1 — Settings**
1. Open Settings popover
2. Verify the "Default permission mode" dropdown appears in the Sessions section
3. Change it to "Accept Edits" → close and reopen → value is persisted

**Test 2 — New session with default mode**
1. Set default permission mode to "Default" in Settings
2. Create a new session (draft)
3. Verify the MessageInput dropdown shows "Default"
4. Send a message → verify Claude asks for tool approval (default mode behavior)

**Test 3 — New session with bypass mode**
1. Set default permission mode to "Bypass" in Settings
2. Create a new session
3. Verify dropdown shows "Bypass"
4. Send a message → verify Claude doesn't ask for approvals

**Test 4 — Resume with persisted mode**
1. After Test 2, kill the process
2. Verify the dropdown shows "Default" (from DB)
3. Send a follow-up message → verify it resumes with "Default" mode

**Test 5 — Change mode on resume**
1. On an existing session with "Default" mode
2. Change dropdown to "Bypass" before sending
3. Send → verify Claude uses bypass mode
4. Kill process, refresh page → verify dropdown shows "Bypass" (persisted)

**Test 6 — Disabled during process**
1. While Claude is running (assistant_turn), verify the dropdown is disabled
2. When Claude is waiting (user_turn), verify the dropdown is still disabled
3. After killing the process, verify the dropdown is enabled

**Step: Commit (if any fixes)**

```
fix: adjustments from manual verification
```

---

### Task 12: Remind user about migration

After all code changes are committed, remind the user to:
1. Run `uv run python -m django migrate` to apply the new migration
2. Restart the dev servers via `uv run ./devctl.py restart`
