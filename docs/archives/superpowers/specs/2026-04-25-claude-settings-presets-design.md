# Claude config presets — design

**Date:** 2026-04-25
**Status:** Proposed

## Summary

Add a global, project-agnostic library of **Claude config presets**. Each preset is a named bundle of Claude session settings (model, context, effort, thinking, permission, claude-in-chrome MCP) where each field is either *unset* (use the global default) or *forced* (override the global default), mirroring the per-session "Default + Force to" semantics already in place in `MessageInput.vue`.

Presets are stored in `<data_dir>/claude-settings-presets.json`, served at app mount time via `/api/bootstrap/`, and synced across connected clients via WebSocket — same pattern as `terminal-config.json`, `message-snippets.json`, `workspaces.json`.

This spec covers **only the management UI** (a two-screen dialog opened from the Claude section of the settings panel). **Applying a preset to a session is out of scope** and will be specified in a follow-up.

## Motivation

Users frequently switch between recurring Claude configurations (e.g. *"opus + max context + high effort + thinking on"* for hard problems vs. *"sonnet + acceptEdits"* for routine work). Today this requires opening the session settings popover and adjusting up to six selects, every time. Presets let the user define those bundles once and reuse them.

Presets are deliberately **not** scoped to a project or workspace: the same bundle (e.g. *"Quick fix"*) is meaningful across all projects, and project-scoping can always be layered on later if needed.

## Non-goals

- **Applying a preset.** No selector, no merge logic with `Session.keep_settings`, no command-palette entry. The dialog only manages the preset library.
- **Importing/exporting presets.** Out of scope.
- **Project-level overrides.** Presets are global only.
- **Extra metadata** (description, color, icon). Each preset is just a name + the six Claude fields. Can be added later without breaking the JSON format.
- **Backend validation of preset values.** The backend persists whatever the client sends, consistent with `terminal-config.json` and `message-snippets.json`. Validation lives in the frontend dialog.

## Data model

### Storage file

Path: `<data_dir>/claude-settings-presets.json` (resolved by a new `get_claude_settings_presets_path()` helper in `src/twicc/paths.py`).

```json
{
  "presets": [
    {
      "name": "Quick fix",
      "model": "claude-sonnet-4-6",
      "context_max": null,
      "effort": null,
      "thinking": null,
      "permission_mode": "acceptEdits",
      "claude_in_chrome": null
    },
    {
      "name": "Hard problem",
      "model": "claude-opus-4-7",
      "context_max": 1000000,
      "effort": "high",
      "thinking": true,
      "permission_mode": null,
      "claude_in_chrome": null
    }
  ]
}
```

### Field semantics

For each Claude field in a preset:

- `null` → "default" → the field is **not forced** by the preset; later, when the preset is applied (out of scope), the global default from the settings panel will be used.
- A non-null value → "force to" → the preset will, when applied, override the global default with this exact value.

This is exactly the semantics of `Session.keep_settings` today (per-session nullable fields in `core/models.py`, surfaced in `MessageInput.vue` with the `__default__` sentinel + "Force to:" group). The set of allowed non-null values per field is identical to what the per-session selects already accept (see "UI: form view" below).

### Identity & ordering

Presets are identified by their **position in the list** (no UUID), like terminal combos. The `name` is a human label and is used as the **uniqueness key** in the management UI (case-insensitive, after `trim()`). Two presets cannot share the same name.

The list order is meaningful: it determines display order in the management dialog and will determine display order in any future "apply preset" picker. Reordering is a first-class action.

## Architecture

