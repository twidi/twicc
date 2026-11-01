# Custom Combos & Snippets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add user-defined custom key combos/sequences and text snippets to the terminal extra keys bar, with persistent storage and WebSocket sync.

**Architecture:** Backend stores config in `~/.twicc/terminal-config.json`, read/written via WebSocket messages (same pattern as `synced_settings`). Frontend Pinia store holds state, ExtraKeysBar gets two new dynamic tabs (Custom + Snippets), two new `wa-dialog` components handle CRUD operations.

**Tech Stack:** Django ASGI (WebSocket), orjson, Vue 3 (Composition API), Pinia, Web Awesome 3 (wa-dialog, wa-select, wa-callout, wa-icon, wa-button)

**Spec:** `docs/superpowers/specs/2026-03-27-custom-combos-and-snippets.md`

**Note:** This project does not use tests or linting. Steps focus on implementation and manual verification. After backend changes, remind user to restart backend via `devctl.py`.

---

## File Structure

### Files to Create

| File | Responsibility |
|------|---------------|
| `src/twicc/terminal_config.py` | Read/write `terminal-config.json` (orjson, atomic write) |
| `frontend/src/stores/terminalConfig.js` | Pinia store: combos/snippets state, getters, mutation actions |
| `frontend/src/utils/comboNotation.js` | Pure utility: auto-generate display notation from combo steps |
| `frontend/src/components/ManageCombosDialog.vue` | wa-dialog: list + form views for combo CRUD |
| `frontend/src/components/ManageSnippetsDialog.vue` | wa-dialog: list + form views for snippet CRUD (grouped by scope) |

### Files to Modify

| File | Changes |
|------|---------|
| `src/twicc/paths.py` (~line 92) | Add `get_terminal_config_path()` |
| `src/twicc/asgi.py` (~lines 513, 578, 989) | Add WS connect burst + message handler + broadcast |
| `frontend/src/composables/useWebSocket.js` (~lines 247, 659) | Add `sendTerminalConfig()` + handle `terminal_config_updated` |
| `frontend/src/composables/useTerminal.js` (~lines 605, 717) | Add `handleComboPress()`, `handleSnippetPress()`, expose them |
| `frontend/src/components/ExtraKeysBar.vue` | Add Custom/Snippets dynamic tabs, new props/emits, Manage buttons |
| `frontend/src/components/TerminalPanel.vue` | Update visibility logic, pass new props, wire emits/dialogs |

---

## Task 1: Backend — Path + Read/Write

**Files:**
- Modify: `src/twicc/paths.py`
- Create: `src/twicc/terminal_config.py`

- [ ] **Step 1: Add path function**

In `src/twicc/paths.py`, add after `get_search_dir()` (around line 92):

```python
def get_terminal_config_path() -> Path:
    return get_data_dir() / "terminal-config.json"
```

- [ ] **Step 2: Create terminal_config.py**

Create `src/twicc/terminal_config.py`. Follow the exact same pattern as `src/twicc/synced_settings.py`:

```python
"""Read/write terminal configuration (custom combos and snippets).

File: <data_dir>/terminal-config.json
"""

import os
import tempfile

import orjson

from twicc.paths import get_terminal_config_path


def read_terminal_config() -> dict:
    """Read terminal-config.json. Returns empty config if file doesn't exist or is invalid."""
    path = get_terminal_config_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {"combos": [], "snippets": {}}


def write_terminal_config(config: dict) -> None:
    """Write terminal-config.json atomically.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_terminal_config_path()
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

- [ ] **Step 3: Commit**

```
git add src/twicc/paths.py src/twicc/terminal_config.py
git commit -m "feat: add terminal config read/write backend"
```

---

## Task 2: Backend — WebSocket Handlers

**Files:**
- Modify: `src/twicc/asgi.py`

Reference the existing `synced_settings` pattern at lines 513–515 (connect burst), 578–579 (dispatch), 989–1008 (handler).

- [ ] **Step 1: Add import**

At the top of `asgi.py`, add the import alongside the existing `synced_settings` import:

```python
from twicc.terminal_config import read_terminal_config, write_terminal_config
```

- [ ] **Step 2: Add connect burst**

In the `websocket_connect` method, right after the `synced_settings_updated` block (around line 515), add:

```python
if self._should_send("terminal_config_updated"):
    terminal_config = await sync_to_async(read_terminal_config)()
    await self.send_json({"type": "terminal_config_updated", "config": terminal_config})
