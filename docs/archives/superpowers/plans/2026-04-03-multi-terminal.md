# Multi-Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **IMPORTANT:** No commits during implementation. No tests (project policy: "no tests and no linting").

**Goal:** Allow users to open multiple independent terminals per session, each with its own PTY/tmux session, navigated via tabs in the terminal toolbar.

**Architecture:** Each terminal is a self-contained `TerminalInstance` component calling `useTerminal(sessionId, terminalIndex)`. A refactored `TerminalPanel` wraps N instances in a `wa-tab-group`, routes the shared `ExtraKeysBar` to the active terminal, and manages create/kill lifecycle. The backend adds a `terminal_index` URL segment. tmux discovery (`list-sessions`) serves as the source of truth for persistent terminals — no database storage needed. Cross-device sync uses broadcasts through the main app WebSocket.

**Tech Stack:** Django ASGI (Channels), tmux, Vue 3 (Composition API), Web Awesome `wa-tab-group`, Pinia, xterm.js

**Spec:** `docs/superpowers/specs/2026-04-03-multi-terminal.md`

---

## File Structure

### Backend — Modified files

| File | Responsibility |
|------|---------------|
| `src/twicc/terminal.py` | Add `terminal_index` param to tmux naming, add `list_tmux_sessions_for_session()`, add `kill_all_tmux_sessions()` |
| `src/twicc/asgi.py` | Update WS URL route with `terminal_index`, add `list_terminals` and `kill_terminal` WS handlers, add terminal broadcast from `terminal_application` |
| `src/twicc/views.py` | Update archive cleanup to use `kill_all_tmux_sessions()` |

### Frontend — New files

| File | Responsibility |
|------|---------------|
| `frontend/src/components/TerminalInstance.vue` | Single terminal: toolbar + xterm container + disconnect overlay. Extracted from current `TerminalPanel.vue`. Calls `useTerminal()` and registers its API with parent via `provide`/`inject`. |
| `frontend/src/stores/terminalTabs.js` | Pinia store holding backend-reported terminal indices per session. Updated by `useWebSocket` on `terminal_list`, `terminal_created`, `terminal_killed` messages. |

### Frontend — Modified files

| File | Responsibility |
|------|---------------|
| `frontend/src/composables/useTerminal.js` | Accept `terminalIndex` parameter, include it in WS URL path |
| `frontend/src/components/TerminalPanel.vue` | Multi-terminal wrapper: `wa-tab-group` with terminal tabs, `[+]` button, active terminal tracking, ExtraKeysBar routing, discovery on mount, cross-device sync |
| `frontend/src/composables/useWebSocket.js` | Handle `terminal_list`, `terminal_created`, `terminal_killed` message types |
| `frontend/src/views/SessionView.vue` | No changes expected (passes same props to TerminalPanel) |

---

## Task 1: Backend — terminal.py utility functions

**Files:**
- Modify: `src/twicc/terminal.py`

The goal is to make all tmux functions terminal-index-aware, and add discovery/cleanup functions.

- [ ] **Step 1: Update `tmux_session_name()` to accept `terminal_index`**

```python
def tmux_session_name(session_id: str, terminal_index: int = 0) -> str:
    """Return the tmux session name for a given twicc session and terminal index.

    Index 0 (main terminal) uses the original naming for backward compatibility.
    Index N (N >= 1) appends '__N' as suffix.
    """
    base = "twicc-" + session_id.replace(".", "_").replace(":", "_")
    if terminal_index == 0:
        return base
    return f"{base}__{terminal_index}"
```

- [ ] **Step 2: Add `list_tmux_sessions_for_session()` function**

Add after `tmux_session_exists()`:

```python
def list_tmux_sessions_for_session(session_id: str) -> list[int]:
    """List all terminal indices that have active tmux sessions for a given twicc session.

    Queries the twicc tmux socket and filters by session name prefix.
    Returns a sorted list of terminal indices (e.g., [0, 3, 7]).
    Returns an empty list if tmux is not installed or no sessions exist.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return []

    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, OSError):
        return []

    prefix = tmux_session_name(session_id, 0)  # "twicc-<normalized_id>"
    indices = []
    for name in result.stdout.strip().split("\n"):
        if not name:
            continue
        if name == prefix:
            indices.append(0)
        elif name.startswith(prefix + "__"):
            suffix = name[len(prefix) + 2:]
            try:
                indices.append(int(suffix))
            except ValueError:
                continue
    return sorted(indices)
```

