# Replace Monaco Editor with CodeMirror 6 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Monaco Editor entirely with CodeMirror 6 for better mobile support, smaller bundle, and elimination of CDN dependency.

**Architecture:** 2 Vue wrapper components (`CodeEditor.vue`, `DiffEditor.vue`) + 1 shared composable (`useCodeMirror.js`). Languages loaded lazily via dynamic `import()`. Theme from community package with background color override.

**Tech Stack:** CodeMirror 6, `@codemirror/merge`, `@uiw/codemirror-theme-github`, `@codemirror/legacy-modes`, Vue 3 Composition API.

**Spec:** `docs/superpowers/specs/2026-03-24-replace-monaco-with-codemirror6-design.md`

**Quality note:** This project uses no tests and no linting (per CLAUDE.md). Skip TDD steps. Verify manually by checking the dev servers.

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `frontend/src/composables/useCodeMirror.js` | Shared CM6 logic: language resolution (lazy-loaded), theme management (reactive to settings), font size, compartment helpers, common extension builder |
| `frontend/src/components/CodeEditor.vue` | Normal code editor wrapper around CM6 `EditorView`. Props: modelValue, filePath, language, readOnly, wordWrap, lineNumbers, saveViewState, extensions. Emits: update:modelValue, save, ready. Exposes: view, isDirty, resetDirty, focus |
| `frontend/src/components/DiffEditor.vue` | Diff editor wrapper. Side-by-side mode = `MergeView`, unified mode = `EditorView` + `unifiedMergeView` extension. Props: original, modified, filePath, language, readOnly, wordWrap, sideBySide, collapseUnchanged, extensions. Emits: update:modified, save, ready. Exposes: goToNextChunk, goToPreviousChunk, isDirty, resetDirty |

### Modified files

| File | What changes |
|------|-------------|
| `frontend/package.json` | Remove `@guolao/vue-monaco-editor`, add CM6 packages |
| `frontend/src/main.js` | Remove Monaco plugin import and registration (lines 42, 55–59) |
| `frontend/src/components/FilePane.vue` | Replace Monaco usage with `CodeEditor`/`DiffEditor`. Remove all Monaco imports, theme registration, options, workarounds. Rename CSS class `.monaco-placeholder` → `.editor-overlay` |
| `frontend/src/components/JsonHumanView.vue` | Replace Monaco usage with `CodeEditor`/`DiffEditor`. Remove `SHIKI_TO_MONACO`, `monacoPath()`, `monacoEditOptions`, theme registration |
| `frontend/src/components/GitPanel.vue` | Update 2 comments (lines 738, 967) |
| `frontend/src/components/FilesPanel.vue` | Update 1 comment (line 458) |
| `frontend/src/composables/useTerminal.js` | Update 1 comment (line 14). Keep the Monaco font name on line 302 |
| `frontend/src/utils/languages.js` | No changes (reused as-is) |

### Deleted files

| File | Reason |
|------|--------|
| `frontend/src/assets/monaco-themes/github-dark.json` | Replaced by `@uiw/codemirror-theme-github` |
| `frontend/src/assets/monaco-themes/github-light.json` | Same |

---