```

- [ ] **Step 3: Add message dispatch**

In the `receive_json` method's message type dispatch (around line 579), add a new `elif`:

```python
elif msg_type == "update_terminal_config":
    await self._handle_update_terminal_config(content)
```

- [ ] **Step 4: Add handler method**

Add the handler method, following the same pattern as `_handle_update_synced_settings` (lines 989–1008):

```python
async def _handle_update_terminal_config(self, content):
    """Handle terminal config update from client."""
    config = content.get("config")
    if not isinstance(config, dict):
        logger.warning("Invalid terminal config update: config is not a dict")
        return

    await sync_to_async(write_terminal_config)(config)

    # Broadcast to all connected clients
    await self.channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "terminal_config_updated",
                "config": config,
            },
        },
    )
```

- [ ] **Step 5: Commit**

```
git add src/twicc/asgi.py
git commit -m "feat: add WebSocket handlers for terminal config sync"
```

**⚠️ Remind user to restart backend (`uv run ./devctl.py restart back`).**

---

## Task 3: Frontend — Combo Notation Utility

**Files:**
- Create: `frontend/src/utils/comboNotation.js`

This is a pure utility with no dependencies — build it first so other components can import it.

- [ ] **Step 1: Create comboNotation.js**

```javascript
/**
 * Auto-generate display notation for combo steps.
 *
 * Notation rules:
 * - Modifiers uppercase, then key, separated by spaces: "CTRL C", "CTRL ALT D"
 * - Multi-step: joined with " · " (middle dot): "CTRL B · C"
 * - No "+" separator (avoids ambiguity with the "+" key)
 */

/** Map key identifiers to short display labels (matching built-in tab labels) */
const KEY_DISPLAY_MAP = {
    Escape: 'ESC',
    Tab: 'TAB',
    ArrowLeft: '←',
    ArrowUp: '↑',
    ArrowDown: '↓',
    ArrowRight: '→',
    Home: 'HOME',
    End: 'END',
    PageUp: 'PGUP',
    PageDown: 'PGDN',
    Delete: 'DEL',
    Insert: 'INS',
}

/**
 * Get display text for a single key identifier.
 * @param {string} key - Key identifier (e.g. "c", "Escape", "F5")
 * @returns {string} Display text (e.g. "C", "ESC", "F5")
 */
function displayKey(key) {
    if (KEY_DISPLAY_MAP[key]) return KEY_DISPLAY_MAP[key]
    // F-keys: already uppercase-ish (F1, F12)
    if (/^F\d{1,2}$/.test(key)) return key
    // Single character: uppercase
    if (key.length === 1) return key.toUpperCase()
    // Fallback: uppercase
    return key.toUpperCase()
}

/**
 * Render a single step as display text.
 * @param {{ modifiers?: string[], key: string }} step
 * @returns {string} e.g. "CTRL C", "ALT .", "ESC"
 */
export function formatStep(step) {
    const parts = []
    if (step.modifiers) {
        for (const mod of step.modifiers) {
            parts.push(mod.toUpperCase())
        }
    }
    parts.push(displayKey(step.key))
    return parts.join(' ')
}

/**
 * Render a full combo (all steps) as display text.
 * If the combo has a label, returns the label instead.
 * @param {{ label?: string, steps: Array<{ modifiers?: string[], key: string }> }} combo
 * @returns {string} e.g. "CTRL C", "CTRL B · C", "tmux:new"
 */
export function formatCombo(combo) {
    if (combo.label) return combo.label
    return combo.steps.map(formatStep).join(' · ')
}

/**
 * Render the notation-only version (ignoring label). Used in Manage dialog
 * to show notation alongside label.
 * @param {{ steps: Array<{ modifiers?: string[], key: string }> }} combo
 * @returns {string}
 */
export function formatComboNotation(combo) {
    return combo.steps.map(formatStep).join(' · ')
}
```

- [ ] **Step 2: Commit**

```
git add frontend/src/utils/comboNotation.js
git commit -m "feat: add combo notation formatting utility"
```

---

## Task 4: Frontend — Terminal Config Store

**Files:**
- Create: `frontend/src/stores/terminalConfig.js`

- [ ] **Step 1: Create the store**

```javascript
import { defineStore } from 'pinia'

