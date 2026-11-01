# Code Comments — Design Spec

## Overview

Inline annotations on code lines across all CodeMirror editors. Users click a line number to open a textarea below that line, type a comment, and it persists across page refreshes via IndexedDB. Multiple comments can be open simultaneously. For now, the data is stored but not exploited.

## Identification Key

Each annotation is identified by a composite key discriminated by source context:

| Source | Fields |
|--------|--------|
| `files` | `filePath`, `lineNumber` |
| `git` | `filePath`, `commitSha` (string \| null for uncommitted), `lineNumber` |
| `tool` | `toolUseId`, `filePath`, `lineNumber` |

`filePath` is always present for consistency.

### Key Structure

```typescript
interface CodeCommentKey {
  source: "files" | "git" | "tool"
  filePath: string
  commitSha: string | null   // only meaningful for "git"
  toolUseId: string | null    // only meaningful for "tool"
  lineNumber: number
}
```

### Stored Data

```typescript
interface CodeComment extends CodeCommentKey {
  content: string
  createdAt: number   // timestamp ms
  updatedAt: number   // timestamp ms
}
```

## User Behavior

1. **Open a file/tool result** — existing annotations are automatically displayed (textareas open with content)
2. **Click a line number** without annotation — empty textarea appears below that line, auto-focused
3. **Click a line number** with an existing open textarea — no-op (already visible)
4. **Type text** — saved to Pinia store, debounced write to IndexedDB
5. **Click "Cancel"** — textarea closed, annotation deleted from store and IndexedDB
6. **Refresh the page** — annotations restored from IndexedDB at startup

## Architecture

### 1. CodeMirror Extension (`frontend/src/extensions/codeComments.js`)

A standalone CM6 extension created via a factory function that receives the comment context and a callbacks object.

**State management:**
- `StateEffect` to add/remove comment widgets
- `StateField` managing a `DecorationSet` of block widgets (`Decoration.widget({ block: true, side: 1 })`) positioned at `line.to`
- Multiple widgets can coexist (one per annotated line)

**Widget (`WidgetType` subclass):**
- Renders a `<div>` containing:
  - A `<textarea>` (auto-resize or fixed rows)
  - A "Cancel" button
- `ignoreEvent()` returns `true` so the textarea is fully interactive
- On input: calls a callback to update the store
- On cancel: calls a callback to remove, dispatches effect to remove the decoration

**Gutter click handler:**
- `EditorView.domEventHandlers({ click })` detecting clicks on `.cm-lineNumbers .cm-gutterElement`
- Uses `posAtCoords` to resolve the clicked line
- If no annotation exists at that line → dispatches add effect
- If annotation already open → no-op

**Initialization:**
- Accepts a list of existing `{ lineNumber, content }` entries to pre-populate decorations at creation time (from the store)

**Theming:**
- `EditorView.baseTheme` for default styling (textarea, cancel button, wrapper)
- Cursor pointer on `.cm-lineNumbers .cm-gutterElement`

### 2. Pinia Store (`frontend/src/stores/codeComments.js`)

- **State:** reactive `Map<string, CodeComment>` (key serialized as `"source|filePath|commitSha|toolUseId|lineNumber"`)
- **Getters:**
  - `getCommentsForContext(source, filePath, commitSha?, toolUseId?)` — returns all annotations matching the context (regardless of lineNumber)
- **Actions:**
  - `addComment(key, content)` — creates entry, writes to IndexedDB
  - `updateComment(key, content)` — updates content + `updatedAt`, debounced write to IndexedDB
  - `removeComment(key)` — removes from state and IndexedDB
  - `loadFromStorage()` — hydrates state from IndexedDB at startup

### 3. IndexedDB Persistence (`frontend/src/utils/codeCommentsStorage.js`)

Following the same pattern as `draftStorage.js`:

- Database: `twicc-code-comments` (or a new object store in the existing DB if appropriate)
- Object store: `comments`
- Index on `[source, filePath]` for efficient context-based queries
- Functions: `loadAll()`, `save(comment)`, `remove(key)`, `clear()`
- Writes are debounced (not on every keypress)
- Hydrated at startup before Vue app mount

### 4. Component Integration

**CodeEditor.vue:**
- New optional prop: `commentContext: { source, filePath, commitSha?, toolUseId? } | null`
- When `commentContext` is provided, creates the CM6 extension with:
  - The context for building keys
  - Callbacks wired to the Pinia store
  - Pre-existing comments from the store for this context
- When `commentContext` is null/undefined, no annotation behavior

**DiffEditor.vue:**
- Same prop, passed down to internal editor views

**Parent components pass context:**
- `FilePane` → `{ source: "files", filePath }`
- Git components → `{ source: "git", filePath, commitSha }`
- `ToolDiffViewer` → `{ source: "tool", toolUseId, filePath }`

## Out of Scope

- Exploitation of annotations (no sending, summarizing, or exporting)
- Smart line tracking when file content changes (lineNumber is fixed)
- UI to list/filter/search annotations outside the editor
- Keyboard shortcut to add annotations
