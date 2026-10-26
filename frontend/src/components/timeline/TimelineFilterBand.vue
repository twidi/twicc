<script setup>
// TimelineFilterBand.vue - The content of the Timeline tab's sticky band (filters design sections 3.1 to 3.3):
// the "Timeline" title, the views (a segmented control), the Filters button and its panel (a wa-popover of
// checkboxes), then the active filter pills and "Clear all".
//
// The filters live in the per-page store (stores/timelineFilters.js): read through `filtersFor`, changed only
// through its actions. The panel's close and focus behaviour is the pure panel controller
// (utils/timeline/panelController.js), which explains why the band does not use `v-popover-focus-fix`.
//
// Three layout levels, measured (spec 3.1): `line` (title, views and button on one line), `stacked` (the title on
// its own row), `compact` (short view labels, the button's label visually hidden; the button wraps under the
// views when it still does not fit). Nothing scrolls sideways.

import { computed, nextTick, onBeforeUnmount, onDeactivated, onMounted, ref, useId, watch } from 'vue'
import { useTimelineFiltersStore } from '../../stores/timelineFilters'
import { getProviderHelpers } from '../../providers'
import { AUTHORS, FAMILIES, PROVIDERS, VIEWS, activeCount, groupPills } from '../../utils/timeline/filters'
import { createPanelController } from '../../utils/timeline/panelController'
import SegmentedControl from '../ui/SegmentedControl.vue'

const props = defineProps({
    /** The timeline page id: the key of its filters in the store. */
    pageId: {
        type: String,
        required: true,
    },
    /** The timeline is the one on screen. When it leaves, the panel closes and stays closed on return (spec 5). */
    active: {
        type: Boolean,
        default: true,
    },
})

const store = useTimelineFiltersStore()
const filters = computed(() => store.filtersFor(props.pageId))
const count = computed(() => activeCount(filters.value))

/** The product name of a provider ("Claude Code", "Codex"), the raw key when the helpers do not name one. */
function providerLabel(provider) {
    return getProviderHelpers(provider)?.constructor?.serviceProductLabel ?? provider
}

const pills = computed(() => groupPills(filters.value, { providerLabel }))

// The ids of this instance: the popover finds its anchor by id, so two bands never share one.
const uid = useId()
const buttonId = `${uid}-filters`

/** The panel groups (spec 3.2), their values in constant order. */
const PANEL_GROUPS = [
    { group: 'families', label: 'Events', options: FAMILIES },
    { group: 'providers', label: 'Provider', options: PROVIDERS.map((value) => ({ value, label: providerLabel(value) })) },
    { group: 'authors', label: 'Done by', options: AUTHORS },
].map((entry) => ({ ...entry, labelId: `${uid}-${entry.group}` }))

function isChecked(group, value) {
    return filters.value[group].includes(value)
}

// ── Elements ───────────────────────────────────────────────────────────────────────────────────────────────

const rootRef = ref(null)
const lineRef = ref(null)
const controlsRef = ref(null)
const viewsRef = ref(null)
const buttonRef = ref(null)
const popoverRef = ref(null)
// The remove control of each pill, by group (`families`, `providers`, `authors`, `session`).
const removeRefs = new Map()

function setRemoveRef(group, element) {
    if (element) removeRefs.set(group, element)
    else removeRefs.delete(group)
}

// ── The panel: close and focus ─────────────────────────────────────────────────────────────────────────────

const controller = createPanelController()

// The band is the whole sticky band (StickyBand.vue, spec 3.1), its padding included: a click there is not a click
// outside the band (spec 3.2). The capture listener sits on that element itself (the band root when there is no
// StickyBand around it): a click on the padding has the StickyBand as target, and a capture listener on one of its
// descendants would never see it. The same element is the boundary of the veto.
let bandBoundary = null

function onBandClick(event) {
    controller.onBandClick(event, { band: bandBoundary, button: buttonRef.value })
}

onMounted(() => {
    const root = rootRef.value
    bandBoundary = root?.closest?.('.sticky-band') ?? root
    bandBoundary?.addEventListener('click', onBandClick, true)
})
onBeforeUnmount(() => {
    bandBoundary?.removeEventListener('click', onBandClick, true)
    bandBoundary = null
})

// The popover's own events only: a nested wa-checkbox or wa-button fires none of these today, but any wa-* child
// that does would bubble here.
function onPopoverShow(event) {
    if (event.target !== event.currentTarget) return
    controller.onShow()
}

function onPopoverHide(event) {
    if (event.target !== event.currentTarget) return
    controller.onHide(event, { activeElement: document.activeElement })
}

