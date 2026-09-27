<script setup>
// A peek overlay (90% of the inner area) for an edge whose dock(s) couldn't fit as a column
// or are collapsed-to-overlay. Backdrop closes it; the gutter stays visible above. Its body
// is a Teleport target registered under 'overlay' (only the overlay-active panel targets it).
import { computed, ref, watchEffect } from 'vue'
import TabPlacementMenu from './TabPlacementMenu.vue'
import SessionTabLink from './SessionTabLink.vue'
import TabBar from '../../ui/TabBar.vue'
import { insetRectStyle, NO_INSETS } from '../../../utils/panelInsets'

const props = defineProps({
    overlay: { type: Object, required: true }, // { edge, rect:{x,y,w,h}, tabs }
    // Inner edges of the overlay rect (SessionLayout, panelInsets.innerEdges): the card is
    // inset by half a panel gap on them.
    insets: { type: Object, default: () => NO_INSETS },
    activeTabId: { type: String, default: null },
    tabHref: { type: Function, required: true },
    dockOf: { type: Function, required: true }, // tabId -> its current dockId | 'center'
    registerTarget: { type: Function, required: true },
    unregisterTarget: { type: Function, required: true },
})
const emit = defineEmits(['select', 'close', 'place'])

const bodyRef = ref(null)

const style = computed(() => insetRectStyle(props.overlay.rect, props.insets))

watchEffect((onCleanup) => {
    const el = bodyRef.value
    if (!el) return
    props.registerTarget('overlay', el)
    onCleanup(() => props.unregisterTarget('overlay'))
})

function onShow(event) { emit('select', event.detail.name) }
</script>

<template>
    <div class="overlay-layer">
        <div class="overlay-backdrop" @click="emit('close')"></div>
        <div class="layout-overlay panel-card" :class="overlay.edge" :style="style" @click.stop>
            <!-- Flex row [scrollable tabs][fixed close], like DockRegion's bar: the close button
                 must stay visible while the tab strip scrolls, and never sit over a tab. -->
            <div class="overlay-topbar">
                <TabBar class="overlay-tabnav" :active="activeTabId" @wa-tab-show.stop="onShow">
                    <wa-tab v-for="t in overlay.tabs" :key="t.id" slot="nav" :panel="t.id" class="overlay-tab">
                        <SessionTabLink :href="tabHref(t.id)">
                            <wa-icon v-if="t.icon" :name="t.icon" class="overlay-tab-icon"></wa-icon>
                            <span>{{ t.label }}</span>
                        </SessionTabLink>
                        <TabPlacementMenu
                            :tab-id="t.id"
                            :current="dockOf(t.id)"
                            @place="(dest) => emit('place', t.id, dest)"
                        />
                    </wa-tab>
                </TabBar>
                <div class="overlay-controls">
                    <wa-button
                        class="overlay-close reduced-height"
                        appearance="plain"
                        size="small"
                        title="Close"
                        aria-label="Close overlay"
                        @click.stop="emit('close')"
                    >
                        <wa-icon name="xmark"></wa-icon>
                    </wa-button>
                </div>
            </div>
            <div ref="bodyRef" class="overlay-body"></div>
        </div>
    </div>
</template>

<style scoped>
.overlay-backdrop {
    position: absolute;
    inset: 0;
    z-index: 8;
    background: rgba(0, 0, 0, 0.2);
    border-radius: var(--panel-radius); /* follow the outer cards' corners */
}
.layout-overlay {
    position: absolute;
    z-index: 11;
    display: flex;
    flex-direction: column;
    overflow: clip; /* not a scroll container — see .session-layout */
    /* Background, full border and radius come from .panel-card; the stronger shadow overrides
       the card's: the overlay floats above the other cards. Not --wa-shadow-l (level 3): its
       48px side reach would be cut flat by the layout's clip in the gap. */
    box-shadow: var(--panel-overlay-shadow);
}
/* While a FilePane preview teleported into this overlay is expanded to full-window
   (position:fixed; z-index:1000), the overlay's own z-index:11 stacking context traps it
   below the gutters (z-index:12, resolved a context higher) — they paint over the fullscreen.
   Lift the overlay above the gutters so the fullscreen escapes them. Same :has() trigger as
   DockRegion's fix; there the region's stacking context comes from `isolation`, so it drops to
   auto, but the overlay's comes from an explicit z-index, so we raise that instead. */
.layout-overlay:has(.file-pane-preview--fullscreen) {
    z-index: 13;
}
.overlay-topbar {
    flex: 0 0 auto;
    min-width: 0;
    display: flex;
    align-items: stretch;
    overflow: hidden;
    /* Keep the first tab and the close button clear of the card's rounded corners. */
    padding-inline: var(--panel-corner-inset);
}
.overlay-tabnav {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
}
/* Continues the tabs' track under the fixed close button (same tokens as WA's track). */
.overlay-controls {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    border-bottom: var(--divider-size) solid var(--wa-color-neutral-fill-normal);
}
.overlay-tabnav::part(body) {
    display: none;
}
.overlay-tabnav::part(tabs) {
    align-items: center;
}
.overlay-tab::part(base) {
    display: inline-flex;
    align-items: center;
}
.overlay-tab-icon {
    font-size: 0.85em;
}
.overlay-close {
    --wa-form-control-padding-inline: 0.3em;
}
.overlay-body {
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow: clip; /* not a scroll container — see .session-layout */
    display: flex;
    flex-direction: column;
}
</style>
