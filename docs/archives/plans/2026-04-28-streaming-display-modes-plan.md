# Streaming Items in Display Modes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make synthetic streaming items participate in the existing grouping/visibility system so simplified mode collapses streaming thinking blocks into their proper group, and conversation mode hides streaming thinking + non-result streaming text unless the user opens "show details" — while keeping streaming visible (and live) when the user expands a group or opens block details.

**Architecture:** Frontend-only change in two files. (1) `frontend/src/stores/data.js` — `recomputeVisualItems` computes `display_level`, `group_head`, `group_tail`, plus a new `isStreamingResultCandidate` flag on each streaming synthetic item; `_retireStreamingBlocks` gains a transfer step that migrates an expanded "fake group" entry from the synthetic line_num to the real item's group_head. (2) `frontend/src/utils/visualItems.js` — conversation mode replaces the blanket `line_num < 0` bypass with synthetic-kind-aware handling; streaming text passes only when it is the result candidate or when the block is detailed; streaming thinking is filtered out unless the block is detailed. The hot-path `_onBufferDrain` requires no changes (the existing object spread preserves the new metadata).

**Tech Stack:** Vue 3 / Pinia / pure JS. No backend changes. No tests (per project quality shortcut).

**Project quality note:** Per `CLAUDE.md`, this project has no test suite. Each task ends with manual verification in the browser instead of automated tests. Do not introduce a test framework.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/stores/data.js` | Compute streaming-item metadata (`display_level`, `group_head`, `group_tail`, `isStreamingResultCandidate`) at synthetic-item construction time. Transfer `expandedGroups` state at retirement. |
| `frontend/src/utils/visualItems.js` | Conversation-mode filter understands streaming items: text → visible iff result candidate or detailed; thinking → visible iff detailed. Simplified/normal/debug modes work via existing `display_level` + `group_head` machinery. |

No new files. No file moves. No backend touches.

---

## Background — Read Before Starting

Before any task, read these to make sure you have the model right:

1. **`frontend/src/utils/visualItems.js`** — full file. Pay close attention to:
   - Lines 95-178: conversation-mode logic (3 phases)
   - Lines 165-175: the current `item.line_num < 0` bypass that we're nuancing
   - Lines 206-253: simplified-mode group logic (the COLLAPSIBLE branch with `isOwnGroupHead` / `groupHeadIsAlways`)

2. **`frontend/src/stores/data.js`**:
   - Lines 1400-1431: streaming synthetic item injection into `allItems`
   - Lines 2227-2415: streaming block lifecycle (`streamBlockStart` → `streamBlockDelta` → `streamBlockStop` → `streamBlockEnd` and `_retireStreamingBlocks`)
   - Lines 2277-2308: `_onBufferDrain` (the hot path — surgical patch of one visual item)
   - Lines 1620-1645: `toggleExpandedGroup` (calls `recomputeVisualItems`)

3. **`frontend/src/constants.js`** — lines 99-117: `DISPLAY_LEVEL` and `SYNTHETIC_ITEM` definitions.

**Mental model recap:**

- Each streaming block (one per `block_index`, type `'text'` or `'thinking'`) becomes ONE synthetic item with `line_num = -1000 - block_index`.
- A "group" in simplified mode is a set of items sharing a `group_head` line_num. The head is either a `COLLAPSIBLE` item with `group_head === line_num` (own head) or an `ALWAYS` item with `group_tail != null` (suffix-group head).
- `expandedGroups` is an array of `group_head` line_nums that are currently expanded. Stored at `localState.sessionExpandedGroups[sessionId]`.
- `detailedBlocks` is a Set of `user_message` line_nums whose conversation block is in detailed mode. Stored at `localState.sessionDetailedBlocks[sessionId]`.
- Conversation mode shows: user_messages + the last `assistant_message` per non-user block (`keptAssistantLineNums`). It currently passes ALL synthetic items via the `item.line_num < 0` bypass at line 165.

**Result-candidate rule (the key new concept):**

Among the active streaming blocks for the current message, the *latest* `text`-type block is the "result candidate" — it is provisionally treated as if it were the kept (final) `assistant_message` of the current conversation block. As soon as another block starts after it (`tool_use`, another `text`, or a `thinking`), it loses that status and is treated like an intermediate text. This is purely a rule on the live `streamingBlocks[sessionId].blocks` array — no backend signal, no speculation beyond what the array currently shows. `thinking`-type blocks are NEVER result candidates.

---

## Task 1: Add `getStreamingItemMetadata` helper in data.js

**Why first:** Encapsulates the per-block metadata decision in one pure function. Keeps `recomputeVisualItems` readable and makes the logic easy to reason about.

**Files:**
- Modify: `frontend/src/stores/data.js` (around lines 1400-1431, the streaming injection block)

**What this helper does:** given a streaming block, the list of all streaming blocks for the session, and the last real item before streaming (or `null`), return the four metadata properties: `{ display_level, group_head, group_tail, isStreamingResultCandidate }`.

- [ ] **Step 1: Read the surrounding context once more**

Read `frontend/src/stores/data.js` lines 1400-1470 to refresh the exact shape of streaming block iteration and where the synthetic item is built.

- [ ] **Step 2: Add the helper as a free function at the top of data.js**

Place the helper function near the other module-level utilities — search for an existing top-level `function` declaration in the file (e.g., near the imports / before `defineStore(...)`). Add it there. If no such location exists, place it directly above the `defineStore` call.

```javascript
/**
 * Compute display metadata for a streaming synthetic item.
 *
 * Decides display_level / group_head / group_tail / isStreamingResultCandidate
 * based on the block's type and surrounding context, so the synthetic item
 * participates in the existing grouping/visibility logic in visualItems.js.
 *
 * Rules:
 *   - text block:
 *       display_level = ALWAYS, no group.
 *       isStreamingResultCandidate = true iff this is the LAST text block
 *       in the streaming list (not followed by any later block of any type).
 *   - thinking block:
 *       display_level = COLLAPSIBLE.
 *       group_head:
 *         - if the last real item is in an open group (COLLAPSIBLE with
 *           group_head set, OR ALWAYS with group_tail set), join it.
 *         - otherwise, become own group head (group_head = self.line_num).
 *       group_tail = null (a streaming thinking is never a suffix anchor).
 *
 * @param {Object} block - a streaming block: { blockIndex, blockType, ... }
 * @param {Object} streaming - the full streaming state: { messageId, blocks: [...] }
 * @param {Object|null} lastRealItem - last item in sessionItems before streaming
 *   was injected, or null if there is none.
 * @param {number} streamingLineNum - the synthetic line_num for this block
 *   (= SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - block.blockIndex).
 * @returns {{display_level: number, group_head: number|null, group_tail: number|null, isStreamingResultCandidate: boolean}}
 */