## Task 1: Install CodeMirror 6 packages and remove Monaco

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Remove Monaco package**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/replace-monaco-with-codemirror/frontend
npm uninstall @guolao/vue-monaco-editor
```

- [ ] **Step 2: Install CodeMirror 6 packages**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/replace-monaco-with-codemirror/frontend
npm install codemirror @codemirror/state @codemirror/view @codemirror/merge @codemirror/language @codemirror/lang-javascript @codemirror/lang-python @codemirror/lang-html @codemirror/lang-css @codemirror/lang-json @codemirror/lang-markdown @codemirror/lang-rust @codemirror/lang-java @codemirror/lang-cpp @codemirror/lang-php @codemirror/lang-xml @codemirror/lang-sql @codemirror/legacy-modes @uiw/codemirror-theme-github
```

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "deps: replace Monaco Editor with CodeMirror 6 packages"
```

---

## Task 2: Create `useCodeMirror.js` composable

**Files:**
- Create: `frontend/src/composables/useCodeMirror.js`
- Reference: `frontend/src/utils/languages.js` (reuse `getLanguageFromPath`)
- Reference: `frontend/src/stores/settings.js` (for `getEffectiveTheme`, `getFontSize`)

This is the foundation — all CM6 components depend on it.

- [ ] **Step 1: Create the composable**

Create `frontend/src/composables/useCodeMirror.js` with these sections:

**a) Language map** — `CM6_LANGUAGE_MAP` object mapping shiki language IDs to async loader functions. Each loader returns a CM6 `LanguageSupport` or `StreamLanguage` instance.

First-party Lezer packages (static imports would be fine but use lazy `import()` for bundle optimization):
- `javascript` → `@codemirror/lang-javascript` with `jsx: true`
- `typescript` → `@codemirror/lang-javascript` with `jsx: true, typescript: true`
- `tsx` → same as typescript
- `jsx` → same as javascript
- `python` → `@codemirror/lang-python`
- `html`, `vue`, `svelte` → `@codemirror/lang-html`
- `css` → `@codemirror/lang-css`
- `json`, `jsonc` → `@codemirror/lang-json`
- `markdown` → `@codemirror/lang-markdown`
- `rust` → `@codemirror/lang-rust`
- `java` → `@codemirror/lang-java`
- `cpp`, `c` → `@codemirror/lang-cpp`
- `php` → `@codemirror/lang-php`
- `xml` → `@codemirror/lang-xml`
- `sql` → `@codemirror/lang-sql`

Legacy modes (via `@codemirror/legacy-modes` + `StreamLanguage.define()`):
- `go` → `@codemirror/legacy-modes/mode/go` export `go`
- `yaml` → `@codemirror/legacy-modes/mode/yaml` export `yaml`
- `ruby` → `@codemirror/legacy-modes/mode/ruby` export `ruby`
- `bash`, `zsh`, `shell`, `shellscript` → `@codemirror/legacy-modes/mode/shell` export `shell`
- `dockerfile` → `@codemirror/legacy-modes/mode/dockerfile` export `dockerFile`
- `lua` → `@codemirror/legacy-modes/mode/lua` export `lua`
- `kotlin` → `@codemirror/legacy-modes/mode/clike` export `kotlin`
- `swift` → `@codemirror/legacy-modes/mode/swift` export `swift`
- `scss` → `@codemirror/legacy-modes/mode/css` export `sCSS`
- `sass` → `@codemirror/legacy-modes/mode/sass` export `sass`
- `less` → `@codemirror/legacy-modes/mode/css` export `less`
- `perl` → `@codemirror/legacy-modes/mode/perl` export `perl`
- `r` → `@codemirror/legacy-modes/mode/r` export `r`
- `scala` → `@codemirror/legacy-modes/mode/clike` export `scala`
- `clojure` → `@codemirror/legacy-modes/mode/clojure` export `clojure`
- `haskell` → `@codemirror/legacy-modes/mode/haskell` export `haskell`
- `erlang` → `@codemirror/legacy-modes/mode/erlang` export `erlang`
- `toml` → `@codemirror/legacy-modes/mode/toml` export `toml`
- `diff` → `@codemirror/legacy-modes/mode/diff` export `diff`
- `powershell` → `@codemirror/legacy-modes/mode/powershell` export `powerShell`

Helper for legacy modes:
```javascript
const legacyMode = (modPath, exportName) => async () => {
    const [mod, { StreamLanguage }] = await Promise.all([
        import(`@codemirror/legacy-modes/mode/${modPath}`),
        import('@codemirror/language'),
    ])
    return StreamLanguage.define(mod[exportName])
}
```

**Languages intentionally NOT mapped** (fall back to plain text): `graphql` (no CM6 legacy mode exists — `@codemirror/legacy-modes` has no GraphQL parser), `json5`, `mdx`, `csharp`, `objective-c`, `objective-cpp`, `erb`, `fish`, `elixir`, `zig`, `proto`, `terraform`, `makefile`, `cmake`, `nix`, `dart`, `asm`, `gitignore`, `gitattributes`, `dotenv`, `ini`. Some of these (`csharp`, `dart`) could potentially be added via legacy modes later — out of scope for this migration.

**b) `resolveLanguage(filePath, languageOverride)`** — async function that:
1. If `languageOverride` is given, look it up in `CM6_LANGUAGE_MAP`
2. Otherwise call `getLanguageFromPath(filePath)` to get shiki ID, then look up in map
3. If found, call the loader and return the `LanguageSupport`/`StreamLanguage`
4. If not found or loader fails, return `null` (plain text fallback)
5. Cache the **Promise** (not just the resolved value) by key to avoid duplicate concurrent imports when multiple editors mount simultaneously requesting the same language

**c) `createThemeExtension(isDark)`** — returns an array of extensions: `[githubDark or githubLight, bgOverride]` where `bgOverride` is an `EditorView.theme()` that overrides the background color to `#1b2733` for dark mode.

