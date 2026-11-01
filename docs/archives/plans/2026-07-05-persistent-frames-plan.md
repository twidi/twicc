# Persistent Frames — Implementation Plan (Browser pane + artifact HTML preview)

**Date:** 2026-07-05
**Branch:** `browser-tab` (follows the Browser pane and companion plans)
**Status:** plan written; implementation NOT started — waiting for explicit go.

## 1. Problem

An `<iframe>` reloads whenever its DOM node is detached or re-parented. Two codepaths do
that today:

1. **Session switch** — `ProjectView.vue` caches session views in
   `<KeepAlive :key="route.params.sessionId">`; deactivation detaches the whole subtree,
   iframes included. Confirmed for both the Browser pane and the artifact HTML preview.
2. **Dock moves** — tool panes are `<Teleport>`ed between docking targets; retargeting
   re-parents the pane, reloading any iframe inside (documented limitation).

The embedded page loses everything (SPA route, scroll, form state, dev-server session).

**Scope (user decision):** HTML iframes only — the Browser pane's frame and FilePane's
HTML preview. PDF / audio / video / Mermaid pan-zoom lose state too but stay out of
scope (many other places lose scroll as well; not this feature's job).

## 2. Solution — a persistent frame layer (VS Code webview pattern)

Iframes stop living inside the per-session subtree. A **`FrameHost`** component, mounted
**once** in `ProjectView` (inside `.main-content`, sibling of the three KeepAlive
branches — session views, project detail, artifacts browser — so it covers all of them),
renders every registered iframe. Panes render a **placeholder** where the iframe used to
be, via a new **`PersistentFrame`** component; the host absolutely positions each iframe
over its placeholder's live rect. Placeholders detach and teleport freely; the iframe
node **never moves** → no reload, ever — neither on session switch nor on dock moves
(the Teleport now only moves the placeholder).

The codebase already has the architectural twin: `TerminalPool.vue` +
`stores/terminalPool.js` — an app-level host owning one live instance per resource,
attached to whichever panel displays it. Terminals tolerate DOM moves, so the pool uses
`<Teleport>`; iframes don't, so this design replaces the Teleport with **rect
synchronization**. Same philosophy, different payload constraint.

### Key design decisions

| Decision | Choice | Why |
|---|---|---|
| Host placement | Inside `<main class="main-content">` (ProjectView), after the three KeepAlive branches | Covers session tabs, project detail AND the sidebar Artifacts browser; lives and dies with all its possible clients; keeps frames inside `.main-content`'s stacking context so existing overlays keep working (see z-tiers) |
| Positioning | Host `position:absolute; inset:0` in `.main-content` (which gains `position:relative`); cells absolute, coords = placeholderRect − hostRect | `container-type: inline-size` on `.main-content` makes it the containing block for `fixed` descendants *except* while preview-expanded drops it — viewport-`fixed` cells would flip coordinate systems mid-flight. Absolute-in-host is stable in both states |
| Fullscreen exception | The cell switches to `position:fixed` + z 1001 while its pane's preview is fullscreen | The fullscreen wrapper is `position:fixed; z-index:1000` and `.main-content` has `overflow:hidden` — an absolute cell would be clipped; `fixed` escapes the clip exactly when `container-type` is dropped (`main-content--preview-expanded`), which the same state change guarantees |
| z-tiers | `base` = 2, `overlay` = 11, `fullscreen` = 1001 — per frame, driven by the owning pane | WA dialogs/popovers/tooltips/dropdowns are **native top-layer** (`showModal()` / `showPopover()`, verified in the bundles) → always above frames, no conflict. App overlays inside `.main-content` (docking overlay backdrop 8 / panel 11, fullscreen wrapper 1000) must cover base frames (2 < 8 ✓); a frame *inside* the docking overlay is raised to 11 (host renders after the panel in DOM order → wins the tie), below gutters (12); fullscreen 1001 covers the wrapper (1000) |
| Over-iframe chrome | Each cell carries a `frame-overlay-layer` div **above** the iframe; panes `<Teleport>` their floating chrome into it | FilePane's `.preview-actions` (z 2) and `ArtifactBrokerPrompt` float over the iframe today; once the iframe lives in the host layer, no z-index inside the pane (capped by DockRegion's `isolation:isolate` context) can beat it. Teleporting plain DOM is safe; `<Teleport :disabled>` renders in place for non-iframe previews — zero template duplication |
| Geometry tracking | `useElementBounding` (VueUse ≥ 14, already a dep) on placeholder + host; plus a store-level `geometryEpoch` bumped on layout mutations | ResizeObserver + window scroll (capture) + resize cover size changes and scrolling; **position-only** moves (dock retarget with identical size) are invisible to RO → the epoch forces a re-measure |
| Visibility | Per-frame `visible` = component activated (KeepAlive hooks) AND placeholder rect non-degenerate; hidden = `visibility:hidden` + `pointer-events:none`, cell keeps its last rect | `onDeactivated`/`onActivated` are the authoritative detach signal (RO does not reliably fire on DOM removal); `visibility` (not `display:none`) keeps the page rendering/running normally |
| Lifecycle | Frame registered at setup, destroyed in `onBeforeUnmount` — NOT on `onDeactivated` | Unmount hooks fire on real unmount (KeepAlive eviction, `v-if` teardown, session close) but not on deactivation → frame lifetime = component lifetime across KeepAlive, memory bounded by `maxCachedSessions` exactly like the rest of the cached views |
| DOM order in host | Registry object, **append-only key order; never sort/reorder** | A keyed `v-for` that reorders moves iframe nodes → reload. Insertions append; deleting a middle key doesn't move siblings. Hard invariant, documented in the store |
| Divider drags | Store-level `dividerDragDepth`; host sets `pointer-events:none` on iframes while > 0 | An iframe is a separate browsing context that swallows pointermove — the existing fix (`SessionLayout.vue:259` `.resizing :deep(iframe)`) stops matching once frames leave that subtree. Wired from the docking gutters AND (new) the three plain splits — project sidebar, FilesPanel tree/content, GitPanel tree/content — which are buggy today already |
| Fallback | `PersistentFrame` renders the iframe inline (plus a local overlay div) when no host is mounted | Any future FilePane usage outside ProjectView degrades to today's behavior instead of an invisible frame |
| `hostMounted` timing | Set by **ProjectView's setup** (and cleared in its `onUnmounted`), NOT by FrameHost's lifecycle | FrameHost mounts *after* the KeepAlive branches (its DOM position is what wins the z=11 tie), so on a cold-load deep link (session URL with the Browser tab active, artifacts URL on an HTML bookmark) the panes mount first — a FrameHost-owned flag would still be false and the inline fallback would be taken permanently, silently re-introducing the reload bug. ProjectView's setup runs before any child renders; registering before the host's DOM exists is harmless (the registry is store state; FrameHost renders whatever is registered when it mounts, same tick) |
| Frame identity | `frameId` prop, unique + stable per pane instance (`useId()`-based) | Two cached sessions = two Browser frames; FilePane instances are stable across KeepAlive |