/**
 * Store for terminal custom combos and snippets.
 * Data is persisted in ~/.twicc/terminal-config.json via WebSocket sync.
 */
export const useTerminalConfigStore = defineStore('terminalConfig', {
    state: () => ({
        combos: [],
        snippets: {}, // { global: [], "project:<id>": [] }
        _initialized: false,
    }),

    getters: {
        /**
         * Get snippets for display on the bar: global + current project, merged.
         * @returns {Function} (projectId: string) => Array
         */
        getSnippetsForProject: (state) => (projectId) => {
            const global = state.snippets.global || []
            const projectKey = `project:${projectId}`
            const project = state.snippets[projectKey] || []
            return [...global, ...project]
        },

        /**
         * Check if there are any snippets for a given project (global or project-specific).
         * Used for visibility logic on desktop.
         * @returns {Function} (projectId: string) => boolean
         */
        hasSnippetsForProject: (state) => (projectId) => {
            const global = state.snippets.global || []
            const projectKey = `project:${projectId}`
            const project = state.snippets[projectKey] || []
            return global.length > 0 || project.length > 0
        },

        /**
         * Get all snippet scopes that have entries, for the Manage dialog.
         * Returns array of { scope, snippets } sorted: global first, then projects alphabetically.
         * @returns {Array<{ scope: string, snippets: Array }>}
         */
        allSnippetScopes: (state) => {
            const result = []
            // Global always first
            result.push({ scope: 'global', snippets: state.snippets.global || [] })
            // Project scopes
            const projectScopes = Object.keys(state.snippets)
                .filter(k => k.startsWith('project:'))
                .sort()
            for (const scope of projectScopes) {
                result.push({ scope, snippets: state.snippets[scope] })
            }
            return result
        },
    },

    actions: {
        /**
         * Apply config received from WebSocket (on connect or broadcast).
         */
        applyConfig(config) {
            this.combos = config.combos || []
            this.snippets = config.snippets || {}
            this._initialized = true
        },

        /**
         * Send the full config to the backend via WebSocket.
         * Uses lazy import to avoid circular dependency with useWebSocket.
         */
        async _sendConfig() {
            const { sendTerminalConfig } = await import('../composables/useWebSocket')
            sendTerminalConfig({
                combos: this.combos,
                snippets: this.snippets,
            })
        },

        // ── Combo mutations ──────────────────────────────────

        addCombo(combo) {
            this.combos.push(combo)
            this._sendConfig()
        },

        updateCombo(index, combo) {
            this.combos[index] = combo
            this._sendConfig()
        },

        deleteCombo(index) {
            this.combos.splice(index, 1)
            this._sendConfig()
        },

        reorderCombo(fromIndex, toIndex) {
            if (toIndex < 0 || toIndex >= this.combos.length) return
            const [item] = this.combos.splice(fromIndex, 1)
            this.combos.splice(toIndex, 0, item)
            this._sendConfig()
        },

        // ── Snippet mutations ────────────────────────────────

        addSnippet(scope, snippet) {
            if (!this.snippets[scope]) {
                this.snippets[scope] = []
            }
            this.snippets[scope].push(snippet)
            this._sendConfig()
        },

        updateSnippet(scope, index, snippet, newScope = null) {
            if (newScope && newScope !== scope) {
                // Move to different scope
                this.snippets[scope].splice(index, 1)
                // Clean up empty scope arrays
                if (this.snippets[scope].length === 0 && scope !== 'global') {
                    delete this.snippets[scope]
                }
                if (!this.snippets[newScope]) {
                    this.snippets[newScope] = []
                }
                this.snippets[newScope].push(snippet)
            } else {
                this.snippets[scope][index] = snippet
            }
            this._sendConfig()
        },

        deleteSnippet(scope, index) {
            this.snippets[scope].splice(index, 1)
            // Clean up empty scope arrays (but keep "global" even if empty)
            if (this.snippets[scope].length === 0 && scope !== 'global') {
                delete this.snippets[scope]
            }
            this._sendConfig()
        },

        reorderSnippet(scope, fromIndex, toIndex) {
            const arr = this.snippets[scope]
            if (!arr || toIndex < 0 || toIndex >= arr.length) return
            const [item] = arr.splice(fromIndex, 1)
            arr.splice(toIndex, 0, item)
            this._sendConfig()
        },
    },
})
```

- [ ] **Step 2: Commit**

```
git add frontend/src/stores/terminalConfig.js
git commit -m "feat: add Pinia store for terminal config (combos & snippets)"
```

---

## Task 5: Frontend — WebSocket Integration

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js`

