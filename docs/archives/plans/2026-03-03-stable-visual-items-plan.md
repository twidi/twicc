# Stable Visual Items — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate unnecessary Vue re-renders by stabilizing visual item object references across recomputes.

**Architecture:** After `computeVisualItems()` produces a new array of visual item objects, a stabilization step compares each new visual item with the previously cached one (by `lineNum`). If all properties are identical, the cached object is reused (same JS reference). Vue sees the same reference → skips re-render for that item. `computeVisualItems()` itself is unchanged — stabilization happens in `recomputeVisualItems()` in the store.

**Tech Stack:** Vue 3 reactivity, Pinia store, existing `computeVisualItems` pipeline.

---

## Context

### The problem

Every call to `recomputeVisualItems` creates a brand new array of brand new visual item objects via `computeVisualItems`. Even when 1 item is added to a 1000-item session, all ~500 visible visual items are recreated as new JS objects. Vue sees new references for every item in the `v-for` → compares all props for all 30 rendered items → triggers the virtual scroller's `positions` computed (O(n) `.map()`), etc.

### What this plan fixes

After stabilization, a recompute where only 3 items actually changed produces an array containing 497 **same references** + 3 **new objects**. Vue skips the 497 unchanged items entirely.

### What this plan does NOT change

- `computeVisualItems()` in `visualItems.js` — untouched, remains a pure function
- The callers of `recomputeVisualItems` — unchanged
- The `_parsedContent` cache system — unchanged (stabilization handles it via the existing `setParsedContent` forwarding)

---

## Task 1: Add `visualItemEqual` utility function

**Files:**
- Create: `frontend/src/utils/visualItems.js` (add export to existing file)

**Step 1: Add the function at the end of `visualItems.js`**

Add after the `computeVisualItems` function:

```js
/**
 * Shallow-compare two visual items, ignoring the internal _parsedContent cache.
 * Used by the stabilization layer to decide whether to reuse a cached visual item
 * (same JS reference → Vue skips re-render) or replace it with the new one.
 *
 * @param {Object} a - Previous (cached) visual item
 * @param {Object} b - Newly computed visual item
 * @returns {boolean} true if all non-cache properties are identical
 */
export function visualItemEqual(a, b) {
    if (a === b) return true
    if (!a || !b) return false
    const keysA = Object.keys(a)
    const keysB = Object.keys(b)
    if (keysA.length !== keysB.length) return false
    for (const key of keysA) {
        if (key === '_parsedContent') continue
        if (a[key] !== b[key]) return false
    }
    return true
}
```

**Step 2: Commit**

```bash
git add frontend/src/utils/visualItems.js
git commit -m "feat: add visualItemEqual utility for visual item reference stabilization"
```

---

## Task 2: Add `visualItemCache` to localState and cleanup

**Files:**
- Modify: `frontend/src/stores/data.js` — localState definition (~line 135) and `unloadSession` (~line 1003)

**Step 1: Add `visualItemCache` to `localState`**

After the `sessionVisualItems` declaration (line 135), add:

```js
            // Visual item reference cache - used to stabilize object references
            // across recomputes so Vue skips re-renders for unchanged items.
            // { sessionId: Map<lineNum, visualItem> }
            // Not reactive (plain object + Maps) — only used internally by
            // recomputeVisualItems, never read by Vue templates.
            visualItemCache: {},
```

**Step 2: Clean up cache in `unloadSession`**

In the `unloadSession` action, after `delete this.localState.sessionVisualItems[sessionId]`, add:

```js
            delete this.localState.visualItemCache[sessionId]
```

**Step 3: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat: add visualItemCache to localState with cleanup on unload"
```

---

## Task 3: Add stabilization step in `recomputeVisualItems`

**Files:**
- Modify: `frontend/src/stores/data.js` — `recomputeVisualItems` action (~line 1051)
- Modify: `frontend/src/stores/data.js` — import line for `visualItems.js`

**Step 1: Update imports**

Add `visualItemEqual` to the existing import from `visualItems.js`:

```js
import { computeVisualItems, visualItemEqual } from '../utils/visualItems'
```

**Step 2: Add stabilization after `computeVisualItems` call**

In `recomputeVisualItems`, replace the final assignment block (after syntheticKind propagation, around line 1153):

```js
            this.localState.sessionVisualItems[sessionId] = visualItems
