# KeepAlive LRU Cache for Session Views

**Date:** 2026-02-07
**Status:** COMPLETED

## Overview

Implement Vue's `<KeepAlive>` component to cache the last 5 session views in an LRU (Least Recently Used) fashion. When switching between sessions, the entire session panel (header, items list, virtual scroller, message input, dev tools panel) is preserved in memory instead of being destroyed and recreated. This eliminates re-rendering, preserves scroll position, user state, open terminals, and enables near-instant session switching.

## Problem

Currently, when a user switches from Session A to Session B:

1. Vue Router **destroys** the entire `SessionView` instance for Session A
2. Vue Router **creates** a brand new `SessionView` instance for Session B
3. The VirtualScroller rebuilds from scratch (~50-200+ DOM nodes)
4. Scroll-to-bottom logic runs with `visibility: hidden` until stable (visible flash)
5. DevTools panel height must be restored from localStorage
6. All component-local state is lost (auto-hide header/footer, viewport detection, etc.)

While the Pinia store retains session data (items, draft messages, expanded groups, open tabs), the DOM and all component-internal state are lost. Every session switch costs a full re-render cycle.

## Solution

Wrap the `<router-view>` in ProjectView with `<KeepAlive :max="5">` to maintain up to 5 session view DOM trees in memory. Vue's KeepAlive has built-in LRU eviction via the `:max` prop — no custom LRU implementation is needed.

### How Vue KeepAlive Works (Technical Detail)

KeepAlive does **not** use `display: none` or `visibility: hidden`. The mechanism is entirely DOM-structural:

1. **Deactivation**: When a component is deactivated, Vue calls `parent.insertBefore(child, storageContainer)` where `storageContainer` is a detached `<div>` created by KeepAlive and **never inserted into the document**. The browser physically removes the DOM nodes from the live document and reparents them under this detached container in memory.

2. **Activation**: The reverse operation moves DOM nodes **back from** the detached container into the live document at the correct position.

3. **Eviction** (when `:max` is exceeded): The least recently used cached component receives a real `unmount`, destroying it completely.

### Consequences for Layout and Observers

