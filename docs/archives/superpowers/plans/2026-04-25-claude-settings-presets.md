# Claude Config Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a global, project-agnostic library of Claude config presets (name + six nullable Claude fields), managed through a two-screen dialog reachable from the Claude section of the settings panel. Storage in `<data_dir>/claude-settings-presets.json`, served at bootstrap, synced via WebSocket.

**Architecture:** Mirrors the existing `terminal-config.json` pipeline end-to-end — backend module (read/write atomic) + WS handlers + bootstrap include + Pinia store + dialog component. Reuses the `__default__` sentinel pattern from `MessageInput.vue` for the "Default + Force to" semantics, and the layout/CSS structure of `TerminalCombosDialog.vue` (with renamed classes) for the dialog.

**Tech Stack:** Django Channels (ASGI WebSocket), `orjson` for JSON I/O, Vue 3 (Composition API + `<script setup>`), Pinia, Web Awesome 3.1 components.

**Spec:** `docs/superpowers/specs/2026-04-25-claude-settings-presets-design.md`

**Project conventions:**
- All UI strings, comments, and identifiers must be in **English**.
- The project intentionally has **no tests and no linting** (per CLAUDE.md). Verification steps below are read-back code reviews and end-of-task manual smoke tests in the browser.
- **The user — not Claude — restarts dev servers and runs migrations.** When backend changes need a restart for verification, ask the user.
- Use `orjson` (not stdlib `json`) for backend JSON.
- Pinia stores use Composition API setup style (cf. `terminalConfig.js`).
- Stores avoid circular imports by lazy-importing the `useWebSocket` sender (`await import(...)`).
- Web Awesome components must be explicitly imported in `frontend/src/main.js` to load their styles. The components used here (`wa-dialog`, `wa-button`, `wa-input`, `wa-select`, `wa-option`, `wa-icon`, `wa-callout`) are already imported by `TerminalCombosDialog.vue`/`SettingsPopover.vue` siblings — no new import needed in `main.js`.

**Reviewer advisory items addressed:**
- *Concurrent edits while form view is open*: explicitly accepted (last-write-wins, same as combos). No snapshot logic.
- *Reorder triggers a `_sendConfig()` per swap*: accepted (consistent with combos, no debounce).
- *Unknown future preset fields*: dropped silently on read (the store normalizes to the known shape). Acceptable for v1.

**Line number conventions in this plan:** the line ranges cited next to file paths (e.g. "asgi.py:598–614") are *approximate hints*. Files drift between the time this plan was written and the time you read it. Always locate by **anchor strings** (e.g. `_handle_update_message_snippets`, `sendMessageSnippetsConfig`, `terminal_config_updated`) and treat the line ranges as a "look around here." The plan uses anchors that are unambiguous in the codebase.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/twicc/paths.py` | Modify | Add `get_claude_settings_presets_path()` |
| `src/twicc/claude_settings_presets.py` | Create | `read_claude_settings_presets()` / `write_claude_settings_presets()` (atomic) |
| `src/twicc/views.py` | Modify | `bootstrap()` includes `claude_settings_presets` key |
| `src/twicc/asgi.py` | Modify | Initial WS send + `update_claude_settings_presets` handler + broadcast |
| `frontend/src/stores/claudeSettingsPresets.js` | Create | Pinia store: state + `applyConfig` + `_sendConfig` + CRUD |
| `frontend/src/composables/useWebSocket.js` | Modify | `sendClaudeSettingsPresets()` + `claude_settings_presets_updated` handler case |
| `frontend/src/main.js` | Modify | Bootstrap dispatch into the new store |
| `frontend/src/components/ClaudePresetsDialog.vue` | Create | Two-view dialog (list + form) |
| `frontend/src/components/SettingsPopover.vue` | Modify | "Manage presets…" button in Claude section + dialog mount |

---

## Task 1: Backend — path helper + read/write module

**Files:**
- Modify: `src/twicc/paths.py`
- Create: `src/twicc/claude_settings_presets.py`

- [ ] **Step 1: Add the path helper**

In `src/twicc/paths.py`, add (next to the other `get_*_path` helpers):

```python
def get_claude_settings_presets_path() -> Path:
    return get_data_dir() / "claude-settings-presets.json"
```

Place it logically with the other config-file path getters (e.g. just after `get_workspaces_path`).

- [ ] **Step 2: Create the read/write module**

Create `src/twicc/claude_settings_presets.py` with the exact pattern of `src/twicc/terminal_config.py`:

```python
"""Read/write Claude config presets.

File: <data_dir>/claude-settings-presets.json
"""
import os
import tempfile

import orjson

from twicc.paths import get_claude_settings_presets_path


def read_claude_settings_presets() -> dict:
    """Read claude-settings-presets.json. Returns empty config if missing or invalid."""
    path = get_claude_settings_presets_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {"presets": []}


