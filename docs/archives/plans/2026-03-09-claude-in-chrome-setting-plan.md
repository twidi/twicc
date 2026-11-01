# Claude In Chrome Setting — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a global setting (synced, default enabled) to toggle the Claude built-in Chrome MCP, overridable per session via the MessageInput popover, stored on the Session model, and passed to the SDK process at creation time.

**Architecture:** Follows the exact pattern of `effort`/`thinking_enabled`: global default + "always apply" flag in settings store (synced), per-session column on Session model (`BooleanField(default=False)`), resolve logic in MessageInput, pending settings for new sessions, and conditional `extra_args` in `ClaudeProcess.start()`.

**Tech Stack:** Django (model + migration), Python backend (asgi, manager, process, watcher, serializer), Vue.js 3 frontend (constants, settings store, SettingsPopover, MessageInput).

---

### Task 1: Backend — Session model + migration

**Files:**
- Modify: `src/twicc/core/models.py:298` (after `thinking_enabled` field)
- Create: migration file via `makemigrations`

**Step 1: Add the field to Session model**

In `src/twicc/core/models.py`, after line 298 (`thinking_enabled = models.BooleanField(null=True, default=None)`), add:

```python
    # Whether the built-in Chrome MCP (Claude in Chrome) is activated for this session
    claude_in_chrome = models.BooleanField(default=False)
```

**Step 2: Create the migration**

Run:
```bash
cd /home/twidi/dev/twicc-poc && uv run python -m django makemigrations core
```

**Step 3: Commit**

```bash
git add src/twicc/core/models.py src/twicc/core/migrations/
git commit -m "feat: add claude_in_chrome field to Session model"
```

---

### Task 2: Backend — Serializer

**Files:**
- Modify: `src/twicc/core/serializers.py:87` (after `thinking_enabled` line)

**Step 1: Add claude_in_chrome to session serialization**

In `src/twicc/core/serializers.py`, after the line `"thinking_enabled": session.thinking_enabled,` (line 87), add:

```python
        # Claude in Chrome MCP
        "claude_in_chrome": session.claude_in_chrome,
```

**Step 2: Commit**

```bash
git add src/twicc/core/serializers.py
git commit -m "feat: serialize claude_in_chrome in session data"
```

---

### Task 3: Backend — asgi.py (update helper + send_message handler)

**Files:**
- Modify: `src/twicc/asgi.py:188-214` (after `update_session_thinking_enabled`), and `send_message` handler (~lines 540-654)

**Step 1: Add update_session_claude_in_chrome helper**

After the `update_session_thinking_enabled` function (line 214), add a new function following the exact same pattern:

```python
async def update_session_claude_in_chrome(session_id: str, claude_in_chrome: bool) -> None:
    """Update the claude_in_chrome flag for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await sync_to_async(
        Session.objects.filter(id=session_id).exclude(claude_in_chrome=claude_in_chrome).update
    )(claude_in_chrome=claude_in_chrome)
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with claude_in_chrome {claude_in_chrome}")
```

**Step 2: Update send_message handler — extract claude_in_chrome from payload**

In the `_handle_send_message` method, after line 543 (`thinking_enabled = content.get("thinking_enabled")`), add:

```python
        claude_in_chrome = content.get("claude_in_chrome", False)  # bool, defaults to False
```

**Step 3: Update send_message handler — existing session branch**

In the existing session branch (after `update_session_thinking_enabled` call, around line 609), add:

```python
                await update_session_claude_in_chrome(session_id, claude_in_chrome)
```

And update the `manager.send_to_session()` call to include `claude_in_chrome`:

```python
                await manager.send_to_session(
                    session_id, project_id, cwd, text,
                    permission_mode=permission_mode,
                    selected_model=selected_model,
                    effort=effort, thinking_enabled=thinking_enabled,
                    claude_in_chrome=claude_in_chrome,
                    images=images, documents=documents
                )
```

**Step 4: Update send_message handler — new session branch**