function getStreamingItemMetadata(block, streaming, lastRealItem, streamingLineNum) {
    if (block.blockType === 'text') {
        // Result candidate iff this is the latest text block in the list.
        // "Latest" = no block (text or thinking) appears after this one.
        const blocks = streaming.blocks
        const myIdx = blocks.indexOf(block)
        let isLatestText = block.blockType === 'text'
        if (isLatestText) {
            for (let i = myIdx + 1; i < blocks.length; i++) {
                // Any later block (thinking or text) demotes this one.
                isLatestText = false
                break
            }
        }
        return {
            display_level: DISPLAY_LEVEL.ALWAYS,
            group_head: null,
            group_tail: null,
            isStreamingResultCandidate: isLatestText,
        }
    }

    // Thinking block.
    let groupHead = streamingLineNum  // default: own fake group
    if (lastRealItem) {
        if (
            lastRealItem.display_level === DISPLAY_LEVEL.COLLAPSIBLE &&
            lastRealItem.group_head != null
        ) {
            // Join the existing COLLAPSIBLE group.
            groupHead = lastRealItem.group_head
        } else if (
            lastRealItem.display_level === DISPLAY_LEVEL.ALWAYS &&
            lastRealItem.group_tail != null
        ) {
            // Continue the ALWAYS-suffix group started by lastRealItem.
            groupHead = lastRealItem.line_num
        }
    }
    return {
        display_level: DISPLAY_LEVEL.COLLAPSIBLE,
        group_head: groupHead,
        group_tail: null,
        isStreamingResultCandidate: false,
    }
}
```

**Notes:**
- The "any later block" loop looks redundant (it always sets to `false` and breaks). It's written this way for clarity. Feel free to simplify to `const isLatestText = myIdx === blocks.length - 1` if and only if you've verified `blocks` is always in order of `blockIndex` ascending — which it is, because `streamBlockStart` pushes new blocks at the end. So you may simplify:

```javascript
const isLatestText = myIdx === blocks.length - 1
```

Use the simpler version.

- [ ] **Step 3: Verify the import line at the top of `data.js` already imports `DISPLAY_LEVEL`**

Run:

```bash
grep -n "DISPLAY_LEVEL" /home/twidi/dev/twicc-poc/frontend/src/stores/data.js | head -5
```

Expected: at least one line showing `import { ... DISPLAY_LEVEL ... } from '../constants'` (or similar). If not imported, add it.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(streaming): add getStreamingItemMetadata helper

Computes display_level, group_head, group_tail, and isStreamingResultCandidate
for streaming synthetic items based on block type and surrounding context.
Standalone helper, not yet wired."
```

