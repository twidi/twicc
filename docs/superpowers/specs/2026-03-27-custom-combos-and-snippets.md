# Custom Combos & Snippets for Terminal Extra Keys Bar

**Date:** 2026-03-27
**Status:** Approved
**Depends on:** Terminal Extra Keys Bar (implemented)

## Summary

Extend the terminal extra keys bar with two new tabs: **Custom** (user-defined key combos and sequences, mobile only) and **Snippets** (text snippets, mobile + desktop). Configuration is stored in `~/.twicc/terminal-config.json` and synced between devices via a backend API. Both tabs include a "Manage" dialog for adding, editing, deleting, duplicating, and reordering items.

## Motivation

The built-in tabs (Essentials, More, F-keys) cover common keys, but users need:
- **Custom combos**: frequently used Ctrl/Alt combinations (Ctrl+C, Ctrl+Z, Ctrl+R…) and multi-step sequences (tmux prefix combos, emacs chords) as one-tap buttons
- **Snippets**: frequently typed commands or text fragments (deploy scripts, SSH commands, git aliases…) as one-tap buttons, with project-level scoping

Snippets are useful on desktop too — they're not limited to mobile.

## Tab Structure

### Mobile (touch device)

All 5 tabs are shown: **Essentials · More · F-keys · Custom · Snippets**

The tab bar is always visible (same as today with 3 tabs).

### Desktop (non-touch device)

Only the **Snippets** tab is shown. Since there's a single tab, **no tab bar is rendered** — just the row of snippet buttons directly.

**Desktop visibility rules:**
- If the user has snippets → the bar shows the snippet buttons + Manage button
- If the user has no snippets → the bar still shows with "No snippets" text + Manage button (so the user can add their first snippet)

### Visibility logic change in ExtraKeysBar

Currently, `ExtraKeysBar` is gated by `v-if="settingsStore.isTouchDevice"` in `TerminalPanel.vue`. This must change to:

```
v-if="settingsStore.isTouchDevice || isSnippetsMode"
```

On desktop (non-touch), the bar is **always visible** in snippets-only mode — even with no snippets, the user needs access to the Manage button to add their first snippet. The `ExtraKeysBar` component receives a new prop to know whether it's in touch mode, and adjusts its tab list accordingly.

## Data Model

### Storage file: `~/.twicc/terminal-config.json`

A new JSON file in the TwiCC data directory, alongside `settings.json`. Named `terminal-config.json` to allow future terminal-related configuration.

```json
{
  "combos": [
    { "steps": [{ "modifiers": ["ctrl"], "key": "c" }] },
    { "label": "cancel", "steps": [{ "modifiers": ["ctrl"], "key": "c" }] },
    { "label": "tmux:new", "steps": [
      { "modifiers": ["ctrl"], "key": "b" },
      { "key": "c" }
    ] },
    { "steps": [
      { "modifiers": ["ctrl"], "key": "x" },
      { "modifiers": ["ctrl"], "key": "s" }
    ] }
  ],
  "snippets": {
    "global": [
      { "label": "deploy", "text": "cd /app && docker compose up -d", "appendEnter": true },
      { "label": "ssh-prod", "text": "ssh user@prod.example.com", "appendEnter": true }
    ],
    "project:twicc-poc": [
      { "label": "migrate", "text": "python manage.py migrate", "appendEnter": true }
    ]
  }
}
```

### Combo item

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `label` | string | No | Optional display label. If set, shown on button instead of auto-generated notation. |
| `steps` | array | Yes | Ordered list of steps. A single-step array = simple combo. |

Each **step** in `steps`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `modifiers` | string[] | No | Subset of `["ctrl", "alt", "shift"]`. Omitted or empty = no modifier. |
| `key` | string | Yes | Key identifier — same values as the built-in tabs: `"c"`, `"Escape"`, `"Tab"`, `"ArrowUp"`, `"Home"`, `"F5"`, `"/"`, etc. |

### Snippet item

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `label` | string | Yes | Display label shown on the button. |
| `text` | string | Yes | Text to send to the terminal. |
| `appendEnter` | boolean | Yes | If true, append `\n` after the text. |

