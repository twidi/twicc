# Code Comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inline annotations on code lines across all CodeMirror editors, persisted to IndexedDB and restored on page refresh.

**Architecture:** Data flows through 3 layers: IndexedDB (persistence) → Pinia store (state) → CM6 extension (UI). Each annotation is identified by a composite key (source + filePath + commitSha + toolUseId + lineNumber). The CM6 extension renders block widgets (textarea + cancel button) below annotated lines and communicates changes via callbacks to the store.

**Tech Stack:** IndexedDB, Pinia (Options API), CodeMirror 6 (StateField, Decoration.widget, WidgetType, EditorView.domEventHandlers), Vue 3 provide/inject

**Spec:** `docs/superpowers/specs/2026-03-28-code-comments-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `frontend/src/utils/codeCommentsStorage.js` | IndexedDB CRUD for code comments |
| Create | `frontend/src/stores/codeComments.js` | Pinia store: state, getters, actions, debounced persistence |
| Create | `frontend/src/extensions/codeComments.js` | CM6 extension: widget, gutter click, state field, theming |
| Modify | `frontend/src/main.js` | Hydrate code comments store at startup |
| Modify | `frontend/src/components/CodeEditor.vue` | Add `commentContext` prop, wire extension + store |
| Modify | `frontend/src/components/DiffEditor.vue` | Add `commentContext` prop, wire extension to b-side |
| Modify | `frontend/src/components/FilePane.vue` | Add `commitSha` prop, compute and pass `commentContext` |
| Modify | `frontend/src/components/GitPanel.vue` | Pass `commitSha` to FilePane |
| Modify | `frontend/src/components/items/content/ToolUseContent.vue` | `provide()` tool context for child editors |
| Modify | `frontend/src/components/items/content/ToolDiffViewer.vue` | `inject()` tool context, compute and pass `commentContext` |

---

## Task 1: IndexedDB Storage Layer

**Files:**
- Create: `frontend/src/utils/codeCommentsStorage.js`
- Modify: `frontend/src/utils/draftStorage.js` (bump DB_VERSION, add store)

### Context

The project uses a single IndexedDB database (`twicc`) with version-guarded store creation in `draftStorage.js`. Each new store bumps `DB_VERSION` by 1. The code comments store will be version 4.

Both files share the same DB — `draftStorage.js` owns the DB schema (open + upgrade), while `codeCommentsStorage.js` provides CRUD functions that call `getDb()` from draftStorage.

### Steps

- [ ] **Step 1: Add the `codeComments` object store to draftStorage.js**

In `frontend/src/utils/draftStorage.js`:

1. Change `DB_VERSION` from `3` to `4`
2. Add a new constant: `const CODE_COMMENTS_STORE = 'codeComments'`
3. Add a new `onupgradeneeded` block after the draftMedias block:

```javascript
// Create codeComments store if not exists (v4)
if (!db.objectStoreNames.contains(CODE_COMMENTS_STORE)) {
    db.createObjectStore(CODE_COMMENTS_STORE)
}
```

4. Export `getDb` and `CODE_COMMENTS_STORE` so the new file can use them:

```javascript
export { getDb, CODE_COMMENTS_STORE }
```

Note: `getDb` is currently a module-private function. It needs to be exported (add `export` keyword or add to exports).

- [ ] **Step 2: Create codeCommentsStorage.js**

Create `frontend/src/utils/codeCommentsStorage.js`:

```javascript
// frontend/src/utils/codeCommentsStorage.js
// IndexedDB CRUD for code comments (inline annotations on code lines).
// Uses the shared 'twicc' database managed by draftStorage.js.

import { getDb, CODE_COMMENTS_STORE } from './draftStorage'

/**
 * Save (create or update) a code comment.
 * @param {string} key - Serialized composite key
 * @param {Object} comment - The comment data object
 * @returns {Promise<void>}
 */
export async function saveCodeComment(key, comment) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(CODE_COMMENTS_STORE, 'readwrite')
        const store = tx.objectStore(CODE_COMMENTS_STORE)
        const request = store.put(comment, key)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete a code comment by key.
 * @param {string} key - Serialized composite key
 * @returns {Promise<void>}
 */
export async function deleteCodeComment(key) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(CODE_COMMENTS_STORE, 'readwrite')
        const store = tx.objectStore(CODE_COMMENTS_STORE)
        const request = store.delete(key)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get all code comments (used at app startup to hydrate the store).
 * @returns {Promise<Object>} Object mapping serialized key to comment data
 */