---

## Task 2: Wire the helper into `recomputeVisualItems`

**Files:**
- Modify: `frontend/src/stores/data.js` (lines 1400-1431, replace the streaming injection block)

- [ ] **Step 1: Replace the streaming injection block with metadata-aware version**

Find the block at lines 1400-1431. The new version:

```javascript
            // Inject streaming blocks as synthetic items (one per active block).
            // Streaming blocks appear BEFORE the working message in the list.
            const streaming = this.localState.streamingBlocks[sessionId]
            const streamingItems = []
            let hasActiveTextStreaming = false
            if (streaming?.blocks.length) {
                const { baseLineNum, kind: streamingSyntheticKind } = SYNTHETIC_ITEM.STREAMING_BLOCK
                // Last real item before any streaming is appended (used for
                // group inheritance decisions on streaming thinking blocks).
                const lastRealItem = items.length > 0 ? items[items.length - 1] : null
                for (const block of streaming.blocks) {
                    if (!block.stopped && block.blockType === 'text') hasActiveTextStreaming = true
                    const lineNum = baseLineNum - block.blockIndex
                    const displayText = block.displayedText ?? block.text
                    const contentBlock = block.blockType === 'thinking'
                        ? { type: 'thinking', thinking: displayText, streaming: !block.stopped }
                        : { type: 'text', text: displayText }
                    const meta = getStreamingItemMetadata(block, streaming, lastRealItem, lineNum)
                    const streamItem = {
                        line_num: lineNum,
                        content: null,
                        kind: 'assistant_message',
                        syntheticKind: streamingSyntheticKind,
                        display_level: meta.display_level,
                        group_head: meta.group_head,
                        group_tail: meta.group_tail,
                        isStreamingResultCandidate: meta.isStreamingResultCandidate,
                    }
                    setParsedContent(streamItem, {
                        type: 'assistant',
                        syntheticKind: streamingSyntheticKind,
                        message: { role: 'assistant', content: [contentBlock] },
                    })
                    streamingItems.push(streamItem)
                    allItems = allItems === items ? [...items, streamItem] : [...allItems, streamItem]
                }
            }
```

**Key changes vs. current code:**
1. `lastRealItem` declared once before the loop.
2. `meta` computed via the new helper.
3. `display_level`, `group_head`, `group_tail` come from `meta` (no longer hardcoded to `ALWAYS` / `null` / `null`).
4. New property `isStreamingResultCandidate` added to the synthetic item.

- [ ] **Step 2: Make sure the result-candidate flag survives the visual-item stabilization**

The stabilization code at lines 1527-1549 reuses cached visual items when `visualItemEqual` returns true. `visualItemEqual` does a property-by-property comparison ignoring `_parsedContent` only. The new `isStreamingResultCandidate` property is only on the *source item*, not propagated to the visual item by `makeVisualItem` (which only copies `lineNum`, `content`, `kind`, `groupHead`, `groupTail` and the `extras`). So we don't need to worry about `visualItemEqual` for this flag — it never appears on visual items.