```

With:

```js
            // Stabilize visual item references: reuse cached objects when properties
            // haven't changed, so Vue sees the same reference and skips re-render.
            const cache = this.localState.visualItemCache[sessionId] || new Map()
            const newCache = new Map()

            const stableItems = visualItems.map(vi => {
                const cached = cache.get(vi.lineNum)
                if (visualItemEqual(cached, vi)) {
                    // Properties identical — reuse old reference.
                    // Forward the parsed content from the new computation to the
                    // cached object in case items were re-parsed (e.g. content loaded).
                    const parsed = getParsedContent(vi)
                    if (parsed !== null) setParsedContent(cached, parsed)
                    newCache.set(vi.lineNum, cached)
                    return cached
                }
                // Changed or new item — use the new object.
                // Forward parsed content so it's available on the visual item.
                const parsed = getParsedContent(vi)
                if (parsed !== null) setParsedContent(vi, parsed)
                newCache.set(vi.lineNum, vi)
                return vi
            })

            this.localState.visualItemCache[sessionId] = newCache
            this.localState.sessionVisualItems[sessionId] = stableItems
```

**Step 3: Handle the early return (empty items)**

At the top of `recomputeVisualItems`, the early return:

```js
            if (!items && !this.localState.optimisticMessages[sessionId]) {
                this.localState.sessionVisualItems[sessionId] = []
                return
            }
```

Add cache clearing here:

```js
            if (!items && !this.localState.optimisticMessages[sessionId]) {
                this.localState.sessionVisualItems[sessionId] = []
                this.localState.visualItemCache[sessionId] = new Map()
                return
            }
```

**Step 4: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat: stabilize visual item references across recomputes

Reuse cached visual item objects when their properties haven't changed,
so Vue sees the same JS reference and skips re-rendering those items.
This eliminates the Vue reactivity cascade for unchanged items."
```

---

## Task 4: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md` — Virtual Scrolling section

**Step 1: Add note about stabilization to the Virtual Scrolling section**

In the "Virtual Scrolling" paragraph, after the existing text about the visual pipeline, add:

```markdown
Visual items are stabilized across recomputes: when `recomputeVisualItems` runs, each new visual item is compared with the cached version (by `lineNum`). If all properties are identical, the old object reference is reused. This means Vue skips re-rendering for unchanged items, even though `computeVisualItems` creates new objects every time.
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document visual item reference stabilization"
```

---

## Task 5: Manual verification

**Step 1: Start dev servers**

Verify the frontend compiles without errors.

**Step 2: Test normal browsing**

- Open a session with many items
- Scroll through the list — items should render normally
- Toggle groups (simplified mode) — items should expand/collapse
- Toggle detailed mode (conversation mode) — block should expand/collapse

**Step 3: Test during active session**

- Start an interactive Claude session
- Send a message — optimistic message should appear, then real items
- While Claude responds — working message should show with tool name
- When Claude finishes — working message should disappear

**Step 4: Commit any fixes if needed**

---

## Notes

### Why `_parsedContent` is skipped in comparison

`_parsedContent` is a cache set via `markRaw()`. It's not a visual property — it's the parsed JSON content object used downstream by `SessionItem`. Two visual items with the same `content` string but different `_parsedContent` references should be considered equal (the parse result is identical).

### Why we forward `_parsedContent` to reused cached items

When an item's content is first loaded (goes from `null` to a JSON string), `getParsedContent` parses it and caches the result on the new visual item. If we reuse the cached visual item (because other properties didn't change), we need to forward this parsed content so that `SessionItemsList` can access it via `getParsedContent(item)`.

### Why `newCache` replaces `cache` entirely

Using a new Map instead of mutating the old one ensures items that are no longer in the visual items list (e.g., collapsed group members) are garbage-collected. The cache always mirrors the current visual items list exactly.

### Why `visualItemCache` is not reactive

The cache is never read by Vue templates or computed properties. It's only used internally by `recomputeVisualItems`. Making it reactive would add unnecessary overhead (Vue proxying the Map and all its entries). Using a plain object + Maps keeps it lightweight.
