# Artifact HTML Preview — Select Mode, Screenshots & Responsive Mode

**Date:** 2026-07-06
**Branch:** `browser-tab` (follows the Browser pane, companion, persistent-frames and responsive-mode work)
**Status:** code-complete plan; implementation NOT started — waiting for explicit go.

This plan is written to be executed as-is: new files are given in full, edits to
existing files are given as exact old → new blocks. Line numbers refer to the tree at
commit `0ab679f3` and are hints only — always match on the quoted code. Execute tasks
in order; tasks 1–3 are behavior-neutral refactors of the Browser pane, tasks 4–5 add
the feature to FilePane, task 6 is docs + build + verification.

## 1. Goal

Bring three Browser-pane features to the **HTML artifact preview** (FilePane's HTML
preview iframe) **in a session context only** — the session Artifacts tab and the
session Files tab for any `.html` file. The sessionless sidebar Artifacts browser
(slash route) is deliberately excluded (§3):

1. **Select mode** — pick an element in the rendered page, walk the DOM from it, hand
   a description (selector chain + opening tag + text) to the agent via the comment
   widget.
2. **Element screenshots** — the comment widget's "Include screenshot" switch,
   rendering the picked element to a PNG attached to the session draft.
3. **Responsive mode** — exact CSS-pixel viewport (presets, manual W×H, swap, drag
   handles) inside the scrollable hatched canvas.

## 2. Key architectural decision — direct DOM access, no injected companion

The Browser pane needs the companion script because its page is **cross-origin**. The
artifact preview iframe is **same-origin** with the SPA (`/api/…/file-raw/…`,
`sandbox="allow-scripts allow-same-origin allow-forms"`), so the SPA can script
`frameEl.contentDocument` directly. The artifact CSP (`broker_html.py`) is irrelevant
here: it governs what the artifact document loads/connects to, not same-origin DOM
manipulation by the parent.

**Decision:** the element picker is extracted from `companion.js` into a shared,
environment-agnostic module parameterized on `(win, doc)`:

- `companion.js` keeps using it *inside* the user's page, wrapped in its existing
  postMessage plumbing (behavior-neutral for the Browser pane).
- FilePane uses it *from the SPA*, on the iframe's `contentWindow`/`contentDocument`,
  calling methods directly.

Rejected alternative — inject a second script into artifact documents (like the broker
shim): a third bundle to build/serve/version, a handshake, and an async transport
where a function call suffices. Kept only as a fallback if the parent-side screenshot
capture fails (§6, R1).

Responsive mode is already **pure host-side** in the Browser pane; it transfers by
extraction alone.

## 3. Scope & gating

| Context | Responsive | Select + screenshot |
|---|---|---|
| Session **Artifacts tab** (FilesPanel render-only → FilePane) | yes | yes |
| Session **Files tab**, any `.html` preview | yes | yes |
| Sidebar **Artifacts browser** (`ArtifactsBrowserView`, the slash-route viewer) | **no** | **no** |
| Dedicated artifact page (`/artifacts/<id>/`, standalone shell) | no (out of scope) | no (out of scope) |