However, `visualItems.js` reads the flag from the *source item* during iteration, not from the visual item. Verify this assumption by re-reading the conv-mode loop (lines 143-176): the loop iterates `items`, not `result`. ✓

No code change for this step — just verification. Move on.

- [ ] **Step 3: Restart the dev server and visually confirm nothing breaks yet**

Tell the user the backend may need a restart. **Do not restart it yourself** (per `CLAUDE.md`: server restart is reserved to the user). Ask the user to restart the backend and then verify the app still loads and existing sessions still render correctly. The behavior shouldn't be different yet — visualItems.js hasn't been updated — but no regression should appear either.

Frontend reload via Vite HMR happens automatically on save.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(streaming): assign group/display metadata to streaming items

Streaming synthetic items now carry display_level, group_head, group_tail,
and isStreamingResultCandidate based on block type and the last real item.
visualItems.js still uses the old line_num<0 bypass — wired in next commit."
```

---

## Task 3: Update visualItems.js conversation mode

**Files:**
- Modify: `frontend/src/utils/visualItems.js` (the `if (mode === DISPLAY_MODE.CONVERSATION)` branch, lines 95-178)

**Goal:** Replace the blanket `item.line_num < 0` synthetic bypass with a rule that lets non-streaming synthetic items through (working-message, optimistic-user, starting-assistant) but applies the streaming-specific rule to streaming blocks.

- [ ] **Step 1: Update the non-detailed branch (around line 165)**

Find the line:

```javascript
            } else if (item.line_num < 0 || (item.kind === 'assistant_message' && keptAssistantLineNums.has(item.line_num))) {
```

Replace the whole `else if` body (lines 165-175) with:

```javascript
            } else if (
                isVisibleInConversation(item, keptAssistantLineNums)
            ) {
                // Non-detailed mode: show kept assistant_messages, non-streaming synthetic
                // items (working-message, optimistic, starting), and streaming text blocks
                // that are currently the "result candidate". Streaming thinking and
                // non-candidate streaming text are filtered out (only visible in detailed).
                const visualItem = makeVisualItem(item, { groupHead: null, groupTail: null })
                // First visible non-user item of this block gets the toggle
                // (only if the block has 2+ visible items, otherwise toggle is useless)
                if (!togglePlaced.has(blockId) && (blockVisibleCount.get(blockId) || 0) > 1) {
                    visualItem.detailToggleFor = blockId
                    togglePlaced.add(blockId)
                }
                result.push(visualItem)
            }
```

- [ ] **Step 2: Add the `isVisibleInConversation` helper at the top of visualItems.js**

Add this function above `computeVisualItems` (after the imports, before line 6's docstring or after it — match existing module structure):

```javascript
/**
 * Decide whether an item is visible in conversation mode (non-detailed branch).
 *
 * Visible items:
 *   - kept assistant_messages (last per non-user block)
 *   - non-streaming synthetic items (line_num < 0, syntheticKind != streaming-block):
 *     working-message, optimistic-user-message, starting-assistant-message
 *   - streaming text blocks that are the current result candidate
 *     (= last text block in streamingBlocks, no later block has been opened)
 *
 * Hidden items:
 *   - real assistant_messages that aren't the kept one
 *   - streaming thinking blocks (always hidden in non-detailed conversation mode)
 *   - streaming text blocks that have been demoted (a later block exists)
 *   - everything else
 *
 * @param {Object} item - source session item (real or synthetic)
 * @param {Set<number>} keptAssistantLineNums - line_nums of kept assistant_messages
 * @returns {boolean}
 */
