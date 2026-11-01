# Drag-to-Switch (Spring-Loaded Tabs & Sessions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When dragging files or text over the app, hovering 1 second on a session tab switches to that tab, and hovering 1 second on a session in the sidebar switches to that session — enabling drag-and-drop into any session's chat without manual navigation.

**Architecture:** A shared composable (`useDragHover`) encapsulates the "hover timer with visual feedback" pattern. It's consumed by `SessionView` (for tab switching) and `SessionListItem` (for session switching). Only drags carrying `Files` or `text/plain` types are eligible (same filter as the existing drop overlay in `SessionItemsList`).

**Tech Stack:** Vue 3 Composition API, Web Awesome components, CSS animations

**No tests** per project convention (CLAUDE.md: "no tests and no linting").

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/src/composables/useDragHover.js` | **Create** | Reusable composable: `dragenter`/`dragleave` handlers with a configurable delay timer, visual feedback state, type filtering, and cleanup |
| `frontend/src/views/SessionView.vue` | **Modify** | Add drag-hover behavior on each `<wa-tab>` (desktop) and each compact-mode `<wa-button>` (mobile) to trigger `switchToTab()` after the delay |
| `frontend/src/components/SessionListItem.vue` | **Modify** | Add drag-hover behavior on the root wrapper to emit `select` after the delay (the `shouldActivate` guard skips the already-active session, so the toggle in `handleSessionSelect` is never reached) |

---

## Task 1: Create `useDragHover` composable

**Files:**
- Create: `frontend/src/composables/useDragHover.js`

### Design

The composable returns event handlers and reactive state. The consumer binds them to any DOM element.

```js
/**
 * useDragHover — "spring-loaded folder" pattern for drag-and-drop.
 *
 * When a drag carrying Files or text/plain hovers over the element for `delay` ms,
 * fires the `onActivate` callback. Provides reactive `isPending` state for visual feedback.
 *
 * @param {Object} options
 * @param {Function} options.onActivate — called when the hover delay expires
 * @param {number}  [options.delay=1000] — hover duration in ms before activation
 * @param {Function} [options.shouldActivate] — optional guard, return false to skip (e.g. "already on this tab")
 * @returns {{ onDragenter, onDragleave, onDragover, onDrop, isPending, cancel }}
 */
```

Key behaviors:
- **Type filtering:** Only react to drags with `Files` or `text/plain` in `dataTransfer.types` (same as `SessionItemsList`). Ignore empty types (apps that don't expose MIME info).
- **Timer management:** `dragenter` starts a `setTimeout(delay)`. `dragleave` with counter reaching 0 cancels it. Moving to another drag-hover target cancels the previous one.
- **`shouldActivate` guard:** Lets the consumer skip activation for the already-active tab/session.
- **`isPending` ref:** `true` while the timer is running (for CSS animation). Reset to `false` on cancel, activation, or drag end.
- **`cancel()`:** Imperative cancel for cleanup (`onDeactivated`, `onUnmounted`).
- **`onDragover`:** Calls `event.preventDefault()` — required so the browser accepts the drop zone without interfering with the actual drop on `SessionItemsList`.
- **`onDrop`:** Cancels the timer and resets state — the drop will be handled by `SessionItemsList`, not here.
- **`dragend` cleanup:** Listens on `document` for `dragend` while timer is active, to handle Escape cancellation.
- **Nested element counter:** Same `dragCounter` pattern as `SessionItemsList` to handle `dragenter`/`dragleave` on child elements.

### Steps

- [ ] **Step 1: Create the composable file**

```js
// frontend/src/composables/useDragHover.js
import { ref } from 'vue'

/**
 * Check if a drag event carries files or text content.
 * Returns false for empty types (some external apps don't expose MIME info)
 * and for browser-internal drags like bookmarks/links.
 */
function isDragEligible(event) {
    const types = event.dataTransfer?.types
    if (!types || types.length === 0) return false
    return types.includes('Files') || types.includes('text/plain')
}

/**
 * Spring-loaded folder composable: after hovering with a drag for `delay` ms,
 * calls `onActivate`. Provides `isPending` state for visual feedback (e.g. a
 * progress animation on the hovered element).
 *
 * @param {Object} options
 * @param {Function} options.onActivate - Callback fired when the hover delay expires
 * @param {number} [options.delay=1000] - Hover duration in ms before activation
 * @param {Function} [options.shouldActivate] - Guard: return false to skip (e.g. already on this tab)
 * @returns {{ onDragenter: Function, onDragleave: Function, onDragover: Function, onDrop: Function, isPending: import('vue').Ref<boolean>, cancel: Function }}
 */
