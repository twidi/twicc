# Replace Monaco Editor with CodeMirror 6

**Date:** 2026-03-24
**Status:** Draft
**Branch:** `feature/replace-monaco-with-codemirror`

## Motivation

Monaco Editor has critical limitations in TwiCC:

- **No mobile support** — unusable on touch devices
- **CDN dependency** — requires internet access, cannot be bundled
- **Massive payload** — 2.4–5+ MB loaded from jsDelivr
- **Workarounds accumulated** — diff navigation bug (infinite loop with `hideUnchangedRegions`), `domReadOnly` hack for mobile keyboard suppression, manual language detection for diff editor, model cache sentinel paths

CodeMirror 6 is designed mobile-first (~135 KB gzipped), tree-shakeable, and bundleable via npm/Vite. It provides a full editor, side-by-side diff (`MergeView`), and unified inline diff (`unifiedMergeView`), all with editable support.

## Scope

**In scope:**
- Remove `@guolao/vue-monaco-editor` dependency and CDN loading
- Remove Monaco theme JSON files
- Add CodeMirror 6 packages via npm
- Create 2 Vue wrapper components + 1 shared composable
- Migrate `FilePane.vue` to use new CM6 components
- Migrate `JsonHumanView.vue` to use new CM6 components
- Update `main.js` (remove Monaco plugin registration)
- Adapt language detection strategy for CM6
- Use community GitHub Dark/Light themes for CM6

**Out of scope:**
- Changes to `FilesPanel.vue` or `GitPanel.vue` beyond updating any Monaco-specific comments (the interface with `FilePane` via props/events stays the same)
- Changes to the `languages.js` utility (will be reused and extended)
- Changes to xterm.js terminal theming

## Architecture

### New files

```
frontend/src/
├── composables/
│   └── useCodeMirror.js              # Shared CM6 logic
├── components/
│   ├── CodeEditor.vue                # Normal editor (read/write)
│   └── DiffEditor.vue               # Diff editor (side-by-side or unified, via sideBySide prop)
```

### Removed files

```
frontend/src/assets/monaco-themes/    # Entire directory
```

### npm packages

**Add:**
- `codemirror` — meta-package (basic setup + core re-exports)
- `@codemirror/state` — editor state, compartments, transactions
- `@codemirror/view` — editor view, decorations, keymaps
- `@codemirror/merge` — MergeView + unifiedMergeView
- `@codemirror/lang-javascript` — JS/TS/JSX/TSX
- `@codemirror/lang-python`
- `@codemirror/lang-html` — HTML/Vue/Svelte
- `@codemirror/lang-css`
- `@codemirror/lang-json`
- `@codemirror/lang-markdown`
- `@codemirror/lang-rust`
- `@codemirror/lang-java`
- `@codemirror/lang-cpp` — C/C++
- `@codemirror/lang-php`
- `@codemirror/lang-xml`
- `@codemirror/lang-sql`
- `@codemirror/language` — needed for `StreamLanguage.define()` with legacy modes
- `@codemirror/legacy-modes` — stream parsers for ~80+ additional languages (go, yaml, ruby, shell, dockerfile, lua, kotlin, scss, sass, etc.)
- `@uiw/codemirror-theme-github` — GitHub Dark/Light themes

**Remove:**
- `@guolao/vue-monaco-editor`

---

## Component Design

### `useCodeMirror.js` — Shared composable

#### Responsibilities

1. **Language resolution** — Given a file path or language ID, return the appropriate CM6 language extension. Uses lazy `import()` for each language package to minimize initial bundle size.

2. **Theme management** — Expose a reactive theme extension that follows `settingsStore.getEffectiveTheme`. Override the editor background color to match `--wa-color-surface-default` (currently `#1b2733` for dark).

3. **Font size management** — Expose a reactive font size extension that follows `settingsStore.getFontSize`. Applied via `EditorView.theme({ '.cm-content': { fontSize: '...' }, '.cm-gutters': { fontSize: '...' } })` in its own compartment, reconfigured when the setting changes.