In the new session branch, add `claude_in_chrome` to `pending_kwargs` (after the `thinking_enabled` block, around line 645):

```python
                pending_kwargs["claude_in_chrome"] = claude_in_chrome
```

And update the `manager.create_session()` call to include `claude_in_chrome`:

```python
                await manager.create_session(
                    session_id, project_id, cwd, text,
                    permission_mode=permission_mode,
                    selected_model=selected_model,
                    effort=effort, thinking_enabled=thinking_enabled,
                    claude_in_chrome=claude_in_chrome,
                    images=images, documents=documents
                )
```

**Step 5: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat: handle claude_in_chrome in WS send_message handler"
```

---

### Task 4: Backend — sessions_watcher.py (create_session)

**Files:**
- Modify: `src/twicc/sessions_watcher.py:140-181` (create_session function) and `~339-350` (pop_pending call)

**Step 1: Add claude_in_chrome parameter to create_session**

Update the `create_session` function signature (line 141) to add `claude_in_chrome`:

```python
@sync_to_async
def create_session(
    parsed: ParsedPath,
    project: Project,
    parent_session: Session | None = None,
    permission_mode: str | None = None,
    selected_model: str | None = None,
    effort: str | None = None,
    thinking_enabled: bool | None = None,
    claude_in_chrome: bool | None = None,
) -> Session:
```

Update the docstring accordingly, and add the field to the kwargs block (after line 180):

```python
        if claude_in_chrome is not None:
            kwargs["claude_in_chrome"] = claude_in_chrome
```

**Step 2: Pass claude_in_chrome from pending settings**

In the watcher's `_handle_session_file` (around line 344-350), add `claude_in_chrome` to the `create_session` call:

```python
        session = await create_session(
            parsed, project, parent_session,
            permission_mode=pending.get("permission_mode"),
            selected_model=pending.get("selected_model"),
            effort=pending.get("effort"),
            thinking_enabled=pending.get("thinking_enabled"),
            claude_in_chrome=pending.get("claude_in_chrome"),
        )
```

**Step 3: Commit**

```bash
git add src/twicc/sessions_watcher.py
git commit -m "feat: pass claude_in_chrome through session creation pipeline"
```

---

### Task 5: Backend — manager.py

**Files:**
- Modify: `src/twicc/agent/manager.py:122-294` (send_to_session, create_session, _start_process)

**Step 1: Add claude_in_chrome parameter to all three methods**

Update `send_to_session` signature (line 122):

```python
    async def send_to_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        permission_mode: str = "default",
        selected_model: str | None = None,
        effort: str | None = None,
        thinking_enabled: bool | None = None,
        claude_in_chrome: bool = False,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
```

Update the `_start_process` call inside `send_to_session` (around line 197) to pass `claude_in_chrome`:

```python
            await self._start_process(
                session_id, project_id, cwd, text, resume=True,
                permission_mode=permission_mode, selected_model=selected_model,
                effort=effort, thinking_enabled=thinking_enabled,
                claude_in_chrome=claude_in_chrome,
                images=images, documents=documents
            )
```

Update `create_session` signature (line 204) similarly, adding `claude_in_chrome: bool = False,` and pass it to `_start_process`.

Update `_start_process` signature (line 257):

```python
    async def _start_process(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        resume: bool,
        permission_mode: str = "default",
        selected_model: str | None = None,
        effort: str | None = None,
        thinking_enabled: bool | None = None,
        claude_in_chrome: bool = False,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
```

Update the `ClaudeProcess()` constructor call (line 294) to pass `claude_in_chrome`:

```python
        process = ClaudeProcess(session_id, project_id, cwd, permission_mode, selected_model, effort, thinking_enabled, claude_in_chrome, get_last_session_slug=get_last_session_slug)
```

**Step 2: Commit**

```bash
git add src/twicc/agent/manager.py
git commit -m "feat: thread claude_in_chrome through ProcessManager"
```

---

### Task 6: Backend — process.py (constructor + start)

**Files:**
- Modify: `src/twicc/agent/process.py:64-121` (constructor) and `~698-714` (start method, extra_args)

**Step 1: Add claude_in_chrome to constructor**

Update the `__init__` signature (line 64) to add `claude_in_chrome: bool = False` between `thinking_enabled` and `get_last_session_slug`:

```python
    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        permission_mode: str,
        selected_model: str | None,
        effort: str | None,
        thinking_enabled: bool | None,
        claude_in_chrome: bool = False,
        get_last_session_slug: Callable[[str], Coroutine[Any, Any, str | None]],
    ) -> None:
```

Update the docstring to document the parameter, then store it (after `self.thinking_enabled = thinking_enabled`, line 95):

```python
        self.claude_in_chrome = claude_in_chrome
```

Update the logger.debug call (line 112) to include `claude_in_chrome`:

```python
        logger.debug(
            "ClaudeProcess created for session %s, project %s, cwd=%s, permission_mode=%s, model=%s, effort=%s, thinking=%s, chrome=%s",
            session_id,
            project_id,
            cwd,
            permission_mode,
            selected_model,
            effort,
            thinking_enabled,
            claude_in_chrome,
        )
```

**Step 2: Update start() to conditionally set extra_args**

In `start()`, replace the hardcoded `extra_args` block (lines 711-713):

```python
                extra_args={
                    "chrome": None
                },
```

with conditional logic:

```python
                extra_args={
                    "chrome": None
                } if self.claude_in_chrome else {
                    "no-chrome": None
                },
```

**Step 3: Commit**

```bash
git add src/twicc/agent/process.py
git commit -m "feat: conditionally pass --chrome or --no-chrome based on setting"
```

---

### Task 7: Frontend — constants.js

**Files:**
- Modify: `frontend/src/constants.js` (after THINKING block ~line 269, and SYNCED_SETTINGS_KEYS ~line 275)

**Step 1: Add CLAUDE_IN_CHROME constant and default**

After the `THINKING_DISPLAY_LABELS` block (line 269), add:

```javascript
/**
 * Claude in Chrome MCP mode values.
 * Controls whether the built-in Chrome MCP is activated.
 */
export const CLAUDE_IN_CHROME = {
    ENABLED: true,
    DISABLED: false,
}

export const DEFAULT_CLAUDE_IN_CHROME = CLAUDE_IN_CHROME.ENABLED

/**
 * Human-friendly labels for each Claude in Chrome mode.
 */
export const CLAUDE_IN_CHROME_LABELS = {
    [CLAUDE_IN_CHROME.ENABLED]: 'Enabled',
    [CLAUDE_IN_CHROME.DISABLED]: 'Disabled',
}

/**
 * Display input text for each Claude in Chrome mode (shown in the collapsed select).
 */
export const CLAUDE_IN_CHROME_DISPLAY_LABELS = {
    [CLAUDE_IN_CHROME.ENABLED]: 'Chrome MCP',
    [CLAUDE_IN_CHROME.DISABLED]: 'No Chrome MCP',
}
```

**Step 2: Add to SYNCED_SETTINGS_KEYS**

In the `SYNCED_SETTINGS_KEYS` Set (line 275), add after `'alwaysApplyDefaultThinking'`:

```javascript
    'defaultClaudeInChrome',
    'alwaysApplyDefaultClaudeInChrome',