```
┌────────────────────────────────────────────┐         ┌──────────────────────────────────┐
│ Frontend                                   │         │ Backend                          │
│                                            │         │                                  │
│  SettingsPopover.vue                       │         │  paths.py                        │
│   └─ "Manage presets…" button              │         │   └─ get_claude_settings_…_path  │
│       │                                    │         │                                  │
│       ▼                                    │         │  claude_settings_presets.py      │
│  ClaudePresetsDialog.vue                   │         │   ├─ read_claude_settings_…      │
│   ├─ list view                             │         │   └─ write_claude_settings_…     │
│   └─ form view (one per preset)            │         │       (atomic tempfile+replace)  │
│                                            │         │                                  │
│  stores/claudeSettingsPresets.js  ◄────────┼─ WS ────┼─►  asgi.py                       │
│   ├─ state: { presets }                    │         │   ├─ on connect: send initial    │
│   ├─ applyConfig(config)                   │         │   ├─ handle update_… message     │
│   ├─ _sendConfig()                         │         │   └─ broadcast to group          │
│   └─ CRUD actions                          │         │                                  │
│                                            │         │  views.py                        │
│  composables/useWebSocket.js               │         │   └─ /api/bootstrap/             │
│   ├─ sendClaudeSettingsPresets             │         │       includes presets           │
│   └─ on 'claude_settings_presets_updated'  │         │                                  │
│                                            │         │                                  │
│  bootstrap injection (main.js)             │         │                                  │
│   └─ applyConfig from bootstrap payload    │         │                                  │
└────────────────────────────────────────────┘         └──────────────────────────────────┘
```

The data flow mirrors **exactly** the `terminal_config.json` pipeline:

1. **Initial mount:** `/api/bootstrap/` returns the file content alongside the other configs. The frontend store is hydrated synchronously before the app mounts.
2. **WebSocket reconnect:** when a client connects to `/ws/`, the server sends a `claude_settings_presets_updated` message with the current file content (in case the file changed between the bootstrap and the WS connect, or for clients that reconnect mid-session).
3. **Mutation:** any CRUD action on the store calls `_sendConfig()`, which sends the **full** new state via `update_claude_settings_presets`. The server writes atomically (tempfile + `os.replace`) and broadcasts `claude_settings_presets_updated` to all connected clients (including the sender, for confirmation), so other devices stay in sync.

There is no per-mutation diff protocol; sending the full `presets` array on every change is consistent with the existing pattern and keeps the protocol trivial.

## Backend

### `src/twicc/paths.py`

Add:

```python
def get_claude_settings_presets_path() -> Path:
    return get_data_dir() / "claude-settings-presets.json"
```

### `src/twicc/claude_settings_presets.py` (new module)

Two functions, modeled exactly on `terminal_config.py`:

- `read_claude_settings_presets() -> dict` — reads the file with `orjson.loads(path.read_bytes())`. Returns `{"presets": []}` if the file does not exist or fails to parse (`FileNotFoundError`, `orjson.JSONDecodeError`).
- `write_claude_settings_presets(config: dict) -> None` — serializes with `orjson.dumps(config, option=orjson.OPT_INDENT_2)`, writes to a tempfile in the same directory, then `os.replace()` for atomicity. Cleans up the tempfile on failure.

The backend does **not** validate the `presets` array shape. It accepts whatever the client sends, just like the other config files.

### `src/twicc/views.py` — `bootstrap()` endpoint

Add an import and one extra key to the `JsonResponse`:

```python
from twicc.claude_settings_presets import read_claude_settings_presets
# ...
"claude_settings_presets": read_claude_settings_presets(),
```

This is the lone integration point that the user explicitly asked for. The frontend already has bootstrap-time hydration for `terminal_config`, `message_snippets`, `workspaces`; we add `claude_settings_presets` next to them.

### `src/twicc/asgi.py`

Two additions, both modeled on the `terminal_config` handlers:

1. **On WS connect** (in the same block that sends `terminal_config_updated`): read the presets file and send `{"type": "claude_settings_presets_updated", "config": <file content>}` to the connecting client.
2. **New incoming message handler** for `update_claude_settings_presets`:
   - Receive `msg["config"]`
   - Call `write_claude_settings_presets(msg["config"])`
   - `group_send` `{"type": "claude_settings_presets_updated", "config": msg["config"]}` to the existing broadcast group, so all clients (including other devices) receive the update.