export async function getAllCodeComments() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(CODE_COMMENTS_STORE, 'readonly')
        const store = tx.objectStore(CODE_COMMENTS_STORE)
        const comments = {}

        const request = store.openCursor()
        request.onsuccess = (event) => {
            const cursor = event.target.result
            if (cursor) {
                comments[cursor.key] = cursor.value
                cursor.continue()
            } else {
                resolve(comments)
            }
        }
        request.onerror = () => reject(request.error)
    })
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/utils/draftStorage.js frontend/src/utils/codeCommentsStorage.js
git commit -m "feat(code-comments): add IndexedDB storage layer"
```

---

## Task 2: Pinia Store

**Files:**
- Create: `frontend/src/stores/codeComments.js`

### Context

Pinia stores in this project use Options API (`defineStore('name', { state, getters, actions })`). The store manages an in-memory object of comments, syncs to IndexedDB on mutations, and debounces writes for content updates (textarea typing).

### Key design

The composite key is serialized using null-byte (`\0`) separator — safe because `\0` is invalid in file paths on all OSes:

```
"files\0/path/to/file\0\0\042"
"git\0/path/to/file\0abc123\0\015"
"tool\0/path/to/file\0\0tool_use_xyz\07"
```

### Steps

- [ ] **Step 1: Create the store**

Create `frontend/src/stores/codeComments.js`:

```javascript
// frontend/src/stores/codeComments.js
// Pinia store for code comments (inline annotations on code lines).

import { defineStore, acceptHMRUpdate } from 'pinia'
import { getAllCodeComments, saveCodeComment, deleteCodeComment } from '../utils/codeCommentsStorage'

// ─── Key helpers ────────────────────────────────────────────────────────────

/**
 * Build a serialized key from comment identity fields.
 * Uses \0 as separator (invalid in file paths on all OSes).
 */
export function buildCommentKey({ source, filePath, commitSha, toolUseId, lineNumber }) {
    return `${source}\0${filePath}\0${commitSha ?? ''}\0${toolUseId ?? ''}\0${lineNumber}`
}

/**
 * Parse a serialized key back into its component fields.
 */
export function parseCommentKey(key) {
    const [source, filePath, commitSha, toolUseId, lineNumber] = key.split('\0')
    return {
        source,
        filePath,
        commitSha: commitSha || null,
        toolUseId: toolUseId || null,
        lineNumber: Number(lineNumber),
    }
}

// ─── Debounce timers (module-level, not reactive) ───────────────────────────

const _debouncers = {}
const DEBOUNCE_MS = 500

// ─── Store ──────────────────────────────────────────────────────────────────