**Both features are gated on session context** — the sidebar Artifacts browser is a
sessionless cross-session viewer, so it gets neither (user decision: "no reason to have
any of this there"). The exact discriminator is the **`insertTextAtCursor` inject**,
which **only `SessionView` provides** (`views/SessionView.vue:292`, unconditional) —
present in the Files tab AND the Artifacts tab (both live inside a SessionView), absent
in `ArtifactsBrowserView` (a separate top-level view). So:

```js
// Inside a SessionView (Files or Artifacts tab) → both toggles show; the
// sessionless slash-route Artifacts browser has no such inject → neither shows.
const inSessionContext = computed(() => !!insertTextAtCursor)
```

- **Both toggles** render only when `inSessionContext` is true.
- **Select additionally** needs a session id for the screenshot draft attachment.
  Beware the wiring quirk: the session **Artifacts tab passes `session-id="null"`**
  (`SessionView.vue:2336`) and carries the session as `artifactBookmarkSessionId`
  (`:2343`), while the **Files tab passes `session-id="session.id"`** (`:2256`,
  `artifactBookmarkSessionId` null). So the composer session id is
  `props.sessionId || props.artifactBookmarkSessionId` — NOT `props.sessionId` alone
  (which would wrongly disable the feature in the Artifacts tab). `insertTextAtCursor`
  itself needs no id: it targets the enclosing SessionView's own composer.
- On artifact **reload** (agent edit bumping the cache-bust src, manual reload,
  KeepAlive re-parenting) the select mode **re-arms automatically** on the fresh
  document (selection cleared) — unlike the Browser pane, which drops the mode on
  `hello` because a navigation may land anywhere; artifact reloads are iterations on
  the same document. A **file switch** turns the mode off.
- Deliberately not included: page-error capture for artifacts, whole-page screenshots,
  persistence of viewport size / select state (parity with the Browser pane).

## 4. New/changed files overview

```
frontend/src/element-select/picker.js                 NEW   shared picker core (task 1)
frontend/src/browser-companion/companion.js           EDIT  becomes a postMessage adapter (task 1)
frontend/src/components/frames/SelectAreaToolbar.vue  NEW   shared sub-toolbar (task 2)
frontend/src/components/frames/viewport.js            NEW   shared presets/bounds (task 3)
frontend/src/components/frames/ViewportToolbar.vue    NEW   shared sub-toolbar (task 3)
frontend/src/components/frames/ViewportStage.vue      NEW   shared body/canvas/stage/handles (task 3)
frontend/src/components/browser/BrowserPane.vue       EDIT  refactor onto the shared pieces (tasks 2–3)
frontend/src/components/files/FilePane.vue            EDIT  responsive + select integration (tasks 4–5)
CLAUDE.md / AGENTS.md                                 EDIT  docs sync (task 6)
```

No backend change. No protocol change (`browser-companion/protocol.js` untouched).
`wa-select` / `wa-option` / `wa-input` are already imported in `main.js` (used by the
Browser pane today). Icons `mobile-screen-button`, `arrow-pointer`, `right-left`,
`ban`, `comment`, `xmark`, the four arrows — all already in use, FA-free.

---

## Task 1 — Extract the picker core; rewire `companion.js`

### 1a. NEW `frontend/src/element-select/picker.js`

Verbatim extraction of `companion.js`'s select machinery (currently lines ~97–343),
with `window`/`document` replaced by injected `win`/`doc` and postMessage calls
replaced by return values / the `onState` callback.

```js
// In-page element picker, shared by two run contexts:
// - the browser companion (browser-companion/companion.js) runs it INSIDE the
//   user's own page and drives it over postMessage from the Browser pane;
// - the SPA runs it directly against the artifact HTML preview's same-origin
//   iframe (FilePane), calling methods and receiving callbacks synchronously.
// Framework-free and parameterized on the target window/document on purpose:
// it must never touch globals (the companion bundles it into its IIFE, the
// SPA lazy-imports it).
import { domToPng } from 'modern-screenshot'

// Description caps: a CSS-selector style chain of ancestors plus the
// element's opening tag and visible text. Unlike an indexed XPath, this maps
// onto what the page's SOURCE looks like — ids, classes and text are what an
// agent can grep for.
const MAX_CLASSES = 4
const MAX_TEXT = 100
const MAX_TAG = 300

/**
 * @param {object} opts
 * @param {Window} opts.win  Window owning the page to pick in.
 * @param {Document} opts.doc  Its document.
 * @param {(state: object) => void} opts.onState  Fired on every selection
 *        change with { hasSelection, locked, canParent, canFirstChild,
 *        canPrevSibling, canNextSibling } — drives the host toolbar's buttons.
 */
export function createElementPicker({ win, doc, onState }) {
    // A full-viewport overlay swallows every pointer interaction — the page
    // must not react at all while the mode is on — and the element under the
    // pointer gets a dashed outline. Two states: hovering (red outline
    // follows the pointer) and locked (a click/tap turned it green; hover is
    // ignored so the user can travel to the host's toolbar without losing the
    // selection — toolbar navigation and clear both keep/lift the lock). The
    // outline box is a separate pointer-events:none node, so hit-testing
    // skips it; elementsFromPoint sees the page through the overlay by
    // filtering our own two nodes out.
    let overlay = null
    let outline = null
    let lastPoint = null
    let current = null
    let locked = false

    function isOwnNode(node) {
        return node === overlay || node === outline
    }

    // One DOM step from `el`, skipping our own overlay/outline nodes (both
    // are children of <body>, so a sibling walk there would land on them).
    function stepFrom(el, direction) {
        if (direction === 'parent') return el.parentElement
        let node =
            direction === 'first-child'
                ? el.firstElementChild
                : direction === 'prev-sibling'
                  ? el.previousElementSibling
                  : direction === 'next-sibling'
                    ? el.nextElementSibling
                    : null
        while (node && isOwnNode(node)) {
            node = direction === 'prev-sibling' ? node.previousElementSibling : node.nextElementSibling
        }
        return node
    }

    // Where the host's toolbar buttons can go from the current element —
    // reported on every element change so the buttons enable/disable live.
    function emitState() {
        const el = current
        onState({
            hasSelection: !!el,
            locked,
            canParent: !!(el && stepFrom(el, 'parent')),
            canFirstChild: !!(el && stepFrom(el, 'first-child')),
            canPrevSibling: !!(el && stepFrom(el, 'prev-sibling')),
            canNextSibling: !!(el && stepFrom(el, 'next-sibling')),
        })
    }

    function drawOutline() {
        if (!outline) return
        if (current && !current.isConnected) {
            // The page re-rendered under us (SPA) — the selection is gone.
            setCurrent(null, false)
            return
        }
        if (!current) {
            outline.style.display = 'none'
            return
        }
        const rect = current.getBoundingClientRect()
        outline.style.outlineColor = locked ? '#30a46c' : '#e5484d'
        outline.style.display = 'block'
        outline.style.left = `${rect.left}px`
        outline.style.top = `${rect.top}px`
        outline.style.width = `${rect.width}px`
        outline.style.height = `${rect.height}px`
    }

    function setCurrent(el, isLocked) {
        const changed = el !== current || isLocked !== locked
        current = el
        locked = isLocked
        drawOutline()
        if (changed) emitState()
    }

    function nav(direction) {
        if (!overlay || !current) return
        const next = stepFrom(current, direction)
        if (!next) return
        // An explicit choice locks the selection (green), whatever it started
        // from, and drops the pointer anchor (the scrollIntoView below would
        // otherwise immediately re-aim at the stale pointer position).
        lastPoint = null
        setCurrent(next, true)
        next.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }

    function clear() {
        lastPoint = null
        setCurrent(null, false)
    }

    function highlightAt(x, y) {
        lastPoint = { x, y }
        const el = doc.elementsFromPoint(x, y).find((node) => !isOwnNode(node))
        setCurrent(el || null, false)
    }

    // One chain segment: tag#id.classes, with :nth-of-type only when the
    // segment alone would be ambiguous among same-tag siblings.
    function segmentFor(node) {
        const signature = (el) => `${el.id}|${[...el.classList].slice(0, MAX_CLASSES).join('.')}`
        let segment = node.localName
        if (node.id) segment += `#${node.id}`
        const classes = [...node.classList].slice(0, MAX_CLASSES)
        if (classes.length) segment += `.${classes.join('.')}`
        const sameTag = node.parentElement
            ? [...node.parentElement.children].filter(
                  (sib) => !isOwnNode(sib) && sib.localName === node.localName
              )
            : []
        if (sameTag.length > 1 && sameTag.filter((sib) => signature(sib) === signature(node)).length > 1) {
            segment += `:nth-of-type(${sameTag.indexOf(node) + 1})`
        }
        return segment
    }

    function chainFor(el) {
        const parts = []
        for (let node = el; node && node !== doc.documentElement; node = node.parentElement) {
            parts.unshift(segmentFor(node))
        }
        return parts.length ? parts.join(' > ') : el.localName
    }

    // The element's own opening tag — it carries every attribute (id,
    // classes, data-*, aria-*) in the exact shape the source declares them.
    // Vue's scoped-style data-v-* markers are compile-time noise and dropped.
    function openingTagFor(el) {
        const clone = el.cloneNode(false)
        for (const name of clone.getAttributeNames()) {
            if (name.startsWith('data-v-')) clone.removeAttribute(name)
        }
        const html = clone.outerHTML
        const end = html.indexOf('>')
        const tag = end === -1 ? html : html.slice(0, end + 1)
        return tag.length > MAX_TAG ? `${tag.slice(0, MAX_TAG)}…>` : tag
    }

    function textFor(el) {
        const text = (el.textContent || '').replace(/\s+/g, ' ').trim()
        return text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}…` : text
    }

    function describe() {
        if (!current) return null
        return { chain: chainFor(current), openingTag: openingTagFor(current), text: textFor(current) }
    }

    // Render the current element to a PNG data URL with modern-screenshot (a
    // live-DOM-to-image renderer, foreignObject-based). Fidelity is
    // best-effort by nature — webfont glyphs and cross-origin images may not
    // survive the round-trip.
    async function capture() {
        if (!current) throw new Error('no element selected')
        return await domToPng(current)
    }

    function onPointerMove(event) {
        // Hover only drives the outline while nothing is locked.
        if (locked) return
        highlightAt(event.clientX, event.clientY)
    }

    function onPointerDown(event) {
        // A click/tap locks the element under the point (touch has no hover —
        // the tap IS the pointing gesture). preventDefault also keeps it from
        // focusing/activating anything underneath.
        event.preventDefault()
        const el = doc.elementsFromPoint(event.clientX, event.clientY).find((node) => !isOwnNode(node))
        if (!el) return
        lastPoint = null
        setCurrent(el, true)
    }

    function blockEvent(event) {
        event.preventDefault()
    }

    function onScroll() {
        // The page still scrolls under the overlay (wheel/touch chaining). A
        // locked selection sticks to its element (redraw its rect); a hover
        // one re-aims at the last pointer position so the outline tracks the
        // element now under it.
        if (!locked && lastPoint) highlightAt(lastPoint.x, lastPoint.y)
        else drawOutline()
    }

    function enable() {
        if (overlay) return
        overlay = doc.createElement('div')
        overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483646;cursor:crosshair;background:transparent;'
        overlay.addEventListener('pointermove', onPointerMove)
        overlay.addEventListener('pointerdown', onPointerDown)
        overlay.addEventListener('click', blockEvent)
        overlay.addEventListener('contextmenu', blockEvent)
        outline = doc.createElement('div')
        outline.style.cssText =
            'position:fixed;z-index:2147483647;pointer-events:none;display:none;' +
            'outline:2px dashed #e5484d;outline-offset:-2px;'
        win.addEventListener('scroll', onScroll, true)
        const root = doc.body || doc.documentElement
        root.appendChild(overlay)
        root.appendChild(outline)
    }

    function disable() {
        if (!overlay) return
        win.removeEventListener('scroll', onScroll, true)
        overlay.remove()
        outline.remove()
        overlay = null
        outline = null
        lastPoint = null
        current = null
        locked = false
    }

    // Teardown that must never throw — the document may already be dead
    // (iframe reloaded out from under the SPA caller).
    function destroy() {
        try {
            disable()
        } catch {
            overlay = null
            outline = null
            lastPoint = null
            current = null
            locked = false
        }
    }

    return { enable, disable, nav, clear, describe, capture, destroy }
}
```

### 1b. EDIT `frontend/src/browser-companion/companion.js`

**Imports** — replace:

```js
import { domToPng } from 'modern-screenshot'

import { companionMessage, isHostMessage } from './protocol'
```

with:

```js
import { createElementPicker } from '../element-select/picker'
import { companionMessage, isHostMessage } from './protocol'
```

**Command dispatch** — in the `window.addEventListener('message', …)` handler,
replace the five select branches:

```js
        } else if (message.action === 'select-mode') {
            setSelectMode(message.enabled === true)
        } else if (message.action === 'select-nav') {
            selectNav(message.direction)
        } else if (message.action === 'select-clear') {
            selectClear()
        } else if (message.action === 'select-describe') {
            postSelectDescribe()
        } else if (message.action === 'select-capture') {
            postSelectCapture()
        }
```

with:

```js
        } else if (message.action === 'select-mode') {
            if (message.enabled === true) picker.enable()
            else picker.disable()
        } else if (message.action === 'select-nav') {
            picker.nav(message.direction)
        } else if (message.action === 'select-clear') {
            picker.clear()
        } else if (message.action === 'select-describe') {
            postSelectDescribe()
        } else if (message.action === 'select-capture') {
            postSelectCapture()
        }
```

**Select machinery** — delete the whole block from the comment
`// ── Select-area mode: a host-toggled element picker. …` down to and including the
`setSelectMode` function (everything between the message listener and the
`// SPA URL changes.` comment — i.e. the old lines ~97–343: `selectOverlay`…
`setSelectMode`), and replace it with:

```js
    // ── Select-area mode: the host-toggled element picker, extracted to the
    // shared element-select/picker.js (the SPA runs the same picker directly
    // against the artifact HTML preview). Here it runs inside the user's own
    // page; this section is the postMessage adapter around it.
    const picker = createElementPicker({
        win: window,
        doc: document,
        onState: (state) => {
            if (hostOrigin) post(companionMessage('select-state', state), hostOrigin)
        },
    })

    function postSelectDescribe() {
        if (!hostOrigin) return
        const description = picker.describe()
        if (!description) return
        post(companionMessage('select-describe', description), hostOrigin)
    }

    // Always answers (success or error), so the host's pending capture state
    // can't get stuck.
    async function postSelectCapture() {
        if (!hostOrigin) return
        try {
            const dataUrl = await picker.capture()
            post(companionMessage('select-capture', { dataUrl }), hostOrigin)
        } catch (error) {
            post(companionMessage('select-capture', { error: String(error?.message || error) }), hostOrigin)
        }
    }
```

**Known micro-delta (accepted):** a `select-capture` command with no current element
used to stay silent (host timed out after 15 s); it now answers with an error — the
host code already handles the error answer. Everything else is byte-for-byte the same
logic.

### 1c. Build + verify

- `cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab/frontend && npm run build`
  (the companion bundle is NOT HMR'd).
- Manual: Browser pane on a companion-equipped page — toggle select mode, hover, lock,
  the four nav buttons, clear, comment, "Include screenshot". All identical to before.
- **R1 spike** (blocking for task 5, not for 2–4): from the SPA console, on an open
  artifact HTML preview, run
  `const el = document.querySelector('.frame-iframe').contentDocument.body;`
  `(await import('/src/element-select/picker.js')).createElementPicker` — or simply
  `import('modern-screenshot').then(m => m.domToPng(el)).then(u => console.log(u.slice(0, 50)))`
  and confirm a `data:image/png` URL comes back for a child-document element. If it
  fails, STOP task 5 and report (fallback = capture-only injected script, to be
  re-planned).

---

## Task 2 — Extract `SelectAreaToolbar.vue`; refactor BrowserPane onto it

### 2a. NEW `frontend/src/components/frames/SelectAreaToolbar.vue`

```vue
<script setup>
// Sub-toolbar of the element-picking mode (select area), shared by the
// Browser pane (companion-driven over postMessage) and the artifact HTML
// preview (direct picker). Purely presentational: the owner supplies the
// picker's state report and executes the emitted actions.
import { useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'

defineProps({
    // The picker's select-state report ({ hasSelection, locked, canParent,
    // canFirstChild, canPrevSibling, canNextSibling }); null until an element
    // has been highlighted.
    state: { type: Object, default: null },
})
defineEmits(['nav', 'clear', 'comment', 'close'])

const instanceId = useId()
</script>

<template>
    <div class="select-toolbar">
        <span class="subbar-label">Select area</span>
        <wa-button :id="`select-clear-${instanceId}`" appearance="plain" size="small" class="subbar-btn" :disabled="!state?.locked" @click="$emit('clear')">
            <wa-icon name="ban"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-clear-${instanceId}`">Clear the selection</AppTooltip>
        <wa-button :id="`select-parent-${instanceId}`" appearance="plain" size="small" class="subbar-btn" :disabled="!state?.canParent" @click="$emit('nav', 'parent')">
            <wa-icon name="arrow-up"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-parent-${instanceId}`">Select the parent</AppTooltip>
        <wa-button :id="`select-child-${instanceId}`" appearance="plain" size="small" class="subbar-btn" :disabled="!state?.canFirstChild" @click="$emit('nav', 'first-child')">
            <wa-icon name="arrow-down"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-child-${instanceId}`">Select the first child</AppTooltip>
        <wa-button :id="`select-prev-${instanceId}`" appearance="plain" size="small" class="subbar-btn" :disabled="!state?.canPrevSibling" @click="$emit('nav', 'prev-sibling')">
            <wa-icon name="arrow-left"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-prev-${instanceId}`">Select the previous sibling</AppTooltip>
        <wa-button :id="`select-next-${instanceId}`" appearance="plain" size="small" class="subbar-btn" :disabled="!state?.canNextSibling" @click="$emit('nav', 'next-sibling')">
            <wa-icon name="arrow-right"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-next-${instanceId}`">Select the next sibling</AppTooltip>
        <wa-button :id="`select-comment-${instanceId}`" appearance="plain" size="small" class="subbar-btn" :disabled="!state?.hasSelection" @click="$emit('comment')">
            <wa-icon name="comment" variant="regular"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-comment-${instanceId}`">Comment on the selection</AppTooltip>
        <wa-button :id="`select-close-${instanceId}`" appearance="plain" size="small" class="subbar-btn subbar-close" @click="$emit('close')">
            <wa-icon name="xmark"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-close-${instanceId}`">Exit select mode</AppTooltip>
    </div>
</template>

<style scoped>
/* Same look as the responsive-viewport sub-toolbar (ViewportToolbar.vue) —
   scoped twins, kept in sync by hand. */
.select-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

.subbar-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    padding-inline: var(--wa-space-2xs);
}

.subbar-btn {
    flex-shrink: 0;
}

/* Exit button pinned to the far right, away from the mode's controls. */
.subbar-close {
    margin-left: auto;
}
</style>
```

### 2b. EDIT `frontend/src/components/browser/BrowserPane.vue`

**Import** — after `import PersistentFrame from '../frames/PersistentFrame.vue'`, add:

```js
import SelectAreaToolbar from '../frames/SelectAreaToolbar.vue'
```

**Template** — replace the whole `<div v-if="selectAreaActive" ref="selectToolbarRef"
class="select-toolbar">` element — everything from its opening tag to its closing
`</div>`, the final `Exit select mode` tooltip being its last child *inside* it; the
preceding HTML comment stays — with:

```html
        <SelectAreaToolbar
            v-if="selectAreaActive"
            ref="selectToolbarRef"
            :state="selectState"
            @nav="selectNav"
            @clear="selectClear"
            @comment="selectComment"
            @close="setSelectArea(false)"
        />
```

**Script** — in `openSelectComment`, `selectToolbarRef` now holds a component instance:

```js
    const rect = selectToolbarRef.value?.getBoundingClientRect()
```
→
```js
    const rect = selectToolbarRef.value?.$el?.getBoundingClientRect()
