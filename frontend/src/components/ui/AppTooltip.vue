<script>
const mountedTooltips = new Set()
const tooltipDismissals = new Set()
const openInteractiveTooltips = new Set()

export function onTooltipDismissal(callback) {
    tooltipDismissals.add(callback)
    return () => tooltipDismissals.delete(callback)
}

function clearPendingTimer(el) {
    if (typeof el?.hoverTimeout === 'number') {
        clearTimeout(el.hoverTimeout)
    }
}

export function hideAllTooltips() {
    for (const callback of tooltipDismissals) callback()
    for (const el of mountedTooltips) {
        clearPendingTimer(el)
        el.hide()
    }
}
</script>

<script setup>
/**
 * AppTooltip - Unified tooltip wrapper around wa-tooltip.
 *
 * Automatically hides tooltips on touch devices (where hover is not available).
 * On non-touch devices, tooltips are always shown.
 *
 * Usage:
 *   <AppTooltip :for="elementId">Tooltip text</AppTooltip>
 *
 * Props:
 *   - force: When true, the tooltip is always shown even on touch devices.
 *     Use for critical UI elements like quota indicators where the tooltip
 *     provides essential information.
 *   - interactive: When true, the tooltip holds controls the pointer must be
 *     able to reach (buttons, links). Adds a grace period before it closes,
 *     cancelled as soon as the pointer lands on it. See cancelPendingHide.
 *   - lazy: Mount the slot on show and remove it after the hide animation.
 *
 * All extra attributes are forwarded to the underlying <wa-tooltip>.
 *
 * Color scheme: the tooltip is a glass surface in the page scheme, like the popovers and
 * the toasts (styles/glass.css). The slotted content resolves the page's own tokens, so
 * nothing inside needs flipping.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { serializeTooltipTransitions } from '../../utils/tooltipTransitions.js'

/**
 * Grace period, in ms, before an interactive tooltip closes once the pointer
 * has left it. Long enough to cross the gap wa-tooltip leaves between the
 * anchor and the tooltip body (its `distance`, 8px by default) whatever the
 * placement and however diagonal the move, short enough to still feel
 * immediate when moving away for good.
 */
const INTERACTIVE_HIDE_DELAY = 300

/**
 * Delay, in ms, before a tooltip shows (Web Awesome's default is 150ms): a pointer
 * crossing the UI does not flash tooltips. Bound before `v-bind="$attrs"`, so a
 * caller's own `show-delay` still wins.
 */
const TOOLTIP_SHOW_DELAY_MS = 250

/**
 * Gap, in px, between the tooltip and its target (Web Awesome's default is 8). The glass
 * arrow is one and a half times the theme's (styles/glass.css): 3px more keep its tip off
 * the target. Bound before `v-bind="$attrs"`, so a caller's own `distance` still wins.
 */
const TOOLTIP_DISTANCE = 11

/**
 * Open interactive tooltips, which are mutually exclusive. The grace period
 * would otherwise keep one on screen while the next one opens (its `showDelay`
 * is shorter), and the two would overlap — anchors are usually stacked close
 * together, their tooltips are much taller than the anchors themselves.
 */
const props = defineProps({
    force: {
        type: Boolean,
        default: false,
    },
    interactive: {
        type: Boolean,
        default: false,
    },
    lazy: {
        type: Boolean,
        default: false,
    },
})

const settingsStore = useSettingsStore()
const shouldShow = computed(() => props.force || !settingsStore.isTouchDevice)

const tooltipEl = ref(null)
const contentMounted = ref(false)
const renderContent = computed(() => !props.lazy || contentMounted.value)

function show() {
    clearPendingTimer(tooltipEl.value)
    return tooltipEl.value?.show()
}

function hide() {
    clearPendingTimer(tooltipEl.value)
    return tooltipEl.value?.hide()
}

// Update synchronously before the browser sends focus or compatibility mouse events.
function setTrigger(trigger) {
    clearPendingTimer(tooltipEl.value)
    if (tooltipEl.value) tooltipEl.value.trigger = trigger
}

defineExpose({ show, hide, setTrigger })

/**
 * wa-tooltip never cancels a pending hide when the pointer enters the tooltip:
 * it binds `mouseover` on the anchor only, and its own `mouseout` handler
 * returns *before* clearing the timer when the tooltip is hovered. So the hide
 * scheduled while the pointer crosses the anchor-to-tooltip gap still fires,
 * closing the tooltip under the pointer. That is invisible with the default 0ms
 * delay, but it defeats the grace period interactive tooltips need — so we
 * close that gap ourselves.
 *
 * `hoverTimeout` is wa-tooltip's single show/hide timer handle (a plain
 * property, not a private field). Clearing it here can never swallow a pending
 * *show*: a closed tooltip has no hit area, so it emits no mouseover.
 */
