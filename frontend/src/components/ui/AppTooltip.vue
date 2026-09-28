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
 *
 * All extra attributes are forwarded to the underlying <wa-tooltip>.
 *
 * Color scheme: the tooltip body is painted with `--wa-color-text-normal`, so it
 * always reads as the opposite of the page (dark bubble in light mode and vice
 * versa) — but that is a "loud fill", not a scheme switch, so WA components and
 * semantic tokens inside the tooltip would still resolve against the page
 * scheme and become unreadable. The slotted content is therefore wrapped in a
 * `.wa-invert` box (see template), which flips the whole `--wa-color-*` set for
 * the subtree. The class goes on the content, never on the <wa-tooltip> host:
 * on the host it would also flip `--wa-tooltip-background-color` and the bubble
 * would lose its contrast.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'

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
 * Open interactive tooltips, which are mutually exclusive. The grace period
 * would otherwise keep one on screen while the next one opens (its `showDelay`
 * is shorter), and the two would overlap — anchors are usually stacked close
 * together, their tooltips are much taller than the anchors themselves.
 */
const openInteractiveTooltips = new Set()

const props = defineProps({
    force: {
        type: Boolean,
        default: false,
    },
    interactive: {
        type: Boolean,
        default: false,
    },
})

const settingsStore = useSettingsStore()
const shouldShow = computed(() => props.force || !settingsStore.isTouchDevice)

const tooltipEl = ref(null)

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

function clearPendingTimer(el) {
    if (typeof el?.hoverTimeout === 'number') {
        clearTimeout(el.hoverTimeout)
    }
}

/**
 * Light-dismiss for click-triggered tooltips.
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
    if (watchingOutside || !trigger?.split(' ').includes('click')) {
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
    if (event.target !== tooltipEl.value) {
        return
    }
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
    openInteractiveTooltips.delete(listeningEl)
    stopOutsideWatch()
    listeningEl = null
}

// The show/hide listeners are bound for every tooltip — the outside dismiss is
// keyed on the trigger, not on `interactive` — while the grace period the
// pointer needs to reach the tooltip only concerns the interactive ones.
watch([tooltipEl, () => props.interactive], ([el, interactive]) => {
    stopListening()
    if (!el) {
        return
    }
    if (interactive) {
        el.addEventListener('mouseover', cancelPendingHide)
    }
    el.addEventListener('wa-show', handleShow)
    el.addEventListener('wa-after-hide', handleAfterHide)
    listeningEl = el
})

onBeforeUnmount(stopListening)
</script>

<template>
    <wa-tooltip
        v-if="shouldShow"
        ref="tooltipEl"
        :show-delay="TOOLTIP_SHOW_DELAY_MS"
        :hide-delay="interactive ? INTERACTIVE_HIDE_DELAY : undefined"
        v-bind="$attrs"
    >
        <!-- display: contents — the box only carries the inverted color tokens,
             it never takes part in the layout. -->
        <div class="wa-invert tooltip-invert">
            <slot />
        </div>
    </wa-tooltip>
</template>

<style scoped>
.tooltip-invert {
    display: contents;
}
</style>