```

**CSS** — the shared sub-toolbar rule loses its select half (the responsive toolbar
still needs it until task 3):

```css
/* Mode sub-toolbars (responsive viewport, select area) share one look. */
.select-toolbar,
.responsive-toolbar {
```
→
```css
/* Responsive-viewport sub-toolbar (the select one moved into
   SelectAreaToolbar.vue; extracted entirely in the ViewportToolbar step). */
.responsive-toolbar {
```

`.subbar-label` / `.subbar-close` stay for now (used by the responsive toolbar).

### 2c. Verify

Browser pane select mode: toolbar renders identically, nav buttons enable/disable with
the selection, comment widget opens anchored under the toolbar, close works.

---

## Task 3 — Extract `viewport.js` + `ViewportToolbar.vue` + `ViewportStage.vue`; refactor BrowserPane

### 3a. NEW `frontend/src/components/frames/viewport.js`

```js
// Shared constants/helpers of the responsive-viewport mode (ViewportStage +
// ViewportToolbar), extracted from BrowserPane so the artifact HTML preview
// reuses the exact same presets and bounds.

export const VIEWPORT_PRESETS = [
    { label: 'iPhone SE', width: 375, height: 667 },
    { label: 'iPhone 15', width: 393, height: 852 },
    { label: 'iPhone 15 Pro Max', width: 430, height: 932 },
    { label: 'Pixel 8', width: 412, height: 915 },
    { label: 'Galaxy S24', width: 360, height: 780 },
    { label: 'iPad Mini', width: 768, height: 1024 },
    { label: 'iPad Pro 11"', width: 834, height: 1194 },
    { label: 'iPad Pro 12.9"', width: 1024, height: 1366 },
    { label: 'Laptop', width: 1280, height: 800 },
    { label: 'Laptop L', width: 1440, height: 900 },
    { label: 'Desktop', width: 1920, height: 1080 },
]

export const VIEWPORT_MIN = 100
export const VIEWPORT_MAX = 8000

export function clampViewportSize(value) {
    return Math.min(VIEWPORT_MAX, Math.max(VIEWPORT_MIN, Math.round(value)))
}
```

### 3b. NEW `frontend/src/components/frames/ViewportToolbar.vue`

```vue
<script setup>
// Sub-toolbar of the responsive-viewport mode, shared by the Browser pane and
// the artifact HTML preview. Preset list, manual dimension fields and the
// swap button all drive the same width/height pair the stage's drag handles
// update — everything stays in sync whichever way the size changes.
import { computed, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { VIEWPORT_MAX, VIEWPORT_MIN, VIEWPORT_PRESETS, clampViewportSize } from './viewport'

const props = defineProps({
    width: { type: Number, required: true },
    height: { type: Number, required: true },
})
const emit = defineEmits(['update:width', 'update:height', 'close'])

const instanceId = useId()

// The preset matching the current dimensions in either orientation (a rotated
// device is still that device); '' → the select shows its "Custom" placeholder.
const presetValue = computed(() => {
    const match = VIEWPORT_PRESETS.find(
        (p) =>
            (p.width === props.width && p.height === props.height) ||
            (p.width === props.height && p.height === props.width)
    )
    return match ? `${match.width}x${match.height}` : ''
})

function onPresetChange(event) {
    const match = VIEWPORT_PRESETS.find((p) => `${p.width}x${p.height}` === event.target.value)
    if (!match) return
    emit('update:width', match.width)
    emit('update:height', match.height)
}

function onSizeChange(axis, event) {
    const current = axis === 'w' ? props.width : props.height
    const parsed = Number.parseInt(event.target.value, 10)
    const applied = Number.isFinite(parsed) ? clampViewportSize(parsed) : current
    if (applied !== current) emit(axis === 'w' ? 'update:width' : 'update:height', applied)
    // Reflect the applied value back into the field (covers clamping and
    // garbage input, which leave the binding unchanged).
    event.target.value = String(applied)
}

function swap() {
    emit('update:width', props.height)
    emit('update:height', props.width)
}
</script>

<template>
    <div class="viewport-toolbar">
        <span class="subbar-label">Viewport</span>
        <wa-select
            class="viewport-preset"
            size="small"
            placeholder="Custom size"
            :value="presetValue"
            @change="onPresetChange"
        >
            <wa-option
                v-for="preset in VIEWPORT_PRESETS"
                :key="preset.label"
                :value="`${preset.width}x${preset.height}`"
            >{{ preset.label }} — {{ preset.width }}×{{ preset.height }}</wa-option>
        </wa-select>
        <div class="viewport-dimensions">
            <wa-input
                class="viewport-size-input"
                size="small"
                type="number"
                :min="VIEWPORT_MIN"
                :max="VIEWPORT_MAX"
                :value="String(width)"
                @change="onSizeChange('w', $event)"
            ></wa-input>
            <span class="viewport-glue">×</span>
            <wa-input
                class="viewport-size-input"
                size="small"
                type="number"
                :min="VIEWPORT_MIN"
                :max="VIEWPORT_MAX"
                :value="String(height)"
                @change="onSizeChange('h', $event)"
            ></wa-input>
            <span class="viewport-glue">px</span>
        </div>
        <wa-button :id="`viewport-swap-${instanceId}`" appearance="plain" size="small" class="subbar-btn" @click="swap">
            <wa-icon name="right-left"></wa-icon>
        </wa-button>
        <AppTooltip :for="`viewport-swap-${instanceId}`">Swap width and height</AppTooltip>
        <wa-button :id="`viewport-close-${instanceId}`" appearance="plain" size="small" class="subbar-btn subbar-close" @click="$emit('close')">
            <wa-icon name="xmark"></wa-icon>
        </wa-button>
        <AppTooltip :for="`viewport-close-${instanceId}`">Exit responsive mode</AppTooltip>
    </div>
</template>

<style scoped>
/* Same look as the select-area sub-toolbar (SelectAreaToolbar.vue) — scoped
   twins, kept in sync by hand. */
.viewport-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

.subbar-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    padding-inline: var(--wa-space-2xs);
}

.subbar-btn {
    flex-shrink: 0;
}

/* Exit button pinned to the far right, away from the mode's controls. */
.subbar-close {
    margin-left: auto;
}

.viewport-preset {
    width: 14rem;
    max-width: 100%;
}

.viewport-dimensions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-3xs);
}

.viewport-size-input {
    width: 5.25rem;
}

/* The "×" between the fields and the trailing "px" unit. */
.viewport-glue {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}
</style>
```

### 3c. NEW `frontend/src/components/frames/ViewportStage.vue`

```vue
<script setup>
// Scrollable body + hatched canvas + fixed-size stage of the responsive-
// viewport mode, shared by the Browser pane and the artifact HTML preview.
// The default slot carries the pane's PersistentFrame; the stage box is the
// frame placeholder's sizing parent. In normal mode every wrapper is a plain
// flex pass-through; in responsive mode the stage takes the exact CSS-pixel
// width/height and the body scrolls around it. The wrappers render in BOTH
// modes (restyled only) so the slotted frame never unmounts on a toggle — a
// remount would re-register the pooled iframe and reload it.
//
// Owners bind v-model:width/v-model:height and read `bodyEl` (exposed) as the
// frame's clip container (PersistentFrame's clip-el prop).
import { computed, onBeforeUnmount, ref } from 'vue'
import { useFramePoolStore } from '../../stores/framePool'
import { clampViewportSize } from './viewport'

// Resize handles around the stage — one per side and corner. dirX/dirY say
// which way each handle pushes a dimension: e.g. the west handle (dirX -1)
// grows the width when dragged left. 0 = that axis is untouched.
const VIEWPORT_HANDLES = [
    { key: 'n', dirX: 0, dirY: -1 },
    { key: 's', dirX: 0, dirY: 1 },
    { key: 'e', dirX: 1, dirY: 0 },
    { key: 'w', dirX: -1, dirY: 0 },
    { key: 'ne', dirX: 1, dirY: -1 },
    { key: 'nw', dirX: -1, dirY: -1 },
    { key: 'se', dirX: 1, dirY: 1 },
    { key: 'sw', dirX: -1, dirY: 1 },
]