function cancelPendingHide() {
    clearPendingTimer(tooltipEl.value)
}

/**
 * Light-dismiss for click-triggered and manual tooltips.
 *
 * wa-tooltip has no backdrop and no outside-click handling: once open, it only
 * closes on a second click on its own anchor, on Escape, or — for interactive
 * ones — when the next tooltip opens. On a touch device that leaves a tapped
 * tooltip on screen with no obvious way out, since the pointer never leaves the
 * anchor. So while a click-triggered tooltip is open, watch the document and
 * close it on the first pointer press outside.
 *
 * `pointerdown` in capture covers touch and mouse alike, and still fires when
 * the target swallows the click. Two exclusions are mandatory:
 *
 *   - the anchor, because its pointerdown precedes the click that toggles the
 *     tooltip: closing here would let that click re-open it, so the second tap
 *     would never close anything;
 *   - the tooltip itself, because it can hold controls the user must reach
 *     (buttons, links) — see the `interactive` prop.
 *
 * composedPath() is required for both: the tooltip renders its content in a
 * shadow root, so event.target alone never resolves to it.
 */
function handleOutsidePointerDown(event) {
    const el = tooltipEl.value
    if (!el) {
        return
    }
    const path = event.composedPath()
    if (path.includes(el) || (el.anchor && path.includes(el.anchor))) {
        return
    }
    clearPendingTimer(el)
    el.hide()
}

let watchingOutside = false

function startOutsideWatch() {
    // Read the trigger off the element: it can change at runtime (the sidebar
    // quota tooltips swap hover for click on touch devices).
    const trigger = tooltipEl.value?.trigger
    if (watchingOutside || !trigger?.split(' ').some(value => value === 'click' || value === 'manual')) {
        return
    }
    document.addEventListener('pointerdown', handleOutsidePointerDown, { capture: true })
    watchingOutside = true
}

function stopOutsideWatch() {
    if (!watchingOutside) {
        return
    }
    document.removeEventListener('pointerdown', handleOutsidePointerDown, { capture: true })
    watchingOutside = false
}

function handleShow(event) {
    // wa-show bubbles and is composed: ignore the ones fired by nested wa-*.
    if (event.target !== tooltipEl.value) {
        return
    }
    contentMounted.value = true
    if (props.interactive) {
        for (const other of [...openInteractiveTooltips]) {
            if (other !== tooltipEl.value) {
                clearPendingTimer(other)
                other.hide()
            }
        }
        openInteractiveTooltips.add(tooltipEl.value)
    }
    startOutsideWatch()
}

function handleAfterHide(event) {
    if (event.target !== tooltipEl.value || tooltipEl.value?.open) {
        return
    }
    contentMounted.value = false
    openInteractiveTooltips.delete(tooltipEl.value)
    stopOutsideWatch()
}

let listeningEl = null

function stopListening() {
    if (!listeningEl) {
        return
    }
    listeningEl.removeEventListener('mouseover', cancelPendingHide)
    listeningEl.removeEventListener('wa-show', handleShow)
    listeningEl.removeEventListener('wa-after-hide', handleAfterHide)
    mountedTooltips.delete(listeningEl)
    openInteractiveTooltips.delete(listeningEl)
    stopOutsideWatch()
    listeningEl = null
}

// The show/hide listeners are bound for every tooltip — the outside dismiss is
// keyed on the trigger, not on `interactive` — while the grace period the
// pointer needs to reach the tooltip only concerns the interactive ones.
watch([tooltipEl, () => props.interactive], ([el, interactive]) => {
    stopListening()
    contentMounted.value = !!el?.open
    if (!el) {
        return
    }
    serializeTooltipTransitions(el)
    if (interactive) {
        el.addEventListener('mouseover', cancelPendingHide)
    }
    el.addEventListener('wa-show', handleShow)
    el.addEventListener('wa-after-hide', handleAfterHide)
    mountedTooltips.add(el)
    listeningEl = el
})

onBeforeUnmount(stopListening)
</script>

<template>
    <wa-tooltip
        v-if="shouldShow"
        ref="tooltipEl"
        :show-delay="TOOLTIP_SHOW_DELAY_MS"
        :distance="TOOLTIP_DISTANCE"
        :hide-delay="interactive ? INTERACTIVE_HIDE_DELAY : undefined"
        v-bind="$attrs"
    >
        <slot v-if="renderContent" />
    </wa-tooltip>
</template>
