# Smart Collapse Unchanged Lines — Design Spec

**Date:** 2026-04-03
**Status:** Approved

## Problem

CodeMirror's `@codemirror/merge` package provides `collapseUnchanged`, which hides runs of unchanged lines behind a clickable banner ("N unchanged lines"). Clicking the banner reveals **all** hidden lines at once. For large files with hundreds of unchanged lines (e.g., 900), this dumps a massive block into the editor, making it hard to find what you were looking for.

There is no native API for partial or incremental expansion — no custom widget option, no block-by-block reveal, no "show N more lines" mechanism.

## Solution

Write a custom CodeMirror extension (`smartCollapseUnchanged`) that replaces the native `collapseUnchanged` with progressive expansion support. The extension uses only public CM6 APIs and public exports from `@codemirror/merge`.

## Architecture

### File location

`frontend/src/extensions/smartCollapseUnchanged.js` — alongside the existing `codeComments.js` custom extension.

### Public API from `@codemirror/merge` used

| Export | Purpose |
|---|---|
| `getChunks(state)` | Returns `{ chunks, side }` — changed regions and which side (a/b) of the diff. Used to compute unchanged zones. |
| `mergeViewSiblings(view)` | Returns `{ a, b }` in side-by-side mode, `null` otherwise. Used to synchronize expansion across both editors. |
| `Chunk` | Chunk objects with `fromA`, `toA`, `fromB`, `toB` — positions of changes in each document. |

### State effects

| Effect | Payload | Action |
|---|---|---|
| `expandTop` | `{ pos: number, count: number }` | Reveals `count` lines from the top of the collapsed zone at position `pos`. |
| `expandBottom` | `{ pos: number, count: number }` | Reveals `count` lines from the bottom of the collapsed zone at position `pos`. |
| `expandAll` | `pos: number` | Removes the decoration at position `pos` entirely (same as native behavior). |

### StateField (`SmartCollapsedRanges`)

- **`create()`**: Returns `Decoration.none`. The actual initial decorations are computed by `StateField.init()`, which overrides `create()` on the first state — this is a standard CM6 pattern for fields that need access to other fields during initialization.
- **`update(deco, tr)`**:
  - Maps decorations through document changes (`deco.map(tr.changes)`).
  - Processes effects:
    - `expandAll` → filters out the decoration at the given position.
    - `expandTop` → adjusts the `from` boundary forward by N lines, replacing the decoration with a narrower one (new widget with updated line count).
    - `expandBottom` → adjusts the `to` boundary backward by N lines, same replacement approach.
    - If remaining lines after adjustment < `minSize` → removes the decoration entirely (auto-expand).
  - Provides decorations to the editor via `EditorView.decorations.from(f)`.

### Initialization function (`buildSmartCollapsedRanges`)

Same algorithm as native `buildCollapsedRanges`: iterates chunks from `getChunks(state)`, computes unchanged gaps between them, creates `Decoration.replace({ widget, block: true })` for gaps ≥ `minSize` lines (after applying `margin` context lines).

### Side-by-side synchronization

Handled in the widget's click handlers (not in the StateField). On click:
1. Detect sibling via `mergeViewSiblings(view)` — returns `{ a, b }` or `null`.
2. If sibling exists, determine which side the current view is on via `getChunks(view.state).side` (`"a"` or `"b"`).
3. Map the position to the sibling document using a local `mapPos(pos, chunks, isA)` helper. This helper walks the chunk list, translating positions between the two documents by tracking the offsets between each side's chunk boundaries (same algorithm as the native internal `mapPos`).
4. Dispatch the same effect (with the mapped position) on the sibling view.

## Widget UX

`SmartCollapseWidget` extends `WidgetType`. Renders a `<div class="cm-collapsedLines">` containing clickable `<span>` elements separated by `·` delimiters.

### Constructor parameters

- `lines`: total number of hidden lines
- `canExpandTop`: whether partial expansion from the top is available (false when `lines ≤ step`)
- `canExpandBottom`: whether partial expansion from the bottom is available (false when `lines ≤ step`)
- `step`: number of lines to reveal per click