- [ ] **Step 1: Add send function**

Near the existing `sendSyncedSettings` function (around line 247), add:

```javascript
export function sendTerminalConfig(config) {
    return sendWsMessage({ type: 'update_terminal_config', config })
}
```

- [ ] **Step 2: Add message handler**

In the `handleMessage` function's `switch (msg.type)` block, near the existing `synced_settings_updated` case (around line 659), add:

```javascript
case 'terminal_config_updated':
    import('../stores/terminalConfig').then(({ useTerminalConfigStore }) => {
        useTerminalConfigStore().applyConfig(msg.config)
    })
    break
```

Uses lazy `import()` to avoid circular dependency (same pattern as `synced_settings_updated`).

- [ ] **Step 3: Commit**

```
git add frontend/src/composables/useWebSocket.js
git commit -m "feat: wire terminal config sync to WebSocket"
```

---

## Task 6: Frontend — useTerminal Handlers

**Files:**
- Modify: `frontend/src/composables/useTerminal.js`

- [ ] **Step 1: Add handleComboPress**

After `handleExtraKeyPaste` (around line 647), add:

```javascript
/**
 * Handle a custom combo button press. Sends each step's ANSI sequence
 * in order. Combos are self-contained — they don't interact with
 * the one-shot modifier state from the Essentials tab.
 */
function handleComboPress(combo) {
    for (const step of combo.steps) {
        const syntheticEvent = {
            key: step.key,
            ctrlKey: step.modifiers?.includes('ctrl') ?? false,
            altKey: step.modifiers?.includes('alt') ?? false,
            shiftKey: step.modifiers?.includes('shift') ?? false,
        }
        const sequence = imeKeyToAnsiSequence(syntheticEvent, { ignoreShift: false })
        if (sequence) {
            wsSend({ type: 'input', data: sequence })
        } else {
            const char = step.key
            if (syntheticEvent.altKey) {
                wsSend({ type: 'input', data: `\x1b${char}` })
            } else {
                wsSend({ type: 'input', data: char })
            }
        }
    }
    terminal?.focus()
}
```

- [ ] **Step 2: Add handleSnippetPress**

Right after `handleComboPress`:

```javascript
/**
 * Handle a snippet button press. Sends the text as raw terminal input,
 * optionally appending a newline.
 */
function handleSnippetPress(snippet) {
    let text = snippet.text
    if (snippet.appendEnter) {
        text += '\n'
    }
    wsSend({ type: 'input', data: text })
    terminal?.focus()
}
```

- [ ] **Step 3: Expose new functions**

In the `return` statement (around line 717), add the new functions:

```javascript
return {
    containerRef, isConnected, started, start, reconnect,
    // Extra keys bar
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
    handleComboPress, handleSnippetPress,
}
```

- [ ] **Step 4: Commit**

```
git add frontend/src/composables/useTerminal.js
git commit -m "feat: add combo and snippet press handlers to useTerminal"
```

---

## Task 7: Frontend — ExtraKeysBar Dynamic Tabs

**Files:**
- Modify: `frontend/src/components/ExtraKeysBar.vue`

This is the largest change. The component gains new props, emits, and two new dynamic tab sections.

- [ ] **Step 1: Update props and emits**

Replace the existing `defineProps` and `defineEmits` with:

```javascript
import { ref, computed } from 'vue'
import { formatCombo } from '../utils/comboNotation'

const props = defineProps({
    activeModifiers: { type: Object, required: true },
    lockedModifiers: { type: Object, required: true },
    theme: { type: String, default: 'dark' },
    isTouchDevice: { type: Boolean, default: false },
    combos: { type: Array, default: () => [] },
    snippets: { type: Array, default: () => [] },
})

const emit = defineEmits([
    'key-input', 'modifier-toggle', 'paste',
    'combo-press', 'snippet-press',
    'manage-combos', 'manage-snippets',
])
```