function isVisibleInConversation(item, keptAssistantLineNums) {
    if (item.kind === 'assistant_message' && item.line_num >= 0 && keptAssistantLineNums.has(item.line_num)) {
        return true
    }
    if (item.line_num >= 0) return false
    // synthetic item
    if (item.syntheticKind === 'streaming-block') {
        return item.isStreamingResultCandidate === true
    }
    // other synthetic items (working, optimistic, starting): always visible
    return true
}
```

**Notes:**
- The string `'streaming-block'` matches `SYNTHETIC_ITEM.STREAMING_BLOCK.kind` from constants.js. Hardcode the string here to avoid an extra import; if you prefer to import, do so but verify there are no circular-import concerns (`visualItems.js` already imports from `'../constants'` so this is safe — feel free to use `SYNTHETIC_ITEM.STREAMING_BLOCK.kind` instead of the literal). Use the imported value for clarity:

```javascript
import { DISPLAY_LEVEL, DISPLAY_MODE, SYNTHETIC_ITEM } from '../constants'
```

and then:

```javascript
if (item.syntheticKind === SYNTHETIC_ITEM.STREAMING_BLOCK.kind) {
```

- [ ] **Step 3: Verify the detailed branch (around line 153) still works for streaming items**

The detailed branch at lines 153-164 currently does:

```javascript
} else if (isDetailed) {
    if (item.display_level == null || item.display_level !== DISPLAY_LEVEL.DEBUG_ONLY) {
        const visualItem = makeVisualItem(item)
        ...
    }
}
```

After our changes, streaming thinking has `display_level = COLLAPSIBLE`. That passes the filter (`COLLAPSIBLE !== DEBUG_ONLY` ✓). Streaming text has `display_level = ALWAYS` — passes too. ✓

**No code change here.** Just verify by re-reading the branch.

- [ ] **Step 4: Verify simplified mode handles streaming thinking correctly**

The simplified-mode branch at line 226-253 handles `COLLAPSIBLE` items:
- A streaming thinking with `group_head === self.line_num` triggers the `isOwnGroupHead` branch (line 234) → renders with `isGroupHead: true`, hidden when not expanded. ✓
- A streaming thinking with `group_head` pointing to an ALWAYS triggers the `groupHeadIsAlways` branch (line 241) → only shown if expanded. ✓
- A streaming thinking with `group_head` pointing to another COLLAPSIBLE (joining a regular group) triggers the `isExpanded` branch (line 248) → only shown if expanded. ✓

`alwaysLineNums` and `groupSizes` are computed from `items` at lines 71-82 — synthetic streaming items participate naturally because they have `display_level` and `group_head` set. ✓

`expandedGroups` parameter contains line_nums; if user expanded the synthetic line_num (fake group), it's there and `isExpanded` is true. ✓

**No code change here.** Just verify.

- [ ] **Step 5: Manual test — simplified mode, streaming thinking creates a fake group**

Tell the user to:
1. Start a session with thinking enabled.
2. Switch to simplified display mode.
3. Send a message that triggers thinking.
4. **Expected:** during thinking, no thinking text is visible — instead, a collapsed group toggle appears (showing the thinking block as a collapsed group). Clicking the toggle expands and reveals the streaming thinking text live.
5. **Expected:** when thinking ends and text streams, the text appears as ALWAYS (visible) below the (still-collapsed by default) thinking group.

If the toggle does not appear or thinking text is visible without expansion, the metadata is wrong — re-check Task 1's helper.

- [ ] **Step 6: Manual test — simplified mode, streaming thinking joins existing group**

This requires a session where the previous turn ended with an item that already has group metadata set (e.g., a tool_result that's COLLAPSIBLE with group_head set). It's hard to set up by hand. As a proxy:
1. In a session with multiple turns, find a tool-heavy turn.
2. Send a follow-up message that will produce thinking.
3. **Expected:** the streaming thinking joins the previous group's expansion state — i.e., if the previous group is expanded, the streaming thinking is visible; if collapsed, it's hidden.

If you can't reproduce the joining case live, accept Task 1 logic as untested and document this as a manual-verification gap.

- [ ] **Step 7: Manual test — conversation mode, only result-candidate text streams visibly**

Tell the user to:
1. Switch to conversation display mode.
2. Send a message that produces thinking + tool use + final text (e.g., "search the codebase for X and tell me what you find").
3. **Expected during the run:**
   - Thinking blocks: invisible.
   - First text block (before tool use): visible while it's the latest streaming block. As soon as the tool_use starts, this text disappears from the conversation view (now hidden as non-result).
   - Tool blocks: invisible.
   - Final text block (after tool result): visible (latest result candidate).
4. **Expected if user clicks "show details" on the current block during the run:** all streaming items appear (thinking, intermediate text, final text).
5. **Expected after streaming ends:** the conversation view shows only the user message + the final assistant text. Same as before this change.

If intermediate text never disappears, the result-candidate logic is wrong — re-check Task 1's `isLatestText` computation.

- [ ] **Step 8: Manual test — normal mode unchanged**

Tell the user to:
1. Switch to normal display mode.
2. Send a message with thinking + text.
3. **Expected:** all streaming items visible (thinking and text), no group collapsing. Same as before this change.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/utils/visualItems.js
git commit -m "feat(streaming): apply mode-aware visibility to streaming items

Conversation mode now hides streaming thinking and demoted streaming text
unless the block is in detailed mode. Simplified mode collapses streaming
thinking into its proper group via group_head metadata."
```