Note: both `canExpandTop` and `canExpandBottom` are derived solely from `lines > step`. The directional buttons are always both present (or both absent) — there is no position-based logic for hiding one direction.

### Widget methods

- **`eq(other)`**: Compares all fields (`lines`, `canExpandTop`, `canExpandBottom`, `step`) for decoration reconciliation.
- **`estimatedHeight`**: Returns `27` (same as native) — helps CM6's viewport height estimation.
- **`ignoreEvent(e)`**: Returns `true` for `MouseEvent` — prevents clicks from triggering editor selection changes.
- **Position resolution**: Click handlers use `view.posAtDOM(e.target)` to obtain the current decoration position. This works correctly after partial expansions because `posAtDOM` resolves against the current document state.

### Layout variants

**Lines > step** (both directional buttons shown):
```
⦚  ▼ Show 20 lines  ·  Show all 450 unchanged lines  ·  Show 20 lines ▲  ⦚
```

**Lines ≤ step** (only "show all", directional buttons hidden):
```
⦚  Show all 15 unchanged lines  ⦚
```

### Styling

- Reuses the native `.cm-collapsedLines` class for the container → existing dark mode styles in `DiffEditor.vue` continue to work.
- The `⦚` decorators are preserved (CSS pseudo-elements `::before` / `::after` from the native base theme).
- Clickable spans get `cursor: pointer` and a hover effect (color change or underline).
- The "N unchanged lines" count in the center is only clickable as part of "Show all N unchanged lines".

## Integration

### DiffEditor.vue

**New prop:**
```javascript
collapseStep: { type: Number, default: 20 }
```

**Existing prop** `collapseUnchanged` (Boolean, default `true`) continues to control whether collapsing is enabled.

**Conditional gating:** The `collapseUnchanged` boolean prop still controls whether collapsing is active. The extension is only added when the prop is true:

```javascript
...(props.collapseUnchanged
    ? [smartCollapseUnchanged({ margin: 3, minSize: 4, step: props.collapseStep })]
    : [])
```

**Unified mode:** Stop passing `collapseUnchanged` to `unifiedMergeView()` (set to `undefined`). Add the extension conditionally to the extensions array.

**Side-by-side mode:** Stop passing `collapseUnchanged` to `MergeView()` (set to `undefined`). Add the extension conditionally to both the `a` and `b` extension arrays.

**Static prop:** `collapseStep` is treated as static — changing it at runtime requires a view recreation (same behavior as the native `collapseUnchanged` config).

### EditContent.vue

No changes needed. The padding calculation (`target = 7` = margin 3 + minSize 4) remains valid since we keep the same `margin` and `minSize` defaults.

### CSS (DiffEditor.vue)

Add a small style block for the clickable spans inside `.cm-collapsedLines`:
- Hover effect (lighter color / underline) on the action spans.
- The existing dark mode override (`html.wa-dark .diff-editor .cm-collapsedLines`) continues to work.

## Extension entry point

```javascript
/**
 * Smart collapse for unchanged lines in diffs.
 * Replaces @codemirror/merge's built-in collapseUnchanged with
 * progressive expansion (show N lines top/bottom, or show all).
 *
 * @param {Object} config
 * @param {number} [config.margin=3] - Context lines around each changed chunk.
 * @param {number} [config.minSize=4] - Minimum lines to trigger collapse.
 * @param {number} [config.step=20]   - Lines revealed per partial expand click.
 * @returns {Extension}
 */
export function smartCollapseUnchanged({ margin = 3, minSize = 4, step = 20 } = {})
```

Returns an array of extensions: `[SmartCollapsedRanges.init(...), baseStyles]`.

## Scope

- Both unified and side-by-side modes supported from the start.
- Side-by-side synchronization included.
- No changes to `EditContent.vue` padding logic.
- No changes to other consumers (`FilePane.vue`, `ToolDiffViewer.vue`, `JsonHumanView.vue`) — they inherit the behavior through `DiffEditor.vue`.