### Snippet scope

Snippets are grouped by scope in the `snippets` object:
- Key `"global"` → available in all projects
- Key `"project:<project_id>"` → available only when viewing that project (e.g., `"project:-home-twidi-dev-twicc-poc"`)

The **display order** on the bar is: global snippets first, then current project snippets. Snippets from other projects are not shown on the bar (but are visible in the Manage dialog).

### Array ordering

The order of items in `combos` and in each snippet scope array determines the display order on the bar and in the Manage dialog. Reordering updates the array order.

## Auto-generated Notation for Combos

When a combo has no label, the button text is auto-generated from its steps.

### Step notation

Each step is rendered as its modifiers (uppercase) followed by the key, separated by spaces:
- `{ modifiers: ["ctrl"], key: "c" }` → `CTRL C`
- `{ modifiers: ["ctrl", "alt"], key: "d" }` → `CTRL ALT D`
- `{ key: "c" }` → `C`
- `{ key: "Escape" }` → `ESC`
- `{ key: "ArrowUp" }` → `↑`

Key display mapping (same labels as the built-in tabs):
- `Escape` → `ESC`, `Tab` → `TAB`
- `ArrowLeft` → `←`, `ArrowUp` → `↑`, `ArrowDown` → `↓`, `ArrowRight` → `→`
- `Home` → `HOME`, `End` → `END`, `PageUp` → `PGUP`, `PageDown` → `PGDN`
- `Delete` → `DEL`, `Insert` → `INS`
- `F1`–`F12` → `F1`–`F12`
- Single characters → uppercase: `c` → `C`, `/` → `/`, `+` → `+`

### Multi-step notation (sequences)

Steps are joined with a middle dot separator ` · `:
- `[{ctrl, b}, {c}]` → `CTRL B · C`
- `[{ctrl, x}, {ctrl, s}]` → `CTRL X · CTRL S`
- `[{ctrl, a}, {shift, 5}]` → `CTRL A · SHIFT 5`

### No "+" separator

The `+` sign is NOT used between modifier and key (unlike conventional notation like `Ctrl+C`). This avoids ambiguity when the key itself is `+`. Spaces separate modifiers from key within a step.

## Custom Tab — UI Details

### Bar appearance (populated)

```
[Essentials] [More] [F-keys] [Custom] [Snippets]
┌──────┐ ┌──────┐ ┌──────┐ ┌─────────────┐ ┌─────────┐
│CTRL C│ │CTRL Z│ │CTRL R│ │CTRL B · C   │ │⚙ Manage │
└──────┘ └──────┘ └──────┘ └─────────────┘ └─────────┘
```

With labels:
```
┌──────┐ ┌──────┐ ┌────────┐ ┌──────────┐ ┌─────────┐
│CTRL C│ │CTRL Z│ │tmux:new│ │tmux:split│ │⚙ Manage │
└──────┘ └──────┘ └────────┘ └──────────┘ └─────────┘
```

### Bar appearance (empty)

```
[Essentials] [More] [F-keys] [Custom] [Snippets]
  No custom combos          [⚙ Manage]
```

The "No custom combos" text is centered, dimmed. The Manage button is always present.

### Manage button

The `⚙ Manage` button is always the last item in the key row. It has a distinct style: dashed border, slightly dimmed, to differentiate it from action buttons. Clicking it opens the Manage Custom Combos dialog.

### Combo button behavior

When a combo button is pressed:
1. For **single-step combos**: the step's ANSI sequence is sent to the terminal (exactly like pressing the equivalent keys on the extra keys bar today)
2. For **multi-step sequences**: each step's ANSI sequence is sent in order, immediately one after another. No delay between steps is needed — terminal programs (tmux, screen, emacs…) process the buffer sequentially and correctly separate prefix keys from command keys even when they arrive in the same read. If edge cases arise with slow programs, a configurable delay could be added later.
3. The existing modifier state (from the Essentials tab CTRL/ALT/SHIFT buttons) is **not applied** to custom combo buttons — the combo defines its own modifiers per step. After execution, one-shot modifiers from the Essentials tab are NOT reset (custom combos are self-contained).