export const useCodeCommentsStore = defineStore('codeComments', {
    state: () => ({
        /**
         * All comments indexed by serialized key.
         * @type {Object<string, {source: string, filePath: string, commitSha: string|null, toolUseId: string|null, lineNumber: number, content: string, createdAt: number, updatedAt: number}>}
         */
        comments: {},
    }),

    getters: {
        /**
         * Returns a function that filters comments matching a given context.
         * Usage: store.getCommentsForContext({ source, filePath, commitSha, toolUseId })
         * Returns an array of comment objects (with lineNumber and content).
         */
        getCommentsForContext: (state) => (context) => {
            if (!context) return []
            return Object.values(state.comments).filter(c =>
                c.source === context.source &&
                c.filePath === context.filePath &&
                c.commitSha === (context.commitSha ?? null) &&
                c.toolUseId === (context.toolUseId ?? null)
            )
        },
    },

    actions: {
        /**
         * Hydrate the store from IndexedDB at app startup.
         */
        async hydrateComments() {
            try {
                const all = await getAllCodeComments()
                this.comments = all
            } catch (err) {
                console.error('[codeComments] Failed to hydrate from IndexedDB:', err)
            }
        },

        /**
         * Add a new comment (empty content). Writes to IndexedDB immediately.
         * @param {Object} context - { source, filePath, commitSha, toolUseId }
         * @param {number} lineNumber
         */
        addComment(context, lineNumber) {
            const key = buildCommentKey({ ...context, lineNumber })
            if (this.comments[key]) return // already exists

            const now = Date.now()
            const comment = {
                source: context.source,
                filePath: context.filePath,
                commitSha: context.commitSha ?? null,
                toolUseId: context.toolUseId ?? null,
                lineNumber,
                content: '',
                createdAt: now,
                updatedAt: now,
            }
            this.comments[key] = comment
            saveCodeComment(key, comment).catch(err =>
                console.error('[codeComments] Failed to save:', err)
            )
        },

        /**
         * Update comment content. Debounces IndexedDB write.
         * @param {Object} context - { source, filePath, commitSha, toolUseId }
         * @param {number} lineNumber
         * @param {string} content
         */
        updateComment(context, lineNumber, content) {
            const key = buildCommentKey({ ...context, lineNumber })
            const comment = this.comments[key]
            if (!comment) return

            comment.content = content
            comment.updatedAt = Date.now()

            // Debounce IndexedDB write
            clearTimeout(_debouncers[key])
            _debouncers[key] = setTimeout(() => {
                saveCodeComment(key, this.comments[key]).catch(err =>
                    console.error('[codeComments] Failed to save:', err)
                )
                delete _debouncers[key]
            }, DEBOUNCE_MS)
        },

        /**
         * Remove a comment. Deletes from IndexedDB immediately.
         * @param {Object} context - { source, filePath, commitSha, toolUseId }
         * @param {number} lineNumber
         */
        removeComment(context, lineNumber) {
            const key = buildCommentKey({ ...context, lineNumber })
            // Flush any pending debounced write
            clearTimeout(_debouncers[key])
            delete _debouncers[key]

            delete this.comments[key]
            deleteCodeComment(key).catch(err =>
                console.error('[codeComments] Failed to delete:', err)
            )
        },
    },
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useCodeCommentsStore, import.meta.hot))
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/codeComments.js
git commit -m "feat(code-comments): add Pinia store with debounced persistence"
```

---

## Task 3: Startup Hydration

**Files:**
- Modify: `frontend/src/main.js`

### Context

The app hydrates IndexedDB data in `main.js` after `createPinia()` but before `app.mount()`. Hydration is async and non-blocking (fire-and-forget). The code comments store follows the same pattern.

### Steps

- [ ] **Step 1: Add hydration call**

In `frontend/src/main.js`, after the existing draft hydration block (after line 98), add:

```javascript
// Hydrate code comments from IndexedDB (async, non-blocking)
import { useCodeCommentsStore } from './stores/codeComments'
const codeCommentsStore = useCodeCommentsStore()
codeCommentsStore.hydrateComments()
```

Note: This import is static at the top level — add it with the other store imports near line 46.

Specifically:
1. Add `import { useCodeCommentsStore } from './stores/codeComments'` near the other store imports (after `useDataStore` import on line 46)
2. After line 98 (after the draft hydration `.then()` block), add:

```javascript
// Hydrate code comments from IndexedDB (async, non-blocking)
const codeCommentsStore = useCodeCommentsStore()
codeCommentsStore.hydrateComments()
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/main.js
git commit -m "feat(code-comments): hydrate comments from IndexedDB at startup"
```

---

## Task 4: CodeMirror Extension

**Files:**
- Create: `frontend/src/extensions/codeComments.js`

### Context

This is a standalone CM6 extension with no Vue/Pinia dependencies. It communicates with the outside world through callbacks (`onAdd`, `onUpdate`, `onRemove`) and an exported `StateEffect` (`syncCommentsEffect`) that the host component can dispatch to sync decorations from the store.

### Key design decisions

- **Block widget** (`Decoration.widget({ block: true, side: 1 })`) at `line.to` → appears between the line and the next one
- **Multiple widgets** can coexist (one per annotated line)
- **StateField** manages a `DecorationSet` + a `Map<lineNumber, content>` for tracking
- **Gutter click** uses `EditorView.domEventHandlers({ click })` targeting `.cm-lineNumbers .cm-gutterElement`
- **Line resolution** uses `view.posAtCoords()` projected into the content area from the gutter element's Y position
- **Widget DOM**: `<div>` wrapper → `<textarea>` + `<button>Cancel</button>` — all native HTML, styled via `EditorView.baseTheme`
- **`ignoreEvent() → true`** so textarea and button handle their own events

### Steps

- [ ] **Step 1: Create the extension**

Create `frontend/src/extensions/codeComments.js`:

```javascript
// frontend/src/extensions/codeComments.js
// CodeMirror 6 extension for inline code comments.
// Renders a textarea widget below annotated lines. Communicates with the
// host component via callbacks (no Vue/Pinia dependency).

import { StateField, StateEffect } from '@codemirror/state'
import { EditorView, Decoration, WidgetType } from '@codemirror/view'

// ─── Effects ────────────────────────────────────────────────────────────────

/**
 * Sync all comments for this editor from the store.
 * Value: Array<{ lineNumber: number, content: string }>
 * Dispatched by the host component when the store changes (e.g. after hydration).
 */
export const syncCommentsEffect = StateEffect.define()

/** Add a new comment widget at a line. Value: { lineNumber: number } */
const addCommentEffect = StateEffect.define()

/** Remove a comment widget at a line. Value: { lineNumber: number } */
const removeCommentEffect = StateEffect.define()

