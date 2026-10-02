// useSessionLayout — wiring for the dockable layout: measure -> pure resolve -> dumb render.
//
// Mirrors the computeVisualItems pattern (raw inputs -> pure transform -> render). All layout
// rules live in the pure `resolveLayout` (utils/layoutResolver.js); this composable only:
//   - measures the session content area (useElementSize),
//   - reads the per-session layout intention from the store (assignment/collapsed/active*),
//   - feeds the resolver and exposes the renderDescription + helpers + actions.
//
// Active-tab model (agreed): the route is the single pointer to "the active/focused tab".
// `routeActiveTabId` overrides the active tab of the region that contains it; other regions
// fall back to per-group memory (activeByGroup), then the first content-bearing tab. Tab
// clicks navigate the route (handled by the caller); a watch here records the memory and
// makes a routed tab visible if its dock was collapsed.

import { computed, nextTick, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import { resolveLayout, DOCKS } from '../utils/layoutResolver'
import { edgeOfDock } from '../components/session/layout/dockMeta'
import { moveLayoutTabOrder, orderLayoutTabs } from '../utils/layoutDrag'
import { useDataStore } from '../stores/data'
import { useHelpStore } from '../stores/help'
import { useSettingsStore } from '../stores/settings'

const EMPTY_INTENTION = Object.freeze({
    assignment: Object.freeze({}),
    collapsed: Object.freeze([]),
    activeSide: 'left',
    activeResize: 'left',
    activeByGroup: Object.freeze({}),
    resizeFractions: Object.freeze({}),
    tabOrder: Object.freeze([]),
    maximized: null,
})

// Stable key for a region's active-tab memory. 'center' for the center; otherwise the
// region's dockIds sorted+joined (so a merged region has its own key). Matches the prototype.
export function groupKeyOf(slots) {
    const docks = slots.map((s) => s.dockId)
    if (docks.includes('center')) return 'center'
    return docks.slice().sort().join('+')
}

export function useSessionLayout({ sessionId, containerRef, tabs, routeActiveTabId }) {
    const store = useDataStore()
    const helpStore = useHelpStore()
    const settings = useSettingsStore()

    // ---- Measure ----
    // The raw measure drops to 0×0 while the session is deactivated (KeepAlive detaches the
    // DOM), which would collapse the resolver to its mobile 'tabs' fallback and tear the whole
    // dock layout down — then back up on return. A cached session is not being looked at, so
    // there is nothing to re-resolve: latch the last size actually seen on screen and let the
    // layout stay exactly as the user left it.
    const { width: liveWidth, height: liveHeight } = useElementSize(containerRef)
    const width = ref(0)
    const height = ref(0)
    watch([liveWidth, liveHeight], ([w, h]) => {
        if (w > 0 && h > 0) {
            width.value = w
            height.value = h
        }
    }, { immediate: true })
    const measured = computed(() => width.value > 0 && height.value > 0)

    // ---- Intention (null-safe; mutations go through store actions only) ----
    const intention = computed(() => store.getSessionLayout(sessionId.value) || EMPTY_INTENTION)

    // Apply the persisted ``Session.layout`` to the live working copy — reactively. The session can
    // load AFTER this composable mounts (async fetch via loadSessionById / loadSessions, which assign
    // ``this.sessions[id]`` directly, bypassing add/updateSession), so a one-shot hydrate at mount
    // would read an empty layout and stick on single pane until some later broadcast. Watching the
    // persisted blob re-seeds the working copy the moment it appears (or changes cross-device); the
    // store action no-ops while a local edit is in flight (ours wins).
    watch(
        [sessionId, () => store.getSession(sessionId.value)?.layout],
        () => {
            const id = sessionId.value
            if (!id) return
            store.ensureSessionLayout(id)
            store._hydrateSessionLayoutFromPersisted(id, store.getSession(id)?.layout)
        },
        { immediate: true },
    )

    // A present tab is "docked" when assigned to one of the six docks.
    const isDockingActive = computed(() => {
        const a = intention.value.assignment
        return tabs.value.some((t) => !t.fixedCenter && DOCKS.includes(a[t.id]))
    })

    // ---- Resolve ----
    const orderedTabs = computed(() => orderLayoutTabs(tabs.value, intention.value.tabOrder))
    const render = computed(() => resolveLayout({
        tabs: orderedTabs.value,
        assignment: intention.value.assignment,
        viewport: { w: width.value, h: height.value },
        activeSide: intention.value.activeSide,
        activeResize: intention.value.activeResize,
        collapsed: intention.value.collapsed,
        config: intention.value.resizeFractions,
        maximized: intention.value.maximized,
    }))

    // The multi-region layout actually renders only when something is docked, the area is
    // measured, and we're not in the mobile 'tabs' fallback (where everything folds back into
    // the center strip). Until then the view behaves exactly like the plain tab group.
    const dockingRendered = computed(
        () => isDockingActive.value && measured.value && render.value.mode !== 'tabs'
    )

    // ---- Active-tab resolution per region (route override -> memory -> first content) ----
    function regionActiveTabId(region) {
        const groupTabs = region.slots.flatMap((s) => s.tabs)
        if (!groupTabs.length) return null
        const routed = routeActiveTabId.value
        if (routed && groupTabs.some((t) => t.id === routed)) return routed
        const wanted = intention.value.activeByGroup[groupKeyOf(region.slots)]
        if (wanted && groupTabs.some((t) => t.id === wanted)) return wanted
        const firstContent = groupTabs.find((t) => !t.optional || t.hasContent)
        return (firstContent || groupTabs[0]).id
    }

    // The active tab for a single dock sitting in the rail (gutter), from its dockId + the tabs it
    // holds. Mirrors regionActiveTabId: route override, then memory (activeByGroup, keyed by the dockId
    // when un-merged), then first content. The route override matters for the one case where a railed
    // dock DOES own the route — its tab peeking as an overlay (the overlay is derived from the route):
    // the rail then marks that tab as active and its empty area acts on it, so a click closes the peek.
    // Lets the gutter mark, and its empty area act on, "the active tab" of the dock it points at.
    function dockActiveTabId(dockId, tabs) {
        if (!tabs || !tabs.length) return null
        const routed = routeActiveTabId.value
        if (routed && tabs.some((t) => t.id === routed)) return routed
        const wanted = intention.value.activeByGroup[dockId]
        if (wanted && tabs.some((t) => t.id === wanted)) return wanted
        const firstContent = tabs.find((t) => !t.optional || t.hasContent)
        return (firstContent || tabs[0]).id
    }

    // The dock a tab is assigned to ('center' when unassigned).
    function dockOf(tabId) {
        return intention.value.assignment[tabId] || 'center'
    }

    function tabById(tabId) {
        return tabs.value.find((tab) => tab.id === tabId) || null
    }

    // ---- Teleport target resolution (logical keys; SessionView maps key -> element) ----
    // 'center:<tabId>'  -> the tab's wa-tab-panel slot in the center group (WA hides inactive)
    // 'overlay'         -> the open overlay body (only the overlay-active tab targets it)
    // 'region:<dockId>' -> a shown dock region's body
    // null              -> nowhere visible (collapsed / overlay-but-not-active) -> hidden host
    function targetKeyForTab(tabId) {
        if (!dockingRendered.value) return `center:${tabId}`
        const dock = dockOf(tabId)
        if (dock === 'center') return `center:${tabId}`
        const edge = edgeOfDock(dock)
        if (openOverlayEdge.value === edge) {
            return routeActiveTabId.value === tabId ? 'overlay' : null
        }
        const shown = render.value.regions.some((r) => r.slots.some((s) => s.dockId === dock))
        return shown ? `region:${dock}` : null
    }

    // Whether a tool panel should be shown (vs hidden) at its destination. Center relies on
    // WA's own inactive-panel hiding; dock regions stack all their panels and need this.
    function isToolPanelVisible(tabId) {
        if (!dockingRendered.value) return true
        const dock = dockOf(tabId)
        if (dock === 'center') return true
        const edge = edgeOfDock(dock)
        if (openOverlayEdge.value === edge) return routeActiveTabId.value === tabId
        const region = render.value.regions.find((r) => r.slots.some((s) => s.dockId === dock))
        if (!region) return false
        return regionActiveTabId(region) === tabId
    }

    // The edge whose gutter currently holds this tab with the given action, or null. Gutter actions:
    // 'overlay' (the tab's dock is in peek mode), 'swap' (the tab's side lost mutual exclusion and
    // waits in its rail), 'restore' (the dock is minimized). A tab shown in a region is in no gutter.
    function gutterEdgeForTabAction(tabId, action) {
        for (const g of render.value.gutters) {
            for (const item of g.items) {
                if (item.action === action && item.tabs.some((t) => t.id === tabId)) return g.edge
            }
        }
        return null
    }

    // The edge whose overlay would show this tab — its dock is in overlay (peek) mode, not in a
    // region. Drives the derived overlay open-state from the route (navigation / bookmark / history).
    function overlayEdgeForTab(tabId) { return gutterEdgeForTabAction(tabId, 'overlay') }

    // ---- Overlay (peek) — DERIVED from the route, the single source of truth. The overlay is open
    // iff the active tab's dock is in overlay mode (overlayEdgeForTab), on that edge, showing the
    // active tab. Navigation / back-forward / bookmarks drive it directly: no separate state to
    // desync, nothing to open or close imperatively — opening/closing is just navigating. ----
    const openOverlayEdge = computed(() => overlayEdgeForTab(routeActiveTabId.value))

    // ---- Actions (intention mutations via the store) ----
    function place(tabId, dest) {
        store.setTabDock(sessionId.value, tabId, dest)
        // First time the user manually docks a tab (anywhere but back to
        // center): surface the layout help once. No-ops if already seen,
        // disabled, or another help is open. place() is the single funnel for
        // every manual placement (tab-header arrow, overlay, command palette),
        // and is never hit by programmatic layout loads.
        if (dest && dest !== 'center') {
            helpStore.maybeAutoShow('layout-docks', {
                platform: settings._isTouchDevice ? 'mobile' : 'desktop',
                os: settings.os,
                enabledProviders: settings.enabledProviders,
            })
        }
    }
    function moveTab(tabId, dest, {
        targetTabId = null,
        position = 'after',
        restoreDestination = true,
    } = {}) {
        const draggableIds = tabs.value.filter((tab) => !tab.fixedCenter).map((tab) => tab.id)
        const tabOrder = moveLayoutTabOrder({
            tabOrder: intention.value.tabOrder,
            knownTabIds: draggableIds,
            tabId,
            targetTabId,
            position,
        })
        store.moveLayoutTab(sessionId.value, { tabId, dest, tabOrder, restoreDestination })
        if (dest && dest !== 'center') {
            helpStore.maybeAutoShow('layout-docks', {
                platform: settings._isTouchDevice ? 'mobile' : 'desktop',
                os: settings.os,
                enabledProviders: settings.enabledProviders,
            })
        }
    }
    function minimize(dockIds) {
        for (const d of [].concat(dockIds)) store.minimizeDock(sessionId.value, d)
    }
    function restore(dockId) { store.restoreDock(sessionId.value, dockId) }
    function swapSide(edge) { store.setLayoutActiveSide(sessionId.value, edge) }
    function rememberActive(groupKey, tabId) {
        store.setLayoutGroupActiveTab(sessionId.value, groupKey, tabId)
    }
    // Resize intention. `activeResize` picks which side column wins when both are shown and space is
    // tight; `setResizeFraction` writes a draggable fraction (configKey -> 0..1) the resolver clamps.
    function setActiveResize(side) { store.setLayoutActiveResize(sessionId.value, side) }
    function setResizeFraction(key, value) { store.setLayoutResizeFraction(sessionId.value, key, value) }

    // Maximize: a region (its dockIds, or ['center']) fills the whole layout area; the only exit is
    // restore. Transient — never persisted. The resolver short-circuits to a single 'maximized' region.
    function maximize(dockIds) { store.setLayoutMaximized(sessionId.value, dockIds) }
    function restoreMaximized() { store.setLayoutMaximized(sessionId.value, null) }
    const maximizedRegion = computed(() => render.value.regions.find((r) => r.kind === 'maximized') || null)
    const isCenterMaximized = computed(() => !!maximizedRegion.value?.slots.some((s) => s.dockId === 'center'))
    const centerVisible = computed(() => !maximizedRegion.value || isCenterMaximized.value)

    // Route drives the focus → keep the active tab visible: if a region that doesn't hold it is
    // maximized, restore first (the resolver renders ONLY the maximized region, so the tab would be
    // hidden); then reveal it if its dock is minimized (restore) or its side lost mutual exclusion
    // (swap its column in). Then record it as its region's remembered tab so the region keeps it when
    // focus moves away. Mirrors the overlay's route-derived visibility; together they uphold "the
    // active tab is always shown somewhere".
    // Watch the relevant resolved layout state as well as the route. On a direct URL / bookmark the
    // route id is already present when this composable mounts, often before the session's persisted
    // layout is hydrated. Watching only routeActiveTabId therefore missed a dock that became known as
    // collapsed a moment later. Primitive sources for the routed dock's swap/group state also rerun
    // after tab-presence, measurement and responsive changes, but avoid every unrelated resize.
    // Explicit window actions (minimize / maximize) may intentionally make the currently routed
    // pane disappear and move the route as part of the same gesture. Keep the route-reveal watcher
    // from undoing the layout half of that atomic transition before Vue Router finishes the route
    // half. The extra tick keeps the guard active while route-driven watchers flush.
    let routeRevealSuspensions = 0
    async function withRouteRevealSuspended(action) {
        routeRevealSuspensions += 1
        try {
            return await action()
        } finally {
            await nextTick()
            routeRevealSuspensions -= 1
        }
    }

    watch([
        routeActiveTabId,
        () => intention.value.assignment[routeActiveTabId.value] || null,
        () => {
            const dock = intention.value.assignment[routeActiveTabId.value]
            return !!dock && intention.value.collapsed.includes(dock)
        },
        () => intention.value.maximized,
        () => gutterEdgeForTabAction(routeActiveTabId.value, 'swap'),
        () => {
            const id = routeActiveTabId.value
            const region = render.value.regions.find(
                (r) => r.slots.some((s) => s.tabs.some((t) => t.id === id))
            )
            return region ? groupKeyOf(region.slots) : null
        },
    ], ([id, dock, collapsed, maximized, swapEdge, groupKey]) => {
        if (!id) return
        if (routeRevealSuspensions > 0) return
        // Demaximize when focusing a tab outside the maximized region — covers any programmatic focus
        // into a tab the maximized region hides (View Agent from a workflow in a maximized dock,
        // opening a file in Files from a maximized Git, …). Must run BEFORE the dock guard below,
        // which early-returns for any center tab (no assignment). No assignment ⇒ the center.
        // Minimized docks / overlays reveal themselves below / via the route — only maximize is left.
        if (maximized?.length && !maximized.includes(dock || 'center')) {
            restoreMaximized()
        }
        if (!dock || !DOCKS.includes(dock)) return
        // While this dock IS the maximized region, freeze its underlying rail state: don't un-minimize
        // or swap its column in just because the route points into it. This is what lets a dock that was
        // maximized straight from the rail drop back to the rail on restore (minimized → minimized,
        // swap → re-swap). rememberActive still runs below so the region keeps its active tab.
        if (!(maximized || []).includes(dock)) {
            if (collapsed) restore(dock)
            if (swapEdge) swapSide(swapEdge)
        }
        if (groupKey) rememberActive(groupKey, id)
    }, { immediate: true })

    // Auto-restore if the maximized region lost every tab (e.g. an optional tab emptied while
    // maximized) — not supposed to happen, but never leave the user stuck on an empty maximized panel.
    watch(maximizedRegion, (region) => {
        if (region && region.slots.every((s) => !s.tabs.length)) restoreMaximized()
    })

    return {
        width, height, measured,
        render, orderedTabs, isDockingActive, dockingRendered,
        regionActiveTabId, dockActiveTabId, dockOf, tabById, groupKeyOf,
        targetKeyForTab, isToolPanelVisible, overlayEdgeForTab,
        openOverlayEdge, routeActiveTabId,
        place, moveTab, minimize, restore, swapSide, rememberActive,
        setActiveResize, setResizeFraction,
        maximize, restoreMaximized, maximizedRegion, isCenterMaximized, centerVisible,
        withRouteRevealSuspended,
    }
}