---

## Task 4: Transfer expanded-group state in `_retireStreamingBlocks`

**Files:**
- Modify: `frontend/src/stores/data.js` (around lines 2370-2415, `_retireStreamingBlocks` function)

**Why:** When a streaming thinking block is its own fake group_head (synthetic line_num like `-1000`) and the user has expanded it, that line_num is in `sessionExpandedGroups[sessionId]`. After retirement, the synthetic item disappears and the real persisted item arrives with its real `group_head` (a positive line_num). To preserve "expanded" state across the swap, we need to migrate the entry.

**Important:** This only applies to thinking blocks that started a *fake group* (i.e., had `group_head === streamingLineNum`). Thinking blocks that joined an existing group used the existing group's line_num for `group_head`, which is already a real positive line_num — no migration needed.

- [ ] **Step 1: Read `_retireStreamingBlocks` start to finish**

Re-read `frontend/src/stores/data.js` lines 2370-2415. The thinking-specific block (lines 2389-2403) currently transfers wa-details state. We'll add expandedGroups transfer right after it.

- [ ] **Step 2: Augment the thinking-specific transfer block**

Inside the `if (block.blockType === 'thinking')` branch (lines 2389-2403), AFTER the existing wa-details transfer, add:

```javascript
                        // Transfer expandedGroups state for fake-group case.
                        // If this thinking block was its own group_head (fake group)
                        // and the user expanded it, migrate that entry to the real
                        // item's group_head so expansion persists across the swap.
                        const expanded = this.localState.sessionExpandedGroups[sessionId]
                        if (expanded && expanded.length > 0) {
                            const streamingLineNum = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - block.blockIndex
                            const idxInExpanded = expanded.indexOf(streamingLineNum)
                            if (idxInExpanded !== -1) {
                                // Determine the real group_head this thinking block
                                // belongs to. Look at the real item's group_head; if
                                // null (no group), drop the entry; else add the real
                                // group_head if not already there.
                                const realGroupHead = item.group_head
                                expanded.splice(idxInExpanded, 1)
                                if (realGroupHead != null && !expanded.includes(realGroupHead)) {
                                    expanded.push(realGroupHead)
                                }
                            }
                        }
```