const props = defineProps({
    // Responsive mode on/off (off = transparent flex pass-through).
    active: { type: Boolean, default: false },
    width: { type: Number, required: true },
    height: { type: Number, required: true },
    // Whether the canvas/stage (default slot) renders at all; when false the
    // `empty` slot renders instead (e.g. the Browser pane's no-URL state).
    showStage: { type: Boolean, default: true },
})
const emit = defineEmits(['update:width', 'update:height'])

const framePool = useFramePoolStore()
const bodyEl = ref(null)

const stageStyle = computed(() =>
    props.active ? { width: `${props.width}px`, height: `${props.height}px` } : null
)

// Drag-resize via the handles in the hatched gutter. Pointer capture keeps
// the events flowing to the handle; beginDividerDrag() additionally turns off
// pointer-events on ALL pooled iframes (same need as split dividers: an
// iframe is a separate browsing context that swallows move events the moment
// the pointer crosses it). Incremental (per-move) deltas, so the doubling
// below can flip mid-drag as an axis crosses the fit/overflow boundary. The
// drag tracks its own width/height copy — reading props back would race the
// parent's async re-render between two pointermove events.
let drag = null // { dirX, dirY, lastX, lastY, width, height }
const dragKey = ref(null) // template mirror — keeps the handle lit

function startResize(handle, event) {
    if (drag) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    drag = {
        dirX: handle.dirX,
        dirY: handle.dirY,
        lastX: event.clientX,
        lastY: event.clientY,
        width: props.width,
        height: props.height,
    }
    dragKey.value = handle.key
    framePool.beginDividerDrag()
}

function onResizeMove(event) {
    if (!drag) return
    const body = bodyEl.value
    const dx = event.clientX - drag.lastX
    const dy = event.clientY - drag.lastY
    drag.lastX = event.clientX
    drag.lastY = event.clientY
    // While an axis has no overflow the stage is centered on it (margin:auto),
    // so its dragged edge moves only half as fast as the box grows — double
    // the delta to keep the handle under the pointer. Once the axis overflows
    // the stage anchors to the start and it's back to 1:1. `+ 1` absorbs
    // sub-pixel rounding on the scroll/client comparison. dirX/dirY carry the
    // handle's sign, so a west/north handle grows the box when dragged out.
    if (drag.dirX) {
        const factor = body && body.scrollWidth <= body.clientWidth + 1 ? 2 : 1
        drag.width = clampViewportSize(drag.width + drag.dirX * dx * factor)
        emit('update:width', drag.width)
    }
    if (drag.dirY) {
        const factor = body && body.scrollHeight <= body.clientHeight + 1 ? 2 : 1
        drag.height = clampViewportSize(drag.height + drag.dirY * dy * factor)
        emit('update:height', drag.height)
    }
}

function endResize() {
    if (!drag) return
    drag = null
    dragKey.value = null
    framePool.endDividerDrag()
}

// Balance the pool's divider-drag depth if unmounted mid-drag.
onBeforeUnmount(endResize)

defineExpose({ bodyEl })
</script>

<template>
    <div ref="bodyEl" class="viewport-body" :class="{ 'viewport-body--responsive': active }">
        <div v-if="showStage" class="viewport-canvas">
            <div class="viewport-stage" :style="stageStyle">
                <slot />
                <!-- Resize handles live in the hatched gutter just OUTSIDE the
                     stage box, so the pooled iframe (which overlays exactly
                     the stage rect) never covers them. -->
                <template v-if="active">
                    <div
                        v-for="handle in VIEWPORT_HANDLES"
                        :key="handle.key"
                        class="viewport-handle"
                        :class="[
                            `viewport-handle--${handle.key}`,
                            { 'viewport-handle--dragging': dragKey === handle.key },
                        ]"
                        @pointerdown="startResize(handle, $event)"
                        @pointermove="onResizeMove"
                        @pointerup="endResize"
                        @pointercancel="endResize"
                    ></div>
                </template>
            </div>
        </div>
        <slot v-else name="empty" />
    </div>
</template>

<style scoped>
.viewport-body {
    flex: 1;
    min-height: 0;
    display: flex;
}

/* Responsive mode: the body turns into a scroll container around the
   fixed-size stage. */
.viewport-body--responsive {
    display: block;
    overflow: auto;
}

/* Pass-through wrapper in normal mode; in responsive mode it carries the
   hatched "neutral zone" background and the gutter around the stage. */
.viewport-canvas {
    flex: 1;
    display: flex;
    min-width: 0;
    min-height: 0;
}

/* width/height: max-content on purpose: unlike the scroll container's own
   padding, a child's box is part of the scrollable content on EVERY side, so
   the gutter (and the resize handles living in it) stays reachable even when
   the stage overflows the pane. min-*: 100% makes the canvas at least fill the
   pane, giving margin:auto (below) room to center the stage. The +12px in the
   padding hosts the resize handles, which sit just outside the stage box. */
.viewport-body--responsive .viewport-canvas {
    display: flex;
    width: max-content;
    height: max-content;
    min-width: 100%;
    min-height: 100%;
    padding: calc((var(--wa-space-m) + 12px) * 2);
    background-color: var(--wa-color-surface-lowered);
    background-image: repeating-linear-gradient(
        45deg,
        transparent 0,
        transparent 6px,
        color-mix(in srgb, var(--wa-color-neutral-fill-loud) 12%, transparent) 6px,
        color-mix(in srgb, var(--wa-color-neutral-fill-loud) 12%, transparent) 7px
    );
}

/* Normal mode: the stage just relays the flex sizing down to the frame. */
.viewport-stage {
    flex: 1;
    display: flex;
    min-width: 0;
    min-height: 0;
}

/* Responsive mode: the stage IS the device viewport — an exact-CSS-pixel box
   (inline style) the placeholder fills. margin:auto centers it both axes when
   it fits; per the flexbox spec, auto margins resolve to 0 on overflow, so it
   then anchors to the start (top-left) and the whole thing stays scroll-
   reachable — NOT the unreachable-start trap of justify/align: center. */
.viewport-body--responsive .viewport-stage {
    position: relative;
    flex: none;
    display: block;
    margin: auto;
}

/* Drag handles in the gutter just outside the stage box (see template note).
   Sides span the corresponding edge; corners fill the 12px squares the sides
   leave uncovered (sides run 0→edge, corners sit at -12px), so the perimeter
   is seamless with no overlap. */
.viewport-handle {
    position: absolute;
    display: flex;
    align-items: center;
    justify-content: center;
    touch-action: none;
}

.viewport-handle::after {
    content: '';
    border-radius: 999px;
    background: var(--wa-color-neutral-fill-loud);
    opacity: 0.5;
    transition: opacity 0.15s ease;
}

.viewport-handle:hover::after,
.viewport-handle--dragging::after {
    opacity: 1;
}

/* Vertical sides (west/east): full-height strips, a vertical grip bar. */
.viewport-handle--e,
.viewport-handle--w {
    top: 0;
    bottom: 0;
    width: 12px;
    cursor: ew-resize;
}

.viewport-handle--e {
    right: -12px;
}

.viewport-handle--w {
    left: -12px;
}

.viewport-handle--e::after,
.viewport-handle--w::after {
    width: 4px;
    height: 2.5rem;
    max-height: 60%;
}

/* Horizontal sides (north/south): full-width strips, a horizontal grip bar. */
.viewport-handle--n,
.viewport-handle--s {
    left: 0;
    right: 0;
    height: 12px;
    cursor: ns-resize;
}

.viewport-handle--s {
    bottom: -12px;
}

.viewport-handle--n {
    top: -12px;
}

.viewport-handle--n::after,
.viewport-handle--s::after {
    height: 4px;
    width: 2.5rem;
    max-width: 60%;
}