### Button styling

Custom combo buttons use the same base styling as regular keys (neutral colors). Since combos are in their own tab, there's no need to visually distinguish them from built-in keys.

## Snippets Tab — UI Details

### Bar appearance (populated, mobile)

```
[Essentials] [More] [F-keys] [Custom] [Snippets]
┌──────┐ ┌────────┐ ┌────────┐ ┌───────┐ ┌─────────┐
│deploy│ │ssh-prod│ │migrate │ │restart│ │⚙ Manage │
└──────┘ └────────┘ └────────┘ └───────┘ └─────────┘
```

Global snippets come first, then current project snippets. No visual separator between global and project snippets on the bar (they just flow together).

### Bar appearance (populated, desktop)

No tab bar (single tab). Just the snippet buttons + Manage:

```
┌──────┐ ┌────────┐ ┌────────┐ ┌─────────┐
│deploy│ │ssh-prod│ │migrate │ │⚙ Manage │
└──────┘ └────────┘ └────────┘ └─────────┘
```

### Bar appearance (empty)

Same on mobile and desktop: "No snippets" text + Manage button. On desktop this is the only content of the bar (no tab bar since single tab).

### Snippet button behavior

When a snippet button is pressed:
1. The snippet's `text` value is sent to the terminal as raw input
2. If `appendEnter` is true, a `\n` character is appended
3. The text is sent via `wsSend({ type: 'input', data: text })` — same mechanism as typed input
4. One-shot modifiers from the Essentials tab are NOT applied or reset

### Button styling

Snippet buttons use the same base styling as regular keys (neutral colors). Since snippets are in their own tab, there's no need to visually distinguish them from other key types.

## Manage Custom Combos Dialog

### Dialog structure

A `wa-dialog` following the project's dialog form pattern (`ProjectEditDialog.vue` as reference):
- Header: "Custom combos" + close button
- Body: scrollable list of existing combos
- Footer: "+ Add combo" button

### List items

Each combo is a row with:
- **Reorder arrows** (↑↓): positioned at the left. The up arrow on the first item and the down arrow on the last item are visually disabled (dimmed). Clicking an arrow swaps the item with its neighbor and immediately saves.
- **Display text**: the auto-generated notation or label. For labeled combos, the label is shown first, then the notation in smaller dimmed text to the right (e.g., `tmux:new` then `CTRL B · C` in gray).
- **Edit button** (✏️): opens the Add/Edit form pre-filled with this combo's data.
- **Duplicate button** (📋): opens the Add/Edit form pre-filled with a copy of this combo's data (creating a new entry on save).
- **Delete button** (🗑️): removes the combo after confirmation (inline — no separate dialog, just remove on click). The delete is immediate.

### Add/Edit Combo Form

Displayed inside the same dialog, replacing the list view. Header changes to "Add combo" or "Edit combo". Back/Cancel returns to the list.

#### Fields

**Label** (optional):
- Text input
- Placeholder: `e.g. "tmux:new"` (italic, dimmed)
- Helper text: "Optional — replaces notation on button"

**Steps**:

When there is **only 1 step**, no "Step 1" label is shown — just the modifier toggles and key input directly. The "+ Add step" button below transforms it into a sequence.

When there are **2+ steps**, each step is in a bordered card showing:
- Header: "Step 1", "Step 2", etc. + a ✕ button to remove the step (disabled if only 1 step remains, hidden if only 1 step exists)
- A vertical ↓ arrow between steps (visual separator, not interactive)

Each step contains:

**Modifier toggles**: three toggle buttons: `CTRL`, `ALT`, `SHIFT`. Clicking toggles selection (highlighted border + fill when active, dimmed when inactive). Multiple modifiers can be active simultaneously.

**Key input**: a text input field where the user can type a single character. Below it, a **key picker** grid with all keys from the built-in tabs:

Row 1 (from Essentials): `ESC`, `TAB`, `←`, `↑`, `↓`, `→`, `-`, `/`, `|`, `~`
Row 2 (from More): `HOME`, `END`, `PGUP`, `PGDN`, `DEL`, `INS`, `\`, `_`, `*`, `&`, `.`, `+`
Row 3 (F-keys): `F1` through `F12`

Helper text below the input: "Type a key or pick from common keys below"

Clicking a picker key fills the input field. The input also accepts free typing for letters a-z, digits 0-9, and any character.

**"+ Add step" button**: below the last step. Styled as a dashed-border button, dimmed. Clicking adds a new empty step.

#### Buttons

- **Cancel**: returns to the list without saving
- **Save**: validates and saves

#### Validation

- At least one step must have a key defined
- Each step must have a non-empty key
- Duplicate detection: warn (but don't block) if an identical combo already exists

## Manage Snippets Dialog

### Dialog structure

Same pattern as Manage Custom Combos. Header: "Snippets".

### List organization

Snippets are **grouped by scope**:
1. **"All projects"** group header — always first, even if empty
2. **Per-project groups** — each headed by the project's `ProjectBadge` component (colored dot + name). Only projects that have snippets are shown. Ordered alphabetically by project display name.

A thin horizontal separator (`border-top`) separates groups.

Within each group, items are ordered by their array position.

### List items

Each snippet row has:
- **Reorder arrows** (↑↓): same as combos. Reorder is within the scope group only (cannot move a snippet from one project to another via arrows).
- **Label**: shown in a green badge/chip style (matching the bar button appearance)
- **Text preview**: the snippet text, truncated with ellipsis, in dimmed monospace. If `appendEnter` is true, a `↵` symbol is appended to the preview.
- **Edit button** (✏️): opens the Add/Edit form
- **Duplicate button** (📋): opens the Add/Edit form pre-filled. The scope selector defaults to the **original snippet's scope** (not the current project).
- **Delete button** (🗑️): immediate removal, no confirmation dialog.

### Add/Edit Snippet Form

Displayed inside the same dialog, replacing the list. Header: "Add snippet" or "Edit snippet".

#### Fields

**Label** (required):
- Text input
- This is what appears on the button. Should be short.

**Text to send** (required):
- Textarea (multi-line)
- Monospace font
- The actual content sent to the terminal

**Append Enter** (checkbox):
- Default: checked (most snippets are commands)
- When checked, `\n` is appended after the text

**Scope** (dropdown):
- Uses `wa-select` + `wa-option` pattern (consistent with existing dropdowns in the app)
- First option: "All projects" (no color dot)
- Then: each project, rendered with `ProjectBadge` inline (colored dot + display name). Projects are listed alphabetically by display name. Only non-stale projects are shown.
- **Default on Add**: the current project (the project whose terminal is being viewed)
- **Default on Duplicate**: the scope of the source snippet being duplicated
- **Default on Edit**: the current scope (cannot change scope while editing — this would be a move, which is better done via duplicate + delete)

Actually, changing scope on edit IS allowed — it moves the snippet from one scope to another. This is simpler than requiring duplicate + delete.

#### Buttons

- **Cancel**: returns to the list
- **Save**: validates and saves

#### Validation

- Label is required (non-empty after trim)
- Text is required (non-empty after trim)
- Label uniqueness: warn if a snippet with the same label already exists in the same scope (but don't block — user might want duplicates)

## Backend API

### Endpoint: Terminal Config

A new **WebSocket message** type (consistent with how `synced_settings` works — no REST endpoint):

#### Read (on connect)

When a WebSocket client connects, the backend reads `~/.twicc/terminal-config.json` (if it exists) and sends:

```json
{
  "type": "terminal_config_updated",
  "config": {
    "combos": [...],
    "snippets": { "global": [...], "project:xxx": [...] }
  }
}
```

If the file doesn't exist, send an empty config: `{ "combos": [], "snippets": {} }`.

#### Write (on client update)

Client sends:

```json
{
  "type": "update_terminal_config",
  "config": {
    "combos": [...],
    "snippets": { ... }
  }
}
```

Backend writes to `~/.twicc/terminal-config.json` and broadcasts `terminal_config_updated` to all connected clients (same as `synced_settings_updated`).

### Path resolution

Add a new function in `paths.py`:

```python
def get_terminal_config_path() -> Path:
    return get_data_dir() / "terminal-config.json"