// ─── Widget ─────────────────────────────────────────────────────────────────

class CodeCommentWidget extends WidgetType {
    constructor(lineNumber, content, callbacks) {
        super()
        this.lineNumber = lineNumber
        this.content = content
        this.callbacks = callbacks
    }

    toDOM(view) {
        const wrap = document.createElement('div')
        wrap.className = 'cm-code-comment-wrap'

        const textarea = document.createElement('textarea')
        textarea.className = 'cm-code-comment-textarea'
        textarea.value = this.content
        textarea.rows = 3
        textarea.placeholder = 'Add a comment...'

        // Notify on input
        textarea.addEventListener('input', () => {
            this.callbacks.onUpdate(this.lineNumber, textarea.value)
        })

        // Close on Escape
        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                e.stopPropagation()
                view.dispatch({ effects: removeCommentEffect.of({ lineNumber: this.lineNumber }) })
                this.callbacks.onRemove(this.lineNumber)
                view.focus()
            }
        })

        const actions = document.createElement('div')
        actions.className = 'cm-code-comment-actions'

        const cancelBtn = document.createElement('button')
        cancelBtn.className = 'cm-code-comment-cancel'
        cancelBtn.textContent = 'Cancel'
        cancelBtn.type = 'button'
        cancelBtn.addEventListener('click', (e) => {
            e.stopPropagation()
            view.dispatch({ effects: removeCommentEffect.of({ lineNumber: this.lineNumber }) })
            this.callbacks.onRemove(this.lineNumber)
            view.focus()
        })

        actions.appendChild(cancelBtn)
        wrap.appendChild(textarea)
        wrap.appendChild(actions)

        // Auto-focus the textarea (only for newly added comments, not restored ones)
        if (!this.content) {
            requestAnimationFrame(() => textarea.focus())
        }

        return wrap
    }

    eq(other) {
        // Widgets are equal if they're on the same line with the same content.
        // Returning false forces DOM rebuild when content changes externally
        // (e.g. from syncCommentsEffect). This is fine since sync is rare.
        return this.lineNumber === other.lineNumber && this.content === other.content
    }

    get estimatedHeight() {
        return 100
    }

    ignoreEvent() {
        return true // Let textarea and button handle their own events
    }
}

// ─── State field ────────────────────────────────────────────────────────────

/**
 * Build the DecorationSet from a line→content map.
 * @param {Map<number, string>} commentsMap - lineNumber → content
 * @param {Object} callbacks - { onUpdate, onRemove }
 * @param {import('@codemirror/state').EditorState} state
 * @returns {import('@codemirror/view').DecorationSet}
 */
function buildDecorations(commentsMap, callbacks, state) {
    const decorations = []
    for (const [lineNumber, content] of commentsMap) {
        if (lineNumber < 1 || lineNumber > state.doc.lines) continue
        const line = state.doc.line(lineNumber)
        decorations.push(
            Decoration.widget({
                widget: new CodeCommentWidget(lineNumber, content, callbacks),
                block: true,
                side: 1, // after the line
            }).range(line.to)
        )
    }
    // Decorations must be sorted by position
    decorations.sort((a, b) => a.from - b.from)
    return Decoration.set(decorations)
}

function createField(callbacks, initialComments) {
    return StateField.define({
        create(state) {
            const map = new Map()
            for (const { lineNumber, content } of initialComments) {
                map.set(lineNumber, content)
            }
            return {
                commentsMap: map,
                deco: buildDecorations(map, callbacks, state),
            }
        },

        update(fieldState, tr) {
            let { commentsMap, deco } = fieldState
            let changed = false

            for (const effect of tr.effects) {
                if (effect.is(syncCommentsEffect)) {
                    // Full replacement from the store (e.g. after hydration)
                    commentsMap = new Map()
                    for (const { lineNumber, content } of effect.value) {
                        commentsMap.set(lineNumber, content)
                    }
                    changed = true
                } else if (effect.is(addCommentEffect)) {
                    const { lineNumber } = effect.value
                    if (!commentsMap.has(lineNumber)) {
                        commentsMap = new Map(commentsMap)
                        commentsMap.set(lineNumber, '')
                        changed = true
                    }
                } else if (effect.is(removeCommentEffect)) {
                    const { lineNumber } = effect.value
                    if (commentsMap.has(lineNumber)) {
                        commentsMap = new Map(commentsMap)
                        commentsMap.delete(lineNumber)
                        changed = true
                    }
                }
            }

            if (changed) {
                return {
                    commentsMap,
                    deco: buildDecorations(commentsMap, callbacks, tr.state),
                }
            }

            // If doc changed but no comment effects, remap decorations
            if (tr.docChanged) {
                return {
                    commentsMap,
                    deco: buildDecorations(commentsMap, callbacks, tr.state),
                }
            }

            return fieldState
        },

        provide(field) {
            return EditorView.decorations.from(field, (val) => val.deco)
        },
    })
}