- [ ] **Step 3: Update `kill_tmux_session()` to accept `terminal_index`**

Update the existing function signature:

```python
def kill_tmux_session(session_id: str, terminal_index: int = 0) -> bool:
    """Kill the tmux session for the given twicc session ID and terminal index."""
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(session_id, terminal_index)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "kill-session", "-t", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("Failed to kill tmux session %s", name)
        return False
```

- [ ] **Step 4: Add `kill_all_tmux_sessions()` function**

Add after `kill_tmux_session()`:

```python
def kill_all_tmux_sessions(session_id: str) -> int:
    """Kill all tmux sessions for the given twicc session (all terminal indices).

    Returns the number of sessions killed.
    """
    indices = list_tmux_sessions_for_session(session_id)
    killed = 0
    for index in indices:
        if kill_tmux_session(session_id, index):
            killed += 1
    return killed
```

- [ ] **Step 5: Update all internal callers of `tmux_session_name()`**

Inside `terminal.py`, the following functions call `tmux_session_name(session_id)` without `terminal_index`. Add `terminal_index` to each and pass through to `tmux_session_name(session_id, terminal_index)`.

**Parameter placement matters.** In Python, a parameter with a default value cannot precede parameters without defaults. So `terminal_index=0` must come *after* any required positional parameters in each function:

| Function | Current signature | New signature |
|----------|-------------------|---------------|
| `tmux_session_exists` | `(session_id)` | `(session_id, terminal_index=0)` |
| `spawn_tmux_pty` | `(cwd, session_id)` | `(cwd, session_id, terminal_index=0)` |
| `_tmux_pane_state` | `(session_id)` | `(session_id, terminal_index=0)` |
| `tmux_set_option` | `(session_id, option, value)` | `(session_id, option, value, terminal_index=0)` |
| `_tmux_scroll` | `(session_id, lines)` | `(session_id, lines, terminal_index=0)` |
| `_tmux_pane_monitor` | `(session_id, send)` | `(session_id, send, terminal_index=0)` |

Note: `_tmux_pane_monitor` calls `_tmux_pane_state` internally — pass `terminal_index` through.

- [ ] **Step 6: Verify**

Run: `uv run python -c "from twicc.terminal import tmux_session_name, list_tmux_sessions_for_session; print(tmux_session_name('test.id:1', 0)); print(tmux_session_name('test.id:1', 3))"`

Expected output:
```
twicc-test_id_1
twicc-test_id_1__3
```

---

## Task 2: Backend — ASGI route and terminal_application

**Files:**
- Modify: `src/twicc/asgi.py`
- Modify: `src/twicc/terminal.py` (terminal_application function)

- [ ] **Step 1: Update WebSocket URL pattern in `asgi.py`**

In `websocket_urlpatterns`, replace the terminal route:

```python
# Before:
path("ws/terminal/<str:project_id>/<str:session_id>/", terminal_application),

# After:
path("ws/terminal/<str:project_id>/<str:session_id>/<int:terminal_index>/", terminal_application),
```

- [ ] **Step 2: Update `terminal_application()` in `terminal.py` to use `terminal_index`**

At the top of `terminal_application()`, extract the new kwarg:

```python
session_id = scope["url_route"]["kwargs"]["session_id"]
project_id = scope["url_route"]["kwargs"].get("project_id")
terminal_index = scope["url_route"]["kwargs"].get("terminal_index", 0)
if project_id == "_":
    project_id = None
```

Then pass `terminal_index` to all tmux-related calls within `terminal_application()`. The specific call sites to update (line numbers refer to the current code):

| Call site | Current code | Updated code |
|-----------|-------------|-------------|
| Archived check (~line 504) | `tmux_session_exists(session_id)` | `tmux_session_exists(session_id, terminal_index)` |
| Post-spawn poll (~line 531) | `tmux_session_exists(session_id)` | `tmux_session_exists(session_id, terminal_index)` |
| Mouse off (~line 537) | `tmux_set_option(session_id, "mouse", "off")` | `tmux_set_option(session_id, "mouse", "off", terminal_index)` |
| Global mouse off (~line 538) | `_tmux_set_global_option("mouse", "off")` | Unchanged (global, no session) |
| Spawn (~line 498) | `spawn_tmux_pty(cwd, session_id)` | `spawn_tmux_pty(cwd, session_id, terminal_index)` |
| Pane monitor (~line 645) | `_tmux_pane_monitor(session_id, send)` | `_tmux_pane_monitor(session_id, send, terminal_index)` |
| Scroll (~line 627) | `_tmux_scroll(session_id, scroll_lines)` | `_tmux_scroll(session_id, scroll_lines, terminal_index)` |