4. **Compartment helpers** — Factory for creating and reconfiguring compartments cleanly.

5. **Common extensions builder** — Build the standard extension set (theme, fontSize, language, readOnly, editable, keymap, lineWrapping, highlightActiveLine, etc.) with compartments for dynamic reconfiguration. `highlightActiveLine()` is included only when `editable` is true.

#### Language mapping strategy

The existing `languages.js` maps file extensions → shiki language IDs. We add a new mapping from shiki IDs → CM6 language loader functions:

```javascript
// Helper for legacy mode loading
const legacyMode = (modPath, exportName) => async () => {
    const [mod, { StreamLanguage }] = await Promise.all([
        import(`@codemirror/legacy-modes/mode/${modPath}`),
        import('@codemirror/language'),
    ])
    return StreamLanguage.define(mod[exportName])
}

const CM6_LANGUAGE_MAP = {
    // First-party Lezer-based packages (best quality)
    'javascript': () => import('@codemirror/lang-javascript').then(m => m.javascript({ jsx: true })),
    'typescript': () => import('@codemirror/lang-javascript').then(m => m.javascript({ jsx: true, typescript: true })),
    'tsx':        () => import('@codemirror/lang-javascript').then(m => m.javascript({ jsx: true, typescript: true })),
    'jsx':        () => import('@codemirror/lang-javascript').then(m => m.javascript({ jsx: true })),
    'python':     () => import('@codemirror/lang-python').then(m => m.python()),
    'html':       () => import('@codemirror/lang-html').then(m => m.html()),
    'vue':        () => import('@codemirror/lang-html').then(m => m.html()),
    'svelte':     () => import('@codemirror/lang-html').then(m => m.html()),
    'css':        () => import('@codemirror/lang-css').then(m => m.css()),
    'json':       () => import('@codemirror/lang-json').then(m => m.json()),
    'jsonc':      () => import('@codemirror/lang-json').then(m => m.json()),
    'markdown':   () => import('@codemirror/lang-markdown').then(m => m.markdown()),
    'rust':       () => import('@codemirror/lang-rust').then(m => m.rust()),
    'java':       () => import('@codemirror/lang-java').then(m => m.java()),
    'cpp':        () => import('@codemirror/lang-cpp').then(m => m.cpp()),
    'c':          () => import('@codemirror/lang-cpp').then(m => m.cpp()),
    'php':        () => import('@codemirror/lang-php').then(m => m.php()),
    'xml':        () => import('@codemirror/lang-xml').then(m => m.xml()),
    'sql':        () => import('@codemirror/lang-sql').then(m => m.sql()),

    // Legacy stream parsers (good quality, covers remaining common languages)
    'go':         legacyMode('go', 'go'),
    'yaml':       legacyMode('yaml', 'yaml'),
    'ruby':       legacyMode('ruby', 'ruby'),
    'bash':       legacyMode('shell', 'shell'),
    'zsh':        legacyMode('shell', 'shell'),
    'shell':      legacyMode('shell', 'shell'),
    'shellscript': legacyMode('shell', 'shell'),
    'dockerfile': legacyMode('dockerfile', 'dockerFile'),
    'lua':        legacyMode('lua', 'lua'),
    'kotlin':     legacyMode('clike', 'kotlin'),
    'swift':      legacyMode('swift', 'swift'),
    'scss':       legacyMode('css', 'sCSS'),
    'sass':       legacyMode('sass', 'sass'),
    'less':       legacyMode('css', 'less'),
    'perl':       legacyMode('perl', 'perl'),
    'r':          legacyMode('r', 'r'),
    'scala':      legacyMode('clike', 'scala'),
    'clojure':    legacyMode('clojure', 'clojure'),
    'haskell':    legacyMode('haskell', 'haskell'),
    'erlang':     legacyMode('erlang', 'erlang'),
    'toml':       legacyMode('toml', 'toml'),
    'diff':       legacyMode('diff', 'diff'),
    'powershell': legacyMode('powershell', 'powerShell'),
    'graphql':    legacyMode('simple-mode', 'simpleMode'), // fallback
}
```