```

**Step 3: Commit**

```bash
git add frontend/src/constants.js
git commit -m "feat: add CLAUDE_IN_CHROME constants and synced settings keys"
```

---

### Task 8: Frontend — stores/settings.js

**Files:**
- Modify: `frontend/src/stores/settings.js`

**Step 1: Update import to include new constants**

Update the import line (line 6) to add `DEFAULT_CLAUDE_IN_CHROME`:

```javascript
import { DEFAULT_DISPLAY_MODE, DEFAULT_THEME_MODE, DEFAULT_SESSION_TIME_FORMAT, DEFAULT_TITLE_SYSTEM_PROMPT, DEFAULT_MAX_CACHED_SESSIONS, DEFAULT_PERMISSION_MODE, DEFAULT_MODEL, DEFAULT_EFFORT, DEFAULT_THINKING, DEFAULT_CLAUDE_IN_CHROME, DISPLAY_MODE, THEME_MODE, SESSION_TIME_FORMAT, PERMISSION_MODE, MODEL, EFFORT, SYNCED_SETTINGS_KEYS } from '../constants'
```

**Step 2: Add to SETTINGS_SCHEMA**

After `alwaysApplyDefaultThinking: false,` (line 42), add:

```javascript
    defaultClaudeInChrome: DEFAULT_CLAUDE_IN_CHROME,
    alwaysApplyDefaultClaudeInChrome: false,
```

**Step 3: Add validators**

After `alwaysApplyDefaultThinking: (v) => typeof v === 'boolean',` (line 87), add:

```javascript
    defaultClaudeInChrome: (v) => typeof v === 'boolean',
    alwaysApplyDefaultClaudeInChrome: (v) => typeof v === 'boolean',
```

**Step 4: Add getters**

After `isAlwaysApplyDefaultThinking` (line 181), add:

```javascript
        getDefaultClaudeInChrome: (state) => state.defaultClaudeInChrome,
        isAlwaysApplyDefaultClaudeInChrome: (state) => state.alwaysApplyDefaultClaudeInChrome,
```

**Step 5: Add actions**

After `setAlwaysApplyDefaultThinking` (line 455-459), add:

```javascript
        /**
         * Set the default Claude in Chrome MCP mode for new sessions.
         * @param {boolean} enabled - true to enable, false to disable
         */
        setDefaultClaudeInChrome(enabled) {
            if (SETTINGS_VALIDATORS.defaultClaudeInChrome(enabled)) {
                this.defaultClaudeInChrome = enabled
            }
        },

        /**
         * Set whether the default Claude in Chrome MCP mode should always be applied,
         * even for sessions that have an explicit value in the database.
         * @param {boolean} enabled
         */
        setAlwaysApplyDefaultClaudeInChrome(enabled) {
            if (SETTINGS_VALIDATORS.alwaysApplyDefaultClaudeInChrome(enabled)) {
                this.alwaysApplyDefaultClaudeInChrome = enabled
            }
        },
```

**Step 6: Add to localStorage watcher**

In the `initSettings()` watch callback (around line 560), add the two new keys to the watched object:

```javascript
            defaultClaudeInChrome: store.defaultClaudeInChrome,
            alwaysApplyDefaultClaudeInChrome: store.alwaysApplyDefaultClaudeInChrome,
```

**Step 7: Commit**

```bash
git add frontend/src/stores/settings.js
git commit -m "feat: add Claude in Chrome settings to settings store"
```

---

### Task 9: Frontend — SettingsPopover.vue

**Files:**
- Modify: `frontend/src/components/SettingsPopover.vue`

**Step 1: Update imports**

Add `CLAUDE_IN_CHROME` and `CLAUDE_IN_CHROME_LABELS` to the constants import (line 8):

```javascript
import { DISPLAY_MODE, THEME_MODE, SESSION_TIME_FORMAT, DEFAULT_TITLE_SYSTEM_PROMPT, DEFAULT_MAX_CACHED_SESSIONS, PERMISSION_MODE, PERMISSION_MODE_LABELS, PERMISSION_MODE_DESCRIPTIONS, MODEL, MODEL_LABELS, EFFORT, EFFORT_LABELS, THINKING, THINKING_LABELS, CLAUDE_IN_CHROME, CLAUDE_IN_CHROME_LABELS } from '../constants'
```

**Step 2: Add computed refs**

After `alwaysApplyDefaultThinking` computed (line 64), add:

```javascript
const defaultClaudeInChrome = computed(() => store.getDefaultClaudeInChrome)
const alwaysApplyDefaultClaudeInChrome = computed(() => store.isAlwaysApplyDefaultClaudeInChrome)
```

**Step 3: Add Claude in Chrome options**

After `thinkingOptions` (line 123), add:

```javascript
// Claude in Chrome options for the select (use string values for wa-select compatibility)
const claudeInChromeOptions = [
    { value: 'true', label: CLAUDE_IN_CHROME_LABELS[true] },
    { value: 'false', label: CLAUDE_IN_CHROME_LABELS[false] },
]
```

**Step 4: Add event handlers**

After `onAlwaysApplyDefaultThinkingChange` (line 254-256), add:

```javascript
/**
 * Handle default Claude in Chrome change.
 */