export function useDragHover({ onActivate, delay = 1000, shouldActivate = null }) {
    const isPending = ref(false)
    let timer = null
    let dragCounter = 0

    function startTimer() {
        cancelTimer()
        isPending.value = true
        timer = setTimeout(() => {
            isPending.value = false
            timer = null
            document.removeEventListener('dragend', cancel, true)
            onActivate()
        }, delay)
        // Listen for drag cancellation (Escape, drop outside window)
        document.addEventListener('dragend', cancel, true)
    }

    function cancelTimer() {
        if (timer) {
            clearTimeout(timer)
            timer = null
        }
        isPending.value = false
        document.removeEventListener('dragend', cancel, true)
    }

    function onDragenter(event) {
        if (!isDragEligible(event)) return
        if (shouldActivate && !shouldActivate()) return
        dragCounter++
        if (dragCounter === 1) {
            startTimer()
        }
    }

    function onDragleave(event) {
        if (!isDragEligible(event)) return
        dragCounter--
        if (dragCounter <= 0) {
            dragCounter = 0
            cancelTimer()
        }
    }

    function onDragover(event) {
        // preventDefault required so browser treats the area as a valid drop zone.
        // Without this, dragenter/dragleave fire unreliably in some browsers.
        if (!isDragEligible(event)) return
        event.preventDefault()
    }

    function onDrop() {
        // Drop is handled by SessionItemsList, not here.
        // Just clean up our timer state.
        dragCounter = 0
        cancelTimer()
    }

    function cancel() {
        dragCounter = 0
        cancelTimer()
    }

    return { onDragenter, onDragleave, onDragover, onDrop, isPending, cancel }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/composables/useDragHover.js
git commit -m "feat: add useDragHover composable for spring-loaded drag-and-drop"
```

---

## Task 2: Add drag-hover on session tabs (`SessionView.vue`)

**Files:**
- Modify: `frontend/src/views/SessionView.vue`

### Design

Each `<wa-tab>` (desktop) and compact-mode `<wa-button>` (mobile) gets drag event handlers. We need one `useDragHover` instance per tab panel. Since tabs are a mix of static (main, files, git, terminal) and dynamic (subagent tabs via `v-for`), we use a helper function that creates handlers for a given panel name and caches them in a plain `Map` keyed by panel name — avoids recreating on each render.

### Steps

- [ ] **Step 1: Import composable and set up handler cache**

In `SessionView.vue` `<script setup>`, add:

```js
import { useDragHover } from '../composables/useDragHover'

// Cache of drag-hover handlers per tab panel name, to avoid recreating on each render.
const tabDragHandlers = new Map()

/**
 * Get (or create) drag-hover handlers for a tab panel.
 * Each tab gets its own useDragHover instance that triggers switchToTab on activation.
 */
function getTabDragHandlers(panel) {
    if (tabDragHandlers.has(panel)) return tabDragHandlers.get(panel)
    const handlers = useDragHover({
        onActivate: () => switchToTab(panel),
        shouldActivate: () => panel !== activeTabId.value,
    })
    tabDragHandlers.set(panel, handlers)
    return handlers
}
```

Add cleanup in the existing `onDeactivated` (KeepAlive) and `onBeforeUnmount`:

```js
// In existing onDeactivated callback, add:
tabDragHandlers.forEach(h => h.cancel())

// In existing onBeforeUnmount callback, add:
tabDragHandlers.forEach(h => h.cancel())
tabDragHandlers.clear()
```

- [ ] **Step 2: Add drag handlers to desktop `<wa-tab>` elements**

For each `<wa-tab>` in the template (lines 748, 768, 785, 794, 803), add the four drag event handlers. Example for the Chat tab:

```html
<wa-tab slot="nav" panel="main"
    @dragenter="getTabDragHandlers('main').onDragenter"
    @dragleave="getTabDragHandlers('main').onDragleave"
    @dragover="getTabDragHandlers('main').onDragover"
    @drop="getTabDragHandlers('main').onDrop"
    :class="{ 'drag-hover-pending': getTabDragHandlers('main').isPending.value }"
>
```

Same pattern for subagent tabs (using `tab.id`), Files (`'files'`), Git (`'git'`), Terminal (`'terminal'`).

- [ ] **Step 3: Add drag handlers to compact-mode `<wa-button>` elements**

Same pattern on the compact-mode buttons (lines 671, 686, 702, 709, 717). These use `switchToTabAndCollapse` for clicks — but for drag-hover we want `switchToTab` (no collapse needed since the user is mid-drag, not clicking). The `getTabDragHandlers` already uses `switchToTab`, so the same handlers work.

- [ ] **Step 4: Add CSS for the pending visual feedback**

Add a CSS class `.drag-hover-pending` on `wa-tab` / compact `wa-button` that shows a visual cue (animated bottom border that fills up during the 1s delay):

```css
/* Spring-loaded drag hover: visual feedback during the 1s delay */
wa-tab.drag-hover-pending,
.compact-tab-scroll-area wa-button.drag-hover-pending {
    position: relative;
}

wa-tab.drag-hover-pending::after,
.compact-tab-scroll-area wa-button.drag-hover-pending::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    height: 2px;
    background: var(--wa-color-brand-600);
    animation: drag-hover-fill 1s linear forwards;
}

