# Smart Collapse Unchanged Lines — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace CodeMirror's native `collapseUnchanged` with a custom extension that supports progressive expansion (show N lines top/bottom, or show all).

**Architecture:** A standalone CM6 extension (`smartCollapseUnchanged`) using StateField + StateEffects + custom WidgetType. Replaces native collapse in both unified and side-by-side modes. Integrated via `DiffEditor.vue` props.

**Tech Stack:** CodeMirror 6 (`@codemirror/view`, `@codemirror/state`), `@codemirror/merge` (public API only: `getChunks`, `mergeViewSiblings`), Vue 3.

**Spec:** `docs/superpowers/specs/2026-04-03-smart-collapse-unchanged-design.md`

**Note:** This project has no tests and no linting (per CLAUDE.md). Steps are implementation + manual verification + commits.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `frontend/src/extensions/smartCollapseUnchanged.js` | Create | StateEffects, SmartCollapseWidget, StateField, mapPos helper, entry point function |
| `frontend/src/components/DiffEditor.vue` | Modify | Add `collapseStep` prop, import extension, wire into both modes, add hover CSS |

---

## Task 1: Create the complete extension

**Files:**
- Create: `frontend/src/extensions/smartCollapseUnchanged.js`

Creates the full extension in one file: effects, widget, StateField, initialization, and entry point.

- [ ] **Step 1: Create the file with imports, effects, and mapPos helper**

Create `frontend/src/extensions/smartCollapseUnchanged.js`:

```javascript
// frontend/src/extensions/smartCollapseUnchanged.js
// Custom CodeMirror 6 extension for progressive expansion of unchanged lines in diffs.
// Replaces @codemirror/merge's built-in collapseUnchanged.

import { StateField, StateEffect } from '@codemirror/state'
import { EditorView, Decoration, WidgetType } from '@codemirror/view'
import { getChunks, mergeViewSiblings } from '@codemirror/merge'

// ─── State Effects ─────────────────────────────────────────────────────────

/** Reveal `count` lines from the top of the collapsed zone at `pos`. */
const expandTop = StateEffect.define({
    map: (value, change) => ({ ...value, pos: change.mapPos(value.pos) }),
})

/** Reveal `count` lines from the bottom of the collapsed zone at `pos`. */
const expandBottom = StateEffect.define({
    map: (value, change) => ({ ...value, pos: change.mapPos(value.pos) }),
})

/** Remove the collapsed zone at `pos` entirely. */
const expandAll = StateEffect.define({
    map: (value, change) => change.mapPos(value),
})

// ─── Position mapping (side-by-side sync) ──────────────────────────────────

/**
 * Map a document position from one side of a diff to the other.
 * Walks the chunk list, translating positions by tracking offsets between
 * each side's chunk boundaries. Same algorithm as the native internal mapPos.
 *
 * @param {number} pos - Position in the source document
 * @param {Chunk[]} chunks - Array of Chunk objects from getChunks()
 * @param {boolean} isA - Whether pos is in document A (true) or B (false)
 * @returns {number} Corresponding position in the other document
 */
function mapPos(pos, chunks, isA) {
    let startOur = 0, startOther = 0
    for (let i = 0; ; i++) {
        const next = i < chunks.length ? chunks[i] : null
        if (!next || (isA ? next.fromA : next.fromB) >= pos)
            return startOther + (pos - startOur)
        ;[startOur, startOther] = isA ? [next.toA, next.toB] : [next.toB, next.toA]
    }
}
```

- [ ] **Step 2: Add the SmartCollapseWidget class**

Append to the same file:

```javascript
// ─── Widget ────────────────────────────────────────────────────────────────

class SmartCollapseWidget extends WidgetType {
    /**
     * @param {number} lines - Total hidden lines
     * @param {boolean} canExpandTop - Show "▼ Show N lines" button
     * @param {boolean} canExpandBottom - Show "Show N lines ▲" button
     * @param {number} step - Lines to reveal per click
     */
    constructor(lines, canExpandTop, canExpandBottom, step) {
        super()
        this.lines = lines
        this.canExpandTop = canExpandTop
        this.canExpandBottom = canExpandBottom
        this.step = step
    }

    eq(other) {
        return this.lines === other.lines
            && this.canExpandTop === other.canExpandTop
            && this.canExpandBottom === other.canExpandBottom
            && this.step === other.step
    }

    toDOM(view) {
        const outer = document.createElement('div')
        outer.className = 'cm-collapsedLines'

        const parts = []

        // "▼ Show N lines" — expand from top
        if (this.canExpandTop) {
            const span = document.createElement('span')
            span.className = 'cm-collapsedLines-action'
            span.textContent = `▼ Show ${this.step} lines`
            span.addEventListener('click', e => {
                e.stopPropagation()
                this._dispatch(view, e.target, expandTop.of({ pos: 0, count: this.step }))
            })
            parts.push(span)
        }

        // "Show all N unchanged lines" — always present
        const showAll = document.createElement('span')
        showAll.className = 'cm-collapsedLines-action'
        showAll.textContent = view.state.phrase('Show all $ unchanged lines', this.lines)
        showAll.addEventListener('click', e => {
            e.stopPropagation()
            this._dispatch(view, e.target, expandAll.of(0))
        })
        parts.push(showAll)

        // "Show N lines ▲" — expand from bottom
        if (this.canExpandBottom) {
            const span = document.createElement('span')
            span.className = 'cm-collapsedLines-action'
            span.textContent = `Show ${this.step} lines ▲`
            span.addEventListener('click', e => {
                e.stopPropagation()
                this._dispatch(view, e.target, expandBottom.of({ pos: 0, count: this.step }))
            })
            parts.push(span)
        }

        // Assemble with " · " separators
        parts.forEach((part, i) => {
            if (i > 0) outer.appendChild(document.createTextNode(' · '))
            outer.appendChild(part)
        })

        return outer
    }

    /**
     * Dispatch an effect on the current view, and synchronize with sibling
     * in side-by-side mode. Resolves `pos` from the DOM element at dispatch time.
     */
    _dispatch(view, target, effectTemplate) {
        const pos = view.posAtDOM(target)

        // Build the actual effect with the resolved position
        let effect
        if (effectTemplate.is(expandAll)) {
            effect = expandAll.of(pos)
        } else if (effectTemplate.is(expandTop)) {
            effect = expandTop.of({ pos, count: effectTemplate.value.count })
        } else {
            effect = expandBottom.of({ pos, count: effectTemplate.value.count })
        }

        view.dispatch({ effects: effect })

        // Synchronize with sibling in side-by-side mode
        const siblings = mergeViewSiblings(view)
        if (siblings) {
            const chunkInfo = getChunks(view.state)
            if (chunkInfo) {
                const mappedPos = mapPos(pos, chunkInfo.chunks, chunkInfo.side === 'a')
                let siblingEffect
                if (effectTemplate.is(expandAll)) {
                    siblingEffect = expandAll.of(mappedPos)
                } else if (effectTemplate.is(expandTop)) {
                    siblingEffect = expandTop.of({ pos: mappedPos, count: effectTemplate.value.count })
                } else {
                    siblingEffect = expandBottom.of({ pos: mappedPos, count: effectTemplate.value.count })
                }
                const sibling = chunkInfo.side === 'a' ? siblings.b : siblings.a
                sibling.dispatch({ effects: siblingEffect })
            }
        }
    }

    ignoreEvent(e) { return e instanceof MouseEvent }

    get estimatedHeight() { return 27 }
}
```

- [ ] **Step 3: Add the StateField with late-init support for side-by-side mode**

Append to the same file. **Critical detail:** In side-by-side mode (`MergeView`), `ChunkField` is added via `appendConfig` AFTER the initial state is created. This means our `field.init()` runs before chunks are available — `getChunks()` returns `null` and we get `Decoration.none`. The `update` method must detect when chunks first become available (transition from `getChunks(startState) === null` to `getChunks(state) !== null`) and compute the initial decorations at that point.

