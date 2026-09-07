<script setup>
// Dumb render of the dockable layout from the resolver's renderDescription. No layout math
// here. The CENTER is provided by the parent as the default slot (the existing wa-tab-group);
// this component only positions it and adds dock regions / gutters / overlay around it.
//
// Layout-only interactions (place, minimize, restore, gutter actions, overlay) are dispatched
// straight to the composable's actions. Tab selection is routed, so it bubbles up as
// 'select-tab' for the parent (which owns the route).
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import DockRegion from './DockRegion.vue'
import DockGutter from './DockGutter.vue'
import LayoutOverlay from './LayoutOverlay.vue'
import { useFramePoolStore } from '../../../stores/framePool'
import { dropZoneAt, layoutDropZones } from '../../../utils/layoutDrag'
import { DOCK_ICONS, DOCK_LABELS } from './dockMeta'

const props = defineProps({
    layout: { type: Object, required: true },       // the useSessionLayout() return
    tabHref: { type: Function, required: true },
    registerTarget: { type: Function, required: true },
    unregisterTarget: { type: Function, required: true },
})
const emit = defineEmits(['select-tab', 'tab-activate', 'minimize', 'maximize', 'restore-maximized', 'focus-pane', 'overlay-activate', 'overlay-dismiss', 'tab-drag-start', 'tab-drop'])

// props.layout is the useSessionLayout() return — a bag of refs/functions. Refs accessed
// through a prop object are NOT auto-unwrapped, so read them via .value here.
const docking = computed(() => props.layout.dockingRendered.value)
const render = computed(() => props.layout.render.value)
const openOverlayEdge = computed(() => props.layout.openOverlayEdge.value)

// The route owner — the one tab whose content the URL points at, across all regions. Each DockRegion
// compares it to its own shown tab: the region that owns it keeps a full-opacity tab bar, the others
// dim theirs, so the active region stands out among the several that can show content at once.
const focusedTabId = computed(() => props.layout.routeActiveTabId.value)

const centerRegion = computed(() => render.value.regions.find((r) => r.kind === 'center'))
// Normal dock regions only — excludes the synthetic 'maximized' region, which is handled apart.
const dockRegions = computed(() => render.value.regions.filter((r) => r.kind !== 'center' && r.kind !== 'maximized'))

// Maximized mode: the resolver returns a single region of kind 'maximized'. A maximized DOCK renders
// as that one full-bleed DockRegion (center + everything else hidden); a maximized CENTER instead just
// fills the center slot (the center is the default slot — no teleport), hiding all docks.
const maximizedRegion = computed(() => render.value.regions.find((r) => r.kind === 'maximized') || null)
const isCenterMaximized = computed(() => !!maximizedRegion.value?.slots.some((s) => s.dockId === 'center'))
const maximizedDockRegion = computed(() => (maximizedRegion.value && !isCenterMaximized.value) ? maximizedRegion.value : null)
const centerVisible = computed(() => !maximizedDockRegion.value)

// Context classes on the root so descendant CSS can react to the mode and to what sits on the
// edges. Used to inset gutter icons away from the sidebar-reopen toggle when closed, and to
// modulate the chat composer's toggle-clearance padding: a bottom dock/gutter lifts the composer
// above the toggle, a left column pushes it clear, a left gutter clears it only partly.
const rootClasses = computed(() => {
    const d = render.value
    return {
        [`mode-${d.mode}`]: true,
        'has-left-gutter': d.gutters.some((g) => g.edge === 'left'),
        'has-left-col': d.regions.some((r) => r.kind === 'col-left'),
        'has-bottom-gutter': d.gutters.some((g) => g.edge === 'bottom'),
        'has-bottom-region': d.regions.some((r) => r.kind === 'bottom'),
    }
})