@keyframes drag-hover-fill {
    from { width: 0; }
    to { width: 100%; }
}
```

The animation duration matches the `delay` parameter (1s). This gives a clear visual cue: "hold here and this bar fills up, then it switches".

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "feat: drag-hover on session tabs switches tab after 1s delay"
```

---

## Task 3: Add drag-hover on session list items (`SessionListItem.vue`)

**Files:**
- Modify: `frontend/src/components/SessionListItem.vue`

### Design

Each `SessionListItem` gets drag-hover handlers. When the delay expires, it emits `select` — the same event as a click. The `shouldActivate` guard (`!props.active`) prevents activation on the already-active session, so the toggle/deselect branch in `handleSessionSelect` is never reached.

**VirtualScroller consideration:** Items are recycled by the virtual scroller. The `session` prop changes when an item is reused for a different session. We need to cancel any pending timer when the `session` prop changes (via a `watch`).

### Steps

- [ ] **Step 1: Add drag-hover to `SessionListItem.vue`**

```js
import { useDragHover } from '../composables/useDragHover'

const { onDragenter, onDragleave, onDragover, onDrop, isPending: isDragPending, cancel: cancelDragHover } = useDragHover({
    onActivate: () => emit('select', props.session),
    shouldActivate: () => !props.active,
})

// Cancel pending drag timer when the virtual scroller recycles this component for a different session
watch(() => props.session.id, () => {
    cancelDragHover()
})
```

Add handlers on the root `<div class="session-item-wrapper">`:
```html
<div
    class="session-item-wrapper"
    :class="{
        'session-item-wrapper--active': active,
        'session-item-wrapper--highlighted': highlighted,
        'session-item-wrapper--compact': compactView,
        'session-item-wrapper--drag-pending': isDragPending
    }"
    @dragenter="onDragenter"
    @dragleave="onDragleave"
    @dragover="onDragover"
    @drop="onDrop"
>
```

- [ ] **Step 2: Add CSS feedback on `SessionListItem`**

```css
.session-item-wrapper--drag-pending {
    position: relative;
}

.session-item-wrapper--drag-pending::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    height: 2px;
    background: var(--wa-color-brand-600);
    animation: drag-hover-fill 1s linear forwards;
}

@keyframes drag-hover-fill {
    from { width: 0; }
    to { width: 100%; }
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/SessionListItem.vue
git commit -m "feat: drag-hover on session list items switches session after 1s delay"
```

---

## Task 4: Edge cases and polish

**Files:**
- Possibly modify: `frontend/src/composables/useDragHover.js`, `frontend/src/views/SessionView.vue`

### Items to verify and fix

- [ ] **Step 1: Verify drop still works after tab switch**

After drag-hover switches to the Chat tab, the `SessionItemsList` drop overlay should appear (since the cursor is now over the chat area while still dragging). Verify that `dragenter` fires on `SessionItemsList` after the tab switch. If not, the composable's `onDragover` calling `preventDefault()` on the tab might interfere — in that case, stop calling `preventDefault()` after activation.

- [ ] **Step 2: Verify dragend cleanup**

When a drag is cancelled (Escape, drop outside window), verify that all `isPending` states are reset. The `dragend` listener added by `useDragHover` in `startTimer()` should handle this. Verify it works for:
- Escape during drag over a tab
- Escape during drag over a session item
- Dropping outside the browser window

- [ ] **Step 3: Verify virtual scroller recycling**

In the session list, scroll while dragging to ensure that when a `SessionListItem` is recycled (new session prop), the pending state is properly cancelled and doesn't "stick" to the wrong session. The `watch(() => props.session.id)` should handle this.

- [ ] **Step 4: Commit any fixes**

```bash
git add frontend/src/composables/useDragHover.js frontend/src/views/SessionView.vue frontend/src/components/SessionListItem.vue
git commit -m "fix: drag-hover edge cases — dragend cleanup and post-switch drop"
```
