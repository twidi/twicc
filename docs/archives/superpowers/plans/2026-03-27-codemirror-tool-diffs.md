# CodeMirror Tool Diffs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current markdown-based diff rendering in Edit/Write tool items with CodeMirror DiffEditor (unified + side-by-side) and CodeEditor (full content for new files), including Wrap and Side-by-side toggles.

**Architecture:** A new `ToolDiffViewer.vue` component wraps the existing `DiffEditor.vue` and `CodeEditor.vue` with height constraints and toggles. `EditContent.vue` and `WriteContent.vue` become thin data-preparation layers that compute `original`/`modified` strings from available data (originalFile, structuredPatch, old_string/new_string, content) and delegate rendering to `ToolDiffViewer`. `ToolUseContent.vue` is extended to extract `originalFile` alongside the existing `structuredPatch` from tool_result items.

**Tech Stack:** Vue 3 (Composition API), CodeMirror 6 (`@codemirror/merge`), `diff` npm package (`applyPatch`), Web Awesome (`wa-switch`)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/src/utils/patchUtils.js` | **Create** | Utility: `applyStructuredPatch(source, hunks)` — wraps `diff.applyPatch` |
| `frontend/src/components/items/content/ToolDiffViewer.vue` | **Create** | Shared viewer: toggles (Wrap, Side by side) + DiffEditor/CodeEditor, height-constrained |
| `frontend/src/components/items/content/ToolUseContent.vue` | **Modify** (lines 603–647) | Extract `originalFile` from `toolUseResult` alongside `structuredPatch`, pass as new prop |
| `frontend/src/components/items/content/EditContent.vue` | **Modify** (full rewrite) | Compute original/modified, delegate to ToolDiffViewer |
| `frontend/src/components/items/content/WriteContent.vue` | **Modify** (full rewrite) | Compute original/modified or full content, delegate to ToolDiffViewer |

---

## Data Flow Summary

### Edit tool

| Condition | Original | Modified | Mode |
|-----------|----------|----------|------|
| Has `originalFile` + `structuredPatch` (extras loaded) | `originalFile` | `applyStructuredPatch(originalFile, structuredPatch)` | diff |
| No extras (or `originalFile` absent) | `input.old_string` | `input.new_string` | diff |

### Write tool

| Condition | Original | Modified | Mode |
|-----------|----------|----------|------|
| Has `originalFile` (non-empty) — file update | `originalFile` | `input.content` | diff |
| `originalFile` empty or absent — new file / no extras | — | `input.content` | full |

---

## Task 1: Create `applyStructuredPatch` utility

**Files:**
- Create: `frontend/src/utils/patchUtils.js`

- [ ] **Step 1: Create the utility file**

```javascript
import { applyPatch } from 'diff'

/**
 * Apply a structured patch (array of hunks from the Claude SDK's toolUseResult)
 * to a source string, returning the modified string.
 *
 * The hunks format matches the `diff` package's StructuredPatchHunk:
 * { oldStart, oldLines, newStart, newLines, lines: string[] }
 *
 * Returns null if the patch cannot be applied (should not happen with valid data).
 */
export function applyStructuredPatch(source, hunks) {
    const result = applyPatch(source, {
        hunks,
    })
    // applyPatch returns false on failure
    return result === false ? null : result
}
```

- [ ] **Step 2: Verify the `diff` package exports `applyPatch`**

Run: `node -e "const d = require('/home/twidi/dev/twicc-poc/frontend/node_modules/diff'); console.log(typeof d.applyPatch)"`
Expected: `function`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/utils/patchUtils.js
git commit -m "feat: add applyStructuredPatch utility for reconstructing modified files"
```

---

## Task 2: Create `ToolDiffViewer.vue`

**Files:**
- Create: `frontend/src/components/items/content/ToolDiffViewer.vue`

**Dependencies:** `DiffEditor.vue`, `CodeEditor.vue`, `useSettingsStore` (for initial toggle values)

