<script setup>
/**
 * TabBar - Unified wrapper around <wa-tab-group>.
 *
 * A transparent, pre-styled tab group: write the exact same `<wa-tab slot="nav">`
 * and `<wa-tab-panel>` children you would put in a raw <wa-tab-group>, and they
 * pass straight through. The wrapper only carries our shared defaults so the
 * compact size no longer has to be re-declared at every call site.
 *
 * Behaviour added on top, both only when the tabs overflow (WA shows its chevrons):
 *  - a vertical mouse wheel over the tab strip scrolls it horizontally (touch and
 *    horizontal trackpad already pan via the native overflow-x);
 *  - the active tab is kept in view when the tab list is reordered — WA only
 *    re-scrolls on an `active` change, not when the active tab merely moves.
 * And always: the active tab's line glides from tab to tab (utils/glideInk.js, visual
 * refresh step 4c).
 * With `crossfade` (read at mount): a user's click or key on a tab of this bar switches the
 * panel in a view-transition crossfade (utils/tabCrossfade.js, step 5c); `crossfade-start`
 * is emitted just before, synchronously.
 *
 * Usage:
 *   <TabBar :active="activeId" @wa-tab-show="onShow">
 *     <wa-tab slot="nav" panel="a">A</wa-tab>
 *     <wa-tab-panel name="a">…</wa-tab-panel>
 *   </TabBar>
 *
 * All attributes, classes and listeners are forwarded verbatim to the underlying
 * <wa-tab-group> (inheritAttrs is off + v-bind="$attrs"; Vue still merges class/style).
 *
 * Exposes:
 *   - el: the native <wa-tab-group> element, for the rare consumer that needs the
 *     shadow root (e.g. setting a title on ::part(nav)). A template `ref` on this
 *     component yields the Vue instance, not the element — reach the host via `.el`.
 */
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { createGlideInk } from '../../utils/glideInk.js'
import { installTabCrossfade } from '../../utils/tabCrossfade.js'
import { afterViewTransitionUpdate, supportsViewTransitions } from '../../utils/viewTransition.js'

defineOptions({ inheritAttrs: false })

const props = defineProps({
    // Crossfade the panels on a user's tab switch (the session's center and dock bars).
    crossfade: { type: Boolean, default: false },
})
const emit = defineEmits(['crossfade-start'])

const el = ref(null)
defineExpose({ el })

// WA's internal `.nav` scroller (the same element its chevrons drive). All our
// scroll handling is scoped to it, so a wheel/scroll over a tab panel is never
// hijacked. It's a WA internal, so every path no-ops when it's absent.
let navEl = null

// ── Wheel → horizontal scroll ──────────────────────────────────────────────────
// Only a vertical wheel is translated, and only when the strip overflows — otherwise
// the page scrolls as usual. Horizontal wheel (deltaX / trackpad) is left to native.
function onWheel(event) {
    if (event.deltaY === 0 || event.shiftKey) return
    if (!navEl || navEl.scrollWidth <= navEl.clientWidth) return
    navEl.scrollLeft += event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY
    event.preventDefault()
}

// ── Keep the active tab visible across tab-list changes ─────────────────────────
// WA only re-scrolls to the active tab when its `active` property changes (its
// `watch("active")` → scrollIntoView). A pure REORDER — the active tab keeps the
// same panel but moves in the DOM (e.g. a workflow run jumping to the front once it
// re-sorts) — fires no such scroll, leaving the tab parked off-screen. We watch the
// slotted tab list and re-apply WA's own scrollIntoView logic. `el.active` is read
// live at scroll time, so this only ever FOLLOWS the active tab (chosen by the call
// site), never picks one: when a freshly-added tab is also activated, WA already
// scrolled to it and this is a no-op.
let listObserver = null
let pendingFrame = 0

function ensureActiveVisible() {
    pendingFrame = 0
    if (!navEl || navEl.scrollWidth <= navEl.clientWidth) return
    const active = el.value?.active
    if (!active) return
    const tab = [...el.value.querySelectorAll(':scope > wa-tab')].find((t) => t.panel === active)
    if (!tab) return
    // WA's own algorithm: align whichever near edge is out of view, so the tab shows
    // fully when it fits and otherwise as much as possible.
    const offsetLeft = tab.getBoundingClientRect().left - navEl.getBoundingClientRect().left + navEl.scrollLeft
    const minX = navEl.scrollLeft
    const maxX = navEl.scrollLeft + navEl.offsetWidth
    if (offsetLeft < minX) {
        navEl.scrollTo({ left: offsetLeft, behavior: 'smooth' })
    } else if (offsetLeft + tab.clientWidth > maxX) {
        navEl.scrollTo({ left: offsetLeft - navEl.offsetWidth + tab.clientWidth, behavior: 'smooth' })
    }
}

function scheduleEnsureActiveVisible() {
    if (pendingFrame) return
    pendingFrame = requestAnimationFrame(ensureActiveVisible)
}

// ── Gliding line (visual refresh step 4c) ──────────────────────────────────────
// The line under the active tab is an ink (the ::after of WA's `tabs` part, styled
// below) that glides from tab to tab. Its box is written on the host by a glide
// controller; WA's own border on the active tab stays until the ink is placed.
let glideInk = null
let activeObserver = null
let unmounted = false