```javascript
// ─── StateField ────────────────────────────────────────────────────────────

/**
 * Build a new RangeSet by updating a single decoration identified by `fromPos`.
 * Used by expandTop/expandBottom to narrow a collapsed range.
 */
function adjustDecoration(deco, fromPos, state, adjustFn, step, minSize) {
    const result = []
    let found = false
    const cursor = deco.iter()
    while (cursor.value) {
        if (!found && cursor.from === fromPos) {
            found = true
            const fromLine = state.doc.lineAt(cursor.from).number
            const toLine = state.doc.lineAt(cursor.to).number
            const adjusted = adjustFn(fromLine, toLine)
            if (adjusted && adjusted.lines >= minSize) {
                const newFrom = state.doc.line(adjusted.from).from
                const newTo = state.doc.line(adjusted.to).to
                result.push({
                    from: newFrom,
                    to: newTo,
                    value: Decoration.replace({
                        widget: new SmartCollapseWidget(adjusted.lines, adjusted.lines > step, adjusted.lines > step, step),
                        block: true,
                    }),
                })
            }
            // else: lines < minSize → drop the decoration (auto-expand)
        } else {
            result.push({ from: cursor.from, to: cursor.to, value: cursor.value })
        }
        cursor.next()
    }
    return Decoration.set(result.map(r => r.value.range(r.from, r.to)))
}

function createSmartCollapsedRanges(margin, minSize, step) {
    return StateField.define({
        create() { return Decoration.none },
        update(deco, tr) {
            deco = deco.map(tr.changes)

            // Late initialization for side-by-side mode: MergeView adds ChunkField
            // via appendConfig AFTER state creation, so our init() returns Decoration.none.
            // Detect the first transaction where chunks become available and build decorations.
            if (deco.size === 0 && !getChunks(tr.startState) && getChunks(tr.state)) {
                return buildSmartCollapsedRanges(tr.state, margin, minSize, step)
            }

            for (const e of tr.effects) {
                if (e.is(expandAll)) {
                    deco = deco.update({ filter: from => from !== e.value })
                } else if (e.is(expandTop)) {
                    deco = adjustDecoration(deco, e.value.pos, tr.state, (fromLine, toLine) => {
                        const newFrom = fromLine + e.value.count
                        const lines = toLine - newFrom + 1
                        return { from: newFrom, to: toLine, lines }
                    }, step, minSize)
                } else if (e.is(expandBottom)) {
                    deco = adjustDecoration(deco, e.value.pos, tr.state, (fromLine, toLine) => {
                        const newTo = toLine - e.value.count
                        const lines = newTo - fromLine + 1
                        return { from: fromLine, to: newTo, lines }
                    }, step, minSize)
                }
            }
            return deco
        },
        provide: f => EditorView.decorations.from(f),
    })
}
```

- [ ] **Step 4: Add buildSmartCollapsedRanges, base styles, and the entry point function**

Append to the same file:

```javascript
// ─── Initialization ────────────────────────────────────────────────────────

/**
 * Compute the initial set of collapsed decorations from the diff chunks.
 * Same algorithm as native buildCollapsedRanges but uses public getChunks() API.
 */
function buildSmartCollapsedRanges(state, margin, minSize, step) {
    const chunkInfo = getChunks(state)
    if (!chunkInfo) return Decoration.none

    const { chunks, side } = chunkInfo
    const isA = side === 'a'
    const ranges = []
    let prevLine = 1

    for (let i = 0; ; i++) {
        const chunk = i < chunks.length ? chunks[i] : null
        const collapseFrom = i ? prevLine + margin : 1
        const collapseTo = chunk
            ? state.doc.lineAt(isA ? chunk.fromA : chunk.fromB).number - 1 - margin
            : state.doc.lines
        const lines = collapseTo - collapseFrom + 1

        if (lines >= minSize) {
            ranges.push(
                Decoration.replace({
                    widget: new SmartCollapseWidget(lines, lines > step, lines > step, step),
                    block: true,
                }).range(
                    state.doc.line(collapseFrom).from,
                    state.doc.line(collapseTo).to,
                )
            )
        }

        if (!chunk) break
        prevLine = state.doc.lineAt(Math.min(state.doc.length, isA ? chunk.toA : chunk.toB)).number
    }

    return Decoration.set(ranges)
}

// ─── Base styles ───────────────────────────────────────────────────────────

const baseStyles = EditorView.baseTheme({
    // Override native cursor: pointer on the whole container; only action spans are clickable
    '.cm-collapsedLines': {
        cursor: 'default',
    },
    '.cm-collapsedLines .cm-collapsedLines-action': {
        cursor: 'pointer',
    },
    '.cm-collapsedLines .cm-collapsedLines-action:hover': {
        textDecoration: 'underline',
    },
})

// ─── Entry point ───────────────────────────────────────────────────────────

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
export function smartCollapseUnchanged({ margin = 3, minSize = 4, step = 20 } = {}) {
    const field = createSmartCollapsedRanges(margin, minSize, step)
    return [
        field.init(state => buildSmartCollapsedRanges(state, margin, minSize, step)),
        baseStyles,
    ]
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/extensions/smartCollapseUnchanged.js
git commit -m "feat: add smartCollapseUnchanged extension with progressive expansion"
```

---

## Task 2: Integrate into DiffEditor.vue

**Files:**
- Modify: `frontend/src/components/DiffEditor.vue`
  - Line 15: add import
  - Lines 24-40: add `collapseStep` prop
  - Line 94: add `buildCollapseExtension()` helper
  - Lines 219-224, 227-235: add extension to side-by-side arrays
  - Line 242: remove native `collapseUnchanged` from MergeView
  - Lines 262-271: remove native `collapseUnchanged` from unifiedMergeView
  - Lines 273-281: add extension to unified extensions array

- [ ] **Step 1: Add the import**

In `DiffEditor.vue`, after line 18 (`import { createCodeCommentsExtension ... }`), add:

```javascript
import { smartCollapseUnchanged } from '../extensions/smartCollapseUnchanged'
```

- [ ] **Step 2: Add the `collapseStep` prop**