Note: `terminal_index` is the **last** parameter in `tmux_set_option`, `_tmux_scroll`, and `_tmux_pane_monitor` (see Task 1 Step 5 signature table). Pass it as a keyword argument for clarity: `tmux_set_option(session_id, "mouse", "off", terminal_index=terminal_index)`.

- [ ] **Step 3: Add `terminal_created` broadcast in `terminal_application()`**

After the PTY is successfully spawned and the terminal is ready (after the tmux mouse-off setup, or after `spawn_pty` for non-tmux), broadcast via the channel layer:

```python
# Broadcast terminal_created to all clients
try:
    from channels.layers import get_channel_layer
    channel_layer = get_channel_layer()
    if channel_layer:
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "terminal_created",
                    "session_id": session_id,
                    "terminal_index": terminal_index,
                },
            },
        )
except Exception:
    logger.debug("Failed to broadcast terminal_created", exc_info=True)
```

Place this right before the `asyncio.wait()` call that starts the reader/writer tasks.

- [ ] **Step 4: Verify**

Restart the backend. Open browser dev tools, check that the existing terminal still connects (it will fail until the frontend is updated in Task 5, since the URL format changed). Check backend logs for any import errors.

---

## Task 3: Backend — WS handlers for terminal lifecycle

**Files:**
- Modify: `src/twicc/asgi.py` (UpdatesConsumer)

- [ ] **Step 1: Add `list_terminals` handler**

Add a new async method to `UpdatesConsumer`:

```python
async def _handle_list_terminals(self, data):
    """Handle list_terminals request: return active tmux terminal indices for a session."""
    session_id = data.get("session_id")
    if not session_id:
        await self.send_json({"type": "error", "message": "Missing session_id"})
        return

    from twicc.terminal import list_tmux_sessions_for_session
    indices = await asyncio.to_thread(list_tmux_sessions_for_session, session_id)

    await self.send_json({
        "type": "terminal_list",
        "session_id": session_id,
        "terminals": indices,
    })
```

- [ ] **Step 2: Add `kill_terminal` handler**

```python
async def _handle_kill_terminal(self, data):
    """Handle kill_terminal request: kill a secondary terminal's tmux session and broadcast."""
    session_id = data.get("session_id")
    terminal_index = data.get("terminal_index")
    if not session_id or terminal_index is None:
        await self.send_json({"type": "error", "message": "Missing session_id or terminal_index"})
        return

    # Safety: never kill the main terminal via this handler (main terminal uses disconnect only)
    if terminal_index == 0:
        await self.send_json({"type": "error", "message": "Cannot kill main terminal"})
        return

    from twicc.terminal import kill_tmux_session
    await asyncio.to_thread(kill_tmux_session, session_id, terminal_index)

    # Broadcast to all clients
    await self.channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "terminal_killed",
                "session_id": session_id,
                "terminal_index": terminal_index,
            },
        },
    )
```

- [ ] **Step 3: Add routing in `receive_json()`**

In the `receive_json()` method of `UpdatesConsumer`, add the new cases (alongside the existing `match msg_type` or `if/elif` chain):

```python
elif msg_type == "list_terminals":
    await self._handle_list_terminals(data)
elif msg_type == "kill_terminal":
    await self._handle_kill_terminal(data)
```

- [ ] **Step 4: Verify**

Restart backend. Use browser console to send a test message:

```javascript
// From browser console while on the app (WS must be connected)
// This should return a terminal_list response in the WS messages
```

Check backend logs for any errors.

---

## Task 4: Backend — Archive cleanup

**Files:**
- Modify: `src/twicc/views.py`

- [ ] **Step 1: Replace `kill_tmux_session` with `kill_all_tmux_sessions` in archive handler**

In `views.py`, around line 369-370, change:

```python
# Before:
from twicc.terminal import kill_tmux_session
kill_tmux_session(session_id)

# After:
from twicc.terminal import kill_all_tmux_sessions
kill_all_tmux_sessions(session_id)
```

- [ ] **Step 2: Verify**

Restart backend. Archive a session that had terminals — check backend logs for cleanup behavior.

---

## Task 5: Frontend — useTerminal.js accepts terminalIndex

**Files:**
- Modify: `frontend/src/composables/useTerminal.js`

- [ ] **Step 1: Add `terminalIndex` parameter to composable signature**

```javascript
// Before:
export function useTerminal(sessionId) {

// After:
export function useTerminal(sessionId, terminalIndex = 0) {
```

- [ ] **Step 2: Update `getWsUrl()` to include `terminal_index` in path**

```javascript
// Before:
const base = `${wsProtocol}//${location.host}/ws/terminal/${projectId}/${sessionId}/`

// After:
const base = `${wsProtocol}//${location.host}/ws/terminal/${projectId}/${sessionId}/${terminalIndex}/`
```

- [ ] **Step 3: Verify**

Start dev servers. Open a session's terminal tab. Check browser dev tools Network tab — the WebSocket URL should now be `/ws/terminal/<project>/<session>/0/`. The terminal should connect and work as before.

---

## Task 6: Frontend — Create TerminalInstance.vue (extraction)

**Files:**
- Create: `frontend/src/components/TerminalInstance.vue`
- Modify: `frontend/src/components/TerminalPanel.vue`

This task extracts the per-terminal UI (toolbar + xterm container + disconnect overlay) from the current `TerminalPanel.vue` into a new `TerminalInstance.vue`. After this task, `TerminalPanel` renders a single `TerminalInstance` and everything works as before.

- [ ] **Step 1: Create `TerminalInstance.vue`**

The component contains everything from the current `TerminalPanel.vue` that is specific to a single terminal. It does NOT include ExtraKeysBar, ManageCombosDialog, ManageSnippetsDialog, or the snippets computation.

**Props:**
```javascript
const props = defineProps({
    sessionId: { type: String, default: null },
    terminalIndex: { type: Number, default: 0 },
    active: { type: Boolean, default: false },
    isMain: { type: Boolean, default: true },
})
```

**Emits:**
```javascript
const emit = defineEmits(['kill'])
```

**Script setup core:**
```javascript
import { watch } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import { useSettingsStore } from '../stores/settings'
import AppTooltip from './AppTooltip.vue'

const settingsStore = useSettingsStore()

const {
    containerRef, isConnected, started, start, reconnect, disconnect,
    touchMode, hasSelection, copySelection,
    paneAlternate,
    canScrollUp, canScrollDown,
    scrollToEdge, scrollingToEdge, cancelScrollToEdge,
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
    handleComboPress, handleSnippetPress,
} = useTerminal(props.sessionId, props.terminalIndex)

function handleTouchModeChange(event) {
    touchMode.value = event.target.checked ? 'select' : 'scroll'
}

function handlePaste() {
    handleExtraKeyPaste()
}

function handleDisconnect() {
    if (props.isMain) {
        disconnect()
    } else {
        emit('kill')
    }
}

// Lazy init: start only when this terminal becomes active
watch(
    () => props.active,
    (active) => {
        if (active && !started.value) {
            start()
        }
    },
    { immediate: true },
)
```

**Registration with parent** — use `inject` to register this terminal's API so the parent can route ExtraKeysBar events:

```javascript
import { inject, onUnmounted } from 'vue'

const registerTerminal = inject('registerTerminal', null)
const unregisterTerminal = inject('unregisterTerminal', null)

// Register this terminal's API with the parent for ExtraKeysBar routing
const terminalApi = {
    activeModifiers,
    lockedModifiers,
    handleExtraKeyInput,
    handleExtraKeyModifierToggle,
    handleExtraKeyPaste,
    handleComboPress,
    handleSnippetPress,
}
registerTerminal?.(props.terminalIndex, terminalApi)