function onPopoverAfterHide(event) {
    if (event.target !== event.currentTarget) return
    controller.onAfterHide({ button: buttonRef.value })
}

// The panel is closed when the timeline leaves the screen (spec 5): the tab changes (`active`), or the page is
// detached by KeepAlive (a session opened) or unmounted, which may happen without `active` changing first. A
// detached popover has lost its document listeners: left open, it would still be open on return.
function closePanelFromCode() {
    if (popoverRef.value) controller.closeFromCode(popoverRef.value)
}

watch(() => props.active, (active) => {
    if (!active) closePanelFromCode()
})
onDeactivated(closePanelFromCode)
onBeforeUnmount(closePanelFromCode)

const filtersButton = () => buttonRef.value

/** Focus the Filters button (spec 3.1: after "Clear all", or a session filter released). */
function focusFiltersButton() {
    controller.focus(filtersButton, popoverRef.value)
}

/** Focus the remove control of the session pill (spec 3.1: after a session filter is set). */
function focusSessionPill() {
    controller.focus(() => removeRefs.get('session') ?? null, popoverRef.value)
}

defineExpose({ focusFiltersButton, focusSessionPill })

// ── Actions ────────────────────────────────────────────────────────────────────────────────────────────────

function setView(view) {
    store.setView(props.pageId, view)
}

function toggle(group, value) {
    store.toggle(props.pageId, group, value)
}

function resetPanel() {
    store.resetPanel(props.pageId)
}

/**
 * Remove a pill's filter. Its remove control disappears with it: the focus goes to the next pill's remove control,
 * else the previous one, else the Filters button (spec 3.1). Chosen before the change, resolved after the render.
 */
async function removePill(group) {
    const list = pills.value
    const index = list.findIndex((pill) => pill.group === group)
    const target = list[index + 1]?.group ?? list[index - 1]?.group ?? null
    if (group === 'session') store.clearSession(props.pageId)
    else store.clearGroup(props.pageId, group)
    await nextTick()
    controller.focus(() => (target ? removeRefs.get(target) ?? null : buttonRef.value), popoverRef.value)
}

async function clearAll() {
    store.clearAll(props.pageId)
    await nextTick()
    focusFiltersButton()
}

// ── Layout levels (spec 3.1) ───────────────────────────────────────────────────────────────────────────────
// `line` -> `stacked` when the line element overflows; `stacked` -> `compact` when the views + button line
// overflows with full labels. Each step up records the overflowing line's `scrollWidth` (`records`); a step down
// happens when the available width (the `clientWidth` of that line, which fills its row) reaches the record of
// the level above, and writes nothing. A level change is measured again once rendered (`settled`): a reading of
// the DOM of the previous level would be stale.
//
// The ResizeObserver callback never measures: it asks for one animation frame that does (utils/detailsMotion.js does
// the same). A level change resizes the band, so the panel's own observer on StickyBand (the top room) sees it;
// done inside the observer broadcast, that resize would land on an already-notified (shallower) target, and the
// browser reports "ResizeObserver loop completed with undelivered notifications". The cost: the frame of the
// resize is painted at the previous level (clipped, never scrolled sideways); the frame after it has the new one.

const layout = ref('line')
const records = { line: null, stacked: null }
let settled = true

function setLayout(next) {
    if (next === layout.value) return
    layout.value = next
    settled = false
}

function overflows(element) {
    return (element.scrollWidth ?? 0) > (element.clientWidth ?? 0)
}

function measure() {
    const line = lineRef.value
    const controls = controlsRef.value
    if (!settled || !line || !controls) return
    if (layout.value === 'line') {
        if (overflows(line)) {
            records.line = line.scrollWidth
            setLayout('stacked')
        }
        return
    }
    if (layout.value === 'stacked') {
        if (overflows(controls)) {
            records.stacked = controls.scrollWidth
            setLayout('compact')
        } else if (records.line !== null && line.clientWidth >= records.line) {
            setLayout('line')
        }
        return
    }
    if (records.stacked !== null && controls.clientWidth >= records.stacked) setLayout('stacked')
}

watch(layout, () => {
    settled = true
    measure()
}, { flush: 'post' })

// The count appearing or disappearing changes the content, not the line box: a recorded width would be stale, so
// measure again from the first level.
watch(count, () => {
    if (layout.value === 'line') measure()
    else setLayout('line')
}, { flush: 'post' })

let observer = null
let frame = 0

/** One measure in the next animation frame, outside any ResizeObserver broadcast (see above). */
function scheduleMeasure() {
    if (frame) return
    frame = requestAnimationFrame(() => {
        frame = 0
        measure()
    })
}

