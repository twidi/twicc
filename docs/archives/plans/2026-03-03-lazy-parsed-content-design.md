# Lazy Parsed Content — Design

## Problem

Session item `content` (a JSON string from JSONL lines) is parsed multiple times across the frontend:

1. **`recomputeVisualItems`** — backward walk does `JSON.parse(item.content)` on each assistant item to find the last `tool_use`, then `JSON.stringify` to build the synthetic working message
2. **`SessionItem.vue`** — `parsedContent` computed does `JSON.parse(props.content)` for every rendered item
3. **`_migrateInternalSuffixToExternal`** — parses content to find suffix boundaries

Combined with frequent calls to `recomputeVisualItems` (every WebSocket message, every process state change), this creates a parse/stringify/parse cycle that wastes CPU on the main thread during active sessions.

## Approach: Lazy cached property on the item object

Parse content **once on first access**, cache the result directly on the item object. Inspired by Python's `cached_property` pattern.

### `getParsedContent(item)`

Utility function that:
- If `item.content` is null → returns `null` (metadata-only placeholder)
- If `item._parsedContent !== undefined` → returns cached value
- Otherwise → `JSON.parse(item.content)`, stores result in `item._parsedContent` via `markRaw()`, returns it

`markRaw()` prevents Vue from making the parsed object deeply reactive (the content is immutable after parsing).

### `hasContent(item)`

Utility function that returns `true` if the item has content available (either raw string or pre-parsed). Needed because synthetic items (working message, optimistic message) will have `_parsedContent` set directly without a `content` string.

```js
function hasContent(item) {
    return !!(item.content || item._parsedContent !== undefined)
}
```

### No cache invalidation needed

- Content is immutable once non-null (JSONL lines never change)
- The only transition is `null → string` (lazy loading); since we don't cache null, first real access parses naturally
- Item replacement (`targetArray[index] = newItem`) creates a new object without cache
- `unloadSession()` deletes the entire array

## Changes

### New utilities
- `getParsedContent(item)` — exported from a utils file or the store
- `hasContent(item)` — same location

### `recomputeVisualItems` (data.js)
- Backward walk: use `getParsedContent(item)` instead of `JSON.parse(item.content)`
- Working message synthetic: store object directly in `_parsedContent` with `markRaw()`, no `JSON.stringify` in `content`

### `setOptimisticMessage` (data.js)
- Store object directly in `_parsedContent` with `markRaw()`, no `JSON.stringify` in `content`

### `computeVisualItems` (visualItems.js)
- No change: continues propagating `item.content` as-is. `getParsedContent` works on visual items too (same mechanism: has `.content`, caches `._parsedContent` on first access)

### `SessionItem.vue`
- `parsedContent` computed uses `getParsedContent(item)` instead of `JSON.parse(props.content)` (will need to receive the visual item object or enough context)

### `_migrateInternalSuffixToExternal` (data.js)
- Use `getParsedContent` instead of its own `JSON.parse`

### "Has content?" checks (3 locations)
- `data.js` line 564: `existingItem.content` → `hasContent(existingItem)`
- `SessionItemsList.vue` line 701: `!visualItem.content` → `!hasContent(visualItem)`
- `SessionItemsList.vue` line 882: `v-if="!item.content"` → `v-if="!hasContent(item)"`

### Documentation
- Add rule in `CLAUDE.md` frontend patterns: never access `.content` directly, use `getParsedContent()` and `hasContent()`
- Add warning comment in the Pinia store near `sessionItems` definition

## Visual items propagation

`getParsedContent` is agnostic — works on any object with a `.content` property. Visual items (created by `computeVisualItems`) carry `content: item.content`. When `getParsedContent` is called on a visual item, it parses and caches on the visual item object. Since visual items are recreated on each recompute, the cache lives for one render cycle — but that's enough to avoid double-parsing within a single render.

For synthetic items (no `.content` string, only `._parsedContent`), `computeVisualItems` propagates both `content` and `_parsedContent` so the visual item carries the pre-parsed data.