/* Corners: 12px squares with a small square grip; cursor matches the diagonal
   (nwse for the ↖↘ pair, nesw for the ↗↙ pair). */
.viewport-handle--ne,
.viewport-handle--nw,
.viewport-handle--se,
.viewport-handle--sw {
    width: 12px;
    height: 12px;
}

.viewport-handle--ne::after,
.viewport-handle--nw::after,
.viewport-handle--se::after,
.viewport-handle--sw::after {
    width: 8px;
    height: 8px;
    border-radius: var(--wa-border-radius-s);
}

.viewport-handle--nw {
    top: -12px;
    left: -12px;
    cursor: nwse-resize;
}

.viewport-handle--se {
    bottom: -12px;
    right: -12px;
    cursor: nwse-resize;
}

.viewport-handle--ne {
    top: -12px;
    right: -12px;
    cursor: nesw-resize;
}

.viewport-handle--sw {
    bottom: -12px;
    left: -12px;
    cursor: nesw-resize;
}
</style>
```

### 3d. EDIT `frontend/src/components/browser/BrowserPane.vue`

**Imports** — remove `import { useFramePoolStore } from '../../stores/framePool'`;
next to the `SelectAreaToolbar` import add:

```js
import ViewportStage from '../frames/ViewportStage.vue'
import ViewportToolbar from '../frames/ViewportToolbar.vue'
```

**Script** — remove `const framePool = useFramePoolStore()` and
`const browserBodyRef = ref(null)` (and its comment if any). Replace the whole
responsive block — from the `// ── Responsive mode: …` comment through
`endViewportResize` (i.e. `VIEWPORT_PRESETS`, `VIEWPORT_MIN/MAX`, `VIEWPORT_HANDLES`,
`responsiveActive`, `viewportWidth/Height`, `stageStyle`, `viewportPresetValue`,
`clampViewportSize`, `onViewportPresetChange`, `onViewportSizeChange`, `swapViewport`,
`viewportDrag`, `viewportDragKey`, `startViewportResize`, `onViewportResizeMove`,
`endViewportResize`) — with:

```js
// ── Responsive mode: the stage (the iframe's placeholder box) takes an exact
// CSS-pixel viewport size instead of filling the pane. Pure host-side feature
// — no companion involved: the page reacts to its iframe size exactly as to a
// small window. All the machinery (toolbar, hatched canvas, drag handles)
// lives in the shared frames/ViewportStage + ViewportToolbar; only the mode
// flag and the dimensions are the pane's.
const responsiveActive = ref(false)
const viewportWidth = ref(375)
const viewportHeight = ref(667)
const viewportStageRef = ref(null)
```

**`onBeforeUnmount`** — remove the line
`endViewportResize() // balance the pool's divider-drag depth if mid-drag`
(ViewportStage balances it itself).

**Template** — replace the responsive toolbar block (`<div v-if="responsiveActive"
class="responsive-toolbar">` … its closing tooltip; keep the preceding HTML comment)
with:

```html
        <ViewportToolbar
            v-if="responsiveActive"
            v-model:width="viewportWidth"
            v-model:height="viewportHeight"
            @close="responsiveActive = false"
        />
```

Replace the body block — starting at the existing HTML comment
`<!-- The canvas/stage wrappers exist in BOTH modes (only restyled) … -->` (include
it: the replacement below carries its own version of that comment) and the
`<div ref="browserBodyRef" class="browser-body" …>` it introduces, down to that div's
matching closing `</div>` (the one right before `</div></template>`), i.e. the whole
canvas/stage/handles/empty structure — with:

```html
        <!-- The stage wrappers render in BOTH modes (only restyled) so the
             PersistentFrame component never unmounts on a mode toggle — a
             remount would re-register the pooled iframe and reload it. -->
        <ViewportStage
            ref="viewportStageRef"
            :active="responsiveActive"
            v-model:width="viewportWidth"
            v-model:height="viewportHeight"
            :show-stage="!!(everActivated && currentUrl && !mixedContentBlocked)"
        >
            <PersistentFrame
                ref="persistentFrameRef"
                :frame-id="`browser:${instanceId}`"
                :src="frameSrc"
                :remount-key="frameKey"
                :attrs="BROWSER_FRAME_ATTRS"
                :elevated="props.frameElevated"
                :fullscreen="isFullscreen"
                :clip-el="viewportStageRef?.bodyEl ?? null"
                class="browser-frame"
                @load="onFrameLoad"
            />
            <template #empty>
                <div v-if="!currentUrl" class="browser-empty">
                    <wa-icon name="globe" class="browser-empty-icon"></wa-icon>
                    <p>Enter a URL above to preview your project — e.g. your dev server.</p>
                    <p class="browser-empty-hint">
                        Use the <wa-icon name="bookmark"></wa-icon> menu to save it as the
                        default for this project or one of its workspaces.
                    </p>
                </div>
            </template>
        </ViewportStage>
```

(Keep the PersistentFrame attributes exactly as they are in the current file — only
`:clip-el` changes, from `browserBodyRef` to `viewportStageRef?.bodyEl ?? null`.)