### Accepted limits (document, don't fight)

- A hidden (cached) dev-server page keeps running — HMR sockets stay open, JS timers run.
  That's the point (state preservation) and the memory cost is bounded by
  `maxCachedSessions`, but it IS more than today, where detached iframes died.
- During a divider drag or layout animation, the iframe visually lags its placeholder by
  ≤ 1 frame (rect sync is reactive, not atomic). VS Code behaves the same.
- The `.session-layout.resizing :deep(iframe)` CSS rule stays (it still covers inline
  fallback frames) but the pooled path relies on the store flag.
- `window.open` / downloads / focus semantics are unchanged (same element, same browsing
  context).

## 3. Task 1 — `framePool` store + node tests

### 3.1 New file `frontend/src/stores/framePool.js`

```js
import { defineStore } from 'pinia'
import { markRaw } from 'vue'

/**
 * App-level pool of persistent iframes (Browser pane, artifact HTML preview).
 *
 * An <iframe> reloads whenever its DOM node is detached or re-parented — which
 * is exactly what KeepAlive (session switch) and Teleport (dock moves) do to
 * the panes. So the iframes live OUTSIDE the pane subtree: FrameHost (mounted
 * once in ProjectView's .main-content) renders every registered frame,
 * absolutely positioned over its pane's placeholder rect (PersistentFrame
 * tracks it). The placeholder moves freely; the iframe node never does.
 *
 * Same philosophy as stores/terminalPool.js, different payload constraint:
 * terminals tolerate <Teleport> relocation, iframes need rect sync instead.
 *
 * HARD INVARIANT — append-only key order. FrameHost renders `frames` with a
 * keyed v-for in Object.keys() order; reordering keys would move iframe DOM
 * nodes and reload them. Insertions append, deletions don't shift siblings;
 * never sort, never rebuild the object.
 */
export const useFramePoolStore = defineStore('framePool', {
    state: () => ({
        // id → descriptor {
        //   src, remountKey, attrs, zTier ('base'|'overlay'|'fullscreen'),
        //   visible, rect {x,y,width,height},
        //   onLoad (markRaw fn|null),
        //   el (markRaw iframe element|null, set by FrameHost),
        //   overlayEl (markRaw div|null, set by FrameHost),
        // }
        frames: {},
        // FrameHost presence — PersistentFrame falls back to an inline iframe
        // (today's behavior) when no host is mounted. Written by PROJECTVIEW's
        // setup/onUnmounted (not FrameHost): panes can mount before FrameHost's
        // DOM on cold-load deep links, and the flag must already be true.
        hostMounted: false,
        // Bumped by layout owners after mutations that can move a placeholder
        // without resizing it (dock retarget, overlay open/close, maximize):
        // ResizeObserver is blind to position-only changes.
        geometryEpoch: 0,
        // >0 while a split/dock divider drag is in progress → FrameHost turns
        // off pointer-events on iframes so the drag keeps tracking.
        dividerDragDepth: 0,
    }),

    getters: {
        isDividerDragging: (state) => state.dividerDragDepth > 0,
        frameEl: (state) => (id) => state.frames[id]?.el || null,
        frameOverlayEl: (state) => (id) => state.frames[id]?.overlayEl || null,
    },

    actions: {
        register(id, { src, remountKey, attrs, zTier, onLoad }) {
            this.frames[id] = {
                src,
                remountKey,
                attrs: attrs || {},
                zTier: zTier || 'base',
                visible: false,
                rect: { x: 0, y: 0, width: 0, height: 0 },
                onLoad: onLoad ? markRaw(onLoad) : null,
                el: null,
                overlayEl: null,
            }
        },

        patch(id, fields) {
            const frame = this.frames[id]
            if (frame) Object.assign(frame, fields)
        },

        setRect(id, rect) {
            const frame = this.frames[id]
            if (frame) frame.rect = rect
        },

        // Separate setters: Vue invokes a function ref synchronously when ITS
        // vnode mounts, before the next sibling is patched — at the iframe's
        // first ref call the overlay div doesn't exist yet, so a single
        // "el + nextElementSibling" setter would record overlayEl = null.
        setFrameEl(id, el) {
            const frame = this.frames[id]
            if (frame) frame.el = el ? markRaw(el) : null
        },

        setOverlayEl(id, el) {
            const frame = this.frames[id]
            if (frame) frame.overlayEl = el ? markRaw(el) : null
        },

        unregister(id) {
            delete this.frames[id]
        },

        setHostMounted(mounted) {
            this.hostMounted = mounted
        },

        bumpGeometry() {
            this.geometryEpoch++
        },

        beginDividerDrag() {
            this.dividerDragDepth++
        },

        endDividerDrag() {
            if (this.dividerDragDepth > 0) this.dividerDragDepth--
        },
    },
})
```