- [ ] **Step 1: Create the component**

```vue
<script setup>
import { ref, computed, watch } from 'vue'
import { EditorView } from '@codemirror/view'
import DiffEditor from '../../DiffEditor.vue'
import CodeEditor from '../../CodeEditor.vue'
import { useSettingsStore } from '../../../stores/settings'

const SIDE_BY_SIDE_MIN_WIDTH = 900

const props = defineProps({
    /** 'diff' = show DiffEditor, 'full' = show CodeEditor read-only */
    mode: { type: String, default: 'diff', validator: v => ['diff', 'full'].includes(v) },
    /** Original content (for diff mode) */
    original: { type: String, default: '' },
    /** Modified content (diff mode) or full content (full mode) */
    modified: { type: String, default: '' },
    /** File path for language detection */
    filePath: { type: String, default: null },
})

const settingsStore = useSettingsStore()

// Toggle state — initialized from global settings
const wordWrap = ref(settingsStore.isEditorWordWrap)
const sideBySide = ref(settingsStore.isDiffSideBySide)

// Container width tracking for side-by-side availability
const editorAreaRef = ref(null)
const editorAreaWidth = ref(0)
let resizeObserver = null

watch(editorAreaRef, (el, _, onCleanup) => {
    if (!el) return
    resizeObserver = new ResizeObserver(entries => {
        const w = entries[0]?.contentRect?.width
        if (w > 0) editorAreaWidth.value = w
    })
    resizeObserver.observe(el)
    onCleanup(() => {
        resizeObserver?.disconnect()
        resizeObserver = null
    })
}, { flush: 'post' })

const canSideBySide = computed(() => editorAreaWidth.value > SIDE_BY_SIDE_MIN_WIDTH)
const effectiveSideBySide = computed(() => sideBySide.value && canSideBySide.value)

function onWordWrapToggle(event) {
    wordWrap.value = event.target.checked
}
function onSideBySideToggle(event) {
    sideBySide.value = event.target.checked
}

// CodeMirror extensions to constrain editor height
const heightExtensions = [
    EditorView.theme({
        '&': { maxHeight: '20.25rem' },
        '.cm-scroller': { overflow: 'auto' },
    }),
]
</script>

<template>
    <div class="tool-diff-viewer">
        <div class="tool-diff-header">
            <wa-switch
                :checked="wordWrap"
                size="small"
                @change="onWordWrapToggle"
            >Wrap</wa-switch>
            <wa-switch
                v-if="mode === 'diff' && canSideBySide"
                :checked="sideBySide"
                size="small"
                class="side-by-side-toggle"
                @change="onSideBySideToggle"
            >Side by side</wa-switch>
        </div>
        <div ref="editorAreaRef" class="tool-diff-body">
            <DiffEditor
                v-if="mode === 'diff'"
                :original="original"
                :modified="modified"
                :file-path="filePath"
                :read-only="true"
                :word-wrap="wordWrap"
                :side-by-side="effectiveSideBySide"
                :collapse-unchanged="true"
                :extensions="heightExtensions"
            />
            <CodeEditor
                v-else
                :model-value="modified"
                :file-path="filePath"
                :read-only="true"
                :word-wrap="wordWrap"
                :extensions="heightExtensions"
            />
        </div>
    </div>
</template>

<style scoped>
.tool-diff-viewer {
    padding: var(--wa-space-xs) 0;
}

.tool-diff-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding-bottom: var(--wa-space-xs);
}

.side-by-side-toggle {
    flex-shrink: 0;
}

.tool-diff-body {
    overflow: hidden;
    border-radius: var(--wa-border-radius-m);
}

/* Override height: 100% from DiffEditor/CodeEditor — let maxHeight from
   the EditorView.theme extension control sizing instead */
.tool-diff-body :deep(.diff-editor),
.tool-diff-body :deep(.code-editor) {
    height: auto;
}
.tool-diff-body :deep(.cm-mergeView) {
    height: auto;
}
</style>
```