**CSS** — delete these now-extracted rules: the `.responsive-toolbar` block,
`.subbar-label`, `.subbar-close`, `.viewport-preset`, `.viewport-dimensions`,
`.viewport-size-input`, `.viewport-glue`, `.browser-body`,
`.browser-body--responsive`, `.browser-canvas`,
`.browser-body--responsive .browser-canvas`, `.browser-stage`,
`.browser-body--responsive .browser-stage`, and every `.viewport-handle*` rule.
Keep `.browser-frame` (slot content, parent-scoped) and the `.browser-empty*` rules
(slot content too — slotted nodes keep the parent's scope). Keep the
`.select-toggle.active wa-icon, .responsive-toggle.active wa-icon` rule (toolbar
toggles are still the pane's).

### 3e. Verify (behavior-neutral)

Browser pane responsive mode: toggle, presets, manual W×H (clamping, garbage input),
swap, all 8 handles (centered 2× tracking, overflow 1:1), hatched gutter + doubled
padding, empty state (no URL), select-toolbar + responsive-toolbar stacking order,
fullscreen, and the select-toggle handle-offset fix (toggling select mode in
responsive mode must not shift the handles).

---

## Task 4 — FilePane: responsive mode

All edits in `frontend/src/components/files/FilePane.vue`.

**Imports** — after `import PersistentFrame from '../frames/PersistentFrame.vue'`:

```js
import ViewportStage from '../frames/ViewportStage.vue'
import ViewportToolbar from '../frames/ViewportToolbar.vue'
```

**Script** — next to the other button ids (after
`const htmlPreviewReloadButtonId = useId()`):

```js
const responsiveButtonId = useId()
```

After the network-broker section (after the `useArtifactBroker(…)` call), add:

```js
// --- Session context (both HTML-preview modes) -------------------------------
// Responsive + select mode only make sense inside a session: the sidebar
// slash-route Artifacts browser (ArtifactsBrowserView) is a sessionless
// cross-session viewer. `insertTextAtCursor` is provided ONLY by SessionView,
// so its presence is the exact "we're in a session tab" signal (true in both
// the Files and Artifacts tabs, false in the slash-route viewer).
const inSessionContext = computed(() => !!insertTextAtCursor)

// --- Responsive viewport (HTML preview) --------------------------------------
// Same feature as the Browser pane's responsive mode, through the shared
// frames/ViewportStage + ViewportToolbar: the preview iframe takes an exact
// CSS-pixel viewport inside a scrollable hatched canvas. Pure host-side — the
// artifact just sees a small iframe. Per-pane refs, no persistence (parity
// with the Browser pane).
const responsiveActive = ref(false)
const viewportWidth = ref(375)
const viewportHeight = ref(667)
const viewportStageRef = ref(null)
```

(`insertTextAtCursor` is already injected at the top of the file, line ~118, and used
by the existing text-selection widget — no new inject.)

**Template** — replace the HTML preview `PersistentFrame` block:

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

with (the surrounding HTML comment about PersistentFrame stays):

```html
                <div v-if="showHtmlPreview && isHtmlFile && htmlPreviewSrc && !diffMode" class="html-preview-area">
                    <ViewportToolbar
                        v-if="responsiveActive"
                        v-model:width="viewportWidth"
                        v-model:height="viewportHeight"
                        @close="responsiveActive = false"
                    />
                    <!-- The stage wrappers render in BOTH modes (only restyled)
                         so PersistentFrame never unmounts on a mode toggle — a
                         remount would re-register the pooled iframe and reload
                         it. bodyEl is the frame's clip container: a scrolled-out
                         stage must not paint over the pane's chrome. -->
                    <ViewportStage
                        ref="viewportStageRef"
                        :active="responsiveActive"
                        v-model:width="viewportWidth"
                        v-model:height="viewportHeight"
                    >
                        <PersistentFrame
                            ref="persistentFrameRef"
                            :frame-id="`artifact-html:${instanceId}`"
                            :src="htmlPreviewSrc"
                            :remount-key="filePath"
                            :attrs="HTML_PREVIEW_FRAME_ATTRS"
                            :elevated="props.frameElevated"
                            :fullscreen="isPreviewFullscreen"
                            :clip-el="viewportStageRef?.bodyEl ?? null"
                            class="html-preview"
                        />
                    </ViewportStage>
                </div>
```

In the `.preview-actions` block, right BEFORE the existing
`<template v-if="showHtmlPreview && isHtmlFile && !diffMode && artifactBookmark">`
open-in-tab block, add:

```html
                    <!-- Responsive-mode toggle (HTML preview, session context
                         only): exact device-size viewport, same feature as the
                         Browser pane. Hidden in the sessionless slash-route
                         Artifacts browser (inSessionContext). -->
                    <template v-if="showHtmlPreview && isHtmlFile && !diffMode && htmlPreviewSrc && inSessionContext">
                        <wa-button
                            :id="responsiveButtonId"
                            class="preview-action-btn"
                            :class="{ 'preview-action-btn--active': responsiveActive }"
                            size="small"
                            variant="neutral"
                            appearance="filled"
                            @click="responsiveActive = !responsiveActive"
                        >
                            <wa-icon name="mobile-screen-button"></wa-icon>
                        </wa-button>
                        <AppTooltip :for="responsiveButtonId">
                            {{ responsiveActive ? 'Exit responsive mode' : 'Responsive mode — preview at a device size' }}
                        </AppTooltip>
                    </template>
```

Known cosmetic edge (accepted, no code): in the non-pooled fallback (no FrameHost —
outside ProjectView), `frameOverlayEl` is null and `.preview-actions` renders at the
top-right of `.file-pane-preview`, where it can overlap the `ViewportToolbar`'s close
button. All three target contexts run inside ProjectView (pooled), so this is
practically unreachable.

**CSS** — replace (including the two-line comment above the rule, which the new text
supersedes):

```css
/* Placeholder sizing only — the pooled iframe (in FrameHost) carries the
   border/background; PersistentFrame positions it over this box. */
.html-preview {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
}
```

with:

```css
/* The preview area stacks the mode sub-toolbars over the viewport stage
   (flex:1 via its own .viewport-body rule). */
.html-preview-area {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
}

/* Placeholder sizing only — the pooled iframe (in FrameHost) carries the
   visuals; PersistentFrame positions it over this box. */
.html-preview {
    flex: 1;
    width: 100%;
    height: 100%;
}
```

and after the `.preview-action-btn:hover, …` rule add:

```css
/* Mode toggles (responsive / select): fully opaque + brand icon while active. */
.preview-action-btn--active {
    opacity: 1;
}
.preview-action-btn--active wa-icon {
    color: var(--wa-color-brand-fill-loud);
}
```

### Verify

- Session Artifacts tab + session Files tab: the toggle shows; the toolbar + hatched
  stage appear; presets/fields/swap/handles work; the iframe content reflows to the
  stage size; scrolled-out stage does not paint over the pane header (clip).
- **Sidebar Artifacts browser (slash route): NO responsive toggle** (sessionless).
- Toggling responsive does NOT reload the artifact (watch a stateful artifact).
- Fullscreen preview + responsive together.
- Agent-edit live reload (Artifacts tab) keeps working in responsive mode.

---

## Task 5 — FilePane: select mode + comment + screenshot

All edits in `frontend/src/components/files/FilePane.vue` (requires the R1 spike from
task 1c to have passed).

**Imports** — add:

```js
import SelectAreaToolbar from '../frames/SelectAreaToolbar.vue'
```

**Script** — after the responsive-viewport block added in task 4, add:

```js
// --- Element select mode (HTML preview) --------------------------------------
// Same feature as the Browser pane's select-area mode, but with NO companion:
// the preview iframe is same-origin (file-raw on TwiCC's own host,
// allow-same-origin sandbox), so the shared picker
// (element-select/picker.js — the very module the companion runs in-page)
// runs from HERE, directly against the iframe's document.
//
// The session whose composer/draft the picked-element comment feeds: the Files
// tab passes it as sessionId, the Artifacts tab as artifactBookmarkSessionId
// (its sessionId is null — SessionView.vue:2336). Both are inside the same
// SessionView, so insertTextAtCursor targets the right composer regardless; the
// id is only needed to attach the screenshot to that session's draft.
const composerSessionId = computed(() => props.sessionId || props.artifactBookmarkSessionId || null)
// Gate: in a session tab (inSessionContext) with a resolvable composer session.
const canSelectElement = computed(() => inSessionContext.value && !!composerSessionId.value)
const selectButtonId = useId()
const selectToolbarRef = ref(null)
const selectModeActive = ref(false)
const selectState = ref(null)
let picker = null

async function createPicker() {
    const frameEl = previewIframeRef.value
    const win = frameEl?.contentWindow
    const doc = frameEl?.contentDocument
    if (!win || !doc) return
    // Lazy import: modern-screenshot stays out of the main bundle until the
    // mode is first used.
    const { createElementPicker } = await import('../../element-select/picker')
    // Re-check after the await: the mode may have been toggled off, the iframe
    // element replaced, or the SAME element reloaded onto a new document (the
    // contentDocument identity check — a reload keeps the element) while the
    // module loaded. Building on the captured-but-dead doc would silently
    // break the mode until the next reload.
    if (
        !selectModeActive.value ||
        picker ||
        previewIframeRef.value !== frameEl ||
        frameEl.contentDocument !== doc
    )
        return
    picker = createElementPicker({
        win,
        doc,
        onState: (state) => {
            selectState.value = state
        },
    })
    picker.enable()
}

function destroyPicker() {
    picker?.destroy()
    picker = null
    selectState.value = null
}

function setSelectMode(enabled) {
    if (selectModeActive.value === enabled) return
    selectModeActive.value = enabled
    // Drop any open element comment so a stale one can't flash back on re-entry.
    elementCommentPosition.value = null
    if (enabled) createPicker()
    else destroyPicker()
}

// Toolbar relays. Named functions (not inline arrows in the template) so the
// non-reactive `picker` closure variable is always read at call time.
function selectNav(direction) {
    picker?.nav(direction)
}

function selectClear() {
    picker?.clear()
}

// Re-arm across reloads. Agent edits bump the cache-bust src, the reload
// button does too, and KeepAlive re-parenting gives the pooled iframe a fresh
// browsing context — in every case the picker's document dies; `load` on the
// live element is the one reliable signal (same pattern as
// useArtifactBroker's rebind). Unlike the Browser pane (which drops the mode
// on navigation — the page may be a different site), an artifact reload is an
// iteration on the same document: keep the mode on, selection cleared.
let pickerIframe = null
function onPickerFrameLoad() {
    destroyPicker()
    if (selectModeActive.value) createPicker()
}
watch(
    previewIframeRef,
    (iframe) => {
        if (iframe === pickerIframe) return
        pickerIframe?.removeEventListener('load', onPickerFrameLoad)
        pickerIframe = iframe ?? null
        pickerIframe?.addEventListener('load', onPickerFrameLoad)
        // New element = new browsing context: rebuild on it too.
        onPickerFrameLoad()
    },
    { flush: 'post' },
)
// A different file is a different context — drop the mode (its `load` would
// otherwise re-arm the picker on an unrelated page).
watch(() => props.filePath, () => setSelectMode(false))
watch(isHtmlPreviewActive, (active) => {
    if (!active) setSelectMode(false)
})

// --- Element comment widget (select mode) ---
// Second TextSelectionComment instance, parallel to the text-selection one
// (they share the screen, not state — opening one closes the other). The
// "quote" is the picked element's description; the screenshot switch renders
// the element itself.
const elementCommentText = ref('')
const elementCommentPosition = ref(null) // viewport anchor, or null = closed
const elementCommentSourceLabel = computed(
    () => `from the HTML preview of ${props.displayPath || props.filePath}`,
)

function openElementComment() {
    const description = picker?.describe()
    const rect = selectToolbarRef.value?.$el?.getBoundingClientRect()
    if (!description || !rect) return
    const lines = []
    if (description.openingTag) lines.push(`Element: ${description.openingTag}`)
    if (description.text) lines.push(`Text: "${description.text}"`)
    lines.push(`Path: ${description.chain}`)
    closeTextSelectionComment()
    elementCommentText.value = lines.join('\n')
    elementCommentPosition.value = { top: rect.bottom, left: rect.left + rect.width / 2, above: false }
}

// Passed to the comment widget's "Include screenshot" switch; rejects cleanly
// so the switch can revert.
function captureElementScreenshot() {
    if (!picker) return Promise.reject(new Error('select mode is off'))
    return picker.capture()
}

// dataUrl → File → draft attachment (the same path a manual image upload
// takes, so provider validation / resize / caps all apply). Uses the resolved
// composer session id (NOT props.sessionId — null in the Artifacts tab).
async function attachElementScreenshot(dataUrl) {
    const sessionId = composerSessionId.value
    if (!sessionId) throw new Error('no session to attach to')
    const blob = await (await fetch(dataUrl)).blob()
    const file = new File([blob], `artifact-capture-${Date.now()}.png`, { type: 'image/png' })
    await dataStore.addAttachment(sessionId, file)
}
```

**Unmount** — in the pane's main `onBeforeUnmount` (the one at the end of the setup,
around line 847), add:

```js
    pickerIframe?.removeEventListener('load', onPickerFrameLoad)
    pickerIframe = null
    destroyPicker()
```

**Template** — in `.html-preview-area` (task 4), between `<ViewportToolbar …/>` and
`<ViewportStage …>`, add (responsive toolbar first, then select — same order as the
Browser pane):

```html
                    <SelectAreaToolbar
                        v-if="selectModeActive"
                        ref="selectToolbarRef"
                        :state="selectState"
                        @nav="selectNav"
                        @clear="selectClear"
                        @comment="openElementComment"
                        @close="setSelectMode(false)"
                    />
```

In `.preview-actions`, right AFTER the responsive-toggle `<template>` added in task 4,
add:

```html
                    <!-- Select-element toggle (HTML preview, composer reachable):
                         pick an element in the rendered page and hand its
                         description — with an optional screenshot — to the agent. -->
                    <template v-if="showHtmlPreview && isHtmlFile && !diffMode && htmlPreviewSrc && canSelectElement">
                        <wa-button
                            :id="selectButtonId"
                            class="preview-action-btn"
                            :class="{ 'preview-action-btn--active': selectModeActive }"
                            size="small"
                            variant="neutral"
                            appearance="filled"
                            @click="setSelectMode(!selectModeActive)"
                        >
                            <wa-icon name="arrow-pointer"></wa-icon>
                        </wa-button>
                        <AppTooltip :for="selectButtonId">
                            {{ selectModeActive ? 'Stop selecting an element' : 'Select an element' }}
                        </AppTooltip>
                    </template>
```

At the end of the template, after the existing text-selection
`<Teleport to="body">…</Teleport>`, add:

```html
        <!-- Element-select comment widget (HTML preview) — teleported to body
             like every other consumer. The "quote" is the element description;
             no source selection to clear. -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="elementCommentPosition"
                :selected-text="elementCommentText"
                :position="elementCommentPosition"
                :source-label="elementCommentSourceLabel"
                subject="selected area"
                auto-expand
                :clear-source-selection="() => {}"
                :capture-screenshot="captureElementScreenshot"
                :attach-screenshot="attachElementScreenshot"
                @close="elementCommentPosition = null"
            />
        </Teleport>
```

### Verify

- Session Artifacts tab, HTML artifact: toggle appears (composer present), picking
  works (hover red / lock green), the four nav buttons walk the DOM, clear, comment →
  widget opens with `Element:`/`Text:`/`Path:` lines, "Add to message" inserts into
  the composer, "Include screenshot" attaches a PNG of the element to the draft.
  **Confirm the screenshot attaches** — this is the case that would break if the code
  used `props.sessionId` (null in the Artifacts tab) instead of `composerSessionId`.
- Session Files tab, `.html` file: same, using `props.sessionId` for the draft.
- Works with responsive mode on simultaneously (toolbars stacked responsive-first).
- Agent edit while select mode is on → preview reloads, mode re-arms, selection
  cleared, no console error.
- File switch → mode off. Preview toggled to source → mode off.
- **Sidebar Artifacts browser (slash route): NO select toggle AND NO responsive
  toggle** (sessionless — the whole feature is absent there).
- Browser pane still fully functional (regression pass on select + capture).

---

## Task 6 — Docs sync, build, final verification

1. **CLAUDE.md** — in the *Artifact Network Broker* section, update the non-HMR
   sentence:

   > The shim + shell + browser-companion bundles are **not HMR'd** — `cd frontend &&
   > npm run build` after editing `artifact-broker/*`, `artifact-shell/*` or
   > `browser-companion/*`.

   →

   > The shim + shell + browser-companion bundles are **not HMR'd** — `cd frontend &&
   > npm run build` after editing `artifact-broker/*`, `artifact-shell/*`,
   > `browser-companion/*` or `element-select/*` (the shared element picker, bundled
   > into the companion and lazy-imported by the SPA for the artifact HTML preview's
   > select mode).

2. **AGENTS.md** — mirror the same sentence (same wording) in its broker section.

3. `cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab/frontend && npm run build` —
   must pass (companion bundle rebuilt with the extracted picker).

4. Full manual pass: Browser pane (companion select + capture + responsive), the two
   session FilePane contexts (Artifacts tab + Files tab — both features), and the
   sidebar Artifacts browser (confirm BOTH toggles are absent). Per the task 4/5
   verify lists.

---

## 6. Risks

- **R1 — parent-side `domToPng` on a child-document element**: the one unproven
  assumption; spiked in task 1c before task 5 starts. Fallback (only if it fails): a
  capture-only script injected via `broker_html.py`, to be re-planned.
- **R2 — behavior-neutral refactors of fresh code** (tasks 1–3): each is a standalone
  step with its own manual re-verification of the Browser pane before FilePane work
  starts.
- **R3 — dead documents**: every picker reference dies with the iframe's document; the
  `load`-listener lifecycle in task 5 is the only owner of create/destroy, and
  `destroy()` never throws.
- **R4 — companion bundle not HMR'd**: tasks 1 and 6 run `npm run build`; dev-server
  testing of the companion needs the rebuilt bundle.

## 7. Out of scope

- Dedicated artifact page (`/artifacts/<id>/`) — no composer; possible later via the
  same shared components in the shell bundle.
- Page-error capture for artifacts (possible follow-up via direct `contentWindow`
  listeners).
- Whole-page screenshots; persistence of viewport size or select state (parity with
  the Browser pane).