const centerStyle = computed(() => {
    // When the center is maximized it fills the whole area (the resolver's region rect is the viewport).
    const r = (isCenterMaximized.value && maximizedRegion.value) || (docking.value && centerRegion.value)
    if (!r) return {}
    return { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` }
})

const overlay = computed(() =>
    render.value.overlays.find((o) => o.edge === openOverlayEdge.value) || null
)
// The overlay shows the active (route) tab — it's open precisely because that tab is in overlay
// mode (see useSessionLayout: openOverlayEdge is derived from the route).
const overlayActive = computed(() => overlay.value ? props.layout.routeActiveTabId.value : null)

function onGutterAction({ edge, dockId, tabId, action }) {
    // Double-clicking a gutter chip maximizes its dock, focusing the double-clicked tab — same path as a
    // dock-region's maximize button. The watch in useSessionLayout keeps the dock's rail state frozen
    // while maximized, so restore drops it back to the rail (minimized → minimized, swap → re-swap).
    if (action === 'maximize') {
        emit('maximize', [dockId], tabId)
        return
    }
    // An overlay chip's second rapid click: force the peek open (rather than toggling it shut) so a
    // double-click leaves it open. Single clicks fall through to the toggle branch below.
    if (action === 'overlay-open') {
        emit('overlay-activate', tabId)
        return
    }
    // Clicking a gutter chip to swap a hidden column in or restore a minimized dock is an explicit
    // activation of that tab — like a dock-tab header click — so besides claiming the route (select-tab)
    // we emit tab-activate to focus the panel's content (the overlay branch focuses via overlay-activate).
    if (action === 'swap') {
        props.layout.swapSide(edge)
        emit('select-tab', tabId)
        emit('tab-activate', tabId)
    } else if (action === 'restore') {
        props.layout.restore(dockId)
        emit('select-tab', tabId)
        emit('tab-activate', tabId)
    } else {
        // The overlay is derived from the route: opening it = navigating to the tab ('activate');
        // re-clicking the tab already shown = dismissing it (navigate back to the prior tab).
        if (props.layout.openOverlayEdge.value === edge && props.layout.routeActiveTabId.value === tabId) {
            emit('overlay-dismiss')
        } else {
            emit('overlay-activate', tabId)
        }
    }
}
function onOverlaySelect(tabId) {
    if (tabId === overlayActive.value) return
    emit('overlay-activate', tabId)
}
// Explicit close (backdrop / close button): dismiss navigates back to the pre-open active tab,
// which drops the route out of overlay mode and closes the overlay.
function onOverlayClose() {
    emit('overlay-dismiss')
}

// ---- Resize splitters. The resolver emits every draggable boundary with its geometry + math params
// (axis / origin / extent / from / configKey): kind 'dock' (a side column's width or the bottom's
// height vs the center) and kind 'sib' (between two siblings of a split column, or the two bottom
// siblings). One generic handler covers both — we draw an invisible hit strip over each boundary and
// write the dragged fraction to the store, which the resolver re-clamps on the next render (px mins
// for docks, siblingMaxFrac for sibs). The fraction is part of the (ephemeral) layout intention —
// persistence is deferred. Window-level listeners so the drag survives the re-render that repositions
// the handle. ----
const sessionLayoutEl = ref(null)
const resizeSplitters = computed(() => render.value.splitters || [])
const draggingId = ref(null)

function splitterStyle(s) {
    // Only the resolver position + the long dimension. The thin dimension and the on-divider
    // centering live in CSS (--resize-grab + translate -50%), so there's no px magic here and it
    // adapts to the theme's divider thickness.
    return s.axis === 'v'
        ? { left: `${s.x}px`, top: `${s.y}px`, height: `${s.h}px` }
        : { left: `${s.x}px`, top: `${s.y}px`, width: `${s.w}px` }
}

const framePool = useFramePoolStore()
let drag = null
function onSplitterMove(event) {
    if (!drag || !sessionLayoutEl.value) return
    if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > SPLITTER_CLICK_SLOP) {
        drag.moved = true
    }
    const rect = sessionLayoutEl.value.getBoundingClientRect()
    const pointer = drag.axis === 'h' ? event.clientY - rect.top : event.clientX - rect.left
    const frac = (drag.from === 'end' ? drag.origin - pointer : pointer - drag.origin) / drag.extent
    props.layout.setResizeFraction(drag.configKey, frac)
}
function endDrag() {
    // Guard the pool release: endDrag also runs from onBeforeUnmount even when
    // no drag started (and again after one ends). An unguarded end would
    // decrement some OTHER live drag's depth. Neutralize pooled-iframe
    // pointer-events only for the duration of a real drag.
    const ended = drag
    drag = null
    draggingId.value = null
    window.removeEventListener('pointermove', onSplitterMove)
    window.removeEventListener('pointerup', endDrag)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    if (ended) framePool.endDividerDrag()
    // A press-and-release that never moved is a click on the splitter; a quick second one maximizes
    // the zone it resizes. Runs after the cleanup so the maximize re-render starts from a clean state.
    if (ended && !ended.moved) onSplitterClick(ended)
}
function onSplitterDown(event, s) {
    event.preventDefault()
    // Only the side-column docks set activeResize (the dragged side wins the squeeze when both show);
    // sibling and bottom-height splitters leave it untouched.
    if (s.configKey === 'leftColFrac') props.layout.setActiveResize('left')
    else if (s.configKey === 'rightColFrac') props.layout.setActiveResize('right')
    drag = {
        axis: s.axis, origin: s.axis === 'h' ? s.originY : s.originX, from: s.from || 'start',
        extent: s.extent, configKey: s.configKey,
        // Double-click-to-maximize bookkeeping (see onSplitterClick).
        id: s.id, docks: s.docks || [], startX: event.clientX, startY: event.clientY, moved: false,
    }
    draggingId.value = s.id
    framePool.beginDividerDrag()
    window.addEventListener('pointermove', onSplitterMove)
    window.addEventListener('pointerup', endDrag)
    document.body.style.userSelect = 'none'
    document.body.style.cursor = s.axis === 'v' ? 'col-resize' : 'row-resize'
}
onBeforeUnmount(endDrag)

// ---- Double-click a splitter = maximize the dock zone it resizes. preventDefault() on the
// splitter's pointerdown suppresses the compatibility mouse events, so no native dblclick fires —
// we detect it ourselves: two no-move press-releases on the SAME splitter within the delay. Keep
// the delay in sync with the rest of the layout's double-click feel (DockGutter.vue). ----
const SPLITTER_CLICK_SLOP = 4 // px of pointer travel beyond which a press counts as a drag
const SPLITTER_DBLCLICK_DELAY = 250 // ms
let lastSplitterClick = { id: null, at: 0 }

function onSplitterClick(ended) {
    const now = Date.now()
    if (lastSplitterClick.id === ended.id && now - lastSplitterClick.at < SPLITTER_DBLCLICK_DELAY) {
        lastSplitterClick = { id: null, at: 0 }
        maximizeSplitterZone(ended.docks)
        return
    }
    lastSplitterClick = { id: ended.id, at: now }
}

// Maximize the zone adjacent to a splitter (its shown docks, from the resolver). If the route's
// active tab lives in one of them, maximize the region holding it (focusing that tab); otherwise
// maximize the whole zone as one merged region, focusing the group's remembered active tab —
// regionActiveTabId on the synthetic merged slots resolves memory (groupKeyOf) then first content.
function maximizeSplitterZone(zoneDocks) {
    if (!zoneDocks.length) return
    const regions = render.value.regions.filter((r) => r.slots.some((s) => zoneDocks.includes(s.dockId)))
    if (!regions.length) return
    const routed = props.layout.routeActiveTabId.value
    const routedDock = routed ? props.layout.dockOf(routed) : null
    const target = zoneDocks.includes(routedDock)
        ? regions.find((r) => r.slots.some((s) => s.dockId === routedDock))
        : null
    if (target) {
        emit('maximize', target.slots.map((s) => s.dockId), routed)
        return
    }
    const slots = regions.flatMap((r) => r.slots)
    emit('maximize', slots.map((s) => s.dockId), props.layout.regionActiveTabId({ slots }))
}

// ---- Tab drag and drop -------------------------------------------------------
// Pointer-based rather than native HTML DnD: one implementation covers mouse, pen and touch, and
// the gesture survives its source disappearing when an overlay/maximized region closes. Mouse starts
// after a short movement; touch/pen starts after a stationary long press so normal taps stay native.
const MOUSE_DRAG_DISTANCE = 5
const TOUCH_MOVE_TOLERANCE = 9
const TOUCH_LONG_PRESS_MS = 425

const tabDrag = ref(null)
const activeDrop = ref(null)
let pendingTabDrag = null
let suppressClickUntil = 0
let previousUserSelect = ''

const tabDropZones = computed(() => layoutDropZones(
    props.layout.width.value,
    props.layout.height.value,
    props.layout.render.value,
))

function tabIdFromHandle(node) {
    if (!(node instanceof Element)) return null
    const gutterChip = node.closest('.g-chip[data-layout-tab-id]')
    if (gutterChip) return gutterChip.dataset.layoutTabId
    const tab = node.closest('wa-tab[slot="nav"]')
    if (!tab) return null
    const group = tab.parentElement
    if (!group?.matches?.('.session-tabs, .dock-tabnav, .overlay-tabnav')) return null
    return tab.panel || tab.getAttribute('panel')
}

function eventHitsDragControl(event) {
    return event.composedPath().some((node) => node instanceof Element && node.matches(
        '.placement-menu, .layout-nav-cluster, .dock-winbtn, .overlay-close, wa-button, wa-dropdown, button, input, textarea, select, [contenteditable="true"]'
    ))
}

// The mobile tab strip has no layout: every placement affordance is hidden there (placement arrows,
// nav cluster, layout commands) and the resolver folds every dock back into one flat strip. Dragging
// must follow — the only reachable destination would be 'center', which silently CLEARS the dragged
// tab's dock assignment for the wide layout the user cannot see. Reordering is refused too: the tab
// order is one global list filtered per dock, so moving a tab past a tab of another dock is an
// operation the model cannot represent, and its wide-screen effect is unpredictable from here.
const tabDragDisabled = computed(() => render.value.mode === 'tabs')

function sourceTabForEvent(event) {
    if (tabDragDisabled.value || eventHitsDragControl(event)) return null
    const tabId = tabIdFromHandle(event.target)
    const tab = tabId && props.layout.tabById(tabId)
    return tab && !tab.fixedCenter ? tab : null
}

function addTabPointerListeners() {
    window.addEventListener('pointermove', onTabPointerMove, { passive: false })
    window.addEventListener('pointerup', onTabPointerUp)
    window.addEventListener('pointercancel', cancelTabPointer)
}
function removeTabPointerListeners() {
    window.removeEventListener('pointermove', onTabPointerMove)
    window.removeEventListener('pointerup', onTabPointerUp)
    window.removeEventListener('pointercancel', cancelTabPointer)
}
function clearLongPress() {
    if (pendingTabDrag?.timer) clearTimeout(pendingTabDrag.timer)
    if (pendingTabDrag) pendingTabDrag.timer = null
}

// ---- Touch pan on the tab strips ----
// `touch-action: none` on the tab links reserves the long press for dragging, but it also kills the
// browser's native horizontal pan on the very surface a finger naturally lands on (the icon/name) —
// and the strip's own padding is too thin to aim for. So the pan is reimplemented: a touch/pen
// gesture that moves mostly horizontally BEFORE the long press arms the drag pans the strip's
// scroller by hand (wa-tab-group's shadow `.nav`, the same element WA's chevrons drive), with a
// small fling on release. Vertical movement keeps cancelling the pending drag as before. Mouse is
// untouched: it arms the drag on any direction and already pans via TabBar's wheel handler.
//
// The pan is NOT tied to the drag: a tab the layout refuses to drag still wears `touch-action:
// none`, so without a pan of its own its icon/name is a dead zone in an otherwise pannable strip.
// That covers the fixed Chat tab and every tab created on the fly outside the resolver's model
// (the `agent-*` subagent tabs), so those arm a pan-only gesture — see `canPanOnly`.
const PAN_FLING_DECAY = 0.94        // per-16ms multiplier on the fling velocity
const PAN_FLING_MIN_VELOCITY = 0.02 // px/ms below which the fling stops

let tabPan = null
let panInertiaFrame = 0

// The scrollable nav of the tab strip the gesture started in. Gutter chips resolve to null
// (their rails don't scroll), which simply keeps today's cancel behaviour for them.
function navScrollerForEvent(event) {
    const tab = event.target instanceof Element ? event.target.closest('wa-tab[slot="nav"]') : null
    const group = tab?.parentElement
    if (!group?.matches?.('.session-tabs, .dock-tabnav, .overlay-tabnav')) return null
    return group.shadowRoot?.querySelector('.nav') || null
}

// Whether the pan is ours to run. `touch-action: none` is set on `.session-tab-link` only, so the
// browser still pans the strip natively from everywhere else (the tab's own padding, the placement
// arrow, a subagent tab's close icon). Panning by hand there too would scroll twice as fast.
function panIsOurs(event) {
    return event.target instanceof Element && !!event.target.closest('.session-tab-link')
}

// A pointer press on a tab the layout will not drag: arm the gesture for panning only, so the
// strip still follows the finger there. Mouse is excluded (it never panned by drag and has the
// wheel), and so is the mobile strip, where `.tab-drag-disabled` hands the links back to the
// browser's own pan.
function canPanOnly(event, scroller) {
    if (tabDragDisabled.value || event.pointerType === 'mouse') return false
    return !!scroller && panIsOurs(event) && !eventHitsDragControl(event)
}

function stopPanInertia() {
    if (panInertiaFrame) cancelAnimationFrame(panInertiaFrame)
    panInertiaFrame = 0
}

function startPanInertia(scroller, velocity) {
    let v = -velocity // finger velocity → content moves the opposite way
    let last = performance.now()
    const step = (now) => {
        panInertiaFrame = 0
        const dt = now - last
        last = now
        const before = scroller.scrollLeft
        scroller.scrollLeft = before + v * dt
        v *= PAN_FLING_DECAY ** (dt / 16)
        // Stop when the energy is spent or the scroller hit an edge (scrollLeft self-clamps).
        if (Math.abs(v) < PAN_FLING_MIN_VELOCITY || scroller.scrollLeft === before) return
        panInertiaFrame = requestAnimationFrame(step)
    }
    panInertiaFrame = requestAnimationFrame(step)
}

// Promote the pending press into a pan: from here the gesture only scrolls, never drags.
function startTabPan(event) {
    const { pointerId, scroller } = pendingTabDrag
    clearLongPress()
    pendingTabDrag = null
    tabPan = {
        pointerId,
        scroller,
        lastX: event.clientX,
        lastT: event.timeStamp,
        velocity: 0,
    }
}

function onTabPanMove(event) {
    const dx = event.clientX - tabPan.lastX
    tabPan.scroller.scrollLeft -= dx
    const dt = event.timeStamp - tabPan.lastT
    // Exponential smoothing over the recent moves, so the release picks up the gesture's
    // current speed rather than one noisy last sample.
    if (dt > 0) tabPan.velocity = 0.8 * (dx / dt) + 0.2 * tabPan.velocity
    tabPan.lastX = event.clientX
    tabPan.lastT = event.timeStamp
    event.preventDefault()
}

function finishTabPan({ fling }) {
    const pan = tabPan
    tabPan = null
    removeTabPointerListeners()
    // The finger crossed the move tolerance to get here: the release must not also click the
    // tab under it.
    suppressClickUntil = Date.now() + 500
    if (fling && Math.abs(pan.velocity) >= PAN_FLING_MIN_VELOCITY) startPanInertia(pan.scroller, pan.velocity)
}

function onTabPointerDown(event) {
    if (pendingTabDrag || tabDrag.value || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
    if (tabPan) return
    stopPanInertia() // a new touch grabs a flinging strip, as native scrolling would
    const tab = sourceTabForEvent(event)
    const scroller = navScrollerForEvent(event)
    if (!tab && !canPanOnly(event, scroller)) return
    pendingTabDrag = {
        pointerId: event.pointerId,
        pointerType: event.pointerType,
        x: event.clientX,
        y: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        tab,
        scroller,
        // Set once at press time: the pan reads the surface the finger LANDED on, not the one it
        // has moved over since.
        panOwned: panIsOurs(event),
        timer: null,
    }
    addTabPointerListeners()
    // Only a draggable tab reserves the long press; a pan-only press keeps its native long press
    // (text callout, link context menu) since nothing else claims it.
    if (tab && event.pointerType !== 'mouse') {
        pendingTabDrag.timer = setTimeout(() => startTabDrag(pendingTabDrag.lastX, pendingTabDrag.lastY), TOUCH_LONG_PRESS_MS)
    }
}

function startTabDrag(clientX, clientY) {
    if (!pendingTabDrag?.tab || tabDrag.value) return
    clearLongPress()
    const { pointerId, pointerType, tab } = pendingTabDrag
    tabDrag.value = {
        pointerId,
        pointerType,
        tab,
        clientX,
        clientY,
        startX: clientX,
        startY: clientY,
        moved: pointerType === 'mouse',
    }
    previousUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none'
    document.body.classList.add('layout-tab-dragging')
    framePool.beginDividerDrag()
    window.addEventListener('keydown', onTabDragKeydown)
    emit('tab-drag-start', tab.id)
    if (tabDrag.value.moved) updateTabDrop(clientX, clientY)
}

function tabHeaderAt(clientX, clientY) {
    for (const node of document.elementsFromPoint(clientX, clientY)) {
        if (!(node instanceof Element)) continue
        const gutterChip = node.closest?.('.g-chip[data-layout-tab-id]')
        if (gutterChip) {
            const targetTabId = gutterChip.dataset.layoutTabId
            const targetTab = props.layout.tabById(targetTabId)
            const gutter = gutterChip.closest('.dock-gutter')
            if (!targetTab || targetTab.fixedCenter || !gutter) continue
            const edge = ['left', 'right', 'bottom'].find((name) => gutter.classList.contains(name))
            const rect = gutterChip.getBoundingClientRect()
            const rootRect = sessionLayoutEl.value.getBoundingClientRect()
            const verticalRail = edge !== 'bottom'
            // Side-gutter groups are rotated -90deg: DOM "before" is toward the visual bottom,
            // while "after" is toward the visual top. The bottom gutter keeps normal left/right order.
            const position = verticalRail
                ? (clientY < rect.top + rect.height / 2 ? 'after' : 'before')
                : (clientX < rect.left + rect.width / 2 ? 'before' : 'after')
            return {
                dockId: gutterChip.dataset.layoutDockId || props.layout.dockOf(targetTabId),
                targetTabId,
                position,
                activate: false,
                restoreDestination: false,
                indicator: verticalRail
                    ? {
                        axis: 'h',
                        x: rect.left - rootRect.left + 3,
                        y: (position === 'before' ? rect.bottom : rect.top) - rootRect.top,
                        w: Math.max(18, rect.width - 6),
                    }
                    : {
                        axis: 'v',
                        x: (position === 'before' ? rect.left : rect.right) - rootRect.left,
                        y: rect.top - rootRect.top + 3,
                        h: Math.max(18, rect.height - 6),
                    },
            }
        }
        const tabEl = node.closest?.('wa-tab[slot="nav"]')
        if (!tabEl) continue
        const group = tabEl.parentElement
        if (!group?.matches?.('.session-tabs, .dock-tabnav, .overlay-tabnav')) continue
        const targetTabId = tabEl.panel || tabEl.getAttribute('panel')
        const targetTab = props.layout.tabById(targetTabId)
        if (!targetTab || targetTab.fixedCenter) continue
        const rect = tabEl.getBoundingClientRect()
        const inCenter = group.matches('.session-tabs') || !props.layout.dockingRendered.value
        const dockId = inCenter ? 'center' : props.layout.dockOf(targetTabId)
        const position = clientX < rect.left + rect.width / 2 ? 'before' : 'after'
        const rootRect = sessionLayoutEl.value.getBoundingClientRect()
        return {
            dockId,
            targetTabId,
            position,
            indicator: {
                axis: 'v',
                x: (position === 'before' ? rect.left : rect.right) - rootRect.left,
                y: rect.top - rootRect.top + 3,
                h: Math.max(18, rect.height - 6),
            },
        }
    }
    return null
}

function updateTabDrop(clientX, clientY) {
    if (!tabDrag.value || !sessionLayoutEl.value) return
    tabDrag.value.clientX = clientX
    tabDrag.value.clientY = clientY
    const header = tabHeaderAt(clientX, clientY)
    if (header) {
        activeDrop.value = header
        return
    }
    const rect = sessionLayoutEl.value.getBoundingClientRect()
    const zone = dropZoneAt(tabDropZones.value, clientX - rect.left, clientY - rect.top)
    activeDrop.value = zone ? { dockId: zone.dockId, targetTabId: null, position: 'after' } : null
}

function onTabPointerMove(event) {
    if (tabPan) {
        if (event.pointerId === tabPan.pointerId) onTabPanMove(event)
        return
    }
    if (!pendingTabDrag || event.pointerId !== pendingTabDrag.pointerId) return
    pendingTabDrag.lastX = event.clientX
    pendingTabDrag.lastY = event.clientY
    if (!tabDrag.value) {
        const dx = event.clientX - pendingTabDrag.x
        const dy = event.clientY - pendingTabDrag.y
        const distance = Math.hypot(dx, dy)
        if (pendingTabDrag.pointerType === 'mouse') {
            if (distance >= MOUSE_DRAG_DISTANCE) startTabDrag(event.clientX, event.clientY)
        } else if (distance > TOUCH_MOVE_TOLERANCE) {
            // Early movement decides the touch gesture: mostly horizontal over a scrollable
            // strip → pan it; anything else → not a drag, give the pointer up (which also hands
            // a press that started outside `.session-tab-link` back to the browser's own pan).
            const scroller = pendingTabDrag.scroller
            if (Math.abs(dx) > Math.abs(dy) && pendingTabDrag.panOwned && scroller && scroller.scrollWidth > scroller.clientWidth) {
                startTabPan(event)
            } else {
                cancelTabPointer()
            }
            return
        }
    }
    if (!tabDrag.value) return
    event.preventDefault()
    if (!tabDrag.value.moved && Math.hypot(
        event.clientX - tabDrag.value.startX,
        event.clientY - tabDrag.value.startY,
    ) > 2) tabDrag.value.moved = true
    if (!tabDrag.value.moved) return
    updateTabDrop(event.clientX, event.clientY)
}

function finishTabDrag() {
    const wasDragging = !!tabDrag.value
    clearLongPress()
    pendingTabDrag = null
    tabDrag.value = null
    activeDrop.value = null
    removeTabPointerListeners()
    if (wasDragging) {
        suppressClickUntil = Date.now() + 500
        document.body.style.userSelect = previousUserSelect
        document.body.classList.remove('layout-tab-dragging')
        framePool.endDividerDrag()
    }
    window.removeEventListener('keydown', onTabDragKeydown)
}

function onTabPointerUp(event) {
    if (tabPan) {
        if (event.pointerId === tabPan.pointerId) finishTabPan({ fling: true })
        return
    }
    if (!pendingTabDrag || event.pointerId !== pendingTabDrag.pointerId) return
    const dragState = tabDrag.value
    const drop = activeDrop.value
    if (dragState && drop) {
        props.layout.moveTab(dragState.tab.id, drop.dockId, {
            targetTabId: drop.targetTabId,
            position: drop.position,
            restoreDestination: drop.restoreDestination,
        })
        emit('tab-drop', dragState.tab.id, { activate: drop.activate !== false })
    }
    finishTabDrag()
}
function cancelTabPointer(event) {
    if (tabPan) {
        if (event?.pointerId == null || event.pointerId === tabPan.pointerId) finishTabPan({ fling: false })
        return
    }
    if (event?.pointerId != null && pendingTabDrag && event.pointerId !== pendingTabDrag.pointerId) return
    finishTabDrag()
}
function onTabDragKeydown(event) {
    if (event.key !== 'Escape') return
    event.preventDefault()
    cancelTabPointer()
}
// A gesture that outlives the mode it started in: the area can shrink into the tab strip mid-drag
// (window resize, or the measured area briefly collapsing). Drop it rather than let it land.
watch(tabDragDisabled, (disabled) => { if (disabled) cancelTabPointer() })
function onCapturedClick(event) {
    if (Date.now() >= suppressClickUntil) return
    event.preventDefault()
    event.stopImmediatePropagation()
}
function onCapturedContextMenu(event) {
    // A pan-only press (`tab` null) reserves nothing, so its native long press stays available.
    if (tabDrag.value || tabPan || (pendingTabDrag?.tab && pendingTabDrag.pointerType !== 'mouse')) event.preventDefault()
}
function onNativeDragStart(event) {
    if (sourceTabForEvent(event)) event.preventDefault()
}

const dragGhostStyle = computed(() => tabDrag.value ? {
    left: `${tabDrag.value.clientX + 14}px`,
    top: `${tabDrag.value.clientY + 14}px`,
} : {})
function zoneStyle(zone) {
    return { left: `${zone.x}px`, top: `${zone.y}px`, width: `${zone.w}px`, height: `${zone.h}px` }
}
function insertionStyle(indicator) {
    return {
        left: `${indicator.x}px`,
        top: `${indicator.y}px`,
        ...(indicator.axis === 'h' ? { width: `${indicator.w}px` } : { height: `${indicator.h}px` }),
    }
}

onBeforeUnmount(() => {
    cancelTabPointer()
    stopPanInertia()
})
</script>

<template>
    <div
        ref="sessionLayoutEl"
        class="session-layout"
        :class="[rootClasses, { resizing: draggingId !== null, 'tab-drag-active': !!tabDrag, 'tab-drag-disabled': tabDragDisabled }]"
        @pointerdown.capture="onTabPointerDown"
        @click.capture="onCapturedClick"
        @contextmenu.capture="onCapturedContextMenu"
        @dragstart.capture="onNativeDragStart"
    >
        <div class="center-slot" :style="centerStyle" v-show="centerVisible">
            <slot></slot>
        </div>

        <!-- A maximized DOCK: the single full-bleed region with a restore button; nothing else. -->
        <DockRegion
            v-if="maximizedDockRegion"
            :region="maximizedDockRegion"
            :active-tab-id="layout.regionActiveTabId(maximizedDockRegion)"
            :focused-tab-id="focusedTabId"
            :tab-href="tabHref"
            :maximized="true"
            :register-target="registerTarget"
            :unregister-target="unregisterTarget"
            @select="(id) => emit('select-tab', id)"
            @tab-activate="(id) => emit('tab-activate', id)"
            @pane-focus="(id) => emit('focus-pane', id)"
            @restore="emit('restore-maximized')"
            @place="(id, dest) => layout.place(id, dest)"
        />

        <!-- Normal dockable layout — skipped while maximizing (a maximized center shows only the
             center slot above; a maximized dock shows only the region above). -->
        <template v-else-if="docking && !maximizedRegion">
            <DockRegion
                v-for="r in dockRegions"
                :key="r.id"
                :region="r"
                :active-tab-id="layout.regionActiveTabId(r)"
                :focused-tab-id="focusedTabId"
                :tab-href="tabHref"
                :register-target="registerTarget"
                :unregister-target="unregisterTarget"
                @select="(id) => emit('select-tab', id)"
                @tab-activate="(id) => emit('tab-activate', id)"
                @pane-focus="(id) => emit('focus-pane', id)"
                @minimize="(dockIds) => emit('minimize', dockIds)"
                @maximize="(dockIds, tab) => emit('maximize', dockIds, tab)"
                @place="(id, dest) => layout.place(id, dest)"
            />

            <DockGutter
                v-for="g in render.gutters"
                :key="g.edge"
                :gutter="g"
                :open-overlay-edge="openOverlayEdge"
                :resolve-active-tab="(item) => layout.dockActiveTabId(item.dockId, item.tabs)"
                :tab-href="tabHref"
                @action="onGutterAction"
            />

            <div
                v-for="s in resizeSplitters"
                :key="s.id"
                class="layout-splitter"
                :class="[`axis-${s.axis}`, `kind-${s.kind}`, { dragging: draggingId === s.id }]"
                :style="splitterStyle(s)"
                title="Drag to resize · double-click to maximize"
                @pointerdown="onSplitterDown($event, s)"
            >
                <wa-icon name="grip-lines-vertical" auto-width class="splitter-grip"></wa-icon>
            </div>

            <LayoutOverlay
                v-if="overlay"
                :overlay="overlay"
                :active-tab-id="overlayActive"
                :tab-href="tabHref"
                :dock-of="layout.dockOf"
                :register-target="registerTarget"
                :unregister-target="unregisterTarget"
                @select="onOverlaySelect"
                @close="onOverlayClose"
                @place="(id, dest) => layout.place(id, dest)"
            />
        </template>

        <div v-if="tabDrag" class="tab-drop-layer" aria-hidden="true">
            <div
                v-for="zone in tabDropZones"
                :key="zone.dockId"
                class="tab-drop-zone"
                :class="{ active: activeDrop?.dockId === zone.dockId }"
                :style="zoneStyle(zone)"
            >
                <div class="tab-drop-card">
                    <wa-icon :src="DOCK_ICONS[zone.dockId]"></wa-icon>
                    <span>{{ DOCK_LABELS[zone.dockId] }}</span>
                </div>
            </div>
            <div
                v-if="activeDrop?.indicator"
                class="tab-drop-insertion"
                :class="`axis-${activeDrop.indicator.axis}`"
                :style="insertionStyle(activeDrop.indicator)"
            ></div>
        </div>

        <!-- A real WA tab provides the exact normal-tab rendering for the drag ghost, regardless of
             whether the source came from an overlay or a minimized gutter. -->
        <wa-tab v-if="tabDrag" class="layout-tab-drag-ghost" :style="dragGhostStyle" active>
            <wa-icon v-if="tabDrag.tab.icon" :name="tabDrag.tab.icon"></wa-icon>
            <span>{{ tabDrag.tab.label }}</span>
        </wa-tab>
    </div>
</template>

<style scoped>
.session-layout {
    position: relative;
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
}
/* While dragging a resize splitter, neutralize iframe panes (HTML/PDF previews): an iframe is a
   separate browsing context that would otherwise capture pointermove/up as soon as the pointer
   crosses into it, freezing the window-level drag. pointer-events:none lets the events fall through
   to the parent document so the drag keeps tracking. */
.session-layout.resizing :deep(iframe) {
    pointer-events: none;
}
.session-layout.tab-drag-active :deep(iframe) {
    pointer-events: none;
}
/* A long press on the icon/name is reserved for tab dragging, and the horizontal pan is
   reimplemented there (see "Touch pan on the tab strips" above — including for the tabs that
   cannot be dragged). The surrounding tab strip still owns its native horizontal touch pan through
   its empty space and its controls (placement arrow, close icon). */
.session-layout :deep(.session-tab-link),
.session-layout :deep(.g-chip) {
    touch-action: none;
    -webkit-user-drag: none;
}
/* No drag in the mobile tab strip (see tabDragDisabled) — nothing reserves the touch gesture
   anymore, so hand the links back to the browser: a finger on a tab's icon/name pans the strip
   natively (overflow-x on wa-tab-group's .nav), with inertia. Without this, `none` above would
   leave the strip with NEITHER drag NOR touch pan. */
.session-layout.tab-drag-disabled :deep(.session-tab-link) {
    touch-action: auto;
}
.tab-drop-layer {
    position: absolute;
    inset: 0;
    z-index: 40;
    pointer-events: none;
    background: color-mix(in srgb, var(--wa-color-surface-default, #fff) 18%, transparent);
}
.tab-drop-zone {
    position: absolute;
    padding: 5px;
    display: flex;
    align-items: center;
    justify-content: center;
}
.tab-drop-card {
    width: 100%;
    height: 100%;
    min-width: 0;
    min-height: 0;
    border: 1px dashed color-mix(in srgb, var(--wa-color-brand-600, #2563eb) 58%, transparent);
    border-radius: var(--wa-border-radius-m, 8px);
    background: color-mix(in srgb, var(--wa-color-brand-500, #3b82f6) 8%, transparent);
    color: var(--wa-color-text-quiet);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s, 0.85rem);
    transition: background-color 100ms ease, border-color 100ms ease, color 100ms ease;
}
.tab-drop-card wa-icon {
    width: 28px;
    height: 28px;
}
.tab-drop-zone.active .tab-drop-card {
    border-style: solid;
    border-color: var(--wa-color-brand-600, #2563eb);
    background: color-mix(in srgb, var(--wa-color-brand-500, #3b82f6) 24%, transparent);
    color: var(--wa-color-text-normal);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--wa-color-brand-600, #2563eb) 35%, transparent);
}
.tab-drop-insertion {
    position: absolute;
    z-index: 2;
    border-radius: 2px;
    background: var(--wa-color-brand-600, #2563eb);
    box-shadow: 0 0 0 1px var(--wa-color-surface-default, #fff);
}
.tab-drop-insertion.axis-v { width: 3px; }
.tab-drop-insertion.axis-h { height: 3px; }
.layout-tab-drag-ghost {
    position: fixed;
    z-index: 10000;
    pointer-events: none;
    opacity: 0.94;
    filter: drop-shadow(0 5px 12px rgba(0, 0, 0, 0.25));
    background: var(--wa-color-surface-default, #fff);
    border-radius: var(--wa-border-radius-s, 4px);
}
.layout-tab-drag-ghost::part(base) {
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    gap: var(--wa-space-2xs);
}
.center-slot {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
}

/* When the session-list sidebar is closed, its reopen toggle floats at the bottom-left of the
   content area; nearby UI must clear it. The base values live once on body.sidebar-closed (App.vue);
   here we refine per dock context. --left-x is the left-edge-only clearance (thin left rail →
   reduced, full left column → none). The composer's -x follows --left-x, but a bottom dock *region*
   zeroes it (the composer is lifted above the toggle); a bottom *gutter* does not (it's thin — the
   composer still overlaps the toggle). The composer, the left gutter and the terminal extra-keys bar
   below consume these — the single place "which dock context needs how much" lives. */
body.sidebar-closed .session-layout.has-left-gutter:not(.has-left-col) {
    --sidebar-toggle-clearance-left-x: 1rem;
}
body.sidebar-closed .session-layout.has-left-col {
    --sidebar-toggle-clearance-left-x: 0rem;
}
body.sidebar-closed .session-layout {
    --sidebar-toggle-clearance-x: var(--sidebar-toggle-clearance-left-x);
    /* Terminal extra-keys bar: no clearance by default (overridden below where it sits bottom-left). */
    --sidebar-toggle-clearance-extra-keys: var(--wa-space-xs);
}
body.sidebar-closed .session-layout.has-bottom-region {
    --sidebar-toggle-clearance-x: 0rem;
}

/* The terminal's extra-keys bar sits where the composer would but is taller, so it clears the toggle
   by --left-x + 1rem — and only when it's actually at the bottom-left: the terminal is the sole or
   bottom-left bottom dock (never bottom-right), or it's in the center and the center reaches the
   bottom-left (no left column, no bottom dock lifting it), or it's maximized full-bleed. The value is
   set on the nearest layout element (the bottom dock region / the center slot / the maximized region);
   it inherits across the panel teleport into the bar, so no :deep into the relocated terminal needed. */
body.sidebar-closed .session-layout:not(.has-left-col) .dock-region[data-rid="bottom"],
body.sidebar-closed .session-layout:not(.has-left-col) .dock-region[data-rid="bottom-left"],
body.sidebar-closed .session-layout .dock-region[data-rid="maximized"] {
    --sidebar-toggle-clearance-extra-keys: calc(var(--sidebar-toggle-clearance-left-x) + 1rem);
}
body.sidebar-closed .session-layout:not(.has-left-col):not(.has-bottom-region) .center-slot {
    --sidebar-toggle-clearance-extra-keys: calc(var(--sidebar-toggle-clearance-left-x) + 1rem);
}

/* The bottom gutter's own start icons sit slightly closer to the toggle than the composer, so they
   keep their own inset (not the shared -x) to stay pixel-stable. */
body.sidebar-closed .session-layout.mode-widescreen:not(.has-left-gutter):not(.has-left-col) :deep(.dock-gutter.bottom .g-group.start) {
    padding-inline-start: 3rem;
}
body.sidebar-closed .session-layout.mode-widescreen.has-left-gutter :deep(.dock-gutter.bottom .g-group.start) {
    padding-inline-start: 1.5rem;
}
body.sidebar-closed .session-layout :deep(.dock-gutter.left .g-group.end) {
    padding-block-end: var(--sidebar-toggle-clearance-y);
}

/* Dock resize handles: an invisible hit strip over a column/center or bottom/center boundary. The
   divider line itself is the adjacent region's border; the ::after is only the grab affordance,
   revealed on hover and during the drag. Below the gutters (12) and overlay (11) so those win. */
.layout-splitter {
    position: absolute;
    z-index: 5;
    touch-action: none;
    /* Grid so the grip child is centered with place-content (no px offsets, no transform tricks). */
    display: grid;
    place-content: center;
    /* The only sizing knob: the grab-strip thickness. The visible line stays var(--divider-size),
       which varies with the theme; the strip is centered on its divider with translate, so
       everything stays aligned whatever the divider thickness is. */
    --resize-grab: 0.6rem;
}
/* Center each strip on its divider with translate (not a px offset); thin dimension from the token. */
.layout-splitter.axis-v { width: var(--resize-grab); translate: -50% 0; cursor: col-resize; }
.layout-splitter.axis-h { height: var(--resize-grab); translate: 0 -50%; cursor: row-resize; }

/* Hover/drag highlight: a line the thickness of the theme's divider, centered in the strip. */
.layout-splitter::after {
    content: '';
    position: absolute;
    background: var(--wa-color-brand-fill-loud);
    opacity: 0;
    transition: opacity 0.12s ease;
}
.layout-splitter.axis-v::after { top: 0; bottom: 0; left: 50%; width: var(--divider-size); translate: -50% 0; }
.layout-splitter.axis-h::after { left: 0; right: 0; top: 50%; height: var(--divider-size); translate: 0 -50%; }
.layout-splitter:hover::after { opacity: 0.5; }
.layout-splitter.dragging::after { opacity: 1; }

/* Touch affordance: a persistent grip centered on the strip (by the strip's grid place-content),
   mirroring the sidebar splitter's .divider-handle — shown only on coarse-pointer devices where
   there's no hover. Scaled up so it overflows the thin strip: a pointerdown on it bubbles to the
   strip and starts the drag, so the grip is a large tap surface. Rotated 90° on the horizontal
   (axis-h) splitters so the grip lines run along the divider. */
.splitter-grip {
    display: none;
    scale: 3;
    color: var(--wa-color-surface-border);
}
.layout-splitter.axis-h .splitter-grip { rotate: 90deg; }
@media (pointer: coarse) {
    .splitter-grip { display: block; }
}
</style>
