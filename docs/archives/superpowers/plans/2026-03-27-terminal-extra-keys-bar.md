# Terminal Extra Keys Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mobile-friendly "extra keys bar" below the terminal with tabbed groups (Essentials/More/F-keys), one-shot + lock modifiers, and clipboard paste.

**Architecture:** A new `ExtraKeysBar.vue` component handles rendering and user interaction (tabs, double-tap detection). The existing `useTerminal.js` composable gains modifier state and input handlers that convert key identifiers to ANSI sequences via the existing `imeKeyToAnsiSequence` function. `TerminalPanel.vue` wires them together.

**Tech Stack:** Vue 3 Composition API, pure custom HTML/CSS (no Web Awesome), xterm.js

**No tests** per project convention (CLAUDE.md: "no tests and no linting").

**Spec:** `docs/superpowers/specs/2026-03-27-terminal-extra-keys-bar-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/src/composables/useTerminal.js` | **Modify** | Add `activeModifiers`, `lockedModifiers` reactive state; add `handleExtraKeyInput()`, `handleExtraKeyModifierToggle()`, `handleExtraKeyPaste()` functions; integrate modifier state with existing IME handler; expose new state and functions from composable return |
| `frontend/src/components/ExtraKeysBar.vue` | **Create** | Pure custom component: 3 tabs (Essentials/More/F-keys), key grid, double-tap detection for modifiers, `@pointerdown.prevent` focus management, dark/light theme support |
| `frontend/src/components/TerminalPanel.vue` | **Modify** | Import ExtraKeysBar, wire props/events between useTerminal and ExtraKeysBar |

---

## Task 1: Add modifier state and input handlers to `useTerminal.js`

**Files:**
- Modify: `frontend/src/composables/useTerminal.js`

### Design

Add reactive modifier state and three handler functions to the composable. The key insight: `imeKeyToAnsiSequence` already maps `event.key` + modifier flags to ANSI sequences — we can call it with a duck-typed synthetic object `{ key, ctrlKey, altKey, shiftKey }` instead of a real `KeyboardEvent`.

The existing custom key event handler (line 428-460) should also consult `activeModifiers` so that modifiers set via the bar work with physical/virtual keyboard input too.

### Steps

- [ ] **Step 1: Add modifier state and imports**

At the top of the `useTerminal` function (after line 205 `let intentionalClose = false`), add:

```js
// ── Extra keys bar state ────────────────────────────────────────────
const activeModifiers = reactive({ ctrl: false, alt: false, shift: false })
const lockedModifiers = reactive({ ctrl: false, alt: false, shift: false })
```

Add `reactive` to the Vue import on line 2:

```js
import { ref, reactive, watch, onMounted, onUnmounted } from 'vue'
```

- [ ] **Step 2: Add `handleExtraKeyModifierToggle` function**

Add after the `reconnect` function (after line 532):

```js
/**
 * Handle modifier toggle from the extra keys bar.
 * Called with the modifier name and whether it should be locked.
 *
 * Three transitions:
 * - Inactive → one-shot: active=true, locked=false
 * - Inactive → locked (double-tap): active=true, locked=true
 * - Active/locked → inactive: active=false, locked=false
 */
function handleExtraKeyModifierToggle(modifier, locked) {
    if (activeModifiers[modifier] && !locked) {
        // Was active (one-shot or locked) → deactivate
        activeModifiers[modifier] = false
        lockedModifiers[modifier] = false
    } else if (locked) {
        // Double-tap → lock
        activeModifiers[modifier] = true
        lockedModifiers[modifier] = true
    } else {
        // Single tap → one-shot
        activeModifiers[modifier] = true
        lockedModifiers[modifier] = false
    }
}
```

- [ ] **Step 3: Add `resetOneShotModifiers` helper**

Add right after `handleExtraKeyModifierToggle`:

```js
/**
 * Reset all one-shot (non-locked) modifiers after a key has been sent.
 */
function resetOneShotModifiers() {
    for (const mod of ['ctrl', 'alt', 'shift']) {
        if (activeModifiers[mod] && !lockedModifiers[mod]) {
            activeModifiers[mod] = false
        }
    }
}
```

- [ ] **Step 4: Add `handleExtraKeyInput` function**