In the `defineProps` block (line 32), after the `collapseUnchanged` prop, add:

```javascript
    collapseStep: { type: Number, default: 20 },
```

- [ ] **Step 3: Create a helper to build the collapse extension array**

After the `diffConfig` declaration (line 94), add:

```javascript
/** Build the smart collapse extension array (empty if collapse is disabled). */
function buildCollapseExtension() {
    if (!props.collapseUnchanged) return []
    return [smartCollapseUnchanged({ margin: 3, minSize: 4, step: props.collapseStep })]
}
```

- [ ] **Step 4: Update createSideBySideView — remove native collapse, add extension**

In `createSideBySideView()`, change the `aExtensions` array (lines 219-224) to include the collapse extension:

```javascript
    const aExtensions = [
        ...cmA.extensions,
        panelsExt,
        ...(langExtension ? [langExtension] : []),
        ...buildCollapseExtension(),
        ...props.extensions,
    ]
```

Similarly for `bExtensions` (lines 227-235):

```javascript
    const bExtensions = [
        ...cmB.extensions,
        panelsExt,
        ...(langExtension ? [langExtension] : []),
        saveKeymap,
        updateListener,
        ...buildCommentExtension(),
        ...buildCollapseExtension(),
        ...props.extensions,
    ]
```

Remove the native `collapseUnchanged` from the `MergeView` constructor (line 242). Change:

```javascript
        collapseUnchanged: props.collapseUnchanged ? {} : undefined,
```

to:

```javascript
        collapseUnchanged: undefined,
```

- [ ] **Step 5: Update createUnifiedView — remove native collapse, add extension**

In `createUnifiedView()`, change the `unifiedMergeView` call (lines 262-271) to remove `collapseUnchanged`:

```javascript
    const unifiedExt = unifiedMergeView({
        original: props.original,
        highlightChanges: true,
        gutter: true,
        mergeControls: false,
        diffConfig,
    })
```

And add the collapse extension to `allExtensions` (lines 273-281):

```javascript
    const allExtensions = [
        ...cmB.extensions,
        ...(langExtension ? [langExtension] : []),
        unifiedExt,
        saveKeymap,
        updateListener,
        ...buildCommentExtension(),
        ...buildCollapseExtension(),
        ...props.extensions,
    ]
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/DiffEditor.vue
git commit -m "feat: integrate smartCollapseUnchanged into DiffEditor"
```

---

## Task 3: Add hover CSS for dark mode

**Files:**
- Modify: `frontend/src/components/DiffEditor.vue:608-612` (unscoped `<style>` block)

The base styles for `.cm-collapsedLines-action` (cursor, hover underline) are bundled in the extension itself via `EditorView.baseTheme`. This task only adds the dark-mode color refinement that depends on project CSS variables.

- [ ] **Step 1: Add CSS for the action spans in dark mode**

In the unscoped `<style>` block of `DiffEditor.vue`, after the existing `.cm-collapsedLines` dark mode rule (line 612), add:

```css
html.wa-dark .diff-editor .cm-collapsedLines .cm-collapsedLines-action:hover {
    color: var(--wa-color-text-default);
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/DiffEditor.vue
git commit -m "style: add dark mode hover color for smart collapse actions"
```

---

## Task 4: Manual verification

- [ ] **Step 1: Start dev servers**

```bash
uv run ./devctl.py start
```

- [ ] **Step 2: Verify unified mode**

Open a session with a diff (Edit tool result). Verify:
- The collapsed banner shows "▼ Show 20 lines · Show all N unchanged lines · Show 20 lines ▲"
- Clicking "▼ Show 20 lines" reveals 20 lines from the top, banner updates count
- Clicking "Show 20 lines ▲" reveals 20 lines from the bottom
- Clicking "Show all N unchanged lines" reveals everything
- When ≤ 20 lines remain, only "Show all N unchanged lines" is shown
- Dark mode styling works
- Cursor is pointer only on clickable text, default on separators

- [ ] **Step 3: Verify side-by-side mode**

Switch to side-by-side mode. Verify:
- Both editors show the smart collapse banner
- Clicking in one editor synchronizes the other (both expand together)
- Progressive expansion works the same as in unified mode

- [ ] **Step 4: Verify EditContent fragment diff**

Find an Edit tool result that shows a fragment diff (old_string/new_string, not full file). Verify:
- The padding logic still produces the collapse banner at the top
- Expanding works as expected

- [ ] **Step 5: Final commit (if any fixes needed)**

If manual verification revealed issues that needed fixing, commit the fixes:

```bash
git add frontend/src/extensions/smartCollapseUnchanged.js frontend/src/components/DiffEditor.vue
git commit -m "fix: address issues found during manual verification"
```