// ─── Gutter click handler ───────────────────────────────────────────────────

function createGutterClickHandler(callbacks) {
    return EditorView.domEventHandlers({
        click(event, view) {
            const gutterEl = event.target.closest('.cm-lineNumbers .cm-gutterElement')
            if (!gutterEl) return false

            // Use the center of the gutter element to find the corresponding line
            const gutterRect = gutterEl.getBoundingClientRect()
            const y = gutterRect.top + gutterRect.height / 2
            const contentRect = view.contentDOM.getBoundingClientRect()
            const pos = view.posAtCoords({ x: contentRect.left + 1, y })
            if (pos === null) return false

            const line = view.state.doc.lineAt(pos)
            const fieldState = view.state.field(view.state.field ? undefined : null)

            // Check if this line already has a comment by inspecting decorations
            // We look at the field directly via a tagged approach
            // Simpler: check via the commentsMap stored in the field
            let hasComment = false
            // Access the field value — we'll store a reference to the field
            // in the extension's closure so we can access it here
            const fieldValue = view.state.field(_fieldRef)
            if (fieldValue?.commentsMap.has(line.number)) {
                // Already has a comment — do nothing (user can see and use it)
                return false
            }

            // Add a new comment
            view.dispatch({ effects: addCommentEffect.of({ lineNumber: line.number }) })
            callbacks.onAdd(line.number)

            event.preventDefault()
            return true
        },
    })
}

// Module-level reference to the field, set during createCodeCommentsExtension
let _fieldRef = null

// ─── Theme ──────────────────────────────────────────────────────────────────

const baseTheme = EditorView.baseTheme({
    '& .cm-lineNumbers .cm-gutterElement': {
        cursor: 'pointer',
    },
    '& .cm-code-comment-wrap': {
        padding: '6px 12px 6px 8px',
        borderTop: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.2))',
        borderBottom: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.2))',
        backgroundColor: 'var(--wa-color-surface-lowered, rgba(128, 128, 128, 0.06))',
    },
    '& .cm-code-comment-textarea': {
        display: 'block',
        width: '100%',
        minHeight: '60px',
        padding: '8px',
        border: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.3))',
        borderRadius: '4px',
        backgroundColor: 'var(--wa-color-surface-default, transparent)',
        color: 'inherit',
        fontFamily: 'inherit',
        fontSize: 'inherit',
        lineHeight: '1.4',
        resize: 'vertical',
        outline: 'none',
        boxSizing: 'border-box',
    },
    '& .cm-code-comment-textarea:focus': {
        borderColor: 'var(--wa-color-primary, rgba(100, 150, 255, 0.5))',
        boxShadow: '0 0 0 2px color-mix(in srgb, var(--wa-color-primary, rgba(100, 150, 255, 0.15)) 20%, transparent)',
    },
    '& .cm-code-comment-textarea::placeholder': {
        color: 'var(--wa-color-text-quiet, rgba(128, 128, 128, 0.6))',
    },
    '& .cm-code-comment-actions': {
        display: 'flex',
        justifyContent: 'flex-end',
        marginTop: '4px',
    },
    '& .cm-code-comment-cancel': {
        padding: '2px 12px',
        border: '1px solid var(--wa-color-border-default, rgba(128, 128, 128, 0.3))',
        borderRadius: '4px',
        backgroundColor: 'transparent',
        color: 'inherit',
        fontSize: '0.85em',
        cursor: 'pointer',
        outline: 'none',
    },
    '& .cm-code-comment-cancel:hover': {
        backgroundColor: 'var(--wa-color-surface-lowered, rgba(128, 128, 128, 0.1))',
    },
})

// ─── Public API ─────────────────────────────────────────────────────────────

/**
 * Create a CodeMirror extension for inline code comments.
 *
 * @param {Object} options
 * @param {Array<{lineNumber: number, content: string}>} options.initialComments
 *   Comments to display immediately (from store).
 * @param {(lineNumber: number) => void} options.onAdd
 *   Called when user clicks a line number to add a new comment.
 * @param {(lineNumber: number, content: string) => void} options.onUpdate
 *   Called when user types in a comment textarea.
 * @param {(lineNumber: number) => void} options.onRemove
 *   Called when user clicks Cancel or presses Escape.
 * @returns {import('@codemirror/state').Extension[]}
 */