function onDefaultClaudeInChromeChange(event) {
    store.setDefaultClaudeInChrome(event.target.value === 'true')
}

/**
 * Toggle "always apply default Claude in Chrome" setting.
 */
function onAlwaysApplyDefaultClaudeInChromeChange(event) {
    store.setAlwaysApplyDefaultClaudeInChrome(event.target.checked)
}
```

**Step 5: Add UI in template**

In the "Claude settings" section, after the thinking setting-group `</div>` (before the "always apply" hint `<div>` at line 447), add:

```html
                    <div class="setting-group">
                        <label class="setting-group-label">Default Chrome MCP</label>
                        <wa-select
                            :value.prop="String(defaultClaudeInChrome)"
                            @change="onDefaultClaudeInChromeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in claudeInChromeOptions"
                                :key="option.value"
                                :value="option.value"
                            >
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                        <wa-switch
                            :checked="alwaysApplyDefaultClaudeInChrome"
                            @change="onAlwaysApplyDefaultClaudeInChromeChange"
                            size="small"
                        >Always apply *</wa-switch>
                        <span class="setting-group-hint">Only applies to new sessions.</span>
                    </div>
```

**Step 6: Commit**

```bash
git add frontend/src/components/SettingsPopover.vue
git commit -m "feat: add Claude in Chrome setting to SettingsPopover"
```

---

### Task 10: Frontend — MessageInput.vue

**Files:**
- Modify: `frontend/src/components/MessageInput.vue`

This is the most complex task. Follow the exact pattern used for `thinking`.

**Step 1: Update constants import**

Add `CLAUDE_IN_CHROME_LABELS`, `CLAUDE_IN_CHROME_DISPLAY_LABELS` to the import (line 11).

**Step 2: Add Claude in Chrome dropdown options**

After `thinkingOptions` (line 90), add:

```javascript
// Claude in Chrome options for the dropdown (use string values for wa-select compatibility)
const claudeInChromeOptions = [
    { value: 'true', label: CLAUDE_IN_CHROME_LABELS[true] },
    { value: 'false', label: CLAUDE_IN_CHROME_LABELS[false] },
]
```

**Step 3: Add to settings summary**

Update `settingsSummary` (line 93) to include Chrome MCP:

```javascript
const settingsSummary = computed(() => [
    MODEL_LABELS[selectedModel.value],
    EFFORT_DISPLAY_LABELS[selectedEffort.value],
    THINKING_DISPLAY_LABELS[selectedThinking.value],
    CLAUDE_IN_CHROME_DISPLAY_LABELS[selectedClaudeInChrome.value],
    PERMISSION_MODE_LABELS[selectedPermissionMode.value],
].join(' · '))
```

**Step 4: Add selected and active refs**

After `selectedThinking` ref (line 110), add:

```javascript
// Selected Claude in Chrome mode for the current session
const selectedClaudeInChrome = ref(true)
```

After `activeThinking` ref (line 204), add:

```javascript
const activeClaudeInChrome = ref(null)
```

**Step 5: Update hasDropdownsChanged**

Add `selectedClaudeInChrome.value !== activeClaudeInChrome.value` to the computed (line 208-213).

**Step 6: Add resolveClaudeInChrome function**

After `resolveThinking` (line 254-259), add:

```javascript
// Determine the effective Claude in Chrome mode for the current session.
function resolveClaudeInChrome(sess) {
    if (settingsStore.isAlwaysApplyDefaultClaudeInChrome) {
        return settingsStore.getDefaultClaudeInChrome
    }
    return sess?.claude_in_chrome ?? settingsStore.getDefaultClaudeInChrome
}
```

**Step 7: Update session change watcher**

In the `watch(() => props.sessionId, ...)` callback (line 262), add resolving and setting for `claudeInChrome`:

```javascript
    const resolvedClaudeInChrome = resolveClaudeInChrome(sess)
    selectedClaudeInChrome.value = resolvedClaudeInChrome
    activeClaudeInChrome.value = sess?.claude_in_chrome ?? resolvedClaudeInChrome
