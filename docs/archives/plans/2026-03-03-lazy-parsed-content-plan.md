# Lazy Parsed Content Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate redundant JSON.parse/stringify on session item `content` by caching the parse result lazily on the item object.

**Architecture:** Two utility functions (`getParsedContent`, `hasContent`) provide the only sanctioned access to item content. Parsing happens once on first access and is cached as `_parsedContent` on the item via `markRaw()`. Synthetic items store their object directly in `_parsedContent` without stringifying.

**Tech Stack:** Vue 3 (`markRaw`), Pinia store, existing `computeVisualItems` pipeline.

**Quality approach (from CLAUDE.md):** No tests, no linting. Implement to best standards.

---

### Task 1: Create utility functions `getParsedContent` and `hasContent`

**Files:**
- Create: `frontend/src/utils/parsedContent.js`

**Step 1: Create the utility file**

```js
// frontend/src/utils/parsedContent.js

import { markRaw } from 'vue'

/**
 * Get the parsed content of a session item (or visual item).
 * Parses lazily on first access and caches the result as `_parsedContent`
 * on the item object via markRaw() to prevent Vue deep reactivity.
 *
 * IMPORTANT: This is the ONLY sanctioned way to access parsed content.
 * Never call JSON.parse(item.content) directly — always use this function.
 *
 * @param {Object} item - A session item or visual item with a `.content` string
 * @returns {Object|null} The parsed content object, or null if no content
 */
export function getParsedContent(item) {
    if (item._parsedContent !== undefined) return item._parsedContent
    if (!item.content) return null
    try {
        item._parsedContent = markRaw(JSON.parse(item.content))
    } catch {
        item._parsedContent = markRaw({ error: 'Invalid JSON', raw: item.content })
    }
    return item._parsedContent
}

/**
 * Check whether an item has content available (loaded or pre-parsed).
 * Use this instead of checking `item.content` directly.
 *
 * Returns true for:
 * - Regular items with a content string (loaded from backend)
 * - Synthetic items with pre-parsed _parsedContent (no content string)
 *
 * Returns false for:
 * - Metadata-only placeholders (content is null, not yet loaded)
 *
 * @param {Object} item - A session item or visual item
 * @returns {boolean}
 */
export function hasContent(item) {
    return !!(item.content || item._parsedContent !== undefined)
}
```

**Step 2: Commit**

```bash
git add frontend/src/utils/parsedContent.js
git commit -m "feat: add getParsedContent and hasContent utility functions

Lazy cached property pattern for session item content parsing.
Parses JSON once on first access, caches with markRaw() to avoid
Vue deep reactivity overhead."
```

---

### Task 2: Update `computeVisualItems` — propagate `_parsedContent` + DRY helper

**Files:**
- Modify: `frontend/src/utils/visualItems.js`

The function creates visual item objects in ~10 different places, each repeating the same 5-6 base properties. Introduce a local `makeVisualItem` helper that always propagates both `content` and `_parsedContent`.

**Step 1: Add `makeVisualItem` helper and replace all creation sites**

At the top of `computeVisualItems` (after the `result` declaration, line 46), add:

```js
    // Helper to build visual items — always propagates _parsedContent for cache forwarding.
    // Extras override or extend the base properties (isGroupHead, isExpanded, etc.).
    const makeVisualItem = (item, extras) => ({
        lineNum: item.line_num,
        content: item.content,
        _parsedContent: item._parsedContent,
        kind: item.kind,
        groupHead: item.group_head ?? null,
        groupTail: item.group_tail ?? null,
        ...extras
    })
```

Then replace every `result.push({ lineNum: item.line_num, content: item.content, ... })` with `result.push(makeVisualItem(item, { ... }))`.

Exhaustive list of replacements (10 sites):

1. **Line 129-135** (conversation, user_message):
   ```js
   result.push(makeVisualItem(item, { groupHead: null, groupTail: null }))
   ```

2. **Line 142-148** (conversation, detailed block):
   ```js
   const visualItem = makeVisualItem(item)
   ```