Add right after `resetOneShotModifiers`:

```js
/**
 * Handle a key press from the extra keys bar.
 * Constructs a synthetic key descriptor with current modifier state,
 * converts to ANSI via imeKeyToAnsiSequence, and sends to the PTY.
 *
 * @param {string} key - Key identifier (e.g. 'Escape', 'Tab', 'ArrowUp', '/', 'F5')
 */
function handleExtraKeyInput(key) {
    const syntheticEvent = {
        key,
        ctrlKey: activeModifiers.ctrl,
        altKey: activeModifiers.alt,
        shiftKey: activeModifiers.shift,
    }

    // Try ANSI sequence conversion (handles special keys, Ctrl+letter, Alt+letter)
    const sequence = imeKeyToAnsiSequence(syntheticEvent, { ignoreShift: false })

    if (sequence) {
        wsSend({ type: 'input', data: sequence })
    } else {
        // Simple character — send directly (with Alt prefix if applicable)
        if (activeModifiers.alt) {
            wsSend({ type: 'input', data: `\x1b${key}` })
        } else {
            wsSend({ type: 'input', data: key })
        }
    }

    resetOneShotModifiers()
    terminal?.focus()
}
```

- [ ] **Step 5: Add `handleExtraKeyPaste` function**

Add right after `handleExtraKeyInput`:

```js
/**
 * Handle paste from the extra keys bar.
 * Reads clipboard and sends content to the PTY.
 * Must be called from a click event handler (not pointerdown)
 * to preserve the user gesture chain for the Clipboard API.
 */
async function handleExtraKeyPaste() {
    try {
        const text = await navigator.clipboard.readText()
        if (text) {
            wsSend({ type: 'input', data: text })
        }
    } catch {
        toast.error('Clipboard access denied')
    }
    terminal?.focus()
}
```

- [ ] **Step 6: Integrate modifier state with existing IME handler**

Modify the existing custom key event handler (inside `initTerminal`, the `terminal.attachCustomKeyEventHandler` block). The touch-device section (lines 440-447) currently reads modifier state from the event itself. We need it to also consider `activeModifiers` from the extra keys bar.

Replace lines 440-447:

```js
if (settingsStore.isTouchDevice) {
    const sequence = imeKeyToAnsiSequence(event, { ignoreShift: true })
    if (sequence) {
        event.preventDefault()
        wsSend({ type: 'input', data: sequence })
        return false
    }
}
```

With:

```js
if (settingsStore.isTouchDevice) {
    // Merge extra-keys-bar modifiers with the event's own modifiers
    const mergedEvent = (activeModifiers.ctrl || activeModifiers.alt || activeModifiers.shift)
        ? {
            key: event.key,
            ctrlKey: event.ctrlKey || activeModifiers.ctrl,
            altKey: event.altKey || activeModifiers.alt,
            shiftKey: event.shiftKey || activeModifiers.shift,
        }
        : event
    const sequence = imeKeyToAnsiSequence(mergedEvent, { ignoreShift: !activeModifiers.shift })
    if (sequence) {
        event.preventDefault()
        wsSend({ type: 'input', data: sequence })
        if (mergedEvent !== event) resetOneShotModifiers()
        return false
    }
    // If no sequence but extra modifiers were active and it's a single char,
    // the modifier was meant for this key
    if (mergedEvent !== event && event.key.length === 1) {
        event.preventDefault()
        if (activeModifiers.alt) {
            wsSend({ type: 'input', data: `\x1b${event.key}` })
        } else {
            wsSend({ type: 'input', data: event.key })
        }
        resetOneShotModifiers()
        return false
    }
}
```

- [ ] **Step 7: Update composable return value**

Replace line 602:

```js
return { containerRef, isConnected, started, start, reconnect }
```

With:

```js
return {
    containerRef, isConnected, started, start, reconnect,
    // Extra keys bar
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
}
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/composables/useTerminal.js
git commit -m "feat(terminal): add extra keys bar state and handlers to useTerminal"
```

---

## Task 2: Create `ExtraKeysBar.vue` component

**Files:**
- Create: `frontend/src/components/ExtraKeysBar.vue`

### Design

