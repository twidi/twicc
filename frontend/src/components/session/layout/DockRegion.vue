<script setup>
// One shown dock region (a side column slice or the bottom). Renders a native WA tab bar
// (nav-only, like TerminalPanel) with a per-tab placement arrow and a far-right minimize
// button; the body is a Teleport target the parent fills with the real panel(s). The region
// holds one dockId (split) or two (merged) — its body is registered under each.
import { computed, ref, watchEffect } from 'vue'
import TabPlacementMenu from './TabPlacementMenu.vue'
import SessionTabLink from './SessionTabLink.vue'
import TabBar from '../../ui/TabBar.vue'

const props = defineProps({
    region: { type: Object, required: true },
    activeTabId: { type: String, default: null },
    // The global route owner (the tab the URL points at). When it lives in this region the region's
    // tab bar stays full opacity; otherwise the bar is dimmed, marking it as a non-active region.
    focusedTabId: { type: String, default: null },
    tabHref: { type: Function, required: true },
    // When true this region is the maximized one (fills the whole layout area): its tab bar shows a
    // restore button instead of minimize/maximize, and the per-tab placement arrows are hidden (the
    // only exit is restore).
    maximized: { type: Boolean, default: false },
    registerTarget: { type: Function, required: true },
    unregisterTarget: { type: Function, required: true },
})
const emit = defineEmits(['select', 'tab-activate', 'minimize', 'maximize', 'restore', 'place', 'pane-focus'])

const bodyRef = ref(null)

// This region owns the route when its shown tab is the URL's tab (the route overrides a region's
// active tab, so its activeTabId equals focusedTabId precisely for the owning region). Non-owning
// regions dim their tab bar to mark them as inactive.
const isRouteActive = computed(() => props.activeTabId != null && props.activeTabId === props.focusedTabId)

const style = computed(() => ({
    left: `${props.region.x}px`,
    top: `${props.region.y}px`,
    width: `${props.region.w}px`,
    height: `${props.region.h}px`,
}))
const tabs = computed(() => props.region.slots.flatMap((s) => s.tabs))
const dockIds = computed(() => props.region.slots.map((s) => s.dockId))

function dockOfTab(tabId) {
    const slot = props.region.slots.find((s) => s.tabs.some((t) => t.id === tabId))
    return slot ? slot.dockId : 'center'
}

// Register the body element as the teleport target for every dock this region hosts.
watchEffect((onCleanup) => {
    const el = bodyRef.value
    if (!el) return
    const ids = dockIds.value
    for (const d of ids) props.registerTarget(`region:${d}`, el)
    onCleanup(() => { for (const d of ids) props.unregisterTarget(`region:${d}`) })
})

// wa-tab-group emits wa-tab-show not only on a user tab switch but also when it (re)mounts and
// asserts its initial active tab — and this region IS destroyed/recreated whenever dockingRendered
// flips, e.g. on every KeepAlive return (the area measures 0×0 while detached). A fresh tab-group
// has activeTab === undefined, so it fires wa-tab-show for the tab it already shows. Treating that
// as a switch would navigate the route onto a docked secondary the user never touched. A genuine
// switch always targets a DIFFERENT tab than the region's current active one (clicking the active
// tab fires no wa-tab-show — onBodyClick claims it instead), so only forward real changes.
function onShow(event) {
    if (event.detail.name !== props.activeTabId) emit('select', event.detail.name)
}

// Click-to-focus: interacting anywhere in a pane should make its tab the route owner. We listen
// on the CLICK (capture phase, so it fires even if the panel stops propagation — e.g. xterm),
// NOT pointerdown: a click is the END of the gesture, after any file-select / terminal-tab change
// it produced. This is a *deferred* focus request (see SessionView.requestPaneFocus), superseded
// by any real navigation from the same gesture — so a navigating click wins and a plain click
// falls through to focusing the pane's current state, with no competing navigation.
function onBodyClick() {
    if (props.activeTabId) emit('pane-focus', props.activeTabId)
}

// A tab HEADER click is an explicit activation: focus its panel's filter (tab-activate) AND claim the
// route for it (pane-focus, deferred — superseded by a real switch). A body click only claims (no focus).
function onTabClick(tabId) {
    emit('tab-activate', tabId)
    emit('pane-focus', tabId)
}