For languages not in the map, the editor renders without syntax highlighting (plain text). This covers all common languages encountered in Claude Code sessions. The `legacyMode` helper lazily loads both the mode and `StreamLanguage` only when needed.

The `getLanguageFromPath` function from `languages.js` is reused as-is — it returns a shiki ID, which we map through `CM6_LANGUAGE_MAP`.

#### Theme strategy

Use `@uiw/codemirror-theme-github` which provides `githubDark` and `githubLight` themes. Override the background color to match the app's surface color:

```javascript
import { githubDark, githubLight } from '@uiw/codemirror-theme-github'
import { EditorView } from '@codemirror/view'

function createTheme(isDark) {
    const base = isDark ? githubDark : githubLight
    const bgOverride = EditorView.theme({
        '&': { backgroundColor: isDark ? '#1b2733' : undefined },
        '.cm-gutters': { backgroundColor: isDark ? '#1b2733' : undefined },
    })
    return [base, bgOverride]
}
```

The composable watches `settingsStore.getEffectiveTheme` and reconfigures the theme compartment reactively.

---

### `CodeEditor.vue` — Normal editor

Wraps a single `EditorView`.

#### Props

| Prop | Type | Default | Purpose |
|------|------|---------|---------|
| `modelValue` | `String` | `''` | Document content (v-model) |
| `filePath` | `String` | `null` | For automatic language detection |
| `language` | `String` | `null` | Explicit language override (shiki ID) |
| `readOnly` | `Boolean` | `false` | When true, prevents content mutations AND disables focus/cursor/mobile keyboard. Internally sets both `EditorState.readOnly` and `EditorView.editable.of(false)`. |
| `wordWrap` | `Boolean` | `false` | Line wrapping |
| `lineNumbers` | `Boolean` | `true` | Show line number gutter |
| `saveViewState` | `Boolean` | `false` | Preserve scroll/cursor across content changes via `filePath` key |
| `extensions` | `Array` | `[]` | Additional CM6 extensions from parent |

**Note on `readOnly`:** A single prop controls both `EditorState.readOnly` (prevents mutations) and `EditorView.editable` (controls focus/cursor). In FilePane, read-only files should not show a cursor or trigger mobile keyboards. In the rare case where a consumer needs readOnly content but still wants cursor/selection (e.g., for copy), they can pass `EditorView.editable.of(true)` via the `extensions` prop to override.

#### Events

| Event | Payload | Purpose |
|-------|---------|---------|
| `update:modelValue` | `String` | Content changed |
| `save` | — | Ctrl+S pressed (only when editable and content changed) |
| `ready` | `{ view }` | Editor mounted |

#### Expose

| Name | Type | Purpose |
|------|------|---------|
| `view` | `EditorView` | Direct access to CM6 view |
| `isDirty` | `Ref<Boolean>` | Whether content has changed since last save/reset |
| `resetDirty()` | `Function` | Reset dirty tracking (called after save) |
| `focus()` | `Function` | Focus the editor |

#### Dirty tracking strategy

CM6 does not have Monaco's `getAlternativeVersionId()`. We use a boolean ref toggled by the update listener, with a full string comparison only at reset time:

```javascript
let cleanDoc = ''  // snapshot of doc at last save/reset
const isDirty = ref(false)

// In the updateListener — lightweight, just sets a flag
EditorView.updateListener.of((update) => {
    if (update.docChanged) {
        isDirty.value = true  // fast: no string allocation
    }
})

// Called after save or on initial load
function resetDirty() {
    cleanDoc = view.value.state.doc.toString()
    isDirty.value = false
}
```

This is undo-aware in spirit — if the user types and undoes, the doc content matches `cleanDoc` but `isDirty` stays `true`. This is acceptable because the save operation is idempotent (saving an unchanged file is harmless). The alternative (per-keystroke string comparison) is too expensive for large files.