Pure custom component — no Web Awesome. The component defines the key layout as data, renders it dynamically, and handles interaction. All buttons use `@pointerdown.prevent` to prevent focus theft from the terminal, except PASTE which uses `@click` to preserve the clipboard API gesture chain.

Double-tap detection for modifiers: track the timestamp of the last `pointerdown` per modifier. If two `pointerdown` events on the same modifier occur within 350ms, it's a double-tap (→ lock).

### Steps

- [ ] **Step 1: Create the component file with script setup**

Create `frontend/src/components/ExtraKeysBar.vue`:

```vue
<script setup>
import { ref } from 'vue'

const props = defineProps({
    activeModifiers: {
        type: Object,
        required: true,
    },
    lockedModifiers: {
        type: Object,
        required: true,
    },
    theme: {
        type: String,
        default: 'dark',
    },
})

const emit = defineEmits(['key-input', 'modifier-toggle', 'paste'])

// ── Tab state ───────────────────────────────────────────────────────
const activeTab = ref('essentials')

// ── Key layout definition ───────────────────────────────────────────
// Each key has: label (display), key (emitted value), and optional flags
const TABS = {
    essentials: {
        label: 'Essentials',
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
        label: 'More',
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
        label: 'F-keys',
        keys: Array.from({ length: 12 }, (_, i) => ({
            label: `F${i + 1}`,
            key: `F${i + 1}`,
        })),
    },
}

const tabIds = Object.keys(TABS)

// ── Double-tap detection for modifiers ──────────────────────────────
const lastModifierTap = {}

function handleModifierPointerDown(event, keyDef) {
    event.preventDefault() // Prevent focus theft from terminal

    const now = Date.now()
    const prev = lastModifierTap[keyDef.key] || 0
    const isDoubleTap = (now - prev) < 350
    lastModifierTap[keyDef.key] = now

    const mod = keyDef.key
    const isActive = props.activeModifiers[mod]
    const isLocked = props.lockedModifiers[mod]

    if (isActive && isDoubleTap && !isLocked) {
        // Was one-shot, double-tap → lock
        emit('modifier-toggle', mod, true)
    } else if (isActive) {
        // Was active (one-shot or locked) → deactivate
        emit('modifier-toggle', mod, false)
    } else if (isDoubleTap) {
        // Was inactive, double-tap → lock directly
        emit('modifier-toggle', mod, true)
    } else {
        // Was inactive, single tap → one-shot
        emit('modifier-toggle', mod, false)
    }
}

// ── Regular key handling ────────────────────────────────────────────
function handleKeyPointerDown(event, keyDef) {
    event.preventDefault() // Prevent focus theft from terminal
    emit('key-input', keyDef.key)
}

// ── Paste handling (uses @click, not @pointerdown) ──────────────────
function handlePasteClick() {
    emit('paste')
}

// ── CSS classes for a key button ────────────────────────────────────
function keyClasses(keyDef) {
    return {
        'extra-key': true,
        'modifier': keyDef.modifier,
        'active-mod': keyDef.modifier && props.activeModifiers[keyDef.key],
        'locked': keyDef.modifier && props.lockedModifiers[keyDef.key],
        'arrow': keyDef.arrow,
        'wide': keyDef.wide,
        'paste-key': keyDef.paste,
    }
}
</script>

<template>
    <div class="extra-keys-bar" :class="theme">
        <div class="extra-keys-tabs">
            <button
                v-for="id in tabIds"
                :key="id"
                class="extra-keys-tab"
                :class="{ active: activeTab === id }"
                @pointerdown.prevent="activeTab = id"
            >
                {{ TABS[id].label }}
            </button>
        </div>
        <div class="extra-keys-keys">
            <template v-for="keyDef in TABS[activeTab].keys" :key="keyDef.key">
                <!-- Paste button: @pointerdown.prevent keeps focus on terminal,
                     @click preserves gesture chain for Clipboard API -->
                <button
                    v-if="keyDef.paste"
                    :class="keyClasses(keyDef)"
                    @pointerdown.prevent
                    @click="handlePasteClick"
                >
                    {{ keyDef.label }}
                </button>
                <!-- Modifier button: has special double-tap logic -->
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
        </div>
    </div>
</template>

<style scoped>
/* ── Bar container ─────────────────────────────────────────────────── */
.extra-keys-bar {
    background: #141e28;
    border-top: 1px solid #2a3a4a;
    padding: 4px 6px 6px;
    user-select: none;
    -webkit-user-select: none;
}

/* ── Tabs ───────────────────────────────────────────────────────────── */
.extra-keys-tabs {
    display: flex;
    gap: 2px;
    margin-bottom: 5px;
}

.extra-keys-tab {
    background: none;
    border: none;
    color: #5a6a7a;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 3px 8px;
    border-radius: 3px;
    cursor: pointer;
    font-family: inherit;
    transition: color 0.15s, background-color 0.15s;
    touch-action: manipulation;
}

.extra-keys-tab:hover {
    color: #8a9aaa;
}

.extra-keys-tab.active {
    color: #c0d0e0;
    background: rgba(255, 255, 255, 0.08);
}

/* ── Key grid ──────────────────────────────────────────────────────── */
.extra-keys-keys {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
}

/* ── Key buttons ───────────────────────────────────────────────────── */
.extra-key {
    background: #1e2d3d;
    border: 1px solid #2a3a4a;
    color: #c0d0e0;
    font-family: 'Fira Code', 'Cascadia Code', 'JetBrains Mono', monospace;
    font-size: 12px;
    padding: 7px 0;
    min-width: 34px;
    text-align: center;
    border-radius: 4px;
    cursor: pointer;
    transition: background-color 0.1s, border-color 0.1s, transform 0.1s;
    touch-action: manipulation;
    -webkit-user-select: none;
    user-select: none;
    line-height: 1;
}

.extra-key:hover {
    background: #263a4d;
    border-color: #3a4a5a;
}

.extra-key:active {
    background: #2a4050;
    transform: scale(0.95);
}

/* Wide keys */
.extra-key.wide {
    min-width: 42px;
}

/* Arrow keys */
.extra-key.arrow {
    font-size: 14px;
    min-width: 30px;
    font-family: system-ui, sans-serif;
}

/* ── Modifier states ───────────────────────────────────────────────── */
.extra-key.modifier {
    color: #c084fc;
    border-color: #3a2d5a;
}

.extra-key.modifier:hover {
    background: #2a2545;
}

.extra-key.modifier.active-mod {
    background: #3a2560;
    border-color: #7c5cbf;
    color: #d8b4fe;
    box-shadow: 0 0 6px rgba(192, 132, 252, 0.3);
}

.extra-key.modifier.locked {
    background: #3a2560;
    border-color: #9b7ad8;
    color: #e0c4ff;
    box-shadow: 0 0 6px rgba(192, 132, 252, 0.3), inset 0 0 0 1px rgba(192, 132, 252, 0.3);
}

/* ── Light theme ───────────────────────────────────────────────────── */
.extra-keys-bar.light {
    background: #f0f2f4;
    border-top-color: #d0d7de;
}

.light .extra-keys-tab {
    color: #8b949e;
}

.light .extra-keys-tab:hover {
    color: #57606a;
}

.light .extra-keys-tab.active {
    color: #24292e;
    background: rgba(0, 0, 0, 0.06);
}

.light .extra-key {
    background: #ffffff;
    border-color: #d0d7de;
    color: #24292e;
}

.light .extra-key:hover {
    background: #f6f8fa;
    border-color: #b0b8c0;
}

.light .extra-key:active {
    background: #eaeef2;
}

.light .extra-key.modifier {
    color: #8250df;
    border-color: #c8b8e8;
}

.light .extra-key.modifier:hover {
    background: #f5f0ff;
}

.light .extra-key.modifier.active-mod {
    background: #ede4ff;
    border-color: #8250df;
    color: #6e3dc7;
    box-shadow: 0 0 4px rgba(130, 80, 223, 0.2);
}

.light .extra-key.modifier.locked {
    background: #ede4ff;
    border-color: #6e3dc7;
    color: #5b21b6;
    box-shadow: 0 0 4px rgba(130, 80, 223, 0.2), inset 0 0 0 1px rgba(130, 80, 223, 0.2);
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ExtraKeysBar.vue
git commit -m "feat(terminal): create ExtraKeysBar component with tabs, modifiers, and theme support"
```