The handler does not echo back errors to the client. Failure modes are local I/O issues; the existing handlers do not error-report either.

## Frontend

### Store: `frontend/src/stores/claudeSettingsPresets.js` (new)

Calqued on `frontend/src/stores/terminalConfig.js`. Pinia setup-style store with:

- **State:** `presets` (ref array, default `[]`)
- **Actions:**
  - `applyConfig(config)` — replaces `presets` with `config?.presets ?? []`. Called from the bootstrap injection and from the WebSocket handler. Does **not** trigger `_sendConfig()` (no echo loop).
  - `addPreset(preset)` — appends, then `_sendConfig()`.
  - `updatePreset(index, preset)` — replaces at index, then `_sendConfig()`.
  - `deletePreset(index)` — splices, then `_sendConfig()`.
  - `duplicatePreset(index)` — inserts a copy right after, with name suffixed `" (copy)"` and de-duplicated against existing names by appending `" 2"`, `" 3"`, … until unique. Then `_sendConfig()`.
  - `reorderPreset(index, direction)` — swaps with neighbor (`direction: -1 | 1`); no-op at boundaries. Then `_sendConfig()`.
- **Getter:** `findPresetByName(name, excludeIndex = null)` — case-insensitive lookup, used by the dialog for uniqueness validation.
- **Internal:** `_sendConfig()` — lazy-imports `sendClaudeSettingsPresets` from `useWebSocket.js` and calls it with `{ presets: presets.value }`.

### WebSocket glue: `frontend/src/composables/useWebSocket.js`

- Add `sendClaudeSettingsPresets(config)` → `sendWsMessage({ type: 'update_claude_settings_presets', config })`.
- In the `onMessage` switch, add a case for `'claude_settings_presets_updated'` that lazy-imports the store and calls `applyConfig(msg.config)`.

### Bootstrap injection

Wherever the existing bootstrap response is dispatched into the various stores (`terminal_config` → `useTerminalConfigStore().applyConfig(...)`, etc.), add an analogous line:

```js
useClaudeSettingsPresetsStore().applyConfig({
    presets: bootstrap.claude_settings_presets?.presets ?? [],
})
```

The exact file is whatever currently does these dispatches today (likely `main.js` or a dedicated bootstrap module). The implementation plan must locate it before editing.

### Dialog component: `frontend/src/components/ClaudePresetsDialog.vue` (new)

Modeled structurally on `frontend/src/components/TerminalCombosDialog.vue`. Single `<wa-dialog>`, two views toggled via a local `view` ref (`'list' | 'form'`), no router involvement, footer template swapped per view.

#### Props / emits

- Prop: `open: boolean` (v-model).
- Emit: `update:open`.

#### Local state

- `view: 'list' | 'form'`
- `editIndex: number | null` (null = creating a new preset)
- `formData: { name, model, context_max, effort, thinking, permission_mode, claude_in_chrome }`
- `errorMessage: string`
- `warningMessage: string`

When the dialog opens (`@wa-after-show`), reset to list view and clear messages. When `view` switches to `'form'`, focus the name input and `setSelectionRange(len, len)` (cf. the pattern in `ProjectEditDialog.vue`).

#### List view

- If `presets.length === 0`: show an empty-state callout ("No presets yet.").
- Else, render a `.preset-list` containing one `.preset-row` per preset:
  - `.reorder-arrows` with up/down `<wa-button>`s (disabled at boundaries) → calls `reorderPreset`.
  - `.preset-display`:
    - `.preset-name` — the preset's `name` in bold.
    - `.preset-summary` — a one-line recap of forced fields, computed by `formatPresetSummary(preset)`:
      - Filter the six Claude fields, keeping only those whose value is not null.
      - For each, render `<key>: <value-label>` using the same labels as the session settings selects (e.g. `model: opus-4-7`, `effort: high`, `thinking: on`, `permission: acceptEdits`, `chrome: on`).
      - Join with ` · `.
      - If the array is empty, render `all default`.
  - `.preset-actions`:
    - Edit button (`pencil` icon) → `openEditForm(index)`.
    - Duplicate button (`copy` icon) → `duplicatePreset(index)`.
    - Delete button (`trash` icon, `.action-btn-danger`) → `deletePreset(index)`. **No confirmation dialog** — consistent with combos.