onUnmounted(() => {
    unregisterTerminal?.(props.terminalIndex)
})
```

**Template:** Move the following from current `TerminalPanel.vue`:
- The entire `.terminal-actions-bar` div (toolbar with scroll/copy/paste/disconnect buttons)
- The entire `.terminal-area` div (containerRef + disconnect overlay)
- Change the disconnect button's `@click` from `disconnect` to `handleDisconnect`
- The disconnect button's tooltip text: use `isMain ? 'Disconnect' : 'Kill terminal'`

**IMPORTANT — Unique element IDs:** The current `TerminalPanel.vue` uses static IDs like `id="terminal-scroll-top-button"`, `id="terminal-paste-button"`, etc. for `AppTooltip for="..."` anchoring. With multiple `TerminalInstance` components in the DOM, these would collide. Make all IDs unique per terminal by appending the terminal index:
```html
:id="`terminal-scroll-top-button-${terminalIndex}`"
```
And update corresponding `AppTooltip` `for` attributes:
```html
<AppTooltip :for="`terminal-scroll-top-button-${terminalIndex}`" ...>
```
Apply this to all tooltip-anchored elements: `terminal-scroll-top-button`, `terminal-scroll-bottom-button`, `terminal-paste-button`, `terminal-disconnect-button`.

**Styles:** Move all relevant scoped styles from `TerminalPanel.vue`:
- `.terminal-actions-bar` and its children
- `.scroll-edge-button`, `.push-right`, `.disconnect-button`
- `.touch-mode-group`, `.touch-mode-label`
- `.copy-button`, `.paste-button`, `.copy-fade-*`
- `.terminal-area`, `.terminal-container`, `.terminal-container :deep(.xterm)`, `.terminal-container :deep(.xterm-viewport)`
- `.disconnect-overlay`, `.disconnect-content`

Remove `.terminal-panel` from TerminalInstance (the outer wrapper stays in TerminalPanel).

- [ ] **Step 2: Update `TerminalPanel.vue` to use `TerminalInstance`**

Strip TerminalPanel down to a thin wrapper that renders a single TerminalInstance plus the shared ExtraKeysBar and dialogs.

**Script setup:**
```javascript
import { computed, provide, reactive, ref } from 'vue'
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { useTerminalConfigStore } from '../stores/terminalConfig'
import { toast } from '../composables/useToast'
import { getUnavailablePlaceholders } from '../utils/snippetPlaceholders'
import TerminalInstance from './TerminalInstance.vue'
import ExtraKeysBar from './ExtraKeysBar.vue'
import ManageCombosDialog from './ManageCombosDialog.vue'
import ManageSnippetsDialog from './ManageSnippetsDialog.vue'

const props = defineProps({
    sessionId: { type: String, default: null },
    active: { type: Boolean, default: false },
})

const settingsStore = useSettingsStore()
const dataStore = useDataStore()
const terminalConfigStore = useTerminalConfigStore()

const session = computed(() => dataStore.getSession(props.sessionId))
const projectId = computed(() => session.value?.project_id)

// --- Terminal registration (provide/inject) ---
const terminalApis = reactive(new Map())

provide('registerTerminal', (index, api) => {
    terminalApis.set(index, api)
})
provide('unregisterTerminal', (index) => {
    terminalApis.delete(index)
})

// For now, single terminal — active is always index 0
const activeIndex = ref(0)
const activeApi = computed(() => terminalApis.get(activeIndex.value) || null)

// --- Snippets (shared) ---
const snippetsForProject = computed(() => {
    // Same logic as current TerminalPanel (merge global + project snippets,
    // enrich with _disabled / _disabledReason)
    // ... (copy the existing computed from current TerminalPanel)
})

const manageCombosDialogRef = ref(null)
const manageSnippetsDialogRef = ref(null)
```

**Template:**
```html
<div class="terminal-panel">
    <TerminalInstance
        :session-id="sessionId"
        :terminal-index="0"
        :active="active"
        :is-main="true"
    />

    <ExtraKeysBar
        :active-modifiers="activeApi?.activeModifiers ?? { ctrl: false, alt: false, shift: false }"
        :locked-modifiers="activeApi?.lockedModifiers ?? { ctrl: false, alt: false, shift: false }"
        :is-touch-device="settingsStore.isTouchDevice"
        :combos="terminalConfigStore.combos"
        :snippets="snippetsForProject"
        @key-input="(...args) => activeApi?.handleExtraKeyInput?.(...args)"
        @modifier-toggle="(...args) => activeApi?.handleExtraKeyModifierToggle?.(...args)"
        @paste="() => activeApi?.handleExtraKeyPaste?.()"
        @combo-press="(...args) => activeApi?.handleComboPress?.(...args)"
        @snippet-press="(...args) => activeApi?.handleSnippetPress?.(...args)"
        @snippet-disabled-press="(snippet) => toast.warning(snippet._disabledReason)"
        @manage-combos="manageCombosDialogRef?.open()"
        @manage-snippets="manageSnippetsDialogRef?.open()"
    />

    <ManageCombosDialog ref="manageCombosDialogRef" />
    <ManageSnippetsDialog ref="manageSnippetsDialogRef" :current-project-id="projectId" />