---

## Task 3: Integrate ExtraKeysBar into TerminalPanel

**Files:**
- Modify: `frontend/src/components/TerminalPanel.vue`

### Design

Import the new component and wire it up. The composable already exposes everything needed — just destructure the new values and bind them.

### Steps

- [ ] **Step 1: Update script setup**

Add the ExtraKeysBar import and destructure the new composable values. Replace the script section of `TerminalPanel.vue`:

```vue
<script setup>
import { watch } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import { useSettingsStore } from '../stores/settings'
import ExtraKeysBar from './ExtraKeysBar.vue'

const props = defineProps({
    sessionId: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: false,
    },
})

const settingsStore = useSettingsStore()

const {
    containerRef, isConnected, started, start, reconnect,
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
} = useTerminal(props.sessionId)

// Lazy init: start the terminal only when the tab becomes active for the first time
watch(
    () => props.active,
    (active) => {
        if (active && !started.value) {
            start()
        }
    },
    { immediate: true },
)
</script>
```

- [ ] **Step 2: Update template**

Add the ExtraKeysBar component between `.terminal-container` and the disconnect overlay:

```vue
<template>
    <div class="terminal-panel">
        <div ref="containerRef" class="terminal-container"></div>

        <ExtraKeysBar
            :active-modifiers="activeModifiers"
            :locked-modifiers="lockedModifiers"
            :theme="settingsStore.getEffectiveTheme"
            @key-input="handleExtraKeyInput"
            @modifier-toggle="handleExtraKeyModifierToggle"
            @paste="handleExtraKeyPaste"
        />

        <!-- Disconnect overlay -->
        <div v-if="started && !isConnected" class="disconnect-overlay">
            <wa-callout variant="warning" appearance="outlined">
                <wa-icon slot="icon" name="plug-circle-xmark"></wa-icon>
                <div class="disconnect-content">
                    <div>Terminal disconnected</div>
                    <wa-button
                        variant="warning"
                        appearance="outlined"
                        size="small"
                        @click="reconnect"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        Reconnect
                    </wa-button>
                </div>
            </wa-callout>
        </div>
    </div>
</template>
```