#### Form view

A `<form id="preset-form" @submit.prevent="handleSave">` containing:

1. **Name** — `.form-group` with `<wa-input>` (autofocus on open via `@wa-after-show`).

2. **Six Claude field selects**, each in a `.form-group`. Each select uses the `__default__` sentinel pattern from `MessageInput.vue`:
   - `<wa-option value="__default__">Default</wa-option>` (no value suffix — see "Why no 'Default: <value>' label" below).
   - `<small class="select-group-label">Force to:</small>`
   - The explicit options.

   The list of explicit options must come from the existing constants/registries used by `MessageInput.vue` (model registry, `EFFORT`, `CONTEXT_MAX`, `PERMISSION_MODE`, thinking on/off, claude-in-chrome on/off). The implementation plan must reuse those exact sources rather than redefine them, to avoid drift.

3. **Error callout** — `<wa-callout variant="danger" v-if="errorMessage">` for validation errors.

#### Why no "Default: \<value\>" label

In `MessageInput.vue`, the per-session select shows `"Default: <current global default>"` because the session has a live context: a global default exists *now* and the user wants to know what they'd be falling back to. A preset has no such context — it is a definition that will be applied later, when the global default may have changed. Rendering a stale "Default: opus" inside a preset definition would be misleading. The bare label "Default" is honest.

#### Validation in `handleSave`

1. `name = formData.name.trim()`. If empty → `errorMessage = "Name is required"`, abort.
2. Look up `findPresetByName(name, editIndex)`. If found → `errorMessage = "A preset with this name already exists"`, abort.
3. Build the preset payload (with the trimmed name and the current six field values, mapping the `__default__` sentinel back to `null`).
4. Call `addPreset(payload)` (if `editIndex === null`) or `updatePreset(editIndex, payload)`.
5. `view = 'list'`.

#### Footer

```html
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
    <wa-button variant="brand" type="submit" form="preset-form">Save</wa-button>
  </template>
</div>
```

The submit button uses `form="preset-form"` so it can live outside the `<form>` while still triggering its submit. Because `wa-button` does not expose `form` as a prop, the implementation plan must use the `setAttribute('form', 'preset-form')` workaround documented in `CLAUDE.md` ("Dialog Forms Pattern").

#### CSS

The CSS block is a near-copy of `TerminalCombosDialog.vue`'s scoped styles, with **renames**:

| Combos | Presets |
|---|---|
| `.manage-combos-dialog` | `.manage-presets-dialog` |
| `.combo-list` | `.preset-list` |
| `.combo-row` | `.preset-row` |
| `.combo-display` | `.preset-display` |
| `.combo-text` | `.preset-name` |
| `.combo-notation` | `.preset-summary` |
| `.combo-actions` | `.preset-actions` |

Generic dialog/form classes are kept identical: `.dialog-content`, `.dialog-footer`, `.reorder-arrows`, `.reorder-btn`, `.action-btn`, `.action-btn-danger`, `.form-group`, `.form-label`, `.form-hint`, `.select-group-label`.

Combos-specific classes (`.step-wrapper`, `.modifier-row`, `.modifier-btn`, `.key-input-row`, `.key-capture-input`, `.key-picker`, `.picker-key`, `.add-step-btn`, `.step-card`, `.step-header`, `.step-title`, `.step-remove-btn`, `.step-separator`) are dropped — they have no equivalent in the preset form.