</div>
```

**Styles:** Keep only `.terminal-panel` (flex column, height 100%) in TerminalPanel. All terminal-specific styles are now in TerminalInstance.

- [ ] **Step 3: Verify**

Open a session's terminal tab. Everything should work exactly as before: toolbar buttons, scroll, copy/paste, disconnect/reconnect, ExtraKeysBar, combos, snippets. This is a pure refactoring step — no new functionality.

---

## Task 7: Frontend — Multi-terminal tabs in TerminalPanel

**Files:**
- Modify: `frontend/src/components/TerminalPanel.vue`
- Possibly: `frontend/src/main.js` (verify `wa-tab-group`/`wa-tab`/`wa-tab-panel` imports exist)

- [ ] **Step 1: Verify Web Awesome tab imports in main.js**

Check that the following are imported in `frontend/src/main.js`:
```javascript
import '@awesome.me/webawesome/dist/components/tab-group/tab-group.js'
import '@awesome.me/webawesome/dist/components/tab/tab.js'
import '@awesome.me/webawesome/dist/components/tab-panel/tab-panel.js'
```

These are already used in `SessionView.vue`, so they should be present. Add if missing.

- [ ] **Step 2: Add terminal management state**

In `TerminalPanel.vue` script setup, add the `sendWsMessage` import (direct import — no circular dependency risk from a component) and replace the single-terminal code with multi-terminal state:

```javascript
import { sendWsMessage } from '../composables/useWebSocket'

// --- Terminal tab management ---
// Each entry: { index: number, label: string }
const terminals = ref([{ index: 0, label: 'Main' }])
const activeIndex = ref(0)
const nextIndex = ref(1)  // monotonically increasing counter

const activeTabPanel = computed(() => `term-${activeIndex.value}`)

function createTerminal() {
    const index = nextIndex.value++
    terminals.value.push({ index, label: `Term ${index + 1}` })
    activeIndex.value = index
}

function killTerminal(index) {
    if (index === 0) return  // main terminal cannot be killed

    // Send kill_terminal request to backend (for tmux cleanup)
    sendWsMessage({
        type: 'kill_terminal',
        session_id: props.sessionId,
        terminal_index: index,
    })

    // Remove from local list
    const idx = terminals.value.findIndex(t => t.index === index)
    if (idx !== -1) {
        terminals.value.splice(idx, 1)
    }

    // Switch to previous tab or main
    if (activeIndex.value === index) {
        const prevTerminal = terminals.value[Math.max(0, idx - 1)]
        activeIndex.value = prevTerminal?.index ?? 0
    }
}

function onTerminalTabShow(event) {
    const panelName = event.detail?.name || event.target?.getAttribute?.('panel')
    if (panelName?.startsWith('term-')) {
        activeIndex.value = parseInt(panelName.slice(5), 10)
    }
}
```

- [ ] **Step 3: Update template with `wa-tab-group`**

Replace the single `<TerminalInstance>` with:

```html
<div class="terminal-panel">
    <wa-tab-group
        :active="activeTabPanel"
        class="terminal-tabs"
        @wa-tab-show="onTerminalTabShow"
    >
        <!-- Terminal tabs -->
        <wa-tab
            v-for="term in terminals"
            :key="term.index"
            slot="nav"
            :panel="`term-${term.index}`"
        >
            {{ term.label }}
        </wa-tab>

        <!-- Create new terminal button (use wa-button + wa-icon, not wa-icon-button which is not imported) -->
        <wa-button
            slot="nav"
            variant="text"
            size="small"
            class="add-terminal-button"
            @click="createTerminal"
        >
            <wa-icon name="plus" library="solid"></wa-icon>
        </wa-button>

        <!-- Terminal panels -->
        <wa-tab-panel
            v-for="term in terminals"
            :key="term.index"
            :name="`term-${term.index}`"
        >
            <TerminalInstance
                :session-id="sessionId"
                :terminal-index="term.index"
                :active="active && activeIndex === term.index"
                :is-main="term.index === 0"
                @kill="killTerminal(term.index)"
            />
        </wa-tab-panel>
    </wa-tab-group>

    <ExtraKeysBar ... />  <!-- same as Task 6 -->

    <ManageCombosDialog ... />
    <ManageSnippetsDialog ... />