### 3.2 New file `frontend/src/stores/framePool.test.js`

`node:test` (run from `frontend/`: `node --test src/stores/framePool.test.js`) — Pinia
works in Node without a DOM:

```js
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useFramePoolStore } from './framePool.js'

beforeEach(() => {
    setActivePinia(createPinia())
})

test('registration order is append-only and survives middle deletion', () => {
    const pool = useFramePoolStore()
    pool.register('a', { src: 'x' })
    pool.register('b', { src: 'x' })
    pool.register('c', { src: 'x' })
    pool.unregister('b')
    pool.register('d', { src: 'x' })
    assert.deepEqual(Object.keys(pool.frames), ['a', 'c', 'd'])
})

test('patch and setRect only touch existing frames', () => {
    const pool = useFramePoolStore()
    pool.patch('ghost', { visible: true }) // no throw, no creation
    pool.setRect('ghost', { x: 1, y: 1, width: 1, height: 1 })
    assert.deepEqual(Object.keys(pool.frames), [])
    pool.register('a', { src: 'x' })
    pool.patch('a', { visible: true, zTier: 'overlay' })
    assert.equal(pool.frames.a.visible, true)
    assert.equal(pool.frames.a.zTier, 'overlay')
})

test('divider drag depth nests and clamps', () => {
    const pool = useFramePoolStore()
    assert.equal(pool.isDividerDragging, false)
    pool.beginDividerDrag()
    pool.beginDividerDrag()
    pool.endDividerDrag()
    assert.equal(pool.isDividerDragging, true)
    pool.endDividerDrag()
    pool.endDividerDrag() // extra end must not go negative
    assert.equal(pool.isDividerDragging, false)
})

test('geometry epoch increments', () => {
    const pool = useFramePoolStore()
    pool.bumpGeometry()
    pool.bumpGeometry()
    assert.equal(pool.geometryEpoch, 2)
})
```

## 4. Task 2 — `PersistentFrame.vue`, `FrameHost.vue`, drag-flag composable

### 4.1 New file `frontend/src/components/frames/PersistentFrame.vue`

```vue
<script setup>
// Placeholder side of the persistent-frame system (see stores/framePool.js).
// Renders an empty div where the iframe visually belongs; the actual <iframe>
// is rendered by FrameHost, positioned over this placeholder's live rect, and
// NEVER moves in the DOM — so KeepAlive session switches and dock Teleports
// stop reloading it. Falls back to a plain inline iframe when no host is
// mounted (contexts outside ProjectView).
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useElementBounding } from '@vueuse/core'
import { useFramePoolStore } from '../../stores/framePool'

const props = defineProps({
    // Unique, stable identity (derive from useId() in the owner). Two cached
    // sessions must NOT share an id.
    frameId: { type: String, required: true },
    src: { type: String, required: true },
    // Changing it re-creates the iframe element in place (intentional reload).
    remountKey: { type: [String, Number], default: 0 },
    // Extra iframe attributes (sandbox, allow, title, …), applied via v-bind.
    attrs: { type: Object, default: () => ({}) },
    // Owner-provided stacking situation (see plan §2 z-tiers).
    elevated: { type: Boolean, default: false },   // inside the docking overlay
    fullscreen: { type: Boolean, default: false }, // preview expanded full-window
})

const emit = defineEmits(['load'])

const pool = useFramePoolStore()
const placeholderEl = ref(null)
const inlineFrameEl = ref(null)
const inlineOverlayEl = ref(null)

// Pooled unless no host exists — decided at mount time (a host appearing later
// would re-home the iframe, i.e. reload it; not worth handling). ProjectView
// sets hostMounted in its OWN setup, which runs before any of its children —
// so this snapshot is reliable even on cold-load deep links where panes mount
// before FrameHost's DOM does.
const pooled = pool.hostMounted

const zTier = computed(() => (props.fullscreen ? 'fullscreen' : props.elevated ? 'overlay' : 'base'))

// KeepAlive detach is invisible to ResizeObserver — the hooks are the
// authoritative signal.
const activated = ref(true)
onActivated(() => {
    activated.value = true
    bounding.update()
})
onDeactivated(() => {
    activated.value = false
})

const bounding = useElementBounding(placeholderEl)
const visible = computed(
    () => activated.value && bounding.width.value > 0.5 && bounding.height.value > 0.5
)

if (pooled) {
    pool.register(props.frameId, {
        src: props.src,
        remountKey: props.remountKey,
        attrs: props.attrs,
        zTier: zTier.value,
        onLoad: (event) => emit('load', event),
    })
    onBeforeUnmount(() => pool.unregister(props.frameId))

    watch([() => props.src, () => props.remountKey, () => props.attrs, zTier], () => {
        pool.patch(props.frameId, {
            src: props.src,
            remountKey: props.remountKey,
            attrs: props.attrs,
            zTier: zTier.value,
        })
    })
    watch(visible, (v) => pool.patch(props.frameId, { visible: v }), { immediate: true })
    watch(
        [bounding.x, bounding.y, bounding.width, bounding.height],
        ([x, y, width, height]) => pool.setRect(props.frameId, { x, y, width, height })
    )
    // Layout mutations that move without resizing (dock retarget, overlay
    // open/close, maximize) are announced through the epoch.
    watch(() => pool.geometryEpoch, () => bounding.update(), { flush: 'post' })
}

onMounted(() => bounding.update())

// The live iframe element (pooled or inline) — owners use it for
// contentWindow (companion postMessage) and load listeners (broker).
const frameEl = computed(() =>
    pooled ? pool.frameEl(props.frameId) : inlineFrameEl.value
)
// Target for the owner's over-iframe chrome (Teleport :to) — sits above the
// iframe in the host cell (or locally in inline mode).
const overlayEl = computed(() =>
    pooled ? pool.frameOverlayEl(props.frameId) : inlineOverlayEl.value
)

defineExpose({ frameEl, overlayEl })
</script>

<template>
    <div ref="placeholderEl" class="persistent-frame-placeholder">
        <template v-if="!pooled">
            <iframe
                ref="inlineFrameEl"
                :key="remountKey"
                :src="src"
                v-bind="attrs"
                class="persistent-frame-inline"
                @load="emit('load', $event)"
            ></iframe>
            <div ref="inlineOverlayEl" class="persistent-frame-inline-overlay"></div>
        </template>
    </div>
</template>

<style scoped>
.persistent-frame-placeholder {
    position: relative;
    overflow: hidden;
}

.persistent-frame-inline {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: none;
    background: #fff;
}

/* Same opt-in pointer-events contract as FrameHost's .frame-overlay-layer:
   the layer is inert, each teleported piece re-enables itself. */
.persistent-frame-inline-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
}
</style>
```