```

### Backend implementation

Add two new functions in a new file `src/twicc/terminal_config.py` (or in `synced_settings.py` if we want to keep it simple):

```python
def read_terminal_config() -> dict:
    """Read terminal-config.json, return {} if not found."""

def write_terminal_config(config: dict) -> None:
    """Write terminal-config.json atomically."""
```

Use `orjson` for JSON parsing (consistent with project conventions).

### WebSocket handler

In `asgi.py`, add handlers for the new message type in the `receive_json` method:
- On `"update_terminal_config"`: call `write_terminal_config()`, then broadcast `"terminal_config_updated"` to the channel group.
- On connect: include `terminal_config_updated` in the initial data burst sent to the client.

## Frontend Store

### New store: `terminalConfig.js` (or extend `settings.js`)

A new Pinia store `useTerminalConfigStore` to manage the config state:

```javascript
state: () => ({
    combos: [],
    snippets: {},  // { global: [], "project:xxx": [] }
}),

getters: {
    // Get snippets for the bar display: global + current project
    getSnippetsForProject: (state) => (projectId) => {
        const global = state.snippets.global || []
        const projectKey = `project:${projectId}`
        const project = state.snippets[projectKey] || []
        return [...global, ...project]
    },

    // For the Manage dialog: all snippets grouped
    getAllSnippetsGrouped: (state) => { ... },

    // Does the user have any snippets? (for visibility logic)
    hasSnippetsForProject: (state) => (projectId) => {
        const global = state.snippets.global || []
        const projectKey = `project:${projectId}`
        const project = state.snippets[projectKey] || []
        return global.length > 0 || project.length > 0
    },
},

actions: {
    // Called when receiving terminal_config_updated from WebSocket
    applyConfig(config) { ... },

    // Mutations that send updates via WebSocket
    addCombo(combo) { ... },
    updateCombo(index, combo) { ... },
    deleteCombo(index) { ... },
    reorderCombo(fromIndex, toIndex) { ... },

    addSnippet(scope, snippet) { ... },
    updateSnippet(scope, index, snippet) { ... },
    deleteSnippet(scope, index) { ... },
    reorderSnippet(scope, fromIndex, toIndex) { ... },
    moveSnippet(fromScope, fromIndex, toScope) { ... },
}
```

Each mutation action updates local state immediately (optimistic), then sends `update_terminal_config` via WebSocket. The broadcast will confirm the update to all clients.

## ExtraKeysBar Component Changes

### New props

| Prop | Type | Description |
|------|------|-------------|
| `isTouchDevice` | Boolean | Whether the device is touch. Determines which tabs to show. |
| `combos` | Array | Custom combos from the store. |
| `snippets` | Array | Snippets for the current project (global + project, already merged). |
| `currentProjectId` | String | Current project ID, for the snippet scope default. |

### Removed prop

The `v-if="settingsStore.isTouchDevice"` on `TerminalPanel.vue` is replaced by the new visibility logic (touch OR has snippets).

### Tab computation

```javascript
const tabs = computed(() => {
    if (props.isTouchDevice) {
        return ['essentials', 'more', 'fkeys', 'custom', 'snippets']
    } else {
        return ['snippets']
    }
})
```

When `tabs` has a single entry, the tab bar row is not rendered.

### New emits

- `manage-combos` — emitted when the ⚙ Manage button on Custom tab is clicked
- `manage-snippets` — emitted when the ⚙ Manage button on Snippets tab is clicked
- `combo-press(combo)` — emitted when a custom combo button is pressed
- `snippet-press(snippet)` — emitted when a snippet button is pressed

### Dynamic key rendering

The Custom and Snippets tabs render their keys dynamically from props instead of static `TABS` definitions:

```javascript
// In the template, for Custom tab:
// Render each combo as a button (label or auto-notation)
// + the Manage button at the end