// Empty area of the dock's tab bar (a shadow part, retargeted to the host — tabs and buttons are
// slotted, so target !== host for them) acts as the region's active tab: a click activates it, a
// double-click toggles maximize/restore. An active tab's own dblclick is .stop'd, so it never reaches
// here; an active tab's plain click bubbles up but is filtered out by the host-only guard.
function onEmptyBarClick(event) {
    if (event.target !== event.currentTarget) return
    if (props.activeTabId) onTabClick(props.activeTabId)
}
function onEmptyBarDblClick(event) {
    if (event.target !== event.currentTarget) return
    if (props.maximized) emit('restore')
    else emit('maximize', dockIds.value, props.activeTabId)
}
</script>

<template>
    <div class="dock-region" :class="region.kind" :data-rid="region.id" :style="style">
        <!-- Flex row [scrollable tabs][fixed controls], like TerminalPanel's actions bar: the
             window buttons must stay visible while the tab strip scrolls, and never sit over a
             tab — they live beside the scroll area, not inside it. -->
        <div class="dock-topbar" :class="{ 'tabnav-dimmed': !isRouteActive }" :title="maximized ? 'Double-click to restore' : 'Double-click to maximize'">
        <TabBar class="dock-tabnav" :active="activeTabId" @wa-tab-show.stop="onShow" @click="onEmptyBarClick" @dblclick="onEmptyBarDblClick">
            <!-- Clicking a tab header activates it: focuses its filter + claims the route (onTabClick).
                 wa-tab-show handles switching to a *different* tab; the claim also covers clicking the
                 tab that is ALREADY this group's active one (no wa-tab-show fires then) while another
                 region owns the route. The placement arrow stops its own click, so it never reaches here. -->
            <!-- Double-click toggles maximize for this region: maximize it (focusing the double-clicked
                 tab), or restore it when it is already the maximized region. -->
            <wa-tab v-for="t in tabs" :key="t.id" slot="nav" :panel="t.id" class="dock-tab"
                @click="onTabClick(t.id)"
                @dblclick.stop="maximized ? emit('restore') : emit('maximize', dockIds, t.id)">
                <SessionTabLink :href="tabHref(t.id)">
                    <wa-icon v-if="t.icon" :name="t.icon" class="dock-tab-icon"></wa-icon>
                    <span class="dock-tab-label">{{ t.label }}</span>
                </SessionTabLink>
                <TabPlacementMenu
                    v-if="!maximized"
                    :tab-id="t.id"
                    :current="dockOfTab(t.id)"
                    @place="(dest) => emit('place', t.id, dest)"
                />
            </wa-tab>
        </TabBar>
        <div class="dock-controls" @click="onEmptyBarClick" @dblclick="onEmptyBarDblClick">
            <!-- Maximized cue: the restore button is the only exit, and the rest of the layout is gone —
                 so it wears a loud brand-accent fill (not the plain min/max styling) to flag "you are
                 maximized, click here to come back" against the otherwise-neutral bar. -->
            <wa-button
                v-if="maximized"
                class="dock-winbtn dock-restore reduced-height"
                variant="brand"
                appearance="accent"
                size="small"
                title="Restore (Alt+Shift+Enter)"
                aria-label="Restore"
                @click.stop="emit('restore')"
            >
                <wa-icon name="compress"></wa-icon>
            </wa-button>
            <template v-else>
                <wa-button
                    class="dock-winbtn dock-minimize reduced-height"
                    appearance="plain"
                    size="small"
                    title="Minimize to gutter (Alt+Shift+Backspace)"
                    aria-label="Minimize to gutter"
                    @click.stop="emit('minimize', dockIds)"
                >
                    <wa-icon name="window-minimize"></wa-icon>
                </wa-button>
                <wa-button
                    class="dock-winbtn dock-maximize reduced-height"
                    appearance="plain"
                    size="small"
                    title="Maximize (Alt+Shift+Enter)"
                    aria-label="Maximize"
                    @click.stop="emit('maximize', dockIds, activeTabId)"
                >
                    <wa-icon name="expand"></wa-icon>
                </wa-button>
            </template>
        </div>
        </div>
        <div ref="bodyRef" class="dock-body" @click.capture="onBodyClick"></div>
    </div>