onMounted(() => {
    measure()
    if (typeof ResizeObserver === 'undefined') return
    observer = new ResizeObserver(scheduleMeasure)
    // The lines AND their children: a child that grows (a label) does not always resize its line.
    for (const element of [lineRef.value, controlsRef.value, viewsRef.value?.$el, buttonRef.value]) {
        if (element) observer.observe(element)
    }
})
onBeforeUnmount(() => {
    observer?.disconnect()
    if (frame) cancelAnimationFrame(frame)
    frame = 0
})

const viewOptions = computed(() => VIEWS.map((view) => (layout.value === 'compact'
    ? { value: view.value, label: view.short, title: view.label }
    : { value: view.value, label: view.label })))
</script>

<template>
    <div ref="rootRef" class="timeline-filter-band">
        <div ref="lineRef" :class="['timeline-filter-line', `timeline-filter-line--${layout}`]">
            <span class="timeline-filter-title">Timeline</span>
            <div ref="controlsRef" class="timeline-filter-controls">
                <SegmentedControl
                    ref="viewsRef"
                    class="timeline-filter-views"
                    label="View"
                    :model-value="filters.view"
                    :options="viewOptions"
                    @update:model-value="setView"
                />
                <wa-button :id="buttonId" ref="buttonRef" class="timeline-filter-button" size="small" appearance="outlined">
                    <wa-icon slot="start" name="filter"></wa-icon>
                    <span class="timeline-filter-button-label">Filters</span>
                    <span v-if="count > 0" class="timeline-filter-count">{{ count }}</span>
                </wa-button>
                <!-- Right after its button: Tab from the button reaches the panel before the pills. -->
                <wa-popover
                    ref="popoverRef"
                    :for="buttonId"
                    placement="bottom-end"
                    class="timeline-filter-popover"
                    @wa-show="onPopoverShow"
                    @wa-hide="onPopoverHide"
                    @wa-after-hide="onPopoverAfterHide"
                >
                    <div class="timeline-filter-panel">
                        <div
                            v-for="entry in PANEL_GROUPS"
                            :key="entry.group"
                            class="timeline-filter-group"
                            role="group"
                            :aria-labelledby="entry.labelId"
                        >
                            <div :id="entry.labelId" class="timeline-filter-group-label">{{ entry.label }}</div>
                            <wa-checkbox
                                v-for="option in entry.options"
                                :key="option.value"
                                size="small"
                                :checked="isChecked(entry.group, option.value)"
                                :hint="option.hint"
                                @change="toggle(entry.group, option.value)"
                            >{{ option.label }}</wa-checkbox>
                        </div>
                        <div class="timeline-filter-panel-actions">
                            <wa-button size="small" appearance="plain" @click="resetPanel">Reset</wa-button>
                        </div>
                    </div>
                </wa-popover>
            </div>
        </div>
        <div v-if="pills.length" class="timeline-filter-pills">
            <span v-for="pill in pills" :key="pill.group" class="timeline-filter-pill" :title="pill.title">
                <span class="timeline-filter-pill-name">{{ pill.label }}:</span>
                <span class="timeline-filter-pill-value">{{ pill.shown.join(', ') }}</span>
                <span v-if="pill.more > 0" class="timeline-filter-pill-more">+{{ pill.more }}</span>
                <button
                    :ref="(element) => setRemoveRef(pill.group, element)"
                    type="button"
                    class="timeline-filter-pill-remove"
                    :aria-label="`Remove the ${pill.label} filter`"
                    @click="removePill(pill.group)"
                >
                    <wa-icon name="xmark"></wa-icon>
                </button>
            </span>
            <wa-button class="timeline-filter-clear" size="small" appearance="plain" @click="clearAll">Clear all</wa-button>
        </div>
    </div>
</template>

<style scoped>
/* The flex item of StickyBand (a centered flex box): it fills the band, and `min-width: 0` lets it be narrower than
   its content (a flex item's automatic minimum is its min-content width, and `overflow: clip` does not change
   that), so the band never pushes the scroller into a sideways scroll. The inline padding aligns the title with
   the cards' inset (--timeline-card-inset) and keeps the focus rings inside the scroller, which clips. */
.timeline-filter-band {
    flex: 1 1 auto;
    min-width: 0;
    padding-inline: var(--wa-space-m);
}

/* `overflow-clip-margin` is the app's focus ring: --wa-focus-ring-width (0.25rem) + --wa-focus-ring-offset (0rem),
   the halo of styles/glow.css. A literal on purpose: Chrome rejects math functions in this property (`calc()`,
   `max()`, a calc-valued token all compute to 0px and cut the Filters button's ring). */