</div>
```

- [ ] **Step 4: Add CSS for terminal tabs**

```css
.terminal-tabs {
    flex: 1;
    min-height: 0;
    overflow: hidden;
}
.terminal-tabs::part(base) {
    height: 100%;
    overflow: hidden;
}
.terminal-tabs::part(nav) {
    /* Compact tab bar */
}
.terminal-tabs::part(body) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}
.terminal-tabs :deep(wa-tab-panel[active]) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}
.terminal-tabs :deep(wa-tab-panel[active])::part(base) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    padding: 0;
}
/* Also zero-pad inactive panels */
.terminal-tabs :deep(wa-tab-panel)::part(base) {
    padding: 0;
}
.add-terminal-button {
    font-size: var(--wa-font-size-2xs);
    align-self: center;
}
```

Use the existing `SessionView.vue` tab CSS (`.session-tabs`) as a reference — the pattern is nearly identical (flex: 1, min-height: 0, overflow: hidden at each level).

- [ ] **Step 5: Verify**

Open a session terminal. You should see "Main" tab and a `[+]` button. Click `[+]` — a "Term 2" tab appears and becomes active, with its own terminal connecting. Switch between tabs — each terminal should be independent. Click disconnect on Term 2 — the tab should disappear and you're back on Main.

---

## Task 8: Frontend — terminalTabs store and WS integration

**Files:**
- Create: `frontend/src/stores/terminalTabs.js`
- Modify: `frontend/src/composables/useWebSocket.js`

- [ ] **Step 1: Create `terminalTabs.js` Pinia store**

```javascript
// frontend/src/stores/terminalTabs.js
import { defineStore } from 'pinia'

export const useTerminalTabsStore = defineStore('terminalTabs', {
    state: () => ({
        // sessionId → sorted array of terminal indices from backend
        indices: {},
    }),
    actions: {
        setIndices(sessionId, terminalIndices) {
            this.indices[sessionId] = [...terminalIndices].sort((a, b) => a - b)
        },
        addIndex(sessionId, index) {
            if (!this.indices[sessionId]) {
                this.indices[sessionId] = []
            }
            if (!this.indices[sessionId].includes(index)) {
                this.indices[sessionId].push(index)
                this.indices[sessionId].sort((a, b) => a - b)
            }
        },
        removeIndex(sessionId, index) {
            if (this.indices[sessionId]) {
                this.indices[sessionId] = this.indices[sessionId].filter(i => i !== index)
            }
        },
    },
})
```

- [ ] **Step 2: Add message handling in `useWebSocket.js`**

In the `handleMessage` switch statement, add three new cases (follow the lazy-import pattern used by `terminal_config_updated` and `synced_settings_updated`):

```javascript
case 'terminal_list':
    import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
        useTerminalTabsStore().setIndices(msg.session_id, msg.terminals)
    })
    break
case 'terminal_created':
    import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
        useTerminalTabsStore().addIndex(msg.session_id, msg.terminal_index)
    })
    break
case 'terminal_killed':
    import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
        useTerminalTabsStore().removeIndex(msg.session_id, msg.terminal_index)
    })
    break
```

- [ ] **Step 3: Verify**

Open browser dev tools. Check that `terminal_created` messages arrive in the WS when opening a terminal. Check the Pinia devtools to see the `terminalTabs` store state update.

---

## Task 9: Frontend — Discovery and cross-device sync in TerminalPanel

**Files:**
- Modify: `frontend/src/components/TerminalPanel.vue`

- [ ] **Step 1: Add discovery request on mount**

When TerminalPanel first becomes active, request the list of existing terminals:

```javascript
import { sendWsMessage } from '../composables/useWebSocket'
import { useTerminalTabsStore } from '../stores/terminalTabs'

const terminalTabsStore = useTerminalTabsStore()
let discoveryDone = false