export function createCodeCommentsExtension({ initialComments = [], onAdd, onUpdate, onRemove }) {
    const callbacks = { onAdd, onUpdate, onRemove }
    const field = createField(callbacks, initialComments)
    _fieldRef = field
    const gutterHandler = createGutterClickHandler(callbacks)
    return [field, gutterHandler, baseTheme]
}
```

**Important implementation note about `_fieldRef`:** The module-level `_fieldRef` variable is used by `createGutterClickHandler` to access the StateField value from the view state. This works because `_fieldRef` is set before the handler is ever called. However, if multiple CodeMirror instances with comments exist simultaneously, `_fieldRef` will point to the last created field. To fix this, the gutter click handler should look up the field via a different mechanism.

**Cleaner approach — avoid `_fieldRef`:** Refactor `createGutterClickHandler` to receive `field` as a parameter:

```javascript
function createGutterClickHandler(callbacks, field) {
    return EditorView.domEventHandlers({
        click(event, view) {
            // ... same as above but use `field` directly ...
            const fieldValue = view.state.field(field)
            // ...
        },
    })
}
```

And in `createCodeCommentsExtension`:
```javascript
const field = createField(callbacks, initialComments)
const gutterHandler = createGutterClickHandler(callbacks, field)
return [field, gutterHandler, baseTheme]
```

This removes the need for `_fieldRef` entirely. **Use this approach.**

- [ ] **Step 2: Commit**

```bash
git add frontend/src/extensions/codeComments.js
git commit -m "feat(code-comments): add CM6 extension with widget, gutter handler, and theming"
```

---

## Task 5: CodeEditor Integration

**Files:**
- Modify: `frontend/src/components/CodeEditor.vue`

### Context

CodeEditor is a Vue 3 component wrapping CM6's EditorView. It accepts an `extensions` prop for additional extensions. For code comments, we add a `commentContext` prop. When provided, the component:
1. Reads existing comments from the store
2. Creates the CM6 extension with callbacks wired to the store
3. Watches the store for late hydration and syncs via `syncCommentsEffect`

### Steps

- [ ] **Step 1: Add commentContext prop and wiring**

Changes to `frontend/src/components/CodeEditor.vue`:

1. **Add prop** (in the `defineProps` block):

```javascript
/** Comment context for inline annotations. Null = comments disabled.
 *  Shape: { source: 'files'|'git'|'tool', filePath: string, commitSha: string|null, toolUseId: string|null } */
commentContext: { type: Object, default: null },
```

2. **Add imports** (at top of `<script setup>`):

```javascript
import { createCodeCommentsExtension, syncCommentsEffect } from '../extensions/codeComments'
import { useCodeCommentsStore } from '../stores/codeComments'
```

3. **Get store instance** (after the existing `useSettingsStore()` call):

```javascript
const codeCommentsStore = useCodeCommentsStore()
```

4. **Build extension in onMounted** — replace the `allExtensions` assembly (currently lines ~136-141) to include the comment extension:

```javascript
// 4b. Build code comments extension (if context provided)
const commentExtParts = []
if (props.commentContext) {
    const existingComments = codeCommentsStore.getCommentsForContext(props.commentContext)
    commentExtParts.push(...createCodeCommentsExtension({
        initialComments: existingComments.map(c => ({ lineNumber: c.lineNumber, content: c.content })),
        onAdd: (lineNumber) => codeCommentsStore.addComment(props.commentContext, lineNumber),
        onUpdate: (lineNumber, content) => codeCommentsStore.updateComment(props.commentContext, lineNumber, content),
        onRemove: (lineNumber) => codeCommentsStore.removeComment(props.commentContext, lineNumber),
    }))
}

// 5. Assemble full extension array
const allExtensions = [
    ...cmExtensions.extensions,
    saveKeymap,
    updateListener,
    ...commentExtParts,
    ...props.extensions,
]
```

5. **Watch store for late hydration sync** — after the `onMounted` block, add a watcher:

```javascript
// Code comments: sync decorations when store changes (handles late hydration)
if (props.commentContext) {
    watch(
        () => codeCommentsStore.getCommentsForContext(props.commentContext),
        (comments) => {
            if (!view.value) return
            view.value.dispatch({
                effects: syncCommentsEffect.of(
                    comments.map(c => ({ lineNumber: c.lineNumber, content: c.content }))
                ),
            })
        },
        // Don't use immediate — initial comments are already set via initialComments
    )
}
```

Note: This watcher will trigger when the store's `comments` object changes (add/remove/hydrate). It won't trigger on `updateComment` because the store only mutates the content string of an existing comment object (not the comments object itself). This is intentional — content updates come from the widget's own textarea, so syncing them back would create a loop.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/CodeEditor.vue
git commit -m "feat(code-comments): wire CM6 extension into CodeEditor with store sync"
```