**Why this works:**
- If the thinking became part of a real COLLAPSIBLE group with `group_head` pointing to some real line_num, that line_num becomes the new "expansion key".
- If the real item ends up with `group_head === null` (no group at all in the finalized data — possible if the streaming was a "fake group" but the persisted layout doesn't actually group it), we drop the expansion entry. This is acceptable because there's no group to be expanded anymore.
- If the synthetic line_num was NOT in `expanded` (user never expanded it), we do nothing.

- [ ] **Step 3: Verify `SYNTHETIC_ITEM` is in scope**

Search for the import at the top of data.js:

```bash
grep -n "SYNTHETIC_ITEM" /home/twidi/dev/twicc-poc/frontend/src/stores/data.js | head -5
```

Expected: at least one import line. If the constant is referenced inside the function but not imported, add it to the import statement at the top of `data.js`.

- [ ] **Step 4: Manual test — fake-group expansion survives retirement**

Tell the user to:
1. Switch to simplified display mode.
2. Send a message that produces thinking (where the previous item is NOT in a group, so the streaming creates a fake group).
3. Expand the fake group during streaming.
4. **Expected:** the streaming thinking text becomes visible inside the expanded group.
5. Wait until the message finishes and the real item is persisted (a second or two after `streamBlockEnd`).
6. **Expected:** the group remains expanded — the thinking text stays visible inside its group. There is NO collapse-flicker. The group toggle now shows the real group's `group_head` (the persisted line_num).

If the group collapses on retirement, the migration is not happening — re-check the if-condition and the imports.

- [ ] **Step 5: Manual test — joining-group case (no migration needed)**

Tell the user to:
1. In a session where the previous turn ends with an item already in a real group (hard to set up; if you can't, mark this test "skipped — no fixture available" and move on).
2. Send a follow-up that produces thinking.
3. Expand the existing group during streaming.
4. **Expected:** the group stays expanded after the real item arrives — because the streaming used the real group's existing line_num as `group_head`, no migration was needed.

Skip with note if not reproducible live.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(streaming): migrate expanded-group state on thinking retirement

When a streaming thinking block was its own fake group_head and the user
expanded it, transfer the expansion entry from the synthetic line_num to
the real item's group_head so the group stays expanded across the swap."
```

---

## Task 5: Sanity sweep + final manual run

**Files:** none (verification only)

- [ ] **Step 1: Re-read both modified files for stray issues**

Read the diff:

```bash
git -C /home/twidi/dev/twicc-poc diff main -- frontend/src/stores/data.js frontend/src/utils/visualItems.js
```

Look for:
- Unused imports
- Forgotten `console.log` from debugging
- Comments that no longer match the code
- Indentation drift

- [ ] **Step 2: Verify HMR didn't break (no full-page reload introduced)**

Per `CLAUDE.md`, HMR is sensitive to circular imports. The changes only touch `data.js` and `visualItems.js`. `visualItems.js` is imported by `data.js` (already the case). No new cross-imports. ✓

- [ ] **Step 3: Final end-to-end manual test**

Tell the user to run a full session in each display mode (normal, simplified, conversation) with a message that triggers thinking + tools + final text. Confirm:

| Mode | Thinking visible? | Intermediate text visible? | Final text visible? |
|---|---|---|---|
| normal (detailed) | yes (live streaming) | yes | yes |
| simplified, group collapsed | no | yes | yes |
| simplified, group expanded | yes (live) | yes | yes |
| conversation, block closed | no | only while it's the latest block, disappears on next block | yes (latest = result) |
| conversation, block "show details" | yes | yes | yes |

If any cell mismatches, debug Tasks 1-3 logic.

- [ ] **Step 4: Update CHANGELOG.md**

Per project convention (see `CHANGELOG.md` if it exists), add an entry under `[Unreleased]`:

```markdown
- Streaming display modes: thinking blocks now collapse into their group in simplified mode; conversation mode hides intermediate streaming text and thinking unless the block is in "show details".
```

Verify by running:

```bash
head -30 /home/twidi/dev/twicc-poc/CHANGELOG.md
```

- [ ] **Step 5: Commit changelog**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note streaming display modes update"
```

---

## Out of scope (do not do)

- **Backend changes.** No new SDK-level signals. The result-candidate decision is purely a frontend interpretation of the live `streamingBlocks` array.
- **Tests.** Per `CLAUDE.md`, this project skips automated tests.
- **Refactoring `_onBufferDrain` or the stabilization layer.** They already preserve all visual-item properties via object spread — no change needed.
- **Reworking `keptAssistantLineNums` to include synthetic items.** We deliberately keep them separate; the `isVisibleInConversation` helper handles synthetics outside `keptAssistantLineNums` to keep the structural meaning of `keptAssistantLineNums` clean.
- **Persisting `expandedGroups` or `detailedBlocks` across page reloads.** Out of scope for this plan.

## Known acceptable side effects

- In conversation mode (closed), if a streaming text block A starts as result candidate (visible), then a tool_use block B starts after it, A becomes hidden retroactively. The user sees text appear and then disappear. This is intentional — there is no way during streaming to know in advance whether A is the final result or an intermediate text. The user can always click "show details" to see A again. Documented as the agreed trade-off.