watch(
    () => props.active,
    (active) => {
        if (active && !discoveryDone) {
            discoveryDone = true
            sendWsMessage({
                type: 'list_terminals',
                session_id: props.sessionId,
            })
        }
    },
    { immediate: true },
)
```

- [ ] **Step 2: Watch store for backend terminal updates**

Add a watcher on the `terminalTabsStore` to sync the local tab list with backend-reported terminals:

```javascript
watch(
    () => terminalTabsStore.indices[props.sessionId],
    (backendIndices) => {
        if (!backendIndices || !backendIndices.length) return
        syncTerminalsFromBackend(backendIndices)
    },
)

function syncTerminalsFromBackend(backendIndices) {
    const localIndices = new Set(terminals.value.map(t => t.index))

    // Add tabs for backend terminals not present locally
    for (const index of backendIndices) {
        if (!localIndices.has(index)) {
            const label = index === 0 ? 'Main' : `Term ${index + 1}`
            terminals.value.push({ index, label })
        }
    }

    // Sort terminals by index for consistent ordering
    terminals.value.sort((a, b) => a.index - b.index)

    // Update nextIndex to avoid collisions
    const maxIndex = Math.max(...backendIndices, ...terminals.value.map(t => t.index))
    if (maxIndex >= nextIndex.value) {
        nextIndex.value = maxIndex + 1
    }
}
```

- [ ] **Step 3: Handle `terminal_killed` — remove tab if present**

Add another watcher or extend the existing one. When a terminal is killed from another device, the store's `removeIndex` fires, and we should remove the tab locally:

```javascript
// Watch for removed indices (terminal_killed from another device)
watch(
    () => terminalTabsStore.indices[props.sessionId],
    (newIndices, oldIndices) => {
        if (!oldIndices || !newIndices) return

        // Find indices that were removed
        const removedIndices = oldIndices.filter(i => !newIndices.includes(i))
        for (const index of removedIndices) {
            if (index === 0) continue  // main terminal never removed
            const idx = terminals.value.findIndex(t => t.index === index)
            if (idx !== -1) {
                terminals.value.splice(idx, 1)
                // If the removed terminal was active, switch to previous or main
                if (activeIndex.value === index) {
                    const prevTerminal = terminals.value[Math.max(0, idx - 1)]
                    activeIndex.value = prevTerminal?.index ?? 0
                }
            }
        }
    },
)
```

Note: This can be combined with the `syncTerminalsFromBackend` watcher into a single unified watcher that does both add and remove in one pass. The implementation can decide the cleanest approach.

- [ ] **Step 4: Handle `terminal_created` — idempotent add**

The `syncTerminalsFromBackend` function already handles this: it checks `localIndices.has(index)` before adding. If a `terminal_created` broadcast arrives for a terminal this client already created locally, it's a no-op.

- [ ] **Step 5: Verify discovery flow**

With tmux enabled:
1. Open a session terminal. Create Term 2 and Term 3.
2. Refresh the page.
3. Navigate back to the session's terminal tab.
4. Expected: Main, Term 2, and Term 3 tabs should appear (from tmux discovery).
5. Click on Term 2 — it should reconnect to the existing tmux session.

- [ ] **Step 6: Verify cross-device sync**

Open the app in two browser tabs:
1. In tab A, create Term 2.
2. In tab B, the terminal panel should receive the `terminal_created` broadcast and show a Term 2 tab.
3. In tab A, kill Term 2.
4. In tab B, the Term 2 tab should disappear.

---

## Verification Checklist

After all tasks are complete, verify the following end-to-end scenarios:

- [ ] Single terminal works exactly as before (regression check)
- [ ] Creating multiple terminals via `[+]` button works
- [ ] Each terminal has its own independent shell/connection
- [ ] Switching between terminal tabs preserves each terminal's state
- [ ] ExtraKeysBar sends input to the active terminal only
- [ ] Disconnecting the main terminal shows reconnect overlay, tab stays
- [ ] Killing a secondary terminal removes the tab and cleans up
- [ ] With tmux: terminals persist across page refresh (discovered via `list-sessions`)
- [ ] Without tmux (draft session): only main terminal on refresh, `[+]` still works
- [ ] Archiving a session kills all tmux terminals (check with `tmux -L twicc list-sessions`)
- [ ] Cross-device: creating/killing terminals is reflected on other clients