.timeline-filter-line {
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    gap: var(--wa-space-xs) var(--wa-space-m);
    min-width: 0;
    overflow: clip;
    overflow-clip-margin: 0.25rem;
}

.timeline-filter-line--stacked {
    flex-wrap: wrap;
}

.timeline-filter-line--compact {
    flex-wrap: wrap;
}

.timeline-filter-title {
    flex: none;
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
}

.timeline-filter-line--stacked .timeline-filter-title {
    flex-basis: 100%;
}

.timeline-filter-line--compact .timeline-filter-title {
    flex-basis: 100%;
}

/* On the `line` level it must NOT clip: its overflow has to reach the line element, whose overflow is the first
   threshold. */
.timeline-filter-controls {
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    gap: var(--wa-space-xs) var(--wa-space-s);
    min-width: 0;
    overflow: visible;
    margin-inline-start: auto;
}

/* It fills its row, so its `clientWidth` is the available width (the way back to `line` reads it). */
.timeline-filter-line--stacked .timeline-filter-controls {
    flex: 1 1 100%;
    flex-wrap: nowrap;
    overflow: clip;
    overflow-clip-margin: 0.25rem;
    margin-inline-start: 0;
}

.timeline-filter-line--compact .timeline-filter-controls {
    flex: 1 1 100%;
    flex-wrap: wrap;
    overflow: clip;
    overflow-clip-margin: 0.25rem;
    margin-inline-start: 0;
}

/* Never shrink: wa-radio-group lays its radios out with `flex-wrap: wrap`, so a shrinkable control would wrap its
   segments into rows and the line would never overflow. */
.timeline-filter-views {
    flex: none;
}

.timeline-filter-button {
    flex: none;
}

/* Out of sight, still in the button's accessible name (never display: none). */
.timeline-filter-line--compact .timeline-filter-button-label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
}

.timeline-filter-count {
    margin-inline-start: var(--wa-space-2xs);
    padding-inline: var(--wa-space-2xs);
    min-width: 1.25rem;
    border-radius: var(--wa-border-radius-pill);
    background: var(--wa-color-brand-fill-normal);
    color: var(--wa-color-brand-on-normal);
    font-size: var(--wa-font-size-xs);
    line-height: 1.25rem;
    text-align: center;
}

.timeline-filter-line--compact .timeline-filter-count {
    margin-inline-start: 0;
}

.timeline-filter-popover {
    --max-width: min(22rem, calc(100vw - 2rem));
}

/* A short window never hides the end of the panel: it scrolls inside. */
.timeline-filter-panel {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    max-height: calc(100dvh - 8rem);
    overflow-y: auto;
    font-size: var(--wa-font-size-s);
}

.timeline-filter-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.timeline-filter-group-label {
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
}

.timeline-filter-panel-actions {
    display: flex;
    justify-content: flex-end;
}

/* The pills wrap: the band grows by a line, never sideways. */
.timeline-filter-pills {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-2xs) var(--wa-space-xs);
    min-width: 0;
    margin-block-start: var(--wa-space-xs);
}

.timeline-filter-pill {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    min-width: 0;
    max-width: 100%;
    padding-block: var(--wa-space-3xs);
    padding-inline: var(--wa-space-s) var(--wa-space-3xs);
    border: 1px solid var(--wa-color-brand-border-quiet);
    border-radius: var(--wa-border-radius-pill);
    background: var(--wa-color-brand-fill-quiet);
    color: var(--wa-color-brand-on-quiet);
    font-size: var(--wa-font-size-xs);
    line-height: 1.25;
}

.timeline-filter-pill-name {
    flex: none;
    font-weight: var(--wa-font-weight-semibold);
}

/* A long value (a 200-character session title) ends with an ellipsis: the full list is the pill's tooltip. */
.timeline-filter-pill-value {
    min-width: 0;
    max-width: 20rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.timeline-filter-pill-more {
    flex: none;
    font-weight: var(--wa-font-weight-semibold);
}

/* Web Awesome's native.css makes every <button> a form control of the form-control height: reset to a small round
   icon button. */
.timeline-filter-pill-remove {
    flex: none;
    appearance: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: auto;
    padding: var(--wa-space-3xs);
    border: none;
    border-radius: var(--wa-border-radius-circle);
    background: none;
    color: inherit;
    font: inherit;
    line-height: 1;
    cursor: pointer;
}

.timeline-filter-pill-remove:hover {
    background: var(--wa-color-brand-fill-normal);
}

.timeline-filter-pill-remove:focus-visible {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