3. **Line 159-164** (conversation, synthetic/kept assistant):
   ```js
   const visualItem = makeVisualItem(item, { groupHead: null, groupTail: null })
   ```

4. **Line 183-189** (debug, no display_level):
   ```js
   result.push(makeVisualItem(item))
   ```

5. **Line 196-202** (debug mode):
   ```js
   result.push(makeVisualItem(item))
   ```

6. **Line 209-215** (normal mode):
   ```js
   result.push(makeVisualItem(item))
   ```

7. **Line 224-230** (simplified, ALWAYS):
   ```js
   const visualItem = makeVisualItem(item)
   ```

8. **Line 258-267** (simplified, COLLAPSIBLE group head):
   ```js
   result.push(makeVisualItem(item, {
       isGroupHead: true,
       isExpanded: isExpanded,
       groupSize: groupSizes.get(item.group_head) || 0
   }))
   ```

9. **Line 272-278** (simplified, COLLAPSIBLE in ALWAYS group):
   ```js
   result.push(makeVisualItem(item))
   ```

10. **Line 283-289** (simplified, regular group member):
    ```js
    result.push(makeVisualItem(item))
    ```

**Step 2: Commit**

```bash
git add frontend/src/utils/visualItems.js
git commit -m "refactor: DRY visual item creation with makeVisualItem helper

Introduces makeVisualItem() inside computeVisualItems to replace 10
repeated object literal patterns. Also propagates _parsedContent for
lazy parsed content cache forwarding."
```

---

### Task 3: Update `recomputeVisualItems` — use `getParsedContent` + synthetic items without stringify

**Files:**
- Modify: `frontend/src/stores/data.js` (around lines 1048-1150)

**Step 1: Add import**

At the top of `data.js` (after line 27), add:

```js
import { getParsedContent, hasContent } from '../utils/parsedContent'
```

**Step 2: Update backward walk (lines 1088-1108)**

Replace `JSON.parse(item.content)` with `getParsedContent(item)`:

```js
for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i]
    if (item.kind !== 'assistant_message' && item.kind !== 'content_items') break
    const parsed = getParsedContent(item)
    if (!parsed) break
    const contentArray = parsed?.message?.content
    if (!Array.isArray(contentArray) || contentArray.length === 0) break
    const lastContent = contentArray[contentArray.length - 1]
    if (lastContent.type === 'tool_use') {
        toolUse = lastContent
        break
    }
    // If every entry is a tool_result, skip this item and keep looking
    if (contentArray.every(c => c.type === 'tool_result')) {
        toolUseCompleted = true
        continue
    }
    // Otherwise (text, image, etc.) stop searching
    break
}
```