**Key design notes:**
- `heightExtensions` passes an `EditorView.theme` extension that sets `maxHeight: 20.25rem` on the CM editor and `overflow: auto` on `.cm-scroller`. This is the documented CM6 way to constrain height while keeping sticky gutters.
- CSS overrides `.diff-editor`/`.code-editor`'s `height: 100%` to `auto` so the editor sizes to content (up to maxHeight).
- The `ResizeObserver` on the body div measures width to conditionally show/hide the side-by-side toggle (same as `FilePane.vue`).
- For MergeView (side-by-side), the `extensions` prop is applied to both `a` and `b` editors, so both respect the maxHeight. The `.cm-mergeView` container is also set to `height: auto`.

- [ ] **Step 2: Verify component renders** (manual — start dev server, navigate to an Edit tool item)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/items/content/ToolDiffViewer.vue
git commit -m "feat: add ToolDiffViewer component with toggles and height constraints"
```

---

## Task 3: Extract `originalFile` in `ToolUseContent.vue`

**Files:**
- Modify: `frontend/src/components/items/content/ToolUseContent.vue` (lines 603–647, 845–846)

- [ ] **Step 1: Add `fileChangeOriginalFile` ref and extract it alongside the patch**

In the `watchEffect` that fetches `fileChangeBackendPatch` (lines 609–647), also extract `originalFile`:

Add a new ref after `fileChangeBackendPatch` (line 606):

```javascript
const fileChangeOriginalFile = ref(null)
```

Then, in each branch of the `watchEffect` where the tool_result item is parsed, also extract `originalFile`. The extraction logic (used in two places — store hit and API fetch):

```javascript
// After extracting patch, also extract originalFile
const originalFile = parsed?.toolUseResult?.originalFile
if (typeof originalFile === 'string') {
    fileChangeOriginalFile.value = originalFile
}
```

In the early-return branch (line 610–613, when conditions are not met), also reset:

```javascript
fileChangeOriginalFile.value = null
```

**Full modified watchEffect:**

```javascript
const fileChangeOriginalFile = ref(null)

watchEffect(async () => {
    if ((!editValid.value && !writeValid.value) || !fileChangeStats.value) {
        fileChangeBackendPatch.value = null
        fileChangeOriginalFile.value = null
        return
    }
    const lineNum = toolState.value?.toolResultLineNum
    if (!lineNum) return
    // Already resolved for this line
    if (fileChangeBackendPatch.value?._lineNum === lineNum) return

    // Helper to extract patch + originalFile from a parsed tool_result item
    function extractPatchData(parsed) {
        const patch = parsed?.toolUseResult?.structuredPatch
        if (Array.isArray(patch) && patch.length > 0) {
            fileChangeBackendPatch.value = Object.freeze(Object.assign([...patch], { _lineNum: lineNum }))
        }
        const origFile = parsed?.toolUseResult?.originalFile
        if (typeof origFile === 'string') {
            fileChangeOriginalFile.value = origFile
        }
    }

    // Check if the item is already in the store
    const item = dataStore.getSessionItem(props.sessionId, lineNum)
    if (item && hasContent(item)) {
        extractPatchData(getParsedContent(item))
        return
    }

    // Fetch the item from the API
    fileChangeBackendPatchLoading.value = true
    try {
        await dataStore.loadSessionItemsRanges(
            props.projectId, props.sessionId, [`${lineNum}`], props.parentSessionId
        )
        const fetched = dataStore.getSessionItem(props.sessionId, lineNum)
        if (fetched && hasContent(fetched)) {
            extractPatchData(getParsedContent(fetched))
        }
    } finally {
        fileChangeBackendPatchLoading.value = false
    }
})
```

- [ ] **Step 2: Pass `originalFile` as prop to EditContent and WriteContent**

Modify lines 845–846 to add the new prop:

```html
<EditContent v-else-if="editValid" :input="input" :backend-patch="fileChangeBackendPatch" :backend-patch-loading="fileChangeBackendPatchLoading" :original-file="fileChangeOriginalFile" />
<WriteContent v-else-if="writeValid" :input="input" :backend-patch="fileChangeBackendPatch" :backend-patch-loading="fileChangeBackendPatchLoading" :original-file="fileChangeOriginalFile" />
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/items/content/ToolUseContent.vue
git commit -m "feat: extract originalFile from tool_result and pass to Edit/WriteContent"
```

---

## Task 4: Rewrite `EditContent.vue` to use ToolDiffViewer

**Files:**
- Modify: `frontend/src/components/items/content/EditContent.vue` (full rewrite)

- [ ] **Step 1: Rewrite the component**

Replace the entire file content with:

```vue
<script setup>
import { computed } from 'vue'
import { applyStructuredPatch } from '../../../utils/patchUtils'
import ToolDiffViewer from './ToolDiffViewer.vue'