| Property | Value when detached |
|---|---|
| `offsetHeight` / `offsetWidth` | **0** |
| `getBoundingClientRect()` | **All zeros** |
| `scrollTop` / `scrollLeft` | **Value retained** on the DOM node (it's an element property, not a layout calculation) |
| ResizeObserver | Fires callback with **size 0×0**, remains attached |
| IntersectionObserver | Fires with **isIntersecting: false**, remains attached |

### What Gets Preserved (benefit)

| Element | Currently preserved? | With KeepAlive |
|---|---|---|
| Session data (items, messages) | Yes (Pinia store) | Yes (Pinia store) |
| Scroll position in VirtualScroller | **No** (lost, rebuilt from scratch) | **Yes** (DOM preserved, scrollTop retained) |
| Expanded/collapsed groups | Yes (Pinia store, but DOM rebuilt) | **Yes** (DOM preserved) |
| Draft message text | Yes (Pinia store) | **Yes** + cursor position preserved |
| Auto-hide header/footer state | **No** (lost) | **Yes** (component state preserved) |
| DevTools panel open/height | Partially (localStorage) | **Yes** (DOM preserved) |
| Terminal state (future) | **No** (destroyed) | **Yes** (instance preserved) |
| Web components internal state | **No** (destroyed) | **Yes** (wa-tab-group active tab, etc.) |

## Architecture

### Insertion Point

The KeepAlive wraps the `<router-view>` inside **ProjectView.vue**:

```
ProjectView.vue (NOT inside KeepAlive)
  ├── <aside> Sidebar (SessionList, settings, etc.)
  └── <main>
        └── <KeepAlive :max="5">            ← NEW
              └── <SessionView :key="sessionId">  ← one cached instance per session
                    ├── <input checkbox> (devtools toggle)
                    ├── <SessionHeader>
                    ├── <wa-split-panel>
                    │     ├── SessionItemsList (VirtualScroller + MessageInput)
                    │     └── DevToolsPanel (Git, Files, Terminal)
                    └── empty-state spinner
```

### Component Tree Inside KeepAlive Boundary

Everything below SessionView is cached together. Components **outside** the boundary (ProjectView, sidebar, SessionRenameDialog) are unaffected.

```
SessionView
  ├── SessionHeader
  │     └── wa-tooltip (×18)
  ├── wa-split-panel (session content + devtools)
  │     ├── wa-tab-group (session tabs)
  │     │     ├── SessionItemsList (main session)
  │     │     │     ├── VirtualScroller
  │     │     │     │     └── VirtualScrollerItem (×N)
  │     │     │     │           └── SessionItem
  │     │     │     │                 └── ToolUseContent (has polling intervals)
  │     │     │     ├── MessageInput
  │     │     │     └── ProcessIndicator
  │     │     └── SessionContent (×N subagent tabs)
  │     │           ├── SessionHeader
  │     │           └── SessionItemsList
  │     └── DevTools panel
  │           ├── DevToolsGitPanel
  │           ├── DevToolsFilesPanel (has API watchers)
  │           └── DevToolsTerminalPanel
```

## Compatibility Analysis

### File-by-file audit

#### SessionView.vue

**`document.querySelector` / `getElementById` (CRITICAL)**:
- Line 92: `document.querySelector('.session-content-split')` in `resetDevToolsPanelToDefault()` — with multiple KeepAlive instances, this matches the wrong instance's split panel
- Line 129: `document.querySelector('.session-content-split')` in `onMounted()` — same issue
- Line 143: `document.querySelector('.session-content-split')` in `onUnmounted()` — same issue
- Line 351: `document.getElementById('devtools-panel-toggle-state')` in `handleDevToolsPanelReposition()` — duplicate IDs with multiple instances

**HTML IDs (must be made unique per session)**:
- Line 402: `id="devtools-panel-toggle-state"` — checkbox, referenced by `<label for=>`
- Line 517: `id="devtools-panel-toggle-label"` — label, referenced by `wa-tooltip for=`

**CSS `:has()` selectors — NOT impacted**: All selectors use the **class** `.devtools-panel-toggle-checkbox`, not the ID. They are scoped to `.session-view:has(...)` which is relative to each instance's own `.session-view` ancestor. Multiple KeepAlive instances each have their own `.session-view`, so the CSS works correctly without changes.

**Lifecycle hooks to migrate**:
- `onMounted` (L124-138): `window.addEventListener('resize', checkViewportHeight)`, split panel height restore + pointerdown listener → migrate to `onActivated`
- `onUnmounted` (L140-145): cleanup resize + pointerdown listeners → migrate to `onDeactivated`

**Event-triggered code (safe)**: `handleDevToolsTabShow` uses `event.target.closest()` / `querySelectorAll` scoped to the event target — safe with multiple instances.

#### SessionHeader.vue

**HTML IDs (18 static IDs, all must include sessionId)**:
All at format `session-header-*`, used as targets for `wa-tooltip for=`:
`archived-tag`, `pin-button`, `archive-button`, `rename-button`, `title`, `git-directory`, `git-branch`, `messages`, `lines`, `mtime`, `cost`, `cost-breakdown`, `model`, `context`, `process-duration`, `process-memory`, `process-indicator`, `stop-button`

**Lifecycle hooks to migrate**:
- `onMounted` (L182-186): starts `setInterval` every 1 second for duration display → migrate to `onActivated`
- `onUnmounted` (L188-192): clears the interval → migrate to `onDeactivated`
- Without migration, all 5 cached sessions would have active 1-second timers running in background.

**provide/inject**: `inject('openRenameDialog')` from ProjectView — safe, provider is outside KeepAlive boundary.

#### SessionItemsList.vue

**No `document.querySelector` or HTML IDs** — clean.

**Lifecycle hooks to migrate**:
- `onUnmounted` (L176-185): clears `temporaryIndicatorTimer` and `stabilityTimeoutId` → migrate to `onDeactivated`

**Watchers requiring `isActive` guard**:
- `watch(visualItems.length)` (L379): auto-scrolls to bottom on new items. Would trigger on deactivated instances when WebSocket pushes new items. Must not call `scrollToBottomUntilStable()` when inactive.
- `watch([sessionId, session])` (L282): loads data and calls `scrollToBottomUntilStable`. Must not scroll when inactive.
- `watch(compute_version_up_to_date)` (L365): triggers load + scroll on compute completion. Must not scroll when inactive.
- `watch(processState)` (L143): starts a temporary indicator timer. Low impact but should be guarded.

**Key behavior note**: The Pinia store updates (new items via WebSocket, visualItems recalculation) should continue happening in background — only DOM-manipulating operations (scroll, stability detection) need to be guarded.

#### SessionItem.vue

**HTML IDs (3 dynamic IDs, insufficiently scoped)**:
- `` :id="`json-toggle-${lineNum}`" `` — two sessions could share the same lineNum
- `` :id="`json-toggle-hide-${lineNum}`" `` — same concern
- `` :id="`line-number-${lineNum}`" `` — same concern

Must include sessionId: `` `json-toggle-${sessionId}-${lineNum}` ``

#### MessageInput.vue

**HTML IDs**: `id="attachments-popover-trigger"` (L388) — used by `wa-popover for=`. Must include sessionId.

**Composable concern**: `useVisualViewport()` uses a reference-counted singleton pattern with `onMounted`/`onUnmounted`. With KeepAlive, `onUnmounted` only fires on eviction. The singleton pattern means only one global listener exists regardless of instance count — this is acceptable.

#### VirtualScroller.vue — NO CHANGES NEEDED

The VirtualScroller has existing protections that handle the KeepAlive detach/reattach cycle:

1. **`batchUpdateItemHeights` rejects 0-height values** (useVirtualScroll.js L357):
   ```js
   if (newHeight === 0) { continue }
   ```
   When KeepAlive detaches the DOM, ResizeObserver fires with 0-height for all items. These updates are silently ignored. The height cache retains the real measurements.

2. **Container ResizeObserver detects hidden→visible transitions** (L350-358):
   ```js
   const wasHidden = lastKnownViewportHeight === 0
   const isNowVisible = height > 0
   if (wasHidden && isNowVisible && hasMounted) {
       invalidateZeroHeights()
   }
   ```
   This code was written for `wa-tab-panel` (`display: none` switching). KeepAlive produces the same sequence: container reports 0 → container reports real height. The recovery logic triggers automatically.

3. **scrollTop is retained**: The browser preserves `scrollTop` on the DOM node even when detached. When reattached, the scroll position is naturally restored.

**The KeepAlive detach/reattach cycle is structurally identical to wa-tab-panel switching**, which the VirtualScroller already handles correctly. No modifications are needed.

#### VirtualScrollerItem.vue — NO CHANGES NEEDED

Lifecycle is driven by VirtualScroller's render range, not by KeepAlive. Items register/unregister with the shared ResizeObserver via `onMounted`/`onUnmounted`, which is correct — these items are created/destroyed by the VirtualScroller's virtual rendering, not by KeepAlive.

#### SessionContent.vue — NO CHANGES NEEDED

Thin wrapper component (header + items list for subagent tabs). No lifecycle hooks, no observers, no IDs.

#### DevToolsGitPanel.vue — NO CHANGES NEEDED

Placeholder component. No KeepAlive concerns.

#### DevToolsFilesPanel.vue

**All `querySelector` calls are scoped** to `treeContainerRef.value?.querySelector(...)` — safe with multiple instances.

**Watcher to guard**:
- `watch([projectId, sessionId, directory, showHidden, showIgnored])` (L163): fetches tree data via API. If store updates change props on a deactivated instance, this would trigger unnecessary API calls. Should check `isActive` before fetching.

#### DevToolsTerminalPanel.vue — NO CHANGES NEEDED

Placeholder component. No KeepAlive concerns.

#### ToolUseContent.vue (deeply nested in SessionItem)

**Polling intervals**:
- `pollingIntervalId` (L121): `setInterval` polling for tool use results
- `agentPollingIntervalId` (L304): `setInterval` polling for agent link availability
- Both cleaned up in `onUnmounted` (L191) — with KeepAlive, `onUnmounted` only fires on LRU eviction

**Risk**: If a tool_use is pending (polling active) when the session is deactivated, the polling continues making API calls in background. The polling is self-limiting (stops when result arrives), so the impact is wasted API calls, not a bug.

**Priority**: Low — can be addressed in a later optimization phase.

#### SessionRenameDialog.vue — NOT IMPACTED

Lives in ProjectView (outside KeepAlive boundary). Single shared instance. Dynamic `formId` scoped by session ID.

#### useVisualViewport.js — NOT IMPACTED

Reference-counted singleton pattern. Global viewport tracking that should remain active regardless of KeepAlive state.

### Summary of Changes Required

**24 HTML IDs to make unique per session:**
| Component | Count | Pattern |
|---|---|---|
| SessionView | 2 | `devtools-panel-toggle-state`, `devtools-panel-toggle-label` |
| SessionHeader | 18 | `session-header-*` (tooltip targets) |
| MessageInput | 1 | `attachments-popover-trigger` |
| SessionItem | 3 | `json-toggle-*`, `line-number-*` (add sessionId alongside lineNum) |

**3 `document.querySelector`/`getElementById` to replace with template refs:**
| Component | Count | What |
|---|---|---|
| SessionView | 4 | `.session-content-split` (×3), `#devtools-panel-toggle-state` (×1) |

**6 lifecycle hooks to migrate:**
| Component | From | To | What |
|---|---|---|---|
| SessionView | `onMounted` | `onActivated` | window resize listener + split panel setup |
| SessionView | `onUnmounted` | `onDeactivated` | cleanup listeners |
| SessionHeader | `onMounted` | `onActivated` | start 1s interval |
| SessionHeader | `onUnmounted` | `onDeactivated` | clear interval |
| SessionItemsList | `onUnmounted` | `onDeactivated` | clear timers |

**5 watchers to guard with `isActive`:**
| Component | Watch target | Risk |
|---|---|---|
| SessionItemsList | `visualItems.length` | Auto-scroll on detached DOM |
| SessionItemsList | `[sessionId, session]` | Scroll-to-bottom on detached DOM |
| SessionItemsList | `compute_version_up_to_date` | Load + scroll on detached DOM |
| SessionItemsList | `processState` | Timer on inactive instance |
| DevToolsFilesPanel | `[projectId, sessionId, ...]` | Unnecessary API fetch |

**Components requiring NO changes:**
VirtualScroller, VirtualScrollerItem, SessionContent, DevToolsGitPanel, DevToolsTerminalPanel, SessionRenameDialog, useVisualViewport.

## `v-if` vs `v-show` for Empty State

Currently, ProjectView uses `v-if="sessionId"` on the `<router-view>`. When no session is selected, the `v-if` removes the entire `<router-view>` from the DOM, which would **destroy the KeepAlive cache**.

The solution is to use `v-show` so that the KeepAlive remains mounted (its cache is preserved) but visually hidden:

```vue
<div v-show="sessionId" class="main-content">
    <router-view v-slot="{ Component }">
        <KeepAlive :max="5">
            <component :is="Component" :key="route.params.sessionId" />
        </KeepAlive>
    </router-view>
</div>
<div v-if="!sessionId" class="empty-state">
    <p>Select a session from the list</p>
</div>
```

With `v-show`, the KeepAlive cache survives when the user deselects all sessions (e.g., filter returns no results, navigating to project root). The last active session stays in the DOM (hidden by `display: none`), and the other 4 cached sessions remain in KeepAlive's detached storage. When the user navigates to a different project, ProjectView itself is re-mounted by the router, naturally purging the cache.

## `:key="sessionId"` on Child Components

Currently, `VirtualScroller` and `MessageInput` inside `SessionItemsList` have `:key="sessionId"`:
```vue
<VirtualScroller :key="sessionId" ... />
<MessageInput :key="sessionId" ... />
```

**Without KeepAlive (current behavior)**: These keys are essential. When the router changes sessionId, Vue Router reuses the same SessionView instance and just updates the route params. The `:key` forces VirtualScroller and MessageInput to be destroyed and recreated for the new session, preventing stale state from the previous session leaking through.

**With KeepAlive**: Each SessionView instance has a fixed, immutable sessionId (thanks to `:key="sessionId"` on the `<component>` wrapper). The `:key="sessionId"` on child components never changes — they are evaluated to the same value for the lifetime of the instance. They are functionally inert but harmless.

**Important**: These keys must NOT be removed before the KeepAlive is in place. Removing them without KeepAlive would break session switching (VirtualScroller would retain the previous session's scroll state, items, etc.). They should only be cleaned up **after** KeepAlive is implemented and verified.

## `isActive` Flag — Provide/Inject Pattern

SessionView creates an `isActive` ref and provides it to all descendants:

```
SessionView
  │ provide('sessionActive', readonly(isActive))
  │ onActivated → isActive = true
  │ onDeactivated → isActive = false
  │
  ├── SessionHeader
  │     inject('sessionActive')
  │     → pause/resume setInterval based on isActive
  │
  ├── SessionItemsList
  │     inject('sessionActive')
  │     → guard scroll watchers: if !isActive, skip scrollToBottomUntilStable()
  │     → on reactivation: if items were added while inactive, trigger scroll
  │
  └── DevToolsFilesPanel
        inject('sessionActive')
        → guard fetch watcher: if !isActive, skip API calls
```

### What Keeps Running While Inactive

- **Pinia store updates** (WebSocket pushes, new items): always active, not guarded
- **visualItems recalculation**: always active (computed from Pinia data)
- **VirtualScroller ResizeObserver**: remains attached, fires with 0-height (ignored by existing protections)

### What Gets Paused While Inactive

- **Auto-scroll to bottom** (`scrollToBottomUntilStable`)
- **Scroll stability detection** (timers, callbacks)
- **SessionHeader duration timer** (`setInterval` 1s)
- **Window resize listener** (viewport height detection)
- **DevToolsFilesPanel API fetches** (tree data)

### Reactivation Behavior

When a session is reactivated:
1. The ResizeObserver on the VirtualScroller container fires with the real viewport height
2. `wasHidden && isNowVisible` triggers `invalidateZeroHeights()` (purges any 0-height cache entries)
3. `updateViewportHeight(realHeight)` recalculates the render range
4. The VirtualScroller renders the correct items using the preserved height cache
5. `scrollTop` is naturally restored (retained by the browser on the DOM node)
6. If items were added while inactive and the user was near the bottom, the scroll guard in `onActivated` triggers a scroll-to-bottom

## Memory Impact

### Estimated per cached session

| Component | Estimated memory |
|---|---|
| VirtualScroller DOM (~100-200 visible nodes) | ~2-5 MB |
| Rendered Markdown/code blocks | ~1-3 MB |
| DevTools panel DOM | ~0.5-1 MB |
| Web Components shadow DOM (wa-*) | ~0.5-1 MB |
| MessageInput + header | Negligible |

**Per session: ~4-10 MB of DOM**
**For 5 sessions: ~20-50 MB**

Acceptable for a desktop/browser application. Pinia store data is already in memory regardless of KeepAlive.

## Implementation Plan

### Phase 1 — Prerequisites (does NOT break current behavior)

**1.1 — Replace global DOM queries with template refs in SessionView**
- `document.querySelector('.session-content-split')` (×3) → `ref splitPanelRef`
- `document.getElementById('devtools-panel-toggle-state')` (×1) → `ref devtoolsCheckboxRef`

**1.2 — Make HTML IDs unique per session**
- SessionView: 2 IDs → include sessionId
- SessionHeader: 18 IDs → include sessionId
- MessageInput: 1 ID → include sessionId
- SessionItem: 3 IDs → include sessionId alongside lineNum
- Update all corresponding `wa-tooltip for=` / `wa-popover for=` / `<label for=>` attributes

### Phase 2 — KeepAlive Setup (THE behavioral switch)

**2.1 — ProjectView: add KeepAlive wrapper**
```vue
<div v-show="sessionId" class="main-content">
    <router-view v-slot="{ Component }">
        <KeepAlive :max="5">
            <component :is="Component" :key="route.params.sessionId" />
        </KeepAlive>
    </router-view>
</div>
<div v-if="!sessionId" class="empty-state">
    <p>Select a session from the list</p>
</div>
```

### Phase 3 — Lifecycle Adaptation (must follow Phase 2 immediately)

**3.1 — SessionView: create and provide isActive flag**
```js
const isActive = ref(true)
onActivated(() => { isActive.value = true })
onDeactivated(() => { isActive.value = false })
provide('sessionActive', readonly(isActive))
```

**3.2 — SessionView: migrate own hooks**
- `window.addEventListener('resize')` → `onActivated` / `onDeactivated`
- Split panel setup/cleanup → `onActivated` / `onDeactivated` (using template ref from Phase 1.1)

**3.3 — SessionHeader: pause/resume timer**
- `inject('sessionActive')` → start `setInterval` on active, clear on inactive

**3.4 — SessionItemsList: guard scroll watchers + migrate timer cleanup**
- `inject('sessionActive')` → guard `scrollToBottomUntilStable()` calls in watchers
- Move timer cleanup from `onUnmounted` to `onDeactivated`
- On `onActivated`: if items were added during inactivity and user was near bottom, trigger scroll-to-bottom

**3.5 — DevToolsFilesPanel: guard fetch watcher**
- `inject('sessionActive')` → skip API calls when inactive

### Phase 4 — Cleanup (after KeepAlive is verified working)

**4.1 — Remove redundant `:key="sessionId"` from child components**
- VirtualScroller `:key="sessionId"` in SessionItemsList
- MessageInput `:key="sessionId"` in SessionItemsList

### Phase 5 — Optimization (can be done later)

**5.1 — ToolUseContent: pause polling when inactive**
- `inject('sessionActive')` → pause `setInterval` polling for tool results and agent links when inactive, resume on activation

### Phase 6 — Testing

- Switch rapidly between 5+ sessions → verify scroll position and state preservation
- LRU eviction (access 6th session) → verify proper cleanup of evicted session
- WebSocket push on inactive session → verify Pinia updates without DOM corruption
- Resize window/sidebar while session is inactive → verify recalculation on return
- Navigate to "no session selected" → verify KeepAlive cache survives
- DevTools file explorer on inactive session → verify no unnecessary API calls
- Active tool_use polling during session switch → verify behavior
- Subagent tabs within a cached session → verify tab state preservation

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Web Components (wa-*) misbehave after detach/reattach | Medium | High | Test wa-split-panel and wa-tab-group early in Phase 2 |
| VirtualScroller scroll position corruption | Low | High | Existing 0-height protections + structurally identical to wa-tab-panel switching (already works) |
| Memory pressure with 5 large sessions | Low | Medium | `:max="5"` + proper eviction cleanup. 20-50 MB is acceptable |
| CSS leaks between cached instances | Low | Low | KeepAlive detaches inactive DOM (no rendering), CSS `:has()` is scoped to `.session-view` |
| Stale watchers causing unexpected behavior | Medium | Medium | `isActive` guard via provide/inject |

## Task tracking
- [x] Task 1 (Phase 1.1): Replace global DOM queries with template refs in SessionView
- [x] Task 2 (Phase 1.2): Make HTML IDs unique per session (SessionView, SessionHeader, MessageInput, SessionItem)
- [x] Task 3 (Phase 2.1): ProjectView: add KeepAlive wrapper with v-show and router-view slot
- [x] Task 4 (Phase 3.1): SessionView: create and provide isActive flag
- [x] Task 5 (Phase 3.2): SessionView: migrate lifecycle hooks to onActivated/onDeactivated
- [x] Task 6 (Phase 3.3): SessionHeader: pause/resume timer based on isActive
- [x] Task 7 (Phase 3.4): SessionItemsList: guard scroll watchers + migrate timer cleanup
- [x] Task 8 (Phase 3.5): DevToolsFilesPanel: guard fetch watcher with isActive
- [x] Task 9 (Phase 4.1): Remove redundant :key="sessionId" from child components
- [x] Task 10 (Phase 5.1): ToolUseContent: pause polling when inactive

## Decisions made during implementation

### Task 1 (Phase 1.1)
- The `handleDevToolsSplitPanelPointerDown` function uses `event.currentTarget` to access the split panel element. This is correct and does not need to change to use the ref, because `event.currentTarget` already points to the element the listener is attached to (which is the same split panel). The ref is used for the initial setup/teardown of this listener.
- The `handleDevToolsTabShow` function uses `event.target.closest('wa-tab-group')` and scoped `querySelectorAll`. As noted in the spec, these are event-scoped queries that are safe with multiple instances, so they were not converted to template refs.

### Task 2 (Phase 1.2)
- SessionHeader had CSS rules using ID selectors (`#session-header-cost-breakdown`, `#session-header-lines`). Since IDs are now dynamic (containing the sessionId), these CSS rules were converted to use class selectors instead (`.cost-breakdown-item`, `.nb_lines`). The `cost-breakdown-item` class was added to the cost-breakdown element; the lines element already had the `nb_lines` class.
- The ID naming pattern for SessionHeader uses `session-header-${sessionId}-*` (sessionId inserted between `session-header` and the element name) to keep a consistent prefix that reads naturally.
- SessionView, SessionHeader, and MessageInput all access `sessionId` from their existing props/computeds (no new props or injects were needed). SessionItem also already had `sessionId` as a prop.

### Task 4 (Phase 3.1)
- The `isActive` flag, lifecycle hooks, and `provide` call are placed between the template refs and the auto-hide header/footer section, as a dedicated section with its own comment banner. This groups the KeepAlive lifecycle concern in one visible block, right after the refs it will eventually interact with (e.g., `splitPanelRef` in Task 5).
- The ref is initialized to `true` because the component starts in the active (mounted) state. `onActivated` is called after the initial mount as well, so the value stays consistent.

### Task 5 (Phase 3.2)
- Moved all code from `onMounted` and `onUnmounted` into `onActivated` and `onDeactivated` respectively, rather than keeping a separate `onMounted`. The operations are idempotent: `window.addEventListener`/`removeEventListener` with the same function reference and capture flag are deduplicated by the browser, and restoring the split panel height on every activation is the desired behavior (the user returns to this session and the panel should reflect the stored height).
- Removed `onMounted` and `onUnmounted` imports entirely since they are no longer used in this component.
- The `onActivated`/`onDeactivated` hooks are registered early in the script (before the functions they reference are defined), but this is safe because the callbacks only execute after mount, when all declarations have been evaluated.

### Task 6 (Phase 3.3)
- Used `watch(sessionActive, ..., { immediate: true })` instead of `onActivated`/`onDeactivated` lifecycle hooks. The `watch` approach is reactive and integrates with the provide/inject pattern established in Task 4. With `immediate: true`, the watcher fires on component setup (starting the timer for the initial active state), and subsequently fires whenever `sessionActive` changes. This handles both the initial mount and KeepAlive activate/deactivate transitions.
- The `inject('sessionActive', ref(true))` uses a default value of `ref(true)` so the timer works correctly if `SessionHeader` is ever rendered outside a KeepAlive context (e.g., in a subagent tab where no provider exists yet). The default `true` preserves the same behavior as before this change.
- On activation, `now.value` is immediately updated before starting the interval, so the displayed duration is correct from the first render frame (no 1-second lag).
- Removed `onMounted` and `onUnmounted` imports since they are no longer used in this component.

### Task 7 (Phase 3.4)
- Used `onDeactivated` instead of `onUnmounted` for timer cleanup (`temporaryIndicatorTimer` and `stabilityTimeoutId`). The `onDeactivated` hook also captures the item count and scroll position at deactivation time, which `onActivated` then uses to decide whether to scroll to bottom on reactivation.
- The `inject('sessionActive', ref(true))` uses a default value of `ref(true)` so SessionItemsList works correctly outside a KeepAlive context (e.g., in subagent tabs where no provider exists). The default `true` preserves the same behavior as before this change.
- Guards are placed so that Pinia store operations (data loading, subagent session loading) continue running in the background even when inactive. Only DOM-manipulating operations (`scrollToBottomUntilStable`) are skipped.
- The `processState` watcher still clears existing timers when inactive (to avoid stale timers), but skips creating new timers since the DOM is detached and the indicator would not be visible.
- The reactivation check in `onActivated` uses the `AUTO_SCROLL_THRESHOLD` (150px) constant already defined in the component for consistent "near bottom" detection across all scroll behaviors.

### Task 8 (Phase 3.5)
- Used a `fetchSkippedWhileInactive` boolean flag to track whether the fetch watcher was triggered while inactive. When `sessionActive` becomes `true` again, if the flag is set, the current values of `projectId`, `sessionId`, `directory`, etc. are re-fetched. This avoids stale tree data when the watched values changed while the session was deactivated.
- The reactivation watcher also re-runs the active search query (if any) or clears the search state, mirroring the exact same logic as the main fetch watcher. This ensures search results are consistent with the re-fetched tree data.
- The `inject('sessionActive', ref(true))` uses a default value of `ref(true)` so DevToolsFilesPanel works correctly outside a KeepAlive context (e.g., if rendered without a SessionView provider). The default `true` preserves the same behavior as before this change.

### Task 9 (Phase 4.1)
- Straightforward removal of `:key="sessionId"` from both `VirtualScroller` and `MessageInput` in `SessionItemsList.vue`. With KeepAlive in place, each `SessionView` instance has a fixed `sessionId`, so these keys evaluated to a constant value and served no purpose.

### Task 3 (Phase 2.1)
- The spec shows the `v-show` and `v-if` on sibling `<div>` elements. In the actual code, the `<main slot="end">` element is needed for the `wa-split-panel` slot assignment. Rather than duplicating `<main slot="end">` (which could cause issues with web component slot distribution), the implementation keeps a single `<main slot="end" class="main-content">` container with two children: a `<div v-show="sessionId" class="session-content">` wrapping the KeepAlive router-view, and a `<div v-if="!sessionId" class="empty-state">`. A `.session-content { height: 100% }` CSS rule ensures the wrapper fills the main-content area.

### Task 10 (Phase 5.1)
- Used a `watch(sessionActive)` approach (without `{ immediate: true }`) rather than `onActivated`/`onDeactivated` lifecycle hooks, consistent with the pattern in SessionHeader (Task 6). No `immediate: true` is needed because polling is user-initiated (via opening `<wa-details>` or clicking "View Agent"), not started on mount.
- On deactivation, the intervals are cleared and in-flight requests are aborted, but the reactive state (`isPolling`, `agentLinkState`) is preserved so the UI still reflects the correct state if the user switches back. Plain variables (`resultPollingPaused`, `agentPollingPausedAttempts`) track which polling was active when suspended.
- On reactivation, result polling resumes only if the result data is still empty. Agent polling resumes only if the state is still `'retrying'` and the saved attempt count is below `AGENT_POLLING_MAX_ATTEMPTS`. The attempt count is preserved across the pause so the max-attempts limit is not reset by session switching.
- The `inject('sessionActive', ref(true))` uses a default value of `ref(true)` so ToolUseContent works correctly outside a KeepAlive context (e.g., in subagent tabs where no provider exists). The default `true` preserves the same behavior as before this change.

## Problems Encountered

### VirtualScroller Scroll Position Loss

**Symptom**: When switching between cached sessions, the VirtualScroller scroll position is lost — it always shows the top of the session instead of where the user was scrolling.

**Initial spec assumption (WRONG)**: The spec stated "VirtualScroller — NO CHANGES NEEDED" because:
1. `batchUpdateItemHeights` rejects 0-height values
2. Container ResizeObserver detects hidden→visible transitions
3. `scrollTop` is retained by the browser on the DOM node even when detached

**Why this was wrong**: Point 3 is technically true — the DOM node's `scrollTop` property IS retained while it exists in memory. However, what the spec didn't account for is that the **VirtualScroller component is destroyed and recreated** during KeepAlive deactivation/activation cycles, because of the `v-else-if` conditional rendering in SessionItemsList.vue's template.

#### Attempt 1: ResizeObserver-based suspend detection

**Approach**: Detect when the container height becomes 0 (indicating KeepAlive detachment) in the `updateViewportHeight` function of `useVirtualScroll.js`. Save scrollTop when height drops to 0, restore when it becomes positive.

**Result**: FAILED. The condition `height === 0 && viewportHeight.value > 0` never fired because `viewportHeight.value` was already 0 when the deactivation resize event arrived. The ResizeObserver fires callbacks asynchronously and there's no guarantee about the ordering relative to `viewportHeight` updates.

#### Attempt 2: Explicit suspend/resume with anchor-based approach

**Approach**: Instead of detecting 0-height in ResizeObserver, add explicit `suspend()` and `resume()` methods to the composable, called from `onDeactivated`/`onActivated` in VirtualScroller.vue. Use an anchor-based approach: save `{ index, key, offset }` of the first visible item, then restore by finding the anchor item in the positions array and computing the target scrollTop.

**Result**: FAILED. Testing revealed that `resume()` was called with `suspended=false`. Investigation with debug logs (`onMounted`, `onUnmounted`, `onDeactivated`, `onActivated`) revealed the **true root cause**:

1. `onDeactivated` fires → `suspend()` saves anchor ✅
2. `onUnmounted` fires → VirtualScroller IS BEING DESTROYED
3. `onMounted` fires → NEW VirtualScroller created (new composable instance with `suspended=false`)
4. `onActivated` fires → `resume()` called on new instance with `suspended=false` ❌

The VirtualScroller is destroyed because of the `v-else-if="visualItems.length > 0"` condition in SessionItemsList.vue template (line 898). Even though SessionView is cached by KeepAlive, components rendered via `v-if`/`v-else-if` chains inside the cached tree are destroyed when Vue evaluates the condition chain during deactivation. The VirtualScroller is not a direct child of the KeepAlive boundary — it's conditionally rendered within a chain of `v-if`/`v-else-if` states (compute pending, error, loading, items list, draft empty, empty).

**Key insight**: `onActivated`/`onDeactivated` propagate to ALL descendants within a KeepAlive boundary, but this doesn't prevent `onMounted`/`onUnmounted` from ALSO firing if the component is conditionally rendered.

#### Attempt 3: Save/restore at SessionItemsList level

**Approach**: Since SessionItemsList IS preserved by KeepAlive (it's a direct, unconditional child of the cached SessionView), move the scroll anchor save/restore to that level:
- In `onDeactivated`: `savedScrollAnchor = scroller.getScrollAnchor()`
- In `onActivated`: after `nextTick()`, call `scroller.scrollToAnchor(savedScrollAnchor)`

**Result**: PARTIALLY FAILED. The anchor is saved correctly (at SessionItemsList level, the VirtualScroller exists and has valid state during `onDeactivated`). However, `scrollToAnchor()` on the freshly recreated VirtualScroller produces incorrect positions because:
1. The **height cache is lost** when the old composable instance is destroyed
2. All items in the new composable use estimated `minItemHeight` (50px) instead of measured heights
3. The anchor key IS found in the positions array (items are the same), but `anchorItem.top` is computed from estimated heights → wrong pixel position

**Additional complication**: The session data watcher (`watch([sessionId, session])`) may re-trigger `scrollToBottomUntilStable({ isInitial: isFirstLoad })` on the recreated VirtualScroller, potentially overriding any anchor restore. However, since `isFirstLoad` is false for an already-loaded session, this path calls `scrollToBottomUntilStable({ isInitial: false })` which doesn't set `isInitialScrolling` but still scrolls to bottom.

#### Attempt 4: Prevent VirtualScroller destruction with v-show + Fix sessionId reactivity

**Approach (two parts)**:

**Part A — v-show for VirtualScroller**: Change the SessionItemsList template from `v-if`/`v-else-if` chain to use `v-show` for the VirtualScroller. This keeps the VirtualScroller mounted in the DOM at all times, preserving:
- The composable instance (height cache, suspended state)
- The `suspend()`/`resume()` methods (same instance across activate/deactivate)
- The ResizeObserver registrations
- All measured item heights

The other states (compute pending, error, loading, empty) remain as `v-if`/`v-else-if` since they don't need state preservation. A `showVirtualScroller` computed was added to control the `v-show` condition.

**Edge case fix**: When VirtualScroller is always mounted but has no items (`renderRange = {start:0, end:0}`), the `spacerAfterHeight` computed would access `posArray[-1]` (undefined), causing a crash. Fixed by adding `end <= 0` guard. Similarly added `start >= posArray.length` guard to `spacerBeforeHeight`.

**Part B — sessionId reactivity bug**: Testing revealed a second root cause: in `SessionView.vue`, `sessionId` was defined as `computed(() => route.params.sessionId)`. Since `route` is a global reactive object, ALL cached SessionView instances (in the KeepAlive cache) would see the NEW session's route params when the route changes. This caused:
1. `onDeactivated` hooks to see the wrong `props.sessionId` (the new session, not the old one being deactivated)
2. `visualItems` computed (which depends on `props.sessionId`) to return the wrong session's items (typically 0 items because the new session is still loading)
3. The VirtualScroller's `suspend()` to save `anchor=null` because `items=0`

**Fix**: Changed `sessionId` and `projectId` in `SessionView.vue` from `computed(() => route.params.sessionId)` to `ref(route.params.sessionId)` — capturing the value at component creation time. Since KeepAlive uses `:key="route.params.sessionId"`, each SessionView instance gets the correct value at creation and keeps it permanently.

**Result**: SUCCESS ✅. Scroll position is preserved exactly when switching between cached sessions. Test verified:
- Session 7a4a085e (356 visual items) scrolled to position ~11597px
- Switched to session 27e5d716 (5 items)
- Switched back: scrollTop restored to exactly 11597.27px, same visual content displayed
- Anchor-based restore in `useVirtualScroll.js` `resume()` correctly finds the anchor item by key and computes the target scrollTop

**Files modified**:
- `SessionItemsList.vue`: Template restructured — VirtualScroller uses `v-show="showVirtualScroller"` outside the `v-if`/`v-else-if` chain. Added `showVirtualScroller` computed.
- `useVirtualScroll.js`: Fixed `spacerAfterHeight` and `spacerBeforeHeight` computed properties for empty items edge case.
- `SessionView.vue`: Changed `sessionId` and `projectId` from `computed()` to `ref()`.

#### Follow-up fix: `suspended` must be a `ref`, not a plain `let`

**Symptom**: After switching back to a cached session where new items were added while inactive, the space for the new items is visible (scrollable) but the items are NOT rendered — blank space. Worse, even normal scrolling is broken after reactivation.

**Root cause**: `suspended` was a plain `let` variable, not a Vue `ref()`. The `renderRange` `watchEffect` reads `suspended` to decide whether to early-return. When the effect runs while `suspended = true`, it returns early after reading only `positions.value` — so Vue only tracks `positions` as a dependency. `scrollTop` and `viewportHeight` are never read and therefore NOT tracked. When `resume()` sets `suspended = false`, Vue doesn't know (it's not reactive), and even updating `scrollTop.value` doesn't trigger the effect (it's not tracked). Result: `renderRange` stays frozen permanently.

**Fix**: Changed `suspended` from `let` to `ref(false)`. Now `suspended.value` is tracked by the `watchEffect`. When `resume()` sets `suspended.value = false`, the `watchEffect` re-runs, re-reads `scrollTop.value` and `viewportHeight.value` (re-establishing all dependencies), and correctly recalculates `renderRange` including any new items.

#### Note on log duplication

During testing, console logs from `onDeactivated`/`onActivated` appear duplicated 2-3 times. This appears to be a quirk of the web component tab panel lifecycle (`wa-tab-group`/`wa-tab-panel`), not a functional issue. The core deactivation/activation/resume flow works correctly despite the log noise.

#### Debugging notes

- **HMR interference**: Vite's Hot Module Replacement recreates component instances, creating new composable instances with fresh state. This invalidated several test runs where `suspend()` was called on the old instance but `resume()` on a new one. **Solution**: Force reload the page (`Ctrl+Shift+R`) between tests instead of relying on HMR.
- **Console log versioning**: Since Chrome DevTools console can't be cleared programmatically via MCP tools, we use version markers in console.log messages (e.g., `[VirtualScroll v3]`) to distinguish between old and new code versions without clearing the console.

### Important behavioral notes

**Scroll position on session switch — two distinct cases:**
- **Session already in KeepAlive cache**: The scroll position MUST be preserved (restored to where the user was). This is the main goal of the VirtualScroller fix work.
- **Session NOT in cache (first load)**: The session should scroll to the bottom (end of conversation). This is the existing behavior via `scrollToBottomUntilStable({ isInitial: true })`.

These two cases must not be confused. The KeepAlive restore must NOT override to "scroll to bottom" for cached sessions — it must restore the previous scroll position.

**Chrome automation header causing viewport height changes:**
When using MCP/browser automation tools, Chrome periodically shows and hides a header bar that says an external agent is controlling the browser. This causes the viewport height to change slightly (the header takes/releases space). This is NOT a bug — it's normal Chrome behavior during automation. The ResizeObserver on the VirtualScroller container will fire with slightly different heights, but this is handled correctly by the existing code. Do not be alarmed by minor viewport height fluctuations during testing.

## Resolved questions and doubts
<!-- Questions and resolutions will be documented here -->