### 4.2 New file `frontend/src/components/frames/FrameHost.vue`

```vue
<script setup>
// Host side of the persistent-frame system (see stores/framePool.js and
// PersistentFrame.vue). Mounted ONCE in ProjectView's .main-content, after the
// KeepAlive branches. Renders every registered iframe absolutely positioned
// over its placeholder rect. The v-for relies on the store's append-only key
// order — iframe nodes must never move in the DOM (that reloads them).
import { computed, ref, watch } from 'vue'
import { useElementBounding } from '@vueuse/core'
import { useFramePoolStore } from '../../stores/framePool'

// NOTE: pool.hostMounted is owned by ProjectView (set in its setup, cleared in
// its onUnmounted), NOT here — panes can mount before this component does on
// cold-load deep links, and the flag must already be true for them.
const pool = useFramePoolStore()
const hostEl = ref(null)
const hostRect = useElementBounding(hostEl)

const ids = computed(() => Object.keys(pool.frames))

const Z_TIERS = {
    base: 2,        // above pane content, below the docking overlay backdrop (8)
    overlay: 11,    // docking overlay panel level (host renders later in DOM → wins the tie)
    fullscreen: 1001, // above the fullscreen preview wrapper (1000)
}

function cellStyle(frame) {
    const { x, y, width, height } = frame.rect
    const zIndex = Z_TIERS[frame.zTier] ?? Z_TIERS.base
    if (frame.zTier === 'fullscreen') {
        // The fullscreen wrapper is position:fixed while .main-content drops
        // its container-type (main-content--preview-expanded), so viewport
        // coordinates are correct AND escape .main-content's overflow clip.
        return {
            position: 'fixed',
            left: `${x}px`,
            top: `${y}px`,
            width: `${width}px`,
            height: `${height}px`,
            zIndex,
        }
    }
    return {
        position: 'absolute',
        left: `${x - hostRect.x.value}px`,
        top: `${y - hostRect.y.value}px`,
        width: `${width}px`,
        height: `${height}px`,
        zIndex,
    }
}

watch(() => pool.geometryEpoch, () => hostRect.update(), { flush: 'post' })
</script>

<template>
    <div ref="hostEl" class="frame-host" :class="{ 'frame-host--dragging': pool.isDividerDragging }">
        <div
            v-for="id in ids"
            :key="id"
            class="frame-cell"
            :class="{ 'frame-cell--hidden': !pool.frames[id].visible }"
            :style="cellStyle(pool.frames[id])"
        >
            <iframe
                :key="pool.frames[id].remountKey"
                :src="pool.frames[id].src"
                v-bind="pool.frames[id].attrs"
                class="frame-iframe"
                :ref="(el) => pool.setFrameEl(id, el)"
                @load="pool.frames[id].onLoad && pool.frames[id].onLoad($event)"
            ></iframe>
            <div class="frame-overlay-layer" :ref="(el) => pool.setOverlayEl(id, el)"></div>
        </div>
    </div>
</template>

<style scoped>
/* The host itself is inert glass over .main-content; only cells take events. */
.frame-host {
    position: absolute;
    inset: 0;
    pointer-events: none;
}

.frame-cell {
    pointer-events: auto;
}

/* visibility (not display): the page keeps rendering/running while hidden, so
   a cached session's dev server stays exactly where the user left it. */
.frame-cell--hidden {
    visibility: hidden;
    pointer-events: none;
}

.frame-iframe {
    width: 100%;
    height: 100%;
    border: none;
    /* Most pages assume a light default background (same rationale as the old
       in-pane .browser-frame / .html-preview rules). */
    background: #fff;
}

/* Owner chrome teleported over the iframe (preview actions, route callout).
   NO generic `> *` re-enable rule here: it would tie (0-2-0 specificity, bundle
   source order decides) with owner rules like FilesPanel's
   `.pane-callout-overlay { pointer-events: none }` and could turn a full-size
   teleported wrapper into a click shield over the iframe. Each teleported
   piece opts back in itself (owner-scoped rules reach teleported nodes). */
.frame-overlay-layer {
    position: absolute;
    inset: 0;
    pointer-events: none;
}

/* While a divider drag is in progress the iframes must not swallow pointer
   events (an iframe is a separate browsing context) — same rationale as
   SessionLayout's `.resizing :deep(iframe)` rule, which no longer reaches
   pooled frames. */
.frame-host--dragging .frame-iframe {
    pointer-events: none;
}
</style>
```