// For Snippets tab:
// Render each snippet as a button (label)
// + the Manage button at the end
// If empty: "No snippets" + Manage button (or nothing on desktop)
```

## New Components

### `ManageCombosDialog.vue`

A `wa-dialog` component for managing custom combos.

**Views:**
1. **List view** (default): shows all combos with reorder/edit/duplicate/delete
2. **Form view**: add or edit a combo (replaces the list in-place within the dialog)

**Props:**
- `combos`: Array — current combos list

**Emits:**
- `update:combos` — emits the full updated combos array on any change

**Internal state:**
- `view`: `'list'` | `'form'`
- `editIndex`: number | null (null = adding new, number = editing existing)
- `formData`: the combo being edited

**Exposed:**
- `open()` / `close()` — called from parent

### `ManageSnippetsDialog.vue`

A `wa-dialog` component for managing snippets.

**Views:**
1. **List view**: snippets grouped by scope, with reorder/edit/duplicate/delete
2. **Form view**: add or edit a snippet

**Props:**
- `snippets`: Object — the full snippets object `{ global: [], "project:xxx": [] }`
- `currentProjectId`: String — for default scope on add
- `projects`: Array — list of projects for the scope dropdown

**Emits:**
- `update:snippets` — emits the full updated snippets object on any change

**Internal state:**
- `view`: `'list'` | `'form'`
- `editScope`: string | null — scope of snippet being edited
- `editIndex`: number | null
- `formData`: the snippet being edited
- `isDuplicate`: boolean — true when duplicating (affects scope default)

**Exposed:**
- `open()` / `close()`

## Execution in useTerminal.js

### New function: `handleComboPress(combo)`

```javascript
function handleComboPress(combo) {
    for (const step of combo.steps) {
        // Build synthetic event for this step
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
            // Plain character, possibly with Alt prefix
            const char = step.key
            if (syntheticEvent.altKey) {
                wsSend({ type: 'input', data: `\x1b${char}` })
            } else {
                wsSend({ type: 'input', data: char })
            }
        }
    }
    terminal?.focus()
    // Do NOT reset one-shot modifiers — combos are self-contained
}
```

### New function: `handleSnippetPress(snippet)`

```javascript
function handleSnippetPress(snippet) {
    let text = snippet.text
    if (snippet.appendEnter) {
        text += '\n'
    }
    wsSend({ type: 'input', data: text })
    terminal?.focus()
    // Do NOT reset one-shot modifiers — snippets are self-contained
}
```

Both functions are exposed from `useTerminal` and wired to the new emits in `TerminalPanel.vue`.

## Files to Create

| File | Purpose |
|------|---------|
| `src/twicc/terminal_config.py` | Backend read/write for `terminal-config.json` |
| `frontend/src/stores/terminalConfig.js` | Pinia store for combos & snippets |
| `frontend/src/components/ManageCombosDialog.vue` | Dialog for managing custom combos |
| `frontend/src/components/ManageSnippetsDialog.vue` | Dialog for managing snippets |

## Files to Modify

| File | Changes |
|------|---------|
| `src/twicc/paths.py` | Add `get_terminal_config_path()` |
| `src/twicc/asgi.py` | Add WS handlers for `update_terminal_config` and include config in connect burst |
| `frontend/src/composables/useTerminal.js` | Add `handleComboPress()`, `handleSnippetPress()`, expose them |
| `frontend/src/composables/useWebSocket.js` | Handle `terminal_config_updated` message, wire to store |
| `frontend/src/components/ExtraKeysBar.vue` | Add Custom/Snippets tabs, dynamic rendering, new props/emits, Manage buttons |
| `frontend/src/components/TerminalPanel.vue` | Update visibility logic, pass new props, wire new emits to handlers and dialogs |

## Out of Scope

- Drag & drop reordering (using arrows ↑↓ instead — simpler, works on mobile)
- Import/export of combos and snippets
- Sharing combos/snippets between TwiCC instances (handled naturally by the file-based storage in `~/.twicc/`)
- Combo recording ("press keys and we capture them") — could be a future enhancement
