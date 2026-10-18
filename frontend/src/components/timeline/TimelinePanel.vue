<script setup>
// TimelinePanel.vue - The Timeline tab of a scope page (global, workspace, project, worktree).
// Design: docs/plans/2026-10-15-timeline-display-design.md.
//
// The Stats tab's structure: in compact-height mode the navigation list of the scope first (the page header
// drops its own copy then), then the "Timeline" band (StickyBand.vue, the band of Stats' provider filter), then
// the newest-first list. The list and the band scroll together: the navigation list scrolls away, the band
// stays at the top of the list (position: sticky).
//
// The list lives in the simple virtual scroller, its own scroll container, so the band and the navigation list
// sit in the scroller's `before` slot (a sticky element must be in the container it sticks to). The scroller
// knows nothing of that slot: it lays its rows out from 0. So the two take no room in the flow (the band's
// negative bottom margin) and the first row of the list (`top`) keeps the room under them, its height measured
// from them. Outside the list (loading, empty, error), the state area is a plain scroll container: the band
// sticks there the same way.
//
// The data side lives in utils/timeline/ (pure, tested): which events the scope asks for, the fetch queue,
// the grouping into rows and the wording of each row. This component wires them to the stores and the DOM.

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useTimelineFeed } from '../../composables/useTimelineFeed'
import { ALL_PROJECTS_ID, useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { getProviderHelpers } from '../../providers'
import { compactHeight } from '../../utils/compactHeight'
import { bindTimelineEvents } from '../../utils/timeline/liveEvents'
import { buildTimelineRows, estimateRowHeight, isNearEnd } from '../../utils/timeline/rows'
import { createLabelsFor } from '../../utils/timeline/providerLabels'
import { resolveTimelineScope } from '../../utils/timeline/scope'
import { createScrollKeeper } from '../../utils/timeline/scrollKeeper'
import ProjectTabNavList from '../project/ProjectTabNavList.vue'
import DaySeparator from '../session/detail/items/DaySeparator.vue'
import StickyBand from '../ui/StickyBand.vue'
import VirtualScroller from '../virtual-scroller/VirtualScroller.vue'
import TimelineEventRow from './TimelineEventRow.vue'

const props = defineProps({
    /** Project ID, `ws:<id>` of a workspace, or ALL_PROJECTS_ID. */
    projectId: {
        type: String,
        required: true,
    },
    /** The Timeline tab is the one on screen (the page is not behind a session, nor on another tab). */
    active: {
        type: Boolean,
        default: true,
    },
})

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()
const feed = useTimelineFeed()
const state = feed.state

// ── What the page asks for ────────────────────────────────────────────────────────────────────────────────

const scope = computed(() => resolveTimelineScope({
    pageId: props.projectId,
    allProjectsId: ALL_PROJECTS_ID,
    projectsLoaded: dataStore.projectsLoaded,
    // The archived-blind list, as Stats uses: the "show archived projects" setting does not apply here.
    workspaceOf: (id) => (workspacesStore.getWorkspaceById(id) ? { allProjectIds: workspacesStore.getAllProjectIds(id) } : null),
    projectOf: (id) => dataStore.getProject(id),
    projectScopeIds: (id) => dataStore.getProjectScopeIds(id),
}))

// The feed compares the SET of ids: a project added, removed or archived reloads the first page.
watch(() => scope.value.projectIds, (ids) => feed.setScope(ids), { immediate: true })

// ── Staying current (design section 9.2) ───────────────────────────────────────────────────────────────────
// The page is kept alive per scope: it keeps fetching while hidden and renders nothing. The signal and the
// socket events reach it through window events dispatched by useWebSocket.js.
let unbindEvents = null
onMounted(() => { unbindEvents = bindTimelineEvents(window, feed) })
onBeforeUnmount(() => {
    unbindEvents?.()
    feed.dispose()
})

// Back on the tab (or the kept-alive page shown again): catch up once; the fetches coalesce in the queue.
watch(() => props.active, (active) => {
    if (active) feed.refresh()
})

// ── What the page shows ───────────────────────────────────────────────────────────────────────────────────

const context = computed(() => ({
    sessions: state.sessions,
    labelsFor: createLabelsFor(getProviderHelpers),
    // A subagent has no openable route as a top-level session; the store knows the ones it loaded.
    isSubagent: (id) => dataStore.getSession(id)?.type === 'subagent',
}))

const eventRows = computed(() => buildTimelineRows(state.events, {
    sessions: state.sessions,
    scope: { kind: scope.value.kind, projectId: scope.value.projectId },
    isSubagent: context.value.isSubagent,
}))

// The room kept under the navigation list and the band (see the top of the file).
const TOP_ROW = Object.freeze({ key: 'timeline-top', type: 'top', withNav: false })
const TOP_ROW_WITH_NAV = Object.freeze({ key: 'timeline-top', type: 'top', withNav: true })
const items = computed(() => [compactHeight.value ? TOP_ROW_WITH_NAV : TOP_ROW, ...eventRows.value])

/** Which block of the page is visible. */
const view = computed(() => {
    if (scope.value.state === 'loading') return 'loading'
    if (scope.value.state === 'empty') return 'empty'
    if (scope.value.state === 'not-found') return 'not-found'
    if (state.status === 'error') return 'error'
    if (state.status !== 'ready') return 'loading'
    return state.events.length ? 'list' : 'empty'
})

const itemKey = (item) => item.key

// ── The room under the navigation list and the band ───────────────────────────────────────────────────────
// Their height together, measured, as `--timeline-top-height` on the panel: the height of the top row and the
// band's negative bottom margin. A band of no height is a hidden tab (display: none): the last room is kept.
const topNavRef = ref(null)
const bandRef = ref(null)
const topHeight = ref(0)
const topStyle = computed(() => ({ '--timeline-top-height': `${topHeight.value}px` }))
let topObserver = null

function measureTop() {
    const band = bandRef.value?.$el
    const bandHeight = band?.getBoundingClientRect?.().height ?? 0
    if (!bandHeight) return
    const navHeight = topNavRef.value?.$el?.getBoundingClientRect?.().height ?? 0
    topHeight.value = bandHeight + navHeight
}

watch([bandRef, topNavRef], ([band, nav]) => {
    topObserver?.disconnect()
    topObserver = null
    if (!band) return
    measureTop()
    if (typeof ResizeObserver === 'undefined') return
    topObserver = new ResizeObserver(measureTop)
    topObserver.observe(band.$el)
    if (nav) topObserver.observe(nav.$el)
}, { flush: 'post' })
onBeforeUnmount(() => topObserver?.disconnect())

// ── Scroll: rows inserted above the view must not move it ─────────────────────────────────────────────────
const scrollerRef = ref(null)
const scrollKeeper = createScrollKeeper(() => scrollerRef.value)

watch(items, async (next) => {
    const snapshot = scrollKeeper.capture()
    if (!snapshot) return
    await nextTick()
    scrollKeeper.restore(snapshot, new Map(next.map((row, index) => [row.key, index])))
}, { flush: 'pre' })

// ── Scroller: estimates, next page ────────────────────────────────────────────────────────────────────────

// A first height estimate per row type (day separator, event line, block head and end, top room) for the rows the
// scroller has not measured yet. It runs AFTER the render: the scroller only exists once the first page flipped the
// view to the list, and it is created again each time the list comes back. A row the scroller already measured keeps
// its measure (seeding would overwrite it, and no resize would correct it). `seeded` maps a key to the estimate
// written for it (null: the scroller had its own height): when a live insertion flips a row's head or end status, the new
// estimate replaces the old one while the cache still holds that old seed (the row was not measured since). `seeded`
// follows the list: it is emptied when the scroller goes away and trimmed to the keys still in the list.
const seeded = new Map()
function seedHeights() {
    const scroller = scrollerRef.value
    if (!scroller) {
        seeded.clear()
        return
    }
    const keys = new Set()
    for (const row of items.value) {
        keys.add(row.key)
        const estimate = estimateRowHeight(row)
        if (!seeded.has(row.key)) {
            const unmeasured = scroller.getItemHeight(row.key) === undefined
            if (unmeasured) scroller.seedItemHeight(row.key, estimate)
            seeded.set(row.key, unmeasured ? estimate : null)
            continue
        }
        const previous = seeded.get(row.key)
        if (previous === null || previous === estimate) continue
        if (scroller.getItemHeight(row.key) === previous) {
            scroller.seedItemHeight(row.key, estimate)
            seeded.set(row.key, estimate)
        } else {
            seeded.set(row.key, null)
        }
    }
    for (const key of seeded.keys()) if (!keys.has(key)) seeded.delete(key)
}
watch([items, scrollerRef], seedHeights, { flush: 'post' })

let visibleEnd = 0

function maybeLoadMore() {
    if (view.value === 'list' && isNearEnd(items.value.length, visibleEnd)) feed.loadMore()
}

function onScrollerUpdate(range) {
    visibleEnd = range.visibleEndIndex
    maybeLoadMore()
}

// What is in view now, asked to the scroller (its last `update` may be older: a page landed, the tab came back).
function refreshVisibleEnd() {
    const range = scrollerRef.value?.getVisibleRange()
    if (range) visibleEnd = range.end
}

function onBecameVisible() {
    refreshVisibleEnd()
    maybeLoadMore()
}

// A page that just landed may leave the end of the list still in view (a tall window): ask again.
watch(() => state.events.length, async () => {
    await nextTick()
    refreshVisibleEnd()
    maybeLoadMore()
})
</script>

<template>
    <div class="timeline-panel" :style="topStyle">
        <div v-if="view !== 'list'" class="timeline-state-area">
            <ProjectTabNavList v-if="compactHeight" :project-id="projectId" />
            <StickyBand class="timeline-band">
                <span class="timeline-band-label">Timeline</span>
            </StickyBand>
            <div v-if="view === 'loading'" class="timeline-state" role="status">
                <wa-spinner></wa-spinner>
                <span>Loading…</span>
            </div>
            <div v-else-if="view === 'empty'" class="timeline-state">No events yet</div>
            <div v-else-if="view === 'not-found'" class="timeline-state">
                <span>Project not found</span>
                <RouterLink :to="{ name: 'projects-all' }">Back to All Projects</RouterLink>
            </div>
            <div v-else class="timeline-state">
                <!-- wa-callout has a default slot and an icon slot only: the button goes in the default one. -->
                <wa-callout variant="danger">
                    <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                    <div class="timeline-error-content">
                        <span>Failed to load the timeline</span>
                        <wa-button variant="danger" appearance="outlined" size="small" @click="feed.retry()">
                            <wa-icon name="arrow-rotate-right" slot="start"></wa-icon>
                            Retry
                        </wa-button>
                    </div>
                </wa-callout>
            </div>
        </div>

        <div v-else class="timeline-list">
            <VirtualScroller
                ref="scrollerRef"
                class="timeline-scroller"
                :items="items"
                :item-key="itemKey"
                :min-item-height="28"
                :buffer="400"
                :unload-buffer="800"
                prevent-auto-scroll-to-bottom
                @update="onScrollerUpdate"
                @became-visible="onBecameVisible"
            >
                <template #before>
                    <ProjectTabNavList v-if="compactHeight" ref="topNavRef" :project-id="projectId" class="timeline-top-nav" />
                    <StickyBand ref="bandRef" class="timeline-band timeline-band--over-list">
                        <span class="timeline-band-label">Timeline</span>
                    </StickyBand>
                </template>
                <template #default="{ item }">
                    <div v-if="item.type === 'top'" class="timeline-top-room" aria-hidden="true"></div>
                    <DaySeparator v-else-if="item.type === 'day'" :label="item.label" :day-key="item.dayKey" />
                    <TimelineEventRow v-else :row="item" :context="context" />
                </template>
            </VirtualScroller>

            <div v-if="state.loadingMore" class="timeline-footer" role="status">
                <wa-spinner></wa-spinner>
            </div>
            <div v-else-if="state.loadError" class="timeline-footer timeline-footer--error">
                <!-- wa-callout has a default slot and an icon slot only: the button goes in the default one. -->
                <wa-callout variant="danger">
                    <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                    <div class="timeline-error-content">
                        <span>Failed to load more events</span>
                        <wa-button variant="danger" appearance="outlined" size="small" @click="feed.retry()">
                            <wa-icon name="arrow-rotate-right" slot="start"></wa-icon>
                            Retry
                        </wa-button>
                    </div>
                </wa-callout>
            </div>
        </div>
    </div>
</template>

<style scoped>
.timeline-panel {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.timeline-band-label {
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
}

/* In the list, the navigation list and the band take no room in the flow: the top row keeps it (see the top of
   the script). The navigation list is positioned, so its links stay above that empty row. */
.timeline-band--over-list {
    margin-block-end: calc(-1 * var(--timeline-top-height));
}

.timeline-top-nav {
    position: relative;
    z-index: 1;
}

.timeline-top-room {
    height: var(--timeline-top-height);
    pointer-events: none;
}

.timeline-list {
    flex: 1;
    min-height: 0;
    position: relative;
    display: flex;
    flex-direction: column;
}

/* The scroller shares the column with the footer (spinner, error): it takes what is left. */
.timeline-list .timeline-scroller {
    flex: 1;
    min-height: 0;
}

/* The scroller pins itself to the bottom when the user is near it (native scroll anchoring on its sentinel).
   A newest-first list must never do that: loading older events would drag the view down. */
.timeline-list .timeline-scroller.at-bottom {
    overflow-anchor: none;
}

.timeline-state-area {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.timeline-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xl) var(--wa-space-l);
    color: var(--wa-color-text-quiet);
}

.timeline-footer {
    flex: 0 0 auto;
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) var(--wa-space-l);
}

.timeline-footer--error {
    display: block;
}

.timeline-error-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-s);
    text-align: center;
}
</style>