No CSS changes needed — `.terminal-container` already has `flex: 1; min-height: 0` so it will shrink to accommodate the bar. The existing `ResizeObserver` will detect the size change and trigger `fitAddon.fit()`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TerminalPanel.vue
git commit -m "feat(terminal): integrate ExtraKeysBar into TerminalPanel"
```

---

## Task 4: Manual testing checklist

After all code is committed, verify the following in the browser:

- [ ] **Step 1: Visual rendering** — Navigate to a session's Terminal tab. The extra keys bar should appear below the terminal with the "Essentials" tab active. Verify all 12 keys render correctly.

- [ ] **Step 2: Tab switching** — Click "More" and "F-keys" tabs. Each should show its set of keys. Click back to "Essentials".

- [ ] **Step 3: Regular key input** — Click ESC, TAB, arrow keys, and character keys (`-`, `/`, `|`, `~`). Verify each produces the expected terminal behavior (e.g., TAB triggers autocomplete, arrows move cursor in shell history).

- [ ] **Step 4: Modifier one-shot** — Click CTRL, then type `c` on keyboard (or click a key). Verify Ctrl+C is sent (process interrupt). Verify CTRL deactivates after the keypress. Check the visual glow appears when active.

- [ ] **Step 5: Modifier lock** — Double-tap CTRL quickly. Verify the locked visual (inset border). Type multiple keys — CTRL should stay active. Tap CTRL again to unlock.

- [ ] **Step 6: F-keys** — Switch to F-keys tab, click F1. Run `htop` and verify F-keys work for its controls (F10 to quit).

- [ ] **Step 7: PASTE** — Copy text to clipboard, switch to More tab, click PASTE. Verify the text appears in the terminal.

- [ ] **Step 8: Focus preservation** — Click keys in the bar and verify the terminal retains focus (blinking cursor still visible, keyboard input still goes to terminal).

- [ ] **Step 9: Theme switching** — Toggle dark/light mode in settings. Verify the bar colors update to match.

- [ ] **Step 10: Terminal resize** — The terminal should have reflowed its rows when the bar appeared. Verify no clipping or overflow.