Ref-callback timing: Vue invokes a function ref synchronously when its own vnode
mounts, **before the next sibling is patched** — hence the two independent setters
(`setFrameEl` / `setOverlayEl`) instead of one callback reading `nextElementSibling`
(which would be `null` on first mount). On remount (`remountKey` change) Vue re-invokes
the iframe's ref with the fresh element.

### 4.3 New file `frontend/src/composables/useSplitDividerDragFlag.js`

```js
import { onBeforeUnmount, onMounted } from 'vue'
import { useFramePoolStore } from '../stores/framePool'

/**
 * Flag divider drags of a <wa-split-panel> into the frame pool so FrameHost
 * can neutralize iframe pointer-events for the duration (an iframe would
 * otherwise capture pointermove and freeze the drag). The docking gutters
 * have their own wiring in SessionLayout.vue; this covers the three plain
 * wa-split-panels (project sidebar, FilesPanel tree/content, GitPanel
 * tree/content) whose drags over an iframe are broken today already.
 */
export function useSplitDividerDragFlag(splitPanelRef) {
    const pool = useFramePoolStore()
    let dragging = false

    function onPointerDown(event) {
        const onDivider = event
            .composedPath()
            .some((node) => node?.getAttribute?.('part')?.split(' ').includes('divider'))
        if (!onDivider) return
        dragging = true
        pool.beginDividerDrag()
    }

    function onPointerEnd() {
        if (!dragging) return
        dragging = false
        pool.endDividerDrag()
    }

    onMounted(() => {
        splitPanelRef.value?.addEventListener('pointerdown', onPointerDown)
        window.addEventListener('pointerup', onPointerEnd, true)
        window.addEventListener('pointercancel', onPointerEnd, true)
    })
    onBeforeUnmount(() => {
        splitPanelRef.value?.removeEventListener('pointerdown', onPointerDown)
        window.removeEventListener('pointerup', onPointerEnd, true)
        window.removeEventListener('pointercancel', onPointerEnd, true)
        onPointerEnd() // never leave the depth stuck if unmounted mid-drag
    })
}
```

## 5. Task 3 — ProjectView: mount the host, containing block, sidebar split flag

`frontend/src/views/ProjectView.vue`:

1. Import + mount. After the artifacts-browser `<div>` (line ~2392), right before
   `</main>`:

   ```html
           <FrameHost />
       </main>
   ```

   with `import FrameHost from '../components/frames/FrameHost.vue'` in the script.

   **ProjectView owns the `hostMounted` flag** — in setup (so it is true before ANY
   child mounts, including cold-load deep links where a pane's frame mounts before
   FrameHost's DOM):

   ```js
   import { useFramePoolStore } from '../stores/framePool'
   const framePool = useFramePoolStore()
   framePool.setHostMounted(true)
   onUnmounted(() => framePool.setHostMounted(false))
   ```

2. `.main-content` CSS gains an explicit containing block for the host (today it relies
   on `container-type`, which `--preview-expanded` drops — `position:relative` is stable
   in both states):

   ```css
   .main-content {
       /* … existing rules … */
       position: relative; /* containing block for FrameHost regardless of container-type */
   }
   ```

   Implementation note: this also makes `.main-content` the containing block for
   absolute descendants in the *expanded* state (container-type previously did it only
   in the normal state) — eyeball existing absolute floaters inside it (e.g.
   `.new-session-split-button`) for regressions; none expected.

3. Project sidebar split drag flag. The `<wa-split-panel class="project-view">` element
   gets a ref (`const projectSplitRef = ref(null)` + `ref="projectSplitRef"` on the
   element) and:

   ```js
   import { useSplitDividerDragFlag } from '../composables/useSplitDividerDragFlag'
   useSplitDividerDragFlag(projectSplitRef)
   ```

No geometry-epoch bump needed here: sidebar toggling/split dragging resizes
`.main-content` and the placeholders, so ResizeObserver fires on both sides.

## 6. Task 4 — SessionView / SessionLayout: drag flag + geometry epochs

1. **`frontend/src/components/session/layout/SessionLayout.vue`** — wire the existing
   gutter drag state into the pool with DIRECT calls, not a watcher (a pre-flush watch
   job is dropped when the component unmounts mid-drag — `endDrag()` in
   `onBeforeUnmount` would never release the depth, freezing every iframe's pointer
   events). The CSS rule at line ~259 stays for inline-fallback frames but no longer
   reaches pooled ones:

   ```js
   import { useFramePoolStore } from '../../../stores/framePool'
   const framePool = useFramePoolStore()
   // In onSplitterDown (where draggingId is set):
   framePool.beginDividerDrag()
   // In endDrag: ADD a guard — endDrag() is currently unconditional and also
   // runs from onBeforeUnmount even when no drag ever started (and again after
   // a finished drag); an unguarded end call would decrement some OTHER
   // component's live drag. Early-return `if (!drag) return` at the top (or
   // wrap only the pool call in `if (drag)`), then:
   framePool.endDividerDrag()
   ```

2. **`frontend/src/views/SessionView.vue`** — bump the geometry epoch after layout
   mutations that can move a placeholder without resizing it (ResizeObserver-blind):

   ```js
   import { useFramePoolStore } from '../stores/framePool'
   const framePool = useFramePoolStore()
   // Dock re-assignments, overlay open/close, maximize/minimize, region
   // resizes: re-measure pooled frames after the DOM settles.
   watch(
       [() => layout.render.value, () => layout.openOverlayEdge.value, () => layout.maximizedRegion.value],
       () => nextTick(() => framePool.bumpGeometry()),
       { flush: 'post' }
   )
   ```

   (Names verified against `useSessionLayout.js`'s return object: `render`,
   `openOverlayEdge`, `maximizedRegion`. No `deep: true` — `resolveLayout` returns a
   fresh object every recompute, so identity alone triggers, and this watcher fires per
   pointermove during splitter drags.)

3. **Elevated (overlay) flags** for the four frame-bearing tool panes (FilePane is
   rendered by the Files and Artifacts panels AND by PlanPane in render-only mode —
   an HTML plan document gets a pooled frame too):

   ```js
   const browserFrameElevated = computed(() => layout.targetKeyForTab('browser') === 'overlay')
   const filesFrameElevated = computed(() => layout.targetKeyForTab('files') === 'overlay')
   const artifactsFrameElevated = computed(() => layout.targetKeyForTab('artifacts') === 'overlay')
   const planFrameElevated = computed(() => layout.targetKeyForTab('plan') === 'overlay')
   ```

   passed as `:frame-elevated="…"` on `<BrowserPane>`, the two `<FilesPanel>`
   instances, and `<PlanPane>`.

4. **`frontend/src/components/git/GitPanel.vue`** — third plain split (tree/content,
   `wa-split-panel` at line ~1620): add a ref on it + `useSplitDividerDragFlag(ref)`,
   same one-liner as FilesPanel (its drag over a visible pooled frame freezes today
   already).

5. **`frontend/src/components/plan/PlanPane.vue`** — new pass-through prop
   `frameElevated` (default false) forwarded to its `<FilePane>`.

## 7. Task 5 — BrowserPane adoption

`frontend/src/components/browser/BrowserPane.vue` deltas:

1. New prop, imports:

   ```js
   import PersistentFrame from '../frames/PersistentFrame.vue'
   // props: add
   frameElevated: { type: Boolean, default: false },
   ```

2. Replace the iframe element (template) — same `v-if`, class kept for layout. The
   attrs object is a module constant, NOT an inline literal (a fresh identity per
   render would churn the `[src, remountKey, attrs, zTier]` watch and re-patch the
   store on every re-render):

   ```js
   const BROWSER_FRAME_ATTRS = { allow: 'clipboard-read; clipboard-write; fullscreen', title: 'Browser' }
   ```

   ```html
           <PersistentFrame
               v-if="everActivated && currentUrl && !mixedContentBlocked"
               ref="persistentFrameRef"
               :frame-id="`browser:${instanceId}`"
               :src="frameSrc"
               :remount-key="frameKey"
               :attrs="BROWSER_FRAME_ATTRS"
               :elevated="props.frameElevated"
               class="browser-frame"
               @load="onFrameLoad"
           />
   ```

3. `frameEl` becomes a computed over the exposed element (all existing call sites —
   `sendToCompanion`, `onWindowMessage`'s `event.source` check — keep reading
   `frameEl.value`):

   ```js
   const persistentFrameRef = ref(null)
   const frameEl = computed(() => persistentFrameRef.value?.frameEl ?? null)
   ```

   (delete the old `const frameEl = ref(null)`.)

4. CSS: `.browser-frame` keeps `flex: 1; width: 100%; height: 100%;` (placeholder
   sizing); drop `border: none; background: #fff;` (now on the host's `.frame-iframe`).

5. Header comment: update the fallback-mode sentence — dock moves and session switches
   no longer reload the frame; `frameKey` remount and address-bar hard navigations still
   re-create it (via `remount-key`).

**Companion interplay — nothing to change.** The handshake, `event.source ===
frameEl.value.contentWindow` validation and `sendToCompanion` all read the live element
through the pool; the element is the same object across session switches now, so the
presence machine simply stops seeing spurious `bye`/`hello` cycles on tab/session
switches. `pagehide`-on-detach used to fire when KeepAlive parked the session — that
whole path disappears (the frame is never detached).

## 8. Task 6 — FilePane adoption (HTML preview only) + FilesPanel

`frontend/src/components/files/FilePane.vue`:

1. New prop `frameElevated: { type: Boolean, default: false }` + import
   `PersistentFrame`.

2. Replace the HTML preview iframe (template lines ~1304-1312) — same `v-if`, key
   semantics preserved (`remount-key = filePath` re-creates on file switch; the reload
   button keeps working through `htmlPreviewSrc`'s cache-bust param → src navigation):

   ```js
   const HTML_PREVIEW_FRAME_ATTRS = { sandbox: 'allow-scripts allow-same-origin allow-forms', title: 'HTML preview' }
   ```

   ```html
                   <PersistentFrame
                       v-if="showHtmlPreview && isHtmlFile && htmlPreviewSrc && !diffMode"
                       ref="persistentFrameRef"
                       :frame-id="`artifact-html:${instanceId}`"
                       :src="htmlPreviewSrc"
                       :remount-key="filePath"
                       :attrs="HTML_PREVIEW_FRAME_ATTRS"
                       :elevated="props.frameElevated"
                       :fullscreen="isPreviewFullscreen"
                       class="html-preview"
                   />
   ```

   (module-constant attrs for the same watch-churn reason as BrowserPane)

   `instanceId` from `useId()` (add if FilePane doesn't already keep one; it has
   several `useId()` button ids — introduce `const instanceId = useId()`).

3. Broker wiring: `previewIframeRef` becomes the exposed element (the composable
   already takes an arbitrary ref and re-binds its own `load` listener):

   ```js
   const persistentFrameRef = ref(null)
   const previewIframeRef = computed(() => persistentFrameRef.value?.frameEl ?? null)
   ```

   (delete the old `const previewIframeRef = ref(null)`; the `useArtifactBroker(...)`
   call and its watch list are untouched — it already handles the element appearing,
   disappearing and being replaced.)

4. Over-iframe chrome moves into the frame's overlay layer **only while it exists** —
   `<Teleport :disabled>` renders in place otherwise, so every other preview type keeps
   today's DOM:

   ```html
   <Teleport :to="frameOverlayEl" :disabled="!frameOverlayEl">
       <!-- existing .preview-actions div, unchanged -->
       <!-- existing <ArtifactBrokerPrompt :prompt="brokerPrompt" @decision="onBrokerDecision" /> -->
   </Teleport>
   ```

   ```js
   const frameOverlayEl = computed(() =>
       isHtmlPreviewActive.value ? (persistentFrameRef.value?.overlayEl ?? null) : null
   )
   ```

   Both nodes are plain Vue-rendered DOM — teleporting them is safe (unlike iframes);
   scoped styles follow the component, so `.preview-actions` CSS keeps applying.
   `frameOverlayEl` is also added to `defineExpose` (FilesPanel needs it, see item 9).

5. Hover-reveal + pointer-events CSS: `.file-pane-preview:hover .preview-action-btn
   { opacity: 1 }` stops matching once the buttons are teleported out, and the frame
   overlay layer is `pointer-events: none` with NO generic re-enable (see FrameHost
   CSS note) — each teleported piece opts back in itself. Add to FilePane's scoped
   styles (scoped selectors only stamp the data-v attribute on the FINAL element, so a
   FrameHost-owned ancestor class works):

   ```css
   .frame-overlay-layer:hover .preview-action-btn {
       opacity: 1;
   }

   .preview-actions {
       pointer-events: auto; /* re-enable inside the inert frame overlay layer */
   }
   ```

   (FilesPanel's `.pane-callout-overlay` already carries its own
   `pointer-events: none` + re-enable pair; `ArtifactBrokerPrompt` is a `wa-dialog` —
   native top-layer, indifferent to the layer's pointer-events.)

6. Comments: update the two "never teleported / moving an iframe reloads it" blocks
   (script ~556-566, template ~1267-1275) — the statement stays true and is now the
   *reason* the HTML iframe lives in the FrameHost layer; PDF/media previews still
   expand in place. Also fix the stale `.file-pane-preview--fullscreen` CSS comment
   ("teleported to `<body>`" — it never was; it expands in place).

7. CSS: `.html-preview` keeps its box rules (absolute inset 0) as placeholder sizing;
   drop iframe-only rules (border/background) if any — verify at implementation.

`frontend/src/components/files/FilesPanel.vue`:

8. New prop `frameElevated: { type: Boolean, default: false }`, passed through to
   `<FilePane :frame-elevated="frameElevated" …>` (all FilePane render sites in the
   panel).

9. `.pane-callout-overlay` (z 20, the routeIssueMessage callout) paints above the
   inline iframe today but — like all pane-local z-index — cannot beat a pooled frame
   (DockRegion's isolated context). Teleport it into the active frame's overlay layer
   when one exists, same `:disabled` pattern:

   ```html
   <Teleport :to="filePaneFrameOverlayEl" :disabled="!filePaneFrameOverlayEl">
       <!-- existing .pane-callout-overlay div, unchanged -->
   </Teleport>
   ```

   ```js
   const filePaneFrameOverlayEl = computed(() => filePaneRef.value?.frameOverlayEl ?? null)
   ```

   (uses FilePane's exposed `frameOverlayEl` from item 4; when teleported, the callout
   covers the preview area instead of the whole panel — acceptable, it stays visible
   and clickable, which is the requirement.)

10. Inner tree/content split drag flag — the panel already keeps a ref on its split
    (`splitPanelRef`, FilesPanel.vue:1012); reuse it:

   ```js
   import { useSplitDividerDragFlag } from '../../composables/useSplitDividerDragFlag'
   useSplitDividerDragFlag(splitPanelRef)
   ```

Other FilePane hosts (`ArtifactsBrowserView`, project-detail) pass nothing —
`frameElevated` defaults to false (they are never inside the docking overlay),
`fullscreen` still works there (the wrapper + `expandPreviewHost` machinery is
host-agnostic). PlanPane is covered by Task 4 item 5. The standalone artifact-shell
page does NOT use FilePane (own minimal app with a local iframe) — untouched.

## 9. Task 7 — Docs

1. **CLAUDE.md**, Frontend Patterns section — new short subsection after "Virtual
   scrolling":

   > ### Persistent frames (iframes)
   >
   > An `<iframe>` reloads whenever its DOM node is detached or re-parented — which is
   > what KeepAlive (session switch) and Teleport (dock moves) do to panes. Embedded
   > pages that must survive (Browser pane, artifact HTML preview) therefore render
   > through `PersistentFrame` (`frontend/src/components/frames/`): the pane keeps a
   > placeholder; the real iframe lives in `FrameHost` (mounted once in ProjectView),
   > absolutely positioned over the placeholder's rect (`stores/framePool.js`). Never
   > `<Teleport>` an iframe and never reorder `FrameHost`'s registry — both move the
   > node and reload it. Over-iframe chrome goes through the frame's overlay layer
   > (`overlayEl` + `<Teleport :disabled>`), not pane-local z-index (capped by
   > `DockRegion`'s `isolation: isolate`).

2. **AGENTS.md** — mirror byte-for-byte.

3. In-code comment updates already listed in Tasks 5-6 (BrowserPane header, FilePane
   expand blocks, SessionLayout CSS rule note).

Historical plan docs (browser-pane, companion) stay untouched.

## 10. Task 8 — Verification

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab/frontend
node --test src/stores/framePool.test.js src/browser-companion/protocol.test.js src/utils/browserUrl.test.js
npm run build
cd .. && TWICC_DATA_DIR=$PWD uv run --active pytest tests/ -q   # unchanged, sanity
uv run --active ruff check src/ tests/                          # unchanged, sanity
```

Manual E2E script (user, after `devctl start`):

1. Session A → Browser tab → load the dev server, navigate somewhere inside the page,
   scroll. Switch to session B, come back → **no reload**: same page, same scroll, plug
   icon stayed green the whole time (no `bye`/`hello` churn in companion mode).
2. Same with an artifact HTML page holding JS state (e.g. a counter) → switch away and
   back → counter intact.
3. Move the Browser tab between docks (center → side dock → overlay) → no reload; in
   the overlay, the frame renders above the overlay panel and below dialogs.
4. Artifact HTML preview → Full screen → frame covers the window, the floating
   expand/compress toggle and the broker consent prompt stay clickable above the
   iframe; collapse → back in place. Trigger a broker consent (artifact fetch to a new
   host) in both states.
5. Drag the project sidebar split, a dock gutter, the FilesPanel tree split and the
   GitPanel tree split across an iframe → the drag keeps tracking (no freeze).
5b. Open an HTML plan document in the Plan tab (render-only FilePane) docked to the
   overlay → the frame renders above the overlay panel. Trigger a route-issue callout
   in a Files/Artifacts pane showing an HTML preview → the callout is visible above
   the iframe.
5c. Cold-load checks: open a session URL whose remembered tab is Browser directly (page
   reload) and an artifacts URL on an HTML bookmark directly → the frames must be
   POOLED (inspect: iframe lives under `.frame-host`, not inline) — this exercises the
   hostMounted-before-children ordering.
6. Open a wa-dialog / dropdown / command palette over a visible frame → they render
   above it (top-layer).
7. Close the session (evict from cache / navigate to all-projects) → frames are
   destroyed (no orphan iframes in the host, check devtools).
8. Address bar navigation and Refresh in fallback mode still remount the frame
   (remount-key), companion mode still navigates in place.

## 11. Gotchas encountered at design time (do not re-litigate)

- **Coordinates must be host-relative, not viewport-`fixed`** (except the fullscreen
  tier): `container-type: inline-size` on `.main-content` makes it the containing block
  for fixed descendants, and `--preview-expanded` toggles that off — a fixed-positioned
  cell would jump between coordinate systems. Absolute-in-host with
  `placeholderRect − hostRect` is immune; the fullscreen tier flips to `fixed` exactly
  when the container-type is dropped.
- **Pane-local z-index cannot beat the host layer** — `DockRegion` uses
  `isolation: isolate`, capping every pane-internal z-index inside a context that sits
  below the host's cells. Hence the overlay layer + Teleport for `.preview-actions`,
  `ArtifactBrokerPrompt` and FilesPanel's `.pane-callout-overlay`; do not try to raise
  them with z-index in the pane.
- **The overlay layer has no generic pointer-events re-enable** (review finding): a
  scoped `.frame-overlay-layer > *` rule ties at 0-2-0 specificity with owner rules
  like `.pane-callout-overlay { pointer-events: none }` — bundle source order would
  decide, and the wrong winner turns a full-size teleported wrapper into a click
  shield over the iframe. Each teleported piece opts back in itself.
- **`onDeactivated`/`onActivated` are the detach signal, not ResizeObserver** — RO does
  not reliably emit when an observed element is removed from the DOM, so a KeepAlive
  park would leave a stale visible rect without the hooks.
- **The epoch exists for position-only moves** — a dock retarget can relocate a pane
  without resizing it; RO and window-scroll listeners are both blind to that.
- **Append-only registry order** — a keyed `v-for` reorder moves iframe DOM nodes,
  which is precisely the reload this whole design exists to avoid.
- **`visibility: hidden`, not `display: none`, for hidden cells** — display:none zeroes
  the iframe's layout; some pages misbehave on re-show (and rAF-driven apps pause
  differently). Visibility keeps rendering semantics stable.
- **Frames must not take pointer events while hidden or during divider drags** — an
  iframe is a separate browsing context that swallows pointer events; both states set
  `pointer-events: none`.
- **WA floating UI is top-layer** (`showModal()` / `showPopover()`) — dialogs,
  dropdowns, tooltips and the command palette always paint above frames by
  construction; no z-tier needed for them.
- **`hostMounted` must be set by ProjectView's setup, not FrameHost's lifecycle**
  (review finding, BLOCKER): FrameHost mounts after the KeepAlive branches (its DOM
  position wins the z=11 tie), so on cold-load deep links the panes mount first — a
  FrameHost-owned flag would trap them in the inline fallback permanently.
- **Function refs fire before the next sibling exists** (review finding): the iframe's
  ref callback cannot read `nextElementSibling` to find the overlay div on first mount
  — hence the two separate setter callbacks.
- **Watch sources must be identity-stable** (review finding): inline `:attrs="{…}"`
  literals re-trigger the descriptor watch on every owner render — attrs are module
  constants in both owners.
- **Every FilePane host is a frame host** (review finding): PlanPane's render-only
  FilePane pools an HTML plan document too — the elevated flag wiring must cover it,
  not just Files/Artifacts/Browser.