function createTabGlide(host) {
    const tabs = host.shadowRoot?.querySelector('[part~="tabs"]')
    // WA internals changed: no ink, WA's border stays.
    if (!tabs) return
    glideInk = createGlideInk({
        container: tabs,
        target: host,
        flushTarget: tabs,
        flushPseudo: '::after',
        getActive: () => (host.placement === 'top' ? host.querySelector(':scope > wa-tab[active]') : null),
        // A tab before the active one can change width on its own (an indicator, a label).
        getItems: () => [...host.querySelectorAll(':scope > wa-tab')],
    })
    // Every path that changes the active tab goes through WA's setActiveTab, which
    // reflects `active` on the tabs. No subtree childList: it would fire on every change
    // inside the panels. A switch made just before a view transition (the overlay's bar:
    // WA switches on the click, the overlay crossfade starts in the same task) moves the
    // ink after the transition's update, so it glides in the new image instead of behind
    // the frozen old one.
    activeObserver = new MutationObserver((mutations) => {
        if (mutations.some((m) => m.target.tagName === 'WA-TAB' && m.target.closest('wa-tab-group') === host)) {
            afterViewTransitionUpdate(() => glideInk?.update())
        }
    })
    activeObserver.observe(host, { attributes: true, attributeFilter: ['active'], subtree: true })
}

let uninstallCrossfade = null

onMounted(async () => {
    if (el.value?.updateComplete) await el.value.updateComplete
    // Unmounted during the await: create nothing (the observers would leak).
    if (unmounted || !el.value) return
    if (props.crossfade && supportsViewTransitions() && typeof el.value.setActiveTab === 'function') {
        uninstallCrossfade = installTabCrossfade(el.value, { onStart: () => emit('crossfade-start') })
    }
    navEl = el.value.shadowRoot?.querySelector('.nav')
    navEl?.addEventListener('wheel', onWheel, { passive: false })
    // The wa-tab elements are direct light-DOM children, so a keyed reorder surfaces
    // as childList mutations here. Coalesce a burst into one rAF (lets layout settle).
    // A reorder, an added or a removed tab also moves the active one: the ink follows
    // (and starts watching an added tab).
    listObserver = new MutationObserver(() => {
        scheduleEnsureActiveVisible()
        glideInk?.update()
    })
    listObserver.observe(el.value, { childList: true })
    createTabGlide(el.value)
})

onBeforeUnmount(() => {
    unmounted = true
    uninstallCrossfade?.()
    uninstallCrossfade = null
    navEl?.removeEventListener('wheel', onWheel)
    listObserver?.disconnect()
    activeObserver?.disconnect()
    glideInk?.destroy()
    glideInk = null
    if (pendingFrame) cancelAnimationFrame(pendingFrame)
})
</script>

<template>
    <wa-tab-group ref="el" class="tab-bar" v-bind="$attrs"><slot /></wa-tab-group>
</template>

<style scoped>
.tab-bar {
    --track-width: var(--divider-size);
    /* The glowing ink (glow.css tokens): a thin line; its glow carries the emphasis. */
    --glow-ink-thickness: 2px;
}

/* The compact size, carried once for every call site. Slotted <wa-tab>s come from
   the parent (they bear the parent's scope id, not ours), so reaching ::part requires
   :deep(). The direct-child combinator keeps the size from leaking into a nested tab
   bar rendered inside one of our panels (e.g. a TerminalPanel's own TabBar). */
.tab-bar > :deep(wa-tab::part(base)) {
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    gap: var(--wa-space-2xs);
}

/* WA's track (the line under the tabs) is the bottom border of ::part(tabs), which
   lives inside the scroller — so it stops short of the scroll chevrons, leaving a gap
   under them. The chevrons span the bar's full height (top:0; bottom:0), so a matching
   bottom border lands exactly on the tabs' track and runs the line edge to edge. */
.tab-bar::part(scroll-button) {
    border-bottom: var(--track-width) solid var(--track-color);
}

/* The gliding line (step 4c): the active tab's own box, with the line at its bottom, so the
   line stays exactly where WA draws its border (some call sites center shorter tabs in the
   strip). --safe-track-width is WA's, declared on its :host. */
.tab-bar::part(tabs)::after {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    box-sizing: border-box;
    width: var(--glide-w, 0px);
    height: var(--glide-h, 0px);
    translate: var(--glide-x, 0px) var(--glide-y, 0px);
    /* An accent gradient strip at the bottom of the active tab's box, glowing (step 6a). The
       nav clips its overflow: only the upward half of the glow shows. */
    border: 0;
    background: linear-gradient(90deg, var(--glow-accent), var(--glow-accent-shifted))
        no-repeat left bottom / 100% var(--glow-ink-thickness);
    filter: drop-shadow(0 0 0.3125rem color-mix(in oklab, var(--glow-accent) 70%, transparent));
    pointer-events: none;
    opacity: 0;
    transition: var(--glide-fade);
}
.tab-bar[data-glide-ready]::part(tabs)::after {
    opacity: 1;
    transition: var(--glide-transition);
}
.tab-bar[data-glide-instant]::part(tabs)::after {
    transition: none;
}
/* The ink draws the line: the active tab keeps its border width (no layout shift) but not
   its color. Direct children only, so a nested TabBar keeps its own. */
.tab-bar[data-glide-ready] > :deep(wa-tab[active]) {
    border-block-end-color: transparent;
}
</style>