The dialog width: `--width: min(40rem, calc(100vw - 2rem))` (same as combos).

### `SettingsPopover.vue` integration

Inside the `<section v-if="activeSection === 'claude'">` block, after the last `setting-group` ("Default Chrome MCP") and before the section's closing `</section>`:

```html
<div class="setting-group">
  <label class="setting-group-label">Presets</label>
  <wa-button size="small" @click="openClaudePresetsDialog">
    <wa-icon slot="start" name="sliders"></wa-icon>
    Manage presets…
  </wa-button>
</div>
```

Setup additions:

- Import `ClaudePresetsDialog`.
- `const claudePresetsDialogOpen = ref(false)`.
- `const openClaudePresetsDialog = () => { claudePresetsDialogOpen.value = true }`.

Mount the dialog at the **template root**, **outside** the `<wa-popover>`, so closing the popover does not unmount the dialog:

```html
<ClaudePresetsDialog v-model:open="claudePresetsDialogOpen" />
```

## Non-functional requirements

- **Atomic writes:** the backend uses tempfile + `os.replace` (already standard in `terminal_config.py`).
- **Multi-device sync:** the WebSocket broadcast covers concurrent edits from different clients (last-write-wins on the file; no CRDT merging — same as the other config files).
- **Bootstrap latency:** adding one extra file read in `/api/bootstrap/` is negligible.
- **HMR:** no new circular import risks — the new store imports the WS composable lazily, like `terminalConfig.js`.

## Edge cases

- **Empty preset list:** the dialog shows an empty-state callout and the "Add preset" button.
- **All fields default:** allowed. The `formatPresetSummary` returns `"all default"`. The preset is still useful as a named "do nothing" reset.
- **Name uniqueness on edit:** the lookup excludes `editIndex`, so saving an unchanged name does not raise a uniqueness error.
- **Duplicate auto-renaming:** if `"Quick fix"` is duplicated and `"Quick fix (copy)"` already exists, append `" 2"`, `" 3"`, … to the suffix until unique.
- **Invalid file content on read:** the backend returns `{"presets": []}` and overwrites with valid JSON on the next write. (Same behavior as `terminal_config.py`.)
- **Unknown future fields:** if a future client version adds a seventh Claude field, an older client reading the file would silently ignore it on the form (and would round-trip it correctly when saving, only if the implementation reads the raw object — not relevant for v1 since we have a fixed six-field shape).

## Out of scope (explicit)

- A "currently active preset" notion. Presets are stateless until applied.
- A way to apply a preset to a session. This will be specified separately.
- A way to edit the global defaults from the preset dialog (those live in the Claude section of the settings panel and stay there).
- Search / filtering inside the preset list. Will be added if the list grows large enough to warrant it.
- Drag-and-drop reordering. Up/down arrow buttons are sufficient (consistent with combos).
- Keyboard shortcuts to open the dialog or apply a preset.

## Files touched

**New**
- `src/twicc/claude_settings_presets.py`
- `frontend/src/stores/claudeSettingsPresets.js`
- `frontend/src/components/ClaudePresetsDialog.vue`

**Modified**
- `src/twicc/paths.py` — add `get_claude_settings_presets_path()`
- `src/twicc/views.py` — `bootstrap()` adds `claude_settings_presets` key
- `src/twicc/asgi.py` — initial WS send + `update_claude_settings_presets` handler + broadcast
- `frontend/src/composables/useWebSocket.js` — `sendClaudeSettingsPresets` + handler case
- `frontend/src/main.js` (or wherever bootstrap dispatches today) — store hydration
- `frontend/src/components/SettingsPopover.vue` — Presets button + dialog mount

## Open questions

None at design-approval time. The implementation plan must:

- locate the exact bootstrap-dispatch site for store hydration;
- enumerate the exact option lists per Claude field by reading the constants currently used by `MessageInput.vue`, to avoid drift.