- [ ] **Step 2: Add dynamic tab computation**

Replace the static `const tabIds = Object.keys(TABS)` with:

Rename the existing `TABS` constant to `BUILT_IN_TABS` (keeping the same content — `essentials`, `more`, `fkeys` objects with their `label` and `keys` arrays). Remove the `label` field from each since we'll use `TAB_LABELS` instead:

```javascript
const BUILT_IN_TABS = {
    essentials: {
        keys: [
            { label: 'ESC', key: 'Escape' },
            { label: 'TAB', key: 'Tab' },
            { label: 'CTRL', key: 'ctrl', modifier: true },
            { label: 'ALT', key: 'alt', modifier: true },
            { label: '←', key: 'ArrowLeft', arrow: true },
            { label: '↑', key: 'ArrowUp', arrow: true },
            { label: '↓', key: 'ArrowDown', arrow: true },
            { label: '→', key: 'ArrowRight', arrow: true },
            { label: '-', key: '-' },
            { label: '/', key: '/' },
            { label: '|', key: '|' },
            { label: '~', key: '~' },
        ],
    },
    more: {
        keys: [
            { label: 'SHIFT', key: 'shift', modifier: true },
            { label: 'HOME', key: 'Home', wide: true },
            { label: 'END', key: 'End', wide: true },
            { label: 'PGUP', key: 'PageUp', wide: true },
            { label: 'PGDN', key: 'PageDown', wide: true },
            { label: 'DEL', key: 'Delete' },
            { label: 'INS', key: 'Insert' },
            { label: '\\', key: '\\' },
            { label: '_', key: '_' },
            { label: '*', key: '*' },
            { label: '&', key: '&' },
            { label: '.', key: '.' },
            { label: 'PASTE', key: 'paste', paste: true, wide: true },
        ],
    },
    fkeys: {
        keys: Array.from({ length: 12 }, (_, i) => ({
            label: `F${i + 1}`,
            key: `F${i + 1}`,
        })),
    },
}

// Remove old: const tabIds = Object.keys(TABS)

const tabIds = computed(() => {
    if (props.isTouchDevice) {
        return ['essentials', 'more', 'fkeys', 'custom', 'snippets']
    }
    return ['snippets']
})

const TAB_LABELS = {
    essentials: 'Essentials',
    more: 'More',
    fkeys: 'F-keys',
    custom: 'Custom',
    snippets: 'Snippets',
}

// Default to first available tab
const activeTab = ref(null)
// Initialize activeTab when tabIds changes
watch(tabIds, (ids) => {
    if (!ids.includes(activeTab.value)) {
        activeTab.value = ids[0]
    }
}, { immediate: true })
```

Add `import { ref, computed, watch } from 'vue'`.

- [ ] **Step 3: Update template — tab bar**

Replace the tab bar section to conditionally hide when single tab:

```html
<div class="extra-keys-bar" :class="theme">
    <!-- Tab bar: hidden when only 1 tab (desktop snippets-only) -->
    <div v-if="tabIds.length > 1" class="extra-keys-tabs">
        <button
            v-for="id in tabIds"
            :key="id"
            class="extra-keys-tab"
            :class="{ active: activeTab === id }"
            @pointerdown.prevent="activeTab = id"
        >
            {{ TAB_LABELS[id] }}
        </button>
    </div>

    <div class="extra-keys-keys">
        <!-- Built-in tabs (essentials, more, fkeys) -->
        <template v-if="BUILT_IN_TABS[activeTab]">
            <template v-for="keyDef in BUILT_IN_TABS[activeTab].keys" :key="keyDef.key">
                <!-- Paste button -->
                <button
                    v-if="keyDef.paste"
                    :class="keyClasses(keyDef)"
                    @click="handlePasteClick"
                >
                    {{ keyDef.label }}
                </button>
                <!-- Modifier button -->
                <button
                    v-else-if="keyDef.modifier"
                    :class="keyClasses(keyDef)"
                    @pointerdown="handleModifierPointerDown($event, keyDef)"
                >
                    {{ keyDef.label }}
                </button>
                <!-- Regular key button -->
                <button
                    v-else
                    :class="keyClasses(keyDef)"
                    @pointerdown="handleKeyPointerDown($event, keyDef)"
                >
                    {{ keyDef.label }}
                </button>
            </template>
        </template>

        <!-- Custom tab -->
        <template v-else-if="activeTab === 'custom'">
            <template v-if="combos.length > 0">
                <button
                    v-for="(combo, i) in combos"
                    :key="'combo-' + i"
                    class="extra-key"
                    @pointerdown="handleComboPointerDown($event, combo)"
                >
                    {{ formatCombo(combo) }}
                </button>
            </template>
            <span v-else class="empty-tab-text">No custom combos</span>
            <button
                class="extra-key manage-key"
                @pointerdown.prevent="emit('manage-combos')"
            >
                ⚙ Manage
            </button>
        </template>

        <!-- Snippets tab -->
        <template v-else-if="activeTab === 'snippets'">
            <template v-if="snippets.length > 0">
                <button
                    v-for="(snippet, i) in snippets"
                    :key="'snippet-' + i"
                    class="extra-key"
                    @pointerdown="handleSnippetPointerDown($event, snippet)"
                >
                    {{ snippet.label }}
                </button>
            </template>
            <span v-else class="empty-tab-text">No snippets</span>
            <button
                class="extra-key manage-key"
                @pointerdown.prevent="emit('manage-snippets')"
            >
                ⚙ Manage
            </button>
        </template>
    </div>
</div>
```

- [ ] **Step 4: Add combo/snippet handlers**

```javascript
function handleComboPointerDown(event, combo) {
    event.preventDefault()
    emit('combo-press', combo)
}

function handleSnippetPointerDown(event, snippet) {
    event.preventDefault()
    emit('snippet-press', snippet)
}
```

- [ ] **Step 5: Add CSS for new elements**

```css
.empty-tab-text {
    font-size: 12px;
    opacity: 0.4;
    padding: 4px 8px;
    align-self: center;
}

.extra-key.manage-key {
    border-style: dashed;
    opacity: 0.6;
    font-size: 11px;
}

.extra-key.manage-key:hover {
    opacity: 0.9;
}

/* Light theme variants */
.light .empty-tab-text {
    opacity: 0.5;
}
```

- [ ] **Step 6: Verify manually**

Open the app in a browser. The bar should still render correctly for the 3 built-in tabs. On desktop, it should now show a snippets-only bar with "No snippets" + Manage button.

- [ ] **Step 7: Commit**

```
git add frontend/src/components/ExtraKeysBar.vue
git commit -m "feat: add Custom and Snippets dynamic tabs to ExtraKeysBar"
```

---

## Task 8: Frontend — TerminalPanel Integration

**Files:**
- Modify: `frontend/src/components/TerminalPanel.vue`

- [ ] **Step 1: Add imports and store usage**

Add to the `<script setup>`:

```javascript
import { computed, ref } from 'vue'
import { useDataStore } from '../stores/data'
import { useTerminalConfigStore } from '../stores/terminalConfig'
import ManageCombosDialog from './ManageCombosDialog.vue'
import ManageSnippetsDialog from './ManageSnippetsDialog.vue'

const dataStore = useDataStore()
const terminalConfigStore = useTerminalConfigStore()
```

- [ ] **Step 2: Add computed properties**

```javascript
// Resolve projectId from sessionId
const session = computed(() => props.sessionId ? dataStore.getSession(props.sessionId) : null)
const projectId = computed(() => session.value?.project_id)

// Snippets for the current project (global + project-specific, merged)
const snippetsForProject = computed(() =>
    projectId.value ? terminalConfigStore.getSnippetsForProject(projectId.value) : []
)

// Dialog refs
const manageCombosDialogRef = ref(null)
const manageSnippetsDialogRef = ref(null)
```

- [ ] **Step 3: Update visibility and ExtraKeysBar**

Remove the `v-if="settingsStore.isTouchDevice"` from ExtraKeysBar. The bar is now always visible: on touch devices it shows all 5 tabs, on desktop it shows the snippets-only mode (even if empty, for the Manage button). The `ExtraKeysBar` component itself handles the tab selection logic via the `isTouchDevice` prop.