#### Ctrl+S keybinding

Registered via CM6's `keymap` extension:

```javascript
keymap.of([{
    key: 'Mod-s',
    run() {
        if (!readOnly && isDirty) emit('save')
        return true  // prevent browser default
    }
}])
```

#### View state persistence

When `saveViewState` is true and `filePath` changes, the editor saves scroll position and selection for the old path in a `Map<string, ViewStateSnapshot>`, then restores them for the new path if previously saved. This replicates Monaco's `saveViewState: true` behavior.

```javascript
const viewStateCache = new Map()

function saveViewStateForPath(path) {
    if (!view.value || !path) return
    viewStateCache.set(path, {
        scroll: view.value.scrollDOM.scrollTop,
        selection: view.value.state.selection,
    })
}

function restoreViewStateForPath(path) {
    const cached = viewStateCache.get(path)
    if (!cached || !view.value) return
    view.value.scrollDOM.scrollTop = cached.scroll
    if (cached.selection) {
        view.value.dispatch({ selection: cached.selection })
    }
}
```

#### Content updates

An internal flag (`_internalUpdate`) prevents echo loops between the watcher and the updateListener.

When `modelValue` changes externally (file switch, reload), the editor content is replaced via a transaction — the view is never destroyed:

```javascript
let _internalUpdate = false

watch(() => props.modelValue, (newVal) => {
    if (_internalUpdate || !view.value) return
    if (newVal !== view.value.state.doc.toString()) {
        view.value.dispatch({
            changes: { from: 0, to: view.value.state.doc.length, insert: newVal }
        })
    }
})
```

When the user edits inside the editor, an `updateListener` emits `update:modelValue` with the flag set:

```javascript
EditorView.updateListener.of((update) => {
    if (update.docChanged) {
        _internalUpdate = true
        emit('update:modelValue', update.state.doc.toString())
        nextTick(() => { _internalUpdate = false })
    }
})
```

---

### `DiffEditor.vue` — Diff editor (side-by-side and unified)

A single component that handles both side-by-side (`MergeView`) and unified inline (`EditorView` + `unifiedMergeView` extension) diff modes, controlled by the `sideBySide` prop.

#### Props

| Prop | Type | Default | Purpose |
|------|------|---------|---------|
| `original` | `String` | `''` | Left/original side content |
| `modified` | `String` | `''` | Right/modified side content |
| `filePath` | `String` | `null` | For language detection (both sides) |
| `language` | `String` | `null` | Explicit language override |
| `readOnly` | `Boolean` | `true` | If false, modified side is editable |
| `wordWrap` | `Boolean` | `false` | Line wrapping |
| `sideBySide` | `Boolean` | `true` | `true` = MergeView (two panels), `false` = unified inline view |
| `collapseUnchanged` | `Boolean` | `true` | Collapse unchanged regions |
| `extensions` | `Array` | `[]` | Additional CM6 extensions |

#### Events

| Event | Payload | Purpose |
|-------|---------|---------|
| `update:modified` | `String` | Modified side content changed |
| `save` | — | Ctrl+S on modified side |
| `ready` | `{ view }` | Editor/MergeView mounted |

#### Expose

| Name | Type | Purpose |
|------|------|---------|
| `goToNextChunk()` | `Function` | Navigate to next diff chunk |
| `goToPreviousChunk()` | `Function` | Navigate to previous diff chunk |
| `isDirty` | `Ref<Boolean>` | Whether modified content has changed |

#### Internal architecture

The component maintains one of two internal states depending on `sideBySide`:

**Side-by-side mode (`sideBySide: true`):** Creates a `MergeView` where:
- `.a` is the original side (always readOnly)
- `.b` is the modified side (editable when `readOnly` is false)

**Unified mode (`sideBySide: false`):** Creates a regular `EditorView` with the `unifiedMergeView` extension in a compartment.