---

## Task 6: DiffEditor Integration

**Files:**
- Modify: `frontend/src/components/DiffEditor.vue`

### Context

DiffEditor supports side-by-side (MergeView with `a` and `b` sides) and unified modes. Comments should only appear on the modified side (`b`). The extension is added to `bExtensions` in side-by-side mode and to `allExtensions` in unified mode, but NOT to `aExtensions`.

### Steps

- [ ] **Step 1: Add commentContext prop and wiring**

Changes to `frontend/src/components/DiffEditor.vue`:

1. **Add prop**:

```javascript
/** Comment context for inline annotations. Null = comments disabled. */
commentContext: { type: Object, default: null },
```

2. **Add imports**:

```javascript
import { createCodeCommentsExtension, syncCommentsEffect } from '../extensions/codeComments'
import { useCodeCommentsStore } from '../stores/codeComments'
```

3. **Get store instance** (after the existing `useSettingsStore()` call):

```javascript
const codeCommentsStore = useCodeCommentsStore()
```

4. **Create a helper that builds the comment extension parts** (after the diffConfig definition):

```javascript
function buildCommentExtension() {
    if (!props.commentContext) return []
    const existingComments = codeCommentsStore.getCommentsForContext(props.commentContext)
    return createCodeCommentsExtension({
        initialComments: existingComments.map(c => ({ lineNumber: c.lineNumber, content: c.content })),
        onAdd: (lineNumber) => codeCommentsStore.addComment(props.commentContext, lineNumber),
        onUpdate: (lineNumber, content) => codeCommentsStore.updateComment(props.commentContext, lineNumber, content),
        onRemove: (lineNumber) => codeCommentsStore.removeComment(props.commentContext, lineNumber),
    })
}
```

5. **Add comment extension to bExtensions in `createSideBySideView`** — in the bExtensions array, add `...buildCommentExtension()` BEFORE `...props.extensions`:

```javascript
const bExtensions = [
    ...cmB.extensions,
    panelsExt,
    ...(langExtension ? [langExtension] : []),
    saveKeymap,
    updateListener,
    ...buildCommentExtension(),
    ...props.extensions,
]
```

Note: Do NOT add it to `aExtensions` (original side).

6. **Add comment extension to unified view** — in `createUnifiedView`, add to allExtensions:

```javascript
const allExtensions = [
    ...cmB.extensions,
    ...(langExtension ? [langExtension] : []),
    unifiedExt,
    saveKeymap,
    updateListener,
    ...buildCommentExtension(),
    ...props.extensions,
]
```

7. **Watch store for late hydration** — same pattern as CodeEditor:

```javascript
if (props.commentContext) {
    watch(
        () => codeCommentsStore.getCommentsForContext(props.commentContext),
        (comments) => {
            const v = getModifiedView()
            if (!v) return
            v.dispatch({
                effects: syncCommentsEffect.of(
                    comments.map(c => ({ lineNumber: c.lineNumber, content: c.content }))
                ),
            })
        },
    )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/DiffEditor.vue
git commit -m "feat(code-comments): wire CM6 extension into DiffEditor (modified side only)"
```

---

## Task 7: FilePane Context

**Files:**
- Modify: `frontend/src/components/FilePane.vue`

### Context

FilePane is used in two contexts:
- **Files tab**: shows a single file (`diffMode=false`). Context = `{ source: 'files', filePath }`.
- **Git tab**: shows a diff (`diffMode=true`). Context = `{ source: 'git', filePath, commitSha }`.

The `commitSha` is not currently available in FilePane — it will be added as a new prop (passed by GitPanel in Task 8).

The distinction between contexts is based on whether `commitSha` is passed:
- `commitSha === undefined` (not passed) → files context
- `commitSha === null` → git context, uncommitted changes (index)
- `commitSha === 'abc...'` → git context, specific commit

### Steps

- [ ] **Step 1: Add commitSha prop and compute commentContext**

Changes to `frontend/src/components/FilePane.vue`:

1. **Add prop**:

```javascript
/** Git commit SHA (null = index/uncommitted, undefined = not a git context) */
commitSha: { type: String, default: undefined },
```

2. **Add imports**:

```javascript
import { computed } from 'vue'
```

Note: `computed` should already be imported. If not, add it.

3. **Add computed commentContext** (in the `<script setup>` section, after props):