Note: The `try/catch` around `JSON.parse` is no longer needed — `getParsedContent` handles parse errors internally and returns an error object. We check `if (!parsed) break` which handles both null content and parse-error objects (the error object is truthy, but won't have `.message?.content`, so the next check catches it).

**Step 3: Update working message synthesis (lines 1110-1128)**

Replace `JSON.stringify` with direct `_parsedContent` assignment using `markRaw()`:

```js
import { markRaw } from 'vue'  // add to existing import at line 4
```

```js
workingMessage = {
    line_num: lineNum,
    content: null,  // No stringified content — use _parsedContent directly
    _parsedContent: markRaw({
        type: 'assistant',
        syntheticKind,
        toolUse,
        toolUseCompleted,
        message: {
            role: 'assistant',
            content: []
        }
    }),
    kind: 'assistant_message',
    syntheticKind,
    display_level: DISPLAY_LEVEL.ALWAYS,
    group_head: null,
    group_tail: null,
}
```

**Step 4: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "perf: use getParsedContent in recomputeVisualItems backward walk

Eliminates JSON.parse in the backward walk for tool_use detection and
removes JSON.stringify for synthetic working message. The working message
now stores its parsed object directly in _parsedContent."
```

---

### Task 4: Update `setOptimisticMessage` — direct `_parsedContent` instead of stringify

**Files:**
- Modify: `frontend/src/stores/data.js` (lines 1172-1192)

**Step 1: Replace JSON.stringify with direct _parsedContent**

```js
setOptimisticMessage(sessionId, text) {
    const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE
    this.localState.optimisticMessages[sessionId] = {
        line_num: lineNum,
        content: null,  // No stringified content — use _parsedContent directly
        _parsedContent: markRaw({
            type: 'user',
            syntheticKind,
            message: {
                role: 'user',
                content: [{ type: 'text', text }]
            }
        }),
        kind: 'user_message',
        syntheticKind,
        display_level: DISPLAY_LEVEL.ALWAYS,
        group_head: null,
        group_tail: null
    }
    this.recomputeVisualItems(sessionId)
},
```

**Step 2: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "perf: use _parsedContent directly in setOptimisticMessage

Eliminates JSON.stringify for the optimistic user message. The parsed
object is stored directly in _parsedContent with markRaw()."
```

---

### Task 5: Update `_migrateInternalSuffixToExternal` — use `getParsedContent`

**Files:**
- Modify: `frontend/src/stores/data.js` (lines 564-565 caller, lines 625-665 function)

**Step 1: Update the caller (line 564)**

Change the guard and call to pass the item instead of the content string:

```js
if (!hadGroupTail && willHaveGroupTail && hasContent(existingItem)) {
    this._migrateInternalSuffixToExternal(sessionId, update.line_num, existingItem)
}
```

**Step 2: Update the function signature and body (lines 625-665)**

Change param from `contentString` to `item`, use `getParsedContent`:

```js
/**
 * Migrate internal suffix expansion state to external group expansion.
 * ...
 * @param {string} sessionId
 * @param {number} lineNum - The line_num of the ALWAYS item
 * @param {Object} item - The session item object
 * @private
 */
_migrateInternalSuffixToExternal(sessionId, lineNum, item) {
    const itemInternalGroups = this.localState.sessionInternalExpandedGroups[sessionId]?.[lineNum]
    if (!itemInternalGroups?.length) return

    const parsed = getParsedContent(item)
    if (!parsed) return

    const content = parsed?.message?.content
    if (!Array.isArray(content) || content.length === 0) return

    // ... rest unchanged ...
```

**Step 3: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "refactor: use getParsedContent in _migrateInternalSuffixToExternal

Replaces direct JSON.parse with cached getParsedContent. The function
now receives the item object instead of the raw content string."
```

---

### Task 6: Update `SessionItem.vue` — use `getParsedContent` via new prop

**Files:**
- Modify: `frontend/src/components/SessionItem.vue`

**Step 1: Update props and computed**

Add import:
```js
import { getParsedContent } from '../utils/parsedContent'
```

Change the `content` prop to optional (synthetic items have null content):
```js
content: {
    type: String,
    default: null
},
```

Add a new prop for pre-parsed content (forwarded from visual item's `_parsedContent`):
```js
preParsedContent: {
    type: Object,
    default: undefined
},
```

Replace the `parsedContent` computed (lines 88-94):
```js
// Parsed content: uses pre-parsed content if available (synthetic items, cache hit),
// otherwise parses lazily via getParsedContent helper.
const parsedContent = computed(() => {
    if (props.preParsedContent !== undefined) return props.preParsedContent
    if (!props.content) return null
    // Build a minimal item-like object for getParsedContent to cache on.
    // We can't cache on props directly (frozen in Vue 3), so we parse inline.
    // This is still called only once per component instance thanks to Vue's computed caching.
    try {
        return markRaw(JSON.parse(props.content))
    } catch {
        return { error: 'Invalid JSON', raw: props.content }
    }
})
```

Add `markRaw` to the Vue import:
```js
import { computed, ref, markRaw } from 'vue'
```

**Step 2: Commit**

```bash
git add frontend/src/components/SessionItem.vue
git commit -m "perf: use preParsedContent prop in SessionItem.vue

SessionItem now accepts an optional preParsedContent prop for cached
parsed content (synthetic items, backward walk cache hits). Falls back
to inline JSON.parse for regular items (still cached by Vue computed)."
```

---

### Task 7: Update `SessionItemsList.vue` — use `hasContent` + pass `preParsedContent`

**Files:**
- Modify: `frontend/src/components/SessionItemsList.vue`

**Step 1: Add import**

```js
import { hasContent } from '../utils/parsedContent'
```

**Step 2: Update lazy-load content check (line 701)**

```js
if (visualItem && !hasContent(visualItem)) {
```

**Step 3: Update template placeholder check (line 882)**

```html
<div v-if="!hasContent(item)" :style="{ minHeight: MIN_ITEM_SIZE + 'px' }"></div>
```

Note: `hasContent` must be available in the template. Since it's imported at module scope in `<script setup>`, it's automatically available.

**Step 4: Pass `preParsedContent` to both `SessionItem` usages**

First usage (line 891-900, group head expanded):
```html
<SessionItem
    v-if="item.isExpanded"
    :content="item.content"
    :pre-parsed-content="item._parsedContent"
    :kind="item.kind"
    ...
```

Second usage (line 904-918, regular item):
```html
<SessionItem
    v-else
    :content="item.content"
    :pre-parsed-content="item._parsedContent"
    :kind="item.kind"
    ...
```

**Step 5: Commit**

```bash
git add frontend/src/components/SessionItemsList.vue
git commit -m "perf: use hasContent and pass preParsedContent in SessionItemsList

Replaces direct item.content truthiness checks with hasContent() helper.
Passes _parsedContent from visual items to SessionItem for cache reuse."
```

---

### Task 8: Update documentation — CLAUDE.md + store comment

**Files:**
- Modify: `CLAUDE.md`
- Modify: `frontend/src/stores/data.js`

**Step 1: Add rule to CLAUDE.md**

After the "### Virtual Scrolling" section (after line 186), add a new section:

```markdown
### Session Item Content Access

**IMPORTANT:** Never access `item.content` (the raw JSON string) directly for parsing. Always use the helpers from `frontend/src/utils/parsedContent.js`:

- **`getParsedContent(item)`** — Returns the parsed content object. Parses lazily on first access and caches with `markRaw()`. Works on both session items and visual items.
- **`hasContent(item)`** — Returns `true` if the item has content available (raw string or pre-parsed). Use this instead of `!!item.content` for placeholder detection, since synthetic items have `_parsedContent` but no `content` string.

Direct `JSON.parse(item.content)` is forbidden — it bypasses the cache and wastes CPU on repeated parsing.
```

**Step 2: Add warning comment in the store**

In `data.js`, near the `sessionItems` state declaration (around line 67-72, wherever `sessionItems` is defined in state), add a comment:

```js
        // Session items indexed by session ID.
        // { sessionId: [items] } where each item has line_num, content, display_level, etc.
        //
        // ⚠️  IMPORTANT: Never access item.content directly for parsing.
        // Use getParsedContent(item) from utils/parsedContent.js instead.
        // It caches the parse result lazily on the item as _parsedContent.
        // Use hasContent(item) to check if content is available.
        sessionItems: {},
```

**Step 3: Commit**

```bash
git add CLAUDE.md frontend/src/stores/data.js
git commit -m "docs: add rules for session item content access

Documents getParsedContent/hasContent as the only sanctioned way to
access parsed session item content. Adds warning comment in the store."
```

---

### Task 9: Manual verification

Start the dev servers and verify:

1. **Session list loads** — sessions display correctly
2. **Session view works** — items render (both conversation and debug modes)
3. **Active session** — working message (spinner/tool name) appears during assistant_turn
4. **Send a message** — optimistic message appears and transitions to real message
5. **Toggle groups** — expand/collapse works in simplified mode
6. **Toggle detail** — conversation mode detail toggle works
7. **Switch display modes** — conversation → simplified → normal → debug all render correctly