</template>

<style scoped>
.dock-region {
    position: absolute;
    /* Own stacking context so the panel's internal z-index (sticky headers, selects, the
       pane-callout overlay) stays local and can't paint over the resize splitters / gutters /
       overlay, which live in the parent (.session-layout) stacking context. */
    isolation: isolate;
    display: flex;
    flex-direction: column;
    overflow: clip; /* not a scroll container — see SessionLayout's .session-layout */
    background: var(--wa-color-surface-default, transparent);
    min-width: 0;
    min-height: 0;
    --dock-border: var(--divider-size) solid var(--wa-color-surface-border, rgba(0, 0, 0, 0.12));
}
/* While a FilePane preview inside is expanded to full-window (position:fixed; z-index:1000), drop the
   isolation so the overlay escapes this region and covers the splitters / gutters — otherwise it is
   trapped at the region's z-auto level and the gutters (z-index:12, resolved a context higher) paint
   over it. Pure CSS via :has() — no provide/inject chain (the panels teleport, so an inject-based host
   never reliably reached this region). Verified live: dropping only this isolation puts the fullscreen
   image above the gutters. */
.dock-region:has(.file-pane-preview--fullscreen) {
    isolation: auto;
}

/* Borders sit only on inner edges (facing the center or a sibling divider); edges that touch
   the layout's outer boundary stay bare. The resolver encodes "both siblings shown" in the
   region id: left-bottom / right-bottom exist ONLY when the side column is split, so their top
   border is the divider between the two stacked docks; bottom-right exists ONLY when the bottom
   is split, and owns the vertical divider toward bottom-left. */
.dock-region.col-left { border-right: var(--dock-border); }
.dock-region.col-right { border-left: var(--dock-border); }
.dock-region.bottom { border-top: var(--dock-border); }
.dock-region[data-rid="left-bottom"],
.dock-region[data-rid="right-bottom"] { border-top: var(--dock-border); }
.dock-region[data-rid="bottom-right"] { border-left: var(--dock-border); }

/* The bar row: the tab strip takes the space and scrolls; the window buttons keep their own
   fixed zone at the end. Stretch aligns the controls' bottom border with the strip's track. */
.dock-topbar {
    flex: 0 0 auto;
    min-width: 0;
    display: flex;
    align-items: stretch;
    overflow: hidden;
    transition: opacity var(--wa-transition-fast, 0.15s) var(--wa-transition-easing, ease);
}
/* Active-region cue: a dock that doesn't own the route dims its tab bar (nav + window buttons; the
   panel content lives in .dock-body and stays full opacity), so the region holding the URL's tab
   reads as the active one among the several that can show content at once. */
.dock-topbar.tabnav-dimmed {
    opacity: 0.5;
}
/* TabBar used only for its nav — hide its (empty) body, like TerminalPanel */
.dock-tabnav {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
}
.dock-tabnav::part(base) {
    overflow: hidden;
}
/* Continues the tabs' track (and the double-click affordance) under the fixed buttons, so the
   bar reads as one piece. Same tokens as WA's track: TabBar's --track-width is --divider-size,
   the color is WA's default. */
.dock-controls {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    border-bottom: var(--divider-size) solid var(--wa-color-neutral-fill-normal);
    cursor: pointer;
}
/* The bar is double-clickable to maximize/restore (empty area + tabs); pointer signals it. The
   min/max/restore buttons keep their own cursor + richer titles. */
.dock-tabnav::part(nav) {
    cursor: pointer;
}
.dock-tabnav::part(body) {
    display: none;
}
.dock-tabnav::part(tabs) {
    align-items: center;
}
.dock-tab {
    display: inline-flex;
}
.dock-tab::part(base) {
    display: inline-flex;
    align-items: center;
}
.dock-tab-icon {
    font-size: 0.85em;
}
.dock-winbtn {
    --wa-form-control-padding-inline: 0.3em;
}

.dock-body {
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow: clip; /* not a scroll container — see SessionLayout's .session-layout */
    display: flex;
    flex-direction: column;
}
</style>