const props = defineProps({
    input: {
        type: Object,
        required: true
    },
    backendPatch: {
        type: Array,
        default: null
    },
    backendPatchLoading: {
        type: Boolean,
        default: false
    },
    originalFile: {
        type: String,
        default: null
    }
})

/**
 * Compute original and modified strings for the diff viewer.
 *
 * Priority:
 * 1. If originalFile + backendPatch available (extras loaded):
 *    original = originalFile, modified = applyStructuredPatch(originalFile, patch)
 * 2. Fallback: original = old_string, modified = new_string (fragment only)
 */
const diffData = computed(() => {
    if (props.originalFile != null && props.backendPatch) {
        const modified = applyStructuredPatch(props.originalFile, props.backendPatch)
        if (modified != null) {
            return {
                original: props.originalFile,
                modified,
            }
        }
        // applyPatch failed — fall through to fragment mode
    }

    // Fragment mode: use old_string / new_string directly
    return {
        original: props.input.old_string ?? '',
        modified: props.input.new_string ?? '',
    }
})

const showSpinner = computed(() => props.backendPatchLoading && !props.backendPatch)
</script>

<template>
    <div class="edit-content">
        <div v-if="showSpinner" class="edit-loading">
            <wa-spinner></wa-spinner>
        </div>
        <ToolDiffViewer
            v-else
            mode="diff"
            :original="diffData.original"
            :modified="diffData.modified"
            :file-path="input.file_path"
        />
    </div>
</template>

<style scoped>
.edit-content {
    padding: var(--wa-space-xs) 0;
}

.edit-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) 0;
}
</style>
```

**What changed:**
- Removed: `structuredPatch` import from `diff`, `MarkdownContent`, `buildHunk()`, `diffHunks`, hunk iteration template, all markdown/diff CSS
- Added: `applyStructuredPatch` import, `ToolDiffViewer`, `diffData` computed, `originalFile` prop
- The component is now purely a data-preparation layer

- [ ] **Step 2: Verify** — dev server, check Edit tool items render correctly in both states (with and without extras)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/items/content/EditContent.vue
git commit -m "feat: replace markdown diff with CodeMirror DiffEditor in EditContent"
```

---

## Task 5: Rewrite `WriteContent.vue` to use ToolDiffViewer

**Files:**
- Modify: `frontend/src/components/items/content/WriteContent.vue` (full rewrite)

- [ ] **Step 1: Rewrite the component**

Replace the entire file content with:

```vue
<script setup>
import { computed } from 'vue'
import ToolDiffViewer from './ToolDiffViewer.vue'

const props = defineProps({
    input: {
        type: Object,
        required: true
    },
    backendPatch: {
        type: Array,
        default: null
    },
    backendPatchLoading: {
        type: Boolean,
        default: false
    },
    originalFile: {
        type: String,
        default: null
    }
})

/**
 * Determine display mode and content.
 *
 * - If originalFile is a non-empty string (file update with extras):
 *   diff mode with original = originalFile, modified = input.content
 * - Otherwise (new file, or no extras):
 *   full mode showing input.content in a read-only CodeEditor
 */
const viewerMode = computed(() => {
    if (typeof props.originalFile === 'string' && props.originalFile.length > 0) {
        return 'diff'
    }
    return 'full'
})

const original = computed(() => {
    return viewerMode.value === 'diff' ? props.originalFile : ''
})

const modified = computed(() => {
    return props.input.content ?? ''
})

const showSpinner = computed(() => props.backendPatchLoading && !props.backendPatch && viewerMode.value !== 'full')
</script>

<template>
    <div class="write-content">
        <div v-if="showSpinner" class="write-loading">
            <wa-spinner></wa-spinner>
        </div>
        <ToolDiffViewer
            v-else
            :mode="viewerMode"
            :original="original"
            :modified="modified"
            :file-path="input.file_path"
        />
    </div>
</template>

<style scoped>
.write-content {
    padding: var(--wa-space-xs) 0;
}

.write-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) 0;
}
</style>
```

**What changed:**
- Removed: `getLanguageFromPath`, `MarkdownContent`, `buildHunk()`, `diffHunks`, `codeSource`, hunk iteration template, all markdown/diff CSS
- Added: `ToolDiffViewer`, `viewerMode`/`original`/`modified` computeds, `originalFile` prop
- `showSpinner`: only show when loading AND we expect diff mode (in full mode, we already have content to show)

- [ ] **Step 2: Verify** — dev server, check Write tool items:
  - New file creation: shows CodeEditor with syntax highlighting
  - File update with extras: shows DiffEditor with full file diff
  - File update without extras yet: shows CodeEditor with full content, upgrades to diff when extras arrive

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/items/content/WriteContent.vue
git commit -m "feat: replace markdown diff with CodeMirror in WriteContent"
```

---

## Task 6: Final polish and edge cases

**Files:**
- Possibly adjust: `ToolDiffViewer.vue` (CSS tweaks)

- [ ] **Step 1: Test edge cases**

Verify the following scenarios manually:
1. **Edit with `replace_all: true`** — should still work (old_string vs new_string in fragment mode)
2. **Very large files** — check that maxHeight constraint and scrolling work properly
3. **Side-by-side toggle** — shrink the browser window below 900px, verify toggle hides and view switches to unified
4. **Wrap toggle** — verify long lines wrap/unwrap correctly
5. **Dark mode** — verify diff colors are correct (uses CSS variables from `App.vue`)
6. **MergeView (side-by-side) height** — verify both panels are constrained and scroll sync works

- [ ] **Step 2: CSS adjustments if needed**

If the MergeView container doesn't respect the height constraint, add a border or overflow tweaks to `.tool-diff-body`. If CodeEditor in full mode doesn't look right, adjust styles.

- [ ] **Step 3: Final commit**

```bash
git add frontend/src/components/items/content/ToolDiffViewer.vue
git commit -m "fix: polish ToolDiffViewer edge cases and styling"
```

---

## Notes

- **No backend changes required.** `originalFile` is already stored in `SessionItem.content` (raw JSONL), and the existing API returns it as part of the full item content.
- **`diff` package compatibility:** The backend's `structuredPatch` is a bare hunks array. `applyPatch` from `diff` v8 accepts `{ hunks: [...] }` — just wrap the array. The hunk shape (`oldStart`, `oldLines`, `newStart`, `newLines`, `lines`) is identical.
- **Performance:** `applyPatch` is called once per render (in a `computed`). For large files this is O(n) and runs in < 1ms. The `DiffEditor` recreates on `[original, modified]` change but this only happens once when extras arrive.
- **Collapse unchanged:** `DiffEditor` has `collapseUnchanged: true` by default, which hides unchanged regions in the full-file diff. This is key for large files where only a few lines changed.