def write_claude_settings_presets(config: dict) -> None:
    """Write claude-settings-presets.json atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_claude_settings_presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = orjson.dumps(config, option=orjson.OPT_INDENT_2)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] **Step 3: Smoke-verify the module**

From the project root, run a one-shot Python invocation to verify import and round-trip:

```bash
uv run python -c "
from twicc.claude_settings_presets import read_claude_settings_presets, write_claude_settings_presets
print('initial:', read_claude_settings_presets())
write_claude_settings_presets({'presets': [{'name': 'smoke', 'model': None, 'context_max': None, 'effort': None, 'thinking': None, 'permission_mode': None, 'claude_in_chrome': None}]})
print('after write:', read_claude_settings_presets())
write_claude_settings_presets({'presets': []})
print('reset:', read_claude_settings_presets())
"
```

Expected: prints `{'presets': []}`, then the round-tripped object, then `{'presets': []}` again. The file `<data_dir>/claude-settings-presets.json` must be present at the data dir for the duration of the test. Reset to empty at the end so we don't ship local data.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/paths.py src/twicc/claude_settings_presets.py
git commit -m "feat(presets): backend read/write for Claude config presets

Adds claude-settings-presets.json under the data dir with the same
atomic write pattern used for terminal-config.json and friends."
```

---

## Task 2: Backend — `/api/bootstrap/` includes presets

**Files:**
- Modify: `src/twicc/views.py:2042-2068` (the `bootstrap()` function)

- [ ] **Step 1: Add the import inside `bootstrap()`**

Inside the `bootstrap()` function body (alongside the other local imports), add:

```python
from twicc.claude_settings_presets import read_claude_settings_presets
```

Place it next to `from twicc.terminal_config import read_terminal_config` to keep imports grouped by feature.

- [ ] **Step 2: Add the response key**

In the `JsonResponse({...})` dict, add the new key (alphabetically near the other configs):

```python
"claude_settings_presets": read_claude_settings_presets(),
```

The diff inside the `JsonResponse` looks like:

```python
return JsonResponse({
    "settings": clean_settings,
    "settings_version": version,
    "default_settings": SYNCED_SETTINGS_DEFAULTS,
    "claude_settings_categories": CLAUDE_SETTINGS_CATEGORIES,
    "dev_mode": settings.DEV_MODE,
    "uvx_mode": settings.UVX_MODE,
    "workspaces": workspaces_data.get("workspaces", []),
    "terminal_config": read_terminal_config(),
    "message_snippets": read_message_snippets_config(),
    "claude_settings_presets": read_claude_settings_presets(),  # NEW
    "model_registry": serialize_model_registry(),
})
```

- [ ] **Step 3: Read back the change**

Re-read the function and confirm:
- The new import is inside the function (matching the local-import style).
- The new key is exactly `claude_settings_presets` (snake_case, matches the WS event name pattern).
- No other dict key was modified.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/views.py
git commit -m "feat(presets): include claude_settings_presets in /api/bootstrap/"
```

> ⚠️ Backend restart required for this change to take effect. **Do not** restart yourself — note in your end-of-task message that the user must restart the backend before the bootstrap response will include presets.

---

## Task 3: Backend — WebSocket initial send + handler + broadcast

**Files:**
- Modify: `src/twicc/asgi.py` (initial-send block ~lines 598–614, routing block ~lines 662–724, handler block ~lines 1336–1395)

- [ ] **Step 1: Add the import**

At the top of `asgi.py`, alongside `from twicc.terminal_config import read_terminal_config, write_terminal_config`, add:

```python
from twicc.claude_settings_presets import read_claude_settings_presets, write_claude_settings_presets
```

- [ ] **Step 2: Send initial state on WS connect**

In the connect block (around line 598–614, where `terminal_config_updated` is sent), add a new conditional block immediately after the `message_snippets_updated` block:

```python
if self._should_send("claude_settings_presets_updated"):
    presets = await sync_to_async(read_claude_settings_presets)()
    await self.send_json({"type": "claude_settings_presets_updated", "config": presets})
```

The position must be after `message_snippets_updated` and before `workspaces_updated` to mirror the bootstrap response ordering.

- [ ] **Step 3: Route incoming messages**

In `receive_json()` (around lines 662–724), add a new `elif` branch immediately after the `update_message_snippets` branch:

```python
elif msg_type == "update_claude_settings_presets":
    await self._handle_update_claude_settings_presets(content)
```

- [ ] **Step 4: Implement the handler**

In the handler section (around lines 1336–1395), add a new method right after `_handle_update_message_snippets`:

```python
async def _handle_update_claude_settings_presets(self, content: dict) -> None:
    config = content.get("config")
    if not isinstance(config, dict):
        return
    await sync_to_async(write_claude_settings_presets)(config)
    await self.channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "claude_settings_presets_updated", "config": config},
    })
```

The validation is intentionally light (`isinstance(config, dict)`) — same as the sibling handlers. Trust the client to send a well-formed `{"presets": [...]}`.

- [ ] **Step 5: Read back the change**

Verify by re-reading `asgi.py`:
- New import is present.
- Initial-send block is present and uses `_should_send("claude_settings_presets_updated")`.
- The `elif msg_type == "update_claude_settings_presets":` branch dispatches to the new handler.
- The handler writes via `write_claude_settings_presets` and broadcasts via `group_send("updates", ...)` with `"type": "broadcast"` and the inner `data` dict.

- [ ] **Step 6: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat(presets): WebSocket sync for Claude config presets

On connect, send current presets to the client. Accept
update_claude_settings_presets messages, write atomically, and
broadcast the change to all connected clients."
```

> ⚠️ Backend restart required. Mention this to the user at the end of the backend tasks.

---

## Task 4: Frontend — Pinia store

**Files:**
- Create: `frontend/src/stores/claudeSettingsPresets.js`

- [ ] **Step 1: Create the store skeleton**

Calque this on `frontend/src/stores/terminalConfig.js`. Create the file with the following content:

```js
import { defineStore } from 'pinia'
import { ref } from 'vue'

const PRESET_FIELDS = [
    'model',
    'context_max',
    'effort',
    'thinking',
    'permission_mode',
    'claude_in_chrome',
]

function normalizePreset(raw) {
    const preset = { name: typeof raw?.name === 'string' ? raw.name : '' }
    for (const field of PRESET_FIELDS) {
        preset[field] = raw && field in raw ? raw[field] : null
    }
    return preset
}

export const useClaudeSettingsPresetsStore = defineStore('claudeSettingsPresets', () => {
    const presets = ref([])
    const initialized = ref(false)

    function applyConfig(config) {
        const list = Array.isArray(config?.presets) ? config.presets : []
        presets.value = list.map(normalizePreset)
        initialized.value = true
    }

    async function _sendConfig() {
        const { sendClaudeSettingsPresets } = await import('../composables/useWebSocket')
        sendClaudeSettingsPresets({ presets: presets.value })
    }

    function findPresetIndexByName(name, excludeIndex = null) {
        const target = name.trim().toLowerCase()
        return presets.value.findIndex((p, i) => i !== excludeIndex && p.name.trim().toLowerCase() === target)
    }

    function findPresetByName(name, excludeIndex = null) {
        const idx = findPresetIndexByName(name, excludeIndex)
        return idx === -1 ? null : presets.value[idx]
    }

    function addPreset(preset) {
        presets.value.push(normalizePreset(preset))
        _sendConfig()
    }

    function updatePreset(index, preset) {
        if (index < 0 || index >= presets.value.length) return
        presets.value.splice(index, 1, normalizePreset(preset))
        _sendConfig()
    }

    function deletePreset(index) {
        if (index < 0 || index >= presets.value.length) return
        presets.value.splice(index, 1)
        _sendConfig()
    }

    function duplicatePreset(index) {
        if (index < 0 || index >= presets.value.length) return
        const source = presets.value[index]
        const baseName = `${source.name} (copy)`
        let candidate = baseName
        let n = 2
        while (findPresetIndexByName(candidate) !== -1) {
            candidate = `${baseName} ${n}`
            n += 1
        }
        const copy = normalizePreset({ ...source, name: candidate })
        presets.value.splice(index + 1, 0, copy)
        _sendConfig()
    }

    function reorderPreset(index, direction) {
        const target = index + direction
        if (target < 0 || target >= presets.value.length) return
        const [moved] = presets.value.splice(index, 1)
        presets.value.splice(target, 0, moved)
        _sendConfig()
    }

    return {
        presets,
        initialized,
        applyConfig,
        findPresetByName,
        findPresetIndexByName,
        addPreset,
        updatePreset,
        deletePreset,
        duplicatePreset,
        reorderPreset,
    }
})
```

Notes:
- `normalizePreset` enforces the six-field shape: unknown keys are dropped, missing keys become `null`. This matches the spec's accepted behavior for unknown future fields.
- `applyConfig` does **not** call `_sendConfig()` — bootstrap and WS-incoming hydration must not echo back to the server.
- The lazy `import('../composables/useWebSocket')` mirrors `terminalConfig.js` and avoids a circular import.

- [ ] **Step 2: Read back the file**

Re-read the new file and confirm:
- All six field names match the JSON schema and the `Session.keep_settings` columns (`model`, `context_max`, `effort`, `thinking`, `permission_mode`, `claude_in_chrome`).
- `addPreset`, `updatePreset`, `deletePreset`, `duplicatePreset`, `reorderPreset` all call `_sendConfig()` exactly once on success.
- `findPresetIndexByName` is case-insensitive and trims, and respects `excludeIndex`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/claudeSettingsPresets.js
git commit -m "feat(presets): Pinia store for Claude config presets"
```

---

## Task 5: Frontend — WebSocket sender + incoming handler

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js` (sender block ~lines 277–309, message switch ~line 866)

- [ ] **Step 1: Add the sender**

After the `sendMessageSnippetsConfig` export (around line 309), add:

```js
export function sendClaudeSettingsPresets(config) {
    return sendWsMessage({ type: 'update_claude_settings_presets', config })
}
```

- [ ] **Step 2: Add the incoming case**

In the `switch (msg.type)` block (around line 675), after the `'message_snippets_updated'` case, add:

```js
case 'claude_settings_presets_updated':
    import('../stores/claudeSettingsPresets').then(({ useClaudeSettingsPresetsStore }) => {
        useClaudeSettingsPresetsStore().applyConfig(msg.config)
    })
    break
```

The lazy `import()` is required (cf. circular-import rules in CLAUDE.md and the sibling cases).

- [ ] **Step 3: Read back**

Confirm:
- The sender uses message type `update_claude_settings_presets`.
- The receiver matches event type `claude_settings_presets_updated`.
- The payload field is `config` (consistent with terminal_config / message_snippets) — not `presets`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat(presets): WebSocket glue for Claude config presets"
```

---

## Task 6: Frontend — bootstrap dispatch

**Files:**
- Modify: `frontend/src/main.js:106-146`

- [ ] **Step 1: Add the import**

In the import block at the top (around lines 53–55, where `useWorkspacesStore`, `useTerminalConfigStore`, `useMessageSnippetsStore` are imported), add:

```js
import { useClaudeSettingsPresetsStore } from './stores/claudeSettingsPresets'
```

- [ ] **Step 2: Add the dispatch line**

In the bootstrap block (around line 144, just after `useMessageSnippetsStore().applyConfig(bootstrapData.message_snippets)`), add:

```js
useClaudeSettingsPresetsStore().applyConfig(bootstrapData.claude_settings_presets)
```

The store's `applyConfig` already handles `undefined` / `null` / missing key gracefully (falls back to empty list).

- [ ] **Step 3: Read back**

Confirm:
- The new import sits with its three peer store imports.
- The new dispatch sits after the three peer dispatches.
- No other line in `main.js` was modified.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/main.js
git commit -m "feat(presets): hydrate presets store from /api/bootstrap/"
```

---

## Task 7: Frontend — dialog skeleton (component + dialog wrapper, both views empty)

**Files:**
- Create: `frontend/src/components/ClaudePresetsDialog.vue`

- [ ] **Step 1: Create the dialog skeleton**

Create the file with the dialog wrapper, the `view` ref, and empty placeholders for list and form views. We'll fill the list view in Task 8 and the form view in Task 9.

```vue
<script setup>
import { computed, nextTick, ref } from 'vue'
import { useClaudeSettingsPresetsStore } from '../stores/claudeSettingsPresets'

const props = defineProps({
    open: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open'])

const store = useClaudeSettingsPresetsStore()

const view = ref('list')
const editIndex = ref(null)
const formData = ref(emptyFormData())
const errorMessage = ref('')

const dialogRef = ref(null)
const nameInputRef = ref(null)

const presets = computed(() => store.presets)

function emptyFormData() {
    return {
        name: '',
        model: null,
        context_max: null,
        effort: null,
        thinking: null,
        permission_mode: null,
        claude_in_chrome: null,
    }
}

function closeDialog() {
    emit('update:open', false)
}

function onAfterShow() {
    view.value = 'list'
    editIndex.value = null
    formData.value = emptyFormData()
    errorMessage.value = ''
}

// List → Form transitions (filled in Task 8/9)
function openAddForm() {
    formData.value = emptyFormData()
    editIndex.value = null
    errorMessage.value = ''
    view.value = 'form'
    focusNameInput()
}

function openEditForm(index) {
    const source = presets.value[index]
    if (!source) return
    formData.value = { ...source }
    editIndex.value = index
    errorMessage.value = ''
    view.value = 'form'
    focusNameInput()
}

function cancelForm() {
    view.value = 'list'
    errorMessage.value = ''
}

async function focusNameInput() {
    await nextTick()
    const el = nameInputRef.value
    if (!el) return
    el.focus?.()
    const inner = el.querySelector?.('input')
    if (inner && typeof inner.setSelectionRange === 'function') {
        const len = inner.value.length
        inner.setSelectionRange(len, len)
    }
}

function handleSave() {
    // Filled in Task 9
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        class="manage-presets-dialog"
        label="Claude config presets"
        :open="props.open"
        @wa-after-show="onAfterShow"
        @wa-after-hide="closeDialog"
    >
        <div class="dialog-content">
            <div v-if="view === 'list'" class="preset-list-view">
                <!-- Filled in Task 8 -->
                <p class="empty-hint">List view placeholder.</p>
            </div>
            <form v-else id="preset-form" class="preset-form" @submit.prevent="handleSave">
                <!-- Filled in Task 9 -->
                <p class="empty-hint">Form view placeholder.</p>
            </form>
        </div>

        <div slot="footer" class="dialog-footer">
            <template v-if="view === 'list'">
                <wa-button @click="closeDialog">Close</wa-button>
                <wa-button variant="brand" @click="openAddForm">
                    <wa-icon slot="start" name="plus"></wa-icon>
                    Add preset
                </wa-button>
            </template>
            <template v-else>
                <wa-button @click="cancelForm">Cancel</wa-button>
                <wa-button ref="submitButtonRef" variant="brand" type="submit">Save</wa-button>
            </template>
        </div>
    </wa-dialog>
</template>

<style scoped>
.manage-presets-dialog {
    --width: min(40rem, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.dialog-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

.empty-hint {
    color: var(--wa-color-text-quiet);
    font-style: italic;
    margin: 0;
}
</style>
```

> Note on the submit button: `wa-button` does not expose `form` as a property, so we will wire `setAttribute('form', 'preset-form')` in Task 9 via a ref watcher. Leaving the button without `form="…"` for now is fine; the form is empty and the submit can't fire until Task 9.

- [ ] **Step 2: Verify the file parses**

The frontend dev server (Vite) auto-reloads on save. Ask the user to open the app and confirm no console error appears (the dialog isn't mounted yet, but the SFC compilation must succeed). If the user says "looks good in the console", proceed.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ClaudePresetsDialog.vue
git commit -m "feat(presets): dialog skeleton with two-view scaffold"
```

---

## Task 8: Frontend — dialog list view

**Files:**
- Modify: `frontend/src/components/ClaudePresetsDialog.vue` (replace the list-view placeholder + add `formatPresetSummary` import + extend `<style>`)

- [ ] **Step 1: Add the formatter**

In `<script setup>`, add the imports near the top:

```js
import {
    PERMISSION_MODE_LABELS,
    EFFORT_LABELS,
    THINKING_LABELS,
    CLAUDE_IN_CHROME_LABELS,
    CONTEXT_MAX_LABELS,
    getModelLabel,
} from '../constants'
```

Below `emptyFormData`, add the formatter:

```js
function formatPresetSummary(preset) {
    const parts = []
    if (preset.model !== null && preset.model !== undefined) {
        parts.push(`model: ${getModelLabel(preset.model)}`)
    }
    if (preset.context_max !== null && preset.context_max !== undefined) {
        parts.push(`context: ${CONTEXT_MAX_LABELS[preset.context_max] ?? preset.context_max}`)
    }
    if (preset.effort !== null && preset.effort !== undefined) {
        parts.push(`effort: ${EFFORT_LABELS[preset.effort] ?? preset.effort}`)
    }
    if (preset.thinking !== null && preset.thinking !== undefined) {
        parts.push(`thinking: ${THINKING_LABELS[String(preset.thinking)] ?? preset.thinking}`)
    }
    if (preset.permission_mode !== null && preset.permission_mode !== undefined) {
        parts.push(`permission: ${PERMISSION_MODE_LABELS[preset.permission_mode] ?? preset.permission_mode}`)
    }
    if (preset.claude_in_chrome !== null && preset.claude_in_chrome !== undefined) {
        parts.push(`chrome: ${CLAUDE_IN_CHROME_LABELS[String(preset.claude_in_chrome)] ?? preset.claude_in_chrome}`)
    }
    return parts.length === 0 ? 'all default' : parts.join(' · ')
}
```

- [ ] **Step 2: Implement list view actions**

Add to `<script setup>`:

```js
function handleDelete(index) {
    store.deletePreset(index)
}

function handleDuplicate(index) {
    store.duplicatePreset(index)
}

function handleReorder(index, direction) {
    store.reorderPreset(index, direction)
}
```

- [ ] **Step 3: Replace the list-view placeholder**

Replace the placeholder block (`<div v-if="view === 'list'" class="preset-list-view"> ... </div>`) with:

```html
<div v-if="view === 'list'" class="preset-list-view">
    <p v-if="presets.length === 0" class="empty-hint">No presets yet.</p>
    <ul v-else class="preset-list">
        <li v-for="(preset, index) in presets" :key="index" class="preset-row">
            <div class="reorder-arrows">
                <button
                    type="button"
                    class="reorder-btn"
                    :disabled="index === 0"
                    aria-label="Move up"
                    @click="handleReorder(index, -1)"
                >
                    <wa-icon name="chevron-up"></wa-icon>
                </button>
                <button
                    type="button"
                    class="reorder-btn"
                    :disabled="index === presets.length - 1"
                    aria-label="Move down"
                    @click="handleReorder(index, 1)"
                >
                    <wa-icon name="chevron-down"></wa-icon>
                </button>
            </div>
            <div class="preset-display">
                <div class="preset-name">{{ preset.name }}</div>
                <div class="preset-summary">{{ formatPresetSummary(preset) }}</div>
            </div>
            <div class="preset-actions">
                <button
                    type="button"
                    class="action-btn"
                    aria-label="Edit preset"
                    @click="openEditForm(index)"
                >
                    <wa-icon name="pencil"></wa-icon>
                </button>
                <button
                    type="button"
                    class="action-btn"
                    aria-label="Duplicate preset"
                    @click="handleDuplicate(index)"
                >
                    <wa-icon name="copy"></wa-icon>
                </button>
                <button
                    type="button"
                    class="action-btn action-btn-danger"
                    aria-label="Delete preset"
                    @click="handleDelete(index)"
                >
                    <wa-icon name="trash"></wa-icon>
                </button>
            </div>
        </li>
    </ul>
</div>
```

- [ ] **Step 4: Add list-view CSS**

Append the following to `<style scoped>` (these match `TerminalCombosDialog.vue` with renamed classes — read that file's `<style>` block as reference for visual parity):

```css
.preset-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.preset-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-xs) var(--wa-space-s);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-surface-default);
}

.reorder-arrows {
    display: flex;
    flex-direction: column;
    gap: 2px;
}

.reorder-btn,
.action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    border: none;
    cursor: pointer;
    color: var(--wa-color-text-normal);
    padding: var(--wa-space-3xs);
    border-radius: var(--wa-border-radius-s);
}

.reorder-btn:hover:not(:disabled),
.action-btn:hover {
    background: var(--wa-color-surface-raised);
}

.reorder-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
}

.action-btn-danger:hover {
    color: var(--wa-color-danger-fill-loud);
}

.preset-display {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
}

.preset-name {
    font-weight: var(--wa-font-weight-semibold);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.preset-summary {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.preset-actions {
    display: flex;
    gap: var(--wa-space-3xs);
}
</style>
```

> Cross-reference `frontend/src/components/TerminalCombosDialog.vue` while writing the styles. If a value differs in that file (e.g. exact padding, exact gap), prefer the combos value to keep visual parity.

- [ ] **Step 5: Manual smoke test (with the user)**

Ask the user to:
1. Confirm the frontend dev server is running.
2. (Temporarily, since the dialog isn't reachable from the UI yet) open the browser devtools console, evaluate:

```js
const { useClaudeSettingsPresetsStore } = await import('/src/stores/claudeSettingsPresets.js')
const s = useClaudeSettingsPresetsStore()
s.addPreset({ name: 'Test', model: 'opus', effort: 'high' })
```

3. Confirm the network tab shows a WS message `update_claude_settings_presets` and the file `<data_dir>/claude-settings-presets.json` was written. Then run `s.deletePreset(0)` to clean up.

If the user prefers to skip this until Task 10 (when the dialog is reachable), that's fine — note in the commit that the list view is built but unverified visually.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ClaudePresetsDialog.vue
git commit -m "feat(presets): list view (rows, reorder, edit/duplicate/delete, summary)"
```

---

## Task 9: Frontend — dialog form view (selects + validation + save)

**Files:**
- Modify: `frontend/src/components/ClaudePresetsDialog.vue`

- [ ] **Step 1: Add the constant imports + sentinel**

Extend the existing `import { ... } from '../constants'` to also include the value enums and the latest model registry getter:

```js
import {
    PERMISSION_MODE,
    PERMISSION_MODE_LABELS,
    EFFORT,
    EFFORT_LABELS,
    THINKING_LABELS,
    CLAUDE_IN_CHROME_LABELS,
    CONTEXT_MAX,
    CONTEXT_MAX_LABELS,
    getModelLabel,
} from '../constants'
import { getModelRegistry } from '../stores/settings'
```

Add at module scope (top of `<script setup>`):

```js
const DEFAULT_SENTINEL = '__default__'
```

> Cross-reference: how `MessageInput.vue` imports the same constants and uses the same sentinel — copy that exact mechanism for the form selects.

- [ ] **Step 2: Add computed option lists**

Below the `presets` computed:

```js
const modelOptions = computed(() => {
    const registry = getModelRegistry() || []
    return registry.map((entry) => ({ value: entry.id, label: getModelLabel(entry.id) }))
})

const contextOptions = Object.values(CONTEXT_MAX).map((value) => ({
    value: String(value),
    raw: value,
    label: CONTEXT_MAX_LABELS[value],
}))

const effortOptions = Object.values(EFFORT).map((value) => ({ value, label: EFFORT_LABELS[value] }))

const permissionOptions = Object.values(PERMISSION_MODE).map((value) => ({
    value,
    label: PERMISSION_MODE_LABELS[value],
}))

const thinkingOptions = [
    { value: 'true', raw: true, label: THINKING_LABELS[true] },
    { value: 'false', raw: false, label: THINKING_LABELS[false] },
]

const chromeOptions = [
    { value: 'true', raw: true, label: CLAUDE_IN_CHROME_LABELS[true] },
    { value: 'false', raw: false, label: CLAUDE_IN_CHROME_LABELS[false] },
]
```

- [ ] **Step 3: Add value <-> sentinel helpers**

```js
function toSentinel(value) {
    return value === null || value === undefined ? DEFAULT_SENTINEL : String(value)
}

function fromSentinel(stringValue, parser = (v) => v) {
    if (stringValue === DEFAULT_SENTINEL) return null
    return parser(stringValue)
}
```

- [ ] **Step 4: Wire up `handleSave`**

Replace the placeholder `handleSave` from Task 7 with:

```js
function handleSave() {
    errorMessage.value = ''
    const trimmedName = formData.value.name.trim()
    if (!trimmedName) {
        errorMessage.value = 'Name is required'
        return
    }
    if (store.findPresetIndexByName(trimmedName, editIndex.value) !== -1) {
        errorMessage.value = 'A preset with this name already exists'
        return
    }
    const payload = { ...formData.value, name: trimmedName }
    if (editIndex.value === null) {
        store.addPreset(payload)
    } else {
        store.updatePreset(editIndex.value, payload)
    }
    view.value = 'list'
}
```

- [ ] **Step 5: Wire the submit button to the form**

`wa-button` doesn't expose `form` as a Vue prop, so set the attribute manually after mount. Add a watch:

```js
import { watch } from 'vue'
const submitButtonRef = ref(null)

watch(
    () => view.value,
    async (newView) => {
        if (newView !== 'form') return
        await nextTick()
        const btn = submitButtonRef.value
        if (btn) btn.setAttribute?.('form', 'preset-form')
    },
)
```

(Reference: `ProjectEditDialog.vue` uses the same pattern.)

- [ ] **Step 6: Replace the form-view placeholder**

Replace the placeholder block with the full form. The layout is repetitive (six selects all follow the same pattern); please preserve it verbatim.

```html
<form v-else id="preset-form" class="preset-form" @submit.prevent="handleSave">
    <div class="form-group">
        <label class="form-label" for="preset-name-input">Name</label>
        <wa-input
            id="preset-name-input"
            ref="nameInputRef"
            :value="formData.name"
            size="small"
            required
            @input="formData.name = $event.target.value"
        ></wa-input>
    </div>

    <div class="form-group">
        <label class="form-label">Model</label>
        <wa-select
            size="small"
            :value.prop="toSentinel(formData.model)"
            @change="formData.model = fromSentinel($event.target.value)"
        >
            <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
            <small class="select-group-label">Force to:</small>
            <wa-option v-for="opt in modelOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
            </wa-option>
        </wa-select>
    </div>

    <div class="form-group">
        <label class="form-label">Context size</label>
        <wa-select
            size="small"
            :value.prop="toSentinel(formData.context_max)"
            @change="formData.context_max = fromSentinel($event.target.value, (v) => Number(v))"
        >
            <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
            <small class="select-group-label">Force to:</small>
            <wa-option v-for="opt in contextOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
            </wa-option>
        </wa-select>
    </div>

    <div class="form-group">
        <label class="form-label">Effort</label>
        <wa-select
            size="small"
            :value.prop="toSentinel(formData.effort)"
            @change="formData.effort = fromSentinel($event.target.value)"
        >
            <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
            <small class="select-group-label">Force to:</small>
            <wa-option v-for="opt in effortOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
            </wa-option>
        </wa-select>
    </div>

    <div class="form-group">
        <label class="form-label">Thinking</label>
        <wa-select
            size="small"
            :value.prop="toSentinel(formData.thinking)"
            @change="formData.thinking = fromSentinel($event.target.value, (v) => v === 'true')"
        >
            <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
            <small class="select-group-label">Force to:</small>
            <wa-option v-for="opt in thinkingOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
            </wa-option>
        </wa-select>
    </div>

    <div class="form-group">
        <label class="form-label">Permission mode</label>
        <wa-select
            size="small"
            :value.prop="toSentinel(formData.permission_mode)"
            @change="formData.permission_mode = fromSentinel($event.target.value)"
        >
            <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
            <small class="select-group-label">Force to:</small>
            <wa-option v-for="opt in permissionOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
            </wa-option>
        </wa-select>
    </div>

    <div class="form-group">
        <label class="form-label">Chrome MCP</label>
        <wa-select
            size="small"
            :value.prop="toSentinel(formData.claude_in_chrome)"
            @change="formData.claude_in_chrome = fromSentinel($event.target.value, (v) => v === 'true')"
        >
            <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
            <small class="select-group-label">Force to:</small>
            <wa-option v-for="opt in chromeOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
            </wa-option>
        </wa-select>
    </div>

    <wa-callout v-if="errorMessage" variant="danger">{{ errorMessage }}</wa-callout>
</form>
```

- [ ] **Step 7: Append form-view CSS**

```css
.preset-form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.form-label {
    font-weight: var(--wa-font-weight-semibold);
}

.select-group-label {
    display: block;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    font-style: italic;
}
```

- [ ] **Step 8: Read back**

Re-read the file and confirm:
- Six selects all use the `__default__` sentinel + `Force to:` label pattern.
- Numeric fields (`context_max`) parse via `Number(v)`.
- Boolean fields (`thinking`, `claude_in_chrome`) parse via `v === 'true'`.
- `handleSave` validates (name required + unique), then `addPreset`/`updatePreset`, then returns to list.
- The submit button's `form` attribute is set via the `watch(view)` block (workaround for `wa-button`).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/ClaudePresetsDialog.vue
git commit -m "feat(presets): form view with default+force selects and validation"
```

---

## Task 10: Frontend — settings popover entry point

**Files:**
- Modify: `frontend/src/components/SettingsPopover.vue` (Claude section ~lines 742–852)

- [ ] **Step 1: Add the import + state in `<script setup>`**

In the imports block, add:

```js
import ClaudePresetsDialog from './ClaudePresetsDialog.vue'
```

In the setup body (near other dialog state if any, otherwise at the bottom of the refs):

```js
const claudePresetsDialogOpen = ref(false)
function openClaudePresetsDialog() {
    claudePresetsDialogOpen.value = true
}
```

- [ ] **Step 2: Add the "Manage presets…" button**

In the Claude section (`<section v-if="activeSection === 'claude'">`), after the last `setting-group` block (the "Default Chrome MCP" one) and before the closing `</section>`, insert:

```html
<div class="setting-group">
    <label class="setting-group-label">Presets</label>
    <wa-button size="small" @click="openClaudePresetsDialog">
        <wa-icon slot="start" name="sliders"></wa-icon>
        Manage presets…
    </wa-button>
</div>
```

- [ ] **Step 3: Mount the dialog at the template root**

The dialog must live **outside** the `<wa-popover>` so closing the popover doesn't unmount it. Find the top-level template root of `SettingsPopover.vue` (usually a wrapping `<div>` or fragment that contains `<wa-popover>`), and append:

```html
<ClaudePresetsDialog v-model:open="claudePresetsDialogOpen" />
```

If the template is currently a single `<wa-popover>` element with no wrapper, wrap it in a `<template>`-style fragment by adding a `<template>` root or, equivalently, use a parent `<div>`.

> Cross-reference: if other dialogs are already mounted from `SettingsPopover.vue`, follow the existing convention. If not, the wrapper-or-append decision is a one-time judgment call.

- [ ] **Step 4: Read back the change**

Verify in the file:
- The `<div class="setting-group">` block sits inside the Claude section, after the last existing setting-group.
- `<ClaudePresetsDialog>` is mounted at the template root, not inside the popover.
- The import and state additions in `<script setup>` are present.

- [ ] **Step 5: Manual smoke test (with the user)**

Ask the user to (frontend HMR auto-reloads; backend should already have been restarted after Task 3):

1. Open the app, then the settings popover (gear icon).
2. Switch to the Claude section.
3. Confirm the new "Presets" group appears at the bottom with a "Manage presets…" button.
4. Click the button. The dialog opens. The list view shows "No presets yet." (empty state).
5. Click "Add preset". Form appears, name input has focus.
6. Save without entering a name → error callout "Name is required".
7. Enter `Quick fix`, set Model = `opus` (force to), leave the rest as Default. Save. The list view shows one row with the summary `model: opus-4-7` (or equivalent label).
8. Click the duplicate icon on that row. A new row `Quick fix (copy)` appears.
9. Click the up/down arrows. Rows reorder.
10. Click the edit (pencil) icon. Form pre-fills with the preset's values. Cancel returns to list without changes.
11. Click delete (trash). Row disappears.
12. Verify (devtools → Application → file system or `<data_dir>/claude-settings-presets.json`) that the file content matches the UI state after each mutation.
13. (Optional, multi-device sync) open the app in a second browser tab; mutations in tab A should reflect in tab B's dialog if it's open.

If anything is off, fix and re-iterate before committing.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SettingsPopover.vue
git commit -m "feat(presets): Manage presets entry point in Claude settings"
```

---

## Task 11: End-of-feature pass — verify, document gaps, hand off

- [ ] **Step 1: Re-read the spec**

Open `docs/superpowers/specs/2026-04-25-claude-settings-presets-design.md` and check that every "Files touched" entry is implemented and every behavior in the spec is covered. Tick mentally: bootstrap include, WS sync, list view actions, form validation, sentinel pattern, summary formatter, dialog placement.

- [ ] **Step 2: Self-review against the project conventions**

- All UI strings in English ✓
- No new `console.log`s
- No tests added (intentional)
- No new circular import patterns introduced (lazy import in store, lazy import in WS handler case)
- No backward-compat shims, no defensive code beyond what the sibling stores do

- [ ] **Step 3: Hand off**

Tell the user:
- Backend changes (Tasks 2 + 3) require a backend restart for the new `/api/bootstrap/` key and the new WS handler. **They restart the server, not Claude.**
- Applying a preset to a session is intentionally out of scope for this work — it will be a separate spec/plan.
- If they want the `<data_dir>/claude-settings-presets.json` file format to be inspectable from the CLI, no extra command is required: it's a plain JSON file in the same directory as `terminal-config.json`.

- [ ] **Step 4: Optional final commit (if any cleanup was done)**

If you made small cleanups during the self-review pass (e.g., removed an unused import), commit them:

```bash
git add -- <specific files>
git commit -m "chore(presets): cleanup after end-of-feature review"
```

Otherwise, no additional commit is needed.

---

## Dependencies & ordering notes

- **Tasks 1–3 (backend) must precede Task 6 (bootstrap dispatch)** because the frontend will throw on a missing `bootstrapData.claude_settings_presets` only if the backend hasn't been restarted with Task 2 in place. The store's `applyConfig` defends against `undefined` so the order is forgiving, but conceptually the chain is backend → bootstrap → store.
- **Tasks 7–9 are sequential** within the dialog component (skeleton → list → form). Task 10 (mount in popover) depends on the dialog being functional enough to be usable.
- **The two "manual smoke test" steps** (Task 8 step 5, Task 10 step 5) require dev servers to be running. The user — not Claude — manages those servers.