Import themes:
```javascript
import { githubDark } from '@uiw/codemirror-theme-github'
import { githubLight } from '@uiw/codemirror-theme-github'
```

**d) `createFontSizeExtension(fontSize)`** — returns an `EditorView.theme()` that sets `.cm-content` and `.cm-gutters` font-size.

**e) `useCodeMirrorExtensions(options)` composable** — takes reactive options `{ filePath, language, readOnly, wordWrap, lineNumbers, fontSize, theme }` and returns:
- `extensions` — a reactive computed extension array with compartments for each dynamic option
- `reconfigure(option, value)` — function to reconfigure a specific compartment on a given view
- The composable watches `settingsStore.getEffectiveTheme` and `settingsStore.getFontSize` and reconfigures automatically

Each dynamic option is in its own `Compartment`:
- `languageCompartment` — for the language extension
- `themeCompartment` — for the theme
- `fontSizeCompartment` — for font size
- `readOnlyCompartment` — for `EditorState.readOnly.of(bool)` + `EditorView.editable.of(!bool)`
- `lineWrappingCompartment` — for `EditorView.lineWrapping`
- `lineNumbersCompartment` — for `lineNumbers()` extension or empty

**IMPORTANT — Do NOT use `basicSetup`.** It includes `lineNumbers()`, `highlightActiveLine()`, `foldGutter()` etc. that conflict with our dynamic compartments (duplicate line numbers, double active-line highlight). Instead, use `minimalSetup` from `codemirror` (provides only: default keymap, undo/redo history, bracket matching, special chars, base styling) and add individual extensions in compartments.

Static extensions (always included):
- `minimalSetup` from `codemirror` — base editing behavior
- `syntaxHighlighting(defaultHighlightStyle, { fallback: true })` — token highlighting
- `drawSelection()` — visible selection
- `dropCursor()` — cursor on drag-drop
- `bracketMatching()` — bracket pair highlighting
- `closeBrackets()` — auto-close brackets
- `rectangularSelection()`, `crosshairCursor()` — multi-cursor
- `EditorView.updateListener` — for change events
- `indentOnInput()` — auto-indent
- `foldGutter()` — code folding

Dynamic extensions (in compartments — listed above in the compartment list).

- [ ] **Step 2: Commit**

```bash
git add frontend/src/composables/useCodeMirror.js
git commit -m "feat: add useCodeMirror composable for CM6 integration"
```

---

## Task 3: Create `CodeEditor.vue` component