```html
<ExtraKeysBar
    :active-modifiers="activeModifiers"
    :locked-modifiers="lockedModifiers"
    :theme="settingsStore.getEffectiveTheme"
    :is-touch-device="settingsStore.isTouchDevice"
    :combos="terminalConfigStore.combos"
    :snippets="snippetsForProject"
    @key-input="handleExtraKeyInput"
    @modifier-toggle="handleExtraKeyModifierToggle"
    @paste="handleExtraKeyPaste"
    @combo-press="handleComboPress"
    @snippet-press="handleSnippetPress"
    @manage-combos="manageCombosDialogRef?.open()"
    @manage-snippets="manageSnippetsDialogRef?.open()"
/>
```

- [ ] **Step 4: Add dialog components to template**

After the disconnect overlay, add:

```html
<ManageCombosDialog ref="manageCombosDialogRef" />
<ManageSnippetsDialog
    ref="manageSnippetsDialogRef"
    :current-project-id="projectId"
/>
```

- [ ] **Step 5: Wire new handlers from useTerminal**

Update the destructuring from `useTerminal` to include the new functions:

```javascript
const {
    containerRef, isConnected, started, start, reconnect,
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
    handleComboPress, handleSnippetPress,
} = useTerminal(props.sessionId)
```

- [ ] **Step 6: Commit**

```
git add frontend/src/components/TerminalPanel.vue
git commit -m "feat: integrate combos/snippets into TerminalPanel"
```

---

## Task 9: Frontend — ManageCombosDialog

**Files:**
- Create: `frontend/src/components/ManageCombosDialog.vue`

This is a complex component. Build it in parts.

- [ ] **Step 1: Create dialog skeleton with list view**

Create `frontend/src/components/ManageCombosDialog.vue`. Follow the `ProjectEditDialog.vue` pattern:
- `wa-dialog` with `ref="dialogRef"`
- `defineExpose({ open, close })` using `open()` that sets `dialogRef.value.open = true`
- Header slot: "Custom combos"
- Body: list of combos when `view === 'list'`, form when `view === 'form'`
- Footer slot: "+ Add combo" button (list view) or "Cancel / Save" (form view)

List view rows contain:
- ▲/▼ reorder arrows (dimmed at boundaries) calling `terminalConfigStore.reorderCombo(index, index ± 1)`
- Display text via `formatCombo(combo)`, and for labeled combos also show `formatComboNotation(combo)` in dimmed text
- ✏️ edit button → switches to form view, pre-fills `formData`
- 📋 duplicate button → switches to form view, pre-fills `formData`, sets `isDuplicate = true`
- 🗑️ delete button → calls `terminalConfigStore.deleteCombo(index)`

- [ ] **Step 2: Add combo form view**

The form replaces the list within the same dialog body.

Fields:
- **Label input** (optional): `wa-input` or plain `<input>`, placeholder `e.g. "tmux:new"`
- **Steps**: each step rendered in a bordered card with:
  - Step N header + ✕ remove button (only shown when 2+ steps)
  - Modifier toggles: 3 buttons (CTRL/ALT/SHIFT), clicking toggles a flag in `formData.steps[i].modifiers`
  - Key input: `<input>` field (single char / key name) + key picker grid
  - ↓ arrow separator between steps (visual only)
- **"+ Add step"** button: pushes a new `{ key: '' }` to `formData.steps`

The key picker grid contains all keys from the built-in tabs (ESC, TAB, arrows, HOME, END, PGUP, PGDN, DEL, INS, special chars, F1-F12). Clicking a picker key sets `formData.steps[i].key`.

Save validation:
- Every step must have a non-empty `key`
- At least one step required
- **Duplicate detection**: if an identical combo already exists (same steps, same modifiers), show a `wa-callout variant="warning"` warning but still allow save (user may intentionally want duplicates with different labels)

On Save: call `terminalConfigStore.addCombo(formData)` or `terminalConfigStore.updateCombo(editIndex, formData)`, then switch back to list view.

- [ ] **Step 3: Verify manually**

Open the terminal tab, click the Custom tab, click ⚙ Manage:
- Dialog opens with empty list
- Click "+ Add combo" → form appears with 1 step
- Set CTRL + c → Save → appears in list
- Click "+ Add combo" again → add a 2-step sequence (CTRL B → c) with label "tmux:new"
- Verify reorder arrows work
- Verify edit, duplicate, delete work
- Verify the combos appear as buttons on the Custom tab and execute correctly