```

**Step 8: Add settings default watcher**

After the thinking settings watcher (line 323-332), add:

```javascript
// When the default Claude in Chrome setting changes, update the dropdown for sessions that should follow the default.
watch(
    () => [settingsStore.getDefaultClaudeInChrome, settingsStore.isAlwaysApplyDefaultClaudeInChrome],
    () => {
        if (processIsActive.value) return
        const sess = store.getSession(props.sessionId)
        const resolved = resolveClaudeInChrome(sess)
        selectedClaudeInChrome.value = resolved
        activeClaudeInChrome.value = resolved
    }
)
```

**Step 9: Add backend data watcher**

After the thinking_enabled backend watcher (line 378-388), add:

```javascript
// React when claude_in_chrome data arrives from backend.
watch(
    () => store.getSession(props.sessionId)?.claude_in_chrome,
    (newValue) => {
        if (newValue != null) {
            activeClaudeInChrome.value = newValue
            if (!processIsActive.value) {
                selectedClaudeInChrome.value = newValue
            }
        }
    }
)
```

**Step 10: Add claude_in_chrome to send payload**

In `handleSend()`, add to the payload object (after `thinking_enabled`, line 712):

```javascript
        claude_in_chrome: selectedClaudeInChrome.value,
```

**Step 11: Sync active value on send**

In the success block after send (after `activeThinking.value = selectedThinking.value`, line 745), add:

```javascript
        activeClaudeInChrome.value = selectedClaudeInChrome.value
```

**Step 12: Add to handleReset**

In `handleReset()`, add inside the `hasDropdownsChanged` block (after the thinking reset, line 850-852):

```javascript
        if (activeClaudeInChrome.value !== null) {
            selectedClaudeInChrome.value = activeClaudeInChrome.value
        }
```

**Step 13: Add dropdown to template**

In the settings popover template, after the Permission `setting-row` (after line 1022), add:

```html
                        <div class="setting-row">
                            <label class="setting-label">Claude built-in Chrome MCP</label>
                            <wa-select
                                :value.prop="String(selectedClaudeInChrome)"
                                @change="selectedClaudeInChrome = $event.target.value === 'true'"
                                size="small"
                                :disabled="isEffortThinkingDisabled"
                            >
                                <wa-option v-for="option in claudeInChromeOptions" :key="option.value" :value="option.value" :label="option.label">
                                    {{ option.label }}
                                </wa-option>
                            </wa-select>
                            <span class="setting-help">Cannot be changed while a process is running.</span>
                        </div>
```

**Step 14: Commit**

```bash
git add frontend/src/components/MessageInput.vue
git commit -m "feat: add Claude in Chrome toggle to MessageInput popover"
```

---

### Task 11: README — Remove limitation

**Files:**
- Modify: `README.md:141-143`

**Step 1: Remove the limitation line**

Remove lines 141-143 (the "Current limitations" section header and the Chrome limitation bullet). If no other limitations remain, remove the entire section.

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: remove Chrome MCP always-active limitation (now configurable)"
```

---

### Task 12: Run migration

Remind the user to run:
```bash
cd /home/twidi/dev/twicc-poc && uv run python -m django migrate
```