```javascript
const commentContext = computed(() => {
    if (!props.filePath) return null
    if (props.commitSha !== undefined) {
        // Git context
        return {
            source: 'git',
            filePath: props.filePath,
            commitSha: props.commitSha ?? null,
            toolUseId: null,
        }
    }
    // Files context
    return {
        source: 'files',
        filePath: props.filePath,
        commitSha: null,
        toolUseId: null,
    }
})
```

4. **Pass to CodeEditor** — add `:comment-context="commentContext"`:

```vue
<CodeEditor
    ...existing props...
    :comment-context="commentContext"
    ...
/>
```

5. **Pass to DiffEditor** — add `:comment-context="commentContext"`:

```vue
<DiffEditor
    ...existing props...
    :comment-context="commentContext"
    ...
/>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/FilePane.vue
git commit -m "feat(code-comments): compute and pass comment context in FilePane"
```

---

## Task 8: GitPanel Commit Hash Forwarding

**Files:**
- Modify: `frontend/src/components/GitPanel.vue`

### Context

GitPanel uses FilePane to display git diffs. The commit hash is in `selectedCommit.value.hash` and `isViewingIndex` indicates whether viewing the index (uncommitted changes). Currently neither is forwarded to FilePane.

### Steps

- [ ] **Step 1: Pass commitSha to FilePane**

In `frontend/src/components/GitPanel.vue`, find the `<FilePane>` usage (around line 1017-1032) and add the `:commit-sha` prop:

```vue
<FilePane
    ...existing props...
    :commit-sha="isViewingIndex ? null : selectedCommit?.hash ?? null"
    ...
/>
```

This passes:
- `null` when viewing the index (uncommitted changes)
- The commit hash string when viewing a specific commit
- `null` as fallback if selectedCommit is null (shouldn't happen since FilePane is gated by `selectedFile && diffData`)

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/GitPanel.vue
git commit -m "feat(code-comments): forward commit hash from GitPanel to FilePane"
```

---

## Task 9: Tool Context via Provide/Inject

**Files:**
- Modify: `frontend/src/components/items/content/ToolUseContent.vue`
- Modify: `frontend/src/components/items/content/ToolDiffViewer.vue`

### Context

ToolUseContent has `toolId`, `sessionId`, `projectId` as props. ToolDiffViewer (used by EditContent and WriteContent) has no knowledge of these. We use Vue's `provide/inject` to thread the tool context down without modifying the intermediate EditContent/WriteContent components.

### Steps

- [ ] **Step 1: ToolUseContent — provide tool context**

In `frontend/src/components/items/content/ToolUseContent.vue`:

1. **Add import** (if not already imported):

```javascript
import { provide } from 'vue'
```

Note: `provide` may need to be added to the existing vue import.

2. **Add provide** (early in `<script setup>`, after props are defined):

```javascript
// Provide tool context for code comments in child editors (ToolDiffViewer)
provide('codeCommentToolContext', {
    toolUseId: props.toolId,
    sessionId: props.sessionId,
})
```

- [ ] **Step 2: ToolDiffViewer — inject context and pass to editors**

In `frontend/src/components/items/content/ToolDiffViewer.vue`:

1. **Add imports**:

```javascript
import { inject, computed } from 'vue'
```

2. **Inject tool context and compute commentContext**:

```javascript
const toolContext = inject('codeCommentToolContext', null)

const commentContext = computed(() => {
    if (!toolContext || !props.filePath) return null
    return {
        source: 'tool',
        filePath: props.filePath,
        commitSha: null,
        toolUseId: toolContext.toolUseId,
    }
})
```

3. **Pass to DiffEditor** — add `:comment-context="commentContext"`:

```vue
<DiffEditor
    ...existing props...
    :comment-context="commentContext"
    ...
/>
```

4. **Pass to CodeEditor** — add `:comment-context="commentContext"`:

```vue
<CodeEditor
    ...existing props...
    :comment-context="commentContext"
    ...
/>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/items/content/ToolUseContent.vue frontend/src/components/items/content/ToolDiffViewer.vue
git commit -m "feat(code-comments): thread tool context via provide/inject to ToolDiffViewer"
```

---

## Verification

After all tasks are complete, verify the feature end-to-end:

1. **Start dev servers** in the worktree (`uv run ./devctl.py start`)
2. **Files tab**: Open a file → click a line number → textarea appears → type → refresh → textarea restored
3. **Git tab**: View a commit diff → click a line number in the modified side → textarea appears
4. **Conversation**: Find a tool use (Edit/Write) → click a line number → textarea appears
5. **Multiple comments**: Open several textareas on different lines → all visible
6. **Cancel**: Click Cancel → textarea removed, not restored after refresh
7. **Cross-context isolation**: Comments on the same file in Files tab vs Git tab are independent