- [ ] **Step 4: Commit**

```
git add frontend/src/components/ManageCombosDialog.vue
git commit -m "feat: add ManageCombosDialog with list and form views"
```

---

## Task 10: Frontend — ManageSnippetsDialog

**Files:**
- Create: `frontend/src/components/ManageSnippetsDialog.vue`

- [ ] **Step 1: Create dialog with grouped list view**

Same `wa-dialog` pattern. Props: `currentProjectId` (String).

List view groups snippets by scope using `terminalConfigStore.allSnippetScopes`:
- **"All projects"** group header — always shown
- **Per-project groups** — use `ProjectBadge` component for the header (colored dot + name). Import it.
- Thin separator between groups

Each snippet row:
- ▲/▼ reorder arrows (within scope group only)
- Label in a badge-style chip
- Text preview (truncated, monospace, dimmed) + `↵` if `appendEnter`
- ✏️ edit → form view, pre-fills
- 📋 duplicate → form view, pre-fills, `isDuplicate = true` (scope defaults to source scope)
- 🗑️ delete → `terminalConfigStore.deleteSnippet(scope, index)`

To get project list for scope headers, use `dataStore.getProjects` from the data store.

- [ ] **Step 2: Add snippet form view**

Fields:
- **Label** (required): text input
- **Text to send** (required): `<textarea>` with monospace font
- **Append Enter** (checkbox): default checked. Use `wa-checkbox` or plain `<input type="checkbox">`
- **Scope** (dropdown): `wa-select` + `wa-option`. First option "All projects" (value `"global"`), then each non-stale project from `dataStore.getProjects` (filter out projects where `p.stale === true`) with display name from `dataStore.getProjectDisplayName(p.id)`, sorted alphabetically. Default: `props.currentProjectId` for add, source scope for duplicate, current scope for edit.

Save: calls `terminalConfigStore.addSnippet(scope, snippet)` or `terminalConfigStore.updateSnippet(oldScope, index, snippet, newScope)`.

Validation:
- Label required (non-empty after trim)
- Text required (non-empty after trim)
- **Label uniqueness**: if a snippet with the same label already exists in the same scope, show a `wa-callout variant="warning"` warning but still allow save

- [ ] **Step 3: Verify manually**

Open Snippets tab → ⚙ Manage:
- Empty state with "All projects" header
- Add a global snippet (label: "deploy", text: "docker compose up -d", append enter: yes)
- Add a project snippet (scope: current project)
- Verify they appear grouped correctly
- Verify reorder, edit, duplicate (check scope default), delete
- Verify snippets appear on the bar and execute correctly

- [ ] **Step 4: Commit**

```
git add frontend/src/components/ManageSnippetsDialog.vue
git commit -m "feat: add ManageSnippetsDialog with grouped list and form views"
```

---

## Task 11: Final Verification & Cleanup

- [ ] **Step 1: End-to-end verification**

Test the full flow:
1. Desktop: snippets bar visible, add a snippet via Manage, verify it appears and executes
2. Emulate mobile (browser devtools → toggle device toolbar): all 5 tabs visible, test Custom combos
3. Add a multi-step sequence (e.g. tmux prefix), verify it sends both steps
4. Open a second browser tab → verify WebSocket sync (changes appear on both tabs)
5. Verify `~/.twicc/terminal-config.json` is written correctly

- [ ] **Step 2: Add Web Awesome imports if needed**

Check `frontend/src/main.js` for the following imports. Add any that are missing (most are likely already imported from existing features):

```javascript
// These are likely already imported:
import '@awesome.me/webawesome/dist/components/dialog/dialog.js'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/select/select.js'
import '@awesome.me/webawesome/dist/components/option/option.js'

// This one might be new — check if wa-checkbox is used in the dialogs:
import '@awesome.me/webawesome/dist/components/checkbox/checkbox.js'
```

```
git add frontend/src/main.js
git commit -m "chore: add Web Awesome imports for manage dialogs"
```

- [ ] **Step 3: Final commit — update CHANGELOG**

The CHANGELOG entry was already added during the brainstorming phase. Verify it's still accurate, update if needed.