#### Diff navigation

CM6 `@codemirror/merge` provides `goToNextChunk` and `goToPreviousChunk` as commands (functions that take an `EditorView` and return `boolean`). This eliminates the Monaco workaround entirely:

```javascript
import { goToNextChunk, goToPreviousChunk } from '@codemirror/merge'

// In side-by-side mode, dispatch on the modified editor view (.b)
function goToNext() {
    goToNextChunk(mergeView.b)  // pass EditorView directly
}
function goToPrev() {
    goToPreviousChunk(mergeView.b)
}

// In unified mode, dispatch on the single editor view
function goToNext() {
    goToNextChunk(view.value)
}
```

#### Content updates

When `original` or `modified` props change (file switch), the strategy differs by mode:

**Side-by-side mode:** Destroy and recreate the `MergeView`. `MergeView.reconfigure()` only supports config options, not document replacement. This matches the current Monaco behavior (GitPanel's diff editor is `v-if` gated and recreated per file anyway).

**Unified mode:** Reconfigure the `unifiedMergeView` compartment with new original content, and dispatch a document replacement transaction for the modified content:

```javascript
// Update original via compartment reconfiguration
view.value.dispatch({
    effects: mergeCompartment.reconfigure(
        unifiedMergeView({ original: newOriginal, ... })
    )
})

// Update modified via document replacement
view.value.dispatch({
    changes: { from: 0, to: view.value.state.doc.length, insert: newModified }
})
```

#### Mode switching (`sideBySide` toggle)

When `sideBySide` changes, the current view is destroyed and a new one is created in the other mode. This is an infrequent user action so the cost is negligible. Editor state (scroll, cursor) is not preserved across mode switches — this matches Monaco's current behavior.

---

## Migration: `FilePane.vue`

### Current structure (Monaco)

FilePane uses two distinct Monaco components (`vue-monaco-editor` and `vue-monaco-diff-editor`) toggled by `diffMode` prop. The normal editor is kept alive with `v-show`; the diff editor uses `v-if`.

### New structure (CM6)

Same pattern, using the new components:

```html
<!-- Normal mode -->
<CodeEditor
    v-if="!diffMode"
    v-show="showEditor && !showMarkdownPreview"
    v-model="currentContent"
    :file-path="filePath"
    :read-only="!isEditing"
    :word-wrap="wordWrap"
    :line-numbers="true"
    :save-view-state="true"
    @save="save"
    @ready="onEditorReady"
/>

<!-- Diff mode -->
<DiffEditor
    v-if="diffMode"
    :original="originalContent"
    :modified="modifiedContent"
    :file-path="filePath"
    :read-only="diffReadOnly"
    :word-wrap="wordWrap"
    :side-by-side="sideBySide"
    :collapse-unchanged="true"
    @update:modified="onDiffModifiedChange"
    @save="save"
    @ready="onDiffReady"
/>
```

### What gets removed from FilePane

- `import { useMonaco } from '@guolao/vue-monaco-editor'`
- Theme JSON imports and `defineTheme()` calls
- `monacoRef` and all code gated on it
- `diffLanguage` computed (CM6 handles this internally)
- `monacoPath` computed and sentinel path logic
- `editorOptions` / `diffEditorOptions` computed objects
- `onEditorMount` / `onDiffEditorMount` handlers (replaced by simpler `onReady`)
- Diff navigation workaround (replaced by CM6's `goToNextChunk`/`goToPreviousChunk`)
- `registerSaveAction` (replaced by CM6's `keymap`)
- Minimap logic and `editorAreaWidth` tracking for minimap (CM6 has no minimap — not needed)
- Side-by-side breakpoint tracking via `editorAreaWidth` (no longer needed — `DiffEditor` handles mode internally via `sideBySide` prop)
- Mobile `domReadOnly` workaround (replaced by CM6's `editable` extension)
- `ResizeObserver` for minimap threshold (can be removed or repurposed)

### What stays in FilePane

- All file loading/saving logic (fetch, save, revert)
- Binary file detection
- Markdown preview toggle
- Edit mode toggle
- Word wrap / side-by-side toggle UI
- Dirty detection (delegated to the component's `isDirty` expose)
- DOM structure and toolbar
- All props/events interface with parents (FilesPanel, GitPanel)

### DOM reparenting

The DOM reparenting pattern in `FilesPanel.vue` and `GitPanel.vue` is **not affected**. It moves the FilePane DOM node, which will now contain CM6 DOM instead of Monaco DOM. CM6 handles DOM moves gracefully — the editor view remains functional after being reparented (unlike Monaco which sometimes needed `layout()` calls).

The `automaticLayout` feature from Monaco is replaced by CM6's built-in resize handling (it uses `ResizeObserver` internally by default).

---

## Migration: `JsonHumanView.vue`

### Current structure (Monaco)

Uses `vue-monaco-editor` for inline code/markdown/multiline editing, and `VueMonacoDiffEditor` for old/new diff pairs.

### New structure (CM6)

```html
<!-- Inline editor for code/markdown/multiline -->
<CodeEditor
    v-model="editValue"
    :language="resolvedLanguage"
    :line-numbers="false"
    :word-wrap="true"
    :extensions="compactEditorExtensions"
    :style="{ height: computedHeight }"
/>

<!-- Diff editor for old/new pairs -->
<DiffEditor
    v-show="showDiffMode"
    :original="oldValue"
    :modified="newValue"
    :language="resolvedLanguage"
    :read-only="false"
    :word-wrap="true"
    :side-by-side="true"
    @update:modified="onNewValueChange"
/>
```

### What changes

- Remove `useMonaco` import and `monacoRef`
- Remove `SHIKI_TO_MONACO` mapping (replaced by `CM6_LANGUAGE_MAP` in composable)
- Remove `monacoPath()` synthetic path builder (CM6 takes a language extension, not a file path for model URI)
- Remove `monacoEditOptions` (replaced by `compactEditorExtensions` passed via props)
- Remove theme registration (handled by composable)
- Remove `monacoHeight` (still need height calculation, but CM6 can auto-size or use a simpler approach)
- The `v-show` pattern for keeping the diff editor alive is preserved

### Compact editor configuration

For `JsonHumanView`'s inline editors, we pass extra extensions to strip chrome:

```javascript
const compactEditorExtensions = [
    EditorView.theme({
        '.cm-gutters': { display: 'none' },
        '.cm-content': { padding: '16px' },
    }),
]
```

Combined with `lineNumbers: false` prop on `CodeEditor`.

---

## Migration: `main.js`

### Remove

```javascript
import { install as VueMonacoEditorPlugin } from '@guolao/vue-monaco-editor'
// ...
app.use(VueMonacoEditorPlugin, { paths: { vs: '...' } })
```

### Add

Nothing — CM6 components are imported locally where used. No global plugin registration needed.

---

## What gets removed entirely

| Item | Location |
|------|----------|
| `@guolao/vue-monaco-editor` package | `package.json` |
| Monaco CDN reference | `main.js` |
| Monaco plugin registration | `main.js` |
| `github-dark.json` theme file | `assets/monaco-themes/` |
| `github-light.json` theme file | `assets/monaco-themes/` |
| `assets/monaco-themes/` directory | entire directory |
| `SHIKI_TO_MONACO` mapping | `JsonHumanView.vue` |
| `monacoPath()` helpers | `FilePane.vue`, `JsonHumanView.vue` |
| `monacoHeight()` | `JsonHumanView.vue` |
| Diff navigation workaround | `FilePane.vue` |
| `domReadOnly` mobile workaround | `FilePane.vue` |
| `diffLanguage` computed | `FilePane.vue` |
| `editorAreaWidth` / ResizeObserver for minimap | `FilePane.vue` |
| Side-by-side breakpoint width tracking | `FilePane.vue` |
| Model cache sentinel path logic | `FilePane.vue` |
| `registerSaveAction` | `FilePane.vue` |
| `monacoEditOptions` | `JsonHumanView.vue` |
| `.monaco-placeholder` CSS class (rename to `.editor-overlay`) | `FilePane.vue` |
| Monaco-specific comments in `GitPanel.vue` | lines mentioning "Monaco" |
| Monaco-specific comments in `FilesPanel.vue` | line mentioning "Monaco editor" |
| Monaco-specific comment in `useTerminal.js` | line about background colors matching "Monaco editor" (NOT the Monaco font name in fontFamily) |
| All other Monaco-specific comments and workaround documentation | `FilePane.vue`, `JsonHumanView.vue` |

## Feature mapping

| Monaco feature | CM6 equivalent | Notes |
|---|---|---|
| `automaticLayout: true` | Built-in `ResizeObserver` | No configuration needed |
| `domReadOnly` (mobile keyboard) | `EditorView.editable.of(false)` | Native CM6 concept |
| `saveViewState: true` | Manual scroll/selection cache in `CodeEditor.vue` | Per-filePath Map |
| `getAlternativeVersionId()` | Doc string comparison | Undo-aware by nature |
| `model.getLineChanges()` + `setScrollTop()` | `goToNextChunk` / `goToPreviousChunk` | No workaround needed |
| `hideUnchangedRegions` | `collapseUnchanged` option in MergeView | Built-in, works correctly |
| `renderSideBySide` toggle | `DiffEditor`'s `sideBySide` prop (destroy/recreate internally) | Single component handles both |
| `useTrueInlineView` | `unifiedMergeView` | Native CM6 feature |
| `showMoves` | Not available in CM6 merge | Acceptable loss |
| Minimap | Not available in CM6 | Acceptable loss — rarely useful |
| `editor.addAction()` for Ctrl+S | `keymap.of([...])` extension | Cleaner API |
| Language auto-detect from file path | `getLanguageFromPath()` → `CM6_LANGUAGE_MAP` | Reuses existing utility |
| `defineTheme()` with JSON | `@uiw/codemirror-theme-github` + override | Community themes |
| `fontSize` setting | `EditorView.theme({ '.cm-content': { fontSize } })` in compartment | Reactive via `settingsStore.getFontSize` |
| `scrollBeyondLastLine: false` | CM6 default behavior (no `scrollPastEnd()` extension) | No action needed |
| `renderLineHighlight` | `highlightActiveLine()` extension | Included only when editable |

## Migration: `JsonHumanView.vue` — Detailed diff behavior

### Diff/split toggle preservation

`JsonHumanView` has a toggle between diff editor and "split mode" (old read-only + new editable as two separate recursive `JsonHumanView` instances). This pattern is unaffected by the migration — the split mode doesn't use a diff editor at all.

The `v-show` pattern to keep the `DiffEditor` alive when toggling to split mode is preserved:

```html
<!-- Diff editor — kept alive with v-show so toggling back is instant -->
<DiffEditor
    v-show="showDiffMode"
    :original="oldValue"
    :modified="newValue"
    :language="resolvedLanguage"
    :read-only="false"
    :word-wrap="true"
    :side-by-side="true"
    @update:modified="onNewValueChange"
/>
```

Since `DiffEditor` is never destroyed (only hidden), the destroy/recreate on content change is not an issue — the content doesn't change while the diff is shown for a given item.

### Wiring `@update:modified` to content propagation

The current `onDiffEditorMount` subscribes to modified editor changes and propagates them upward via `onChildObjectUpdate`. With CM6, this is replaced by the `@update:modified` event:

```javascript
function onNewValueChange(newContent) {
    onChildObjectUpdate(newKey, newContent)
}
```

## Error handling

- If a language extension fails to load (lazy import error), fall back to plain text (no syntax highlighting). Log a warning to console.
- If `MergeView` creation fails, show an error message in the diff area (same pattern as current binary file display).