**Files:**
- Create: `frontend/src/components/CodeEditor.vue`
- Reference: `frontend/src/composables/useCodeMirror.js`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/CodeEditor.vue` as a `<script setup>` SFC.

**Template:** A single `<div ref="editorEl" class="code-editor"></div>`. Style it to fill its container (`width: 100%; height: 100%;`).

**Props:**
```javascript
const props = defineProps({
    modelValue: { type: String, default: '' },
    filePath: { type: String, default: null },
    language: { type: String, default: null },
    readOnly: { type: Boolean, default: false },
    wordWrap: { type: Boolean, default: false },
    lineNumbers: { type: Boolean, default: true },
    saveViewState: { type: Boolean, default: false },
    extensions: { type: Array, default: () => [] },
})
```

**Emits:** `['update:modelValue', 'save', 'ready']`

**Lifecycle:**

`onMounted`:
1. Resolve language via `resolveLanguage(props.filePath, props.language)`
2. Build extensions using `useCodeMirrorExtensions` compartments
3. Add Ctrl+S keymap with guard: `keymap.of([{ key: 'Mod-s', run: () => { if (!props.readOnly && isDirty.value) emit('save'); return true } }])`. The `return true` always prevents the browser's default Save dialog. The guard ensures `save` only emits when the editor is editable and has changes.
4. Add `updateListener` that on `docChanged` sets `isDirty.value = true` and emits `update:modelValue` (with `_internalUpdate` flag)
5. Add `highlightActiveLine()` only when `!props.readOnly`
6. Append `props.extensions` to the extension array
7. Create `EditorView` with `doc: props.modelValue`, the extensions, and `parent: editorEl.value`
8. Store in `const view = shallowRef(null)`
9. Emit `ready` with `{ view: view.value }`

`onBeforeUnmount`:
1. If `saveViewState` and `filePath`, save view state to cache
2. Call `view.value?.destroy()`

**Watchers:**

- `watch(() => props.modelValue)` — external content update. Guard with `_internalUpdate` flag. If different from current doc, dispatch a replace-all transaction. If `saveViewState` is true, save view state for old path, then restore for new path after content swap.
- `watch(() => props.filePath)` — trigger language re-resolution. If `saveViewState`, save old path's view state, restore new path's after a tick.
- `watch(() => props.language)` — re-resolve language, reconfigure compartment.
- `watch(() => props.readOnly)` — reconfigure readOnly compartment (`EditorState.readOnly.of(val)` + `EditorView.editable.of(!val)`). Also toggle `highlightActiveLine`.
- `watch(() => props.wordWrap)` — reconfigure lineWrapping compartment.
- `watch(() => props.lineNumbers)` — reconfigure lineNumbers compartment.
- Settings store watchers for theme and fontSize are handled inside `useCodeMirrorExtensions`.

**Expose:**
```javascript
defineExpose({
    view,
    isDirty,
    resetDirty() { isDirty.value = false },
    focus() { view.value?.focus() },
})
```

**Dirty tracking:** A simple `const isDirty = ref(false)`. Set to `true` in updateListener on `docChanged`. Reset by `resetDirty()`. Also reset when `modelValue` changes externally (file switch = clean state).

**View state cache:** A module-level `const viewStateCache = new Map()` (shared across instances but keyed by filePath). Save: `{ scrollTop, selection }`. Restore: set scrollTop + dispatch selection.

**Style:**
```css
.code-editor {
    width: 100%;
    height: 100%;
}
/* CM6 editor fills its container */
.code-editor :deep(.cm-editor) {
    height: 100%;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/CodeEditor.vue
git commit -m "feat: add CodeEditor Vue component wrapping CM6 EditorView"
```

---

## Task 4: Create `DiffEditor.vue` component

**Files:**
- Create: `frontend/src/components/DiffEditor.vue`
- Reference: `frontend/src/composables/useCodeMirror.js`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/DiffEditor.vue` as a `<script setup>` SFC.

**Template:** A single `<div ref="diffEl" class="diff-editor"></div>`.

**Props:**
```javascript
const props = defineProps({
    original: { type: String, default: '' },
    modified: { type: String, default: '' },
    filePath: { type: String, default: null },
    language: { type: String, default: null },
    readOnly: { type: Boolean, default: true },
    wordWrap: { type: Boolean, default: false },
    sideBySide: { type: Boolean, default: true },
    collapseUnchanged: { type: Boolean, default: true },
    extensions: { type: Array, default: () => [] },
})
```

**Emits:** `['update:modified', 'save', 'ready']`

**Internal state:**

The component maintains either a `MergeView` (side-by-side) or a regular `EditorView` with `unifiedMergeView` extension (unified), stored in:
- `let currentView = null` (not reactive — either MergeView or EditorView)
- `let currentMode = null` ('side-by-side' or 'unified')

**`createSideBySideView()`:**
1. Resolve language extension
2. Build common extensions (theme, fontSize, wordWrap, readOnly for each side)
3. Create `new MergeView({ a: { doc, extensions: [...common, readOnly, editable(false)] }, b: { doc, extensions: [...common, readOnlyIfNeeded, Ctrl+S keymap, updateListener] }, parent: diffEl.value, collapseUnchanged: props.collapseUnchanged ? {} : undefined })`
4. The `updateListener` on side `.b` emits `update:modified` and sets `isDirty`
5. Store as `currentView`

**`createUnifiedView()`:**
1. Resolve language extension
2. Build common extensions + `unifiedMergeView({ original: props.original, highlightChanges: true, gutter: true, collapseUnchanged: props.collapseUnchanged ? { margin: 3, minSize: 4 } : undefined })`
3. Create `new EditorView({ doc: props.modified, extensions, parent: diffEl.value })`
4. Store as `currentView`

**`destroyCurrentView()`:** Calls `.destroy()` on `currentView` and sets to null.

**Lifecycle:**

`onMounted`: Call `createSideBySideView()` or `createUnifiedView()` based on `props.sideBySide`. Emit `ready`.

`onBeforeUnmount`: `destroyCurrentView()`

**Watchers:**

- `watch(() => props.sideBySide)` — destroy current view, create the other mode. This is an infrequent toggle.
- `watch([() => props.original, () => props.modified])` — content changed (file switch). Strategy depends on mode:
  - **Side-by-side:** destroy + recreate MergeView (MergeView doesn't support doc replacement)
  - **Unified:** Reconfigure the `unifiedMergeView` compartment with new original. Replace doc with new modified via transaction.
- `watch(() => props.readOnly)` — reconfigure readOnly compartment on the modified side
- `watch(() => props.wordWrap)` — reconfigure lineWrapping compartment
- `watch(() => props.filePath)` / `watch(() => props.language)` — re-resolve language, reconfigure

**Diff navigation (`goToNextChunk`, `goToPreviousChunk`):**

```javascript
import { goToNextChunk, goToPreviousChunk } from '@codemirror/merge'

function getModifiedView() {
    if (currentMode === 'side-by-side') return currentView?.b  // MergeView.b is the modified EditorView
    return currentView  // unified: the single EditorView
}

function goToNext() {
    const v = getModifiedView()
    if (v) goToNextChunk(v)
}

function goToPrev() {
    const v = getModifiedView()
    if (v) goToPreviousChunk(v)
}
```

**Expose:**
```javascript
defineExpose({
    goToNextChunk: goToNext,
    goToPreviousChunk: goToPrev,
    isDirty,
    resetDirty() { isDirty.value = false },
})
```

**Style:**
```css
.diff-editor {
    width: 100%;
    height: 100%;
}
.diff-editor :deep(.cm-editor),
.diff-editor :deep(.cm-mergeView) {
    height: 100%;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/DiffEditor.vue
git commit -m "feat: add DiffEditor Vue component wrapping CM6 MergeView"
```

---

## Task 5: Migrate `FilePane.vue`

**Files:**
- Modify: `frontend/src/components/FilePane.vue`

This is the biggest migration. Transform FilePane to use `CodeEditor` and `DiffEditor` instead of Monaco.

- [ ] **Step 1: Replace imports**

Remove (lines 3, 6–7):
```javascript
import { useMonaco } from '@guolao/vue-monaco-editor'
import githubDark from '../assets/monaco-themes/github-dark.json'
import githubLight from '../assets/monaco-themes/github-light.json'
```

Add:
```javascript
import CodeEditor from './CodeEditor.vue'
import DiffEditor from './DiffEditor.vue'
```

- [ ] **Step 2: Remove Monaco setup code**

Remove all of these sections:

- Theme registration (lines 73–86): `const { monacoRef } = useMonaco()` and the `watch(monacoRef, ...)` block
- Editor instance refs (lines 88–91): `editorRef`, `diffEditorRef`, `savedVersionId`
- Diff navigation workaround (lines 118–159): `currentDiffIndex` ref (line 124), the `navigateDiff()`, `goToPreviousDiff()`, `goToNextDiff()` functions and the comment explaining the Monaco bug. Also remove `currentDiffIndex.value = 0` reset in the filePath watcher (line 401).
- ResizeObserver for minimap and side-by-side breakpoint (lines 169–205): `SIDE_BY_SIDE_MIN_WIDTH`, `editorAreaRef` (keep the ref but repurpose for the container), `editorAreaWidth`, `resizeObserver`, the `onMounted` ResizeObserver setup, `onBeforeUnmount` disconnect, `canSideBySide`, `MINIMAP_EDITOR_MIN_WIDTH`
- `isDirty` computed (lines 210–224): the entire Monaco-based isDirty
- `monacoTheme` computed (lines 226–228)
- `editorOptions` computed (lines 230–241)
- `diffEditorOptions` computed (lines 243–265)
- `diffLanguage` computed (lines 272–281)
- `monacoPath` computed (lines 283–291)
- `registerSaveAction` function (lines 428–442)
- `onEditorMount` function (lines 444–448)
- `_contentChangeGuard` and `onDiffEditorMount` function (lines 450–464)
- `snapshotVersionId` function (lines 467–478)
- `onEditorChange` function (lines 482–484)

- [ ] **Step 3: Add new CM6-based code**

Add template refs for the new components:
```javascript
const codeEditorRef = ref(null)
const diffEditorRef = ref(null)
```

Replace `isDirty`:
```javascript
const isDirty = computed(() => {
    if (props.diffMode) return diffEditorRef.value?.isDirty ?? false
    return codeEditorRef.value?.isDirty ?? false
})
```

Add diff navigation functions (delegating to DiffEditor):
```javascript
function goToPreviousDiff() {
    diffEditorRef.value?.goToPreviousChunk()
}

function goToNextDiff() {
    diffEditorRef.value?.goToNextChunk()
}
```

Add editor ready handlers:
```javascript
function onEditorReady({ view }) {
    // Can be used for any post-mount setup
}

function onDiffReady({ view }) {
    // Can be used for any post-mount setup
}
```

Add diff modified change handler:
```javascript
function onDiffModifiedChange(newContent) {
    currentContent.value = newContent
}
```

Update `save()` function: after successful save, call `codeEditorRef.value?.resetDirty()` (normal mode) or `diffEditorRef.value?.resetDirty()` (diff mode). Both components expose `resetDirty()`. Remove the `snapshotVersionId()` call — dirty tracking is internal to the components.

Update `snapshotVersionId` → remove entirely. Dirty tracking is internal to `CodeEditor`/`DiffEditor`.

In `fetchFileContent`, after setting `currentContent.value = data.content`, add a `nextTick(() => codeEditorRef.value?.resetDirty())` to mark the freshly loaded content as clean.

Remove `canSideBySide` — no longer gating the side-by-side toggle on editor width (CM6 handles narrow widths gracefully). The toggle is always shown in diff mode.

- [ ] **Step 4: Replace template**

Replace the Monaco editors in the template (around lines 710–734):

**Old:**
```html
<vue-monaco-diff-editor v-if="diffMode && showEditor && !showMarkdownPreview" ... />
<vue-monaco-editor v-if="!diffMode" v-show="showEditor && !showMarkdownPreview" ... />
```

**New:**
```html
<!-- CodeMirror diff editor (diff mode) -->
<DiffEditor
    v-if="diffMode && showEditor && !showMarkdownPreview"
    ref="diffEditorRef"
    :original="originalContent ?? ''"
    :modified="modifiedContent ?? ''"
    :file-path="filePath"
    :read-only="diffReadOnly || !isEditing"
    :word-wrap="wordWrap"
    :side-by-side="sideBySide"
    :collapse-unchanged="true"
    @update:modified="onDiffModifiedChange"
    @save="save"
    @ready="onDiffReady"
/>

<!-- CodeMirror editor — mounted once, never destroyed on file switch (normal mode) -->
<CodeEditor
    v-if="!diffMode"
    v-show="showEditor && !showMarkdownPreview"
    ref="codeEditorRef"
    v-model="currentContent"
    :file-path="filePath"
    :read-only="!isEditing"
    :word-wrap="wordWrap"
    :line-numbers="true"
    :save-view-state="true"
    @save="save"
    @ready="onEditorReady"
/>
```

Note: `v-model="currentContent"` replaces the separate `:value` + `@change` pattern.

- [ ] **Step 5: Rename CSS class**

In the template, rename all occurrences of `monaco-placeholder` to `editor-overlay` (lines 695, 737).

In the `<style>` section, rename `.monaco-placeholder` to `.editor-overlay` (line 831).

- [ ] **Step 6: Remove the side-by-side width condition**

In the header template (around line 673), change:
```html
v-if="diffMode && !showMarkdownPreview && canSideBySide"
```
to:
```html
v-if="diffMode && !showMarkdownPreview"
```

- [ ] **Step 7: Remove the unused `editorAreaRef` template ref if no longer needed**

Check if `editorAreaRef` is still used. If the ResizeObserver was removed and it's only used for that, remove the `ref="editorAreaRef"` from the template. The `.editor-area` div stays (it's a layout container), just the ref is removed.

- [ ] **Step 8: Verify and commit**

```bash
git add frontend/src/components/FilePane.vue
git commit -m "refactor: migrate FilePane from Monaco to CodeMirror 6"
```

---

## Task 6: Migrate `JsonHumanView.vue`

**Files:**
- Modify: `frontend/src/components/JsonHumanView.vue`

- [ ] **Step 1: Replace imports**

Remove (lines 23, 29–30):
```javascript
import { useMonaco, DiffEditor as VueMonacoDiffEditor } from '@guolao/vue-monaco-editor'
import githubDark from '../assets/monaco-themes/github-dark.json'
import githubLight from '../assets/monaco-themes/github-light.json'
```

Add:
```javascript
import CodeEditor from './CodeEditor.vue'
import DiffEditor from './DiffEditor.vue'
```

- [ ] **Step 2: Remove Monaco setup code**

Remove all of these sections:

- `useMonaco()` and `monacoRef` (lines 67)
- Theme registration `watch(monacoRef, ...)` (lines 69–80)
- `monacoTheme` computed (lines 82–84)
- `monacoEditOptions` object (lines 86–104)
- `monacoHeight()` function (lines 112–119) — BUT keep the height calculation logic, just rename it to `editorHeight()` and adjust line height for CM6 (CM6 defaults to ~20px line height vs Monaco's 19px)
- `monacoDiffHeight()` function (lines 128–137) — rename to `diffEditorHeight()`, same adjustment
- `monacoDiffOptions` object (lines 139–159)
- `SHIKI_TO_MONACO` mapping (lines 166–188)
- `toMonacoLanguage()` function (lines 196–198)
- `monacoPath()` function (lines 211–218) and `_jhvInstanceCounter`/`_instanceId` (lines 211–212)
- `onDiffEditorMount()` function (lines 296–302) — replaced by `@update:modified` event on `DiffEditor`

- [ ] **Step 3: Add new CM6 code**

Add height calculation function (adjusted for CM6):
```javascript
function editorHeight(content) {
    const lineCount = (content || '').split('\n').length
    const lineHeight = 20  // CM6 default line height
    const padding = 32     // top + bottom padding
    const raw = lineCount * lineHeight + padding
    return Math.max(64, Math.min(raw, 800)) + 'px'
}

function diffEditorHeight(oldStr, newStr) {
    const oldLines = (oldStr || '').split('\n').length
    const newLines = (newStr || '').split('\n').length
    const lineCount = Math.max(oldLines, newLines)
    const lineHeight = 20
    const padding = 32
    const raw = lineCount * lineHeight + padding
    return Math.max(80, Math.min(raw, 500)) + 'px'
}
```

Add compact editor extensions for inline editors (no chrome):
```javascript
import { EditorView } from '@codemirror/view'

const compactEditorExtensions = [
    EditorView.theme({
        '.cm-content': { padding: '16px 0' },
        '.cm-scroller': { overflow: 'auto' },
        '&.cm-editor': { maxHeight: '40dvh' },
    }),
]
```

The `lineNumbers: false` prop on `CodeEditor` handles hiding line numbers.

Add a function to resolve language for JHV context (from override or sibling overrides):
```javascript
function resolveJhvLanguage(keyOverride) {
    const lang = keyOverride?.language ?? props.override?.language
    return lang || null  // null = no language = plain text in CodeEditor
}
```

- [ ] **Step 4: Replace template Monaco editors**

For each `<vue-monaco-editor>` in the template (markdown editing ~line 761, code editing ~line 782, multiline editing ~line 868), replace with:

```html
<div class="jhv-edit-monaco" :style="{ height: editorHeight(value) }">
    <CodeEditor
        :model-value="value"
        :language="resolveJhvLanguage(override)"
        :line-numbers="false"
        :word-wrap="true"
        :extensions="compactEditorExtensions"
        @update:model-value="emitUpdate($event)"
    />
</div>
```

Adjust the `language` prop for each case:
- Markdown editor: `:language="'markdown'"`
- Code editor: `:language="resolveJhvLanguage(override)"`
- Multiline editor: `:language="'plaintext'"` (or just omit since null = no highlighting)

For the diff editor (~line 1022), replace `<VueMonacoDiffEditor>` with:

```html
<div v-show="!diffSplitMode[pair.baseName]" class="jhv-edit-diff" :style="{ height: diffEditorHeight(value[pair.oldKey], value[pair.newKey]) }">
    <DiffEditor
        :original="value[pair.oldKey] ?? ''"
        :modified="value[pair.newKey] ?? ''"
        :language="resolveJhvLanguage(overrides[pair.newKey] ?? siblingOverrides[pair.newKey])"
        :read-only="false"
        :word-wrap="true"
        :side-by-side="true"
        @update:modified="onChildObjectUpdate(pair.newKey, $event)"
    />
</div>
```

This replaces the `@mount="onDiffEditorMount(…)"` pattern with the cleaner `@update:modified` event.

- [ ] **Step 5: Update comment at top of file**

Line 13: change `Monaco editor` to `CodeMirror editor`.

- [ ] **Step 6: Rename CSS class if needed**

If `jhv-edit-monaco` CSS class exists (line 1279), rename to `jhv-edit-editor` for consistency. Update all template references.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/JsonHumanView.vue
git commit -m "refactor: migrate JsonHumanView from Monaco to CodeMirror 6"
```

---

## Task 7: Update `main.js` — Remove Monaco plugin

**Files:**
- Modify: `frontend/src/main.js`

- [ ] **Step 1: Remove Monaco imports and plugin registration**

Remove line 42:
```javascript
import { install as VueMonacoEditorPlugin } from '@guolao/vue-monaco-editor'
```

Remove lines 55–59:
```javascript
app.use(VueMonacoEditorPlugin, {
    paths: {
        vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs'
    }
})
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/main.js
git commit -m "refactor: remove Monaco Editor plugin from main.js"
```

---

## Task 8: Clean up Monaco artifacts

**Files:**
- Delete: `frontend/src/assets/monaco-themes/github-dark.json`
- Delete: `frontend/src/assets/monaco-themes/github-light.json`
- Delete: `frontend/src/assets/monaco-themes/` directory
- Modify: `frontend/src/components/GitPanel.vue` (lines 738, 967)
- Modify: `frontend/src/components/FilesPanel.vue` (line 458)
- Modify: `frontend/src/composables/useTerminal.js` (line 14)

- [ ] **Step 1: Delete Monaco theme files**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/replace-monaco-with-codemirror
rm -rf frontend/src/assets/monaco-themes/
```

- [ ] **Step 2: Update comments in GitPanel.vue**

Line 738: Change `"clear stale diff data so Monaco doesn't show old content"` to `"clear stale diff data so the editor doesn't show old content"`.

Line 967: Change `"Diff viewer (Monaco diff editor via FilePane)"` to `"Diff viewer (CodeMirror diff editor via FilePane)"`.

- [ ] **Step 3: Update comment in FilesPanel.vue**

Line 458: Change `"Monaco editor, unsaved"` to `"editor, unsaved"`. The full line should read something like: `"container so the component state (tree expansion, editor, unsaved"`.

- [ ] **Step 4: Update comment in useTerminal.js**

Line 14: Change `"Background colors match the Monaco editor and --wa-color-surface-default"` to `"Background colors match the code editor and --wa-color-surface-default"`.

Do NOT touch line 302 — that's the Monaco font name in the fontFamily list, not the editor.

- [ ] **Step 5: Commit**

```bash
git add -u frontend/src/assets/monaco-themes/ frontend/src/components/GitPanel.vue frontend/src/components/FilesPanel.vue frontend/src/composables/useTerminal.js
git commit -m "chore: remove Monaco theme files and update stale comments"
```

---

## Task 9: Update CLAUDE.md stack table

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the stack table**

In the Stack table, change:
```
| Code Editor      | Monaco (CDN-loaded via vue-monaco-editor)   |
```
to:
```
| Code Editor      | CodeMirror 6 (bundled via npm)               |
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md stack table for CodeMirror 6"
```

---

## Task 10: Final verification and grep sweep

- [ ] **Step 1: Search for any remaining Monaco references**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/replace-monaco-with-codemirror
grep -ri "monaco" frontend/src/ --include="*.vue" --include="*.js" --include="*.json" | grep -v node_modules | grep -v "fontFamily.*Monaco"
```

Expected: zero results (except the Monaco font name in useTerminal.js fontFamily which is excluded by the grep filter).

Also check:
```bash
grep -ri "vue-monaco" frontend/src/ --include="*.vue" --include="*.js"
grep -ri "@guolao" frontend/src/ --include="*.vue" --include="*.js"
grep -ri "cdn.jsdelivr" frontend/src/ --include="*.vue" --include="*.js"
```

Expected: all return zero results.

- [ ] **Step 2: Check that package.json has no Monaco dependency**

```bash
grep -i "monaco" frontend/package.json
```

Expected: zero results.

- [ ] **Step 3: Verify npm install works**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/replace-monaco-with-codemirror/frontend
npm install
```

Expected: no errors.

- [ ] **Step 4: Verify Vite build works**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/replace-monaco-with-codemirror/frontend
npx vite build
```

Expected: build succeeds without errors. If there are import errors, fix them and recommit.

- [ ] **Step 5: Fix any remaining issues found, commit if needed**

If the grep sweep or build found issues, fix them and create a commit:
```bash
git commit -m "fix: address remaining Monaco references found during cleanup"
```
